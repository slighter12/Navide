"""The G3 F0 builder (H2, schema `acr-g3-freeze-f0/1`). Build and verify only: this module never runs a freeze by itself and measures nothing.

F0 records, for head `H`: the HEAD and tracked-diff hash; the sidecar path, bytes and SHA-256; the toolchain identities (g1e `toolchain.observe`) and uv's; the workload and schedule goldens
(files and the schedule computed for the NEW seed); the SHA-256 of the proposal, of v14-r2 and of v10, each compared with its registered digest; a new seed (refused if it is a G1-E seed); the
g1e pin manifest digest; the row registry digest and reader fixtures; the host class (U8: model, ncpu, memory, arch, OS build) with `sameAsG1E`; and the user's approval line (one whole
line of the ledger that names the proposal digest). The G1-E F0 (`acr-g1e-freeze/2`) is closed and untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import freeze as fz
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e import toolchain as tc
from tests.cli_regression.support.g3 import fixtures as fixtures_mod, launch, pins, probe, regression, rows

SCHEMA = "acr-g3-freeze-f0/1"
EVIDENCE_REVISION = "g3-cost-1"      # bound into every receipt by the TS runner (`binding()` reads these from F0)
METHOD_REVISION = "prereg-g3/1"
VENDORS = ["claude", "opencode"]     # the shell workload's vendors (runtime-workload-g1e.json)
PROPOSAL_SHA256 = "d81ccb2897a1c652ef4fc968dbb6b133bc6d4db28075f820d2f040553efb6b43"
REVIEW_SHA256 = "c1f4ed71b5a7412cecfc77486072f7675028f1b06ab123d4e1b384adac24eef5"
V14R2_SHA256 = summarise.V14R2_SHA256
V10_SHA256 = summarise.V10_SHA256
G1E_SEEDS = {"955de8733ae3c1a7ce0bf9642ec83bda"}          # `evidence/e4/r11/f0.json` scheduleSeed: a G3 seed must be new
G1E_HOST = {"ncpu": 15, "arch": "aarch64-apple-darwin"}      # the only host facts G1-E recorded (r14 host-record, r11 f0.json)
DOC_PATHS = {
    "proposal": ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/prereg/g3-prereg-proposal.md",
    "v14r2": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v14-r2.md",
    "v10": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v10.md",
}
DOC_DIGESTS = {"proposal": PROPOSAL_SHA256, "v14r2": V14R2_SHA256, "v10": V10_SHA256}
WORKLOAD_PATH = "tests/fixtures/cli-regression/runtime-workload-g1e.json"
SCHEDULE_GOLDEN_PATH = "tests/fixtures/cli-regression/g1e-schedule-golden.json"
LEDGER_PATH = summarise.LEDGER_RELATIVE
APPROVAL_PREFIX = "- "


class F0Error(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def canonical_sha256(obj) -> str:
    return fz.canonical_sha256(obj)


def approval_problems(approval: object, ledger_text: str | None) -> list[str]:
    """The user's dated line, whole and verbatim in the ledger, that names the proposal digest and U1-U8."""
    if not (isinstance(approval, dict) and set(approval) == {"ledger", "date", "quote"} and all(isinstance(v, str) and v.strip() for v in approval.values())):
        return ["the approval record is not {ledger, date, quote}"]
    out = []
    if approval["ledger"] != LEDGER_PATH:
        out.append(f"the approval names the ledger {approval['ledger']!r}, not {LEDGER_PATH}")
    if not isinstance(ledger_text, str):
        return out + ["the ledger cannot be read"]
    lines = [line.rstrip("\r") for line in ledger_text.split("\n")]
    if approval["quote"] not in lines:
        return out + ["the approval quote is not one whole line of the ledger"]
    quote = approval["quote"]
    if not quote.startswith(f"{APPROVAL_PREFIX}{approval['date']}"):
        out.append("the approval date is not the date the line starts with")
    if PROPOSAL_SHA256 not in quote or "U1-U8" not in quote or "USER APPROVAL" not in quote:
        out.append("the line is not the user's approval of U1-U8 for the proposal digest")
    return out


