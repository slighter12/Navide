"""Supplemental bridge bookkeeping units (create marks, request ids, retention, control locks, shutdown
fence), not native execution proof. The watermark/release/_finish_exit tests of the removed data plane
are retired: output ordering is the Terminal Stream's (E1 8, 9) and is proven in test_cli_runtime_lifecycle.py."""
import asyncio
from collections import deque
import json
import os
import socket
import threading
from types import SimpleNamespace

import pytest

from agent_team_backend import cli_runtime_frames as wire
from agent_team_backend.cli_runtime import RuntimeProcessView, RuntimeSession, RustTerminalService
from agent_team_backend.cli_runtime_handoff import HandoffSlot


@pytest.mark.asyncio
@pytest.mark.parametrize("tracking", [True, False])
@pytest.mark.parametrize("supplied_metadata", [True, False])
async def test_native_create_uses_upstream_detached_server_mark(
    tmp_path, monkeypatch, tracking, supplied_metadata,
):
    """Real create bookkeeping; only sidecar transport and settings are substituted."""
    from agent_team_backend import cli_runtime, detached_servers, resource_limits

    async def emit(_frame):
        pass

    service = RustTerminalService(emit)
    monkeypatch.setattr(resource_limits, "current", lambda: {
        resource_limits.TRACK_DETACHED_KEY: tracking,
    })
    marks = []

    def new_mark():
        mark = f"owned-mark-{len(marks)}"
        marks.append(mark)
        return mark

    monkeypatch.setattr(detached_servers, "new_mark", new_mark)
    monkeypatch.setenv(detached_servers.MARK_ENV, "inherited-mark")
    launches = []

    def prepare(argv, *, cwd, env):
        launches.append(dict(env))
        return env

    monkeypatch.setattr(cli_runtime, "prepare_native_launch", prepare)

    async def started():
        pass

    async def request(op, args, session=None, **kw):
        if op == "adopt":
            return {"adopted": True}
        assert op == "create"
        master, _slave = os.openpty()  # the descriptor the sidecar would have handed over
        service._slots[args["session_id"]] = HandoffSlot(args["session_id"], master, f"/dev/fake-{args['session_id']}")
        service._owned[args["session_id"]].set_result({})
        return {"root": {"pid": 100}, "state": "owned"}

    monkeypatch.setattr(service, "_ensure_started", started)
    monkeypatch.setattr(service, "_request", request)
    for index in range(2):
        metadata = {"surface": "editor", "workspace_path": str(tmp_path)}
        session = await service.create_async(
            pane_id=f"pane-{index}", command=["owned-fixture"], cwd=str(tmp_path),
            env={detached_servers.MARK_ENV: "caller-mark"},
            env_remove=[detached_servers.MARK_ENV],
            **({"metadata": metadata} if supplied_metadata else {}),
        )
        if tracking:
            assert launches[-1][detached_servers.MARK_ENV] == marks[index]
            assert session.metadata["pane_mark"] == marks[index]
            if supplied_metadata:
                assert metadata["pane_mark"] == marks[index]
        else:
            assert detached_servers.MARK_ENV not in launches[-1]
            assert "pane_mark" not in session.metadata
        if supplied_metadata:
            assert session.metadata["surface"] == "editor"
    assert len(marks) == (2 if tracking else 0)
    assert service.live_pane_marks() == set(marks)
    session.closed = True
    assert service.live_pane_marks() == set(marks[:1])


