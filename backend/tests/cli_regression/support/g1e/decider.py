"""The G1-E decider (ticket E4): a manifested evidence bundle -> the E2 outcome document -> the registered decision table (E2 v10 section 8).

HARD GATE (E2 freeze record, pre/post-matrix split): missing, invalid or malformed evidence can never derive CONTINUE.
How that holds, in layers (each is a separate, tested refusal):
  1. the manifest is pinned by hash; a replaced, edited, unlisted, missing or tampered file raises `EvidenceInvalid` before anything is read;
  2. the frozen run binding (`freeze.json`) must repeat the registered constants; a stale, unbound or mis-scheduled trial receipt is not admitted (its slot counts
     as missing and an integrity failure is recorded), an admitted trial whose recomputed validity fails is evidence-invalid; both make the matrix incomplete (I1);
  3. every document section is built from recomputed or hash-verified records; a record that is absent where the document has a natural "not available" value
     (correctness row, reader benchmark, maintainability, joint review, continuation authority, a canary value, a trial) becomes that value and the table yields
     INCONCLUSIVE (or STOP when a stop reason is present: a stop reason is never hidden by missing evidence elsewhere); a record that is absent or malformed where
     no such value exists (baseline identity, locality, preregistration, freeze) raises `EvidenceInvalid`: there is then NO outcome, not even INCONCLUSIVE;
  4. the finished document must pass the reference `document_ok(doc, 'production')` (frozen schema by hash, closed key sets, all row/fact/outcome re-derivation),
     every `{path, sha256}` reference in it must resolve in the manifest, and the baseline reviewer verdicts must be distinct (id, session and report hash);
  5. `decide()` returns the outcome only if the document's own derivation equals what the builder asked for.
  6. (E2 v13) a bundle with NO matrix attempt (no `trials/` file at all) is decided by the no-matrix path only: facts that need matrix pairs are `not_derivable`, `matrixComplete`
     is false, the outcome is STOP when a derivable or carried stop fact holds and INCONCLUSIVE otherwise (I1 always), never CONTINUE; a bundle with any matrix attempt uses the v10
     path unchanged, where `not_derivable` is out of domain and raises. Stop facts of earlier runs (S1-S6, `carry.py`) are carried into either path; the earlier runs the caller
     supplies must be exactly the ones F0 names, so a run cannot be dropped to drop its stop facts.
There is no code path that sets a fact by hand and none that reads a producer's declared status, count or outcome: counts, statuses, facts and the outcome are derived.
Producer honesty (a timestamp taken where it says) remains an obligation of the E4 producer and a reviewer question (E2 v10 section 12, "Limit").
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.cli_regression.support.g1e import carry
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e.bundle import Bundle, BundleError

SIDECAR_NAMES = ("rust-shell",)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceInvalid(Exception):
    """The evidence is not usable at all; there is no outcome (never CONTINUE, never a partial verdict)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Decision:
    outcome: str
    reasons: list[str]
    facts: dict
    document: dict
    summary: dict = field(default_factory=dict)


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _need(bundle: Bundle, name: str) -> Any:
    if not bundle.exists(name):
        raise EvidenceInvalid([f"required record {name} is absent from the manifest"])
    try:
        return bundle.read_json(name)
    except BundleError as error:
        raise EvidenceInvalid([str(error)]) from None


ABSENT = object()


def _optional(bundle: Bundle, name: str) -> Any:
    """ABSENT only when the record is absent from the manifest; a present record (even JSON `null`) is returned as read, and an unreadable/tampered one raises."""
    if not bundle.exists(name):
        return ABSENT
    try:
        return bundle.read_json(name)
    except BundleError as error:
        raise EvidenceInvalid([str(error)]) from None


# ------------------------------------------------------------------------------------------------------------------ sections
GATE_RECEIPT_FIELDS = {"schema", "row", "head", "sidecarSha256", "commands", "result"}


def gate_receipt_ok(bundle: Bundle, row_id: str, ref_obj: Any, freeze: dict) -> list[str]:
    """A `pass-current` row stands only on a manifested receipt bound to this row, the E4 HEAD and the sidecar hash, whose commands all exited 0."""
    if not bundle.resolves(ref_obj):
        return ["the receipt reference does not resolve to a manifested file with that digest"]
    try:
        rec = bundle.read_json(ref_obj["path"])
    except BundleError as error:
        return [str(error)]
    if not isinstance(rec, dict) or set(rec) != GATE_RECEIPT_FIELDS or rec["schema"] != "acr-g1e-gate/1":
        return ["the receipt is not an acr-g1e-gate/1 record"]
    out = []
    if rec["row"] != row_id:
        out.append("the receipt belongs to another row")
    if rec["head"] != freeze["head"]:
        out.append("the receipt was produced at another HEAD than the E4 HEAD")
    if rec["sidecarSha256"] != freeze["sidecarSha256"]:
        out.append("the receipt was produced with another sidecar than the frozen one")
    commands = rec["commands"]
    if not (isinstance(commands, list) and commands and all(isinstance(c, dict) and set(c) == {"argv", "exit"} and c["exit"] == 0 and isinstance(c["argv"], list) and c["argv"] for c in commands)):
        out.append("a command is missing or did not exit 0")
    if rec["result"] != "pass":
        out.append("the receipt result is not pass")
    return out


def build_correctness(bundle: Bundle, freeze: dict, reader_required: bool) -> tuple[dict, list[str]]:
    declared = _optional(bundle, "gates.json")
    notes: list[str] = []
    rows: dict = {}
    declared_rows = declared.get("rows") if isinstance(declared, dict) and set(declared) == {"schema", "rows"} and declared.get("schema") == "acr-g1e-gates/1" else None
    if declared is not ABSENT and declared_rows is None:
        raise EvidenceInvalid(["gates.json is malformed (schema/keys)"])
    if declared_rows is not None and not isinstance(declared_rows, dict):
        raise EvidenceInvalid(["gates.json rows is not an object"])
    for cid in ref.CORRECTNESS_IDS:
        entry = (declared_rows or {}).get(cid)
        if cid == ref.OPTIONAL_CORRECTNESS and not reader_required:
            rows[cid] = {"status": "not-required"}
            continue
        if entry is None:
            rows[cid] = {"status": "not-rerun"}
            notes.append(f"{cid}: no gate record")
            continue
        if not isinstance(entry, dict) or "status" not in entry:
            raise EvidenceInvalid([f"gates.json row {cid} is malformed"])
        if entry["status"] == "pass-current":
            problems = gate_receipt_ok(bundle, cid, entry.get("receipt"), freeze)
            if problems:
                rows[cid] = {"status": "not-rerun", "note": "pass-current claim not backed by its receipt: " + "; ".join(problems)}
                notes.append(f"{cid}: {problems[0]}")
            else:
                rows[cid] = {"status": "pass-current", "receipt": {"path": entry["receipt"]["path"], "sha256": entry["receipt"]["sha256"]}}
        else:
            if entry["status"] == "not-required":  # only the optional row may be not-required, and only when the reader is not required
                rows[cid] = {"status": "not-rerun", "note": "not-required claimed for a required row"}
            else:
                rows[cid] = {k: v for k, v in entry.items() if k in ("status", "attribution", "boundedCorrectivePath", "note")}
    return rows, notes


