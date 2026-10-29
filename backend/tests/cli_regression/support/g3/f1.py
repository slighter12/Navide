"""The G3 F1 (`acr-g3-freeze-f1/1`, the `acr-g1e-freeze-f1/2` pattern): noisy and uncalibrated lists, the derived margins and the reader margins, frozen BEFORE any candidate attempt.

F1 = F0's SHA-256 + a re-verification of F0 against the world + the calibration record of `calibrate.build_calibration`. `build_f1` refuses (raises) on any F0 difference or any inconsistency in the
calibration (a list out of registry order, a derived margin below its floor or non-positive, a noisy flag that disagrees with the margin cap, a launch row both derived and uncalibrated). `verify_f1`
is what runs before every matrix attempt and reader run: F1 well-formed, naming this F0, and F0 still verifying.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g3 import calibrate, f0 as f0mod, launch, margins, rows

SCHEMA = "acr-g3-freeze-f1/1"
F1_NAME = "freeze-f1.json"


class F1Error(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def f1_problems(f1: object, f0_sha: str, f0: dict) -> list[str]:
    """Structural and consistency problems of an F1 against the F0 it names (empty = well-formed)."""
    if not isinstance(f1, dict) or f1.get("schema") != SCHEMA:
        return ["not an acr-g3-freeze-f1/1 record"]
    out = []
    if f1.get("f0Sha256") != f0_sha: out.append("F1 names another F0")
    if f1.get("reverified", {}).get("ok") is not True: out.append("F1 does not record a successful re-verification of F0")
    c = f1.get("calibration")
    if not isinstance(c, dict) or c.get("schema") != calibrate.SCHEMA:
        return out + ["F1 carries no acr-g3-calibration/1 record"]
    fixtures = ((f0.get("rowRegistry") or {}).get("readerFixtures") or {}).get("registered") or []
    order = rows.decision_ids(fixtures)
    noisy, uncal, derived = c.get("noisyRows"), c.get("uncalibratedRows"), c.get("derivedMargins")
    for name, listed in (("noisyRows", noisy), ("uncalibratedRows", uncal)):
        if not (isinstance(listed, list) and all(i in order for i in listed) and listed == [i for i in order if i in listed]):
            out.append(f"{name} is not a registry-ordered list of decision-row ids")
    if not (isinstance(derived, dict) and isinstance(noisy, list) and isinstance(uncal, list)):
        return out + ["derivedMargins / lists are malformed"]
    for spec in rows.agent_launch_specs():
        rid = spec["id"]
        d = derived.get(rid)
        if (d is None) == (rid not in uncal):
            out.append(f"{rid}: exactly one of 'derived margin' and 'uncalibrated' must hold")
        if d is None:
            continue
        m, med = d.get("margin"), d.get("median")
        if not (ref.finite(m) and ref.finite(med) and m > 0 and med > 0): out.append(f"{rid}: margin or median is not a positive finite number")
        elif m < spec["floor"]: out.append(f"{rid}: margin below its floor")
        elif d.get("noisy") is not (m > margins.NOISY_CAP * med) or (rid in noisy) != d["noisy"]: out.append(f"{rid}: noisy flag disagrees with the 25 % cap or the noisy list")
    if set(derived) - {s["id"] for s in rows.agent_launch_specs()}: out.append("derivedMargins names a row that is not a new agent-launch row")
    rm = c.get("readerMargins")
    if not (isinstance(rm, dict) and sorted(rm) == sorted(fixtures) and all(sorted(v) == sorted(rows.READER_METRICS) and all(ref.finite(x) and x > 0 for x in v.values()) for v in rm.values())):
        out.append("readerMargins do not cover exactly the registered reader fixtures and metrics")
    if c.get("marginFormula") != margins.FORMULA: out.append("the margin formula differs from the registered one")
    if c.get("launchStampSourcesSha256") != launch.stamp_sources_sha256() or (f0.get("agentLaunch") or {}).get("stampSourcesSha256") != launch.stamp_sources_sha256():
        out.append("the launch stamp sources differ from the ones F0 froze")
    rowset = c.get("rows")
    if not isinstance(rowset, dict) or any(i not in rowset for i in noisy + uncal): out.append("a listed row has no calibration report row")
    return out


def build_f1(root: Path, f0: dict, f0_sha: str, pin_manifest: dict, sidecar: Path | None, calibration: dict, *, verify_f0: Callable[..., list[str]] = f0mod.verify_f0,
             observe: Callable[[], dict] | None = None, ledger: Path | None = None) -> dict:
    """F1 for `calibration`. Refuses when F0 no longer verifies, when the toolchain observation does not match, or when the calibration is inconsistent. Nothing here reads a candidate value."""
    problems = verify_f0(Path(root), f0, pin_manifest, sidecar, ledger=ledger)
    if problems:
        raise F1Error(problems)
    if observe is not None:
        record = observe()
        if not record["match"]:
            raise F1Error([f"toolchain {m['tool']}.{m['field']}: recorded {m['recorded']!r}, observed {m['observed']!r}" for m in record["mismatches"]])
    f1 = {"schema": SCHEMA, "f0Sha256": f0_sha, "reverified": {"ok": True, "items": ["head", "trackedDiff", "sidecar", "documents", "g1ePins", "workload", "schedule", "approval", "agentLaunch"]},
          "calibration": copy.deepcopy(calibration)}
    if problems := f1_problems(f1, f0_sha, f0):
        raise F1Error(problems)
    return f1


def verify_f1(root: Path, f1: dict, f0: dict, f0_sha: str, pin_manifest: dict, sidecar: Path | None, *, verify_f0: Callable[..., list[str]] = f0mod.verify_f0, ledger: Path | None = None) -> list[str]:
    return f1_problems(f1, f0_sha, f0) + verify_f0(Path(root), f0, pin_manifest, sidecar, ledger=ledger)


def margin_specs(f1: dict) -> dict[str, dict]:
    """The frozen margins as registry-shaped specs for the new rows: derived launch rows (`margins.spec_with_margin`) and the combined RSS rows (A(7.5))."""
    derived = f1["calibration"]["derivedMargins"]
    out = {s["id"]: margins.spec_with_margin(s, {"status": "derived", **derived[s["id"]]}) for s in rows.agent_launch_specs() if s["id"] in derived}
    out.update({s["id"]: s for s in rows.combined_rss_specs()})
    return out
