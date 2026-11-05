"""One byte budget per observation pass, over the real sidecar (Q1).

A pass answers one observation document, and the sidecar refuses a document over 8 MiB (`MAX_OBSERVATION_PARTS` x
`OBSERVATION_CHUNK` = 128 x 65,280 = 8,355,840 bytes). The core therefore gives a pass one ledger of 3,637,248 bytes of event bytes (the
cap less the open text 3 MiB, the checkpoint 1 MiB, and the state and the fixed fields 256 KiB each), shared by every walk and every
source: the rest of the cap is room for the open text, the dedup keys, the typed state and the checkpoint the same document carries.

- A mark (or line) that would cross what is left of the budget waits for the next pass; one that alone is over the whole budget is
  delivered with the `text` of its events cut (head and tail kept), so the pass always advances, and the cut is reported once.
- The same budget bounds a first activity pass over a JSONL log.

Every store here is a real SQLite file in the schema the live Copilot CLI wrote (`tests.vendors.test_copilot`), and every log a
1.0.75-style `events.jsonl`. A missing sidecar binary fails these tests, it never skips them.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.claude import ClaudeLogReader
from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
from agent_team_backend.log_readers import OpencodeLogReader
from tests.native_reader_peer import wire
from tests.vendors import test_opencode as oc
from tests.vendors.test_copilot import _SID, _add_session, _add_turn, _add_usage, _assistant, _event, _store, _user, _write_session

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(240)]

MIB = 1024 * 1024
CAP = 128 * 65_280
#: What the answer ledger holds: the cap less the fixed envelope (open text 3 MiB, checkpoint 1 MiB, state and fixed fields 256 KiB each).
LEDGER = CAP - 3 * MIB - MIB - 512 * 1024


@pytest.fixture
def copilot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    _reset_db_warning()
    return root


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


@pytest.fixture
def native(sidecar):
    return cli_runtime_native.native_reader(CopilotLogReader, "copilot")()


def _drain(native, path: Path, limit: int = 60) -> list[list]:
    """Every pass the reader needs to read `path` dry, each pass's events, ending with the empty pass."""
    seen: set[str] = set()
    passes: list[list] = []
    for _ in range(limit):
        batch = native.parse_activity(path, seen)
        passes.append(batch)
        if not batch:
            return passes
    raise AssertionError(f"not drained in {limit} passes")


def _weight(events) -> int:
    return sum(len(event.text or "") + len(event.timestamp or "") for event in events)


def test_one_turn_with_a_nine_mib_reply_is_delivered_cut_to_its_head_and_tail_and_the_walk_moves_on(copilot_root, native, caplog):
    db = _store(copilot_root)
    _add_session(db)
    _add_turn(db, index=0, user="fix it", assistant="HEAD" + "m" * (9 * MIB) + "TAIL")
    _add_turn(db, index=1, user="again", assistant="second")
    seen: set[str] = set()
    with caplog.at_level(logging.INFO, logger="agent_team_backend.cli_runtime_native"):
        first = native.parse_activity(db, seen)
    reply = next(event for event in first if event.event_type == "turn_complete" and event.dedup_key == "db_turn:1")
    assert reply.text.startswith("HEAD") and reply.text.endswith("TAIL"), "the head and the tail of the reply survive"
    assert 0 < len(reply.text) <= LEDGER, "the reply is cut to fit the ledger"
    assert [r.getMessage() for r in caplog.records if "truncated" in r.getMessage()] and \
        len([r for r in caplog.records if "truncated" in r.getMessage()]) == 1, "the cut is reported once"
    # t36i T1: the reader goes on while the sidecar says `more`, so the one call also carries the turn after the oversized one.
    assert [event.text for event in first if event.event_type == "turn_complete"][-1] == "second", "the walk moved past the oversized turn"
    assert native.parse_activity(db, seen) == [], "and the next call is empty"


