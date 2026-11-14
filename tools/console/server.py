"""The console's HTTP and WebSocket server (docs/orchestration/05-facility-console.md).

It listens on 127.0.0.1 only. Every request needs the token: the URL printed at start carries it
once, and the server swaps it for an HttpOnly, SameSite=Strict cookie. The Host header must name
this listener (so a DNS-rebound page can't reach it), and the WebSocket and every POST must come
from this page's own Origin.
"""
import asyncio
import hmac
import json
import re
import secrets
import shlex
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from .flow import Flow, FlowError
from .status import WORKLOAD_ID, Status
from .terminal import Busy, Terminal

STATIC = Path(__file__).parent / "static"
POLL_SECONDS = 5

# What each button types (spec: Actions). Deploy, Update and Redeploy are flows (flow.py).
MOUNT = ("mountpoint -q /mnt/wsl/instances/$WSL_DISTRO_NAME || "
         "{ sudo mkdir -p /mnt/wsl/instances/$WSL_DISTRO_NAME && "
         "sudo mount --bind / /mnt/wsl/instances/$WSL_DISTRO_NAME; }")
PER_WORKLOAD = {"open-ui": "windows-browser", "status": "status", "finalise": "finalise"}
# They change what an open deploy planned against: it has to plan again (Flow.facility_changed).
LIFECYCLE = {"start", "stop", "finalise"}
ARCHIVE_RUN = re.compile(r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?-\d{8}T\d{6}Z$")
REDEPLOY_NOTE = ("finalise deleted this workload's secrets: if the design references any, the import asks for "
                 "each again")


def command_for(action, workload, facility):
    fac = "" if facility == "local" else f" FACILITY={shlex.quote(facility)}"
    if action == "export":
        return "make facility-export" + fac
    if action == "mount":
        return MOUNT
    if action == "start":
        return "make start NO_BROWSER=1"
    if action == "stop":
        return "make stop"
    if action in PER_WORKLOAD:
        if not workload or not WORKLOAD_ID.match(workload):
            raise web.HTTPBadRequest(text="a valid workload id is required")
        return f"make {PER_WORKLOAD[action]} WORKLOAD={workload}"
    raise web.HTTPBadRequest(text=f"unknown action {action!r}")


class Console:
    def __init__(self, repo, port, facility="local", token=None, status=None, terminal_argv=("bash", "-l"),
                 poll_seconds=POLL_SECONDS, toolkit_url="http://localhost:3001"):
        self.repo = Path(repo)
        self.port = port
        self.facility = facility
        self.token = token or secrets.token_urlsafe(32)
        self.cookie = f"dmf_console_{port}"
        self.hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.status = status or Status(repo, facility)
        self.poll_seconds = poll_seconds
        self.snapshot = None
        self.clients = set()
        self.toolkit_url = toolkit_url.rstrip("/")
        self.terminal = Terminal(repo, argv=terminal_argv, on_output=self._output, on_event=self._terminal_event)
        self.flow = Flow(self.repo, self.terminal.run, lambda st: self._event({"type": "flow", "flow": st}),
                         lambda delay, fn: asyncio.get_running_loop().call_later(delay, fn), facility)
        self.app = web.Application(middlewares=[self.guard])
        self.app.add_routes([
            web.get("/", self.index),
            web.get("/api/status", self.get_status),
            web.get("/api/actions", self.get_actions),
            web.post("/api/action", self.post_action),
            web.post("/api/flow", self.post_flow),
            web.get("/ws", self.ws),
            web.static("/static", STATIC, follow_symlinks=False),
        ])
        self.app.on_startup.append(self._startup)
        self.app.on_shutdown.append(self._shutdown)

    # ---- auth ----

    def _has_token(self, request):
        return hmac.compare_digest(request.cookies.get(self.cookie, ""), self.token)

    @web.middleware
    async def guard(self, request, handler):
        if request.host not in self.hosts:
            return web.Response(status=403, text="Open the console as http://localhost:<port>/.\n")
        if request.path == "/" and "token" in request.query:
            if not hmac.compare_digest(request.query["token"], self.token):
                return web.Response(status=403, text="Wrong token. Use the URL make console printed.\n")
            resp = web.Response(status=303, headers={"Location": "/"})
            resp.set_cookie(self.cookie, self.token, httponly=True, samesite="Strict", path="/")
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if not self._has_token(request):
            return web.Response(status=403, text="Open the URL make console printed (it carries the token).\n")
        if request.method != "GET" or request.path == "/ws":
            if request.headers.get("Origin") not in self.origins:
                return web.Response(status=403, text="Foreign origin.\n")
        resp = await handler(request)
        if not isinstance(resp, web.WebSocketResponse):
            resp.headers.setdefault("Cache-Control", "no-store")
            resp.headers["Content-Security-Policy"] = (
                f"default-src 'self'; connect-src 'self' ws://{request.host}; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    # ---- pages and API ----

    async def index(self, request):
        return web.FileResponse(STATIC / "index.html")

    async def get_status(self, request):
        return web.json_response(self.snapshot or {})

    async def get_actions(self, request):
        return web.json_response(self.actions())

    def actions(self):
        return {
            "facility": self.facility,
            "commands": {
                "export": command_for("export", None, self.facility),
                "mount": MOUNT,
                "start": command_for("start", None, self.facility),
                "stop": command_for("stop", None, self.facility),
                **{a: f"make {t} WORKLOAD=<id>" for a, t in PER_WORKLOAD.items()},
                "import": f"make import XML=<source>{'' if self.facility == 'local' else ' FACILITY=' + self.facility}",
                "design-xml": f"{self.toolkit_url}/api/workloads/<id>/published.xml",
                "archive-xml": ".state/archive/<run>/source.xml",
            },
            "running": self.terminal.action,
            "flow": self.flow.snapshot(),
        }

    @staticmethod
    async def _body(request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="JSON body expected")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object expected")
        return body

    async def post_action(self, request):
        body = await self._body(request)
        action, workload = body.get("action"), body.get("workload")
        command = command_for(action, workload, self.facility)
        if action == "finalise" and body.get("confirm") != workload:
            return web.json_response({"ok": False, "reason": "type the workload id to confirm finalise"}, status=400)
        try:
            self.terminal.run(action, command)
        except Busy as e:
            return web.json_response({"ok": False, "reason": str(e)}, status=409)
        if action in LIFECYCLE:
            self.flow.facility_changed(action, workload)
        return web.json_response({"ok": True, "command": command, "flow": self.flow.snapshot()})

    def deployed(self):
        items = ((self.snapshot or {}).get("workloads") or {}).get("items") or []
        return {w["id"] for w in items if w.get("deployed")}

    async def post_flow(self, request):
        body = await self._body(request)
        op = body.get("op")
        try:
            if op == "deploy":
                design = body.get("design")
                if not isinstance(design, str) or not WORKLOAD_ID.match(design):
                    raise web.HTTPBadRequest(text="a valid design id is required")
                kind = "update" if design in self.deployed() else "deploy"
                self.flow.start(kind, f"{self.toolkit_url}/api/workloads/{design}/published.xml", design)
            elif op == "redeploy":
                run = body.get("run")
                if not isinstance(run, str) or not ARCHIVE_RUN.match(run):
                    raise web.HTTPBadRequest(text="a valid archive run is required")
                source = f".state/archive/{run}/source.xml"
                if not (self.repo / source).is_file():
                    raise web.HTTPBadRequest(text=f"{source} doesn't exist")
                self.flow.start("redeploy", source, None, note=REDEPLOY_NOTE)
            else:
                self.flow.op(op)
        except FlowError as e:
            return web.json_response({"ok": False, "reason": str(e), "flow": self.flow.snapshot()}, status=409)
        return web.json_response({"ok": True, "flow": self.flow.snapshot()})

    # ---- WebSocket: terminal I/O and status pushes ----

    async def ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2**20)
        await ws.prepare(request)
        await ws.send_bytes(self.terminal.replay())
        await ws.send_str(json.dumps({"type": "hello", **self.actions()}))
        if self.snapshot:
            await ws.send_str(json.dumps({"type": "status", "status": self.snapshot}))
        self.clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self.terminal.write(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    if m.get("type") == "input":
                        self.terminal.write(m["data"])
                    elif m.get("type") == "resize":
                        self.terminal.resize(m["rows"], m["cols"])
        finally:
            self.clients.discard(ws)
        return ws

    def _broadcast(self, send):
        async def safe(ws):
            try:
                await send(ws)
            except (ConnectionError, RuntimeError):  # a page closing as we send: it reattaches with a replay
                self.clients.discard(ws)
        for ws in list(self.clients):
            if ws.closed:
                self.clients.discard(ws)
            else:
                asyncio.ensure_future(safe(ws))

    def _output(self, data):
        self._broadcast(lambda ws: ws.send_bytes(data))

    def _event(self, event):
        text = json.dumps(event)
        self._broadcast(lambda ws: ws.send_str(text))

    def _terminal_event(self, event):
        self._event(event)
        if event.get("type") == "done":
            self.flow.on_done(event)

    # ---- lifecycle ----

    async def _startup(self, app):
        self.terminal.start()
        self._poller = asyncio.get_running_loop().create_task(self._poll())

    async def _shutdown(self, app):
        self._poller.cancel()
        for ws in list(self.clients):
            await ws.close()
        self.terminal.stop()

    async def _poll(self):
        while True:
            started = time.monotonic()
            try:
                self.snapshot = await self.status.collect()
                self._event({"type": "status", "status": self.snapshot})
            except Exception as e:  # never let the poller die
                self._event({"type": "status-error", "error": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(max(0.5, self.poll_seconds - (time.monotonic() - started)))


def run(repo, port, facility="local"):
    console = Console(repo, port, facility)
    print(f"Facility console: http://localhost:{port}/?token={console.token}", flush=True)
    print("Listening on 127.0.0.1 only. Ctrl-C stops it (and its terminal).", flush=True)
    web.run_app(console.app, host="127.0.0.1", port=port, print=None, handle_signals=True)


