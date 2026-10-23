"""Review 1 of E3 checkpoint 1: one red-first proof per accepted finding (evidence/e3/reviewer-e3-1.md).

Each test is written to fail against the checkpoint-1 code and pass after the fix; the closure table
of checkpoint 2 maps finding -> test. Real sidecar (product or harness build) wherever the behaviour is
the sidecar's, the production `RustTerminalService` everywhere, a stub sidecar only to put protocol
violations the real one never emits onto the production receiver.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from agent_team_backend import cli_runtime, pty_registry
from agent_team_backend import cli_runtime_frames as wire
from agent_team_backend.cli_runtime import RuntimeProcessView, RuntimeSession, RustTerminalService
from agent_team_backend.cli_runtime_frames import FrameType, code
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT, FramePeer
from tests.test_cli_runtime_lifecycle import LifeRig, alive
from tests.test_cli_runtime_observe import STUB_LIMITS

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built macOS native runtime (product and harness)"),
]

GENERATION = "00112233445566778899aabbccddeeff"
TAG = wire.runtime_tag(GENERATION)
HOLD = """
import os, signal, sys, time
signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
path = sys.argv[1]
open(path + ".tmp", "w").write(str(os.getpid()))
os.replace(path + ".tmp", path)
time.sleep(120)
"""


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, HARNESS)
    try:
        yield rig
    finally:
        await rig.close()


def receipts_of(root: Path, event: str, **match) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [r for r in records if r["event"] == event and all(r.get(k) == v for k, v in match.items())]


def stub_service(tmp_path, monkeypatch, mode: str, **limits) -> RustTerminalService:
    stub = tmp_path / f"stub-{mode}"
    stub.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(Path(__file__).parent / 'cli_regression/support/stub_sidecar.py')!r}, run_name='__main__')\n")
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    monkeypatch.setenv("NAVIDE_STUB_MODE", mode)
    monkeypatch.setenv("NAVIDE_STUB_BACKEND", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("NAVIDE_STUB_LIMITS", json.dumps({**STUB_LIMITS, **limits}))
    return RustTerminalService(lambda frame: asyncio.sleep(0))


async def wait_for(predicate, message: str, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.01)


def kill_stub(service) -> None:
    if service._runtime is not None and service._runtime.returncode is None:
        service._runtime.kill()


async def stop_stub(service) -> None:
    """End the stub sidecar and wait for the loss it causes to finish, so no task of this service outlives its test."""
    kill_stub(service)
    deadline = time.monotonic() + 30
    while service._runtime is not None and service._runtime.returncode is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    invalidation = getattr(service, "_invalidation", None)
    if invalidation is not None:
        with suppress(Exception):
            await asyncio.wait_for(asyncio.shield(invalidation), 30)


# ------------------------------------------------------------------------------ B1: fast exit

async def test_b1_a_root_that_exits_before_the_create_answer_still_delivers_its_output_ends_its_stream_and_publishes_its_exit(harness):
    """E1 5.2 I3: SESSION_EXITED may precede the CREATE answer. The unpublished record must not close its own
    stream (or the reader that attaches at publication is suppressed and `_finish_stream` never runs), and the
    CLI's output must survive the wait: the slot reads its master from the claim (v8 8 item 4, finding F-1)."""
    service = harness.service()
    release = harness.root / "release-answer"
    task = asyncio.create_task(service.create_async(
        pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(harness.workspace),
        env=harness.env, cols=100, rows=30,
        command=[base_python_executable(), "-c", "import sys; sys.stdout.write('FASTOUT\\n'); sys.stdout.flush()"]))
    await wait_for(lambda: receipts_of(harness.root, "exit"), "SESSION_EXITED arrived before the answer")
    assert not task.done(), "the answer is still held"
    await asyncio.sleep(1.0)  # past the kernel's ~0.6 s drain window: only a reader on the slot keeps the output
    release.touch()
    session = await asyncio.wait_for(task, 20)
    await wait_for(lambda: harness.sink.exits(session.id), "terminal.exit after the stream ended", 20)
    assert "FASTOUT" in harness.sink.text(session.id), "the CLI's output was lost while its create was unpublished"
    exit_ = harness.sink.exits(session.id)
    assert len(exit_) == 1 and exit_[0]["reason"] == "exit" and exit_[0]["exit_code"] == 0
    harness.sink.assert_exit_after_last_byte(session.id)
    await wait_for(session.cleanup.is_set, "verified cleanup after the stream ended", 20)
    assert not session.cleanup_error and session.handle.fd is None