def _evidence_ref(x: Any) -> bool:
    return isinstance(x, dict) and set(x) == {"ledger", "date", "quote"} and all(isinstance(v, str) and v.strip() for v in x.values())


def _walk_refs(node: Any, found: list[dict]) -> None:
    if isinstance(node, dict):
        if set(node) == {"path", "sha256"}:
            found.append(node)
        for value in node.values():
            _walk_refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_refs(value, found)


def unresolved_refs(bundle: Bundle, doc: dict) -> list[str]:
    found: list[dict] = []
    _walk_refs(doc, found)
    return [f"{r.get('path')!r}" for r in found if not bundle.resolves(r)]


def baseline_distinctness(doc: dict) -> list[str]:
    """E2 v10 section 9 / v13 V5 item 5: for each listed Python-arm file the verdicts must come from distinct reviewers, sessions and report hashes, and a `refactor_reviewed`
    classification stands only if every verdict was given on the file's loaded bytes at F0."""
    baseline = doc["baselineIdentity"]
    if baseline is None:
        return []
    problems = []
    for entry in baseline["changedFiles"]:
        verdicts = entry["verdicts"]
        for key in ("reviewer", "session"):
            values = [v[key] for v in verdicts]
            if len(set(values)) != len(values):
                problems.append(f"{entry['path']}: verdicts share a {key}")
        hashes = [v["report"]["sha256"] for v in verdicts]
        if len(set(hashes)) != len(hashes):
            problems.append(f"{entry['path']}: verdicts share a report hash")
        if baseline["status"] == "refactor_reviewed" and any(v["loadedSha256"] != entry["loadedSha256"] for v in verdicts):
            problems.append(f"{entry['path']}: a verdict was not given on the loaded bytes at F0")
    return problems


def baseline_problems(bundle: Bundle, baseline: dict, freeze: dict) -> list[str]:
    """The V5 record must be the F0 measurement: the same commits, E3set, file list and reference manifest, and exactly the listing item 4 defines over F0's loaded-byte hashes."""
    arm = freeze["pythonArm"]
    if not isinstance(baseline, dict):
        return ["baseline-identity.json is not an object"]
    out = [f"baselineIdentity.{k} differs from F0" for k in ("upstreamMerge", "e3Parent", "e3Commit", "e3Set") if baseline.get(k) != arm[k]]
    for key, want in (("pythonArmFileList", arm["fileList"]), ("referenceManifest", arm["referenceManifest"])):
        reference = baseline.get(key)
        try:
            if not bundle.resolves(reference) or bundle.read_json(reference["path"]) != want:
                out.append(f"baselineIdentity.{key} is not F0's {key}")
        except BundleError as error:
            out.append(str(error))
    loaded = {f["path"]: f["sha256"] for f in arm["fileList"]}
    reference_sha = {m["path"]: m["sha256"] for m in arm["referenceManifest"]}
    expected = sorted(path for path in loaded if loaded[path] != reference_sha[path])
    changed = baseline.get("changedFiles") if isinstance(baseline.get("changedFiles"), list) else []
    if sorted(e.get("path") for e in changed if isinstance(e, dict)) != expected:
        out.append("the listed files are not exactly the files whose loaded bytes differ from their reference (V5 item 4) at F0")
    for entry in changed:
        if isinstance(entry, dict) and entry.get("path") in loaded and (entry.get("loadedSha256") != loaded[entry["path"]] or entry.get("referenceSha256") != reference_sha[entry["path"]]):
            out.append(f"{entry['path']}: the listed digests differ from F0's")
    return out


LOCALITY_ALLOWLIST = "locality/allowlist.json"


LISTING_NAME = "freeze-inputs.json"
LOCALITY_SCRIPT_COPY = "locality/check.py"
SCRIPT_INPUT = "backend/tests/cli_regression/support/g1e/locality_check.py"
ALLOWLIST_INPUT = "backend/tests/cli_regression/support/g1e/locality-allowlist.json"


def frozen_input_problems(repo: Path, freeze: dict, bundle: Bundle, running_script: Path) -> list[str]:
    """Bind the locality re-run to F0's FROZEN INPUTS (v13 section 4: F0 freezes "the locality check script, scope and allowlist"). The listing F0's `inputsSha256` hashes travels in the bundle
    (`freeze-inputs.json`); it must hash to that digest, every frozen input of the `--repo` tree must still equal it (the comparison `verify_freeze` makes), and the bundle's allowlist, the
    script this decider is about to run and the bundle's copy of the script must each equal the digest F0 listed for it. Anything else is a check or an allowlist that did not exist at F0."""
    from tests.cli_regression.support.g1e import freeze as fz
    try:
        listing = bundle.read_json(LISTING_NAME)
    except BundleError as error:
        return [f"the frozen-input listing {LISTING_NAME} is not usable: {error}"]
    if not (isinstance(listing, list) and all(isinstance(e, dict) and set(e) == {"path", "sha256"} and isinstance(e["path"], str) and isinstance(e["sha256"], str) for e in listing)):
        return [f"{LISTING_NAME} is not a listing of {{path, sha256}}"]
    if fz.canonical_sha256(listing) != freeze["inputsSha256"]:
        return [f"{LISTING_NAME} does not hash to F0's inputsSha256: it is not the listing F0 froze"]
    out = []
    for entry in listing:
        path = Path(repo) / entry["path"]
        if not path.is_file() or fz.sha256_file(path) != entry["sha256"]:
            out.append(f"frozen input {entry['path']} in the --repo tree is missing or differs from F0's")
    digests = {entry["path"]: entry["sha256"] for entry in listing}
    for what, name, actual in (("the bundle's locality allowlist", ALLOWLIST_INPUT, lambda: bundle.digest(LOCALITY_ALLOWLIST)),
                               ("the locality_check.py the decider runs", SCRIPT_INPUT, lambda: fz.sha256_file(running_script)),
                               ("the bundle's copy of the locality script", SCRIPT_INPUT, lambda: bundle.digest(LOCALITY_SCRIPT_COPY))):
        try:
            if name not in digests:
                out.append(f"F0 does not list {name}, so {what} cannot be bound to it")
            elif actual() != digests[name]:
                out.append(f"{what} is not the one F0 froze ({name})")
        except (BundleError, OSError) as error:
            out.append(f"{what} cannot be read: {error}")
    return out


