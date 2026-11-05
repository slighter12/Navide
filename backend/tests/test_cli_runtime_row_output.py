"""What a row store's declaration says an event carries, over the real sidecar (t36j U3).

Goose's baseline gives every usage event `raw={"row_id": n}` (`goose.py:223`): the id of the ledger row the event came from. It is a
field of the returned event, so the core produces it from the declaration (`store.rows.raw_id`), on both usage paths, and the event
equals the baseline's field for field. A missing sidecar binary fails these tests, it never skips them.
"""
from __future__ import annotations

import dataclasses
import sqlite3
import sys

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.log_readers import GooseLogReader
from tests.native_reader_peer import wire
from tests.vendors import test_goose as gs

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(300)]


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(tmp_path / "root"))
    path = tmp_path / "root" / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(gs.SCHEMA)
    gs._session(con, "20261020_1")
    for n in range(1, 8):
        gs._usage(con, "20261020_1", n, n + 1)
    con.close()
    return path


def test_a_goose_usage_event_carries_the_id_of_its_ledger_row_on_both_usage_paths_as_the_baseline_does(tmp_path, monkeypatch, sidecar):
    path = _ledger(tmp_path, monkeypatch)
    baseline, native = GooseLogReader(), cli_runtime_native.native_reader(GooseLogReader, "goose")()
    for read in (lambda reader: reader.parse_incremental(path, {}).events, lambda reader: reader.parse_session_file(path, set())):
        want = [dataclasses.asdict(event) | {"checkpoint": None} for event in read(baseline)]
        got = [dataclasses.asdict(event) | {"checkpoint": None} for event in read(native)]
        assert [event["raw"] for event in want] == [{"row_id": n} for n in range(1, 8)], "the baseline's own field"
        assert got == want, "the native event equals the baseline's, `raw` included"
