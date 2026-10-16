"""Owned test-only fault for the review-9 F1 proof: the backend REFUSES `terminal.kill` and
`manual_pane.release_pty` for one pane without acting on them.

Installed only in a disposable backend under NAVIDE_RETIREMENT_REFUSAL_GATE=1. While
`<root>/refuse-retirement` names a pane id (and, for kills, its terminal id), the two handlers
answer an error and do nothing, so the pane's process demonstrably stays alive — the case a renderer
must not treat as retired. Removing the file restores the real handlers; nothing else changes.
"""
import json
from pathlib import Path

from .fixture_exec import stable_file


def install(root: Path) -> None:
    from agent_team_backend import ws_handlers
    from agent_team_backend.ipc import make_error

    kill, release = ws_handlers._REGISTRY["terminal.kill"], ws_handlers._REGISTRY["manual_pane.release_pty"]

    def refused(payload: dict) -> bool:
        marker = root / "refuse-retirement"
        try:
            # The evaluation's stable read: ancestor descriptors, no-follow, non-blocking, a
            # regular file only, and pre/post identity checks (raises PermissionError otherwise).
            data, _identity = stable_file(marker)
        except FileNotFoundError:
            return False
        target = json.loads(data)
        return (payload.get("pane_id") == target["pane_id"]
                or payload.get("terminal_session_id") == target.get("terminal_session_id"))

    async def gated_kill(session, msg_id, msg_type, payload):
        if refused(payload):
            await session.send_json(make_error(msg_id, msg_type, "TEST_FAULT", "kill refused (injected)"))
            return
        await kill(session, msg_id, msg_type, payload)

    async def gated_release(session, msg_id, msg_type, payload):
        if refused(payload):
            await session.send_json(make_error(msg_id, msg_type, "TEST_FAULT", "release refused (injected)"))
            return
        await release(session, msg_id, msg_type, payload)

    ws_handlers._REGISTRY["terminal.kill"] = gated_kill
    ws_handlers._REGISTRY["manual_pane.release_pty"] = gated_release