def make_approval_check(repo: Path):
    """`check(freeze) -> problems`: F0's approval must be a real one, its quote verbatim in the ledger of `repo` as it is now (`summarise.approval_problems`)."""
    from tests.cli_regression.support.g1e import freeze as fz
    repo = Path(repo)

    def check(freeze: dict) -> list[str]:
        approval = freeze.get("preregistration", {}).get("userApproval") if isinstance(freeze.get("preregistration"), dict) else None
        return [f"approval: {problem}" for problem in fz.approval_check(repo, approval)]
    return check


def make_locality_recheck(repo: Path):
    """The decider's re-run of the frozen locality check (v10 section 12, v13 V6): `recheck(freeze, bundle)` runs THIS package's `locality_check` (the frozen script, pinned by F0's inputs) in
    `repo` with F0's reference manifest and the bundle's allowlist, after checking that `repo` is the F0 tree (HEAD, tracked diff, loaded bytes of every Python-arm file)."""
    from tests.cli_regression.support.g1e import baseline_identity as bi
    from tests.cli_regression.support.g1e import freeze as fz
    from tests.cli_regression.support.g1e import locality_check as lc
    repo = Path(repo)

    def recheck(freeze: dict, bundle: Bundle) -> dict:
        problems: list[str] = []
        try:
            if fz.git_output(repo, "rev-parse", "HEAD").decode().strip() != freeze["head"]:
                problems.append("HEAD differs from the frozen HEAD: this is not the F0 tree")
            if fz.tracked_diff_sha256(repo) != freeze["trackedDiffSha256"]:
                problems.append("the tracked diff differs from the frozen one: this is not the F0 tree")
        except RuntimeError as error:
            problems.append(str(error))
        arm = freeze["pythonArm"]
        frozen = {f["path"]: f["sha256"] for f in arm["fileList"]}
        for now in bi.python_arm_file_list(repo, list(frozen)):
            if now["sha256"] != frozen[now["path"]]:
                problems.append(f"the loaded bytes of Python-arm file {now['path']} differ from F0's: this is not the F0 tree")
        problems += frozen_input_problems(repo, freeze, bundle, Path(lc.__file__))
        problems += fz.locality_scope_problems(repo, freeze["localityScope"])        # the scope F0 froze: path set and bytes of every file the check scans, untracked ones included
        try:
            allowlist = json.loads(bundle.read(LOCALITY_ALLOWLIST))
            lc.validate_allowlist(allowlist)
            result = lc.check(repo, allowlist, lc.reference_texts(repo, arm["referenceManifest"]))
        except (BundleError, ValueError, RuntimeError, OSError) as error:
            raise EvidenceInvalid([f"locality: the frozen check could not be re-run: {str(error)[:200]}"]) from None
        return {"problems": problems, "violation": bool(result["violation"]), "findings": lc.findings_lines(result), "scopeList": lc.scope_text(freeze["localityScope"]),
                "checkScriptSha256": fz.sha256_file(Path(lc.__file__)), "referenceManifestSha256": arm["referenceManifestSha256"]}
    return recheck


def locality_problems(bundle: Bundle, record: Any, fresh: dict) -> list[str]:
    """The recorded `locality.json` must be what the frozen check, re-run, says: made with F0's manifest, by the frozen script, over the F0 tree, with the same scope, violation and findings."""
    out = [f"locality: {problem}" for problem in fresh["problems"]]
    if not isinstance(record, dict):
        return out + ["locality: locality.json is not an object"]
    for key in ("checkScript", "scopeList", "allowlist"):
        if not bundle.resolves(record.get(key)):
            out.append(f"locality: the {key} reference does not resolve to a manifested file with that digest")
    if out:
        return out
    if record["allowlist"]["path"] != LOCALITY_ALLOWLIST:
        out.append(f"locality: the allowlist is not the file the check was re-run with ({LOCALITY_ALLOWLIST})")
    if record.get("referenceManifestSha256") != fresh["referenceManifestSha256"]:
        out.append("locality: the record was not made with F0's reference manifest")
    if record["checkScript"]["sha256"] != fresh["checkScriptSha256"]:
        out.append("locality: the recorded check script is not the frozen locality script")
    if bundle.read(record["scopeList"]["path"]).decode("utf-8", "replace") != fresh["scopeList"]:
        out.append("locality: the recorded scope list is not the scope F0 froze (its paths, the bytes of each file and the scope digest)")
    if record.get("violation") is not fresh["violation"] or not isinstance(record.get("findings"), list) or sorted(map(str, record["findings"])) != sorted(fresh["findings"]):
        out.append("locality: the recorded violation or findings differ from the frozen check's re-run (the fact is the recomputed one)")
    return out


def noisy_calibration_problems(doc: dict, cal_rows: dict | None) -> list[str]:
    """v14-r2 V11 item 4: the calibration-noise part of every `noisyReport` entry (qHat, qHatAllPairs, calValues, calMedAbs, calP90Abs, marginAtMedian, marginOverCalMedAbs) must equal the numbers the
    decider recomputes from the Python receipts (`cal_rows` = `calibration_report(...)['rows']`). The matrix part of the entry is checked by `reference.validate_document`; the reference cannot see
    the receipts, so this is the one place the two halves of the entry meet. `cal_rows` is None only when no calibration was recomputed (then no row can be noisy)."""
    entries = {entry["id"]: entry for entry in doc["noisyReport"]}
    listed = ref.noisy_ids(doc)
    if list(entries) != listed:
        return [f"noisyReport: the entries {list(entries)} are not the frozen noisy rows {listed} in registry order"]
    if not listed:
        return []
    if cal_rows is None:
        return ["noisyReport: noisy rows are listed but no calibration was recomputed from the receipts"]
    out = []
    for rid in listed:
        if rid not in cal_rows:
            out.append(f"noisyReport: {rid} has no row in the calibration recomputed from the receipts")
        elif entries[rid]["calibration"] != ref.noise_block(cal_rows[rid]):
            out.append(f"noisyReport: the calibration noise of {rid} differs from the numbers recomputed from the Python receipts")
    return out