def test_two_walks_whose_combined_output_crosses_the_cap_are_delivered_in_two_passes_whole(copilot_root, native, sidecar):
    """A usage row with a 2 MiB timestamp and a turn with a 2 MiB reply: 4 MiB together, over the answer ledger (the cap less the
    fixed envelope, 3,637,248 bytes). The turn waits for the next pass, so each pass answers and nothing is cut; the reader goes on
    while the sidecar says `more` (t36i T1), so one call carries both passes and they are the baseline's one read."""
    db = _store(copilot_root)
    _add_session(db)
    _add_usage(db, ts="t" * (2 * MIB))
    _add_turn(db, index=0, user="u", assistant="r" * (2 * MIB))
    baseline = CopilotLogReader().parse_activity(db, set())
    assert _weight(baseline) > LEDGER, "the two walks together are over the answer ledger"
    passes = _drain(native, db)
    events = [event for batch in passes for event in batch]
    assert len([batch for batch in passes if batch]) == 1, "one reader call carries every walk"
    assert len([request for request in sidecar.asked if request["mode"] == "activity"]) >= 3, "the usage walk in the first pass, the turn in the second, then an empty call"
    assert [(e.dedup_key, e.event_type, e.detail) for e in events] == [(e.dedup_key, e.event_type, e.detail) for e in baseline]
    assert [e.text for e in events if e.event_type == "turn_complete"] == [e.text for e in baseline if e.event_type == "turn_complete"]


def test_a_first_activity_pass_over_a_jsonl_log_over_the_cap_stops_at_the_budget_and_the_passes_read_the_whole_log(
        copilot_root, native, monkeypatch):
    turns = [[_user("go", eid=f"u-{index}"), _assistant("x" * 300_000, eid=f"a-{index}"), _event("assistant.turn_end", {}, eid=f"e-{index}")]
             for index in range(40)]
    log = _write_session(copilot_root, events=[record for turn in turns for record in turn])
    baseline = CopilotLogReader().parse_activity(log, set())
    assert _weight(baseline) > CAP, "the log alone is over the observation cap"
    weights: list[int] = []
    original = cli_runtime_native.observe_core

    def measured(vendor, path, mode, **kwargs):
        answer = original(vendor, path, mode, **kwargs)
        weights.append(sum(len(event.get("text") or "") + len(event.get("timestamp") or "") for event in answer["events"]))
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", measured)
    passes = _drain(native, log)
    events = [event for batch in passes for event in batch]
    # t36i T1: one reader call goes on while the sidecar says `more`, so the passes are the sidecar's answers, not the reader's calls.
    assert len([batch for batch in passes if batch]) == 1, "one reader call carries the log"
    assert len([weight for weight in weights if weight]) >= 2, "one pass cannot carry the log"
    assert all(weight <= LEDGER for weight in weights), "no pass is over the ledger"
    assert [(e.dedup_key, len(e.text or "")) for e in events] == [(e.dedup_key, len(e.text or "")) for e in baseline]


