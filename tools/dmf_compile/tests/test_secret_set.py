"""scripts/secret-set.py (make secret-set): mode 600, merges, never prints the value."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "secret-set.py"


class SecretSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "secrets"

    def run_set(self, workload, name, value):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), str(self.dir), workload, name],
                              input=value + "\n", capture_output=True, text=True)

    def test_writes_mode_600_and_merges(self):
        r = self.run_set("studio-io", "venue-srt", "a-long-srt-passphrase")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("a-long-srt-passphrase", r.stdout + r.stderr)
        self.assertEqual(self.run_set("studio-io", "rtmp-key", "abcd-efgh").returncode, 0)
        path = self.dir / "studio-io.yaml"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(yaml.safe_load(path.read_text()), {"venue-srt": "a-long-srt-passphrase", "rtmp-key": "abcd-efgh"})
        self.assertEqual(self.run_set("studio-io", "rtmp-key", "changed").returncode, 0)
        self.assertEqual(yaml.safe_load(path.read_text())["rtmp-key"], "changed")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["studio-io.yaml"], "no temporary file left")

    def test_refuses_bad_names_and_empty_values(self):
        self.assertEqual(self.run_set("studio-io", "a/b", "x").returncode, 2)
        self.assertEqual(self.run_set("Bad_Id", "n", "x").returncode, 2)
        self.assertEqual(self.run_set("studio-io", "n", "").returncode, 1)
        self.assertFalse((self.dir / "studio-io.yaml").exists())


if __name__ == "__main__":
    unittest.main()
