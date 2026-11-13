"""Auth (spec 05, Tests: Auth): requests and the WebSocket without the token, with a foreign
Origin or with a foreign Host, are refused; with them, the page, status, actions and terminal work."""
import asyncio
import json
import pathlib
import tempfile
import unittest

import aiohttp
from aiohttp import web

from tools.console.server import MOUNT, Console
from tools.console.tests.helpers import Audit, free_port

BASH = ("bash", "--norc", "--noprofile", "-i")


class FakeStatus:
    async def collect(self):
        return {"time": "2026-11-13T00:00:00+00:00", "facility": {}, "workloads": {"items": []}}


class AuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="dmf-console-auth-")
        self.port = free_port()
        self.console = Console(self.dir.name, self.port, status=FakeStatus(), terminal_argv=BASH, poll_seconds=0.2)
        self.console.terminal.env.update({"PS1": "$ ", "HISTFILE": "/dev/null", "HOME": self.dir.name})
        # A status target that takes a second, so a second action reliably finds the first still
        # running; without it, make failed at once and the one-at-a-time check raced.
        pathlib.Path(self.dir.name, "Makefile").write_text("status:\n\t@sleep 1; exit 2\n")
        self.runner = web.AppRunner(self.console.app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.origin = f"http://localhost:{self.port}"
        self.cookie = {self.console.cookie: self.console.token}

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.dir.cleanup()

    def session(self, cookies=None):
        return aiohttp.ClientSession(cookies=cookies, cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def test_token_sets_strict_httponly_cookie(self):
        async with self.session() as s:
            async with s.get(f"{self.base}/?token={self.console.token}", allow_redirects=False) as r:
                self.assertEqual(r.status, 303)
                self.assertEqual(r.headers["Location"], "/")
                cookie = r.headers["Set-Cookie"]
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=Strict", cookie)
            async with s.get(f"{self.base}/") as r:  # the jar now has the cookie
                self.assertEqual(r.status, 200)
                self.assertIn("Facility console", await r.text())
                self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            async with s.get(f"{self.base}/api/status") as r:
                self.assertEqual(r.status, 200)

    async def test_refused_without_token(self):
        async with self.session() as s:
            for path in ("/", "/api/status", "/api/actions", "/static/app.js", "/static/vendor/xterm-6.0.0/xterm.js",
                         "/?token=wrong", f"/?token={self.console.token[:-1]}"):
                async with s.get(self.base + path, allow_redirects=False) as r:
                    self.assertEqual(r.status, 403, path)
            async with s.post(f"{self.base}/api/action", json={"action": "export"},
                              headers={"Origin": self.origin}) as r:
                self.assertEqual(r.status, 403)
            with self.assertRaises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(f"{self.base}/ws", origin=self.origin)
            self.assertEqual(e.exception.status, 403)

    async def test_wrong_cookie(self):
        async with self.session({self.console.cookie: "x" * len(self.console.token)}) as s:
            async with s.get(f"{self.base}/api/status") as r:
                self.assertEqual(r.status, 403)

    async def test_foreign_host_refused(self):
        # A DNS-rebound page reaches 127.0.0.1 under its own name: refuse it even with the cookie.
        async with self.session(self.cookie) as s:
            async with s.get(f"{self.base}/api/status", headers={"Host": f"evil.example:{self.port}"}) as r:
                self.assertEqual(r.status, 403)
            async with s.get(f"{self.base}/api/status", headers={"Host": f"localhost:{self.port + 1}"}) as r:
                self.assertEqual(r.status, 403)

    async def test_websocket_origin(self):
        async with self.session(self.cookie) as s:
            for origin in (None, "http://evil.example", f"http://localhost:{self.port + 1}", f"https://localhost:{self.port}"):
                with self.assertRaises(aiohttp.WSServerHandshakeError, msg=origin) as e:
                    await s.ws_connect(f"{self.base}/ws", origin=origin)
                self.assertEqual(e.exception.status, 403)
            ws = await s.ws_connect(f"{self.base}/ws", origin=self.origin)
            first = await ws.receive(timeout=5)
            self.assertEqual(first.type, aiohttp.WSMsgType.BINARY)  # the replay comes first
            hello = json.loads((await ws.receive(timeout=5)).data)
            self.assertEqual(hello["type"], "hello")
            self.assertEqual(hello["commands"]["mount"], MOUNT)
            await ws.close()

    async def test_post_needs_origin(self):
        async with self.session(self.cookie) as s:
            for headers in ({}, {"Origin": "http://evil.example"}):
                async with s.post(f"{self.base}/api/action", json={"action": "export"}, headers=headers) as r:
                    self.assertEqual(r.status, 403, headers)
            self.assertIsNone(self.console.terminal.action)

    async def test_actions_and_reattach(self):
        await asyncio.sleep(0.5)  # bash's first prompt
        h = {"Origin": self.origin}
        async with self.session(self.cookie) as s:
            ws = await s.ws_connect(f"{self.base}/ws", origin=self.origin)
            for bad in ({"action": "start"}, {"action": "finalise", "workload": "studio-a"},
                        {"action": "status", "workload": "x; rm -rf /"}, {"action": "status"}, {"action": "rm"}):
                async with s.post(f"{self.base}/api/action", json=bad, headers=h) as r:
                    self.assertEqual(r.status, 400, bad)  # stage 9 actions and bad ids aren't wired
            Audit.start()
            async with s.post(f"{self.base}/api/action", json={"action": "status", "workload": "studio-a"}, headers=h) as r:
                body = await r.json()
                self.assertEqual((r.status, body["command"]), (200, "make status WORKLOAD=studio-a"))
            async with s.post(f"{self.base}/api/action", json={"action": "export"}, headers=h) as r:
                self.assertEqual(r.status, 409)  # one at a time
                self.assertIn("still running", (await r.json())["reason"])
            events = []
            while not any(e.get("type") == "done" for e in events):
                m = await ws.receive(timeout=10)
                if m.type == aiohttp.WSMsgType.TEXT:
                    events.append(json.loads(m.data))
            audited = Audit.stop()
            done = [e for e in events if e["type"] == "done"][0]
            self.assertEqual(done["command"], "make status WORKLOAD=studio-a")
            self.assertEqual(done["status"], 2)  # the scratch Makefile's status target fails
            self.assertTrue(any(e["type"] == "status" for e in events), "status pushes reach the page")
            # Typed input goes to the PTY.
            await ws.send_str(json.dumps({"type": "input", "data": "echo REATTACH-$((40+2))\r"}))
            await asyncio.sleep(0.5)
            await ws.close()
            # Reloading the page reattaches: the replay holds what happened before.
            ws = await s.ws_connect(f"{self.base}/ws", origin=self.origin)
            replay = (await ws.receive(timeout=5)).data
            self.assertIn(b"make status WORKLOAD=studio-a", replay)
            self.assertIn(b"REATTACH-42", replay)
            await ws.close()
        self.assertEqual(Audit.writes(audited), [], "the server opened a file for writing")


if __name__ == "__main__":
    unittest.main()
