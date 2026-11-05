"""OpenCode 2 on the observation core: the fixtures of `vendors/test_opencode_v2.py` through the Python reader and the sidecar.

OpenCode 2 never had a legacy Rust observer, so the Python reader (`cli_vendors/opencode.py`) is the baseline. Each case builds one
`opencode.db` with the very helper that file uses (`_Db`), asks the Python reader and the sidecar the same question, and compares
the events (every field the reader fills), the dedup keys and the checkpoint. The mixed-schema and upgraded-store cases are included.

The sidecar is asked directly (`FramePeer`), as `test_sidecar_observes_each_ported_vendor` does; no backend process is involved.
A missing sidecar binary fails these tests, it never skips them.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.log_readers import KiloLogReader, OpencodeLogReader
from tests.cli_regression.support.frame_peer import PRODUCT, FramePeer
from tests.native_reader_peer import wire
from tests.vendors import test_opencode_v2 as v2
from tests.vendors.test_opencode_v2 import _Db, _tokens

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(120)]

USAGE_FIELDS = ("vendor", "input_tokens", "output_tokens", "cwd", "session_id", "file_path", "dedup_key", "timestamp", "model",
                "cli_version")
ACTIVITY_FIELDS = ("vendor", "event_type", "cwd", "session_id", "file_path", "dedup_key", "timestamp", "detail", "text")


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / "opencode"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def peer(tmp_path):
    assert PRODUCT.is_file(), f"the native sidecar is not built: {PRODUCT}"
    sidecar = FramePeer(tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


def _rows(events, fields) -> list[dict]:
    return [{name: getattr(event, name) if not isinstance(event, dict) else event.get(name) for name in fields} for event in events]


def _sidecar(peer, path, mode, **args):
    document, final = peer.observe("opencode", path, mode, **args)
    assert document is not None, final.meta
    return document


def test_usage_cursor_follows_a_streaming_step_and_agrees_with_python(data_dir, peer):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db")
    db.session(v2._SID, "/ws")
    streaming = db.assistant(v2._SID, tokens=_tokens(), completed=False)
    db.user(v2._SID, "next")
    python_first = reader.parse_incremental(db.path, {})
    rust_first = _sidecar(peer, db.path, "usage", checkpoint={})
    assert python_first.events == [] and rust_first["events"] == []
    assert python_first.checkpoint == rust_first["checkpoint"]
    assert rust_first["checkpoint"]["schema"] == "v2" and rust_first["layout"] == 0

    db.update(streaming, {"time": {"created": v2._NOW_MS, "completed": v2._NOW_MS + 1}})
    python_second = reader.parse_incremental(db.path, python_first.checkpoint)
    rust_second = _sidecar(peer, db.path, "usage", checkpoint=rust_first["checkpoint"])
    assert _rows(python_second.events, USAGE_FIELDS) == _rows(rust_second["events"], USAGE_FIELDS)
    assert [event["dedup_key"] for event in rust_second["events"]] == [f"msg:{streaming}"]
    assert python_second.checkpoint == rust_second["checkpoint"]
    assert [event.checkpoint for event in python_second.events] == [event["checkpoint"] for event in rust_second["events"]]
    python_third = reader.parse_incremental(db.path, python_second.checkpoint)
    rust_third = _sidecar(peer, db.path, "usage", checkpoint=rust_second["checkpoint"])
    assert python_third.events == [] and rust_third["events"] == [] and python_third.checkpoint == rust_third["checkpoint"]


def test_a_1x_cursor_on_an_upgraded_db_restarts_at_the_newest_row_in_both(data_dir, peer):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    for _ in range(3):
        db.assistant(v2._SID, tokens=_tokens())
    stat = db.path.stat()
    old = {"kind": "sqlite", "row_id": 1, "pending": [], "anchor": "", "identity": f"{stat.st_dev}:{stat.st_ino}"}
    python_first = reader.parse_incremental(db.path, old)
    rust_first = _sidecar(peer, db.path, "usage", checkpoint=old)
    assert python_first.events == [] and rust_first["events"] == []
    assert python_first.checkpoint == rust_first["checkpoint"] and rust_first["checkpoint"]["row_id"] == 3
    fresh = db.assistant(v2._SID, tokens=_tokens())
    python_second = reader.parse_incremental(db.path, python_first.checkpoint)
    rust_second = _sidecar(peer, db.path, "usage", checkpoint=rust_first["checkpoint"])
    assert [e.dedup_key for e in python_second.events] == [e["dedup_key"] for e in rust_second["events"]] == [f"msg:{fresh}"]
    assert _rows(python_second.events, USAGE_FIELDS) == _rows(rust_second["events"], USAGE_FIELDS), "every field of the second round"
    assert python_second.checkpoint == rust_second["checkpoint"]


def test_the_whole_store_replay_agrees_and_settles_against_the_seen_bag(data_dir, peer):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db")
    db.session(v2._SID, "/ws")
    db.assistant(v2._SID, text="done", tokens=_tokens())
    db.user(v2._SID, "next")
    db.assistant(v2._SID, tokens=_tokens(1, 2, 3, 4, 5), completed=False)
    bag: set[str] = set()
    python_events = reader.parse_session_file(db.path, bag)
    rust = _sidecar(peer, db.path, "legacy", seen=[])
    assert _rows(python_events, USAGE_FIELDS) == _rows(rust["events"], USAGE_FIELDS)
    # The completed step and the user row are settled; the streaming step is not marked seen, so a later replay reads it again.
    assert bag == set(rust["seen"]) == {"msg:msg_0001", "msg:msg_0002"}
    again = reader.parse_session_file(db.path, bag)
    rust_again = _sidecar(peer, db.path, "legacy", seen=rust["seen"])
    # t36g R5: an answer's `seen` names only the keys the pass delivered (none now); the caller's bag is what it asked with plus them.
    assert again == [] and rust_again["events"] == [] and rust_again["seen"] == []
    assert bag == set(rust["seen"]) | set(rust_again["seen"])


def _activity_rounds(reader, db, peer, writes):
    """Run the activity mode of both readers across rounds of writes; each round's events must agree."""
    bag: set[str] = set()
    state: dict = {}
    seen: list[str] = []
    rounds = []
    for write in writes:
        write()
        python_events = reader.parse_activity(db.path, bag)
        rust = _sidecar(peer, db.path, "activity", seen=seen, state=state)
        state, seen = rust["state"], rust["seen"]
        assert _rows(python_events, ACTIVITY_FIELDS) == _rows(rust["events"], ACTIVITY_FIELDS)
        marks = state.get("marks", {}).get("session_message", {})
        assert bag == {f"opencode_message::{marks.get('last')}"} and seen == []
        rounds.append(rust["events"])
    return rounds


