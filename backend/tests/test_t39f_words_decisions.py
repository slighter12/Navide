"""Every decision about a command reads its words first; a rewrite stays a `Command` (t39f M2, M3, M9)."""

from __future__ import annotations

import pytest

from agent_team_backend import push_delivery, webhook_service
from agent_team_backend.cli_launch import Command, command_tokens, read_literal, rewritten
from agent_team_backend.cli_vendors import registry
from agent_team_backend.cli_vendors._vendor_facts import FACTS
from agent_team_backend.cli_vendors.base import resume_id_from_command
from agent_team_backend.codex_session_hooks import _sets_hooks
from agent_team_backend.mcp_server.wiring import _already_wired


def words(*parts: dict) -> Command:
    return Command.from_words(list(parts), quote=lambda text: f"'{text}'" if " " in text else text)


# ---- M3: the words are read first, only a literal's text is parsed -----------------------------------------------------


def test_an_argument_with_a_space_is_one_token_as_it_stands() -> None:
    command = words({"exe": "claude"}, {"arg": "--flag with space"})
    assert command_tokens(command) == ["claude", "--flag with space"]


def test_an_option_reads_as_its_spelling_and_consecutive_literals_are_one_text() -> None:
    command = words({"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"}, {"lit": " --x"}, {"lit": "=1"})
    assert command_tokens(command) == ["claude", "--model", "m", "--x=1"]


def test_a_literal_is_read_once_by_one_routine() -> None:
    literal = read_literal("  'my tool' --a b")
    assert literal.tokens == ("my tool", "--a", "b")
    assert literal.head == (0, 11)  # the head spans the leading blanks and the whole quoted token


def test_a_flag_inside_one_argument_is_not_a_flag_the_command_holds() -> None:
    command = words({"exe": "codex"}, {"arg": "note --port"})
    assert not push_delivery._has_flag(command, "--port")
    assert push_delivery._has_flag(words({"exe": "codex"}, {"arg": "--port=1"}), "--port")
    assert not push_delivery._has_flag(Command.literal("codex --portable"), "--port")


def test_a_codex_hooks_override_is_found_in_words_and_in_text() -> None:
    assert _sets_hooks(words({"exe": "codex"}, {"arg": "-c"}, {"arg": "hooks.x=1"}))
    assert _sets_hooks(Command.literal("codex -chooks.x"))
    assert not _sets_hooks(words({"exe": "codex"}, {"arg": "-c hooks.x=1"}))  # one argument is not a `-c` flag
    assert not _sets_hooks(Command.literal("codex -c model=x"))


def test_a_wired_marker_in_an_argument_is_found_and_a_list_command_reads_its_line() -> None:
    wired = words({"exe": "claude"}, {"arg": "--mcp-config"}, {"arg": '{"navide": 1}'})
    assert _already_wired(wired, '"navide"')
    assert _already_wired(["bash", "-lc", wired], "--mcp-config")
    assert not _already_wired(words({"exe": "claude"}), "--mcp-config")


# ---- M2: a rewrite keeps the words ------------------------------------------------------------------------------------


def test_a_rewrite_stays_a_command_and_keeps_the_words_that_still_open_the_line() -> None:
    command = words({"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"})
    result = rewritten(command, lambda text: text + " --extra")
    assert isinstance(result, Command)
    assert str(result) == "claude --model m --extra"
    assert result.words[:2] == command.words
    assert result.words[2] == {"lit": " --extra"}


def test_a_rewrite_that_changes_the_start_keeps_only_what_still_agrees() -> None:
    command = words({"exe": "claude"}, {"opt": "model", "value": "m", "text": " --model m"})
    result = rewritten(command, lambda text: text.replace("claude", "claude --bare", 1))
    assert isinstance(result, Command) and str(result) == "claude --bare --model m"
    assert all("opt" not in word for word in result.words)  # the option no longer sits where it was


def test_a_wrapped_command_changes_only_its_last_element() -> None:
    inner = words({"exe": "claude"}, {"opt": "skip_permissions", "text": " --dangerously-skip-permissions"})
    result = rewritten(["bash", "-lc", inner], lambda text: text.replace(" --dangerously-skip-permissions", ""))
    assert result[:2] == ["bash", "-lc"] and isinstance(result[2], Command) and str(result[2]) == "claude"


