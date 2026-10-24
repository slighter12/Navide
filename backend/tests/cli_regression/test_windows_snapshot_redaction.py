"""The Windows snapshot leak check, on any host: a token-shaped value is found wherever it sits.

The credential below is fake. Each case puts it in one field of a snapshot and requires the check to see it.
"""
from __future__ import annotations

import pytest

from .test_launch_snapshots_windows import _redact_split_launch_token, token_shaped_values

FAKE = "aB3_dE6-gH9jK2mN5pQ8sT1vW4yZ7cF0xR3uL6oI9eA"


def _snapshot(**fields) -> dict:
    return {"child_argv": ["prog", "--flag"], "child_env": {"KEY": "value"}, "submitted_command": ["cmd.exe", "/k", "x"],
            **fields}


@pytest.mark.parametrize("snapshot", [
    _snapshot(child_argv=["prog", f"{FAKE}\\ --data-binary"]),
    _snapshot(child_argv=["prog", f"-H \\X-Other: {FAKE}"]),
    _snapshot(child_env={"KEY": FAKE}),
    _snapshot(child_env={FAKE: "value"}),
    _snapshot(submitted_command=f"prog --token {FAKE}"),
    _snapshot(submitted_command=["cmd.exe", "/k", FAKE]),
    _snapshot(refused={"code": "X", "message": f"bad {FAKE}"}),
], ids=["argv-split", "argv-inline", "env-value", "env-name", "command-string", "command-list", "refusal"])
def test_a_token_shaped_value_is_found_in_every_field(snapshot):
    assert token_shaped_values(snapshot) == [FAKE]


def test_the_redaction_sentinel_and_the_encoded_script_are_not_tokens():
    script = "A" * 300
    assert token_shaped_values(_snapshot(child_argv=["prog", "<token>\\ --x", script, "<ROOT>/data/hook-auth"])) == []


def test_a_token_split_from_its_header_is_redacted_then_clean():
    parts = ["-H", "\\X-Navide-Codex-Launch:", f"{FAKE}\\ --data-binary"]
    redacted = _redact_split_launch_token(parts)
    assert redacted[2] == "<token>\\ --data-binary"
    assert token_shaped_values(redacted) == []
