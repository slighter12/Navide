"""Python-only calibration (E2 v14-r2 V19, `rows+reader`) and the reader benchmark record (v10 section 10).

Calibration never changes n, a margin or a row. It runs as ONE discarded warm-up (in the first chain's cell) and two back-to-back chains of ten scored Python-only attempts, one chain per cell,
the chain order seeded from the schedule seed; every scored attempt carries its frozen `slot` 0..9 in its chain. For every PAIRED row the adjacent estimator is computed over the chain
x_0..x_9 of that row's cell, IN SLOT ORDER (never file order or wall-clock): with `e_k = [x_k - x_{k+1} > m(x_{k+1})]` and `e'_k = [x_{k+1} - x_k > m(x_k)]` (strict `>`) over the valid adjacent
pairs, `q_hat = events / (2 P)`; the row is UNCALIBRATED if fewer than 9 adjacent pairs are valid and NOISY if `q_hat > 0.0833` (at most one event of eighteen is clear). The old 90-pair figure
is kept as a report-only `qHatAllPairs`. The lists `noisyRows` and `uncalibratedRows` come in registry order and are frozen in F1; the decider recomputes them. One-arm budget rows have no Python
value and are not calibrated. The warm-up enters no list, pair, count or estimate.
Reader benchmark: the absolute margins of m1 and m2 are +10 % of the median of the >= 10 Python-only runs per fixture, frozen as numbers; m3 is +1.0 MiB (section 10).
"""
from __future__ import annotations

import statistics
from typing import Any

from tests.cli_regression.support.g1e import reference as ref

P_STAR = 0.0833
MIN_VALID_PAIRS = 9            # v14-r2 V19: fewer valid adjacent pairs than this and the row cannot be estimated
CHAIN_LENGTH = 10
READER_MIN_RUNS = 10


def margin_for(spec: dict, python_value: float) -> float:
    return ref.margin_for(spec, python_value)


def _valid(x: Any) -> bool:
    return ref.finite(x)


def adjacent_events(values: list, spec: dict) -> tuple[int, int]:
    """(events, P) over the chain `values` (slot order; None = no valid value in that slot): an adjacent pair is valid iff both slots hold a finite value; each valid pair contributes the two
    oriented events (strict `>`, the margin of the value the difference is measured to), so `q_hat = events / (2 P)`."""
    events = pairs = 0
    for a, b in zip(values, values[1:]):
        if _valid(a) and _valid(b):
            pairs += 1
            events += (a - b > margin_for(spec, b)) + (b - a > margin_for(spec, a))
    return events, pairs


def q_hat(values: list, spec: dict) -> float | None:
    """The adjacent estimator, or None when fewer than `MIN_VALID_PAIRS` adjacent pairs are valid."""
    events, pairs = adjacent_events(values, spec)
    return events / (2 * pairs) if pairs >= MIN_VALID_PAIRS else None


def q_hat_all_pairs(values: list, spec: dict) -> float | None:
    """The v10 figure: the fraction of the ordered pairs (i != j) of the valid values with x_i - x_j > margin(x_j). REPORT-ONLY since v14-r2 (a drift ramp inflates it); never classifies."""
    valid = [v for v in values if _valid(v)]
    n = len(valid)
    if n < 2:
        return None
    return sum(1 for i in range(n) for j in range(n) if i != j and valid[i] - valid[j] > margin_for(spec, valid[j])) / (n * (n - 1))


def cal_values(chain: list) -> list:
    """The chain as ten slots (the invalid ones None)."""
    padded = list(chain)[:CHAIN_LENGTH] + [None] * (CHAIN_LENGTH - len(chain))
    return [v if _valid(v) else None for v in padded]


def cal_abs_diffs(values: list) -> list[float]:
    return [abs(b - a) for a, b in zip(values, values[1:]) if _valid(a) and _valid(b)]


def cal_med_abs(values: list) -> float | None:
    """Median of the valid adjacent |differences|."""
    diffs = cal_abs_diffs(values)
    return statistics.median(diffs) if diffs else None