def test_a_jsonl_log_over_eight_mib_is_replayed_in_pages_that_equal_the_baselines_single_pass_with_dedup_intact(
        tmp_path, monkeypatch, sidecar):
    """Q2 over a JSONL log: 9,000 usage records whose model name is 1 KB (a log of about 9.5 MiB, over 10 MiB of events) with a
    record repeated and a key already in the bag. The reader asks the sidecar again while it says `more`; the pages together are
    the baseline's single pass, each key once, the pre-seeded one never, and the finished bag leaves no watermark.

    Claude's log is the one the core replays event by event (`session_usage` of per-turn usage): Copilot's `events.jsonl` has
    cumulative usage, whose replay is one delta event however long the log, so it cannot page."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    log = tmp_path / "projects" / "-tmp-demo" / "abc-123.jsonl"
    log.parent.mkdir(parents=True)
    pad = "m" * 1000
    lines = []
    for n in range(1, 9001):
        record = json.dumps({"type": "assistant", "sessionId": "abc-123", "requestId": f"r{n}", "timestamp": f"2026-05-28T00:{n // 60 % 60:02d}:{n % 60:02d}Z",
                             "message": {"id": f"m{n}", "model": pad, "usage": {"input_tokens": 10, "output_tokens": 5}}})
        lines += [record] * (2 if n % 1000 == 0 else 1)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.stat().st_size > 8 * MIB, "the log alone is over the observation cap"
    baseline_bag = {"m5::r5"}
    baseline = ClaudeLogReader().parse_session_file(log, baseline_bag)
    assert len(baseline) == 8999, "a repeated record is one event, the seeded key none"
    bag = {"m5::r5"}
    native = cli_runtime_native.native_reader(ClaudeLogReader, "claude")()
    events = native.parse_session_file(log, bag)
    asked = [request for request in sidecar.asked if request["mode"] == "session_usage"]
    assert len(asked) >= 2, "one page cannot carry the replay"
    assert [(e.dedup_key, e.input_tokens, e.output_tokens, e.timestamp, e.model) for e in events] == \
        [(e.dedup_key, e.input_tokens, e.output_tokens, e.timestamp, e.model) for e in baseline], "the pages are the single pass"
    assert len({e.dedup_key for e in events}) == len(events), "each key once"
    assert "m5::r5" not in {e.dedup_key for e in events}
    rows = lambda keys: {key for key in keys if not key.startswith("native_state::")}  # noqa: E731
    assert rows(bag) == rows(baseline_bag) and "m5::r5" in bag
    assert not any('"replay"' in key for key in bag), "the finished replay leaves no watermark in the bag"
    assert native.parse_session_file(log, bag) == [], "and a replay of what the bag holds delivers nothing"


def test_a_session_usage_replay_over_the_budget_is_paged_and_the_pages_are_the_baselines_single_pass(copilot_root, native, sidecar):
    """9,000 usage rows whose model name is 1 KB: about 10 MiB of events, over the 3.5 MiB (the ledger) a page may carry. The reader asks the
    sidecar again while it says `more`, adds each page's keys to the bag, and ends with the baseline's events and bag."""
    db = _store(copilot_root)
    _add_session(db)
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO assistant_usage_events (session_id, turn_index, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, reasoning_tokens, created_at) VALUES (?, 0, ?, 10, 5, 0, 0, 0, ?)",
        [(_SID, "m" * 1000, f"2026-07-27T18:{index // 60 % 60:02d}:{index % 60:02d}.000Z") for index in range(9000)])
    con.commit()
    con.close()
    baseline_bag: set[str] = set()
    baseline = CopilotLogReader().parse_session_file(db, baseline_bag)
    assert len(baseline) == 9000
    bag: set[str] = set()
    events = native.parse_session_file(db, bag)
    asked = [request for request in sidecar.asked if request["mode"] == "session_usage"]
    assert len(asked) >= 2, "one page cannot carry the replay"
    assert [(e.dedup_key, e.timestamp) for e in events] == [(e.dedup_key, e.timestamp) for e in baseline], "the pages are the single pass"
    assert {key for key in bag if not key.startswith("native_state::")} == {key for key in baseline_bag if not key.startswith("native_state::")}
    assert not any('"replay"' in key for key in bag), "the finished replay leaves no watermark in the bag"
    assert native.parse_session_file(db, bag) == [], "and a replay of what the bag holds delivers nothing"


# ---------------------------------------------------------------------------------------------------- t36g: R2, R3, R5, R7, R9

def _usage_rows(db: Path, models: list[str]) -> None:
    """One `assistant_usage_events` row per model name (the live schema, `tests.vendors.test_copilot._STORE_SCHEMA`)."""
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO assistant_usage_events (session_id, turn_index, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, reasoning_tokens, created_at) VALUES (?, 0, ?, 10, 5, 0, 0, 0, ?)",
        [(_SID, model, f"2026-07-27T18:{n // 60 % 60:02d}:{n % 60:02d}.000Z") for n, model in enumerate(models)])
    con.commit()
    con.close()


def _keys(events) -> list[str]:
    return [event.dedup_key for event in events]


def _state_keys(bag: set[str]) -> set[str]:
    return {key for key in bag if not key.startswith("native_state::")}


def test_one_page_of_two_thousand_heavy_rows_is_replayed_row_by_row_within_the_budget(copilot_root, native, sidecar):
    """2,000 rows (the whole page) whose events weigh 4.6 KB each: 9.6 MB, over the cap by themselves. The replay stops at the row
    that would cross the budget and answers `more`, so the pages together are the baseline's single pass."""
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 4500] * 2000)
    baseline = CopilotLogReader().parse_session_file(db, set())
    assert len(baseline) == 2000 and sum(len(event.model) for event in baseline) > 8 * MIB
    bag: set[str] = set()
    events = native.parse_session_file(db, bag)
    assert len([request for request in sidecar.asked if request["mode"] == "session_usage"]) >= 2, "one page cannot carry the replay"
    assert _keys(events) == _keys(baseline), "the pages are the single pass, in order, each row once"
    assert not any('"replay"' in key for key in bag) and native.parse_session_file(db, bag) == []


