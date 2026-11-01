#!/usr/bin/env python3
"""PLAN-stage capacity check (DMF RA: "Reserve matching capacity ... protected
from oversubscription").

Compares a workload's resource profile (plus its Flow Domain tmpfs, which is
RAM) with cluster allocatable minus every other workload's reservation
(ResourceQuota "dmf-reservation"). Prints a JSON verdict; exit 1 if it won't fit.

usage: capacity.py <workload.yaml> <kubeconfig>
"""
import json
import subprocess
import sys

UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "m": 1e-3}


def qty(v) -> float:
    s = str(v)
    for suf in sorted(UNITS, key=len, reverse=True):
        if s.endswith(suf):
            return float(s[: -len(suf)]) * UNITS[suf]
    return float(s)


def kubectl(kubeconfig, *args):
    out = subprocess.run(["kubectl", "--kubeconfig", kubeconfig, *args, "-o", "json"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def load_workload(path):
    # PyYAML ships with Ansible; import lazily so --help works without it.
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh)


def main() -> int:
    wl = load_workload(sys.argv[1])
    kubeconfig = sys.argv[2]
    name = wl["metadata"]["name"]
    prof = wl["spec"]["resourceProfile"]
    shm = wl["spec"]["mediaExchange"]["sizeMiB"] * 2**20

    nodes = kubectl(kubeconfig, "get", "nodes")["items"]
    alloc_cpu = sum(qty(n["status"]["allocatable"]["cpu"]) for n in nodes)
    alloc_mem = sum(qty(n["status"]["allocatable"]["memory"]) for n in nodes)

    reserved_cpu = reserved_mem = 0.0
    for q in kubectl(kubeconfig, "get", "resourcequota", "-A", "--field-selector", "metadata.name=dmf-reservation")["items"]:
        if q["metadata"]["namespace"] == f"wl-{name}":
            continue  # re-planning an already provisioned workload
        hard = q["spec"].get("hard", {})
        reserved_cpu += qty(hard.get("requests.cpu", 0))
        reserved_mem += qty(hard.get("requests.memory", 0))
    # Platform services (monitoring, Keycloak, ingress ...) are not quota'd: use their actual requests.
    for p in kubectl(kubeconfig, "get", "pods", "-A", "--field-selector", "status.phase=Running")["items"]:
        if p["metadata"]["namespace"].startswith("wl-"):
            continue
        for c in p["spec"]["containers"]:
            req = c.get("resources", {}).get("requests", {})
            reserved_cpu += qty(req.get("cpu", 0))
            reserved_mem += qty(req.get("memory", 0))

    need_cpu = qty(prof["cpu"])
    need_mem = qty(prof["memory"]) + shm
    free_cpu = alloc_cpu - reserved_cpu
    free_mem = alloc_mem - reserved_mem
    verdict = {
        "workload": name,
        "fits": need_cpu <= free_cpu and need_mem <= free_mem,
        "requested": {"cpu": need_cpu, "memory_gib": round(need_mem / 2**30, 2), "flow_domain_mib": shm // 2**20},
        "free": {"cpu": round(free_cpu, 2), "memory_gib": round(free_mem / 2**30, 2)},
        "allocatable": {"cpu": alloc_cpu, "memory_gib": round(alloc_mem / 2**30, 2)},
    }
    print(json.dumps(verdict))
    return 0 if verdict["fits"] else 1


if __name__ == "__main__":
    sys.exit(main())
