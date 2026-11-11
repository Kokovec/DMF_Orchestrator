"""ansible/scripts/storage.py, with kubectl and statvfs faked."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

SCRIPTS = Path(__file__).resolve().parents[3] / "ansible" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("storage", SCRIPTS / "storage.py")
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
GIB = 2**30


def pvc(name, size):
    return {"metadata": {"name": name}, "spec": {"resources": {"requests": {"storage": size}}}}


class Storage(unittest.TestCase):
    def verdict(self, volumes, live, free_gib=500):
        wl = {"metadata": {"name": "a"}, "spec": {"functions": [{"kind": "mxl", "name": "cam"},
                                                                {"kind": "switchframe", "name": "vm", "volumes": volumes}]}}
        def fake_run(argv, **kw):
            if "configmap" in argv:
                body = {"data": {"config.json": json.dumps({"nodePathMap": [{"paths": ["/disk"]}]})}}
            else:
                self.assertEqual(argv[3:7], ["-n", "wl-a", "get", "pvc"])
                body = {"items": live}
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as fh:
            yaml.safe_dump(wl, fh)
            fh.flush()
            out = io.StringIO()
            with mock.patch.object(storage.sys.modules["capacity"].subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(storage.os, "statvfs", return_value=SimpleNamespace(f_bavail=free_gib, f_frsize=GIB)), \
                    mock.patch.object(storage.sys, "argv", ["storage.py", fh.name, "kubeconfig"]), redirect_stdout(out):
                rc = storage.main()
        return rc, json.loads(out.getvalue())

    def test_matching_sizes_pass(self):
        rc, v = self.verdict({"stateGi": 11, "cacheGi": 18}, [pvc("vm-state", "11Gi"), pvc("vm-cache", "18Gi")])
        self.assertEqual((rc, v["problems"], v["new_gib"]), (0, [], 0))

    def test_shrinking_and_growing_are_refused_with_both_sizes_and_ways_out(self):
        rc, v = self.verdict({"stateGi": 21, "cacheGi": 20}, [pvc("vm-state", "71Gi"), pvc("vm-cache", "18Gi")])
        self.assertEqual(rc, 1)
        self.assertEqual(len(v["problems"]), 2)
        self.assertIn("vm-state: the design asks for 21 GiB, but the live volume is 71 GiB", v["problems"][1])
        self.assertIn("vm-cache: the design asks for 20 GiB, but the live volume is 18 GiB", v["problems"][0])
        for p in v["problems"]:
            self.assertIn("Restore the value", p)
            self.assertIn("make finalise WORKLOAD=a and deploy again", p)

    def test_recordings_size_is_checked(self):
        rc, v = self.verdict({"stateGi": 5, "recordingsGi": 10}, [pvc("vm-state", "5Gi"), pvc("vm-recordings", "5Gi")])
        self.assertEqual(rc, 1)
        self.assertIn("vm-recordings", v["problems"][0])

    def test_turning_recording_off_with_a_live_recordings_volume_is_refused(self):
        rc, v = self.verdict({"stateGi": 5, "cacheGi": 18},
                             [pvc("vm-state", "5Gi"), pvc("vm-cache", "18Gi"), pvc("vm-recordings", "10Gi")])
        self.assertEqual(rc, 1)
        self.assertEqual(len(v["problems"]), 1)
        p = v["problems"][0]
        self.assertIn("vm-recordings: the design turns recording off, but the live recordings volume (10 GiB) exists", p)
        self.assertIn("Keep recording on in the design", p)
        self.assertIn("make finalise WORKLOAD=a, which archives the recordings, and deploy again", p)
        # Recording off with no recordings volume is fine.
        self.assertEqual(self.verdict({"stateGi": 5, "cacheGi": 18}, [pvc("vm-state", "5Gi"), pvc("vm-cache", "18Gi")])[0], 0)

    def test_new_volumes_must_fit_the_free_disk(self):
        rc, v = self.verdict({"stateGi": 400, "cacheGi": 101}, [], free_gib=500)
        self.assertEqual(rc, 1)
        self.assertIn("New volumes total 501 GiB, more than the 500 GiB free under /disk", v["problems"][0])
        self.assertEqual(self.verdict({"stateGi": 400, "cacheGi": 100}, [], free_gib=500)[0], 0)

    def test_only_new_volumes_count_against_the_disk(self):
        rc, v = self.verdict({"stateGi": 400, "recordingsGi": 2}, [pvc("vm-state", "400Gi")], free_gib=1)
        self.assertEqual((rc, v["new_gib"]), (1, 2))
        self.assertEqual(self.verdict({"stateGi": 400}, [pvc("vm-state", "400Gi")], free_gib=1)[0], 0)

    def test_hand_written_workload_is_skipped(self):
        wl = {"metadata": {"name": "n"}, "spec": {"functions": [{"kind": "switchframe", "name": "vm"}]}}
        self.assertIsNone(storage.wanted_volumes(wl))


if __name__ == "__main__":
    unittest.main()