def test_a_plain_string_rewrite_becomes_a_command() -> None:
    assert isinstance(rewritten("claude -x", str.upper), Command)
    assert isinstance(rewritten(None, lambda text: text + "claude"), Command)


def test_the_webhook_rewrite_goes_through_the_words_api() -> None:
    command = words({"exe": "claude"}, {"opt": "skip_permissions", "text": " " + FACTS["claude"].permission_flag})
    result = webhook_service._dropped_bypass(command)
    assert isinstance(result, Command)
    assert str(result) == "claude"
    forced = webhook_service._rewrite("claude", lambda text: text + " x")
    assert isinstance(forced, Command) and str(forced) == "claude x"


# ---- M3: the webhook's bypass spellings come from the declarations -----------------------------------------------------


@pytest.mark.parametrize("key", ["claude", "codex"])
def test_the_dropped_mode_flags_are_the_declarations(key: str) -> None:
    declared = FACTS[key]
    assert webhook_service._BYPASS[key] == declared.permission_flag
    line = f"{declared.command} {declared.permission_flag} --x"
    assert webhook_service._with_mode(key, line, unattended=False).count(declared.permission_flag) == 0


@pytest.mark.parametrize("key", sorted(webhook_service.VENDORS))
def test_every_declared_bypass_spelling_of_a_webhook_vendor_is_recognised(key: str) -> None:
    flag = FACTS[key].permission_flag
    assert webhook_service._BYPASS_FLAG.search(f"{FACTS[key].command} {flag} x") is not None
    for spelling in FACTS[key].permission_aliases.spellings:
        assert webhook_service._BYPASS_FLAG.search(f"{FACTS[key].command} {spelling} x") is not None


def test_a_vendor_a_webhook_run_may_not_use_adds_no_spelling_to_the_bypass_pattern() -> None:
    """The baseline pattern named Claude's spellings only; the declarations feed it the same set."""
    for key in set(registry.VENDORS) - webhook_service.VENDORS:
        flag = FACTS[key].permission_flag
        if flag and flag not in {FACTS["claude"].permission_flag, *FACTS["claude"].permission_aliases.spellings}:
            assert webhook_service._BYPASS_FLAG.search(f"{FACTS[key].command} {flag} x") is None


# ---- M9: the plain-only rule holds on the words path, and a bare `--` ends the flag search -----------------------------


def plain_vendors() -> list[str]:
    return ["antigravity", "grok"]


@pytest.mark.parametrize("key", plain_vendors())
def test_a_plain_only_vendor_reads_no_resume_from_a_literal_with_shell_syntax(key: str) -> None:
    declared = FACTS[key]
    template = declared.resume_args
    assert template is not None
    spelled = template.replace("{id}", "abc")
    plain = Command.literal(f"{declared.command} {spelled}")
    assert resume_id_from_command(plain, declared, plain_only=True) == "abc"
    chained = Command.literal(f"{declared.command} {spelled} && echo hi")
    assert resume_id_from_command(chained, declared, plain_only=True) == ""


@pytest.mark.parametrize("key", plain_vendors())
def test_a_resume_option_word_beside_a_shell_literal_is_still_no_resume_for_a_plain_only_vendor(key: str) -> None:
    declared = FACTS[key]
    command = Command.from_words(
        [{"exe": declared.command}, {"opt": "resume", "value": "abc", "text": " " + declared.resume_args.replace("{id}", "abc")},
         {"lit": " ; echo hi"}]
    )
    assert resume_id_from_command(command, declared, plain_only=True) == ""
    assert resume_id_from_command(command, declared, plain_only=False) == "abc"


def test_a_resume_option_word_alone_is_the_id() -> None:
    declared = FACTS["claude"]
    command = Command.from_words(
        [{"exe": "claude"}, {"opt": "resume", "value": "abc", "text": " " + declared.resume_args.replace("{id}", "abc")}]
    )
    assert resume_id_from_command(command, declared) == "abc"


def test_a_bare_double_dash_ends_the_flag_search() -> None:
    declared = FACTS["claude"]
    flag = declared.resume_args.split()[0]
    assert resume_id_from_command(Command.literal(f"claude {flag} abc"), declared) == "abc"
    assert resume_id_from_command(Command.literal(f"claude -- {flag} abc"), declared) == ""
    assert resume_id_from_command(Command.literal(f"claude {flag}=abc"), declared) == "abc"
