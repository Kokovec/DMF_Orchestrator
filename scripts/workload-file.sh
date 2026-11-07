#!/usr/bin/env bash
# Prints the path of a workload's definition: the hand-written workloads/<id>.yaml, else the
# compiler's workloads/generated/<id>.yaml. Fails if neither or both exist, so a hand-written
# workload and a generated one can never be confused. Every reader of a workload file uses this.
#
# usage: scripts/workload-file.sh <workload id>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="${1:?usage: $0 <workload id>}"
[[ "$ID" =~ ^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$ ]] || { echo "workload id '$ID' is not valid" >&2; exit 2; }

hand="$ROOT/workloads/$ID.yaml"
gen="$ROOT/workloads/generated/$ID.yaml"
if [ -f "$hand" ] && [ -f "$gen" ]; then
  echo "workload $ID is both hand-written (workloads/$ID.yaml) and generated (workloads/generated/$ID.yaml); remove one" >&2
  exit 1
elif [ -f "$hand" ]; then
  echo "$hand"
elif [ -f "$gen" ]; then
  echo "$gen"
else
  echo "no workload $ID: neither workloads/$ID.yaml nor workloads/generated/$ID.yaml exists (make import XML=...)" >&2
  exit 1
fi
