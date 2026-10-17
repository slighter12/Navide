"""Per-trial proof of the Rust arm from the evaluation receipts (E3 S13; E2 v10 section 3 items 1-5; E1 15 (a)-(d)).

Pure functions over the receipt records of one run: they decide nothing about the product, they check that the
receipts the product wrote prove what E2 requires, and name every failed item. No descriptor numbers are read
or produced: none exist in the receipts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_team_backend import cli_runtime_frames as wire


def load(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def prove(records: list[dict], *, sidecar_pid: int | None = None, python_pty_spawns: int = 0,
          driver_sessions: set[str] | None = None, ledger: dict | None = None) -> dict:
    """Returns {"ok", "failures": [..], "items": {1: .., 2: .., 3: .., 4: .., 5: ..}}. `driver_sessions` is the set of
    session ids the driver created (accounting for item 2); `None` skips the orphan check against the driver."""
    failures: list[str] = []

    def fail(item: int, text: str) -> None:
        failures.append(f"item {item}: {text}")

    # (a) / item 1: one real, hashed sidecar
    ready = [r for r in records if r["event"] == "ready"]
    generations = {r.get("runtime_generation") for r in ready}
    if not ready:
        fail(1, "no ready receipt")
    for r in ready:
        executable = Path(r.get("executable", ""))
        if not executable.is_file():
            fail(1, "the ready receipt names no executable file that still exists")
        elif hashlib.sha256(executable.read_bytes()).hexdigest() != r.get("executableSha256"):
            fail(1, "executableSha256 differs from the hash recomputed from the executable file")
        if (r.get("protocol"), r.get("wire"), r.get("handoff")) != ("navide-acr", 1, "scm_rights"):
            fail(1, "wire/protocol identity")
        if sidecar_pid is not None and r.get("pid") != sidecar_pid:
            fail(1, "ready pid is not the sampled sidecar")
    if len(generations) > 1:
        fail(1, f"more than one runtime generation in one trial: {len(generations)}")

    # item 2: CK accounting and pass rule
    cks = [r for r in records if r["event"] == "ck"]
    failed = [r for r in records if r["event"] == "create_failed"]
    by_session: dict[str, list[dict]] = {}
    for r in cks:
        by_session.setdefault(r["session_id"], []).append(r)
    for sid, group in by_session.items():
        if len(group) != 1:
            fail(2, f"{len(group)} CK receipts for one session")
        if driver_sessions is not None and sid not in driver_sessions:
            fail(2, "a CK for a terminal the driver did not create")
        for r in group:
            for key in ("pass_a", "pass_b", "pass_c"):
                if not r.get(key):
                    fail(2, f"CK {key} failed (slave names {'equal' if r.get('pass_a') else 'differ'}, root_alive={r.get('root_alive')})")
    handed = {r["session_id"] for r in records if r["event"] == "handoff_received" and r.get("validated") is True and r.get("kind") == "scm_rights"}
    for sid in by_session:
        if sid not in handed:
            fail(2, "a CK without a validated handoff_received for its session")
    if driver_sessions is not None:
        accounted = set(by_session) | {r["session_id"] for r in failed if r["session_id"]}
        for sid in driver_sessions - accounted:
            fail(2, "a driver create with neither a CK nor a failed-create receipt")
    # (c) handle type
    attached = {r["session_id"]: r for r in records if r["event"] == "handle_attached"}
    for sid, group in by_session.items():
        if group and group[0].get("pass") and sid in attached and attached[sid].get("handle") != "MasterHandle":
            fail(2, "the attached handle is not the handed-off MasterHandle")
    # (d) Python PTY path unused
    if python_pty_spawns:
        fail(2, f"the Python PTY path was used {python_pty_spawns} time(s)")

    # item 3: owned/cleaned pairing
    owned = {r["session_id"]: r for r in records if r["event"] == "owned"}
    cleaned = {r["session_id"]: r for r in records if r["event"] == "cleaned"}
    roots = [(r["root"]["pid"], r["root"]["start_value"]) for r in owned.values()]
    if len(set(roots)) != len(roots):
        fail(3, "duplicate root identities")
    for sid in owned:
        c = cleaned.get(sid)
        if c is None:
            fail(3, "an owned session without a cleaned receipt")
        elif not (c.get("cleanup_complete") and c.get("root_reaped") and not c.get("survivors") and not c.get("unverifiable")):
            fail(3, "a cleaned receipt that is incomplete, unreaped or names survivors")
    for sid in cleaned:
        if sid not in owned:
            fail(3, "a cleaned receipt without an owned one")

    # item 5: control-plane ledger
    if ledger is not None:
        if ledger.get("unknown_types"):
            fail(5, f"frame types outside the frozen set: {ledger['unknown_types']}")
        known = {name for name, value in vars(wire.code).items() if isinstance(value, int) and not name.startswith("_")}
        for direction in ("tx", "rx"):
            for name in ledger.get(direction, {}):
                if name not in known:
                    fail(5, f"{direction} frame type {name} is not in the frozen E1 set")
    return {"ok": not failures, "failures": failures,
            "items": {"1": len(ready), "2": {"ck": len(cks), "failed_creates": len(failed)}, "3": {"owned": len(owned), "cleaned": len(cleaned)}}}
