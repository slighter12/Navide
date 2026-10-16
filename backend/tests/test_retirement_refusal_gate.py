"""Review-10 standards: the refusal gate's control marker is read through the evaluation's stable,
no-follow ancestor-descriptor reader with pre/post identity checks — a symlinked marker is refused,
never followed."""
from __future__ import annotations

import asyncio
import errno
import json
from pathlib import Path

import pytest

from agent_team_backend import ws_handlers
from tests.cli_regression.support import retirement_refusal_gate


@pytest.fixture
def gate(tmp_path, monkeypatch):
    calls: list[str] = []

    async def kill(session, msg_id, msg_type, payload):
        calls.append("kill")

    async def release(session, msg_id, msg_type, payload):
        calls.append("release")

    registry = dict(ws_handlers._REGISTRY)
    registry["terminal.kill"], registry["manual_pane.release_pty"] = kill, release
    monkeypatch.setattr(ws_handlers, "_REGISTRY", registry)
    retirement_refusal_gate.install(tmp_path)
    return tmp_path, registry, calls


class Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _call(registry, name, payload):
    socket = Socket()
    session = type("S", (), {"send_json": socket.send_json})()
    asyncio.run(registry[name](session, "m1", name, payload))
    return socket.sent


def test_a_regular_marker_refuses_the_named_pane(gate):
    root, registry, calls = gate
    (root / "refuse-retirement").write_text(json.dumps({"pane_id": "p1", "terminal_session_id": "t1"}))
    assert _call(registry, "manual_pane.release_pty", {"pane_id": "p1"})[0]["error"]["code"] == "TEST_FAULT"
    assert calls == []


def test_a_symlinked_marker_is_never_followed(gate, tmp_path_factory):
    root, registry, calls = gate
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "marker.json"
    elsewhere.write_text(json.dumps({"pane_id": "p1"}))
    (root / "refuse-retirement").symlink_to(elsewhere)
    # The no-follow open refuses a symlinked leaf (ELOOP); the gate fails closed, it never reads
    # through the link.
    with pytest.raises(OSError) as refused:
        _call(registry, "manual_pane.release_pty", {"pane_id": "p1"})
    assert refused.value.errno == errno.ELOOP or isinstance(refused.value, PermissionError)
    assert calls == []


def test_no_marker_passes_through(gate):
    root, registry, calls = gate
    assert _call(registry, "manual_pane.release_pty", {"pane_id": "p1"}) == []
    assert calls == ["release"]