def cal_p90_abs(diffs: list[float]) -> float | None:
    """Nearest rank of the ascending list of `diffs` (valid adjacent |differences|): the element of rank ceil(0.9 k)."""
    ordered = sorted(diffs)
    return ordered[(9 * len(ordered) + 9) // 10 - 1] if ordered else None       # ceil(0.9 k) in integers


def margin_at_median(values: list, spec: dict) -> float | None:
    """The row's margin at the median of its valid chain values."""
    valid = [v for v in values if _valid(v)]
    return margin_for(spec, statistics.median(valid)) if valid else None


def paired_specs() -> list[dict]:
    return [spec for spec in ref.REG if spec["source"] == "paired-delta"]


def row_calibration(chain: list, spec: dict) -> dict:
    """One row's calibration report from its cell's chain (`chain[slot]` = the scalar dict of the attempt with that slot, or None)."""
    values = cal_values([None if t is None else t.get(spec["metric"]) for t in chain])
    events, pairs = adjacent_events(values, spec)
    q = q_hat(values, spec)
    klass = "uncalibrated" if q is None else ("noisy" if q > P_STAR else "clear")
    row = {"values": sum(1 for v in values if _valid(v)), "validPairs": pairs, "qHat": q, "qHatAllPairs": q_hat_all_pairs(values, spec), "calValues": values, "calMedAbs": cal_med_abs(values),
           "calP90Abs": cal_p90_abs(cal_abs_diffs(values)), "marginAtMedian": margin_at_median(values, spec), "class": klass}
    if klass == "uncalibrated":
        row["why"] = f"only {pairs} valid adjacent pairs (< {MIN_VALID_PAIRS})"
    return row


def rows_calibration(chains: dict[int, list]) -> dict:
    """`chains[cell]` = the cell's scored attempts INDEXED BY SLOT (`summarise.trial_scalars` dicts, None for a slot without a valid attempt). Returns {rows, noisyRows, uncalibratedRows}, both
    lists in registry order."""
    report, noisy, uncalibrated = {}, [], []
    for spec in paired_specs():
        row = report[spec["id"]] = row_calibration(chains.get(spec["cell"], []), spec)
        if row["class"] == "noisy":
            noisy.append(spec["id"])
        elif row["class"] == "uncalibrated":
            uncalibrated.append(spec["id"])
    return {"rows": report, "noisyRows": noisy, "uncalibratedRows": uncalibrated}


def chain_cells(seed: str) -> list[int]:
    """The cell order of the two calibration chains (v14-r2 V19): the schedule's seeded shuffle, label `calibration`."""
    return ref._shuffle([1, 4], seed, "calibration")


def calibration_order(seed: str) -> list[dict]:
    """The execution order of the Python-only calibration: the discarded warm-up first (in the first chain's cell), then chain 0 (slot 0..9 in its cell) and chain 1, back to back. The
    TypeScript runner reproduces this list (`calibrationOrder` in tests/cli/g1eSchedule.ts; the golden file proves the equality)."""
    cells = chain_cells(seed)
    order = [{"cell": cells[0], "chain": 0, "slot": None, "k": None, "warmup": True}]
    for chain, cell in enumerate(cells):
        order += [{"cell": cell, "chain": chain, "slot": slot, "k": slot, "warmup": False} for slot in range(CHAIN_LENGTH)]
    return order


def matrix_warmups() -> list[dict]:
    """The two discarded matrix warm-ups (v14-r2 V19): cell 4, Python then Rust shell, before the first scheduled pair."""
    return [{"mode": "python", "cell": 4, "index": 0}, {"mode": "rust-shell", "cell": 4, "index": 0}]


READER_UNITS = {"m1_wall_ms": "ms", "m2_cpu_s": "s", "m3_peak_rss_mib": "MiB"}


def reader_margins(python_runs: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """Per fixture: absolute margins frozen from the Python-only runs (E2 10): m1, m2 = +10 % of the median; m3 = +1.0 MiB. Fewer than 10 runs is an error."""
    out = {}
    for fixture, runs in python_runs.items():
        if len(runs) < READER_MIN_RUNS or not all(r.get("m0") is True for r in runs):
            raise ValueError(f"{fixture}: reader calibration needs >= {READER_MIN_RUNS} runs with m0 true")
        out[fixture] = {"m1_wall_ms": 0.10 * statistics.median(r["m1_wall_s"] for r in runs) * 1000.0,
                        "m2_cpu_s": 0.10 * statistics.median(r["m2_cpu_s"] for r in runs), "m3_peak_rss_mib": 1.0}
    return out


def reader_record(fixtures: list[dict], golden: dict, margins_ref: dict, margins: dict[str, dict[str, float]], calibration_runs: int, pairs: dict[str, list[dict]]) -> dict:
    """The `reader.json` content: rows carry only id, unit, margin and the per-pair values (counts and statuses are derived by the decider)."""
    rows: dict[str, Any] = {}
    m0 = {fx: {"python": True, "rust": True} for fx in ("claude", "opencode")}
    for fixture in ("claude", "opencode"):
        for pair in pairs[fixture]:
            m0[fixture]["python"] = m0[fixture]["python"] and bool(pair["python"]["m0"])
            m0[fixture]["rust"] = m0[fixture]["rust"] and bool(pair["rust"]["m0"])
        for metric, unit in READER_UNITS.items():
            def pick(run):
                return {"m1_wall_ms": run["m1_wall_s"] * 1000.0, "m2_cpu_s": run["m2_cpu_s"], "m3_peak_rss_mib": run["m3_peak_rss_increase_bytes"] / 1048576}[metric]
            rid = f"reader|{metric}|{fixture}"
            rows[rid] = {"id": rid, "unit": unit, "margin": {"mode": "absolute", "value": margins[fixture][metric], "frozenIn": margins_ref},
                         "pairs": [{"repeat": i, "python": pick(p["python"]), "rust": pick(p["rust"]), "valid": bool(p["python"]["m0"] and p["rust"]["m0"])} for i, p in enumerate(pairs[fixture])]}
    runs = min(len(pairs["claude"]), len(pairs["opencode"]))
    return {"fixtures": fixtures, "goldenEvents": golden, "calibrationRuns": calibration_runs, "runs": runs, "findings": [], "m0": m0, "rows": rows}
