"""`has_session` over the real wire: the lookup names its session in `wanted_session`, never in an ownership key.

The sidecar reads `session_id`, `session_generation`, `pane_id` and `workspace_path` as who owns an observation and refuses a
`session_id` that is not a live session (`UNKNOWN_SESSION`), so a lookup that spelled its target `session_id` could never reach the
store. Each case asks a native reader (`NativeStore.has_session`) of a store vendor whose real sidecar answers, for an id the
sidecar's live `Sessions` map has never held (the peer has no live session at all).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.goose import GooseLogReader
from agent_team_backend.log_readers import KiloLogReader, OpencodeLogReader
from tests.native_reader_peer import wire
from tests.vendors import test_goose, test_opencode_v2 as v2

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(120)]

OWNERSHIP_KEYS = {"session_id", "session_generation", "pane_id", "workspace_path"}


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


def _xdg(tmp_path: Path, monkeypatch, vendor: str) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / vendor
    directory.mkdir(parents=True)
    return directory


def _opencode_1x(tmp_path, monkeypatch):
    path = _xdg(tmp_path, monkeypatch, "opencode") / "opencode.db"
    connection = sqlite3.connect(path)
    connection.executescript(v2._V1_LEFTOVER_SCHEMA)
    connection.execute("INSERT INTO session VALUES ('ses_known', 'proj1', NULL, 'slug', '/ws', 't', '1.19.3', 1, 1)")
    connection.commit()
    connection.close()
    return cli_runtime_native.native_reader(OpencodeLogReader, "opencode")(), "ses_known"


def _opencode_2(tmp_path, monkeypatch):
    db = v2._Db(_xdg(tmp_path, monkeypatch, "opencode") / "opencode.db")
    db.session(v2._SID, "/ws")
    return cli_runtime_native.native_reader(OpencodeLogReader, "opencode")(), v2._SID


def _opencode_upgraded(tmp_path, monkeypatch):
    """Both schemas in one file: a valid id may live on either side, and either side's id resumes."""
    db = v2._Db(_xdg(tmp_path, monkeypatch, "opencode") / "opencode.db", upgraded_from_v1=True)
    db.session(v2._SID, "/ws")
    db.con.execute("INSERT INTO session VALUES ('ses_known', 'proj1', NULL, 'slug', '/ws', 't', '1.19.3', 1, 1)")
    db.con.commit()
    return cli_runtime_native.native_reader(OpencodeLogReader, "opencode")(), "ses_known"


def _kilo(tmp_path, monkeypatch):
    db = v2._Db(_xdg(tmp_path, monkeypatch, "kilo") / "kilo.db")
    db.session(v2._SID, "/ws")
    return cli_runtime_native.native_reader(KiloLogReader, "kilo")(), v2._SID


def _goose(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(root))
    path = root / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(test_goose.SCHEMA)
    test_goose._session(connection, "20261020_1")
    connection.close()
    return cli_runtime_native.native_reader(GooseLogReader, "goose")(), "20261020_1"


@pytest.mark.parametrize("build", [_opencode_1x, _opencode_2, _opencode_upgraded, _kilo, _goose],
                         ids=["opencode-1x", "opencode-2", "opencode-upgraded", "kilo", "goose"])
def test_a_session_lookup_reaches_the_store_through_the_real_sidecar(build, sidecar, tmp_path, monkeypatch):
    reader, known = build(tmp_path, monkeypatch)
    assert reader.has_session(known) is True
    assert reader.has_session("ses_absent") is False
    asked = [request for request in sidecar.asked if request["mode"] == "has_session"]
    assert len(asked) >= 2 and all(request["wanted_session"] for request in asked)
    for request in asked:
        assert not OWNERSHIP_KEYS & set(request), f"a lookup names its target in `wanted_session`, not in an ownership key: {sorted(request)}"
