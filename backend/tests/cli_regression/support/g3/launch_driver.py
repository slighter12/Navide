"""Disposable backend owner of ONE agent-launch attempt (G3 H4). Adapted from `g1e/driver.py` (which stays untouched): same isolated root, `BackendProcess`, sanitized environment and arm
configuration (`g1e.driver.configure`, imported), but the fixture CLIs are `claude` and `aider` shims over the unmodified `baseline_peer.py`, there is exactly one agent pane, and no resource sampler runs.

    python -m tests.cli_regression.support.g3.launch_driver ROOT VENDOR CELL MODE        (MODE: python | rust-shell)

It writes `ROOT/endpoint.json` (url, the one agent pane description) when the backend is ready, waits for a line on stdin, shuts the backend down, and writes `ROOT/launch-final.json`
(the runtime receipts of the pane: `owned` / `ready` facts the receipt needs, survivors). The driver never decides a verdict. Never launches a real vendor CLI: PATH is shimmed and HOME is the root.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

import psutil

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.support.g1e import driver as g1e_driver

SUPPORT = Path(__file__).resolve().parents[1]
WORKLOAD_PATH = g1e_driver.WORKLOAD_PATH
AGENT_VENDORS = ("claude", "aider")
VENDORS = (*AGENT_VENDORS, "shell")          # `shell` = the plain shell pane of the pre-F0 smoke, never a registered row
SHELL_READY_LINE = "printf 'BASELINE_READY {\"smoke\":\"shell\"}\\n'; exec sleep 120"


def agent_pane(root: Path, vendor: str, cell: int, mode: str, bin_dir: Path, workload: dict) -> dict:
    from agent_team_backend import osplat
    pane = str(uuid.uuid5(uuid.NAMESPACE_URL, f"g3launch:{mode}:{vendor}:{cell}:{root.name}"))
    workspace = root / "workspace launch"
    workspace.mkdir(exist_ok=True)
    if vendor == "shell":
        return {"pane": pane, "vendor": vendor, "workspace": str(workspace), "command": ["sh", "-c", SHELL_READY_LINE], "wrapped": True, "commandLine": SHELL_READY_LINE, "argv": [], "index": 0, "logPath": None}
    args = [str(root), vendor, pane, str(workspace), workload["input"], str(WORKLOAD_PATH)]
    argv = [base_python_executable(), "-u", str(SUPPORT / "baseline_peer.py"), *args]
    line = " ".join(osplat.paths.quote_arg(word) for word in argv)
    return {"pane": pane, "vendor": vendor, "workspace": str(workspace), "command": argv, "wrapped": True, "commandLine": line, "argv": args, "index": 0, "logPath": None}


def receipt_facts(root: Path, pane: str, backend_pid: int) -> dict:
    """What the attempt receipt needs from the runtime receipts: `owned` for this pane with its argv_source, the generation's `ready` sidecar digest, loss/restart facts. Python arm: none."""
    path = root / "runtime-receipts.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
    ready = next((r for r in records if r.get("event") == "ready" and r.get("owner_pid") == backend_pid), None)
    owned = next((r for r in records if r.get("event") == "owned" and r.get("pane_id") == pane), None)
    return {"owned": owned is not None, "argvSource": owned.get("argv_source") if owned else None, "readySidecarSha256": ready.get("executableSha256") if ready else None,
            "runtimeLost": any(r.get("event") in ("runtime_invalidated", "loss_settled") for r in records),
            "sidecarRestarts": max(0, sum(1 for r in records if r.get("event") == "ready" and r.get("owner_pid") == backend_pid) - 1)}


async def main(root: Path, vendor: str, cell: int, mode: str) -> None:
    assert vendor in VENDORS and mode in ("python", "rust-shell"), (vendor, mode)
    (root / "home").mkdir(exist_ok=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in AGENT_VENDORS:
        install_cli_shim(bin_dir, name, [base_python_executable(), "-u", str(SUPPORT / "baseline_peer.py")])
    backend = BackendProcess(root, artifact_prefix="launch-")
    g1e_driver.configure(backend, mode, bin_dir)
    for name in (".bash_profile", ".zprofile", ".profile"):
        (Path(backend.env["HOME"]) / name).write_text(f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\"\n", encoding="utf-8")
    workload = json.loads(WORKLOAD_PATH.read_text(encoding="utf-8"))
    (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
    async with backend:
        process = psutil.Process(backend.process.pid)
        pane = agent_pane(root, vendor, cell, mode, bin_dir, workload)
        endpoint = {"url": backend.url, "pane": pane, "home": str(root / "home"), "shell": "/bin/bash", "backendPid": process.pid}
        pending = root / "endpoint.pending.json"
        pending.write_text(json.dumps(endpoint), encoding="utf-8")
        pending.replace(root / "endpoint.json")
        async with asyncio.timeout(120):
            await asyncio.to_thread(sys.stdin.readline)
        children_before = [c.pid for c in process.children(recursive=True)]
    survivors = [pid for pid in children_before if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE]
    (root / "launch-final.json").write_text(json.dumps({**receipt_facts(root, pane["pane"], process.pid), "survivors": survivors, "backendPid": process.pid}), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]))