# ------------------------------------------------------------ B2: SESSION_OWNED identity checks

def owned_world(tmp_path, monkeypatch, *, tty_master=True):
    """A service with one launch in flight (create id 7) and a real PTY master to hand it."""
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    service._runtime_generation = GENERATION
    service._data_dir = tmp_path
    sid = "session-under-test"
    args = {"session_id": sid, "session_generation": "a" * 32, "pane_id": "pane-x", "workspace_path": "/work/space",
            "launch": {"shape": "argv", "argv": ["/bin/cat"]}, "cwd": "/work/space"}
    service._launches[sid] = args
    service._owned[sid] = asyncio.get_running_loop().create_future()
    service._dispatched[sid] = {"generation": GENERATION, "identity": None, "request_id": 7}
    service._provisional[sid] = lambda root: RuntimeSession(
        sid, "pane-x", "terminal", ["/bin/cat"], "/work/space", RuntimeProcessView(root["pid"]), "a" * 32, {}, root,
        published=False)
    persisted = []

    async def persist(record):
        persisted.append(record)

    monkeypatch.setattr(service, "_persist_owned", persist)
    master, slave = os.openpty()
    from agent_team_backend.cli_runtime_handoff import slave_name_of
    meta = {"create_id": 7, "session_id": sid, "session_generation": "a" * 32, "pane_id": "pane-x",
            "workspace_path": "/work/space", "root": {"pid": 4242, "pgid": 4242, "start_kind": "posix-lstart",
                                                      "start_value": "Mon Jan  1 00:00:00 2026"},
            "pty": {"tty": slave_name_of(master)},
            "argv_source": "python", "argv0": {"len": 8, "head": "/bin/cat", "tail": "/bin/cat"}}
    return service, sid, meta, master, slave, persisted


def owned_frame(meta: dict) -> wire.Frame:
    return wire.Frame(FrameType.EVENT, code.SESSION_OWNED, wire.FLAG_HAS_FD, 0, TAG, meta, b"", 200)


def is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


async def test_b2_a_matching_owned_event_is_accepted_control(tmp_path, monkeypatch):
    service, sid, meta, master, slave, persisted = owned_world(tmp_path, monkeypatch)
    try:
        service._on_frame(owned_frame(meta), master)
        assert service._slots[sid].fd == master and sid in service._retained
        await asyncio.sleep(0)
        assert len(persisted) == 1
    finally:
        os.close(slave)
        service._close_slot(sid)


@pytest.mark.parametrize("field,value", [
    ("create_id", 8), ("create_id", None), ("session_generation", "b" * 32), ("pane_id", "pane-other"),
    ("workspace_path", "/elsewhere"), ("root", "not-an-object"), ("root", {"pid": "4242"}),
    ("root", {"pid": 0, "pgid": 0, "start_kind": "posix-lstart", "start_value": "x"}), ("pty", None),
    ("session_id", "never-created"),
])
async def test_b2_owned_with_a_wrong_identity_or_shape_is_a_protocol_error_before_anything_is_claimed(
        tmp_path, monkeypatch, field, value):
    service, sid, meta, master, slave, persisted = owned_world(tmp_path, monkeypatch)
    meta = {**meta, field: value}
    try:
        with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
            service._on_frame(owned_frame(meta), master)
        assert not is_open(master), "the received descriptor is closed on rejection"
        assert not service._slots and not service._claimed_ttys, "nothing was claimed"
        assert sid not in service._retained and not persisted and not service._event_tasks, "nothing was registered or persisted"
    finally:
        os.close(slave)


