"""Grok's old activity bag keeps working for the native reader (t36l Y1, B3 of the t36b checkpoint).

The Python reader (`cli_vendors/grok.py`) persists its activity position as `grok_act::<json>` (a byte cursor: offset, file identity,
anchor) and the open reply text as `grok_text::"<json>"`. A bag written by it reaches the native reader through the declaration's
`legacy_state`: the byte cursor becomes a position and line water the sidecar verifies against the file. These tests make ONE reader
call over the real sidecar after the Python reader's pass and an appended record; the call returns the appended records only.

The log shapes are `tests/vendors/test_grok.py`'s own (`_user`, `_agent`, `_turn`, `_write`, `_append`).
"""
from __future__ import annotations

import json
import logging
import sys

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.grok import GrokLogReader
from tests.native_reader_peer import wire
from tests.vendors.test_grok import SID, _agent, _append, _turn, _user, _write

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"), pytest.mark.timeout(600)]

ACT = "grok_act::"
TEXT = "grok_text::"


@pytest.fixture(autouse=True)
def _grok_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    return tmp_path / ".grok"


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


def _shape(events) -> list:
    return [(event.dedup_key, event.event_type, event.text) for event in events]


def _key(bag: set[str], prefix: str) -> str:
    return next(key for key in bag if key.startswith(prefix))


def test_a_bag_the_python_reader_left_is_read_on_by_the_native_reader_from_its_cursor_with_its_open_reply(_grok_home, sidecar):
    """History, one finished turn and a reply still being built; the Python reader reads it all. A record appended after it finishes the
    reply. The native reader's one call returns what the Python reader's next call would: the appended records, and the turn's whole
    reply, `Hel` from the bag and `lo` from the log, not the history again and not `HelHello`."""
    path = _write(_grok_home, [_user(1, "first"), _agent(2, "old reply"), _turn(3), _user(4, "second"), _agent(5, "Hel")])
    seen: set[str] = set()
    python = GrokLogReader()
    assert [event.event_type for event in python.parse_activity(path, seen)] == ["agent_active", "agent_active", "turn_complete", "agent_active", "agent_active"]
    assert _key(seen, ACT) and _key(seen, TEXT) == f'{TEXT}"Hel"', "the Python reader's bag: a byte cursor and the reply text so far"
    _append(path, [_agent(6, "lo"), _turn(7)])
    expected = GrokLogReader().parse_activity(path, set(seen))
    assert [event.event_type for event in expected] == ["agent_active", "turn_complete"] and expected[-1].text == "Hello"

    native = cli_runtime_native.native_reader(GrokLogReader, "grok")()
    got = native.parse_activity(path, seen)
    assert _shape(got) == _shape(expected), "only the appended records, with the reply text joined once"
    assert not any(key.startswith(ACT) for key in seen), "the byte cursor is spent: the bag keeps only the typed state"
    assert native.parse_activity(path, seen) == [], "nothing is delivered twice"
    _append(path, [_user(8, "third")])
    assert [event.dedup_key for event in native.parse_activity(path, seen)] == [f"act:{SID}-8"], "the next record, from the position the first call left"


def test_a_cursor_the_file_does_not_hold_is_refused_with_a_logged_error_and_the_log_is_read_again_from_its_start(_grok_home, sidecar, caplog):
    """The byte cursor names a file or a place this log is not (a replaced file shorter than the offset, another inode, bytes before the
    offset that changed, an offset inside a line). The sidecar refuses it, the reader logs one warning for the call, and the log is read from
    its start with the reply text of the bag dropped (a half-built reply from another generation is never spliced onto a turn it did not
    belong to): what the Python reader does when its own cursor says the file rotated."""
    path = _write(_grok_home, [_user(1, "first"), _agent(2, "New"), _turn(3)])
    size = path.stat().st_size
    python_bag: set[str] = set()
    GrokLogReader().parse_activity(path, python_bag)
    honest = json.loads(_key(python_bag, ACT)[len(ACT):])
    first_line = path.read_bytes().index(b"\n") + 1
    refusals = {
        "past the end of the file": {**honest, "offset": size + 1000},
        "another file": {**honest, "identity": "1:1"},
        "the bytes before it changed": {**honest, "anchor": "0" * 16},
        "inside a line": {**honest, "offset": first_line + 5, "anchor": ""},
    }
    expected = GrokLogReader().parse_activity(path, set())
    assert [event.event_type for event in expected] == ["agent_active", "agent_active", "turn_complete"] and expected[-1].text == "New"
    native = cli_runtime_native.native_reader(GrokLogReader, "grok")()
    for why, cursor in refusals.items():
        seen = {f"{ACT}{json.dumps(cursor, sort_keys=True)}", f'{TEXT}"stale"'}
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="agent_team_backend.cli_runtime_native"):
            got = native.parse_activity(path, seen)
        assert _shape(got) == _shape(expected), f"{why}: read again from the start, the stale reply text not spliced"
        warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
        assert len(warnings) == 1 and "legacy cursor" in warnings[0] and why in warnings[0], f"{why}: {warnings}"
        assert native.parse_activity(path, seen) == [], f"{why}: and then the position holds"
