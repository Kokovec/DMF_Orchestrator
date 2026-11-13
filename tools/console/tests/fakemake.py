#!/usr/bin/env python3
"""A fake `make` for the deploy flow tests, installed as `make` on the terminal's PATH.

FAKE_MAKE_SCENARIO names a JSON file: {"<target>": [response, ...]}, answered in order (the
count is kept in FAKE_MAKE_SCENARIO + ".n"). A response: {"rc": 0, "last_import": {...},
"plan_summary": {...}, "prompt": true, "sleep": 0}. last_import is written to
.state/last-import.json with this call's XML= as its source; plan_summary to
.state/<WORKLOAD>.plan-summary.json with its workload. With "prompt", it reads a value with no
echo, as scripts/secret-set.py does, and stores its length only. Each call's argv is appended to
FAKE_MAKE_LOG as one JSON list.
"""
import getpass
import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
target = argv[0] if argv else ""
args = dict(a.split("=", 1) for a in argv[1:] if "=" in a)
with open(os.environ["FAKE_MAKE_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
scenario = Path(os.environ["FAKE_MAKE_SCENARIO"])
counts_file = Path(str(scenario) + ".n")
counts = json.loads(counts_file.read_text()) if counts_file.exists() else {}
n = counts.get(target, 0)
counts[target] = n + 1
counts_file.write_text(json.dumps(counts))
responses = json.loads(scenario.read_text()).get(target, [])
r = responses[min(n, len(responses) - 1)] if responses else {"rc": 0}
time.sleep(r.get("sleep", 0))
state = Path(".state")
state.mkdir(exist_ok=True)
print(f"fake make {' '.join(argv)}")
if r.get("prompt"):
    value = getpass.getpass(f"{args.get('NAME')} for {args.get('WORKLOAD')}: ")
    (state / "fake-secret-lengths").open("a").write(f"{args.get('NAME')} {len(value)}\n")
if "last_import" in r:
    (state / "last-import.json").write_text(json.dumps({**r["last_import"], "source": args.get("XML")}))
if "plan_summary" in r:
    (state / f"{args['WORKLOAD']}.plan-summary.json").write_text(
        json.dumps({**r["plan_summary"], "workload": args["WORKLOAD"]}))
sys.exit(r.get("rc", 0))
