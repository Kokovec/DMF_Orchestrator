#!/usr/bin/env python3
"""A fake for systemctl, kubectl, findmnt, curl and ip, installed under those names.

FAKE_SCENARIO names a JSON file: {"<tool>": [{"args": [...], "stdout": "", "stderr": "", "rc": 0,
"sleep": 0}, ...]}. The first rule whose "args" all occur in the joined argv answers; with none,
the fake fails. Every call's argv is appended to FAKE_LOG, one JSON list per line.
"""
import json
import os
import sys
import time

tool = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([tool, *argv]) + "\n")
with open(os.environ["FAKE_SCENARIO"]) as fh:
    rules = json.load(fh).get(tool, [])
joined = " ".join(argv)
for rule in rules:
    if all(a in joined for a in rule.get("args", [])):
        time.sleep(rule.get("sleep", 0))
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("rc", 0))
sys.stderr.write(f"fake {tool}: no rule for {joined}\n")
sys.exit(1)
