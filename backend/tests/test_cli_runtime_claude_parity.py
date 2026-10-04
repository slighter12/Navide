"""Supplemental native-protocol parity; principal proofs use production WS."""
import base64
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import sys

import pytest

from agent_team_backend.cli_vendors.claude import ClaudeLogReader
from agent_team_backend.log_readers.base import ActivityEvent, TokenUsage, TurnCall, TurnUsage, user_prompt_text
from tests.test_cli_runtime_native import NativePeer

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="native macOS reference evidence")


def observe(peer, path, mode, **args):
    request = peer.send("observe", {"vendor": "claude", "path": str(path), "mode": mode, **args})
    reply = peer.result(request)
    parts = [e for e in peer.events if e.get("event") == "observation" and e["observation_id"] == request]
    assert len(parts) == reply["observation_parts"]
    assert [e["part"] for e in parts] == list(range(len(parts)))
    assert all(len(json.dumps(e).encode()) < 65536 for e in parts)
    result = json.loads(b"".join(base64.b64decode(e["data_b64"], validate=True) for e in parts))
    assert result["parser"] == "rust-claude-jsonl-v1" and result["path"] == str(path)
    anchor = base64.b64decode(result["anchor_b64"])
    spelling = hashlib.blake2b(anchor, digest_size=8).hexdigest() if anchor else ""
    result["checkpoint"]["anchor"] = spelling
    for event in result["events"]:
        if "checkpoint" in event:
            event["checkpoint"]["anchor"] = spelling
    if mode == "activity":
        events = [ActivityEvent(**row) for row in result["events"]]
        for event in events:
            if event.detail == "user" and event.text:
                event.text = user_prompt_text(event.text)
        result["events"] = [asdict(event) for event in events]
    else:
        result["events"] = [asdict(TokenUsage(**row)) for row in result["events"]]
    for row in result["turns"]:
        row["calls"] = [TurnCall(**call) for call in row["calls"]]
    result["turns"] = [asdict(TurnUsage(**row)) for row in result["turns"]]
    return result


def assistant(index, content, stop="tool_use", **extra):
    return {"type": "assistant", "requestId": f"request-{index}", "version": "fixture-version", **extra,
            "message": {"id": f"message-{index}", "content": content, "stop_reason": stop,
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": 2,
                                  "cache_creation_input_tokens": 3, "output_tokens": 4}}}


def test_native_usage_activity_and_turns_match_existing_reader(tmp_path):
    records = [
        {"type": "user", "promptId": "p1", "message": {"content": "<metadata>wrapped</metadata>"}},
        {"type": "user", "promptId": "p1", "message": {"content": "Human prompt café 世界 " * 20}},
        assistant(1, [{"type": "text", "text": "thinking"}, {"type": "tool_use", "name": "AskUserQuestion", "input": {}}]),
        {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": "agent-async"}, "message": {"content": []}},
        {"type": "user", "toolUseResult": {"status": "forked", "background": True, "agentId": "agent-fork"}, "message": {"content": []}},
        assistant(2, [{"type": "tool_use", "name": "KillShell", "input": {"shell_id": "shell-1"}},
                      {"type": "tool_use", "name": "KillBash", "input": {"shell_id": "shell-2"}}]),
        {"type": "queue-operation", "operation": "enqueue", "content": "<task-notification><task-id>agent-async</task-id><status>completed</status></task-notification>"},
        {"type": "queue-operation", "operation": "enqueue", "content": "<task-notification><task-id>monitor-only</task-id>monitor</task-notification>"},
        assistant(3, [{"type": "text", "text": "first"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "完成"}], "end_turn"),
        assistant(3, [], "tool_use"),
        {"type": "user", "promptId": "p2", "message": {"content": "Next prompt"}},
        assistant(4, "string completion", "end_turn"),
    ]
    path = tmp_path / "home/.claude/projects/-tmp-parity/session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n\ncorrupt complete\n")
    baseline = ClaudeLogReader()
    with closing(NativePeer(tmp_path)) as peer:
        parsed = observe(peer, path, "usage", checkpoint={})
        expected = baseline.parse_incremental(path, {})
        assert parsed["events"] == [asdict(e) for e in expected.events]
        assert parsed["checkpoint"] == expected.checkpoint
        assert observe(peer, path, "usage", checkpoint=expected.checkpoint,
                       expected_anchor_b64=parsed["anchor_b64"])["events"] == []
        seen = set()
        activity = observe(peer, path, "activity", seen=[])
        expected_activity = baseline.parse_activity(path, seen)
        assert activity["events"] == [asdict(e) for e in expected_activity]
        assert activity["seen"] == sorted(seen)
        assert observe(peer, path, "activity", seen=sorted(seen))["events"] == []
        assert observe(peer, path, "turns")["turns"] == [asdict(t) for t in baseline.turns_for_session(path)]
        prior = set()
        assert observe(peer, path, "session_usage", seen=[])["events"] == [asdict(e) for e in baseline.parse_session_file(path, prior)]
        assert observe(peer, path, "session_usage", seen=sorted(prior))["events"] == []


