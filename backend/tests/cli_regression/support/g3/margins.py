"""The frozen margin formula for rows with no G1-E basis (G3 proposal section 2.3, U1).

`m = max(0.10 x median(Python calibration values), calP90Abs)`, then the row's floor (`m >= floor`), and the row is `noisy` iff `m > 0.25 x median`.
`calP90Abs` is the nearest-rank 90th percentile of the valid adjacent absolute differences (g1e `calibration.cal_p90_abs`). No candidate value enters. The floors are
30 ms for the agent-launch rows, 0.10 CPU pt, 1.0 MiB RSS; a row family without a floor passes `floor=0.0`.

A chain with fewer than `MIN_VALID_PAIRS` valid adjacent pairs, or a non-positive median, is `uncalibrated`: no margin is derived (the decider reads it as an uncalibrated row).
"""
from __future__ import annotations

import statistics

from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import reference as ref

SHARE = 0.10
NOISY_CAP = 0.25
FLOORS = {"agentLaunchMs": 30.0, "cpuPt": 0.10, "rssMiB": 1.0, "none": 0.0}
FORMULA = {"rule": "max(0.10*median, calP90Abs)", "noisyCap": NOISY_CAP, "floors": dict(FLOORS)}


def derive_margin(values: list, floor: float = 0.0) -> dict:
    """`values` = the Python-only calibration chain of one row, in slot order (None = no valid value). Returns {status, margin, noisy, median, calP90Abs, preFloor, floorApplied, validPairs}.
    `status` is `derived` or `uncalibrated`."""
    slots = cal.cal_values(values)
    diffs = cal.cal_abs_diffs(slots)
    valid = [v for v in slots if ref.finite(v)]
    if len(diffs) < cal.MIN_VALID_PAIRS or not valid or statistics.median(valid) <= 0:
        return {"status": "uncalibrated", "margin": None, "noisy": None, "median": None, "calP90Abs": None, "preFloor": None, "floorApplied": False, "validPairs": len(diffs)}
    median = statistics.median(valid)
    p90 = cal.cal_p90_abs(diffs)
    pre_floor = max(SHARE * median, p90)
    margin = max(pre_floor, floor)
    return {"status": "derived", "margin": margin, "noisy": margin > NOISY_CAP * median, "median": median, "calP90Abs": p90, "preFloor": pre_floor, "floorApplied": margin > pre_floor,
            "validPairs": len(diffs)}


def derive_all(chains: dict[str, list], floors: dict[str, float]) -> dict[str, dict]:
    """{row id: derive_margin} for every row of `chains`; a row id absent from `floors` has no floor."""
    return {rid: derive_margin(chain, floors.get(rid, 0.0)) for rid, chain in chains.items()}


def spec_with_margin(spec: dict, derived: dict) -> dict:
    """A registry-shaped row spec whose margin is the derived absolute one (frozen in F1); refuses an uncalibrated derivation."""
    if derived["status"] != "derived":
        raise ValueError(f"{spec['id']}: no margin can be derived from an uncalibrated chain")
    return {**spec, "margin": ref.A(derived["margin"]), "basis": "G3-formula"}
