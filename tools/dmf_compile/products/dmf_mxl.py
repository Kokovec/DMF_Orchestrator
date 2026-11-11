"""DMF MXL test sources: dmf-mxl.1 (mxl-gst-testsrc) and dmf-mxl.2 (mxl-syncgen)."""
from fractions import Fraction

from .. import sizing
from ..problems import where
from .common import MISSING, Compiled, format_name, get

TYPES = {"dmf-mxl.1": {"1.0.0"}, "dmf-mxl.2": {"1.0.0"}}
KINDS = {"dmf-mxl.1": "mxl-testsrc", "dmf-mxl.2": "mxl-syncsrc"}
PATTERNS = ["smpte", "ball", "snow", "black", "white", "red", "green", "blue", "checkers-8", "circular", "smpte75"]
SYNC_FORMAT = "1080p29.97"   # 30-frame / 48048-sample period


def compile(fn, ctx, report):
    fid, ftype, params = fn["id"], fn["type"], fn.get("parameters") or {}

    for block, n in (fn.get("inputs") or {}).items():
        if n:
            report.refuse(3, where(fid, f"inputs.{block}"), "a test source has no inputs")
    outputs = fn.get("outputs") or {}
    for block, n in outputs.items():
        if block != "mxl":
            report.refuse(1, where(fid, f"outputs.{block}"), f"{ftype} has no output block {block!r}")
    if outputs.get("mxl", 0) != 1:
        report.refuse(8, where(fid, "outputs.mxl"), f"must be 1 copy, not {outputs.get('mxl', 0)}: "
                                                    "a test source exists to write its flows")
    elif not ctx.facility.has("mxl"):
        report.refuse(3, where(fid, "outputs.mxl"), f"facility {ctx.facility.name} has no 'mxl' capability")

    video = ctx.flow_by_writer.get(f"{fid}/mxl.video")
    audio = ctx.flow_by_writer.get(f"{fid}/mxl.audio")
    if video is None or audio is None:
        report.refuse(8, where(fid, "flows"), f"published flows {fid}/mxl.video and {fid}/mxl.audio are both required")

    fmt = format_name(get(params, "mxl.format.video.frameSize", None), get(params, "mxl.format.video.rate", None))
    if ftype == "dmf-mxl.2" and fmt != SYNC_FORMAT:
        report.refuse(5, where(fid, "mxl.format.video"), f"is {fmt}; the A/V sync test source only runs at {SYNC_FORMAT}")

    entry = {"name": fid, "kind": KINDS[ftype], "label": fn["name"]}
    if ftype == "dmf-mxl.1":
        pattern = get(params, "pattern.pattern.pattern", "smpte")
        if pattern not in PATTERNS:
            report.refuse(8, where(fid, "pattern.pattern.pattern"), f"{pattern!r} is not one of {', '.join(PATTERNS)}")
        entry["pattern"] = pattern
        overlay = get(params, "pattern.pattern.overlayText", MISSING)
        entry["overlayText"] = fn["name"] if overlay is MISSING else overlay
    if fmt:
        entry["format"] = fmt
    if video and audio:
        entry["flows"] = {"video": video["id"], "audio": audio["id"]}

    res = fn.get("resources") or {}
    cpu, mem = res.get("cpu"), res.get("memoryMiB")
    entry["resources"] = sizing.test_source_resources(cpu, mem)
    cpu_request = Fraction(str(cpu)) if cpu is not None else sizing.TEST_SOURCE_REQUESTS[0]
    requests = (cpu_request, Fraction(str(mem)) if mem is not None else sizing.TEST_SOURCE_REQUESTS[1],
                sizing.test_source_cpu_limit(cpu_request))
    if res.get("gpu", {}).get("count", 0) > 0 and not ctx.facility.has("gpu"):
        report.refuse(3, where(fid, "resources.gpu"), f"facility {ctx.facility.name} has no 'gpu' capability")
    return Compiled(entry, [requests])