def test_a_turn_ends_with_the_reply_and_a_subagent_never_ends_the_pane_turn(data_dir, peer):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db")
    db.session(v2._SID, "/ws")
    db.session("ses_child", "/ws", parent_id=v2._SID)

    def turn():
        db.user(v2._SID, "run the tests")
        db.assistant(v2._SID, text="Running them.", completed=True)
        db.assistant(v2._SID, text="All 12 tests pass.", tokens=_tokens())
        db.idle(v2._SID)

    def child():
        db.assistant("ses_child", text="child done")
        db.idle("ses_child")
        db.message(v2._SID, "tool", {})

    rounds = _activity_rounds(reader, db, peer, [lambda: None, turn, child, lambda: None])
    assert rounds[0] == [] and rounds[3] == []
    assert [e["event_type"] for e in rounds[1]][-1] == "turn_complete" and rounds[1][-1]["text"] == "All 12 tests pass."
    assert rounds[1][0]["detail"] == "user" and rounds[1][0]["text"] == "run the tests"
    assert [e["detail"] for e in rounds[2]] == ["assistant", "subagent:done", "tool"]


def test_activity_on_an_upgraded_db_ignores_a_1x_part_mark_in_both(data_dir, peer):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    for _ in range(3):
        db.user(v2._SID, "old prompt")
        db.idle(v2._SID)
    assert reader.parse_activity(db.path, {"opencode_part::1"}) == []
    answer = _sidecar(peer, db.path, "activity", seen=[], state={"marks": {"part": {"last": 1, "pending": []}}})
    assert answer["events"] == [] and answer["state"]["marks"]["session_message"]["last"] == 6


