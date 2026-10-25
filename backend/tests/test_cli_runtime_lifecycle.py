"""Lifecycle, resize and loss proofs with the REAL sidecar (E1 T1-T6, R1-R6, S2-S6, S9, L1-L8).

A real RustTerminalService drives the actual release sidecar and macOS PTYs with fixture CLIs.
Substituted pieces, named where used: the WebSocket sink (a controllable `Sink`), the resize
response hold and the injected survivor/corrupt frame (harness build, cargo feature `fault-hooks`,
never the product binary), and the shortened request/ping deadlines of L2.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from agent_team_backend import cli_runtime, pty_registry
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_handoff import MasterHandle
from agent_team_backend.terminals import TerminalService, _clean_for_log
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT
from tests.cli_regression.support.isolation import isolated_environment

ROOT = Path(__file__).resolve().parents[2]
PEER = Path(__file__).parent / "cli_regression/support/lifecycle_peer.py"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built POSIX native runtime (product and harness)"),
]

LOSS_BOUND_S = (cli_runtime.TASK_DRAIN_S + cli_runtime.SIDECAR_EXIT_S + cli_runtime.PERSIST_WAIT_S
                + 8.0 + 2 * cli_runtime.LOSS_FLUSH_S)  # cleanup_ms is 8 s; the 2nd LOSS_FLUSH_S is the exit-queue wait


class Sink:
    """The WebSocket sink: open, or gated per session. A gated emit waits for `allow()` and, like a
    wedged sink, swallows its first cancellation and keeps waiting (L1b)."""

    def __init__(self):
        self.timeline: list[tuple] = []  # ("out", sid, bytes) / ("exit", sid, payload) / ("ack", sid), after the gate
        self.attempts: list[tuple] = []  # every emit the runtime queued, before the gate
        self.held: dict[str, asyncio.Semaphore] = {}
        self.swallow: set[str] = set()
        self.swallowed = 0
        self.delay = 0.0

    def hold(self, sid: str, *, swallow_first_cancel: bool = True) -> None:
        self.held[sid] = asyncio.Semaphore(0)
        if swallow_first_cancel:
            self.swallow.add(sid)

    def allow(self, sid: str, count: int = 1) -> None:
        for _ in range(count):
            self.held[sid].release()

    def open(self, sid: str) -> None:
        semaphore = self.held.pop(sid, None)
        if semaphore is not None:
            for _ in range(10_000):
                semaphore.release()
            self.held[sid] = semaphore  # later emits pass: the semaphore is effectively unbounded

    async def __call__(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            sid_len = frame[5]
            sid = bytes(frame[6:6 + sid_len]).decode()
            record = ("out", sid, bytes(frame[7 + sid_len + frame[6 + sid_len]:]))
        else:
            sid = frame.get("payload", {}).get("terminal_session_id", "")
            record = ("exit", sid, frame["payload"]) if frame.get("type") == "terminal.exit" else ("other", sid, frame)
        self.attempts.append((record[0], sid, record[2] if record[0] == "out" else b""))
        semaphore = self.held.get(sid)
        if semaphore is not None:
            while True:
                try:
                    await semaphore.acquire()
                    break
                except asyncio.CancelledError:
                    if sid in self.swallow:
                        self.swallow.discard(sid)
                        self.swallowed += 1
                        continue
                    raise
        if self.delay:
            await asyncio.sleep(self.delay)
        self.timeline.append(record)

    def text(self, sid: str) -> str:
        return b"".join(r[2] for r in self.timeline if r[0] == "out" and r[1] == sid).decode("utf-8", "replace")

    def raw(self, sid: str) -> bytes:
        return b"".join(r[2] for r in self.timeline if r[0] == "out" and r[1] == sid)

    def exits(self, sid: str | None = None) -> list[dict]:
        return [r[2] for r in self.timeline if r[0] == "exit" and (sid is None or r[1] == sid)]

    def numbers(self, sid: str, prefix: str = "FLOOD ") -> list[int]:
        return [int(line.split()[1]) for line in self.text(sid).splitlines() if line.startswith(prefix) and len(line.split()) > 1]

    def assert_exit_after_last_byte(self, sid: str) -> None:
        """T5: terminal.exit is never emitted before the last output byte of its session."""
        exits = [i for i, r in enumerate(self.timeline) if r[0] == "exit" and r[1] == sid]
        assert len(exits) <= 1, "a second terminal.exit was published"
        if exits:
            assert not [r for r in self.timeline[exits[0] + 1:] if r[0] == "out" and r[1] == sid], \
                "output followed terminal.exit"


class LifeRig:
    def __init__(self, root: Path, monkeypatch, binary: Path = PRODUCT):
        self.root, self.monkeypatch = root, monkeypatch
        self.sink = Sink()
        self.services: list[RustTerminalService] = []
        self.batches: dict[int, list[bytes]] = {}
        self.pids: set[int] = set()
        self.binary = root / "runtime-under-test"
        shutil.copyfile(binary, self.binary)
        self.binary.chmod(0o755)
        monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(self.binary))
        monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(root))
        self.peer = root / "lifecycle_peer.py"
        shutil.copyfile(PEER, self.peer)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.env = isolated_environment(root)

    def service(self) -> RustTerminalService:
        service = RustTerminalService(self.sink)
        mirror = service._mirror_flush_to_log

        def recording_mirror(session, combined: bytes) -> None:
            self.batches.setdefault(session.id, []).append(combined)  # a taken batch, including pieces still behind a blocked emit
            return mirror(session, combined)

        service._mirror_flush_to_log = recording_mirror
        self.services.append(service)
        return service

    def receipts(self) -> list[dict]:
        path = self.root / "runtime-receipts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def receipts_of(self, event: str, **match) -> list[dict]:
        return [r for r in self.receipts() if r["event"] == event and all(r.get(k) == v for k, v in match.items())]

    async def life(self, service, pane: str, *, agent_key: str = "terminal", ready: bool = True, **extra):
        session = await service.create_async(
            pane_id=pane, agent_key=agent_key, command=[base_python_executable(), "-u", str(self.peer)],
            cwd=str(self.workspace), cols=100, rows=30, env=self.env, **extra)
        self.pids.add(session.proc.pid)
        if ready:
            await self.until(lambda: "LIFE_READY " in self.sink.text(session.id), "peer ready")
        return session

    def seen(self, service, session, marker: bytes) -> int:
        """How many times `marker` has been read from the CLI so far: bytes the sink saw or holds
        (taken, blocked in an emit) plus bytes still buffered. Deterministic readiness, not a sleep."""
        taken = b"".join(a[2] for a in self.sink.attempts if a[0] == "out" and a[1] == session.id)
        delivered = self.sink.raw(session.id)
        buffered = b"".join(service._out_buffers.get(session.id, []))
        batched = b"".join(self.batches.get(session.id, []))
        return max(taken, delivered, batched, key=len).count(marker) + buffered.count(marker)

    @staticmethod
    async def until(predicate, message: str, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            await asyncio.sleep(0.01)

    async def say(self, service, session, line: str) -> None:
        await service.write_async(session.id, line + "\r")

    def peer_pid(self, session) -> int:
        return json.loads(self.sink.text(session.id).split("LIFE_READY ")[1].splitlines()[0])["pid"]

    def kill_sidecar(self, service, sig=signal.SIGKILL) -> None:
        identity = service._runtime_identity
        process = psutil.Process(identity["pid"])
        assert process.pid == service._runtime.pid and process.create_time() == identity["created"]
        os.kill(identity["pid"], sig)

    async def recovered(self, service) -> None:
        async with asyncio.timeout(LOSS_BOUND_S + 15):
            while service._invalidation is None:
                await asyncio.sleep(0.01)
            await asyncio.shield(service._invalidation)

    async def close(self):
        for sid in list(self.sink.held):
            self.sink.open(sid)
        for service in self.services:
            runtime = service._runtime
            if runtime is not None and runtime.returncode is None:
                try:
                    self.kill_sidecar(service, signal.SIGCONT)
                    self.kill_sidecar(service)
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
    rig = LifeRig(tmp_path, monkeypatch)
    try:
        yield rig
    finally:
        await rig.close()


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, HARNESS)
    try:
        yield rig
    finally:
        await rig.close()


# ----------------------------------------------------------------------------- T1 / T6 / T5

async def test_t1a_root_exit_with_an_unread_tail_and_a_slow_consumer_loses_nothing(rig):
    service = rig.service()
    session = await rig.life(service, "pane-t1a")
    rig.sink.delay = 0.01  # a slow consumer
    count = 6000  # ~1.2 MB
    await rig.say(service, session, f"flood {count}")
    await rig.say(service, session, "exit")
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit", 60)
    assert rig.sink.numbers(session.id) == list(range(count)), "every byte reached the renderer, in order, once"
    rig.sink.assert_exit_after_last_byte(session.id)
    assert rig.sink.exits(session.id)[0]["reason"] == "exit" and rig.sink.exits(session.id)[0]["exit_code"] == 0


async def test_t1b_forced_interleaving_every_retained_byte_precedes_the_exit_and_the_log_matches(rig):
    service = rig.service()
    log = rig.root / "pipeline.log"
    session = await rig.life(service, "pane-t1b", output_log_file=str(log))
    rig.sink.hold(session.id)
    await rig.say(service, session, "flood 400")  # ~80 KB: a taken batch whose first piece blocks the sink
    await rig.until(lambda: session.id in service._drain_tasks, "a drain in flight")
    # Let the bounded reader pause expire so the reader resumes and absorbs more behind the drain.
    await rig.until(lambda: session.id not in service._pause_timers, "READER_PAUSE_MAX_MS expiry", 5)
    await rig.say(service, session, "flood 200")
    await rig.until(lambda: len(service._out_buffers.get(session.id, [])) > 0 and b"".join(service._out_buffers[session.id]).count(b"FLOOD_DONE") >= 0
                    and service._out_buf_bytes.get(session.id, 0) > 30_000, "output absorbed behind the stalled drain", 20)
    await rig.say(service, session, "closestdio 0.2")  # EOF arrives; the root exits right after
    await rig.until(lambda: session.stream_ending, "EOF reached the stream end", 10)
    rig.sink.open(session.id)
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit", 30)
    text = rig.sink.text(session.id)
    produced = [int(line.split()[1]) for line in text.splitlines() if line.startswith("FLOOD ")]
    assert produced == list(range(400)) + list(range(200)), "no byte lost or repeated across the interleaving"
    rig.sink.assert_exit_after_last_byte(session.id)
    logged = [int(n) for n in re.findall(r"FLOOD (\d{6}) ", log.read_text(encoding="utf-8"))]  # chunk-wise cleaning may join lines
    assert logged == produced, "the log mirror holds the same bytes (no drop, no repeat)"


async def test_t6_eof_during_an_active_resize_barrier_waits_for_the_control_lock_then_publishes_the_tail(harness):
    service = harness.service()
    release = harness.root / "release-resize"
    session = await harness.life(service, f"fault:resize-hold:{release}")
    order: list[str] = []
    original = service._drain_output_behind_barrier

    async def traced(session_):
        order.append(f"drain(control_lock={session_.control_lock.locked()})")
        return await original(session_)

    service._drain_output_behind_barrier = traced

    async def resize():
        async with service.control_async(session.id, 120, 40):
            harness.sink.timeline.append(("ack", session.id))
            order.append("ack")

    task = asyncio.create_task(resize())
    await harness.until(lambda: any(r["event"] == "owned" for r in harness.receipts()) and service._request_number > 2
                        and session.control_lock.locked(), "resize in its round trip")
    await harness.say(service, session, "flood 200")
    await harness.say(service, session, "closestdio 0.2")
    await harness.until(lambda: session.stream_ending, "EOF while the barrier is held", 10)
    assert not harness.sink.exits(session.id), "_finish_stream must wait for the control lock"
    release.touch()
    await asyncio.wait_for(task, 15)
    await harness.until(lambda: harness.sink.exits(session.id), "terminal.exit", 30)
    assert harness.sink.numbers(session.id) == list(range(200))
    kinds = [r[0] for r in harness.sink.timeline if r[1] == session.id]
    assert kinds.index("ack") < kinds.index("exit"), "the resize acknowledged before the exit"
    harness.sink.assert_exit_after_last_byte(session.id)
    assert order.count("ack") == 1 and order.index("ack") < len(order)
    assert [o for o in order if o.startswith("drain")][0].endswith("True)"), "control_lock held, then the barrier, in the resize path"


# ------------------------------------------------------------------------------------ T2, T3

async def test_t2_eof_before_root_exit_publishes_after_both(rig):
    service = rig.service()
    session = await rig.life(service, "pane-t2")
    await rig.say(service, session, "closestdio 0.4")
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit after EOF and root exit", 15)
    exit_ = rig.sink.exits(session.id)[0]
    assert exit_["reason"] == "exit" and exit_["exit_code"] == 0
    assert not rig.receipts_of("root_live"), "the root exited within eof_reap_ms"
    assert rig.receipts_of("exit", session_id=session.id)


async def test_t3_root_live_fence_refuses_creates_then_the_user_path_kills_it_and_a_create_succeeds(rig):
    service = rig.service()
    pane = "pane-t3"
    session = await rig.life(service, pane)
    pid = session.proc.pid
    await rig.say(service, session, "closestdio")  # stdio closed, never exits by itself
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit with the root still alive", 15)
    exit_ = rig.sink.exits(session.id)[0]
    assert exit_["exit_code"] is None and alive(pid)
    assert rig.receipts_of("root_live", session_id=session.id)
    assert session.id in service._retained and not session.cleanup.is_set()
    assert service.live_session_ids_for_pane(pane) == [session.id], "the overridden predicate returns the root-live session"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=rf"CREATE_BLOCKED: the previous CLI of this pane closed its terminal but is still running \(pid {pid},"):
        await rig.life(service, pane, ready=False)
    assert time.monotonic() - started < 2, "refused at once, not after a launch wait"
    # The existing user path: the settlement manual_pane.release_pty runs for Rebuild/close.
    from agent_team_backend import ws_handlers
    await ws_handlers._sweep_pane_ptys(SimpleNamespace(terminals=service), pane)
    assert session.cleanup.is_set() and not session.cleanup_error and not alive(pid)
    cleaned = rig.receipts_of("cleaned", session_id=session.id)[0]
    assert cleaned["cleanup_complete"] is True and cleaned["root_reaped"] is True
    again = await rig.life(service, pane)
    assert again.id != session.id and len(rig.sink.exits(session.id)) == 1, "no second terminal.exit"


async def test_t3_shutdown_variant_kill_all_reaches_a_root_live_session_within_cleanup_ms(rig):
    service = rig.service()
    session = await rig.life(service, "pane-t3s")
    pid = session.proc.pid
    await rig.say(service, session, "closestdio")
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit with the root still alive", 15)
    assert alive(pid)
    started = time.monotonic()
    await service.kill_all()
    assert not alive(pid) and session.cleanup.is_set() and not session.cleanup_error
    assert time.monotonic() - started < service._limits["cleanup_ms"] / 1000, "not a wait for natural exit"
    assert len(rig.sink.exits(session.id)) == 1


# --------------------------------------------------------------------------------------- T4

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only: the kernel revokes the controlling terminal when the session leader exits; the Linux behaviour is test_t4_on_linux_...")
async def test_t4_on_macos_root_exit_always_ends_the_stream_even_with_a_descendant_holding_the_slave(rig):
    """E1 R-d expects no EOF while a descendant holds the slave. Measured on macOS (also for the
    baseline Python spawn, see the checkpoint note): the kernel revokes the controlling terminal when
    its session leader exits, so EOF follows the root's exit regardless. This records that behaviour."""
    service = rig.service()
    session = await rig.life(service, "pane-t4")
    await rig.say(service, session, "child")
    await rig.until(lambda: "CHILD " in rig.sink.text(session.id), "descendant started")
    child = json.loads(rig.sink.text(session.id).split("CHILD ")[1].splitlines()[0])["pid"]
    rig.pids.add(child)
    await rig.say(service, session, "exit")
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit after the root's exit", 15)
    rig.sink.assert_exit_after_last_byte(session.id)
    await rig.until(lambda: session.cleanup.is_set(), "cleanup after EOF", 15)
    assert not alive(child) and not session.cleanup_error


