"""No caller restates a vendor fact or launches an agent outside the seam.

``scripts/audit-cli-callers.py`` reads the declarations in ``shared/agent-cli``
and reports every keyed literal that restates a declared command or flag, and
every process spawn outside the terminal service seam whose argv names a
declared command.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "audit_cli_callers", REPO_ROOT / "scripts" / "audit-cli-callers.py"
)
assert _spec and _spec.loader
audit_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_mod)


def _tree(tmp_path: Path) -> Path:
    decl = tmp_path / "shared" / "agent-cli"
    decl.mkdir(parents=True)
    (decl / "zed.json").write_text(
        json.dumps({"key": "zed", "command": "zedcli", "permission_flag": "--zap"}),
        encoding="utf-8",
    )
    return tmp_path


def test_repository_has_no_vendor_fact_table_or_launch_bypass() -> None:
    assert audit_mod.audit(REPO_ROOT) == []


def test_a_new_vendor_fact_table_is_found(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    target = root / "src" / "renderer" / "src" / "table.ts"
    target.parent.mkdir(parents=True)
    target.write_text(
        "export const T = { zed: { command: 'zedcli', yoloFlag: '--zap' } }\n",
        encoding="utf-8",
    )
    found = audit_mod.audit(root)
    assert len(found) == 1
    assert found[0].startswith("src/renderer/src/table.ts:1: fact-table:")


def test_a_launch_bypass_is_found_outside_the_seam_but_not_inside(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    backend = root / "backend" / "agent_team_backend"
    backend.mkdir(parents=True)
    source = "import subprocess\nsubprocess.Popen(['zedcli', '--x'])\n"
    (backend / "rogue.py").write_text(source, encoding="utf-8")
    (backend / "terminals.py").write_text(source, encoding="utf-8")
    found = audit_mod.audit(root)
    assert [line.split(":")[0] for line in found] == [
        "backend/agent_team_backend/rogue.py"
    ]
    assert "launch-bypass" in found[0]


def test_generated_facts_and_strategies_are_exempt(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    vendors = root / "backend" / "agent_team_backend" / "cli_vendors"
    vendors.mkdir(parents=True)
    (vendors / "zed.py").write_text("SPEC = dict(command='zedcli')\n", encoding="utf-8")
    assert audit_mod.audit(root) == []
