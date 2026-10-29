"""The G3 calibration runner (tools-report open question 8): Python-only chains -> margins, noisy and uncalibrated lists. Pure: it reads chains, never receipts' files, and measures nothing.

Inputs (all Python-only, slot order, None = no valid value in the slot):
* `shell_chains[cell]`: the ten `summarise.trial_scalars` dicts of the shell calibration chain of that cell. Feeds (a) the 49 carried registry rows through the unchanged g1e
  `calibration.rows_calibration` (carried margins, adjacent `q_hat` rule), and (b) the 6 combined RSS rows: the Python value is the backend alone (`res:<phase>:component:backend:rssMiB`), the
  margin is the fixed A(7.5 MiB), the class comes from the same adjacent `q_hat` rule (manager answer 4);
* `launch_chains[(vendor, cell)]`: the ten launch times; margin from the frozen formula (`margins.derive_margin`, floor 30 ms), noisy iff `m > 0.25 x median`, uncalibrated iff fewer than 9 valid
  adjacent pairs;
* `reader_runs[fixture]`: the >= 10 Python-only reader runs in run order. The margins are the carried rule (`calibration.reader_margins`: m1/m2 +10 % of the median, m3 1.0 MiB). Reader rows
  are not classified (manager answer 1): never `noisy`, never `uncalibrated`; they take their status from their E count.
Lists come in decision-registry order (`rows.decision_ids`). The output is what F1 freezes.
"""
from __future__ import annotations

from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g3 import launch, margins, rows

SCHEMA = "acr-g3-calibration/1"
READER_CLASS = "clear"        # the fixed, non-noisy class of every reader row (no q-hat)


def _classify_chain(chain: list, spec: dict) -> dict:
    return cal.row_calibration(chain, spec)


def combined_rss_calibration(shell_chains: dict[int, list]) -> dict[str, dict]:
    out = {}
    for spec in rows.combined_rss_specs():
        backend_metric = f"res:{spec['metric'].split(':')[1]}:component:backend:rssMiB"
        proxy = {**spec, "metric": backend_metric}
        out[spec["id"]] = _classify_chain(shell_chains.get(spec["cell"], []), proxy)
    return out


def launch_calibration(launch_chains: dict[tuple[str, int], list]) -> tuple[dict[str, dict], dict[str, dict]]:
    """({row id: report row}, {row id: derivation}) for the four agent-launch rows."""
    report, derived = {}, {}
    for spec in rows.agent_launch_specs():
        values = launch_chains.get((spec["vendor"], spec["cell"]), [])
        derivation = margins.derive_margin(values, spec["floor"])
        derived[spec["id"]] = derivation
        klass = "uncalibrated" if derivation["status"] != "derived" else ("noisy" if derivation["noisy"] else "clear")
        row = {"values": sum(1 for v in cal.cal_values(values) if ref.finite(v)), "validPairs": derivation["validPairs"], "calValues": cal.cal_values(values), "class": klass,
               "marginAtMedian": derivation["margin"], "calP90Abs": derivation["calP90Abs"], "median": derivation["median"]}
        if klass == "uncalibrated":
            row["why"] = f"only {derivation['validPairs']} valid adjacent pairs (< {cal.MIN_VALID_PAIRS}) or a non-positive median"
        report[spec["id"]] = row
    return report, derived


def reader_calibration(reader_runs: dict[str, list[dict]], fixtures: list[str]) -> tuple[dict[str, dict], dict[str, dict[str, float]]]:
    """(report rows, margins) for the registered reader fixtures. Fewer than 10 runs or an m0 failure is an error (the carried rule).

    Reader rows are NOT classified (manager answer 1, 2026-10-29): G1-E carried them unchanged, they are never `noisy` and never `uncalibrated` by the q-hat rule, and take
    pass / unresolved / adverse from their E count. Each report row therefore holds a fixed non-noisy class and the run values, no q-hat."""
    margin_table = cal.reader_margins({fx: reader_runs.get(fx, []) for fx in fixtures})
    pick = {"m1_wall_ms": lambda r: r["m1_wall_s"] * 1000.0, "m2_cpu_s": lambda r: r["m2_cpu_s"], "m3_peak_rss_mib": lambda r: r["m3_peak_rss_increase_bytes"] / 1048576}
    report = {}
    for spec in rows.reader_row_specs(fixtures):
        metric, fixture = spec["metric"], spec["fixture"]
        values = [pick[metric](run) for run in reader_runs[fixture]]
        report[spec["id"]] = {"values": len(values), "calValues": values, "class": READER_CLASS, "classified": False, "margin": margin_table[fixture][metric]}
    return report, margin_table


def build_calibration(shell_chains: dict[int, list], launch_chains: dict[tuple[str, int], list], reader_runs: dict[str, list[dict]], fixtures: list[str]) -> dict:
    """The calibration record F1 freezes."""
    registry = cal.rows_calibration(shell_chains)
    combined = combined_rss_calibration(shell_chains)
    launch_report, derived = launch_calibration(launch_chains)
    reader_report, reader_margin_table = reader_calibration(reader_runs, fixtures)
    report = {**registry["rows"], **launch_report, **combined, **reader_report}
    order = rows.decision_ids(fixtures)
    classes = {rid: report[rid]["class"] for rid in order if rid in report}
    missing = [rid for rid in order if rid not in report and rid in {s["id"] for s in ref.REG if s["source"] == "paired-delta"}]
    if missing:
        raise ValueError(f"calibration lacks paired rows {missing}")
    return {"schema": SCHEMA, "rows": report,
            "noisyRows": [rid for rid in order if classes.get(rid) == "noisy"],
            "uncalibratedRows": [rid for rid in order if classes.get(rid) == "uncalibrated"],
            "derivedMargins": {rid: {k: d[k] for k in ("margin", "median", "calP90Abs", "preFloor", "floorApplied", "noisy", "validPairs")} for rid, d in derived.items() if d["status"] == "derived"},
            "readerMargins": reader_margin_table, "marginFormula": margins.FORMULA, "launchStampSourcesSha256": launch.stamp_sources_sha256()}
