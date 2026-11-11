"""switchframe.1 -> the vision mixer's workload entry, its args and <id>.config.json.

Flags and request bodies are those of SwitchFrame at the pinned commit 79236154 (server/cmd/switchframe
and server/control). A flag is only emitted when its value differs from the product's default, so
an unset value and a value equal to the default give the same args. API settings are emitted for
every value that is set; the reconciler applies them. See 02-switchframe-options.md.
"""
import re
from fractions import Fraction

from .. import sizing
from ..problems import where
from .common import FORMATS, MISSING, RATES, Compiled, copies, format_name, frame_size, get, is_number, is_secret, rate_fraction, rate_name

TYPES = {"switchframe.1": {"1.1.0"}}

INPUTS = {"srt-src": "srt-in", "moq-src": "moq", "mxl-src": "mxl", "st2110-src": "st2110"}
HEVC_PROFILES = ("standard-hevc", "professional")
OUTPUTS = {"srt-out": None, "rtmp-out": "rtmp-out", "mxl-out": "mxl", "st2110-out": "st2110"}  # srt-out: by mode
FEATURES = {"captions": False, "captions.asr": False, "scte35": False, "scte35.scte104": False,
            "replay": True, "playout": False, "ai-segment": False, "recording": False}
FEATURE_CAPS = {"recording": "recording", "captions.asr": "asr", "scte35.scte104": "scte104", "ai-segment": "ai-segment"}

# Defaults of the flags this module emits, from `switchframe --help` at the pinned commit.
FLAG_DEFAULTS = {
    "--color-format": "standard", "--frc-quality": "none", "--srt-latency": 120,
    "--output-bitrate": 0, "--output-gop-secs": 2, "--output-preset": "balanced",
    "--replay-buffer-secs": 300, "--clip-storage-max": 10 << 30, "--clip-ephemeral-ttl": "24h",
    "--scte35-pid": 258, "--scte35-preroll": 4000, "--scte35-heartbeat": 5000, "--scte35-webhook": "",
    "--playout-cache-max": 200, "--playout-asrun-retention": 30,
    "--preview-resolution": "480p", "--preview-bitrate": 500000,
    "--relay-resolution": "720p", "--relay-bitrate": 2000000,
}
BOOL_FLAGS = ["--frame-sync", "--low-latency-sync", "--output-timecode", "--captions", "--scte35", "--scte35-verify",
              "--playout", "--preview-proxy"]
# Flags the chart emits from fields of the generated workload.
CHART_FLAGS = ["--addr", "--http-fallback", "--http-addr", "--admin-addr", "--allowed-origins", "--format",
               "--log-level", "--cache-dir", "--replay-tmpfs-dir", "--mxl-domain", "--mxl-sources", "--mxl-output",
               "--mxl-output-video-def", "--mxl-output-audio-def", "--srt-listen"]
EMITTABLE_FLAGS = sorted(set(FLAG_DEFAULTS) | set(BOOL_FLAGS) | set(CHART_FLAGS))

# The chart's endpoints: the compiler uses these and warns when the design says otherwise.
CHART_HEALTH = {"type": "http", "port": 9090, "path": "/health"}
CHART_API = {"port": 8081, "path": "/api"}

# Defaults of the catalog's schema, for request bodies that need every field (the toolkit leaves
# out fields hidden by x-when, such as a disabled gate's threshold).
HPF_DEFAULTS = {"frequencyHz": 80}
GATE_DEFAULTS = {"thresholdDb": -40, "rangeDb": -40, "attackMs": 5, "releaseMs": 100, "holdMs": 50}
EQ_DEFAULTS = {"low": {"frequencyHz": 100, "gainDb": 0, "q": 0.7}, "mid": {"frequencyHz": 1000, "gainDb": 0, "q": 0.7},
               "high": {"frequencyHz": 8000, "gainDb": 0, "q": 0.7}}
EQ_BANDS = {"low": 0, "mid": 1, "high": 2}                  # docs/reference/api/audio.md
# server/audio/eq.go, tighter than the API's own check (server/control/validate.go), which only
# bounds what reaches the mixer: a value between the two is accepted by the API and refused by it.
EQ_FREQUENCY = {"low": (80, 1000), "mid": (200, 8000), "high": (1000, 16000)}
EQ_GAIN = (-12, 12)
EQ_Q = (0.5, 4)
LIMITER_THRESHOLD = (-12, 0)                                # server/audio/mixer.go SetLimiterThreshold
LIMITER_CEILING = (-3, 0)                                   # server/audio/mixer.go SetLimiterCeiling
COMPRESSOR_DEFAULTS = {"thresholdDb": 0, "ratio": 1, "attackMs": 5, "releaseMs": 100, "makeupGainDb": 0}
CHROMA_DEFAULTS = {"keyColorY": 145, "keyColorCb": 54, "keyColorCr": 34, "similarity": 0.4, "smoothness": 0.1, "spillSuppress": 0.5}
LUMA_DEFAULTS = {"lowClip": 0, "highClip": 1, "softness": 0.1}
LIMITER_DEFAULTS = {"thresholdDb": -3, "ceilingDb": -1}
SCTE35_ACTIONS = {"pass": "pass", "drop": "delete"}         # server/scte35/rules.go: pass, delete, replace

