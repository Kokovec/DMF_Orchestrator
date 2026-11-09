#!/usr/bin/env python3
"""make secret-set: store one workload secret in .state/secrets/<id>.yaml (mode 600, git-ignored).

Workload XML names secrets (<value type="secret">name</value>, decision D8); their values live
here. The value is read without echo from a terminal, or as one line from stdin otherwise, and is
never printed. Compile rule 6 checks the referenced names exist; make provision copies the ones
the config refers to into the workload-refs Secret, which the reconciler reads.

usage: secret-set.py <secrets dir> <workload id> <name>
"""
import getpass
import os
import re
import sys
import tempfile

import yaml

WORKLOAD = re.compile(r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$")
NAME = re.compile(r"^[-._A-Za-z0-9]{1,253}$")       # a Kubernetes Secret key: the reconciler's file name


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    directory, workload, name = argv[1:]
    if not WORKLOAD.match(workload):
        print(f"workload id {workload!r} is not valid", file=sys.stderr)
        return 2
    if not NAME.match(name) or name in (".", ".."):
        print(f"secret name {name!r} must match {NAME.pattern} (it becomes a Kubernetes Secret key)", file=sys.stderr)
        return 2
    if sys.stdin.isatty():
        value = getpass.getpass(f"{name} for {workload}: ")
        if getpass.getpass("again: ") != value:
            print("the two entries differ; nothing changed", file=sys.stderr)
            return 1
    else:
        value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        print("empty value; nothing changed", file=sys.stderr)
        return 1

    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = os.path.join(directory, f"{workload}.yaml")
    values = {}
    if os.path.exists(path):
        with open(path) as f:
            values = yaml.safe_load(f) or {}
        if not isinstance(values, dict):
            print(f"{path} doesn't map names to values; fix or remove it", file=sys.stderr)
            return 1
    existed = name in values
    values[name] = value
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{workload}.", suffix=".yaml")   # mode 600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"# Secret values of workload {workload} (make secret-set). Mode 600; never commit.\n")
            yaml.safe_dump(values, f, default_flow_style=False, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    rel = os.path.relpath(path)
    print(f"{'updated' if existed else 'set'} {name} in {rel}. Run make provision WORKLOAD={workload} to apply it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