async def test_b2_a_second_owned_for_the_same_session_and_one_after_prevented_are_protocol_errors(tmp_path, monkeypatch):
    service, sid, meta, master, slave, _ = owned_world(tmp_path, monkeypatch)
    try:
        service._on_frame(owned_frame(meta), master)
        second, extra = os.openpty()
        with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
            service._on_frame(owned_frame(meta), second)
        assert not is_open(second)
        os.close(extra)
        service._close_slot(sid)
        service._prevented = getattr(service, "_prevented", set())
        service._prevented.add(sid)  # E1 10.1: a later SESSION_OWNED after `prevented` is a protocol error
        third, extra = os.openpty()
        with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
            service._on_frame(owned_frame(meta), third)
        assert not is_open(third)
        os.close(extra)
    finally:
        os.close(slave)
        service._close_slot(sid)


# --------------------------------------------------------- B3: answers match code and id

def pending_world(op_code: int, request_id: int = 5):
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    service._runtime_generation = GENERATION
    future = asyncio.get_running_loop().create_future()
    service._pending[request_id] = future
    service._request_codes = getattr(service, "_request_codes", {})  # the table this fix introduces
    service._request_codes[request_id] = op_code
    service._request_number = request_id
    return service, future


def answer(frame_type: FrameType, request_code: int, request_id: int, meta: dict | None = None) -> wire.Frame:
    return wire.Frame(frame_type, request_code, 0, request_id, TAG, meta or {}, b"")


async def test_b3_an_answer_with_the_wrong_code_is_a_protocol_error_for_response_and_error(tmp_path):
    for frame_type in (FrameType.RESPONSE, FrameType.ERROR):
        service, future = pending_world(code.KILL)
        with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
            service._on_frame(answer(frame_type, code.PING, 5), None)
        assert not future.done(), "a wrong-code answer must not settle the request"


async def test_b3_unsolicited_and_duplicate_answers_are_protocol_errors(tmp_path):
    service, future = pending_world(code.PING)
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
        service._on_frame(answer(FrameType.RESPONSE, code.PING, 99), None)  # an id nobody asked
    service._on_frame(answer(FrameType.RESPONSE, code.PING, 5, {"pong": True}), None)  # the real answer
    assert future.done()
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
        service._on_frame(answer(FrameType.RESPONSE, code.PING, 5, {"pong": True}), None)  # the same id again


async def test_b3_end_to_end_a_wrong_code_answer_ends_the_generation(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "wrong-code")
    generation = None
    try:
        await service._ensure_started()
        generation = service._runtime_generation
        with pytest.raises(RuntimeError):
            await service._request("ping")
        await wait_for(lambda: receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation), "loss ran")
        cause = receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation)[0]["cause"]
        assert "PROTOCOL_ERROR" in cause
    finally:
        await stop_stub(service)


async def test_b3_a_late_answer_to_an_expired_request_is_tolerated_once(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "late-answer", ping_ms=300)
    try:
        await service._ensure_started()
        with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
            await service._request("ping")
        await asyncio.sleep(1.4)  # the stub answers the expired ping after ~1 s
        assert service._lost is None, "an answer to a request that merely expired is not a protocol error"
    finally:
        await stop_stub(service)


# --------------------------------------------------- B4/B5: create answer deadline and the single rule