def test_a_light_page_followed_by_a_heavy_one_does_not_overshoot_the_budget(copilot_root, native, sidecar):
    """The page before is light (2,000 rows of 0.4 KB), so a prediction from it lets the next whole page in: 2,000 rows of 4.6 KB."""
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 100] * 2000 + ["m" * 4500] * 2000)
    baseline = CopilotLogReader().parse_session_file(db, set())
    bag: set[str] = set()
    events = native.parse_session_file(db, bag)
    assert len([request for request in sidecar.asked if request["mode"] == "session_usage"]) >= 2
    assert _keys(events) == _keys(baseline) and not any('"replay"' in key for key in bag)


def test_the_row_cursor_stops_at_the_budget_commits_the_last_row_it_delivered_and_the_next_pass_goes_on(copilot_root, native, sidecar):
    """`usage` over a store of 2,000 heavy rows: the incremental cursor reads one 2,000-row page and used to hand all of it over."""
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 4500] * 2000)
    baseline = CopilotLogReader().parse_session_file(db, set())
    # t36i T1: the reader goes on while the sidecar says `more`, so one call reads the page to its end however the ledger cuts it.
    result = native.parse_incremental(db, {})
    assert len([request for request in sidecar.asked if request["mode"] == "usage"]) >= 2, "two passes carry the rows"
    assert _keys(result.events) == _keys(baseline), "every row once, in order, however the passes cut them"
    assert result.checkpoint["row_id"] == 2000 and result.checkpoint["pending"] == []
    assert native.parse_incremental(db, result.checkpoint).events == [], "and the next call finds nothing"


def _opencode_db(root: Path, monkeypatch: pytest.MonkeyPatch, name: str = "opencode.db") -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "xdg"))
    directory = root / "xdg" / "opencode"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def _opencode_messages(con: sqlite3.Connection, prefix: str, count: int, *, model: str, pending: tuple[str, ...] = ()) -> None:
    """`count` assistant messages `<prefix><n>` in the OpenCode 1.x schema (`tests.vendors.test_opencode`), in one transaction:
    complete, or (the ids in `pending`) still being written. The row id of the first message in a fresh store is 1."""
    rows = []
    for n in range(1, count + 1):
        mid = f"{prefix}{n}"
        data = oc._assistant_data({"input": 10, "output": 5}, completed=None if mid in pending else oc._NOW_MS + 15_000, model=model)
        rows.append((mid, oc._SID, oc._NOW_MS, oc._NOW_MS, json.dumps(data)))
    con.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()


