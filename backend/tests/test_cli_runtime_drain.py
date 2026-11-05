"""A bounded pass drains through the production seam, over the real sidecar (t36i T1 and T5).

The sidecar stops a pass at its answer ledger and says `more`; the reader that production calls must go on. These tests make ONE
call of the reader's own method (`parse_activity`, `parse_incremental`, `turns_for_session`) over an input whose backlog is over the
ledger and assert that the end of the backlog (the `turn_complete` of the last turn, the last usage row) arrives in that call. No
test here loops over passes: a loop in a test is what hid the defect from the earlier matrix.

Every log and store uses the shapes the Copilot reader's own tests build (`tests.vendors.test_copilot`). A missing sidecar binary
fails these tests, it never skips them.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.claude import ClaudeLogReader
from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
from tests.native_reader_peer import wire
from tests.vendors import test_cursor as cur
from tests.vendors.test_copilot import (_SID, _STORE_SCHEMA, _add_session, _add_turn, _add_usage, _assistant, _event, _store, _user,
                                        _write_session)

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(600)]

MIB = 1024 * 1024


@pytest.fixture
def copilot_root(tmp_path, monkeypatch):
    root = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    _reset_db_warning()
    return root


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


@pytest.fixture
def native(sidecar):
    return cli_runtime_native.native_reader(CopilotLogReader, "copilot")()


def _shape(events) -> list:
    return [(event.dedup_key, event.event_type, len(event.text or "")) for event in events]


def test_one_activity_call_over_a_log_backlog_past_the_ledger_ends_with_the_last_turn_complete(copilot_root, native, sidecar):
    """40 turns whose replies are 300 KB: 12 MB of events, over the 3,637,248-byte ledger, ending with a `turn_complete`."""
    events = []
    for n in range(40):
        events += [_user("go", eid=f"u-{n}"), _assistant("x" * 300_000, eid=f"a-{n}"), _event("assistant.turn_end", {}, eid=f"e-{n}")]
    log = _write_session(copilot_root, events=events)
    baseline = CopilotLogReader().parse_activity(log, set())
    assert baseline[-1].event_type == "turn_complete", "the backlog ends with a turn end"
    asked = len(sidecar.asked)
    got = native.parse_activity(log, set())
    assert len(sidecar.asked) - asked >= 2, "one pass cannot carry the backlog: the call goes on while the sidecar says `more`"
    assert got[-1].event_type == "turn_complete" and got[-1].dedup_key == baseline[-1].dedup_key, "the last turn end arrives in this one call"
    assert _shape(got) == _shape(baseline)


def test_one_activity_call_over_a_store_backlog_past_the_ledger_ends_with_the_last_turn_complete(copilot_root, native, sidecar):
    """24 turns of a Copilot store whose replies are 300 KB: over the ledger, the last row is a turn end."""
    db = _store(copilot_root)
    _add_session(db)
    for n in range(24):
        _add_turn(db, index=n, user="go", assistant="x" * 300_000)
    baseline = CopilotLogReader().parse_activity(db, set())
    assert baseline[-1].event_type == "turn_complete"
    asked = len(sidecar.asked)
    got = native.parse_activity(db, set())
    assert len(sidecar.asked) - asked >= 2
    assert got[-1].event_type == "turn_complete" and got[-1].dedup_key == baseline[-1].dedup_key
    assert _shape(got) == _shape(baseline)


def test_one_incremental_call_over_a_store_backlog_past_the_ledger_returns_every_usage_row_and_the_final_checkpoint(copilot_root, native, sidecar):
    """Three usage rows of a session whose working directory is 2 MiB (every event repeats it): two do not fit one pass. The reader asks
    again while the store says `more`."""
    db = _store(copilot_root)
    _add_session(db, cwd="/w" + "d" * (2 * MIB))
    for _ in range(3):
        _add_usage(db)
    asked = len(sidecar.asked)
    got = native.parse_incremental(db, {})
    assert len(sidecar.asked) - asked >= 2, "the first pass says `more` and the call goes on from its checkpoint"
    assert len(got.events) == 3
    again = native.parse_incremental(db, got.checkpoint)
    assert again.events == [], "the checkpoint the call returns is the end of the store"


def test_an_error_on_a_later_page_returns_what_the_earlier_pages_delivered_and_one_on_the_first_page_is_raised(copilot_root, native, sidecar, monkeypatch):
    events = []
    for n in range(40):
        events += [_user("go", eid=f"u-{n}"), _assistant("x" * 300_000, eid=f"a-{n}"), _event("assistant.turn_end", {}, eid=f"e-{n}")]
    log = _write_session(copilot_root, events=events)
    real, calls = sidecar.observe_core, {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= fail_at:
            raise RuntimeError("the sidecar went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(cli_runtime_native, "observe_core", flaky)
    fail_at = 2
    got = native.parse_activity(log, set())
    assert calls["n"] == 2 and got, "the second request failed: the first page's events are returned, not lost with the bag that kept them"
    calls["n"], fail_at = 0, 1
    with pytest.raises(RuntimeError, match="went away"):
        native.parse_activity(log, set())


def test_one_cursor_activity_call_over_several_pages_stamps_every_turn_strictly_after_the_one_before(tmp_path, monkeypatch, sidecar):
    """U2 (t36j): Cursor's blobs hold no time, so the baseline stamps a pass from `meta.json`'s `updatedAtMs` and nudges each event 1 ms past
    the previous one (`cursor.py:447-455`), because the frontend admits a `turn_complete` for messaging only when its stamp is strictly
    greater than the last one it processed. One call over 700 replies is two pages of the blob walk (512 + 188), and `updatedAtMs` does not
    move between them: the offset has to go on across the pages, not start again at the second."""
    reader = cur._reader_rooted_at(tmp_path, monkeypatch)
    db = cur._make_store(tmp_path / ".cursor" / "chats", "6" * 32, cur._SID, cwd="/work", updated_at_ms=1786844146562)
    cursor = cli_runtime_native.native_reader(type(reader), "cursor")()
    seen: set[str] = set()
    assert cursor.parse_activity(db, seen) == [], "the first pass of a store only plants its walk"
    cur._append_blobs(db, "x", *[cur._assistant(f"reply {n}") for n in range(700)])
    asked = len(sidecar.asked)
    got = cursor.parse_activity(db, seen)
    assert len(sidecar.asked) - asked >= 2, "700 blobs are more than the walk's page of 512: the call goes on while the sidecar says `more`"
    assert len(got) == 700 and {event.event_type for event in got} == {"turn_complete"}
    stamps = [datetime.fromisoformat(event.timestamp) for event in got]
    assert all(later > earlier for earlier, later in zip(stamps, stamps[1:])), "every turn_complete is stamped after the one before it"
    assert cursor.parse_activity(db, seen) == []


def test_a_failing_usage_query_beside_a_turns_backlog_over_a_page_does_not_stop_the_drain(copilot_root, native, sidecar, caplog, monkeypatch):
    """V2 (t36k, A2 of the t36j checkpoint): under `compat.store_error_reported` an answer that carries the failed query AND `more` (the
    turns walk, no longer waiting for the usage walk the failure ended, came back with a full page) used to end the call after 256 turns.
    The baseline reads the turns with no limit beside the failed query and warns once. ONE call delivers all 300 turns, the last one's
    `turn_complete` included, and the failure is reported once."""
    copilot_root.mkdir(parents=True, exist_ok=True)
    db = copilot_root / "session-store.db"
    schema = "".join(statement + ";" for statement in _STORE_SCHEMA.split(";") if statement.strip() and "TABLE assistant_usage_events" not in statement)
    connection = sqlite3.connect(db)
    connection.executescript(schema)
    connection.commit()
    connection.close()
    _add_session(db)
    connection = sqlite3.connect(db)
    connection.executemany("INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp)"
                           " VALUES (?, ?, 'u', 'a', '2026-09-29T10:00:00Z')", [(_SID, index) for index in range(300)])
    connection.commit()
    connection.close()
    logger = "agent_team_backend.log_readers.copilot"
    with caplog.at_level(logging.WARNING, logger=logger):
        baseline = CopilotLogReader().parse_activity(db, set())
    assert len(baseline) == 600 and baseline[-1].event_type == "turn_complete", "the baseline reads every turn beside the failed query"
    _reset_db_warning()
    caplog.clear()
    asked = len(sidecar.asked)
    seen: set[str] = set()
    with caplog.at_level(logging.WARNING, logger=logger):
        got = native.parse_activity(db, seen)
    assert len(sidecar.asked) - asked >= 2, "300 turns are over the declared page of 256: the call goes on while the sidecar says `more`"
    assert [event.dedup_key for event in got] == [event.dedup_key for event in baseline]
    assert got[-1].event_type == "turn_complete" and got[-1].dedup_key == baseline[-1].dedup_key
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "session-store" in warnings[0], "the failed query is reported once for the call"
    assert native.parse_activity(db, seen) == [], "the bag the call kept leaves nothing to read"

    # A3 (t36b, A3 of the t36k checkpoint): that was one warning because the discovery reader's process-global once-guard
    # (`_warn_db_once`) swallowed the second. Reset the guard before every sidecar answer and each answer that carries the failed query is
    # reported exactly once, whatever the guard does: a reader that warned twice per answer would show here.
    answers = []
    real = cli_runtime_native.observe_core

    def fresh_guard(*args, **kwargs):
        _reset_db_warning()
        answers.append(real(*args, **kwargs))
        return answers[-1]

    monkeypatch.setattr(cli_runtime_native, "observe_core", fresh_guard)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger):
        again = native.parse_activity(db, set())
    failed_answers = [answer for answer in answers if answer.get("error")]
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(again) == len(baseline) and len(failed_answers) >= 2, "a second drain over the same store: every page carries the failed query"
    assert len(warnings) == len(failed_answers), "each answer that carries the failed query is reported exactly once"

    # Sensitivity of that equality, with nothing reverted (t36l Y8, R9 of the t36b review): a reader that warned a second time for each
    # answer that carries the failed query (simulated here by one more warning of the same logger per such answer) does NOT satisfy it.
    log = logging.getLogger(logger)

    def twice(*args, **kwargs):
        answer = fresh_guard(*args, **kwargs)
        if answer.get("error"):
            log.warning("session-store: a second warning for the same answer")
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", twice)
    answers.clear()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger):
        native.parse_activity(db, set())
    failed_again = [answer for answer in answers if answer.get("error")]
    doubled = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(failed_again) >= 2 and len(doubled) == 2 * len(failed_again), "a warning per answer, twice, is counted twice"
    assert len(doubled) != len(failed_again), "so the equality above fails for a reader that warns twice per answer"


def _set_updated_at(db, updated_at_ms: int) -> None:
    """Rewrite `meta.json` the way the CLI does at each turn's commit: the only clock Cursor's blobs have."""
    meta = db.parent / "meta.json"
    body = json.loads(meta.read_text(encoding="utf-8"))
    body["updatedAtMs"] = updated_at_ms
    meta.write_text(json.dumps(body), encoding="utf-8")


def test_cursor_stamps_keep_growing_across_calls_whatever_updated_at_ms_does(tmp_path, monkeypatch, sidecar):
    """V1 (t36k, B1 and A3 of the t36j checkpoint): the frontend's gate (`cliCoordination.ts:52-56`) drops a turn whose stamp is not
    strictly greater than the last one the pane accepted. A drain of 700 replies stamps `base .. base + 699 ms`; the NEXT call must go on
    from there and not start again at `updatedAtMs`: when the clock did not move, when it moved by less than the drain was long, and
    after the store shrank under the walk. A clock that moved past the drain is followed (the stamp is the clock, not pushed beyond
    it). Every call here is ONE reader call; there is no test-side loop."""
    base_ms = 1786844146562
    reader = cur._reader_rooted_at(tmp_path, monkeypatch)
    db = cur._make_store(tmp_path / ".cursor" / "chats", "6" * 32, cur._SID, cwd="/work", updated_at_ms=base_ms)
    cursor = cli_runtime_native.native_reader(type(reader), "cursor")()
    seen: set[str] = set()
    assert cursor.parse_activity(db, seen) == [], "the first pass of a store only plants its walk"

    def call(prefix: str, replies: int, updated_at_ms: int) -> list[datetime]:
        cur._append_blobs(db, prefix, *[cur._assistant(f"{prefix} {n}") for n in range(replies)])
        _set_updated_at(db, updated_at_ms)
        got = cursor.parse_activity(db, seen)
        assert len(got) == replies and {event.event_type for event in got} == {"turn_complete"}
        stamps = [datetime.fromisoformat(event.timestamp) for event in got]
        assert all(later > earlier for earlier, later in zip(stamps, stamps[1:])), "strictly increasing inside the call"
        return stamps

    def at(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    asked = len(sidecar.asked)
    drained = call("a", 700, base_ms)
    assert len(sidecar.asked) - asked >= 2 and drained[0] == at(base_ms), "a large backlog: two pages, from the clock"
    last = drained[-1]
    assert last == at(base_ms + 699)
    unchanged = call("b", 1, base_ms)
    assert unchanged[0] > last, "updatedAtMs did not move: the stamp is still after the drain's last"
    last = unchanged[-1]
    nudged = call("c", 2, base_ms + 10)
    assert nudged[0] > last, "updatedAtMs moved by less than the drain was long: still after the last stamp given"
    last = nudged[-1]
    later = call("d", 1, base_ms + 60_000)
    assert later[0] == at(base_ms + 60_000) and later[0] > last, "a clock that moved past the drain is followed, not pushed beyond"
    last = later[-1]
    connection = sqlite3.connect(db)
    connection.execute("DELETE FROM blobs")
    connection.commit()
    connection.close()
    cur._append_blobs(db, "e", cur._assistant("a store that began again"))
    assert cursor.parse_activity(db, seen) == [], "a store shorter than the walk's position plants the walk again and delivers nothing"
    after_shrink = call("f", 1, base_ms + 60_000)
    assert after_shrink[0] > last, "the walk's position was re-anchored, the pane's gate was not: the stamp floor stays"


def test_a_cursor_bag_works_for_the_next_reader_after_a_switch_in_either_direction(tmp_path, monkeypatch, sidecar):
    """W1 (t36b, B1 of the t36k checkpoint): the watcher keeps ONE bag per store, whichever reader (native or Python) the setting selects at
    a restart. A blob that arrives before the first scan after a switch is delivered by that scan (the Python reader marks a bag with no
    position of its own read, `cursor.py:427-432`), and the stamps stay strictly increasing across the switches whatever `updatedAtMs`
    does (the pane's gate, `cliCoordination.ts:52-56`). native -> Python -> native -> Python -> native; `updatedAtMs` never moves."""
    base_ms = 1786844146562
    python = cur._reader_rooted_at(tmp_path, monkeypatch)
    db = cur._make_store(tmp_path / ".cursor" / "chats", "6" * 32, cur._SID, cwd="/work", updated_at_ms=base_ms)
    native = cli_runtime_native.native_reader(type(python), "cursor")()
    seen: set[str] = set()
    assert native.parse_activity(db, seen) == [], "the first pass of a store only plants its walk"
    stamps: list[datetime] = []

    def scan(reader, prefix: str, replies: int) -> None:
        cur._append_blobs(db, prefix, *[cur._assistant(f"{prefix} {n}") for n in range(replies)])
        got = reader.parse_activity(db, seen)
        assert [event.dedup_key.startswith("blob:") for event in got] == [True] * replies, f"{prefix}: every blob that arrived is delivered"
        assert {event.event_type for event in got} == {"turn_complete"}
        stamps.extend(datetime.fromisoformat(event.timestamp) for event in got)
        assert all(later > earlier for earlier, later in zip(stamps, stamps[1:])), f"{prefix}: strictly after every stamp given before"

    for reader, prefix, replies in ((native, "a", 3), (python, "b", 2), (native, "c", 2), (python, "d", 1), (native, "e", 1)):
        scan(reader, prefix, replies)
    assert len(stamps) == 9
    assert native.parse_activity(db, seen) == [] and python.parse_activity(db, seen) == [], "nothing is delivered twice"


# ---- T5: `turns` over the prefix the first page saw -------------------------------------------------------------------------

def _claude_log(tmp_path, monkeypatch, prompts: int, model: int):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    log = tmp_path / "projects" / "-tmp-demo" / "abc-123.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(_turn_lines(1, prompts, model)), encoding="utf-8")
    return log


def _turn_lines(first: int, prompts: int, model: int) -> list[str]:
    pad, lines = "m" * model, []
    for n in range(first, first + prompts):
        lines.append(json.dumps({"type": "user", "promptId": f"p{n}", "message": {"role": "user", "content": f"go {n}"}}) + "\n")
        lines.append(json.dumps({"type": "assistant", "sessionId": "abc-123", "requestId": f"r{n}", "timestamp": "2026-05-28T00:00:00Z",
                                 "message": {"id": f"m{n}", "model": pad, "role": "assistant", "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
    return lines


def test_an_append_between_two_pages_of_a_breakdown_does_not_restart_it(tmp_path, monkeypatch, sidecar):
    """40 turns of 200 KB make several pages. The log grows by one turn after the first page: the call still returns the 40 turns the
    first page saw, in the pages that continue them, and asks for no restart (`TURNS_UNSTABLE` is what a restart storm ends in)."""
    log = _claude_log(tmp_path, monkeypatch, 40, 200_000)
    claude = cli_runtime_native.native_reader(ClaudeLogReader, "claude")()
    real, calls, restarts = sidecar.observe_core, {"n": 0}, []

    def growing(*args, **kwargs):
        calls["n"] += 1
        answer = real(*args, **kwargs)
        restarts.append(bool(answer.get("restart")))
        if calls["n"] == 1:
            with log.open("a", encoding="utf-8") as stream:
                stream.write("".join(_turn_lines(41, 1, 10)))
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", growing)
    turns = claude.turns_for_session(log, "abc-123")
    assert calls["n"] >= 3 and not any(restarts), "the pages continue; none says `restart`"
    assert [turn.turn_index for turn in turns] == list(range(1, 41)), "the breakdown the first page began, whole, once"
    fresh = claude.turns_for_session(log, "abc-123")
    assert len(fresh) == 41, "the next call sees the appended turn"
