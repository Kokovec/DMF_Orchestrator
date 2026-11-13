"""usage: python -m tools.console [--port 8090] [--facility local]"""
import argparse
from pathlib import Path

from .server import run


def main():
    p = argparse.ArgumentParser(prog="make console", description="Facility console on 127.0.0.1")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--facility", default="local")
    args = p.parse_args()
    run(Path(__file__).resolve().parents[2], args.port, args.facility)


if __name__ == "__main__":
    main()
