"""G2 ticket 04: Claude and ordinary-shell launches are composed by the Rust runtime.

Principal seam: authenticated WebSocket -> Python handlers -> sidecar -> native PTY, with the
recorder of ticket 01 standing in for the CLI. Every row submits the command the way the frontend
does (the CLI name plus the flags the user chose) and adds nothing that an injector adds, so the
MCP, settings and push flags in the child's argv can only come from the composition under test.

Every run asserts the exact child argv: the submitted flags in submitted order and multiplicity,
then the injectors' flags (the same subject's plain row, observed in the same run on the same host
with the same skill state). The acceptance run adds the before/after comparison:

  NAVIDE_G2_SNAPSHOT_DIR   write each row's normalised observation there (the "before" capture, made
                           at the commit before the composition move, on each host).
  NAVIDE_G2_ACCEPTANCE=1   require NAVIDE_G2_BASELINE_DIR (this matrix's before-snapshots for this
                           host) and NAVIDE_G2_TICKET01_DIR (ticket 01's snapshots for this host);
                           a missing directory or row FAILS. Compare child argv, cwd and the full
                           environment of every row, and the plain rows with ticket 01's.
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
    BOOT_DIR, PROBE, _SECRET_NAME, _binary_name, _normalise_argv, _normaliser, _snapshot_leaks,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX composition only")]

NESTED_SETTINGS = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                                            "command": "echo '世界' \\\" ; | & café"}]}]}})
NESTED_TOML = 'a = "b c" # it\'s\n[hooks.stop]\ncommand = "echo \'世界\' \\\\ ; | &"\n'
RESUME_ID = "22222222-2222-4222-8222-222222222222"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
#: Variables this harness itself sets for the backend (runtime selection and a binary kept elsewhere).
HARNESS_ENV = ("NAVIDE_ACR_EVAL_RUNTIME", "NAVIDE_CLI_RUNTIME_BIN")
BASELINE_FORM ={"wrapped": "posix-login-shell", "direct-string": "direct"}

#: (id, subject, form, user flags as submitted, extra create payload)
ROWS = [
    ("claude-plain", "claude", "wrapped", "", {}),
    ("terminal-plain", "terminal", "wrapped", "", {}),
    ("claude-direct-plain", "claude", "direct-string", "", {}),
    ("terminal-direct-plain", "terminal", "direct-string", "", {}),
    ("claude-model-effort", "claude", "wrapped", "--model fixture-model --effort high", {}),
    ("claude-resume", "claude", "wrapped", f"--resume {RESUME_ID}", {}),
    ("claude-session-id", "claude", "wrapped", f"--session-id {SESSION_ID}", {}),
    ("claude-effort-handwritten", "claude", "wrapped", "--effort extreme", {}),
    ("claude-permission-flag", "claude", "wrapped", "--dangerously-skip-permissions", {}),
    ("claude-repeated-model", "claude", "wrapped", "--model first --model second", {}),
    ("claude-nested-json", "claude", "wrapped", f"--settings {shlex.quote(NESTED_SETTINGS)}", {}),
    ("claude-nested-toml", "claude", "wrapped", f"--config-override {shlex.quote(NESTED_TOML)}", {}),
    ("claude-login", "claude", "wrapped", "", {"is_login": True}),
    ("claude-direct-model", "claude", "direct-string", "--model fixture-model", {}),
    ("terminal-metacharacters", "terminal", "wrapped", "--x 'a b' café \"q\\\"uote\" 'it'\"'\"'s' ';|&'", {}),
    ("claude-long-shell", "claude", "wrapped", "--model fixture-model", {"long_shell": True}),
]
#: What the injectors alone put in the child's argv in the isolated fixture (no skills installed), as
#: normalised by the ticket 01 snapshot test: the Plan MCP config and the pane hook settings. The shell
#: is not wired. Written out, not derived, so a plain row cannot agree with itself by construction.
PLAIN_ARGV = {
    "claude": ["--mcp-config", "<ROOT>/data/plan-mcp-panes/claude-<hex>.json",
               "--settings", "<ROOT>/data/runtime/claude/settings.json"],
    "terminal": [],
}
PLAIN = {("claude", "wrapped"): "claude-plain", ("terminal", "wrapped"): "terminal-plain",
         ("claude", "direct-string"): "claude-direct-plain", ("terminal", "direct-string"): "terminal-direct-plain"}


def _receipts(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _owned(root: Path) -> list[dict]:
    return [record for record in _receipts(root) if record.get("event") == "owned"]


def _backend(root: Path) -> BackendProcess:
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):  # a build kept outside native/*/target (Linux container)
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    backend.env["NAVIDE_SNAPSHOT_PASSWD_HOME"] = str(root / "home")
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])
    return backend


def _shim(root: Path, subject: str) -> Path:
    return install_cli_shim(root, _binary_name(subject),
                            [base_python_executable(), "-u", str(PROBE), subject, str(root / "launch.json")])


def _long_shell(root: Path) -> str:
    """/bin/bash reached through a path of more than 256 bytes."""
    directory = root.joinpath(*(["long-path-component-" + "x" * 40] * 5))
    directory.mkdir(parents=True)
    link = directory / "bash"
    link.symlink_to("/bin/bash")
    assert len(str(link).encode()) > 256
    return str(link)


def _submitted(shim: Path, flags: str, form: str, shell: str = "/bin/bash"):
    text = f"{shlex.quote(str(shim))} {flags}".strip()
    return [shell, "-lc", text] if form == "wrapped" else text


async def _observe(row: tuple, root: Path) -> tuple[dict, list[str], list[dict]]:
    """Launch one row and return its normalised snapshot, the raw child argv and the owned receipts."""
    name, subject, form, flags, extra = row
    extra = dict(extra)
    long_shell = extra.pop("long_shell", False)
    root.mkdir(parents=True)
    workspace = root / "pane-cwd"
    workspace.mkdir()
    shim = _shim(root, subject)
    normalise = _normaliser(root)
    backend = _backend(root)
    command = _submitted(shim, flags, form, _long_shell(root) if long_shell else "/bin/bash")
    async with backend, backend.connect() as socket:
        payload = {"pane_id": f"composition-{name}", "agent_key": subject, "cwd": str(workspace),
                   "command": command, "cols": 100, "rows": 30, "create_generation": str(uuid.uuid4()),
                   "env": {"REGRESSION_MARKER": "ordinary-setting"}, **extra}
        response = await backend.request(socket, "terminal.create", payload, ok=False)
        assert response.get("ok"), response.get("error")
        await backend.expect_output(socket, response["payload"]["terminal_session_id"], "LAUNCH_SNAPSHOT_READY")
    observed = json.loads((root / "launch.json").read_text(encoding="utf-8"))
    snapshot = {
        "subject": subject, "shell_form": BASELINE_FORM[form], "platform": sys.platform,
        "child_argv": _normalise_argv(normalise, observed["argv"]),
        "child_cwd": normalise(observed["cwd"]),
        "child_env": {key: "<redacted>" if _SECRET_NAME.search(key) else normalise(value)
                      for key, value in sorted(observed["env"].items())},
    }
    return snapshot, observed["argv"], _owned(root)


def _expected_argv(row: tuple, plain_argv: list[str], normalise_user) -> list[str]:
    _, subject, _, flags, extra = row
    if extra.get("is_login"):
        return ["auth", "login"]
    user = normalise_user(shlex.split(flags))
    tail = list(plain_argv)
    if "--settings" in user and "--settings" in tail:  # a user-named settings file replaces the injected one
        at = tail.index("--settings")
        tail[at:at + 2] = []
    return user + tail


@pytest.mark.parametrize("row", ROWS, ids=[row[0] for row in ROWS])
async def test_row_child_observation_and_receipt(row, tmp_path):
    name, subject, form, flags, extra = row
    snapshot, _raw, owned = await _observe(row, tmp_path / "row")
    assert snapshot["child_cwd"] == "<ROOT>/pane-cwd"
    assert snapshot["child_env"]["REGRESSION_MARKER"] == "ordinary-setting"
    assert not _snapshot_leaks(snapshot)
    target = os.environ.get("NAVIDE_G2_SNAPSHOT_DIR")
    if target:
        directory = Path(target) / sys.platform
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Exact argv: submitted flags (order, multiplicity) then the injectors' flags.
    # The injectors' flags are an independent constant (the normalised ticket 01 values), never copied
    # from what this run observed, so the plain rows are checked too.
    plain_name = PLAIN[(subject, form)]
    normalise = _normaliser(tmp_path / "row")
    expected_argv = _expected_argv(row, PLAIN_ARGV[subject], lambda words: _normalise_argv(normalise, words))
    assert snapshot["child_argv"] == expected_argv, snapshot["child_argv"]
    # The acceptance run: before/after child argv, cwd and full environment.
    if os.environ.get("NAVIDE_G2_ACCEPTANCE") == "1":
        for variable in ("NAVIDE_G2_BASELINE_DIR", "NAVIDE_G2_TICKET01_DIR"):
            assert os.environ.get(variable), f"acceptance run needs {variable}"
        before = Path(os.environ["NAVIDE_G2_BASELINE_DIR"]) / sys.platform / f"{name}.json"
        assert before.is_file(), f"no before-snapshot for {name} on {sys.platform}"
        expected = json.loads(before.read_text(encoding="utf-8"))
        # The Phase A baselines predate the redaction of secret-named variables (ticket 01's rule).
        expected["child_env"] = {key: "<redacted>" if _SECRET_NAME.search(key) else value
                                 for key, value in expected["child_env"].items()}
        for gone in HARNESS_ENV:
            snapshot["child_env"].pop(gone, None)
            expected["child_env"].pop(gone, None)
        for key in ("child_argv", "child_cwd", "child_env"):
            assert snapshot[key] == expected[key], f"{name}: {key} differs from the before-snapshot"
        if name == plain_name:
            ticket01 = Path(os.environ["NAVIDE_G2_TICKET01_DIR"]) / sys.platform / f"{subject}.{BASELINE_FORM[form]}.json"
            assert ticket01.is_file(), f"no ticket 01 snapshot {ticket01.name}"
            reference = json.loads(ticket01.read_text(encoding="utf-8"))
            for key in ("child_argv", "child_cwd"):
                assert snapshot[key] == reference[key], f"{name}: {key} differs from ticket 01"
            # Ticket 01 ran the Python runtime; this harness selects the Rust one through this one variable.
            ours = {k: v for k, v in snapshot["child_env"].items() if k not in HARNESS_ENV}
            assert ours == reference["child_env"], f"{name}: child_env differs from ticket 01"
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


async def test_policy_refusal_is_pythons_and_never_reaches_create(tmp_path):
    """A refusal before the CREATE request is built allocates no session id: its `create_failed`
    receipt is `pre-dispatch` with `session_id` null. A sidecar refusal after CREATE carries the
    session id it was given and the sidecar's own stage, so the two cannot be confused."""
    shim = _shim(tmp_path, "claude")
    backend = _backend(tmp_path)
    async with backend, backend.connect() as socket:
        response = await backend.request(socket, "terminal.create", {
            "pane_id": "composition-refused", "agent_key": "claude", "cwd": str(tmp_path / "does-not-exist"),
            "command": _submitted(shim, "", "wrapped"), "cols": 100, "rows": 30,
            "create_generation": str(uuid.uuid4())}, ok=False)
    assert not response.get("ok")
    failed = [record for record in _receipts(tmp_path) if record.get("event") == "create_failed"]
    assert len(failed) == 1, failed
    assert failed[0]["stage"] == "pre-dispatch" and failed[0]["session_id"] is None and failed[0]["effect"] == "none"
    assert _owned(tmp_path) == []
    assert not (tmp_path / "launch.json").exists()
