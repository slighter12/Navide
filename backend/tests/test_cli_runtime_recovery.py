"""Supplemental cancellation / runtime-loss proofs with the REAL native sidecar.

The production WebSocket proofs live in tests/cli/runtimeRecovery.test.ts. Here a
real RustTerminalService drives the actual release sidecar and macOS PTYs; faults
are real OS signals on the sidecar (SIGSTOP/SIGKILL, only after its pid +
create_time were verified) and fixture executables. Substituted pieces are named
at their tests.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import threading
import time

import psutil
import pytest

from agent_team_backend import pty_registry
from agent_team_backend.applog import in_data_dir
from agent_team_backend.cli_runtime import CreateCancelled, RustTerminalService
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.isolation import isolated_environment

BINARY = Path(__file__).resolve().parents[2] / "native/navide-cli-runtime/target/release/navide-cli-runtime"
PEER = Path(__file__).parent / "cli_regression/support/native_shell_peer.py"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not BINARY.is_file(),
                       reason="requires the explicitly built macOS native runtime"),
]


class Rig:
    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.frames: list[dict] = []
        self.output: dict[str, bytes] = {}
        self.watch: list[int] = []
        self.alive_at_exit: dict[str, list[bool]] = {}
        self.services: list[RustTerminalService] = []
        self.pids: set[int] = set()
        self.runtime_bin = root / "runtime-under-test"
        shutil.copyfile(BINARY, self.runtime_bin)
        self.runtime_bin.chmod(0o755)
        monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(self.runtime_bin))
        monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(root))
        self.peer = root / "native_shell_peer.py"
        shutil.copyfile(PEER, self.peer)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.env = isolated_environment(root)

    async def emit(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            sid_len = frame[5]
            sid = bytes(frame[6:6 + sid_len]).decode()
            pane_len = frame[6 + sid_len]
            self.output[sid] = self.output.get(sid, b"") + bytes(frame[7 + sid_len + pane_len:])
            return
        self.frames.append(frame)
        if frame.get("type") == "terminal.exit":
            self.alive_at_exit[frame["payload"]["terminal_session_id"]] = [alive(pid) for pid in self.watch]

    def service(self) -> RustTerminalService:
        service = RustTerminalService(self.emit)
        self.services.append(service)
        return service

    def exits(self, sid: str | None = None) -> list[dict]:
        return [f["payload"] for f in self.frames if f.get("type") == "terminal.exit"
                and (sid is None or f["payload"]["terminal_session_id"] == sid)]

    def receipts(self) -> list[dict]:
        path = self.root / "runtime-receipts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def shell(self, service, pane: str, **extra):
        session = await service.create_async(
            pane_id=pane, agent_key=extra.pop("agent_key", "terminal"),
            command=extra.pop("command", [base_python_executable(), "-u", str(self.peer)]),
            cwd=str(self.workspace), cols=100, rows=30, env=self.env, **extra)
        await service.activate_async(session.id)
        self.pids.add(session.proc.pid)
        await self.until(lambda: b"NATIVE_READY " in self.output.get(session.id, b""), "peer ready")
        return session

    async def tree(self, service, session) -> list[int]:
        await service.write_async(session.id, "spawn-tree-hup\r")
        await self.until(lambda: b"TREE {" in self.output.get(session.id, b""), "tree spawned")
        pids = json.loads(self.output[session.id].split(b"TREE ")[1].splitlines()[0])["pids"]
        self.pids.update(pids)
        # The registry only knows descendants from the sidecar's periodic snapshot.
        await self.until(lambda: len(session.descendants) >= 2, "descendant snapshot", timeout=25)
        return pids

    @staticmethod
    async def until(predicate, message: str, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            await asyncio.sleep(0.01)

    @staticmethod
    def signal_sidecar(service, sig) -> None:
        identity = service._runtime_identity
        process = psutil.Process(identity["pid"])
        assert process.pid == service._runtime.pid and process.create_time() == identity["created"]
        os.kill(identity["pid"], sig)

    async def recovered(self, service) -> None:
        async with asyncio.timeout(40):
            while service._invalidation is None:
                await asyncio.sleep(0.01)
            await asyncio.shield(service._invalidation)

    async def close(self):
        for service in self.services:
            runtime = service._runtime
            if runtime is not None and runtime.returncode is None:
                try:
                    self.signal_sidecar(service, signal.SIGKILL)
                except (psutil.NoSuchProcess, AssertionError):
                    pass
            for generation in {r["runtime_generation"] for r in self.receipts() if "runtime_generation" in r}:
                pty_registry.reap_runtime(generation, grace=0.0, settle=1.0)
        for pid in self.pids:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    try:
        yield rig
    finally:
        await rig.close()


async def test_cancel_while_waiting_for_the_bridge_wire_prevents_any_native_spawn(rig):
    service = rig.service()
    await service._ensure_started()
    await service._send_lock.acquire()  # the create waits at the wire, not yet dispatched
    task = asyncio.create_task(service.create_async(
        pane_id="never", agent_key="terminal", command=[base_python_executable(), "-u", str(rig.peer)],
        cwd=str(rig.workspace), env=rig.env))
    await asyncio.sleep(0.2)
    task.cancel()
    service._send_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    ping = await service._request("ping")
    assert ping["bookkeeping"]["live_sessions"] == 0 and ping["bookkeeping"]["session_workers"] == 0
    assert not service._launches and not service._owned and service._lost is None
    assert not [r for r in rig.receipts() if r["event"] == "owned"]


async def test_sidecar_death_invalidates_every_session_and_cleans_only_its_own_tree(rig):
    service = rig.service()
    unrelated = psutil.Popen([base_python_executable(), "-c", "import time; time.sleep(120)"])
    rig.pids.add(unrelated.pid)
    sessions = [await rig.shell(service, f"pane-{index}") for index in range(3)]
    pids = await rig.tree(service, sessions[0])
    rig.watch = [sessions[0].proc.pid, *pids]
    lost_generation = service._runtime_generation
    lost_sidecar = service._runtime_identity["pid"]

    rig.signal_sidecar(service, signal.SIGSTOP)  # hold a real request outstanding
    # Terminal input is a local write on the handed-off PTY (E1 8); the held request is a control message.
    pending = asyncio.create_task(service._request("resize", {"cols": 110, "rows": 40, "redraw": False}, sessions[1]))
    await rig.until(lambda: any(not f.done() for f in service._pending.values()), "request outstanding")
    rig.signal_sidecar(service, signal.SIGKILL)

    with pytest.raises(RuntimeError, match="RUNTIME_LOST"):
        await asyncio.wait_for(pending, 15)
    await rig.recovered(service)

    assert sorted(e["terminal_session_id"] for e in rig.exits()) == sorted(s.id for s in sessions)
    assert {e["reason"] for e in rig.exits()} == {"runtime_lost"}
    assert all(e["exit_code"] is None and e["signal"] is None for e in rig.exits())
    # Presented only after cleanup: every watched process was already gone.
    assert all(not any(flags) for flags in rig.alive_at_exit.values())
    assert not [pid for pid in (sessions[0].proc.pid, *pids) if alive(pid)]
    assert alive(unrelated.pid)
    assert not service._sessions and service._lost is None
    assert not [e for e in pty_registry._load().values() if e.get("runtime_generation") == lost_generation]
    with pytest.raises(Exception):
        await service.write_async(sessions[1].id, "stale\r")
    assert not any(e["terminal_session_id"] == sessions[1].id for e in rig.exits()[3:])

    fresh = await rig.shell(service, "pane-fresh")  # fresh runtime, only after cleanup
    assert service._runtime_generation != lost_generation
    assert service._runtime_identity["pid"] != lost_sidecar
    records = rig.receipts()
    ready = [i for i, r in enumerate(records) if r["event"] == "ready"]
    invalidated = [i for i, r in enumerate(records) if r["event"] == "runtime_invalidated"]
    owned_fresh = next(i for i, r in enumerate(records) if r["event"] == "owned" and r["session_id"] == fresh.id)
    assert len(ready) == 2 and len(invalidated) == 1 and ready[0] < invalidated[0] < ready[1] < owned_fresh
    assert records[invalidated[0]]["lost_generation"] == lost_generation
    assert records[invalidated[0]]["unresolved"] == 0 and records[invalidated[0]]["sessions"] == 3


async def test_loss_cleanup_never_touches_another_runtime(rig):
    first, second = rig.service(), rig.service()
    survivor = await rig.shell(second, "other-runtime-pane")
    doomed = await rig.shell(first, "lost-runtime-pane")
    survivor_tree = await rig.tree(second, survivor)

    rig.signal_sidecar(first, signal.SIGKILL)
    await rig.recovered(first)

    assert not alive(doomed.proc.pid)
    assert alive(survivor.proc.pid) and all(alive(pid) for pid in survivor_tree)
    await second.write_async(survivor.id, "still-here\r")
    await rig.until(lambda: b"still-here" in rig.output[survivor.id], "other runtime still serves its session")
    assert rig.exits(survivor.id) == [] and second._lost is None


@pytest.mark.parametrize("failure", ["not-executable", "protocol-mismatch", "exits-before-ready"])
async def test_replacement_runtime_failure_after_loss_is_explicit(rig, failure):
    service = rig.service()
    lost = await rig.shell(service, "pane-lost")
    rig.signal_sidecar(service, signal.SIGKILL)
    await rig.recovered(service)
    rig.runtime_bin.unlink()
    if failure == "not-executable":
        shutil.copyfile(BINARY, rig.runtime_bin)
        rig.runtime_bin.chmod(0o600)
        expected = "START_FAILED at exec"
    elif failure == "protocol-mismatch":
        harness = BINARY.parents[2] / "target-harness/release/navide-cli-runtime"  # the forced-wire hook is harness-only
        rig.runtime_bin.write_text(f'#!/bin/sh\nexec {harness} "$@" --ready-wire 999\n')
        rig.runtime_bin.chmod(0o755)
        expected = "PROTOCOL_MISMATCH at handshake"
    else:
        rig.runtime_bin.write_text("#!/bin/sh\nexit 3\n")
        rig.runtime_bin.chmod(0o755)
        expected = "START_FAILED before ready"
    with pytest.raises(RuntimeError, match=expected):
        await rig.shell(service, "pane-replacement")
    owned = [r["session_id"] for r in rig.receipts() if r["event"] == "owned"]
    assert owned == [lost.id]  # no replacement child, and no Python PTY fallback
    assert len(rig.exits(lost.id)) == 1
    with pytest.raises(RuntimeError):  # the failure stays explicit afterwards
        await rig.shell(service, "pane-again")


async def test_incomplete_cleanup_keeps_fresh_runtime_and_observation_refused(rig, monkeypatch):
    # Substituted: only the cleanup verdict (a survivor the registry cannot clear).
    service = rig.service()
    await rig.shell(service, "pane-lost")
    monkeypatch.setattr(pty_registry, "reap_runtime", lambda generation, *_args, **_: {
        "signalled": [], "unresolved": [4242], "unverifiable": False})
    rig.signal_sidecar(service, signal.SIGKILL)
    await rig.recovered(service)

    assert service._lost and "CLEANUP_FAILED" in service._lost
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await rig.shell(service, "pane-blocked")
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await service.observe("codex", "rust-codex-jsonl-v1", {"path": str(rig.root / "x"), "mode": "usage"}, match=lambda sessions: None)
    assert len([r for r in rig.receipts() if r["event"] == "ready"]) == 1
    invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["unresolved"] == 1


@pytest.mark.parametrize("send_cancel", [True, False])
async def test_abandoned_create_is_cleaned_never_resurrected_and_blocks_replacement(rig, monkeypatch, send_cancel):
    service = rig.service()
    await service._ensure_started()
    if not send_cancel:  # substituted: exercise only the response-driven cleanup
        real_request = service._request

        async def no_cancel(op, *args, **kwargs):
            if op == "cancel":
                raise RuntimeError("cancel withheld by test")
            return await real_request(op, *args, **kwargs)
        monkeypatch.setattr(service, "_request", no_cancel)
    command = [base_python_executable(), "-u", str(rig.peer)]
    rig.signal_sidecar(service, signal.SIGSTOP)
    first = asyncio.create_task(service.create_async(
        pane_id="pane-race", agent_key="terminal", command=command, cwd=str(rig.workspace), env=rig.env))
    await rig.until(lambda: service._launches, "create dispatched to the stopped sidecar")
    first_sid = next(iter(service._launches))
    first.cancel()  # e.g. the WebSocket handler task cancelled by a disconnect
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(rig.shell(service, "pane-race"))
    await asyncio.sleep(0.5)
    assert not second.done(), "replacement overlapped an unresolved launch"
    assert set(service._launches) == {first_sid}, "replacement was dispatched before the old launch resolved"
    assert service._lost is None

    rig.signal_sidecar(service, signal.SIGCONT)
    replacement = await asyncio.wait_for(second, 30)

    records = rig.receipts()
    owned = [r["session_id"] for r in records if r["event"] == "owned"]
    assert len(owned) == len(set(owned)) and owned[-1] == replacement.id  # one create per session id
    if first_sid in owned:
        cleaned = next(i for i, r in enumerate(records) if r["event"] == "cleaned" and r["session_id"] == first_sid)
        later = next(i for i, r in enumerate(records) if r["event"] == "owned" and r["session_id"] == replacement.id)
        assert cleaned < later and records[cleaned]["cleanup_complete"] and records[cleaned]["root_reaped"]
        root = next(r["root"]["pid"] for r in records if r["event"] == "owned" and r["session_id"] == first_sid)
        assert not alive(root)
    assert rig.exits(first_sid) == [] and first_sid not in rig.output  # never presented, no stale output
    assert first_sid not in service._sessions and service._lost is None and alive(replacement.proc.pid)
    assert not service._launches and not service._unresolved


async def test_abandoned_create_blocks_another_pane_resuming_the_same_session(rig):
    from tests.cli_regression.support.cli_shim import install_cli_shim
    shims = rig.root / "bin"
    shims.mkdir()
    install_cli_shim(shims, "claude", [base_python_executable(), "-u", str(rig.peer)])  # fixture, never a real CLI
    env = {**rig.env, "PATH": f"{shims}{os.pathsep}{rig.env['PATH']}"}
    assert shutil.which("claude", path=env["PATH"]) == str(shims / "claude")
    resume = ["/bin/sh", "-c", "claude --resume 11111111-1111-4111-8111-111111111111"]
    service = rig.service()
    await service._ensure_started()
    rig.signal_sidecar(service, signal.SIGSTOP)
    first = asyncio.create_task(service.create_async(
        pane_id="pane-one", agent_key="claude", command=resume, cwd=str(rig.workspace), env=env))
    await rig.until(lambda: service._launches, "create dispatched")
    first_sid = next(iter(service._launches))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    rig.env = env
    other = asyncio.create_task(rig.shell(service, "pane-two", agent_key="claude", command=resume))
    await asyncio.sleep(0.5)
    assert not other.done(), "a second pane resumed the same session while the first launch was unresolved"
    assert set(service._launches) == {first_sid}, "the second launch was dispatched before the first resolved"
    rig.signal_sidecar(service, signal.SIGCONT)
    await asyncio.wait_for(other, 30)
    owned = [r["session_id"] for r in rig.receipts() if r["event"] == "owned"]
    assert len(owned) == len(set(owned))
    assert service._lost is None and not service._unresolved


async def test_create_cancelled_mid_send_never_leaves_a_torn_control_frame(rig):
    # The create frame carries the child environment; padded here so it exceeds the AF_UNIX
    # buffers (8 KiB each) whatever the caller's own environment, the send to the stopped
    # sidecar suspends part-way through it, and the cancel lands inside that send.
    service = rig.service()
    await service._ensure_started()
    env = {**rig.env, "NAVIDE_FRAME_PADDING": "x" * 65536}
    rig.signal_sidecar(service, signal.SIGSTOP)
    first = asyncio.create_task(service.create_async(
        pane_id="pane-torn", agent_key="terminal", command=[base_python_executable(), "-u", str(rig.peer)],
        cwd=str(rig.workspace), env=env))
    await rig.until(lambda: service._dispatched, "create frame partly written to the stopped sidecar")
    await asyncio.sleep(0.2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 5)  # the caller is released at once; its frame is not
    rig.signal_sidecar(service, signal.SIGCONT)

    # The channel still frames: a request sent after the cancelled one is answered.
    assert "bookkeeping" in await service._request("ping")
    replacement = await asyncio.wait_for(rig.shell(service, "pane-torn"), 30)
    assert service._lost is None and alive(replacement.proc.pid)
    assert not [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert not service._launches and not service._unresolved


async def test_replacement_create_after_the_abandoned_launch_lost_the_runtime_starts_a_fresh_one(rig):
    service = rig.service()
    await service._ensure_started()
    lost_generation = service._runtime_generation
    rig.signal_sidecar(service, signal.SIGSTOP)
    first = asyncio.create_task(service.create_async(
        pane_id="pane-reloss", agent_key="terminal", command=[base_python_executable(), "-u", str(rig.peer)],
        cwd=str(rig.workspace), env=rig.env))
    await rig.until(lambda: service._dispatched, "create dispatched to the stopped sidecar")
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(rig.shell(service, "pane-reloss"))
    await asyncio.sleep(0.5)
    assert not second.done(), "replacement overlapped an unresolved launch"

    rig.signal_sidecar(service, signal.SIGKILL)  # the abandoned launch ends in a runtime loss
    replacement = await asyncio.wait_for(second, 40)

    assert service._runtime_generation != lost_generation and service._lost is None
    assert alive(replacement.proc.pid)
    invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["lost_generation"] == lost_generation
    assert invalidated[0]["complete"]


async def test_observation_across_runtime_loss_is_unchanged_and_not_duplicated(rig):
    import hashlib  # noqa: F401
    sid = "22222222-2222-4222-8222-222222222222"
    path = rig.root / f"{sid}.jsonl"
    records = [
        {"type": "user", "promptId": "p1", "message": {"content": "hello"}},
        {"type": "assistant", "requestId": "r1", "message": {
            "id": "m1", "model": "fixture", "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 7, "output_tokens": 5}}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    request = {"path": str(path), "mode": "usage", "checkpoint": {}, "seen": [], "anchor_invalid": False}

    async def observe_claude(service, args):
        return await service.observe("claude", "rust-claude-jsonl-v1", dict(args), match=lambda sessions: None)

    service = rig.service()
    before = await observe_claude(service, request)
    assert before["checkpoint"]["offset"] == path.stat().st_size and len(before["events"]) == 1

    rig.signal_sidecar(service, signal.SIGSTOP)
    outstanding = asyncio.create_task(observe_claude(service, request))
    await rig.until(lambda: any(not f.done() for f in service._pending.values()), "observation outstanding")
    rig.signal_sidecar(service, signal.SIGKILL)
    with pytest.raises(RuntimeError, match="RUNTIME_LOST"):
        await asyncio.wait_for(outstanding, 15)  # the existing reader exception path, no fallback parser
    await rig.recovered(service)

    after = await observe_claude(service, request)  # same persisted cursor input
    strip = lambda r: {k: r[k] for k in ("events", "checkpoint", "seen", "parser", "mode")}  # noqa: E731
    assert strip(after) == strip(before)


# ── review-1 corrections (assignment 3) ────────────────────────────────────────

SPAWN_AND_HOLD = """
import os, signal, sys, time
signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
path = sys.argv[1]
open(path + ".tmp", "w").write(str(os.getpid()))
os.replace(path + ".tmp", path)
time.sleep(120)
"""


def owned_pids(rig, session_id: str | None = None) -> list[int]:
    return [r["root"]["pid"] for r in rig.receipts()
            if r["event"] == "owned" and (session_id is None or r["session_id"] == session_id)]


async def test_f1_unpersisted_owned_spawn_is_cleaned_by_identity_before_a_fresh_runtime(rig, monkeypatch):
    """The `owned` event was read but its durable registration is still in flight when
    the sidecar dies. Substituted: only the persistence step is held (external store pacing)."""
    service = rig.service()
    await service._ensure_started()
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned

    def held(*args, **kwargs):
        entered.set()
        assert hold.wait(40), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    ready_file = rig.root / "peer-ready"
    rig.env = {**rig.env, "NAVIDE_PEER_IGNORE_HUP": "1", "NAVIDE_PEER_READY_FILE": str(ready_file)}
    create = asyncio.create_task(rig.shell(service, "pane-held"))
    try:
        assert await asyncio.to_thread(entered.wait, 15), "owned persistence never started"
        pid = owned_pids(rig)[0]
        await rig.until(ready_file.exists, "peer finished installing its signal handlers")
        assert alive(pid)
        rig.signal_sidecar(service, signal.SIGKILL)
        await rig.recovered(service)
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(create, 15)
        assert not alive(pid), "an unregistered, SIGHUP-immune child survived a generation declared clean"
        invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
        assert len(invalidated) == 1 and invalidated[0]["complete"] is True
        assert service._lost is None
    finally:
        hold.set()
    fresh = await rig.shell(service, "pane-fresh")  # only after verified cleanup
    assert fresh.id
    await rig.until(lambda: not any(str(pid) == key for key in pty_registry._load()), "late persistence undone", timeout=10)


async def test_b2_child_spawned_before_owned_was_read_is_cleaned_from_the_sidecar_journal(rig):
    """The sidecar spawned a SIGHUP/TERM-immune child, but neither its `SESSION_OWNED` nor the create
    answer was ever sent to Python (substituted: the harness build's `suppress-owned-and-response`
    fault, which replaces the old gated read side of the pipe). Its identity can only come from the
    sidecar's own durable ownership journal. The child is NOT registered with `rig.pids`."""
    harness = BINARY.parents[2] / "target-harness/release/navide-cli-runtime"
    shutil.copyfile(harness, rig.runtime_bin)
    rig.runtime_bin.chmod(0o755)
    pid_file = rig.root / "spawned.pid"
    service = rig.service()
    await service._ensure_started()
    create = asyncio.create_task(service.create_async(
        pane_id="fault:suppress-owned-and-response", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=[base_python_executable(), "-c", SPAWN_AND_HOLD, str(pid_file)]))
    pid = None
    try:
        await rig.until(pid_file.exists, "the sidecar spawned the child")
        pid = int(pid_file.read_text())
        rig.signal_sidecar(service, signal.SIGKILL)
        await rig.recovered(service)
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(create, 15)

        assert not [r for r in rig.receipts() if r["event"] == "owned"], "the owned event must never have been read"
        assert not alive(pid), "a spawned, never-reported child survived: the sidecar left no durable identity"
        invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
        assert len(invalidated) == 1 and invalidated[0]["complete"] is True, invalidated
        assert service._lost is None
        shutil.copyfile(BINARY, rig.runtime_bin)  # the fault belongs to the lost generation only
        fresh = await rig.shell(service, "pane-after")  # only after verified, journal-based cleanup
        assert fresh.id
    finally:
        if pid is not None and alive(pid):
            psutil.Process(pid).kill()  # failure path only: never a pass condition


