"""plan_summary.py: counts as Terraform counts them, replaced resources, restarts found by
rendering the chart before and after, and no value from the plan in the summary."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import plan_summary as S  # noqa: E402

os.environ["PATH"] = f"{REPO / '.tools' / 'bin'}:{os.environ.get('PATH', '')}"

SF = {
    "chartHash": "aaa", "workload": "studio-a",
    "image": {"repository": "registry.local/switchframe", "tag": "dev"}, "imageId": "sha256:1",
    "mxl": {"domainPath": "/var/lib/dmf/mxl/studio-a",
            "sources": [{"video": "a0000001-0000-0000-0000-000000000001", "audio": "a0000001-0000-0000-0000-000000000002"}],
            "output": {"videoFlow": {"id": "x"}, "audioFlow": {"id": "y"}}},
    "switchframe": {"existingSecret": "switchframe-tokens"},
    "ingress": {"hostname": "studio-a.dmf.test"},
    "sso": {"issuerUrl": "https://keycloak.dmf.test/realms/dmf", "internalUrl": "http://keycloak:8080/realms/dmf",
            "existingSecret": "sso-gateway", "secretChecksum": "c1"},
    "config": {"revision": 3, "global": [{"method": "PUT", "path": "/api/audio/limiter", "body": {"threshold": -1}}]},
}


def release(address, before, after, unknown=None, actions=("update",)):
    def attrs(values):
        return None if values is None else {"name": "vision-mixer", "namespace": "wl-studio-a",
                                            "chart": str(REPO / "charts" / "switchframe"), "values": [yaml.safe_dump(values)]}
    return {"address": address, "mode": "managed", "type": "helm_release", "name": address.split(".")[1],
            "change": {"actions": list(actions), "before": attrs(before), "after": attrs(after),
                       "after_unknown": unknown or {}}}


def resource(address, actions, after=None):
    return {"address": address, "mode": "managed", "type": address.split(".")[0], "name": address.split(".")[1],
            "change": {"actions": list(actions), "before": {}, "after": after or {}, "after_unknown": {}}}


def changed(**edits):
    v = copy.deepcopy(SF)
    for path, value in edits.items():
        node = v
        *head, last = path.split("__")
        for k in head:
            node = node[k]
        node[last] = value
    return v


class Summary(unittest.TestCase):
    def setUp(self):
        if not shutil.which("helm"):
            if os.environ.get("DMF_REQUIRE_TOOLS"):
                self.fail("helm is required (DMF_REQUIRE_TOOLS=1) but not found")
            self.skipTest("helm not installed")

    def summarise(self, *changes):
        return S.summarise({"resource_changes": list(changes)}, "studio-a", {"fits": True}, 3)

    def test_settings_only_change_restarts_nothing(self):
        new = changed(config__global=[{"method": "PUT", "path": "/api/audio/limiter", "body": {"threshold": -3}}])
        s = self.summarise(release("helm_release.mixer", SF, new))
        self.assertEqual(s["counts"], {"add": 0, "change": 1, "destroy": 0})
        self.assertEqual((s["changed"], s["restarts"], s["replaced"]), (["helm_release.mixer"], [], []))

    def test_image_change_restarts_the_mixer(self):
        s = self.summarise(release("helm_release.mixer", SF, changed(imageId="sha256:2")))
        restarted = {r["resource"] for r in s["restarts"]}
        self.assertIn("Deployment/vision-mixer", restarted)
        self.assertTrue(all(r["address"] == "helm_release.mixer" for r in s["restarts"]))

    def test_sso_secret_change_restarts_only_the_gateway(self):
        s = self.summarise(release("helm_release.mixer", SF, changed(sso__secretChecksum="c2")))
        restarted = [r["resource"] for r in s["restarts"]]
        self.assertEqual(len(restarted), 1, restarted)
        self.assertNotEqual(restarted[0], "Deployment/vision-mixer")

    def test_chart_change_may_restart(self):
        s = self.summarise(release("helm_release.mixer", SF, changed(chartHash="bbb")))
        self.assertEqual([(r["resource"], r["why"]) for r in s["restarts"]], [(None, "its chart files changed: may restart")])

    def test_values_unknown_until_apply(self):
        for mask in ({"values": True}, {"values": [True]}, {"values": [False, True]}):
            s = self.summarise(release("helm_release.mixer", SF, SF, unknown=mask))
            self.assertIn("known only at apply", s["restarts"][0]["why"], mask)

    def test_known_values_as_terraform_marks_them(self):
        # As a live plan showed: the release's computed id and metadata are unknown, its values known.
        new = changed(config__global=[])
        s = self.summarise(release("helm_release.mixer", SF, new, unknown={"id": True, "metadata": True, "values": [False]}))
        self.assertEqual(s["restarts"], [])

    def test_render_failure_is_not_quoted(self):
        s = S.summarise({"resource_changes": [release("helm_release.mixer", SF, changed(imageId="x"))]}, "studio-a",
                        None, render=lambda *a: None)
        self.assertEqual(s["restarts"][0]["why"], "helm template failed: may restart")

    def test_counts_replaced_and_no_values(self):
        plan = {"resource_changes": [
            resource("kubernetes_namespace_v1.workload", ["update"], {"metadata": [{"labels": {"dmf.ebu.ch/revision": "4"}}]}),
            resource("kubernetes_secret_v1.workload_refs", ["delete", "create"], {"data": {"venue-srt": "CANARY-SECRET"}}),
            resource("random_password.invite", ["create"], {"result": "CANARY-PASSWORD"}),
            resource("keycloak_group.role[\"viewer\"]", ["delete"]),
            resource("kubernetes_limit_range_v1.defaults", ["no-op"]),
            {"address": "data.keycloak_user.member", "mode": "data", "type": "keycloak_user", "name": "member",
             "change": {"actions": ["read"], "before": None, "after": {"x": "CANARY-DATA"}}},
            release("helm_release.source[\"cam1\"]", None, SF, actions=("create",)),
        ]}
        s = S.summarise(plan, "studio-a", {"fits": True, "free": {"cpu": 5}}, 4)
        self.assertEqual(s["counts"], {"add": 3, "change": 1, "destroy": 2})
        self.assertEqual(s["replaced"], ["kubernetes_secret_v1.workload_refs"])
        self.assertEqual(s["created"], ["random_password.invite", "helm_release.source[\"cam1\"]"])
        self.assertEqual(s["destroyed"], ["keycloak_group.role[\"viewer\"]"])
        self.assertEqual(s["changed"], ["kubernetes_namespace_v1.workload"])
        self.assertEqual((s["workload"], s["revision"], s["capacity"]["fits"]), ("studio-a", 4, True))
        self.assertNotIn("CANARY", json.dumps(s))
        self.assertNotIn("switchframe-tokens", json.dumps(s))

    def test_written_mode_600(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.plan-summary.json"
            out.write_text("old")
            os.chmod(out, 0o644)
            S.write_private(str(out), {"a": 1})
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(out.read_text()), {"a": 1})
            self.assertEqual(os.listdir(d), ["x.plan-summary.json"])


if __name__ == "__main__":
    unittest.main()