@pytest.mark.asyncio
async def test_concurrent_request_counters_follow_wire_order_and_settle():
    service = RustTerminalService.__new__(RustTerminalService)
    service._loop = asyncio.get_running_loop()
    service._lost = None
    service._runtime_generation = "00112233445566778899aabbccddeeff"
    service._request_number = 0
    service._pending = {}
    service._request_codes = {}
    from agent_team_backend.cli_runtime_receipts import ControlLedger
    service._ledger = ControlLedger()
    service._expired = {}
    service._inbound_used = 0
    service._inbound_waiters = []
    service._observations = {}
    service._observing = {}
    service._admission = asyncio.Semaphore(256)
    service._observe_slots = asyncio.Semaphore(4)
    service._send_lock = asyncio.Lock()
    service._limits = {"request_ms": 15_000, "ping_ms": 5_000, "max_inbound_bytes": 16_777_216}
    parent, child = socket.socketpair()
    parent.setblocking(False)
    child.setblocking(False)
    service._sock = parent
    tag = wire.runtime_tag(service._runtime_generation)
    ids = []
    maximum = 0

    async def peer():
        nonlocal maximum
        buffer = b""
        while len(ids) < 1000:
            buffer += await service._loop.sock_recv(child, 65536)
            while len(buffer) >= wire.HEADER_LEN:
                header = wire.parse_header(buffer[:wire.HEADER_LEN], wire.Direction.PYTHON_TO_RUST, tag)
                if len(buffer) < wire.HEADER_LEN + header.body_len:
                    break
                buffer = buffer[wire.HEADER_LEN + header.body_len:]
                ids.append(header.correlation_id)
                maximum = max(maximum, len(service._pending))
                service._on_answer(
                    wire.Frame(wire.FrameType.RESPONSE, wire.code.PING, 0, header.correlation_id, tag, {"pong": True}))

    reader = asyncio.create_task(peer())
    await asyncio.gather(*(service._request("ping") for _ in range(1000)))
    await reader
    parent.close()
    child.close()
    assert ids == list(range(1, 1001)), "request ids are numeric, increasing, in wire order"
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
@pytest.mark.parametrize("failure", ["request-error", "cancelled-in-request", "ack-error"])
async def test_control_failure_unlocks_the_control_lock_and_the_barrier(failure):
    """Supplemental unit with a controlled private transport, not PTY proof (the real order is R5/T6)."""
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    session = RuntimeSession("control-unit", "pane", "terminal", [], "unit-cwd",
                             RuntimeProcessView(100), "unit-generation", {}, {})
    session.handle = SimpleNamespace(read=lambda n: (_ for _ in ()).throw(BlockingIOError()),
                                     pause_reading=lambda: None, resume_reading=lambda: None)
    service._sessions[session.id] = service._retained[session.id] = session
    entered = asyncio.Event()

    calls = []

    async def transport(op, args, stored, **kw):
        calls.append(op)
        entered.set()
        if len(calls) > 1:  # only the first control fails; the later one proves the locks were released
            return {"applied": True, "steps": 1}
        if failure == "request-error":
            raise RuntimeError("controlled RESIZE failure")
        if failure == "cancelled-in-request":
            await asyncio.Event().wait()
        return {"applied": True, "steps": 1}

    service._request = transport
    acknowledged = []

    async def control():
        async with service.control_async(session.id, 110, 40):
            if failure == "ack-error":
                raise OSError("controlled ACK failure")
            acknowledged.append(True)

    task = asyncio.create_task(control())
    if failure == "cancelled-in-request":
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises((RuntimeError, OSError)):
            await asyncio.wait_for(task, 2)
    assert not session.control_lock.locked(), "the control lock is released on every failure path"
    assert not service._resize_barriers[session.id].locked(), "so is the barrier"
    assert acknowledged == []
    async with service.control_async(session.id, 90, 30):  # a later control still works
        pass


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
    """Supplemental event race: real handlers; controlled ACK and external store pacing. A retained session
    whose exit was published (closed) is still reached by kill_all, which waits for its verified cleanup."""
    from agent_team_backend import pty_registry

    async def emit(_frame):
        pass

    service = RustTerminalService(emit)
    service._data_dir = tmp_path
    service._runtime = SimpleNamespace(returncode=None)
    service._runtime_generation = "unit-generation"
    session = RuntimeSession("naturally-exited", "pane", "terminal", [], str(tmp_path),
                             RuntimeProcessView(100), "unit-generation", {}, {})
    session.closed = session.stream_done = session.exit_published = True  # its exit is already out
    session.joined.set()
    service._retained[session.id] = session
    sent = []

    async def transport(op, args, stored, **kw):
        sent.append(op)
        return {"accepted": True}

    service._request = transport
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
        assert sent == ["kill"], "kill_all sent KILL for the closed-but-uncleaned session (not the base predicate)"
    finally:
        allow_ack.set()
        allow_unregister.set()
        if cleanup:
            await asyncio.gather(cleanup, return_exceptions=True)
        if not fence.done():
            fence.cancel()
        await asyncio.gather(fence, return_exceptions=True)
