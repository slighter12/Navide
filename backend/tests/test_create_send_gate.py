"""The owned test-only create-send gate must publish its hold file atomically: a reader that sees
`create-send-held-N.json` must see all of it (review-8 standards gap)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_team_backend import app
from tests.cli_regression.support import create_send_gate


@pytest.mark.asyncio
async def test_the_hold_file_is_never_observable_partially_written(tmp_path, monkeypatch):
    sent: list[dict] = []

    async def real_send(_session, frame):
        sent.append(frame)

    monkeypatch.setattr(app.Session, "send_json", real_send)
    partial: list[str] = []
    final = tmp_path / "create-send-held-1.json"
    real_write_text = Path.write_text

    def observed_write_text(self, data, *args, **kwargs):
        # A concurrent reader polling for the final name, at the worst moment: the file exists
        # (opened for writing) but none of its bytes have been written yet.
        self.touch()
        if final.exists():
            try:
                json.loads(final.read_text() or "")
            except ValueError:
                partial.append(final.read_text())
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", observed_write_text)
    create_send_gate.install(tmp_path)
    frame = {"id": "m1", "type": "terminal.create.result", "ok": True,
             "payload": {"pane_id": "gated-1", "terminal_session_id": "t1", "pid": 7, "create_generation": "g"}}
    sending = asyncio.create_task(app.Session.send_json(object(), frame))
    for _ in range(200):
        if final.exists():
            break
        await asyncio.sleep(0.005)
    assert json.loads(final.read_text())["terminal_session_id"] == "t1"
    (tmp_path / "create-send-release-1").write_text("")
    await asyncio.wait_for(sending, 5)
    assert sent == [frame]
    assert partial == [], f"the hold file was observable before its JSON was complete: {partial!r}"