@pytest.mark.skipif(sys.platform != "linux", reason="Linux counterpart of the macOS T4 case")
async def test_t4_on_linux_a_descendant_holding_the_slave_survives_the_root_and_is_reported(rig):
    """Linux hangs up the foreground group when the session leader exits, so a plain descendant dies
    with it; one that ignores SIGHUP keeps the slave open (E1 R-d). The sidecar never signals it by a
    reusable id (ticket 03): the stream stays open, and a kill ends the session with an incomplete
    cleanup that names the descendant, which is still running."""
    service = rig.service()
    session = await rig.life(service, "pane-t4l")
    await rig.say(service, session, "child-nohup")
    await rig.until(lambda: "CHILD " in rig.sink.text(session.id), "descendant started")
    child = json.loads(rig.sink.text(session.id).split("CHILD ")[1].splitlines()[0])["pid"]
    rig.pids.add(child)
    await rig.say(service, session, "exit")
    await asyncio.sleep(2.0)  # a functional hold: the stream must stay open while the slave is held
    assert not rig.sink.exits(session.id), "no EOF while a descendant holds the slave"
    assert not session.cleanup.is_set() and alive(child), "the descendant was not signalled by the root's exit"
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED: native cleanup incomplete"):
        await service.kill(session.id, force=True)
    cleaned = rig.receipts_of("cleaned", session_id=session.id)[0]
    assert cleaned["cleanup_complete"] is False and cleaned["root_reaped"] is True, cleaned
    assert [s["pid"] for s in cleaned["survivors"]] == [child], "the receipt names the descendant it did not signal"
    assert alive(child)