def build_document(bundle: Bundle, freeze: dict, freeze_sha: str, matrix: dict, path: str = "matrix", earlier: tuple | list = (), f1: dict | None = None,
                   locality_recheck=None, cal_rows: dict | None = None) -> tuple[dict, dict]:
    """`path` is 'matrix' (the v10 derivation) or 'no-matrix' (no matrix attempt exists, V10 item 5); `earlier` = the carried-fact records of the earlier runs; `f1` = the F1 record (or None);
    `cal_rows` = the calibration report rows recomputed from the receipts (`calibration_report`), from which each noisy row's calibration noise is taken."""
    nm = path == "no-matrix"
    reader_required = freeze["options"]["readerRequired"]
    calibration_mode = freeze["options"]["calibration"]
    trials = matrix["trials"]
    # --- rows / matrix / design / canaries, all from the admitted trials ---
    built = summarise.build_rows(freeze, matrix)
    rows = {}
    for spec in ref.REG:
        rows[spec["id"]] = {"id": spec["id"], "class": spec["cls"], "metric": spec["metric"], "cell": spec["cell"], "kind": spec["kind"], "unit": spec["unit"],
                            "source": spec["source"], "margin": {**spec["margin"], "basis": spec["basis"]}, "pairs": built[spec["id"]]["pairs"],
                            "validPairs": 0, "exceedCount": 0, "medianExcess": None, "status": "unresolved"}
        if spec["kind"] == "beat":
            rows[spec["id"]]["notImprovedCount"] = 0
    canaries = [{"id": cid, "values": values, "S": None, "trip": None} for cid, values in summarise.canary_values(freeze, matrix).items()]
    invalid = [summarise.trial_id(*s) for s in matrix["invalid"]]
    missing = [summarise.trial_id(*s) for s in matrix["missing"]]
    identities = [{"trial": summarise.trial_id(*slot), "pid": t["backend"]["pid"], "start": t["backend"]["start"]}
                  for slot, t in sorted(trials.items()) if isinstance(t.get("backend"), dict) and summarise.is_int(t["backend"].get("pid"), 1) and isinstance(t["backend"].get("start"), str) and t["backend"]["start"].strip()]
    survivors = sorted(summarise.trial_id(*s) for s, t in trials.items() if s[0] == "rust-shell" and _ownership_failure(t))
    breach = sorted(summarise.trial_id(*s) for s, t in trials.items() if _isolation_breach(t))
    # v13 V10 5(a): S1 and S2 come from ALL records of the run, also the receipts that were not admitted and the run's own Python-only calibration attempts
    every = carry.receipt_stop_facts(bundle)
    survivors = sorted(set(survivors) | set(every["S1"]))
    breach = sorted(set(breach) | set(every["S2"]))
    isolation_trials = [{"trial": summarise.trial_id(*s), "backendPidBefore": t["backend"]["pid"], "backendPidAfter": t["backendEnd"]["pid"],
                         "backendStartBefore": t["backend"]["start"], "backendStartAfter": t["backendEnd"]["start"]}
                        for s, t in sorted(trials.items()) if _isolation_breach(t) and _identity_ok(t.get("backend")) and _identity_ok(t.get("backendEnd"))]
    # --- records with a natural "not available" value ---
    correctness, notes = build_correctness(bundle, freeze, reader_required)
    maint = _optional(bundle, "maintainability.json")
    if maint is not ABSENT:
        maintainability = maint
    elif nm:
        maintainability = None                      # no record on the no-matrix path: `not_derivable`, not "incomplete"
    else:
        maintainability = {"complete": False, "analyses": [], "touchedResponsibilities": [], "repeatedFacts": [], "manualCompositionSites": []}
    review = _optional(bundle, "joint-review.json")
    review = {"status": "pending", "reports": []} if review is ABSENT else review          # only ABSENCE defaults; a present but empty or malformed record is refused by the schema
    continuation = _optional(bundle, "continuation.json")
    continuation = {"recorded": False} if continuation is ABSENT else continuation
    calibration = _optional(bundle, "calibration.json")
    empty_calibration = {"receipts": [], "noisyRows": [], "uncalibratedRows": []}
    if calibration_mode == "none":
        calibration = dict(empty_calibration)
    elif calibration is ABSENT:
        if not nm:
            raise EvidenceInvalid(["calibration.json is absent although the freeze requires calibration " + calibration_mode])
        calibration = dict(empty_calibration)      # calibration did not complete: the no-matrix path derives `not_derivable`
    elif f1 is not None and calibration != f1["calibration"]:
        raise EvidenceInvalid(["calibration.json differs from the calibration output frozen in F1"])
    listed_noisy = [rid for rid in ref.PAIRED_IDS if rid in set(calibration["noisyRows"])]
    if listed_noisy and cal_rows is None:
        raise EvidenceInvalid(["noisy rows are listed without a calibration recomputed from the receipts: their calibration noise cannot be reported"])
    noisy_partial = [{"id": rid, "calibration": ref.noise_block(cal_rows[rid])} for rid in listed_noisy]     # `reference.recompute` completes the entries from the pairs (matrix part, band reading, label)
    reader = _optional(bundle, "reader.json")
    if not reader_required:
        reader_section = {"status": "not_required", "fixtures": [], "goldenEvents": None, "calibrationRuns": 0, "runs": 0, "findings": [],
                          "m0": {fx: {"python": True, "rust": True} for fx in ("claude", "opencode")}, "rows": {}}
    elif reader is ABSENT:
        reader_section = {"status": "not_run", "fixtures": [], "goldenEvents": None, "calibrationRuns": 0, "runs": 0, "findings": ["reader.json absent"],
                          "m0": {fx: {"python": False, "rust": False} for fx in ("claude", "opencode")}, "rows": {}}
    elif not isinstance(reader, dict) or not isinstance(reader.get("rows"), dict):
        raise EvidenceInvalid(["reader.json is malformed"])
    else:
        # counts and statuses of the reader rows are derived from their pairs; nothing the producer declared there is kept
        rows_in = {rid: {**{k: v for k, v in row.items() if k not in ("validPairs", "exceedCount", "status")}, "validPairs": 0, "exceedCount": 0, "status": "unresolved"}
                   for rid, row in reader["rows"].items() if isinstance(row, dict)}
        if len(rows_in) != len(reader["rows"]):
            raise EvidenceInvalid(["reader.json holds a non-object row"])
        reader_section = {"status": "not_run", **{k: v for k, v in reader.items() if k not in ("status", "rows")}, "rows": rows_in}
        if f1 is None or f1["readerMargins"] is None:
            raise EvidenceInvalid(["reader.json is present without the absolute reader margins frozen in F1"])
        runs, calibration_runs = reader.get("runs"), reader.get("calibrationRuns")
        if not (summarise.is_int(runs, 1) and summarise.is_int(calibration_runs, 1)):
            raise EvidenceInvalid(["reader.json does not state its run counts"])
        # V7 item 2: every reader-calibration run (one reader, two fixtures) and every reader-measure run (two readers, two fixtures, `runs` pairs each) checked the toolchain before it started
        bad = []
        for name, expected in (("reader/toolchain-calibration.json", 2 * calibration_runs), ("reader/toolchain-measure.json", 4 * runs)):
            try:
                bad += summarise.reader_toolchain_problems(bundle.read_json(name) if bundle.exists(name) else None, freeze, expected, name)
            except BundleError as error:
                bad.append(str(error))
        if bad:
            raise EvidenceInvalid(bad)
        for rid, row in rows_in.items():
            _tag, metric, fixture = rid.split("|", 2) if rid.count("|") == 2 else (None, None, None)
            frozen_margin = (f1["readerMargins"].get(fixture) or {}).get(metric) if fixture else None
            margin = row.get("margin")
            if not isinstance(margin, dict) or frozen_margin is None or margin.get("value") != frozen_margin:
                raise EvidenceInvalid([f"{rid}: the reader margin differs from the absolute margin frozen in F1"])
    # --- records without such a value: absent means no outcome, except on the no-matrix path where it is `not_derivable` ---
    baseline = _optional(bundle, "baseline-identity.json") if nm else _need(bundle, "baseline-identity.json")
    baseline = None if baseline is ABSENT else baseline
    locality = _optional(bundle, "locality.json") if nm else _need(bundle, "locality.json")
    locality = None if locality is ABSENT else locality
    if locality is not None:
        # v10 section 12 makes running the locality script a decider obligation: the frozen check is RE-RUN against the F0 tree with F0's manifest and the bundle's allowlist, and the record must
        # equal the result. `localityViolation` is then the recomputed fact; the recorded flag is never trusted.
        if locality_recheck is None:
            raise EvidenceInvalid(["locality: the frozen check cannot be re-run without the F0 tree (give the decider --repo, or a re-check)"])
        bad = locality_problems(bundle, locality, locality_recheck(freeze, bundle))
        if bad:
            raise EvidenceInvalid(bad)
    if baseline is not None:
        bad = baseline_problems(bundle, baseline, freeze)
        if bad:
            raise EvidenceInvalid(bad)
    exploratory = _optional(bundle, "exploratory.json")
    exploratory = [] if exploratory is ABSENT else exploratory
    limitations = _need(bundle, "limitations.json")
    pre = freeze["preregistration"]
    summary = {"admittedTrials": len(trials), "integrityFailures": matrix["integrity"], "invalidTrials": invalid, "missingSlots": missing,
               "trialProblems": {summarise.trial_id(*s): p for s, p in matrix["problems"].items() if p}, "gateNotes": notes}
    review_ok = isinstance(review, dict) and review.get("status") == "pass"
    authorizations = _optional(bundle, "authorizations.json")
    hosted_ok = authorizations is not ABSENT and isinstance(authorizations, dict) and set(authorizations) == {"hostedActions"} and _evidence_ref(authorizations["hostedActions"])
    manifest_record = _need(bundle, "manifest-record.json")
    doc = {
        "schema": ref.SCHEMA_ID, "ticket": "E4", "derivation": {"path": path, "earlierRuns": [dict(run) for run in earlier]},
        "evaluated": {"head": freeze["head"], "branch": freeze["branch"], "sidecarSha256": freeze["sidecarSha256"], "methodRevision": freeze["methodRevision"],
                      "freezeSha256": freeze_sha, "e1ProtocolSha256": freeze["e1ProtocolSha256"], "evidenceRevision": freeze["evidenceRevision"],
                      "summarySha256": canonical_sha256(summary), "inputsSha256": freeze["inputsSha256"], "receiptsManifestSha256": bundle.manifest_sha256},
        "preregistration": {"document": pre["document"], "sha256": pre["sha256"], "userApproval": pre["userApproval"],
                            "options": {"readerRequired": reader_required, "descendantSnapshot": ref.SNAPSHOT_OPTION, "calibration": calibration_mode, "pairsPerCell": ref.N_PAIRS,
                                        "c1Noisy": freeze["options"]["c1Noisy"], "noisyGuard": freeze["options"]["noisyGuard"]},       # constants frozen in F0 (v14-r2 V11, V12; no cap)
                            "calibration": calibration},
        "design": {"scheduleSeed": freeze["scheduleSeed"], "schedule": matrix["schedule"], "scheduleSha256": freeze["scheduleSha256"], "compliant": False,
                   "logPolicy": {"logLimitBytes": ref.LOG_LIMIT_BYTES, "peerMaxBytes": ref.PEER_MAX_BYTES, "preOriginReadMaxBytes": ref.PRE_ORIGIN_READ_MAX,
                                 "rotationInvalidatesTrial": True, "logComparedAfterExitFdReleasedStable": True, "originFromRendererStream": True,
                                 "logFdReleasedAfterExit": True, "logStableReads": ref.LOG_STABLE_READS, "logStableGapMs": ref.LOG_STABLE_GAP_MS},
                   "backendIdentities": identities},
        "independence": {"canaries": canaries, "status": "fail"},
        "matrix": {"attempted": 40, "accepted": 0, "complete": False, "integrityFailures": list(matrix["integrity"]), "evidenceInvalidTrials": invalid, "missingSlots": missing},
        "survivors": {"rustArm": bool(survivors), "trials": survivors}, "isolation": {"breach": bool(breach), "trials": isolation_trials},
        "claudeObservation": summarise.claude_state(trials, freeze),
        "baselineIdentity": baseline, "locality": locality, "maintainability": maintainability, "readerBenchmark": reader_section, "correctness": correctness,
        "rows": rows, "reportOnly": {"nonDecisional": True, "resizeAckMs": summarise.resize_report(matrix)}, "exploratory": exploratory, "outcome": "inconclusive",
        "limitations": limitations, "receiptsManifest": {"sha256": bundle.manifest_sha256, "evidence": manifest_record}, "noisyReport": noisy_partial,
        "jointReview": review, "continuationAuthority": continuation,
        "gates": {"ticketE5": {"open": False, "missing": []}, "g2": {"open": False, "missing": []}}, "gateSemantics": "necessary-not-sufficient", "notAdoption": True,
    }
    doc["_hosted_authorization"] = hosted_ok
    doc["_review_ok"] = review_ok
    return doc, summary


