"""Shared test helpers: a throwaway repo, XML edits, strict JSON equality."""
import io
import os
import shutil
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from tools.dmf_compile import compiler, facility, reader
from tools.dmf_compile.__main__ import main
from tools.dmf_compile.problems import CompileError

REPO = Path(__file__).resolve().parents[3]
STUDIO_A = REPO / "examples" / "studio-a.workload.xml"
GOLDEN = Path(__file__).resolve().parent / "golden"
TOOLKIT_FIXTURES = Path(os.environ.get(
    "DMF_TOOLKIT_FIXTURES", "/mnt/wsl/instances/Ubuntu/home/dansi/DMF_Toolkit/scripts/fixtures"))
NS = reader.NS
UID_NAMESPACE = uuid.UUID("9e6d3b0a-7c1f-5d4e-8a2b-1f0c3d5e7a9b")  # 01-contract.md, Flow identity
ET.register_namespace("", NS)


def require_tool(test, name):
    """Skip without the tool, unless validate.sh asked for every tool (DMF_REQUIRE_TOOLS=1)."""
    path = shutil.which(name, path=f"{REPO / '.tools' / 'bin'}:{os.environ.get('PATH', '')}")
    if path is None:
        if os.environ.get("DMF_REQUIRE_TOOLS"):
            test.fail(f"{name} is required (DMF_REQUIRE_TOOLS=1) but not found")
        raise unittest.SkipTest(f"{name} not installed")
    return path


class TempRepo:
    """A copy of what the compiler reads: facility profiles and the hand-written workload."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory(prefix="dmf-compile-")
        self.path = Path(self.dir.name)
        (self.path / "workloads").mkdir()
        self.gen = self.path / "workloads" / "generated"
        (self.path / "scripts").mkdir()
        shutil.copy2(REPO / "scripts" / "workload-file.sh", self.path / "scripts")
        shutil.copytree(REPO / "facility", self.path / "facility")
        shutil.copy(REPO / "workloads" / "news-studio.yaml", self.path / "workloads")

    def cleanup(self):
        self.dir.cleanup()

    def facility(self, name="local", **changes):
        """Load a facility, optionally from a modified copy of local.yaml saved as <name>."""
        if changes:
            doc = yaml.safe_load((self.path / "facility" / "local.yaml").read_text())
            doc["metadata"]["name"] = name
            for path, value in changes.items():
                node = doc["spec"]
                *head, last = path.split(".")
                for k in head:
                    node = node[k]
                node[last] = value
            (self.path / "facility" / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        return facility.load(self.path, name)

    def secrets(self, workload_id, values, mode=0o600):
        d = self.path / ".state" / "secrets"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{workload_id}.yaml"
        f.write_text(yaml.safe_dump(values))
        os.chmod(f, mode)

    def compile(self, data, fac=None, name="test.workload.xml"):
        doc = reader.read(data)
        return compiler.compile_document(doc, self.path, fac or facility.load(self.path, "local"), name, data)

    def compile_and_write(self, data, fac=None, name="test.workload.xml"):
        result = self.compile(data, fac, name)
        compiler.write(self.path, result)
        return result

    def problems(self, data, fac=None):
        try:
            self.compile(data, fac)
        except CompileError as e:
            return e.problems
        raise AssertionError("expected the compile to be refused")

    def cli(self, *argv, cwd=REPO):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd)
        try:
            rc = main(list(argv), repo=self.path, out=out, err=err)
        finally:
            os.chdir(old)
        return rc, out.getvalue(), err.getvalue()


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)


# ---------------------------------------------------------------------------------------------
# XML edits
# ---------------------------------------------------------------------------------------------
def q(name):
    return f"{{{NS}}}{name}"


class Doc:
    """An editable published workload; bytes() gives schema-valid XML when the edits are."""

    def __init__(self, data=None):
        self.root = ET.fromstring(data if data is not None else STUDIO_A.read_bytes())

    def bytes(self):
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(self.root, encoding="utf-8") + b"\n"

    def fn(self, fid):
        for f in self.root.iter(q("function")):
            if f.get("id") == fid:
                return f
        raise KeyError(fid)

    def set(self, fid, path, value, vtype=None):
        """Set (or add) a parameter value. vtype from the Python value when not given."""
        if vtype is None:
            vtype = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else \
                    "number" if isinstance(value, float) else "string"
        text = ("true" if value else "false") if isinstance(value, bool) else "" if value is None else str(value)
        params = self.fn(fid).find(q("parameters"))
        for v in params.findall(q("value")):
            if v.get("path") == path:
                v.set("type", vtype)
                v.text = text
                return self
        v = ET.SubElement(params, q("value"), {"path": path, "type": vtype})
        v.text = text
        return self

    def unset(self, fid, prefix):
        params = self.fn(fid).find(q("parameters"))
        for v in list(params.findall(q("value"))):
            if v.get("path") == prefix or v.get("path").startswith(prefix + "."):
                params.remove(v)
        return self

    def settings(self, fid, values):
        f = self.fn(fid)
        s = f.find(q("settings"))
        if s is None:
            s = ET.Element(q("settings"))
            f.insert(list(f).index(f.find(q("parameters"))) + 1, s)
        for path, vtype, text in values:
            v = ET.SubElement(s, q("value"), {"path": path, "type": vtype})
            v.text = text
        return self

    def use(self, fid, kind, block, n):
        sel = self.fn(fid).find(q(kind))
        for u in sel.findall(q("use")):
            if u.get("block") == block:
                u.set("copies", str(n))
                return self
        ET.SubElement(sel, q("use"), {"block": block, "copies": str(n)})
        return self

    def feature(self, fid, block, enabled):
        for f in self.fn(fid).find(q("features")).findall(q("feature")):
            if f.get("block") == block:
                f.set("enabled", "true" if enabled else "false")
        return self

    def flow(self, writer):
        for f in self.root.iter(q("flow")):
            if f.get("writer") == writer:
                return f
        raise KeyError(writer)

    def remove_function(self, fid):
        fns = self.root.find(q("functions"))
        fns.remove(self.fn(fid))
        for f in list(self.root.find(q("flows"))):
            if f.get("writer").startswith(fid + "/"):
                self.root.find(q("flows")).remove(f)
        for w in list(self.root.find(q("wires"))):
            if w.get("from").startswith(fid + "/"):
                self.root.find(q("wires")).remove(w)
        return self

    def rename(self, workload_id):
        """A new id with its own uid and flow IDs, by the contract's derivation (01-contract.md)."""
        self.root.set("id", workload_id)
        uid = uuid.uuid5(UID_NAMESPACE, workload_id)
        self.root.set("uid", str(uid))
        renames = {}
        for flow in self.root.iter(q("flow")):
            new = str(uuid.uuid5(uid, flow.get("writer")))
            renames[flow.get("id")] = new
            flow.set("id", new)
        for el in self.root.iter():
            for attr, value in el.attrib.items():
                if value in renames:
                    el.set(attr, renames[value])
            if el.text and el.text.strip() in renames:
                el.text = renames[el.text.strip()]
        return self