async def test_t4_kill_cleans_a_recorded_descendant_by_identity(rig):
    service = rig.service()
    session = await rig.life(service, "pane-t4k")
    await rig.say(service, session, "detach")
    await rig.until(lambda: "DETACHED " in rig.sink.text(session.id), "detached descendant started")
    detached = json.loads(rig.sink.text(session.id).split("DETACHED ")[1].splitlines()[0])["pid"]
    rig.pids.add(detached)
    await rig.until(lambda: len(session.descendants) >= 1, "descendant recorded by the ownership snapshot", 30)
    await service.kill(session.id, force=True)
    assert not alive(detached) and not alive(session.proc.pid) and not session.cleanup_error
    assert rig.receipts_of("cleaned", session_id=session.id)[0]["survivors"] == []


async def test_t4_after_eof_the_remaining_descendants_are_swept_by_identity(rig):
    service = rig.service()
    session = await rig.life(service, "pane-t4b")
    await rig.say(service, session, "detach")  # a descendant in its own session: not holding the slave
    await rig.until(lambda: "DETACHED " in rig.sink.text(session.id), "detached descendant started")
    detached = json.loads(rig.sink.text(session.id).split("DETACHED ")[1].splitlines()[0])["pid"]
    rig.pids.add(detached)
    await rig.until(lambda: len(session.descendants) >= 1, "descendant recorded by the ownership snapshot", 30)
    await rig.say(service, session, "closestdio 0.2")
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit", 15)
    await rig.until(lambda: session.cleanup.is_set(), "cleanup after EOF", 15)
    assert not alive(detached) and not session.cleanup_error, "the remaining descendant was swept after EOF"


# --------------------------------------------------------------------------- S2, S3 (kill policy)

async def test_s2_a_term_trapping_cli_escalates_and_a_plain_one_completes_without_escalation(rig):
    service = rig.service()
    stubborn = await rig.life(service, "pane-s2a")
    plain = await rig.life(service, "pane-s2b")
    await rig.say(service, stubborn, "stubborn")  # ignores SIGTERM and SIGHUP: only the escalation ends it
    await rig.until(lambda: "STUBBORN" in rig.sink.text(stubborn.id), "trap armed")
    await asyncio.gather(service.kill(stubborn.id), service.kill(plain.id))
    await rig.until(lambda: rig.sink.exits(stubborn.id) and rig.sink.exits(plain.id), "both exits", 15)
    assert rig.receipts_of("exit", session_id=stubborn.id)[0]["exit_code"] == -signal.SIGKILL, "the root was reaped only after the escalation"
    assert not alive(stubborn.proc.pid)
    assert rig.sink.exits(plain.id)[0]["signal"] in ("SIGTERM", "SIGHUP"), "a plain CLI never needed the escalation"
    for session in (stubborn, plain):
        assert rig.receipts_of("cleaned", session_id=session.id)[0]["cleanup_complete"] is True


