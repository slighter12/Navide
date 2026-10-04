"""Supplemental bridge bookkeeping/watermark units, not native execution proof."""
import asyncio
from collections import deque
import json
import threading
from types import SimpleNamespace

import pytest

from agent_team_backend.cli_runtime import RuntimeProcessView, RuntimeSession, RustTerminalService


@pytest.mark.asyncio
async def test_concurrent_request_counters_follow_wire_order_and_settle():
    service = RustTerminalService.__new__(RustTerminalService)
    service._loop = asyncio.get_running_loop()
    service._lost = None
    service._runtime_generation = "unit-generation"
    service._request_number = 0
    service._pending = {}
    service._observations = {}
    service._admission = asyncio.Semaphore(256)
    service._send_lock = asyncio.Lock()
    ids = []
    maximum = 0

    class Sink:
        def write(self, data):
            nonlocal maximum
            request = json.loads(data)
            ids.append(request["id"])
            maximum = max(maximum, len(service._pending))

        async def drain(self):
            await asyncio.sleep(0)
            service._pending[ids[-1]].set_result({"ok": True, "result": {"pong": True}})

    service._runtime = SimpleNamespace(stdin=Sink())
    await asyncio.gather(*(service._request("ping") for _ in range(1000)))
    assert ids == [f"r{index}" for index in range(1, 1001)]
    assert maximum <= 256
    assert not service._pending


def test_settled_application_views_keep_only_256_confirmed_receipts():
    service = RustTerminalService.__new__(RustTerminalService)
    service._retained = {}
    service._settled = deque()
    for index in range(1000):
        session = RuntimeSession(str(index), "pane", "terminal", [], "unit-cwd",
                                 RuntimeProcessView(index + 100), "unit-generation", {}, {})
        session.closed = True
        session.cleanup.set()
        service._retained[session.id] = session
        service._retire(session)
        service._retire(session)
    assert len(service._retained) == len(service._settled) == 256
    assert list(service._retained) == [str(index) for index in range(744, 1000)]


@pytest.mark.asyncio
async def test_dropped_tail_settles_output_watermark_without_waiting_for_missing_data():
    service = RustTerminalService.__new__(RustTerminalService)
    service._loop = asyncio.get_running_loop()
    service._lost = None
    service._settled = deque()
    session = RuntimeSession("unit-tail", "pane", "terminal", [], "unit-cwd",
                             RuntimeProcessView(100), "unit-generation", {}, {})
    session.received_seq = 3
    session.cleanup.set()
    service._sessions = {session.id: session}
    service._retained = {session.id: session}
    for name in ("_out_dropped", "_recent_chunks", "_echo_probe", "_resize_barriers", "_flow_desired", "_write_locks"):
        setattr(service, name, {})
    observed = []

    async def drain(sid):
        observed.append((sid, service._out_dropped[sid]))

    async def emit(event):
        observed.append(event)

    service.drain_output = drain
    service._emit = emit
    await asyncio.wait_for(service._finish_exit(session, {
        "last_output_seq": 10, "dropped_tail_first_seq": 4, "dropped_tail_bytes": 200,
        "reason": "exit", "exit_code": 7, "uptime_ms": 30,
    }), timeout=1)
    assert session.received_seq == 10 and session.closed
    assert observed[0] == (session.id, 200)
    assert observed[1]["type"] == "terminal.exit"


