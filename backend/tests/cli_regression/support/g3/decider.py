"""The G3 cost decider (proposal sections 4, 5, 7; schema `acr-g3-cost-outcome/1`, table `prereg-g3/1`).

Cost only. Correctness is a precondition of the run (section 4 item 7), not a fact here; locality, maintainability and baselineIdentity are not outcome facts; C1 is report-only (U5): there
is no `c1Status` fact. The outcome is derived from facts by code (`decide`), and the facts from row counts (`facts_from_rows`); nothing is entered by hand.

Order (exactly one outcome per fact combination): adverse, then inconclusive, then adopt-favourable.
"""
from __future__ import annotations

from tests.cli_regression.support.g1e import reference as ref

TABLE_VERSION = "prereg-g3/1"
SCHEMA_ID = "acr-g3-cost-outcome/1"
OUTCOMES = ("adverse", "inconclusive", "adopt-favourable")
STATUS = ["pass", "adverse", "unresolved"]
CLASS_FACTS = {"stream": "streamStatus", "lifecycle": "lifecycleStatus", "budget": "budgetStatus", "agentLaunch": "agentLaunchStatus", "combinedRss": "combinedRssStatus", "reader": "readerStatus"}
FACT_DOMAINS = {
    "matrixComplete": [False, True], "designCompliant": [False, True], "independenceScreen": ["pass", "fail"], "artifactIdentity": ["ok", "predates-code", "digest-mismatch"],
    "survivorsRustArm": [False, True], "isolationBreach": [False, True],
    "streamStatus": STATUS, "lifecycleStatus": STATUS, "budgetStatus": STATUS, "agentLaunchStatus": STATUS, "combinedRssStatus": STATUS, "readerStatus": STATUS,
    "noisyAdverse": [False, True], "noisyGuard": [False, True], "uncalibratedRow": [False, True], "readerFixtureMissing": [False, True], "quietGateTimedOut": [False, True],
}
REMOVED = ["correctness (as S3)", "locality", "maintainability", "baselineIdentity as outcome fact", "claudeObservation fact", "c1Status as CONTINUE requirement", "outcome enum stop|inconclusive|continue"]
STATUS_FACTS = tuple(CLASS_FACTS.values())
ADOPT_SCOPE = ("ADOPT-FAVOURABLE means at most that no performance regression was found on the decision-bearing cost rows at these margins and this n. It says nothing about maintainability, "
               "coverage, UI acceptance or the adoption decision.")
NOISY_SENTENCE = "Noisy rows are measured and reported with their error, not certified."
NO_CROSS_RUNNER = "No cross-runner speedup claim: numbers are never compared across platforms, host classes, or hosted runs."
KILL_REAP_NOTE = ref.KILL_REAP_NOTE
UNEXPLAINED_ADVERSE = "The cost dimension is adverse with unknown cause; no cost-based recommendation to adopt is stated."


def _check_facts(f: dict) -> None:
    if set(f) != set(FACT_DOMAINS):
        raise ValueError(f"facts must be exactly {sorted(FACT_DOMAINS)}; got extra {sorted(set(f) - set(FACT_DOMAINS))} missing {sorted(set(FACT_DOMAINS) - set(f))}")
    for key, domain in FACT_DOMAINS.items():
        value = f[key]
        if value not in domain or (isinstance(domain[0], bool) and not isinstance(value, bool)):
            raise ValueError(f"fact {key} is outside its domain: {value!r}")