def full_settings():
    """studio-a with every stage 4-6 option this facility can run, at a non-default value."""
    d = Doc()
    vm = "vision-mixer"
    d.use(vm, "inputs", "srt-src", 3).use(vm, "outputs", "srt-out", 1).use(vm, "outputs", "rtmp-out", 1)
    for f in ("captions", "scte35", "playout", "recording"):
        d.feature(vm, f, True)
    for copy, mode, stream, lat in (("srt-src", "listener", "venue1", 200), ("srt-src_2", "caller", "venue2", 300),
                                    ("srt-src_3", "listener", "venue3", 120)):
        d.set(vm, f"{copy}.srt.mode", mode).set(vm, f"{copy}.srt.streamId", stream).set(vm, f"{copy}.srt.latencyMs", lat)
        d.set(vm, f"{copy}.source.label", f"Venue {stream[-1]}").set(vm, f"{copy}.source.position", 3 + int(stream[-1]))
    d.set(vm, "srt-src_2.srt.address", "enc.example.net:9000").set(vm, "srt-src_2.srt.inputBandwidthMbps", 10)
    d.set(vm, "srt-src_2.srt.overheadPercent", 30)
    d.set(vm, "srt-src.key.type", "chroma").set(vm, "srt-src.key.keyColorY", 150).set(vm, "srt-src.key.similarity", 0.3)
    d.set(vm, "srt-src.key.fillSource", "mxl-src_2")
    d.set(vm, "srt-src_2.key.type", "luma").set(vm, "srt-src_2.key.lowClip", 0.1).set(vm, "srt-src_2.key.softness", 0.2)
    d.set(vm, "mxl-src.audio.trimDb", 3.5).set(vm, "mxl-src.audio.afv", False).set(vm, "mxl-src.audio.delayMs", 40)
    d.set(vm, "mxl-src.source.delayMs", 20)
    d.set(vm, "mxl-src.audio.highPass.enabled", True).set(vm, "mxl-src.audio.highPass.frequencyHz", 120)
    d.set(vm, "mxl-src.audio.gate.enabled", True).set(vm, "mxl-src.audio.gate.thresholdDb", -50).set(vm, "mxl-src.audio.gate.holdMs", 20)
    d.set(vm, "mxl-src.audio.eq.mid.enabled", True).set(vm, "mxl-src.audio.eq.mid.gainDb", 3).set(vm, "mxl-src.audio.eq.mid.q", 1.2)
    d.set(vm, "mxl-src.audio.compressor.thresholdDb", -18).set(vm, "mxl-src.audio.compressor.ratio", 4)
    d.set(vm, "engine.engine.colorProfile", "standard").set(vm, "engine.engine.frameSync", "source-driven")
    d.set(vm, "engine.engine.frameRateConversion", "blend")
    d.set(vm, "audio.master.limiter.thresholdDb", -6).set(vm, "audio.master.limiter.ceilingDb", -2)
    d.set(vm, "encoder.encoder.bitrateMbps", 8).set(vm, "encoder.encoder.gopSeconds", 4)
    d.set(vm, "encoder.encoder.preset", "quality").set(vm, "encoder.encoder.timecode", True)
    d.set(vm, "srt-out.srt.mode", "caller").set(vm, "srt-out.srt.address", "cdn.example.net").set(vm, "srt-out.srt.port", 9000)
    d.set(vm, "srt-out.srt.latencyMs", 250).set(vm, "srt-out.srt.streamId", "pgm").set(vm, "srt-out.srt.encryption", "aes-128")
    d.set(vm, "srt-out.srt.passphrase", "venue-srt", "secret").set(vm, "srt-out.srt.maxBandwidthMbps", 20)
    d.set(vm, "srt-out.srt.scte35", False)
    d.set(vm, "rtmp-out.rtmp.url", "rtmp://live.example.com/app").set(vm, "rtmp-out.rtmp.streamKey", "yt-key", "secret")
    d.set(vm, "recording.recording.rotateAfterMinutes", 30).set(vm, "recording.recording.maxFileSizeMB", 2000)
    d.set(vm, "recording.recording.filenamePrefix", "studio").set(vm, "recording.recording.storageGiB", 100)
    d.set(vm, "captions.captions.mode", "author")
    for k, v in (("pid", 500), ("prerollMs", 2000), ("heartbeatMs", 0), ("verify", False),
                 ("webhookUrl", "http://hooks.example/scte"), ("defaultRule", "drop")):
        d.set(vm, f"scte35.scte35.{k}", v)
    d.set(vm, "replay.replay.bufferSeconds", 120).set(vm, "replay.replay.shutterAngle", 90)
    d.set(vm, "clips.clips.storageMaxGB", 20).set(vm, "clips.clips.ephemeralTtlHours", 48)
    d.set(vm, "playout.playout.cacheMaxGB", 50).set(vm, "playout.playout.asRunRetentionDays", 7)
    d.set(vm, "control.control.previewResolution", "720p").set(vm, "control.control.previewBitrateKbps", 1000)
    d.set(vm, "control.control.relayResolution", "480p").set(vm, "control.control.relayBitrateKbps", 1500)
    d.settings(vm, [("extraArgs[0]", "string", "--log-level"), ("extraArgs[1]", "string", "debug")])
    d.rename("full-settings")
    return d


FULL_SETTINGS_SECRETS = {"venue-srt": "a-long-srt-passphrase", "yt-key": "abcd-efgh-ijkl"}


def strict_equal(a, b, path="$"):
    """JSON equality: booleans aren't numbers, and every key and index must match."""
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if type(a) is type(b) and a == b else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    if type(a) is not type(b):
        return [f"{path}: {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        diffs = [f"{path}.{k}: only on one side" for k in set(a) ^ set(b)]
        for k in set(a) & set(b):
            diffs += strict_equal(a[k], b[k], f"{path}.{k}")
        return diffs
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in strict_equal(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]
