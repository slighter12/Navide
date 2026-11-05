"""Copilot's store, read through the Python reader and the real sidecar: what the declaration (`copilot.json`) decides at the
boundary between the two. Every store here is a real SQLite file in the schema the live CLI wrote (`tests.vendors.test_copilot`).

The reader under test is the native one (`native_reader`); its `observe_core` is wired straight to the sidecar (`FramePeer`), the
way `test_sidecar_observes_each_ported_vendor` asks it, so no backend process is involved. A missing sidecar binary fails these
tests, it never skips them.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
from tests.native_reader_peer import wire
from tests.vendors.test_copilot import (_SID, _STORE_SCHEMA, _add_session, _add_usage, _assistant, _shutdown, _store, _user,
                                        _write_session)

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(120)]


@pytest.fixture
def fake_copilot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    return root


@pytest.fixture(autouse=True)
def _rearm_store_warning() -> None:
    _reset_db_warning()


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """The real sidecar, wired to `observe_core`; `asked` lists every request it was sent."""
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


@pytest.fixture
def native(sidecar):
    """A Copilot reader on the observation core whose every observation is the real sidecar's answer."""
    return cli_runtime_native.native_reader(CopilotLogReader, "copilot")()


def _store_without_turns(root: Path) -> Path:
    """A store the CLI has not written a turn into yet: everything but the `turns` table."""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "session-store.db"
    schema = "".join(statement + ";" for statement in _STORE_SCHEMA.split(";") if statement.strip() and "TABLE turns" not in statement)
    connection = sqlite3.connect(db)
    connection.executescript(schema)
    connection.commit()
    connection.close()
    return db


def _kinds(events) -> list[tuple[str, str]]:
    return [(event.event_type, event.detail) for event in events]


def test_a_store_without_turns_still_answers_its_usage_heartbeats_with_one_warning(fake_copilot_root, native, caplog):
    """`compat.store_error_reported`: the failed query is reported once, as the baseline reader reported it, and the pass
    still delivers what it did read with the bag it kept. The next pass, once `turns` exists, answers the turns."""
    db = _store_without_turns(fake_copilot_root)
    _add_session(db)
    for _ in range(3):
        _add_usage(db)
    seen: set[str] = set()
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.copilot"):
        first = native.parse_activity(db, seen)
        again = native.parse_activity(db, seen)
    assert _kinds(first) == [("agent_active", "assistant")] * 3, "the usage heartbeats are answered"
    assert again == [], "the bag the pass kept stops the same rows from being answered twice"
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "session-store" in warnings[0].getMessage()
    connection = sqlite3.connect(db)
    connection.executescript("CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
                             " turn_index INTEGER NOT NULL, user_message TEXT, assistant_response TEXT, timestamp TEXT,"
                             " UNIQUE(session_id, turn_index));")
    connection.execute("INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp)"
                       " VALUES (?, 0, 'fix it', 'done', '2026-09-29T10:00:00Z')", (_SID,))
    connection.commit()
    connection.close()
    assert _kinds(native.parse_activity(db, seen)) == [("agent_active", "user"), ("turn_complete", "turn_row")]


def _fill(db: Path, usage: int, turns: int, reply: str = "a") -> None:
    connection = sqlite3.connect(db)
    connection.executemany(
        "INSERT INTO assistant_usage_events (session_id, turn_index, model, input_tokens, output_tokens, created_at)"
        " VALUES (?, 0, 'm', 1, 1, '2026-09-29T10:00:00Z')", [(_SID,)] * usage)
    connection.executemany(
        "INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp)"
        " VALUES (?, ?, 'u', ?, '2026-09-29T10:00:00Z')", [(_SID, index, reply) for index in range(turns)])
    connection.commit()
    connection.close()


