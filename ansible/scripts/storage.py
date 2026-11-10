#!/usr/bin/env python3
"""PLAN-stage volume check for a generated workload.

local-path can't resize a volume, so a changed size otherwise fails partway through the apply and
leaves the Deployment changed. It doesn't enforce a volume's size either, so nothing else stops
new volumes promising more than the disk has. Hand-written workloads size theirs in chart values
and are skipped. Prints a JSON verdict; exit 1 if the workload is refused.

usage: storage.py <workload.yaml> <kubeconfig>
"""
import json
import os
import sys

from capacity import kubectl, load_workload, qty

GIB = 2**30
VOLUMES = (("state", "stateGi"), ("cache", "cacheGi"), ("recordings", "recordingsGi"))


def wanted_volumes(wl):
    """{pvc name: bytes} the design asks for, or None if the workload isn't generated."""
    mixer = next(f for f in wl["spec"]["functions"] if f["kind"] == "switchframe")
    sizes = mixer.get("volumes")
    if not sizes:
        return None
    return {f"{mixer['name']}-{vol}": sizes[key] * GIB for vol, key in VOLUMES if sizes.get(key)}


def local_path_root(kubeconfig):
    cm = kubectl(kubeconfig, "-n", "kube-system", "get", "configmap", "local-path-config")
    paths = json.loads(cm["data"]["config.json"])["nodePathMap"][0]["paths"]
    return paths[0]


def free_bytes(path):
    """Free disk at path, or at its nearest parent that exists and can be read."""
    while True:
        try:
            st = os.statvfs(path)
            return st.f_bavail * st.f_frsize
        except OSError:
            if path in ("", "/"):
                raise
            path = os.path.dirname(path)


def gib(n):
    return f"{n / GIB:g} GiB"


def main() -> int:
    wl = load_workload(sys.argv[1])
    kubeconfig = sys.argv[2]
    name = wl["metadata"]["name"]
    wanted = wanted_volumes(wl)
    if wanted is None:
        print(json.dumps({"workload": name, "ok": True, "skipped": "hand-written workload"}))
        return 0

    live = {p["metadata"]["name"]: qty(p["spec"]["resources"]["requests"]["storage"])
            for p in kubectl(kubeconfig, "-n", f"wl-{name}", "get", "pvc")["items"]}
    problems = []
    for pvc, size in sorted(wanted.items()):
        if pvc in live and live[pvc] != size:
            problems.append(f"{pvc}: the design asks for {gib(size)}, but the live volume is {gib(live[pvc])}. "
                            "local-path can't resize a volume. Restore the value in the design, "
                            f"or run make finalise WORKLOAD={name} and deploy again.")

    new = sum(size for pvc, size in wanted.items() if pvc not in live)
    root = local_path_root(kubeconfig)
    free = free_bytes(root)
    if new > free:
        problems.append(f"New volumes total {gib(new)}, more than the {gib(free)} free under {root}. "
                        "local-path doesn't enforce a volume's size, so nothing else would stop this. "
                        "Reduce the clip, playout or recording sizes, or free disk space.")

    print(json.dumps({"workload": name, "ok": not problems, "new_gib": new / GIB, "free_gib": round(free / GIB, 1),
                      "problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
