"""Owned test-only gate on the terminal.create SUCCESS send (review-7 R7-1 proofs).

Production dispatch, the native runtime and the real WebSocket send all run; nothing is
faked. For a pane id starting with ``gated-`` the success frame is held at the awaited
send boundary (after the backend committed the create) until the test releases it, so a
cancel on the same connection can be answered while the success is still unsent.
"""
import asyncio
from pathlib import Path

from .runtime_recovery_driver import _publish


def install(root: Path) -> None:
    from agent_team_backend import app

    send = app.Session.send_json
    held = 0

    async def gated_send(session, frame):
        nonlocal held
        payload = frame.get("payload") or {}
        if (frame.get("type") == "terminal.create.result" and frame.get("ok")
                and str(payload.get("pane_id", "")).startswith("gated-")):
            held += 1
            number = held
            # Atomic (temp + rename, the driver's own publication): a reader that sees the
            # file sees all of it.
            _publish(root, f"create-send-held-{number}", {
                "terminal_session_id": payload.get("terminal_session_id"), "pid": payload.get("pid"),
                "pane_id": payload.get("pane_id"), "create_generation": payload.get("create_generation"),
            })
            async with asyncio.timeout(60):
                while not (root / f"create-send-release-{number}").exists():
                    await asyncio.sleep(0.005)
        await send(session, frame)

    app.Session.send_json = gated_send