def test_s3_a_declared_graceful_fixture_defers_the_master_close_without_a_vendor_key(tmp_path):
    """The declaration (a test declaration `fault-graceful`, not a vendor name) drives the policy: KILL
    sends no SESSION_HANGUP for it and does for an undeclared agent. Raw peer: the service refuses
    non-integrated agent keys before the sidecar is reached."""
    from tests.cli_regression.support.frame_peer import FramePeer
    from agent_team_backend.cli_runtime_frames import code as wire_code
    peer = FramePeer(tmp_path, binary=HARNESS)
    try:
        script = tmp_path / "lifecycle_peer.py"
        shutil.copyfile(PEER, script)
        argv = [base_python_executable(), "-u", str(script)]
        graceful = peer.create(argv, agent_key="fault-graceful")
        ordinary = peer.create(argv, agent_key="")
        for sid, generation in (graceful, ordinary):
            peer.result(peer.send(wire_code.KILL, {"session_id": sid, "session_generation": generation, "force": False}))
        peer.wait_for(lambda: len([e for e in peer.events if e.code == wire_code.SESSION_CLEANED]) == 2, "both cleaned", 20)
        hung = {e.meta["session_id"] for e in peer.events if e.code == wire_code.SESSION_HANGUP}
        assert graceful[0] not in hung, "declared deferred master close: no SESSION_HANGUP"
        assert ordinary[0] in hung, "an undeclared agent gets the hangup"
        assert all(e.meta["cleanup_complete"] for e in peer.events if e.code == wire_code.SESSION_CLEANED)
    finally:
        peer.close()


# ------------------------------------------------------------------------------ resize R1-R6

async def test_r1_b1_quiescent_all_old_width_lines_precede_the_acknowledgement(rig):
    service = rig.service()
    session = await rig.life(service, "pane-r1")
    done = rig.root / "marker-written"
    await rig.say(service, session, "numbered 60")
    await rig.say(service, session, f"marker-file {done}")
    await rig.until(lambda: done.exists() and "MARKED" in rig.sink.text(session.id), "marker written, peer idle")
    async with service.control_async(session.id, 120, 40):
        rig.sink.timeline.append(("ack", session.id))
    kinds = [(r[0], r[2] if r[0] == "out" else None) for r in rig.sink.timeline if r[1] == session.id]
    ack = kinds.index(("ack", None))
    before = b"".join(data for kind, data in kinds[:ack] if kind == "out").decode()
    assert "MARKER" in before and before.count("width=100") == 60, "every old-width line through the marker precedes the ack"
    await rig.say(service, session, "size")
    await rig.until(lambda: "SIZE [120, 40]" in rig.sink.text(session.id), "new size in effect")
    assert b"\x1b[3J" not in rig.sink.raw(session.id) and b"\x1bc" not in rig.sink.raw(session.id), "scrollback never cleared"


class DropCounter(dict):
    """`_out_dropped` with a running total of everything ever added (the drop accounting)."""
    total = 0

    def __setitem__(self, key, value):
        if value > self.get(key, 0):
            DropCounter.total += value - self.get(key, 0)
        super().__setitem__(key, value)


@pytest.mark.parametrize("slow", [False, True])
async def test_r2_flood_during_resize_preserves_order_accounts_drops_and_emits_late_bytes_after_the_ack(rig, slow):
    service = rig.service()
    session = await rig.life(service, f"pane-r2-{slow}")
    DropCounter.total = 0
    service._out_dropped = DropCounter(service._out_dropped)
    reads = {"total": 0}
    inner = session.handle.read

    def counting_read(count):
        data = inner(count)
        reads["total"] += len(data or b"")
        return data

    session.handle.read = counting_read
    slurp: dict[str, int] = {}
    original = service._drain_output_behind_barrier

    async def traced(session_):
        await original(session_)
        slurp["read_total"] = reads["total"]

    service._drain_output_behind_barrier = traced
    base = len(rig.sink.raw(session.id))
    if slow:
        rig.sink.delay = 0.02
    count = 5000 if slow else 2500
    await rig.say(service, session, f"flood {count}")
    async with asyncio.timeout(service._limits["request_ms"] / 1000):
        async with service.control_async(session.id, 130, 45):
            before_ack = len(rig.sink.raw(session.id)) - base
            rig.sink.timeline.append(("ack", session.id))
    await rig.until(lambda: "FLOOD_DONE" in rig.sink.text(session.id), "flood delivered", 90)
    numbers = rig.sink.numbers(session.id)
    assert numbers == sorted(set(numbers)), "order preserved, no byte emitted twice"
    produced = (count * len(f"FLOOD {0:06d} " + "x" * 200 + "\r\n") + len("FLOOD_DONE\r\n")
                + len(f"flood {count}\r\n"))  # the tty also echoes the typed command line
    delivered = len(rig.sink.raw(session.id)) - base
    assert delivered + DropCounter.total == produced, "every gap equals the reported drop accounting"
    assert before_ack <= slurp["read_total"], "B2: no byte first read after the slurp was emitted before the ack"
    await rig.say(service, session, "size")
    await rig.until(lambda: "SIZE [130, 45]" in rig.sink.text(session.id), "resize completed")


async def test_r3_resize_for_a_finished_session_is_not_applied_and_does_not_hang(rig):
    service = rig.service()
    session = await rig.life(service, "pane-r3")
    await service.kill(session.id, force=True)
    await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit", 15)
    async with asyncio.timeout(10):
        answer = await service._request("resize", {"cols": 90, "rows": 30, "redraw": False}, session)
    assert answer == {"applied": False, "steps": 0}


