"""Bundle assembly (tools-report open question 9): the run's receipts -> the decider's row facts -> the `acr-g3-cost-outcome/1` document, with the section 2.5 variability fields, the section 3
regression since G1-E and the section 7 fields. Derived by code from receipts; nothing is entered by hand. The shell matrix is admitted by the unchanged g1e `summarise.load_matrix` through
`freeze_view(f0)` (the G3 F0 seen in the g1e vocabulary), so every per-trial structural check G1-E made is made again.

Not here: the correctness preconditions (section 4 item 7), locality, maintainability, baseline identity (outside the cost decider, H3); mechanism classes (two reviewers, section 5.3):
every adverse or unresolved row gets a mechanism entry `unexplained` with no reviewers until the manager records them.
"""
from __future__ import annotations

import statistics
from pathlib import Path

from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e.bundle import Bundle
from tests.cli_regression.support.g3 import decider, f1 as f1mod, launch, regression, rows

SHELL_CLASSES = ("stream", "lifecycle", "budget")


def freeze_view(f0: dict) -> dict:
    """The G3 F0 in the g1e freeze vocabulary, only the keys `summarise.load_matrix`, `structure_problems` and `trial_problems` read."""
    return {"scheduleSeed": f0["scheduleSeed"], "evidenceRevision": f0["evidenceRevision"], "methodRevision": f0["methodRevision"], "sidecarSha256": f0["sidecarSha256"],
            "workload": dict(summarise.REGISTERED_WORKLOAD), "toolchain": f0["toolchain"], "vendors": list(f0["vendors"])}


def shell_matrix(bundle: Bundle, f0: dict, bound_sha256: str) -> tuple[dict, dict]:
    """(matrix, registry rows) from the sealed bundle. `bound_sha256` is the digest the receipts are bound to (F1 for the matrix)."""
    view = freeze_view(f0)
    matrix = summarise.load_matrix(bundle, view, bound_sha256)
    return matrix, summarise.build_rows(view, matrix)


def combined_rows(matrix: dict) -> dict[str, dict]:
    """The 6 combined RSS rows (H6): python = backend alone, rust = backend + sidecar of the same trial pair. A missing term makes the pair invalid."""
    invalid = set(matrix["invalid"])
    scalars = {slot: (summarise.trial_scalars(t) if slot not in invalid else {}) for slot, t in matrix["trials"].items()}
    out = {}
    for spec in rows.combined_rss_specs():
        phase = spec["metric"].split(":")[1]
        pairs = []
        for repeat in range(ref.N_PAIRS):
            py = scalars.get(("python", spec["cell"], repeat), {}).get(f"res:{phase}:component:backend:rssMiB")
            rb = scalars.get(("rust-shell", spec["cell"], repeat), {}).get(f"res:{phase}:component:backend:rssMiB")
            rs = scalars.get(("rust-shell", spec["cell"], repeat), {}).get(f"res:{phase}:component:sidecar:rssMiB")
            ok = all(summarise.is_num(v) for v in (py, rb, rs))
            pair = rows.combined_rss_pair(py, rb, rs) if ok else {"python": None, "rust": None}
            pairs.append({"repeat": repeat, **pair, "valid": ok})
        out[spec["id"]] = {"pairs": pairs}
    return out


def reader_rows(reader_record: dict) -> dict[str, dict]:
    return {rid: {"pairs": row["pairs"], "margin": row["margin"]["value"]} for rid, row in reader_record["rows"].items()}


