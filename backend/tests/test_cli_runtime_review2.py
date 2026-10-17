"""Review 2 of E3 checkpoint 2 (evidence/e3/reviewer-e3-2.md): one proof per accepted finding.

Test names carry the finding id (`test_sp1_…`, `test_s2_…`) so a receipt line maps to a finding without a table.
SP-1 / SP-2 / SP-3 / SP-5 prove the E1 v8 text (design/e1-runtime-protocol-v8.md, "Changes from v7"); the real sidecar
is used wherever the behaviour is the sidecar's, a stub sidecar only to put frames the real one never sends onto the
production receiver.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from agent_team_backend import cli_runtime
from agent_team_backend import cli_runtime_frames as wire
from agent_team_backend.cli_runtime import RuntimeProcessView, RuntimeSession, RustTerminalService
from agent_team_backend.cli_runtime_frames import FrameType, code
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT, FramePeer
from tests.test_cli_runtime_lifecycle import LifeRig, alive
from tests.test_cli_runtime_review1 import GENERATION, TAG, receipts_of, stub_service, stop_stub, wait_for

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built macOS native runtime (product and harness)"),
]


def new_service(**limits) -> RustTerminalService:
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    service._runtime_generation = GENERATION
    service._limits = {**service._limits, **limits}
    return service


def part_frame(cid: int, number: int, blob: bytes = b"{}") -> wire.Frame:
    return wire.Frame(FrameType.EVENT, code.OBSERVATION_PART, 0, cid, TAG, {"part": number}, blob, 100 + len(blob))


def answer_frame(request_code: int, cid: int, meta: dict | None = None) -> wire.Frame:
    return wire.Frame(FrameType.RESPONSE, request_code, 0, cid, TAG, meta or {}, b"", 100)


def expired_observe(service: RustTerminalService, cid: int = 7, parts: int = 0, size: int = 0) -> None:
    """An OBSERVE the caller gave up on (request deadline or cancellation) after `parts` parts / `size` bytes arrived."""
    service._request_codes[cid] = code.OBSERVE
    service._observing[cid] = [parts, size]
    service._pending[cid] = asyncio.get_running_loop().create_future()
    service._forget_request(cid)


# ======================================================================================= SP-1 (E1 v8 14, 10.1)

async def test_sp1_late_parts_of_an_expired_observe_are_ignored_counted_and_never_buffered():
    service = new_service()
    expired_observe(service)
    for number in range(5):
        service._on_frame(part_frame(7, number, b"x" * 1000), None)
    ledger = service._ledger.snapshot()
    assert ledger["late_observation_parts"] == 5 and ledger["late_observation_bytes"] == 5000
    assert not service._observations and service._lost is None, "ignored: nothing buffered, no loss"


async def test_sp1_a_late_final_answer_is_tolerated_once_and_a_part_after_it_is_a_protocol_error():
    service = new_service()
    expired_observe(service)
    service._on_frame(part_frame(7, 0), None)
    service._on_frame(answer_frame(code.OBSERVE, 7, {"observation_parts": 1}), None)  # ignored, counted
    assert service._ledger.snapshot()["late_answers"] == 1 and 7 not in service._expired
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):  # a part after the final answer
        service._on_frame(part_frame(7, 1), None)
    expired_observe(service, cid=8)
    service._on_frame(answer_frame(code.OBSERVE, 8, {"observation_parts": 0}), None)
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):  # a second final answer
        service._on_frame(answer_frame(code.OBSERVE, 8, {"observation_parts": 0}), None)


async def test_sp1_late_parts_stay_under_the_open_observations_order_and_counters():
    service = new_service(max_observation_parts=128, max_observation_bytes=100_000)
    expired_observe(service, cid=1, parts=2, size=0)
    with pytest.raises(RuntimeError, match="observation order"):  # the next part is 2, not 5
        service._on_frame(part_frame(1, 5), None)
    service = new_service(max_observation_parts=128, max_observation_bytes=100_000)
    expired_observe(service, cid=1, parts=127, size=0)
    service._on_frame(part_frame(1, 127), None)  # the 128th part: at the bound
    with pytest.raises(RuntimeError, match="advertised bounds"):  # the 129th: an endless stream ends here
        service._on_frame(part_frame(1, 128), None)
    service = new_service(max_observation_parts=128, max_observation_bytes=100_000)
    expired_observe(service, cid=1, parts=3, size=90_000)  # bytes seen before the expiry count too
    service._on_frame(part_frame(1, 3, b"y" * 10_000), None)
    with pytest.raises(RuntimeError, match="advertised bounds"):
        service._on_frame(part_frame(1, 4, b"y"), None)


async def test_sp1_a_part_for_an_id_that_was_never_opened_answered_or_not_an_observe_is_a_protocol_error():
    service = new_service()
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):  # never opened
        service._on_frame(part_frame(99, 0), None)
    service = new_service()
    service._request_codes[5] = code.PING  # an open PING: not an OBSERVE
    service._pending[5] = asyncio.get_running_loop().create_future()
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
        service._on_frame(part_frame(5, 0), None)
    service = new_service()
    service._request_codes[6] = code.PING  # a PING that expired: still not an OBSERVE
    service._pending[6] = asyncio.get_running_loop().create_future()
    service._forget_request(6)
    with pytest.raises(RuntimeError, match="not an OBSERVE"):
        service._on_frame(part_frame(6, 0), None)
    service = new_service()
    service._request_codes[8] = code.OBSERVE  # an OBSERVE answered normally: a later part is after the final answer
    service._observing[8] = [0, 0]
    future = asyncio.get_running_loop().create_future()
    service._pending[8] = future
    service._on_frame(answer_frame(code.OBSERVE, 8, {"observation_parts": 0}), None)
    service._forget_request(8)  # answered: nothing is remembered
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):
        service._on_frame(part_frame(8, 0), None)


async def test_sp5_the_expired_id_tolerance_is_bounded_to_the_newest_ids():
    service = new_service()
    for cid in range(1, cli_runtime.EXPIRED_IDS + 31):
        expired_observe(service, cid=cid)
    assert len(service._expired) == cli_runtime.EXPIRED_IDS == 1024 and len(service._late_counters) <= 1024
    newest = cli_runtime.EXPIRED_IDS + 30
    service._on_frame(part_frame(newest, 0), None)  # inside the bound: tolerated
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR"):  # evicted: no longer remembered, so a protocol error
        service._on_frame(part_frame(1, 0), None)


async def test_sp1_end_to_end_an_observation_that_expires_while_the_sidecar_keeps_sending_does_not_end_the_generation(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "late-parts", request_ms=300, ping_ms=3000)
    try:
        with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
            await service.observe("codex", "p", {"path": str(tmp_path / "f"), "mode": "usage"}, match=lambda sessions: None)
        await wait_for(lambda: service._ledger.snapshot()["late_answers"] == 1, "the late parts and the final answer arrived", 15)
        ledger = service._ledger.snapshot()
        assert ledger["late_observation_parts"] == 3 and service._lost is None and not service._observations
        assert (await service._request("ping"))["pong"] is True, "the runtime is alive and unaffected"
    finally:
        await stop_stub(service)


async def test_sp1_end_to_end_an_endless_late_stream_still_ends_in_loss_at_the_counters(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "late-parts-endless", request_ms=300, ping_ms=3000, max_observation_parts=16)
    try:
        with pytest.raises(RuntimeError, match="REQUEST_TIMEOUT"):
            await service.observe("codex", "p", {"path": str(tmp_path / "f"), "mode": "usage"}, match=lambda sessions: None)
        generation = service._runtime_generation
        await wait_for(lambda: receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation), "loss ran", 20)
        assert "advertised bounds" in receipts_of(tmp_path, "runtime_invalidated", lost_generation=generation)[0]["cause"]
        assert service._ledger.snapshot()["late_observation_parts"] == 16, "ignored up to the bound, the 17th ends the generation"
    finally:
        await stop_stub(service)


# ===================================================================================== SP-2 (E1 v8 4 rule 2)

async def test_sp2_a_root_live_event_is_attributed_by_session_and_generation_only():
    service = new_service()
    session = RuntimeSession("s-root-live", "pane", "terminal", [], "/work", RuntimeProcessView(11), "g" * 32, {}, {})
    service._retained[session.id] = session
    event = lambda generation, **extra: wire.Frame(FrameType.EVENT, code.SESSION_ROOT_LIVE, 0, 0, TAG,  # noqa: E731
                                                  {"session_id": session.id, "session_generation": generation, **extra}, b"", 100)
    with pytest.raises(RuntimeError, match="wrong session generation"):
        service._on_frame(event("h" * 32), None)
    service._on_frame(event("g" * 32), None)  # the event carries no pane or workspace (5.5): none is demanded
    assert session.root_live is True
    with pytest.raises(RuntimeError, match="wrong routing attribution"):  # every other session event still carries and is checked
        service._on_frame(wire.Frame(FrameType.EVENT, code.SESSION_HANGUP, 0, 0, TAG, {"session_id": session.id, "session_generation": "g" * 32,
                                                                                         "pane_id": "elsewhere", "workspace_path": "/work"}, b"", 100), None)


# ============================================================================== SP-3 (E1 v8 5.5 race item 6)

def eof(peer: FramePeer, sid: str, generation: str):
    return peer.result(peer.send(code.TTY_EOF, {"session_id": sid, "session_generation": generation, "reason": "eof"}), allow_error=True, timeout=30)


async def test_sp3_a_late_tty_eof_for_a_session_evicted_from_the_registry_is_still_accepted(tmp_path):
    peer = FramePeer(tmp_path)
    try:
        total = 260  # the registry keeps 256 settled sessions; the first ones leave it
        made: list[tuple[str, str]] = []
        for index in range(total):
            made.append(peer.create(["/usr/bin/true"], pane=f"p{index}"))
            sid, generation = made[-1]
            if sid in peer.masters:
                os.close(peer.masters.pop(sid))
            assert eof(peer, sid, generation).meta["accepted"] is True
        peer.wait_for(lambda: len([e for e in peer.events if e.code == code.SESSION_CLEANED]) == total, "every session cleaned", 120)
        bookkeeping = peer.result(peer.send(code.PING, {})).meta["bookkeeping"]
        assert bookkeeping["settled_sessions"] == 256, "the registry really evicted the oldest settled sessions"
        first_sid, first_generation = made[0]
        late = eof(peer, first_sid, first_generation)
        assert late.frame_type is FrameType.RESPONSE and late.meta == {"accepted": True}, "evicted but remembered: idempotent"
        stale = eof(peer, first_sid, "f" * 32)
        assert stale.frame_type is FrameType.ERROR and stale.meta["code"] in ("STALE_SESSION", "UNKNOWN_SESSION") and stale.meta["effect"] == "none"
        unknown = eof(peer, "never-existed", "f" * 32)
        assert unknown.frame_type is FrameType.ERROR and unknown.meta["code"] == "UNKNOWN_SESSION"
        assert peer.result(peer.send(code.PING, {})).meta["pong"] is True
    finally:
        peer.close()


# ======================================================================================= S-2 (receipt root)

async def test_s2_a_service_writes_its_receipts_to_the_root_it_was_built_with(tmp_path, monkeypatch):
    """The diagnosed cause of the failing B5 run (`receipts/cp2-review1/green-review1-all.log`): the receipt
    destination was read from the environment at each write, so the late loss task of a finished service wrote its
    `runtime_invalidated` into the NEXT test's directory (that file's first record had another generation than the
    test's own `ready`). The destination is now fixed at construction."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(first))
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(second))  # the next test starts
    service._receipt({"event": "runtime_invalidated", "lost_generation": "late"})
    assert (first / "runtime-receipts.jsonl").exists() and not (second / "runtime-receipts.jsonl").exists()