def test_native_partial_anchor_rewrite_rotation_and_activity_highwater(tmp_path):
    path = tmp_path / "home/.claude/projects/-tmp-parity/session.jsonl"
    path.parent.mkdir(parents=True)
    baseline = ClaudeLogReader()
    first = json.dumps(assistant(1, [], "tool_use")).encode() + b"\n"
    second = json.dumps(assistant(2, [{"type": "text", "text": "completion"}], "end_turn")).encode()
    path.write_bytes(first + second[:70])
    with closing(NativePeer(tmp_path)) as peer:
        first_result = observe(peer, path, "usage", checkpoint={})
        first_expected = baseline.parse_incremental(path, {})
        assert first_result["checkpoint"] == first_expected.checkpoint
        seen = set()
        one = observe(peer, path, "activity", seen=[])
        assert one["events"] == [asdict(e) for e in baseline.parse_activity(path, seen)]
        path.write_bytes(first + second)  # Valid unterminated record: activity consumes it, usage does not.
        continued = observe(peer, path, "activity", seen=sorted(seen))
        assert continued["events"] == [asdict(e) for e in baseline.parse_activity(path, seen)]
        assert observe(peer, path, "usage", checkpoint=first_expected.checkpoint,
                       expected_anchor_b64=first_result["anchor_b64"])["events"] == []
        with path.open("ab") as stream:
            stream.write(b"\n")
        complete = observe(peer, path, "usage", checkpoint=first_expected.checkpoint,
                           expected_anchor_b64=first_result["anchor_b64"])
        expected = baseline.parse_incremental(path, first_expected.checkpoint)
        assert complete["events"] == [asdict(e) for e in expected.events]
        assert complete["checkpoint"] == expected.checkpoint
        # Same-inode/same-size rewrite must reset the usage cursor, but the
        # independently persisted activity high-water deliberately stays put.
        path.write_bytes(path.read_bytes().replace(b"message-1", b"message-X"))
        rewritten = observe(peer, path, "usage", checkpoint=expected.checkpoint,
                            expected_anchor_b64=complete["anchor_b64"])
        rewritten_expected = baseline.parse_incremental(path, expected.checkpoint)
        assert rewritten["events"] == [asdict(e) for e in rewritten_expected.events]
        assert rewritten["checkpoint"] == rewritten_expected.checkpoint
        assert observe(peer, path, "activity", seen=sorted(seen))["events"] == baseline.parse_activity(path, seen) == []
        path.rename(path.with_suffix(".old"))
        path.write_bytes(first)
        rotated = observe(peer, path, "usage", checkpoint=rewritten_expected.checkpoint,
                          expected_anchor_b64=rewritten["anchor_b64"])
        rotated_expected = baseline.parse_incremental(path, rewritten_expected.checkpoint)
        assert rotated["events"] == [asdict(e) for e in rotated_expected.events]
        assert rotated["checkpoint"] == rotated_expected.checkpoint


def test_native_large_observation_preserves_wire_bound(tmp_path):
    path = tmp_path / "home/.claude/projects/-tmp-parity/session.jsonl"
    path.parent.mkdir(parents=True)
    text = "café 世界" * 20000
    path.write_text(json.dumps(assistant(1, [{"type": "text", "text": text}], "end_turn")) + "\n")
    with closing(NativePeer(tmp_path)) as peer:
        result = observe(peer, path, "activity", seen=[])
        assert [e["text"] for e in result["events"] if e["event_type"] == "turn_complete"] == [text]


def test_native_exceptional_activity_returns_progress_without_swallowing_error(tmp_path):
    """Supplemental real-native boundary compared with inherited error/finally semantics."""
    path = tmp_path / "home/.claude/projects/-tmp-error/session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(assistant(1, [], "tool_use")) + "\nnull\n" +
                    json.dumps(assistant(2, [{"type": "text", "text": "After exceptional record"}], "end_turn")) + "\n")
    baseline = ClaudeLogReader()
    seen = {"unrelated-key"}
    with pytest.raises(AttributeError, match="has no attribute 'get'"):
        baseline.parse_activity(path, seen)
    assert seen == {"unrelated-key", "act_hw::2"}
    with closing(NativePeer(tmp_path)) as peer:
        request = peer.send("observe", {"vendor": "claude", "path": str(path), "mode": "activity", "seen": ["unrelated-key"]})
        while request not in peer.responses:
            peer.receive()
        failed = peer.responses.pop(request)
        assert not failed["ok"] and "not an object" in failed["error"]["message"]
        assert "observation_parts" in failed.get("result", {}), "exception lost its activity progress"
        parts = [row for row in peer.events if row.get("event") == "observation" and row["observation_id"] == request]
        progress = json.loads(b"".join(base64.b64decode(row["data_b64"]) for row in parts))
        assert set(progress["seen"]) == seen and progress["events"] == []
        assert progress["parser"] == "rust-claude-jsonl-v1" and progress["path"] == str(path)
        assert progress["ownership"]["session_id"] is None
        assert all(row["runtime_generation"] == peer.generation for row in parts)
        result = observe(peer, path, "activity", seen=sorted(seen))
        assert result["events"] == [asdict(event) for event in baseline.parse_activity(path, seen)]
        assert [event["text"] for event in result["events"] if event["event_type"] == "turn_complete"] == ["After exceptional record"]
        assert observe(peer, path, "activity", seen=result["seen"])["events"] == []