def decide(f: dict) -> tuple[str, list[str]]:
    """The registered decision table (5.2): (outcome, reason codes)."""
    _check_facts(f)
    adverse = []
    adverse += [f"A-row-adverse:{key}" for key in STATUS_FACTS if f[key] == "adverse"]
    if f["noisyAdverse"]: adverse.append("A-noisy-row-adverse")
    if f["survivorsRustArm"]: adverse.append("A-survivors-rust-arm")
    if f["isolationBreach"]: adverse.append("A-isolation-breach")
    if f["matrixComplete"] and f["artifactIdentity"] != "ok": adverse.append("A-artifact-identity:" + f["artifactIdentity"])
    if adverse:
        return "adverse", adverse
    inc = []
    if not f["matrixComplete"]: inc.append("I-matrix-incomplete")
    if not f["designCompliant"]: inc.append("I-design-noncompliant")
    if f["independenceScreen"] == "fail": inc.append("I-independence-screen-failed")
    inc += [f"I-row-unresolved:{key}" for key in STATUS_FACTS if f[key] == "unresolved"]
    if f["noisyGuard"]: inc.append("I-noisy-row-guard")
    if f["uncalibratedRow"]: inc.append("I-uncalibrated-row")
    if f["readerFixtureMissing"]: inc.append("I-reader-fixture-missing")
    if f["quietGateTimedOut"]: inc.append("I-quiet-gate-timed-out")
    if inc:
        return "inconclusive", inc
    return "adopt-favourable", ["F-all-registered-conditions-met"]


def adopt_predicate(f: dict) -> bool:
    """Independent statement of when `adopt-favourable` is allowed (checked against `decide` over the whole fact space)."""
    return (f["matrixComplete"] is True and f["designCompliant"] is True and f["independenceScreen"] == "pass" and f["artifactIdentity"] == "ok"
            and f["survivorsRustArm"] is False and f["isolationBreach"] is False and all(f[k] == "pass" for k in STATUS_FACTS)
            and f["noisyAdverse"] is False and f["noisyGuard"] is False and f["uncalibratedRow"] is False and f["readerFixtureMissing"] is False and f["quietGateTimedOut"] is False)


def adverse_predicate(f: dict) -> bool:
    return (any(f[k] == "adverse" for k in STATUS_FACTS) or f["noisyAdverse"] or f["survivorsRustArm"] or f["isolationBreach"]
            or (f["matrixComplete"] and f["artifactIdentity"] != "ok"))


# ---------------------------------------------------------------------------------------------------------------- facts from rows
def row_status(row: dict) -> str:
    """The status of a paired or budget row from its counts, the g1e rule: `n` valid pairs, `e` exceed count, `noisy` from F1. The report-only C1 row is never passed here."""
    if row.get("cls") == "beat":
        raise ValueError("C1 is report-only (U5): it has no status in the cost derivation")
    return ref.status_from_counts("parity" if row["kind"] == "budget" else row["kind"], row["n"], row["e"], noisy=bool(row.get("noisy")))


def facts_from_rows(rows: list[dict], flags: dict) -> dict:
    """`rows` = [{id, cls, kind, n, e, noisy, uncalibrated?}] of the decision-bearing rows (C1 and the descriptive packaged row excluded by the caller; C1 here is refused).
    `flags` = the non-row facts: matrixComplete, designCompliant, independenceScreen, artifactIdentity, survivorsRustArm, isolationBreach, readerFixtureMissing, quietGateTimedOut.
    Each class status aggregates its non-noisy rows (g1e `aggregate`: adverse > unresolved > pass); a class with no row is `pass` only if it is not required, so every class must be present."""
    by_class: dict[str, list[str]] = {cls: [] for cls in CLASS_FACTS}
    noisy_adverse = noisy_guard = uncalibrated = False
    for row in rows:
        if row["cls"] not in CLASS_FACTS:
            raise ValueError(f"row {row.get('id')}: class {row['cls']!r} has no cost fact (C1 and descriptive rows are report-only)")
        if row.get("uncalibrated"):
            uncalibrated = True
            by_class[row["cls"]].append("unresolved")
            continue
        status = row_status(row)
        by_class[row["cls"]].append(status)
        if row.get("noisy") and row["n"] >= ref.N_PAIRS:
            noisy_adverse |= row["e"] == ref.N_PAIRS
            noisy_guard |= row["e"] == ref.N_PAIRS - 1
    missing = [cls for cls, statuses in by_class.items() if not statuses]
    if missing:
        raise ValueError(f"no decision-bearing row for class(es) {missing}: the facts cannot be derived")
    facts = {fact: ref.aggregate(by_class[cls]) for cls, fact in CLASS_FACTS.items()}
    facts.update({"noisyAdverse": noisy_adverse, "noisyGuard": noisy_guard, "uncalibratedRow": uncalibrated})
    facts.update({k: flags[k] for k in ("matrixComplete", "designCompliant", "independenceScreen", "artifactIdentity", "survivorsRustArm", "isolationBreach", "readerFixtureMissing", "quietGateTimedOut")})
    _check_facts(facts)
    return facts