def doc_records(root: Path) -> tuple[dict, list[str]]:
    records, problems = {}, []
    for name, relative in DOC_PATHS.items():
        path = Path(root) / relative
        if not path.is_file():
            problems.append(f"{name}: missing at {relative}")
            continue
        digest = fz.sha256_file(path)
        records[name] = {"path": relative, "sha256": digest}
        if digest != DOC_DIGESTS[name]:
            problems.append(f"{name}: hashes to {digest[:12]}..., not the registered {DOC_DIGESTS[name][:12]}...")
    return records, problems


def sidecar_record(path: Path) -> dict:
    data = Path(path).read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def calibration_order_sha(seed: str) -> str:
    return canonical_sha256(cal.calibration_order(seed))


TOOLS = ("node", "pnpm", "python")


def toolchain_problems(toolchain: object) -> list[str]:
    """Every toolchain field the reused G1-E per-attempt checks read from F0 (`g1e.toolchain.compare` over node/pnpm/python/sidecar, and the Rust-arm `readyVersion` check of `g1eAttempt.ts`)
    must be a non-empty string. A missing one is a defect of F0, found here and not on the first Rust-arm attempt (G3-r2 amendment 1)."""
    if not isinstance(toolchain, dict):
        return ["the toolchain record is missing"]
    out = []
    for tool in TOOLS:
        record = toolchain.get(tool)
        out += [f"toolchain.{tool}.{field} is missing" for field in tc.IDENTITY_FIELDS if not (isinstance(record, dict) and isinstance(record.get(field), str) and record[field])]
    sidecar = toolchain.get("sidecar")
    out += [f"toolchain.sidecar.{field} is missing" for field in (*tc.SIDECAR_FIELDS, "readyVersion") if not (isinstance(sidecar, dict) and isinstance(sidecar.get(field), str) and sidecar[field])]
    return out


def probe_ready_version(sidecar: Path) -> str:
    """The G1-E `sidecar-probe` step (one start-and-shutdown readiness probe of the sidecar; `g1e/helper.py sidecar-probe`), through `g3/probe.py`: the same steps with the wire version the backend speaks
    now (the g1e function hard-codes wire 1 and cannot start the sidecar at H). Not a measurement."""
    with tempfile.TemporaryDirectory(prefix="g3-probe-") as scratch:
        return probe.probe_ready_version(sidecar, Path(scratch))


