"""Agent-launch attempts (G3 H4, manager answer 2): the frozen stamp sources, the attempt plan, the receipt `acr-g3-launch-trial/1` and its validity, and the paired rows.

One attempt = one agent pane created through the production WebSocket client against the isolated backend (`g3/launch_driver.py`) with a fixture vendor (`baseline_peer.py` behind a CLI shim;
never a real vendor CLI, never a real home). The two stamps are recorded by the TypeScript attempt runner (`tests/cli/g3LaunchAttempt.ts`) from its own monotonic clock and nowhere else:

* `agentCreateAtMs`: `performance.now()` read immediately before the client sends the `terminal.create` request of the agent pane (the same call, payload and wrapped command in both arms; only
  the composer of the launch differs: Python composes in the Python arm, the sidecar in the Rust arm);
* `firstUsableAtMs`: `performance.now()` read when the `terminal.output` chunk that completes the fixture's `BASELINE_READY {json}\\n` line reaches the client (the child is running, its argv, cwd,
  home and injection were read from that line, and it has accepted the pty): the same event in both arms, for claude (Rust observer on macOS) and aider (no observer).

The vendor's observer is a property of the arm, not a third stamp: the Rust sidecar observes claude sessions independently of the launch path, so any cost it adds to launch is inside the stamped
interval; aider has none. The receipt also carries the arm identity facts (section 4 item 1) that make the attempt valid or not: the `owned` runtime receipt of the pane (`argv_source`,
`runtime_generation`) in the Rust arm, none in the Python arm.
"""
from __future__ import annotations

import hashlib
import json
import math

from tests.cli_regression.support.g1e import reference as ref

SCHEMA = "acr-g3-launch-trial/1"
PLAN_SCHEMA = "acr-g3-launch-plan/1"
METRIC = "lat:agent_create_to_first_usable_ms"
VENDORS = {"claude": "rust", "aider": "none"}
CELLS = (1, 4)
PAIRS = ref.N_PAIRS
ARMS = ("python", "rust-shell")
STAMP_SOURCES = {
    "agentCreateAtMs": {
        "clock": "node performance.now() of the attempt runner",
        "event": "the instant before the production client sends the WebSocket `terminal.create` request of the agent pane",
        "code": "tests/cli/g3LaunchAttempt.ts: `const start = performance.now()` immediately before `request('terminal.create', ...)`",
        "claude": {"python": "same instant", "rust-shell": "same instant"},
        "aider": {"python": "same instant", "rust-shell": "same instant"},
    },
    "firstUsableAtMs": {
        "clock": "node performance.now() of the attempt runner",
        "event": "arrival at the client of the `terminal.output` chunk that completes the fixture line `BASELINE_READY {json}\\n` of that pane",
        "code": "tests/cli/g3LaunchAttempt.ts: `StreamBuffer.until` over the READY line, stamped with the frame arrival `performance.now()` as in g1eAttempt.ts `create()`",
        "claude": {"python": "READY line of the claude shim fixture", "rust-shell": "READY line of the claude shim fixture"},
        "aider": {"python": "READY line of the aider shim fixture", "rust-shell": "READY line of the aider shim fixture"},
    },
    "armIdentity": {
        "rust-shell": "runtime receipt event `owned` of the pane (runtime-receipts.jsonl) with `argv_source: rust`, in the generation of the `ready` receipt whose owner is the backend, and `ready.executableSha256` equal to the frozen sidecar digest",
        "python": "no `owned` receipt for the pane; the backend runs without NAVIDE_ACR_EVAL_RUNTIME",
    },
    "observer": {"claude": "rust sidecar observer present on the Rust arm (inside the interval), python observer on the Python arm", "aider": "none in either arm"},
}


