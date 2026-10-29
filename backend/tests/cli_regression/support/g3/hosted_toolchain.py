"""The toolchain seams of a hosted runner (design section 1 rows 4, 5 and 6; section 7.1 rows 1 and 2): no `mise`, a Windows venv layout, and a readiness probe without `AF_UNIX`.

rf4c (D2, D3): the JavaScript runtime is plain default Bun. F0's toolchain record is `{bun, python, sidecar}` (`bun_toolchain.py`), there is no node or pnpm to observe, and the TS attempt clients run
on Bun (`bunx --bun vitest`, `bun run ...`), never on the image's Node. `translating_runner` / `runner_in` are the `bun_toolchain` ones (no shell, no `mise`); `runtime_record` proves, per command, that
the runtime really is Bun and stops the run when it is not.
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
from tests.cli_regression.support.g3 import bun_toolchain, probe

Runner = tc.Runner
translating_runner = bun_toolchain.translating_runner
runner_in = bun_toolchain.runner_in


def default_call(argv: list[str], cwd: Path | None = None) -> str:
    return tc.default_runner(argv, cwd)


PROBE_EXPRESSION = "JSON.stringify({bun:process.versions.bun||null,node:process.version,exec:require('path').basename(process.execPath)})"
RUNTIME_COMMANDS = {"bun run": ["bun", "run", "node", "-p", PROBE_EXPRESSION], "bunx --bun": ["bunx", "--bun", "node", "-p", PROBE_EXPRESSION]}


def runtime_record(*, which: Callable[[str], str | None] = shutil.which, call: Callable[[list[str]], str] | None = None, cwd: Path | None = None) -> dict:
    """What the hosted run's JavaScript commands really execute on. `bun` is the identity (real path, version, sha256) of the binary on PATH; `commands` maps `bun run ...` (the build steps) and
    `bunx --bun ...` (the vitest steps) to the runtime a probe process reported. Both must be Bun: a command that runs on Node (an image Node shadowing the alias) stops the run here, because the
    registered method is Bun for every TS client. No Node is needed on PATH: the probe is Bun's own `node` alias. Written next to the run logs; F0's record holds the same Bun identity."""
    call = call or (lambda argv: default_call(argv, cwd))
    found = which("bun")
    if not found:
        raise RuntimeError("bun does not resolve on PATH")
    identity = {**tc.file_identity(Path(found)), "version": tc.last_line(call([found, "--version"]))}
    commands = {}
    for label, argv in RUNTIME_COMMANDS.items():
        probe_out = json.loads(tc.last_line(call(argv)))
        runtime = "bun" if probe_out.get("bun") else "node"
        if runtime != "bun":
            raise RuntimeError(f"`{label}` executes on node {probe_out.get('node')}, not on bun: the TS attempt clients must run on Bun")
        commands[label] = {"runtime": runtime, "reportedVersion": probe_out["bun"], "execName": probe_out["exec"]}
    return {"kind": "hosted-js-runtime-record", "bun": identity, "commands": commands}


def venv_python(root: Path, *, windows: bool) -> Path:
    return Path(root) / "backend" / ".venv" / (os.path.join("Scripts", "python.exe") if windows else os.path.join("bin", "python"))


def observe_python(root: Path, runner: Runner, *, windows: bool | None = None) -> dict:
    """`{realPath, version, sha256}` of the backend venv python: `bin/python` on Linux and macOS, `Scripts\\python.exe` on Windows. The same record the macOS function writes."""
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
