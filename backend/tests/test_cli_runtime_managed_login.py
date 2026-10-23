"""G2 ticket 05, delta addendum 10.2: a managed vendor's login launch is a structured `credential` launch.

A child-observed managed login needs a verified credential store, which the isolated fixture does not have
(the create is refused with CREDENTIAL_STORE_UNVERIFIED, `test_cli_runtime_vendor_transport.py`). The
CREATE the Rust sidecar is sent is still observable: the boot module of the regression backend logs its
`launch` object (never its environment). This test pins the three properties of the login exclusion for
both managed vendors: the words are `[head, {"opt":"login"}]`, the helper carries `--login`, and no REPL
injector added a word.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys

import pytest

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.test_launch_snapshots import BOOT_DIR

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression, pytest.mark.cli_vendor,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX composition only")]


@pytest.mark.parametrize("vendor", ["kilo", "opencode"])
async def test_a_managed_login_create_is_a_credential_launch_without_repl_injection(tmp_path, vendor):
    backend = BackendProcess(tmp_path)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_G2_META_LOG"] = str(tmp_path / "create-meta.jsonl")  # popped by the boot module
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])
    if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    # A bash-wrapped literal invocation is one the wrapper can verify (an unwrappable login command is
    # refused before any CREATE, see `test_managed_login_rejects_unverifiable_command`).
    script = Path(__file__).parent / "cli_regression" / "support" / "vendor_transport.py"
    shim = install_cli_shim(tmp_path, vendor, [base_python_executable(), "-u", str(script), vendor, str(tmp_path)])
    command = ["/bin/bash", "-lc", shlex.quote(str(shim))]
    async with backend, backend.connect() as ws:
        response = await backend.request(ws, "terminal.create", {
            "pane_id": vendor, "agent_key": vendor, "command": command, "cwd": str(tmp_path), "is_login": True},
            ok=False)
    assert response["ok"] is False and response["error"]["code"] == "CREDENTIAL_STORE_UNVERIFIED"
    log = tmp_path / "create-meta.jsonl"
    creates = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    # One CREATE; the service encodes it twice (size check, then send), so the log holds two identical lines.
    assert creates and all(record == creates[0] for record in creates), creates
    launch = creates[0]["launch"]
    assert launch["shape"] == "credential", launch
    assert "--login" in launch["helper"] and launch["helper"][launch["helper"].index("--vendor") + 1] == vendor
    inner = launch["inner"]
    # Only the head the user's command started with and the declared login option: no `arg` word means no
    # MCP, skills, push or port flag reached a login pane.
    assert inner["words"] == [{"lit": command[-1]}, {"opt": "login"}], inner