# ---------------------------------------------------------------------------------------------------------------- variability (2.5)
def _sd(values: list[float]) -> float | None:
    """Sample SD, two-pass."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def variability(spec: dict, row: dict, calibration_row: dict | None) -> dict:
    """Report-only: mean and SD of d, of each arm, the band [d(2), d(9)], q_hat, calMedAbs, calP90Abs; the one descriptive flag `rustSd > 2 x pythonSd and rustSd > margin`."""
    valid = [p for p in row["pairs"] if p.get("valid") and ref.finite(p.get("rust")) and (spec["source"] == "rust-value" or ref.finite(p.get("python")))]
    d = [p["rust"] - (0.0 if spec["source"] == "rust-value" else p["python"]) for p in valid]
    py = [p["python"] for p in valid] if spec["source"] != "rust-value" else []
    ru = [p["rust"] for p in valid]
    py_sd, ru_sd = _sd(py), _sd(ru)
    margin = ref.margin_for(spec, statistics.median(py)) if py else spec["margin"]["value"]
    cr = calibration_row or {}
    return {"meanD": (sum(d) / len(d)) if d else None, "sdD": _sd(d), "pythonSd": py_sd, "rustSd": ru_sd, "band": ref.band(d), "qHat": cr.get("qHat"), "calMedAbs": cr.get("calMedAbs"),
            "calP90Abs": cr.get("calP90Abs"), "rustSdFlag": bool(py_sd is not None and ru_sd is not None and ru_sd > 2 * py_sd and ru_sd > margin)}


# ---------------------------------------------------------------------------------------------------------------- the row table
def all_specs(f1: dict) -> dict[str, dict]:
    """Every decision-bearing row's registry-shaped spec (C1 included, flagged `beat`), the new rows with their F1-frozen margins. A reader row's margin is its F1 absolute margin."""
    fixtures = f1["calibration"]["readerMargins"].keys()
    specs = {s["id"]: s for s in ref.REG}
    specs.update({s["id"]: s for s in rows.agent_launch_specs()})        # an uncalibrated launch row keeps its spec with margin None
    specs.update(f1mod.margin_specs(f1))
    for spec in rows.reader_row_specs(list(fixtures)):
        specs[spec["id"]] = {**spec, "margin": ref.A(f1["calibration"]["readerMargins"][spec["fixture"]][spec["metric"]]), "basis": "reader"}
    return specs


def row_table(f1: dict, shell: dict, launch_rows: dict, combined: dict, reader: dict) -> tuple[list[dict], dict | None]:
    """(decision rows for the decider incl. variability, C1 report). A row in `uncalibratedRows` has no margin and enters as `uncalibrated`."""
    specs = all_specs(f1)
    cal = f1["calibration"]
    noisy, uncal = set(cal["noisyRows"]), set(cal["uncalibratedRows"])
    source = {**shell, **launch_rows, **combined, **{rid: {"pairs": r["pairs"]} for rid, r in reader.items()}}
    out, c1 = [], None
    for rid in rows.decision_ids(list(cal["readerMargins"])):
        spec = specs.get(rid)
        row = source.get(rid)
        if spec is None or row is None:
            out.append({"id": rid, "cls": (spec or {}).get("cls", "unknown"), "kind": (spec or {}).get("kind", "parity"), "n": 0, "e": 0, "noisy": False, "uncalibrated": rid in uncal, "missing": True})
            continue
        if rid in uncal and spec.get("margin") is None:
            out.append({"id": rid, "cls": spec["cls"], "kind": spec["kind"], "n": 0, "e": 0, "noisy": False, "uncalibrated": True})
            continue
        counts = ref.row_counts(spec, row, set())
        entry = {"id": rid, "cls": spec["cls"], "kind": spec["kind"], "n": counts["validPairs"], "e": counts["exceedCount"], "noisy": rid in noisy, "uncalibrated": rid in uncal,
                 "medianExcess": counts["medianExcess"], "unit": spec["unit"], "margin": spec["margin"], "variability": variability(spec, row, cal["rows"].get(rid)),
                 "pairs": row["pairs"]}
        if spec["cls"] == "beat":
            valid = [p for p in row["pairs"] if p.get("valid")]
            ds = [p["rust"] - p["python"] for p in valid]
            c1 = {"row": rid, "n": len(valid), "medianD": statistics.median(ds) if ds else None, "band": ref.band(ds), "e": counts["exceedCount"], "notImproved": counts["notImprovedCount"]}
            continue          # report-only (U5): never a decider row
        out.append(entry)
    return out, c1


# ---------------------------------------------------------------------------------------------------------------- flags, regression, document
def matrix_flags(f0: dict, matrix: dict, shell_run: dict, *, artifact_identity: str, quiet_timed_out: bool, reader_fixture_missing: bool, launch_complete: bool) -> tuple[dict, dict]:
    """The non-row facts, derived: (flags for `decider.facts_from_rows`, evidence detail). `matrixComplete` includes the launch chains and a complete run record."""
    trials = matrix["trials"]
    complete = (not matrix["integrity"] and not matrix["missing"] and not matrix["invalid"] and shell_run.get("complete") is True and launch_complete)
    identities = [(t["backend"]["pid"], t["backend"]["start"]) for t in trials.values() if isinstance(t.get("backend"), dict)]
    design = (len(trials) == 40 and len(set(identities)) == len(identities) and ref.schedule_sha(matrix["schedule"]) == f0["scheduleSha256"]
              and all(sum(1 for s in matrix["schedule"] if s["cell"] == c and s["firstArm"] == "python") == 5 for c in ref.CELLS))
    canaries = [{"id": cid, "values": values} for cid, values in summarise.canary_values(freeze_view(f0), matrix).items()]
    independence = ref.independence_status({"independence": {"canaries": canaries}})
    survivors = sorted(summarise.trial_id(*s) for s, t in trials.items() if s[0] == "rust-shell" and summarise.ownership_failure(t))
    breach = sorted(summarise.trial_id(*s) for s, t in trials.items() if summarise.isolation_breach(t))
    flags = {"matrixComplete": complete, "designCompliant": design, "independenceScreen": independence, "artifactIdentity": artifact_identity, "survivorsRustArm": bool(survivors),
             "isolationBreach": bool(breach), "readerFixtureMissing": reader_fixture_missing, "quietGateTimedOut": quiet_timed_out}
    return flags, {"survivors": survivors, "isolation": breach, "invalid": [summarise.trial_id(*s) for s in matrix["invalid"]], "missing": [summarise.trial_id(*s) for s in matrix["missing"]],
                   "matrix": {"integrity": list(matrix["integrity"]), "invalid": [summarise.trial_id(*s) for s in matrix["invalid"]], "problems": {summarise.trial_id(*s): list(p) for s, p in (matrix.get("problems") or {}).items() if p}}}


def g1e_pairs(bundle_dir: Path, manifest_sha256: str) -> dict[str, dict]:
    """The G1-E per-pair data of the sealed bundle (`evidence/e4/r14/bundle`): {row id: {pairs, spec}}, the 49 matrix rows (registry spec) and the reader rows (`reader.json`, absolute margin
    as G1-E froze it). Reading is allowed (not G3 candidate data, section 3 item 2). The receipts are bound to the bundle's F1, whose digest the manifest lists."""
    bundle = Bundle(Path(bundle_dir), manifest_sha256)
    freeze = bundle.read_json("freeze.json")
    matrix = summarise.load_matrix(bundle, freeze, bundle.files["freeze-f1.json"])
    registry = {s["id"]: s for s in ref.REG}
    out = {rid: {"pairs": row["pairs"], "spec": registry[rid]} for rid, row in summarise.build_rows(freeze, matrix).items()}
    if "reader.json" in bundle.files:
        for rid, row in bundle.read_json("reader.json")["rows"].items():
            out[rid] = {"pairs": row["pairs"], "spec": {"id": rid, "source": "paired-delta", "kind": "parity", "margin": ref.A(row["margin"]["value"])}}
    return out