async def test_b1_known_survivor_keeps_its_durable_row_through_late_registration_and_restart(rig, monkeypatch):
    """Registration held beyond the 5 s wait; the real identity-matched child SURVIVES the
    cleanup (substituted: signal delivery). Its identity must be durable, must not be undone
    when the late registration completes, and a restarted backend must still reap it."""
    service = rig.service()
    await service._ensure_started()
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned

    def held(*args, **kwargs):
        entered.set()
        assert hold.wait(60), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    real_signal = pty_registry._signal_each
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # nothing dies
    ready_file = rig.root / "peer-ready"
    rig.env = {**rig.env, "NAVIDE_PEER_IGNORE_HUP": "1", "NAVIDE_PEER_READY_FILE": str(ready_file)}
    create = asyncio.create_task(rig.shell(service, "pane-held"))
    pid = None
    try:
        assert await asyncio.to_thread(entered.wait, 15), "owned persistence never started"
        pid = owned_pids(rig)[0]
        await rig.until(ready_file.exists, "peer installed its handlers")
        rig.signal_sidecar(service, signal.SIGKILL)
        await rig.recovered(service)  # the 5 s wait elapses with the registration still held
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(create, 15)
        assert alive(pid) and service._lost and "CLEANUP_FAILED" in service._lost
        generation = service._runtime_generation
        row = pty_registry._load().get(str(pid))
        assert row and row["runtime_generation"] == generation, "the survivor's identity was not made durable"

        hold.set()  # the late registration now completes
        await asyncio.sleep(1.0)
        row = pty_registry._load().get(str(pid))
        assert row and row["runtime_generation"] == generation, "late completion undid a survivor's durable row"
        assert alive(pid)

        # Restart of the backend: the owner is gone, signals work again.
        entries = pty_registry._load()
        entries[str(pid)]["owner"] = 1
        pty_registry._save(entries)
        monkeypatch.setattr(pty_registry, "_signal_each", real_signal)
        signalled = pty_registry.reap_stale(grace=0.3)
        assert pid in signalled
        await rig.until(lambda: not alive(pid), "the restarted backend reaps the survivor by its identity", timeout=10)
    finally:
        hold.set()
        if pid is not None and alive(pid):
            psutil.Process(pid).kill()


