"""Codex token-total basis through the Rust sidecar: the same story as `test_codex_token_basis.py`, read natively.

Codex's `input_tokens` already holds `cached_input_tokens` and its `output_tokens` already holds
`reasoning_output_tokens`. A checkpoint an older Navide wrote kept its totals in the double-counting basis and carries no
`basis` marker; the Python reader re-reads the true pair from the rollout once. The sidecar must do the same from the
declared rule (`totals_basis: 2`), and a checkpoint either arm writes must be read correctly by the other.
"""
from contextlib import closing
import json
import sys

import pytest

from agent_team_backend.cli_vendors.codex import CodexLogReader
from tests.cli_regression.support.frame_peer import FramePeer as NativePeer

pytestmark = pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="native POSIX evidence")


def _meta() -> str:
    return json.dumps({"type": "session_meta", "payload": {
        "id": "cx1", "cwd": "/Users/me/proj", "model_provider": "openai"}}) + "\n"


def _count(inp: int, cached: int, out: int, reasoning: int) -> str:
    return json.dumps({"type": "event_msg", "timestamp": "2026-11-05T02:00:00Z", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": reasoning, "total_tokens": inp + out}}}}) + "\n"


def _rollout(tmp_path):
    path = tmp_path / "home" / ".codex" / "sessions" / "rollout-2026-11-05T02-00-00-cx1.jsonl"
    path.parent.mkdir(parents=True)
    return path


def _usage(peer, path, checkpoint):
    result, _ = peer.observe("codex", path, "usage", checkpoint=checkpoint)
    assert result["parser"] == "rust-codex-jsonl-v1", result
    return result


def _pair(event):
    return event["input_tokens"], event["output_tokens"]


def _old_basis(checkpoint, inp, out):
    """The checkpoint an older Navide left: same cursor, totals in the input+cached / output+reasoning basis, no marker."""
    old = {key: value for key, value in checkpoint.items() if key != "basis"}
    old.update({"input_total": inp, "output_total": out})
    return old


def test_native_reader_counts_each_token_once(tmp_path):
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    with closing(NativePeer(tmp_path)) as peer:
        result = _usage(peer, path, {})
    [event] = result["events"]
    assert _pair(event) == (1000, 50)
    assert (result["checkpoint"]["input_total"], result["checkpoint"]["output_total"]) == (1000, 50)


def test_native_reader_resumes_an_old_basis_checkpoint_without_loss_or_repeat(tmp_path):
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    with closing(NativePeer(tmp_path)) as peer:
        first = _usage(peer, path, {})
        old = _old_basis(first["checkpoint"], 1600, 70)
        with path.open("a") as fh:
            fh.write(_count(2500, 1800, 90, 30))
        result = _usage(peer, path, old)
        [event] = result["events"]
        assert _pair(event) == (1500, 40)
        assert (result["checkpoint"]["input_total"], result["checkpoint"]["output_total"]) == (2500, 90)
        assert result["checkpoint"]["basis"] == 2
        assert event["checkpoint"]["basis"] == 2

        # The migrated checkpoint carries on in the new basis.
        with path.open("a") as fh:
            fh.write(_count(3000, 2000, 100, 30))
        [event] = _usage(peer, path, result["checkpoint"])["events"]
        assert _pair(event) == (500, 10)


def test_native_reader_with_nothing_counted_before_the_offset_starts_from_zero(tmp_path):
    """The old checkpoint holds non-zero totals but the log has no reading before its offset: the re-read finds the zero pair.
    (With zero saved totals there is nothing to migrate, so the re-read is never reached and the case proves nothing.)"""
    path = _rollout(tmp_path)
    path.write_text(_meta())
    with closing(NativePeer(tmp_path)) as peer:
        old = _old_basis(_usage(peer, path, {})["checkpoint"], 5, 2)
        with path.open("a") as fh:
            fh.write(_count(1000, 600, 50, 20))
        [event] = _usage(peer, path, old)["events"]
    assert _pair(event) == (1000, 50)


def test_native_reader_takes_a_checkpoint_that_carries_the_basis_as_saved(tmp_path):
    """The re-read is for a checkpoint without the marker only: one with it is not re-derived from the log."""
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    with closing(NativePeer(tmp_path)) as peer:
        marked = dict(_usage(peer, path, {})["checkpoint"], input_total=900, output_total=40)
        with path.open("a") as fh:
            fh.write(_count(2500, 1800, 90, 30))
        [event] = _usage(peer, path, marked)["events"]
    assert _pair(event) == (1600, 50)


def test_native_reader_reads_the_checkpoint_the_python_reader_wrote(tmp_path):
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    python = CodexLogReader()
    written = python.parse_incremental(path, {}).checkpoint
    with closing(NativePeer(tmp_path)) as peer:
        # Written by the Python arm after migration: the marker is there and the totals are taken as they are.
        assert written["basis"] == 2
        with path.open("a") as fh:
            fh.write(_count(2500, 1800, 90, 30))
        [event] = _usage(peer, path, written)["events"]
        assert _pair(event) == (1500, 40)
        # Written by the Python arm before the basis existed: re-read once.
        [expected] = python.parse_incremental(path, _old_basis(written, 1600, 70)).events
        [event] = _usage(peer, path, _old_basis(written, 1600, 70))["events"]
    assert _pair(event) == (expected.input_tokens, expected.output_tokens) == (1500, 40)


@pytest.mark.parametrize("later", [None, (2500, 1800, 90, 30)], ids=["no-later-record", "later-record"])
def test_both_readers_agree_when_the_old_offset_falls_inside_a_rewritten_record(tmp_path, later):
    """A same-inode rewrite leaves the old checkpoint's offset between a complete record's JSON and its newline. Both readers
    see the changed anchor and read the tail from byte 0, but the basis re-read still uses the old offset: the Python one parses
    the exact prefix (`head.splitlines()`), so the unterminated final element counts when it is complete JSON. The native one
    must give the same events and the same checkpoint."""
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    with closing(NativePeer(tmp_path)) as peer:
        old = _old_basis(_usage(peer, path, {})["checkpoint"], 1600, 70)
        record = _count(1000, 600, 50, 20).rstrip("\n")
        # The old offset is the end of the original file: one byte past the rewritten record's JSON, before its newline.
        body = _meta() + record + "  \n" + (_count(*later) if later else "")
        assert len(_meta() + record) + 1 == old["offset"] < len(_meta() + record + "  \n")
        path.write_text(body)
        native = _usage(peer, path, old)
    python = CodexLogReader().parse_incremental(path, old)
    assert [_pair(e) for e in native["events"]] == [(e.input_tokens, e.output_tokens) for e in python.events]
    assert [_pair(e) for e in native["events"]] == ([(1500, 40)] if later else [])
    for key in ("input_total", "output_total", "basis", "offset", "identity", "anchor"):
        assert native["checkpoint"][key] == python.checkpoint[key], key


def test_python_reader_reads_the_checkpoint_the_native_reader_wrote(tmp_path):
    path = _rollout(tmp_path)
    path.write_text(_meta() + _count(1000, 600, 50, 20))
    with closing(NativePeer(tmp_path)) as peer:
        old = _old_basis(_usage(peer, path, {})["checkpoint"], 1600, 70)
        with path.open("a") as fh:
            fh.write(_count(2500, 1800, 90, 30))
        migrated = _usage(peer, path, old)["checkpoint"]
    assert migrated["basis"] == 2
    with path.open("a") as fh:
        fh.write(_count(3000, 2000, 100, 30))
    [event] = CodexLogReader().parse_incremental(path, migrated).events
    assert (event.input_tokens, event.output_tokens) == (500, 10)
