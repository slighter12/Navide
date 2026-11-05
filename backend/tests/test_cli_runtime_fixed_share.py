"""The answer's fixed share is bounded at the producer, over the real sidecar (t36i T3).

The answer ledger reserves 256 KiB for the fields that do not grow with the work (`observe/answer.rs`, `FIXED_BYTES`). Two producers
copied a store's own cells into that share without a cap: the marker lookup (`identify`) copied the `group` and `dir` of the row that
holds the marker, and the schema report of a foreign store named up to 256 tables of up to 8,192 characters each. Both are bounded now,
and `Run::into_document` refuses an answer whose fixed fields are over their share rather than send one the transport would drop.

The stores use the shapes the vendors' own tests build (`tests.vendors.test_goose`; OpenCode's file name `opencode.db`). A missing
sidecar binary fails these tests, it never skips them.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.log_readers import GooseLogReader
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.native_reader_peer import wire
from tests.vendors import test_goose as gs

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(300)]

PEER = Path(__file__).parent / "cli_regression/support/handoff_peer.py"
MIB = 1024 * 1024
ANSWER_CAP = 128 * 65_280
FIELD_CHARS = 8192


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


def _head_tail(text: str, size: int = FIELD_CHARS) -> str:
    """`cap_head_tail`: the first and last `size // 2` characters around a marker."""
    return text if len(text) <= size else f"{text[:size // 2]}\n…\n{text[-(size // 2):]}"


def test_one_short_marker_bound_to_a_five_mib_stored_session_and_directory_is_answered_with_the_binding_cut(tmp_path, monkeypatch, sidecar):
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(tmp_path / "root"))
    path = tmp_path / "root" / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(gs.SCHEMA)
    group, directory = "S" * (5 * MIB), "/work/" + "d" * (5 * MIB)
    gs._session(con, group, cwd=directory)
    gs._message(con, group, "user", gs._text("at-pane:p1 hello"))
    con.close()
    native = cli_runtime_native.native_reader(GooseLogReader, "goose")()
    found = native.find_sessions_by_marker(["at-pane:p1"])
    assert list(found) == ["at-pane:p1"], "the marker is found: the lookup is answered, not refused for its size"
    bound_group, bound_dir = found["at-pane:p1"]
    assert (bound_group, bound_dir) == (_head_tail(group), _head_tail(directory)), "each stored value keeps its head and tail at the field cap"


def test_many_markers_bound_to_escape_heavy_directories_are_answered_inside_the_cap_and_the_lookup_does_not_fail_as_a_whole(
        tmp_path, monkeypatch, sidecar, caplog):
    """t36j U5: each marker found carries `[group, dir]`, up to 2 x 8,192 characters, and the attribution caller sends every unbound
    marker of the vendor (`attribution.py:643-650`). 120 sessions whose directories are 10,000 control characters (cut to 8,192, six bytes
    each once serialized: about 48 KiB a marker) are about 6 MB of bindings, over the whole ledger and far over the 256 KiB fixed share that
    held them before. The bindings are charged to the ledger like the other variable parts: the answer stays inside the cap, the lookup
    does not fail as a whole, the markers that fit are bound exactly as the baseline binds them, and the ones that did not fit are counted
    in `markers_suppressed`, never silently lost. t36k V3: the reader's `find_sessions_by_marker` goes on while the answer counts a
    suppressed marker, so ONE call returns every binding, equal to the baseline's."""
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(tmp_path / "root"))
    path = tmp_path / "root" / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(gs.SCHEMA)
    directory = "/work/" + chr(1) * 10_000
    for n in range(120):
        gs._session(con, f"s{n:03d}", cwd=directory)
        gs._message(con, f"s{n:03d}", "user", gs._text(f"at-pane:p{n:03d} hello"))
    con.close()
    markers = [f"at-pane:p{n:03d}" for n in range(120)]
    answer = sidecar.observe_core("goose", path, "identify", checkpoint={}, seen=[], state={}, markers=markers)
    size = len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= ANSWER_CAP, f"an answer of {size} bytes is over the cap"
    bound, dropped = answer["markers"], answer.get("markers_suppressed", 0)
    assert 0 < len(bound) < 120 and len(bound) + dropped == 120, (len(bound), dropped)
    # t36k V3: the reader does not hand a partial map to its caller. It asks again, in the same call, for the markers the answer did not
    # bind, so the result is what the baseline's one unbounded lookup returns.
    native = cli_runtime_native.native_reader(GooseLogReader, "goose")()
    asked = len(sidecar.asked)
    with caplog.at_level("WARNING"):
        found = native.find_sessions_by_marker(markers)
    assert len(sidecar.asked) - asked >= 2, "one answer cannot carry 120 bindings: the call asks again for the ones left unbound"
    baseline = GooseLogReader().find_sessions_by_marker(markers)
    assert set(found) == set(baseline) == set(markers), "every marker is bound in this one call"
    for marker, (group, bound_dir) in found.items():
        assert (group, bound_dir) == (baseline[marker][0], _head_tail(baseline[marker][1])), "bound as the baseline binds it"
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING], "nothing is left unbound, so nothing is reported"


