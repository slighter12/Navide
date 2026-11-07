"""t39 (L1): the injectors and rewriters keep a command's words, so the launch is built from structure and never
from a command line read back.

Each test hands an injector a `Command` and reads the words of what comes back.
"""
from __future__ import annotations

from pathlib import Path

from agent_team_backend import app, cli_launch, claude_hooks, codex_session_hooks, push_delivery
from agent_team_backend.cli_launch import Command

SHELL = "/bin/zsh"


def test_claude_settings_are_appended_as_two_arg_words_after_the_existing_words():
    base = Command.from_words([{"exe": "claude"}, {"opt": "skip_permissions", "text": " --dangerously-skip-permissions"}])
    final = claude_hooks.append_settings([SHELL, "-ilc", base], Path("/p a/s.json"))
    assert final[:2] == [SHELL, "-ilc"]
    assert final[2] == "claude --dangerously-skip-permissions --settings '/p a/s.json'"
    assert final[2].words == (*base.words, {"arg": "--settings"}, {"arg": "/p a/s.json"})


def test_every_rewrite_the_create_handler_runs_hands_back_a_command_that_keeps_its_words(monkeypatch):
    """t39f M2: a plain `str` reaching CREATE would lose the words, and the sidecar would then render the text. Each
    rewrite goes through the words API, so what it returns is a `Command` whose words still hold the options."""
    from agent_team_backend import onboarding_deps, webhook_service

    flag = "--dangerously-skip-permissions"
    base = Command.from_words([
        {"exe": "claude"},
        {"opt": "resume", "value": "abc", "text": " --resume abc"},
        {"opt": "skip_permissions", "text": f" {flag}"},
    ])
    wrapped = [SHELL, "-ilc", base]

    monkeypatch.setattr(onboarding_deps, "cli_binary_override", lambda key: "/opt/c l/claude")
    rebound = app._command_with_persisted_cli_binary("claude", wrapped)
    assert isinstance(rebound[2], Command) and rebound[2].words[1:] == base.words[1:]
    assert rebound[2] == f"'/opt/c l/claude' --resume abc {flag}"

    dropped = webhook_service._dropped_bypass(wrapped)
    assert isinstance(dropped[2], Command) and dropped[2] == "claude --resume abc"
    assert dropped[2].words[:2] == base.words[:2]  # the program and the resume option survive the rewrite

    held = webhook_service.without_bypass("claude", wrapped)
    assert isinstance(held[2], Command) and held[2].words[:2] == base.words[:2]

    moded = webhook_service._rewrite(wrapped, lambda text: webhook_service._with_mode("claude", text, unattended=False))
    assert isinstance(moded[2], Command) and "--permission-mode default" in moded[2]

    assert isinstance(webhook_service._rewrite("claude", str.upper), Command)
    assert isinstance(webhook_service._rewrite(None, lambda text: text), Command)


def test_the_codex_session_hooks_are_two_config_arg_pairs(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_session_hooks, "trust_gate_blocks_injection", lambda: False)
    base = Command.literal("codex resume abc")
    final = codex_session_hooks.wire(base, {}, {}, tmp_path, tmp_path / "port", tmp_path / "auth")
    appended = final.words[1:]
    assert [next(iter(word)) for word in appended] == ["arg"] * 4
    assert [word["arg"] for word in appended[::2]] == ["-c", "-c"]
    assert appended[1]["arg"].startswith("hooks.SessionStart=") and appended[3]["arg"].startswith("hooks.PreToolUse=")
    assert final.words[0] == {"lit": "codex resume abc"}


def test_the_push_channel_flags_are_arg_words(monkeypatch):
    monkeypatch.setattr(push_delivery, "_free_port", lambda host: 4321)
    monkeypatch.setattr(push_delivery, "_panes", {})
    command, state = push_delivery.wire_spawn("opencode", Command.literal("opencode"), "pane-words", {})
    assert state is not None and state.port == 4321
    assert [word.get("arg") for word in command.words[1:]] == ["--port", "4321", "--hostname", "127.0.0.1"]


def test_the_login_rewrite_keeps_the_program_word_and_adds_one_login_opt():
    base = [SHELL, "-ilc", Command.from_words([{"exe": "/x y/claude"}, {"opt": "model", "value": "m", "text": " --model m"}])]
    final = app._login_spawn_command("claude", base)
    assert final == [SHELL, "-ilc", "'/x y/claude' auth login"]
    assert final[2].words == ({"exe": "/x y/claude"}, {"opt": "login", "text": " auth login"})
    assert cli_launch.build_launch(final)["words"] == [{"exe": "/x y/claude"}, {"opt": "login"}]


def test_a_literal_login_command_keeps_its_first_token_as_the_literal_head():
    final = app._login_spawn_command("claude", [SHELL, "-ilc", "'/x y/claude' --yolo"])
    assert final[2].words == ({"lit": "'/x y/claude'"}, {"opt": "login", "text": " auth login"})
