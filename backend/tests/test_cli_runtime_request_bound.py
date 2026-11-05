"""The request side of the bound (t36h S2, S3), over the real sidecar.

- S3: a replay never sends the whole bag it has grown: each page of one call is sent the bag as it was when the call began, so
  `total_usage_for_session` over a store of 150,000 rows (more than 4 MiB of real-length keys) equals the baseline's totals; and a
  request that would be over the meta cap is an explicit, logged error, never a silent partial result.
- S2: a dedup key over the fixed bound (1 KiB) travels as a stable digest, so the answer fits and dedup holds across retries.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.claude import ClaudeLogReader
from agent_team_backend.log_readers import OpencodeLogReader
from tests.native_reader_peer import wire
from tests.vendors import test_opencode as oc

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"), pytest.mark.timeout(900)]

MIB = 1024 * 1024


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


def _opencode_db(tmp_path, monkeypatch, messages: int):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg" / "opencode"
    directory.mkdir(parents=True)
    db = directory / "opencode.db"
    con = oc._create_db(db, wal=False)
    oc._add_session(con, oc._SID, "/work")
    data = json.dumps(oc._assistant_data({"input": 10, "output": 5}))
    con.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)",
                    [(f"msg_{n:09d}{'a' * 20}", oc._SID, oc._NOW_MS, oc._NOW_MS, data) for n in range(messages)])
    con.commit()
    con.close()
    return db


def test_total_usage_over_150000_rows_of_real_length_keys_equals_the_baseline_with_every_request_inside_the_cap(tmp_path, monkeypatch, sidecar):
    db = _opencode_db(tmp_path, monkeypatch, 150_000)
    baseline = OpencodeLogReader().total_usage_for_session(db, oc._SID)
    sizes: list[int] = []
    original = cli_runtime_native.observe_core

    def measured(vendor, path, mode, **kwargs):
        sizes.append(len(json.dumps({"seen": sorted(kwargs.get("seen") or ()), "state": kwargs.get("state") or {}}, separators=(",", ":")).encode()))
        return original(vendor, path, mode, **kwargs)

    monkeypatch.setattr(cli_runtime_native, "observe_core", measured)
    native = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
    assert native.total_usage_for_session(db, oc._SID) == baseline
    assert len(sizes) >= 2 and max(sizes) < 4 * MIB, f"every page's request is inside the cap: {sizes}"


def test_a_request_over_the_meta_cap_is_an_explicit_logged_error_never_a_partial_result(tmp_path, monkeypatch, sidecar, caplog):
    db = _opencode_db(tmp_path, monkeypatch, 10)
    native = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
    bag = {f"old::{n:030d}" for n in range(140_000)}  # a bag kept across calls: about 5.6 MB of keys
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="REQUEST_TOO_LARGE"):
        native.parse_session_file(db, bag)
    assert any("REQUEST_TOO_LARGE" in record.getMessage() for record in caplog.records), "and it is logged"


def test_the_guard_sits_on_the_request_observe_core_sends_so_a_direct_caller_cannot_bypass_it(monkeypatch, caplog):
    """t36i T6: `observe_core` is the one place a request is made, so it is also the one place it is checked: a caller that does not go
    through the reader (and so never reaches `_native`) still gets the explicit, logged refusal, and nothing is submitted."""
    from agent_team_backend import cli_runtime_observation
    submitted: list[dict] = []
    monkeypatch.setattr(cli_runtime_observation, "_submit", lambda *args, **kwargs: submitted.append(args))
    bag = {f"old::{n:030d}" for n in range(140_000)}
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="REQUEST_TOO_LARGE"):
        cli_runtime_observation.observe_core("opencode", "/tmp/opencode.db", "legacy", seen=bag)
    assert submitted == [], "an over-cap request is never submitted"
    assert any("REQUEST_TOO_LARGE" in record.getMessage() for record in caplog.records), "and it is logged"
    cli_runtime_observation.observe_core("opencode", "/tmp/opencode.db", "legacy", seen={"k1"})
    assert len(submitted) == 1, "a request inside the cap goes through"


def test_a_page_that_redelivers_a_key_of_an_earlier_page_of_the_call_is_dropped_and_each_page_gets_the_bag_the_call_began_with(
        tmp_path, monkeypatch, sidecar):
    db = _opencode_db(tmp_path, monkeypatch, 10)
    native = cli_runtime_native.native_reader(OpencodeLogReader, "opencode")()
    original = cli_runtime_native.observe_core
    asked: list[list[str]] = []
    first_answer: list[dict] = []

    def paged(vendor, path, mode, **kwargs):
        asked.append(sorted(kwargs.get("seen") or ()))
        if not first_answer:
            first_answer.append(original(vendor, path, mode, **kwargs))
            return {**first_answer[0], "more": True}
        return {**first_answer[0], "more": False}  # a second page that reads the same rows again (the file changed under it)

    monkeypatch.setattr(cli_runtime_native, "observe_core", paged)
    bag = {"began::1"}
    events = native.parse_session_file(db, bag)
    baseline = OpencodeLogReader().parse_session_file(db, set())
    assert [event.dedup_key for event in events] == [event.dedup_key for event in baseline], "each key once"
    assert len(asked) == 2 and asked[0] == asked[1] == ["began::1"], "the second page is not sent the first page's keys"
    assert {key for key in bag if not key.startswith("native_state::")} == {"began::1", *(event.dedup_key for event in baseline)}


def _claude_log(tmp_path, monkeypatch, request_id: str):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    log = tmp_path / "projects" / "-tmp-demo" / "abc-123.jsonl"
    log.parent.mkdir(parents=True)
    records = [{"type": "assistant", "sessionId": "abc-123", "requestId": rid, "timestamp": f"2026-05-28T00:00:0{n}Z",
                "message": {"id": f"m{n}", "model": "m", "usage": {"input_tokens": 10, "output_tokens": 5}}}
               for n, rid in ((1, "short"), (2, request_id))]
    log.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return log


def test_a_nine_mib_dedup_key_travels_as_a_stable_digest_and_dedup_holds_across_retries(tmp_path, monkeypatch, sidecar):
    log = _claude_log(tmp_path, monkeypatch, "R" * (9 * MIB))
    native = cli_runtime_native.native_reader(ClaudeLogReader, "claude")()
    bag: set[str] = set()
    events = native.parse_session_file(log, bag)
    keys = [event.dedup_key for event in events]
    assert keys[0] == "m1::short", "a real key never changes"
    assert len(keys[1]) <= 1100 and keys[1].startswith("long::"), keys[1][:40]
    assert native.parse_session_file(log, bag) == [], "a retry with the bag delivers nothing"
    again = cli_runtime_native.native_reader(ClaudeLogReader, "claude")().parse_session_file(log, set())
    assert [event.dedup_key for event in again] == keys, "the digest is stable across calls"
    checkpoint: dict = {}
    first = native.parse_incremental(log, checkpoint)
    assert [event.dedup_key for event in first.events] == keys
    assert native.parse_incremental(log, first.checkpoint).events == [], "the cursor does not deliver it twice"
    assert all(len(json.dumps(event.checkpoint)) < 200_000 for event in first.events if getattr(event, "checkpoint", None))
