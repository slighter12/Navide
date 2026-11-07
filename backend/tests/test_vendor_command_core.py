"""The one resume-id reader, the one data-dirs routine and the one resume-id rewrite.

Every vendor reads its resume id and names its data directories through the same routine, driven by the
vendor's declaration (`shared/agent-cli/<vendor>.json`). These tests pin that routine on the declared spellings
and the named compatibility rules the Python arm keeps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_backend.cli_launch import Command, with_resume_id
from agent_team_backend.cli_vendors._vendor_facts import FACTS
from agent_team_backend.cli_vendors.base import (
    VendorRuntimeContext,
    declared_data_dirs,
    declared_log_files,
    declared_session_path,
    data_dirs_of,
    resume_id_from_command,
)

SHELL = ["/bin/zsh", "-ilc"]


def resume(vendor: str, command: object, **kwargs: bool) -> str:
    return resume_id_from_command(command, FACTS[vendor], **kwargs)


@pytest.mark.parametrize(
    ("vendor", "line", "expected"),
    [
        ("claude", "claude --resume abc-123", "abc-123"),
        ("claude", "claude --resume=abc-123", "abc-123"),
        ("claude", "claude --model x --resume abc-123 --foo", "abc-123"),
        ("codex", "codex resume abc-123", "abc-123"),
        ("codex", "codex resume --last", "--last"),  # a positional word has no dash guard
        ("codex", "codex --model x resume abc-123", ""),
        ("cursor", "agent --resume=abc-123", "abc-123"),
        ("cursor", "cursor-agent --resume=abc-123", "abc-123"),
        ("cursor", "cursor-agent --resume abc-123", "abc-123"),
        ("droid", "droid -r abc-123", "abc-123"),
        ("droid", "droid --resume abc-123", "abc-123"),
        ("qwen", "qwen -r abc-123", "abc-123"),
        ("opencode", "opencode -s abc-123", "abc-123"),
        ("opencode", "opencode --session abc-123", "abc-123"),
        ("goose", "goose session -r --session-id abc-123", "abc-123"),
        ("goose", "goose s --session-id abc-123", "abc-123"),
        ("goose", "goose --session-id abc-123", ""),  # the subcommand is part of the declared spelling
    ],
)
def test_the_resume_id_is_read_by_the_declared_spelling(vendor: str, line: str, expected: str) -> None:
    assert resume(vendor, line) == expected
    assert resume(vendor, [*SHELL, line]) == expected


def test_a_flag_followed_by_another_flag_names_no_id() -> None:
    assert resume("claude", "claude --resume --model x") == ""


def test_a_dash_leading_id_is_never_an_id() -> None:
    assert resume("claude", "claude --resume=--help") == ""
    assert resume("droid", "droid -r --help") == ""


def test_the_resume_flag_stops_at_a_bare_double_dash() -> None:
    assert resume("claude", "claude -- --resume abc-123") == ""
    assert resume("claude", "claude --resume abc-123 -- --resume other") == "abc-123"


def test_another_program_resumes_nothing() -> None:
    assert resume("claude", "codex --resume abc-123") == ""
    assert resume("claude", "") == ""


def test_a_vendor_without_a_resume_spelling_resumes_nothing() -> None:
    nameless = [key for key, facts in FACTS.items() if facts.resume_args is None]
    assert nameless
    for key in nameless:
        assert resume(key, f"{FACTS[key].command} --resume abc-123") == ""


def test_a_list_names_its_launch_by_its_last_element_whatever_the_shell() -> None:
    # As at the baseline: the real command is the last element of the argv the frontend wrapped.
    assert resume("claude", ["claude", "--resume", "abc-123"]) == ""
    assert resume("claude", ["/bin/zsh", "-x", "claude --resume abc-123"]) == "abc-123"
    assert resume("claude", ["cmd.exe", "/c", "claude --resume abc-123"]) == "abc-123"


@pytest.mark.parametrize("vendor", ["grok", "antigravity"])
def test_plain_only_vendors_still_read_only_a_known_shells_argv(vendor: str) -> None:
    facts = FACTS[vendor]
    line = f"{facts.command} {facts.resume_args.split()[0]} abc-123"
    assert resume_id_from_command(["/bin/zsh", "-ilc", line], facts, plain_only=True) == "abc-123"
    assert resume_id_from_command(["/bin/tcsh", "-ilc", line], facts, plain_only=True) == ""


def test_an_unbalanced_quote_falls_back_to_whitespace_splitting() -> None:
    assert resume("claude", "claude --resume abc-123 'oops") == "abc-123"


def test_a_resume_option_word_is_read_as_it_stands() -> None:
    command = Command.from_words(
        [{"exe": "claude"}, {"opt": "resume", "value": "abc-123", "text": " --resume abc-123"}]
    )
    assert resume("claude", command) == "abc-123"
    assert resume("claude", [*SHELL, command]) == "abc-123"


@pytest.mark.parametrize("vendor", ["grok", "antigravity"])
def test_plain_only_vendors_read_no_resume_out_of_shell_syntax(vendor: str) -> None:
    facts = FACTS[vendor]
    flag = facts.resume_args.split()[0]
    plain = f"{facts.command} {flag} abc-123"
    assert resume_id_from_command(plain, facts, plain_only=True) == "abc-123"
    for syntax in ("; echo", " && echo", " | cat", " $X", " `x`"):
        assert resume_id_from_command(plain + syntax, facts, plain_only=True) == ""
    assert resume_id_from_command(plain + " | cat", facts) == "abc-123"


def context(tmp_path: Path, **env: str) -> VendorRuntimeContext:
    return VendorRuntimeContext(home=tmp_path / "home", env=env, cwd=tmp_path / "cwd")


def test_data_dirs_default_to_the_home_directory(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    assert declared_data_dirs(FACTS["claude"].data_dirs, ctx) == (tmp_path / "home" / ".claude",)
    assert declared_data_dirs(FACTS["cursor"].data_dirs, ctx) == (tmp_path / "home" / ".cursor" / "chats",)


def test_data_dirs_take_the_first_non_empty_variable(tmp_path: Path) -> None:
    ctx = context(tmp_path, MINIMAX_DATA_DIR="", MAVIS_DATA_DIR=str(tmp_path / "mavis"))
    assert declared_data_dirs(FACTS["mcode"].data_dirs, ctx) == (tmp_path / "mavis",)
    ctx = context(tmp_path, MINIMAX_DATA_DIR=str(tmp_path / "minimax"), MAVIS_DATA_DIR=str(tmp_path / "mavis"))
    assert declared_data_dirs(FACTS["mcode"].data_dirs, ctx) == (tmp_path / "minimax",)


def test_data_dirs_append_to_the_root_in_both_cases(tmp_path: Path) -> None:
    assert declared_data_dirs(FACTS["opencode"].data_dirs, context(tmp_path)) == (
        tmp_path / "home" / ".local" / "share" / "opencode",
    )
    assert declared_data_dirs(FACTS["opencode"].data_dirs, context(tmp_path, XDG_DATA_HOME=str(tmp_path / "x"))) == (
        tmp_path / "x" / "opencode",
    )


def test_data_dirs_add_each_extra_variable_once(tmp_path: Path) -> None:
    ctx = context(tmp_path, CODEX_SQLITE_HOME=str(tmp_path / "db"))
    assert declared_data_dirs(FACTS["codex"].data_dirs, ctx) == (tmp_path / "home" / ".codex", tmp_path / "db")
    same = context(tmp_path, CODEX_HOME=str(tmp_path / "db"), CODEX_SQLITE_HOME=str(tmp_path / "db"))
    assert declared_data_dirs(FACTS["codex"].data_dirs, same) == (tmp_path / "db",)


def test_a_tilde_is_the_home_only_where_declared(tmp_path: Path) -> None:
    assert FACTS["qwen"].data_dirs.tilde and not FACTS["claude"].data_dirs.tilde
    ctx = context(tmp_path, QWEN_RUNTIME_DIR="~/runtime")
    assert declared_data_dirs(FACTS["qwen"].data_dirs, ctx) == (
        tmp_path / "home" / ".qwen",
        tmp_path / "home" / "runtime",
    )
    plain = context(tmp_path, CLAUDE_CONFIG_DIR="~/cfg")
    assert declared_data_dirs(FACTS["claude"].data_dirs, plain) == (tmp_path / "cwd" / "~" / "cfg",)


def test_a_relative_directory_is_made_absolute_against_the_cwd(tmp_path: Path) -> None:
    ctx = context(tmp_path, GROK_HOME="rel")
    assert declared_data_dirs(FACTS["grok"].data_dirs, ctx) == (tmp_path / "cwd" / "rel",)


def test_a_vendor_without_a_declaration_has_no_data_dirs() -> None:
    assert data_dirs_of(None) is None
    assert FACTS["aider"].data_dirs is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("codex resume child", "codex resume parent"),
        ([*SHELL, "codex resume child"], [*SHELL, "codex resume parent"]),
        ("codex", "codex"),
        ([], []),
        (None, ""),
    ],
)
def test_the_resume_id_is_swapped_in_text(command: object, expected: object) -> None:
    assert with_resume_id(command, "child", "parent") == expected


def test_the_resume_id_is_swapped_in_every_word_and_the_words_survive() -> None:
    command = Command.from_words(
        [
            {"exe": "codex"},
            {"opt": "resume", "value": "child", "text": " resume child"},
            {"arg": "note child"},
        ]
    )
    swapped = with_resume_id([*SHELL, command], "child", "parent")
    assert swapped[:2] == SHELL
    assert str(swapped[2]) == str(command).replace("child", "parent")
    assert swapped[2].words[1] == {"opt": "resume", "value": "parent", "text": " resume parent"}
    assert swapped[2].words[2] == {"arg": "note parent"}
    assert resume("codex", swapped) == "parent"


def test_the_resume_id_swap_is_a_plain_text_replace() -> None:
    """Named compatibility rule: the id is replaced wherever it stands in the line, as the baseline did."""
    assert with_resume_id("codex resume a && echo a", "a", "b") == "codex resume b && echo b"


@pytest.mark.parametrize(
    ("vendor", "workspace", "expected"),
    [
        ("claude", "/Users/me/proj", ("-Users-me-proj", "sid.jsonl")),
        ("droid", "/Users/me/proj", ("-Users-me-proj", "sid.jsonl")),
        ("grok", "/Users/me/my proj", ("%2FUsers%2Fme%2Fmy%20proj", "sid", "updates.jsonl")),
        ("qwen", "/Users/me/proj", ("-Users-me-proj", "chats", "sid.jsonl")),
        ("copilot", "/Users/me/proj", ("session-state", "sid", "events.jsonl")),
        ("antigravity", "/Users/me/proj", ("sid.db",)),
    ],
)
def test_the_session_path_is_composed_from_the_declaration(
    tmp_path: Path, vendor: str, workspace: str, expected: tuple[str, ...]
) -> None:
    assert declared_session_path(FACTS[vendor].session_path, tmp_path, workspace, "sid") == tmp_path.joinpath(*expected)


def test_a_vendor_that_cannot_name_a_session_path_declares_none() -> None:
    assert FACTS["cursor"].session_path is None
    assert FACTS["codex"].session_path is None


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_the_log_files_of_a_cwd_named_layout_scope_to_one_workspace(tmp_path: Path) -> None:
    mine = touch(tmp_path / "-Users-me-proj" / "a.jsonl")
    other = touch(tmp_path / "-Users-me-other" / "b.jsonl")
    touch(tmp_path / "-Users-me-proj" / "notes.txt")
    (tmp_path / "-Users-me-proj" / "c.jsonl.d").mkdir()
    assert set(declared_log_files(FACTS["claude"].logs, [tmp_path])) == {mine, other}
    assert declared_log_files(FACTS["claude"].logs, [tmp_path], "/Users/me/proj") == [mine]
    assert declared_log_files(FACTS["claude"].logs, [tmp_path], "/Users/me/none") == []


def test_a_layout_with_flat_and_cwd_named_files_scopes_to_the_cwd_ones(tmp_path: Path) -> None:
    flat = touch(tmp_path / "flat.jsonl")
    nested = touch(tmp_path / "-Users-me-proj" / "n.jsonl")
    fork = touch(tmp_path / "btw" / "f.jsonl")
    assert set(declared_log_files(FACTS["droid"].logs, [tmp_path])) == {flat, nested, fork}
    assert declared_log_files(FACTS["droid"].logs, [tmp_path], "/Users/me/proj") == [nested]


def test_the_log_files_of_a_nested_layout_include_the_archive(tmp_path: Path) -> None:
    live = touch(tmp_path / "-p" / "chats" / "a.jsonl")
    archived = touch(tmp_path / "-p" / "chats" / "archive" / "b.jsonl")
    assert set(declared_log_files(FACTS["qwen"].logs, [tmp_path])) == {live, archived}
    assert declared_log_files(FACTS["qwen"].logs, [tmp_path], "/p") == [live, archived]


def test_a_workspace_name_with_glob_characters_is_matched_literally(tmp_path: Path) -> None:
    odd = touch(tmp_path / "-Users-me-[a]*" / "a.jsonl")
    touch(tmp_path / "-Users-me-a" / "b.jsonl")
    assert declared_log_files(FACTS["droid"].logs, [tmp_path], "/Users/me/[a]*") == [odd]  # droid keeps such characters


def test_a_layout_of_one_store_file_is_found_only_where_it_is(tmp_path: Path) -> None:
    assert declared_log_files(FACTS["goose"].logs, [tmp_path]) == []
    db = touch(tmp_path / "sessions.db")
    touch(tmp_path / "sessions.db-wal")
    assert declared_log_files(FACTS["goose"].logs, [tmp_path]) == [db]


def test_a_root_that_does_not_exist_has_no_log_files(tmp_path: Path) -> None:
    assert declared_log_files(FACTS["kimi"].logs, [tmp_path / "missing"]) == []


def test_the_accept_test_drops_a_path_the_pattern_cannot_tell(tmp_path: Path) -> None:
    keep = touch(tmp_path / "proj" / "11111111-1111-1111-1111-111111111111" / "store.db")
    touch(tmp_path / "proj" / "not-an-id" / "store.db")
    found = declared_log_files(FACTS["cursor"].logs, [tmp_path], accept=lambda p: p.parent.name[0] == "1")
    assert found == [keep]


def test_the_cursor_project_name_is_the_md5_of_the_workspace(tmp_path: Path) -> None:
    import hashlib

    digest = hashlib.md5(b"/w").hexdigest()
    mine = touch(tmp_path / digest / "chat" / "store.db")
    touch(tmp_path / "other" / "chat" / "store.db")
    assert declared_log_files(FACTS["cursor"].logs, [tmp_path], "/w") == [mine]


@pytest.mark.parametrize(
    ("vendor", "name"),
    [("goose", "sessions.db"), ("opencode", "opencode.db"), ("kilo", "kilo.db"), ("copilot", "session-store.db")],
)
def test_a_reader_and_its_declaration_name_the_same_store_file(vendor: str, name: str) -> None:
    assert name in FACTS[vendor].logs.files


def test_the_declarations_that_scope_by_workspace_are_the_cwd_named_ones() -> None:
    scoped = sorted(key for key, facts in FACTS.items() if facts.logs and facts.logs.cwd != "absent")
    assert scoped == ["claude", "cursor", "droid", "grok", "pi", "qwen"]