def regression_entries(table: list[dict], g1e: dict[str, dict] | None, *, same_class: bool, python_arm_attributable: bool) -> list[dict]:
    """Section 3 for every decision row. A row absent from G1-E (new families) is `new`. The G1-E data is mandatory: without it the section cannot be derived and this raises."""
    if g1e is None:
        raise ValueError("the G1-E per-pair data is mandatory for the regression section")
    out = []
    for row in table:
        rid = row["id"]
        if rid not in g1e:
            out.append(regression.compare_row(rid, spec=None, g1e_d=None, g3_d=None, g1e_python_median=None, g3_python_median=None, g1e_e=None, g3_e=row.get("e"),
                                              same_host_class=same_class, python_arm_attributable=python_arm_attributable))
            continue
        spec = g1e[rid]["spec"]
        old = [p for p in g1e[rid]["pairs"] if p.get("valid")]
        new = [p for p in row.get("pairs", []) if p.get("valid")]
        d = lambda ps: [p["rust"] - (0.0 if spec["source"] == "rust-value" else p["python"]) for p in ps]
        med = lambda ps: statistics.median(p["python"] for p in ps) if ps and spec["source"] != "rust-value" else None
        old_e = ref.row_counts(spec, {"pairs": g1e[rid]["pairs"]}, set())["exceedCount"]
        out.append(regression.compare_row(rid, spec=spec, g1e_d=d(old), g3_d=d(new) if new else None, g1e_python_median=med(old), g3_python_median=med(new), g1e_e=old_e, g3_e=row.get("e"),
                                          same_host_class=same_class, python_arm_attributable=python_arm_attributable))
    return out