CALIBRATION_NAME = re.compile(r"^calibration/trial-python-(1|4)-([0-9])\.json$")
CALIBRATION_WARMUP_NAME = re.compile(r"^calibration/warmup-python-([0-9]{2})\.json$")


def _start_ms(trial: dict) -> float | None:
    """The wall-clock start of an attempt: its first phase (`phases[0].wallMs`). Never an ambient host number, so a host record cannot influence a decision."""
    phases = trial.get("phases")
    first = phases[0] if isinstance(phases, list) and phases and isinstance(phases[0], dict) else None
    return first["wallMs"] if first is not None and summarise.is_num(first.get("wallMs")) else None


def _judge_attempt(bundle: Bundle, freeze: dict, path: str, trial: dict, mode: str, cell: int) -> tuple[list[str], list[dict]]:
    """The decider's own checks of one attempt receipt (structure, consistency, recomputed validity); empty problems = valid evidence. Returns (problems, resource samples)."""
    shape, samples = summarise.structure_problems(trial, freeze, bundle, path[:-len(".json")] + summarise.RESOURCES_SUFFIX)
    problems = shape + (summarise.consistency_problems(trial) if not shape else [])
    if not problems:
        problems = summarise.trial_problems(trial, freeze, mode, cell)
    return problems, samples