def stamp_sources_sha256() -> str:
    return hashlib.sha256(json.dumps(STAMP_SOURCES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_id(vendor: str, cell: int) -> str:
    return f"lifecycle|{METRIC}:{vendor}|cell{cell}"


def chain_keys() -> list[tuple[str, int]]:
    return [(vendor, cell) for vendor in VENDORS for cell in CELLS]


def chain(seed: str, vendor: str, cell: int) -> list[dict]:
    """One chain (vendor, cell): a discarded warm-up per arm (Python, then Rust), then 10 pairs, both arms back to back, the first arm balanced 5/5 and seeded. Mirrors `agentLaunchChain` in g3AgentLaunch.ts."""
    first = ref._shuffle(["python"] * 5 + ["rust-shell"] * 5, seed, f"agent:{vendor}:first{cell}")
    out = [{"vendor": vendor, "cell": cell, "repeat": 0, "mode": mode, "firstArm": "python", "warmup": True} for mode in ARMS]
    for repeat in range(PAIRS):
        second = "rust-shell" if first[repeat] == "python" else "python"
        out += [{"vendor": vendor, "cell": cell, "repeat": repeat, "mode": mode, "firstArm": first[repeat], "warmup": False} for mode in (first[repeat], second)]
    return out


def plan(seed: str) -> list[dict]:
    """All four chains run separately after the shell matrix, in a seeded chain order: 4 x (2 warm-ups + 20 scored) = 8 warm-ups + 80 scored attempts (`agentLaunchPlan` in g3AgentLaunch.ts)."""
    order = ref._shuffle(chain_keys(), seed, "agent-chains")
    return [attempt for vendor, cell in order for attempt in chain(seed, vendor, cell)]


def calibration_plan(seed: str) -> list[dict]:
    """The Python-only launch calibration (proposal 2.4: the same pattern per vendor/cell, 10 attempts per chain): one discarded Python warm-up per chain, then ten scored Python attempts in slot
    order, chains in the seeded `agent-calibration` order. 4 warm-ups + 40 scored attempts."""
    order = ref._shuffle(chain_keys(), seed, "agent-calibration")
    out = []
    for vendor, cell in order:
        out.append({"vendor": vendor, "cell": cell, "repeat": 0, "mode": "python", "slot": None, "warmup": True})
        out += [{"vendor": vendor, "cell": cell, "repeat": slot, "mode": "python", "slot": slot, "warmup": False} for slot in range(PAIRS)]
    return out


def plan_sha256(seed: str) -> str:
    body = {"measure": plan(seed), "calibration": calibration_plan(seed)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def f0_record(seed: str) -> dict:
    """What F0 records about agent launch (fixed before any calibration)."""
    return {"schema": PLAN_SCHEMA, "metric": METRIC, "vendors": dict(VENDORS), "cells": list(CELLS), "stampSources": STAMP_SOURCES, "stampSourcesSha256": stamp_sources_sha256(),
            "planSha256": plan_sha256(seed), "scored": 80, "warmups": 8, "calibrationScored": 40, "calibrationWarmups": 4}


def launch_ms(stamps: dict) -> float | None:
    """`agentLaunchMs` of g3AgentLaunch.ts: None unless both stamps are finite numbers and the order is not reversed."""
    start, end = stamps.get("agentCreateAtMs"), stamps.get("firstUsableAtMs")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (start, end))
    return float(end - start) if ok and end >= start else None


def receipt_problems(receipt: object, *, mode: str, vendor: str, cell: int, repeat: int, freeze_sha256: str, sidecar_sha256: str, warmup: bool = False) -> list[str]:
    """Section 4 item 1-5 for one launch attempt. Empty = the attempt is valid. A receipt that is not a dict is invalid."""
    if not isinstance(receipt, dict):
        return ["not a receipt object"]
    out = []
    if receipt.get("schema") != SCHEMA: out.append("not an acr-g3-launch-trial/1 receipt")
    if (receipt.get("selectedRuntime"), receipt.get("vendor"), receipt.get("cell"), receipt.get("repeat")) != (mode, vendor, cell, repeat): out.append("receipt content does not match its slot")
    if bool(receipt.get("warmup")) != warmup or receipt.get("measuring") is not (not warmup and receipt.get("kind") == "measured"): out.append("warm-up / measuring flags differ from the slot")
    if receipt.get("freezeSha256") != freeze_sha256: out.append("freeze digest differs (stale or unbound receipt)")
    if (receipt.get("toolchain") or {}).get("match") is not True: out.append("toolchain check did not match F0")
    if receipt.get("evidenceValid") is not True or receipt.get("passed") is not True: out.append("the attempt did not pass")
    if receipt.get("runtimeLost") is not False or receipt.get("sidecarRestarts") != 0: out.append("RUNTIME_LOST or a sidecar restart in the measured window")
    if receipt.get("survivors") != []: out.append("a process survived teardown")
    if receipt.get("isolationBreach") is not False: out.append("isolation breach")
    if mode == "rust-shell":
        if receipt.get("argvSource") != "rust" or receipt.get("owned") is not True: out.append("Rust arm: no owned receipt with argv_source rust (hidden fallback)")
        if receipt.get("readySidecarSha256") != sidecar_sha256: out.append("Rust arm: the ready receipt does not name the frozen sidecar digest")
        if receipt.get("pythonPtySpawns") != 0: out.append("Rust arm: Python spawned a PTY")
    elif receipt.get("argvSource") not in (None, "python") or receipt.get("owned") is True or receipt.get("readySidecarSha256") is not None:
        out.append("Python arm carries a Rust runtime receipt (owned, argv_source or a runtime ready receipt)")
    if not warmup and launch_ms(receipt) is None: out.append("the two stamps do not give a launch time")
    return out


def pairs_by_row(receipts: dict) -> dict[str, dict]:
    """`receipts[(mode, vendor, cell, repeat)] = receipt or None` (None / invalid = no value). Returns {row id: {pairs: [10 {repeat, python, rust, valid}]}} for the four rows, in the shape of
    g1e `summarise.build_rows`. A pair is valid iff both arms' receipts hold a launch time."""
    out = {}
    for vendor, cell in chain_keys():
        pairs = []
        for repeat in range(PAIRS):
            py, ru = (launch_ms(receipts.get((mode, vendor, cell, repeat)) or {}) for mode in ARMS)
            ok = py is not None and ru is not None
            pairs.append({"repeat": repeat, "python": py if ok else None, "rust": ru if ok else None, "valid": ok})
        out[row_id(vendor, cell)] = {"pairs": pairs}
    return out
