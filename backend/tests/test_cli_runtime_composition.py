"""G2 ticket 04: Claude and ordinary-shell launches are composed by the Rust runtime.

Principal seam: authenticated WebSocket -> Python handlers -> sidecar -> native PTY, with the
recorder of ticket 01 standing in for the CLI. Every row submits the command the way the frontend
does (the CLI name plus the flags the user chose) and adds nothing that an injector adds, so the
MCP, settings and push flags in the child's argv can only come from the composition under test.

Environment:
  NAVIDE_G2_BASELINE_DIR  a ticket 01 snapshot directory for this host (same skill fixture state);
                          the plain rows are then compared with it, normalised the same way.
  NAVIDE_G2_SNAPSHOT_DIR  also write each row's normalised observation there.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import uuid

import pytest

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.test_launch_snapshots import (
    BOOT_DIR, PROBE, _binary_name, _normalise_argv, _normaliser, _snapshot_leaks,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX composition only")]

NESTED_SETTINGS = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                                            "command": "echo '世界' \\\" ; | & café"}]}]}})
RESUME_ID = "22222222-2222-4222-8222-222222222222"
SESSION_ID = "11111111-1111-4111-8111-111111111111"

#: (id, subject, form, user flags as submitted, extra create payload)
ROWS = [
    ("claude-plain", "claude", "wrapped", "", {}),
    ("terminal-plain", "terminal", "wrapped", "", {}),
    ("claude-model-effort", "claude", "wrapped", "--model fixture-model --effort high", {}),
    ("claude-resume", "claude", "wrapped", f"--resume {RESUME_ID}", {}),
    ("claude-session-id", "claude", "wrapped", f"--session-id {SESSION_ID}", {}),
    ("claude-nested-settings", "claude", "wrapped", f"--settings {shlex.quote(NESTED_SETTINGS)}", {}),
    ("claude-login", "claude", "wrapped", "", {"is_login": True}),
    ("claude-direct-string", "claude", "direct-string", "--model fixture-model", {}),
    ("terminal-direct-string", "terminal", "direct-string", "", {}),
]
BASELINE_FILES = {"wrapped": "posix-login-shell", "direct-string": "direct"}


def _receipts(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _owned(root: Path) -> list[dict]:
    return [record for record in _receipts(root) if record.get("event") == "owned"]


def _backend(root: Path) -> BackendProcess:
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_SNAPSHOT_PASSWD_HOME"] = str(root / "home")
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])
    return backend


def _shim(root: Path, subject: str) -> Path:
    return install_cli_shim(root, _binary_name(subject),
                            [base_python_executable(), "-u", str(PROBE), subject, str(root / "launch.json")])


def _submitted(shim: Path, flags: str, form: str):
    text = f"{shlex.quote(str(shim))} {flags}".strip()
    return ["/bin/bash", "-lc", text] if form == "wrapped" else text


def _contiguous_once(haystack: list[str], needle: list[str]) -> bool:
    spans = [i for i in range(len(haystack) - len(needle) + 1) if haystack[i:i + len(needle)] == needle]
    return len(spans) == 1


@pytest.mark.parametrize("row", ROWS, ids=[row[0] for row in ROWS])
async def test_row_child_observation_and_receipt(row, tmp_path):
    name, subject, form, flags, extra = row
    workspace = tmp_path / "pane-cwd"
    workspace.mkdir()
    shim = _shim(tmp_path, subject)
    normalise = _normaliser(tmp_path)
    backend = _backend(tmp_path)
    async with backend, backend.connect() as socket:
        payload = {"pane_id": f"composition-{name}", "agent_key": subject, "cwd": str(workspace),
                   "command": _submitted(shim, flags, form), "cols": 100, "rows": 30,
                   "create_generation": str(uuid.uuid4()), "env": {"REGRESSION_MARKER": "ordinary-setting"}, **extra}
        response = await backend.request(socket, "terminal.create", payload, ok=False)
        assert response.get("ok"), response.get("error")
        await backend.expect_output(socket, response["payload"]["terminal_session_id"], "LAUNCH_SNAPSHOT_READY")
    observed = json.loads((tmp_path / "launch.json").read_text(encoding="utf-8"))
    snapshot = {
        "subject": subject, "shell_form": BASELINE_FILES[form], "platform": sys.platform,
        "child_argv": _normalise_argv(normalise, observed["argv"]),
        "child_cwd": normalise(observed["cwd"]),
        "child_env": {key: normalise(value) for key, value in sorted(observed["env"].items())},
    }
    assert snapshot["child_cwd"] == "<ROOT>/pane-cwd"
    assert snapshot["child_env"]["REGRESSION_MARKER"] == "ordinary-setting"
    # Each user flag reaches the child once and in order, whoever spelled it.
    if flags and not extra.get("is_login"):
        assert _contiguous_once(observed["argv"], shlex.split(flags)), observed["argv"]
    if extra.get("is_login"):
        assert observed["argv"][:2] == ["auth", "login"], observed["argv"]
    if name in ("claude-plain", "terminal-plain", "claude-direct-string", "terminal-direct-string") and flags == "":
        baseline_dir = os.environ.get("NAVIDE_G2_BASELINE_DIR")
        if baseline_dir:
            expected = json.loads((Path(baseline_dir) / sys.platform / f"{subject}.{BASELINE_FILES[form]}.json")
                                  .read_text(encoding="utf-8"))
            for key in ("child_argv", "child_cwd", "child_env"):
                assert snapshot[key] == expected[key], key
    target = os.environ.get("NAVIDE_G2_SNAPSHOT_DIR")
    if target:
        directory = Path(target) / sys.platform
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert not _snapshot_leaks(snapshot)
    owned = _owned(tmp_path)
    assert len(owned) == 1, owned
    assert owned[0].get("argv_source") == "rust"
    assert "argv0" not in owned[0], "the attested argv0 feeds the registry only, not the receipt"


async def test_direct_list_is_an_explicit_expected_refusal_for_an_injecting_vendor(tmp_path):
    """Delta 2.2: the list form keeps appending injected flags to its last element, which no program
    answers to, so the launch is refused and no child is observed."""
    workspace = tmp_path / "pane-cwd"
    workspace.mkdir()
    shim = _shim(tmp_path, "claude")
    backend = _backend(tmp_path)
    async with backend, backend.connect() as socket:
        response = await backend.request(socket, "terminal.create", {
            "pane_id": "composition-direct-list", "agent_key": "claude", "cwd": str(workspace),
            "command": [str(shim)], "cols": 100, "rows": 30, "create_generation": str(uuid.uuid4())}, ok=False)
    assert not response.get("ok")
    assert not (tmp_path / "launch.json").exists()
    assert all(record.get("argv_source") != "python" for record in _owned(tmp_path))


async def test_policy_refusal_never_reaches_the_sidecar_spawn(tmp_path):
    shim = _shim(tmp_path, "claude")
    backend = _backend(tmp_path)
    async with backend, backend.connect() as socket:
        response = await backend.request(socket, "terminal.create", {
            "pane_id": "composition-refused", "agent_key": "claude", "cwd": str(tmp_path / "does-not-exist"),
            "command": _submitted(shim, "", "wrapped"), "cols": 100, "rows": 30,
            "create_generation": str(uuid.uuid4())}, ok=False)
    assert not response.get("ok")
    assert _owned(tmp_path) == []
    assert not (tmp_path / "launch.json").exists()
