"""Facility profile: facility/<name>.yaml decides what can run here (decision D6)."""
import json
import re
from pathlib import Path

import yaml

from .problems import CompileError, Problem

# Every capability the compiler gates on (docs/orchestration/02-switchframe-options.md).
CAPABILITIES = ["mxl", "srt-in", "srt-caller-out", "rtmp-out", "recording", "srt-listener-out",
                "st2110", "moq", "gpu", "asr", "neural-key", "ai-segment", "scte104"]
_DOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)+$")


class Facility:
    def __init__(self, doc, path):
        self.path = Path(path)
        self.doc = doc
        spec = doc["spec"]
        self.name = doc["metadata"]["name"]
        self.base_domain = spec["baseDomain"]
        self.uids = (spec["workloadUids"]["first"], spec["workloadUids"]["last"])
        self.quic_ports = (spec["ports"]["quic"]["first"], spec["ports"]["quic"]["last"])
        self.srt_ports = (spec["ports"]["srt"]["first"], spec["ports"]["srt"]["last"])
        self.capabilities = dict(spec["capabilities"])
        self.limits = dict(spec.get("limits") or {})

    def has(self, capability):
        return bool(self.capabilities.get(capability, False))

    def export(self):
        """What the toolkit reads through DMF_FACILITY (toolkit spec section 8)."""
        return {"apiVersion": self.doc["apiVersion"], "kind": "FacilityExport", "name": self.name,
                "capabilities": {c: self.has(c) for c in CAPABILITIES}, "limits": self.limits}


def _problem(path, message):
    return Problem("facility", str(path), message)


def load(repo, name="local"):
    path = Path(repo) / "facility" / f"{name}.yaml"
    if not re.fullmatch(r"[a-z0-9][-a-z0-9]*", name):
        raise CompileError([_problem(path, f"facility name {name!r} is not valid")])
    try:
        doc = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise CompileError([_problem(path, "no such facility profile")])
    problems = []
    try:
        if doc.get("kind") != "Facility" or doc.get("apiVersion") != "dmf.local/v1alpha1":
            problems.append(_problem(path, "must be apiVersion dmf.local/v1alpha1, kind Facility"))
        if doc["metadata"]["name"] != name:
            problems.append(_problem(path, f"metadata.name must be {name!r}"))
        spec = doc["spec"]
        if not _DOMAIN.match(str(spec["baseDomain"])):
            problems.append(_problem(path, f"spec.baseDomain {spec['baseDomain']!r} is not a DNS name"))
        for label, rng in (("workloadUids", spec["workloadUids"]), ("ports.quic", spec["ports"]["quic"]),
                           ("ports.srt", spec["ports"]["srt"])):
            if not (isinstance(rng["first"], int) and isinstance(rng["last"], int) and rng["first"] <= rng["last"]):
                problems.append(_problem(path, f"spec.{label} must have integer first <= last"))
        for cap, v in spec["capabilities"].items():
            if cap not in CAPABILITIES:
                problems.append(_problem(path, f"spec.capabilities.{cap} is not a capability the compiler knows"))
            elif not isinstance(v, bool):
                problems.append(_problem(path, f"spec.capabilities.{cap} must be true or false"))
    except (KeyError, TypeError, AttributeError) as e:
        problems.append(_problem(path, f"missing or malformed field: {e}"))
    if problems:
        raise CompileError(problems)
    facility = Facility(doc, path)
    _check_inventory(repo, facility)
    return facility


def _check_inventory(repo, facility):
    """The inventory (written by `make infra`) must agree on the base domain, if it exists."""
    inv = Path(repo) / ".state" / "inventory.yml"
    if not inv.exists():
        return
    try:
        text = inv.read_text()
    except OSError:
        return
    m = re.search(r'"?dmf_base_domain"?\s*:\s*"?([^"\s,}]+)', text)
    if m and m.group(1) != facility.base_domain:
        raise CompileError([_problem(facility.path, f"spec.baseDomain {facility.base_domain!r} differs from "
                                                    f"dmf_base_domain {m.group(1)!r} in {inv}")])


def export(repo, name="local"):
    facility = load(repo, name)
    out = Path(repo) / ".state" / f"facility-{facility.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(facility.export(), indent=2) + "\n")
    tmp.replace(out)
    return out
