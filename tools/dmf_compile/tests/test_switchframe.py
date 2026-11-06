"""SwitchFrame's flags and request bodies, against the product at the pinned commit 79236154.

- fixtures/switchframe-0.1.0-help.txt is `switchframe --help` from dmf.local/switchframe:0.1.0; its flag
  names are identical to the flag.* calls in server/cmd/switchframe at the pinned commit.
- BODIES holds the json tags of each request struct. With SWITCHFRAME_SRC=<checkout of 79236154>,
  a test re-reads them from the Go source.
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tools.dmf_compile.products import switchframe

from .util import FULL_SETTINGS_SECRETS, Doc, RepoTest, full_settings

HELP = Path(__file__).resolve().parent / "fixtures" / "switchframe-0.1.0-help.txt"
IMAGE = "dmf.local/switchframe:0.1.0"
VM = "vision-mixer"

# (method, path) -> (Go file, struct, json tags). "{}" stands for any path parameter.
BODIES = {
    ("POST", "/api/sources"): ("server/control/api_source.go", "createSourceRequest",
                               {"type", "mode", "address", "streamID", "label", "latencyMs", "inputBW", "overheadBW"}),
    ("POST", "/api/sources/{}/label"): ("server/control/api_source.go", "labelRequest", {"label"}),
    ("PUT", "/api/sources/{}/position"): ("server/control/api_source.go", "positionRequest", {"position"}),
    ("POST", "/api/sources/{}/delay"): ("server/control/api_source.go", "delayRequest", {"delayMs"}),
    ("PUT", "/api/sources/{}/srt"): ("server/control/api_source.go", "updateSourceSRTRequest", {"latencyMs"}),
    ("POST", "/api/audio/trim"): ("server/control/api_audio.go", "audioTrimRequest", {"source", "trim"}),
    ("POST", "/api/audio/afv"): ("server/control/api_audio.go", "audioAFVRequest", {"source", "afv"}),
    ("PUT", "/api/audio/{}/audio-delay"): ("server/control/api_audio.go", "audioDelayRequest", {"delayMs"}),
    ("PUT", "/api/audio/{}/hpf"): ("server/control/api_audio.go", "hpfRequest", {"frequency", "enabled"}),
    ("PUT", "/api/audio/{}/gate"): ("server/control/api_audio.go", "gateRequest",
                                    {"threshold", "range", "attack", "release", "hold", "enabled"}),
    ("PUT", "/api/audio/{}/eq"): ("server/control/api_audio.go", "eqRequest", {"band", "frequency", "gain", "q", "enabled"}),
    ("PUT", "/api/audio/{}/compressor"): ("server/control/api_audio.go", "compressorRequest",
                                          {"threshold", "ratio", "attack", "release", "makeupGain"}),
    ("PUT", "/api/audio/limiter"): ("server/control/api_audio.go", "limiterRequest", {"threshold", "ceiling"}),
    ("PUT", "/api/sources/{}/key"): ("server/graphics/key_processor.go", "KeyConfig",
                                     {"type", "enabled", "keyColorY", "keyColorCb", "keyColorCr", "similarity", "smoothness",
                                      "spillSuppress", "spillReplaceCb", "spillReplaceCr", "lowClip", "highClip", "softness",
                                      "fillSource"}),
    ("DELETE", "/api/sources/{}/key"): (None, None, set()),
    ("POST", "/api/captions/mode"): ("server/control/api_captions.go", "captionModeRequest", {"mode"}),
    ("PUT", "/api/replay/shutter-angle"): ("server/control/api_replay.go", None, {"angle"}),   # inline struct
    ("PUT", "/api/scte35/rules/default"): ("server/control/api_scte35.go", "scte35DefaultRequest", {"action"}),
    ("POST", "/api/recording/start"): ("server/control/api_output.go", "recordingStartRequest",
                                       {"outputDir", "rotateAfterMins", "maxFileSizeMB", "filenamePrefix"}),
    ("POST", "/api/output/destinations"): ("server/output/destination.go", "DestinationConfig",
                                           {"type", "address", "port", "url", "latency", "streamID", "encryption",
                                            "passphrase", "maxBandwidth", "maxConns", "name", "scte35Enabled", "engine"}),
}


def help_flags(text):
    return {"--" + m for m in re.findall(r"^  -([a-z0-9-]+)", text, re.M)}


def emitted_flags(args, extra=()):
    return {a.split("=", 1)[0] for a in args if a.startswith("--") and a not in extra}


def norm(path):
    return re.sub(r"\{[^}]*\}", "{}", path)


def calls(config):
    out = [(c["method"], c["path"], c.get("body")) for s in config["sources"] for c in s["apply"]]
    out += [(c["method"], c["path"], c["body"]) for c in config["global"]]
    out += [("POST", "/api/sources", s["ref"]["create"]) for s in config["sources"] if "create" in s["ref"]]
    out += [("POST", "/api/output/destinations", d["body"]) for d in config["destinations"]]
    if config["recording"]:
        out.append((config["recording"]["method"], config["recording"]["path"], config["recording"]["body"]))
    return out


class Flags(RepoTest):
    def compile(self, doc, secrets=None):
        if secrets:
            self.repo.secrets(doc.root.get("id"), secrets)
        return self.repo.compile(doc.bytes())

    def args(self, doc, secrets=None):
        return next(f for f in self.compile(doc, secrets).doc["spec"]["functions"] if f["kind"] == "switchframe")["extraArgs"]

    def test_emittable_flags_are_in_the_products_help(self):
        missing = set(switchframe.EMITTABLE_FLAGS) - help_flags(HELP.read_text())
        self.assertEqual(missing, set())

    def test_every_flag_emitted_is_declared(self):
        variants = [Doc(), full_settings(), Doc().set(VM, "engine.engine.frameSync", "freerun"),
                    Doc().feature(VM, "replay", False)]
        seen = set()
        for d in variants:
            seen |= emitted_flags(self.args(d, FULL_SETTINGS_SECRETS if d.root.get("id") == "full-settings" else None),
                                  extra={"--log-level"})
        self.assertEqual(seen - set(switchframe.EMITTABLE_FLAGS), set())
        self.assertEqual(seen - help_flags(HELP.read_text()), set())

    def test_flags_in_the_image(self):
        """The image's --help, live. Skipped (and said so) without Docker or the image."""
        docker = shutil.which("docker")
        if not docker:
            raise unittest.SkipTest("Docker is not available: checked against the saved --help only")
        if subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True).returncode != 0:
            raise unittest.SkipTest(f"{IMAGE} is not built: checked against the saved --help only")
        live = subprocess.run([docker, "run", "--rm", "--entrypoint", "switchframe", IMAGE, "--help"],
                              capture_output=True, text=True, timeout=120)
        text = live.stdout + live.stderr
        self.assertEqual(set(switchframe.EMITTABLE_FLAGS) - help_flags(text), set())
        self.assertEqual(help_flags(text), help_flags(HELP.read_text()), "the saved --help is out of date")

    def test_studio_a_args(self):
        # Every value is set in the XML; only the one that differs from the product's default gives a flag.
        self.assertEqual(self.args(Doc()), ["--preview-proxy"])

    def test_full_settings_args(self):
        self.assertEqual(self.args(full_settings(), FULL_SETTINGS_SECRETS), [
            "--color-format=professional", "--low-latency-sync", "--frc-quality=blend", "--srt-latency=200",
            "--output-bitrate=8000000", "--output-gop-secs=4", "--output-preset=quality", "--output-timecode",
            "--replay-buffer-secs=120", "--clip-storage-max=21474836480", "--clip-ephemeral-ttl=48h",
            "--captions", "--scte35", "--scte35-pid=500", "--scte35-preroll=2000", "--scte35-heartbeat=0",
            "--scte35-verify=false", "--scte35-webhook=http://hooks.example/scte",
            "--playout", "--playout-cache-max=50", "--playout-asrun-retention=7",
            "--preview-proxy", "--preview-resolution=720p", "--preview-bitrate=1000000",
            "--relay-resolution=480p", "--relay-bitrate=1500000", "--log-level", "debug"])

    def test_feature_off_removes_its_flags(self):
        self.assertIn("--replay-buffer-secs=0", self.args(Doc().feature(VM, "replay", False)))
        self.assertIn("--frame-sync", self.args(Doc().set(VM, "engine.engine.frameSync", "freerun")))
        self.assertEqual(self.args(Doc().set(VM, "control.control.previewProxy", False)), [])
        d = full_settings()
        for f in ("captions", "scte35", "playout"):
            d.feature(VM, f, False)
        args = self.args(d, FULL_SETTINGS_SECRETS)
        self.assertFalse([a for a in args if a.startswith(("--captions", "--scte35", "--playout"))], args)

    def test_unset_values_give_no_flag(self):
        d = Doc()
        for block in ("engine.engine.colorProfile", "engine.engine.frameSync", "encoder", "replay", "clips", "control"):
            d.unset(VM, block)
        self.assertEqual(self.args(d), [])