async def test_b4_a_create_waits_create_answer_ms_not_request_ms_and_then_the_generation_is_lost(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "create-silent", request_ms=300, create_answer_ms=1500, ping_ms=300)
    kwargs = dict(pane_id="pane-silent", agent_key="terminal", command=["/bin/cat"], cwd=str(tmp_path))
    try:
        started = time.monotonic()
        task = asyncio.create_task(service.create_async(**kwargs))
        await wait_for(lambda: service._dispatched, "create dispatched")
        await asyncio.sleep(0.7)
        assert not task.done(), "a compliant create is abandoned at request_ms instead of create_answer_ms"
        with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
            await asyncio.wait_for(task, 10)
        assert time.monotonic() - started >= 1.4
        generation = service._runtime_generation
        await wait_for(lambda: receipts_of(tmp_path, "abandoned_create"), "the resolver ran")
        assert receipts_of(tmp_path, "abandoned_create")[0]["outcome"] == "runtime-loss", \
            "no OWNED, no prevented proof, no answer: that is runtime loss, not `unanswered`"
        await wait_for(lambda: receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation), "generation retired")
    finally:
        await stop_stub(service)


async def test_b5_a_prevented_cancel_settles_the_lost_create_without_a_generation_loss(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "create-prevented", request_ms=300, create_answer_ms=20_000, ping_ms=2000)
    kwargs = dict(pane_id="pane-prevented", agent_key="terminal", command=["/bin/cat"], cwd=str(tmp_path))
    try:
        task = asyncio.create_task(service.create_async(**kwargs))
        await wait_for(lambda: service._dispatched, "create dispatched")
        sid = next(iter(service._dispatched))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_for(lambda: receipts_of(tmp_path, "abandoned_create"), "the resolver settled", 10)
        assert receipts_of(tmp_path, "abandoned_create")[0]["outcome"] == "prevented"
        generation = service._runtime_generation  # receipts share a path: only this service's generation counts
        assert service._lost is None and not receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation), \
            "no generation loss on a proof"
        assert not service._unresolved, "the pane and resume keys are released"
        assert sid in service._prevented
        assert (await service._request("ping"))["pong"] is True
        create_id = receipts_of(tmp_path, "abandoned_create")[0]["create_id"]
        with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):  # a create settled `prevented` cannot succeed later
            service._on_frame(answer(FrameType.RESPONSE, code.CREATE, create_id, {"state": "owned"}), None)
    finally:
        await stop_stub(service)


# ------------------------------------------------------------------------ B6: inbound bytes

async def test_b6_python_admits_requests_against_the_inbound_byte_budget(tmp_path):
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    loop = asyncio.get_running_loop()
    parent, child = socket.socketpair()
    parent.setblocking(False)
    child.setblocking(False)
    service._sock = parent
    service._runtime_generation = GENERATION
    service._limits = {**service._limits, "max_inbound_bytes": 1_000_000, "request_ms": 20_000}
    received = {"bytes": 0}

    async def drain():  # the sidecar's socket: reads, never answers
        while True:
            chunk = await loop.sock_recv(child, 1 << 16)
            if not chunk:
                return
            received["bytes"] += len(chunk)

    reader = asyncio.create_task(drain())
    blob = b"x" * 300_000
    tasks = [asyncio.create_task(service._request("ping", blob=blob)) for _ in range(5)]
    await asyncio.sleep(0.5)
    in_flight = len([t for t in tasks if not t.done()])
    assert in_flight == 5
    assert received["bytes"] <= 1_000_000, f"{received['bytes']} request bytes were sent against a 1 MB budget"
    assert received["bytes"] >= 2 * 300_000, "the budget must admit what fits"
    for request_id, future in list(service._pending.items()):  # answer what was sent, one by one
        if not future.done():
            service._on_frame(answer(FrameType.RESPONSE, code.PING, request_id, {"pong": True}), None)
        await asyncio.sleep(0.05)
    for _ in range(5):
        await asyncio.sleep(0.1)
        for request_id, future in list(service._pending.items()):
            if not future.done():
                service._on_frame(answer(FrameType.RESPONSE, code.PING, request_id, {"pong": True}), None)
    results = await asyncio.gather(*tasks)
    assert all(r["pong"] is True for r in results), "backpressure never drops a request"
    assert received["bytes"] >= 5 * 300_000
    reader.cancel()
    parent.close()
    child.close()


