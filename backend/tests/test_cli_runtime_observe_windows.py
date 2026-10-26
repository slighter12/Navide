"""G2 ticket 09 repair 1: Windows vendor observation over the principal seam (spec D2).

On Windows a vendor pane is launched through the Rust runtime (ConPTY, real sidecar) and observed by the Python
watcher: the sidecar has no Windows observer by design. For every registry vendor with a reader, the vendor's
synthetic catalog fixture (`tests/fixtures/cli-regression/<vendor>.json`) is written in two stages with a backend
restart between them, as the POSIX cases in `test_cli_runtime_observe_vendors.py` do, and the Python-observed
outward result is asserted against the fixture's own expected result: `session.detected` attributed to the pane,
the completion activity row, the persisted totals and the persisted usage and activity checkpoints. A Rust observer
does not exist here, so nothing is compared with one and no native observation receipt may appear.

Principal seam: authenticated WebSocket -> Python handlers -> real sidecar -> ConPTY (the `vendor_transport.py` peer
stands in for the CLI) -> production `LogWatcher`, sinks and persistence. Membership comes from the registry.

The cases run on Windows. Setting `NAVIDE_G2_OBSERVE_LOGIC=1` runs the same assertions on a POSIX host with the
Python terminal service, which checks the observation half of the cases only (no launch receipt is asserted there).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from agent_team_backend.cli_vendors.registry import VENDORS
from agent_team_backend import osplat
from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
from tests.cli_regression.support.vendor_data import MARKER, PANE, VendorData
from tests.cli_regression.test_vendor_transport import _install

WINDOWS = sys.platform == "win32"
pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(not WINDOWS and not os.environ.get("NAVIDE_G2_OBSERVE_LOGIC"),
                                 reason="Windows observation (D2); NAVIDE_G2_OBSERVE_LOGIC=1 checks the logic on POSIX")]

#: Every registry member with a Python watcher reader; the registry, never a count, decides membership.
OBSERVED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader)
CODEX_PEER = Path(__file__).parent / "cli_regression" / "support" / "codex_hook_peer.py"


def _command(backend: BackendProcess, vendor: str, data: VendorData) -> list[str]:
    """Codex binds a pane to its session only through its SessionStart hook (`app._maybe_announce_session`), so its
    stand-in fires the hook the launch wired, with the fixture's session as the payload, as the real codex does."""
    if vendor != "codex":
        return _install(backend, vendor)
    (backend.root / "codex-session-start.json").write_text(json.dumps({
        "hook_event_name": "SessionStart", "session_id": data.fixture["sessionId"], "source": "startup",
        "cwd": str(data.workspace), "transcript_path": str(data.path)}), encoding="utf-8")
    shim = install_cli_shim(backend.root, vendor, [base_python_executable(), "-u", str(CODEX_PEER), str(backend.root)])
    return osplat.paths.shell_command(osplat.paths.quote_arg(str(shim)))
#: The wait that stands for "the watcher has caught up" after a stage's writes.
QUIET_S = 6.0
STAGE_BOUND_S = 90.0


def _backend(root: Path, stage: int, home: Path) -> BackendProcess:
    backend = BackendProcess(root, artifact_prefix=f"windows-stage-{stage}-")
    if WINDOWS:
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    backend.env["XDG_DATA_HOME"] = str(home / ".local" / "share")  # where `VendorData` writes the vendor stores
    backend.env["GOOSE_PATH_ROOT"] = str(home / "goose-root")
    return backend


def _types(backend: BackendProcess, name: str) -> list[dict]:
    return [event["payload"] for event in backend.events if event.get("type") == name]


def _completed(backend: BackendProcess) -> bool:
    return any(row["event_type"] == "turn_complete" for row in _types(backend, "agent.activity"))


async def _pump(backend: BackendProcess, ws, until, what: str) -> None:
    try:
        async with asyncio.timeout(STAGE_BOUND_S):
            while not until():
                await backend.receive(ws)
    except TimeoutError:
        raise AssertionError(f"timed out after {STAGE_BOUND_S}s waiting for {what}") from None


async def _settle(backend: BackendProcess, ws) -> None:
    """Drain events until none arrives for QUIET_S (a bounded wait on a stream, not a poll)."""
    async with asyncio.timeout(STAGE_BOUND_S):
        while True:
            try:
                async with asyncio.timeout(QUIET_S):
                    await backend.receive(ws)
            except TimeoutError:
                return


