"""Disposable backend owner and psutil recorder for the production WS workload."""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shlex
import platform
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import psutil

from .backend_process import BackendProcess
from .cli_shim import base_python_executable, install_cli_shim


async def main(root: Path, count: int, repeat: int):
    driver = psutil.Process()
    identity = {"driver": {"pid": driver.pid, "created": driver.create_time()},
                "platform": platform.platform(), "machine": platform.machine(),
                "python": sys.version, "selectedRuntime": "python", "actualRuntime": "python-native-pty"}
    (root / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    # Startup version discovery must see owned shims even after the real
    # login-shell PATH refresh. Production PATH policy/guards stay unchanged.
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for vendor in ("claude", "opencode"):
        install_cli_shim(bin_dir, vendor, [base_python_executable(), "-u", str(Path(__file__).with_name("baseline_peer.py"))])
    backend = BackendProcess(root, artifact_prefix="first-")
    backend.env["PATH"] = str(bin_dir) + os.pathsep + backend.env["PATH"]
    if os.name != "nt":
        for name in (".bash_profile", ".zprofile", ".profile"):
            (Path(backend.env["HOME"]) / name).write_text(
                f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\"\n", encoding="utf-8")
    if os.environ.get("ACR_DIAGNOSE") == "1":
        backend.env["NAVIDE_BASELINE_TRACE"] = "first"
    # All import-time application state belongs to the isolated child, not
    # this driver. The platform command seam contains no application stores.
    from agent_team_backend import osplat
    fixture_path = Path(__file__).resolve().parents[4] / "tests/fixtures/cli-regression/runtime-workload.json"
    workload = json.loads(fixture_path.read_text(encoding="utf-8"))
    identity["workloadSha256"] = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
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

    # Stable roots exist before the real watcher subscribes. Late-root
    # behavior belongs to the existing 30-second rescan regression, not to
    # this fixed observation-latency workload.
    (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
    (root / "share/opencode").mkdir(parents=True, exist_ok=True)
    fixture = json.loads((fixture_path.parent / "opencode.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(root / "share/opencode/opencode.db")) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(fixture["phases"]["initial"][0]["schema"])
    sampler = None
    async with backend:
        process = psutil.Process(backend.process.pid)
        identity["backend"] = {"pid": process.pid, "created": process.create_time(), "generation": "first"}
        (root / "first-identity.json").write_text(json.dumps(identity["backend"], indent=2), encoding="utf-8")
        (root / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        # Retain Process instances so every read/cleanup keeps creation-time
        # identity. Sample known children after reparenting, too.
        owned = {process.pid: process}

        async def sample():
            with (root / "resources.jsonl").open("w", encoding="utf-8") as stream:
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
                            counters.append({"pid": child.pid, "created": child.create_time(),
                                             "category": "controlled_child" if child.name().lower().startswith("python") and
                                             "baseline_peer.py" in " ".join(child.cmdline()) else "runtime",
                                             "cpuSeconds": cpu.user + cpu.system, "rssBytes": child.memory_info().rss,
                                             "name": child.name(), "executable": child.exe()})
                        except psutil.NoSuchProcess:
                            pass
                    stream.write(json.dumps({"wallMs": time.time_ns() / 1_000_000,
                                             "generation": "first", "backend": identity["backend"],
                                             "monotonicNs": time.monotonic_ns(), "processes": counters}) + "\n")
                    stream.flush()
                    await asyncio.sleep(workload["resourceIntervalMs"] / 1000)

        def peer(vendor: str, index: int, wrapped=True):
            pane = str(uuid.uuid5(uuid.NAMESPACE_URL, f"baseline:{vendor}:{index}"))
            workspace = root / f"workspace {index} café 世界"
            workspace.mkdir(exist_ok=True)
            script = Path(__file__).with_name("baseline_peer.py")
            args = [str(root), vendor, pane, str(workspace), workload["input"], str(fixture_path)]
            argv = [base_python_executable(), "-u", str(script), *args]
            line = subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(osplat.paths.quote_arg(word) for word in argv)
            return {"pane": pane, "vendor": vendor, "workspace": str(workspace), "command": argv,
                    "wrapped": wrapped, "commandLine": line, "argv": args}

        shells = [peer("terminal", -1, False), peer("terminal", -2)]
        peers = [peer(workload["vendors"][(index + repeat) % len(workload["vendors"])], index) for index in range(count)]
        endpoint = {"url": backend.url, "shells": shells, "peers": peers,
                    "home": str(root / "home"), "shell": "" if os.name == "nt" else "/bin/bash",
                    "actualRuntime": "python-native-pty"}
        pending = root / "endpoint.pending.json"
        pending.write_text(json.dumps(endpoint), encoding="utf-8")
        pending.replace(root / "endpoint.json")
        sampler = asyncio.create_task(sample())
        waiter = asyncio.create_task(stdin_shutdown())
        try:
            async with asyncio.timeout(180):
                await stopped.wait()
        finally:
            sampler.cancel()
            try:
                await sampler
            except asyncio.CancelledError:
                pass
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
    with sqlite3.connect(root / "share/opencode/opencode.db") as connection:
        stores = {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in ("session", "message", "part")}
    (root / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
    if restart_requested:
        # Restart the actual backend against its own isolated durable state.
        # No reader/checkpoint/store internals are invoked by the driver.
        restarted_owner = BackendProcess(root, artifact_prefix="restart-")
        restarted_owner.env["PATH"] = str(bin_dir) + os.pathsep + restarted_owner.env["PATH"]
        if os.environ.get("ACR_DIAGNOSE") == "1":
            restarted_owner.env["NAVIDE_BASELINE_TRACE"] = "restart"
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


def rescue(root: Path, expected_pid: int):
    identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
    assert identity["driver"]["pid"] == expected_pid
    owned = []
    for entry in (identity.get("backend"), identity.get("restartedBackend"), identity["driver"]):
        if not entry:
            continue
        try:
            process = psutil.Process(entry["pid"])
            assert process.create_time() == entry["created"], "refusing PID-reused rescue"
            for child in process.children(recursive=True):
                child.create_time()
                owned.append(child)
            owned.append(process)
        except psutil.NoSuchProcess:
            pass
    # Rescue is explicitly diagnostic, never a passing lifecycle verdict.
    for process in owned:
        if process.is_running():
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    _, survivors = psutil.wait_procs(owned, timeout=5)
    assert not [process.pid for process in survivors if process.is_running()], "rescue survivors"


if __name__ == "__main__":
    if sys.argv[1] == "--rescue":
        rescue(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        root = Path(sys.argv[1])
        try:
            asyncio.run(main(root, int(sys.argv[2]), int(sys.argv[3])))
        finally:
            # Backend exit/refusal must not lose the store-side diagnostic.
            path = root / "share/opencode/opencode.db"
            if path.exists():
                with sqlite3.connect(path) as connection:
                    stores = {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                              for table in ("session", "message", "part")}
                (root / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
