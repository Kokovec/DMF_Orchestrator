"""ansible/scripts/capacity.py, with kubectl and findmnt faked."""
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import yaml

from .util import REPO

spec = importlib.util.spec_from_file_location("capacity", REPO / "ansible" / "scripts" / "capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)

GIB = 2**30
FINDMNT = ("/dev/shm 8589934592\n/var/lib/dmf/mxl/studio-25 542113792\n"
           "/var/lib/dmf/mxl/news-studio 2147483648\n/var/lib/dmf/mxl/studio-a 688914432\n")
CLUSTER = {
    ("get", "nodes"): {"items": [{"status": {"allocatable": {"cpu": "16", "memory": "20Gi"}}}]},
    ("get", "resourcequota"): {"items": [{"metadata": {"namespace": "wl-studio-25"},
                                          "spec": {"hard": {"requests.cpu": "4", "requests.memory": "6Gi"}}}]},
    ("get", "pods"): {"items": []},
}


def fake_run(argv, **kw):
    if argv[0] == "findmnt":
        return subprocess.CompletedProcess(argv, 0, FINDMNT, "")
    key = tuple(argv[3:5])
    return subprocess.CompletedProcess(argv, 0, json.dumps(CLUSTER[key]), "")


class Capacity(unittest.TestCase):
    def verdict(self, memory, size_mib):
        wl = {"metadata": {"name": "studio-a"},
              "spec": {"resourceProfile": {"cpu": "4", "memory": memory},
                       "mediaExchange": {"domain": "studio-a", "sizeMiB": size_mib}}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as fh:
            yaml.safe_dump(wl, fh)
            fh.flush()
            out = io.StringIO()
            with mock.patch.object(capacity.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(capacity.sys, "argv", ["capacity.py", fh.name, "kubeconfig"]), redirect_stdout(out):
                rc = capacity.main()
        return rc, json.loads(out.getvalue())

    def test_other_domains_are_reserved_and_its_own_is_not(self):
        rc, v = self.verdict("6Gi", 657)
        self.assertEqual(v["other_flow_domains_mib"], {"news-studio": 2048, "studio-25": 517})
        # 20 GiB - 6 GiB (studio-25 quota) - 2 GiB - 517 MiB (other domains)
        self.assertEqual(v["free"]["memory_gib"], round((20 * GIB - 6 * GIB - 2 * GIB - 542113792) / GIB, 2))
        self.assertTrue(v["fits"])
        self.assertEqual(rc, 0)

    def test_domains_can_be_what_tips_it_over(self):
        # Needs 11 GiB + 657 MiB = 11.64 GiB: 11.50 GiB is free with the other domains counted, 14 without.
        rc, v = self.verdict("11Gi", 657)
        self.assertFalse(v["fits"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