# ======================================================================================= S-4 (cleanup_ms)

async def test_s4_a_sidecar_that_advertises_the_old_12_second_cleanup_is_refused(tmp_path, monkeypatch):
    refused = stub_service(tmp_path, monkeypatch, "late-ok", cleanup_ms=12_000)
    try:
        with pytest.raises(RuntimeError, match="PROTOCOL_MISMATCH"):
            await refused._ensure_started()
    finally:
        await stop_stub(refused)
    assert cli_runtime._LIMIT_CEILINGS["cleanup_ms"] == 8_000, "the ceiling is the derived value (E1 13.3 row 10)"
    (tmp_path / "ok").mkdir()
    accepted = stub_service(tmp_path / "ok", monkeypatch, "late-ok", cleanup_ms=8_000)
    try:
        await accepted._ensure_started()
        assert accepted._limits["cleanup_ms"] == 8_000
    finally:
        await stop_stub(accepted)


# ==================================================================================== S-5 (code spelling)

async def test_s5_observer_not_integrated_is_spelled_as_the_e1_table_spells_it(tmp_path):
    peer = FramePeer(tmp_path)
    try:
        refused = peer.result(peer.send(code.OBSERVE, {"vendor": "", "path": str(tmp_path / "x"), "mode": "usage"}), allow_error=True)
        assert refused.frame_type is FrameType.ERROR and refused.meta["code"] == "observer not integrated"
        assert refused.meta["effect"] == "none"
    finally:
        peer.close()


