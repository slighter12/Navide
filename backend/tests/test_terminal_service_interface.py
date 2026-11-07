"""One async terminal-service interface that the in-process service and the Rust service both implement.

The handlers call one surface and never ask which service they hold. The in-process service has no
cancellation fence and no native launch shape, so what only the Rust service reads is accepted and ignored there.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
from pathlib import Path

import pytest

from agent_team_backend import terminals
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.terminals import CreateCancel, TerminalService

PY_SRC = Path(__file__).resolve().parents[1] / "agent_team_backend"

#: The coroutine methods every handler may await on `session.terminals`.
ASYNC_INTERFACE = ("create_async", "activate_async", "write_async", "shell_in_foreground_async",
                   "interrupt_async", "kill_verified", "cancel_dispatched", "observe")


def test_no_caller_branches_on_the_type_of_the_terminal_service():
    pattern = re.compile(r"isinstance\([^()]*(?:\([^()]*\)[^()]*)*(?:Rust)?TerminalService\s*\)"
                         r"|getattr\([^()]*terminals[^()]*,\s*[\"'](?:kill_verified|cancel_dispatched)[\"']")
    offenders = []
    for path in sorted(PY_SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(PY_SRC)}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_the_handlers_do_not_import_the_rust_service():
    text = (PY_SRC / "ws_handlers.py").read_text()
    assert "RustTerminalService" not in text, "ws_handlers must hold no reference to a concrete service"


@pytest.mark.parametrize("service", [TerminalService, RustTerminalService], ids=["in-process", "rust"])
def test_both_services_implement_the_whole_async_interface(service):
    for name in ASYNC_INTERFACE:
        assert inspect.iscoroutinefunction(getattr(service, name)), f"{service.__name__}.{name} is not a coroutine function"
    assert callable(service.control_async) and callable(service.prewarm) and callable(service.receipt_refused_create)


#: The synchronous members the handlers, the app and the tools call on `session.terminals`.
SYNC_INTERFACE = ("create", "write", "shell_in_foreground", "interrupt", "resize", "force_redraw", "log_sent", "get",
                  "list_sessions", "list_session_ids", "live_pane_marks", "memory_pid_groups",
                  "live_session_ids_for_pane", "live_ids_for_surface", "find_live_by_resume_id")


@pytest.mark.parametrize("service", [TerminalService, RustTerminalService], ids=["in-process", "rust"])
def test_the_interface_declares_every_member_a_caller_uses(service):
    for name in SYNC_INTERFACE + ("drain_output", "kill", "wait_until_reaped", "kill_all"):
        assert hasattr(terminals.TerminalInterface, name), f"TerminalInterface does not declare {name}"
        assert hasattr(service, name)


def test_a_service_that_implements_nothing_answers_nothing_live():
    bare = terminals.TerminalInterface()
    assert bare.get("s") is None and bare.list_sessions() == [] and bare.list_session_ids() == []
    assert bare.live_pane_marks() == set() and bare.memory_pid_groups() == {}
    assert bare.live_session_ids_for_pane("p") == [] and bare.live_ids_for_surface("dock") == []
    assert bare.find_live_by_resume_id("claude", "r", lambda argv: "") == []
    with pytest.raises(NotImplementedError):
        bare.create(pane_id="p")


async def test_list_sessions_is_the_public_view_of_what_the_service_holds(tmp_path):
    service = TerminalService(Sink())
    session = await service.create_async(pane_id="p", agent_key=None, command=["/bin/cat"], cwd=str(tmp_path))
    try:
        assert service.list_sessions() == [session] and service.get(session.id) is session
    finally:
        await service.kill(session.id, force=True)


def test_no_caller_reads_the_private_session_table_of_a_service():
    offenders = []
    for name in ("ws_handlers.py", "app.py", "mcp_server/server.py"):
        for number, line in enumerate((PY_SRC / name).read_text().splitlines(), 1):
            if re.search(r"(terminals|service)\W+(?:[\"'])?_sessions\b", line):
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, offenders


class Sink:
    def __init__(self) -> None:
        self.frames: list = []

    async def __call__(self, frame) -> None:
        self.frames.append(frame)


async def test_the_in_process_service_creates_through_the_async_interface_and_ignores_what_only_native_reads(tmp_path):
    service = TerminalService(Sink())
    session = await service.create_async(
        pane_id="p", agent_key=None, command=["/bin/cat"], cwd=str(tmp_path), cols=90, rows=20, env=None, env_remove=None,
        metadata={}, output_log_file="", cancelled=lambda: False, cancel_record={}, credential=None)
    try:
        assert service.get(session.id) is session and session.pane_id == "p"
        assert await service.activate_async(session.id) is None
    finally:
        await service.kill(session.id, force=True)


async def test_the_in_process_service_forwards_a_spawn_command(tmp_path):
    service = TerminalService(Sink())
    session = await service.create_async(pane_id="p", agent_key=None, command=["/bin/echo", "logical"], cwd=str(tmp_path),
                                         spawn_command=["/bin/cat"])
    try:
        assert session.command == ["/bin/echo", "logical"], "the logical command is kept; the spawn command is what ran"
    finally:
        await service.kill(session.id, force=True)


@pytest.mark.skipif(sys.platform == "win32", reason="a PTY child that echoes input")
async def test_write_and_foreground_and_interrupt_answer_as_their_synchronous_forms(tmp_path):
    sink = Sink()
    service = TerminalService(sink)
    session = await service.create_async(pane_id="p", agent_key=None, command=["/bin/cat"], cwd=str(tmp_path))
    try:
        assert await service.write_async(session.id, "") == service.write(session.id, "") == 0
        assert await service.shell_in_foreground_async(session.id) == service.shell_in_foreground(session.id)
        assert await service.interrupt_async(session.id) is None
        with pytest.raises(KeyError):
            await service.write_async("no-such-session", "x")
    finally:
        await service.kill(session.id, force=True)


async def test_control_async_orders_the_resize_after_the_drain_and_before_the_caller_acknowledges():
    service = TerminalService(Sink())
    events: list = []

    async def drain(session_id):
        events.append(("drain", session_id))

    service.drain_output = drain
    service.resize = lambda sid, cols, rows: events.append(("resize", sid, cols, rows))
    service.force_redraw = lambda sid, cols, rows: events.append(("redraw", sid, cols, rows))
    async with service.control_async("s1", 100, 30):
        events.append(("acknowledge",))
    async with service.control_async("s1", 101, 31, redraw=True):
        events.append(("acknowledge",))
    assert events == [("drain", "s1"), ("resize", "s1", 100, 30), ("acknowledge",),
                      ("drain", "s1"), ("redraw", "s1", 101, 31), ("acknowledge",)]


async def test_kill_verified_reports_a_killed_process_as_gone(tmp_path):
    service = TerminalService(Sink())
    session = await service.create_async(pane_id="p", agent_key=None, command=["/bin/cat"], cwd=str(tmp_path))
    assert await service.kill_verified(session.id) is True
    assert service.get(session.id) is None


class _GracefulKill:
    """A graceful vendor's kill: it returns while the child lives, and the reap event is set when the child is gone."""

    def __init__(self, service: TerminalService):
        self.reaped = asyncio.Event()
        self.service = service

    async def kill(self, session_id: str, force: bool = False) -> None:
        self.service._graceful_kills[session_id] = self.reaped


