"""Stop facts of an earlier run (E2 v13 V7 item 5): irrevocable across a re-freeze.

Every stop fact derivable from an earlier run's SEALED records is carried into the decision of every restarted round: S1 and S2 from any receipt of the run (matrix trials and
Python-only calibration attempts), S3 from its correctness rows, and S4-S6 from any row whose ten pairs that run completed (a row with fewer than ten valid pairs is `unresolved`, never
`adverse`). The definitions are v10 section 8's own (S3 after the v10 section 7 attribution, which `ref.reduce_correctness` applies). Deriving them is mechanical and is not an
inspection of a candidate result in the sense of V7 item 4 (ii): nothing here prints a measured value, a pair or a statistic; the result is only the list of stop-rule codes.

An earlier run that is unreadable or whose manifest does not verify cannot be ignored: `EvidenceInvalid` (no outcome), because dropping a run would drop its stop facts.
A run that was stopped by the toolchain check and holds no stop fact carries nothing (its `stopFacts` is empty) and so has no effect on the outcome.
"""
from __future__ import annotations

from pathlib import Path

from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e.bundle import Bundle, BundleError


def row_statuses(freeze: dict, matrix: dict) -> dict[str, str]:
    """Registry row id -> status (`pass`/`improved`/`adverse`/`unresolved`/`not_improved`) from the admitted, evidence-valid pairs of ONE run (the same counting the decider uses)."""
    built = summarise.build_rows(freeze, matrix)
    doc = {"matrix": {"evidenceInvalidTrials": [summarise.trial_id(*s) for s in matrix["invalid"]], "missingSlots": [summarise.trial_id(*s) for s in matrix["missing"]]}}
    bad = ref.bad_slot_pairs(doc)
    out = {}
    for spec in ref.REG:
        counts = ref.row_counts(spec, built[spec["id"]], bad[spec["cell"]])
        out[spec["id"]] = ref.status_from_counts(spec["kind"], counts["validPairs"], counts["exceedCount"], counts.get("notImprovedCount"))
    return out


def stop_codes_of_rows(statuses: dict[str, str]) -> set[str]:
    cls = lambda c: [statuses[r["id"]] for r in ref.REG if r["cls"] in c]
    held = set()
    if ref.aggregate(cls({"stream"})) == "adverse":
        held.add("S4")
    if ref.aggregate(cls({"lifecycle", "budget"})) == "adverse":
        held.add("S5")
    if cls({"beat"})[0] == "adverse":
        held.add("S6")
    return held


def receipt_stop_facts(bundle: Bundle) -> dict[str, list[str]]:
    """S1 and S2 from EVERY trial-like record of a bundle (v13 V10 5(a): "S1 and S2 from all records of all runs"): the matrix receipts under `trials/`, whether or not they were admitted, the
    Python-only attempts under `calibration/` and the discarded warm-ups (`calibration/warmup-*`, `warmup/`: v14-r2 V19, a discarded attempt still shows a survivor or a breach). Used for the current run and for a carried run alike. A file that cannot be read as a trial receipt (not JSON, not an object, another schema) cannot
    show a fact and contributes none. Returns {"S1": [receipt names], "S2": [receipt names]}."""
    found: dict[str, list[str]] = {"S1": [], "S2": []}
    for name in bundle.names():
        if not (name.startswith(("trials/", "calibration/", "warmup/")) and name.endswith(".json")):
            continue
        try:
            trial = bundle.read_json(name)
        except BundleError:
            continue
        if not isinstance(trial, dict) or trial.get("schema") != "acr-g1e-trial/1":
            continue
        if trial.get("selectedRuntime") == "rust-shell" and summarise.ownership_failure(trial):
            found["S1"].append(name)
        if summarise.isolation_breach_recorded(trial):
            found["S2"].append(name)
    return found


def run_facts(bundle_dir: Path | str, manifest_sha256: str) -> dict:
    """{manifestSha256, kind, stopReason, stopFacts} of one earlier run, derived from its sealed bundle."""
    from tests.cli_regression.support.g1e import decider
    try:
        bundle = Bundle(Path(bundle_dir), manifest_sha256)
        whole = bundle.problems()
    except BundleError as error:
        raise decider.EvidenceInvalid([f"earlier run: {error}"]) from None
    if whole:
        raise decider.EvidenceInvalid([f"earlier run {manifest_sha256[:12]}: {m}" for m in whole])
    run = decider._need(bundle, summarise.RUN_NAME)
    freeze = decider._need(bundle, summarise.FREEZE_NAME)
    problems = summarise.run_problems(run) + summarise.freeze_problems(freeze, bundle.digest(summarise.FREEZE_NAME))
    if problems:
        raise decider.EvidenceInvalid([f"earlier run {manifest_sha256[:12]}: {m}" for m in problems])
    bind_sha = bundle.digest(summarise.F1_NAME) if bundle.exists(summarise.F1_NAME) else bundle.digest(summarise.FREEZE_NAME)
    held: set[str] = {code for code, names in receipt_stop_facts(bundle).items() if names}
    has_trials = any(name.startswith("trials/") and not name.endswith(summarise.RESOURCES_SUFFIX) for name in bundle.names())
    reader_required = freeze["options"]["readerRequired"]
    if bundle.exists("gates.json"):
        rows, _notes = decider.build_correctness(bundle, freeze, reader_required)
        if ref.reduce_correctness(rows, reader_required) == "failed_rust_unbounded":
            held.add("S3")
    if has_trials:
        held |= stop_codes_of_rows(row_statuses(freeze, summarise.load_matrix(bundle, freeze, bind_sha)))
    return {"manifestSha256": manifest_sha256, "kind": run["kind"], "stopReason": run["stopReason"], "stopFacts": [c for c in ref.STOP_RULES if c in held]}