async def test_f2_failed_abandoned_cleanup_keeps_the_replacement_blocked_while_the_child_lives(rig, monkeypatch):
    """Substituted: the kill/ack path (fails) and the registry cleanup verdict (unresolved)."""
    service = rig.service()
    await service._ensure_started()
    real_request = service._request

    async def no_cancel(op, *args, **kwargs):  # let the sidecar really spawn
        if op == "cancel":
            raise RuntimeError("cancel withheld by test")
        return await real_request(op, *args, **kwargs)

    monkeypatch.setattr(service, "_request", no_cancel)
    ready_file = rig.root / "peer-ready"
    rig.env = {**rig.env, "NAVIDE_PEER_IGNORE_HUP": "1",  # the old child outlives the retired PTY
               "NAVIDE_PEER_READY_FILE": str(ready_file)}
    rig.signal_sidecar(service, signal.SIGSTOP)
    first = asyncio.create_task(service.create_async(
        pane_id="pane-race", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=[base_python_executable(), "-u", str(rig.peer)]))
    await rig.until(lambda: service._launches, "create dispatched to the stopped sidecar")
    first_sid = next(iter(service._launches))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    async def failing_kill(sid, force=False):
        await rig.until(ready_file.exists, "old child finished installing its signal handlers")
        raise RuntimeError("CLEANUP_FAILED: injected native cleanup failure")

    monkeypatch.setattr(service, "kill", failing_kill)
    monkeypatch.setattr(pty_registry, "reap_runtime", lambda generation, *_args, **_: {
        "signalled": [], "unresolved": [4242], "unverifiable": False})
    second = asyncio.create_task(rig.shell(service, "pane-race"))
    await asyncio.sleep(0.3)
    rig.signal_sidecar(service, signal.SIGCONT)
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await asyncio.wait_for(second, 40)
    old_root = owned_pids(rig, first_sid)
    assert old_root and alive(old_root[0]), "the old child must still be there for this to prove anything"
    assert owned_pids(rig) == old_root, "a replacement was owned while the old child survived"
    assert service._lost and "CLEANUP_FAILED" in service._lost


