"""Grok's turn breakdown is answered by the native core and equals the Python reader's (t36l Y5).

`GrokLogReader.turns_for_session` (`cli_vendors/grok.py:395-456`) is the oracle: one turn per `turn_completed` record, its calls the entries of
`usage.modelUsage` in the order the line spells them, its prompt the user chunks before it. Every case below is read twice over the same log,
once by the Python reader and once by the native reader over the real sidecar, and the rows must be equal field for field; the native read must
have asked the sidecar for the `turns` mode (a reader that fell back to the Python code would pass the comparison and fail that).

The record shapes are `tests/vendors/test_grok.py`'s own (`_record`, `_user`, `_turn`); the hand-spelled line keeps the order of its models.
"""
from __future__ import annotations

import dataclasses
import json
import sys

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.grok import GrokLogReader
from tests.native_reader_peer import wire
from tests.vendors.test_grok import SID, _agent, _record, _turn, _user, _write

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"), pytest.mark.timeout(600)]

OTHER = "other-session"


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


def _usage_turn(n: int, usage, session_id: str = SID) -> dict:
    return _record("turn_completed", n, session_id, stop_reason="end_turn", usage=usage)


def _rows(reader, path, session: str = "") -> list[dict]:
    return [dataclasses.asdict(turn) for turn in reader.turns_for_session(path, session)]


def _log() -> list:
    """Every branch of the Python reader's loop: a typed prompt over two chunks, a wrapper that only stands in until a typed prompt arrives, a
    usage with no `modelUsage`, cache counts that are parts of the input, `modelCalls` zero, a usage that is no object (skipped, the prompt
    waits for the next turn), a turn that counts nothing (dropped, the prompt spent), another session's chunks and turn, and a line whose
    models are spelled `zeta` before `alpha` (a parsed map would sort them)."""
    zeta_first = (
        '{"params":{"sessionId":"%s","_meta":{"eventId":"zz-1","agentTimestampMs":1789379460000},"update":{"sessionUpdate":"turn_completed",'
        '"usage":{"inputTokens":9,"outputTokens":3,"modelCalls":2,"modelUsage":{"zeta-model":{"inputTokens":6,"outputTokens":2,'
        '"cachedReadTokens":4,"cacheCreationTokens":1},"alpha-model":{"inputTokens":3,"outputTokens":1}}}}}}' % SID)
    return [
        _user(1, "  rename   this\tpane  "), _user(2, "and then more"), _agent(3, "reply"),
        _usage_turn(4, {"inputTokens": 100, "outputTokens": 10, "modelCalls": 3, "modelUsage": {
            "grok-4.6": {"inputTokens": 80, "outputTokens": 7, "cachedReadTokens": 30, "cacheCreationTokens": 5},
            "grok-fast": {"inputTokens": 20, "outputTokens": 3}}}),
        _user(5, "<system-reminder>injected</system-reminder>"), _user(6, "the typed prompt"),
        _usage_turn(7, {"inputTokens": 12, "outputTokens": 4, "modelCalls": 0}),
        _user(8, "waits for a turn"), _usage_turn(9, "not an object"),
        _user(10, "<wrapper only>"), _usage_turn(11, {"inputTokens": 0, "outputTokens": 0, "modelUsage": {"m": {}}}),
        _user(12, "after the empty one"), _usage_turn(13, {"inputTokens": 5, "outputTokens": 1, "modelUsage": {"m": "not a map", "n": {"inputTokens": 5, "outputTokens": 1}}}),
        _user(14, "other session's words", OTHER), _usage_turn(15, {"inputTokens": 50, "outputTokens": 5, "modelUsage": {"o": {"inputTokens": 50, "outputTokens": 5}}}, OTHER),
        _user(16, "x" * 600),
        zeta_first,
        _turn(17, inp=2, out=1),
    ]


def _write_log(home) -> "Path":  # noqa: F821
    path = _write(home, [])
    path.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in _log()), encoding="utf-8")
    return path


@pytest.mark.parametrize("session", ["", SID, OTHER, "nobody"])
def test_the_native_breakdown_equals_the_python_readers_for_every_session_filter(_grok_home, sidecar, monkeypatch, session):
    path = _write_log(_grok_home)
    modes: list[str] = []
    original = cli_runtime_native.observe_core

    def asked(vendor, path, mode, **kwargs):
        modes.append(str(mode))
        return original(vendor, path, mode, **kwargs)

    monkeypatch.setattr(cli_runtime_native, "observe_core", asked)
    want = _rows(GrokLogReader(), path, session)
    got = _rows(cli_runtime_native.native_reader(GrokLogReader, "grok")(), path, session)
    assert any(mode.endswith("turns") for mode in modes), f"the sidecar was never asked for the breakdown: {modes}"
    assert got == want
    if session in ("", SID):
        assert want, "the scenario holds turns"
    if session == "":
        zeta = want[-2]
        assert [call["model"] for call in zeta["calls"]] == ["zeta-model", "alpha-model"], "the models keep the order the line spells"
        assert zeta["model_calls"] == 2 and zeta["calls"][0]["cache_read"] == 4 and zeta["calls"][0]["input"] == 1
        assert any(turn["model_calls"] is None for turn in want), "modelCalls zero is no count"
    if session == "nobody":
        assert want == [], "no record names this session"