def test_a_history_over_the_observation_cap_is_read_by_one_call_and_no_heartbeat_follows_its_last_turn_end(
        fake_copilot_root, native, sidecar):
    """P2, Q1 and U1 (t36j): every sidecar answer is bounded by the answer ledger (3,637,248 bytes of events, under the 8 MiB document
    cap) and by the declared page (256 marks a walk). 9,000 usage rows and 300 turns with 64 KB replies are 36 full usage pages and
    several ledger passes of turns. The baseline has no limit and reads all of it in one call, so ONE `parse_activity` call must deliver
    all of it too, in the baseline's order, ending in the last turn end; a second call finds nothing. No loop in this test."""
    db = _store(fake_copilot_root)
    _add_session(db)
    _fill(db, usage=9000, turns=300, reply="r" * 65536)
    baseline = CopilotLogReader().parse_activity(db, set())
    history = sum(len(event.text or "") for event in baseline)
    assert history > 8 * 1024 * 1024, "the history alone is over the document cap"
    seen: set[str] = set()
    asked = len(sidecar.asked)
    events = native.parse_activity(db, seen)
    assert len(sidecar.asked) - asked > 36, "the call went on while the sidecar said `more`: a page per answer, then the ledger passes"
    kinds = [(event.event_type, event.detail) for event in events]
    assert kinds.count(("agent_active", "assistant")) == 9000 and kinds.count(("turn_complete", "turn_row")) == 300
    assert [event.dedup_key for event in events] == [event.dedup_key for event in baseline]
    assert events[-1].event_type == "turn_complete" and events[-1].dedup_key == baseline[-1].dedup_key, "the last turn end arrives in this call"
    last_turn_end = max(index for index, kind in enumerate(kinds) if kind[0] == "turn_complete")
    last_heartbeat = max(index for index, kind in enumerate(kinds) if kind == ("agent_active", "assistant"))
    assert last_heartbeat < last_turn_end, "no heartbeat of the session follows its last turn_complete"
    assert native.parse_activity(db, seen) == [], "the bag the call kept leaves nothing to read"


def test_one_call_over_more_new_rows_than_a_page_reads_what_the_baseline_reads_in_its_order(fake_copilot_root, native, sidecar):
    """U1 (t36j): the declared page bounds a sidecar answer (256 rows per walk), not a call. 700 usage rows and 700 turns: the walk of
    the turns waits while the usage page is full, so every heartbeat comes before every turn end, and ONE call delivers both
    walks to their end (the last answer is short; an exactly full last page would cost one empty answer)."""
    db = _store(fake_copilot_root)
    _add_session(db)
    _fill(db, usage=700, turns=700)
    baseline = CopilotLogReader().parse_activity(db, set())
    seen: set[str] = set()
    asked = len(sidecar.asked)
    events = native.parse_activity(db, seen)
    assert len(sidecar.asked) - asked == 5, "usage 256+256+188 with the turns' first 256 beside the short one, then turns 256 and 188"
    kinds = [(event.event_type, event.detail) for event in events]
    assert [event.dedup_key for event in events] == [event.dedup_key for event in baseline]
    assert events[-1].event_type == "turn_complete", "the last turn end arrives in this call, not at the next write of the store"
    assert max(index for index, kind in enumerate(kinds) if kind == ("agent_active", "assistant")) < \
        min(index for index, kind in enumerate(kinds) if kind[0] == "turn_complete")
    assert native.parse_activity(db, seen) == []


def test_one_incremental_call_over_more_usage_rows_than_a_page_returns_every_row_and_the_final_checkpoint(fake_copilot_root, native, sidecar):
    """U1 (t36j): the usage cursor's page is 2,000 rows. 4,500 rows: the baseline reads them in one call, so does one call here; the
    checkpoint it returns is the end of the store."""
    db = _store(fake_copilot_root)
    _add_session(db)
    _fill(db, usage=4500, turns=0)
    baseline = CopilotLogReader().parse_incremental(db, {})
    asked = len(sidecar.asked)
    got = native.parse_incremental(db, {})
    assert len(sidecar.asked) - asked == 3, "2,000 + 2,000 + 500"
    assert [event.dedup_key for event in got.events] == [event.dedup_key for event in baseline.events] and len(got.events) == 4500
    assert native.parse_incremental(db, got.checkpoint).events == [], "the checkpoint the call returns is the end of the store"


def _log(root: Path) -> Path:
    """A 1.0.75-style per-session log under `<root>/session-state/<session>/events.jsonl`: one assistant message, one shutdown."""
    return _write_session(root, events=[_user("hi"), _assistant("ok"), _shutdown(input=7, cache_read=0, cache_write=0, output=3)])