async def test_r4_redraw_applies_two_size_changes_and_concurrent_resizes_serialize(rig):
    """`redraw:true` applies rows-1 then rows (`steps:2`). Whether a CLI sees two SIGWINCH deliveries is
    not the kernel's guarantee (pending signals coalesce when the two calls are back to back, as in the
    baseline `force_redraw`), so the CLI-side counts are recorded as evidence, not asserted as two."""
    service = rig.service()
    session = await rig.life(service, "pane-r4")
    await rig.say(service, session, "winch")
    await rig.until(lambda: "WINCH_ARMED" in rig.sink.text(session.id), "SIGWINCH handler armed")
    answers = []
    original = service._request

    async def keep(op, args=None, session_=None, **kw):
        result = await original(op, args, session_, **kw)
        if op == "resize":
            answers.append(result)
        return result

    service._request = keep
    for index in range(20):
        async with service.control_async(session.id, 110, 40 + index % 2, redraw=True):
            pass
    assert all(a == {"applied": True, "steps": 2} for a in answers) and len(answers) == 20, "two size changes each time"
    await rig.until(lambda: rig.sink.text(session.id).count("WINCH ") >= 1, "at least one SIGWINCH delivery", 10)
    sizes = [json.loads(line.split("WINCH ")[1]) for line in rig.sink.text(session.id).splitlines() if line.startswith("WINCH ")]
    (rig.root / "r4-winch-observations.json").write_text(json.dumps({"redraws": 20, "deliveries": len(sizes), "distinct_sizes": sorted({tuple(x) for x in sizes})}))
    assert sizes[-1][0] == 110 and sizes[-1][1] in (40, 41), "the CLI reads the final size"
    acks: list[int] = []

    async def resize(number: int, cols: int):
        async with service.control_async(session.id, cols, 40):
            acks.append(number)
            await asyncio.sleep(0)  # still inside the lock: the other resize cannot be in its round trip

    await asyncio.gather(resize(1, 111), resize(2, 112), resize(3, 113))
    assert acks == [1, 2, 3], "concurrent resizes on one session serialize"


async def test_r4_resizes_on_different_sessions_run_independently(harness):
    service = harness.service()
    release = harness.root / "release-r4"
    held = await harness.life(service, f"fault:resize-hold:{release}")
    free = await harness.life(service, "pane-r4-free")
    pending = asyncio.create_task(service._request("resize", {"cols": 90, "rows": 30, "redraw": False}, held))
    await harness.until(lambda: service._request_number > 3 and not pending.done(), "the held resize is in its round trip")
    async with asyncio.timeout(5):
        async with service.control_async(free.id, 95, 35):
            pass  # the other session's resize completes while the first is held
    assert not pending.done()
    release.touch()
    assert (await asyncio.wait_for(pending, 10))["applied"] is True


async def test_r5_barrier_mechanics_lock_held_across_slurp_resize_ack_and_tail(harness):
    service = harness.service()
    release = harness.root / "release-r5"
    session = await harness.life(service, f"fault:resize-hold:{release}")
    events: list[tuple] = []
    barrier_locked = lambda: service._resize_barriers[session.id].locked()  # noqa: E731

    async def traced_drain(session_, original=service._drain_output_behind_barrier):
        events.append(("slurp", barrier_locked(), session_.control_lock.locked()))
        return await original(session_)

    service._drain_output_behind_barrier = traced_drain
    request = service._request

    async def traced_request(op, args=None, session_=None, **kw):
        if op == "resize":
            events.append(("resize-sent", barrier_locked()))
        try:
            return await request(op, args, session_, **kw)
        finally:
            if op == "resize":
                events.append(("resize-answered", barrier_locked()))

    service._request = traced_request
    service.drain_output = lambda *a, **k: (_ for _ in ()).throw(AssertionError("drain_output is not used on this path"))
    harness.sink.delay = 0.02  # a slow sender
    task_done = asyncio.Event()

    async def resize():
        async with service.control_async(session.id, 120, 40):
            events.append(("ack", barrier_locked()))
            harness.sink.timeline.append(("ack", session.id))
        events.append(("released", barrier_locked()))
        task_done.set()

    task = asyncio.create_task(resize())
    await harness.until(lambda: any(e[0] == "resize-sent" for e in events), "RESIZE sent")
    await harness.say(service, session, "flood 800")  # bytes absorbed during the round trip
    await harness.until(lambda: service._out_buf_bytes.get(session.id, 0) > 50_000, "bytes absorbed under the held lock", 20)
    assert not [r for r in harness.sink.timeline if r[0] == "out" and b"FLOOD 0000" in r[2]], "nothing emitted while the lock is held"
    release.touch()
    async with asyncio.timeout(60):
        await task_done.wait()
    await task
    assert events[0] == ("slurp", True, True), "the drain ran with the barrier held by control_async, after control_lock"
    assert ("resize-sent", True) in events and ("resize-answered", True) in events
    assert ("ack", True) in events and ("released", False) in events
    order = [e[0] for e in events]
    assert order.index("slurp") < order.index("resize-sent") < order.index("resize-answered") < order.index("ack") < order.index("released")
    await harness.until(lambda: "FLOOD_DONE" in harness.sink.text(session.id), "tail flushed after the lock was released", 60)
    kinds = [r[0] for r in harness.sink.timeline if r[1] == session.id]
    first_flood = next(i for i, r in enumerate(harness.sink.timeline) if r[0] == "out" and b"FLOOD" in r[2])
    assert harness.sink.timeline.index(("ack", session.id)) < first_flood, "bytes absorbed during the round trip follow the ack"
    assert kinds.count("ack") == 1


async def test_r6_a_partial_redraw_reports_the_first_step_and_is_never_retried(harness):
    service = harness.service()
    session = await harness.life(service, "fault:resize-partial")
    sent: list[str] = []
    request = service._request

    async def counted(op, args=None, session_=None, **kw):
        sent.append(op)
        return await request(op, args, session_, **kw)

    service._request = counted
    with pytest.raises(cli_runtime._RuntimeRefused) as caught:
        async with service.control_async(session.id, 100, 40, redraw=True):
            raise AssertionError("the barrier body must not run when RESIZE failed")
    assert caught.value.meta["code"] == "IO_FAILED" and caught.value.meta["effect"] == "partial" and caught.value.meta["steps"] == 1
    assert sent.count("resize") == 1, "Python reports the failure and does not retry"


# ---------------------------------------------------------------- L: loss (S6, L1, L2, L4, L7, L8)

async def test_s6_kill_accepted_then_an_incomplete_cleanup_is_a_fence_and_loss(harness):
    service = harness.service()
    session = await harness.life(service, "fault:survivor")
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
        await service.kill(session.id, force=True)
    generation = service._runtime_generation
    await harness.recovered(service)
    cleaned = harness.receipts_of("cleaned", session_id=session.id)[0]
    assert cleaned["cleanup_complete"] is False and cleaned["survivors"]
    invalidated = harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert "CLEANUP_FAILED" in invalidated["cause"], "the incomplete cleanup ended the generation"


