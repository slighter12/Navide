"""Disposable owner for the production-TS-client runtime-recovery cases (ticket 05).

Owns a real isolated backend (and, for the "runtime" scenario, a second one as
"another runtime") plus an unrelated sleeper. The TS test drives the production
WebSocket client; this process only performs OS-level fault injection on OWN
processes, each verified by pid + creation time first, and answers identity
queries. Commands arrive as `<seq> <command> [argument]` lines on stdin and are
answered by an atomically written reply-<seq>.json; `stop` ends the scenario.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

import psutil

from agent_team_backend import pty_registry
from agent_team_backend.applog import in_data_dir

from .backend_process import BackendProcess
from .cli_shim import base_python_executable, install_cli_shim

# NAVIDE_T05_SIDECAR lets a differential run name another explicitly built sidecar image.
BINARY = Path(os.environ.get("NAVIDE_T05_SIDECAR") or
              Path(__file__).resolve().parents[4] / "native/navide-cli-runtime/target/release/navide-cli-runtime")
PEER = Path(__file__).with_name("native_shell_peer.py")


def _reply(root: Path, seq: str, value) -> None:
    pending = root / f"reply-{seq}.tmp"
    pending.write_text(json.dumps(value))
    pending.replace(root / f"reply-{seq}.json")


def _receipts(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _verified(pid: int, created: float) -> psutil.Process:
    process = psutil.Process(pid)
    if abs(process.create_time() - created) > 1e-3:
        raise RuntimeError("refusing to act on a process whose identity does not match")
    return process


def _alive(pid: int, created: float) -> bool:
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - created) <= 1e-3 and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class Watched:
    """Process-identity monitor for one backend, as the other drivers do."""

    def __init__(self, backend: BackendProcess, log: Path):
        self.backend, self.log = backend, log
        self.identity = psutil.Process(backend.process.pid)
        self.owned = {self.identity.pid: self.identity}
        self.stopped = asyncio.Event()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        with self.log.open("w") as stream:
            while not self.stopped.is_set():
                try:
                    children = self.identity.children(recursive=True)
                except psutil.NoSuchProcess:  # the backend itself was killed on purpose
                    children = []
                for process in children:
                    process.create_time()
                    self.owned.setdefault(process.pid, process)
                    self.backend.track_child(process.pid)
                records = []
                for process in self.owned.values():
                    try:
                        if process.is_running():
                            records.append({"pid": process.pid, "created": process.create_time(),
                                            "parent": process.ppid()})
                    except psutil.NoSuchProcess:
                        pass
                stream.write(json.dumps(records) + "\n")
                stream.flush()
                await asyncio.sleep(0.05)

    async def stop(self):
        self.stopped.set()
        await self.task


def _wire(backend: BackendProcess, watched: Watched, extra: dict | None = None) -> dict:
    peer = backend.root / "native_shell_peer.py"
    shutil.copyfile(PEER, peer)
    command = [base_python_executable(), "-u", str(peer)]
    return {"url": backend.url, "command": command, "workspace": str(backend.root),
            "backend": {"pid": watched.identity.pid, "created": watched.identity.create_time()}, **(extra or {})}


def _publish(root: Path, name: str, value) -> None:
    pending = root / f"{name}.pending.json"
    pending.write_text(json.dumps(value))
    pending.replace(root / f"{name}.json")


async def _commands(root: Path, backend: BackendProcess, handlers: dict):
    while True:
        line = (await asyncio.to_thread(sys.stdin.readline)).strip()
        seq, _, rest = line.partition(" ")
        name, _, argument = rest.partition(" ")
        if not line or name == "stop":
            return
        try:
            _reply(root, seq, await handlers[name](argument))
        except Exception as error:  # noqa: BLE001 - reported to the test, never swallowed
            _reply(root, seq, {"error": f"{type(error).__name__}: {error}"})


def _install_common(root: Path, backend: BackendProcess) -> dict:
    def sidecar() -> psutil.Process:
        ready = [r for r in _receipts(root) if r.get("event") == "ready"][-1]
        process = _verified(ready["pid"], ready["created"])
        if process.ppid() != backend.process.pid:
            raise RuntimeError("the recorded sidecar is not a child of this backend")
        return process

    async def signal_sidecar(sig):
        process = sidecar()
        record = {"pid": process.pid, "created": process.create_time()}
        process.send_signal(sig)
        return record

    async def identify(argument):
        return {pid: psutil.Process(int(pid)).create_time() for pid in argument.split(",") if pid}

    async def alive(argument):
        flags = {}
        for item in argument.split(","):
            pid, _, created = item.partition(":")
            flags[pid] = _alive(int(pid), float(created))
        return flags

    async def descendants(_argument):
        entries = in_data_dir(root / "data", pty_registry._load)()
        return {pid: len(info.get("descendants") or {}) for pid, info in entries.items()
                if info.get("owner") == backend.process.pid}

    async def create_send_release(argument):
        (root / f"create-send-release-{int(argument)}").write_text("")
        return {"released": int(argument)}

    async def refuse_retirement(argument):
        marker = root / "refuse-retirement"
        if not argument:
            marker.unlink(missing_ok=True)
            return {"refusing": None}
        pane_id, _, terminal_id = argument.partition(":")
        _publish(root, "refuse-retirement-staged", {"pane_id": pane_id, "terminal_session_id": terminal_id})
        (root / "refuse-retirement-staged.json").replace(marker)
        return {"refusing": pane_id}

    async def replace_runtime(kind):
        target = root / "runtime-under-test"
        target.unlink(missing_ok=True)
        if kind == "not-executable":
            shutil.copyfile(BINARY, target)
            target.chmod(0o600)
        elif kind == "protocol-mismatch":
            target.write_text(f'#!/bin/sh\nexec {BINARY} "$@" --ready-protocol 999\n')
            target.chmod(0o755)
        elif kind == "exits-before-ready":
            target.write_text("#!/bin/sh\nexit 3\n")
            target.chmod(0o755)
        else:
            raise RuntimeError(f"unknown replacement {kind}")
        return {"replaced": kind}

    journals = root / "data" / "runtime-ownership"

    async def tamper_journal(_argument):
        # Substituted state: a torn trailing append, as after a crash in the middle of a write.
        for path in sorted(journals.glob("*.jsonl")):
            with path.open("ab") as stream:
                stream.write(b'{"journal":1,"pid":')
        return {"journals": sorted(path.name for path in journals.glob("*.jsonl"))}

    async def repair_journal(_argument):
        # Remove exactly the torn tail (the bytes after the last newline).
        for path in sorted(journals.glob("*.jsonl")):
            data = path.read_bytes()
            path.write_bytes(data[: data.rfind(b"\n") + 1])
        return {"journals": sorted(path.name for path in journals.glob("*.jsonl"))}

    held_readers: list[int] = []

    async def journal_hold(_argument):
        # Substituted state: the sidecar's append target becomes a FIFO that holds its append
        # (a deterministic stall of the journal write while the root waits at the ownership gate).
        ready = [r for r in _receipts(root) if r.get("event") == "ready"][-1]
        path = journals / f"{ready['runtime_generation']}.jsonl"
        path.unlink()
        os.mkfifo(path, 0o600)
        return {"journal": path.name}

    async def journal_release(_argument):
        ready = [r for r in _receipts(root) if r.get("event") == "ready"][-1]
        path = journals / f"{ready['runtime_generation']}.jsonl"
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        os.set_blocking(fd, True)
        held_readers.append(fd)
        asyncio.get_running_loop().run_in_executor(None, lambda: os.read(fd, 1 << 20))
        return {"released": True}

    async def journal_files(_argument):
        return {"journals": sorted(path.name for path in journals.glob("*.jsonl")) if journals.is_dir() else []}

    return {
        "journal-hold": journal_hold, "journal-release": journal_release, "tamper-journal": tamper_journal, "repair-journal": repair_journal, "journal-files": journal_files,
        "sidecar-stop": lambda _a: signal_sidecar(signal.SIGSTOP),
        "sidecar-cont": lambda _a: signal_sidecar(signal.SIGCONT),
        "sidecar-kill": lambda _a: signal_sidecar(signal.SIGKILL),
        "identify": identify, "alive": alive, "descendants": descendants,
        "replace-runtime": replace_runtime,
        "create-send-release": create_send_release,
        "refuse-retirement": refuse_retirement,
    }


async def claude_scenario(root: Path):
    """One backend with a fixture `claude` (never a real CLI) and its vendor-data files:
    the TS test drives the production client; this owner injects faults on the sidecar,
    appends the next stage of vendor data and reports the durable checkpoint at the end."""
    runtime_bin = root / "runtime-under-test"
    shutil.copyfile(BINARY, runtime_bin)
    runtime_bin.chmod(0o755)
    workspace = root / "workspace reference café 世界"
    workspace.mkdir()
    session_id = "22222222-2222-4222-8222-222222222222"
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(runtime_bin)
    script = Path(__file__).with_name("claude_runtime_peer.py")
    executable = install_cli_shim(root, "claude", [base_python_executable(), "-u", str(script), str(root)])
    (root / "home").mkdir(exist_ok=True)
    (root / "home/.bash_profile").write_text(
        f"export PATH={executable.parent}:$PATH\nexport REFERENCE_SHELL_RC=login\n")
    (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
    from agent_team_backend.log_readers.base import encode_claude_cwd
    path = root / "home/.claude/projects" / encode_claude_cwd(str(workspace)) / f"{session_id}.jsonl"
    async with backend:
        watched = Watched(backend, root / "process-identities.jsonl")
        common = _install_common(root, backend)

        async def append_stage(argument):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as stream:  # external vendor data, never application state
                stream.write((root / f"stage-{argument}.bin").read_bytes())
            return {"size": path.stat().st_size}

        async def checkpoint(_argument):
            """Read-only view of the durable ingestion checkpoints of the session file."""
            def read():
                import sqlite3
                connection = sqlite3.connect(f"file:{root / 'data' / 'navide.db'}?mode=ro", uri=True, timeout=2)
                try:
                    rows = connection.execute(
                        "SELECT scope, data FROM ingestion_checkpoints WHERE path LIKE ? ORDER BY scope",
                        (f"%{path.name}",)).fetchall()
                finally:
                    connection.close()
                return {scope: json.loads(data) for scope, data in rows}
            return await asyncio.to_thread(read)

        try:
            _publish(root, "wire", {**_wire(backend, watched), "shell": "/bin/bash", "workspace": str(workspace),
                                    "home": str(root / "home"), "sessionId": session_id})
            await _commands(root, backend, {**common, "append-stage": append_stage, "checkpoint": checkpoint})
        finally:
            await watched.stop()
    from agent_team_backend.db import Database
    from agent_team_backend.tokens_store import TokensStore
    db = Database(root / "data/navide.db")
    try:
        store = TokensStore(db=db)
        (root / "durable.json").write_text(json.dumps({
            "usage": store.get_ingestion_checkpoint(str(path), None),
            "activity": store.get_ingestion_checkpoint(str(path), "@activity"),
            "totals": store.snapshot(str(workspace))["global"]["all_time"],
            "file_size": path.stat().st_size if path.exists() else None,
        }, indent=2))
    finally:
        db.close()


async def runtime_scenario(root: Path, gated: bool = False, refusal: bool = False):
    runtime_bin = root / "runtime-under-test"
    shutil.copyfile(BINARY, runtime_bin)
    runtime_bin.chmod(0o755)
    unrelated = psutil.Popen([base_python_executable(), "-c", "import time; time.sleep(600)"],
                             stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                             stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    other_root = root / "other"
    other_root.mkdir()
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(runtime_bin)
    if gated:
        backend.env["NAVIDE_CREATE_SEND_GATE"] = "1"
    if refusal:
        backend.env["NAVIDE_RETIREMENT_REFUSAL_GATE"] = "1"
    other = BackendProcess(other_root, artifact_prefix="other-")
    other.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    try:
        async with backend, other:
            watched = Watched(backend, root / "process-identities.jsonl")
            other_watched = Watched(other, other_root / "process-identities.jsonl")
            try:
                _publish(root, "wire", _wire(backend, watched, {
                    "other": _wire(other, other_watched),
                    "unrelated": {"pid": unrelated.pid, "created": unrelated.create_time()}}))
                await _commands(root, backend, _install_common(root, backend))
            finally:
                await watched.stop()
                await other_watched.stop()
        _publish(root, "closed", {"unrelatedAlive": _alive(unrelated.pid, unrelated.create_time())})
    finally:
        if unrelated.is_running():
            unrelated.kill()
            unrelated.wait(timeout=5)


async def backend_death_scenario(root: Path):
    unrelated = psutil.Popen([base_python_executable(), "-c", "import time; time.sleep(600)"],
                             stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                             stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    killed = asyncio.Event()
    crashed_children: list[tuple[int, float]] = []
    runtime_bin = root / "runtime-under-test"
    shutil.copyfile(BINARY, runtime_bin)
    runtime_bin.chmod(0o755)
    backend = BackendProcess(root, artifact_prefix="stage-1-")
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(runtime_bin)
    try:
        await backend.__aenter__()
        watched = Watched(backend, root / "stage-1-process-identities.jsonl")
        common = _install_common(root, backend)

        async def registry_drop(argument):
            # Substituted state: remove the registry row, modelling a backend that died
            # before it could register the root it never got to read `owned` for.
            def drop():
                entries = pty_registry._load(strict=True)
                removed = entries.pop(argument, None) is not None
                pty_registry._save(entries)
                return removed
            return {"removed": await asyncio.to_thread(in_data_dir(root / "data", drop))}

        async def crash_pair(argument):
            """Freeze the backend, SIGKILL the sidecar, SIGKILL the backend: nobody is left
            to clean up. `argument` = `<pid>:<created>` of the SIGHUP-immune child."""
            child_pid, _, child_created = argument.partition(":")
            backend_process = _verified(watched.identity.pid, watched.identity.create_time())
            ready = [r for r in _receipts(root) if r.get("event") == "ready"][-1]
            sidecar = _verified(ready["pid"], ready["created"])
            backend_process.send_signal(signal.SIGSTOP)
            sidecar.send_signal(signal.SIGKILL)
            backend_process.send_signal(signal.SIGKILL)
            await asyncio.to_thread(backend_process.wait, 10)
            state = {"child": int(child_pid), "childCreated": float(child_created),
                     "childAliveAfterCrash": _alive(int(child_pid), float(child_created)),
                     "sidecarGone": not _alive(sidecar.pid, ready["created"])}
            crashed_children.append((int(child_pid), float(child_created)))
            killed.set()
            return state

        async def backend_kill(_argument):
            process = _verified(watched.identity.pid, watched.identity.create_time())
            ready = [r for r in _receipts(root) if r.get("event") == "ready"][-1]
            sidecar = psutil.Process(ready["pid"])
            owned = [p for p in watched.owned.values() if p.pid not in (process.pid, sidecar.pid)]
            process.send_signal(signal.SIGKILL)
            await asyncio.to_thread(process.wait, 10)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                state = {"sidecarGone": not _alive(sidecar.pid, ready["created"]),
                         "ownedGone": all(not _alive(p.pid, p.create_time()) for p in owned)}
                if all(state.values()):
                    break
                await asyncio.sleep(0.05)
            registry_left = len(in_data_dir(root / "data", pty_registry._load)())
            # Only now consult the harness' own rescue: anything it still finds is a failure.
            registry_reaped = await backend._reap_registry_children()
            rescued = await asyncio.to_thread(backend._reap_surviving_children)
            report = {**state, "registryEntriesLeft": registry_left, "registryReapedAfterDeath": registry_reaped,
                      "harnessRescued": len(rescued), "ownedCount": len(owned),
                      "unrelatedAlive": _alive(unrelated.pid, unrelated.create_time())}
            (root / "backend-death.json").write_text(json.dumps(report))
            killed.set()
            return report

        _publish(root, "wire", _wire(backend, watched, {
            "unrelated": {"pid": unrelated.pid, "created": unrelated.create_time()}}))
        await _commands(root, backend, {**common, "backend-kill": backend_kill,
                                        "registry-drop": registry_drop, "crash-pair": crash_pair})
        await watched.stop()
        backend._log.close()
        if not killed.is_set():
            raise AssertionError("the backend-death scenario ended without the backend being killed")
        second = BackendProcess(root, artifact_prefix="stage-2-")
        second.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        second.env["NAVIDE_CLI_RUNTIME_BIN"] = str(runtime_bin)
        async with second:
            watched2 = Watched(second, root / "stage-2-process-identities.jsonl")
            try:
                _publish(root, "wire-2", _wire(second, watched2))
                await _commands(root, second, _install_common(root, second))
            finally:
                await watched2.stop()
    finally:
        rescued = []
        for pid, created in crashed_children:  # failure path only: a pass leaves nothing here
            if _alive(pid, created):
                _verified(pid, created).kill()
                rescued.append(pid)
        if crashed_children:
            _publish(root, "orphan-rescue", {"rescued": rescued})
        if unrelated.is_running():
            unrelated.kill()
            unrelated.wait(timeout=5)


if __name__ == "__main__":
    scenario_root = Path(sys.argv[1])
    asyncio.run({"runtime": runtime_scenario, "runtime-gated": lambda root: runtime_scenario(root, gated=True),
                 "runtime-refusal": lambda root: runtime_scenario(root, refusal=True),
                 "backend-death": backend_death_scenario, "claude": claude_scenario}[sys.argv[2]](scenario_root))