def _warmup_problems_of(trial: dict, problems: list[str]) -> list[str]:
    """A warm-up is discarded but must itself have run cleanly (v14-r2 V19): the recomputed validity AND every phase passed. A failed warm-up is a stop, not a missing value."""
    if problems:
        return problems
    failed = sorted(name for name, outcome in trial["phaseOutcomes"].items() if outcome != "passed")
    return [f"phase(s) {failed} did not pass"] if failed else []


def calibration_problems(bundle: Bundle, freeze: dict, f0_sha: str, f1: dict) -> list[str]:
    """The problems of `calibration_report` (the receipts and the recomputed lists), for the callers that need only the verdict."""
    return calibration_report(bundle, freeze, f0_sha, f1)[0]


def calibration_report(bundle: Bundle, freeze: dict, f0_sha: str, f1: dict) -> tuple[list[str], dict | None]:
    """(problems, report): `report` is `calibration.rows_calibration` over the chains recomputed from the receipts (None when there is no calibration or a problem was found); its `rows` are the single
    source of the calibration noise the document reports for each noisy row (`noisy_calibration_problems` compares the document with it).
    F1 may exist only after a COMPLETE Python-only calibration (v14-r2 V19): ONE discarded warm-up in the first chain's cell, then two back-to-back chains of ten scored attempts, one per cell, each
    carrying its frozen `slot` 0..9. v13 V10 item 4 says an incomplete calibration means no F1 and no matrix attempt, so a listed receipt that is missing, not bound, of another attempt than its
    name says, with a repeated slot, or a warm-up that did not run cleanly refuses the document (no outcome, never CONTINUE). An attempt whose own evidence is invalid stays in F1's list but gives
    its slot no value: the rows of its cell then lose adjacent pairs and may become `uncalibrated` (rule I4).
    Each listed receipt is re-judged with the decider's own checks. The start time (`phases[0].wallMs`) must follow the frozen slot order (warm-up, chain 0 slots 0..9, chain 1 slots 0..9). The
    noisy and the uncalibrated rows are RECOMPUTED from the valid values in slot order (the adjacent estimator) and must equal, as lists in registry order, the lists F1 froze. The warm-up enters no
    list, pair, count or estimate."""
    from tests.cli_regression.support.g1e import calibration as cal
    if freeze["options"]["calibration"] == "none":
        return [], None
    out: list[str] = []
    cells = cal.chain_cells(freeze["scheduleSeed"])
    scored: dict[int, dict[int, dict]] = {cell: {} for cell in ref.CELLS}          # cell -> slot -> {"path", "start", "scalars" (None if invalid)}
    warmups: list[dict] = []
    for receipt in f1["calibration"]["receipts"]:
        path = receipt["path"]
        match, warm_match = CALIBRATION_NAME.match(path), CALIBRATION_WARMUP_NAME.match(path)
        if not (match or warm_match):
            out.append(f"calibration: {path!r} is not the name of a Python calibration attempt or of its warm-up")
            continue
        label = f"warm-up receipt {path}" if warm_match else path
        try:
            if not bundle.resolves(receipt):
                out.append(f"calibration: {label} is not in the manifest with the digest F1 froze")
                continue
            trial = bundle.read_json(path)
        except BundleError as error:
            out.append(f"calibration: {label}: {error}")
            continue
        if not isinstance(trial, dict):
            out.append(f"calibration: {label} is not a receipt")
            continue
        wrong = []
        if trial.get("schema") != "acr-g1e-trial/1" or trial.get("kind") != "calibration" or trial.get("measuring") is not False:
            wrong.append("not a calibration receipt")
        if warm_match:
            if (trial.get("selectedRuntime"), trial.get("count"), trial.get("repeat"), trial.get("slot")) != ("python", cells[0], 0, None) or trial.get("warmup") is not True:
                wrong.append(f"not the calibration warm-up (a warm-up runs in the first chain's cell {cells[0]}, repeat 0, without a slot)")
        else:
            cell, repeat = int(match.group(1)), int(match.group(2))
            if (trial.get("selectedRuntime"), trial.get("count"), trial.get("repeat")) != ("python", cell, repeat) or trial.get("warmup") is not False:
                wrong.append("the receipt is not the scored attempt its name says")
        if trial.get("freezeSha256") != f0_sha:
            wrong.append("not bound to F0")
        if trial.get("evidenceRevision") != freeze["evidenceRevision"] or trial.get("methodRevision") != freeze["methodRevision"]:
            wrong.append("another evidence or method revision than F0's")
        if wrong:
            out.append(f"calibration: {label}: " + "; ".join(wrong))
            continue
        if warm_match:
            problems, _samples = _judge_attempt(bundle, freeze, path, trial, "python", cells[0])
            problems = _warmup_problems_of(trial, problems)
            if problems:
                out.append(f"calibration: warm-up {path} did not run cleanly (a failed warm-up is a stop): " + "; ".join(problems[:3]))
            warmups.append({"path": path, "start": _start_ms(trial)})
            continue
        slot = trial.get("slot")
        if not summarise.is_int(slot) or slot >= cal.CHAIN_LENGTH:
            out.append(f"calibration: {path}: its slot must be an integer 0..{cal.CHAIN_LENGTH - 1}")
            continue
        if slot in scored[cell]:
            out.append(f"calibration: cell {cell}: slot {slot} is claimed by both {scored[cell][slot]['path']} and {path}")
            continue
        problems, samples = _judge_attempt(bundle, freeze, path, trial, "python", cell)
        scored[cell][slot] = {"path": path, "start": _start_ms(trial), "scalars": None if problems else summarise.trial_scalars({**trial, "_samples": samples})}    # invalid evidence: retained, no value
    if len(warmups) != 1:
        out.append(f"calibration: F1 lists {len(warmups)} warm-up receipts; v14-r2 V19 runs exactly one discarded calibration warm-up")
    for cell in ref.CELLS:
        if not out and len(scored[cell]) != cal.CHAIN_LENGTH:
            out.append(f"calibration: cell {cell} lists {len(scored[cell])} scored attempts; v14-r2 V19 needs {cal.CHAIN_LENGTH} (an incomplete calibration gives no F1)")
    if out:
        return out, None
    # the start time of every attempt must follow the frozen order: the warm-up, then chain 0 slot 0..9, then chain 1 slot 0..9
    sequence = [("the warm-up", warmups[0]["start"])] + [(f"{scored[cell][slot]['path']} (cell {cell}, slot {slot})", scored[cell][slot]["start"]) for cell in cells for slot in range(cal.CHAIN_LENGTH)]
    last_label, last = None, None
    for label, start in sequence:
        if start is None:
            continue
        if last is not None and not start > last:
            out.append(f"calibration: the start time of {label} is not after the start time of {last_label}: it contradicts the frozen slot order (warm-up, then slot 0..9 of each chain)")
        last_label, last = label, start
    if out:
        return out, None
    chains = {cell: [scored[cell][slot]["scalars"] for slot in range(cal.CHAIN_LENGTH)] for cell in ref.CELLS}
    report = cal.rows_calibration(chains)
    for key, name in (("noisyRows", "noisy"), ("uncalibratedRows", "uncalibrated")):
        if report[key] != f1["calibration"][key]:
            out.append(f"calibration: the {name} rows frozen in F1 are not the rows recomputed from the receipts, as a list in registry order (v14-r2 V19 adjacent estimator)")
    return out, (None if out else report)


