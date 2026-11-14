"""The deploy flow (spec 05, Tests: Deploy flow), driven through the console's HTTP API and its
real terminal against a fake `make` that writes the two JSON files: a design needing one secret,
a refused design, stale or missing files, failures and retries, Redeploy, and what the lifecycle
buttons do to an open deploy. The console reads the files, never the terminal's output."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

from tools.console import flow as F
from tools.console.server import Console
from tools.console.tests.helpers import free_port

BASH = ("bash", "--norc", "--noprofile", "-i")
FAKEMAKE = Path(__file__).with_name("fakemake.py")
os.chmod(FAKEMAKE, 0o755)
TOOLKIT = "http://localhost:3001"
SOURCE = f"{TOOLKIT}/api/workloads/studio-a/published.xml"
SECRET_VALUE = "CANARY-passphrase-0123"

IMPORT_OK = {"ok": True, "id": "studio-a", "revision": 4, "warnings": ["ignored for now: placement.* is set"],
             "problems": [], "allocation": {"uid": 20002, "gid": 20002, "quicPort": 8444, "srtPort": 6465, "new": True}}
MISSING = {"ok": False, "id": "studio-a", "revision": 4, "warnings": [], "allocation": None, "problems": [
    {"rule": 6, "where": "vision-mixer: srt-out.srt.passphrase", "secret": "venue-srt",
     "message": "secret 'venue-srt' is not in .state/secrets/studio-a.yaml; run: make secret-set ..."}]}
REFUSED = {"ok": False, "id": "studio-a", "revision": 4, "warnings": [], "allocation": None, "problems": [
    {"rule": 4, "where": "vision-mixer: engine.format", "message": "720p with an MXL source"}]}
SUMMARY = {"revision": 4, "counts": {"add": 0, "change": 2, "destroy": 0}, "created": [], "destroyed": [],
           "changed": ["helm_release.mixer", "kubernetes_namespace_v1.workload"], "replaced": [], "restarts": [],
           "capacity": {"fits": True, "requested": {"cpu": 11}, "free": {"cpu": 15.3}}}


class FakeStatus:
    def __init__(self):
        self.deployed = []

    async def collect(self):
        return {"time": "2026-11-13T00:00:00+00:00", "facility": {},
                "workloads": {"items": [{"id": i, "deployed": True} for i in self.deployed]}}


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="dmf-console-flow-")
        self.root = Path(self.dir.name)
        self.repo = self.root / "repo"
        (self.repo / ".state").mkdir(parents=True)
        bin_ = self.root / "bin"
        bin_.mkdir()
        (bin_ / "make").symlink_to(FAKEMAKE)
        self.log = self.root / "make.log"
        self.log.touch()
        self.scenario_file = self.root / "scenario.json"
        self.status = FakeStatus()
        self.port = free_port()
        self.console = Console(self.repo, self.port, status=self.status, terminal_argv=BASH, poll_seconds=0.1,
                               toolkit_url=TOOLKIT)
        self.console.terminal.env.update({
            "PS1": "$ ", "HISTFILE": "/dev/null", "HOME": self.dir.name, "PATH": f"{bin_}:/usr/bin:/bin",
            "FAKE_MAKE_LOG": str(self.log), "FAKE_MAKE_SCENARIO": str(self.scenario_file)})
        self.runner = web.AppRunner(self.console.app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.headers = {"Origin": f"http://localhost:{self.port}"}
        self.session = aiohttp.ClientSession(cookies={self.console.cookie: self.console.token})
        self.messages = []
        self.ws = await self.session.ws_connect(f"{self.base}/ws", origin=self.headers["Origin"])
        self.reader = asyncio.ensure_future(self._read_ws())
        await self.wait(lambda: self.console.terminal.shell_idle() and b"$ " in self.console.terminal.replay())

    async def asyncTearDown(self):
        self.reader.cancel()
        await self.ws.close()
        await self.session.close()
        await self.runner.cleanup()
        self.dir.cleanup()

    async def _read_ws(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                self.messages.append(msg.data)

    def scenario(self, rules):
        self.scenario_file.write_text(json.dumps(rules))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def wait(self, pred, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return
            await asyncio.sleep(0.03)
        raise AssertionError(f"timed out; flow: {json.dumps(self.console.flow.snapshot(), indent=1)}\n"
                             f"terminal: {self.console.terminal.replay()[-600:]!r}")

    def flow(self):
        return self.console.flow.snapshot()

    async def at(self, step, running=False):
        await self.wait(lambda: self.flow() and self.flow()["step"] == step and self.flow()["running"] == running
                        and not self.flow()["pending"] and not self.console.terminal.action)
        return self.flow()

    async def post(self, path, body):
        async with self.session.post(f"{self.base}{path}", json=body, headers=self.headers) as r:
            text = await r.text()
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, {"reason": text}

    # ------------------------------------------------------------------------------------------

    async def test_deploy_with_one_secret(self):
        self.scenario({"import": [{"rc": 1, "last_import": MISSING}, {"last_import": IMPORT_OK}],
                       "secret-set": [{"prompt": True}], "plan": [{"plan_summary": SUMMARY}], "provision": [{}]})
        code, body = await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        self.assertEqual(code, 200, body)
        self.assertEqual((body["flow"]["kind"], body["flow"]["source"]), ("deploy", SOURCE))

        st = await self.at("secrets")
        self.assertEqual((st["id"], st["secrets"]), ("studio-a", ["venue-srt"]))
        self.assertEqual(self.calls(), [["import", f"XML={SOURCE}"]])

        code, body = await self.post("/api/flow", {"op": "run"})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["flow"]["command"], "make secret-set WORKLOAD=studio-a NAME=venue-srt")
        await self.wait(lambda: b"venue-srt for studio-a: " in self.console.terminal.replay())
        self.console.terminal.write(SECRET_VALUE + "\r")  # the user, typing in the terminal

        st = await self.at("review")
        self.assertEqual(st["summary"]["counts"], SUMMARY["counts"])
        self.assertEqual(st["summary"]["restarts"], [])
        self.assertEqual(st["import"]["warnings"], IMPORT_OK["warnings"])
        self.assertEqual(st["secrets"], [])

        code, body = await self.post("/api/flow", {"op": "provision"})
        self.assertEqual(code, 200, body)
        st = await self.at("done")
        self.assertEqual(st["message"], "Provisioned studio-a.")
        self.assertEqual(self.calls(), [
            ["import", f"XML={SOURCE}"], ["secret-set", "WORKLOAD=studio-a", "NAME=venue-srt"],
            ["import", f"XML={SOURCE}"], ["plan", "WORKLOAD=studio-a"], ["provision", "WORKLOAD=studio-a"]])

        # The value reached the fake through the PTY only: not echoed, not in any event to the page.
        self.assertEqual((self.repo / ".state" / "fake-secret-lengths").read_text(), f"venue-srt {len(SECRET_VALUE)}\n")
        self.assertNotIn(SECRET_VALUE.encode(), self.console.terminal.replay())
        self.assertFalse(any(SECRET_VALUE in m for m in self.messages))
        flows = [json.loads(m)["flow"] for m in self.messages if json.loads(m)["type"] == "flow"]
        self.assertEqual([f["step"] for f in flows if f][-1], "done")

        code, _ = await self.post("/api/flow", {"op": "close"})
        self.assertEqual((code, self.flow()), (200, None))

    async def test_refused_design_stops_at_import(self):
        both = {**MISSING, "problems": MISSING["problems"] + REFUSED["problems"]}
        for record in (REFUSED, both):
            with self.subTest(problems=len(record["problems"])):
                self.log.write_text("")
                Path(str(self.scenario_file) + ".n").unlink(missing_ok=True)
                self.scenario({"import": [{"rc": 1, "last_import": record}]})
                await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
                st = await self.at("refused")
                self.assertEqual(st["import"]["problems"], record["problems"])
                self.assertEqual(st["secrets"], [])  # a missing secret alongside another problem isn't offered
                self.assertEqual(self.calls(), [["import", f"XML={SOURCE}"]])
                code, body = await self.post("/api/flow", {"op": "run"})
                self.assertEqual(code, 409)
                await self.post("/api/flow", {"op": "close"})

    async def test_stale_or_foreign_last_import_is_not_read(self):
        # From an earlier import: old, and of another source.
        old = self.repo / ".state" / "last-import.json"
        old.write_text(json.dumps({**IMPORT_OK, "source": SOURCE}))
        os.utime(old, (time.time() - 60, time.time() - 60))
        self.scenario({"import": [{"rc": 2}]})  # e.g. make itself failed: nothing written
        await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        st = await self.at("import")
        self.assertIn("without writing .state/last-import.json", st["error"])
        self.assertIn("exit 2", st["error"])

        # A fresh file naming another source isn't this import's either.
        old.write_text(json.dumps({**IMPORT_OK, "source": "http://localhost:3001/api/workloads/other/published.xml"}))
        self.scenario({"import": [{"rc": 0}]})
        await self.post("/api/flow", {"op": "run"})
        st = await self.at("import")
        self.assertIn("without writing .state/last-import.json", st["error"])
        self.assertEqual(len(self.calls()), 2)

    async def test_plan_failure_then_retry(self):
        self.scenario({"import": [{"last_import": IMPORT_OK}],
                       "plan": [{"rc": 2}, {"plan_summary": SUMMARY}], "provision": [{"rc": 2}]})
        await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        st = await self.at("plan")
        self.assertIn("make plan failed (exit 2)", st["error"])
        code, _ = await self.post("/api/flow", {"op": "provision"})
        self.assertEqual(code, 409)  # not before a reviewed plan
        await self.post("/api/flow", {"op": "run"})
        await self.at("review")
        await self.post("/api/flow", {"op": "provision"})
        st = await self.at("plan")  # a failed provision goes back to plan
        self.assertIn("make provision failed (exit 2)", st["error"])
        self.assertIsNone(st["summary"])
        code, _ = await self.post("/api/flow", {"op": "cancel"})
        self.assertEqual((code, self.flow()["step"]), (200, "cancelled"))

    async def test_plan_without_summary(self):
        self.scenario({"import": [{"last_import": IMPORT_OK}], "plan": [{"rc": 0}]})
        await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        st = await self.at("plan")
        self.assertIn("didn't write .state/studio-a.plan-summary.json", st["error"])

    async def test_update_when_deployed(self):
        self.status.deployed = ["studio-a"]
        await self.wait(lambda: self.console.deployed() == {"studio-a"})
        self.scenario({"import": [{"last_import": IMPORT_OK}], "plan": [{"plan_summary": SUMMARY}]})
        code, body = await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        self.assertEqual(body["flow"]["kind"], "update")
        await self.at("review")
        await self.post("/api/flow", {"op": "cancel"})
        self.assertEqual([c[0] for c in self.calls()], ["import", "plan"])  # nothing provisioned

    async def test_redeploy_from_the_archive(self):
        run = "studio-a-20261112T004502Z"
        (self.repo / ".state" / "archive" / run).mkdir(parents=True)
        (self.repo / ".state" / "archive" / run / "source.xml").write_text("<workload/>")
        self.scenario({"import": [{"rc": 1, "last_import": MISSING}]})
        code, body = await self.post("/api/flow", {"op": "redeploy", "run": run})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["flow"]["kind"], "redeploy")
        self.assertIn("secrets", body["flow"]["note"])
        st = await self.at("secrets")
        self.assertEqual(self.calls(), [["import", f"XML=.state/archive/{run}/source.xml"]])
        self.assertEqual(st["id"], "studio-a")
        await self.post("/api/flow", {"op": "cancel"})
        await self.post("/api/flow", {"op": "close"})
        for bad in ("../studio-a-20261112T004502Z", "studio-a", "studio-a-20261112T004503Z", "x;rm -rf ~-20261112T004502Z"):
            code, _ = await self.post("/api/flow", {"op": "redeploy", "run": bad})
            self.assertEqual(code, 400, bad)

    async def test_one_flow_and_lifecycle_guards(self):
        self.scenario({"import": [{"last_import": IMPORT_OK}], "plan": [{"plan_summary": SUMMARY}] * 3})
        await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        await self.at("review")
        code, body = await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        self.assertEqual(code, 409)
        self.assertIn("still open", body["reason"])
        code, _ = await self.post("/api/action", {"action": "status", "workload": "studio-a"})
        self.assertEqual(code, 200)  # read-only actions leave the deploy alone
        await self.wait(lambda: not self.console.terminal.action)
        self.assertEqual(self.console.flow.snapshot()["step"], "review")

        # Start, Stop and Finalise of another workload run with a deploy open; its plan is stale.
        for action, workload in (("stop", None), ("start", None), ("finalise", "studio-b")):
            body = {"action": action, "workload": workload, "confirm": workload}
            code, out = await self.post("/api/action", body)
            self.assertEqual(code, 200, action)
            self.assertEqual(out["flow"]["step"], "plan", action)
            self.assertIsNone(out["flow"]["summary"])
            self.assertIn("run the plan again", out["flow"]["message"])
            await self.wait(lambda: not self.console.terminal.action)
            code, _ = await self.post("/api/flow", {"op": "provision"})
            self.assertEqual(code, 409)  # the stale plan can't be provisioned
            await self.post("/api/flow", {"op": "run"})
            await self.at("review")

        # Before a plan exists, Start changes nothing in the deploy.
        self.console.flow.state.update(step="plan", summary=None, error="make plan failed (exit 2)")
        code, out = await self.post("/api/action", {"action": "start"})
        self.assertEqual((code, out["flow"]["step"], out["flow"]["error"]), (200, "plan", "make plan failed (exit 2)"))
        await self.wait(lambda: not self.console.terminal.action)

        code, body = await self.post("/api/action", {"action": "finalise", "workload": "studio-a"})
        self.assertEqual(code, 400)
        self.assertIn("type the workload id", body["reason"])
        code, _ = await self.post("/api/action", {"action": "finalise", "workload": "studio-a", "confirm": "studio-b"})
        self.assertEqual(code, 400)
        self.assertTrue(self.console.flow.active())
        # Finalising the deploy's own workload cancels it: its secrets and generated files go.
        code, out = await self.post("/api/action", {"action": "finalise", "workload": "studio-a", "confirm": "studio-a"})
        self.assertEqual((code, out["command"]), (200, "make finalise WORKLOAD=studio-a"))
        self.assertEqual(out["flow"]["step"], "cancelled")
        await self.wait(lambda: not self.console.terminal.action)
        self.assertEqual([c for c in self.calls() if c[0] in ("finalise", "stop", "start")],
                         [["stop"], ["start", "NO_BROWSER=1"], ["finalise", "WORKLOAD=studio-b"],
                          ["start", "NO_BROWSER=1"], ["finalise", "WORKLOAD=studio-a"]])

    async def test_busy_terminal_leaves_a_run_button(self):
        self.console.terminal.write("half typed")
        self.scenario({"import": [{"last_import": IMPORT_OK}], "plan": [{"plan_summary": SUMMARY}]})
        code, body = await self.post("/api/flow", {"op": "deploy", "design": "studio-a"})
        self.assertEqual(code, 200)
        self.assertIn("partly typed line", body["flow"]["error"])
        self.assertFalse(body["flow"]["running"])
        self.console.terminal.write("\x15")
        await self.post("/api/flow", {"op": "run"})
        await self.at("review")

    def test_bad_design_ids(self):
        flow = F.Flow(Path(self.dir.name), lambda a, c: None, lambda s: None, lambda d, f: None)
        flow.state = {"id": "x; rm -rf ~", "secrets": [], "source": "s"}
        with self.assertRaises(F.FlowError):
            flow.command("plan")
        flow.state = {"id": "studio-a", "secrets": ["a b"], "source": "s"}
        with self.assertRaises(F.FlowError):
            flow.command("secrets")

    async def test_invalid_deploy_request(self):
        for design in ("Studio A", "../x", "", None):
            code, _ = await self.post("/api/flow", {"op": "deploy", "design": design})
            self.assertEqual(code, 400, design)
        code, _ = await self.post("/api/flow", {"op": "provision"})
        self.assertEqual(code, 409)  # nothing open


if __name__ == "__main__":
    unittest.main()
