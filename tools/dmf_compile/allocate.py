"""Per-workload UID/GID and ports, kept in .state/allocations.json.

Re-importing keeps a workload's allocation; a new workload takes the lowest free values. Values in
use are those of every other allocation and of every other workloads/*.yaml, hand-written or
generated. Hand-written workloads count whether or not they are deployed: they can be deployed at
any time, and the compiler can't see the cluster.
"""
import json
from pathlib import Path

import yaml

from .problems import CompileError, Problem

FIELDS = ("uid", "quicPort", "srtPort")


def state_file(repo):
    return Path(repo) / ".state" / "allocations.json"


def load(repo):
    path = state_file(repo)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write(repo, allocations):
    path = state_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(allocations, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def in_use(repo, workload_id):
    """{field: {value: owner}} for everything except workload_id."""
    used = {f: {} for f in FIELDS}
    for wid, alloc in load(repo).items():
        if wid != workload_id:
            for f in FIELDS:
                used[f][alloc[f]] = f"allocation of {wid}"
    for path in sorted((Path(repo) / "workloads").glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text()) or {}
            name = doc["metadata"]["name"]
            if name == workload_id:
                continue
            spec = doc["spec"]
            values = {"uid": spec["mediaExchange"]["uid"], "quicPort": spec["access"].get("quicPort"),
                      "srtPort": spec["access"].get("srtPort")}
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            continue
        for f, v in values.items():
            if v:
                used[f].setdefault(int(v), f"workloads/{path.name}")
    return used


def allocate(repo, facility, workload_id):
    """Returns ({uid, gid, quicPort, srtPort}, is_new). Nothing is saved until save()."""
    used = in_use(repo, workload_id)
    existing = load(repo).get(workload_id)
    if existing:
        clashes = [f"{f} {existing[f]} is also used by {used[f][existing[f]]}" for f in FIELDS if existing[f] in used[f]]
        if clashes:
            raise CompileError([Problem("allocate", workload_id, f"kept allocation clashes: {'; '.join(clashes)}")])
        return dict(existing, gid=existing["uid"]), False
    ranges = {"uid": facility.uids, "quicPort": facility.quic_ports, "srtPort": facility.srt_ports}
    alloc, problems = {}, []
    for f in FIELDS:
        first, last = ranges[f]
        free = next((v for v in range(first, last + 1) if v not in used[f]), None)
        if free is None:
            problems.append(Problem("allocate", workload_id, f"no free {f} in {first}-{last} of facility {facility.name}"))
        alloc[f] = free
    if problems:
        raise CompileError(problems)
    return dict(alloc, gid=alloc["uid"]), True


def save(repo, workload_id, alloc):
    allocations = load(repo)
    allocations[workload_id] = {f: alloc[f] for f in FIELDS}
    _write(repo, allocations)


def release(repo, workload_id):
    allocations = load(repo)
    if allocations.pop(workload_id, None) is not None:
        _write(repo, allocations)
        return True
    return False