async def test_f4_native_cleanup_failure_invalidates_the_runtime_for_every_session_and_request(rig):
    """Substituted: only the sidecar's `cleaned` verdict (incomplete) for one session."""
    service = rig.service()
    one = await rig.shell(service, "pane-one")
    two = await rig.shell(service, "pane-two")
    rig.signal_sidecar(service, signal.SIGSTOP)  # a real outstanding request on the other session
    pending = asyncio.create_task(service._request("resize", {"cols": 110, "rows": 40, "redraw": False}, two))
    await rig.until(lambda: any(not f.done() for f in service._pending.values()), "request outstanding")

    await service._finish_cleanup(one, {"cleanup_complete": False, "root_reaped": True,
                                        "survivors": [4242], "unverifiable": []})

    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await asyncio.wait_for(pending, 5)
    await rig.recovered(service)
    assert sorted(e["terminal_session_id"] for e in rig.exits()) == sorted([one.id, two.id])
    assert {e["reason"] for e in rig.exits()} == {"runtime_lost"}
    assert service._lost and service._lost.startswith("CLEANUP_FAILED")
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await rig.shell(service, "pane-three")
    assert len([r for r in rig.receipts() if r["event"] == "ready"]) == 1
    invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["complete"] is False and invalidated[0]["sessions"] == 2


class _Socket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def _handler_create(session, pane: str, command: list[str], cwd: str, *, message_id: str, **extra):
    from agent_team_backend import app
    await app.handle_message(session, {"id": message_id, "type": "terminal.create", "payload": {
        "pane_id": pane, "agent_key": "terminal", "command": command, "cwd": cwd,
        "metadata": {"workspace_path": cwd}, "cols": 100, "rows": 30,
        "create_generation": f"gen-{message_id}", **extra}})
    return [m for m in session.websocket.sent if m.get("id") == message_id][-1]


@pytest.fixture
def handler_world(monkeypatch):
    from agent_team_backend import app

    class Attribution:
        def register_pane(self, *_a, **_k):
            pass

        def scan_pane_baseline(self, *_a, **_k):
            pass

        def unregister_pane(self, *_a, **_k):
            pass

    monkeypatch.setattr(app, "attribution", Attribution())
    monkeypatch.setattr(app, "_register_workspace_and_backfill", lambda _ws: None)
    owners = []
    yield app, owners
    for tid in owners:
        app._PTY_OWNERS.pop(tid, None)


async def test_l1_timed_out_cleanup_wait_is_the_explicit_reap_timeout_refusal(rig, monkeypatch, handler_world):
    """A REAL cleanup wait of the advertised `cleanup_ms` (8 s; no timeout is tuned). Substituted: only the durable-store
    acknowledgement is held, so the sidecar's `cleaned` event is read but never settles."""
    app, owners = handler_world
    service = rig.service()
    session = app.Session(_Socket())
    session.terminals = service
    command = [base_python_executable(), "-u", str(rig.peer)]
    first = await _handler_create(session, "pane-replace", command, str(rig.workspace), message_id="m1")
    assert first.get("ok") is not False and not first.get("error"), first
    old_tid = first["payload"]["terminal_session_id"]
    owners.append(old_tid)
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.unregister

    def held(pid):
        entered.set()
        assert hold.wait(60), "controlled store deadline"
        return real(pid)

    monkeypatch.setattr(pty_registry, "unregister", held)
    started = time.monotonic()
    try:
        second = await asyncio.wait_for(_handler_create(
            session, "pane-replace", command, str(rig.workspace), message_id="m2", replaces_terminal_id=old_tid), 45)
        waited = time.monotonic() - started
    finally:
        hold.set()
    # The wait is the sidecar's advertised cleanup_ms, derived like the baseline's reap ceiling (8 s).
    assert entered.is_set() and waited >= service._limits["cleanup_ms"] / 1000 - 1, f"the cleanup wait never actually ran out ({waited:.1f}s)"
    assert second.get("error"), second
    assert second["error"]["code"] == "CREATE_REAP_TIMEOUT", second
    assert second["error"]["details"]["terminal_session_id"] == old_tid
    assert [r["session_id"] for r in rig.receipts() if r["event"] == "owned"] == [old_tid]  # no replacement


async def test_unreadable_registry_keeps_the_fence_closed_and_never_clears_the_loss(rig, monkeypatch):
    """Substituted: only the registry READ (a locked/corrupt store) during the loss window;
    the live child is recorded durably before it and is SIGHUP-immune."""
    service = rig.service()
    ready_file = rig.root / "peer-ready"
    rig.env = {**rig.env, "NAVIDE_PEER_IGNORE_HUP": "1", "NAVIDE_PEER_READY_FILE": str(ready_file)}
    session = await rig.shell(service, "pane-recorded")
    await rig.until(ready_file.exists, "peer finished installing its signal handlers")
    pid = session.proc.pid
    assert str(pid) in pty_registry._load() and alive(pid)
    real_db = pty_registry._get_db

    def unreadable():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pty_registry, "_get_db", unreadable)
    try:
        rig.signal_sidecar(service, signal.SIGKILL)
        await rig.recovered(service)
    finally:
        monkeypatch.setattr(pty_registry, "_get_db", real_db)
    assert alive(pid), "nothing may be declared clean while a recorded process cannot be verified"
    assert service._lost and "CLEANUP_FAILED" in service._lost, service._lost
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await rig.shell(service, "pane-after")
    assert len([r for r in rig.receipts() if r["event"] == "ready"]) == 1
    invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["complete"] is False and invalidated[0]["unverifiable"] is True
    assert service._lost and "CLEANUP_FAILED" in service._lost  # still not cleared
    assert str(pid) in pty_registry._load(), "the identity must remain recorded for a later verified cleanup"


async def test_b3_cancel_while_the_create_waits_for_the_send_lock_never_reaches_the_sidecar(rig, handler_world):
    """The renderer's `terminal.create.cancel` arrives after launch coordination but while the
    create request still waits for the bridge's send lock: it must be re-checked at the
    dispatch boundary, immediately before bytes go on the wire."""
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_Socket())
    session.terminals = service
    command = [base_python_executable(), "-u", str(rig.peer)]
    await service._send_lock.acquire()
    held = True
    try:
        create = asyncio.create_task(_handler_create(session, "pane-b3", command, str(rig.workspace), message_id="m1"))
        await rig.until(lambda: service._launches, "the create reached the send-lock wait")
        await asyncio.sleep(0.2)
        assert not create.done()
        await app.handle_message(session, {"id": "c1", "type": "terminal.create.cancel",
                                           "payload": {"pane_id": "pane-b3", "create_generation": "gen-m1"}})
        service._send_lock.release()
        held = False
        response = await asyncio.wait_for(create, 20)
    finally:
        if held:
            service._send_lock.release()
    assert response.get("error", {}).get("code") == "CREATE_CANCELLED", response
    await asyncio.sleep(0.5)
    ping = await service._request("ping")
    assert ping["bookkeeping"]["live_sessions"] == 0
    assert not [r for r in rig.receipts() if r["event"] == "owned"], "a cancelled create was sent to the sidecar and spawned"
    assert not service._launches and not service._unresolved


async def test_b2_unreadable_sidecar_journal_keeps_the_fence_and_the_journal(rig):
    """The journal is the only record of roots whose `owned` may never be read, so a torn or
    unreadable one is an unverifiable generation, never an empty one. Substituted: a torn line
    appended (while the real sidecar is frozen) to the real journal."""
    service = rig.service()
    session = await rig.shell(service, "pane-journalled")
    generation = service._runtime_generation
    journal = in_data_dir(rig.root, pty_registry.journal_path, generation)()
    assert journal.exists() and str(session.proc.pid) in journal.read_text()
    rig.signal_sidecar(service, signal.SIGSTOP)
    with journal.open("a") as stream:
        stream.write('{"journal":1,"pid":')
    rig.signal_sidecar(service, signal.SIGKILL)
    await rig.recovered(service)

    assert service._lost and "CLEANUP_FAILED" in service._lost
    invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["complete"] is False and invalidated[0]["unverifiable"] is True
    assert journal.exists(), "an unreadable journal must be kept"
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await rig.shell(service, "pane-after")


# ── ownership gate: a root never executes its target before its identity is durable ──────────────

# POSIX sh: its very first action records the target ran; a background child of the same
# group ignores HUP/TERM and survives a kill of the root alone (the detached-child hazard).
GATED_FIXTURE = 'trap "" HUP TERM; echo $$ > "$0"; sleep 120 & echo $! > "$1"; wait'


def sidecar_children(service) -> dict[int, float]:
    identity = service._runtime_identity
    process = psutil.Process(identity["pid"])
    assert process.pid == service._runtime.pid and process.create_time() == identity["created"]
    return {child.pid: child.create_time() for child in process.children()}


def gone(identities: dict[int, float]) -> bool:
    for pid, created in identities.items():
        try:
            if psutil.Process(pid).create_time() == created and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                return False
        except psutil.NoSuchProcess:
            continue
    return True


async def gated_create(rig, service, name: str):
    marker, detached = rig.root / f"{name}.marker", rig.root / f"{name}.detached"
    task = asyncio.create_task(service.create_async(
        pane_id=f"pane-{name}", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)]))
    return task, marker, detached


def make_fifo(rig, service) -> Path:
    journal = in_data_dir(rig.root, pty_registry.journal_path, service._runtime_generation)()
    assert journal.read_text().startswith('{"journal": "header"')  # Python's atomic header, before the sidecar
    journal.unlink()  # substituted state: the sidecar's append target becomes a FIFO that holds it
    os.mkfifo(journal, 0o600)
    return journal


