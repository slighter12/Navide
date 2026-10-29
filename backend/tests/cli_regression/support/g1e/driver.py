"""G1-E attempt driver: the disposable backend owner and psutil recorder of ONE attempt (adapted from ticket 06's `runtime_baseline.py`; E2 v10 section 2).

    python -m tests.cli_regression.support.g1e.driver ROOT COUNT REPEAT MODE          (MODE: python | rust-shell)
    python -m tests.cli_regression.support.g1e.driver --rescue ROOT DRIVER_PID

What differs from method 8 (E2 v10 C1-C12): the workload fixture is `runtime-workload-g1e.json`; measured peers are `g1e_peer.py` (raw mode, nonce marker, byte ledger) while the two ordinary
shells stay `baseline_peer.py`; every peer gets a fresh log file at a unique path in the attempt's isolated root; the backend is started with `NAVIDE_G1E_COUNTERS=1` (receipts-only counter
socket `<root>/gc.sock`); the sidecar binary is the explicit `ACR_SIDECAR_BIN` (Rust arm) so the pinned build is the one that runs. At shutdown the driver writes `g1e/ck-proof.json`
(`trial_proof.prove` over the receipts of the first runtime generation, accounting against the sessions the runner reports in `g1e/ts-final.json`). The driver never decides a verdict: it measures and records.
Stdin protocol: a line `restart` after the shutdown line makes it restart the backend (the durable-state phase); any other line ends the first backend.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import psutil

from tests.cli_regression.support import trial_proof
from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.support.runtime_baseline import ACTUAL_RUNTIME, classify_component, rescue

SUPPORT = Path(__file__).resolve().parents[1]
WORKLOAD_PATH = Path(__file__).resolve().parents[5] / "tests/fixtures/cli-regression/runtime-workload-g1e.json"
PEER_SCRIPTS = ("baseline_peer.py", "g1e_peer.py")


def is_version_probe(cmdline: list[str]) -> bool:
    """The backend's synthetic CLI `--version` discovery probe (same in both arms), not a PTY child."""
    return bool(cmdline) and cmdline[-1] == "--version" and any(word.endswith(PEER_SCRIPTS) for word in cmdline)


def is_controlled_child(name: str, cmdline: list[str]) -> bool:
    return name.lower().startswith("python") and any(script in " ".join(cmdline) for script in PEER_SCRIPTS)


def configure(backend: BackendProcess, mode: str, bin_dir: Path) -> None:
    """The one place a backend of this study is configured: the arm, the counters interface, the explicit sidecar build, the owned shims."""
    backend.env["NAVIDE_G1E_COUNTERS"] = "1"
    if mode == "rust-shell":
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        binary = os.environ.get("ACR_SIDECAR_BIN")
        if binary:
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    backend.env["PATH"] = str(bin_dir) + os.pathsep + backend.env["PATH"]


def first_generation(records: list[dict], backend_pid: int) -> list[dict]:
    """The receipts of the runtime generation whose `ready` the FIRST backend owns. A restarted backend may start its own runtime in the same root: its `ready` (other owner) and any
    record that NAMES another generation are dropped; the E3 per-session records (ck, handoff_received, cleaned, ...) carry no generation and belong to the generation they sit in."""
    ready = [r for r in records if r.get("event") == "ready" and r.get("owner_pid") == backend_pid]
    if not ready:
        return [r for r in records if r.get("event") != "ready"]
    generation = ready[0].get("runtime_generation")
    return [r for r in records if r.get("event") == "ready" and r is ready[0] or r.get("event") != "ready" and r.get("runtime_generation", generation) == generation]