def build_f0(root: Path, *, branch: str, sidecar: Path, seed: str, approval: dict, pin_manifest: dict, host: dict, reader_extra: list | None = None,
             git: Callable[..., bytes] = fz.git_output, observe_toolchain: Callable[[Path, Path], dict] | None = None, observe_uv: Callable[[], dict] | None = None,
             ledger: Path | None = None, reader_descriptor: Path | None = None, probe: Callable[[Path], str] | None = None) -> dict:
    """F0 for the head the tree is at. `reader_descriptor` = the verified reader fixture descriptor (`g3/fixtures.py`), bound into F0 as `readerFixtureDescriptor`. Every item is computed here from files and observations; the caller supplies only the seed, the approval record, the pin manifest and the host.
    `toolchain.sidecar.readyVersion` comes from the G1-E readiness probe (`probe`), exactly as `g1e.toolchain.record_for_freeze` adds it."""
    root = Path(root)
    problems: list[str] = []
    if not isinstance(seed, str) or not seed.strip():
        raise F0Error(["the seed is empty"])
    if seed in G1E_SEEDS:
        problems.append("the seed is a G1-E seed: G3 needs a new one")
    docs, doc_problems = doc_records(root)
    problems += doc_problems
    problems += approval_problems(approval, (Path(ledger) if ledger else root / LEDGER_PATH).read_text(encoding="utf-8") if (Path(ledger) if ledger else root / LEDGER_PATH).is_file() else None)
    problems += [f"g1e pins: {p}" for p in pins.verify_pins(root, pin_manifest)]
    missing_host = [f for f in ("model", "ncpu", "memoryGiB", "arch", "os") if not host.get(f)]
    if missing_host:
        problems.append(f"host class lacks {missing_host}")
    for relative in (WORKLOAD_PATH, SCHEDULE_GOLDEN_PATH):
        if not (root / relative).is_file():
            problems.append(f"{relative} is missing")
    if problems:
        raise F0Error(problems)
    fixtures = rows.reader_fixtures(reader_extra)
    sidecar_rec = sidecar_record(sidecar)
    toolchain = (observe_toolchain or (lambda r, s: tc.observe(r, s)))(root, sidecar)
    ready_version = (probe or probe_ready_version)(sidecar)
    toolchain = {**toolchain, "sidecar": {**(toolchain.get("sidecar") or {}), "readyVersion": ready_version}}      # as `tc.record_for_freeze`
    if toolchain_problems_found := toolchain_problems(toolchain):
        raise F0Error(toolchain_problems_found)
    uv =(observe_uv or (lambda: tc.observe_uv(tc.runner_in(root))))()
    schedule = ref.make_schedule(seed)
    return {
        "schema": SCHEMA, "head": git(root, "rev-parse", "HEAD").decode().strip(), "branch": branch,
        "trackedDiffSha256": hashlib.sha256(git(root, "diff", "HEAD", "--", ".")).hexdigest(),
        "sidecar": sidecar_rec, "toolchain": toolchain, "uv": uv,
        "workload": {"path": WORKLOAD_PATH, "sha256": fz.sha256_file(root / WORKLOAD_PATH), "registered": dict(summarise.REGISTERED_WORKLOAD)},
        "goldens": {"schedule": {"path": SCHEDULE_GOLDEN_PATH, "sha256": fz.sha256_file(root / SCHEDULE_GOLDEN_PATH)}},
        "scheduleSeed": seed, "scheduleSha256": ref.schedule_sha(schedule), "calibrationOrderSha256": calibration_order_sha(seed),
        "documents": docs, "approval": dict(approval),
        "g1ePins": {"path": f"backend/tests/cli_regression/support/g3/{pins.MANIFEST_NAME}", "sha256": pins.manifest_sha256(pin_manifest), "files": len(pin_manifest["files"])},
        "rowRegistry": {"decisionIdsSha256": canonical_sha256(rows.decision_ids(fixtures["registered"])), "readerFixtures": fixtures},
        "hostClass": regression.host_class_record({k: host[k] for k in ("model", "ncpu", "memoryGiB", "arch", "os")}, G1E_HOST),
        "n": ref.N_PAIRS, "cells": list(ref.CELLS),
        "evidenceRevision": EVIDENCE_REVISION, "methodRevision": METHOD_REVISION, "sidecarSha256": sidecar_rec["sha256"], "vendors": list(VENDORS),
        "agentLaunch": launch.f0_record(seed),
        "readerFixtureDescriptor": fixtures_mod.f0_record(Path(reader_descriptor)) if reader_descriptor is not None else None,
    }


def checked_input_count(f0: dict) -> int:
    """G3-r2 round 2: how many frozen inputs `verify_f0` re-checks before every attempt, i.e. what a receipt's `freezeCheckedInputs` stands for (g1eMatrix took the length of its inputs list). One per pinned g1e file,
    per registered document, the workload, the schedule golden, the sidecar, and (when F0 binds one) the reader fixture descriptor plus each of its fixtures. `g1ePins.files` alone is only the first term."""
    descriptor = f0.get("readerFixtureDescriptor")
    reader = 1 + len((descriptor or {}).get("fixtures") or {}) if descriptor else 0
    return int((f0.get("g1ePins") or {}).get("files") or 0) + len(f0.get("documents") or {}) + 1 + 1 + 1 + reader