def test_one_malformed_v2_data_cell_fails_pythons_whole_query_where_the_sidecar_skips_that_row(data_dir, peer):
    """NAMED COMPAT DIFFERENCE (P10), and a defect of the Python baseline. OpenCode 2's usage query wraps `data` in
    `json_set`/`json_extract`, so one malformed `data` cell fails the WHOLE query: the Python reader answers no event for any
    session in the store, records a `query-failed` read problem, and keeps its checkpoint, until the row is gone. The sidecar
    reads `data` as text, skips the one row it cannot parse and answers every other. This is deliberate: the sidecar keeps the
    one session's usage that Python loses; it is on the user's list of Python defects, not a behaviour to copy."""
    from agent_team_backend.cli_vendors import opencode as opencode_module

    opencode_module._READ_PROBLEMS.clear()
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db")
    db.session(v2._SID, "/ws")
    first = db.assistant(v2._SID, tokens=_tokens())
    db.con.execute("INSERT INTO session_message VALUES (?, ?, 'assistant', 50, ?, ?, ?)",
                   ("msg_bad", v2._SID, v2._NOW_MS + 50, v2._NOW_MS + 50, "{this is not json"))
    db.con.commit()
    last = db.assistant(v2._SID, tokens=_tokens())
    python = reader.parse_incremental(db.path, {})
    assert python.events == [] and python.checkpoint == {}, "Python: the whole query failed, nothing is answered"
    assert opencode_module.read_problem("opencode")["reason"] == "query-failed"
    rust = _sidecar(peer, db.path, "usage", checkpoint={})
    assert [event["dedup_key"] for event in rust["events"]] == [f"msg:{first}", f"msg:{last}"], "the sidecar skips only the one row"
    opencode_module._READ_PROBLEMS.clear()


def test_a_1x_part_mark_in_the_bag_is_ignored_by_the_native_reader_as_by_the_python_one(data_dir, tmp_path, monkeypatch):
    """P7: the bag the Python reader kept in its 1.x days holds `opencode_part::<n>`. Both readers read an upgraded db from the
    OpenCode 2 rows and start the new walk at the oldest row, and the native reader's bag then carries the walk's state."""
    db = _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    for _ in range(3):
        db.user(v2._SID, "old prompt")
        db.idle(v2._SID)
    sidecar = wire(monkeypatch, tmp_path / "bag")
    try:
        native = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
        python_bag, native_bag = {"opencode_part::1", "kept-key"}, {"opencode_part::1", "kept-key"}
        assert OpencodeLogReader().parse_activity(db.path, python_bag) == native.parse_activity(db.path, native_bag)
        assert "kept-key" in native_bag and "opencode_part::1" not in native_bag, "the 1.x mark is folded into the walk's state"
        (packed,) = [key for key in native_bag if key.startswith("native_state::")]
        marks = json.loads(packed.split("::", 2)[2])["marks"]
        assert marks["part"]["last"] == 1 and marks["session_message"]["last"] == 6, "the old mark is kept, the new walk read the rows"
        assert native.parse_activity(db.path, native_bag) == [], "nothing is answered twice"
    finally:
        sidecar.close()


