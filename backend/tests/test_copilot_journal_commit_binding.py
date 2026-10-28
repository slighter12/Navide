"""Copilot's marker binding must survive a store commit that becomes visible only after the last .db event.

The store is a rollback-journal SQLite file. A commit rewrites the .db pages (events) and then deletes the
`-journal` file (the commit point, one more event). A sink pass run for the .db events can read the store before the
commit is visible (the writer holds the lock, or has not committed), and the journal deletion is then the only event
left. The watcher must route that event to the sink, or the pane is never bound (the store is in its baseline, so the
single-candidate fallback cannot rescue it).
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from watchdog.events import FileDeletedEvent, FileModifiedEvent

from agent_team_backend.cli_vendors import copilot
from agent_team_backend.cli_vendors.copilot import CopilotLogReader
from agent_team_backend.log_readers.attribution import Attribution
from agent_team_backend.log_readers.base import TokenUsage
from agent_team_backend.log_readers.watcher import LogWatcher, _Handler

pytestmark = pytest.mark.asyncio

MARKER = "at-pane:regression-marker"
SESSION = "12345678-1234-4234-8234-123456789abc"
SCHEMA = (
    "CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT,created_at TEXT);"
    "CREATE TABLE turns(id INTEGER PRIMARY KEY,session_id TEXT,turn_index INTEGER,user_message TEXT,"
    "assistant_response TEXT,timestamp TEXT);"
)


async def _noop(_usage: TokenUsage) -> None:
    return None


@pytest.mark.parametrize("variant", ["busy", "precommit", "control"])
async def test_binding_survives_a_commit_visible_only_at_the_journal_deletion(variant, tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(home))
    monkeypatch.setattr(copilot, "_BUSY_TIMEOUT_MS", 0)  # a locked store answers at once instead of after a wait
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir()
    store = home / "session-store.db"
    journal = Path(f"{store}-journal")

    setup = sqlite3.connect(store)
    setup.executescript(SCHEMA)
    setup.execute("INSERT INTO sessions VALUES (?,?,?)", (SESSION, workspace, "2026-09-29T10:00:00Z"))
    setup.commit()
    setup.close()

    reader = CopilotLogReader()
    attribution = Attribution([reader], workspaces_path=tmp_path / "ws.json")
    attribution.register_pane("pane-1", vendor="copilot", cwd=workspace, workspace_path=workspace, session_marker=MARKER)

    passes = 0
    bound = asyncio.Event()
    first_pass = asyncio.Event()

    async def session_sink(vendor: str, path: Path) -> None:
        nonlocal passes
        usage = TokenUsage(vendor=vendor, input_tokens=0, output_tokens=0, cwd=reader.cwd_from_file(path),
                           session_id=reader.session_id_from_path(path), file_path=str(path), dedup_key="")
        binding = await asyncio.to_thread(attribution.maybe_announce_session, usage)
        passes += 1
        first_pass.set()
        if binding:
            bound.set()

    watcher = LogWatcher(sink=_noop, session_sink=session_sink, seen_path=tmp_path / "seen.json")
    watcher.add_reader(reader)
    # The handler is wired as LogWatcher.start() wires it; events are delivered by hand, in the order a commit makes them.
    handler = _Handler(lambda path: watcher._queue.put_nowait((path, "")),  # noqa: SLF001
                       lambda text: any(r.accepts_watch_path(text) for r in watcher._readers))  # noqa: SLF001
    drain = asyncio.create_task(watcher._drain_loop())  # noqa: SLF001

    writer = sqlite3.connect(store, isolation_level=None)
    try:
        if variant == "busy":
            writer.execute("BEGIN EXCLUSIVE")
        elif variant == "precommit":
            writer.execute("BEGIN")
        else:
            writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO turns VALUES (1,?,0,?,?,?)",
                       (SESSION, f"{MARKER} work", "reply", "2026-09-29T10:00:00Z"))
        if variant == "control":
            writer.execute("COMMIT")
        # The .db event of the write: the sink reads the store while the commit is not visible (or, for the control,
        # after it).
        handler.dispatch(FileModifiedEvent(str(store)))
        async with asyncio.timeout(10):
            await first_pass.wait()
        if variant != "control":
            assert not bound.is_set(), "the marker was visible before the commit"
            writer.execute("COMMIT")
            assert not journal.exists(), "the commit's last file event is the journal's deletion"
            handler.dispatch(FileDeletedEvent(str(journal)))
        try:
            async with asyncio.timeout(5):
                await bound.wait()
        except TimeoutError:
            pytest.fail(f"session never bound after the commit completed ({variant}); sink passes: {passes}")
    finally:
        writer.close()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