async def test_l1_sidecar_sigkill_cleans_every_session_by_identity_and_presents_one_exit_each(rig):
    service = rig.service()
    unrelated = psutil.Popen([base_python_executable(), "-c", "import time; time.sleep(120)"])
    rig.pids.add(unrelated.pid)
    shell = await rig.life(service, "pane-l1-shell")
    agent = await rig.life(service, "pane-l1-agent", agent_key="fault-graceful") if False else await rig.life(service, "pane-l1-agent")
    plain = await rig.life(service, "pane-l1-plain")
    rootlive = await rig.life(service, "pane-l1-rootlive")
    await rig.say(service, shell, "detach")
    await rig.until(lambda: "DETACHED " in rig.sink.text(shell.id), "detached descendant")
    detached = json.loads(rig.sink.text(shell.id).split("DETACHED ")[1].splitlines()[0])["pid"]
    rig.pids.add(detached)
    await rig.until(lambda: len(shell.descendants) >= 1, "descendant snapshot", 30)
    await rig.say(service, rootlive, "closestdio")
    await rig.until(lambda: rig.sink.exits(rootlive.id), "root-live exit published", 15)
    watched = [s.proc.pid for s in (shell, agent, plain, rootlive)] + [detached]
    generation = service._runtime_generation
    rig.kill_sidecar(service)
    await rig.recovered(service)
    assert not [pid for pid in watched if alive(pid)], "no live session left unmanaged"
    assert alive(unrelated.pid)
    for session in (shell, agent, plain):
        exits = rig.sink.exits(session.id)
        assert len(exits) == 1 and exits[0]["reason"] == "runtime_lost" and exits[0]["exit_code"] is None
    assert len(rig.sink.exits(rootlive.id)) == 1 and rig.sink.exits(rootlive.id)[0]["reason"] != "runtime_lost", "not published twice"
    invalidated = rig.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert invalidated["complete"] is True and invalidated["sessions"] == 3
    fresh = await rig.life(service, "pane-l1-fresh")
    assert service._runtime_generation != generation and fresh.id not in {s.id for s in (shell, agent, plain)}


async def test_l2_a_wedged_sidecar_fails_the_request_then_the_unanswered_ping_is_loss(rig):
    service = rig.service()
    stuck = await rig.life(service, "pane-l2-a")
    other = await rig.life(service, "pane-l2-b")
    service._limits = {**service._limits, "request_ms": 600, "ping_ms": 600}  # shortened for the test only
    generation = service._runtime_generation
    rig.kill_sidecar(service, signal.SIGSTOP)
    with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
        await service._request("resize", {"cols": 80, "rows": 24, "redraw": False}, stuck)
    assert not stuck.closed and not other.closed and service._lost is None, "the request alone failed; nothing is lost yet"
    await rig.recovered(service)
    invalidated = rig.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert "no answer to PING" in invalidated["cause"] and invalidated["complete"] is True
    assert len(rig.sink.exits(stuck.id)) == len(rig.sink.exits(other.id)) == 1


async def test_l4_sidecar_killed_after_the_journal_append_is_cleaned_from_the_journal(harness):
    service = harness.service()
    task = asyncio.create_task(service.create_async(
        pane_id="fault:suppress-owned-and-response", agent_key="terminal", cwd=str(harness.workspace), env=harness.env,
        command=[base_python_executable(), "-u", str(harness.peer)]))
    generation = None
    await harness.until(lambda: service._runtime is not None and service._runtime_generation, "sidecar started")
    generation = service._runtime_generation
    journal = Path(pty_journal(service))
    await harness.until(lambda: journal.exists() and '"pid"' in journal.read_text(), "journal record durable", 15)
    record = next(json.loads(line) for line in journal.read_text().splitlines() if '"pid"' in line)
    pid = record["pid"]
    harness.pids.add(pid)
    harness.kill_sidecar(service)
    with pytest.raises(Exception):  # the create never gets an answer, success or otherwise
        await asyncio.wait_for(task, LOSS_BOUND_S)
    await harness.recovered(service)
    assert not alive(pid), "the journal named the root even though no event reached Python"
    assert harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]["complete"] is True


def pty_journal(service) -> str:
    from agent_team_backend.applog import in_data_dir
    return str(in_data_dir(service._data_dir, pty_registry.journal_path, service._runtime_generation)())


async def test_l7_stale_events_of_a_retired_generation_cannot_act_on_the_replacement(rig):
    service = rig.service()
    session = await rig.life(service, "pane-l7")
    old_generation = service._runtime_generation
    old_runtime = service._runtime
    rig.kill_sidecar(service)
    await rig.recovered(service)
    assert old_runtime.returncode is not None, "the old sidecar is gone before any replacement starts"
    fresh = await rig.life(service, "pane-l7-fresh")
    assert service._runtime_generation != old_generation and old_generation in service._retired
    from agent_team_backend import cli_runtime_frames as wire
    stale = wire.encode_raw(wire.FrameType.EVENT, wire.code.SESSION_EXITED, 0, 0, wire.runtime_tag(old_generation), b"{}", b"")
    with pytest.raises(wire.ProtocolError) as caught:
        wire.parse_header(stale[:32], wire.Direction.RUST_TO_PYTHON, wire.runtime_tag(service._runtime_generation))
    assert caught.value.rule == "runtime_tag"
    assert not fresh.closed and fresh.id in service._sessions and session.id not in service._sessions


async def test_l8_f3_principal_a_corrupt_frame_from_the_sidecar_ends_in_loss_and_recovery(harness):
    service = harness.service()
    bystander = await harness.life(service, "pane-l8-bystander")
    generation = service._runtime_generation
    with pytest.raises(Exception):  # the create itself loses its answer to the generation loss
        await harness.life(service, "fault:corrupt-frame", ready=False)
    await harness.recovered(service)
    invalidated = harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert "PROTOCOL_ERROR" in invalidated["cause"] and invalidated["complete"] is True
    assert len(harness.sink.exits(bystander.id)) == 1 and harness.sink.exits(bystander.id)[0]["reason"] == "runtime_lost"
    fresh = await harness.life(service, "pane-l8-fresh")
    assert fresh.id in service._sessions and service._runtime_generation != generation


# --------------------------------------- L1b-L1f: loss order, bounds, delivery and close-once

async def two_batch_session(rig, service, pane, **extra):
    """A session with a taken multi-piece batch blocked in the sink and more output buffered behind it."""
    session = await rig.life(service, pane, **extra)
    rig.sink.hold(session.id)
    await rig.say(service, session, "flood 500")  # ~100 KB: the taken batch has two pieces
    await rig.until(lambda: session.id in service._drain_tasks, "a taken batch in flight")
    await rig.until(lambda: rig.seen(service, session, b"FLOOD_DONE") >= 1, "the first flood fully read", 20)
    await rig.until(lambda: session.id not in service._pause_timers, "reader resumed after the bounded pause", 5)
    await rig.say(service, session, "flood 100")
    await rig.until(lambda: rig.seen(service, session, b"FLOOD_DONE") >= 2, "the second flood fully read, buffered behind the batch", 20)
    return session


