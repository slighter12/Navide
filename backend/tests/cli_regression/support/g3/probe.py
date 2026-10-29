"""The G3 readiness probe: `g1e.toolchain.probe_ready_version` with the wire version the backend speaks NOW.

G3-r2 amendment 1 asks for `toolchain.sidecar.readyVersion` through the G1-E `sidecar-probe` step. That function hard-codes `--wire 1` (G1-E ran at wire 1); the backend's `WIRE_VERSION` is 2 at H, so the
unchanged function makes the sidecar answer with a `fatal` line and F0 cannot be built (found on the first real run of the F0 builder, G3-r2 Phase A). The g1e file is pinned and stays untouched; this
module repeats its steps (start exactly as `cli_runtime._ensure_started` does, read the first line, take `version`, shut down) with the wire version taken from `cli_runtime_frames.WIRE_VERSION`.
Not a measurement.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

from agent_team_backend import cli_runtime_frames
from tests.cli_regression.support.g1e import toolchain as tc


def probe_argv(binary: str | Path, journal_dir: Path, control_fd: int, *, wire_version: int | None = None, generation: str | None = None) -> list[str]:
    """The sidecar's argv as `cli_runtime._ensure_started` builds it on POSIX (no fault flags)."""
    wire = cli_runtime_frames.WIRE_VERSION if wire_version is None else wire_version
    return [str(binary), "--wire", str(wire), "--runtime-generation", generation or os.urandom(16).hex(), "--owner-pid", str(os.getpid()), "--ownership-journal", str(Path(journal_dir) / "journal"),
            "--control-fd", str(control_fd)]


def probe_ready_version(binary: str | Path, journal_dir: Path, timeout: float = tc.READY_DEADLINE_S) -> str:
    """One readiness probe of the sidecar before F0: raises when the first line is not a `ready` event with a version."""
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    process = subprocess.Popen(probe_argv(binary, journal_dir, child.fileno()), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=(child.fileno(),))
    child.close()
    try:
        record = json.loads(tc._read_line(process, timeout))
        if not isinstance(record, dict) or record.get("event") != "ready" or not isinstance(record.get("version"), str) or not record["version"]:
            event = record.get("event") if isinstance(record, dict) else None
            raise RuntimeError(f"the first sidecar line is not a ready event with a version (event: {event!r})")
        return record["version"]
    finally:
        parent.close()
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