def ck_proof(root: Path, mode: str, backend_pid: int) -> dict:
    """`trial_proof.prove` over the first generation's receipts; the runner reports what only it knows (created sessions, Python PTY spawns, control ledger) in `g1e/ts-final.json`."""
    if mode != "rust-shell":
        return {"ok": True, "failures": [], "items": None, "applicable": False}
    final_path = root / "g1e" / "ts-final.json"
    if not final_path.exists():
        return {"ok": False, "failures": ["the runner reported no final record (g1e/ts-final.json)"], "items": None, "applicable": True}
    final = json.loads(final_path.read_text(encoding="utf-8"))
    records = first_generation(trial_proof.load(root), backend_pid)
    ready = next((r for r in records if r.get("event") == "ready"), None)
    verdict = trial_proof.prove(records, sidecar_pid=ready.get("pid") if ready else None, python_pty_spawns=int(final["pythonPtySpawns"]),
                                driver_sessions=set(final["sessions"]), ledger=final.get("controlLedger"))
    return {**verdict, "applicable": True, "records": len(records)}


async def main(root: Path, count: int, repeat: int, mode: str = "python"):
    assert mode in ACTUAL_RUNTIME, mode
    driver = psutil.Process()
    identity = {"driver": {"pid": driver.pid, "created": driver.create_time()}, "platform": platform.platform(), "machine": platform.machine(),
                "python": sys.version, "selectedRuntime": mode, "actualRuntime": ACTUAL_RUNTIME[mode]}
    (root / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    (root / "g1e").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for vendor in ("claude", "opencode"):
        install_cli_shim(bin_dir, vendor, [base_python_executable(), "-u", str(SUPPORT / "g1e_peer.py")])
    backend = BackendProcess(root, artifact_prefix="first-")
    configure(backend, mode, bin_dir)
    if os.name != "nt":
        for name in (".bash_profile", ".zprofile", ".profile"):
            (Path(backend.env["HOME"]) / name).write_text(f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\"\n", encoding="utf-8")
    from agent_team_backend import osplat
    workload = json.loads(WORKLOAD_PATH.read_text(encoding="utf-8"))
    identity["workloadSha256"] = hashlib.sha256(WORKLOAD_PATH.read_bytes()).hexdigest()
    identity["intervalMs"] = workload["resourceIntervalMs"]
    identity["revision"] = workload["baselineRevision"]
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    restart_requested = False

    async def stdin_shutdown():
        nonlocal restart_requested
        restart_requested = (await asyncio.to_thread(sys.stdin.readline)).strip() == "restart"
        stopped.set()

    (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
    (root / "share/opencode").mkdir(parents=True, exist_ok=True)
    fixture = json.loads((WORKLOAD_PATH.parent / "opencode.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(root / "share/opencode/opencode.db")) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(fixture["phases"]["initial"][0]["schema"])
    sampler = None
    async with backend:
        process = psutil.Process(backend.process.pid)
        identity["backend"] = {"pid": process.pid, "created": process.create_time(), "generation": "first"}
        (root / "first-identity.json").write_text(json.dumps(identity["backend"], indent=2), encoding="utf-8")
        (root / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        owned = {process.pid: process}

        async def sample():
            with (root / "resources.jsonl").open("w", encoding="utf-8") as stream:
                interval = workload["resourceIntervalMs"] / 1000
                start = time.monotonic()
                tick = 0
                while True:
                    try:
                        if process.is_running():
                            for child in process.children(recursive=True):
                                child.create_time()
                                previous = owned.get(child.pid)
                                if previous is None or not previous.is_running():
                                    owned[child.pid] = child
                                backend.track_child(child.pid)
                    except psutil.NoSuchProcess:
                        pass
                    counters = []
                    for child in list(owned.values()):
                        try:
                            if not child.is_running():
                                continue
                            cpu = child.cpu_times()
                            executable = child.exe()
                            command = child.cmdline()
                            counters.append({"pid": child.pid, "created": child.create_time(), "ppid": child.ppid(),
                                             "component": classify_component(child.pid, process.pid, executable), "versionProbe": is_version_probe(command),
                                             "category": "controlled_child" if is_controlled_child(child.name(), command) else "runtime",
                                             "cpuSeconds": cpu.user + cpu.system, "rssBytes": child.memory_info().rss, "name": child.name(), "executable": executable})
                        except psutil.NoSuchProcess:
                            pass
                    stream.write(json.dumps({"wallMs": time.time_ns() / 1_000_000, "generation": "first", "backend": identity["backend"],
                                             "monotonicNs": time.monotonic_ns(), "processes": counters}) + "\n")
                    stream.flush()
                    # Fixed-rate schedule: a pass that overruns takes the next tick at once, missed ticks are dropped.
                    tick = max(tick + 1, int((time.monotonic() - start) / interval))
                    await asyncio.sleep(max(0, start + tick * interval - time.monotonic()))

        def peer(vendor: str, index: int, wrapped=True, measured=False):
            pane = str(uuid.uuid5(uuid.NAMESPACE_URL, f"g1e:{mode}:{count}:{repeat}:{vendor}:{index}"))
            workspace = root / f"workspace {index} café 世界"
            workspace.mkdir(exist_ok=True)
            script = SUPPORT / ("g1e_peer.py" if measured else "baseline_peer.py")
            args = [str(root), vendor, pane, str(workspace), workload["input"], str(WORKLOAD_PATH)]
            argv = [base_python_executable(), "-u", str(script), *args]
            line = subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(osplat.paths.quote_arg(word) for word in argv)
            return {"pane": pane, "vendor": vendor, "workspace": str(workspace), "command": argv, "wrapped": wrapped, "commandLine": line, "argv": args,
                    "index": index, "logPath": str(root / "logs" / f"peer-{index}.log") if measured else None}

        shells = [peer("terminal", -1, False), peer("terminal", -2)]
        peers = [peer(workload["vendors"][(index + repeat) % len(workload["vendors"])], index, measured=True) for index in range(count)]
        endpoint = {"url": backend.url, "shells": shells, "peers": peers, "home": str(root / "home"), "shell": "" if os.name == "nt" else "/bin/bash",
                    "actualRuntime": ACTUAL_RUNTIME[mode], "countersSocket": str(root / "gc.sock")}
        pending = root / "endpoint.pending.json"
        pending.write_text(json.dumps(endpoint), encoding="utf-8")
        pending.replace(root / "endpoint.json")
        sampler = asyncio.create_task(sample())
        waiter = asyncio.create_task(stdin_shutdown())
        try:
            async with asyncio.timeout(240):
                await stopped.wait()
        finally:
            for task in (sampler, waiter):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    (root / "g1e" / "ck-proof.json").write_text(json.dumps(ck_proof(root, mode, identity["backend"]["pid"])), encoding="utf-8")
    with sqlite3.connect(root / "share/opencode/opencode.db") as connection:
        stores = {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in ("session", "message", "part")}
    (root / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
    if restart_requested:
        restarted_owner = BackendProcess(root, artifact_prefix="restart-")
        configure(restarted_owner, mode, bin_dir)
        async with restarted_owner as restarted:
            process = psutil.Process(restarted.process.pid)
            identity["restartedBackend"] = {"pid": process.pid, "created": process.create_time(), "generation": "restart"}
            (root / "restart-identity.json").write_text(json.dumps(identity["restartedBackend"], indent=2), encoding="utf-8")
            (root / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
            pending = root / "restarted.pending.json"
            pending.write_text(json.dumps({"url": restarted.url}), encoding="utf-8")
            pending.replace(root / "restarted.json")
            async with asyncio.timeout(30):
                await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    if sys.argv[1] == "--rescue":
        rescue(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        root = Path(sys.argv[1])
        try:
            asyncio.run(main(root, int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "python"))
        finally:
            path = root / "share/opencode/opencode.db"
            if path.exists():
                with sqlite3.connect(path) as connection:
                    stores = {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in ("session", "message", "part")}
                (root / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