def test_kilos_usage_cursor_agrees_with_the_kilo_reader_round_after_round(tmp_path, monkeypatch, peer):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / "kilo"
    directory.mkdir(parents=True)
    db = _Db(directory / "kilo.db")
    db.session(v2._SID, "/ws")
    reader = KiloLogReader()
    cursor_python, cursor_rust = {}, {}
    for write in (lambda: db.assistant(v2._SID, tokens=_tokens()), lambda: db.assistant(v2._SID, tokens=_tokens(3, 4, 5, 6, 7)), lambda: None):
        write()
        python = reader.parse_incremental(db.path, cursor_python)
        document, final = peer.observe("kilo", db.path, "usage", checkpoint=cursor_rust)
        assert document is not None, final.meta
        assert _rows(python.events, USAGE_FIELDS) == _rows(document["events"], USAGE_FIELDS)
        assert python.checkpoint == document["checkpoint"] and document["checkpoint"]["schema"] == "v2"
        cursor_python, cursor_rust = python.checkpoint, document["checkpoint"]


def _native_has_session(tmp_path, monkeypatch, *sessions) -> list[bool]:
    """The same lookups asked through the native reader, whose every observation is the real sidecar's answer."""
    sidecar = wire(monkeypatch, tmp_path / "lookup")
    try:
        reader = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
        return [reader.has_session(session) for session in sessions]
    finally:
        sidecar.close()


def test_a_pane_marker_and_a_session_lookup_agree(data_dir, peer, tmp_path, monkeypatch):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    db.session("ses_child", "/ws", parent_id=v2._SID)
    db.user(v2._SID, "kickoff\nsession marker: at-pane:p9\n")
    db.user("ses_child", "forwarded at-pane:p1")
    db.assistant(v2._SID, text="I saw at-pane:p2")
    markers = ["at-pane:p9", "at-pane:p1", "at-pane:p2"]
    python = reader.find_sessions_by_marker(markers)
    rust = _sidecar(peer, db.path, "identify", markers=markers)["markers"]
    assert python == {key: tuple(value) for key, value in rust.items()} == {"at-pane:p9": (v2._SID, "/ws")}
    db.con.execute("INSERT INTO session VALUES ('ses_v1', 'proj1', NULL, 'slug', '/ws', 't', '1.19.3', 1, 1)")
    db.con.commit()
    asked = [reader.has_session(session) for session in (v2._SID, "ses_v1", "ses_missing")]
    assert asked == [True, True, False]
    assert _native_has_session(tmp_path, monkeypatch, v2._SID, "ses_v1", "ses_missing") == asked, "the real sidecar answers the same"


def test_a_store_a_1x_binary_still_writes_is_read_as_1x_by_both_until_2x_writes_again(data_dir, peer, tmp_path, monkeypatch):
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    db.user(v2._SID, "a 2.x turn")
    v2._write_1x_turn(db, "ses_v1", v2._NOW_MS + 500_000)
    python = reader.parse_session_file(db.path, set())
    rust = _sidecar(peer, db.path, "legacy", seen=[])
    assert [e.session_id for e in python] == [e["session_id"] for e in rust["events"]] == ["ses_v1"]
    assert _rows(python, USAGE_FIELDS) == _rows(rust["events"], USAGE_FIELDS) and rust["layout"] == 1
    assert [reader.has_session(session) for session in (v2._SID, "ses_v1")] == [True, True], "either side's session resumes"
    assert _native_has_session(tmp_path, monkeypatch, v2._SID, "ses_v1") == [True, True], "and so does the real sidecar's"

    db.assistant(v2._SID, tokens=_tokens())
    db.con.execute("UPDATE session_message SET time_updated = ?", (v2._NOW_MS + 900_000,))
    db.con.commit()
    python = reader.parse_session_file(db.path, set())
    rust = _sidecar(peer, db.path, "legacy", seen=[])
    assert _rows(python, USAGE_FIELDS) == _rows(rust["events"], USAGE_FIELDS) and rust["layout"] == 0


