"""Toolchain identity for every G1-E attempt (E2 v13 V7) and uv's informational record (V8).

A toolchain identity is, for node, pnpm and the backend venv python, the triple (real path after following every symlink and mise shim, version string, SHA-256 of the real
file); for the sidecar the pair (real path, SHA-256) checked before start, plus the version string of its `ready` line (`probe_ready_version`, taken once before F0 and compared
on every Rust-arm attempt). F0 records them (`observe`); `check` observes again before an attempt and compares: ANY difference is a mismatch, the attempt is evidence-invalid, the
stop reason is `toolchain-mismatch` and the run stops (the decision mapping is V10 item 4, in the decider). uv is recorded for information only and is never a validity condition.

`runner` is a callable `argv -> stdout text`; the tests inject a fake, the harness uses `default_runner`. node and pnpm are resolved by the exact pinned form the runs use
(`mise exec node@22.23.2 pnpm@10.0.0 --`), so a shim resolves to the file it runs. Nothing here prints an environment value; only resolved paths, versions and digests are kept.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Callable

PIN = ("node@22.23.2", "pnpm@10.0.0")
MISE = ("mise", "exec", *PIN, "--")
STOP_REASON = "toolchain-mismatch"
TOOLS = ("node", "pnpm", "python")
IDENTITY_FIELDS = ("realPath", "version", "sha256")
SIDECAR_FIELDS = ("realPath", "sha256")
READY_DEADLINE_S = 10.0

Runner = Callable[[list[str]], str]


def default_runner(argv: list[str], cwd: Path | None = None) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False, cwd=cwd)
    if done.returncode != 0:
        raise RuntimeError(f"{argv[0]} exited {done.returncode}")
    return done.stdout


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_identity(path: str | Path) -> dict:
    """{realPath, sha256} of the real file behind `path` after following every symlink."""
    real = Path(os.path.realpath(path))
    return {"realPath": str(real), "sha256": sha256_file(real)}


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("the command printed nothing")
    return lines[-1]


def observe_tool(name: str, runner: Runner) -> dict:
    """node or pnpm under the pinned mise form: the file `command -v` resolves, its version, its digest."""
    resolved = last_line(runner([*MISE, "sh", "-c", f"command -v {name}"]))
    version = last_line(runner([*MISE, name, "--version"]))
    return {**file_identity(resolved), "version": version}


def observe_python(root: Path, runner: Runner) -> dict:
    """`backend/.venv/bin/python` resolved to its real file."""
    path = Path(root) / "backend" / ".venv" / "bin" / "python"
    return {**file_identity(path), "version": last_line(runner([str(path), "--version"]))}


def observe_uv(runner: Runner) -> dict:
    """uv's identity, for information only (V8): the real path behind the shim, `uv --version`, the digest."""
    resolved = last_line(runner(["sh", "-c", "command -v uv"]))
    return {**file_identity(resolved), "version": last_line(runner([resolved, "--version"]))}


def runner_in(root: Path) -> Runner:
    """The default runner, working from the repository root: the commands are resolved where the runs use them (pnpm reads the root `package.json`)."""
    return lambda argv: default_runner(argv, Path(root))


def observe(root: Path, sidecar: str | Path | None, runner: Runner | None = None) -> dict:
    """The identities that are checked before an attempt: node, pnpm, python (triples) and, when a sidecar is given, its (real path, SHA-256)."""
    runner = runner or runner_in(root)
    out = {"node": observe_tool("node", runner), "pnpm": observe_tool("pnpm", runner), "python": observe_python(root, runner)}
    if sidecar is not None:
        out["sidecar"] = file_identity(sidecar)
    return out


def compare(recorded: dict, observed: dict) -> list[dict]:
    """Every difference between the recorded (F0) identities and the observed ones, as [{tool, field, recorded, observed}]. An identity that is absent on either side is a difference."""
    mismatches = []
    for tool, fields in (("node", IDENTITY_FIELDS), ("pnpm", IDENTITY_FIELDS), ("python", IDENTITY_FIELDS), ("sidecar", SIDECAR_FIELDS)):
        if tool == "sidecar" and tool not in observed and tool not in recorded:
            continue
        want, got = recorded.get(tool), observed.get(tool)
        for field in fields:
            a = want.get(field) if isinstance(want, dict) else None
            b = got.get(field) if isinstance(got, dict) else None
            if a is None or b is None or a != b:
                mismatches.append({"tool": tool, "field": field, "recorded": a, "observed": b})
    return mismatches