# ====================================================================== (review 2 §4.1) loss-aware admission wait

async def test_s41_a_request_waiting_for_inbound_admission_ends_with_the_generation(tmp_path):
    service = new_service(max_inbound_bytes=1000, request_ms=60_000)
    service._inbound_used = 1000  # the budget is exhausted by requests that will never be released
    waiting = asyncio.create_task(service._inbound_acquire(500))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    service._lost = "RUNTIME_LOST: test"
    for waiter in service._inbound_waiters:
        waiter.set_result(None)
    with pytest.raises(RuntimeError, match="RUNTIME_LOST"):
        await asyncio.wait_for(waiting, 2)
    assert service._inbound_used == 1000, "nothing was reserved by the request that ended with the generation"


# ============================================================================== C-1 (fence on the captured generation)

async def test_c1_the_exit_publication_fence_uses_the_generation_the_task_captured():
    frames: list = []

    async def emit(frame):
        frames.append(frame)

    service = RustTerminalService(emit)
    service._runtime_generation = "new-generation"  # a complete cleanup and a fresh start already happened
    service._retired["old-generation"] = True        # the task's own generation is retired
    session = RuntimeSession("c1", "pane", "terminal", [], "/work", RuntimeProcessView(11), "g" * 32, {}, {})
    session.stream_done = True
    session.exit_record = {"reason": "exit", "exit_code": 0, "uptime_ms": 1}
    service._retained[session.id] = service._sessions[session.id] = session
    service._maybe_publish_exit(session, "old-generation")  # the current generation is live; the captured one is not
    await asyncio.sleep(0.05)
    assert not session.exit_published and not frames, "a task of a retired generation publishes nothing, whatever is current"
    service._maybe_publish_exit(session, "new-generation")
    await asyncio.sleep(0.05)
    assert session.exit_published and len(frames) == 1


