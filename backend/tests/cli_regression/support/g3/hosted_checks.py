"""Per-check verdicts of a hosted rehearsal (round 3 and review-fix round 1, manager decision on the Windows row set).

The rehearsal's own verdict (`rehearsal_verdict.admission`, written to `<out>/rehearsal-verdict.json` by the unchanged `rehearsal-verdict` step) re-admits every receipt of every attempt class through the
unchanged validators, including the byte-ledger and receipt checks of the shell attempts. This module only renames that finding into named checks with one word each: `accepted`, `rejected` or `not-run`.
No timing, CPU or memory value and no receipt text is copied: a name and a verdict.

* the broad receipt-class checks come straight from the admission block;
* the seam checks (`byte-ledger`, `process-identity`, `holds`) are `rejected` when a recorded problem of any receipt class carries that seam's fixed phrase (the phrases of the unchanged validators; the
  problem text itself is only matched, never copied);
* the hook checks (`hook-delivery-<kind>`, `pipe-connection-counters`) read the installed markers the children wrote (`hosted_markers`): a kind that never wrote one did not load, and is named.
`portVerified` is true iff every check of the platform is `accepted` AND the verdict file's own `passed` flag is true; if it is false after the Windows fix rounds the platform is "Not measured (port not
verified)" (the label is the manager's, not computed here).

    hosted_checks VERDICT_JSON OUT_JSON SYSTEM [MARKERS_DIR]     VERDICT_JSON may be absent (the run died before the verdict step): the receipt checks are then `not-run`; the marker checks need MARKERS_DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.cli_regression.support.g3 import hosted_markers

ACCEPTED, REJECTED, NOT_RUN = "accepted", "rejected", "not-run"
SCHEMA = "acr-g3h-rehearsal-checks/2"
SHELL = "shell-receipt-structure-and-byte-ledger-checks"
WARM = "warmup-and-calibration-receipt-checks"
LAUNCH_CAL = "launch-calibration-receipt-checks"
LAUNCH = "launch-matrix-receipt-checks"
PACKAGED = "packaged-receipt-checks"
READER = "reader-pair-checks"
BYTE_LEDGER = "byte-ledger-checks"
PS_IDENTITY = "process-identity-checks"
HOLDS = "holds-checks"
PIPE_CONNECTION = "pipe-connection-counters"
HOOK_PREFIX = "hook-delivery-"
SEAM_PHRASES = {
    BYTE_LEDGER: ("ledger", "peer slave", "delta read_total", "counter went backwards", "drop-oldest", "FINAL", "start marker", "window id sets", "log text identity", "log file irregular",
                  "terminal.log_sent", "origin counters", "pre-origin"),
    PS_IDENTITY: ("processCount", "lstart", "ambient host numbers", "pid/start", "backend start"),
    HOLDS: ("log final check", "sidecar_live", "inventory"),
}
PIPE_PHRASES = ("python_pty_spawns", "Python spawned", "counters")
# marker kinds that are a hook-delivery check; `counters-connection` is the pipe-connection check instead
HOOK_KINDS = {"Linux": ("python-seams", "reader-rss-bytes"), "Windows": ("python-seams", "termios-shim", "counters-server", "node-preload")}
# a hook check that is about one process is satisfied only by a marker of that process's role: the measured peer is `g1e_peer.py`, not any Python step that also loaded the shim
HOOK_ROLES = {"termios-shim": "g1e_peer"}


def receipt_names(system: str) -> list[str]:
    return [SHELL, WARM, LAUNCH_CAL, LAUNCH, PACKAGED] + ([READER] if system == "Linux" else []) + [BYTE_LEDGER, PS_IDENTITY, HOLDS]


def names(system: str) -> list[str]:
    return receipt_names(system) + [HOOK_PREFIX + kind for kind in HOOK_KINDS[system]] + ([PIPE_CONNECTION] if system == "Windows" else [])


def texts(admission: dict, defects: list) -> list[str]:
    """Every recorded problem string of the admission block and the verdict's defects (matched, never copied)."""
    out = [str(d) for d in defects]
    shell, warm = admission["shellMatrix"], admission["warmupAndCalibrationReceipts"]
    for value in list(shell["problems"].values()) + list(warm["withProblems"].values()):
        out += [str(v) for v in (value if isinstance(value, list) else [value])]
    out += [str(v) for v in shell["integrity"]]
    return out


