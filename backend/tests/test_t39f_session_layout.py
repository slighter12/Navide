"""The session root, the workspace encoders and the declaration-derived defaults (t39f M4, M5, M6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_backend.cli_vendors import registry, session_layout
from agent_team_backend.cli_vendors._vendor_facts import FACTS, CwdEncoding
from agent_team_backend.cli_vendors.base import VendorRuntimeContext, declared_log_files
from agent_team_backend.log_readers.base import LogReader


# ---- M6: one encoder per CwdEncoding ----------------------------------------------------------------------------------


def test_every_cwd_encoding_has_an_encoder() -> None:
    assert set(session_layout.ENCODERS) == set(CwdEncoding)


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        (CwdEncoding.ABSENT, ""),
        (CwdEncoding.CLAUDE, "-Users-me-my-proj"),
        (CwdEncoding.DROID, "-Users-me-my proj"),
        (CwdEncoding.URL_QUOTE, "%2FUsers%2Fme%2Fmy%20proj"),
        (CwdEncoding.PI, "--Users-me-my proj--"),
    ],
)
def test_the_encoders_spell_a_workspace_as_one_directory_name(encoding: CwdEncoding, expected: str) -> None:
    assert session_layout.encoded_cwd(encoding, "/Users/me/my proj") == expected


def test_md5_is_empty_for_no_workspace() -> None:
    assert session_layout.encoded_cwd(CwdEncoding.MD5, "") == ""
    assert len(session_layout.encoded_cwd("md5", "/w")) == 32


def test_a_layout_with_no_workspace_in_its_paths_scopes_to_nothing(tmp_path: Path) -> None:
    (tmp_path / "x.db").write_text("")
    logs = FACTS["antigravity"].logs
    assert logs.cwd == CwdEncoding.ABSENT
    assert declared_log_files(logs, [tmp_path]) == [tmp_path / "x.db"]
    assert declared_log_files(logs, [tmp_path], "/w") == []


def test_the_core_imports_no_vendor_module() -> None:
    source = Path(session_layout.__file__).read_text(encoding="utf-8")
    for vendor in FACTS:
        assert f"from .{vendor} import" not in source
        assert f"cli_vendors.{vendor} import" not in source


# ---- M4: the session root ---------------------------------------------------------------------------------------------


def root(vendor: str, env: dict[str, str], home: Path) -> Path:
    return session_layout.session_root(FACTS[vendor].session_root, env, home)


def test_the_default_root_is_under_the_home_directory(tmp_path: Path) -> None:
    assert root("droid", {}, tmp_path) == tmp_path / ".factory" / "sessions"
    assert root("antigravity", {}, tmp_path) == tmp_path / ".gemini" / "antigravity-cli" / "conversations"
    assert root("muse", {}, tmp_path) == tmp_path / ".local" / "share" / "muse" / "sessions"


def test_a_base_variable_moves_the_root_and_the_sub_path_follows(tmp_path: Path) -> None:
    assert root("droid", {"FACTORY_HOME_OVERRIDE": "/o"}, tmp_path) == Path("/o/sessions")
    assert root("muse", {"XDG_DATA_HOME": " /x "}, tmp_path) == Path("/x/muse/sessions")
    assert root("muse", {"XDG_DATA_HOME": "  "}, tmp_path) == tmp_path / ".local" / "share" / "muse" / "sessions"


def test_a_root_variable_names_the_root_itself_and_wins_over_the_base(tmp_path: Path) -> None:
    env = {"PI_CODING_AGENT_SESSION_DIR": "/s", "PI_CODING_AGENT_DIR": "/d"}
    assert root("pi", env, tmp_path) == Path("/s")
    assert root("pi", {"PI_CODING_AGENT_DIR": "/d"}, tmp_path) == Path("/d/sessions")
    assert root("pi", {}, tmp_path) == tmp_path / ".pi" / "agent" / "sessions"


def test_fallbacks_are_tried_in_order_and_the_default_stands_in_when_none_exists(tmp_path: Path) -> None:
    default = tmp_path / ".claude" / "projects"
    assert root("claude", {}, tmp_path) == default
    (tmp_path / ".config" / "claude" / "projects").mkdir(parents=True)
    assert root("claude", {}, tmp_path) == tmp_path / ".config" / "claude" / "projects"
    default.mkdir(parents=True)
    assert root("claude", {}, tmp_path) == tmp_path / ".config" / "claude" / "projects"  # the declared order
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
    assert root("claude", env, tmp_path) == tmp_path / ".config" / "claude" / "projects"  # env is not a directory
    (tmp_path / "cfg" / "projects").mkdir(parents=True)
    assert root("claude", env, tmp_path) == tmp_path / "cfg" / "projects"


def test_the_reader_defaults_read_the_declaration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(tmp_path))
    sessions = tmp_path / "sessions"
    (sessions / "-w").mkdir(parents=True)
    log = sessions / "-w" / "s1.jsonl"
    log.write_text("{}\n")

    class Reader(LogReader):
        vendor = "droid"

        def parse_session_file(self, path, seen_keys):  # pragma: no cover - not read here
            return []

    reader = Reader()
    assert reader.project_dirs() == [sessions]
    assert reader.session_files() == [log]
    assert reader.session_files_for_workspace("/w") == [log]
    monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(tmp_path / "missing"))
    assert reader.project_dirs() == [] and reader.session_files() == []


def test_a_vendor_whose_layout_has_no_workspace_in_its_paths_cannot_scope_by_path(tmp_path: Path) -> None:
    class Reader(LogReader):
        vendor = "kimi"

        def parse_session_file(self, path, seen_keys):  # pragma: no cover - not read here
            return []

    assert Reader().session_files_for_workspace("/w") is None


def test_the_session_path_default_reads_the_declaration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QWEN_RUNTIME_DIR", str(tmp_path))
    spec = registry.vendor("qwen")
    assert spec.session_path("/Users/me/proj", "sid") == tmp_path / "projects" / "-Users-me-proj" / "chats" / "sid.jsonl"
    assert registry.vendor("cursor").session_path is None  # declares none
    assert registry.vendor("grok").session_path is not None  # a named hook (shortened groups) is kept


# ---- M5: the env allowlist is the declaration's ------------------------------------------------------------------------

#: Vendors whose data directory a record cannot say; they name their variables themselves.
EXPLICIT_DATA_DIR_ENV = {"goose": ("GOOSE_PATH_ROOT", "XDG_DATA_HOME", "APPDATA")}


@pytest.mark.parametrize("key", sorted(FACTS))
def test_the_data_dir_variables_are_the_declarations_env_and_extra_env(key: str) -> None:
    spec = registry.vendor(key)
    if spec is None:
        pytest.skip("no spec")
    record = FACTS[key].data_dirs
    if key in EXPLICIT_DATA_DIR_ENV:
        assert spec.data_dir_env_vars == EXPLICIT_DATA_DIR_ENV[key]
    elif record is None:
        assert spec.data_dir_env_vars == ()
    else:
        assert spec.data_dir_env_vars == (*record.env, *record.extra_env)


def test_a_variable_added_to_a_declaration_reaches_the_risk_context() -> None:
    ctx = VendorRuntimeContext(home=Path("/h"), env={"ADDED": "/a"}, cwd=Path("/c"))
    record = FACTS["droid"].data_dirs._replace(extra_env=("ADDED",))
    from agent_team_backend.cli_vendors.base import data_dirs_of

    declared = data_dirs_of(record)
    assert declared.env_vars == ("FACTORY_HOME_OVERRIDE", "ADDED")
    assert Path("/a") in declared(ctx)