# ============================================================ SP-4: P7e (the CLEANED that never arrives), L3, L6

@pytest.fixture
async def harness(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, HARNESS)
    try:
        yield rig
    finally:
        await rig.close()


async def test_p7e_a_kill_whose_cleaned_never_arrives_is_unknown_and_ends_the_generation_after_cleanup_ms(harness):
    """E1 10.4 / P7e: `KILL` accepted and then silence (harness fault `cleaned-silent`: the root is reaped, the
    `SESSION_CLEANED` event is never sent). The existing survivor variant covers an answer that says incomplete; this is
    the answer that never comes: after `cleanup_ms` the outcome is `unknown`, which is runtime loss."""
    service = harness.service()
    session = await harness.life(service, "fault:cleaned-silent")
    service._limits = {**service._limits, "cleanup_ms": 800}  # shortened for the test only
    generation = service._runtime_generation
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await service.kill(session.id, force=True)
    assert 0.7 <= time.monotonic() - started < 6, "bounded by cleanup_ms, not by a wait for the missing event"
    await harness.recovered(service)
    invalidated = harness.receipts_of("runtime_invalidated", lost_generation=generation)[0]
    assert "no SESSION_CLEANED" in invalidated["cause"] and not harness.receipts_of("cleaned", session_id=session.id)
    assert not alive(session.proc.pid), "loss cleanup ended the session by its identity"


