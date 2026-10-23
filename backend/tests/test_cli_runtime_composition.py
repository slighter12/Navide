"""G2 ticket 04: Claude and ordinary-shell launches are composed by the Rust runtime.

Principal seam: authenticated WebSocket -> Python handlers -> sidecar -> native PTY. The child's
observed argv, cwd and environment must stay what they were, and the owned receipt must say who
composed the argv. Before the composition move the field is absent, so these tests fail.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

import pytest

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.test_cli_runtime_claude import receipts, reference_backend

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform != "darwin", reason="native macOS reference evidence")]

PROBE = Path(__file__).parent / "cli_regression/support/launch_snapshot_probe.py"


def owned_receipt(root: Path) -> dict:
    owned = [record for record in receipts(root) if record.get("event") == "owned"]
    assert len(owned) == 1, owned
    return owned[0]


async def test_claude_owned_receipt_says_rust_composed_the_argv(tmp_path):
    backend, command = reference_backend(tmp_path)
    workspace = tmp_path / "workspace café 世界 ' quote"
    workspace.mkdir()
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo '世界' \\\" ; | &"}]}]}}
    sid = "11111111-1111-4111-8111-111111111111"
    command[-1] += f" --model fixture-model --effort high --session-id {sid} --settings {shlex.quote(json.dumps(settings))}"
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id="composition-claude", agent_key="claude", command=command,
                                       cwd=str(workspace), metadata={"explicit_session_id": sid})
        await backend.expect_output(ws, created["terminal_session_id"], "CLAUDE_REFERENCE_READY")
        child = json.loads(next(tmp_path.glob("claude-child-*.json")).read_text())
        assert json.loads(child["argv"][child["argv"].index("--settings") + 1]) == settings
        assert child["cwd"] == str(workspace)
        assert owned_receipt(tmp_path).get("argv_source") == "rust"


async def test_shell_owned_receipt_says_rust_composed_the_argv(tmp_path):
    backend = BackendProcess(tmp_path)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    destination = tmp_path / "launch.json"
    shim = install_cli_shim(tmp_path, "terminal-probe",
                            [base_python_executable(), "-u", str(PROBE), "terminal", str(destination)])
    workspace = tmp_path / "pane cwd café"
    workspace.mkdir()
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id="composition-shell", agent_key="terminal", cwd=str(workspace),
                                       command=["/bin/bash", "-lc", shlex.quote(str(shim))],
                                       env={"REGRESSION_MARKER": "ordinary-setting"})
        await backend.expect_output(ws, created["terminal_session_id"], "LAUNCH_SNAPSHOT_READY")
        observed = json.loads(destination.read_text(encoding="utf-8"))
        assert observed["cwd"] == str(workspace)
        assert observed["env"]["REGRESSION_MARKER"] == "ordinary-setting"
        assert owned_receipt(tmp_path).get("argv_source") == "rust"