async def _stage(root: Path, stage: int, vendor: str, data: VendorData, phases: list[str],
                 completion_phase: str) -> list[dict]:
    backend = _backend(root, stage, data.home)
    command = _command(backend, vendor, data)
    metadata = {} if vendor == "codex" else {"session_marker": MARKER}  # codex is bound by its hook and pane home
    if vendor == "claude":  # claude is bound by the session id its launch names
        metadata["explicit_session_id"] = data.fixture["sessionId"]
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id=PANE, agent_key=vendor, command=command, cwd=str(data.workspace),
                                       metadata=metadata)
        await backend.expect_output(ws, created["terminal_session_id"], "TRANSPORT_READY")
        for phase in phases:
            initial_marker = phase == "initial" and (
                vendor == "codex" or MARKER in json.dumps(data.fixture["phases"]["initial"]))
            if phase == "initial" and data.has_schema(phase) and data.shared_store:
                # A watcher's first sight of a store records its high-water mark and emits nothing for several
                # vendors: let the empty shared store be seen before its rows exist (see the POSIX cases).
                data.apply(phase, only="schema")
                await _settle(backend, ws)
                data.apply(phase, only="rows")
            else:
                data.apply(phase)
            if initial_marker and vendor != "claude":
                # The first sight of a store must happen before the next write, or that write is swallowed by it.
                await _pump(backend, ws, lambda: _types(backend, "session.detected"), "session.detected")
            elif phase == completion_phase:
                await _pump(backend, ws, lambda: _completed(backend), "the turn_complete row")
            await _settle(backend, ws)
        await _settle(backend, ws)
    return backend.events


def _durable(root: Path, workspace: Path, path: Path) -> dict:
    from agent_team_backend.db import Database
    from agent_team_backend.tokens_store import TokensStore
    database = Database(root / "data" / "navide.db")
    try:
        store = TokensStore(db=database)
        usage = store.get_ingestion_checkpoint(str(path), None)
        activity = store.get_ingestion_checkpoint(str(path), "@activity")
        totals = store.snapshot(str(workspace))["global"]["all_time"]
    finally:
        database.close()
    return {"usage_checkpoint": usage, "activity_checkpoint": activity,
            "totals": [totals.get("input"), totals.get("output")]}


def _receipts(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.timeout(900)
@pytest.mark.parametrize("vendor", OBSERVED)
async def test_windows_rust_launched_vendor_is_observed_by_the_python_watcher(vendor, tmp_path, monkeypatch, set_home):
    root = tmp_path / "pane-root"
    root.mkdir()
    data = VendorData(vendor, root, monkeypatch, set_home)
    try:
        completion_phase = "complete" if data.fixture["phases"].get("complete") else "response"
        events = await _stage(root, 1, vendor, data, ["initial", "response"], completion_phase)
        # The writer stays open across the restart, as a live CLI's does; the new backend resumes from its checkpoints.
        events += await _stage(root, 2, vendor, data, ["complete"], completion_phase)
        path = data.path.resolve()
        durable = _durable(root, data.workspace, path)
        receipts = _receipts(root)
        expected = data.fixture["expected"]
        fixture = data.fixture
        workspace = str(data.workspace)
    finally:
        data.close()

    stream = [(event["type"], event["payload"]) for event in events
              if event.get("type") in ("session.detected", "agent.activity")]

    # The complete outward story: one completion, with the fixture's reply, in the pane's workspace.
    completions = [row for kind, row in stream if kind == "agent.activity" and row["event_type"] == "turn_complete"]
    assert len(completions) == 1, [(kind, row.get("event_type"), row.get("detail")) for kind, row in stream]
    assert completions[0]["text"].strip() == expected["reply"]
    assert completions[0]["vendor"] == vendor
    assert completions[0]["workspace_path"] == workspace

    # Attribution: every detected session names the pane and the fixture's session; claude is bound by its explicit id.
    detected = [row for kind, row in stream if kind == "session.detected"]
    if vendor != "claude":
        assert detected, ("the watcher never reported the session", [
            (kind, row.get("event_type"), row.get("pane_id")) for kind, row in stream])
    assert all(row["pane_id"] == PANE and row["session_id"] == fixture["sessionId"] and row["vendor"] == vendor
               for row in detected), detected
    rows = [row for kind, row in stream if kind == "agent.activity"]
    foreign = [row["pane_id"] for row in rows if row["pane_id"] not in (None, "", PANE)]
    assert not foreign, f"activity attributed to another pane: {foreign}"
    if detected:
        index_of_first = [kind for kind, _ in stream].index("session.detected")
        unbound = [row["pane_id"] for position, (kind, row) in enumerate(stream)
                   if position > index_of_first and kind == "agent.activity" and row["pane_id"] != PANE]
        assert not unbound, f"activity after the session binding without the pane: {unbound}"

    # Persistence: the fixture's totals once (the restart replayed nothing), and both checkpoints saved.
    assert durable["totals"] == expected["tokens"], durable
    if any(expected["tokens"]):  # a vendor that reports no usage (cursor) has no usage checkpoint to save
        assert durable["usage_checkpoint"], "no usage checkpoint was persisted"
    assert durable["activity_checkpoint"], "no activity checkpoint was persisted"

    if WINDOWS:
        # Observed Python-side: no native observation happened; launched by the Rust runtime, composed by the sidecar.
        assert not [row for row in receipts if "parser" in row], "a native observation ran on Windows"
        owned = [row for row in receipts if row.get("event") == "owned"]
        assert owned and all(row.get("argv_source") == "rust" for row in owned), owned