def test_a_store_replaced_between_two_replay_pages_is_read_again_from_its_first_row(tmp_path, monkeypatch, sidecar):
    """Store A holds 3,000 messages of 2.3 KB: page 1 stops inside it (the budget, or a full page of 2,000 rows) and answers `more`
    with a watermark in A's row ids. Store B replaces the file before page 2 with 300 messages whose ids (1-300) are all behind
    that watermark: they belong to a store the replay never saw, so none may be skipped."""
    db = _opencode_db(tmp_path / "a", monkeypatch)
    con = oc._create_db(db, wal=False)
    oc._add_session(con, oc._SID, "/work")
    _opencode_messages(con, "msg_a", 3000, model="m" * 2000)
    con.close()
    other = _opencode_db(tmp_path / "b", monkeypatch, "replacement.db")
    con = oc._create_db(other, wal=False)
    oc._add_session(con, oc._SID, "/work")
    _opencode_messages(con, "msg_b", 300, model="n" * 20)
    con.close()
    replaced: list[int] = []
    original = cli_runtime_native.observe_core

    def swap_after_the_first_page(vendor, path, mode, **kwargs):
        answer = original(vendor, path, mode, **kwargs)
        if mode == "legacy" and answer.get("more") and not replaced:
            os.replace(other, db)
            replaced.append(1)
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", swap_after_the_first_page)
    reader = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
    bag: set[str] = set()
    events = reader.parse_session_file(db, bag)
    assert replaced, "the replay took more than one page"
    keys = _keys(events)
    assert len(keys) == len(set(keys)), "no row twice"
    assert {f"msg:msg_b{n}" for n in range(1, 301)} <= set(keys), "every row of B, the ones behind A's watermark included"
    assert not any('"replay"' in key for key in bag)


def test_a_row_completed_between_two_replay_pages_is_delivered_by_the_page_that_revisits_it(tmp_path, monkeypatch, sidecar):
    """Row 1 is still being written when page 1 reads it (no event; it stays pending). Page 1 stops inside the 3,000 messages
    (the budget, or a full page) with a watermark behind most of them. The vendor completes row 1 before page 2: the replay revisits
    the pending row and delivers it once, with the rest."""
    db = _opencode_db(tmp_path, monkeypatch)
    con = oc._create_db(db, wal=False)
    oc._add_session(con, oc._SID, "/work")
    _opencode_messages(con, "msg_", 3000, model="m" * 2000, pending=("msg_1",))
    completed: list[int] = []
    original = cli_runtime_native.observe_core

    def complete_after_the_first_page(vendor, path, mode, **kwargs):
        answer = original(vendor, path, mode, **kwargs)
        if mode == "legacy" and answer.get("more") and not completed:
            oc._update_message(con, "msg_1", oc._assistant_data({"input": 10, "output": 5}, model="m" * 2000))
            completed.append(1)
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", complete_after_the_first_page)
    reader = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
    bag: set[str] = set()
    events = reader.parse_session_file(db, bag)
    con.close()
    assert completed, "the replay took more than one page"
    keys = _keys(events)
    assert sorted(keys) == sorted(f"msg:msg_{n}" for n in range(1, 3001)), "every message once, the completed one included"
    assert not any('"replay"' in key for key in bag)


def test_a_replay_with_more_than_125000_keys_in_the_bag_answers_only_the_keys_it_delivered(copilot_root, native, sidecar, monkeypatch):
    """A bag of 130,000 keys (3.5 MB in the request) and a replay whose one page (1,900 rows, short of a full page) carries 2.5 MB of
    events: the answer used to echo the whole bag beside them (over the cap). The answer carries the delivered keys only; the reader
    keeps the rest."""
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 1200] * 1900)
    bag = {f"old::{n:020d}" for n in range(130_000)}
    baseline_bag = set(bag)
    baseline = CopilotLogReader().parse_session_file(db, baseline_bag)
    answers: list[dict] = []
    original = cli_runtime_native.observe_core

    def record(vendor, path, mode, **kwargs):
        answers.append(original(vendor, path, mode, **kwargs))
        return answers[-1]

    monkeypatch.setattr(cli_runtime_native, "observe_core", record)
    events = native.parse_session_file(db, bag)
    assert len([a for a in answers if a["mode"] == "session_usage"]) == 1, "a single page"
    assert len(answers[0]["seen"]) == 1900, "the answer's keys are the rows it delivered, not the 130,000 the request carried"
    assert _keys(events) == _keys(baseline)
    assert _state_keys(bag) == _state_keys(baseline_bag) and len(_state_keys(bag)) == 131_900, "the reader's bag keeps all of them"