class Bodies(RepoTest):
    def config(self):
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS)
        return self.repo.compile(full_settings().bytes()).config

    def test_every_call_is_a_known_endpoint_with_known_fields(self):
        cfg = self.config()
        seen = set()
        for method, path, body in calls(cfg):
            key = (method, norm(path))
            self.assertIn(key, BODIES, f"{method} {path} is not an endpoint the compiler knows")
            self.assertEqual(set(body or {}) - BODIES[key][2], set(), f"{method} {path}: fields not in {BODIES[key][1]}")
            seen.add(key)
        self.assertEqual(set(BODIES) - seen, set(), "full-settings doesn't exercise every endpoint")

    def test_exact_bodies(self):
        cfg = self.config()
        by_input = {s["input"]: s for s in cfg["sources"]}
        mxl = {(c["method"], c["path"]): c.get("body") for c in by_input["mxl-src"]["apply"] if c["path"] != "/api/audio/{key}/eq"}
        self.assertEqual(mxl, {
            ("POST", "/api/sources/{key}/label"): {"label": "Camera 1"},
            ("PUT", "/api/sources/{key}/position"): {"position": 1},
            ("POST", "/api/sources/{key}/delay"): {"delayMs": 20},
            ("POST", "/api/audio/trim"): {"source": "{key}", "trim": 3.5},
            ("POST", "/api/audio/afv"): {"source": "{key}", "afv": False},
            ("PUT", "/api/audio/{key}/audio-delay"): {"delayMs": 40},
            ("PUT", "/api/audio/{key}/hpf"): {"frequency": 120, "enabled": True},
            ("PUT", "/api/audio/{key}/gate"): {"threshold": -50, "range": -40, "attack": 5, "release": 100, "hold": 20, "enabled": True},
            ("PUT", "/api/audio/{key}/compressor"): {"threshold": -18, "ratio": 4, "attack": 5, "release": 100, "makeupGain": 0},
            ("DELETE", "/api/sources/{key}/key"): None})
        eq = [c["body"] for c in by_input["mxl-src"]["apply"] if c["path"] == "/api/audio/{key}/eq"]
        self.assertEqual(eq[1], {"band": 1, "frequency": 1000, "gain": 3, "q": 1.2, "enabled": True})
        self.assertEqual([b["band"] for b in eq], [0, 1, 2])
        self.assertEqual(by_input["srt-src"]["ref"], {"kind": "srt-listener", "key": "srt:venue1"})
        self.assertEqual(by_input["srt-src_3"]["apply"][0], {"method": "PUT", "path": "/api/sources/{key}/srt", "body": {"latencyMs": 120}})
        self.assertFalse(any(c["path"].endswith("/srt") for c in by_input["srt-src"]["apply"]))   # --srt-latency=200
        self.assertEqual(by_input["srt-src_2"]["ref"], {"kind": "srt-caller", "streamID": "venue2", "create": {
            "type": "srt", "mode": "caller", "address": "enc.example.net:9000", "streamID": "venue2", "label": "Venue 2",
            "latencyMs": 300, "inputBW": 1250000, "overheadBW": 30}})
        key = next(c["body"] for c in by_input["srt-src"]["apply"] if c["path"] == "/api/sources/{key}/key")
        self.assertEqual(key, {"type": "chroma", "enabled": True, "keyColorY": 150, "keyColorCb": 54, "keyColorCr": 34,
                               "similarity": 0.3, "smoothness": 0.1, "spillSuppress": 0.5, "fillSource": "{key:mxl-src_2}"})
        self.assertEqual(cfg["global"], [
            {"method": "PUT", "path": "/api/audio/limiter", "body": {"threshold": -6, "ceiling": -2}},
            {"method": "POST", "path": "/api/captions/mode", "body": {"mode": "author"}},
            {"method": "PUT", "path": "/api/replay/shutter-angle", "body": {"angle": 90}},
            {"method": "PUT", "path": "/api/scte35/rules/default", "body": {"action": "delete"}}])
        self.assertEqual(cfg["destinations"], [
            {"name": "srt-out", "body": {"type": "srt-caller", "name": "srt-out", "address": "cdn.example.net", "port": 9000,
                                         "latency": 250, "streamID": "pgm", "encryption": "aes-128",
                                         "maxBandwidth": 20000000, "scte35Enabled": False},
             "secretFields": {"passphrase": "venue-srt"}},
            {"name": "rtmp-out", "body": {"type": "rtmp", "name": "rtmp-out", "url": "rtmp://live.example.com/app"},
             "secretUrlSuffix": "yt-key"}])
        self.assertEqual(cfg["recording"], {"method": "POST", "path": "/api/recording/start", "body": {
            "outputDir": "/recordings", "rotateAfterMins": 30, "maxFileSizeMB": 2000, "filenamePrefix": "studio"}})
        self.assertEqual(cfg["program"], {"input": "mxl-src", "ref": {"kind": "mxl", "key": "mxl:817de3a0-f16a-5268-9d73-6b157c4b3611"}})

    def test_no_secret_value_in_the_config(self):
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS)
        result = self.repo.compile(full_settings().bytes())
        text = result.config_text + result.yaml_text
        for value in FULL_SETTINGS_SECRETS.values():
            self.assertNotIn(value, text)

    def test_against_the_go_source(self):
        src = os.environ.get("SWITCHFRAME_SRC")
        if not src:
            raise unittest.SkipTest("set SWITCHFRAME_SRC to a checkout of 792361540f14 to re-read the structs")
        root = Path(src)
        routes = set()
        for f in (root / "server" / "control").glob("*.go"):
            for m, p in re.findall(r'HandleFunc\("(GET|POST|PUT|DELETE) (/api/[^"]+)"', f.read_text()):
                routes.add((m, norm(p)))
        for (method, path), (gofile, struct, tags) in BODIES.items():
            with self.subTest(f"{method} {path}"):
                self.assertIn((method, path), routes)
                if gofile is None:
                    continue
                text = (root / gofile).read_text()
                if struct:
                    m = re.search(rf"type {struct} struct \{{(.*?)\n\}}", text, re.S)
                    self.assertIsNotNone(m, f"{struct} not in {gofile}")
                    found = set(re.findall(r'json:"([A-Za-z0-9]+)', m.group(1)))
                else:
                    found = set(re.findall(r'json:"(angle)"', text))
                self.assertEqual(found, tags)


if __name__ == "__main__":
    unittest.main()
