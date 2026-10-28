"""Copilot's marker binding must not be lost when a WAL commit lands between a pass's session sink and its activity parse.

In one watcher pass the session sink reads the store first and `parse_activity` reads it afterwards. A WAL commit
(one `.db-wal` write, then a header update in the shared-memory file that raises no file event) can become visible
between the two reads: the sink misses the marker, `parse_activity` reads the committed turn and reports its
`turn_complete`, and no later event comes for the sink to try again. The binding must reach the sink before that
turn_complete is delivered.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from agent_team_backend.cli_vendors.copilot import CopilotLogReader
from agent_team_backend.log_readers.attribution import Attribution
from agent_team_backend.log_readers.base import TokenUsage
from agent_team_backend.log_readers.watcher import LogWatcher

pytestmark = pytest.mark.asyncio

MARKER = "at-pane:regression-marker"
SESSION = "12345678-1234-4234-8234-123456789abc"
SCHEMA = (
    "CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT,created_at TEXT);"
    "CREATE TABLE turns(id INTEGER PRIMARY KEY,session_id TEXT,turn_index INTEGER,user_message TEXT,"
    "assistant_response TEXT,timestamp TEXT);"
    "CREATE TABLE assistant_usage_events(id INTEGER PRIMARY KEY,session_id TEXT,model TEXT,input_tokens INTEGER,"
    "output_tokens INTEGER,cache_read_tokens INTEGER,cache_write_tokens INTEGER,reasoning_tokens INTEGER,created_at TEXT);"
)


async def _noop(_usage: TokenUsage) -> None:
    return None


async def test_session_binds_before_the_turn_complete_of_the_commit_it_missed(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(home))
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir()
    store = home / "session-store.db"

    setup = sqlite3.connect(store)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.executescript(SCHEMA)
    setup.execute("INSERT INTO sessions VALUES (?,?,?)", (SESSION, workspace, "2026-09-29T10:00:00Z"))
    setup.commit()
    setup.close()

    reader = CopilotLogReader()
    attribution = Attribution([reader], workspaces_path=tmp_path / "ws.json")
    attribution.register_pane("pane-1", vendor="copilot", cwd=workspace, workspace_path=workspace, session_marker=MARKER)

    order: list[str] = []
    writer = sqlite3.connect(store, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO turns VALUES (1,?,0,?,?,?)", (SESSION, f"{MARKER} work", "reply", "2026-09-29T10:00:00Z"))
    commit_pending = True

    async def session_sink(vendor: str, path: Path) -> None:
        nonlocal commit_pending
        usage = TokenUsage(vendor=vendor, input_tokens=0, output_tokens=0, cwd=reader.cwd_from_file(path),
                           session_id=reader.session_id_from_path(path), file_path=str(path), dedup_key="")
        binding = await asyncio.to_thread(attribution.maybe_announce_session, usage)
        if binding:
            order.append("session.detected")
        if commit_pending:
            # The pass's session sink has read the store; the commit becomes visible before its activity parse.
            commit_pending = False
            writer.execute("COMMIT")

    async def activity_sink(event) -> None:
        if event.event_type == "turn_complete":
            order.append("turn_complete")

    watcher = LogWatcher(sink=_noop, activity_sink=activity_sink, session_sink=session_sink,
                         seen_path=tmp_path / "seen.json")
    watcher.add_reader(reader)
    try:
        await watcher._process_realtime_path(store)  # noqa: SLF001 — one watcher pass, as the drain loop runs it
    finally:
        writer.close()
    assert order == ["session.detected", "turn_complete"], order


async def test_a_bound_pane_re_runs_the_sink_without_reading_the_store(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(home))
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir()
    store = home / "session-store.db"

    writer = sqlite3.connect(store, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.executescript(SCHEMA)
    writer.execute("INSERT INTO sessions VALUES (?,?,?)", (SESSION, workspace, "2026-09-29T10:00:00Z"))
    writer.execute("INSERT INTO turns VALUES (1,?,0,?,?,?)", (SESSION, f"{MARKER} work", "reply", "2026-09-29T10:00:00Z"))

    reader = CopilotLogReader()
    lookups = 0
    original = reader.find_sessions_by_marker

    def counted(markers):
        nonlocal lookups
        lookups += 1
        return original(markers)

    monkeypatch.setattr(reader, "find_sessions_by_marker", counted)
    attribution = Attribution([reader], workspaces_path=tmp_path / "ws.json")
    attribution.register_pane("pane-1", vendor="copilot", cwd=workspace, workspace_path=workspace, session_marker=MARKER)
    bindings: list[object] = []

    async def session_sink(vendor: str, path: Path) -> None:
        usage = TokenUsage(vendor=vendor, input_tokens=0, output_tokens=0, cwd=reader.cwd_from_file(path),
                           session_id=reader.session_id_from_path(path), file_path=str(path), dedup_key="")
        if binding := await asyncio.to_thread(attribution.maybe_announce_session, usage):
            bindings.append(binding)

    completions = 0

    async def activity_sink(event) -> None:
        nonlocal completions
        completions += event.event_type == "turn_complete"

    watcher = LogWatcher(sink=_noop, activity_sink=activity_sink, session_sink=session_sink,
                         seen_path=tmp_path / "seen.json")
    watcher.add_reader(reader)
    try:
        await watcher._process_realtime_path(store)  # noqa: SLF001
        assert len(bindings) == 1 and completions == 1
        bound_lookups = lookups
        writer.execute("INSERT INTO turns VALUES (2,?,1,?,?,?)", (SESSION, "next prompt", "reply 2", "2026-09-29T10:01:00Z"))
        await watcher._process_realtime_path(store)  # noqa: SLF001
    finally:
        writer.close()
    assert completions == 2, "the second pass must deliver the new turn"
    assert lookups == bound_lookups, "a bound pane must not read the store again"
    assert len(bindings) == 1
