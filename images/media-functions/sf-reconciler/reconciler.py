#!/usr/bin/env python3
"""sf-reconciler: keeps a SwitchFrame instance at its compiled configuration.

SwitchFrame forgets its programme source, source settings, output destinations and recording on
every restart, and an SRT listener source has no API object until its encoder connects. A sidecar
in the SwitchFrame pod therefore checks the API in a loop (docs/orchestration/04-orchestrator-spec.md,
Reconciler) and applies what is missing:

  1. wait for GET /api/switch/state;
  2. per desired source: resolve its key, and apply its settings once per appearance;
  3. remove SRT caller pulls the design used to have and no longer does (SwitchFrame persists
     them); the stream IDs it manages are kept in a file, so a pull an operator added is never touched;
  4. create and start missing destinations, recreating one only when its desired config changed;
  5. start the recording when it is desired and has not been started on this instance, or failed;
  6. cut to the programme source when nothing is on air.

A setting is applied once per appearance of its source, not every loop, so operator changes stand.
It never exits on API errors and never fails the pod. Secrets come from files (the workload-refs
Secret) and are redacted from everything it logs.

Standard library only. Configured through environment variables:
  SF_API_URL             default http://127.0.0.1:8081
  SWITCHFRAME_API_TOKEN  the session API token (required)
  CONFIG_FILE            default /etc/switchframe/config/config.json
  SECRETS_DIR            default /etc/switchframe/secrets (one file per secret name)
  MANAGED_CALLERS_FILE   default /var/lib/switchframe/dmf-reconciler-callers.json (the state volume)
  WORKLOAD               workload id, a metrics label
  METRICS_ADDR           default 0.0.0.0:9103
  FAST_SECONDS, FAST_WINDOW_SECONDS, SLOW_SECONDS   default 5, 120, 30

`sf-reconciler stop-recording` stops the recording once, for FINALISE: the reconciler doesn't restart
a recording that was stopped cleanly.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A call that fails with one of these is retried on a later loop; any other 4xx is given up for
# this appearance (retrying an invalid request every loop only floods the log).
RETRY_STATUSES = {404, 408, 409, 423, 425, 429}
DEST_NAME = re.compile(r"^(srt|rtmp)-out(_[0-9]+)?$")   # destination names the compiler gives
PLACEHOLDER = re.compile(r"\{key(?::([A-Za-z0-9_-]+))?\}")
AUTOMATION_OPERATOR = re.compile(r"^dmf-automation-[0-9]+(-[0-9]+)?$")


class Unresolved(Exception):
    """A placeholder names a source whose key isn't known yet."""


# ------------------------------------------------------------------------------------------------
# Logging (JSON lines) and redaction
# ------------------------------------------------------------------------------------------------
class Log:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.secrets = []
        self.lock = threading.Lock()

    def redact(self, text):
        text = str(text)
        for value in self.secrets:
            if value:
                text = text.replace(value, "***")
        text = re.sub(r'("passphrase"\s*:\s*")[^"]*(")', r"\1***\2", text)
        # An RTMP URL carries the stream key in its path: keep scheme and host only.
        text = re.sub(r"(rtmps?://[^/\s\"']+)/[^\s\"']*", r"\1/***", text)
        return text

    def __call__(self, level, msg, **fields):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": level, "msg": msg}
        rec.update(fields)
        line = self.redact(json.dumps(rec, default=str))
        with self.lock:
            self.stream.write(line + "\n")
            self.stream.flush()


# ------------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------------
class Response:
    def __init__(self, status, body=b"", error=None):
        self.status = status          # 0: no connection
        self.body = body
        self.error = error

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        try:
            return json.loads(self.body.decode() or "null")
        except (ValueError, UnicodeDecodeError):
            return None

    def message(self):
        """A short error description, for logs (redacted by the logger)."""
        if self.error:
            return self.error
        data = self.json()
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            return data["error"][:200]
        return self.body[:200].decode(errors="replace")


class Api:
    def __init__(self, base, token, timeout=5):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token or self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(resp.status, resp.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read())
        except (OSError, ValueError) as exc:  # URLError, timeouts, connection refused
            return Response(0, error=type(exc).__name__)


