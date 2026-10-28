"""Copilot's marker binding through a started LogWatcher with the platform's real file observer.

Real SQLite commits drive real file events (FSEvents on macOS, inotify on Linux); nothing is injected by hand. A touch of
the store while a writer transaction holds the marker row uncommitted makes the sink miss it first; the commit that
follows must still bind the pane through the events it raises (the `.db-wal` write, or the `-journal` deletion).
"""
from __future__ import annotations

import asyncio
import os
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
)


async def _noop(_usage: TokenUsage) -> None:
    return None


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
async def test_a_real_commit_binds_the_pane_after_a_pass_that_missed_it(journal_mode, tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(home))
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir()
    store = home / "session-store.db"

    setup = sqlite3.connect(store)
    setup.execute(f"PRAGMA journal_mode={journal_mode}")
    setup.executescript(SCHEMA)
    setup.execute("INSERT INTO sessions VALUES (?,?,?)", (SESSION, workspace, "2026-09-29T10:00:00Z"))
    setup.commit()
    setup.close()

    reader = CopilotLogReader()
    attribution = Attribution([reader], workspaces_path=tmp_path / "ws.json")
    attribution.register_pane("pane-1", vendor="copilot", cwd=workspace, workspace_path=workspace, session_marker=MARKER)
    missed = asyncio.Event()
    bound = asyncio.Event()

    async def session_sink(vendor: str, path: Path) -> None:
        if path != store:
            return
        usage = TokenUsage(vendor=vendor, input_tokens=0, output_tokens=0, cwd=reader.cwd_from_file(path),
                           session_id=reader.session_id_from_path(path), file_path=str(path), dedup_key="")
        if await asyncio.to_thread(attribution.maybe_announce_session, usage):
            bound.set()
        else:
            missed.set()

    watcher = LogWatcher(sink=_noop, session_sink=session_sink, seen_path=tmp_path / "seen.json")
    watcher.add_reader(reader)
    watcher.start()
    writer = sqlite3.connect(store, isolation_level=None)
    try:
        writer.execute("BEGIN")
        writer.execute("INSERT INTO turns VALUES (1,?,0,?,?,?)",
                       (SESSION, f"{MARKER} work", "reply", "2026-09-29T10:00:00Z"))
        os.utime(store)  # a real modify event while the row is uncommitted: the sink misses the marker
        async with asyncio.timeout(15):
            await missed.wait()
        assert not bound.is_set()
        writer.execute("COMMIT")
        async with asyncio.timeout(15):
            await bound.wait()
    finally:
        writer.close()
        watcher.stop()