async def test_l1b_loss_order_and_bounds_with_a_wedged_sink(rig):
    service = rig.service()
    logs = {name: rig.root / f"{name}.log" for name in ("a", "b")}
    first = await two_batch_session(rig, service, "pane-l1b-a", output_log_file=str(logs["a"]))
    second = await two_batch_session(rig, service, "pane-l1b-b", output_log_file=str(logs["b"]))
    untaken = {s.id: sum(len(c) for c in service._out_buffers[s.id]) for s in (first, second)}
    assert all(untaken.values()), "output is buffered behind each taken batch"
    taken_log = {name: logs[name].read_text(encoding="utf-8") for name in logs}  # mirrored at take time
    events: list[str] = []
    original_close = MasterHandle.close

    def traced_close(self):
        events.append("close")
        return original_close(self)

    rig.monkeypatch.setattr(MasterHandle, "close", traced_close)
    original_quiesce = service._quiesce_stream

    async def traced_quiesce(session):
        events.append("step3")
        return await original_quiesce(session)

    service._quiesce_stream = traced_quiesce
    generation = service._runtime_generation
    started = time.monotonic()
    rig.kill_sidecar(service)
    await rig.recovered(service)
    assert time.monotonic() - started <= LOSS_BOUND_S, "the whole sequence honours the E1 11 bound"
    assert events.count("close") >= 2 and events.index("step3") > max(i for i, e in enumerate(events) if e == "close"), \
        "steps 1-2 (every Python handle closed) completed before the first await of step 3"
    settled = {r["session_id"]: r for r in rig.receipts_of("loss_settled")}
    for session, name in ((first, "a"), (second, "b")):
        assert settled[session.id]["in_flight_unknown"] is True and settled[session.id]["untaken_dropped"] == untaken[session.id]
        assert len(rig.receipts_of("exit_published", session_id=session.id)) == 1, "queued once per session"
        text = logs[name].read_text(encoding="utf-8")
        assert text.startswith(taken_log[name]), "the taken batch was mirrored at take time and is not rewritten"
        assert text.count(f"[navide: {untaken[session.id]} bytes of output dropped]") == 1, "marked once with the existing marker"
        assert "[navide:" not in taken_log[name]
    assert rig.sink.swallowed >= 1, "the sink really swallowed a cancellation"
    assert rig.receipts_of("runtime_invalidated", lost_generation=generation)[0]["complete"] is True
    # Variant: the sink is released after the bound. The taken batch may finish after the exit; nothing twice.
    for session in (first, second):
        rig.sink.open(session.id)
    await rig.until(lambda: all(rig.sink.exits(s.id) for s in (first, second)), "the exits finally reach the sink", 20)
    for session in (first, second):
        numbers = rig.sink.numbers(session.id)
        assert numbers == sorted(set(numbers)), "no byte delivered twice"
        assert set(numbers) <= set(range(500)), "no byte outside the already-taken batch follows"


async def test_l1b_variant_a_sink_released_inside_the_bound_delivers_everything_in_order_without_a_marker(rig):
    service = rig.service()
    log = rig.root / "inside.log"
    session = await two_batch_session(rig, service, "pane-l1b-inside", output_log_file=str(log))
    rig.kill_sidecar(service)
    await rig.until(lambda: service._lost, "loss detected")
    rig.sink.open(session.id)  # released inside LOSS_FLUSH_S
    await rig.recovered(service)
    await rig.until(lambda: rig.sink.exits(session.id), "exit", 20)
    assert rig.sink.numbers(session.id) == list(range(500)) + list(range(100)), "every byte, in order, none twice"
    assert "bytes of output dropped" not in log.read_text(encoding="utf-8"), "no marker when nothing was cut"
    assert rig.receipts_of("loss_settled", session_id=session.id)[0]["untaken_dropped"] == 0


async def test_l1c_loss_after_partial_delivery_never_resends_the_in_flight_piece(rig):
    service = rig.service()
    log = rig.root / "partial.log"
    session = await rig.life(service, "pane-l1c", output_log_file=str(log))
    already = rig.sink.raw(session.id)  # LIFE_READY etc., delivered before the sink is gated
    rig.sink.hold(session.id)
    rig.sink.allow(session.id, 1)  # the first piece of the taken batch is accepted, then the sink blocks
    await rig.say(service, session, "flood 800")  # ~172 KB: three pieces
    await rig.until(lambda: len(rig.sink.exits(session.id)) == 0 and len(rig.sink.raw(session.id)) > 0 and session.id in service._drain_tasks,
                    "first piece delivered, the second blocked")
    await rig.until(lambda: rig.seen(service, session, b"FLOOD_DONE") >= 1, "the flood fully read", 20)
    delivered_before = rig.sink.raw(session.id)[len(already):]
    await rig.until(lambda: session.id not in service._pause_timers, "reader resumed", 5)
    await rig.say(service, session, "flood 50")
    await rig.until(lambda: rig.seen(service, session, b"FLOOD_DONE") >= 2, "output buffered behind the batch", 20)
    untaken = sum(len(c) for c in service._out_buffers[session.id])
    taken_log = log.read_text(encoding="utf-8")
    rig.kill_sidecar(service)
    await rig.recovered(service)
    receipt = rig.receipts_of("loss_settled", session_id=session.id)[0]
    assert receipt["in_flight_unknown"] is True and receipt["untaken_dropped"] == untaken
    at_cutoff = rig.sink.raw(session.id)
    assert at_cutoff[len(already):] == delivered_before, "at the cutoff the renderer had exactly the delivered leading piece(s)"
    stream = b"flood 800\r\n" + b"".join(f"FLOOD {n:06d} ".encode() + b"x" * 200 + b"\r\n" for n in range(800))
    assert delivered_before == stream[:len(delivered_before)] and 0 < len(delivered_before) < len(stream), "in order, a strict prefix of the batch"
    text = log.read_text(encoding="utf-8")
    assert delivered_before.decode().splitlines()[1] in taken_log, "what the sink accepted was mirrored at take time"
    assert text.startswith(taken_log) and text.count("[navide:") == 1, "the taken batch, then one drop marker"
    assert text.count(f"[navide: {untaken} bytes of output dropped]") == 1
    rig.sink.open(session.id)  # the sink completes the in-flight piece later
    await rig.until(lambda: rig.sink.exits(session.id), "exit queued", 20)
    numbers = rig.sink.numbers(session.id)
    assert numbers == sorted(set(numbers)) and set(numbers) <= set(range(800)), "no piece ever sent twice"


