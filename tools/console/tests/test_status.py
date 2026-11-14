"""Status checks against fakes for systemctl, kubectl, findmnt, curl, the toolkit API and the
Keycloak probe (spec 05, Tests: Status), and that a status read never touches secrets."""
import asyncio
import json
import os
import subprocess
import sys
import time
import unittest

from aiohttp import web

from tools.console.status import Status, mount_unit
from tools.console.tests import helpers
from tools.console.tests.helpers import Audit, FakeRepo, facility_down, facility_up

os.chmod(helpers.FAKEBIN, 0o755)


EXPORTED = {"apiVersion": "dmf.local/v1alpha1", "kind": "FacilityExport", "name": "local",
            "capabilities": {"mxl": True, "gpu": False}, "limits": {"domainsPerWorkload": 1}}


async def toolkit(port, designs=True, profile=EXPORTED):
    """A fake toolkit API: studio-a publishes, new-show has two problems. profile: what
    GET /api/facility answers ({} when the server runs without one), or None for no such route."""
    async def listing(request):
        return web.json_response({"workloads": [
            {"id": "studio-a", "name": "Studio A", "revision": 4, "updatedAt": "x"},
            {"id": "new-show", "name": "new-show", "revision": 1, "updatedAt": "x"}] if designs else []})

    async def published(request):
        if request.match_info["id"] == "studio-a":
            return web.Response(text="<workload/>", content_type="application/xml")
        return web.json_response({"error": "revision 1 is not publishable",
                                  "details": ["sw1: needs a director", "no output"]}, status=422)

    async def facility(request):  # as the toolkit's server/app.ts answers
        if not profile:
            return web.json_response({})
        return web.json_response({"name": profile["name"], "capabilities": profile["capabilities"],
                                  "limits": profile.get("limits", {})})

    app = web.Application()
    app.add_routes([web.get("/api/workloads", listing), web.get(r"/api/workloads/{id}/published.xml", published)])
    if profile is not None:
        app.add_routes([web.get("/api/facility", facility)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


class StatusTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = FakeRepo()
        self.port = helpers.free_port()
        self.status = Status(self.repo.root, toolkit_url=f"http://localhost:{self.port}", timeout=1.0,
                             env=self.repo.env(), systemd_dir=self.repo.systemd, distro="Test-Distro", tools_bin=False)
        self.toolkit = None
        (self.repo.root / ".state" / "facility-local.json").write_text(json.dumps(EXPORTED))

    async def asyncTearDown(self):
        if self.toolkit:
            await self.toolkit.cleanup()
        self.repo.cleanup()

    async def collect(self):
        Audit.start()
        try:
            return await self.status.collect()
        finally:
            self.events = Audit.stop()

    def by_id(self, snap):
        return {w["id"]: w for w in snap["workloads"]["items"]}

    async def test_running_facility(self):
        self.repo.scenario(facility_up())
        self.toolkit = await toolkit(self.port)
        snap = await self.collect()
        f = snap["facility"]
        self.assertEqual((f["k3s"]["state"], f["k3s"]["text"]), ("ok", "running"))
        self.assertEqual(f["kubernetes"]["state"], "ok")
        self.assertEqual((f["keycloak"]["state"], f["keycloak"]["text"]), ("ok", "ready"))
        self.assertEqual((f["nmos"]["state"], f["nmos"]["text"]), ("ok", "up"))
        self.assertEqual(f["mount"]["state"], "ok")
        self.assertEqual(f["profile"]["state"], "ok")
        # capacity.py --facility: platform 0.7 + studio-a max(quota 11, limits 11) = 11.7 CPU;
        # memory 7 GiB quota + 1 GiB platform + 0.5 GiB Flow Domain.
        cap = f["capacity"]["data"]
        self.assertEqual(cap["reserved"], {"cpu": 11.7, "memory_gib": 8.5})
        self.assertEqual(cap["allocatable"], {"cpu": 16.0, "memory_gib": 20.0})
        self.assertEqual(cap["workloads_cpu"], {"studio-a": 11.0})
        self.assertIn("CPU reserved 11.7 / 16", f["capacity"]["text"])

        w = self.by_id(snap)
        self.assertEqual(sorted(w), ["old-one", "studio-a"])  # news-studio: hand-written, not deployed
        a = w["studio-a"]
        self.assertEqual((a["kind"], a["revision"], a["state"], a["cpu"]), ("compiled", 3, "on air", "11"))
        self.assertEqual(a["pods"], {"ready": 2, "total": 2})
        self.assertEqual(a["reconciler"], {"pending": 0, "failed": 0, "api_up": 1})
        self.assertEqual(w["old-one"]["kind"], "no file")  # only in allocations.json
        self.assertIn("no workload old-one", w["old-one"]["errors"][0])

        d = {x["id"]: x for x in snap["designs"]["items"]}
        # The deployed revision is the namespace label (2), not the compiled file's header (3): an
        # import that wasn't provisioned changes the file but not what runs.
        self.assertEqual(a["deployed_revision"], 2)
        self.assertEqual(d["studio-a"], {"id": "studio-a", "name": "Studio A", "revision": 4, "deployed": True,
                                         "deployed_revision": 2, "newer": True, "publishable": True})
        self.assertEqual((d["new-show"]["publishable"], d["new-show"]["reasons"]),
                         (False, ["sw1: needs a director", "no output"]))
        self.assertFalse(d["new-show"]["deployed"])
        self.assertEqual(snap["designs"]["profile"], {"state": "ok", "text": "local", "name": "local"})

        runs = snap["archive"]["items"]
        self.assertEqual([(r["id"], r["source_xml"]) for r in runs], [("studio-a", True), ("news-studio", False)])
        self.assertEqual(runs[0]["time"], "2026-11-12 00:45 UTC")
        self.assertNoSecrets()

    async def test_states(self):
        cases = {
            "failed settings": (dict(metrics=helpers.METRICS_ON_AIR.replace("failed{workload=\"studio-a\"} 0",
                                                                            "failed{workload=\"studio-a\"} 2")),
                                "running, 2 settings failed"),
            "pending": (dict(metrics=helpers.METRICS_ON_AIR.replace("pending{workload=\"studio-a\"} 0",
                                                                    "pending{workload=\"studio-a\"} 1")), "running"),
            "api down": (dict(metrics=helpers.METRICS_ON_AIR.replace("api_up{workload=\"studio-a\"} 1",
                                                                     "api_up{workload=\"studio-a\"} 0")), "running"),
            "pods not ready": (dict(mixer_ready=False), "deployed, stopped"),
        }
        for name, (kw, state) in cases.items():
            with self.subTest(name):
                self.repo.scenario(facility_up(**kw))
                snap = await self.collect()
                self.assertEqual(self.by_id(snap)["studio-a"]["state"], state)

    async def test_facility_down(self):
        self.repo.scenario(facility_down())
        # studio-a's Flow Domain unit is still installed: it was provisioned, then make stop.
        (self.repo.systemd / mount_unit("/var/lib/dmf/mxl/studio-a")).write_text("[Mount]\n")
        os.utime(self.repo.root / ".state" / "facility-local.json", (1_000, 1_000))  # older than the profile source
        started = time.monotonic()
        snap = await self.collect()
        self.assertLess(time.monotonic() - started, 5)
        f = snap["facility"]
        self.assertEqual((f["k3s"]["state"], f["k3s"]["text"]), ("bad", "inactive"))
        for k in ("kubernetes", "nmos", "capacity"):
            self.assertEqual(f[k]["state"], "unknown", k)
        self.assertIn("was refused", f["kubernetes"]["error"])
        self.assertEqual(f["keycloak"]["state"], "bad")
        self.assertIn("172.24.0.9", f["keycloak"]["error"])  # eth0, as facility.sh falls back to
        self.assertEqual((f["mount"]["state"], f["mount"]["text"]), ("bad", "not mounted"))
        self.assertEqual((f["profile"]["state"], f["profile"]["text"]), ("bad", "older than facility/local.yaml"))
        w = self.by_id(snap)
        self.assertEqual((w["studio-a"]["state"], w["studio-a"]["deployed_from"]), ("deployed, stopped", "Flow Domain unit"))
        self.assertNotIn("news-studio", w)
        # Deployed, a hand-written workload is listed like any other.
        (self.repo.systemd / mount_unit("/var/lib/dmf/mxl/news-studio")).write_text("[Mount]\n")
        w = self.by_id(await self.collect())
        self.assertEqual((w["news-studio"]["kind"], w["news-studio"]["state"]), ("hand-written", "deployed, stopped"))
        self.assertIn("was refused", snap["workloads"]["error"])
        self.assertEqual((snap["designs"]["state"], snap["designs"]["text"]),
                         ("bad", f"toolkit API: not reachable on :{self.port}"))
        self.assertEqual(snap["archive"]["state"], "ok")
        self.assertNoSecrets()

    async def test_timeouts_dont_block_other_checks(self):
        sc = facility_up()
        sc["kubectl"] = [dict(r, sleep=5) for r in sc["kubectl"]]  # the API server hangs
        sc["curl"] = [{"args": [], "sleep": 5}]  # whichever address it falls back to
        self.repo.scenario(sc)
        started = time.monotonic()
        snap = await self.collect()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 4.5)  # 1 s timeout per call; checks run side by side
        f = snap["facility"]
        for k in ("kubernetes", "nmos", "capacity", "keycloak"):
            self.assertEqual(f[k]["state"], "unknown", k)
            self.assertIn("timed out", f[k]["error"], k)
        self.assertEqual(f["k3s"]["state"], "ok")
        self.assertEqual(f["mount"]["state"], "ok")
        self.assertEqual(snap["archive"]["state"], "ok")
        self.assertIn("timed out", snap["workloads"]["error"])
        # Timed-out fakes are killed, not left behind.
        out = subprocess.run(["pgrep", "-f", str(self.repo.bin)], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "")

    async def test_deployed_without_revision_label(self):
        sc = facility_up()
        ns = next(r for r in sc["kubectl"] if r["args"] == ["get ns -l dmf.ebu.ch/workload"])
        ns["stdout"] = json.dumps({"items": [{"metadata": {"name": "wl-studio-a",
                                                           "labels": {"dmf.ebu.ch/workload": "studio-a"}}}]})
        self.repo.scenario(sc)
        self.toolkit = await toolkit(self.port)
        snap = await self.collect()
        d = {x["id"]: x for x in snap["designs"]["items"]}["studio-a"]
        self.assertEqual((d["deployed"], d["deployed_revision"]), (True, None))
        self.assertNotIn("newer", d)

    async def test_toolkit_profile(self):
        self.repo.scenario(facility_up())
        cases = [
            ({}, "bad", "none: the toolkit offers options this facility can't run"),
            ({**EXPORTED, "capabilities": {"mxl": True, "gpu": True}}, "bad", "local, not the one exported here"),
            ({**EXPORTED, "name": "other"}, "bad", "other, not the one exported here"),
            (None, "unknown", None),
        ]
        for profile, state, text in cases:
            with self.subTest(profile=profile):
                self.toolkit = await toolkit(self.port, profile=profile)
                try:
                    snap = await self.collect()
                finally:
                    await self.toolkit.cleanup()
                    self.toolkit = None
                got = snap["designs"]["profile"]
                self.assertEqual(snap["designs"]["state"], "ok")  # the designs still list
                self.assertEqual((got["state"], got.get("text")), (state, text))
                if state == "bad":
                    self.assertIn("fix", got)

    async def test_toolkit_hangs(self):
        self.repo.scenario(facility_up())

        async def slow(request):
            await asyncio.sleep(5)
            return web.json_response({"workloads": []})
        app = web.Application()
        app.add_routes([web.get("/api/workloads", slow)])
        self.toolkit = web.AppRunner(app)
        await self.toolkit.setup()
        await web.TCPSite(self.toolkit, "127.0.0.1", self.port).start()
        snap = await self.collect()
        self.assertEqual(snap["designs"]["state"], "bad")
        self.assertEqual(snap["facility"]["k3s"]["state"], "ok")

    async def test_mount_states(self):
        sc = facility_up()
        sc["findmnt"][0]["stdout"] = "/dev/sdd /\n/dev/sdd /\n"
        self.repo.scenario(sc)
        m = await self.status.mount()
        self.assertEqual((m["state"], m["stacked"]), ("ok", 2))
        sc["findmnt"][0]["stdout"] = "C:\\ /\n"
        self.repo.scenario(sc)
        self.assertEqual((await self.status.mount())["state"], "bad")
        self.status.distro = ""
        self.assertEqual((await self.status.mount())["state"], "unknown")

    def test_mount_unit_escaping(self):
        out = subprocess.run(["systemd-escape", "--path", "--suffix=mount", "/var/lib/dmf/mxl/test-studio"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            self.skipTest("systemd-escape not available")
        self.assertEqual(mount_unit("/var/lib/dmf/mxl/test-studio"), out.stdout.strip())

    def assertNoSecrets(self):
        opened = [e[1] for e in self.events if e[0] == "open"]
        ran = [e[1] for e in self.events if e[0] == "exec"] + self.repo.calls()
        for path in opened:
            for bad in helpers.FORBIDDEN_PATHS:
                self.assertNotIn(bad, path, f"a status read opened {path}")
        for argv in ran:
            text = " ".join(argv)
            for bad in helpers.FORBIDDEN_PATHS + helpers.FORBIDDEN_ROUTES:
                self.assertNotIn(bad, text, f"a status read ran {argv}")
            if "exec" in argv:  # the only thing run inside a pod is a read of the reconciler's metrics
                self.assertEqual(argv[argv.index("--") + 1:][-1].rsplit("/", 1)[-1], "metrics", argv)
        self.assertEqual(Audit.writes(self.events), [], "a status read opened a file for writing")
        self.assertTrue(opened, "the audit hook saw nothing: the check is vacuous")


class CapacityCliTest(unittest.TestCase):
    """capacity.py --facility counts what the plan counts: free CPU and memory for a workload that
    isn't deployed equal the facility's free totals."""

    def test_facility_matches_plan(self):
        repo = FakeRepo()
        try:
            repo.scenario(facility_up())
            env = repo.env()
            script = str(repo.root / "ansible" / "scripts" / "capacity.py")
            kc = str(repo.root / ".state" / "kubeconfig")
            fac = json.loads(subprocess.run([sys.executable, script, "--facility", kc], env=env,
                                            capture_output=True, text=True, check=True).stdout)
            wl = repo.root / "new.yaml"
            wl.write_text("metadata: {name: new}\nspec:\n  resourceProfile: {cpu: '2', memory: 1Gi}\n"
                          "  mediaExchange: {domain: new, sizeMiB: 256}\n")
            plan = subprocess.run([sys.executable, script, str(wl), kc], env=env, capture_output=True, text=True)
            self.assertEqual(plan.returncode, 0, plan.stderr)
            verdict = json.loads(plan.stdout)
            self.assertEqual(verdict["free"], fac["free"])
            self.assertEqual(verdict["allocatable"], fac["allocatable"])
            self.assertEqual(verdict["other_workloads_cpu"], fac["workloads_cpu"])
            self.assertEqual(verdict["other_flow_domains_mib"], fac["flow_domains_mib"])
            self.assertEqual(verdict, {
                "workload": "new", "fits": True,
                "requested": {"cpu": 2.0, "memory_gib": 1.25, "flow_domain_mib": 256},
                "free": {"cpu": 4.3, "memory_gib": 11.5},
                "other_workloads_cpu": {"studio-a": 11.0}, "other_flow_domains_mib": {"studio-a": 512},
                "allocatable": {"cpu": 16.0, "memory_gib": 20.0}})
            # Re-planning studio-a itself skips its own reservation and Flow Domain, as before.
            sa = repo.root / "sa.yaml"
            sa.write_text("metadata: {name: studio-a}\nspec:\n  resourceProfile: {cpu: '11', memory: 7Gi}\n"
                          "  mediaExchange: {domain: studio-a, sizeMiB: 512}\n")
            verdict = json.loads(subprocess.run([sys.executable, script, str(sa), kc], env=env,
                                                capture_output=True, text=True).stdout)
            self.assertEqual((verdict["fits"], verdict["free"]), (True, {"cpu": 15.3, "memory_gib": 19.0}))
        finally:
            repo.cleanup()


if __name__ == "__main__":
    unittest.main()
