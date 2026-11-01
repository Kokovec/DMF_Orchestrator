#!/usr/bin/env python3
"""sso-bridge: OIDC single sign-on for SwitchFrame (DMF Application & UI security).

SwitchFrame authenticates operators with its own bearer tokens. DMF RA v2.0
asks UIs to integrate with SSO / enterprise identity (EBU R 143). This bridge
closes that gap without patching SwitchFrame:

  browser -> Traefik (TLS 1.3) -> oauth2-proxy (Keycloak OIDC) -> sso-bridge (127.0.0.1 only)
                                                                -> SwitchFrame UI/API

GET /sso/login
  1. oauth2-proxy has already authenticated the user and set X-Forwarded-* headers.
     The bridge listens on loopback inside the oauth2-proxy pod, so nothing else
     can reach it and the headers can't be spoofed.
  2. The user's groups (/<workload>/<role>) select a SwitchFrame role.
  3. The bridge (re)registers the user as a SwitchFrame operator with a stable
     id derived from the IdP subject. Re-login revokes the previous token.
  4. It redirects to /#token=<operator-token>. SwitchFrame's UI consumes the
     fragment (checkFragmentToken) and strips it from the URL.

Environment:
  WORKLOAD               workload name (group prefix)
  SWITCHFRAME_URL        e.g. http://vision-mixer-api:8081
  SWITCHFRAME_API_TOKEN  session API token (from the workload Secret)
  LISTEN_ADDR            default 127.0.0.1:8000
  ROLE_PRIORITY          default director,audio,graphics,captioner,viewer
"""
import html
import json
import logging
import os
import secrets
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("sso-bridge")
NS = uuid.UUID("0b6f6a0c-8a3e-4f53-9d1e-5a2f3c7e9b41")
BRIDGE_PREFIX = "sso-bridge-"


class SwitchFrame:
    def __init__(self, base: str, api_token: str):
        self.base = base.rstrip("/")
        self.api_token = api_token
        self.bridge_token = None
        self.lock = threading.Lock()

    def _call(self, method, path, token, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"error": raw.decode(errors="replace")}

    def _register(self, name, role, op_id):
        # The session API token lets us set the role directly (no invite token needed).
        return self._call("POST", "/api/operator/register", self.api_token,
                          {"name": name, "role": role, "id": op_id})

    def _bridge(self):
        """Director-role service operator, needed because only directors may delete others."""
        if self.bridge_token:
            status, _ = self._call("POST", "/api/operator/heartbeat", self.bridge_token, {})
            if status < 400:
                return self.bridge_token
        name = BRIDGE_PREFIX + secrets.token_hex(4)
        status, body = self._register(name, "director", str(uuid.uuid5(NS, name)))
        if status != 200:
            raise RuntimeError(f"cannot register bridge operator: {status} {body}")
        self.bridge_token = body["token"]
        status, ops = self._call("GET", "/api/operator/list", self.bridge_token)
        for op in (ops or []) if status == 200 else []:
            if op.get("name", "").startswith(BRIDGE_PREFIX) and op.get("name") != name:
                self._call("DELETE", f"/api/operator/{op['id']}", self.bridge_token)
        return self.bridge_token

    def login(self, username: str, subject: str, role: str) -> str:
        op_id = str(uuid.uuid5(NS, f"sub:{subject}"))
        with self.lock:
            bridge = self._bridge()
            self._call("DELETE", f"/api/operator/{op_id}", bridge)  # revoke previous session
            status, body = self._register(username, role, op_id)
            if status == 409:  # same display name registered outside SSO: replace it
                _, ops = self._call("GET", "/api/operator/list", bridge)
                for op in ops or []:
                    if op.get("name") == username:
                        self._call("DELETE", f"/api/operator/{op['id']}", bridge)
                status, body = self._register(username, role, op_id)
            if status != 200:
                raise RuntimeError(f"register failed: {status} {body}")
            return body["token"]


def pick_role(groups: list, workload: str, priority: list):
    granted = set()
    for g in groups:
        parts = [p for p in g.strip().split("/") if p]
        if len(parts) == 2 and parts[0] == workload:
            granted.add(parts[1])
    return next((r for r in priority if r in granted), None)


def make_handler(sf: SwitchFrame, workload: str, priority: list):
    class H(BaseHTTPRequestHandler):
        def _page(self, code, title, msg):
            body = (f"<!doctype html><title>{html.escape(title)}</title>"
                    f"<h1>{html.escape(title)}</h1><p>{html.escape(msg)}</p>").encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/sso/healthz":
                self._page(200, "ok", "ok")
                return
            if path != "/sso/login":
                self._page(404, "Not found", path)
                return
            user = (self.headers.get("X-Forwarded-Preferred-Username")
                    or self.headers.get("X-Forwarded-User") or "")
            subject = self.headers.get("X-Forwarded-User") or user
            groups = [g for g in (self.headers.get("X-Forwarded-Groups") or "").split(",") if g]
            if not user:
                self._page(401, "Not authenticated", "No identity from the SSO proxy.")
                return
            role = pick_role(groups, workload, priority)
            if not role:
                log.info("deny user=%s groups=%s", user, groups)
                self._page(403, "No access", f"{user} has no role in workload '{workload}'.")
                return
            try:
                token = sf.login(user, subject, role)
            except Exception as exc:  # surfaced to the operator; details stay in the log
                log.error("login user=%s role=%s failed: %s", user, role, exc)
                self._page(502, "SwitchFrame unavailable", "Could not create an operator session.")
                return
            log.info("login user=%s role=%s", user, role)  # accounting (AAA)
            self.send_response(302)
            self.send_header("Location", f"/#token={token}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def log_message(self, *args):
            pass

    return H


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    workload = os.environ.get("WORKLOAD", "")
    base = os.environ.get("SWITCHFRAME_URL", "")
    token = os.environ.get("SWITCHFRAME_API_TOKEN", "")
    if not (workload and base and token):
        log.error("WORKLOAD, SWITCHFRAME_URL and SWITCHFRAME_API_TOKEN are required")
        return 2
    priority = os.environ.get("ROLE_PRIORITY", "director,audio,graphics,captioner,viewer").split(",")
    host, _, port = os.environ.get("LISTEN_ADDR", "127.0.0.1:8000").rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), make_handler(SwitchFrame(base, token), workload, priority))
    log.info("listening on %s:%s for workload %s", host, port, workload)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