async def test_l1d_a_split_multibyte_character_at_the_loss_cut_matches_the_baseline_log_path(rig):
    prefix, tail_ok = "世".encode()[:2], "世".encode()[2:]

    def reference(calls: list[bytes]) -> str:
        """The unmodified baseline TerminalService mirror path replayed with the same mirrored sequence:
        one persistent decoder per session, the tail flushed into the log before it closes."""
        service = TerminalService(lambda frame: asyncio.sleep(0))
        fp = io.StringIO()
        sess = SimpleNamespace(id="ref", output_log_fp=fp)
        for data in calls:
            service._mirror_to_log(sess, data)
        decoder = service._decoders.pop("ref")
        if tail := decoder.decode(b"", final=True):
            fp.write(_clean_for_log(tail))
        return fp.getvalue()

    recorded: dict[str, list[bytes]] = {}
    original = RustTerminalService._mirror_to_log

    def recording(self, session_, data):
        recorded.setdefault(session_.id, []).append(bytes(data))
        return original(self, session_, data)

    rig.monkeypatch.setattr(RustTerminalService, "_mirror_to_log", recording)
    for cut in (True, False):
        service = rig.service()
        log = rig.root / f"split-{cut}.log"
        session = await rig.life(service, f"pane-l1d-{cut}", output_log_file=str(log))
        calls = recorded.setdefault(session.id, [])
        await rig.say(service, session, "noecho")  # so the continuation bytes follow the prefix directly
        await rig.until(lambda: "NOECHO" in rig.sink.text(session.id), "echo off")
        rig.sink.hold(session.id)
        await rig.say(service, session, "rawhex " + (b"A" * 100 + prefix).hex())
        await rig.until(lambda: session.id in service._drain_tasks and any(b"A" * 100 + prefix in c for c in calls), "the first batch is taken and mirrored at take")
        await rig.until(lambda: session.id not in service._pause_timers, "reader resumed", 5)
        await rig.say(service, session, "rawhex " + (tail_ok + b"B" * 10).hex())
        await rig.until(lambda: b"B" * 10 in b"".join(service._out_buffers.get(session.id, [])), "continuation buffered, untaken")
        dropped_expected = sum(len(c) for c in service._out_buffers[session.id])
        decoder = service._decoders[session.id]
        rig.kill_sidecar(service)
        if not cut:
            await rig.until(lambda: service._lost, "loss detected")
            rig.sink.open(session.id)  # released inside the bound
        await rig.recovered(service)
        assert service._decoders.get(session.id) is None and decoder is not None, "the one decoder outlived every log write"
        dropped = rig.receipts_of("loss_settled", session_id=session.id)[0]["untaken_dropped"]
        assert dropped == (dropped_expected if cut else 0)
        text = log.read_text(encoding="utf-8")
        assert text == reference(calls), f"cut={cut}: the log differs from the baseline mirror path fed the same bytes"
        assert ("bytes of output dropped" in text) is cut
        assert ("世" in text) is (not cut), "the intact character only when the continuation was delivered"
        if cut:
            assert "\ufffd" in text, "the dangling prefix is what the baseline decoder produces for it"
        rig.sink.open(session.id)


def open_until_number(number: int) -> list[int]:
    """Open descriptors until the OS hands out `number`; returns every one opened (the last is it)."""
    opened = []
    while True:
        fd = os.open("/dev/null", os.O_RDWR)
        opened.append(fd)
        if fd == number or fd > number or len(opened) > 4096:
            return opened


async def test_l1e_close_once_a_retained_stream_closed_session_never_closes_a_reused_number(rig):
    service = rig.service()
    session = await rig.life(service, "pane-l1e")
    freed = session.handle.fd
    await rig.say(service, session, "closestdio")
    await rig.until(lambda: rig.sink.exits(session.id) and session.handle.fd is None, "root-live: the stream closed normally", 15)
    assert session.id in service._retained and not session.cleanup.is_set()
    opened = open_until_number(freed)
    try:
        assert opened[-1] == freed, "the OS reused the freed number for an unrelated resource"
        for fd in opened[:-1]:
            os.close(fd)
        unrelated = freed
        rig.kill_sidecar(service)
        await rig.recovered(service)
        os.fstat(unrelated)  # still open
        assert os.write(unrelated, b"x") == 1, "the unrelated resource is still usable"
        assert len(rig.sink.exits(session.id)) == 1, "no second exit for the root-live session"
        assert rig.receipts_of("runtime_invalidated")[0]["sessions"] == 0
        assert not alive(session.proc.pid), "identity cleanup of the retained root proceeded"
    finally:
        os.close(freed)


async def test_l1f_a_late_normal_closer_cannot_close_a_reused_number_or_publish_after_loss(rig):
    service = rig.service()
    session = await rig.life(service, "pane-l1f")
    freed = session.handle.fd
    sent: list[str] = []
    request = service._request

    async def traced(op, args=None, session_=None, **kw):
        sent.append(op)
        return await request(op, args, session_, **kw)

    service._request = traced
    rig.sink.hold(session.id)
    await rig.say(service, session, "flood 300")
    await rig.until(lambda: session.id in service._drain_tasks, "first batch taken, blocked")
    await rig.until(lambda: session.id not in service._pause_timers, "reader resumed", 5)
    await rig.say(service, session, "flood 50")
    await rig.until(lambda: service._out_buf_bytes.get(session.id, 0) >= 50 * 200, "more output buffered")
    await rig.say(service, session, "closestdio 0.1")
    await rig.until(lambda: session.stream_ending, "EOF: _finish_stream is running")
    rig.sink.allow(session.id, 1)  # the first batch completes; _finish_stream now emits inline and blocks again
    await rig.until(lambda: len([a for a in rig.sink.attempts if a[0] == "out" and a[1] == session.id]) >= 3, "_finish_stream suspended inside the helper's emit", 15)
    rig.kill_sidecar(service)
    await rig.until(lambda: service._lost, "loss detected")
    assert session.handle.fd is None, "loss step 2 closed the handle"
    opened = open_until_number(freed)
    try:
        assert opened[-1] == freed, "the OS reused the freed number"
        for fd in opened[:-1]:
            os.close(fd)
        before_ops = list(sent)
        rig.sink.open(session.id)  # the emitter resumes
        await rig.recovered(service)
        os.fstat(freed)
        assert os.write(freed, b"x") == 1, "the resumed closer did not touch the reused number"
        assert "tty_eof" not in sent[len(before_ops):], "no TTY_EOF to the retired sidecar"
        exits = rig.sink.exits(session.id)
        assert len(exits) == 1 and exits[0]["reason"] == "runtime_lost", "exactly one exit, the loss one"
        assert len(rig.receipts_of("exit_published", session_id=session.id)) == 1
    finally:
        os.close(freed)


# ------------------------------------------------------------------------ S9 isolation

async def test_s9_unrelated_panes_survive_one_sessions_request_expiry_and_input_does_not_wait_on_output(harness):
    service = harness.service()
    release = harness.root / "release-s9"
    stuck = await harness.life(service, f"fault:resize-hold:{release}")
    other = await harness.life(service, "pane-s9-other")
    service._limits = {**service._limits, "request_ms": 500, "ping_ms": 5000}  # shortened for the test only
    harness.sink.hold(stuck.id)  # session A's output is stalled at the sink
    await harness.say(service, stuck, "flood 400")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
        await service._request("resize", {"cols": 80, "rows": 24, "redraw": False}, stuck)
    await harness.say(service, other, "after expiry")  # input to B with A stalled and A's request just expired
    await harness.until(lambda: "ECHO after expiry" in harness.sink.text(other.id), "B answers while A is stalled")
    assert time.monotonic() - started < 10
    assert service._lost is None and not other.closed and not stuck.closed, "the expiry failed only that request"
    release.touch()
    harness.sink.open(stuck.id)
    ping = await service._request("ping")
    assert ping["pong"] is True
