#!/usr/bin/env python3
"""After a successful PLAN: write .state/<id>.plan-summary.json for the facility console
(docs/orchestration/05-facility-console.md, Deploy and Update, step 4).

The summary holds the plan's add, change and destroy counts, the addresses of the resources it
creates, changes, replaces and destroys, the Media Functions whose pods it restarts, and the
capacity verdict from capacity.py. It holds no value from the plan: Terraform's plan carries the
workload's secret values (D8), so `terraform show -json` is read through a pipe, kept in memory
and never written anywhere, and only resource addresses, names and kinds leave this script.

Restarts. Terraform shows a Helm release as changed in place whatever changed in it, and only
some changes restart pods: the compiled config, for one, is hot-loaded by the reconciler. So each
changed release is rendered with `helm template` from its values before and after the plan, and a
Deployment restarts when its pod template differs. A release whose chart files changed (its
chartHash value) can't be rendered as it was, and is listed as one that may restart.

usage: plan_summary.py --plan <file.tfplan> --tf-dir <terraform/stacks/workload> --workload <id>
                       --out <file> [--revision N] < capacity verdict (JSON)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

POD_OWNERS = {"Deployment", "StatefulSet", "DaemonSet"}


def counts(changes):
    """As Terraform's own "Plan: N to add, N to change, N to destroy" line counts them."""
    c = {"add": 0, "change": 0, "destroy": 0}
    for rc in changes:
        actions = rc["change"]["actions"]
        c["add"] += "create" in actions
        c["change"] += actions == ["update"]
        c["destroy"] += "delete" in actions
    return c


def managed(plan):
    return [rc for rc in plan.get("resource_changes", []) if rc.get("mode", "managed") == "managed"]


def pod_templates(manifests_yaml):
    """{"Kind/name": sha256 of its pod template} for every pod owner in a rendered chart."""
    import yaml
    out = {}
    for doc in yaml.safe_load_all(manifests_yaml):
        if isinstance(doc, dict) and doc.get("kind") in POD_OWNERS:
            template = (doc.get("spec") or {}).get("template")
            out[f"{doc['kind']}/{doc['metadata']['name']}"] = hashlib.sha256(
                json.dumps(template, sort_keys=True, default=str).encode()).hexdigest()
    return out


def helm_render(name, namespace, chart, values):
    """Rendered manifests, or None. The values go through stdin, merged as helm merges -f files.
    helm's errors aren't passed on: they can quote a value."""
    import yaml
    merged = {}
    for v in values:
        merged = deep_merge(merged, yaml.safe_load(v) or {})
    p = subprocess.run(["helm", "template", name, chart, "--namespace", namespace, "-f", "-"],
                       input=yaml.safe_dump(merged), capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def chart_hash(values):
    import yaml
    for v in values or []:
        h = (yaml.safe_load(v) or {}).get("chartHash")
        if h:
            return h
    return None


def has_unknown(mask):
    """after_unknown mirrors the value's shape, with true where it's unknown: values is [false]
    when the one values document is known."""
    if isinstance(mask, dict):
        return any(has_unknown(v) for v in mask.values())
    if isinstance(mask, list):
        return any(has_unknown(v) for v in mask)
    return mask is True


def release_restarts(rc, render):
    """[{"resource", "address", "why"}] for one helm_release being updated."""
    before, after = rc["change"].get("before") or {}, rc["change"].get("after") or {}
    unknown = rc["change"].get("after_unknown") or {}
    address = rc["address"]
    if any(has_unknown(unknown.get(k)) for k in ("values", "name", "namespace", "chart")):
        return [{"address": address, "resource": None, "why": "its values are known only at apply: may restart"}]
    if (before.get("values"), before.get("chart")) == (after.get("values"), after.get("chart")):
        return []  # a change elsewhere (status, timeout ...) restarts nothing
    if chart_hash(before.get("values")) != chart_hash(after.get("values")):
        return [{"address": address, "resource": None, "why": "its chart files changed: may restart"}]
    old = render(before["name"], before["namespace"], before["chart"], before.get("values") or [])
    new = render(after["name"], after["namespace"], after["chart"], after.get("values") or [])
    if old is None or new is None:
        return [{"address": address, "resource": None, "why": "helm template failed: may restart"}]
    old, new = pod_templates(old), pod_templates(new)
    return [{"address": address, "resource": k, "why": "pod template changes" if k in old else "new"}
            for k in sorted(new) if old.get(k) != new[k]]


def summarise(plan, workload, capacity, revision=None, render=helm_render):
    changes = managed(plan)
    by = {"create": [], "update": [], "replace": [], "delete": []}
    restarts = []
    for rc in changes:
        actions = rc["change"]["actions"]
        if actions == ["update"]:
            by["update"].append(rc["address"])
            if rc["type"] == "helm_release":
                restarts += release_restarts(rc, render)
        elif "create" in actions and "delete" in actions:
            by["replace"].append(rc["address"])
            if rc["type"] == "helm_release":
                restarts.append({"address": rc["address"], "resource": None, "why": "release replaced"})
        elif actions == ["create"]:
            by["create"].append(rc["address"])
        elif actions == ["delete"]:
            by["delete"].append(rc["address"])
    return {
        "workload": workload,
        "revision": revision,
        "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts(changes),
        "created": by["create"], "changed": by["update"], "replaced": by["replace"], "destroyed": by["delete"],
        "restarts": restarts,
        "capacity": capacity,
    }


def write_private(path, data):
    """Mode 600 from the start, replaced in one step."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--tf-dir", required=True)
    ap.add_argument("--workload", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--revision", default="")
    args = ap.parse_args()
    capacity = json.loads(sys.stdin.read() or "null")
    p = subprocess.run(["terraform", f"-chdir={args.tf_dir}", "show", "-json", "-no-color", os.path.abspath(args.plan)],
                       capture_output=True, text=True, env={**os.environ, "TF_WORKSPACE": args.workload})
    if p.returncode != 0:
        print(f"terraform show -json failed (exit {p.returncode})", file=sys.stderr)
        return 1
    revision = int(args.revision) if args.revision.isdigit() else None
    summary = summarise(json.loads(p.stdout), args.workload, capacity, revision)
    write_private(args.out, summary)
    c = summary["counts"]
    print(f"{args.out}: {c['add']} to add, {c['change']} to change, {c['destroy']} to destroy; "
          f"{len(summary['replaced'])} replaced, {len(summary['restarts'])} restart(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