@pytest.mark.asyncio
@pytest.mark.parametrize("in_flight", [False, True])
async def test_final_drain_logs_pending_tail_once_before_public_exit(tmp_path, in_flight):
    """Real presentation/drain/mirror unit; only private flow transport is substituted."""
    frames = []
    emitting = asyncio.Event()
    allow_emit = asyncio.Event()

    async def emit(frame):
        if isinstance(frame, bytes):
            emitting.set()
            await allow_emit.wait()
        frames.append(frame)

    service = RustTerminalService(emit)
    session = RuntimeSession("tail-unit", "pane", "terminal", [], str(tmp_path),
                             RuntimeProcessView(100), "unit-generation", {}, {})
    session.received_seq = 3
    session.cleanup.set()
    service._sessions[session.id] = service._retained[session.id] = session
    transcript = tmp_path / "transcript.log"
    session.output_log_fp = transcript.open("a", encoding="utf-8", buffering=1)

    async def flow_transport(*_args, **_kwargs):
        return {"paused": False}

    service._request = flow_transport
    if in_flight:
        service._out_buffers[session.id] = [b"retained\r\n"]
        service._out_buf_bytes[session.id] = 10
        service._flush_output(session)
        await asyncio.wait_for(emitting.wait(), 1)
    exiting = asyncio.create_task(service._finish_exit(session, {
        "last_output_seq": 10, "dropped_tail_first_seq": 4, "dropped_tail_bytes": 200,
        "reason": "exit", "exit_code": 7, "uptime_ms": 30,
    }))
    if in_flight:
        async with asyncio.timeout(1):
            while service._out_dropped.get(session.id) != 200:
                await asyncio.sleep(0)
    allow_emit.set()
    await asyncio.wait_for(exiting, 1)
    if service._event_tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(service._event_tasks)), 1)
    logged = transcript.read_text()
    assert logged.count("[navide: 200 bytes of output dropped]") == 1
    assert logged.count("retained") == int(in_flight)
    assert session.closed and session.output_log_fp is None
    assert session.id not in service._out_dropped
    expected_data = b"\x01\x01\x00\x00\x00\x09tail-unit\x04pane" + b"retained\r\n"
    assert [frame for frame in frames if isinstance(frame, bytes)] == ([expected_data] if in_flight else [])
    assert isinstance(frames[-1], dict) and frames[-1]["type"] == "terminal.exit"
    assert frames[-1]["payload"] == {
        "terminal_session_id": session.id, "pane_id": "pane", "reason": "exit", "exit_code": 7,
        "uptime_ms": 30, "signal": None, "startup_probe": None,
    }
    assert set(frames[-1]) == {"id", "type", "payload", "timestamp"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel-open", "cancel-release", "ack-error"])
async def test_control_failure_keeps_matching_release_and_unlocks(failure):
    """Supplemental ownership unit with a controlled private transport, not PTY proof."""
    frames = []

    async def emit(frame):
        frames.append(frame)

    service = RustTerminalService(emit)
    session = RuntimeSession("control-unit", "pane", "terminal", [], "unit-cwd",
                             RuntimeProcessView(100), "unit-generation", {}, {})
    service._sessions[session.id] = service._retained[session.id] = session
    opened = asyncio.Event()
    allow_open = asyncio.Event()
    releasing = asyncio.Event()
    allow_release = asyncio.Event()
    requests = []

    async def transport(op, args, stored):
        requests.append((op, args))
        if op == "resize":
            opened.set()
            await allow_open.wait()
            return {"barrier_id": "operation-A", "through_seq": 0}
        releasing.set()
        await allow_release.wait()
        return {"released": True}

    service._request = transport

    async def control():
        async with service.control_async(session.id, 110, 40):
            if failure == "ack-error":
                raise OSError("controlled ACK failure")
            await emit({"type": "public-ack"})

    task = asyncio.create_task(control())
    await asyncio.wait_for(opened.wait(), 1)
    if failure == "cancel-open":
        task.cancel()
    allow_open.set()
    await asyncio.wait_for(releasing.wait(), 1)
    if failure in {"cancel-open", "cancel-release"}:
        task.cancel()  # Includes second cancellation during matching release.
        with pytest.raises(asyncio.CancelledError):
            await task
    assert session.control_lock.locked()
    assert requests[-1] == ("release", {"barrier_id": "operation-A"})
    allow_release.set()
    if failure == "ack-error":
        with pytest.raises(OSError, match="controlled ACK failure"):
            await task
    await asyncio.wait_for(asyncio.gather(*tuple(service._event_tasks)), 1)
    assert not session.control_lock.locked()
    assert frames == ([] if failure in {"cancel-open", "ack-error"} else [{"type": "public-ack"}])


@pytest.mark.asyncio
async def test_actual_rust_launch_boundary_refuses_unowned_provider_before_native_create(tmp_path, tmp_path_factory):
    import os
    from pathlib import Path
    from websockets.asyncio.client import connect
    from tests.cli_regression.support.backend_process import BackendProcess

    binary = Path(__file__).resolve().parents[2] / "native/navide-cli-runtime/target/release/navide-cli-runtime"
    if not hasattr(os, "tcgetpgrp") or not binary.is_file():
        pytest.skip("requires an explicitly built native POSIX runtime")
    # A controlled provider-named executable outside this backend's allowed root.
    # It is never a real vendor installation and must not execute at all.
    external = tmp_path_factory.mktemp("unapproved-provider") / "claude"
    external.write_text("#!/bin/sh\necho UNAPPROVED_EXECUTION\n")
    external.chmod(0o700)
    backend = BackendProcess(tmp_path)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    # Preserve the harness refusal verdict; expected refusal is the test's target.
    with pytest.raises(AssertionError, match="external boundary reached"):
        async with backend:
            async with connect(backend.url) as ws:
                response = await backend.request(ws, "terminal.create", {
                    "pane_id": "refusal", "agent_key": "terminal", "command": [str(external), "--version"],
                    "cwd": str(tmp_path), "cols": 100, "rows": 30,
                }, ok=False)
                assert response["ok"] is False
    refusals = [json.loads(line) for line in (tmp_path / "refusals.jsonl").read_text().splitlines()]
    assert len(refusals) == 1 and refusals[0]["boundary"] == "real Rust child CLI"
    receipts = [json.loads(line) for line in (tmp_path / "runtime-receipts.jsonl").read_text().splitlines()]
    assert any(record["event"] == "ready" for record in receipts)
    assert not any(record["event"] == "owned" for record in receipts)


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_already_queued", [False, True])
@pytest.mark.parametrize("cleanup_failed", [False, True])
async def test_kill_all_fences_retained_exit_cleanup_and_late_error(tmp_path, monkeypatch,
                                                                  ack_already_queued, cleanup_failed):
    """Supplemental event race: real bridge handlers; controlled ACK and external store pacing."""
    from agent_team_backend import pty_registry

    async def emit(_frame):
        pass

    service = RustTerminalService(emit)
    service._data_dir = tmp_path
    service._runtime = SimpleNamespace(returncode=None)
    session = RuntimeSession("naturally-exited", "pane", "terminal", [], str(tmp_path),
                             RuntimeProcessView(100), "unit-generation", {}, {})
    service._sessions[session.id] = service._retained[session.id] = session
    await service._finish_exit(session, {"last_output_seq": 0, "reason": "exit", "exit_code": 0,
                                        "uptime_ms": 1, "dropped_tail_bytes": 0})
    assert session.closed and session.id not in service._sessions and not session.cleanup.is_set()
    allow_ack = asyncio.Event()
    unregister_started = threading.Event()
    allow_unregister = threading.Event()
    unregistered = threading.Event()
    original_unregister = pty_registry.unregister

    def paced_unregister(pid):
        unregister_started.set()
        assert allow_unregister.wait(2), "controlled store acknowledgment deadline"
        original_unregister(pid)
        unregistered.set()

    monkeypatch.setattr(pty_registry, "unregister", paced_unregister)

    async def acknowledge():
        await allow_ack.wait()
        await service._finish_cleanup(session, {"cleanup_complete": not cleanup_failed,
            "root_reaped": True, "survivors": [], "unverifiable": []})

    cleanup = service._task(acknowledge()) if ack_already_queued else None
    fence = asyncio.create_task(service.kill_all())
    try:
        await asyncio.sleep(0)
        assert not fence.done(), "shutdown returned before retained ownership ACK"
        if cleanup is None:
            cleanup = service._task(acknowledge())
        allow_ack.set()
        if cleanup_failed:
            with pytest.raises(RuntimeError, match="CLEANUP_FAILED"):
                await asyncio.wait_for(fence, 2)
            assert session.cleanup.is_set() and session.cleanup_error
        else:
            assert await asyncio.to_thread(unregister_started.wait, 1)
            assert not fence.done() and not session.cleanup.is_set()
            allow_unregister.set()
            await asyncio.wait_for(fence, 2)
            assert session.cleanup.is_set() and unregistered.is_set()
    finally:
        allow_ack.set()
        allow_unregister.set()
        if cleanup:
            await asyncio.wait_for(cleanup, 2)
        if not fence.done():
            fence.cancel()
        await asyncio.gather(fence, return_exceptions=True)
