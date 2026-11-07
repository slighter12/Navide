"""G2 ticket 04: the pre-dispatch checks of the structured launch Python hands a Rust-composed subject.

The words themselves are covered in `test_cli_command_words.py`. The POSIX quote and split rules are checked
against the vector files Rust reads too: Python quotes the arguments an injector appends (`shlex.quote`) and
the sidecar splits them back, so the two must agree on every vector.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from agent_team_backend import cli_launch

DATA = Path(__file__).parent / "data"


def test_shlex_quote_and_split_agree_with_the_shared_vectors():
    for vector in json.loads((DATA / "g2_quote_vectors.json").read_text(encoding="utf-8"))["vectors"]:
        assert shlex.quote(vector["input"]) == vector["quoted"], vector["input"]
    for vector in json.loads((DATA / "g2_split_vectors.json").read_text(encoding="utf-8"))["vectors"]:
        if vector.get("error"):
            with pytest.raises(ValueError):
                shlex.split(vector["input"])
        else:
            assert shlex.split(vector["input"]) == vector["words"], vector["input"]


@pytest.mark.parametrize("value", ["a\x00b", "lone\udc80surrogate"])
def test_text_the_wire_cannot_carry_is_refused_before_dispatch(value):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.check_text({"words": [{"lit": value}]})
    assert (refused.value.code, refused.value.stage) == ("CREATE_INVALID_TEXT", "pre-dispatch")


def test_ordinary_text_is_accepted():
    cli_launch.check_text({"words": [{"lit": "café 世界"}]}, "/work", {"A": "b"})
