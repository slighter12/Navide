"""The pre-F0 plumbing smoke (manager answer 7): are the TS runner and `launch_driver.py` wired right, before the one registered run starts. NOT EVIDENCE.

One launch attempt per arm and per vendor (claude, aider) and one plain shell-pane attempt per arm, all through `tests/cli/g3LaunchAttempt.ts` in smoke mode (review-1 finding 1): that path takes
no timestamp, starts no resource sampler and never calls the unchanged `g1eAttempt.runAttempt` (which reads `resources.jsonl` and computes latencies). The runner writes one entry per attempt holding only
pass/fail and the arm-identity facts. This module checks those entries: each runner completed, and the arm-identity receipts are right (Rust arm: `owned` with `argv_source: rust` and the `ready`
digest equal to the sidecar's; Python arm: none of them). The verdict is `{"notEvidence": true, "passed": bool, "entries": [...]}`. Nothing here enters calibration, F1, the matrix or the report's
numbers; a failed smoke is fixed and repeated into a fresh directory (it is not part of the registered run, so section 5.3 does not apply).
What the smoke does NOT exercise: `g1eAttempt.runAttempt` and its sampler (unchanged G1-E code, run in G1-E), which first runs in the registered run.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "acr-g3-smoke/1"
VERDICT_NAME = "smoke-verdict.json"
ARMS = ("python", "rust-shell")
VENDORS = ("claude", "aider")
ENTRY_KEYS = {"kind", "mode", "vendor", "passed", "evidenceValid", "owned", "argvSource", "readySidecarSha256", "runtimeLost", "sidecarRestarts", "survivors"}
EXPECTED = sorted([("launch", mode, vendor) for mode in ARMS for vendor in VENDORS] + [("shell", mode, "shell") for mode in ARMS], key=str)


def entry_problems(entry: object, sidecar_sha256: str) -> list[str]:
    """Problems of one smoke entry (empty = the runner completed and the arm identity is right)."""
    if not isinstance(entry, dict) or entry.get("kind") not in ("launch", "shell") or entry.get("mode") not in ARMS:
        return ["not a launch or shell smoke entry"]
    if set(entry) != ENTRY_KEYS:
        return [f"the entry carries other keys than {sorted(ENTRY_KEYS)} (a smoke entry holds no timing, CPU or memory value)"]
    out = []
    if entry["passed"] is not True: out.append("the runner did not complete the attempt")
    if entry["evidenceValid"] is not True: out.append("the attempt is not evidence-valid")
    if entry["mode"] == "rust-shell":
        if entry["owned"] is not True or entry["argvSource"] != "rust": out.append("the Rust arm has no `owned` receipt with argv_source rust (hidden fallback)")
        if entry["readySidecarSha256"] != sidecar_sha256: out.append("the `ready` receipt digest is not the sidecar's")
    elif entry["owned"] is not False or entry["argvSource"] is not None or entry["readySidecarSha256"] is not None:
        out.append("the Python arm shows a Rust runtime receipt")
    if entry["runtimeLost"] is not False or entry["sidecarRestarts"] != 0 or entry["survivors"] != []:
        out.append("the attempt lost its runtime, restarted the sidecar or left a survivor")
    return out


def verify(entries: list, sidecar_sha256: str) -> dict:
    """The smoke verdict: every expected (kind, arm, vendor) exactly once, each entry clean. Carries pass/fail and problems only."""
    seen = sorted([(e.get("kind"), e.get("mode"), e.get("vendor")) for e in entries if isinstance(e, dict)], key=str)
    problems = [] if seen == EXPECTED else [f"the smoke must hold exactly one entry per {EXPECTED}, not {seen}"]
    rows = []
    for entry in entries:
        found = entry_problems(entry, sidecar_sha256)
        problems += found
        rows.append({"kind": entry.get("kind"), "mode": entry.get("mode"), "vendor": entry.get("vendor"), "passed": not found, "problems": found} if isinstance(entry, dict) else {"passed": False, "problems": found})
    return {"schema": SCHEMA, "notEvidence": True, "passed": not problems, "problems": problems, "entries": rows}


def verify_dir(directory: Path, sidecar_sha256: str) -> dict:
    """Read the runner's `smoke-entries.json` from `directory`, write `smoke-verdict.json` beside it."""
    directory = Path(directory)
    entries = json.loads((directory / "smoke-entries.json").read_text(encoding="utf-8"))
    verdict = verify(entries if isinstance(entries, list) else [], sidecar_sha256)
    (directory / VERDICT_NAME).write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return verdict
