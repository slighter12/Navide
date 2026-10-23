"""The real sidecar below the Python runtime: cancellation, shutdown, refusals (E1 S4, S5, S6, L5).

A raw frame peer (`FramePeer`) drives the actual release sidecar over a real socketpair. Fault hooks
come from the harness build only. Supplemental to the TypeScript production-client proofs
(createCancelOwnership, rebuildOwnership, retirementRule, runtimeRecovery).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time

import psutil
import pytest

from agent_team_backend import cli_runtime
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_frames import FrameType, code
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT, FramePeer

PEER = Path(__file__).parent / "cli_regression/support/lifecycle_peer.py"
BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
    reason="requires the explicitly built macOS native runtime (product and harness)")


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def argv(tmp_path):
    script = tmp_path / "lifecycle_peer.py"
    shutil.copyfile(PEER, script)
    return [base_python_executable(), "-u", str(script)]


def cleaned(peer: FramePeer, sid: str):
    return peer.wait_for(lambda: next((e for e in peer.events if e.code == code.SESSION_CLEANED and e.meta["session_id"] == sid), None),
                         f"cleaned {sid}", 20)


def root_pid(peer: FramePeer, sid: str) -> int:
    return next(e.meta["root"]["pid"] for e in peer.events if e.code == code.SESSION_OWNED and e.meta["session_id"] == sid)


# ---------------------------------------------------------------------------------- S4

def test_s4_cancel_before_the_gate_is_prevented_and_the_target_never_runs(tmp_path, argv):
    peer = FramePeer(tmp_path)
    try:
        peer.journal.unlink(missing_ok=True)
        os.mkfifo(peer.journal)  # the journal append blocks: the gate stays closed
        ran = tmp_path / "i-ran"
        sid, generation = "s-prevent", "a" * 32
        meta = {"session_id": sid, "session_generation": generation, "pane_id": "p", "workspace_path": str(tmp_path),
                "launch": {"shape": "direct", "form": "list", "elements": [sys.executable, "-c", f"open({str(ran)!r}, 'w').close()"], "words": []}, "cwd": str(tmp_path),
                "env": {"PATH": "/usr/bin:/bin"}, "cols": 80, "rows": 24, "agent_key": ""}
        create = peer.send(code.CREATE, meta)
        time.sleep(0.3)  # the create worker is now blocked opening the FIFO (a functional hold, not a measurement)
        cancel = peer.result(peer.send(code.CANCEL, {"target_id": create}))
        assert cancel.meta["state"] == "prevented"
        reader = os.open(peer.journal, os.O_RDONLY | os.O_NONBLOCK)
        try:
            answer = peer.result(create, allow_error=True)
        finally:
            os.close(reader)
        assert answer.frame_type is FrameType.ERROR and answer.meta["code"] in ("CREATE_CANCELLED", "OWNERSHIP_JOURNAL_FAILED")
        time.sleep(0.3)
        assert not ran.exists() and not [e for e in peer.events if e.code == code.SESSION_OWNED]
    finally:
        peer.close()


def test_s4_cancel_of_a_published_session_is_cleanup_started_then_settled_after_adopt(tmp_path, argv):
    peer = FramePeer(tmp_path)
    try:
        def create_raw(name):
            sid, generation = f"s-{name}", name[0] * 32
            meta = {"session_id": sid, "session_generation": generation, "pane_id": f"p-{name}", "workspace_path": str(tmp_path),
                    "launch": {"shape": "direct", "form": "list", "elements": list(argv), "words": []}, "cwd": str(tmp_path), "env": {"PATH": "/usr/bin:/bin"}, "cols": 80, "rows": 24, "agent_key": ""}
            request = peer.send(code.CREATE, meta)
            peer.result(request)
            return request, sid, generation

        request, sid, generation = create_raw("cleanup")
        state = peer.result(peer.send(code.CANCEL, {"target_id": request})).meta["state"]
        assert state == "cleanup-started", "the session is published and not adopted: Rust starts its cleanup"
        done = cleaned(peer, sid)
        assert done.meta["cleanup_complete"] is True and done.meta["root_reaped"] is True
        request, sid, generation = create_raw("settled")
        assert peer.result(peer.send(code.ADOPT, {"session_id": sid, "session_generation": generation})).meta["adopted"] is True
        assert peer.result(peer.send(code.CANCEL, {"target_id": request})).meta["state"] == "settled"
        assert alive(root_pid(peer, sid)), "a settled session is never touched by a late cancel"
        # An id the sidecar has not read yet is answered `prevented`: that create, if it ever arrives, is
        # refused at admission (the released/unknown states are the Rust unit tests, cancel_state_tests).
        assert peer.result(peer.send(code.CANCEL, {"target_id": 10_000})).meta["state"] == "prevented"
    finally:
        peer.close()


# ---------------------------------------------------------------------------------- S5 / L5

def test_s5_graceful_shutdown_reports_exactly_and_ends_every_session(tmp_path, argv):
    peer = FramePeer(tmp_path)
    try:
        sessions = [peer.create(argv, pane=f"p{i}") for i in range(2)]
        roots = [root_pid(peer, sid) for sid, _ in sessions]
        answer = peer.result(peer.send(code.SHUTDOWN, {}), timeout=30)
        assert answer.meta == {"cleanup_complete": True, "survivors": []}
        assert peer.process.wait(timeout=20) == 0 and not [pid for pid in roots if alive(pid)]
    finally:
        peer.close()


def test_s5_shutdown_with_a_survivor_is_cleanup_failed_with_the_survivor_listed(tmp_path, argv):
    peer = FramePeer(tmp_path, binary=HARNESS)
    try:
        peer.create(argv, pane="p-ok")
        peer.create(argv, pane="fault:survivor")
        answer = peer.result(peer.send(code.SHUTDOWN, {}), allow_error=True, timeout=30)
        assert answer.frame_type is FrameType.ERROR and answer.meta["code"] == "CLEANUP_FAILED"
        assert answer.meta["effect"] == "partial" and len(answer.meta["survivors"]) == 1
        assert answer.meta["survivors"][0]["start_value"] == "fault-survivor"
        assert peer.process.wait(timeout=20) != 0
    finally:
        peer.close()


def test_l5_channel_eof_means_the_backend_died_the_sidecar_ends_its_trees_and_exits(tmp_path, argv):
    peer = FramePeer(tmp_path)
    try:
        sid, _ = peer.create(argv)
        root = root_pid(peer, sid)
        peer.sock.shutdown(socket.SHUT_RDWR)  # the backend vanished
        assert peer.process.wait(timeout=30) == 0, "EOF-driven cleanup is complete and silent"
        assert not alive(root), "the session's tree ended by identity"
    finally:
        peer.close()


# ---------------------------------------------------------------------------------- S6

def test_s6_pre_effect_refusals_answer_one_request_and_affect_nothing_else(tmp_path, argv):
    peer = FramePeer(tmp_path)
    try:
        sid, generation = peer.create(argv, pane="p-bystander")
        cases = [
            (code.RESIZE, {"session_id": "nobody", "session_generation": generation, "cols": 80, "rows": 24}, "UNKNOWN_SESSION"),
            (code.RESIZE, {"session_id": sid, "session_generation": "b" * 32, "cols": 80, "rows": 24}, "STALE_SESSION"),
            (code.RESIZE, {"session_id": sid, "session_generation": generation, "cols": 0, "rows": 24}, "BAD_REQUEST"),
            (code.KILL, {"session_id": "nobody", "session_generation": generation}, "UNKNOWN_SESSION"),
        ]
        for request_code, meta, expected in cases:
            refused = peer.result(peer.send(request_code, meta), allow_error=True)
            assert refused.frame_type is FrameType.ERROR and refused.meta["code"] == expected and refused.meta["effect"] == "none", (expected, refused.meta)
        assert peer.result(peer.send(code.PING, {})).meta["pong"] is True
        assert alive(root_pid(peer, sid)), "the bystander was not touched by any refusal"
        ok = peer.result(peer.send(code.RESIZE, {"session_id": sid, "session_generation": generation, "cols": 90, "rows": 30, "redraw": False}))
        assert ok.meta == {"applied": True, "steps": 1}
    finally:
        peer.close()


@pytest.mark.asyncio
async def test_s6_a_create_cleanup_failed_ends_the_generation(tmp_path, monkeypatch):
    from tests.test_cli_runtime_observe import STUB_LIMITS
    stub = tmp_path / "stub"
    stub.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(Path(__file__).parent / 'cli_regression/support/stub_sidecar.py')!r}, run_name='__main__')\n")
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    monkeypatch.setenv("NAVIDE_STUB_MODE", "create-cleanup-failed")
    monkeypatch.setenv("NAVIDE_STUB_BACKEND", str(BACKEND))
    monkeypatch.setenv("NAVIDE_STUB_LIMITS", json.dumps(STUB_LIMITS))
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    with pytest.raises(cli_runtime._RuntimeRefused, match="CLEANUP_FAILED"):
        await service.create_async(pane_id="p", agent_key="terminal", command=["/bin/cat"], cwd=str(tmp_path))
    generation = service._runtime_generation
    deadline = time.monotonic() + 30
    records: list[dict] = []
    while time.monotonic() < deadline:
        path = tmp_path / "runtime-receipts.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        if any(r["event"] == "runtime_invalidated" and r["lost_generation"] == generation for r in records):
            break
        await asyncio.sleep(0.05)
    invalidated = [r for r in records if r["event"] == "runtime_invalidated" and r["lost_generation"] == generation]
    assert len(invalidated) == 1 and "CLEANUP_FAILED: create" in invalidated[0]["cause"], "a create CLEANUP_FAILED is generation loss"
    if service._runtime is not None and service._runtime.returncode is None:
        service._runtime.kill()


def test_sidecar_bookkeeping_is_bounded_after_settled_requests_and_sessions(tmp_path):
    """Migrated from the removed JSONL peer: 1000 settled requests and 260 settled sessions stay bounded."""
    peer = FramePeer(tmp_path)
    try:
        for _ in range(1000):
            answer = peer.result(peer.send(code.PING, {}))
        assert answer.meta["bookkeeping"]["last_request"] == 1000
        for _ in range(260):
            sid, generation = peer.create(["/usr/bin/true"])
            os.close(peer.masters.pop(sid))  # this peer plays the Python stream end: close, then TTY_EOF
            peer.result(peer.send(code.TTY_EOF, {"session_id": sid, "session_generation": generation, "reason": "eof"}))
            cleaned(peer, sid)
            for index in [i for i, e in enumerate(peer.events) if e.code == code.SESSION_CLEANED and e.meta["session_id"] == sid]:
                peer.events[index] = None  # keep the peer's own memory flat as well
            peer.events[:] = [e for e in peer.events if e is not None]
        bookkeeping = peer.result(peer.send(code.PING, {})).meta["bookkeeping"]
        assert bookkeeping["live_sessions"] == 0
        assert bookkeeping["settled_sessions"] <= 256
        assert bookkeeping["session_workers"] == 0
    finally:
        peer.close()
