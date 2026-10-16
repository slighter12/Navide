"""Review-10 B4 (the window ticket 05 introduced): a retirement reads its target, awaits the
settlement of that target, then writes the store. The write must retire only the pane id that was
settled; a record that the same address/session resolves to by then (a new slot pane, a new
same-session pane) is never retired by the earlier request, and the request answers an error."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent_team_backend import app


class Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class HeldTerminals:
    """A published session per pane; the kill of `held_pane` waits until released."""

    def __init__(self, by_pane: dict[str, list[str]], held_pane: str) -> None:
        self.by_pane, self.held_pane = by_pane, held_pane
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.killed: list[str] = []

    def live_session_ids_for_pane(self, pane_id: str) -> list[str]:
        return list(self.by_pane.get(pane_id, []))

    async def kill(self, session_id: str, force: bool = False) -> None:
        pane = next(p for p, ids in self.by_pane.items() if session_id in ids)
        if pane == self.held_pane:
            self.entered.set()
            await self.release.wait()
        self.killed.append(session_id)
        self.by_pane[pane].remove(session_id)


def _session(terminals: HeldTerminals) -> "app.Session":
    session = app.Session(Socket())  # type: ignore[arg-type]
    session.terminals = terminals  # type: ignore[assignment]
    return session


def _status(ws: str, pane_id: str) -> str:
    project = app.project_store.load_or_create(ws)
    return next(p.spawn_status for p in project.panes if p.pane_id == pane_id)


@pytest.mark.asyncio
async def test_slot_unspawn_never_retires_a_pane_the_slot_was_reassigned_to_during_settlement(tmp_path: Path) -> None:
    ws = str(tmp_path)
    app.project_store.start_pipeline(ws, task_description="t", total_stages=1, stage_blueprint=[
        {"stage_id": "01", "title": "Build", "slots": [{"label": "Build", "agent": "codex", "role": "eng"}]}])
    app.project_store.record_slot_spawn(ws, stage_index=0, slot_label="Build", pane_id="P", agent="codex")
    terminals = HeldTerminals({"P": ["term-P"], "Q": ["term-Q"]}, held_pane="P")
    session = _session(terminals)
    request = asyncio.create_task(app.handle_message(session, {"id": "u1", "type": "pipeline.slot_unspawn",
        "payload": {"workspace_path": ws, "stage_index": 0, "slot_label": "Build"}}))
    await asyncio.wait_for(terminals.entered.wait(), 5)
    # A legitimate restart reassigns the slot to a new pane Q while P is being settled.
    app.project_store.record_slot_spawn(ws, stage_index=0, slot_label="Build", pane_id="Q", agent="codex")
    terminals.release.set()
    await asyncio.wait_for(request, 5)

    reply = session.websocket.sent[-1]  # type: ignore[attr-defined]
    assert _status(ws, "Q") == "spawned", "the earlier request retired the slot's new, unsettled pane"
    assert terminals.killed == ["term-P"]
    assert reply.get("ok") is not True, reply
    assert reply["error"]["code"] == "PANE_RELEASE_UNRESOLVED", reply


@pytest.mark.asyncio
async def test_manual_unspawn_never_retires_a_same_session_pane_added_during_settlement(tmp_path: Path) -> None:
    ws = str(tmp_path)
    # A drifted record: the request names a pane id the store does not hold, so the session matches.
    app.project_store.record_manual_pane_spawn(ws, pane_id="A", agent="claude", session_id="S")
    terminals = HeldTerminals({"gone": ["term-x"], "A": ["term-A"], "B": ["term-B"]}, held_pane="A")
    session = _session(terminals)
    request = asyncio.create_task(app.handle_message(session, {"id": "u1", "type": "manual_pane.unspawn",
        "payload": {"workspace_path": ws, "pane_id": "gone", "session_id": "S"}}))
    await asyncio.wait_for(terminals.entered.wait(), 5)
    # A new plain pane resuming the same session is spawned while A is being settled.
    app.project_store.record_manual_pane_spawn(ws, pane_id="B", agent="claude", session_id="S")
    terminals.release.set()
    await asyncio.wait_for(request, 5)

    reply = session.websocket.sent[-1]  # type: ignore[attr-defined]
    assert _status(ws, "B") == "spawned", "the earlier request retired a same-session pane it never settled"
    assert "term-B" not in terminals.killed
    assert reply.get("ok") is not True, reply
    assert reply["error"]["code"] == "PANE_RELEASE_UNRESOLVED", reply
