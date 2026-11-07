"""The evaluation scaffolding of the Rust runtime bridge sits behind one hook that the product never installs.

Receipts, the control ledger, the sidecar fault switch, the launch guard, the per-create kernel checkpoint, the
binary digest and the log-rescan override are the harness's. The product runs with the hook that does nothing; only
the isolated harness's environment selects the one that acts, so the frozen harness files keep reaching each of them
through the same names.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime, cli_runtime_hook
from agent_team_backend.cli_runtime import RuntimeSession, RustTerminalService
from agent_team_backend.cli_runtime_hook import HarnessHook
from agent_team_backend.log_readers.watcher import DEFAULT_RESCAN_S

PY_SRC = Path(__file__).resolve().parents[1] / "agent_team_backend"


async def _sink(_frame) -> None:
    return None


@pytest.fixture
def harness_env(monkeypatch):
    """Every variable a harness sets, as the isolated harness would set them, but no isolated root."""
    monkeypatch.delenv("NAVIDE_REGRESSION_ROOT", raising=False)
    monkeypatch.setenv("NAVIDE_ACR_FAULT", "startup")
    monkeypatch.setenv("NAVIDE_LOG_RESCAN_S", "1")
    return monkeypatch


def test_without_the_isolated_root_the_hook_is_the_products_and_acts_on_nothing(harness_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def refusing_guard(*_args, **_kwargs):
        raise AssertionError("the product consulted a launch guard")

    monkeypatch.setattr(cli_runtime, "prepare_native_launch", refusing_guard, raising=False)
    hook = cli_runtime_hook.from_environment()
    assert type(hook) is HarnessHook
    assert hook.runtime_argv() == [], "the product never reads the fault switch"
    assert hook.rescan_interval_s() == DEFAULT_RESCAN_S, "the product keeps the default interval"
    env = {"HOME": "/h"}
    assert hook.launch_env(["x"], cwd="/", env=env) is env
    binary = tmp_path / "sidecar"
    binary.write_bytes(b"x")
    assert hook.executable_digest(binary) is None, "the product does not hash its binary"
    assert hook.receipt({"event": "any"}) is None
    assert hook.create_checkpoint(3, "sid", "pane", "gen", "/dev/ttys000", {"pid": 1}) is None
    hook.ledger.sent(1, 10)
    hook.ledger.received(object())
    hook.ledger.late_part(5)
    hook.ledger.late_answer()
    assert hook.ledger.snapshot() == {}
    assert list(tmp_path.iterdir()) == [binary], "the product wrote no file"


async def test_a_service_built_without_the_root_counts_and_records_nothing(harness_env):
    service = RustTerminalService(_sink)
    assert type(service._hook) is HarnessHook
    assert service.control_ledger() == {}
    service._receipt({"event": "unrecorded"})


async def test_the_isolated_root_selects_the_hook_that_acts(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    monkeypatch.delenv("NAVIDE_ACR_FAULT", raising=False)
    hook = cli_runtime_hook.from_environment()
    assert type(hook) is not HarnessHook and isinstance(hook, HarnessHook)
    hook.receipt({"event": "a", "n": 1})
    hook.receipt({"event": "b"})
    assert [json.loads(line) for line in (tmp_path / "runtime-receipts.jsonl").read_text().splitlines()] == [
        {"event": "a", "n": 1}, {"event": "b"}]
    assert hook.runtime_argv() == []
    for value, argv in (("startup", ["--fail-startup"]), ("protocol", ["--ready-wire", "999"]), ("other", [])):
        monkeypatch.setenv("NAVIDE_ACR_FAULT", value)
        assert hook.runtime_argv() == argv, "the switch is read when the sidecar is started, as before"
    binary = tmp_path / "sidecar"
    binary.write_bytes(b"sidecar bytes")
    assert hook.executable_digest(binary) == hashlib.sha256(b"sidecar bytes").hexdigest()


async def test_the_harness_hook_asks_the_guard_the_isolation_module_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    hook = cli_runtime_hook.from_environment()
    env = {"HOME": "/h"}
    monkeypatch.delattr(cli_runtime, "prepare_native_launch", raising=False)
    assert hook.launch_env(["x"], cwd="/w", env=env) is env, "no guard installed: the launch environment is unchanged"
    seen = []

    def guard(argv, *, cwd, env):
        seen.append((argv, cwd, env))
        return {**env, "GUARDED": "1"}

    monkeypatch.setattr(cli_runtime, "prepare_native_launch", guard, raising=False)  # what isolation.py assigns
    assert hook.launch_env(["x"], cwd="/w", env=env) == {"HOME": "/h", "GUARDED": "1"}
    assert seen == [(["x"], "/w", env)]


async def test_the_harness_hook_counts_control_frames_and_the_service_reports_them(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    service = RustTerminalService(_sink)
    service._ledger.sent(cli_runtime.code.CREATE, 100)
    service._ledger.late_answer()
    snapshot = service.control_ledger()
    assert snapshot["tx"]["CREATE"] == {"frames": 1, "bytes": 100} and snapshot["late_answers"] == 1
    recorded = [json.loads(line) for line in (tmp_path / "runtime-receipts.jsonl").read_text().splitlines()]
    assert recorded[-1]["event"] == "control_ledger" and recorded[-1]["tx_bytes"] == 100


def test_only_the_harness_hook_may_change_the_rescan_interval_and_it_is_still_validated(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    hook = cli_runtime_hook.from_environment()
    monkeypatch.setenv("NAVIDE_LOG_RESCAN_S", "0.5")
    assert hook.rescan_interval_s() == 0.5
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv("NAVIDE_LOG_RESCAN_S", "never")
        assert hook.rescan_interval_s() == DEFAULT_RESCAN_S
    assert "NAVIDE_LOG_RESCAN_S" in caplog.text


def test_the_runtime_selector_is_one_evaluation_gate(monkeypatch):
    monkeypatch.delenv("NAVIDE_ACR_EVAL_RUNTIME", raising=False)
    monkeypatch.delenv("NAVIDE_REGRESSION_ROOT", raising=False)
    assert cli_runtime_hook.runtime_selection() == "python"
    cli_runtime_hook.require_isolated_harness("python")
    for selected in ("rust-shell", "rust"):
        with pytest.raises(RuntimeError, match="requires the isolated regression harness"):
            cli_runtime_hook.require_isolated_harness(selected)
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", "/isolated")
    cli_runtime_hook.require_isolated_harness("rust-shell")
    with pytest.raises(RuntimeError, match="requires the isolated regression harness"):
        cli_runtime_hook.require_isolated_harness("rust")


# ------------------------------------------------------------------------------ what the product modules hold

SCAFFOLDING = {
    "cli_runtime.py": ["NAVIDE_ACR_FAULT", "NAVIDE_REGRESSION_ROOT", "cli_runtime_receipts", "ControlLedger",
                       "def prepare_native_launch", "hashlib", "evaluation."],
    "app.py": ["NAVIDE_LOG_RESCAN_S", "rescan_interval_from_env", "NAVIDE_ACR_EVAL_RUNTIME", "NAVIDE_REGRESSION_ROOT"],
    "osplat/__init__.py": ["controlling_tty", "controlling_terminal_device"],
    "cli_runtime_native.py": ["NAVIDE_ACR_EVAL_RUNTIME", "NAVIDE_REGRESSION_ROOT"],
}


@pytest.mark.parametrize("module", sorted(SCAFFOLDING))
def test_the_product_module_holds_no_evaluation_scaffolding(module):
    text = (PY_SRC / module).read_text()
    assert [name for name in SCAFFOLDING[module] if name in text] == []


def test_the_vendor_registry_does_not_depend_on_the_runtime():
    offenders = []
    for path in sorted((PY_SRC / "cli_vendors").rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"^\s*(?:from|import)\s+(?:\.+|agent_team_backend\.)"
                         r"(?:cli_runtime|cli_runtime_adapters|cli_runtime_native|cli_runtime_observation|cli_runtime_hook)\b", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "the vendor registry sits below the runtime: " + "; ".join(offenders)


def test_every_runtime_session_field_is_read_or_written_somewhere():
    sources = {path: path.read_text() for path in PY_SRC.rglob("*.py")}
    tests = {path: path.read_text() for path in (PY_SRC.parent / "tests").rglob("*.py")}
    unused = []
    for field in dataclasses.fields(RuntimeSession):
        use = re.compile(rf"\.{field.name}\b")  # a read or an assignment through an object; a bare local does not count
        if not sum(len(use.findall(text)) for text in (*sources.values(), *tests.values())):
            unused.append(field.name)
    assert unused == [], f"RuntimeSession fields nothing reads or writes: {unused}"
