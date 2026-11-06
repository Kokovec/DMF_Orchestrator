"""The generated files, against golden copies and against what reads them: yamllint, the awk lines
in scripts/, the assertions in workload-plan.yml (run by ansible-playbook) and helm template.

UPDATE_GOLDEN=1 rewrites the golden files.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

import yaml

from .util import FULL_SETTINGS_SECRETS, GOLDEN, REPO, Doc, RepoTest, full_settings, require_tool

TOOLS_ENV = dict(os.environ, PATH=f"{REPO / '.tools' / 'bin'}:{os.environ.get('PATH', '')}")


def awk_program(script, var):
    """The awk program of `VAR=$(awk '...' ...)` in a repo script, so the test runs the real line."""
    text = (REPO / "scripts" / script).read_text()
    m = re.search(rf"^\s*{var}=\$\(awk '([^']+)'", text, re.M)
    assert m, f"{var}=$(awk ...) not found in scripts/{script}"
    return m.group(1)


class Golden(RepoTest):
    def golden(self, name, text):
        path = GOLDEN / name
        if os.environ.get("UPDATE_GOLDEN"):
            path.write_text(text)
        self.assertEqual(text, path.read_text(), f"differs from {path}; UPDATE_GOLDEN=1 rewrites it")

    def test_make_import_studio_a(self):
        rc, out, err = self.repo.cli("compile", "examples/studio-a.workload.xml")
        self.assertEqual((rc, err), (0, ""))
        self.golden("studio-a.import.txt", out)
        wl = self.repo.path / "workloads"
        self.golden("studio-a.yaml", (wl / "studio-a.yaml").read_text())
        self.golden("studio-a.config.json", (wl / "studio-a.config.json").read_text())
        self.assertEqual((wl / "studio-a.source.xml").read_bytes(), (REPO / "examples" / "studio-a.workload.xml").read_bytes())

    def test_reimport_is_identical(self):
        self.repo.cli("compile", "examples/studio-a.workload.xml")
        first = (self.repo.path / "workloads" / "studio-a.yaml").read_bytes()
        rc, out, _ = self.repo.cli("compile", "examples/studio-a.workload.xml")
        self.assertEqual(rc, 0)
        self.assertIn("allocation (kept): uid/gid 20002, quicPort 8444, srtPort 6465", out)
        self.assertEqual((self.repo.path / "workloads" / "studio-a.yaml").read_bytes(), first)

    def test_full_settings(self):
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS)
        r = self.repo.compile_and_write(full_settings().bytes(), name="full-settings.workload.xml")
        self.golden("full-settings.yaml", r.yaml_text)
        self.golden("full-settings.config.json", r.config_text)


class Consumers(RepoTest):
    """Runs on studio-a and on full-settings."""

    def setUp(self):
        super().setUp()
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS)
        self.files = {}
        for doc in (Doc(), full_settings()):
            r = self.repo.compile_and_write(doc.bytes())
            self.files[r.workload_id] = r

    def test_yamllint(self):
        yamllint = require_tool(self, "yamllint")
        for wid in self.files:
            for config in ("{extends: relaxed, rules: {line-length: disable}}", "{extends: default, rules: {line-length: disable, document-start: disable}}"):
                with self.subTest(wid=wid, config=config):
                    p = subprocess.run([yamllint, "-s", "-d", config, str(self.repo.path / "workloads" / f"{wid}.yaml")],
                                       capture_output=True, text=True)
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_awk_lines(self):
        require_tool(self, "awk")
        for script, var, expect in (("windows-browser.sh", "HOST", lambda wid: f"{wid}.dmf.test"),
                                    ("facility.sh", "MIXER", lambda wid: "vision-mixer")):
            prog = awk_program(script, var)
            for wid in self.files:
                with self.subTest(script=script, wid=wid):
                    out = subprocess.run(["awk", prog, str(self.repo.path / "workloads" / f"{wid}.yaml")],
                                         capture_output=True, text=True, check=True).stdout
                    self.assertEqual(out, expect(wid) + "\n")

    def test_awk_lines_survive_awkward_text(self):
        d = Doc()
        d.root.find("{urn:dmf:workload:v1}description").text = "line one\nhostname: evil.example\n  - name: x\n\tkind: mixer"
        d.fn("cam1").set("name", "- name: x")
        d.rename("awkward")
        self.repo.compile_and_write(d.bytes())
        path = str(self.repo.path / "workloads" / "awkward.yaml")
        host = subprocess.run(["awk", awk_program("windows-browser.sh", "HOST"), path], capture_output=True, text=True).stdout
        mixer = subprocess.run(["awk", awk_program("facility.sh", "MIXER"), path], capture_output=True, text=True).stdout
        self.assertEqual((host, mixer), ("awkward.dmf.test\n", "vision-mixer\n"))

    def test_text_the_awk_line_would_misread_is_refused(self):
        d = Doc()
        d.fn("cam1").set("name", "kind: switchframe")
        problems = self.repo.problems(d.bytes())
        self.assertEqual([(p.rule, p.where) for p in problems], [(8, "workload: generated YAML")])
        self.assertIn("scripts/facility.sh would read the mixer as ['cam1', 'cam1', 'vision-mixer']", problems[0].message)

    # -- workload-plan.yml ---------------------------------------------------------------------
    def plan(self, wid, others=()):
        """Runs the plan playbook's own validation tasks against workloads/<wid>.yaml."""
        ansible = require_tool(self, "ansible-playbook")
        play = yaml.safe_load((REPO / "ansible" / "playbooks" / "workload-plan.yml").read_text())[0]
        tasks = {t["name"]: t for t in play["tasks"]}
        names = ["Load workload design", "Validate design", "Assert unique workload identity"]
        for n in names:
            self.assertIn(n, tasks, f"workload-plan.yml has no task {n!r}")
        test_play = [{"name": "plan assertions", "hosts": "localhost", "connection": "local", "gather_facts": False,
                      "vars": play["vars"],
                      "tasks": [tasks[names[0]], tasks[names[1]],
                                {"name": "Other workloads (what kubectl would list)",
                                 "ansible.builtin.set_fact": {"others": {"stdout_lines": list(others)}}},
                                tasks[names[2]]]}]
        with tempfile.NamedTemporaryFile("w", suffix=".yml", dir=self.repo.path, delete=False) as fh:
            yaml.safe_dump(test_play, fh)
        env = dict(TOOLS_ENV, ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                   ANSIBLE_NOCOLOR="1", ANSIBLE_CONFIG=str(self.repo.path / "ansible.cfg"))
        (self.repo.path / "ansible.cfg").write_text("[defaults]\n")
        p = subprocess.run([ansible, "-i", "localhost,", fh.name, "-e", f"workload={wid}",
                            "-e", f"dmf_repo_root={self.repo.path}"], capture_output=True, text=True, env=env, timeout=300)
        return p.returncode, p.stdout + p.stderr

    def test_plan_assertions_pass(self):
        news = yaml.safe_load((REPO / "workloads" / "news-studio.yaml").read_text())["spec"]["mediaExchange"]
        for wid in self.files:
            with self.subTest(wid=wid):
                rc, out = self.plan(wid, [f"{news['uid']} {news['domain']}"])
                self.assertEqual(rc, 0, out)
                self.assertRegex(out, r"ok=4 ")

    def test_plan_assertions_are_really_evaluated(self):
        path = self.repo.path / "workloads" / "studio-a.yaml"
        text = path.read_text()
        path.write_text(text.replace("uid: 20002, gid: 20002", "uid: 9999, gid: 9999"))
        rc, out = self.plan("studio-a")
        self.assertNotEqual(rc, 0)
        self.assertIn("is not a valid MediaWorkload design", out)
        path.write_text(text)
        rc, out = self.plan("studio-a", ["20002 other"])
        self.assertNotEqual(rc, 0)
        self.assertIn("uid or Flow Domain already used by another workload", out)

    # -- helm ------------------------------------------------------------------------------
    def mixer_values(self, wid):
        wl = yaml.safe_load((self.repo.path / "workloads" / f"{wid}.yaml").read_text())
        config = json.loads((self.repo.path / "workloads" / f"{wid}.config.json").read_text())
        mixer = next(f for f in wl["spec"]["functions"] if f["kind"] == "switchframe")
        labels = []
        for s in config["sources"]:
            label = next((c["body"]["label"] for c in s["apply"] if c["path"].endswith("/label")), None)
            if s["ref"].get("kind") == "mxl" and label:
                labels.append({"source": s["ref"]["key"], "label": label})
        values = {
            "workload": wid,
            "podSecurity": {"uid": wl["spec"]["mediaExchange"]["uid"], "gid": wl["spec"]["mediaExchange"]["gid"]},
            "configure": {"programSource": mixer.get("programSource", ""), "labels": labels},
            "mxl": {"domainPath": f"/var/lib/dmf/mxl/{wl['spec']['mediaExchange']['domain']}",
                    "sources": mixer["mxlSources"],
                    "output": {"enabled": mixer["programOutput"], "name": "program",
                               "videoFlow": {"id": mixer["flows"]["video"]}, "audioFlow": {"id": mixer["flows"]["audio"]}}},
            "switchframe": {"format": mixer["format"], "quicPort": wl["spec"]["access"]["quicPort"],
                            "srtPort": wl["spec"]["access"]["srtPort"], "extraArgs": mixer["extraArgs"],
                            "existingSecret": "switchframe-tokens", "resources": mixer["resources"],
                            "goMemLimit": mixer["goMemLimit"]},
            "persistence": {"size": f"{mixer['volumes']['stateGi']}Gi"},
            "ingress": {"hostname": wl["spec"]["access"]["hostname"]},
            "sso": {"issuerUrl": "https://keycloak.dmf.test/realms/dmf", "internalUrl": "http://keycloak:8080/realms/dmf",
                    "existingSecret": "sso-gateway"},
        }
        return wl, mixer, values

    def helm(self, chart, values):
        helm = require_tool(self, "helm")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=self.repo.path, delete=False) as fh:
            yaml.safe_dump(values, fh)
        p = subprocess.run([helm, "template", "t", str(REPO / "charts" / chart), "-f", fh.name],
                           capture_output=True, text=True, env=TOOLS_ENV, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_helm_template_switchframe(self):
        for wid in self.files:
            with self.subTest(wid=wid):
                wl, mixer, values = self.mixer_values(wid)
                out = self.helm("switchframe", values)
                srcs = ",".join(f"{s['video']}:{s['audio']}" for s in mixer["mxlSources"])
                for want in (f"--format={mixer['format']}", f"--mxl-sources={srcs}", f"--addr=:{wl['spec']['access']['quicPort']}",
                             f"--srt-listen=:{wl['spec']['access']['srtPort']}", "--mxl-output=program",
                             f"--allowed-origins=https://{wid}.dmf.test", f'value: "{mixer["goMemLimit"]}"',
                             f"memory: {mixer['resources']['limits']['memory']}", f"storage: {mixer['volumes']['stateGi']}Gi",
                             f'- "{mixer["programSource"]}"'):
                    self.assertIn(want, out)
                for arg in mixer["extraArgs"]:
                    self.assertIn(f"- {arg}\n", out)
                docs = [d for d in yaml.safe_load_all(out) if d]
                self.assertIn("Deployment", {d["kind"] for d in docs})

    def test_helm_template_test_sources(self):
        wl = yaml.safe_load((self.repo.path / "workloads" / "studio-a.yaml").read_text())
        mx = wl["spec"]["mediaExchange"]
        for f in wl["spec"]["functions"]:
            if f["kind"] not in ("mxl-testsrc", "mxl-syncsrc"):
                continue
            with self.subTest(f["name"]):
                out = self.helm("mxl-function", {
                    "workload": "studio-a", "role": "input", "podSecurity": {"uid": mx["uid"], "gid": mx["gid"]},
                    "mxl": {"domainPath": f"/var/lib/dmf/mxl/{mx['domain']}"},
                    "command": ["/usr/local/bin/mxl-gst-testsrc"],
                    "args": ["-p", f.get("pattern", "smpte"), "-t", f.get("overlayText", f["label"]), "-g", f["name"]],
                    "flowDefs": {"video.json": {"id": f["flows"]["video"]}, "audio.json": {"id": f["flows"]["audio"]}},
                    "resources": f["resources"]})
                self.assertIn(f["flows"]["video"], out)
                self.assertIn("runAsUser: 20002", out)


if __name__ == "__main__":
    unittest.main()