async def test_l3_a_forged_identity_is_never_signalled_and_an_unreadable_process_table_signals_nothing_and_fences(tmp_path, monkeypatch):
    """E1 L3 on the Rust path: the generation's registry names a bystander under a forged start identity; the sidecar dies;
    loss cleanup must not touch the bystander, then with an unreadable process table nothing is signalled at all and
    the generation is fenced (`CLEANUP_FAILED`)."""
    import psutil
    from agent_team_backend import pty_registry
    from agent_team_backend.applog import in_data_dir
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    bystander = psutil.Popen([base_python_executable(), "-c", "import time; time.sleep(120)"])
    rig.pids.add(bystander.pid)
    try:
        service = rig.service()
        session = await rig.life(service, "pane-l3")
        generation = service._runtime_generation
        in_data_dir(service._data_dir, pty_registry.register_owned, bystander.pid, ["forged"], "Thu Jan  1 00:00:00 1970",
                    generation, "forged-session", "f" * 32)()
        rig.kill_sidecar(service)
        await rig.recovered(service)
        assert bystander.is_running() and bystander.status() != psutil.STATUS_ZOMBIE, "a forged identity was signalled"
        assert not alive(session.proc.pid), "the real session was cleaned by its own identity"
        invalidated = rig.receipts_of("runtime_invalidated", lost_generation=generation)[0]
        assert invalidated["complete"] is True
        # unreadable process table: nothing is signalled and the generation is fenced, never declared clean
        second = rig.service()
        await rig.life(second, "pane-l3-b")
        monkeypatch.setattr(pty_registry, "_ps_table", lambda: None)
        second_generation = second._runtime_generation
        rig.kill_sidecar(second)
        await wait_for(lambda: rig.receipts_of("runtime_invalidated", lost_generation=second_generation), "loss ran", 60)
        fenced = rig.receipts_of("runtime_invalidated", lost_generation=second_generation)[0]
        assert fenced["complete"] is False and fenced["signalled"] == 0, fenced
        assert second._lost and "CLEANUP_FAILED" in second._lost
        assert bystander.is_running()
    finally:
        monkeypatch.undo()
        await rig.close()
        bystander.kill()


async def test_l6_a_transport_loss_keeps_the_ptys_the_descriptors_and_the_same_child(tmp_path, monkeypatch):
    """E1 L6 at the service layer (the principal-seam half is `tests/cli/shellRuntime.test.ts`: reattach, same child,
    no second owned receipt): the renderer's connection going away is invisible to the runtime."""
    import psutil
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        session = await rig.life(service, "pane-l6")
        pid, number, owned_before = session.proc.pid, session.handle.fd, len(rig.receipts_of("owned"))
        me, sidecar = psutil.Process(), psutil.Process(service._runtime.pid)
        counts = (me.num_fds(), sidecar.num_fds())
        rig.sink.hold(session.id, swallow_first_cancel=False)  # the WebSocket is gone: nothing reaches a client
        await rig.say(service, session, "flood 200")
        await asyncio.sleep(0.3)
        rig.sink.open(session.id)  # a new connection reattaches
        await rig.until(lambda: "FLOOD_DONE" in rig.sink.text(session.id), "output resumes after the reattach", 30)
        assert session.proc.pid == pid and alive(pid) and session.handle.fd == number, "same child, same descriptor"
        assert (me.num_fds(), sidecar.num_fds()) == counts, "no descriptor was opened or closed by the disconnect"
        assert len(rig.receipts_of("owned")) == owned_before and not rig.receipts_of("runtime_invalidated")
        assert rig.sink.numbers(session.id) == list(range(200)), "nothing lost, nothing repeated"
    finally:
        await rig.close()


# =============================================== review 2 §4.3: input_blocked / input_unblocked on the handed-off master

async def test_rv2_4_3_input_blocked_and_unblocked_events_are_emitted_over_the_handed_off_master(tmp_path, monkeypatch):
    """The partial-input scenario of the retired JSONL tests, on the Rust path: a CLI that stops reading makes the
    Python write side block (the master is Python's, E1 8); the baseline machinery reports `terminal.input_blocked`
    after its notify threshold and `terminal.input_unblocked` with what was drained once the CLI reads again."""
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        session = await rig.life(service, "pane-input-block")
        amount = 300_000
        await rig.say(service, session, f"stall 2.0 {amount + 0}")
        await rig.until(lambda: "STALLED" in rig.sink.text(session.id), "the CLI stopped reading")
        pending = await service.write_async(session.id, "z" * amount)
        assert pending > 0, "the PTY accepted only part of the input"
        types = lambda: [r[2]["type"] for r in rig.sink.timeline if r[0] == "other" and r[1] == session.id]  # noqa: E731
        await rig.until(lambda: "terminal.input_blocked" in types(), "terminal.input_blocked", 10)
        await rig.until(lambda: "terminal.input_unblocked" in types(), "terminal.input_unblocked after the CLI reads", 30)
        released = next(r[2]["payload"] for r in rig.sink.timeline if r[0] == "other" and r[2]["type"] == "terminal.input_unblocked")
        assert released["drained"] > 0 and released["duration_ms"] >= 500
        await rig.until(lambda: f"DRAINED {amount}" in rig.sink.text(session.id), "the CLI received every byte, in one piece", 30)
    finally:
        await rig.close()