def cleanup_leaks(rig, *paths: Path) -> None:
    for path in paths:  # failure path only: a leaked fixture is never a pass condition
        if path.exists():
            with contextlib.suppress(Exception):
                psutil.Process(int(path.read_text())).kill()


async def test_gate_sidecar_death_during_the_gate_leaves_no_executed_target_and_no_process(rig):
    service = rig.service()
    await service._ensure_started()
    journal = make_fifo(rig, service)  # the sidecar blocks opening the journal (a real FIFO without a reader)
    task, marker, detached = await gated_create(rig, service, "killed")
    try:
        await rig.until(lambda: sidecar_children(service), "the sidecar forked the root")
        held = sidecar_children(service)
        await asyncio.sleep(1.5)  # long enough for an ungated root to have run its first instruction
        assert not task.done(), "the create should still be waiting on the journal"
        rig.signal_sidecar(service, signal.SIGKILL)
        journal.unlink()  # the test's FIFO only held the sidecar; the generation's journal is otherwise absent
        await rig.recovered(service)
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, 15)
        await rig.until(lambda: gone(held), "the gated root exits by itself when the sidecar is gone", timeout=10)
        assert not marker.exists(), "the target executed before its identity was durable"
        assert not detached.exists(), "the target's detached child exists"
        invalidated = [r for r in rig.receipts() if r["event"] == "runtime_invalidated"]
        assert len(invalidated) == 1 and invalidated[0]["complete"] is True, invalidated
        assert service._lost is None
    finally:
        cleanup_leaks(rig, marker, detached)


async def test_gate_journal_refusal_leaves_no_executed_target_and_no_process(rig):
    """The journal is a real FIFO whose buffer is full and whose reader is held by the test: the
    sidecar's append blocks (a deterministic hold), then the reader closes and the append fails
    (EPIPE) — the refusal. An ungated root would have run its target throughout the hold."""
    service = rig.service()
    await service._ensure_started()
    journal = make_fifo(rig, service)
    reader = os.open(journal, os.O_RDONLY | os.O_NONBLOCK)
    filler = os.open(journal, os.O_WRONLY | os.O_NONBLOCK)
    try:
        while True:
            os.write(filler, b"x" * 4096)
    except BlockingIOError:
        pass
    os.close(filler)
    task, marker, detached = await gated_create(rig, service, "refused")
    try:
        await rig.until(lambda: sidecar_children(service), "the sidecar forked the root")
        held = sidecar_children(service)
        await asyncio.sleep(1.5)  # the append is blocked on the full pipe
        assert not task.done()
        os.close(reader)
        reader = -1
        with pytest.raises(RuntimeError, match="OWNERSHIP_JOURNAL_FAILED"):
            await asyncio.wait_for(task, 15)
        await rig.until(lambda: gone(held), "no root remains after the refusal", timeout=10)
        await asyncio.sleep(0.3)
        assert not marker.exists(), "the target executed although its identity could not be journalled"
        assert not detached.exists(), "a detached child of a refused create exists"
        assert sidecar_children(service) == {}
        ping = await service._request("ping")
        assert ping["bookkeeping"]["live_sessions"] == 0 and service._lost is None
        assert not [r for r in rig.receipts() if r["event"] == "owned"]
    finally:
        if reader >= 0:
            os.close(reader)
        cleanup_leaks(rig, marker, detached)


async def test_gate_normal_path_keeps_session_group_and_controlling_terminal(rig):
    service = rig.service()
    session = await rig.shell(service, "pane-normal")
    pid = session.proc.pid
    process = psutil.Process(pid)
    # The properties the 02-04 tests assert about the root: its own session and group, a controlling PTY.
    assert os.getsid(pid) == pid and os.getpgid(pid) == pid
    owned = next(r for r in rig.receipts() if r["event"] == "owned" and r["session_id"] == session.id)
    assert owned["root"]["pid"] == pid and owned["root"]["pgid"] == pid and owned["root"]["start_value"]
    assert process.terminal() and process.terminal().startswith("/dev/tty")
    journal = in_data_dir(rig.root, pty_registry.journal_path, service._runtime_generation)()
    lines = [json.loads(line) for line in journal.read_text().splitlines()]
    assert lines[0]["journal"] == "header" and lines[0]["owner_pid"] == os.getpid()
    lines = [r for r in lines if r["journal"] == 1]
    assert [(r["pid"], r["pgid"], r["start_value"]) for r in lines] == [(pid, pid, owned["root"]["start_value"])]
    assert service._lost is None and sidecar_children(service).keys() == {pid}


async def test_gate_serialises_concurrent_creates_without_cross_talk(rig):
    service = rig.service()
    sessions = await asyncio.gather(*(rig.shell(service, f"pane-concurrent-{i}") for i in range(6)))
    pids = [s.proc.pid for s in sessions]
    assert len(set(pids)) == 6 and all(os.getsid(pid) == pid for pid in pids)
    journal = in_data_dir(rig.root, pty_registry.journal_path, service._runtime_generation)()
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert sorted(r["pid"] for r in records if r["journal"] == 1) == sorted(pids)
    assert service._lost is None


async def test_f2r4_cancel_during_a_held_gate_is_acked_only_with_proof_and_the_target_never_executes(rig, handler_world):
    """The journal append is held (FIFO), the renderer's cancel arrives through the handler while the
    root waits at the gate, and only afterwards the hold is released. The ACK must come with proof the
    target cannot execute; the fixture's first instruction (a marker file) must never appear."""
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()
    journal = make_fifo(rig, service)
    session = app.Session(_Socket())
    session.terminals = service
    marker, detached = rig.root / "cancelled.marker", rig.root / "cancelled.detached"
    create = asyncio.create_task(_handler_create(
        session, "pane-r4", ["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)], str(rig.workspace),
        message_id="m1"))
    reader = -1
    try:
        await rig.until(lambda: sidecar_children(service), "the root is forked and gated")
        held = sidecar_children(service)
        await asyncio.sleep(1.0)  # the journal append is blocked: the gate is closed
        assert not create.done() and not marker.exists()
        await app.handle_message(session, {"id": "c1", "type": "terminal.create.cancel",
                                           "payload": {"pane_id": "pane-r4", "create_generation": "gen-m1"}})
        ack = [m for m in session.websocket.sent if m.get("id") == "c1"][-1]
        # ORDER: the ACK was produced while the journal was still held, so it must be a proven one.
        assert not marker.exists(), "the target executed before the cancel ACK"
        assert ack.get("payload", {}).get("cancelled") is True or ack.get("error"), ack
        reader = os.open(journal, os.O_RDONLY | os.O_NONBLOCK)  # NOW release the hold
        os.set_blocking(reader, True)
        drain = asyncio.get_running_loop().run_in_executor(None, lambda: os.read(reader, 1 << 20))
        response = await asyncio.wait_for(create, 20)
        await asyncio.wait_for(drain, 5) if not drain.done() else None
        await rig.until(lambda: gone(held), "the cancelled create leaves no process", timeout=10)
        await asyncio.sleep(0.5)
        assert not marker.exists(), "the cancelled target executed after the ACK"
        assert not detached.exists() and sidecar_children(service) == {}
        assert response.get("error", {}).get("code") == "CREATE_CANCELLED", response
        assert not [r for r in rig.receipts() if r["event"] == "owned"]
        assert ack["payload"]["cancelled"] is True
    finally:
        if reader >= 0:
            os.close(reader)
        cleanup_leaks(rig, marker, detached)


async def test_f2r4_cancel_after_the_gate_opened_is_proven_only_by_the_verified_cleanup_disposition(rig, monkeypatch):
    """Substituted: the sidecar's answer to `cancel` (forced to "released", i.e. the target may be running)
    while the real sidecar proceeds normally. The verdict is not a proof ("pending"); the transaction's own
    disposition is: the created root cleaned with its verified identity, never published."""
    service = rig.service()
    await service._ensure_started()
    journal = make_fifo(rig, service)
    real_request = service._request

    async def released(op, *args, **kwargs):
        if op == "cancel":
            return {"state": "released"}
        return await real_request(op, *args, **kwargs)

    monkeypatch.setattr(service, "_request", released)
    marker, detached = rig.root / "released.marker", rig.root / "released.detached"
    record: dict = {"cancelled": False}
    create = asyncio.create_task(service.create_async(
        pane_id="pane-released", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)],
        cancelled=lambda: record["cancelled"], cancel_record=record))
    reader = -1
    try:
        await rig.until(lambda: sidecar_children(service), "the root is forked and gated")
        await asyncio.sleep(0.5)
        record["cancelled"] = True
        assert await service.cancel_dispatched(record) == "pending", "a released verdict is not a proof"
        reader = os.open(journal, os.O_RDONLY | os.O_NONBLOCK)
        os.set_blocking(reader, True)
        asyncio.get_running_loop().run_in_executor(None, lambda: os.read(reader, 1 << 20))
        with pytest.raises(CreateCancelled):
            await asyncio.wait_for(create, 30)
        await asyncio.wait_for(record["native"]["settled"].wait(), 30)
        assert record["native"]["outcome"] == "cleaned"
        owned = [r for r in rig.receipts() if r["event"] == "owned"]
        assert len(owned) == 1
        assert not alive(owned[0]["root"]["pid"]), "disposition 'cleaned' before the root was gone"
        cleaned = [r for r in rig.receipts() if r["event"] == "cleaned" and r["session_id"] == owned[0]["session_id"]]
        assert cleaned and cleaned[0]["cleanup_complete"] and cleaned[0]["root_reaped"]
        assert not _alive_pidfile(detached)
        assert service._lost is None and not service._unresolved and not service._sessions
    finally:
        if reader >= 0:
            os.close(reader)
        cleanup_leaks(rig, marker, detached)