# ------------------------------------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------------------------------------
class Metrics:
    def __init__(self, workload):
        self.workload = workload
        self.lock = threading.Lock()
        self.pending = 0
        self.failed = 0
        self.errors = 0
        self.api_up = 0
        self.last_loop = 0.0

    def text(self):
        lab = f'workload="{self.workload}"'
        with self.lock:
            return "\n".join([
                "# HELP dmf_reconcile_pending Desired settings not yet applied (sources not yet present included).",
                "# TYPE dmf_reconcile_pending gauge",
                f"dmf_reconcile_pending{{{lab}}} {self.pending}",
                "# HELP dmf_reconcile_failed Desired settings SwitchFrame refused; not retried until the source reappears.",
                "# TYPE dmf_reconcile_failed gauge",
                f"dmf_reconcile_failed{{{lab}}} {self.failed}",
                "# HELP dmf_reconcile_errors_total Failed API calls and loop errors.",
                "# TYPE dmf_reconcile_errors_total counter",
                f"dmf_reconcile_errors_total{{{lab}}} {self.errors}",
                "# HELP dmf_reconcile_api_up 1 if the last loop reached the SwitchFrame API.",
                "# TYPE dmf_reconcile_api_up gauge",
                f"dmf_reconcile_api_up{{{lab}}} {self.api_up}",
                "# HELP dmf_reconcile_last_loop_timestamp_seconds When the last loop finished.",
                "# TYPE dmf_reconcile_last_loop_timestamp_seconds gauge",
                f"dmf_reconcile_last_loop_timestamp_seconds{{{lab}}} {self.last_loop:.0f}",
            ]) + "\n"

    def serve(self, addr):
        metrics = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/metrics", "/healthz"):
                    self.send_error(404)
                    return
                body = (metrics.text() if self.path == "/metrics" else "ok\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        host, _, port = addr.rpartition(":")
        server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


# ------------------------------------------------------------------------------------------------
# Reconciler
# ------------------------------------------------------------------------------------------------
def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def falsy(v):
    return v is None or v is False or v == 0 or v == ""


class Reconciler:
    def __init__(self, api, config_file, secrets_dir, log, metrics, clock=time.time, managed_file=None):
        self.api = api
        self.config_file = Path(config_file)
        self.secrets_dir = Path(secrets_dir)
        self.log = log
        self.metrics = metrics
        self.clock = clock
        self.config = None
        self.config_fp = None
        self.secrets = {}
        self.op = None                 # (id, token) of this loop's automation operator
        self.api_was_down = False
        self.stale_operators_checked = False
        self.managed_file = Path(managed_file) if managed_file else None
        self.managed = self.load_managed()   # streamIDs of the caller pulls this design created
        self.managed_saved = set(self.managed)
        self.reset("start")

    # -- instance memory -------------------------------------------------------------------
    def reset(self, why):
        """Forget what was applied: SwitchFrame (re)started and remembers none of it."""
        self.applied = {}              # input -> {"gen": ..., "done": set(call index), "failed": set()}
        self.globals_done = set()
        self.globals_failed = set()
        self.dest_done = {}            # name -> fingerprint of the desired config it was created with
        self.dest_failed = {}          # name -> fingerprint SwitchFrame refused
        self.recording_started = False
        self.recording_failed = None   # fingerprint of a refused recording request
        self.caller_keys = {}          # input -> key returned by POST /api/sources
        self.state_seen = False        # a full pass has seen this run's sources
        self.last_seq = None
        if why != "start":
            self.log("info", "SwitchFrame restarted: applying the configuration again", reason=why)

    # -- the caller pulls this reconciler manages --------------------------------------------
    def load_managed(self):
        """Unreadable or invalid means nothing is managed: a pull is left behind rather than an
        operator's being deleted."""
        if not self.managed_file:
            return set()
        try:
            ids = json.loads(self.managed_file.read_text())["streamIDs"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("streamIDs is not a list of strings")
            return set(ids)
        except FileNotFoundError:
            return set()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error("managed callers file unreadable; removing no pulls it listed", file=str(self.managed_file),
                       error=f"{type(exc).__name__}: {exc}")
            return set()

    def save_managed(self):
        """Atomically, and retried on the next loop if it fails."""
        if self.managed == self.managed_saved or not self.managed_file:
            self.managed_saved = set(self.managed)
            return
        tmp = self.managed_file.with_name(self.managed_file.name + ".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump({"streamIDs": sorted(self.managed)}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.managed_file)
            self.managed_saved = set(self.managed)
        except OSError as exc:
            self.error("could not save the managed callers file", file=str(self.managed_file), error=type(exc).__name__)

    # -- inputs ----------------------------------------------------------------------------
    def load(self):
        try:
            raw = self.config_file.read_bytes()
        except OSError as exc:
            if self.config is None:
                self.log("warning", "no config yet", file=str(self.config_file), error=type(exc).__name__)
            return
        fp = hashlib.sha256(raw).hexdigest()
        if fp != self.config_fp:
            try:
                config = json.loads(raw)
                if not isinstance(config, dict):
                    raise ValueError("not an object")
            except ValueError as exc:
                self.error("config is not valid JSON; keeping the last good one", error=str(exc))
                self.config_fp = fp
                return
            self.log("info", "config loaded", revision=config.get("revision"), sources=len(config.get("sources") or []),
                     destinations=len(config.get("destinations") or []))
            self.config, self.config_fp = config, fp
        secrets = {}
        if self.secrets_dir.is_dir():
            for f in self.secrets_dir.iterdir():
                if f.name.startswith(".") or not f.is_file():
                    continue          # Kubernetes' ..data symlinks
                try:
                    secrets[f.name] = f.read_text()
                except OSError:
                    pass
        self.secrets = secrets
        self.log.secrets = [v for v in [self.api.token, *secrets.values()] if v]

    def error(self, msg, **fields):
        with self.metrics.lock:
            self.metrics.errors += 1
        self.log("error", msg, **fields)

    # -- operator --------------------------------------------------------------------------
    def operator(self):
        """A short-lived director, registered on the first command of a loop (as the chart's
        postStart hook did): commands need an identified operator once operators exist."""
        if self.op:
            return self.op[1]
        name = f"dmf-automation-{int(self.clock())}"
        for attempt in range(3):
            r = self.api.call("POST", "/api/operator/register", {"name": name if not attempt else f"{name}-{attempt}",
                                                                 "role": "director"})
            if r.ok and isinstance(r.json(), dict) and r.json().get("token"):
                self.op = (r.json().get("id"), r.json()["token"])
                return self.op[1]
            if r.status != 409:
                break
        self.error("operator registration failed; using the API token", status=r.status, error=r.message())
        return None

    def remove_stale_operators(self):
        """Once per start. A pod killed during a loop leaves its automation operator behind, and
        SwitchFrame keeps operators across restarts (operators.json), so they would pile up."""
        r = self.api.call("GET", "/api/operator/list")
        operators = r.json() if r.ok else None
        if not isinstance(operators, list):
            return                     # tried again next loop
        self.stale_operators_checked = True
        for o in operators:
            if not AUTOMATION_OPERATOR.match(str((o or {}).get("name", ""))):
                continue
            rr = self.command("DELETE", f"/api/operator/{urllib.parse.quote(str(o.get('id')), safe='')}")
            if rr.ok or rr.status == 404:
                self.log("info", "removed an automation operator left by an interrupted loop", operator=o.get("name"))
            else:
                self.error("could not remove a stale automation operator", operator=o.get("name"), status=rr.status)

    def release_operator(self):
        if self.op:
            op_id, token = self.op
            self.op = None
            if op_id:
                r = self.api.call("DELETE", f"/api/operator/{urllib.parse.quote(op_id, safe='')}", token=token)
                if not r.ok and r.status != 404:
                    self.log("warning", "could not delete the automation operator", status=r.status)

    def command(self, method, path, body=None):
        return self.api.call(method, path, body, token=self.operator())

    # -- source keys -----------------------------------------------------------------------
    def srt_caller_key(self, sources, stream_id):
        """A caller's key is runtime-only (01-contract.md): find it by streamID in state."""
        for key, info in sources.items():
            srt = (info or {}).get("srt") or {}
            if srt.get("mode") == "caller" and srt.get("streamID") == stream_id:
                return key
        return None

    def key_of(self, desired, sources):
        ref = desired.get("ref") or {}
        if ref.get("kind") == "srt-caller":
            return self.srt_caller_key(sources, ref.get("streamID")) or self.caller_keys.get(desired.get("input"))
        return ref.get("key")

    def generation(self, desired, info):
        """What identifies one appearance of a source; None while it can't take settings.

        SwitchFrame registers an SRT source afresh on every connection (wireSRTSource unregisters
        and re-registers it), which resets its label, position and keyer, so an SRT source takes
        its settings once per connection: reconnectCount counts the disconnections."""
        if info is None:
            return None
        if (desired.get("ref") or {}).get("kind", "mxl").startswith("srt"):
            srt = info.get("srt") or {}
            if not srt.get("connected"):
                return None
            return ("srt", srt.get("reconnectCount", 0))
        return ("present",)

    def substitute(self, obj, key, keys):
        if isinstance(obj, str):
            def one(m):
                if m.group(1) is None:
                    return key
                k = keys.get(m.group(1))
                if not k:
                    raise Unresolved(m.group(1))
                return k
            return PLACEHOLDER.sub(one, obj)
        if isinstance(obj, list):
            return [self.substitute(v, key, keys) for v in obj]
        if isinstance(obj, dict):
            return {k: self.substitute(v, key, keys) for k, v in obj.items()}
        return obj

    def run_call(self, call, key, keys):
        """Returns 'ok', 'retry' or 'failed'."""
        try:
            path = self.substitute(call["path"], urllib.parse.quote(key or "", safe=":"),
                                   {k: urllib.parse.quote(v, safe=":") for k, v in keys.items() if v})
            body = self.substitute(call.get("body"), key, keys) if "body" in call else None
        except Unresolved as exc:
            self.log("info", "waiting for a source a setting refers to", path=call.get("path"), source=str(exc))
            return "retry"
        r = self.command(call["method"], path, body)
        if r.ok or (call["method"] == "DELETE" and r.status == 404):
            self.log("info", "applied", method=call["method"], path=path)
            return "ok"
        self.error("call failed", method=call["method"], path=path, status=r.status, error=r.message())
        if r.status == 0 or r.status >= 500 or r.status in RETRY_STATUSES:
            return "retry"
        return "failed"

    # -- one loop --------------------------------------------------------------------------
    def step(self):
        """One reconcile pass. Returns the number of pending items (also published as a metric)."""
        self.load()
        r = self.api.call("GET", "/api/switch/state")
        state = r.json() if r.ok else None
        if not isinstance(state, dict):
            if not self.api_was_down:
                self.log("warning", "SwitchFrame API not reachable", status=r.status, error=r.message())
            self.api_was_down = True
            with self.metrics.lock:
                self.metrics.api_up = 0
            return self.publish(None, 0)
        if self.api_was_down:
            # Unreachable, then back: the container restarted (or never answered yet).
            self.api_was_down = False
            self.reset("API came back")
        seq = state.get("seq")
        if isinstance(seq, int):
            if self.last_seq is not None and seq < self.last_seq:
                self.reset("state sequence went back")
            self.last_seq = seq
        with self.metrics.lock:
            self.metrics.api_up = 1
        if self.config is None:
            return self.publish(None, 0)

        pending = failed = 0
        try:
            if not self.stale_operators_checked:
                self.remove_stale_operators()
            p, f = self.sources(state)
            pending, failed = pending + p, failed + f
            p, f = self.global_calls()
            pending, failed = pending + p, failed + f
            p, f = self.destinations()
            pending, failed = pending + p, failed + f
            p, f = self.recording(state)
            pending, failed = pending + p, failed + f
            pending += self.program()
        finally:
            self.release_operator()
        return self.publish(pending, failed)

    def publish(self, pending, failed):
        with self.metrics.lock:
            if pending is not None:
                self.metrics.pending = pending
                self.metrics.failed = failed
            else:
                # Nothing could be checked: everything desired is pending.
                cfg = self.config or {}
                self.metrics.pending = max(1, len(cfg.get("sources") or []) + len(cfg.get("destinations") or []) +
                                           (1 if cfg.get("recording") else 0) + (1 if cfg.get("program") else 0))
            self.metrics.last_loop = self.clock()
            return self.metrics.pending

    def fresh_sources(self):
        r = self.api.call("GET", "/api/sources")
        data = r.json() if r.ok else None
        return data if isinstance(data, dict) else None

    def sources(self, state):
        cfg = self.config
        sources = state.get("sources") or {}
        desired = cfg.get("sources") or []
        pending = failed = 0

        # 3. SRT caller pulls the design used to have and no longer does. SwitchFrame persists every
        # pull in srt_sources.json and restores it at start, so a removed one would otherwise stay.
        # Only stream IDs recorded here are ever removed: a pull an operator added isn't in the file.
        # A wanted ID is recorded before its pull is created, so a crash can't orphan the pull.
        wanted_streams = {(s.get("ref") or {}).get("streamID") for s in desired
                          if (s.get("ref") or {}).get("kind") == "srt-caller"}
        self.managed |= wanted_streams
        present = set()
        for key, info in list(sources.items()):
            srt = (info or {}).get("srt") or {}
            if srt.get("mode") != "caller":
                continue
            stream = srt.get("streamID")
            present.add(stream)
            if stream in wanted_streams or stream not in self.managed:
                continue
            r = self.command("DELETE", f"/api/sources/{urllib.parse.quote(key, safe=':')}")
            if r.ok or r.status == 404:
                self.log("info", "removed SRT caller pull no longer in the design", key=key, streamID=stream)
                sources.pop(key, None)
                self.managed.discard(stream)
            else:
                self.error("could not remove SRT caller pull", key=key, status=r.status, error=r.message())
                pending += 1
        # Already gone (an operator removed it): forget it, so a pull an operator adds later under
        # that ID is theirs. Not on a run's first pass, which might precede SwitchFrame's restore.
        if self.state_seen:
            self.managed -= {i for i in self.managed if i not in wanted_streams and i not in present}
        self.state_seen = True
        self.save_managed()

        # Callers to create: SwitchFrame restores persisted ones itself, so only create a pull whose
        # streamID isn't in state (one source, never two).
        created = False
        for s in desired:
            ref = s.get("ref") or {}
            if ref.get("kind") != "srt-caller" or self.srt_caller_key(sources, ref.get("streamID")):
                continue
            r = self.command("POST", "/api/sources", ref.get("create"))
            body = r.json() if r.ok else None
            if r.ok and isinstance(body, dict) and body.get("key"):
                self.caller_keys[s.get("input")] = body["key"]
                self.log("info", "created SRT caller pull", input=s.get("input"), key=body["key"],
                         streamID=ref.get("streamID"))
                created = True
            else:
                self.error("could not create SRT caller pull", input=s.get("input"), status=r.status, error=r.message())
        if created:
            sources = self.fresh_sources() or sources

        keys = {s.get("input"): self.key_of(s, sources) for s in desired}
        # Only sources that are present can be referred to (a keyer's fill source).
        live = {i: k for i, k in keys.items() if k and k in sources}
        for s in desired:
            inp = s.get("input")
            key = keys.get(inp)
            calls = s.get("apply") or []
            info = sources.get(key) if key else None
            gen = self.generation(s, info)
            if gen is None:
                pending += 1 if calls or (s.get("ref") or {}).get("kind") == "srt-caller" else 0
                continue
            mem = self.applied.get(inp)
            if mem is None or mem["gen"] != gen or mem["key"] != key:
                if mem is not None:
                    self.log("info", "source reappeared: applying its settings again", input=inp, key=key)
                mem = {"gen": gen, "key": key, "done": set(), "failed": set()}
                self.applied[inp] = mem
            for i, call in enumerate(calls):
                if i in mem["done"] or i in mem["failed"]:
                    continue
                result = self.run_call(call, key, live)
                if result == "ok":
                    mem["done"].add(i)
                elif result == "failed":
                    mem["failed"].add(i)
            left = len(calls) - len(mem["done"]) - len(mem["failed"])
            pending += left
            failed += len(mem["failed"])
        return pending, failed

    def global_calls(self):
        calls = self.config.get("global") or []
        for i, call in enumerate(calls):
            if i in self.globals_done or i in self.globals_failed:
                continue
            result = self.run_call(call, None, {})
            if result == "ok":
                self.globals_done.add(i)
            elif result == "failed":
                self.globals_failed.add(i)
        return len(calls) - len(self.globals_done) - len(self.globals_failed), len(self.globals_failed)

    # -- destinations ----------------------------------------------------------------------
    def desired_destination(self, d):
        """The full request body with its secrets, or None when a secret is missing."""
        body = dict(d.get("body") or {})
        for field, name in (d.get("secretFields") or {}).items():
            if name not in self.secrets:
                return None
            body[field] = self.secrets[name].strip()
        if d.get("secretUrlSuffix"):
            name = d["secretUrlSuffix"]
            if name not in self.secrets:
                return None
            body["url"] = body.get("url", "").rstrip("/") + "/" + self.secrets[name].strip()
        return body

    def destinations(self):
        desired = self.config.get("destinations") or []
        r = self.api.call("GET", "/api/output/destinations")
        existing = r.json() if r.ok else None
        if not isinstance(existing, list):
            if desired:
                self.error("could not list destinations", status=r.status, error=r.message())
            return len(desired), 0
        by_name = {}
        for d in existing:
            by_name.setdefault(((d or {}).get("config") or {}).get("name"), []).append(d)
        pending = failed = 0
        wanted = set()
        for d in desired:
            name = d.get("name")
            wanted.add(name)
            body = self.desired_destination(d)
            if body is None:
                self.error("destination secret missing from the workload-refs Secret", destination=name)
                pending += 1
                continue
            fp = fingerprint(body)
            if self.dest_failed.get(name) == fp:
                failed += 1
                continue
            current = by_name.get(name) or []
            if self.dest_done.get(name) == fp and current:
                continue                     # created on this instance with this config
            if self.dest_done.get(name) == fp and not current:
                continue                     # an operator removed it: leave it removed
            same = [c for c in current if self.matches(c.get("config") or {}, body)]
            if same and name not in self.dest_done:
                # Already there with the desired config (created before a reconciler restart).
                self.dest_done[name] = fp
                continue
            # Missing, or its desired config changed: (re)create it.
            for c in current:
                rr = self.command("DELETE", f"/api/output/destinations/{urllib.parse.quote(str(c.get('id')), safe='')}")
                if rr.ok or rr.status == 404:
                    self.log("info", "removed destination to recreate it with the changed config", destination=name)
                else:
                    self.error("could not remove destination", destination=name, status=rr.status, error=rr.message())
            rr = self.command("POST", "/api/output/destinations", body)
            created = rr.json() if rr.ok else None
            if not (rr.ok and isinstance(created, dict) and created.get("id")):
                self.error("could not create destination", destination=name, status=rr.status, error=rr.message())
                if 400 <= rr.status < 500 and rr.status not in RETRY_STATUSES:
                    self.dest_failed[name] = fp
                    failed += 1
                else:
                    pending += 1
                continue
            dest_id = urllib.parse.quote(str(created["id"]), safe="")
            rs = self.command("POST", f"/api/output/destinations/{dest_id}/start")
            if rs.ok or rs.status == 409:
                self.log("info", "destination created and started", destination=name, type=body.get("type"))
                self.dest_done[name] = fp
            else:
                # Remove it again, so the next loop creates and starts it afresh.
                self.error("could not start destination", destination=name, status=rs.status, error=rs.message())
                self.command("DELETE", f"/api/output/destinations/{dest_id}")
                pending += 1
        # Destinations this design once had (compiler names) but no longer does.
        for name, current in by_name.items():
            if name in wanted or not name or not DEST_NAME.match(name):
                continue
            for c in current:
                rr = self.command("DELETE", f"/api/output/destinations/{urllib.parse.quote(str(c.get('id')), safe='')}")
                if rr.ok or rr.status == 404:
                    self.log("info", "removed destination no longer in the design", destination=name)
                    self.dest_done.pop(name, None)
                else:
                    self.error("could not remove destination", destination=name, status=rr.status)
                    pending += 1
        return pending, failed

    @staticmethod
    def matches(current, desired):
        """SwitchFrame returns its config with omitempty: an absent field is false, 0 or ""."""
        for k, v in desired.items():
            c = current.get(k)
            if falsy(v) and falsy(c):
                continue
            if c != v:
                return False
        return True

    # -- recording and programme -----------------------------------------------------------
    def recording(self, state):
        req = self.config.get("recording")
        if not req:
            return 0, 0
        fp = fingerprint(req)
        if self.recording_failed == fp:
            return 0, 1
        status = state.get("recording") or {}
        if status.get("active"):
            self.recording_started = True
            return 0, 0
        if status.get("error"):
            # Failed (a write error, say): SwitchFrame keeps the failed recorder until it's stopped.
            self.error("recording failed; restarting it", error=status.get("error"))
            self.command("POST", "/api/recording/stop")
        elif self.recording_started:
            return 0, 0          # stopped cleanly, by an operator or by FINALISE: leave it stopped
        r = self.command(req.get("method", "POST"), req.get("path", "/api/recording/start"), req.get("body"))
        if r.ok or (r.status == 409 and "active" in r.message()):
            self.log("info", "recording started", outputDir=(req.get("body") or {}).get("outputDir"))
            self.recording_started = True
            return 0, 0
        self.error("could not start recording", status=r.status, error=r.message())
        if 400 <= r.status < 500 and r.status not in RETRY_STATUSES:
            self.recording_failed = fp
            return 0, 1
        return 1, 0

    def program(self):
        prog = self.config.get("program")
        if not prog:
            return 0
        r = self.api.call("GET", "/api/switch/state")
        state = r.json() if r.ok else None
        if not isinstance(state, dict):
            return 1
        if state.get("programSource"):
            return 0
        sources = state.get("sources") or {}
        key = self.key_of(prog, sources)
        if not key or self.generation(prog, sources.get(key)) is None:
            return 1             # its source isn't there (or connected) yet
        rc = self.command("POST", "/api/switch/cut", {"source": key})
        if rc.ok:
            self.log("info", "programme on air", source=key)
            return 0
        self.error("cut to programme failed", source=key, status=rc.status, error=rc.message())
        return 1

    def stop_recording(self):
        """FINALISE: stop the recording so its last file is closed before it's copied."""
        r = self.command("POST", "/api/recording/stop")
        self.release_operator()
        if r.ok or r.status == 409:
            self.log("info", "recording stopped" if r.ok else "recording was not active")
            return 0
        self.error("could not stop recording", status=r.status, error=r.message())
        return 1


def main(argv):
    log = Log()
    token = os.environ.get("SWITCHFRAME_API_TOKEN", "")
    if not token:
        log("error", "SWITCHFRAME_API_TOKEN is not set; waiting (the reconciler never exits)")
    log.secrets = [token] if token else []
    api = Api(os.environ.get("SF_API_URL", "http://127.0.0.1:8081"), token)
    metrics = Metrics(os.environ.get("WORKLOAD", ""))
    rec = Reconciler(api, os.environ.get("CONFIG_FILE", "/etc/switchframe/config/config.json"),
                     os.environ.get("SECRETS_DIR", "/etc/switchframe/secrets"), log, metrics,
                     managed_file=os.environ.get("MANAGED_CALLERS_FILE", "/var/lib/switchframe/dmf-reconciler-callers.json"))
    if argv[1:2] == ["stop-recording"]:
        rec.load()
        return rec.stop_recording()

    metrics.serve(os.environ.get("METRICS_ADDR", "0.0.0.0:9103"))
    fast, window, slow = (float(os.environ.get(k, d)) for k, d in
                          (("FAST_SECONDS", 5), ("FAST_WINDOW_SECONDS", 120), ("SLOW_SECONDS", 30)))
    started = time.monotonic()
    log("info", "reconciler started", api=api.base, config=str(rec.config_file))
    while True:
        try:
            if token:
                rec.step()
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            rec.op = None
            rec.error("loop error", error=f"{type(exc).__name__}: {exc}")
        time.sleep(fast if time.monotonic() - started < window else slow)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
