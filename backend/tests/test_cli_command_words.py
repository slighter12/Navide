"""t39 (L1): a command carries the words it was composed from, so nothing parses a command line back.

The renderer, the injectors and the rewriters hand the backend a `Command`; `build_launch` reads its words.
Expected values are written out, never derived from the code under test.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from agent_team_backend import cli_launch
from agent_team_backend.cli_launch import Command

DATA = Path(__file__).parent / "data"
SHELL = "/bin/bash"


def test_the_posix_quote_of_an_appended_argument_matches_the_shared_vectors():
    for vector in json.loads((DATA / "g2_quote_vectors.json").read_text(encoding="utf-8"))["vectors"]:
        text = Command.literal("x").appended(vector["input"])
        assert text == "x " + vector["quoted"], vector["input"]
        assert shlex.split(text) == ["x", vector["input"]], vector["input"]


def test_a_literal_command_is_one_literal_word_and_reads_as_its_own_text():
    command = Command.literal("claude --keep 'a b'")
    assert command == "claude --keep 'a b'"
    assert command.words == ({"lit": "claude --keep 'a b'"},)


def test_the_text_is_derived_from_the_words_and_an_opt_carries_its_own_spelling():
    command = Command.from_words([
        {"exe": "/x y/claude"}, {"opt": "model", "value": "m1", "text": " --model m1"},
        {"opt": "skip_permissions", "text": " --yolo"}, {"arg": "a b"}, {"lit": " --raw"}])
    assert command == "'/x y/claude' --model m1 --yolo 'a b' --raw"
    assert [next(iter(word)) for word in command.words] == ["exe", "opt", "opt", "arg", "lit"]


def test_the_wire_words_drop_the_spelling_the_text_view_needed():
    command = Command.from_words([{"exe": "claude"}, {"opt": "model", "value": "m1", "text": " --model m1"}])
    assert command.wire_words() == [{"exe": "claude"}, {"opt": "model", "value": "m1"}]


@pytest.mark.parametrize("word", [
    {}, {"lit": 1}, {"lit": "a", "arg": "b"}, {"opt": "model"}, {"opt": "model", "text": 3},
    {"opt": "model", "text": "--model m", "value": 4}, {"exe": ["x"]}, {"nope": "x"}, "claude",
], ids=["empty", "non-text-lit", "two-kinds", "opt-without-spelling", "opt-text-type", "opt-value-type",
        "exe-type", "unknown-kind", "not-an-object"])
def test_a_word_that_is_not_one_of_the_four_kinds_is_refused(word):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        Command.from_words([word])
    assert (refused.value.code, refused.value.stage) == ("COMPOSITION_UNREPRESENTABLE", "words")


def test_appending_arguments_adds_arg_words_and_keeps_every_earlier_word():
    base = Command.from_words([{"lit": "claude"}, {"opt": "skip_permissions", "text": " --yolo"}])
    final = cli_launch.append_args(base, "--mcp-config", "/d/p a.json")
    assert final == "claude --yolo --mcp-config '/d/p a.json'"
    assert final.words == ({"lit": "claude"}, {"opt": "skip_permissions", "text": " --yolo"},
                           {"arg": "--mcp-config"}, {"arg": "/d/p a.json"})
    assert base == "claude --yolo"


def test_appending_to_plain_text_makes_the_text_one_literal_first():
    final = cli_launch.append_args("claude --old 'x y'", "--new")
    assert final.words == ({"lit": "claude --old 'x y'"}, {"arg": "--new"})


def test_appending_to_a_wrapped_list_changes_only_its_last_element():
    final = cli_launch.append_args([SHELL, "-ilc", Command.literal("claude")], "--x", "y z")
    assert final == [SHELL, "-ilc", "claude --x 'y z'"]
    assert isinstance(final[2], Command) and final[:2] == [SHELL, "-ilc"]


def test_a_wrapped_command_becomes_a_wrapped_launch_with_its_words():
    command = [SHELL, "-lc", cli_launch.append_args(Command.from_words([{"lit": "claude"}]), "--mcp-config", "/d/p.json")]
    assert cli_launch.build_launch(command) == {
        "shape": "wrapped", "shell": SHELL, "flags": ["-lc"],
        "words": [{"lit": "claude"}, {"arg": "--mcp-config"}, {"arg": "/d/p.json"}]}


def test_plain_text_is_one_literal_and_is_never_split():
    assert cli_launch.build_launch([SHELL, "-ilc", "claude --model m1 --resume x"]) == {
        "shape": "wrapped", "shell": SHELL, "flags": ["-ilc"], "words": [{"lit": "claude --model m1 --resume x"}]}
    assert cli_launch.build_launch("ls -l") == {"shape": "direct", "form": "string", "words": [{"lit": "ls -l"}]}


def test_declared_option_words_reach_the_launch_where_the_composer_put_them():
    words = [{"exe": "claude"}, {"opt": "model", "value": "m1", "text": " --model m1"},
             {"opt": "effort", "value": "high", "text": " --effort high"}, {"lit": " --keep 'a b'"}]
    launch = cli_launch.build_launch([SHELL, "-ilc", Command.from_words(words)])
    assert launch["words"] == [{"exe": "claude"}, {"opt": "model", "value": "m1"},
                               {"opt": "effort", "value": "high"}, {"lit": " --keep 'a b'"}]


def test_a_direct_list_composes_its_last_element_from_the_words():
    command = ["pwsh.exe", "-NoLogo", "-Command", cli_launch.append_args(Command.literal("& 'C:\\x\\prog.cmd'"), "a b")]
    assert cli_launch.build_launch(command) == {
        "shape": "direct", "form": "list", "elements": ["pwsh.exe", "-NoLogo", "-Command", ""],
        "words": [{"lit": "& 'C:\\x\\prog.cmd'"}, {"arg": "a b"}]}


@pytest.mark.parametrize("command", [7, None, ["claude", 3], {"cmd": "claude"}, []])
def test_a_command_of_an_unsupported_shape_is_a_refusal_not_a_type_error(command):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.build_launch(command)
    assert refused.value.code == "COMPOSITION_UNREPRESENTABLE"


def test_a_credential_plan_wraps_the_inner_launch():
    plan = {"helper": ["h", "--x"], "unset": ["A"], "entrypoint": None}
    launch = cli_launch.build_launch([SHELL, "-lc", Command.literal("kilo")], credential=plan)
    assert launch == {"shape": "credential", "helper": ["h", "--x"], "unset": ["A"],
                      "inner": {"shape": "wrapped", "shell": SHELL, "flags": ["-lc"], "words": [{"lit": "kilo"}]}}


def test_a_payload_without_words_leaves_the_command_as_text():
    command = [SHELL, "-lc", "claude"]
    assert cli_launch.attach_words(command, None) is command


def test_a_payload_with_words_that_agree_with_its_text_carries_them():
    words = [{"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"}]
    attached = cli_launch.attach_words([SHELL, "-ilc", "claude --model m"], words, "claude")
    assert attached == [SHELL, "-ilc", "claude --model m"]
    assert attached[2].words == ({"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"})
    assert cli_launch.attach_words("claude --model m", words, "claude").words == attached[2].words


@pytest.mark.parametrize("command", ["claude --model other", [SHELL, "-lc", "claude --model other"]])
def test_words_that_disagree_with_the_command_text_are_refused_before_create(command):
    words = [{"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"}]
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.attach_words(command, words, "claude")
    assert (refused.value.code, refused.value.stage) == ("COMPOSITION_UNREPRESENTABLE", "words")


FORGED = [{"lit": "claude"}, {"opt": "model", "value": "other", "text": " --model safe"}]


def test_an_opt_whose_text_is_not_its_declared_spelling_is_refused_though_the_command_agrees_with_the_text():
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.attach_words("claude --model safe", FORGED, "claude")
    assert (refused.value.code, refused.value.stage) == ("COMPOSITION_UNREPRESENTABLE", "words")


@pytest.mark.parametrize(
    ("agent_key", "word"),
    [
        ("claude", {"opt": "effort", "value": "bogus", "text": " --effort bogus"}),  # not a declared effort
        ("codex", {"opt": "effort", "value": "high", "text": " --effort high"}),  # not integrated
        ("claude", {"opt": "model", "text": " --model"}),  # a value is required
        ("", {"opt": "model", "value": "m", "text": " --model m"}),  # a subject that declares no options
        ("claude", {"opt": "nonsense", "value": "m", "text": " --x m"}),
    ],
)
def test_an_opt_the_sidecar_would_refuse_is_refused_before_create(agent_key, word):
    text = f"claude{word['text']}"
    with pytest.raises(cli_launch.LaunchRefused):
        cli_launch.attach_words(text, [{"lit": "claude"}, word], agent_key)


def test_every_option_is_checked_against_the_spelling_the_sidecar_renders():
    words = [
        {"exe": "claude"},
        {"opt": "skip_permissions", "text": " --dangerously-skip-permissions"},
        {"opt": "model", "value": "m", "text": " --model m"},
        {"opt": "effort", "value": "high", "text": " --effort high"},
        {"opt": "resume", "value": "abc", "text": " --resume abc"},
        {"opt": "session_id", "value": "abc", "text": " --session-id abc"},
    ]
    text = "claude --dangerously-skip-permissions --model m --effort high --resume abc --session-id abc"
    assert cli_launch.attach_words(text, words, "claude") == text


def test_the_create_handler_refuses_a_forged_option_text_before_anything_else(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from agent_team_backend import ws_handlers

    refused: list = []
    session = SimpleNamespace(terminals=SimpleNamespace(
        receipt_refused_create=lambda pane, why: refused.append((pane, why.code))))
    payload = {"agent_key": "claude", "pane_id": "p1", "command": "claude --model safe", "words": FORGED}
    with pytest.raises(cli_launch.LaunchRefused):
        asyncio.run(ws_handlers._terminal_create_impl(session, "m1", "terminal.create", payload, {}, "g"))
    assert refused == [("p1", "COMPOSITION_UNREPRESENTABLE")]
    assert payload["command"] == "claude --model safe"


def test_words_that_are_not_a_list_are_refused():
    with pytest.raises(cli_launch.LaunchRefused):
        cli_launch.attach_words("claude", {"lit": "claude"})


def test_the_executable_can_be_replaced_without_losing_the_words_after_it():
    command = Command.from_words([{"exe": "claude"}, {"opt": "skip_permissions", "text": " --yolo"}, {"arg": "x"}])
    replaced = cli_launch.with_executable(command, "/opt/c l/claude")
    assert replaced == "'/opt/c l/claude' --yolo x"
    assert replaced.words[1:] == command.words[1:]


def test_a_literal_head_is_replaced_in_the_text_and_stays_one_literal():
    replaced = cli_launch.with_executable(Command.literal("claude --keep"), "/opt/claude")
    assert replaced == "/opt/claude --keep"
    assert replaced.words == ({"lit": "/opt/claude --keep"},)
