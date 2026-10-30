"""Installed markers of the hosted port (review-fix round 1, finding 9; round 2, process-specific roles). Standard library only.

Each child process that installs a hosted seam (the Python seams in every `python -m ...` step, the `termios` shim in a measured peer, the counters pipe server in the isolated backend, the Node preload, the
Linux reader's bytes-valued `ru_maxrss`) writes `<ACR_G3_HOSTED_MARKERS>/<kind>-<pid>.marker` after the install worked; the first client connection to the counters pipe writes `counters-connection`. A marker holds a
kind, a process id and the role of the process (the stem of its script or `-m` module, e.g. `g1e_peer`) and nothing measured. `hosted_checks` reads the directory, so a hook that did not load (the
interpreter-start hook silently not found, a variable that did not reach a child) is named in the rehearsal summary instead of surfacing later as a vague receipt failure, and a check that is about one process
(the `termios` shim in the measured peer) is satisfied only by a marker of that process's role. Without the variable nothing is written (the macOS run, unit tests).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENV = "ACR_G3_HOSTED_MARKERS"
KINDS = ("python-seams", "termios-shim", "counters-server", "counters-connection", "node-preload", "reader-rss-bytes", "ws-defer")      # `ws-defer`: the Linux Node preload had to wait for a port (written by the preload; informative, never required)
EXPECTED = {"Linux": ("python-seams", "reader-rss-bytes"), "Windows": KINDS[:5]}
SUFFIX = ".marker"


def process_role(argv: list[str] | None = None, orig_argv: list[str] | None = None) -> str:
    """The role of this process: the stem of its script, or of its `-m` module (`-m` and `-c` are what `sys.argv[0]` holds at interpreter start). `unknown` if neither can be told."""
    argv = sys.argv if argv is None else argv
    orig = getattr(sys, "orig_argv", []) if orig_argv is None else orig_argv
    first = argv[0] if argv else ""
    if first and first not in ("-m", "-c"):
        return Path(first).stem or "unknown"
    if "-m" in orig and orig.index("-m") + 1 < len(orig):
        return orig[orig.index("-m") + 1].rsplit(".", 1)[-1] or "unknown"
    return "unknown"


def write(kind: str, *, env: dict | None = None, pid: int | None = None, role: str | None = None) -> bool:
    """True = a marker was written now. False = no directory named, the marker exists already, or the directory cannot be written (a missing marker is itself the finding)."""
    if kind not in KINDS:
        raise ValueError(f"unknown marker kind {kind!r}")
    directory = (os.environ if env is None else env).get(ENV)
    if not directory:
        return False
    process = os.getpid() if pid is None else pid
    path = Path(directory) / f"{kind}-{process}{SUFFIX}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "pid": process, "role": process_role() if role is None else role}))
    except FileExistsError:
        return False
    except OSError:
        return False
    return True


def kinds_present(directory: str | Path) -> set[str]:
    """The kinds with at least one marker in `directory` (names only)."""
    try:
        names = [entry.name for entry in Path(directory).iterdir() if entry.name.endswith(SUFFIX)]
    except OSError:
        return set()
    return {name[: -len(SUFFIX)].rsplit("-", 1)[0] for name in names} & set(KINDS)


def roles_present(directory: str | Path) -> dict[str, set[str]]:
    """kind -> the roles of the processes that wrote one (a marker without a role, as the Node preload writes, counts as `unknown`). Names only."""
    out: dict[str, set[str]] = {}
    try:
        entries = [entry for entry in Path(directory).iterdir() if entry.name.endswith(SUFFIX)]
    except OSError:
        return out
    for entry in entries:
        kind = entry.name[: -len(SUFFIX)].rsplit("-", 1)[0]
        if kind not in KINDS:
            continue
        try:
            role = json.loads(entry.read_text(encoding="utf-8")).get("role", "unknown")
        except (OSError, ValueError, AttributeError):
            role = "unknown"
        out.setdefault(kind, set()).add(str(role))
    return out