# ---------------------------------------------------------------------------------------------------------------- outcome document
def noisy_k(rows: list[dict]) -> tuple[int, list[str]]:
    ids = [r["id"] for r in rows if r.get("noisy")]
    return len(ids), ids


def build_outcome(*, facts: dict, source: dict, host_class: dict, calibration: dict, regression: list, mechanisms: list, platforms: list, rows: list, c1: dict | None) -> dict:
    """The `acr-g3-cost-outcome/1` document. `outcome` and `reasons` are derived here; `c1Report` is report-only and carries no status; the V21 kill/reap sentence is present iff C1's median d < 0."""
    outcome, reasons = decide(facts)
    k, noisy_ids = noisy_k(rows)
    doc = {"schema": SCHEMA_ID, "table": TABLE_VERSION, "removed": list(REMOVED), "outcome": outcome, "reasons": reasons, "facts": dict(facts), "k": k, "noisyRows": noisy_ids,
           "source": source, "hostClass": host_class, "rows": {"statusEnum": ["pass", "adverse", "unresolved", "noisy"], "newRowFamilies": ["agentLaunch", "combinedRss", "readerCodex"]},
           "calibration": calibration, "regressionSinceG1E": regression, "mechanisms": mechanisms, "platforms": platforms,
           "interpretation": {"adoptFavourableScope": ADOPT_SCOPE, "noCrossRunnerClaim": NO_CROSS_RUNNER},
           "c1Report": None if c1 is None else {k_: v for k_, v in c1.items() if k_ != "status"}}
    if outcome == "adopt-favourable":
        doc["interpretation"]["noisyRows"] = NOISY_SENTENCE
    if c1 is not None and c1.get("medianD") is not None and c1["medianD"] < 0:
        doc["interpretation"]["killReap"] = KILL_REAP_NOTE
    if problems := outcome_problems(doc):
        raise ValueError("; ".join(problems))
    return doc


def outcome_problems(doc: dict) -> list[str]:
    """The document must say what its own facts say, and carry the schema constants."""
    out = []
    if doc.get("schema") != SCHEMA_ID: out.append("schema is not acr-g3-cost-outcome/1")
    if doc.get("table") != TABLE_VERSION: out.append("table is not prereg-g3/1")
    if doc.get("removed") != REMOVED: out.append("the removed-facts list differs from the registered one")
    try:
        want, _ = decide(doc["facts"])
    except (KeyError, ValueError) as error:
        return out + [f"facts: {error}"]
    if doc.get("outcome") not in OUTCOMES or doc["outcome"] != want: out.append("the outcome is not the table's outcome for the document's facts")
    interp = doc.get("interpretation") or {}
    if interp.get("adoptFavourableScope") != ADOPT_SCOPE: out.append("adoptFavourableScope is not the registered sentence")
    if interp.get("noCrossRunnerClaim") != NO_CROSS_RUNNER: out.append("noCrossRunnerClaim is not the registered sentence")
    c1 = doc.get("c1Report")
    if isinstance(c1, dict) and "status" in c1: out.append("c1Report must carry no status (report-only)")
    needs_note = isinstance(c1, dict) and c1.get("medianD") is not None and c1["medianD"] < 0
    if needs_note != ("killReap" in interp): out.append("the kill/reap note is required iff C1's median d is below zero")
    if (doc.get("outcome") == "adopt-favourable") != ("noisyRows" in interp): out.append("the noisy-rows sentence is required iff the outcome is adopt-favourable")
    for entry in doc.get("mechanisms") or []:
        if entry.get("statusChanged") is not False: out.append(f"mechanism {entry.get('row')}: an attribution must not change a status")
        if entry.get("class") not in ("explained-code", "explained-environment", "unexplained"): out.append(f"mechanism {entry.get('row')}: unknown class")
    for entry in doc.get("platforms") or []:
        if entry.get("comparableAcrossRunners") is not False: out.append(f"platform {entry.get('platform')}: comparableAcrossRunners must be false")
    return out
