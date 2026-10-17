"""S13 receipts and counters, with the real sidecar and a fixture peer (E3; E2 v10 section 3).

Self-tests of the harness pieces: the per-create kernel checkpoint (CK) and the failed-create receipt, the
per-trial proof (and that it fails on each tampering), the reader counters on both arms with identical equalities,
the control-plane ledger and the descriptor probe. Receipts carry no descriptor numbers.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import sys

import pytest

from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.terminals import TerminalService
from tests.cli_regression.support import trial_proof
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.descriptor_probe import DescriptorProbe
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT
from tests.cli_regression.support.reader_counters import PythonPtyGuard, ReaderCounters
from tests.test_cli_runtime_lifecycle import LifeRig

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built macOS native runtime (product and harness)"),
]


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        yield rig
    finally:
        await rig.close()


async def run_trial(rig, guard=None):
    """Two shells, a fast-exit CLI, and two failed creates; returns (service, driver session ids)."""
    service = rig.service()
    created: set[str] = set()
    for index in range(2):
        session = await rig.life(service, f"pane-ck-{index}")
        created.add(session.id)
    fast = await service.create_async(pane_id="pane-ck-fast", agent_key="terminal", command=["/bin/echo", "ck"],
                                      cwd=str(rig.workspace), cols=100, rows=30, env=rig.env)
    created.add(fast.id)
    await rig.until(lambda: rig.sink.exits(fast.id), "the fast CLI exited")
    with pytest.raises(FileNotFoundError):  # refused before anything was dispatched
        await service.create_async(pane_id="pane-ck-bad", agent_key="terminal", command=["/bin/cat"], cwd="/nonexistent-dir", env=rig.env)
    for sid in list(created):
        await service.kill(sid, force=True)
    return service, created


async def test_every_create_has_exactly_one_ck_or_one_failed_create_receipt_and_the_proof_passes(rig, monkeypatch):
    guard = PythonPtyGuard(monkeypatch)
    service, created = await run_trial(rig)
    records = trial_proof.load(rig.root)
    ledger = service.control_ledger()
    verdict = trial_proof.prove(records, sidecar_pid=service._runtime.pid, python_pty_spawns=guard.spawns,
                                driver_sessions=created, ledger=ledger)
    assert verdict["ok"], verdict["failures"]
    cks = [r for r in records if r["event"] == "ck"]
    assert len(cks) == 3 and all(r["pass"] for r in cks)
    live = next(r for r in cks if r["root_alive"])
    assert live["slave_name"] == live["sidecar_tty"] and live["root_ctty_rdev"] == live["slave_rdev"], "(a) and (b) of the CK"
    failed = [r for r in records if r["event"] == "create_failed"]
    assert len(failed) == 1 and failed[0]["stage"] == "pre-dispatch" and failed[0]["effect"] == "none"
    assert guard.spawns == 0, "the Rust arm never used the Python PTY path"
    assert "handoff_received" in {r["event"] for r in records}


async def test_the_ck_is_emitted_before_the_create_reply_is_released(rig):
    service = rig.service()
    order: list[str] = []
    original = service._receipt

    def traced(record):
        if record["event"] == "ck":
            order.append("ck")
        original(record)

    service._receipt = traced
    session = await rig.life(service, "pane-ck-order")
    order.append("create-returned")
    assert order[:2] == ["ck", "create-returned"], order
    await service.kill(session.id, force=True)


async def test_the_receipts_carry_no_descriptor_numbers(rig):
    service, created = await run_trial(rig)
    text = (rig.root / "runtime-receipts.jsonl").read_text()
    for record in map(json.loads, text.splitlines()):
        assert not {"fd", "fds", "descriptor", "fileno", "master_fd", "slave_fd"} & set(record), record["event"]
    assert not re.search(r'"(fd|fds|fileno|descriptor|master_fd|slave_fd)"', text)


# -------------------------------------------------------------------- the proof fails on tampering

BREAKERS = {
    "missing CK": (lambda r: [x for x in r if x["event"] != "ck"][:], "neither a CK nor a failed-create"),
    "duplicate CK": (lambda r: r + [next(x for x in r if x["event"] == "ck")], "CK receipts for one session"),
    "orphan CK": (lambda r: r + [{**next(x for x in r if x["event"] == "ck"), "session_id": "never-created"}], "did not create"),
    "slave names differ": (lambda r: [{**x, "slave_name": "/dev/ttys999", "pass_a": False} if x["event"] == "ck" else x for x in r], "pass_a failed"),
    "ctty differs": (lambda r: [{**x, "pass_b": False} if x["event"] == "ck" and x["root_alive"] else x for x in r], "pass_b failed"),
    "not a master": (lambda r: [{**x, "pass_c": False} if x["event"] == "ck" else x for x in r], "pass_c failed"),
    "wrong binary hash": (lambda r: [{**x, "executableSha256": "0" * 64} if x["event"] == "ready" else x for x in r], "executableSha256"),
    "unreaped cleanup": (lambda r: [{**x, "root_reaped": False} if x["event"] == "cleaned" else x for x in r], "incomplete"),
    "handle not handed off": (lambda r: [{**x, "handle": "PosixTerminalHandle"} if x["event"] == "handle_attached" else x for x in r], "MasterHandle"),
    "no handoff receipt": (lambda r: [x for x in r if x["event"] != "handoff_received"], "validated handoff_received"),
}


@pytest.mark.parametrize("name", sorted(BREAKERS))
async def test_the_proof_names_each_tampering(rig, name):
    service, created = await run_trial(rig)
    records = trial_proof.load(rig.root)
    ledger = service.control_ledger()
    assert trial_proof.prove(copy.deepcopy(records), driver_sessions=created, ledger=ledger)["ok"], "control passes"
    mutate, expected = BREAKERS[name]
    verdict = trial_proof.prove(mutate(copy.deepcopy(records)), driver_sessions=created, ledger=ledger)
    assert not verdict["ok"] and any(expected in f for f in verdict["failures"]), (name, verdict["failures"])


async def test_the_proof_fails_a_python_pty_use_and_a_foreign_frame_type(rig, monkeypatch):
    service, created = await run_trial(rig)
    records = trial_proof.load(rig.root)
    ledger = service.control_ledger()
    assert "the Python PTY path was used" in "".join(trial_proof.prove(records, python_pty_spawns=1)["failures"])
    foreign = {**ledger, "unknown_types": [999], "rx": {**ledger["rx"], "unknown-999": {"frames": 1, "bytes": 40}}}
    assert "frozen" in "".join(trial_proof.prove(records, ledger=foreign)["failures"])


# --------------------------------------------------------------------------- the control-plane ledger

async def test_the_control_ledger_counts_frames_and_bytes_per_type_and_has_no_output_bytes(rig):
    service = rig.service()
    session = await rig.life(service, "pane-ledger")
    for _ in range(3):
        await service._request("ping")
    await rig.say(service, session, "flood 2000")  # terminal bytes: they must not appear in the control ledger
    await rig.until(lambda: "FLOOD_DONE" in rig.sink.text(session.id), "flood delivered", 30)
    ledger = service.control_ledger()
    assert ledger["unknown_types"] == []
    assert ledger["tx"]["PING"]["frames"] == 3 and ledger["rx"]["PING"]["frames"] == 3
    assert ledger["tx"]["CREATE"]["frames"] == 1 and ledger["rx"]["SESSION_OWNED"]["frames"] == 1
    terminal_bytes = len(rig.sink.raw(session.id))
    assert terminal_bytes > 400_000 and ledger["tx_bytes"] + ledger["rx_bytes"] < terminal_bytes // 20, \
        "no control frame carries terminal bytes: the ledger stays far below the stream"
    assert not {"TERMINAL_OUTPUT", "OUTPUT", "WRITE", "INPUT"} & (set(ledger["tx"]) | set(ledger["rx"]))
    await service.kill(session.id, force=True)


# ------------------------------------------------------------- reader counters on both arms

FLOOD = "import sys\nfor n in range(3000): sys.stdout.write('LINE %05d ' % n + 'x' * 88 + '\\n')\nsys.stdout.write('FINISHED\\n'); sys.stdout.flush()"


async def counters_for(service, exited, pane, workspace, env):
    counters = ReaderCounters().attach(service)
    arguments = dict(pane_id=pane, agent_key="terminal", cwd=str(workspace), env=env, cols=100, rows=30,
                     command=[base_python_executable(), "-c", FLOOD])
    session = await service.create_async(**arguments) if hasattr(service, "create_async") else service.create(**arguments)
    deadline = asyncio.get_running_loop().time() + 30
    while not exited():
        assert asyncio.get_running_loop().time() < deadline, "no terminal.exit"
        await asyncio.sleep(0.01)
    return counters, session


async def test_the_reader_counters_satisfy_the_same_equalities_on_both_arms(rig):
    # Rust arm
    rust = rig.service()
    rust_counters, rust_session = await counters_for(rust, lambda: any(r[0] == "exit" for r in rig.sink.timeline), "pane-rust", rig.workspace, rig.env)
    entry = rust_counters.sessions[rust_session.id]
    assert entry["read_total"] > 300_000 and entry["read_total"] == entry["emitted_total"] + entry["dropped_oldest_total"]
    assert entry["dropped_oldest_total"] == 0 and entry["emit_seq"] >= 1
    assert {kind for kind, *_ in entry["intervals"]} >= {"absorb", "emit"}
    # Python arm (the baseline service, unmodified), counted by the same code
    python_frames: list = []

    async def emit(frame):
        python_frames.append(frame)

    baseline = TerminalService(emit)
    py_counters, py_session = await counters_for(
        baseline, lambda: any(isinstance(f, dict) and f.get("type") == "terminal.exit" for f in python_frames), "pane-py", rig.workspace, rig.env)
    py_entry = py_counters.sessions[py_session.id]
    assert py_entry["read_total"] == py_entry["emitted_total"] + py_entry["dropped_oldest_total"]
    assert abs(py_entry["read_total"] - entry["read_total"]) < entry["read_total"] // 50, "same fixture, same byte volume on both arms"
    # origin snapshot and window delta
    origin = {"read_total": 0, "emitted_total": 0, "dropped_oldest_total": 0}
    assert rust_counters.equalities_hold(rust_session.id, origin=origin)


async def test_the_resize_slurp_watermark_is_recorded_after_the_slurp(rig):
    service = rig.service()
    counters = ReaderCounters().attach(service)
    session = await rig.life(service, "pane-watermark")
    await rig.say(service, session, "flood 300")
    async with service.control_async(session.id, 120, 40):
        pass
    entry = counters.sessions[session.id]
    assert len(entry["resize_watermarks"]) == 1 and entry["resize_watermarks"][0] <= entry["read_total"]
    await rig.until(lambda: "FLOOD_DONE" in rig.sink.text(session.id), "flood", 30)
    assert counters.equalities_hold(session.id)
    await service.kill(session.id, force=True)


# ----------------------------------------------------------------------------- the descriptor probe

async def test_the_descriptor_probe_records_freed_numbers_in_memory_only(rig, monkeypatch):
    probe = DescriptorProbe(monkeypatch)
    service = rig.service()
    session = await rig.life(service, "pane-probe")
    number = session.handle.fd
    await service.kill(session.id, force=True)
    assert number in probe.numbers()
    assert probe.numbers().count(number) == 1, "the wrapper closes once"
    keys = {key for line in (rig.root / "runtime-receipts.jsonl").read_text().splitlines() for key in json.loads(line)}
    assert not {"fd", "fds", "fileno", "descriptor", "master_fd", "freed_fd"} & keys, "no receipt field carries a descriptor number"