# ── review-5 N2: the cancellation proof must survive publication of the term ─────────────────────

class _RecordingSocket(_Socket):
    """Records, at the moment each response is sent, which receipts already existed."""

    def __init__(self, rig):
        super().__init__()
        self.rig = rig
        self.receipts_at: dict[str, list[dict]] = {}

    async def send_json(self, payload):
        if payload.get("id"):
            self.receipts_at[payload["id"]] = self.rig.receipts()
        await super().send_json(payload)


def _alive_pidfile(path: Path) -> bool:
    return path.exists() and alive(int(path.read_text()))


async def _n2_case(rig, handler_world, *, hold: str, monkeypatch):
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    marker, detached = rig.root / f"{hold}.marker", rig.root / f"{hold}.detached"
    extra = {}
    released = threading.Event()
    entered = threading.Event()
    if hold == "output-log":
        log_fifo = rig.root / "output-log.fifo"
        os.mkfifo(log_fifo, 0o600)  # opening it for append blocks until a reader exists
        extra["output_log_file"] = str(log_fifo)
    else:
        real = pty_registry.register_owned

        def held(*args, **kwargs):
            entered.set()
            assert released.wait(60), "controlled persistence deadline"
            return real(*args, **kwargs)

        monkeypatch.setattr(pty_registry, "register_owned", held)
    create = asyncio.create_task(_handler_create(
        session, f"pane-{hold}", ["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)],
        str(rig.workspace), message_id="m1", **extra))
    reader = -1
    try:
        # The native create was answered (owned receipt) and Python is now held after it.
        await rig.until(lambda: [r for r in rig.receipts() if r["event"] == "owned"], "native create answered")
        owned = [r for r in rig.receipts() if r["event"] == "owned"][0]
        if hold == "persistence":
            assert await asyncio.to_thread(entered.wait, 10)
        await rig.until(marker.exists, "the released target ran")
        await asyncio.sleep(0.5)
        assert not create.done()
        cancel = asyncio.create_task(app.handle_message(session, {
            "id": "c1", "type": "terminal.create.cancel",
            "payload": {"pane_id": f"pane-{hold}", "create_generation": "gen-m1"}}))
        await asyncio.sleep(1.0)
        acked_while_held = "c1" in session.websocket.receipts_at
        # Release the hold.
        if hold == "output-log":
            reader = os.open(log_fifo, os.O_RDONLY | os.O_NONBLOCK)
        else:
            released.set()
        await asyncio.wait_for(cancel, 70)
        response = await asyncio.wait_for(create, 30)
        ack = [m for m in session.websocket.sent if m.get("id") == "c1"][-1]
        at_ack = session.websocket.receipts_at["c1"]
        root_pid = owned["root"]["pid"]
        cleaned_at_ack = [r for r in at_ack if r["event"] == "cleaned" and r["session_id"] == owned["session_id"]]
        await asyncio.sleep(0.5)
        return {"acked_while_held": acked_while_held, "ack": ack, "response": response,
                "cleaned_at_ack": cleaned_at_ack, "root_alive": alive(root_pid),
                "detached_alive": _alive_pidfile(detached), "owned": owned, "session": session,
                "service": service}
    finally:
        released.set()
        if reader >= 0:
            os.close(reader)
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)
        cleanup_leaks(rig, marker, detached)


@pytest.mark.parametrize("hold", ["output-log", "persistence"])
async def test_n2_cancel_after_the_native_answer_is_proven_only_by_verified_cleanup(rig, handler_world, monkeypatch, hold):
    """Hold Python after the native create answered (the output-log open, or the owned-persistence
    wait after the first cancellation check), cancel, release the hold. The ACK may say cancelled
    only once the created root was cleaned by its verified identity; no live root, no detached child."""
    result = await _n2_case(rig, handler_world, hold=hold, monkeypatch=monkeypatch)
    ack = result["ack"]
    assert not result["root_alive"], "the cancelled create's root is still alive"
    assert not result["detached_alive"], "the cancelled create's detached child is still alive"
    assert (ack.get("payload") or {}).get("cancelled") is True, ack
    assert result["cleaned_at_ack"] and result["cleaned_at_ack"][0]["cleanup_complete"] \
        and result["cleaned_at_ack"][0]["root_reaped"], "cancelled:true was sent before verified cleanup"
    assert not result["acked_while_held"], "acknowledged while the published root could still run"
    assert result["response"].get("error", {}).get("code") == "CREATE_CANCELLED", result["response"]
    assert not result["service"]._sessions, "the cancelled term was published and left live"


# ── review-6 R6-1: the create success/cancel decision is made once, before any yielding send ─────

class _HeldSuccessSocket(_Socket):
    """Holds the first successful `terminal.create` response at its awaited send, and records,
    for every response as it is sent, whether the created root was alive at that moment."""

    def __init__(self, rig, message_id: str):
        super().__init__()
        self.rig, self.message_id = rig, message_id
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.root_alive_at: list[tuple[str, bool, dict]] = []

    def _root_alive(self) -> bool:
        owned = [r for r in self.rig.receipts() if r["event"] == "owned"]
        return bool(owned) and alive(owned[0]["root"]["pid"])

    async def send_json(self, payload):
        if payload.get("id") == self.message_id and payload.get("ok") and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()  # the awaited boundary of the success send
        self.root_alive_at.append((payload.get("id"), self._root_alive(), payload))
        await super().send_json(payload)


async def test_r6_1_a_create_whose_success_is_being_sent_has_exactly_one_coherent_outcome(rig, handler_world):
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()
    socket = _HeldSuccessSocket(rig, "m1")
    session = app.Session(socket)
    session.terminals = service
    command = [base_python_executable(), "-u", str(rig.peer)]
    create = asyncio.create_task(_handler_create(session, "pane-r61", command, str(rig.workspace), message_id="m1"))
    cancel = None
    try:
        await asyncio.wait_for(socket.entered.wait(), 30)  # native term published; success send held
        cancel = asyncio.create_task(app.handle_message(session, {
            "id": "c1", "type": "terminal.create.cancel",
            "payload": {"pane_id": "pane-r61", "create_generation": "gen-m1"}}))
        await asyncio.sleep(1.0)
        socket.release.set()
        await asyncio.wait_for(cancel, 70)
        await asyncio.wait_for(create, 30)
        await asyncio.sleep(0.5)
        create_responses = [(alive_then, m) for mid, alive_then, m in socket.root_alive_at if mid == "m1"]
        cancel_responses = [(alive_then, m) for mid, alive_then, m in socket.root_alive_at if mid == "c1"]
        assert len(create_responses) == 1, f"duplicate responses for one create id: {[m for _, m in create_responses]}"
        assert len(cancel_responses) == 1
        create_ok = create_responses[0][1].get("ok") is True
        cancel_alive, cancel_msg = cancel_responses[0]
        cancelled = (cancel_msg.get("payload") or {}).get("cancelled") is True
        assert not (create_ok and cancelled), "success was sent for a generation whose cancel reports cancelled:true"
        if cancelled:
            assert not cancel_alive, "cancelled:true while the created root was alive"
        if create_ok:
            # The create committed first: the cancel is not a pending create any more, the term is the
            # client's to close normally.
            tid = create_responses[0][1]["payload"]["terminal_session_id"]
            assert tid in service._sessions and cancel_msg.get("ok") is True and not cancelled
            owners.append(tid)
            await service.kill(tid, force=True)
    finally:
        socket.release.set()
        for task in (create, cancel):
            if task is not None and not task.done():
                task.cancel()
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)


async def test_r6_1_a_success_send_that_finds_the_connection_dead_rolls_the_term_back(rig, handler_world):
    """Regression proof (no red: the behaviour is preserved): commit-before-send must not turn a
    disconnect during the success send into a live term nobody holds."""
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()

    class Dropping(_Socket):
        async def send_json(self, payload):
            if payload.get("id") == "m1":
                raise RuntimeError('Cannot call "send" once a close message has been sent')
            await super().send_json(payload)

    session = app.Session(Dropping())
    session.terminals = service
    command = [base_python_executable(), "-u", str(rig.peer)]
    await asyncio.wait_for(app.handle_message(session, {
            "id": "m1", "type": "terminal.create", "payload": {
                "pane_id": "pane-drop", "agent_key": "terminal", "command": command, "cwd": str(rig.workspace),
                "metadata": {"workspace_path": str(rig.workspace)}, "cols": 100, "rows": 30,
                "create_generation": "gen-m1"}}), 30)
    assert session.dead
    owned = [r for r in rig.receipts() if r["event"] == "owned"]
    assert len(owned) == 1
    await rig.until(lambda: not alive(owned[0]["root"]["pid"]), "the undelivered term is rolled back", timeout=15)
    assert not service._sessions
    transaction = session._terminal_create_transactions.get(("pane-drop", "gen-m1"))
    assert transaction is None or not transaction.get("committed")