def mechanisms(statuses: list[dict]) -> list[dict]:
    """One entry per adverse or unresolved row, class `unexplained` until two independent reviewers (distinct hash) agree (section 5.3); the class never changes a status."""
    return [{"row": s["id"], "class": "unexplained", "reviewers": [], "statusChanged": False} for s in statuses if s["status"] in ("adverse", "unresolved", "uncalibrated")]


PLATFORMS = [{"platform": "macos-arm64", "runner": "local", "decisionBearing": True, "measured": True, "comparableAcrossRunners": False},
             {"platform": "linux", "runner": "local-vm / hosted", "decisionBearing": False, "measured": False, "comparableAcrossRunners": False},
             {"platform": "windows", "runner": "hosted", "decisionBearing": False, "measured": False, "comparableAcrossRunners": False}]


def build_document(*, f0: dict, f1: dict, table: list[dict], c1: dict | None, flags: dict, source: dict, g1e: dict[str, list] | None, python_arm_attributable: bool) -> dict:
    """The `acr-g3-cost-outcome/1` document from derived inputs only. Rows with `missing` data fail the facts derivation loudly (a class without rows raises)."""
    facts = decider.facts_from_rows([{k: r[k] for k in ("id", "cls", "kind", "n", "e", "noisy", "uncalibrated") if k in r} for r in table], flags)
    statuses = [{"id": r["id"], "cls": r["cls"], "status": ("uncalibrated" if r.get("uncalibrated") else decider.row_status(r)), "n": r["n"], "e": r["e"], "noisy": r["noisy"],
                 **({k: r[k] for k in ("unit", "margin", "variability", "medianExcess", "pairs") if k in r})} for r in table]
    same = f0["hostClass"]["sameAsG1E"]
    doc = decider.build_outcome(facts=facts, source=source, host_class=f0["hostClass"], calibration={"marginFormula": f1["calibration"]["marginFormula"], "derivedMargins": f1["calibration"]["derivedMargins"],
                                "readerMargins": f1["calibration"]["readerMargins"], "noisyRows": f1["calibration"]["noisyRows"], "uncalibratedRows": f1["calibration"]["uncalibratedRows"]},
                                regression=regression_entries(table, g1e, same_class=same, python_arm_attributable=python_arm_attributable), mechanisms=mechanisms(statuses),
                                platforms=PLATFORMS, rows=table, c1=c1)
    doc["rowTable"] = statuses
    return doc
