"""External launch recorder for the G2 baseline snapshots.

Runs as the child of a native PTY and writes what it observed: argv, cwd and
the complete environment. It never loads vendor packages and reads no
credential store; the backend it is launched by runs with an isolated home.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main():
    vendor, destination, *arguments = sys.argv[1:]
    if "--version" in arguments:
        print("99.0.0-regression" if vendor == "cursor" else f"{vendor} 99.0.0-regression")
        return
    Path(destination).write_text(json.dumps({
        "argv": arguments,
        "cwd": os.getcwd(),
        "env": dict(os.environ),
    }), encoding="utf-8")
    print("LAUNCH_SNAPSHOT_READY", flush=True)
    for line in sys.stdin:
        if line.strip() == "exit":
            return


if __name__ == "__main__":
    main()