async def test_note1_a_registration_that_outlives_the_cleanup_wait_cannot_recreate_a_dead_roots_row(rig, monkeypatch):
    """Substituted: only the durable registry write (held ~40 s, beyond the create's 15 s receipt
    wait AND the cleanup's 15 s wait for it). The root is really spawned and really killed."""
    service = rig.service()
    await service._ensure_started()
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned

    def held(*args, **kwargs):
        entered.set()
        assert hold.wait(90), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    create = asyncio.create_task(service.create_async(
        pane_id="pane-note1", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=[base_python_executable(), "-u", str(rig.peer)]))
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        pid = owned_pids(rig)[0]
        with pytest.raises(Exception):
            await asyncio.wait_for(create, 60)  # the receipt never arrived within 15 s
        await rig.until(lambda: not alive(pid), "the unreceipted spawn was killed", timeout=20)
        await asyncio.sleep(16)  # past the cleanup's own 15 s wait for the held write
        hold.set()  # the delayed registration now completes
        await asyncio.sleep(2.0)
        assert str(pid) not in pty_registry._load(), "a dead root's row was recreated after its cleanup"
        assert service._lost and "CLEANUP_FAILED" in service._lost, \
            "cleanup must not be reported complete while its registry write was unsettled"
        with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
            await rig.shell(service, "pane-after")
    finally:
        hold.set()


# ── review-8 R8-1(b): close of a pane whose create is held before native publication ─────────────