def test_b6_the_sidecar_enforces_its_inbound_byte_budget_against_a_non_conforming_sender(tmp_path):
    """E1 1.4: the sidecar's inbound queue holds at most max_inbound_bytes (16 MiB). Six 3 MiB CREATEs held
    at the ownership gate (journal FIFO) are 18 MiB: the sidecar must stop reading, not buffer them."""
    import threading
    peer = FramePeer(tmp_path)
    try:
        peer.journal.unlink(missing_ok=True)
        os.mkfifo(peer.journal)
        ids: list[int] = []

        def sender():
            for index in range(6):
                meta = {"session_id": f"s-{index}", "session_generation": f"{index:032d}", "pane_id": f"p{index}",
                        "workspace_path": str(tmp_path), "launch": {"shape": "direct", "form": "list", "elements": ["/usr/bin/true"], "words": []}, "cwd": str(tmp_path),
                        "env": {"PATH": "/usr/bin:/bin", "PAD": "x" * 3_000_000}, "cols": 80, "rows": 24, "agent_key": ""}
                ids.append(peer.send(code.CREATE, meta))

        thread = threading.Thread(target=sender, daemon=True)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "the sender must be held back by backpressure (18 MiB cannot all be admitted)"
        reader = os.open(peer.journal, os.O_RDONLY | os.O_NONBLOCK)  # lets every held create proceed
        try:
            thread.join(timeout=60)
            assert not thread.is_alive() and len(ids) == 6
            for request in ids:
                peer.result(request, allow_error=True, timeout=60)
        finally:
            os.close(reader)
        bookkeeping = peer.result(peer.send(code.PING, {})).meta["bookkeeping"]
        assert 3_000_000 < bookkeeping["inbound_peak_bytes"] <= 16 * 1024 * 1024, bookkeeping
    finally:
        peer.close()


# ------------------------------------------------------ B7: exit publication vs retirement

def lost_world(tmp_path):
    frames: list = []

    async def emit(frame):
        frames.append(frame)

    service = RustTerminalService(emit)
    service._runtime_generation = GENERATION
    service._data_dir = tmp_path
    session = RuntimeSession("closed-one", "pane", "terminal", [], str(tmp_path), RuntimeProcessView(101),
                             "g" * 32, {}, {})
    session.published = session.closed = session.stream_done = True
    session.stream_reason = "eof"
    session.exit_record = {"reason": "exit", "exit_code": 0, "uptime_ms": 5}
    session.started_monotonic = time.monotonic()
    master, slave = os.openpty()
    os.close(slave)
    from agent_team_backend.cli_runtime_handoff import MasterHandle
    session.handle = MasterHandle(master, session.proc)
    service._sessions[session.id] = service._retained[session.id] = session
    service._ready_ok = True
    return service, session, frames


async def test_b7_an_exit_claimed_by_the_normal_path_but_not_yet_queued_is_presented_once_by_loss(tmp_path):
    service, session, frames = lost_world(tmp_path)
    service._maybe_publish_exit(session, GENERATION)  # claims the exit and schedules the publication task: it has not run yet
    service._retire_generation()          # loss step 1: cancels that task before its first await
    await service._invalidate("RUNTIME_LOST: test")
    exits = [f for f in frames if not isinstance(f, (bytes, bytearray)) and f.get("type") == "terminal.exit"]
    assert len(exits) == 1, f"{len(exits)} terminal.exit frames"
    assert exits[0]["payload"]["reason"] == "runtime_lost" and exits[0]["payload"]["terminal_session_id"] == session.id


