"""The toolchain seams of a hosted runner (design section 1 rows 4, 5 and 6; section 7.1 rows 1 and 2): no `mise`, a Windows venv layout, and a readiness probe without `AF_UNIX`.

`g1e.toolchain.observe/check` take a `runner` (`argv -> stdout`) and emit exactly four argv shapes: `[*MISE, "sh", "-c", "command -v X"]`, `[*MISE, X, "--version"]`, `["sh", "-c", "command -v uv"]` and
`[<python>, "--version"]`. `translating_runner` answers those shapes with `which` plus a direct call and refuses any other `mise` argv, so a changed g1e file cannot slip through unseen. node and pnpm
are the job's own installs; the per-attempt check compares the job's F0 with the same job, so their versions need not equal the Mac's.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import toolchain as tc
from tests.cli_regression.support.g3 import probe

Runner = tc.Runner
MISE = list(tc.MISE)


def default_call(argv: list[str], cwd: Path | None = None) -> str:
    return tc.default_runner(argv, cwd)


def translating_runner(*, which: Callable[[str], str | None] = shutil.which, call: Callable[[list[str]], str] = default_call) -> Runner:
    """A runner that needs neither `mise` nor `sh`. An unknown tool resolves to a RuntimeError (the observation fails, as the macOS one does when `command -v` finds nothing)."""
    def resolve(name: str) -> str:
        found = which(name)
        if not found:
            raise RuntimeError(f"{name} does not resolve on PATH")
        return found

    def run(argv: list[str]) -> str:
        if argv[:len(MISE)] == MISE:
            rest = argv[len(MISE):]
            if len(rest) == 3 and rest[:2] == ["sh", "-c"] and rest[2].startswith("command -v "):
                return resolve(rest[2][len("command -v "):].strip()) + "\n"
            if len(rest) == 2 and rest[1] == "--version" and rest[0] in tc.TOOLS:
                return call([resolve(rest[0]), "--version"])
            raise RuntimeError(f"unexpected mise argv shape: {rest[:3]}")
        if len(argv) == 3 and argv[:2] == ["sh", "-c"] and argv[2] == "command -v uv":
            return resolve("uv") + "\n"
        return call(argv)
    return run


def runner_in(root: Path) -> Runner:
    """The replacement of `tc.runner_in`: the translating runner, working from the repository root (the commands are resolved on PATH; pnpm reads the root `package.json`)."""
    return translating_runner(call=lambda argv: tc.default_runner(argv, Path(root)))


def venv_python(root: Path, *, windows: bool) -> Path:
    return Path(root) / "backend" / ".venv" / (os.path.join("Scripts", "python.exe") if windows else os.path.join("bin", "python"))


def observe_python(root: Path, runner: Runner, *, windows: bool | None = None) -> dict:
    """`{realPath, version, sha256}` of the backend venv python: `bin/python` on Linux, `Scripts\\python.exe` on Windows. The same record the macOS function writes."""
    path = venv_python(root, windows=(os.name == "nt") if windows is None else windows)
    return {**tc.file_identity(path), "version": tc.last_line(runner([str(path), "--version"]))}


def stdio_probe_argv(binary: str | Path, journal_dir: Path, *, generation: str | None = None) -> list[str]:
    """The sidecar argv as `cli_runtime.py` builds it on Windows: the sidecar's own stdin/stdout is the control channel, so there is no `--control-fd` (design row 6)."""
    argv = probe.probe_argv(binary, journal_dir, 0, generation=generation)
    if argv[-2] != "--control-fd":
        raise RuntimeError("the probe argv shape changed")
    return argv[:-2]


def read_first_line(stream, timeout: float) -> bytes:
    """The first line of a pipe with a deadline, without `select` (which does not work on a Windows pipe): a reader thread and a queue."""
    box: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: box.put(stream.readline()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("READY_TIMEOUT in the readiness probe") from None


def probe_ready_version_stdio(binary: str | Path, journal_dir: Path, timeout: float = tc.READY_DEADLINE_S, *, popen=subprocess.Popen) -> str:
    """The `sidecar-probe` of amendment 1 without `AF_UNIX`: start the sidecar with stdin/stdout pipes, read its first line, take `version`, close stdin. Raises on anything but a `ready` event with a version."""
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    process = popen(stdio_probe_argv(binary, journal_dir), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        line = read_first_line(process.stdout, timeout)
        if not line:
            raise RuntimeError("the sidecar closed stdout before ready")
        record = json.loads(line.decode("utf-8"))
        if not isinstance(record, dict) or record.get("event") != "ready" or not isinstance(record.get("version"), str) or not record["version"]:
            event = record.get("event") if isinstance(record, dict) else None
            raise RuntimeError(f"the first sidecar line is not a ready event with a version (event: {event!r})")
        return record["version"]
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