def _write_1x_activity(db, sid: str, when_ms: int) -> None:
    """What a 1.x binary writes for one finished turn: the leftover tables' session, assistant message, a text part and the
    `stop` step that ends the turn."""
    v2._write_1x_turn(db, sid, when_ms)
    for part_id, data in (("prt_v1_text", {"type": "text", "text": "a 1.x reply"}), ("prt_v1_stop", {"type": "step-finish", "reason": "stop"})):
        db.con.execute("INSERT INTO part VALUES (?, 'msg_v1', ?, ?, ?, ?)", (part_id, sid, when_ms, when_ms, json.dumps(data)))
    db.con.commit()


def test_usage_and_activity_agree_across_a_v2_to_1x_to_v2_flip_of_a_shared_db(data_dir, peer):
    """P7: a db both OpenCode binaries write is read with the side that took the newest write, round after round. Each round
    asks both readers for usage (with the cursor the previous round gave) and for activity (with the bag/state it gave)."""
    reader, db = OpencodeLogReader(), _Db(data_dir / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")

    def two_turn():
        db.user(v2._SID, "a 2.x turn")
        db.assistant(v2._SID, text="two", tokens=_tokens())
        db.idle(v2._SID)

    def one_turn():
        _write_1x_activity(db, "ses_v1", v2._NOW_MS + 500_000)

    def two_again():
        db.assistant(v2._SID, text="two again", tokens=_tokens())
        db.idle(v2._SID)
        db.con.execute("UPDATE session_message SET time_updated = ?", (v2._NOW_MS + 900_000,))
        db.con.commit()

    python_cursor, rust_cursor = {}, {}
    bag: set[str] = set()
    state: dict = {}
    seen: list[str] = []
    layouts = []
    for write in (two_turn, one_turn, two_again, lambda: None):
        write()
        python = reader.parse_incremental(db.path, python_cursor)
        rust = _sidecar(peer, db.path, "usage", checkpoint=rust_cursor)
        assert _rows(python.events, USAGE_FIELDS) == _rows(rust["events"], USAGE_FIELDS)
        assert python.checkpoint == rust["checkpoint"]
        python_cursor, rust_cursor = python.checkpoint, rust["checkpoint"]
        python_activity = reader.parse_activity(db.path, bag)
        rust_activity = _sidecar(peer, db.path, "activity", seen=seen, state=state)
        state, seen = rust_activity["state"], rust_activity["seen"]
        assert _rows(python_activity, ACTIVITY_FIELDS) == _rows(rust_activity["events"], ACTIVITY_FIELDS)
        layouts.append(rust_activity["layout"])
    assert layouts == [0, 1, 0, 0], "v2, then the 1.x tables took the newest write, then v2 wrote again"


def test_a_half_v2_db_and_a_pure_1x_db_are_read_as_1x_by_both(data_dir, peer):
    connection = sqlite3.connect(data_dir / "opencode.db")
    connection.executescript(v2._V1_LEFTOVER_SCHEMA + "CREATE TABLE session_v2 (id TEXT);")
    connection.commit()
    connection.close()
    assert OpencodeLogReader()._schema(data_dir / "opencode.db") == "v1"
    assert _sidecar(peer, data_dir / "opencode.db", "legacy", seen=[])["layout"] == 1


def test_kilo_reads_the_same_two_schemas_through_the_same_reader(tmp_path, monkeypatch, peer):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / "kilo"
    directory.mkdir(parents=True)
    db = _Db(directory / "kilo.db")
    db.session(v2._SID, "/ws")
    db.assistant(v2._SID, tokens=_tokens())
    python = KiloLogReader().parse_session_file(db.path, set())
    document, final = peer.observe("kilo", db.path, "legacy", seen=[])
    assert document is not None, final.meta
    assert _rows(python, USAGE_FIELDS) == _rows(document["events"], USAGE_FIELDS) and document["layout"] == 0
    assert json.dumps(document["checkpoint"]) is not None
