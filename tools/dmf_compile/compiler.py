"""The compile pipeline and the workload-level compile rules (04-orchestrator-spec.md, Compile rules).

Every problem is collected and reported together; nothing is written unless there are none.
"""
import os
import re
import stat
from pathlib import Path

import yaml

from . import allocate, emit, products, sizing
from .emit import Flow, FlowList
from .problems import CompileError, Report, where
from .products.common import leaves, rate_fraction

SCHEMA_VERSIONS = {1}
API_VERSIONS = {"dmf-toolkit/v1alpha4"}
ROLES = ["director", "audio", "graphics", "captioner", "viewer"]   # terraform/stacks/workload: local.roles
USERNAME = re.compile(r"^[A-Za-z0-9._-]+$")
SRT_PASSPHRASE_BYTES = (10, 79)                                    # server/output/srt_common.go

# What ansible/playbooks/workload-plan.yml asserts ("Validate design"), checked before writing.
PLAN_CHECKS = [
    ("kind", lambda d, w: d["kind"] == "MediaWorkload"),
    ("metadata.name", lambda d, w: d["metadata"]["name"] == w),
    ("metadata.name", lambda d, w: re.match(r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$", d["metadata"]["name"])),
    ("spec.mediaExchange.domain", lambda d, w: re.match(r"^[a-z0-9][-a-z0-9]{0,62}$", d["spec"]["mediaExchange"]["domain"])),
    ("spec.mediaExchange.uid", lambda d, w: int(d["spec"]["mediaExchange"]["uid"]) >= 10000),
    ("spec.mediaExchange.gid", lambda d, w: d["spec"]["mediaExchange"]["uid"] == d["spec"]["mediaExchange"]["gid"]),
    ("spec.mediaExchange.sizeMiB", lambda d, w: int(d["spec"]["mediaExchange"]["sizeMiB"]) >= 256),
    ("spec.functions", lambda d, w: len([f for f in d["spec"]["functions"] if f.get("kind") == "switchframe"]) == 1),
    ("spec.access.hostname", lambda d, w: re.match(r"^[a-z0-9.-]+$", d["spec"]["access"]["hostname"])),
]


class Context:
    def __init__(self, doc, facility):
        spec = doc["spec"]
        self.doc = doc
        self.facility = facility
        self.workload_id = doc["metadata"]["id"]
        self.revision = doc["metadata"]["revision"]
        self.flows = spec["flows"]
        self.flow_by_writer = {f["writer"]: f for f in self.flows}
        self.wires = spec["wires"]
        self.wires_to = {}
        for w in self.wires:
            self.wires_to.setdefault(w["to"], []).append(w)
        self.source_functions = {f["id"] for f in spec["functions"] if f["type"].startswith("dmf-mxl.")}


class Result:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def compile_document(doc, repo, facility, source_name, source_bytes):
    """doc: the published JSON from reader.read(). Returns a Result; raises CompileError."""
    report = Report()
    meta, spec = doc["metadata"], doc["spec"]
    wid = meta["id"]
    ctx = Context(doc, facility)

    # Rule 1: versions and types.
    if doc.get("_schemaVersion") not in SCHEMA_VERSIONS:
        report.refuse(1, "workload: schemaVersion", f"{doc.get('_schemaVersion')} is not a schema version the compiler knows (1)")
    if doc["apiVersion"] not in API_VERSIONS:
        report.refuse(1, "workload: apiVersion", f"{doc['apiVersion']!r} is not one the compiler knows ({', '.join(sorted(API_VERSIONS))})")
    known = []
    for fn in spec["functions"]:
        module = products.TYPES.get(fn["type"])
        if module is None:
            report.refuse(1, where(fn["id"], "type"), f"{fn['type']!r} is not a module type the compiler knows "
                                                      f"({', '.join(sorted(products.TYPES))})")
        elif fn["typeVersion"] not in module.TYPES[fn["type"]]:
            report.refuse(1, where(fn["id"], "typeVersion"), f"{fn['type']} version {fn['typeVersion']} is not one the "
                                                             f"compiler knows ({', '.join(sorted(module.TYPES[fn['type']]))})")
        else:
            known.append((fn, module))

    # Rule 2: one SwitchFrame, one domain.
    mixers = [fn for fn in spec["functions"] if fn["type"] == "switchframe.1"]
    if len(mixers) != 1:
        report.refuse(2, "workload: functions", f"needs exactly one switchframe.1 function, has {len(mixers)}"
                      + (f" ({', '.join(f['id'] for f in mixers)})" if mixers else ""))
    domains = [d["id"] for d in spec["domains"]]
    if len(domains) != 1:
        report.refuse(2, "workload: domains", f"needs exactly one domain (all pods share one host), has {len(domains)}: {', '.join(domains)}")
    else:
        for fn in spec["functions"]:
            if fn["domain"] != domains[0]:
                report.refuse(2, where(fn["id"], "domain"), f"{fn['domain']!r} is not the workload's domain {domains[0]!r}")
        for flow in spec["flows"]:
            if flow["domain"] != domains[0]:
                report.refuse(2, f"flow {flow['label']}: domain", f"{flow['domain']!r} is not the workload's domain {domains[0]!r}")

    # Functions, test sources first.
    compiled = {}
    for fn, module in sorted(known, key=lambda x: x[0]["type"] == "switchframe.1"):
        if fn["type"] == "switchframe.1" and len(mixers) != 1:
            continue
        compiled[fn["id"]] = module.compile(fn, ctx, report)
    mixer = compiled.get(mixers[0]["id"]) if len(mixers) == 1 else None
    if mixer and mixer.entry.get("format") and any(c.entry["kind"] == "mxl-syncsrc" for c in compiled.values()) \
            and mixer.entry["format"] != "1080p29.97":
        report.refuse(8, where(mixers[0]["id"], "engine.engine"), f"is {mixer.entry['format']}, but the workload has an "
                                                                  "A/V sync test source, which only runs at 1080p29.97")

    ignored_for_now(doc, report)
    members = access_members(spec, report)

    # Rule 7: the id isn't a hand-written workload's. Those live in workloads/<id>.yaml, generated
    # ones in workloads/generated/, and scripts/workload-file.sh refuses an id that has both.
    if emit.hand_written(repo, wid).exists():
        report.refuse(7, f"workload: id {wid}", f"workloads/{wid}.yaml is a hand-written workload with this id; "
                                                "it is never overwritten")

    # Rule 6: secrets that are referenced exist.
    used = [s for c in compiled.values() for s in c.secrets]
    check_secrets(repo, wid, used, report)

    # Rule 10: two running workloads with the same flow IDs conflict in NMOS.
    flow_ids = [f["id"] for f in spec["flows"]]
    for what, value, other in allocate.identity_clashes(repo, wid, meta["uid"], flow_ids):
        report.refuse(10, f"workload: {what} {value}", f"is already used by workload {other} (.state/allocations.json); "
                                                       "each workload needs its own uid and flow IDs")

    alloc, is_new = None, None
    try:
        alloc, is_new = allocate.allocate(repo, facility, wid)
    except CompileError as e:
        report.problems.extend(e.problems)
    report.raise_if_any()

    # Sizing.
    video = [(f["format"]["frameWidth"], f["format"]["frameHeight"], rate_fraction(f["format"]["grainRate"]), f["historyMs"])
             for f in spec["flows"] if f["essence"] == "video"]
    size_mib = sizing.domain_size_mib(video, sum(1 for f in spec["flows"] if f["essence"] == "audio"))
    test_sources = [r for fid, c in compiled.items() if c is not mixer for r in c.pod_requests]
    profile = sizing.quota(sizing.workload_requests(mixer.pod_requests[0], test_sources))

    functions = [style_function(c.entry) for fid, c in compiled.items() if c is not mixer] + [style_function(mixer.entry)]
    out = {"apiVersion": "dmf.local/v1alpha2", "kind": "MediaWorkload",
           "metadata": {"name": wid, "description": meta.get("description", ""), "uid": meta["uid"],
                        "revision": meta["revision"], "libraryVersion": meta["libraryVersion"]},
           "spec": {"resourceProfile": Flow(profile),
                    "mediaExchange": Flow({"domain": wid, "uid": alloc["uid"], "gid": alloc["gid"], "sizeMiB": size_mib}),
                    "access": {"hostname": f"{wid}.{facility.base_domain}", "quicPort": alloc["quicPort"],
                               "srtPort": alloc["srtPort"], "members": members},
                    "functions": functions}}

    # Rule 8: what plan asserts.
    for field, check in PLAN_CHECKS:
        try:
            ok = check(out, wid)
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            report.refuse(8, f"workload: {field}", "fails the check in ansible/playbooks/workload-plan.yml "
                                                   f"(facility {facility.name}: uids {facility.uids[0]}-{facility.uids[1]}, "
                                                   f"baseDomain {facility.base_domain})")
    report.raise_if_any()

    head = emit.header(source_name, meta["revision"])
    yaml_text = emit.render_yaml(out, head)
    for problem in emit.literal_format_problems(yaml_text, out):
        report.refuse(8, "workload: generated YAML", problem)
    report.raise_if_any()
    return Result(workload_id=wid, revision=meta["revision"], library=meta["libraryVersion"], warnings=report.warnings,
                  allocation=alloc, new_allocation=is_new, hostname=out["spec"]["access"]["hostname"],
                  size_mib=size_mib, profile=profile, doc=out, config=mixer.config, flow_ids=flow_ids,
                  yaml_text=yaml_text, config_text=emit.render_json(mixer.config),
                  source_bytes=source_bytes)


def write(repo, result):
    """Saves the allocation, then the files."""
    allocate.save(repo, result.workload_id, result.allocation, result.doc["metadata"]["uid"], result.flow_ids)
    return emit.write(repo, result.workload_id, result.yaml_text, result.config_text, result.source_bytes)


def style_function(entry):
    """Short leaf mappings and lists in flow style, as in the hand-written workload."""
    out = {}
    for k, v in entry.items():
        if k == "mxlSources":
            out[k] = [Flow(s) for s in v]
        elif k == "resources":
            out[k] = Flow({kk: Flow(vv) for kk, vv in v.items()})
        elif isinstance(v, dict):
            out[k] = Flow(v)
        elif isinstance(v, list):
            out[k] = FlowList(v)
        else:
            out[k] = v
    return out


def access_members(spec, report):
    roles = (spec.get("access") or {}).get("roles") or {}
    members = {}
    for role, users in roles.items():
        if role not in ROLES:
            report.refuse(8, f"workload: access.roles.{role}", f"is not a role of this facility ({', '.join(ROLES)})")
            continue
        bad = [u for u in users if not USERNAME.match(u)]
        if bad:
            report.refuse(8, f"workload: access.roles.{role}", f"{', '.join(map(repr, bad))} not valid usernames")
        if users:
            members[role] = FlowList(users)
    return members


# The toolkit's defaults for the ignored network.* and payload.* leaves (catalogs/switchframe.catalog.json),
# keyed by the path below the block, with pathA/pathB as <path>. An empty object is the toolkit's
# placeholder for a group with nothing set. Leaves with no default here, such as payload bitrateMbps,
# always count as set.
TOOLKIT_DEFAULTS = {
    "network.exposure": "wan-facing",
    "payload.<path>.container": "mpeg-ts",
    "payload.<path>.videoCodec": "h264",
    "payload.<path>.audioCodec": "aac",
}


def _is_toolkit_default(path, value):
    if value == {}:
        return True
    key = re.sub(r"\.path[AB](?=\.|$)", ".<path>", path)
    return key in TOOLKIT_DEFAULTS and TOOLKIT_DEFAULTS[key] == value


def ignored_for_now(doc, report):
    """One warning for each 'Ignored for now' option whose value differs from the toolkit's default
    (02-switchframe-options.md). flows[].historyMs gets none: the schema requires it."""
    spec = doc["spec"]
    found = {}

    def add(kind, text):
        found.setdefault(kind, []).append(text)

    for fn in spec["functions"]:
        fid = fn["id"]
        for block, tree in (fn.get("parameters") or {}).items():
            for kind in ("network", "payload"):
                if isinstance(tree, dict) and kind in tree:
                    changed = [f"{block}.{p}" for p, v in leaves(tree[kind], kind) if not _is_toolkit_default(p, v)]
                    if changed:
                        add(f"{kind}.*", f"{fid}: " + ", ".join(changed))
        if fn.get("placement"):
            add("placement.*", f"{fid}: " + ", ".join(sorted(fn["placement"])))
        res = (fn.get("expectations") or {}).get("resilience")
        if res not in (None, "none"):
            add("expectations.resilience", f"{fid}: {res}")
        disc = fn.get("discovery") or {}
        if disc.get("labels"):
            add("discovery.labels", f"{fid}: {', '.join(disc['labels'])}")
        if disc.get("register") is False:
            add("discovery.register: false", fid)
        if fn.get("dependencies"):
            add("dependencies", f"{fid}: " + ", ".join(d["name"] for d in fn["dependencies"]))
    for flow in spec["flows"]:
        disc = flow["discovery"]
        if disc.get("labels"):
            add("discovery.labels", f"flow {flow['label']}: {', '.join(disc['labels'])}")
        if disc.get("register") is False:
            add("discovery.register: false", f"flow {flow['label']}")

    why = {"discovery.register: false": "mxl-flow-agent registers every flow in the domain"}
    for kind, places in found.items():
        report.warn("ignored for now", f"{kind} is set ({'; '.join(places)}): "
                                       f"{why.get(kind, 'it changes nothing on this single-host facility')}")


# A Kubernetes Secret key, which the reconciler reads as a file name (scripts/secret-set.py too).
SECRET_NAME = re.compile(r"^[-._A-Za-z0-9]{1,253}$")


def secrets_file(repo, workload_id):
    return Path(repo) / ".state" / "secrets" / f"{workload_id}.yaml"


def check_secrets(repo, workload_id, used, report):
    """Rule 6, only when a secret is referenced. Values are never printed."""
    if not used:
        return
    path = secrets_file(repo, workload_id)
    rel = f".state/secrets/{workload_id}.yaml"
    values = {}
    if path.exists():
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            report.refuse(6, f"workload: {rel}", f"is mode {mode:o}; it must be readable by its owner only (600)")
        try:
            values = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            report.refuse(6, f"workload: {rel}", "is not valid YAML")
        if not isinstance(values, dict):
            report.refuse(6, f"workload: {rel}", "must map secret names to values")
            values = {}
    for place, name, param in used:
        if not SECRET_NAME.match(name) or name in (".", ".."):
            report.refuse(6, place, f"secret name {name!r} must match {SECRET_NAME.pattern}: it becomes a key of the "
                                    "workload-refs Secret")
            continue
        if name not in values:
            report.refuse(6, place, f"secret {name!r} is not in {rel}; run: make secret-set WORKLOAD={workload_id} NAME={name}")
            continue
        value = values[name]
        if not isinstance(value, str) or not value:
            report.refuse(6, place, f"secret {name!r} in {rel} must be a non-empty string")
        elif param.endswith(".srt.passphrase") and not SRT_PASSPHRASE_BYTES[0] <= len(value.encode()) <= SRT_PASSPHRASE_BYTES[1]:
            report.refuse(6, place, f"secret {name!r} must be {SRT_PASSPHRASE_BYTES[0]}-{SRT_PASSPHRASE_BYTES[1]} bytes "
                                    "for SRT encryption")