def matrix_warmup_problems(bundle: Bundle, freeze: dict, f1_sha: str) -> list[str]:
    """The two discarded matrix warm-ups (v14-r2 V19: cell 4, Python then Rust shell, before the first scheduled pair) must be in the bundle, bound to F1, of the right arm and cell, and must
    have run cleanly (the recomputed validity and every phase passed): a missing or failed warm-up is a stop for the manager, so the document is refused. They are never trials."""
    from tests.cli_regression.support.g1e import calibration as cal
    expected = {f"warmup/{summarise.warmup_file_name(w['mode'], w['index'])}.json": w for w in cal.matrix_warmups()}
    out = []
    starts: list[tuple[str, float | None]] = []          # the warm-ups in the registered order (Python, then Rust shell), with the wall-clock start of each
    for name in bundle.names():
        if name.startswith("warmup/") and name not in expected and not (name.endswith(summarise.RESOURCES_SUFFIX) and name[:-len(summarise.RESOURCES_SUFFIX)] + ".json" in expected):
            out.append(f"warm-up: unexpected file {name}")
    for name, warm in expected.items():
        if not bundle.exists(name):
            out.append(f"warm-up: {name} is missing (v14-r2 V19 runs a discarded warm-up of each arm before the first scheduled pair)")
            continue
        try:
            trial = bundle.read_json(name)
        except BundleError as error:
            out.append(f"warm-up: {error}")
            continue
        if not isinstance(trial, dict):
            out.append(f"warm-up: {name} is not a receipt")
            continue
        wrong = []
        if (trial.get("schema") != "acr-g1e-trial/1" or trial.get("kind") != "measured" or trial.get("measuring") is not False or trial.get("warmup") is not True
                or trial.get("slot", 0) is not None or trial.get("firstArm", 0) is not None):
            wrong.append("not a matrix warm-up receipt (kind measured, measuring false, warmup true, no slot and no first arm)")
        if (trial.get("selectedRuntime"), trial.get("count"), trial.get("repeat")) != (warm["mode"], warm["cell"], warm["index"]):
            wrong.append(f"not the {warm['mode']} warm-up of cell {warm['cell']}")
        if trial.get("freezeSha256") != f1_sha:
            wrong.append("not bound to F1")
        if trial.get("evidenceRevision") != freeze["evidenceRevision"] or trial.get("methodRevision") != freeze["methodRevision"]:
            wrong.append("another evidence or method revision than F0's")
        if warm["mode"] == "rust-shell" and trial.get("expectedSidecarSha256") != freeze["sidecarSha256"]:
            wrong.append("expected sidecar hash differs from the freeze")
        if wrong:
            out.append(f"warm-up: {name}: " + "; ".join(wrong))
            continue
        problems, _samples = _judge_attempt(bundle, freeze, name, trial, warm["mode"], warm["cell"])
        problems = _warmup_problems_of(trial, problems)
        if problems:
            out.append(f"warm-up: {name} did not run cleanly (a failed warm-up is a stop): " + "; ".join(problems[:3]))
        starts.append((name, _start_ms(trial)))
    if out:
        return out
    # the registered order (v14-r2 V19 item 2): the Python warm-up, then the Rust warm-up, then the first scheduled pair, by the start time of each receipt
    first = min(((_start_ms(trial), scored) for scored, trial in _scored_matrix_receipts(bundle) if _start_ms(trial) is not None), default=None)
    sequence = starts + ([(f"the earliest scored attempt {first[1]}", first[0])] if first is not None else [])
    last_label, last = None, None
    for label, start in sequence:
        if start is None:
            out.append(f"warm-up: {label} has no start time (its first phase carries none), so the registered warm-up order cannot be checked")
            continue
        if last is not None and not start > last:
            out.append(f"warm-up: the start time of {label} is not after the start time of {last_label}: it contradicts the registered order (the Python warm-up, the Rust warm-up, then the first scored pair)")
        last_label, last = label, start
    return out


def _scored_matrix_receipts(bundle: Bundle) -> list[tuple[str, dict]]:
    """The matrix attempt receipts that can be read (an unreadable one is the matrix loader's finding, not this check's)."""
    found = []
    for name in bundle.names():
        if name.startswith("trials/") and name.endswith(".json"):
            try:
                trial = bundle.read_json(name)
            except BundleError:
                continue
            if isinstance(trial, dict):
                found.append((name, trial))
    return found