LABEL_MAX_BYTES = 128                                       # server/control/validate.go MaxLabelLen
SRT_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOST = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[^\s:/\[\]]+)$")
HOST_PORT = re.compile(r"^[^\s:/]+:[0-9]{1,5}$|^\[[0-9A-Fa-f:.]+\]:[0-9]{1,5}$")
DEMO = re.compile(r"^--?demo(-video)?(=.*)?$")


def _flag(args, name, value):
    if value != FLAG_DEFAULTS[name]:
        args.append(f"{name}={value}")


def compile(fn, ctx, report):
    return _Mixer(fn, ctx, report).compile()


class _Mixer:
    def __init__(self, fn, ctx, report):
        self.fn, self.ctx, self.report = fn, ctx, report
        self.fid = fn["id"]
        self.params = fn.get("parameters") or {}
        self.facility = ctx.facility
        self.args = []
        self.secrets = []

    # -- helpers ------------------------------------------------------------------------------
    def refuse(self, rule, parameter, message):
        self.report.refuse(rule, where(self.fid, parameter), message)

    def warn(self, parameter, message):
        self.report.warn(where(self.fid, parameter), message)

    def p(self, path, default=MISSING):
        return get(self.params, path, default)

    def on(self, feature):
        feats = self.fn.get("features") or {}
        value = feats.get(feature, FEATURES[feature])
        if "." in feature:
            value = value and self.on(feature.split(".")[0])
        return value

    def need(self, capability, parameter, what):
        if not self.facility.has(capability):
            self.refuse(3, parameter, f"{what} needs the '{capability}' capability, which facility "
                                      f"{self.facility.name} doesn't have")
            return False
        return True

    def number(self, path, default, lo=None, hi=None, integer=False):
        """The value at path, checked; default when it isn't set; MISSING when it's refused."""
        v = self.p(path, MISSING)
        if v is MISSING:
            return default
        if not is_number(v) or (integer and v != int(v)):
            self.refuse(8, path, f"{v!r} is not {'an integer' if integer else 'a number'}")
            return MISSING
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.refuse(8, path, f"{v} is outside {lo}..{hi}")
            return MISSING
        return int(v) if integer else v

    # -- compile ------------------------------------------------------------------------------
    def compile(self):
        self.selection()
        self.engine()
        mxl_sources, source_functions = self.mxl_inputs()
        sources = self.sources(mxl_sources)
        program = self.program(sources)
        program_output = self.mxl_output()
        self.encoder()
        destinations = self.destinations()
        recording, recordings_gib = self.recording()
        global_calls = self.globals()
        self.features_flags()
        self.control()
        self.settings_args()
        replay_tmpfs = sizing.replay_tmpfs_mib(len(self.inputs), self.on("replay"))
        resources, go_mem_limit, requests = self.resources(replay_tmpfs)

        clips_gb = self.number("clips.clips.storageMaxGB", None, 1, 10000, integer=True)
        cache_gb = self.number("playout.playout.cacheMaxGB", None, 1, 10000, integer=True) if self.on("playout") else None
        width, height = self.size or (1920, 1080)
        volumes = sizing.volumes(width, height, None if clips_gb is MISSING else clips_gb, self.on("playout"),
                                 None if cache_gb is MISSING else cache_gb, recordings_gib, replay_tmpfs)

        entry = {"name": self.fid, "kind": "switchframe", "label": self.fn["name"], "format": self.format,
                 "sources": source_functions, "mxlSources": mxl_sources}
        if program_output:
            entry["programOutput"] = True
            entry["flows"] = program_output
        else:
            entry["programOutput"] = False
        if program and program["ref"].get("key"):
            entry["programSource"] = program["ref"]["key"]
        entry.update({"resources": resources, "goMemLimit": go_mem_limit, "volumes": volumes,
                      "configFile": f"{self.ctx.workload_id}.config.json", "extraArgs": self.args})

        config = {"apiVersion": "dmf.local/v1alpha1", "kind": "SwitchFrameConfig", "workload": self.ctx.workload_id,
                  "function": self.fid, "revision": self.ctx.revision, "program": program, "sources": sources,
                  "global": global_calls, "destinations": destinations, "recording": recording}
        return Compiled(entry, [requests], config, self.secrets)

    def selection(self):
        for kind, known in (("inputs", INPUTS), ("outputs", OUTPUTS)):
            for block, n in (self.fn.get(kind) or {}).items():
                if block not in known:
                    self.refuse(1, f"{kind}.{block}", f"switchframe.1 has no block {block!r}")
                elif n and known[block]:
                    self.need(known[block], f"{kind}.{block}", f"{block} ({n} {'copy' if n == 1 else 'copies'})")
        for feature in (self.fn.get("features") or {}):
            if feature not in FEATURES:
                self.refuse(1, f"features.{feature}", f"switchframe.1 has no feature {feature!r}")
        for feature, cap in FEATURE_CAPS.items():
            if self.on(feature):
                self.need(cap, f"features.{feature}", f"feature {feature}")
        if (self.fn.get("outputs") or {}).get("mxl-out", 0) > 1:
            self.refuse(8, "outputs.mxl-out", "is 0 or 1 copy: SwitchFrame writes one programme output")
        # Input copies that can be sources here, in the toolkit's block order (moq-src and
        # st2110-src are gated off above).
        self.inputs = [c for block in ("srt-src", "mxl-src") for c in copies(self.fn.get("inputs"), block)]

    def engine(self):
        res, rate = self.p("engine.engine.resolution", None), self.p("engine.engine.rate", None)
        self.format = format_name(res, rate) if res and rate else None
        if res is None or rate is None:
            self.refuse(4, "engine.engine.resolution, engine.engine.rate", "both are required")
        elif res not in FORMATS or rate not in FORMATS[res]:
            self.refuse(4, "engine.engine.resolution, engine.engine.rate",
                        f"{self.format or res} is not a SwitchFrame format (valid: "
                        + ", ".join(f"{frame_size(r)[1]}p{x}" for r in FORMATS for x in FORMATS[r]) + ")")
            self.format = None
        self.size = frame_size(res) if self.format else None
        self.rate = RATES.get(rate) if self.format else None
        if self.size and self.size[0] % 6:
            # Stage 7B: SwitchFrame's 8-bit V210 converters (server/mxl/v210.go at the pinned commit)
            # refuse a width not divisible by 6, so every MXL source frame and programme frame fails.
            # Lift this only with a build patch that handles the remainder pixels.
            mxl = [f"{n} MXL source{'s' * (n > 1)}" for n in [(self.fn.get("inputs") or {}).get("mxl-src", 0)] if n]
            mxl += ["mxl-out"] if (self.fn.get("outputs") or {}).get("mxl-out", 0) else []
            if mxl:
                self.refuse(4, "engine.engine.resolution",
                            f"{self.format} can't be used with MXL ({' and '.join(mxl)}): SwitchFrame's V210 path "
                            f"needs a frame width divisible by 6, and {self.size[0]} isn't, so every MXL frame fails. "
                            "Use 1080p or 2160p")
        for path, want in (("engine.engine.scan", "progressive"), ("engine.engine.colorspace", "BT709")):
            v = self.p(path, want)
            if v != want:
                self.refuse(4, path, f"{v!r} is not supported; SwitchFrame runs {want} only")
        profile = self.p("engine.engine.colorProfile", "standard")
        if profile not in ("standard", "standard-hevc", "professional"):
            self.refuse(8, "engine.engine.colorProfile", f"{profile!r} is not a SwitchFrame colour format")
        else:
            _flag(self.args, "--color-format", profile)
        sync = self.p("engine.engine.frameSync", "off")
        if sync == "freerun":
            self.args.append("--frame-sync")
            if (self.fn.get("inputs") or {}).get("mxl-src", 0):
                self.warn("engine.engine.frameSync", "freerun with MXL sources: this repo measured 40% of single-frame "
                                                     "flashes lost on programme. MXL sources are already locked to the "
                                                     "facility clock; leave it off")
        elif sync == "source-driven":
            self.args.append("--low-latency-sync")
        elif sync != "off":
            self.refuse(8, "engine.engine.frameSync", f"{sync!r} is not off, freerun or source-driven")
        frc = self.p("engine.engine.frameRateConversion", "none")
        if frc not in ("none", "nearest", "blend", "mcfi"):
            self.refuse(8, "engine.engine.frameRateConversion", f"{frc!r} is not none, nearest, blend or mcfi")
        else:
            _flag(self.args, "--frc-quality", frc)

    # -- MXL --------------------------------------------------------------------------------
    def check_flow(self, copy, flow, essence):
        """Rule 5: an MXL flow read or written by the mixer matches the engine."""
        fmt, label = flow["format"], flow["label"]

        def bad(what, value, engine):
            self.refuse(5, f"{copy}", f"flow {label} {what} is {value}, the engine's is {engine}")

        if essence == "video":
            if fmt.get("mediaType") != "video/v210":
                bad("media type", fmt.get("mediaType"), "video/v210")
            if self.size and (fmt.get("frameWidth"), fmt.get("frameHeight")) != self.size:
                bad("frame size", f"{fmt.get('frameWidth')}x{fmt.get('frameHeight')}", f"{self.size[0]}x{self.size[1]}")
            if self.rate and ("grainRate" not in fmt or rate_fraction(fmt["grainRate"]) != self.rate):
                got = rate_name(rate_fraction(fmt["grainRate"])) if "grainRate" in fmt else "missing"
                bad("rate", got, rate_name(self.rate))
            if fmt.get("interlace") != "progressive":
                bad("scan", fmt.get("interlace"), "progressive")
            if fmt.get("colorspace") != "BT709":
                bad("colorspace", fmt.get("colorspace"), "BT709")
            if fmt.get("transfer") not in (None, "SDR"):
                bad("transfer", fmt.get("transfer"), "SDR")
        elif essence == "audio":
            if fmt.get("mediaType") != "audio/float32":
                bad("media type", fmt.get("mediaType"), "audio/float32")
            sr = fmt.get("sampleRate")
            if not sr or rate_fraction(sr) != 48000:
                bad("sample rate", f"{sr['num']}/{sr.get('den', 1)}" if sr else "missing", "48000")
            if fmt.get("channelCount") != 2:
                bad("channel count", fmt.get("channelCount"), "2 (the facility's flow definitions are stereo)")

    def mxl_inputs(self):
        mxl_sources, functions = [], []
        self.mxl_by_copy = {}
        for copy in copies(self.fn.get("inputs"), "mxl-src"):
            spec = {}
            writer_fn = None
            for port, essence in (("video", "video"), ("audio", "audio"), ("anc", "data")):
                wires = self.ctx.wires_to.get(f"{self.fid}/{copy}.{port}", [])
                if len(wires) > 1:
                    self.refuse(8, f"{copy}.{port}", f"has {len(wires)} wires; an input reads one flow")
                if not wires:
                    continue
                src = wires[0]["from"]
                flow = self.ctx.flow_by_writer.get(src)
                if flow is None:
                    self.refuse(8, f"{copy}.{port}", f"is wired from {src}, which has no published flow")
                    continue
                if flow["essence"] != essence:
                    self.refuse(5, f"{copy}.{port}", f"is wired from {src}, a {flow['essence']} flow")
                    continue
                self.check_flow(copy, flow, essence)
                spec["data" if port == "anc" else port] = flow["id"]
                if port == "video":
                    writer_fn = src.split("/")[0]
            if "video" not in spec:
                self.refuse(8, f"{copy}.video", "has no wire: an MXL input needs a video flow")
                continue
            if writer_fn not in self.ctx.source_functions:
                self.refuse(8, f"{copy}.video", f"is wired from {writer_fn}, which is not a test source function")
            mxl_sources.append(spec)
            functions.append(writer_fn)
            self.mxl_by_copy[copy] = spec
        if self.on("scte35.scte104") and self.facility.has("scte104") and not any("data" in s for s in mxl_sources):
            self.warn("features.scte35.scte104", "is on, but no mxl-src anc port is wired: SwitchFrame only reads "
                                                 "SCTE-104 when a data flow exists")
        return mxl_sources, functions

    def mxl_output(self):
        for wire in self.ctx.wires:
            if wire["from"] == f"{self.fid}/mxl-out.anc":
                self.refuse(5, "mxl-out.anc", f"is read by {wire['to']}, but SwitchFrame writes no data flow for its programme")
        if f"{self.fid}/mxl-out.anc" in self.ctx.flow_by_writer:
            self.refuse(5, "mxl-out.anc", "has a published flow, but SwitchFrame writes no data flow for its programme")
        if not (self.fn.get("outputs") or {}).get("mxl-out", 0):
            return None
        out = {}
        for port in ("video", "audio"):
            flow = self.ctx.flow_by_writer.get(f"{self.fid}/mxl-out.{port}")
            if flow is None:
                self.refuse(8, f"mxl-out.{port}", "has no published flow")
                continue
            self.check_flow("mxl-out", flow, port)
            out[port] = flow["id"]
        return out if len(out) == 2 else None

    # -- sources ----------------------------------------------------------------------------
    def sources(self, mxl_sources):
        sources, listener_latency, keys = [], [], {}
        for copy in copies(self.fn.get("inputs"), "mxl-src"):
            spec = self.mxl_by_copy.get(copy)
            ref = {"kind": "mxl", "key": f"mxl:{spec['video']}"} if spec else {"kind": "mxl", "key": None}
            sources.append({"input": copy, "ref": ref})
        for copy in copies(self.fn.get("inputs"), "srt-src"):
            srt = self.p(f"{copy}.srt", {})
            mode = srt.get("mode", "listener")
            stream = srt.get("streamId")
            if not isinstance(stream, str) or not SRT_KEY.match(stream):
                self.refuse(8, f"{copy}.srt.streamId", f"{stream!r} must match {SRT_KEY.pattern}" if stream is not None
                            else "is required")
                stream = None
            elif stream in keys:
                self.refuse(8, f"{copy}.srt.streamId", f"{stream!r} is also used by {keys[stream]}")
            else:
                keys[stream] = copy
            latency = self.number(f"{copy}.srt.latencyMs", 120, 20, 8000, integer=True)
            if mode == "listener":
                ref = {"kind": "srt-listener", "key": f"srt:{stream}" if stream else None}
                if latency is not MISSING:
                    listener_latency.append((copy, latency))
            elif mode == "caller":
                address = srt.get("address")
                if not isinstance(address, str) or not HOST_PORT.match(address):
                    self.refuse(8, f"{copy}.srt.address", f"{address!r} must be host:port" if address else "is required in caller mode")
                bw = self.number(f"{copy}.srt.inputBandwidthMbps", 0, 0)
                overhead = self.number(f"{copy}.srt.overheadPercent", 25, 5, 100, integer=True)
                label = self.p(f"{copy}.source.label", None)
                create = {"type": "srt", "mode": "caller", "address": address, "streamID": stream}
                if isinstance(label, str):
                    create["label"] = label
                if latency is not MISSING:
                    create["latencyMs"] = latency
                if bw is not MISSING:
                    create["inputBW"] = round(bw * 125000)      # Mbit/s -> bytes/s
                if overhead is not MISSING:
                    create["overheadBW"] = overhead
                ref = {"kind": "srt-caller", "streamID": stream, "create": create}
            else:
                self.refuse(8, f"{copy}.srt.mode", f"{mode!r} is not listener or caller")
                ref = {"kind": "srt-listener", "key": None}
            sources.append({"input": copy, "ref": ref})

        # One shared listener: the most common listener latency becomes --srt-latency, the others
        # are set per source once it appears (PUT /api/sources/{key}/srt).
        common = None
        if listener_latency:
            counts = {}
            for _, lat in listener_latency:
                counts[lat] = counts.get(lat, 0) + 1
            common = max(counts, key=lambda lat: (counts[lat], -[x for _, x in listener_latency].index(lat)))
            _flag(self.args, "--srt-latency", common)

        self.keys = {s["input"]: s["ref"] for s in sources}
        for s in sources:
            s["apply"] = self.source_calls(s["input"])
            if s["ref"]["kind"] == "srt-listener":
                lat = dict(listener_latency).get(s["input"])
                if lat is not None and lat != common:
                    s["apply"].insert(0, {"method": "PUT", "path": "/api/sources/{key}/srt", "body": {"latencyMs": lat}})
        return sources

    def source_calls(self, copy):
        calls = []
        src = self.p(copy, {})
        label = get(src, "source.label")
        if label is not MISSING:
            if not isinstance(label, str) or len(label.encode()) > LABEL_MAX_BYTES:
                self.refuse(8, f"{copy}.source.label", f"must be text of at most {LABEL_MAX_BYTES} bytes")
            else:
                calls.append({"method": "POST", "path": "/api/sources/{key}/label", "body": {"label": label}})
        pos = self.number(f"{copy}.source.position", MISSING, 1, 64, integer=True)
        if pos is not MISSING:
            calls.append({"method": "PUT", "path": "/api/sources/{key}/position", "body": {"position": pos}})
        delay = self.number(f"{copy}.source.delayMs", MISSING, 0, 500, integer=True)
        if delay is not MISSING:
            calls.append({"method": "POST", "path": "/api/sources/{key}/delay", "body": {"delayMs": delay}})

        trim = self.number(f"{copy}.audio.trimDb", MISSING, -20, 20)
        if trim is not MISSING:
            calls.append({"method": "POST", "path": "/api/audio/trim", "body": {"source": "{key}", "trim": trim}})
        afv = get(src, "audio.afv")
        if afv is not MISSING:
            if isinstance(afv, bool):
                calls.append({"method": "POST", "path": "/api/audio/afv", "body": {"source": "{key}", "afv": afv}})
            else:
                self.refuse(8, f"{copy}.audio.afv", f"{afv!r} is not true or false")
        adelay = self.number(f"{copy}.audio.delayMs", MISSING, 0, 500, integer=True)
        if adelay is not MISSING:
            calls.append({"method": "PUT", "path": "/api/audio/{key}/audio-delay", "body": {"delayMs": adelay}})

        hpf = get(src, "audio.highPass")
        if isinstance(hpf, dict) and hpf:
            calls.append({"method": "PUT", "path": "/api/audio/{key}/hpf", "body": {
                "frequency": self.number(f"{copy}.audio.highPass.frequencyHz", HPF_DEFAULTS["frequencyHz"], 20, 500),
                "enabled": self.flag_value(f"{copy}.audio.highPass.enabled", False)}})
        gate = get(src, "audio.gate")
        if isinstance(gate, dict) and gate:
            g = lambda k, lo, hi: self.number(f"{copy}.audio.gate.{k}", GATE_DEFAULTS[k], lo, hi)
            calls.append({"method": "PUT", "path": "/api/audio/{key}/gate", "body": {
                "threshold": g("thresholdDb", -60, 0), "range": g("rangeDb", -80, -20), "attack": g("attackMs", 0.01, 10),
                "release": g("releaseMs", 10, 500), "hold": g("holdMs", 0, 500),
                "enabled": self.flag_value(f"{copy}.audio.gate.enabled", False)}})
        for band, index in EQ_BANDS.items():
            eq = get(src, f"audio.eq.{band}")
            if isinstance(eq, dict) and eq:
                e = lambda k, r: self.number(f"{copy}.audio.eq.{band}.{k}", EQ_DEFAULTS[band][k], r[0], r[1])
                calls.append({"method": "PUT", "path": "/api/audio/{key}/eq", "body": {
                    "band": index, "frequency": e("frequencyHz", EQ_FREQUENCY[band]), "gain": e("gainDb", EQ_GAIN), "q": e("q", EQ_Q),
                    "enabled": self.flag_value(f"{copy}.audio.eq.{band}.enabled", False)}})
        comp = get(src, "audio.compressor")
        if isinstance(comp, dict) and comp:
            c = lambda k, lo, hi: self.number(f"{copy}.audio.compressor.{k}", COMPRESSOR_DEFAULTS[k], lo, hi)
            calls.append({"method": "PUT", "path": "/api/audio/{key}/compressor", "body": {
                "threshold": c("thresholdDb", -40, 0), "ratio": c("ratio", 1, 20), "attack": c("attackMs", 0.1, 100),
                "release": c("releaseMs", 10, 1000), "makeupGain": c("makeupGainDb", 0, 24)}})

        ktype = get(src, "key.type")
        if ktype == "off":
            calls.append({"method": "DELETE", "path": "/api/sources/{key}/key"})
        elif ktype in ("chroma", "luma"):
            defaults = CHROMA_DEFAULTS if ktype == "chroma" else LUMA_DEFAULTS
            body = {"type": ktype, "enabled": True}
            for k, d in defaults.items():
                integer = k.startswith("keyColor")
                body[k] = self.number(f"{copy}.key.{k}", d, 0, 255 if integer else 1, integer=integer)
            fill = get(src, "key.fillSource")
            if fill is not MISSING and fill != "":
                if fill not in self.inputs:
                    self.refuse(8, f"{copy}.key.fillSource", f"{fill!r} is not an input copy in use ({', '.join(self.inputs)})")
                else:
                    body["fillSource"] = "{key}" if fill == copy else f"{{key:{fill}}}"
            calls.append({"method": "PUT", "path": "/api/sources/{key}/key", "body": body})
        elif ktype == "neural":
            self.need("neural-key", f"{copy}.key.type", "key type neural")
        elif ktype is not MISSING:
            self.refuse(8, f"{copy}.key.type", f"{ktype!r} is not off, chroma, luma or neural")
        return [c for c in calls if all(v is not MISSING for v in (c.get("body") or {}).values())]

    def flag_value(self, path, default):
        v = self.p(path, MISSING)
        if v is MISSING:
            return default
        if not isinstance(v, bool):
            self.refuse(8, path, f"{v!r} is not true or false")
            return MISSING
        return v

    def program(self, sources):
        on_air = self.p("engine.engine.onAirAtStart", MISSING)
        if on_air is MISSING:
            on_air = self.inputs[0] if self.inputs else None
        if on_air is None:
            return None
        ref = self.keys.get(on_air)
        if ref is None:
            self.refuse(8, "engine.engine.onAirAtStart", f"{on_air!r} is not an input copy in use ({', '.join(self.inputs) or 'none'})")
            return None
        return {"input": on_air, "ref": {k: v for k, v in ref.items() if k != "create"}}

    # -- encoder and outputs ----------------------------------------------------------------
    def encoder(self):
        bitrate = self.number("encoder.encoder.bitrateMbps", MISSING, 0)
        if bitrate is not MISSING:
            _flag(self.args, "--output-bitrate", round(bitrate * 1_000_000))
        gop = self.p("encoder.encoder.gopSeconds", MISSING)
        if gop is not MISSING:
            if gop not in (1, 2, 4):
                self.refuse(8, "encoder.encoder.gopSeconds", f"{gop!r} is not 1, 2 or 4")
            else:
                _flag(self.args, "--output-gop-secs", int(gop))
        preset = self.p("encoder.encoder.preset", MISSING)
        if preset is not MISSING:
            if preset not in ("low-latency", "balanced", "quality"):
                self.refuse(8, "encoder.encoder.preset", f"{preset!r} is not low-latency, balanced or quality")
            else:
                _flag(self.args, "--output-preset", preset)
        if self.flag_value("encoder.encoder.timecode", False) is True:
            self.args.append("--output-timecode")

    def secret(self, path):
        v = self.p(path, MISSING)
        if v is MISSING:
            return None
        if not is_secret(v):
            self.refuse(6, path, "must be a secret reference, not a value: secrets never appear in the XML")
            return None
        self.secrets.append((where(self.fid, path), v["$secret"], path))
        return v["$secret"]

    def destinations(self):
        out = []
        for copy in copies(self.fn.get("outputs"), "srt-out"):
            srt = self.p(f"{copy}.srt", {})
            mode = srt.get("mode", "caller")
            if mode == "listener":
                self.need("srt-listener-out", f"{copy}.srt.mode", "SRT output in listener mode")
                continue
            if mode != "caller":
                self.refuse(8, f"{copy}.srt.mode", f"{mode!r} is not caller or listener")
                continue
            if not self.need("srt-caller-out", f"outputs.{copy}", "SRT caller output"):
                continue
            body = {"type": "srt-caller", "name": copy}
            address = srt.get("address")
            if not isinstance(address, str) or not HOST.match(address):
                self.refuse(8, f"{copy}.srt.address", f"{address!r} must be a host name or address, without the port"
                            if address else "is required in caller mode")
            body["address"] = address
            if self.p(f"{copy}.srt.port") is MISSING:
                self.refuse(8, f"{copy}.srt.port", "is required in caller mode")
            body["port"] = self.number(f"{copy}.srt.port", MISSING, 1, 65535, integer=True)
            latency = self.number(f"{copy}.srt.latencyMs", MISSING, 20, 8000, integer=True)
            if latency is not MISSING:
                body["latency"] = latency
            stream = srt.get("streamId")
            if isinstance(stream, str) and stream:
                body["streamID"] = stream
            encryption = srt.get("encryption", "off")
            fields = {}
            if encryption in ("aes-128", "aes-256"):
                body["encryption"] = encryption
                name = self.secret(f"{copy}.srt.passphrase")
                if name is None:
                    self.refuse(6, f"{copy}.srt.passphrase", f"is required with encryption {encryption}")
                else:
                    fields["passphrase"] = name
            elif encryption != "off":
                self.refuse(8, f"{copy}.srt.encryption", f"{encryption!r} is not off, aes-128 or aes-256")
            bw = self.number(f"{copy}.srt.maxBandwidthMbps", MISSING, 0)
            if bw is not MISSING and bw > 0:
                body["maxBandwidth"] = round(bw * 1_000_000)     # bit/s: server/output/manager.go divides by 8
            scte = self.flag_value(f"{copy}.srt.scte35", True)
            if scte is not MISSING:
                body["scte35Enabled"] = scte                   # absent means false in the product
            entry = {"name": copy, "body": {k: v for k, v in body.items() if v is not MISSING}}
            if fields:
                entry["secretFields"] = fields
            out.append(entry)
        profile = self.p("engine.engine.colorProfile", "standard")
        for copy in copies(self.fn.get("outputs"), "rtmp-out"):
            if profile in HEVC_PROFILES:
                self.refuse(9, f"{copy}, engine.engine.colorProfile",
                            f"{copy} can't run with colour profile {profile!r}: the programme is HEVC and SwitchFrame "
                            "can't transcode it for RTMP, so it would report active and send nothing. "
                            "Use the 'standard' profile or remove the output")
            url = self.p(f"{copy}.rtmp.url", None)
            if not isinstance(url, str) or not re.match(r"^rtmps?://[^\s]+$", url):
                self.refuse(8, f"{copy}.rtmp.url", f"{url!r} must start with rtmp:// or rtmps://" if url else "is required")
            entry = {"name": copy, "body": {"type": "rtmp", "name": copy, "url": url}}
            key = self.secret(f"{copy}.rtmp.streamKey")
            if key is not None:
                # The product sends to `url` as given and ignores streamID for RTMP, so the key
                # becomes the URL's last path segment (server/output/manager.go).
                entry["secretUrlSuffix"] = key
            out.append(entry)
        return out

    def recording(self):
        if not self.on("recording"):
            return None, None
        rec = "recording.recording"
        gib = self.number(f"{rec}.storageGiB", MISSING, 1, integer=True)
        limit = self.facility.limits.get("maxRecordingGiB")
        if gib is MISSING:
            if self.p(f"{rec}.storageGiB", MISSING) is MISSING:
                self.refuse(8, f"{rec}.storageGiB", "is required when recording is on")
            gib = None
        elif limit is not None and gib > limit:
            self.refuse(3, f"{rec}.storageGiB", f"{gib} GiB is more than facility {self.facility.name}'s limit of {limit} GiB")
        body = {"outputDir": "/recordings"}
        rotate = self.number(f"{rec}.rotateAfterMinutes", MISSING, 0, 1440, integer=True)
        if rotate == 0:
            self.refuse(8, f"{rec}.rotateAfterMinutes", "0 can't be honoured: SwitchFrame treats 0 as its default of 60")
        elif rotate is not MISSING:
            body["rotateAfterMins"] = rotate
        size = self.number(f"{rec}.maxFileSizeMB", MISSING, 0, integer=True)
        if size is not MISSING:
            body["maxFileSizeMB"] = size
        prefix = self.p(f"{rec}.filenamePrefix", MISSING)
        if prefix is not MISSING:
            if not isinstance(prefix, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", prefix) or ".." in prefix:
                self.refuse(8, f"{rec}.filenamePrefix", f"{prefix!r} must be a plain file name prefix")
            else:
                body["filenamePrefix"] = prefix
        return {"method": "POST", "path": "/api/recording/start", "body": body}, gib

    # -- features ---------------------------------------------------------------------------
    def globals(self):
        calls = []
        lim = self.p("audio.master.limiter", MISSING)
        if isinstance(lim, dict) and lim:
            calls.append({"method": "PUT", "path": "/api/audio/limiter", "body": {
                "threshold": self.number("audio.master.limiter.thresholdDb", LIMITER_DEFAULTS["thresholdDb"], *LIMITER_THRESHOLD),
                "ceiling": self.number("audio.master.limiter.ceilingDb", LIMITER_DEFAULTS["ceilingDb"], *LIMITER_CEILING)}})
        if self.on("captions"):
            mode = self.p("captions.captions.mode", "passthrough")
            if mode not in ("passthrough", "author"):
                self.refuse(8, "captions.captions.mode", f"{mode!r} is not passthrough or author")
            else:
                calls.append({"method": "POST", "path": "/api/captions/mode", "body": {"mode": mode}})
        if self.on("replay"):
            angle = self.number("replay.replay.shutterAngle", MISSING, 0, 360)
            if angle is not MISSING:
                calls.append({"method": "PUT", "path": "/api/replay/shutter-angle", "body": {"angle": angle}})
        if self.on("scte35"):
            rule = self.p("scte35.scte35.defaultRule", MISSING)
            if rule == "rewrite":
                self.refuse(8, "scte35.scte35.defaultRule", "rewrite can't be a default: SwitchFrame only rewrites "
                                                            "through a named rule with replacement parameters")
            elif rule is not MISSING and rule not in SCTE35_ACTIONS:
                self.refuse(8, "scte35.scte35.defaultRule", f"{rule!r} is not pass, drop or rewrite")
            elif rule is not MISSING:
                calls.append({"method": "PUT", "path": "/api/scte35/rules/default", "body": {"action": SCTE35_ACTIONS[rule]}})
        return [c for c in calls if all(v is not MISSING for v in c["body"].values())]

    def features_flags(self):
        if self.on("replay"):
            secs = self.number("replay.replay.bufferSeconds", MISSING, 1, 300, integer=True)
            if secs is not MISSING:
                _flag(self.args, "--replay-buffer-secs", secs)
        else:
            self.args.append("--replay-buffer-secs=0")
        gb = self.number("clips.clips.storageMaxGB", MISSING, 1, 10000, integer=True)
        if gb is not MISSING:
            _flag(self.args, "--clip-storage-max", gb << 30)       # bytes; the product's "10GB" is 10<<30
        ttl = self.number("clips.clips.ephemeralTtlHours", MISSING, 1, 720, integer=True)
        if ttl is not MISSING:
            _flag(self.args, "--clip-ephemeral-ttl", f"{ttl}h")    # a Go duration
        if self.on("captions"):
            self.args.append("--captions")
        if self.on("scte35"):
            self.args.append("--scte35")
            s = "scte35.scte35"
            for key, flag, lo, hi in (("pid", "--scte35-pid", 32, 8190), ("prerollMs", "--scte35-preroll", 0, 60000),
                                      ("heartbeatMs", "--scte35-heartbeat", 0, 60000)):
                v = self.number(f"{s}.{key}", MISSING, lo, hi, integer=True)
                if v is not MISSING:
                    _flag(self.args, flag, v)
            if self.flag_value(f"{s}.verify", True) is False:
                self.args.append("--scte35-verify=false")
            hook = self.p(f"{s}.webhookUrl", MISSING)
            if hook is not MISSING and hook != "":
                if not isinstance(hook, str) or not re.match(r"^https?://[^\s]+$", hook):
                    self.refuse(8, f"{s}.webhookUrl", f"{hook!r} is not an http(s) URL")
                else:
                    _flag(self.args, "--scte35-webhook", hook)
        if self.on("playout"):
            self.args.append("--playout")
            for key, flag, hi in (("cacheMaxGB", "--playout-cache-max", 10000), ("asRunRetentionDays", "--playout-asrun-retention", 3650)):
                v = self.number(f"playout.playout.{key}", MISSING, 1, hi, integer=True)
                if v is not MISSING:
                    _flag(self.args, flag, v)

    def control(self):
        c = "control.control"
        proxy = self.flag_value(f"{c}.previewProxy", False)
        if proxy is True:
            self.args.append("--preview-proxy")
            res = self.p(f"{c}.previewResolution", MISSING)
            if res is not MISSING:
                if res not in ("360p", "480p", "720p"):
                    self.refuse(8, f"{c}.previewResolution", f"{res!r} is not 360p, 480p or 720p")
                else:
                    _flag(self.args, "--preview-resolution", res)
            kbps = self.number(f"{c}.previewBitrateKbps", MISSING, 100, 5000, integer=True)
            if kbps is not MISSING:
                _flag(self.args, "--preview-bitrate", kbps * 1000)
        res = self.p(f"{c}.relayResolution", MISSING)
        if res is not MISSING:
            if res not in ("360p", "480p", "720p", "source"):
                self.refuse(8, f"{c}.relayResolution", f"{res!r} is not 360p, 480p, 720p or source")
            else:
                _flag(self.args, "--relay-resolution", res)
        kbps = self.number(f"{c}.relayBitrateKbps", MISSING, 100, 50000, integer=True)
        if kbps is not MISSING:
            _flag(self.args, "--relay-bitrate", kbps * 1000)

        ctl = self.fn.get("control") or {}
        health = ctl.get("health")
        if health and any(health.get(k) != v for k, v in CHART_HEALTH.items()):
            self.warn("control.health", f"{health} differs from the chart's http {CHART_HEALTH['port']} "
                                        f"{CHART_HEALTH['path']}, which is used")
        api = ctl.get("api")
        if api and any(api.get(k, v) != v for k, v in CHART_API.items()):
            self.warn("control.api", f"{api} differs from the chart's port {CHART_API['port']} {CHART_API['path']}, which is used")

    def settings_args(self):
        settings = self.fn.get("settings") or {}
        extra = settings.get("extraArgs", MISSING)
        if extra is MISSING:
            return
        if not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
            self.refuse(8, "settings.extraArgs", "must be a list of strings")
            return
        demo = [a for a in extra if DEMO.match(a)]
        if demo:
            self.refuse(8, "settings.extraArgs", f"{', '.join(demo)} is refused: demo mode replaces the workload's sources")
            return
        if extra:
            self.warn("settings.extraArgs", f"appends {' '.join(extra)} to SwitchFrame's arguments, unchecked")
            self.args.extend(extra)

    def resources(self, replay_tmpfs=0):
        res = self.fn.get("resources") or {}
        cpu = res.get("cpu", 2)
        mem = res.get("memoryMiB", 4096)
        if not is_number(cpu) or cpu <= 0:
            self.refuse(8, "resources.cpu", f"{cpu!r} must be more than 0")
            cpu = 2
        if not is_number(mem) or mem < 512:
            self.refuse(8, "resources.memoryMiB", f"{mem!r} must be at least 512")
            mem = 4096
        if res.get("gpu", {}).get("count", 0) > 0:
            self.need("gpu", "resources.gpu", f"{res['gpu']['count']} GPU ({res['gpu'].get('class')})")
        resources, gml, request_mib = sizing.switchframe_resources(cpu, mem, replay_tmpfs)
        return resources, gml, (Fraction(str(cpu)), request_mib, sizing.switchframe_cpu_limit(cpu))