def test_a_replay_page_that_fails_returns_the_pages_before_it_and_the_next_call_delivers_the_rest_exactly_once(
        copilot_root, native, sidecar, monkeypatch):
    """Page 2 cannot be read (the store is unreadable between the pages). Page 1's events are returned, the bag holds page 1's keys
    and the watermark, and the next call delivers the rest: no row twice, none lost."""
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 1000] * 9000)
    baseline = CopilotLogReader().parse_session_file(db, set())
    original = cli_runtime_native.observe_core
    broken: list[int] = []

    def lock_after_the_first_page(vendor, path, mode, **kwargs):
        answer = original(vendor, path, mode, **kwargs)
        if mode == "session_usage" and answer.get("more") and not broken:
            os.chmod(db, 0)
            broken.append(1)
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", lock_after_the_first_page)
    bag: set[str] = set()
    first = native.parse_session_file(db, bag)
    os.chmod(db, 0o644)
    assert broken and 0 < len(first) < 9000, "page 1 only"
    assert _state_keys(bag) == set(_keys(first)), "the bag holds page 1's keys"
    assert any('"replay"' in key for key in bag), "and the watermark"
    rest = native.parse_session_file(db, bag)
    assert _keys(first) + _keys(rest) == _keys(baseline), "the next call delivers the rest, each row once"
    assert not any('"replay"' in key for key in bag)


def test_a_replay_page_that_raises_returns_the_pages_before_it(copilot_root, native, sidecar, monkeypatch):
    db = _store(copilot_root)
    _add_session(db)
    _usage_rows(db, ["m" * 1000] * 9000)
    baseline = CopilotLogReader().parse_session_file(db, set())
    original = cli_runtime_native.observe_core
    calls: list[str] = []

    def fail_the_second_page(vendor, path, mode, **kwargs):
        calls.append(mode)
        if calls.count("session_usage") == 2 and not any(call == "boom" for call in calls):
            calls.append("boom")
            raise RuntimeError("the sidecar could not answer page 2")
        return original(vendor, path, mode, **kwargs)

    monkeypatch.setattr(cli_runtime_native, "observe_core", fail_the_second_page)
    bag: set[str] = set()
    first = native.parse_session_file(db, bag)
    assert 0 < len(first) < 9000 and _state_keys(bag) == set(_keys(first))
    rest = native.parse_session_file(db, bag)
    assert _keys(first) + _keys(rest) == _keys(baseline)


@pytest.mark.parametrize("field", ["activity timestamp", "replayed model", "logged model"])
def test_a_mark_whose_non_text_fields_alone_are_over_the_budget_is_delivered_with_them_capped_and_reported_once(
        field, copilot_root, native, caplog, tmp_path, monkeypatch, sidecar):
    """Text is not the only field a record can make long: the core's own event fields (`timestamp`, `detail`, `cwd`, `session_id`,
    `file_path`, `model`) are capped (head and tail kept) to a fixed bound when the mark's events are still over the room after the
    text cut, and the mark is reported in `truncated`. Before, such a mark was refused on every pass."""
    huge = "HEAD" + "t" * (9 * MIB) + "TAIL"
    with caplog.at_level(logging.INFO, logger="agent_team_backend.cli_runtime_native"):
        if field == "activity timestamp":
            db = _store(copilot_root)
            _add_session(db)
            _add_usage(db, ts=huge)
            events = native.parse_activity(db, set())
            value = next(event.timestamp for event in events if event.detail == "assistant")
        elif field == "replayed model":
            db = _store(copilot_root)
            _add_session(db)
            _add_usage(db, model=huge)
            (event,) = native.parse_session_file(db, set())
            value = event.model
        else:
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
            log = tmp_path / "projects" / "-tmp-demo" / "abc-123.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text(json.dumps({"type": "assistant", "sessionId": "abc-123", "requestId": "r1", "timestamp": "2026-05-28T00:00:01Z",
                                       "message": {"id": "m1", "model": huge, "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n",
                           encoding="utf-8")
            (event,) = cli_runtime_native.native_reader(ClaudeLogReader, "claude")().parse_session_file(log, set())
            value = event.model
    assert value.startswith("HEAD") and value.endswith("TAIL"), "the head and the tail of the field survive"
    assert 0 < len(value) <= 8 * 1024 + 8, "and the middle is cut to a fixed bound"
    assert len([r for r in caplog.records if "truncated" in r.getMessage()]) == 1, "the cut is reported once"