def check(root: Path, recorded: dict, sidecar: str | Path | None, runner: Runner | None = None) -> dict:
    """The per-attempt check: observe, compare with F0's record, return the record that goes into the attempt's receipt.
    {recorded, observed, match, mismatches, uv}; `match` is True only when `mismatches` is empty. uv is informational and never part of the match."""
    runner = runner or runner_in(root)
    observed = observe(root, sidecar, runner)
    scope = {k: v for k, v in recorded.items() if k in TOOLS}
    if sidecar is not None and isinstance(recorded.get("sidecar"), dict):
        scope["sidecar"] = {k: recorded["sidecar"].get(k) for k in SIDECAR_FIELDS}   # the ready-line version is checked on the Rust-arm attempt itself
    mismatches = compare(scope, observed)
    try:
        uv = observe_uv(runner)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        uv = {"realPath": None, "version": None, "sha256": None}
    return {"recorded": scope, "observed": observed, "match": not mismatches, "mismatches": mismatches, "uv": uv}


def ready_version_check(recorded_version: str, observed_version: object) -> dict:
    """The Rust-arm check: the version string of the attempt's `ready` line must equal F0's. A missing or non-string observation is a mismatch."""
    return {"recorded": recorded_version, "observed": observed_version, "match": isinstance(observed_version, str) and observed_version == recorded_version}


def probe_ready_version(binary: str | Path, journal_dir: Path, timeout: float = READY_DEADLINE_S) -> str:
    """One readiness probe of the sidecar before F0 (V7 item 1): start it exactly as `cli_runtime._ensure_started` does (a control socketpair, no workload), read its `ready` line,
    take `version`, and shut it down. Not a measurement. Raises when the first line is not a `ready` event with a version."""
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    argv = [str(binary), "--wire", "1", "--runtime-generation", os.urandom(16).hex(), "--owner-pid", str(os.getpid()), "--ownership-journal", str(journal_dir / "journal"),
            "--control-fd", str(child.fileno())]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=(child.fileno(),))
    child.close()
    try:
        line = _read_line(process, timeout)
        record = json.loads(line)
        if not isinstance(record, dict) or record.get("event") != "ready" or not isinstance(record.get("version"), str) or not record["version"]:
            raise RuntimeError("the first sidecar line is not a ready event with a version")
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


def _read_line(process: subprocess.Popen, timeout: float) -> str:
    import selectors
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    if not selector.select(timeout):
        raise RuntimeError("READY_TIMEOUT in the readiness probe")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("the sidecar closed stdout before ready")
    return line.decode("utf-8")


def record_for_freeze(root: Path, sidecar: str | Path, ready_version: str, runner: Runner | None = None) -> tuple[dict, dict]:
    """(toolchain, uv) for F0: the four identities (the sidecar with the `ready` version of the one probe) and uv's informational identity."""
    runner = runner or runner_in(root)
    observed = observe(root, sidecar, runner)
    observed["sidecar"] = {**observed["sidecar"], "readyVersion": ready_version}
    return observed, observe_uv(runner)


def exception_refusal(run: dict) -> list[str]:
    """V7 item 4 (i): the re-freeze exception is available only for a run stopped by the toolchain check itself. Returns the reasons the exception is REFUSED (empty = this run may be used)."""
    reasons = []
    if not isinstance(run, dict) or run.get("schema") != "acr-g1e-run/1":
        return ["the run record is missing or malformed"]
    if run.get("stopReason") != STOP_REASON:
        reasons.append(f"the run was stopped for {run.get('stopReason')!r}, not by the toolchain check: a run stopped for any other reason is final")
    attempts = run.get("attempts")
    if not isinstance(attempts, list) or not attempts or attempts[-1].get("validity") != "invalid" or attempts[-1].get("stopReason") != STOP_REASON:
        reasons.append("the last attempt is not the one the toolchain check invalidated")
    if isinstance(attempts, list) and any(a.get("validity") == "invalid" for a in attempts[:-1]):
        reasons.append("an earlier attempt of the run was already invalid")
    return reasons
