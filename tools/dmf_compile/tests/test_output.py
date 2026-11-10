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
        wl = self.repo.gen
        self.golden("studio-a.yaml", (wl / "studio-a.yaml").read_text())
        self.golden("studio-a.config.json", (wl / "studio-a.config.json").read_text())
        self.assertEqual((wl / "studio-a.source.xml").read_bytes(), (REPO / "examples" / "studio-a.workload.xml").read_bytes())

    def test_reimport_is_identical(self):
        self.repo.cli("compile", "examples/studio-a.workload.xml")
        first = (self.repo.gen / "studio-a.yaml").read_bytes()
        rc, out, _ = self.repo.cli("compile", "examples/studio-a.workload.xml")
        self.assertEqual(rc, 0)
        self.assertIn("allocation (kept): uid/gid 20002, quicPort 8444, srtPort 6465", out)
        self.assertEqual((self.repo.gen / "studio-a.yaml").read_bytes(), first)

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
                    p = subprocess.run([yamllint, "-s", "-d", config, str(self.repo.gen / f"{wid}.yaml")],
                                       capture_output=True, text=True)
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_awk_lines(self):
        require_tool(self, "awk")
        for script, var, expect in (("windows-browser.sh", "HOST", lambda wid: f"{wid}.dmf.test"),
                                    ("facility.sh", "MIXER", lambda wid: "vision-mixer")):
            prog = awk_program(script, var)
            for wid in self.files:
                with self.subTest(script=script, wid=wid):
                    out = subprocess.run(["awk", prog, str(self.repo.gen / f"{wid}.yaml")],
                                         capture_output=True, text=True, check=True).stdout
                    self.assertEqual(out, expect(wid) + "\n")

    def test_awk_lines_survive_awkward_text(self):
        d = Doc()
        d.root.find("{urn:dmf:workload:v1}description").text = "line one\nhostname: evil.example\n  - name: x\n\tkind: mixer"
        d.fn("cam1").set("name", "- name: x")
        d.rename("awkward")
        self.repo.compile_and_write(d.bytes())
        path = str(self.repo.gen / "awkward.yaml")
        host = subprocess.run(["awk", awk_program("windows-browser.sh", "HOST"), path], capture_output=True, text=True).stdout
        mixer = subprocess.run(["awk", awk_program("facility.sh", "MIXER"), path], capture_output=True, text=True).stdout
        self.assertEqual((host, mixer), ("awkward.dmf.test\n", "vision-mixer\n"))

    def test_text_that_misled_the_unanchored_awk_line(self):
        # Before stage 4 facility.sh matched "kind: switchframe" anywhere on a line.
        d = Doc()
        d.fn("cam1").set("name", "kind: switchframe")
        d.rename("misled")
        self.repo.compile_and_write(d.bytes())
        out = subprocess.run(["awk", awk_program("facility.sh", "MIXER"), str(self.repo.gen / "misled.yaml")],
                             capture_output=True, text=True).stdout
        self.assertEqual(out, "vision-mixer\n")

    # -- workload-plan.yml ---------------------------------------------------------------------
    def plan(self, wid, others=(), ports=()):
        """Runs the plan playbook's own validation tasks against the workload file scripts/workload-file.sh resolves."""
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
                                 "ansible.builtin.set_fact": {"others": {"stdout_lines": list(others)},
                                                              "other_ports": {"stdout_lines": list(ports)}}},
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
                rc, out = self.plan(wid, [f"{news['uid']} {news['domain']}"], ["8443", "6464"])
                self.assertEqual(rc, 0, out)
                self.assertRegex(out, r"ok=4 ")

    def test_plan_assertions_are_really_evaluated(self):
        path = self.repo.gen / "studio-a.yaml"
        text = path.read_text()
        path.write_text(text.replace("uid: 20002, gid: 20002", "uid: 9999, gid: 9999"))
        rc, out = self.plan("studio-a")
        self.assertNotEqual(rc, 0)
        self.assertIn("is not a valid MediaWorkload design", out)
        path.write_text(text)
        for others, ports in ((["20002 other"], []), (["20001 news-studio"], ["8444"]), ([], ["6465"])):
            with self.subTest(others=others, ports=ports):
                rc, out = self.plan("studio-a", others, ports)
                self.assertNotEqual(rc, 0)
                self.assertIn("uid 20002, Flow Domain studio-a or port 8444/6465 already used by another workload", out)

    def test_workload_file_resolver(self):
        script = str(self.repo.path / "scripts" / "workload-file.sh")
        run = lambda wid: subprocess.run([script, wid], capture_output=True, text=True)
        self.assertEqual(run("studio-a").stdout, f"{self.repo.gen / 'studio-a.yaml'}\n")
        self.assertEqual(run("news-studio").stdout, f"{self.repo.path / 'workloads' / 'news-studio.yaml'}\n")
        for wid, msg in (("nope", "no workload nope"), ("../etc", "is not valid")):
            p = run(wid)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(msg, p.stderr)
        (self.repo.gen / "news-studio.yaml").write_text("kind: MediaWorkload\n")
        p = run("news-studio")
        self.assertEqual(p.returncode, 1)
        self.assertIn("is both hand-written", p.stderr)

    # -- helm ------------------------------------------------------------------------------
    def mixer_values(self, wid):
        wl = yaml.safe_load((self.repo.gen / f"{wid}.yaml").read_text())
        config = json.loads((self.repo.gen / f"{wid}.config.json").read_text())
        mixer = next(f for f in wl["spec"]["functions"] if f["kind"] == "switchframe")
        values = {
            "workload": wid,
            "podSecurity": {"uid": wl["spec"]["mediaExchange"]["uid"], "gid": wl["spec"]["mediaExchange"]["gid"]},
            "config": config,
            "mxl": {"domainPath": f"/var/lib/dmf/mxl/{wl['spec']['mediaExchange']['domain']}",
                    "sources": mixer["mxlSources"],
                    "output": {"enabled": mixer["programOutput"], "name": "program",
                               "videoFlow": {"id": mixer["flows"]["video"]}, "audioFlow": {"id": mixer["flows"]["audio"]}}},
            "switchframe": {"format": mixer["format"], "quicPort": wl["spec"]["access"]["quicPort"],
                            "srtPort": wl["spec"]["access"]["srtPort"], "extraArgs": mixer["extraArgs"],
                            "existingSecret": "switchframe-tokens", "resources": mixer["resources"],
                            "goMemLimit": mixer["goMemLimit"]},
            "persistence": {"size": f"{mixer['volumes']['stateGi']}Gi"},
            "recordings": {"size": f"{mixer['volumes']['recordingsGi']}Gi" if "recordingsGi" in mixer["volumes"] else ""},
            "replayTmpfsSize": f"{mixer['volumes']['replayTmpfsMi']}Mi" if mixer["volumes"]["replayTmpfsMi"] else "",
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
                docs = [d for d in yaml.safe_load_all(out) if d]
                dep = next(d for d in docs if d["kind"] == "Deployment")
                sf = dep["spec"]["template"]["spec"]["containers"][0]
                access = wl["spec"]["access"]
                self.assertEqual(sf["args"], [
                    f"--addr=:{access['quicPort']}", "--http-fallback", "--http-addr=:8081", "--admin-addr=0.0.0.0:9090",
                    f"--allowed-origins=https://{wid}.dmf.test", f"--format={mixer['format']}", "--log-level=info",
                    "--cache-dir=/cache", "--replay-tmpfs-dir=/replay", f"--mxl-domain=/var/lib/dmf/mxl/{wid}",
                    f"--mxl-sources={srcs}", "--mxl-output=program",
                    "--mxl-output-video-def=/etc/switchframe/flows/output_video.json",
                    "--mxl-output-audio-def=/etc/switchframe/flows/output_audio.json",
                    f"--srt-listen=:{access['srtPort']}"] + mixer["extraArgs"])
                self.assertIn({"name": "GOMEMLIMIT", "value": mixer["goMemLimit"]}, sf["env"])
                self.assertEqual(sf["resources"], mixer["resources"])
                self.assertIn(f"storage: {mixer['volumes']['stateGi']}Gi", out)
                self.assertNotIn("lifecycle", sf, "the postStart hook is gone: the reconciler replaces it")
                # The reconciler reads the compiled config, unchanged, from the ConfigMap.
                rec = dep["spec"]["template"]["spec"]["containers"][1]
                self.assertEqual(rec["name"], "reconciler")
                self.assertEqual(rec["command"], ["python3", "-I", "/usr/local/bin/sf-reconciler"])
                self.assertFalse(rec["securityContext"]["allowPrivilegeEscalation"])
                # Its record of managed SRT callers is on the state volume, so it outlives the pod.
                self.assertIn({"name": "state", "mountPath": "/var/lib/switchframe"}, rec["volumeMounts"])
                cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "t-config")
                self.assertEqual(json.loads(cm["data"]["config.json"]), json.loads((self.repo.gen / f"{wid}.config.json").read_text()))
                mounts = {m["mountPath"] for m in sf["volumeMounts"]}
                pvcs = {d["metadata"]["name"]: d for d in docs if d["kind"] == "PersistentVolumeClaim"}
                if "recordingsGi" in mixer["volumes"]:
                    self.assertIn("/recordings", mounts)
                    self.assertEqual(pvcs["t-recordings"]["spec"]["resources"]["requests"]["storage"],
                                     f"{mixer['volumes']['recordingsGi']}Gi")
                else:
                    self.assertNotIn("/recordings", mounts)
                    self.assertNotIn("t-recordings", pvcs)
                # Prometheus reaches its metrics port.
                np = next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "t")
                self.assertIn({"port": 9103, "protocol": "TCP"}, np["spec"]["ingress"][2]["ports"])

    def test_helm_template_keeps_data_flows_and_quotes_args(self):
        _, mixer, values = self.mixer_values("studio-a")
        values["mxl"]["sources"][0]["data"] = "d0000000-0000-0000-0000-000000000001"
        values["switchframe"]["extraArgs"] = ["--scte35-webhook=http://h/x?a=b: c # d"]
        out = self.helm("switchframe", values)
        s0 = values["mxl"]["sources"][0]
        self.assertIn(f"--mxl-sources={s0['video']}:{s0['audio']}:{s0['data']},", out)
        dep = next(d for d in yaml.safe_load_all(out) if d and d["kind"] == "Deployment")
        args = dep["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(args[-1], "--scte35-webhook=http://h/x?a=b: c # d")
        self.assertTrue(all(isinstance(a, str) for a in args))

    def test_helm_template_sized_volumes_and_config(self):
        _, mixer, values = self.mixer_values("full-settings")
        values["cache"] = {"size": f"{mixer['volumes']['cacheGi']}Gi"}
        values["config"] = json.loads((self.repo.gen / "full-settings.config.json").read_text())
        docs = [d for d in yaml.safe_load_all(self.helm("switchframe", values)) if d]
        dep = next(d for d in docs if d["kind"] == "Deployment")
        cache = next(v for v in dep["spec"]["template"]["spec"]["volumes"] if v["name"] == "cache")
        self.assertEqual(cache, {"name": "cache", "persistentVolumeClaim": {"claimName": "t-cache"}})
        pvcs = {d["metadata"]["name"]: d["spec"]["resources"]["requests"]["storage"] for d in docs if d["kind"] == "PersistentVolumeClaim"}
        self.assertEqual(pvcs, {"t-state": "71Gi", "t-cache": "18Gi", "t-recordings": "100Gi"})
        replay = next(v for v in dep["spec"]["template"]["spec"]["volumes"] if v["name"] == "replay")
        self.assertEqual(replay, {"name": "replay", "emptyDir": {"medium": "Memory",
                                                                 "sizeLimit": f"{mixer['volumes']['replayTmpfsMi']}Mi"}})
        cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-config"))
        self.assertEqual(json.loads(cm["data"]["config.json"]), values["config"])

    def test_helm_template_replay_off_has_no_tmpfs(self):
        _, _, values = self.mixer_values("studio-a")
        values["replayTmpfsSize"] = ""
        docs = [d for d in yaml.safe_load_all(self.helm("switchframe", values)) if d]
        sf = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
        self.assertNotIn("replay", [v["name"] for v in sf["volumes"]])
        self.assertNotIn("--replay-tmpfs-dir=/replay", sf["containers"][0]["args"])
        self.assertNotIn("/replay", [m["mountPath"] for m in sf["containers"][0]["volumeMounts"]])

    def test_helm_template_without_cache_size_keeps_the_emptydir(self):
        _, _, values = self.mixer_values("studio-a")
        docs = [d for d in yaml.safe_load_all(self.helm("switchframe", values)) if d]
        dep = next(d for d in docs if d["kind"] == "Deployment")
        cache = next(v for v in dep["spec"]["template"]["spec"]["volumes"] if v["name"] == "cache")
        self.assertEqual(cache, {"name": "cache", "emptyDir": {}})
        self.assertEqual([d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"], ["t-state"])

    def test_helm_template_test_sources(self):
        wl = yaml.safe_load((self.repo.gen / "studio-a.yaml").read_text())
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
