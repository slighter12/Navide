"""Windows launch snapshots: what the child process really receives, from the Python path.

The Windows counterpart of test_launch_snapshots.py (ticket 01 captured POSIX only). Each registered
vendor, plus the plain terminal, is launched through the authenticated WebSocket, the real Python
handlers and a ConPTY, in the three shapes the application submits on Windows: the agent pane's plain
command string (split by CommandLineToArgvW rules, program resolved through PATHEXT), PowerShell's
`-NoLogo -NoExit -Command` wrapper and cmd.exe's `/k` wrapper. The recorder of ticket 01 stands in for
the CLI behind a `.cmd` shim and reports its own argv, cwd and environment.

The capture runs on the hosted runner (NAVIDE_G2_SNAPSHOT_DIR) before any Rust composition is compared
with it; the Rust cases (test_cli_runtime_composition_windows.py) read the pinned files back.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest

from agent_team_backend.cli_vendors.registry import VENDORS
from .support.backend_process import BackendProcess
from .support.cli_shim import base_python_executable, install_cli_shim
from .test_launch_snapshots import (
    PROBE, _LONG_HEX, _PORT, _PORT_FLAG, _QUERY_TOKEN, _SECRET_NAME, _UUID, _normalise_argv, _snapshot_leaks,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "nt", reason="Windows shell forms only")]

SHELL_FORMS = ("direct", "pwsh", "cmd")
SUBJECTS = [*sorted(VENDORS), "terminal"]


def _binary_name(subject: str) -> str:
    return "terminal-probe" if subject == "terminal" else VENDORS[subject].install_dep.check_cmd[0]


def _long_path(path: str) -> str:
    """The long (non 8.3) spelling of an existing path."""
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetLongPathNameW(path, buffer, len(buffer))  # type: ignore[attr-defined]
    return buffer.value if 0 < size < len(buffer) else path


def _variants(path: Path | str) -> set[str]:
    """A path as spelled, resolved and long-named, with either separator and no trailing one."""
    spelled = str(path).rstrip("\\/")
    found = {spelled, os.path.realpath(spelled), _long_path(spelled)}
    found |= {text.replace("\\", "/") for text in found}
    return {text for text in found if text}


def _normaliser(root: Path):
    table: dict[str, str] = {}
    for label, paths in (
        ("<ROOT>", _variants(root)),
        ("<VENV_BIN>", _variants(Path(sys.executable).parent)),
        ("<PYTHON_BIN>", _variants(Path(base_python_executable()).parent)),
        ("<REPO>", _variants(Path(__file__).resolve().parents[3])),
        ("<TMP>", _variants(tempfile.gettempdir())),
        ("<HOME>", _variants(Path.home())),
    ):
        for path in paths:
            table[path] = label
    replacements = sorted(table.items(), key=lambda item: -len(item[0]))
    user = Path.home().name

    def normalise(text: str) -> str:
        for needle, label in replacements:
            text = re.sub(rf"{re.escape(needle)}(?=[\\/]|$|[^0-9A-Za-z_.\-])", label, text, flags=re.IGNORECASE)
        # Backslash and slash spellings of a normalised root collapse to one separator.
        text = re.sub(r"(<(?:ROOT|VENV_BIN|PYTHON_BIN|REPO|TMP|HOME)>)((?:[\\/][^\s\"'<>|]*)?)",
                      lambda m: m.group(1) + m.group(2).replace("\\", "/"), text)
        if user:
            text = re.sub(rf"(?<=[\\/]){re.escape(user)}(?=[\\/]|$)", "<USER>", text, flags=re.IGNORECASE)
        # Codex's per-launch hook credential travels in its hook command; it is random per run.
        text = re.sub(r"(X-Navide-Codex-Launch: )[A-Za-z0-9_\-]{16,}", r"\1<token>", text)
        text = _UUID.sub("<uuid>", text)
        text = _LONG_HEX.sub("<hex>", text)
        text = _PORT.sub(r"\1:<port>", text)
        text = _PORT_FLAG.sub(r"\1<port>", text)
        return _QUERY_TOKEN.sub(r"\1<token>", text)

    return normalise


def _submitted(form: str, shim: Path):
    """The command the application submits on Windows for each shape."""
    if form == "direct":
        return subprocess.list2cmdline([str(shim)])
    if form == "pwsh":
        pwsh = shutil.which("pwsh") or "pwsh.exe"
        return [pwsh, "-NoLogo", "-NoExit", "-Command", f"& '{shim}'"]
    # No quotes of its own: the hosted temp path has no space, and list2cmdline would turn a quoted
    # element into `\"...\"`, which cmd.exe does not read as a quoted program.
    return [os.environ.get("ComSpec", "cmd.exe"), "/k", str(shim)]


async def _observe(subject: str, form: str, root: Path, *, rust: bool = False) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "pane-cwd"
    workspace.mkdir()
    destination = root / "launch.json"
    shim = install_cli_shim(
        root, _binary_name(subject),
        [base_python_executable(), "-u", str(PROBE), subject, str(destination)],
    )
    command = _submitted(form, shim)
    backend = BackendProcess(root)
    if rust:  # the Rust runtime, selected the way the other evaluation harnesses select it
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    normalise = _normaliser(root)
    refused = None
    async with backend, backend.connect() as socket:
        payload = {"pane_id": f"snapshot-{subject}", "agent_key": subject, "cwd": str(workspace),
                   "command": command, "cols": 100, "rows": 30, "create_generation": str(uuid.uuid4()),
                   "env": {"REGRESSION_MARKER": "ordinary-setting"}}
        response = await backend.request(socket, "terminal.create", payload, ok=False)
        if not response.get("ok"):
            refused = {"code": response["error"]["code"], "message": normalise(response["error"]["message"])}
        else:
            await backend.expect_output(socket, response["payload"]["terminal_session_id"], "LAUNCH_SNAPSHOT_READY")
    snapshot = {
        "subject": subject,
        "shell_form": form,
        "platform": sys.platform,
        "submitted_command": normalise(command) if isinstance(command, str) else [normalise(part) for part in command],
    }
    if refused:
        return {**snapshot, "refused": refused}
    observed = json.loads(destination.read_text(encoding="utf-8"))
    return {
        **snapshot,
        "child_argv": _normalise_argv(normalise, observed["argv"]),
        "child_cwd": normalise(observed["cwd"]),
        "child_env": {key: "<redacted>" if _SECRET_NAME.search(key) else normalise(value)
                      for key, value in sorted(observed["env"].items())},
    }


@pytest.mark.parametrize("form", SHELL_FORMS)
@pytest.mark.parametrize("subject", SUBJECTS)
async def test_windows_launch_snapshot(subject, form, tmp_path):
    snapshot = await _observe(subject, form, tmp_path / "row")
    refused = snapshot.get("refused")
    assert refused is None, f"{subject}/{form} was refused ({refused['code']}: {refused['message']}), so no child was observed"
    assert snapshot["child_cwd"] == "<ROOT>/pane-cwd", snapshot["child_cwd"]
    assert snapshot["child_env"]["REGRESSION_MARKER"] == "ordinary-setting"
    rendered = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    assert not _snapshot_leaks(snapshot), _snapshot_leaks(snapshot)
    assert not re.search(r"X-Navide-Codex-Launch: (?!<token>)", rendered), "a codex launch credential reached the snapshot"
    leaks = [leak for leak in (*_variants(Path.home()), *_variants(tempfile.gettempdir())) if len(leak) > 3]
    assert not [leak for leak in leaks if leak.lower() in rendered.lower()], "a real home or OS temp path leaked"
    target = os.environ.get("NAVIDE_G2_SNAPSHOT_DIR")
    if target:
        directory = Path(target) / sys.platform
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{subject}.{form}.json").write_text(rendered, encoding="utf-8")
