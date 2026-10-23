"""One declaration per registered vendor, and the three languages that are generated from it.

``shared/agent-cli/<key>.json`` is the only place a vendor's launch facts are written. The generator in
``scripts/generate-agent-cli-facts.py`` emits the Python, TypeScript and Rust data. These tests pin the three
promises of that arrangement: every registered vendor is declared, a stale or hand-edited generated file is
caught, and the hand-written consumers read the declared values instead of a second copy.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_team_backend.cli_vendors import registry

ROOT = Path(__file__).resolve().parents[2]
DECLARATIONS = ROOT / "shared" / "agent-cli"
GENERATOR = ROOT / "scripts" / "generate-agent-cli-facts.py"

STATUSES = {"integrated", "vendor-unsupported", "not-yet-integrated", "unknown"}
CAPABILITIES = {"model", "effort", "skip_permissions", "resume_by_id", "session_id_at_launch", "login"}
REQUIRED = {
    "key", "composition", "command", "model_flag", "effort_flag", "known_efforts", "permission_flag", "resume_args",
    "session_id_flag", "login_args", "supports_session_resume", "shutdown", "capabilities",
}


def declarations() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text()) for path in sorted(DECLARATIONS.glob("*.json"))}


def copy_declarations(tmp_path: Path) -> None:
    shutil.copytree(DECLARATIONS, tmp_path / "shared" / "agent-cli")
    shutil.copy(DECLARATIONS.parent / "agent-cli-subjects.json", tmp_path / "shared" / "agent-cli-subjects.json")


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_agent_cli_facts", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_generator(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GENERATOR), *args], capture_output=True, text=True, timeout=60)


def test_every_registered_vendor_has_exactly_one_declaration() -> None:
    declared = declarations()
    assert set(declared) == set(registry.VENDORS)
    for key, facts in declared.items():
        assert facts["key"] == key


def test_declarations_have_the_declared_shape() -> None:
    for key, facts in declarations().items():
        assert set(facts) == REQUIRED, key
        assert set(facts["capabilities"]) == CAPABILITIES, key
        assert set(facts["capabilities"].values()) <= STATUSES, key
        assert facts["command"], key
        if facts["resume_args"] is not None:
            assert "{id}" in facts["resume_args"], key
        # Status stays internal: no evidence or verification text rides along in the declaration.
        assert all(isinstance(value, str) for value in facts["capabilities"].values()), key


def test_a_flag_is_declared_only_where_the_capability_is_integrated() -> None:
    for key, facts in declarations().items():
        capabilities = facts["capabilities"]
        assert (facts["model_flag"] is not None) == (capabilities["model"] == "integrated"), key
        assert (facts["effort_flag"] is not None) == (capabilities["effort"] == "integrated"), key
        assert (facts["permission_flag"] is not None) == (capabilities["skip_permissions"] == "integrated"), key
        assert (facts["resume_args"] is not None) == (capabilities["resume_by_id"] == "integrated"), key
        assert (facts["session_id_flag"] is not None) == (capabilities["session_id_at_launch"] == "integrated"), key
        assert (facts["login_args"] is not None) == (capabilities["login"] == "integrated"), key
        assert (facts["known_efforts"] is not None) == (facts["effort_flag"] is not None), key


def test_goose_marks_missing_evidence_as_unknown() -> None:
    capabilities = declarations()["goose"]["capabilities"]
    assert {capabilities["model"], capabilities["effort"], capabilities["login"]} == {"unknown"}


def test_generated_files_are_current() -> None:
    result = run_generator("--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_hand_edited_generated_file_fails_the_check(tmp_path: Path) -> None:
    generator = load_generator()
    copy_declarations(tmp_path)
    generator.write_all(tmp_path)
    assert run_generator("--check", "--root", str(tmp_path)).returncode == 0
    for relative in generator.output_paths():
        target = tmp_path / relative
        original = target.read_text()
        target.write_text(original + "\n// hand edit\n")
        result = run_generator("--check", "--root", str(tmp_path))
        assert result.returncode != 0, relative
        assert str(relative) in result.stdout + result.stderr
        target.write_text(original)


def test_a_changed_declaration_fails_the_check_until_regenerated(tmp_path: Path) -> None:
    generator = load_generator()
    copy_declarations(tmp_path)
    generator.write_all(tmp_path)
    codex = tmp_path / "shared" / "agent-cli" / "codex.json"
    facts = json.loads(codex.read_text())
    facts["command"] = "codex-renamed"
    codex.write_text(json.dumps(facts, indent=2) + "\n")
    assert run_generator("--check", "--root", str(tmp_path)).returncode != 0
    generator.write_all(tmp_path)
    assert run_generator("--check", "--root", str(tmp_path)).returncode == 0
    assert "codex-renamed" in (tmp_path / generator.PYTHON_OUTPUT).read_text()
    assert "codex-renamed" in (tmp_path / generator.TYPESCRIPT_OUTPUT).read_text()
    assert "codex-renamed" in (tmp_path / generator.RUST_OUTPUT).read_text()


@pytest.mark.parametrize("key", sorted(registry.VENDORS))
def test_backend_spec_follows_the_declaration(key: str) -> None:
    facts = declarations()[key]
    spec = registry.VENDORS[key]
    assert spec.key == facts["key"]
    assert spec.supports_model == (facts["model_flag"] is not None)
    assert spec.supports_effort == (facts["effort_flag"] is not None)
    assert tuple(spec.known_efforts or ()) == tuple(facts["known_efforts"] or ())
    assert spec.login_command_args == facts["login_args"]
    assert spec.supports_session_resume == facts["supports_session_resume"]
    shutdown = facts["shutdown"]
    if shutdown is None:
        assert spec.shutdown is None
    else:
        assert spec.shutdown is not None
        assert (spec.shutdown.graceful, spec.shutdown.grace_s, spec.shutdown.defer_master_close) == (
            shutdown["graceful"], shutdown["grace_s"], shutdown["defer_master_close"])