def test_the_ownership_context_an_observation_carries_is_bounded_by_the_session_that_owns_it(tmp_path, sidecar):
    """t36j U5: `ownership` is added to the answer by the sidecar after the core assembled it, so the core's fixed-share check never counted
    it. It has `OWNERSHIP_BYTES` (4 KiB, `MAX_HAS_FD_FRAME`) of the fixed share and the core's fields have the rest, and it needs no check
    of its own: a session whose identity would not fit that frame is refused when it is created (`handoff_size`), so no observation can be
    bound to one. This pins that bound (it does not change behaviour, so it is not a red-first test)."""
    log = tmp_path / "x.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    peer = sidecar.peer
    script = tmp_path / "handoff_peer.py"
    shutil.copyfile(PEER, script)
    ordinary, generation = peer.create([base_python_executable(), "-u", str(script)], agent_key="claude", pane="pane-1")
    answer, _ = peer.observe("claude", log, "usage", checkpoint={}, session=(ordinary, generation))
    assert answer["ownership"]["pane_id"] == "pane-1" and len(json.dumps(answer["ownership"], separators=(",", ":"))) < 4096
    with pytest.raises(AssertionError, match="handoff_size"):
        peer.create([base_python_executable(), "-u", str(script)], agent_key="claude", pane="p" * 20_000)


def test_a_usage_row_whose_anchor_cells_are_megabytes_leaves_a_checkpoint_inside_its_share(tmp_path, monkeypatch, sidecar):
    """Found while writing the T1 tests: a row cursor's `anchor` is the row's declared anchor cells joined (Copilot's: session, model,
    both token counts, the time). A 2 MiB model name made the anchor, and so the checkpoint and every event's cursor, larger than the
    checkpoint's 1 MiB share, and `usage` was refused for good. An anchor over the key bound travels as a digest (it is only compared)."""
    from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
    from tests.vendors.test_copilot import _add_session, _add_usage, _store
    root = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    _reset_db_warning()
    db = _store(root)
    _add_session(db)
    _add_usage(db, model="m" * (2 * MIB))
    native = cli_runtime_native.native_reader(CopilotLogReader, "copilot")()
    result = native.parse_incremental(db, {})
    assert len(result.events) == 1, "the row is delivered"
    assert len(json.dumps(result.checkpoint)) < 4096, "and its checkpoint is small"
    assert native.parse_incremental(db, result.checkpoint).events == [], "the checkpoint it left is the end of the store"


def test_a_foreign_store_at_a_two_layout_path_with_many_long_escape_heavy_table_names_names_a_bounded_list(tmp_path, sidecar):
    """300 tables, each named with 8,000 control characters (each a six-byte JSON escape): 14 MB of names as they are stored, and
    over the answer cap if the list were only cut by the number of names. `opencode.db` is a two-layout file, so the report names the
    tables it holds when no layout applies."""
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    for n in range(300):
        con.execute(f'CREATE TABLE "t{n:03d}{chr(1) * 8000}" (x)')
    con.commit()
    con.close()
    answer = sidecar.observe_core("opencode", db, "usage", checkpoint={}, seen=[], state={})
    size = len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= ANSWER_CAP, f"an answer of {size} bytes is over the cap"
    problem = answer["schema_problem"]
    assert problem["kind"] == "none_applies" and problem["tables"], problem["kind"]
    listed = len(json.dumps(problem["tables"], ensure_ascii=False, separators=(",", ":")).encode())
    assert listed <= 64 * 1024, f"the list of table names weighs {listed} bytes serialized"
    assert all(len(name) <= 256 + 3 for name in problem["tables"]), "and no name is over 256 characters"
    assert answer["events"] == [] and answer["found"] is False, "the answer is the blank one"