def _read_log_in_every_mode(native, log: Path) -> dict[str, int]:
    """How many events each mode answers for the log (the baseline's answer is the same two-way split: all or none)."""
    return {"usage": len(native.parse_incremental(log, {}).events), "session_usage": len(native.parse_session_file(log, set())),
            "activity": len(native.parse_activity(log, set()))}


def test_a_log_whose_session_the_store_lists_is_declined_in_every_mode_and_read_once_the_row_is_absent(fake_copilot_root, native):
    """M6, on Copilot's real declaration: the log sits `up: 3` below the store (`<home>/session-state/<session>/events.jsonl`
    against `<home>/session-store.db`). The store is the authority for a session it lists, so the log is declined in
    every mode, and is read as the fallback it is meant to be when the row is not there."""
    log = _log(fake_copilot_root)
    db = _store(fake_copilot_root)
    reader = CopilotLogReader()
    read = _read_log_in_every_mode(native, log)
    assert read["usage"] == 1 and read["session_usage"] == 1 and read["activity"] > 0, "no row: the log is read"
    baseline = {"usage": len(reader.parse_incremental(log, {}).events), "session_usage": len(reader.parse_session_file(log, set())),
                "activity": len(reader.parse_activity(log, set()))}
    assert read == baseline
    _add_session(db)
    assert reader._db_owns_session(log) is True
    assert _read_log_in_every_mode(native, log) == {"usage": 0, "session_usage": 0, "activity": 0}, "the store lists it: declined"
    connection = sqlite3.connect(db)
    connection.execute("DELETE FROM sessions")
    connection.commit()
    connection.close()
    assert _read_log_in_every_mode(native, log) == read, "the row is gone: the log is read again"


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("kind", ["no-sessions-table", "wrong-columns", "not-a-database"])
def test_a_store_the_ownership_lookup_cannot_read_owns_nothing_so_the_log_is_read_and_the_failure_is_reported_once(
        fake_copilot_root, native, caplog, kind):
    """Compat and P5: `_db_owns_session` answers False for a store it cannot read (a store between two CREATE TABLEs, a table
    of other columns, a file that is no database) and `_query` warns once as it does. The sidecar answers the same: the log is
    read in every mode, the failure travels in the answer as `ownership_error`, and the reader logs it once."""
    log = _log(fake_copilot_root)
    db = fake_copilot_root / "session-store.db"
    if kind == "not-a-database":
        db.write_bytes(b"this is not a sqlite database at all")
    else:
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT)" if kind == "no-sessions-table"
                           else "CREATE TABLE sessions (other TEXT)")
        connection.commit()
        connection.close()
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.copilot"):
        assert CopilotLogReader()._db_owns_session(log) is False
        baseline = _warnings(caplog)
        _reset_db_warning()
        caplog.clear()
        read = _read_log_in_every_mode(native, log)
    assert read["usage"] == 1 and read["session_usage"] == 1 and read["activity"] > 0, "not owned: the log is read"
    assert len(baseline) == 1 and "session-store" in baseline[0]
    assert len(_warnings(caplog)) == 1 and "session-store" in _warnings(caplog)[0], "reported once across the three passes"


def test_a_store_that_answers_the_ownership_lookup_reports_nothing(fake_copilot_root, native, caplog):
    log = _log(fake_copilot_root)
    _add_session(_store(fake_copilot_root), sid="some-other-session")
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.copilot"):
        read = _read_log_in_every_mode(native, log)
    assert read["usage"] == 1 and _warnings(caplog) == []


def test_usage_and_replay_over_a_failing_store_still_deliver_nothing(fake_copilot_root, native):
    """Only the activity pass keeps what it read: a usage pass or a replay that failed answers nothing and keeps its mark."""
    fake_copilot_root.mkdir(parents=True, exist_ok=True)
    db = fake_copilot_root / "session-store.db"
    db.write_bytes(b"this is not a sqlite database at all")
    bag = {"k"}
    assert native.parse_incremental(db, {"row_id": 3}).checkpoint == {"row_id": 3}
    assert native.parse_session_file(db, bag) == [] and bag == {"k"}
    assert native.parse_activity(db, set()) == []