def verify_f0(root: Path, f0: dict, pin_manifest: dict, sidecar: Path | None, git: Callable[..., bytes] = fz.git_output, ledger: Path | None = None) -> list[str]:
    """Before every later step: F0 is well-formed and the world still equals it (empty list = it verifies)."""
    root = Path(root)
    if not isinstance(f0, dict) or f0.get("schema") != SCHEMA:
        return ["not an acr-g3-freeze-f0/1 record"]
    out = []
    try:
        if git(root, "rev-parse", "HEAD").decode().strip() != f0.get("head"): out.append("HEAD differs from the frozen HEAD")
        if hashlib.sha256(git(root, "diff", "HEAD", "--", ".")).hexdigest() != f0.get("trackedDiffSha256"): out.append("the tracked diff differs from the frozen one")
    except RuntimeError as error:
        out.append(str(error))
    if sidecar is not None:
        record = f0.get("sidecar") or {}
        if not Path(sidecar).is_file() or sidecar_record(sidecar)["sha256"] != record.get("sha256") or Path(sidecar).stat().st_size != record.get("bytes"):
            out.append("the sidecar binary is missing or differs from the frozen path/bytes/hash")
        elif os.path.realpath(sidecar) != os.path.realpath(record.get("path", "")):
            out.append("the sidecar is another file than F0's frozen sidecar path (equal bytes at a different path)")
    if f0.get("readerFixtureDescriptor") is not None:
        out += [f"reader fixtures: {p}" for p in fixtures_mod.f0_problems(f0["readerFixtureDescriptor"])]
    docs, doc_problems = doc_records(root)
    out += doc_problems
    if docs != f0.get("documents"): out.append("the document records differ from F0's")
    out += [f"g1e pins: {p}" for p in pins.verify_pins(root, pin_manifest)]
    if pins.manifest_sha256(pin_manifest) != (f0.get("g1ePins") or {}).get("sha256"): out.append("the pin manifest differs from the one F0 recorded")
    for key, relative in (("workload", WORKLOAD_PATH),):
        if not (root / relative).is_file() or fz.sha256_file(root / relative) != (f0.get(key) or {}).get("sha256"): out.append(f"{relative} differs from F0's")
    golden = (f0.get("goldens") or {}).get("schedule") or {}
    if not (root / SCHEDULE_GOLDEN_PATH).is_file() or fz.sha256_file(root / SCHEDULE_GOLDEN_PATH) != golden.get("sha256"): out.append("the schedule golden differs from F0's")
    seed = f0.get("scheduleSeed")
    if not isinstance(seed, str) or ref.schedule_sha(ref.make_schedule(seed)) != f0.get("scheduleSha256"): out.append("the schedule does not hash to F0's scheduleSha256")
    if isinstance(seed, str) and calibration_order_sha(seed) != f0.get("calibrationOrderSha256"): out.append("the calibration order does not hash to F0's")
    if isinstance(seed, str) and seed in G1E_SEEDS: out.append("F0 carries a G1-E seed")
    if isinstance(seed, str) and f0.get("agentLaunch") != launch.f0_record(seed): out.append("the agent-launch record (stamp sources, plan) differs from the registered one")
    if (f0.get("evidenceRevision"), f0.get("methodRevision"), f0.get("vendors")) != (EVIDENCE_REVISION, METHOD_REVISION, VENDORS): out.append("revision or vendor fields differ from the registered ones")
    if f0.get("sidecarSha256") != (f0.get("sidecar") or {}).get("sha256"): out.append("sidecarSha256 differs from the sidecar record")
    out += toolchain_problems(f0.get("toolchain"))
    ledger_file = Path(ledger) if ledger else root / LEDGER_PATH
    out += approval_problems(f0.get("approval"), ledger_file.read_text(encoding="utf-8") if ledger_file.is_file() else None)
    return out