def _earlier_runs(bundle: Bundle, freeze: dict, own_sha: str, earlier: tuple | list) -> list[dict]:
    """The carried-fact records of the earlier runs. The runs supplied must be exactly the runs F0 names (V7 item 5): leaving one out would leave its stop facts out."""
    given = [(Path(directory), sha) for directory, sha in earlier]
    digests = [sha for _, sha in given]
    if len(set(digests)) != len(digests) or own_sha in digests:
        raise EvidenceInvalid(["an earlier run is listed twice, or is this run"])
    if sorted(digests) != sorted(freeze["earlierRuns"]):
        raise EvidenceInvalid(["the earlier runs supplied are not the earlier runs F0 names (a run cannot be dropped to drop its stop facts)"])
    return sorted((carry.run_facts(directory, sha) for directory, sha in given), key=lambda run: run["manifestSha256"])


_ownership_failure = summarise.ownership_failure
_isolation_breach = summarise.isolation_breach
_identity_ok = summarise.identity_ok


def decide(bundle_dir: Path | str, manifest_sha256: str, earlier: tuple | list = (), *, repo: Path | str | None = None, locality_recheck=None, approval_check=None) -> Decision:
    """The only entry point. `earlier` = [(bundle_dir, manifest_sha256), ...] of the sealed earlier runs under this pre-registration (V7 item 5). `repo` = the F0 tree, in which the frozen
    locality check is re-run (`locality_recheck` replaces that re-run; the tests of synthetic bundles pass a stand-in). Anything unexpected raises `EvidenceInvalid`: a crash is never an outcome
    and never CONTINUE."""
    try:
        if locality_recheck is None and repo is not None:
            locality_recheck = make_locality_recheck(Path(repo))
        if approval_check is None and repo is not None:
            approval_check = make_approval_check(Path(repo))
        return _decide(bundle_dir, manifest_sha256, earlier, locality_recheck, approval_check)
    except EvidenceInvalid:
        raise
    except Exception as error:   # fail closed on any defect, including a malformed record the builder did not anticipate
        raise EvidenceInvalid([f"unexpected {type(error).__name__} while deciding: {str(error)[:200]}"]) from None


def _decide(bundle_dir: Path | str, manifest_sha256: str, earlier: tuple | list = (), locality_recheck=None, approval_check=None) -> Decision:
    try:
        bundle = Bundle(Path(bundle_dir), manifest_sha256)
        whole = bundle.problems()
    except BundleError as error:
        raise EvidenceInvalid([str(error)]) from None
    if whole:
        raise EvidenceInvalid(whole)
    freeze = _need(bundle, summarise.FREEZE_NAME)
    f0_sha = bundle.digest(summarise.FREEZE_NAME)
    bad = summarise.freeze_problems(freeze, f0_sha)
    run = _need(bundle, summarise.RUN_NAME)
    bad += summarise.run_problems(run)
    if bad:
        raise EvidenceInvalid([f"freeze: {m}" for m in bad])
    if approval_check is None:
        raise EvidenceInvalid(["approval: F0's approval cannot be checked against the ledger without the F0 tree (give the decider --repo, or an approval check)"])
    if approval_problems := approval_check(freeze):
        raise EvidenceInvalid(approval_problems)
    names = bundle.names()
    path = "matrix" if any(n.startswith("trials/") for n in names) else "no-matrix"   # a matrix attempt exists as soon as one trial file does, valid or not
    f1 = None
    cal_rows = None
    freeze_sha = f0_sha
    if path == "matrix" or bundle.exists("reader.json") or bundle.exists(summarise.F1_NAME):   # the matrix and the reader-measure run are under F1 (V10 item 1)
        f1 = _need(bundle, summarise.F1_NAME)
        bad = summarise.f1_problems(f1, f0_sha, freeze)
        if not bad:
            bad, report = calibration_report(bundle, freeze, f0_sha, f1)              # an F1 exists only after a complete calibration (V10 item 4, v10 4.5)
            cal_rows = report["rows"] if report is not None else None
        if bad:
            raise EvidenceInvalid([f"F1: {m}" for m in bad])
        if path == "matrix" or bundle.exists("reader.json"):
            freeze_sha = bundle.digest(summarise.F1_NAME)
        if path == "matrix" and (bad := matrix_warmup_problems(bundle, freeze, freeze_sha)):       # v14-r2 V19: a missing or failed matrix warm-up is a stop, not a missing value
            raise EvidenceInvalid(bad)
    earlier_runs = _earlier_runs(bundle, freeze, manifest_sha256, earlier)
    matrix = summarise.load_matrix(bundle, freeze, freeze_sha)
    doc, summary = build_document(bundle, freeze, freeze_sha, matrix, path, earlier_runs, f1, locality_recheck, cal_rows)
    hosted, review_ok = doc.pop("_hosted_authorization"), doc.pop("_review_ok")
    try:
        ref.recompute(doc)                        # every declared count/status/fact/outcome (and the noisy and C1 reports, the interpretation, the k limitation) is derived from the pairs and lists, never read
    except Exception as error:
        raise EvidenceInvalid([f"derivation: {type(error).__name__}: {str(error)[:200]}"]) from None
    if bad := noisy_calibration_problems(doc, cal_rows):          # the calibration half of each noisy-row entry must be what the Python receipts say (the reference can only check the matrix half)
        raise EvidenceInvalid(bad)
    # gates: derived from the derived outcome, the review and recorded authorizations
    e5_missing = []
    if doc["outcome"] != "continue":
        e5_missing.append("outcome is not continue")
    if not review_ok:
        e5_missing.append("joint review pass")
    if not hosted:
        e5_missing.append("user authorization for hosted Actions (D18)")
    doc["gates"]["ticketE5"] = {"open": not e5_missing, "missing": e5_missing}
    doc["gates"]["g2"] = {"open": False, "missing": ["E5 functional pass"] + ([] if doc["continuationAuthority"].get("recorded") else ["continuation authority"])}
    verdict = ref.document_ok(doc, mode="production")
    if not verdict.ok:
        raise EvidenceInvalid([f"the outcome document is not valid: {verdict.kind}: {verdict.why}"])
    problems = [f"unresolved reference {r}" for r in unresolved_refs(bundle, doc)] + baseline_distinctness(doc)
    reports = [r["sha256"] for r in doc["jointReview"]["reports"]]
    if doc["jointReview"]["status"] == "pass" and len(set(reports)) != len(reports):
        problems.append("the joint review reports are not distinct by hash")
    if problems:
        raise EvidenceInvalid(problems)
    facts = doc["decision"]["facts"]
    outcome, reasons = ref.decide(facts, ref.derivation_path(doc), ref.carried_stop_codes(doc))
    if doc["outcome"] != outcome:
        raise EvidenceInvalid(["the document outcome differs from the table's outcome for its facts"])
    return Decision(outcome=outcome, reasons=reasons, facts=facts, document=doc, summary=summary)