async def test_b7_a_normal_publication_in_flight_at_loss_is_neither_repeated_nor_mutated_after_retirement(tmp_path):
    service, session, frames = lost_world(tmp_path)
    gate = asyncio.Event()
    swallowed = []

    async def stuck_emit(frame):
        while True:
            try:
                await gate.wait()
                break
            except asyncio.CancelledError:
                swallowed.append(True)  # a wedged sink swallows the first cancellation
                if len(swallowed) > 1:
                    raise
        frames.append(frame)

    service._emit = stuck_emit
    service._maybe_publish_exit(session, GENERATION)
    await wait_for(lambda: session.exit_published and getattr(session, "exit_started", True) and not frames, "publication in flight")
    await asyncio.sleep(0.05)
    service._retire_generation()
    await asyncio.wait_for(service._invalidate("RUNTIME_LOST: test"), 20)
    gate.set()
    await asyncio.sleep(0.2)
    exits = [f for f in frames if f.get("type") == "terminal.exit"]
    assert len(exits) == 1, f"{len(exits)} exits: one session must publish exactly once"
    assert session.id not in service._sessions or service._retired_generation(GENERATION)


# ------------------------------------------------------------ B8: loss stays bounded

async def test_b8_loss_is_bounded_by_task_drain_when_an_event_task_refuses_to_be_cancelled(harness):
    service = harness.service()
    session = await harness.life(service, "pane-b8")

    stop = asyncio.Event()

    async def stubborn():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 3600)
            except asyncio.CancelledError:
                continue  # a cancellation-resistant emitter

    stuck = service._task(stubborn())
    started = time.monotonic()
    harness.kill_sidecar(service)
    await harness.recovered(service)
    elapsed = time.monotonic() - started
    assert elapsed < cli_runtime.TASK_DRAIN_S + cli_runtime.SIDECAR_EXIT_S + cli_runtime.LOSS_FLUSH_S + 10, elapsed
    assert len(harness.sink.exits(session.id)) == 1 and harness.sink.exits(session.id)[0]["reason"] == "runtime_lost"
    assert not stuck.done(), "the stubborn task really ignored its cancellation"
    stop.set()
    await asyncio.wait_for(stuck, 5)


async def test_b8_the_loss_receipt_carries_every_stage_transition(harness):
    service = harness.service()
    await harness.life(service, "pane-stages")
    generation = service._runtime_generation
    harness.kill_sidecar(service)
    await harness.recovered(service)
    receipt = harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    names = [s["stage"] for s in receipt["stages"]]
    assert names[:2] == ["retired", "handles_closed"] and names[-1] == "presented"
    for needed in ("tasks_settled", "sidecar_exited", "registry_writes_settled", "journal_read", "reaped", "streams_settled"):
        assert needed in names, (needed, names)
    assert all(isinstance(s["elapsed_ms"], int) for s in receipt["stages"])
    assert [s["elapsed_ms"] for s in receipt["stages"]] == sorted(s["elapsed_ms"] for s in receipt["stages"])


# ------------------------------------------------- B10: post-effect failure after the gate opened

def journalled_pid(service) -> int:
    """The root's pid as the sidecar's own durable journal recorded it before it released the gate."""
    from agent_team_backend.applog import in_data_dir
    journal = Path(str(in_data_dir(service._data_dir, pty_registry.journal_path, service._runtime_generation)()))
    return next(json.loads(line)["pid"] for line in journal.read_text().splitlines() if '"pid"' in line)


@pytest.fixture
def hup_immune(monkeypatch):
    """The sidecar (and so every gated child) is started with SIGHUP ignored: the dispositions are
    inherited across exec. Without this a child dies of the SIGHUP that closing the master sends,
    which would make a missing kill/reap invisible."""
    import signal
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    yield
    signal.signal(signal.SIGHUP, previous)


async def test_b10_an_exit_watcher_failure_after_gate_release_kills_and_reaps_before_answering(harness, hup_immune):
    service = harness.service()
    pid_file = harness.root / "spawned.pid"
    with pytest.raises(cli_runtime._RuntimeRefused) as caught:
        await service.create_async(pane_id="fault:watch-exit-fail", agent_key="terminal", cwd=str(harness.workspace),
                                   env=harness.env, command=[base_python_executable(), "-c", HOLD, str(pid_file)])
    pid = journalled_pid(service)
    harness.pids.add(pid)
    assert caught.value.meta["code"] == "SPAWN_FAILED" and caught.value.meta["effect"] == "none"
    assert not alive(pid), "effect:none was claimed but the gated child that ran is still alive"
    assert service._lost is None and not harness.receipts_of("owned")
    assert (await service._request("ping"))["pong"] is True


