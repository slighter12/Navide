"""What the native reader takes from the sidecar's answer to a log's replay and activity passes.

`parse_session_file` and `parse_activity` read the answer's `seen`, `state` and `events` and nothing else: never its
`checkpoint`. The parity tests of the sidecar (`observe/adapters/copilot.rs`) remove the nine resume fields of a log's
`checkpoint` from the answer before they compare it with the legacy observer's golden, after asserting each against the log;
this test is the fact that makes that removal safe: whatever the `checkpoint` of those two passes says, the reader's result is the
same.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors.copilot import CopilotLogReader

pytestmark = pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only")


def _answer(checkpoint) -> dict:
    return {"events": [], "seen": ["k1"], "state": {"water": 3}, "checkpoint": checkpoint, "identity": "1:2", "cwd": "/x"}


@pytest.mark.parametrize("pass_of", ["replay", "activity"])
def test_a_log_replay_and_activity_pass_never_read_the_answers_checkpoint(tmp_path, monkeypatch, pass_of):
    log = tmp_path / "copilot-home" / "session-state" / "log1" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{}\n")
    asked: list[dict] = []
    answers = iter([_answer({}), _answer({"offset": 99, "identity": "other", "kind": "x", "anchor": "zz"}), _answer("not even an object")])

    def observe_core(vendor, path, mode, **request):
        asked.append(request)
        return next(answers)

    monkeypatch.setattr(cli_runtime_native, "observe_core", observe_core)
    reader = cli_runtime_native.native_reader(CopilotLogReader, "copilot")()
    outcomes = []
    for _ in range(3):
        bag: set[str] = set()
        read = reader.parse_session_file(Path(log), bag) if pass_of == "replay" else reader.parse_activity(Path(log), bag)
        outcomes.append((read, sorted(bag)))
    assert len(asked) == 3 and outcomes[0] == outcomes[1] == outcomes[2], "the checkpoint of the answer changes nothing the reader returns"
    assert all("checkpoint" not in request or request["checkpoint"] is None for request in asked), "and none was sent"
