"""The rescan gate must see a commit that only the SQLite write-ahead log holds.

A store opened in WAL mode takes commits into `<store>-wal`; the main file's mtime stays put until a checkpoint. The rescan
loop is the safety net for a change the filesystem watcher did not announce (macOS delivered no event for a WAL append in the
observe-vendors parity test), so its "changed since the last sweep" gate has to count the log, not the main file alone.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from agent_team_backend.log_readers.watcher import LogWatcher


async def _sink(_usage):
    return None


def _watcher() -> LogWatcher:
    return LogWatcher(sink=_sink)


def test_a_commit_held_in_the_wal_makes_the_store_a_changed_file(tmp_path: Path):
    store = tmp_path / "sessions.db"
    writer = sqlite3.connect(store)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")  # as a live CLI's long-lived connection: no checkpoint between commits
        writer.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
        writer.commit()
        watcher = _watcher()
        assert watcher._changed_since_last_scan([store]) == [store], "first sight"  # noqa: SLF001
        assert watcher._changed_since_last_scan([store]) == [], "nothing happened since"  # noqa: SLF001

        main_mtime = store.stat().st_mtime_ns
        writer.execute("INSERT INTO messages (body) VALUES ('row')")
        writer.commit()
        assert store.stat().st_mtime_ns == main_mtime, "the premise: the main file did not move"

        assert watcher._changed_since_last_scan([store]) == [store], "a commit in the log is a change"  # noqa: SLF001
        assert watcher._changed_since_last_scan([store]) == [], "and is reported once"  # noqa: SLF001
    finally:
        writer.close()


def test_a_truncating_checkpoint_is_a_change_reported_once(tmp_path: Path):
    store = tmp_path / "sessions.db"
    writer = sqlite3.connect(store)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
        writer.execute("INSERT INTO messages (body) VALUES ('row')")
        writer.commit()
        watcher = _watcher()
        assert watcher._changed_since_last_scan([store]) == [store]  # noqa: SLF001
        assert (tmp_path / "sessions.db-wal").stat().st_size > 0, "the premise: the log holds the commit"

        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert (tmp_path / "sessions.db-wal").stat().st_size == 0, "the premise: the log was truncated"

        assert watcher._changed_since_last_scan([store]) == [store], "a truncating checkpoint is a change"  # noqa: SLF001
        assert watcher._changed_since_last_scan([store]) == [], "and is reported once"  # noqa: SLF001
    finally:
        writer.close()


def test_a_db_without_a_wal_keeps_its_mtime_gate(tmp_path: Path):
    store = tmp_path / "plain.db"
    conn = sqlite3.connect(store)
    try:
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
    finally:
        conn.close()
    assert not (tmp_path / "plain.db-wal").exists()
    watcher = _watcher()
    assert watcher._changed_since_last_scan([store]) == [store]  # noqa: SLF001
    assert watcher._changed_since_last_scan([store]) == []  # noqa: SLF001
    os.utime(store, ns=(store.stat().st_atime_ns, store.stat().st_mtime_ns + 5_000_000))
    assert watcher._changed_since_last_scan([store]) == [store], "a moved main file is a change"  # noqa: SLF001


def test_a_wal_restart_that_keeps_the_size_is_a_change(tmp_path: Path):
    store = tmp_path / "sessions.db"
    wal = tmp_path / "sessions.db-wal"
    store.write_bytes(b"main")
    wal.write_bytes(b"x" * 64)
    watcher = _watcher()
    assert watcher._changed_since_last_scan([store]) == [store]  # noqa: SLF001
    assert watcher._changed_since_last_scan([store]) == []  # noqa: SLF001

    # A restart overwrites the frames from the start: same size, newer log mtime, main file untouched.
    os.utime(wal, ns=(wal.stat().st_atime_ns, wal.stat().st_mtime_ns + 1))
    assert wal.stat().st_size == 64
    assert watcher._changed_since_last_scan([store]) == [store], "one nanosecond on the log is enough"  # noqa: SLF001


def test_a_plain_file_keeps_its_mtime_gate(tmp_path: Path):
    log = tmp_path / "session.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    watcher = _watcher()
    assert watcher._changed_since_last_scan([log]) == [log]  # noqa: SLF001
    assert watcher._changed_since_last_scan([log]) == []  # noqa: SLF001