async def test_b10_a_failure_whose_reap_cannot_be_verified_is_cleanup_failed_and_ends_the_generation(harness, hup_immune):
    service = harness.service()
    pid_file = harness.root / "spawned-noreap.pid"
    with pytest.raises(cli_runtime._RuntimeRefused) as caught:
        await service.create_async(pane_id="fault:watch-exit-fail-noreap", agent_key="terminal",
                                   cwd=str(harness.workspace), env=harness.env,
                                   command=[base_python_executable(), "-c", HOLD, str(pid_file)])
    pid = journalled_pid(service)
    harness.pids.add(pid)
    assert caught.value.meta["code"] == "CLEANUP_FAILED" and caught.value.meta["effect"] == "partial"
    generation = service._runtime_generation
    await harness.recovered(service)
    assert harness.receipts_of("runtime_invalidated", lost_generation=generation)
    assert not alive(pid), "the journal-based loss cleanup ended the unverifiable child"


async def test_b10_a_session_thread_that_cannot_start_kills_and_reaps_before_answering(harness, hup_immune):
    service = harness.service()
    pid_file = harness.root / "spawned-thread.pid"
    with pytest.raises(cli_runtime._RuntimeRefused) as caught:
        await service.create_async(pane_id="fault:thread-start-fail", agent_key="terminal", cwd=str(harness.workspace),
                                   env=harness.env, command=[base_python_executable(), "-c", HOLD, str(pid_file)])
    pid = journalled_pid(service)
    harness.pids.add(pid)
    assert caught.value.meta["code"] == "SPAWN_FAILED" and caught.value.meta["effect"] == "none"
    assert not alive(pid)
    assert service._lost is None and not harness.receipts_of("owned")
    assert (await service._request("ping"))["pong"] is True


# ---------------------------------------------------------------------------------- B9: the PING liveness probe

async def test_b9_a_request_that_expires_while_the_sidecar_still_answers_ping_fails_only_that_request(harness):
    service = harness.service()
    release = harness.root / "release-b9-held"
    held = await harness.life(service, f"fault:resize-hold:{release}")
    other = await harness.life(service, "pane-b9-other")
    service._limits = {**service._limits, "request_ms": 400, "ping_ms": 3000}  # shortened for the test only
    with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
        await service._request("resize", {"cols": 90, "rows": 30, "redraw": False}, held)
    await asyncio.sleep(0.5)  # the one PING after the expiry is answered
    assert service._lost is None and not held.closed and not other.closed, "a live sidecar is never declared lost by one slow request"
    assert not harness.receipts_of("runtime_invalidated")
    release.touch()
    assert (await service._request("ping"))["pong"] is True


async def test_b9_an_unanswered_ping_after_a_request_expiry_is_runtime_loss(harness):
    import signal
    service = harness.service()
    stuck = await harness.life(service, "pane-b9-stuck")
    service._limits = {**service._limits, "request_ms": 400, "ping_ms": 400}  # shortened for the test only
    generation = service._runtime_generation
    harness.kill_sidecar(service, signal.SIGSTOP)  # a wedged sidecar: no request, no PING is ever answered
    with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
        await service._request("resize", {"cols": 80, "rows": 24, "redraw": False}, stuck)
    await harness.recovered(service)
    invalidated = harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert "no answer to PING" in invalidated["cause"] and invalidated["complete"] is True
    assert len(harness.sink.exits(stuck.id)) == 1 and harness.sink.exits(stuck.id)[0]["reason"] == "runtime_lost"
