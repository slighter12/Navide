"""The open turn's reply text is part of the pass budget, over the real sidecar (t36g R1).

Codex and Copilot `events.jsonl` keep a reply's text in the core's `last_text` from the line that carries it until the turn-end
line delivers it. That text travels in the answer's `state` and in the next request's `state`, so it is weighed with the pass's
events: a pass fits `PASS_EVENT_BYTES`, and a reply too long to carry between passes is cut (head and tail kept) and reported in
`truncated`, so the log always advances. A turn-end line whose events would cross the budget waits for the next pass.

Every log is driven the way the reader drives it: ask, keep the answer's `state`, ask again until a pass at the end of the log
delivers nothing. A missing sidecar binary fails these tests, it never skips them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.native_reader_peer import wire
from tests.vendors.test_copilot import _SID, _assistant, _event, _user

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(240)]

MIB = 1024 * 1024
LINE = 100_000
#: The open reply the core carries between passes is bounded by what a request can carry (3 MiB of the 4 MiB meta cap).
OPEN_REPLY = 3 * MIB

#: How each vendor writes the records a turn is made of. Codex: `codex.rs` `activity` (`event_msg` `user_message`/`agent_message`/
#: `task_complete`); the shapes are the ones `test_cli_runtime_observe.py::test_s8_codex_usage_activity_and_raw_anchor_resume` writes.
#: Copilot: `tests/vendors/test_copilot.py` `_user`/`_assistant`/`_event`.
KINDS = {
    "codex": {
        "path": lambda root: root / "rollout-2026-01-01T00-00-00-abc.jsonl",
        "request": {},
        "user": lambda text: {"type": "event_msg", "payload": {"type": "user_message", "message": text}},
        "reply": lambda text: {"type": "event_msg", "payload": {"type": "agent_message", "message": text}},
        "tool": lambda: {"type": "event_msg", "payload": {"type": "exec_command_begin"}},
        "end": lambda: {"type": "event_msg", "payload": {"type": "task_complete"}},
    },
    "copilot": {
        "path": lambda root: root / "session-state" / _SID / "events.jsonl",
        "request": {"cwd": "/w", "session": _SID},
        "user": lambda text: _user(text),
        "reply": lambda text: _assistant(text),
        "tool": lambda: _event("tool.execution_start", {}),
        "end": lambda: _event("assistant.turn_end", {}),
    },
}


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path)
    try:
        yield sidecar
    finally:
        sidecar.close()


@pytest.fixture(params=sorted(KINDS))
def kind(request, tmp_path):
    vendor = request.param
    shapes = KINDS[vendor]
    path = shapes["path"](tmp_path / "logs")
    path.parent.mkdir(parents=True)
    return vendor, shapes, path


def _append(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write("".join(json.dumps(record) + "\n" for record in records))


def _drive(sidecar, vendor: str, shapes: dict, path: Path, state: dict | None = None, limit: int = 40) -> tuple[list[dict], dict]:
    """Every pass the reader needs to read `path` dry from `state`: the answers, ending with the empty pass at the end of the log."""
    state, answers = state or {}, []
    for _ in range(limit):
        answer = sidecar.observe_core(vendor, path, "activity", seen=[], state=state, **shapes["request"])
        answers.append(answer)
        state = answer["state"]
        if not answer["events"] and (state.get("position") or {}).get("offset") == path.stat().st_size:
            return answers, state
    raise AssertionError(f"not drained in {limit} passes")


def _events(answers: list[dict], event_type: str) -> list[dict]:
    return [event for answer in answers for event in answer["events"] if event["event_type"] == event_type]


def _cuts(answers: list[dict]) -> list[dict]:
    return [cut for answer in answers for cut in answer.get("truncated", [])]


def test_a_nine_mib_reply_whose_turn_is_still_open_is_answered_and_the_text_is_kept_cut_in_the_state(kind, sidecar):
    vendor, shapes, path = kind
    reply = "HEAD" + "r" * (9 * MIB) + "TAIL"
    _append(path, [shapes["user"]("go"), shapes["reply"](reply)])
    answers, state = _drive(sidecar, vendor, shapes, path)
    held = state["last_text"]
    assert held.startswith("HEAD") and held.endswith("TAIL"), "the head and the tail of the open reply survive"
    assert 0 < len(held) <= OPEN_REPLY, "and the middle is cut so the next request can carry it"
    assert [cut["table"] for cut in _cuts(answers)] == ["last_text"], "the cut is reported once, as the open turn's text"
    assert _events(answers, "turn_complete") == [], "the turn has not ended"


def test_the_same_reply_with_its_turn_end_in_a_later_pass_after_other_events_is_delivered_with_head_and_tail_and_the_log_ends_empty(kind, sidecar):
    vendor, shapes, path = kind
    reply = "HEAD" + "r" * (9 * MIB) + "TAIL"
    _append(path, [shapes["user"]("go"), shapes["reply"](reply), shapes["tool"](), shapes["tool"](), shapes["end"]()])
    answers, state = _drive(sidecar, vendor, shapes, path)
    (turn,) = _events(answers, "turn_complete")
    assert turn["text"].startswith("HEAD") and turn["text"].endswith("TAIL"), "the head and the tail survive the cut"
    assert 0 < len(turn["text"]) < 6 * MIB, "and the reply fits the pass that delivers it"
    assert state["last_text"] == "", "the turn spent its text"
    assert len(_cuts(answers)) >= 1
    assert answers[-1]["events"] == [], "and the next pass is empty"


def test_a_two_and_a_half_mib_open_reply_in_the_middle_of_a_six_mib_stream_of_events_never_makes_a_pass_over_the_cap(kind, sidecar):
    """35 lines of 100 KB, the 2.5 MiB reply, 28 more lines of 100 KB, the turn end. Counting only events, the pass would carry
    6.3 MB of them beside the 2.6 MB of open text (8.9 MB, over the 8,355,840-byte cap)."""
    vendor, shapes, path = kind
    reply = "HEAD" + "r" * (5 * MIB // 2) + "TAIL"
    prompt = "p" * LINE
    _append(path, [shapes["user"](prompt)] * 35 + [shapes["reply"](reply)] + [shapes["user"](prompt)] * 28 + [shapes["end"]()])
    answers, _ = _drive(sidecar, vendor, shapes, path)
    prompts = [event for event in _events(answers, "agent_active") if event["text"] == prompt]
    assert len(prompts) == 63, "every prompt line is delivered once"
    (turn,) = _events(answers, "turn_complete")
    assert turn["text"] == reply, "a reply that fits is delivered whole"
    assert _cuts(answers) == [], "nothing was cut"
    assert len(answers) >= 3, "one pass cannot carry the log beside the open reply"


def test_a_short_turn_end_line_that_releases_saved_text_after_earlier_events_waits_for_a_pass_it_fits(kind, sidecar):
    """The first pass leaves a 2.9 MiB reply open. The next stream holds 55 lines of 100 KB and then the short turn-end line: its
    event is as long as the saved text, and the pass already holds 5.5 MB of events (8.4 MB together, over the cap)."""
    vendor, shapes, path = kind
    reply = "HEAD" + "r" * (29 * MIB // 10) + "TAIL"
    _append(path, [shapes["reply"](reply)])
    first, state = _drive(sidecar, vendor, shapes, path)
    assert state["last_text"] == reply and _cuts(first) == []
    prompt = "p" * LINE
    _append(path, [shapes["user"](prompt)] * 55 + [shapes["end"]()])
    answers, state = _drive(sidecar, vendor, shapes, path, state)
    prompts = [event for event in _events(answers, "agent_active") if event["text"] == prompt]
    assert len(prompts) == 55, "every prompt line is delivered once"
    (turn,) = _events(answers, "turn_complete")
    assert turn["text"] == reply and state["last_text"] == ""
