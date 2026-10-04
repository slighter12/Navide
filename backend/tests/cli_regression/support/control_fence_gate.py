"""Owned test-only ACK gate; production dispatch, native runtime and sends run.

No fake terminal or native response. Files coordinate deliberately delayed public
ACKs so the fixture child can produce bytes while a control fence is held.
"""
import asyncio
import json
from pathlib import Path


def install(root: Path) -> None:
    from agent_team_backend import app

    send = app.Session.send_json
    dispatch = app.handle_message
    entered = 0
    acknowledgments = 0
    types = {"terminal.resize", "terminal.redraw", "terminal.reattach"}

    async def handle(session, message):
        nonlocal entered
        if message.get("type") in types:
            entered += 1
            (root / f"control-entered-{entered}.json").write_text(json.dumps({
                "type": message["type"], "id": message["id"],
            }))
        await dispatch(session, message)

    async def delayed_send(session, frame):
        nonlocal acknowledgments
        if frame.get("id") and frame.get("type", "").removesuffix(".result") in types:
            acknowledgments += 1
            number = acknowledgments
            (root / f"control-ack-{number}.json").write_text(json.dumps({
                "type": frame["type"], "id": frame["id"], "ok": frame.get("ok"),
            }))
            async with asyncio.timeout(15):
                while not (root / f"control-release-{number}").exists():
                    await asyncio.sleep(0.005)
        await send(session, frame)

    app.handle_message = handle
    app.Session.send_json = delayed_send