async def test_r8_1b_unspawn_settles_an_unpublished_create_before_it_answers(rig, handler_world, monkeypatch):
    """Substituted: only the durable registration of the FIRST create (held), i.e. the create was
    answered natively (its target ran) but is not yet published. `manual_pane.unspawn` for that pane,
    then an immediate replacement create: the old root must be verified cleaned before unspawn answers
    and before the replacement's `owned`."""
    app, owners = handler_world
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    marker, detached = rig.root / "r81b.marker", rig.root / "r81b.detached"
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned
    calls = {"n": 0}

    def held(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert hold.wait(60), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    create = asyncio.create_task(_handler_create(
        session, "pane-r81b", ["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)],
        str(rig.workspace), message_id="m1"))
    unspawn = None
    try:
        assert await asyncio.to_thread(entered.wait, 15), "the first create's persistence never started"
        old = [r for r in rig.receipts() if r["event"] == "owned"][0]
        await rig.until(marker.exists, "the old target ran (an unseen CLI unless it is settled)")
        unspawn = asyncio.create_task(app.handle_message(session, {
            "id": "u1", "type": "manual_pane.unspawn",
            "payload": {"workspace_path": str(rig.workspace), "pane_id": "pane-r81b", "session_id": ""}}))
        await asyncio.sleep(1.0)
        answered_while_held = "u1" in session.websocket.receipts_at
        threading.Timer(0.5, hold.set).start()  # the store recovers
        await asyncio.wait_for(unspawn, 90)
        reply = [m for m in session.websocket.sent if m.get("id") == "u1"][-1]
        at_reply = session.websocket.receipts_at["u1"]
        old_alive_at_reply = alive(old["root"]["pid"])
        # Immediate replacement (a new pane, as a close-then-open does).
        replacement = await _handler_create(
            session, "pane-r81b-next", [base_python_executable(), "-u", str(rig.peer)],
            str(rig.workspace), message_id="m2")
        new_tid = replacement["payload"]["terminal_session_id"]
        owners.append(new_tid)
        receipts = rig.receipts()
        cleaned = next((i for i, r in enumerate(receipts)
                        if r["event"] == "cleaned" and r["session_id"] == old["session_id"]), -1)
        second = next(i for i, r in enumerate(receipts) if r["event"] == "owned" and r["session_id"] == new_tid)
        assert reply.get("ok") is True, reply
        assert not answered_while_held, "unspawn answered while the pane's unpublished create was still live"
        assert [r for r in at_reply if r["event"] == "cleaned" and r["session_id"] == old["session_id"]], \
            "unspawn answered before the old root's verified cleanup"
        assert not old_alive_at_reply
        assert -1 < cleaned < second, "the replacement's CLI was admitted before the old root was cleaned"
        assert receipts[cleaned]["cleanup_complete"] and receipts[cleaned]["root_reaped"]
        assert not _alive_pidfile(detached)
        await asyncio.wait_for(create, 30)
    finally:
        hold.set()
        if unspawn is not None and not unspawn.done():
            unspawn.cancel()
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)
        cleanup_leaks(rig, marker, detached)


# ── review-9: one retirement rule — settle (in-flight + published) BEFORE any record change ───────

def _pipeline_slot(ws: str, pane_id: str) -> None:
    from agent_team_backend import app
    app.project_store.start_pipeline(
        ws, task_description="t", total_stages=1,
        stage_blueprint=[{"stage_id": "01", "title": "Build",
                          "slots": [{"label": "Build", "agent": "terminal", "role": "eng"}]}])
    app.project_store.record_slot_spawn(ws, stage_index=0, slot_label="Build", pane_id=pane_id, agent="terminal")


def _record_status(ws: str, pane_id: str) -> str:
    from agent_team_backend import app
    project = app.project_store.load_or_create(ws)
    return next(p.spawn_status for p in project.panes if p.pane_id == pane_id)


@pytest.mark.parametrize("retire", ["slot_unspawn", "slot_unspawn_by_pane"])
async def test_f2_pipeline_retirement_settles_an_unpublished_create_before_it_answers(rig, handler_world, monkeypatch, retire):
    """Real sidecar. A pipeline slot's create is answered natively (its target runs) but held before
    publication (substituted: its durable registration). Retiring the slot must clean the old root,
    verified, before it answers and before a new slot pane's `owned`."""
    app, owners = handler_world
    ws = str(rig.workspace)
    _pipeline_slot(ws, "slot-p1")
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    marker, detached = rig.root / f"{retire}.marker", rig.root / f"{retire}.detached"
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned
    calls = {"n": 0}

    def held(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert hold.wait(60), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    create = asyncio.create_task(_handler_create(
        session, "slot-p1", ["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)], ws, message_id="m1"))
    retiring = None
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        old = [r for r in rig.receipts() if r["event"] == "owned"][0]
        await rig.until(marker.exists, "the old target ran")
        payload = ({"workspace_path": ws, "stage_index": 0, "slot_label": "Build"} if retire == "slot_unspawn"
                   else {"workspace_path": ws, "pane_id": "slot-p1"})
        retiring = asyncio.create_task(app.handle_message(session, {"id": "u1", "type": f"pipeline.{retire}", "payload": payload}))
        await asyncio.sleep(1.0)
        answered_while_held = "u1" in session.websocket.receipts_at
        threading.Timer(0.5, hold.set).start()
        await asyncio.wait_for(retiring, 90)
        reply = [m for m in session.websocket.sent if m.get("id") == "u1"][-1]
        at_reply = session.websocket.receipts_at["u1"]
        replacement = await _handler_create(session, "slot-p2", [base_python_executable(), "-u", str(rig.peer)], ws, message_id="m2")
        owners.append(replacement["payload"]["terminal_session_id"])
        receipts = rig.receipts()
        cleaned = next((i for i, r in enumerate(receipts) if r["event"] == "cleaned" and r["session_id"] == old["session_id"]), -1)
        second = next(i for i, r in enumerate(receipts) if r["event"] == "owned" and r["session_id"] == replacement["payload"]["terminal_session_id"])
        assert reply.get("ok") is True, reply
        assert not answered_while_held, f"{retire} answered while the slot's unpublished create was live"
        assert [r for r in at_reply if r["event"] == "cleaned" and r["session_id"] == old["session_id"]], \
            f"{retire} answered before the old root's verified cleanup"
        assert -1 < cleaned < second and receipts[cleaned]["cleanup_complete"] and receipts[cleaned]["root_reaped"]
        assert not _alive_pidfile(detached)
        assert _record_status(ws, "slot-p1") == "removed"
        await asyncio.wait_for(create, 30)
    finally:
        hold.set()
        if retiring is not None and not retiring.done():
            retiring.cancel()
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)
        cleanup_leaks(rig, marker, detached)


@pytest.mark.parametrize("retire", ["manual_pane.unspawn", "pipeline.slot_unspawn", "pipeline.slot_unspawn_by_pane"])
async def test_f3_an_unresolved_settlement_refuses_and_retires_no_record(rig, handler_world, retire):
    """Real sidecar, real live root. The sidecar is frozen (SIGSTOP), so the verified kill cannot be
    confirmed: the handler must refuse and the record must stay 'spawned' while the process lives.
    After SIGCONT, a retry settles (verified) and only then retires the record."""
    app, owners = handler_world
    ws = str(rig.workspace)
    pane = "pane-f3"
    service = rig.service()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    first = await _handler_create(session, pane, [base_python_executable(), "-u", str(rig.peer)], ws, message_id="m1")
    tid = first["payload"]["terminal_session_id"]
    owners.append(tid)
    if retire == "manual_pane.unspawn":
        app.project_store.record_manual_pane_spawn(ws, pane_id=pane, agent="terminal")
        payload = {"workspace_path": ws, "pane_id": pane, "session_id": ""}
    else:
        _pipeline_slot(ws, pane)
        payload = ({"workspace_path": ws, "stage_index": 0, "slot_label": "Build"} if retire == "pipeline.slot_unspawn"
                   else {"workspace_path": ws, "pane_id": pane})
    root = service._sessions[tid].proc.pid
    rig.signal_sidecar(service, signal.SIGSTOP)
    try:
        await asyncio.wait_for(app.handle_message(session, {"id": "u1", "type": retire, "payload": payload}), 90)
        reply = [m for m in session.websocket.sent if m.get("id") == "u1"][-1]
        assert reply.get("ok") is not True, f"{retire} reported success while the pane's root was unverified"
        assert reply["error"]["code"] == "PANE_RELEASE_UNRESOLVED", reply
        assert alive(root)
        assert _record_status(ws, pane) == "spawned", "an unresolved retirement persisted 'removed'"
    finally:
        rig.signal_sidecar(service, signal.SIGCONT)
    assert service._lost is None, "this case needs the runtime to survive the freeze, so the retry is exercised"
    await asyncio.wait_for(app.handle_message(session, {"id": "u2", "type": retire, "payload": payload}), 90)
    retry = [m for m in session.websocket.sent if m.get("id") == "u2"][-1]
    assert retry.get("ok") is True, retry
    assert not alive(root)
    assert _record_status(ws, pane) == "removed"


# ── review-10 B6: a dock window's close settles its panel's in-flight create before it answers ────

async def test_b6_kill_surface_settles_an_unpublished_panel_create_before_it_answers(rig, handler_world, monkeypatch):
    """Real sidecar. An embedded-panel (surface "plan") create is answered natively and its gated
    target runs, but its durable registration is held (substituted: the store), so it is unpublished.
    `terminal.kill_surface` for that window must clean the root, verified, before it answers."""
    app, owners = handler_world
    ws = str(rig.workspace)
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    marker, detached = rig.root / "b6.marker", rig.root / "b6.detached"
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned

    def held(*args, **kwargs):
        entered.set()
        assert hold.wait(60), "controlled persistence deadline"
        return real(*args, **kwargs)

    monkeypatch.setattr(pty_registry, "register_owned", held)
    create = asyncio.create_task(_handler_create(
        session, "panel-b6", ["/bin/sh", "-c", GATED_FIXTURE, str(marker), str(detached)], ws, message_id="m1",
        metadata={"workspace_path": ws, "surface": "plan"}))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        old = [r for r in rig.receipts() if r["event"] == "owned"][0]
        await rig.until(marker.exists, "the panel's target ran")
        closing = asyncio.create_task(app.handle_message(session, {
            "id": "k1", "type": "terminal.kill_surface", "payload": {"surface": "plan", "workspace_path": ws}}))
        await asyncio.sleep(1.0)
        answered_while_held = "k1" in session.websocket.receipts_at
        threading.Timer(0.5, hold.set).start()
        await asyncio.wait_for(closing, 90)
        reply = [m for m in session.websocket.sent if m.get("id") == "k1"][-1]
        at_reply = session.websocket.receipts_at["k1"]
        await asyncio.wait_for(create, 30)
        assert reply.get("ok") is True, reply
        assert not answered_while_held, "kill_surface answered while the panel's unpublished create was live"
        assert [r for r in at_reply if r["event"] == "cleaned" and r["session_id"] == old["session_id"]], \
            "kill_surface answered before the panel's root was verifiably cleaned"
        assert not alive(old["root"]["pid"]) and not _alive_pidfile(detached)
        assert not service._sessions, "the closed window's panel was published after its close"
    finally:
        hold.set()
        if closing is not None and not closing.done():
            closing.cancel()
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)
        cleanup_leaks(rig, marker, detached)


async def test_b6_an_unresolved_panel_settlement_refuses_and_retires_no_record(rig, handler_world, monkeypatch):
    """The panel create cannot be proven cancelled or cleaned (substituted: the disposition, which
    reports nothing within its bound) — kill_surface must answer an error and keep the record."""
    app, owners = handler_world
    ws = str(rig.workspace)
    service = rig.service()
    await service._ensure_started()
    session = app.Session(_RecordingSocket(rig))
    session.terminals = service
    app.project_store.record_manual_pane_spawn(ws, pane_id="panel-b6u", agent="terminal", surface="plan")
    hold, entered = threading.Event(), threading.Event()
    real = pty_registry.register_owned

    def held(*args, **kwargs):
        entered.set()
        assert hold.wait(60), "controlled persistence deadline"
        return real(*args, **kwargs)

    async def never_proven(_transaction):
        return False

    monkeypatch.setattr(pty_registry, "register_owned", held)
    from agent_team_backend import ws_handlers
    monkeypatch.setattr(ws_handlers, "_native_cancel_disposition", never_proven)
    create = asyncio.create_task(_handler_create(
        session, "panel-b6u", [base_python_executable(), "-u", str(rig.peer)], ws, message_id="m1",
        metadata={"workspace_path": ws, "surface": "plan"}))
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        await asyncio.wait_for(app.handle_message(session, {
            "id": "k1", "type": "terminal.kill_surface", "payload": {"surface": "plan", "workspace_path": ws}}), 60)
        reply = [m for m in session.websocket.sent if m.get("id") == "k1"][-1]
        assert reply.get("ok") is not True, f"kill_surface reported success over an unproven panel create: {reply}"
        assert reply["error"]["code"] == "PANE_RELEASE_UNRESOLVED", reply
        assert _record_status(ws, "panel-b6u") == "spawned", "an unresolved close retired the panel's record"
    finally:
        hold.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(create, 30)
        for tid in list(service._sessions):
            with contextlib.suppress(Exception):
                await service.kill(tid, force=True)


async def test_a_sidecar_refusal_keeps_the_session_id_and_the_sidecars_own_stage(rig, monkeypatch):
    """A refusal by the sidecar happens after the session id was allocated, so its `create_failed` receipt
    names that id and the sidecar's stage; a Python pre-CREATE refusal has neither. Substituted: the
    launch Python would build, replaced by one that names an option the shell does not declare."""
    from agent_team_backend import cli_launch
    monkeypatch.setattr(cli_launch, "build_launch", lambda *_a, **_k: {
        "shape": "direct", "form": "string", "words": [{"lit": "true"}, {"opt": "model", "value": "x"}]})
    service = rig.service()
    with pytest.raises(RuntimeError):
        await rig.shell(service, "pane-refused", command="true")
    failed = [r for r in rig.receipts() if r["event"] == "create_failed"]
    assert len(failed) == 1, failed
    assert isinstance(failed[0]["session_id"], str) and failed[0]["session_id"]
    assert failed[0]["stage"] == "option" and failed[0]["effect"] == "none"
    assert not [r for r in rig.receipts() if r["event"] == "owned"]


async def test_an_attestation_that_does_not_match_the_program_sent_is_a_failed_binding_check_that_kills_the_root(rig, monkeypatch):
    """Substituted: Python's belief about the program it sent, so that the real sidecar's honest attestation
    no longer matches it. The real verification then rejects the handoff like any failed binding check."""
    monkeypatch.setattr(RustTerminalService, "_expected_program", staticmethod(lambda _launch: "/not/the/program"))
    registered: list = []
    monkeypatch.setattr(pty_registry, "register_owned", lambda *args, **_k: registered.append(args))
    service = rig.service()
    with pytest.raises(RuntimeError):
        await rig.shell(service, "pane-bad-attestation")
    rejected = [r for r in rig.receipts() if r["event"] == "handoff_rejected"]
    assert len(rejected) == 1 and rejected[0]["check"] == "argv0", rejected
    assert [args[1] for args in registered] == [[""]], "only the identity is registered; no unverified program text is persisted"
    assert registered and not psutil.pid_exists(registered[0][0]), "the rejected root was killed"


async def test_a_malformed_plugin_result_is_a_bounded_pre_dispatch_refusal_with_no_child(rig, monkeypatch, handler_world):
    """Principal seam (terminal.create handler → service). Substituted: only the plugin host's transformer list,
    which holds one plugin returning an int instead of a command."""
    app, _owners = handler_world
    monkeypatch.setattr(app.plugin_host, "spawn_transformers", lambda: [("bad-plugin", lambda *_a, **_k: 7)])
    service = rig.service()
    session = app.Session(_Socket())
    session.terminals = service
    reply = await _handler_create(session, "pane-bad-plugin", [base_python_executable(), "-u", str(rig.peer)],
                                  str(rig.workspace), message_id="m-bad")
    assert reply.get("ok") is False, reply
    failed = [r for r in rig.receipts() if r["event"] == "create_failed"]
    assert len(failed) == 1, failed
    assert (failed[0]["code"], failed[0]["stage"], failed[0]["session_id"], failed[0]["effect"]) == (
        "COMPOSITION_UNREPRESENTABLE", "pre-dispatch", None, "none")
    assert "plugin bad-plugin" in failed[0]["message"], failed[0]
    assert not [r for r in rig.receipts() if r["event"] == "owned"], "no child was created"
    assert not rig.output, "no pane produced output"
