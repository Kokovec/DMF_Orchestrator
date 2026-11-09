"""sf-reconciler against a fake SwitchFrame API (stdlib unittest; run by scripts/validate.sh).

The fake follows SwitchFrame at the pinned commit 79236154 where it matters here: an SRT caller's key
is srt:<streamID> and is pre-registered with srt.mode/streamID before it connects; an SRT source is
registered afresh (settings reset) on every connection; destinations come back from GET with their
passphrase and full RTMP URL; mutating calls need an identified operator once one exists.
"""
import io
import json
import re
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location("reconciler", Path(__file__).with_name("reconciler.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

TOKEN = "api-token-0123456789"
PASSPHRASE = "a-long-srt-passphrase"
STREAM_KEY = "abcd-efgh-ijkl"
CAM1 = "mxl:817de3a0-f16a-5268-9d73-6b157c4b3611"
CAM2 = "mxl:2d0d6a28-4be2-5019-815b-e137d99e7cb9"


class FakeSwitchFrame:
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 1
        self.sources = {}
        self.program = ""
        self.recording = {"active": False}
        self.destinations = []
        self.next_id = 1
        self.operators = {}
        self.calls = []          # (method, path, body)
        self.down = False
        self.refuse = {}         # (method, path regex) -> status
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # -- helpers for tests ---------------------------------------------------------------
    def add_mxl(self, key, label=None):
        self.sources[key] = {"key": key, "label": label or key.upper(), "type": "mxl", "status": "healthy",
                             "position": len(self.sources) + 1}

    def connect_srt(self, key, mode, stream_id):
        """wireSRTSource: unregister and register afresh, which resets label, position and keyer."""
        old = self.sources.get(key)
        reconnects = old["srt"]["reconnectCount"] if old else 0
        label = old.get("createLabel") if old else None
        self.sources[key] = {"key": key, "label": label or key[4:], "type": "srt", "status": "healthy",
                             "position": len(self.sources) + 1, "createLabel": label,
                             "srt": {"mode": mode, "streamID": stream_id, "connected": True,
                                     "reconnectCount": reconnects}}

    def disconnect_srt(self, key):
        self.sources[key]["srt"]["connected"] = False
        self.sources[key]["srt"]["reconnectCount"] += 1

    def restart(self):
        """SwitchFrame restarts: forgets everything but persisted callers (srt_sources.json)."""
        callers = {k: v for k, v in self.sources.items() if (v.get("srt") or {}).get("mode") == "caller"}
        self.sources = {}
        for k, v in callers.items():
            self.sources[k] = {"key": k, "label": v.get("createLabel") or k[4:], "type": "srt", "status": "offline",
                               "position": len(self.sources) + 1, "createLabel": v.get("createLabel"),
                               "srt": {"mode": "caller", "streamID": v["srt"]["streamID"], "connected": False,
                                       "reconnectCount": 0}}
        self.program = ""
        self.recording = {"active": False}
        self.destinations = []
        self.operators = {}
        self.seq = 1

    def mutations(self, path_re=".*"):
        return [(m, p, b) for m, p, b in self.calls if m != "GET" and re.search(path_re, p)
                and not p.startswith("/api/operator/")]

    def state(self):
        return {"seq": self.seq, "programSource": self.program, "sources": self.sources,
                "recording": self.recording,
                "destinations": [{"id": d["id"], "name": d["config"].get("name"), "url": d["config"].get("url", ""),
                                  "state": d["state"]} for d in self.destinations]}

    def handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self, method):
                if fake.down:
                    self.close_connection = True
                    self.connection.close()
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                path = self.path
                auth = self.headers.get("Authorization", "")[7:]
                with fake.lock:
                    fake.calls.append((method, path, body))
                    for (m, rx), status in fake.refuse.items():
                        if m == method and re.search(rx, path):
                            # Echo the request, secrets and all, as a careless server might.
                            return self.reply(status, {"error": f"refused {json.dumps(body)}"})
                    if auth != TOKEN and auth not in {o["token"] for o in fake.operators.values()}:
                        return self.reply(401, {"error": "unauthorized"})
                    if method != "GET" and not path.startswith("/api/operator/") and fake.operators \
                            and auth not in {o["token"] for o in fake.operators.values()}:
                        return self.reply(403, {"error": "operator not identified"})
                    return self.route(method, path, body)

            def route(self, method, path, body):
                if (method, path) == ("GET", "/api/switch/state"):
                    fake.seq += 1
                    return self.reply(200, fake.state())
                if (method, path) == ("GET", "/api/sources"):
                    return self.reply(200, fake.sources)
                if (method, path) == ("POST", "/api/operator/register"):
                    if any(o["name"] == body["name"] for o in fake.operators.values()):
                        return self.reply(409, {"error": "duplicate name"})
                    oid = f"op{len(fake.operators) + 1}-{body['name']}"
                    fake.operators[oid] = {"id": oid, "name": body["name"], "token": f"tok-{oid}"}
                    return self.reply(200, {**fake.operators[oid], "role": body["role"]})
                if (method, path) == ("GET", "/api/operator/list"):
                    return self.reply(200, [{"id": o["id"], "name": o["name"], "role": "director"}
                                            for o in fake.operators.values()])
                m = re.match(r"^/api/operator/([^/]+)$", path)
                if method == "DELETE" and m:
                    return self.reply(200, {"ok": True}) if fake.operators.pop(m.group(1), None) else self.reply(404)
                if (method, path) == ("POST", "/api/sources"):
                    key = "srt:" + body["streamID"]
                    if key in fake.sources:
                        return self.reply(409, {"error": "pull already exists"})
                    fake.sources[key] = {"key": key, "label": body.get("label") or key[4:], "type": "srt",
                                         "status": "offline", "position": len(fake.sources) + 1,
                                         "createLabel": body.get("label"),
                                         "srt": {"mode": "caller", "streamID": body["streamID"], "connected": False,
                                                 "reconnectCount": 0}}
                    return self.reply(201, {"key": key})
                m = re.match(r"^/api/sources/([^/]+)(/.*)?$", path)
                if m:
                    key = urllib.request.unquote(m.group(1))
                    if key not in fake.sources:
                        return self.reply(404, {"error": "source not found"})
                    if method == "DELETE" and not m.group(2):
                        del fake.sources[key]
                        return self.reply(204)
                    if m.group(2) == "/label":
                        fake.sources[key]["label"] = body["label"]
                    if m.group(2) == "/position":
                        fake.sources[key]["position"] = body["position"]
                    return self.reply(200, fake.state())
                if (method, path) == ("POST", "/api/switch/cut"):
                    if body["source"] not in fake.sources:
                        return self.reply(404, {"error": "source not found"})
                    fake.program = body["source"]
                    return self.reply(200, fake.state())
                if (method, path) == ("GET", "/api/output/destinations"):
                    return self.reply(200, fake.destinations)
                if (method, path) == ("POST", "/api/output/destinations"):
                    d = {"id": f"dest-{fake.next_id}", "config": body, "state": "stopped"}
                    fake.next_id += 1
                    fake.destinations.append(d)
                    return self.reply(201, d)
                m = re.match(r"^/api/output/destinations/([^/]+)(/start)?$", path)
                if m:
                    d = next((d for d in fake.destinations if d["id"] == m.group(1)), None)
                    if d is None:
                        return self.reply(404, {"error": "destination not found"})
                    if method == "DELETE":
                        fake.destinations.remove(d)
                        return self.reply(204)
                    d["state"] = "connected"
                    return self.reply(200, d)
                if (method, path) == ("POST", "/api/recording/start"):
                    if fake.recording.get("active") or fake.recording.get("error"):
                        return self.reply(409, {"error": "recorder already active"})
                    fake.recording = {"active": True, "filename": "/recordings/x.ts"}
                    return self.reply(200, fake.recording)
                if (method, path) == ("POST", "/api/recording/stop"):
                    if not (fake.recording.get("active") or fake.recording.get("error")):
                        return self.reply(409, {"error": "recorder not active"})
                    fake.recording = {"active": False}
                    return self.reply(200, fake.recording)
                if method in ("PUT", "POST"):
                    return self.reply(200, {})
                return self.reply(404, {"error": "no route"})

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

            def do_PUT(self):
                self.handle_any("PUT")

            def do_DELETE(self):
                self.handle_any("DELETE")

        return H


def label(text):
    return {"method": "POST", "path": "/api/sources/{key}/label", "body": {"label": text}}


def config(**over):
    cfg = {
        "apiVersion": "dmf.local/v1alpha1", "kind": "SwitchFrameConfig", "workload": "t", "revision": 1,
        "program": {"input": "mxl-src", "ref": {"kind": "mxl", "key": CAM1}},
        "sources": [
            {"input": "mxl-src", "ref": {"kind": "mxl", "key": CAM1}, "apply": [label("Camera 1")]},
            {"input": "mxl-src_2", "ref": {"kind": "mxl", "key": CAM2},
             "apply": [label("Camera 2"), {"method": "PUT", "path": "/api/sources/{key}/position", "body": {"position": 2}}]},
        ],
        "global": [{"method": "PUT", "path": "/api/audio/limiter", "body": {"threshold": -6, "ceiling": -2}}],
        "destinations": [
            {"name": "srt-out", "body": {"type": "srt-caller", "name": "srt-out", "address": "172.17.0.1", "port": 9100,
                                         "latency": 120, "streamID": "pgm", "encryption": "aes-128",
                                         "maxBandwidth": 0, "scte35Enabled": False},
             "secretFields": {"passphrase": "venue-srt"}},
            {"name": "rtmp-out", "body": {"type": "rtmp", "name": "rtmp-out", "url": "rtmp://172.17.0.1/live"},
             "secretUrlSuffix": "yt-key"},
        ],
        "recording": {"method": "POST", "path": "/api/recording/start",
                      "body": {"outputDir": "/recordings", "rotateAfterMins": 1, "filenamePrefix": "t"}},
    }
    cfg.update(over)
    return cfg


class ReconcilerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSwitchFrame()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.config_file = d / "config.json"
        self.secrets = d / "secrets"
        self.managed_file = d / "managed-callers.json"
        self.secrets.mkdir()
        (self.secrets / "venue-srt").write_text(PASSPHRASE)
        (self.secrets / "yt-key").write_text(STREAM_KEY)
        self.write_config(config())
        self.out = io.StringIO()
        self.metrics = rc.Metrics("t")
        self.now = [1_700_000_000]
        self.rec = self.new_reconciler()

    def new_reconciler(self):
        return rc.Reconciler(rc.Api(self.fake.url, TOKEN), self.config_file, self.secrets, rc.Log(self.out),
                             self.metrics, clock=lambda: self.now[0], managed_file=self.managed_file)

    def operator_pull(self, stream_id):
        """A pull added through the UI: SwitchFrame can't tell it from one the design made."""
        self.fake.sources[f"srt:{stream_id}"] = {
            "key": f"srt:{stream_id}", "label": stream_id, "type": "srt", "status": "healthy", "position": 9,
            "srt": {"mode": "caller", "streamID": stream_id, "connected": True, "reconnectCount": 0}}

    def managed(self):
        return json.loads(self.managed_file.read_text())["streamIDs"]

    def both(self):
        self.fake.add_mxl(CAM1)
        self.fake.add_mxl(CAM2)

    def write_config(self, cfg):
        self.config_file.write_text(json.dumps(cfg))

    def step(self):
        self.now[0] += 5
        return self.rec.step()

    def logs(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    # -- the spec's cases ----------------------------------------------------------------
    def test_first_run(self):
        self.both()
        self.assertEqual(self.step(), 0)
        f = self.fake
        self.assertEqual(f.sources[CAM1]["label"], "Camera 1")
        self.assertEqual(f.sources[CAM2]["label"], "Camera 2")
        self.assertEqual(f.sources[CAM2]["position"], 2)
        self.assertEqual(f.program, CAM1)
        self.assertTrue(f.recording["active"])
        self.assertEqual(len(f.mutations("^/api/audio/limiter$")), 1)
        by_name = {d["config"]["name"]: d for d in f.destinations}
        self.assertEqual(by_name["srt-out"]["config"]["passphrase"], PASSPHRASE)
        self.assertEqual(by_name["rtmp-out"]["config"]["url"], f"rtmp://172.17.0.1/live/{STREAM_KEY}")
        self.assertEqual({d["state"] for d in f.destinations}, {"connected"})
        self.assertEqual(f.operators, {}, "the automation operator is deleted after the loop")
        # A second loop changes nothing.
        before = len(f.mutations())
        self.assertEqual(self.step(), 0)
        self.assertEqual(len(f.mutations()), before)

    def test_srt_listener_source_appears_late(self):
        cfg = config(program={"input": "srt-src", "ref": {"kind": "srt-listener", "key": "srt:venue1"}})
        cfg["sources"].append({"input": "srt-src", "ref": {"kind": "srt-listener", "key": "srt:venue1"},
                               "apply": [label("Venue 1")]})
        self.write_config(cfg)
        self.both()
        self.assertEqual(self.step(), 2, "the listener's label and the programme cut wait for its encoder")
        self.assertEqual(self.fake.mutations("srt:venue1"), [])
        self.assertEqual(self.fake.program, "")
        self.fake.connect_srt("srt:venue1", "listener", "venue1")
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources["srt:venue1"]["label"], "Venue 1")
        self.assertEqual(self.fake.program, "srt:venue1")

    def test_srt_source_reconnect_reapplies(self):
        cfg = config()
        cfg["sources"].append({"input": "srt-src", "ref": {"kind": "srt-listener", "key": "srt:venue1"},
                               "apply": [label("Venue 1")]})
        self.write_config(cfg)
        self.both()
        self.fake.connect_srt("srt:venue1", "listener", "venue1")
        self.step()
        self.fake.disconnect_srt("srt:venue1")
        self.assertEqual(self.step(), 1, "disconnected: its settings wait for the next connection")
        self.fake.connect_srt("srt:venue1", "listener", "venue1")    # SwitchFrame resets the label
        self.assertEqual(self.fake.sources["srt:venue1"]["label"], "venue1")
        self.step()
        self.assertEqual(self.fake.sources["srt:venue1"]["label"], "Venue 1")
        self.assertEqual(len(self.fake.mutations("srt:venue1/label")), 2)

    def caller_config(self):
        cfg = config()
        cfg["sources"].append({"input": "srt-src_2", "ref": {"kind": "srt-caller", "streamID": "venue2", "create": {
            "type": "srt", "mode": "caller", "address": "172.17.0.1:9000", "streamID": "venue2", "label": "Venue 2",
            "latencyMs": 200}}, "apply": [label("Venue 2"),
                                          {"method": "PUT", "path": "/api/sources/{key}/position", "body": {"position": 3}}]})
        return cfg

    def test_srt_caller_created_once_and_configured_when_connected(self):
        self.write_config(self.caller_config())
        self.both()
        self.assertEqual(self.step(), 1, "caller created; its settings wait until it connects")
        self.assertEqual(len(self.fake.mutations("^/api/sources$")), 1)
        self.step()
        self.assertEqual(len(self.fake.mutations("^/api/sources$")), 1, "never a second pull")
        self.fake.connect_srt("srt:venue2", "caller", "venue2")
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources["srt:venue2"]["position"], 3)

    def test_restart_with_persisted_caller(self):
        self.write_config(self.caller_config())
        self.both()
        self.step()
        self.fake.connect_srt("srt:venue2", "caller", "venue2")
        self.step()
        # The pod restarts: SwitchFrame restores the pull from srt_sources.json; a new reconciler starts.
        self.fake.restart()
        self.both()
        self.fake.calls.clear()
        self.rec = self.new_reconciler()
        self.step()
        self.assertEqual(self.fake.mutations("^/api/sources$"), [], "the persisted pull is not created again")
        self.assertEqual([k for k in self.fake.sources if k.startswith("srt:")], ["srt:venue2"])
        self.fake.connect_srt("srt:venue2", "caller", "venue2")
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources["srt:venue2"]["label"], "Venue 2")

    def test_removed_caller(self):
        self.write_config(self.caller_config())
        self.both()
        self.step()
        self.assertIn("srt:venue2", self.fake.sources)
        self.write_config(config())          # caller removed from the design
        self.step()
        self.assertNotIn("srt:venue2", self.fake.sources)
        self.assertEqual(self.fake.mutations("^/api/sources/srt:venue2$")[0][0], "DELETE")

    def test_operator_added_caller_survives_loops_and_a_restart(self):
        self.write_config(self.caller_config())
        self.both()
        self.operator_pull("studio-guest")
        for _ in range(3):
            self.step()
        self.assertIn("srt:studio-guest", self.fake.sources)
        self.assertEqual(self.fake.mutations("studio-guest"), [])
        self.assertEqual(self.managed(), ["venue2"])
        # The pod restarts: SwitchFrame restores both pulls, and a new reconciler reads the file.
        self.fake.restart()
        self.both()
        self.fake.calls.clear()
        self.rec = self.new_reconciler()
        for _ in range(3):
            self.step()
        self.assertIn("srt:studio-guest", self.fake.sources)
        self.assertEqual(self.fake.mutations("studio-guest"), [])

    def test_design_caller_removed_later_after_a_restart(self):
        self.write_config(self.caller_config())
        self.both()
        self.operator_pull("studio-guest")
        self.step()
        self.assertEqual(self.managed(), ["venue2"])
        self.fake.restart()                  # the pull is restored from srt_sources.json
        self.both()
        self.write_config(config())          # and the new design no longer has it
        self.rec = self.new_reconciler()
        self.step()
        self.assertNotIn("srt:venue2", self.fake.sources)
        self.assertIn("srt:studio-guest", self.fake.sources)
        self.assertEqual(self.managed(), [])

    def test_failed_removal_is_retried_and_stays_managed(self):
        self.write_config(self.caller_config())
        self.both()
        self.step()
        self.write_config(config())
        self.fake.refuse[("DELETE", "^/api/sources/srt:venue2$")] = 500
        self.step()
        self.assertEqual(self.managed(), ["venue2"])
        self.fake.refuse.clear()
        self.step()
        self.assertNotIn("srt:venue2", self.fake.sources)
        self.assertEqual(self.managed(), [])

    def test_caller_is_recorded_before_it_is_created(self):
        self.write_config(self.caller_config())
        self.both()
        self.fake.refuse[("POST", "^/api/sources$")] = 500
        self.step()
        self.assertEqual(self.managed(), ["venue2"])

    def test_pull_gone_from_state_is_forgotten(self):
        self.write_config(self.caller_config())
        self.both()
        self.step()
        del self.fake.sources["srt:venue2"]  # an operator removes it, then the design drops it
        self.write_config(config())
        self.step()
        self.assertEqual(self.managed(), [])
        self.operator_pull("venue2")         # the operator's own pull under that ID is left alone
        self.step()
        self.assertIn("srt:venue2", self.fake.sources)

    def test_unreadable_managed_file_removes_nothing(self):
        self.managed_file.write_text("{not json")
        self.both()
        self.operator_pull("studio-guest")
        self.rec = self.new_reconciler()
        self.step()
        self.assertIn("srt:studio-guest", self.fake.sources)
        self.assertIn("managed callers file unreadable", self.out.getvalue())

    def test_operator_change_not_overwritten(self):
        self.both()
        self.step()
        self.fake.sources[CAM1]["label"] = "Operator's name"
        self.fake.program = CAM2
        for _ in range(3):
            self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources[CAM1]["label"], "Operator's name")
        self.assertEqual(self.fake.program, CAM2)
        self.assertEqual(len(self.fake.mutations("/label$")), 2)

    def test_operator_stopped_recording_and_removed_destination_stay_so(self):
        self.both()
        self.step()
        self.fake.recording = {"active": False}
        self.fake.destinations = [d for d in self.fake.destinations if d["config"]["name"] != "rtmp-out"]
        self.step()
        self.assertFalse(self.fake.recording["active"])
        self.assertEqual([d["config"]["name"] for d in self.fake.destinations], ["srt-out"])

    def test_failed_recording_is_restarted(self):
        self.both()
        self.step()
        self.fake.recording = {"active": False, "error": "write /recordings/x.ts: no space left on device"}
        self.step()
        self.assertTrue(self.fake.recording["active"])

    def test_api_down(self):
        self.both()
        self.fake.down = True
        pending = self.step()
        self.assertGreater(pending, 0)
        self.assertEqual(self.metrics.api_up, 0)
        self.step()
        self.fake.down = False
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.metrics.api_up, 1)
        self.assertEqual(self.fake.sources[CAM1]["label"], "Camera 1")

    def test_switchframe_restart_reapplies_everything(self):
        self.both()
        self.step()
        self.fake.down = True              # the switchframe container restarts, the reconciler doesn't
        self.step()
        self.fake.restart()
        self.both()
        self.fake.down = False
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources[CAM1]["label"], "Camera 1")
        self.assertTrue(self.fake.recording["active"])
        self.assertEqual(len(self.fake.destinations), 2)
        self.assertEqual(self.fake.program, CAM1)

    # -- more ----------------------------------------------------------------------------
    def test_destination_recreated_when_its_secret_changes(self):
        self.both()
        self.step()
        old = {d["config"]["name"]: d["id"] for d in self.fake.destinations}
        (self.secrets / "venue-srt").write_text("another-long-passphrase")
        self.step()
        new = {d["config"]["name"]: d for d in self.fake.destinations}
        self.assertNotEqual(new["srt-out"]["id"], old["srt-out"])
        self.assertEqual(new["srt-out"]["config"]["passphrase"], "another-long-passphrase")
        self.assertEqual(new["rtmp-out"]["id"], old["rtmp-out"])
        self.assertEqual(len(self.fake.destinations), 2)

    def test_destination_removed_from_design(self):
        self.both()
        self.step()
        cfg = config()
        cfg["destinations"] = cfg["destinations"][:1]
        self.write_config(cfg)
        self.step()
        self.assertEqual([d["config"]["name"] for d in self.fake.destinations], ["srt-out"])

    def test_existing_matching_destination_is_kept_by_a_new_reconciler(self):
        self.both()
        self.step()
        ids = sorted(d["id"] for d in self.fake.destinations)
        self.rec = self.new_reconciler()
        self.step()
        self.assertEqual(sorted(d["id"] for d in self.fake.destinations), ids)

    def test_missing_secret_keeps_destination_pending(self):
        (self.secrets / "yt-key").unlink()
        self.both()
        self.assertEqual(self.step(), 1)
        self.assertEqual([d["config"]["name"] for d in self.fake.destinations], ["srt-out"])

    def test_refused_call_is_not_retried_every_loop(self):
        self.fake.refuse[("PUT", "/api/audio/limiter")] = 400
        self.both()
        self.assertEqual(self.step(), 0)
        self.step()
        self.assertEqual(len(self.fake.mutations("/api/audio/limiter")), 1)
        self.assertEqual(self.metrics.failed, 1)
        self.assertGreaterEqual(self.metrics.errors, 1)

    def test_fill_source_placeholder_waits_for_its_source(self):
        cfg = config()
        cfg["sources"][0]["apply"].append({"method": "PUT", "path": "/api/sources/{key}/key",
                                           "body": {"type": "luma", "enabled": True, "fillSource": "{key:mxl-src_2}"}})
        self.write_config(cfg)
        self.fake.add_mxl(CAM1)
        self.assertEqual(self.step(), 2, "the key waits for mxl-src_2, which is not there either")
        self.fake.add_mxl(CAM2)
        self.assertEqual(self.step(), 0)
        body = self.fake.mutations("/key$")[-1][2]
        self.assertEqual(body["fillSource"], CAM2)

    def test_secrets_never_logged(self):
        self.fake.refuse[("POST", "/api/output/destinations$")] = 400   # echoes the body back in its error
        self.both()
        self.step()
        text = self.out.getvalue()
        self.assertIn("could not create destination", text)
        self.assertNotIn(PASSPHRASE, text)
        self.assertNotIn(STREAM_KEY, text)
        self.assertNotIn(TOKEN, text)
        log = rc.Log(io.StringIO())
        self.assertEqual(log.redact('{"passphrase": "x", "url": "rtmp://h:1935/app/key"}'),
                         '{"passphrase": "***", "url": "rtmp://h:1935/***"}')

    def test_logs_are_json(self):
        self.both()
        self.step()
        logs = self.logs()
        self.assertTrue(logs)
        self.assertTrue(all({"ts", "level", "msg"} <= set(r) for r in logs))

    def test_metrics_endpoint(self):
        self.both()
        self.step()
        server = self.metrics.serve("127.0.0.1:0")
        self.addCleanup(server.shutdown)
        text = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/metrics").read().decode()
        self.assertIn('dmf_reconcile_pending{workload="t"} 0', text)
        self.assertIn('dmf_reconcile_errors_total{workload="t"} 0', text)
        self.assertIn('dmf_reconcile_api_up{workload="t"} 1', text)

    def test_bad_config_keeps_last_good(self):
        self.both()
        self.step()
        self.config_file.write_text("{not json")
        self.assertEqual(self.step(), 0)
        self.assertIn("config is not valid JSON", self.out.getvalue())

    def test_existing_operators_require_an_identified_operator(self):
        # Once an operator exists, the API token alone can't command; the reconciler registers one.
        self.fake.operators["human"] = {"id": "human", "name": "director1", "token": "human-token"}
        self.both()
        self.assertEqual(self.step(), 0)
        self.assertEqual(self.fake.sources[CAM1]["label"], "Camera 1")
        self.assertEqual(list(self.fake.operators), ["human"])

    def test_stale_automation_operators_removed_once(self):
        self.fake.operators["old"] = {"id": "old", "name": "dmf-automation-1794081472", "token": "old-token"}
        self.fake.operators["human"] = {"id": "human", "name": "director1", "token": "human-token"}
        self.both()
        self.assertEqual(self.step(), 0)
        self.assertEqual(list(self.fake.operators), ["human"])
        self.fake.calls.clear()
        self.step()
        self.assertEqual([c for c in self.fake.calls if c[1] == "/api/operator/list"], [], "only at start")

    def test_stop_recording_for_finalise(self):
        self.both()
        self.step()
        self.assertEqual(self.rec.stop_recording(), 0)
        self.assertFalse(self.fake.recording["active"])
        self.step()
        self.assertFalse(self.fake.recording["active"], "a clean stop isn't undone")


if __name__ == "__main__":
    unittest.main()
