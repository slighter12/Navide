"""A store file with several candidate layouts, none of which applies, over the real sidecar.

OpenCode's and Kilo's database file is read with the OpenCode 1.x or the OpenCode 2 layout, told apart by the tables it holds.
A store holding neither (no table at all, or only `session_v2`) answers the blank document in every mode, checkpoint and state
as given, with `schema_problem` naming the tables it holds; the reader logs it once as the baseline's `unrecognized-schema` read
problem. A half-built OpenCode 2 side beside the 1.x tables is still asked for a session, as the baseline asks every held parent
table.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors import opencode as opencode_module
from agent_team_backend.cli_vendors.opencode import read_problem
from agent_team_backend.log_readers import KiloLogReader, OpencodeLogReader
from tests.native_reader_peer import wire
from tests.vendors import test_opencode_v2 as v2

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(120)]

VENDORS = [("opencode", OpencodeLogReader, "opencode.db"), ("kilo", KiloLogReader, "kilo.db")]


@pytest.fixture(autouse=True)
def _no_open_problems():
    opencode_module._READ_PROBLEMS.clear()
    yield
    opencode_module._READ_PROBLEMS.clear()


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


def _db(tmp_path: Path, monkeypatch, vendor: str, name: str, held: str) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / vendor
    directory.mkdir(parents=True)
    path = directory / name
    connection = sqlite3.connect(path)
    if held == "zero-tables":
        connection.execute("CREATE TABLE gone(x)")
        connection.execute("DROP TABLE gone")
    elif held == "session_v2-only":
        connection.execute("CREATE TABLE session_v2(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO session_v2 VALUES ('ses_half')")
    elif held == "half-v2":
        connection.executescript(v2._V1_LEFTOVER_SCHEMA)
        connection.execute("CREATE TABLE session_v2(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO session_v2 VALUES ('ses_half')")
    connection.commit()
    connection.close()
    return path


def _answers(reader, db: Path) -> dict:
    """What the reader answers in every mode for one store file."""
    usage = reader.parse_incremental(db, {"row_id": 7, "pending": [3], "identity": "1:2"})
    replayed = {"already"}
    return {"usage": (usage.events, usage.checkpoint),
            # The native reader keeps its typed state in the bag under one `native_state::` key; the keys of rows are the bag's own.
            "replay": (reader.parse_session_file(db, replayed), sorted(key for key in replayed if not key.startswith("native_state::"))),
            "activity": reader.parse_activity(db, {"kept"}),
            "has_session": reader.has_session("ses_half"),
            "markers": reader.find_sessions_by_marker(["at-pane:0000"])}


def _problem(vendor: str):
    problem = read_problem(vendor)
    return problem and {key: value for key, value in problem.items() if key != "since"}


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING and "cannot be read" in record.getMessage()]


@pytest.mark.parametrize("held", ["zero-tables", "session_v2-only"])
@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_a_store_that_fits_no_layout_answers_blank_in_every_mode_and_is_reported_once_as_the_baseline_reports_it(
        vendor, discovery, name, held, sidecar, tmp_path, monkeypatch, caplog):
    db = _db(tmp_path, monkeypatch, vendor, name, held)
    tables = "session_v2" if held == "session_v2-only" else "none"
    with caplog.at_level(logging.WARNING):
        baseline = _answers(discovery(), db)
        baseline_problem, baseline_warnings = _problem(vendor), _warnings(caplog)
        opencode_module._READ_PROBLEMS.clear()
        caplog.clear()
        native = _answers(cli_runtime_native.native_reader(discovery, vendor)(), db)
    assert native == baseline, "the same answer in every mode"
    assert _problem(vendor) == baseline_problem and baseline_problem["reason"] == "unrecognized-schema"
    assert baseline_problem["detail"] == f"tables: {tables}"
    assert len(baseline_warnings) == 1 and len(_warnings(caplog)) == 1, "logged once across every mode"


@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_a_store_read_once_it_fits_a_layout_clears_the_schema_problem(vendor, discovery, name, sidecar, tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch, vendor, name, "zero-tables")
    reader = cli_runtime_native.native_reader(discovery, vendor)()
    reader.parse_activity(db, set())
    assert _problem(vendor)["reason"] == "unrecognized-schema"
    connection = sqlite3.connect(db)
    connection.executescript(v2._V1_LEFTOVER_SCHEMA)
    connection.commit()
    connection.close()
    reader.parse_activity(db, set())
    assert _problem(vendor) is None, "the same read works again: the recorded problem no longer holds"


#: One store file taken through every kind of schema problem and out again, one SQL script per step. Each step names the problem
#: the baseline records for it (`kind`), which is what the native reader must record too, and clear again when the next step fixes it.
_TRANSITIONS = [
    ("none_applies", ""),
    ("partial", v2._V1_LEFTOVER_SCHEMA + "CREATE TABLE session_v2(id TEXT PRIMARY KEY);"),
    ("mixed", "CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, "
              "time_updated INTEGER, data TEXT); INSERT INTO message VALUES ('msg_v1', 'ses_v1', 1, 900, '{}');"),
    (None, "UPDATE message SET time_updated = 0;"),
    ("mixed", "UPDATE message SET time_updated = 900;"),
    ("partial", "DROP TABLE session_message;"),
    (None, "DROP TABLE session_v2;"),
    ("none_applies", "DROP TABLE session;"),
]


def _play(reader, vendor: str, db: Path) -> list:
    """The read problem the reader holds after each step of `_TRANSITIONS`, and the warnings it logged for them."""
    seen = []
    connection = sqlite3.connect(db)
    for _, script in _TRANSITIONS:
        connection.executescript(script)
        connection.commit()
        reader.parse_activity(db, set())
        seen.append(_problem(vendor))
    connection.close()
    return seen


@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_every_schema_problem_kind_and_every_transition_between_them_is_recorded_and_cleared_as_the_baseline_does(
        vendor, discovery, name, sidecar, tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        baseline = _play(discovery(), vendor, _db(tmp_path / "baseline", monkeypatch, vendor, name, "zero-tables"))
        baseline_warnings = _warnings(caplog)
        opencode_module._READ_PROBLEMS.clear()
        caplog.clear()
        native = _play(cli_runtime_native.native_reader(discovery, vendor)(), vendor,
                       _db(tmp_path / "native", monkeypatch, vendor, name, "zero-tables"))
    reasons = {"none_applies": "unrecognized-schema", "partial": "partial-v2-schema", "mixed": "mixed-schema"}
    assert [problem and problem["reason"] for problem in baseline] == [kind and reasons[kind] for kind, _ in _TRANSITIONS]
    assert [problem and problem["status"] for problem in baseline] == [
        {"none_applies": "unreadable", "partial": "partial", "mixed": "partial", None: None}[kind] for kind, _ in _TRANSITIONS]
    # The two readers read two copies of the store (each step is applied to a fresh file), so the paths differ by their root.
    here = lambda text, root: text.replace(str(tmp_path / root), "ROOT")  # noqa: E731
    assert [problem and {**problem, "path": here(problem["path"], "native")} for problem in native] == \
        [problem and {**problem, "path": here(problem["path"], "baseline")} for problem in baseline], \
        "the same record (reason, status, path, detail) after every step"
    assert [here(line, "native") for line in _warnings(caplog)] == [here(line, "baseline") for line in baseline_warnings], \
        "and the same warnings: once per distinct problem, not per read"


@pytest.mark.parametrize("unreadable", ["locked", "missing"])
@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_a_store_that_cannot_be_read_neither_records_nor_clears_a_schema_problem_as_the_baseline_does(
        vendor, discovery, name, unreadable, sidecar, tmp_path, monkeypatch):
    """A store that is locked by a writer or gone says nothing about its schema: the baseline's `_tables` answers None and `_schema`
    neither records a problem nor clears the one it holds. The sidecar says the table list could not be read (`kind: unknown`), and
    the reader leaves the recorded problem as it is (a locked store used to read as holding no table: a false `unrecognized-schema`;
    a missing one as a store that fits a layout: the recorded problem was cleared)."""
    shape = lambda problem: problem and (problem["reason"], problem["status"], problem["detail"])  # noqa: E731
    held = {}
    for label, make in (("baseline", discovery), ("native", lambda: cli_runtime_native.native_reader(discovery, vendor)())):
        opencode_module._READ_PROBLEMS.clear()
        db = _db(tmp_path / label, monkeypatch, vendor, name, "zero-tables")
        reader = make()
        connection = sqlite3.connect(db)
        connection.executescript(_TRANSITIONS[1][1])
        connection.commit()
        connection.close()
        reader.parse_activity(db, set())
        before = shape(_problem(vendor))
        assert before and before[0] == "partial-v2-schema"
        if unreadable == "locked":
            writer = sqlite3.connect(db, isolation_level=None)
            writer.execute("BEGIN EXCLUSIVE")
            try:
                reader.parse_activity(db, set())
                reader.parse_session_file(db, set())
                reader.parse_incremental(db, {})
            finally:
                writer.close()
        else:
            db.unlink()
            reader.parse_activity(db, set())
            reader.parse_session_file(db, set())
        held[label] = (before, shape(_problem(vendor)))
    assert held["baseline"][1] == held["baseline"][0], "the baseline leaves the recorded problem as it was"
    assert held["native"] == held["baseline"], "and so does the native reader"


@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_a_partial_or_mixed_store_is_read_with_the_layout_the_baseline_reads_it_with_and_answers_the_same_in_every_mode(
        vendor, discovery, name, sidecar, tmp_path, monkeypatch):
    for kind, script in _TRANSITIONS[:3]:
        db = _db(tmp_path / kind, monkeypatch, vendor, name, "zero-tables")
        connection = sqlite3.connect(db)
        for _, earlier in _TRANSITIONS[: [k for k, _ in _TRANSITIONS].index(kind) + 1]:
            connection.executescript(earlier)
        connection.commit()
        connection.close()
        assert _answers(cli_runtime_native.native_reader(discovery, vendor)(), db) == _answers(discovery(), db), kind


@pytest.mark.parametrize("vendor, discovery, name", VENDORS)
def test_a_half_built_v2_side_beside_the_v1_tables_is_asked_for_a_session_as_the_baseline_asks_it(
        vendor, discovery, name, sidecar, tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch, vendor, name, "half-v2")
    assert discovery().has_session("ses_half") is True, "the baseline asks every held parent table"
    assert cli_runtime_native.native_reader(discovery, vendor)().has_session("ses_half") is True
    assert cli_runtime_native.native_reader(discovery, vendor)().has_session("ses_absent") is False
