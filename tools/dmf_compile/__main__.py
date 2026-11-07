"""CLI.

  python -m tools.dmf_compile compile <file | http(s) url> [--facility local] [--allow-remote]
  python -m tools.dmf_compile facility-export [--facility local]
  python -m tools.dmf_compile release <workload id>

Exit status: 0 compiled, 1 refused, 2 usage.
"""
import argparse
import ipaddress
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from . import allocate, compiler, facility, reader
from .problems import CompileError, Problem

REPO = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost"}


def _is_local(host):
    if host in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def fetch(source, allow_remote):
    """Bytes of a path or URL, and the name for the generated header."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in ("http", "https"):
        if not (allow_remote or (parsed.scheme == "http" and _is_local(parsed.hostname or ""))):
            raise CompileError([Problem("read", source, "only http://localhost URLs are read; "
                                                               "set ALLOW_REMOTE=1 to read another")])
        req = urllib.request.Request(source, headers={"Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(reader.MAX_BYTES + 1)
        return data, source
    if parsed.scheme not in ("", "file") or (parsed.scheme == "file" and parsed.netloc):
        raise CompileError([Problem("read", source, "must be a file path or an http(s) URL")])
    path = Path(parsed.path if parsed.scheme == "file" else source)
    try:
        return path.read_bytes(), path.name
    except OSError as e:
        raise CompileError([Problem("read", source, e.strerror or str(e))])


def rel(path, repo):
    try:
        return str(Path(path).relative_to(repo))
    except ValueError:
        return str(path)


def cmd_compile(args, repo, out):
    data, name = fetch(args.source, args.allow_remote)
    doc = reader.read(data)
    fac = facility.load(repo, args.facility)
    result = compiler.compile_document(doc, repo, fac, name, data)
    written = compiler.write(repo, result)
    a = result.allocation
    print(f"Compiled {args.source}: {result.workload_id} revision {result.revision}, library {result.library}, "
          f"facility {fac.name}", file=out)
    for w in result.warnings:
        print(f"warning: {w}", file=out)
    print(f"allocation ({'new' if result.new_allocation else 'kept'}): uid/gid {a['uid']}, quicPort {a['quicPort']}, "
          f"srtPort {a['srtPort']}", file=out)
    print(f"hostname {result.hostname}, MXL domain {result.workload_id} ({result.size_mib} MiB), "
          f"quota cpu {result.profile['cpu']} memory {result.profile['memory']}", file=out)
    for p in written:
        print(f"wrote {rel(p, repo)}", file=out)
    if args.id_file:
        Path(args.id_file).write_text(result.workload_id + "\n")
    return 0


def main(argv=None, repo=REPO, out=sys.stdout, err=sys.stderr):
    ap = argparse.ArgumentParser(prog="python -m tools.dmf_compile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="compile a published workload XML into workloads/generated/<id>.*")
    c.add_argument("source", help="file path, or http://localhost URL")
    c.add_argument("--facility", default="local")
    c.add_argument("--allow-remote", action="store_true", default=bool(os.environ.get("ALLOW_REMOTE")))
    c.add_argument("--id-file", help="after a successful compile, write the workload id here (make deploy)")
    e = sub.add_parser("facility-export", help="write .state/facility-<name>.json for DMF Toolkit")
    e.add_argument("--facility", default="local")
    r = sub.add_parser("release", help="release a workload's allocation")
    r.add_argument("workload")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "compile":
            return cmd_compile(args, repo, out)
        if args.cmd == "facility-export":
            path = facility.export(repo, args.facility)
            print(f"wrote {rel(path, repo)}", file=out)
            return 0
        if args.cmd == "release":
            done = allocate.release(repo, args.workload)
            print(f"{'released' if done else 'no allocation for'} {args.workload}", file=out)
            return 0
    except CompileError as e:
        what = getattr(args, "source", None) or args.cmd
        n = len(e.problems)
        print(f"error: refused {what}: {n} problem{'s' if n != 1 else ''}", file=err)
        for p in e.problems:
            print(f"  {p}", file=err)
        for w in e.warnings:
            print(f"warning: {w}", file=err)
        return 1
    except OSError as e:
        print(f"error: {e}", file=err)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
