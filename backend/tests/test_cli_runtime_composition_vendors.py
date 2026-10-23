"""G2 ticket 05: every registered vendor except Claude is composed by the Rust runtime.

Principal seam as in ticket 04 (`test_cli_runtime_composition.py`): authenticated WebSocket -> Python
handlers -> sidecar -> native PTY, the ticket 01 recorder standing in for the CLI. Membership and
options come from the registry and the generated declaration facts, never from a fixed count.

Exact child argv per row = the submitted flags (order, multiplicity) followed by the vendor's plain tail,
which is an independent copy of ticket 01's normalised snapshots (`data/g2_vendor_plain_argv.json`), not
anything observed in the same run. A login row is exactly the declared `login_args`.

Environment:
  NAVIDE_G2_RUNTIME=python  run the rows on the Python runtime (the "before" capture for vendors the
                            Rust runtime refuses at the pre-Phase-B commit, as ticket 01 did); the
                            Rust-only assertions (`argv_source`, CREATE size) are skipped.
  NAVIDE_G2_SNAPSHOT_DIR    write each row's normalised observation there.
  NAVIDE_G2_ACCEPTANCE=1    require NAVIDE_G2_BASELINE_DIR and NAVIDE_G2_TICKET01_DIR and compare argv,
                            cwd, the full environment and (credential rows) the outer argv, as ticket 04.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import uuid
from types import SimpleNamespace

import pytest

from agent_team_backend import credential_launch
from agent_team_backend.cli_runtime_frames import MAX_META_P2R
from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.test_launch_snapshots import (
    BOOT_DIR, PROBE, _SECRET_NAME, _binary_name, _normalise_argv, _normaliser, _snapshot_leaks,
)
from tests.test_cli_runtime_composition import BASELINE_FORM, HARNESS_ENV, RESUME_ID, SESSION_ID, _owned

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX composition only")]

RECORDER = Path(__file__).parent / "cli_regression" / "support" / "outer_argv_recorder.py"
PLAIN_ARGV = json.loads((Path(__file__).parent / "data" / "g2_vendor_plain_argv.json").read_text(encoding="utf-8"))
ADAPTERS_RS = Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime" / "src" / "adapters.rs"
PYTHON_RUNTIME = os.environ.get("NAVIDE_G2_RUNTIME") == "python"
VENDOR_KEYS = sorted(key for key in VENDORS if key != "claude")
#: Vendors whose launch takes the managed credential path (`live_file_from_context`): the outer wrapper.
MANAGED = frozenset(key for key, spec in VENDORS.items() if spec.live_file_from_context is not None)


def _rows() -> list[tuple]:
    """(id, vendor, form, flags as submitted, extra create payload), derived from the declarations."""
    rows: list[tuple] = []
    for key in VENDOR_KEYS:
        fact = facts.FACTS[key]
        status = fact.capabilities
        rows.append((f"{key}-plain", key, "wrapped", "", {}))
        rows.append((f"{key}-direct-plain", key, "direct-string", "", {}))
        if status.model == "integrated":
            rows.append((f"{key}-model", key, "wrapped", f"{fact.model_flag} fixture-model", {}))
        if status.effort == "integrated":
            rows.append((f"{key}-effort", key, "wrapped", f"{fact.effort_flag} {fact.known_efforts[0]}", {}))
            rows.append((f"{key}-effort-handwritten", key, "wrapped", f"{fact.effort_flag} extreme", {}))
        if status.skip_permissions == "integrated":
            rows.append((f"{key}-permission-flag", key, "wrapped", fact.permission_flag, {}))
        if status.resume_by_id == "integrated":
            rows.append((f"{key}-resume", key, "wrapped", fact.resume_args.replace("{id}", RESUME_ID), {}))
        if status.session_id_at_launch == "integrated":
            rows.append((f"{key}-session-id", key, "wrapped", f"{fact.session_id_flag} {SESSION_ID}", {}))
        if status.login == "integrated" and key not in MANAGED:
            rows.append((f"{key}-login", key, "wrapped", "", {"is_login": True}))
    # Declared gaps are not upgraded: a typed flag the declaration does not integrate reaches the CLI as
    # typed (aider keeps its own model flag; mcode's resume is not yet integrated).
    rows.append(("aider-model-typed", "aider", "wrapped", "--model fixture-model", {}))
    rows.append(("mcode-resume-typed", "mcode", "wrapped", f"--resume {RESUME_ID}", {}))
    return rows


ROWS = _rows()


def _backend(root: Path) -> BackendProcess:
    backend = BackendProcess(root)
    if not PYTHON_RUNTIME:
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        backend.env["NAVIDE_G2_META_LOG"] = str(root / "create-meta.jsonl")  # popped by the boot module
        if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):  # a build kept outside native/*/target
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    backend.env["NAVIDE_SNAPSHOT_PASSWD_HOME"] = str(root / "home")
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])
    return backend


def _recording_shell(root: Path) -> Path:
    """An executable named `bash` that records `[shell, flag, script]` (its own path as started, `$0`, then
    its arguments) and then runs the real /bin/bash with the same arguments."""
    directory = root / "outer-shell"
    directory.mkdir()
    shell = directory / "bash"
    shell.write_text("#!/bin/sh\nexec " + shlex.join([base_python_executable(), "-u", str(RECORDER),
                                                      str(root / "outer.json"), "/bin/bash"]) + ' "$0" "$@"\n',
                     encoding="utf-8")
    shell.chmod(0o700)
    return shell


async def _observe(row: tuple, root: Path) -> tuple[dict, list[dict], str | None]:
    """Launch one row; return its normalised snapshot, the owned receipts and the recorded outer script."""
    name, vendor, form, flags, extra = row
    root.mkdir(parents=True)
    workspace = root / "pane-cwd"
    workspace.mkdir()
    shim = install_cli_shim(root, _binary_name(vendor),
                            [base_python_executable(), "-u", str(PROBE), vendor, str(root / "launch.json")])
    normalise = _normaliser(root)
    backend = _backend(root)
    shell = str(_recording_shell(root)) if vendor in MANAGED and form == "wrapped" else "/bin/bash"
    text = f"{shlex.quote(str(shim))} {flags}".strip()
    command = [shell, "-lc", text] if form == "wrapped" else text
    async with backend, backend.connect() as socket:
        # `snapshot-<vendor>` is ticket 01's pane id: the plain argv embeds it (qwen's input file, codex's URL).
        payload = {"pane_id": f"snapshot-{vendor}", "agent_key": vendor, "cwd": str(workspace),
                   "command": command, "cols": 100, "rows": 30, "create_generation": str(uuid.uuid4()),
                   "env": {"REGRESSION_MARKER": "ordinary-setting"}, **extra}
        response = await backend.request(socket, "terminal.create", payload, ok=False)
        assert response.get("ok"), response.get("error")
        await backend.expect_output(socket, response["payload"]["terminal_session_id"], "LAUNCH_SNAPSHOT_READY")
    observed = json.loads((root / "launch.json").read_text(encoding="utf-8"))
    snapshot = {
        "subject": vendor, "shell_form": BASELINE_FORM[form], "platform": sys.platform,
        "child_argv": _normalise_argv(normalise, observed["argv"]),
        "child_cwd": normalise(observed["cwd"]),
        "child_env": {key: "<redacted>" if _SECRET_NAME.search(key) else normalise(value)
                      for key, value in sorted(observed["env"].items())},
    }
    outer = None
    if shell != "/bin/bash":
        outer = json.loads((root / "outer.json").read_text(encoding="utf-8"))["argv"]
        snapshot["outer_argv"] = _normalise_argv(normalise, outer)
    receipts_path = root / "runtime-receipts.jsonl"
    owned = _owned(root) if receipts_path.exists() else []
    return snapshot, owned, outer


def _expected_argv(row: tuple, normalise_user) -> list[str]:
    name, vendor, form, flags, extra = row
    if extra.get("is_login"):
        return shlex.split(facts.FACTS[vendor].login_args)
    return normalise_user(shlex.split(flags)) + list(PLAIN_ARGV[vendor][sys.platform]["wrapped"])


def _assert_outer_argv(vendor: str, outer: list[str], snapshot: dict, root: Path) -> None:
    """The full outer argv the runtime executed, `[shell, flag, script]`, is the Python oracle's, element
    for element, for the helper Python built and the line the runtime composed (which the child's own
    argv ties back to the submitted command)."""
    assert len(outer) == 3 and outer[0] == str(root / "outer-shell" / "bash") and outer[1] == "-lc", outer[:2]
    script = outer[2]
    prefix = script.split(" --shell-check -- ", 1)[0].rsplit("then if ", 1)[1]
    helper = shlex.split(prefix)
    assert helper[0] == sys.executable and helper[helper.index("--vendor") + 1] == vendor, helper[:2]
    line = script.split("; exec ", 1)[1].split("; else _navide_credential_status", 1)[0]
    assert _normalise_argv(_normaliser(root), shlex.split(line)[1:]) == snapshot["child_argv"], line
    oracle = credential_launch.wrap_command([outer[0], "-lc", line], SimpleNamespace(key=vendor), helper)
    assert oracle is not None
    assert outer == oracle, "the executed outer argv differs from credential_launch.wrap_command"


@pytest.mark.parametrize("row", ROWS, ids=[row[0] for row in ROWS])
async def test_row_child_observation_and_receipt(row, tmp_path):
    name, vendor, form, flags, extra = row
    root = tmp_path / "row"
    snapshot, owned, outer = await _observe(row, root)
    assert snapshot["child_cwd"] == "<ROOT>/pane-cwd"
    assert snapshot["child_env"]["REGRESSION_MARKER"] == "ordinary-setting"
    assert not _snapshot_leaks(snapshot)
    if directory := os.environ.get("NAVIDE_G2_SNAPSHOT_DIR"):
        target = Path(directory) / sys.platform
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = _expected_argv(row, lambda words: _normalise_argv(_normaliser(root), words))
    assert snapshot["child_argv"] == expected, snapshot["child_argv"]
    if outer is not None:
        _assert_outer_argv(vendor, outer, snapshot, root)
    if not PYTHON_RUNTIME:
        log = root / "create-meta.jsonl"
        sizes = [json.loads(line)["meta_bytes"] for line in log.read_text().splitlines()] if log.exists() else []
        assert sizes and len(set(sizes)) == 1 and sizes[0] < MAX_META_P2R // 8, sizes
        if directory := os.environ.get("NAVIDE_G2_META_DIR"):
            (Path(directory) / sys.platform).mkdir(parents=True, exist_ok=True)
            (Path(directory) / sys.platform / f"{name}.json").write_text(
                json.dumps({"row": name, "create_meta_bytes": sizes[0], "cap_bytes": MAX_META_P2R}) + "\n", encoding="utf-8")
        assert len(owned) == 1, owned
        assert owned[0].get("argv_source") == "rust", owned[0]
        assert "argv0" not in owned[0], "the attested argv0 feeds the registry only, not the receipt"
        assert owned[0].get("outer") == ("credential" if vendor in MANAGED else None), owned[0]
    if os.environ.get("NAVIDE_G2_ACCEPTANCE") == "1":
        _compare_with_baselines(name, vendor, form, snapshot, plain=name in (f"{vendor}-plain", f"{vendor}-direct-plain"))


def _compare_with_baselines(name: str, vendor: str, form: str, snapshot: dict, *, plain: bool) -> None:
    for variable in ("NAVIDE_G2_BASELINE_DIR", "NAVIDE_G2_TICKET01_DIR"):
        assert os.environ.get(variable), f"acceptance run needs {variable}"
    before = Path(os.environ["NAVIDE_G2_BASELINE_DIR"]) / sys.platform / f"{name}.json"
    assert before.is_file(), f"no before-snapshot for {name} on {sys.platform}"
    expected = json.loads(before.read_text(encoding="utf-8"))
    for side in (snapshot, expected):
        for gone in HARNESS_ENV:
            side["child_env"].pop(gone, None)
    for key in ("child_argv", "child_cwd", "child_env", "outer_argv"):
        assert snapshot.get(key) == expected.get(key), f"{name}: {key} differs from the before-snapshot"
    if plain:
        ticket01 = Path(os.environ["NAVIDE_G2_TICKET01_DIR"]) / sys.platform / f"{vendor}.{BASELINE_FORM[form]}.json"
        assert ticket01.is_file(), f"no ticket 01 snapshot {ticket01.name}"
        reference = json.loads(ticket01.read_text(encoding="utf-8"))
        for key in ("child_argv", "child_cwd", "child_env"):
            assert snapshot[key] == reference[key], f"{name}: {key} differs from ticket 01"


def test_the_matrix_covers_every_registered_vendor_but_claude():
    """Derived from the registry: no vendor can be added or dropped without a plain row here."""
    assert set(facts.FACTS) == set(VENDORS), "the generated declarations and the registry disagree"
    assert {row[1] for row in ROWS} == set(VENDORS) - {"claude"}
    assert MANAGED == {"kilo", "opencode"}
    for key in VENDOR_KEYS:
        assert (f"{key}-plain" in {row[0] for row in ROWS}) and (f"{key}-direct-plain" in {row[0] for row in ROWS})
        assert PLAIN_ARGV[key][sys.platform]["wrapped"] == PLAIN_ARGV[key][sys.platform]["direct"]


def test_the_declared_gaps_the_literal_rows_rely_on_are_still_declared():
    assert facts.FACTS["aider"].capabilities.model == "not-yet-integrated"
    assert facts.FACTS["mcode"].capabilities.resume_by_id == "not-yet-integrated"


async def test_a_vendor_without_a_rust_observer_launches_through_rust(tmp_path):
    """Admission is independent of observation: goose has no Rust observer and no observer-roster entry."""
    roster = ADAPTERS_RS.read_text(encoding="utf-8")
    assert '"goose"' not in roster, "the case must use a vendor the sidecar does not observe"
    row = ("goose-unobserved", "goose", "wrapped", "", {})
    snapshot, owned, _ = await _observe(row, tmp_path / "row")
    assert snapshot["child_argv"] == PLAIN_ARGV["goose"][sys.platform]["wrapped"]
    if not PYTHON_RUNTIME:  # the Python-runtime "before" capture has no owned receipts
        assert [record.get("argv_source") for record in owned] == ["rust"]