async def test_kill_verified_waits_for_the_reap_of_a_graceful_kill():
    service = TerminalService(Sink())
    graceful = _GracefulKill(service)
    service.kill = graceful.kill
    verified = asyncio.ensure_future(service.kill_verified("s1"))
    await asyncio.sleep(0.05)
    assert not verified.done(), "kill_verified answered while the graceful child was still alive"
    graceful.reaped.set()
    assert await verified is True


async def test_kill_verified_is_false_when_the_child_outlives_the_reap_wait(monkeypatch):
    service = TerminalService(Sink())
    service.kill = _GracefulKill(service).kill
    monkeypatch.setattr(terminals, "_reap_wait_timeout_s", lambda: 0.05)
    assert await service.kill_verified("s1") is False


async def test_a_create_without_a_dispatch_fence_says_its_own_rollback_settles_a_cancel():
    service = TerminalService(Sink())
    assert await service.cancel_dispatched({}) == CreateCancel.ROLLBACK == "rollback"
    assert CreateCancel.PROVEN == "proven" and CreateCancel.PENDING == "pending"


def test_a_refused_create_leaves_no_trace_on_the_in_process_service():
    async def build():
        service = TerminalService(Sink())
        return service.receipt_refused_create("pane", RuntimeError("refused"))

    assert asyncio.run(build()) is None


async def test_native_observation_is_unavailable_on_the_in_process_service():
    service = TerminalService(Sink())
    with pytest.raises(RuntimeError, match="native observation unavailable; no Python parser fallback"):
        await service.observe("claude", "p", {"path": "x", "mode": "m"}, match=lambda sessions: None)


def test_the_interface_is_one_class_the_services_share():
    assert issubclass(TerminalService, terminals.TerminalInterface) and issubclass(RustTerminalService, TerminalService)
