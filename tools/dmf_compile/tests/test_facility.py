"""scripts/facility.sh start: a workload that isn't deployed fails at once, naming those that are."""
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KUBECTL = r'''#!/usr/bin/env bash
# Stub: "get namespace <ns>" answers from $STUB_DIR; "get namespaces -l ..." lists what is deployed.
if [ -e "$STUB_DIR/api-down" ]; then echo "The connection to the server was refused" >&2; exit 1; fi
if [ "$2" = namespace ]; then
  if [ -e "$STUB_DIR/ns-$3" ]; then echo "namespace/$3"; exit 0; fi
  echo "Error from server (NotFound): namespaces \"$3\" not found" >&2; exit 1
fi
if [ "$2" = namespaces ]; then cat "$STUB_DIR/deployed"; exit 0; fi
if [ "$3" = exec ]; then echo "$4" >> "$STUB_DIR/exec-targets"; echo '{"programSource":"mxl:x"}'; exit 0; fi
exit 1
'''


def stub(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class StartNotDeployed(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        (self.root / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "facility.sh", self.root / "scripts")
        shutil.copy(REPO / "scripts" / "workload-file.sh", self.root / "scripts")
        self.bin = self.root / ".tools" / "bin"
        self.bin.mkdir(parents=True)
        self.calls = self.root / "sudo-calls"
        stub(self.bin / "kubectl", KUBECTL)
        stub(self.bin / "sudo", f'#!/usr/bin/env bash\necho "$*" >> "{self.calls}"\n')
        stub(self.bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
        stub(self.bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")   # the wait loops take no time here
        (self.root / "deployed").write_text("news-studio studio-b ")

    def start(self, workload):
        env = {**os.environ, "STUB_DIR": str(self.root), "NO_BROWSER": "1"}
        t0 = time.monotonic()
        p = subprocess.run(["bash", str(self.root / "scripts" / "facility.sh"), "start", workload],
                           capture_output=True, text=True, env=env, timeout=60)
        return p, time.monotonic() - t0

    def test_not_deployed_fails_at_once_listing_the_deployed_ones(self):
        p, _ = self.start("studio-x")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("Workload studio-x is not deployed: namespace wl-studio-x doesn't exist.", p.stderr)
        self.assertIn("Deployed workloads: news-studio studio-b", p.stderr)
        self.assertNotIn("Waiting for", p.stdout)
        self.assertEqual(self.calls.read_text().split("\n")[0], "systemctl start k3s")

    def test_none_deployed(self):
        (self.root / "deployed").write_text("")
        p, _ = self.start("studio-x")
        self.assertEqual(p.returncode, 1)
        self.assertIn("Deployed workloads: none", p.stderr)

    def test_api_not_answering_is_not_reported_as_not_deployed(self):
        (self.root / "api-down").touch()
        p, _ = self.start("studio-x")
        self.assertEqual(p.returncode, 1)
        self.assertIn("did not answer within 2 minutes", p.stderr)
        self.assertNotIn("is not deployed", p.stderr)

    def test_deployed_workload_goes_on_to_wait_for_it(self):
        (self.root / "ns-wl-studio-b").touch()
        p, _ = self.start("studio-b")      # stops at resolving its file, past the namespace check
        self.assertNotIn("is not deployed", p.stderr)
        self.assertIn("no workload studio-b", p.stderr)

    def test_finds_the_mixer_on_a_kind_line_with_a_comment(self):
        # workloads/news-studio.yaml has one, and a hand-written file may always.
        (self.root / "ns-wl-news-studio").touch()
        (self.root / "workloads").mkdir()
        (self.root / "workloads" / "news-studio.yaml").write_text(
            "kind: MediaWorkload\nspec:\n  functions:\n    - name: cam1\n      kind: mxl-testsrc   # a camera\n"
            "    - name: vision-mixer\n      kind: switchframe          # the mixer\n")
        p, _ = self.start("news-studio")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("news-studio is on air.", p.stdout)
        self.assertEqual((self.root / "exec-targets").read_text().split(), ["deploy/vision-mixer"])


if __name__ == "__main__":
    unittest.main()