def has_phrase(found: list[str], phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for text in found for phrase in phrases)


def verdicts(admission: dict, system: str, defects: list | None = None) -> dict[str, str]:
    """Check name -> accepted | rejected, from the public `admission` block of the rehearsal verdict (the receipt-class and seam checks)."""
    shell, warm, launch = admission["shellMatrix"], admission["warmupAndCalibrationReceipts"], admission["launchMatrix"]
    found = texts(admission, defects or [])
    ok = {SHELL: not shell["integrity"] and not shell["invalid"] and not shell["problems"] and shell["files"] == shell["admitted"] and shell["admitted"] > 0,
          WARM: not warm["withProblems"] and not admission["calibrationShellInvalid"],
          LAUNCH_CAL: not admission["calibrationLaunchInvalid"],
          LAUNCH: not launch["invalid"] and launch["admitted"] > 0,
          PACKAGED: not admission["packaged"]["notPassed"] and admission["packaged"]["receipts"] > 0}
    if system == "Linux":
        ok[READER] = not admission["reader"]["rowsWithInvalidPair"]
    for name, phrases in SEAM_PHRASES.items():
        ok[name] = not has_phrase(found, phrases)
    return {name: ACCEPTED if ok[name] else REJECTED for name in receipt_names(system)}


def delivered(markers: dict[str, set[str]], kind: str) -> bool:
    """A marker of `kind` exists, and for a process-specific kind (`HOOK_ROLES`) one written by a process of that role."""
    roles = markers.get(kind, set())
    return HOOK_ROLES[kind] in roles if kind in HOOK_ROLES else bool(roles)


def marker_verdicts(system: str, markers: dict[str, set[str]] | None, found: list[str]) -> dict[str, str]:
    """Hook delivery and the pipe connection, from the installed markers (kind -> roles). No marker directory = `not-run`."""
    out = {}
    for kind in HOOK_KINDS[system]:
        out[HOOK_PREFIX + kind] = NOT_RUN if markers is None else (ACCEPTED if delivered(markers, kind) else REJECTED)
    if system == "Windows":
        out[PIPE_CONNECTION] = NOT_RUN if markers is None else (ACCEPTED if delivered(markers, "counters-connection") and not has_phrase(found, PIPE_PHRASES) else REJECTED)
    return out


def summary(verdict_doc: dict | None, system: str, markers_dir: str | Path | None = None) -> dict:
    markers = hosted_markers.roles_present(markers_dir) if markers_dir is not None else None
    if verdict_doc is None or "admission" not in verdict_doc:
        found = {name: NOT_RUN for name in receipt_names(system)}
        recorded: list[str] = []
        passed = None
    else:
        defects = verdict_doc.get("defects") or []
        found = verdicts(verdict_doc["admission"], system, defects)
        recorded = texts(verdict_doc["admission"], defects)
        passed = verdict_doc.get("passed")
    found.update(marker_verdicts(system, markers, recorded))
    return {"schema": SCHEMA, "notEvidence": True, "system": system, "checks": [{"name": name, "verdict": found[name]} for name in names(system)],
            "verdictPassed": passed if isinstance(passed, bool) else None, "portVerified": all(v == ACCEPTED for v in found.values()) and passed is True}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 64
    source = Path(argv[0])
    doc = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else None
    result = summary(doc, argv[2], argv[3] if len(argv) == 4 else None)
    Path(argv[1]).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
