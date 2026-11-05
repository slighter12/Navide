"""Each per-turn usage event's cursor describes its own offset, over the real sidecar (t36g R4).

The watcher persists an event's `checkpoint` as the place a later pass resumes from, so its `anchor` (the blake2b-8 digest of the
512 bytes that end at its `offset`) has to be the digest of the bytes behind that event's own offset, not the pass's final one. A
cursor whose anchor does not match its offset reads, at the next pass, as a log that was rewritten, and the pass starts over.

Named difference from the baseline reader: `ClaudeLogReader.parse_incremental` (`cli_vendors/claude.py:476-478`) copies the pass's
final checkpoint into each event and sets only its `offset`, so the baseline's per-event anchor is the pass-final one. The native
reader answers the anchor of each event's own offset, which is the self-consistent form the persisted cursor needs.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.native_reader_peer import wire

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(120)]


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


def _record(index: int) -> str:
    """A Claude assistant record of about 700 bytes, so the 512 bytes behind one line's end lie inside that line."""
    return json.dumps({"type": "assistant", "requestId": f"request-{index}", "version": "v",
                       "message": {"id": f"message-{index}", "content": [{"type": "text", "text": f"{index}" * 600}],
                                   "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}})


def _anchor(data: bytes, offset: int) -> str:
    return hashlib.blake2b(data[max(0, offset - 512):offset], digest_size=8).hexdigest()


def test_two_usage_events_at_different_offsets_in_one_pass_each_carry_the_anchor_of_their_own_offset(tmp_path, sidecar):
    log = tmp_path / "-tmp-anchor" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(_record(n) + "\n" for n in (1, 2, 3)))
    data = log.read_bytes()
    answer = sidecar.observe_core("claude", log, "usage", checkpoint={})
    cursors = [event["checkpoint"] for event in answer["events"]]
    assert len(cursors) == 3 and len({cursor["offset"] for cursor in cursors}) == 3, "three events at three offsets"
    assert [cursor["anchor"] for cursor in cursors] == [_anchor(data, cursor["offset"]) for cursor in cursors], \
        "each event's anchor digests the bytes behind its own offset"
    assert answer["checkpoint"]["offset"] == len(data) == cursors[-1]["offset"]
    assert answer["checkpoint"]["anchor"] == cursors[-1]["anchor"], "the last event's cursor is the pass's"


def test_a_pass_resumed_from_the_first_events_cursor_continues_in_place_and_does_not_start_over(tmp_path, sidecar):
    log = tmp_path / "-tmp-anchor" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(_record(n) + "\n" for n in (1, 2, 3)))
    first = sidecar.observe_core("claude", log, "usage", checkpoint={})["events"][0]
    # The window of recent keys would hide a restart (the first record's key is in it), so it is dropped from the cursor.
    resumed = sidecar.observe_core("claude", log, "usage", checkpoint={**first["checkpoint"], "recent_keys": []})
    assert [event["dedup_key"] for event in resumed["events"]] == ["message-2::request-2", "message-3::request-3"], \
        "the rest of the log, once each: the cursor is not read as a rewritten log"
