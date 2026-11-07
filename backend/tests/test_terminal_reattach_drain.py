"""`terminal.reattach` with a size, through the handler, on the real in-process service and a real PTY child.

The output the pane produced at its old size reaches the client before the reply that acknowledges the new size, and the
program is nudged to repaint. The service answers through the one interface every service implements.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from agent_team_backend import app
from agent_team_backend import terminals as terminals_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a POSIX PTY child")

OLD_WIDTH_OUTPUT = "OLD_WIDTH_OUTPUT"


class OrderRecordingSocket:
    """Every frame the client receives, in send order: binary output frames and JSON replies share one list."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any] | bytes] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


async def _until(condition) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.01)


@pytest.fixture
async def pending_output(tmp_path, monkeypatch):
    """A live PTY child that has written output the service holds but has not sent: the batch timer is set so far out
    that only a drain can send it."""
    monkeypatch.setattr(terminals_module, "_COALESCE_MS", 60_000)
    app._PTY_OWNERS.clear()
    socket = OrderRecordingSocket()
    session = app.Session(socket)  # type: ignore[arg-type]
    service = session.terminals
    term = service.create(
        pane_id="pane-1", agent_key=None, cwd=str(tmp_path), cols=100, rows=30,
        command=["/bin/sh", "-c", f"printf {OLD_WIDTH_OUTPUT}; exec sleep 30"])
    await _until(lambda: service._out_buffers.get(term.id))  # noqa: SLF001
    assert not socket.sent, "the output was sent on its own: the batch timer was not held off"
    yield session, service, socket, term
    await service.kill(term.id, force=True)
    app._PTY_OWNERS.clear()


async def _reattach(session: app.Session, term_id: str, **size: int) -> dict[str, Any]:
    await app.handle_message(session, {
        "id": "r1", "type": "terminal.reattach", "payload": {"terminal_session_ids": [term_id], **size}})
    return session.websocket.sent[-1]  # type: ignore[attr-defined,no-any-return]


async def test_a_reattach_with_a_size_sends_the_pending_output_before_the_reply_and_redraws(pending_output, monkeypatch):
    session, service, socket, term = pending_output
    redraws: list[tuple[str, int, int]] = []
    real_redraw = service.force_redraw

    def recording_redraw(session_id: str, cols: int, rows: int) -> None:
        redraws.append((session_id, cols, rows))
        real_redraw(session_id, cols, rows)

    monkeypatch.setattr(service, "force_redraw", recording_redraw)

    reply = await _reattach(session, term.id, cols=120, rows=40)

    assert reply["payload"]["alive"] == [term.id]
    assert reply is socket.sent[-1] and isinstance(reply, dict), "the reply is the last frame the client gets"
    output = [i for i, frame in enumerate(socket.sent) if isinstance(frame, bytes) and OLD_WIDTH_OUTPUT.encode() in frame]
    assert output and output[0] < len(socket.sent) - 1, "the pending output did not reach the client before the reply"
    assert redraws == [(term.id, 120, 40)], "the program was not nudged to repaint at the new size"


async def test_a_reattach_without_a_size_neither_drains_nor_redraws(pending_output, monkeypatch):
    session, service, socket, term = pending_output
    redraws: list[Any] = []
    monkeypatch.setattr(service, "force_redraw", lambda *args: redraws.append(args))

    reply = await _reattach(session, term.id, cols=0, rows=0)

    assert reply["payload"]["alive"] == [term.id]
    assert not any(isinstance(frame, bytes) for frame in socket.sent), "nothing orders the output against the reply"
    assert redraws == []
