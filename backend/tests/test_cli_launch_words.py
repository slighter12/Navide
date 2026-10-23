"""G2 ticket 04: the structured launch Python builds for a Rust-composed subject (delta 2.3).

The expected values are written out, never derived from the code under test. The quote and split rules
are checked against the vector files Rust uses too.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from agent_team_backend import cli_launch

DATA = Path(__file__).parent / "data"
SHELL = "/bin/bash"
RESUME = "22222222-2222-4222-8222-222222222222"


def _wrapped(text: str) -> list[str]:
    return [SHELL, "-lc", text]


def test_quote_matches_the_shared_shlex_quote_vectors():
    for vector in json.loads((DATA / "g2_quote_vectors.json").read_text(encoding="utf-8"))["vectors"]:
        assert cli_launch.quote(vector["input"]) == vector["quoted"], vector["input"]
        assert shlex.quote(vector["input"]) == vector["quoted"], vector["input"]


def test_scan_matches_the_shared_shlex_split_vectors_and_reports_spans():
    for vector in json.loads((DATA / "g2_split_vectors.json").read_text(encoding="utf-8"))["vectors"]:
        text = vector["input"]
        if vector.get("error"):
            with pytest.raises(ValueError):
                cli_launch.scan(text)
            with pytest.raises(ValueError):
                shlex.split(text)
            continue
        assert shlex.split(text) == vector["words"], text
        scanned = cli_launch.scan(text)
        assert [value for _, _, value in scanned] == vector["words"], text
        for start, end, value in scanned:
            assert shlex.split(text[start:end]) == [value], text


def test_a_plain_submitted_command_is_one_literal_and_the_injectors_become_arg_words():
    base = _wrapped("claude")
    final = _wrapped("claude --mcp-config /d/p.json --settings '/d/s a.json'")
    assert cli_launch.build_launch("claude", base, final) == {
        "shape": "wrapped", "shell": SHELL, "flags": ["-lc"],
        "words": [{"lit": "claude"}, {"arg": "--mcp-config"}, {"arg": "/d/p.json"},
                  {"arg": "--settings"}, {"arg": "/d/s a.json"}]}


def test_declared_options_become_opt_words_in_place():
    text = f"claude --model m1 --effort high --resume {RESUME} --keep 'a b'"
    launch = cli_launch.build_launch("claude", _wrapped(text), _wrapped(text))
    assert launch["words"] == [
        {"lit": "claude"}, {"opt": "model", "value": "m1"}, {"opt": "effort", "value": "high"},
        {"opt": "resume", "value": RESUME}, {"lit": " --keep 'a b'"}]


def test_a_handwritten_effort_outside_the_declared_set_stays_literal():
    text = "claude --effort extreme"
    assert cli_launch.build_launch("claude", _wrapped(text), _wrapped(text))["words"] == [{"lit": text}]


def test_a_repeated_flag_stays_literal_in_every_occurrence():
    text = "claude --model first --model second"
    assert cli_launch.build_launch("claude", _wrapped(text), _wrapped(text))["words"] == [{"lit": text}]


def test_the_valueless_permission_flag_is_extracted_once_and_only_once():
    once = "claude --dangerously-skip-permissions"
    assert cli_launch.build_launch("claude", _wrapped(once), _wrapped(once))["words"] == [
        {"lit": "claude"}, {"opt": "skip_permissions"}]
    twice = "claude --dangerously-skip-permissions --dangerously-skip-permissions"
    assert cli_launch.build_launch("claude", _wrapped(twice), _wrapped(twice))["words"] == [{"lit": twice}]


def test_a_value_that_would_change_meaning_when_requoted_stays_literal():
    for text in ("claude --model=fixture", "claude --model \"$HOME\"", "claude --model\tx", "claude --model -x"):
        assert cli_launch.build_launch("claude", _wrapped(text), _wrapped(text))["words"] == [{"lit": text}], text


def test_a_subject_without_declared_options_has_only_literals():
    text = "ls --model x"
    assert cli_launch.build_launch("terminal", _wrapped(text), _wrapped(text))["words"] == [{"lit": text}]


def test_login_becomes_the_login_opt_after_the_executable():
    launch = cli_launch.build_launch("claude", ["/bin/zsh", "-ilc", "'/x y/claude' auth login"],
                                     ["/bin/zsh", "-ilc", "'/x y/claude' auth login"], login=True)
    assert launch["words"] == [{"lit": "'/x y/claude'"}, {"opt": "login"}]
    assert launch["flags"] == ["-ilc"]


def test_a_direct_string_has_no_shell_and_a_direct_list_appends_to_its_last_element():
    assert cli_launch.build_launch("terminal", "ls -l", "ls -l") == {
        "shape": "direct", "form": "string", "words": [{"lit": "ls -l"}]}
    assert cli_launch.build_launch("claude", ["claude"], ["claude --x"]) == {
        "shape": "direct", "form": "list", "elements": ["claude"], "words": [{"arg": "--x"}]}


@pytest.mark.parametrize("final", [
    _wrapped("claude --x; rm -rf ."),              # an appended text with shell syntax
    _wrapped("claude --x \"$HOME\""),               # an appended text the shell would expand
    _wrapped("claude --x 'unterminated"),           # an appended text that does not split
    _wrapped("CLAUDE"),                             # the submitted prefix was edited
    [SHELL, "-ilc", "claude"],                      # the shell flags changed
    "claude",                                       # the command changed shape
], ids=["syntax", "expansion", "unsplittable", "prefix-edited", "flags-changed", "shape-changed"])
def test_an_injector_result_that_is_not_the_base_plus_plain_words_is_refused_before_create(final):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.build_launch("claude", _wrapped("claude"), final)
    assert refused.value.code == "COMPOSITION_UNREPRESENTABLE"


@pytest.mark.parametrize("value", ["a\x00b", "lone\udc80surrogate"])
def test_text_the_wire_cannot_carry_is_refused_before_dispatch(value):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.check_text({"words": [{"lit": value}]})
    assert (refused.value.code, refused.value.stage) == ("CREATE_INVALID_TEXT", "pre-dispatch")


def test_ordinary_text_is_accepted():
    cli_launch.check_text({"words": [{"lit": "café 世界"}]}, "/work", {"A": "b"})


@pytest.mark.parametrize("final", [7, None, ["claude", 3], {"cmd": "claude"}])
def test_a_final_command_of_an_unsupported_type_is_a_refusal_not_a_type_error(final):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.build_launch("claude", _wrapped("claude"), final)
    assert refused.value.code == "COMPOSITION_UNREPRESENTABLE"
