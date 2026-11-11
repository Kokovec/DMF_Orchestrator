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
}


def pod(ns, *containers):
    """containers: (cpu request, cpu limit or None)"""
    return {"metadata": {"namespace": ns},
            "spec": {"containers": [{"resources": {"requests": {"cpu": req},
                                                   **({"limits": {"cpu": lim}} if lim else {})}}
                                    for req, lim in containers]}}


# studio-25 as provisioned before CPU was reserved by limit: quota 4, pods limited to 10.7.
STUDIO_25_PODS = [pod("wl-studio-25", ("2", "6"), ("20m", "200m")), pod("wl-studio-25", ("250m", "1")),
                  pod("wl-studio-25", ("250m", "1")), pod("wl-studio-25", ("250m", "1")),
                  pod("wl-studio-25", ("50m", "500m")), pod("wl-studio-25", ("20m", "500m"), ("10m", "500m"))]


def fake_runner(pods):
    def fake_run(argv, **kw):
        if argv[0] == "findmnt":
            return subprocess.CompletedProcess(argv, 0, FINDMNT, "")
        key = tuple(argv[3:5])
        body = {"items": pods} if key == ("get", "pods") else CLUSTER[key]
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
    return fake_run


class Capacity(unittest.TestCase):
    def verdict(self, memory, size_mib, cpu="4", pods=()):
        wl = {"metadata": {"name": "studio-a"},
              "spec": {"resourceProfile": {"cpu": cpu, "memory": memory},
                       "mediaExchange": {"domain": "studio-a", "sizeMiB": size_mib}}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as fh:
            yaml.safe_dump(wl, fh)
            fh.flush()
            out = io.StringIO()
            with mock.patch.object(capacity.subprocess, "run", side_effect=fake_runner(list(pods))), \
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

    def test_another_workload_counts_by_its_live_cpu_limits_when_its_quota_is_lower(self):
        # studio-25's quota says 4, but its pods are limited to 10.7: 16 - 10.7 = 5.3 free.
        rc, v = self.verdict("6Gi", 657, cpu="11", pods=STUDIO_25_PODS)
        self.assertEqual(v["other_workloads_cpu"], {"studio-25": 10.7})
        self.assertEqual(v["free"]["cpu"], 5.3)
        self.assertFalse(v["fits"])
        self.assertEqual(rc, 1)

    def test_a_quota_higher_than_the_live_limits_still_counts(self):
        # Without pods (stopped, or still starting), the quota is the reservation.
        rc, v = self.verdict("6Gi", 657, cpu="11")
        self.assertEqual(v["other_workloads_cpu"], {"studio-25": 4})
        self.assertEqual(v["free"]["cpu"], 12)
        self.assertTrue(v["fits"])

    def test_platform_services_count_by_request_and_its_own_pods_not_at_all(self):
        pods = [pod("dmf-identity", ("500m", "2")), pod("wl-studio-a", ("2", "6"))]
        rc, v = self.verdict("6Gi", 657, cpu="11", pods=pods)
        self.assertEqual(v["free"]["cpu"], 16 - 4 - 0.5)
        self.assertNotIn("studio-a", v["other_workloads_cpu"])


if __name__ == "__main__":
    unittest.main()
