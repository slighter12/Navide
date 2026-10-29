"""G3-r2 round 2: the pass criterion of a rehearsal (manager review of rehearsal 1).

Rehearsal 1 ended `inconclusive` and that was read as "expected with reduced counts"; the same outcome hid a defect (`freezeCheckedInputs: 0` made `load_matrix` reject every shell trial). A rehearsal
passes only if (1) the SAME admission and validation code the registered `decide` uses accepts every receipt of every attempt class (no integrity, invalid or problem entry), and (2) every reason of
its outcome is PROVABLY caused by n below the registered count, with the code line that proves it. A reason the code cannot prove count-driven is a defect and fails the verdict.

Output: booleans, reason codes, counts and file:line proofs. No timing, CPU or memory value is read into it (a row's n, e and status are counts; no pair value is touched).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path

from tests.cli_regression.support.g1e import assemble as g1e_assemble
from tests.cli_regression.support.g1e import bundle as g1e_bundle
from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g3 import assemble, bundle as g3bundle, decider, launch, margins, packaged

SCHEMA = "acr-g3r2-rehearsal-verdict/1"
REGISTERED_MATRIX_TRIALS = 40


def source_line(module, needle: str) -> str:
    """`dir/file.py:line` of the first line of `module`'s source containing `needle`; raises when the code no longer holds it (the proof then needs re-reading, so the verdict must not pass)."""
    path = Path(inspect.getsourcefile(module))
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return f"{path.parent.name}/{path.name}:{number}"
    raise LookupError(f"{needle!r} not found in {path.name}")


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _short(texts, limit: int = 240) -> list[str]:
    return [str(t)[:limit] for t in texts][:20]


def _trial_check(sealed: g1e_bundle.Bundle, name: str, freeze: dict, mode: str) -> list[str]:
    """structure + consistency + trial problems of one calibration or warm-up receipt (what `load_matrix` does per matrix trial; `shell_chains` alone skips the structure check)."""
    trial = sealed.read_json(name)
    shape, _samples = summarise.structure_problems(trial, freeze, sealed, name[:-len(".json")] + summarise.RESOURCES_SUFFIX)
    out = list(shape)
    if not shape:
        out += summarise.consistency_problems(trial)
        out += summarise.trial_problems({k: v for k, v in trial.items() if k != "_samples"}, freeze, mode, trial.get("count"))
    return out


def admission(out_dir: Path, f0: dict, f1_path: Path) -> dict:
    """Every receipt of every attempt class through the unchanged admission/validation code. Returns the findings (empty lists = admitted) and the counts."""
    out_dir = Path(out_dir)
    manifest = (out_dir / "logs" / "manifest.sha256").read_text(encoding="utf-8").strip()
    sealed = g1e_bundle.Bundle(out_dir / "bundle", manifest)
    f1_sha = hashlib.sha256(Path(f1_path).read_bytes()).hexdigest()
    view = g3bundle.freeze_view(f0)
    matrix, _rows = g3bundle.shell_matrix(sealed, f0, f1_sha)
    files = [n for n in sealed.names() if n.startswith("trials/") and n.endswith(".json")]
    problems_by_trial = {summarise.trial_id(*s): list(p) for s, p in matrix["problems"].items() if p}
    shell = {"files": len(files), "admitted": len(matrix["trials"]), "integrity": _short(matrix["integrity"]), "invalid": [summarise.trial_id(*s) for s in matrix["invalid"]], "problems": problems_by_trial,
             "missing": len(matrix["missing"])}
    warm_names = sorted(n for n in sealed.names() if n.endswith(".json") and re.match(r"(warmup|calibration)/(warmup|trial)-", n))
    warm = {name: _short(_trial_check(sealed, name, view, "rust-shell" if "rust" in name else "python")) for name in warm_names}
    cal_shell = {name: p for name, p in warm.items() if name.startswith("calibration/")}
    launch_receipts, launch_invalid, launch_missing = assemble.launch_matrix_receipts(out_dir / "bundle", f0, f1_sha)
    shell_cal_record, cal_detail = assemble.build_calibration(f0, shell_dir=out_dir / "calibration" / "calibration", launch_dir=out_dir / "calibration", reader_dir=out_dir / "bundle-reader" / "reader",
                                                              calibration_freeze_sha256=hashlib.sha256((out_dir / "bundle" / "freeze.json").read_bytes()).hexdigest())
    launch_cal_invalid = [d for d in cal_detail["launchInvalid"] if d["problems"] != ["missing or unreadable"]]
    launch_cal_missing = sum(1 for d in cal_detail["launchInvalid"] if d["problems"] == ["missing or unreadable"])
    reader = sealed.read_json("reader.json")
    reader_bad = [rid for rid, row in reader["rows"].items() if any(p.get("valid") is not True for p in row["pairs"])]
    packaged_dir = out_dir / "bundle" / "packaged-trials"
    packaged_summary = packaged.summary(packaged_dir, [])
    packaged_receipts = sorted(packaged_dir.glob("trial-*.json")) if packaged_dir.is_dir() else []
    packaged_bad = [p.name for p in packaged_receipts if _json(p).get("passed") is not True]
    return {
        "shellMatrix": shell,
        "warmupAndCalibrationReceipts": {"checked": len(warm), "withProblems": {n: p for n, p in warm.items() if p}},
        "calibrationShellInvalid": _short(d["file"] for d in cal_detail["shellInvalid"]), "calibrationLaunchInvalid": [d["file"] for d in launch_cal_invalid], "calibrationLaunchMissing": launch_cal_missing,
        "launchMatrix": {"admitted": len(launch_receipts), "invalid": [d["file"] for d in launch_invalid], "missing": len(launch_missing)},
        "reader": {"rows": len(reader["rows"]), "rowsWithInvalidPair": reader_bad},
        "packaged": {"receipts": len(packaged_receipts), "notPassed": packaged_bad, "measured": packaged_summary["status"]},
        "_matrix": matrix, "_f1": _json(f1_path), "_launch_missing": len(launch_missing),
    }


def admission_defects(found: dict) -> list[str]:
    """Anything an admission step rejected. Missing receipts are a count effect; a receipt that exists and is rejected is a defect."""
    out = []
    shell = found["shellMatrix"]
    if shell["integrity"]: out.append(f"shell matrix: {len(shell['integrity'])} receipt(s) rejected as integrity failures: {shell['integrity'][:3]}")
    if shell["invalid"]: out.append(f"shell matrix: invalid receipts {shell['invalid']}")
    if shell["problems"]: out.append(f"shell matrix: trial problems {shell['problems']}")
    if shell["files"] != shell["admitted"]: out.append(f"shell matrix: {shell['files']} receipt files, {shell['admitted']} admitted")
    if shell["admitted"] == 0: out.append("shell matrix: no receipt was admitted, so nothing downstream was exercised")
    if found["warmupAndCalibrationReceipts"]["withProblems"]: out.append(f"warm-up/calibration receipts rejected: {found['warmupAndCalibrationReceipts']['withProblems']}")
    if found["calibrationShellInvalid"]: out.append(f"shell calibration invalid: {found['calibrationShellInvalid']}")
    if found["calibrationLaunchInvalid"]: out.append(f"launch calibration invalid: {found['calibrationLaunchInvalid']}")
    if found["launchMatrix"]["invalid"]: out.append(f"launch matrix invalid: {found['launchMatrix']['invalid']}")
    if found["launchMatrix"]["admitted"] == 0: out.append("launch matrix: no receipt was admitted")
    if found["reader"]["rowsWithInvalidPair"]: out.append(f"reader rows with an invalid pair: {found['reader']['rowsWithInvalidPair']}")
    if found["packaged"]["notPassed"]: out.append(f"packaged receipts not passed: {found['packaged']['notPassed']}")
    if found["packaged"]["receipts"] == 0: out.append("packaged: no receipt")
    return out


# ---------------------------------------------------------------------------------------------------------------- reason classification
def _canaries_short(matrix: dict, f0: dict) -> bool:
    """True iff every canary series lacks values (< 10 finite), the only way `canary_trip` is None for a rehearsal that admitted its trials."""
    series = summarise.canary_values(g3bundle.freeze_view(f0), matrix)
    return all(len([v for v in values if v is not None]) < ref.N_PAIRS for values in series.values())


def _proofs() -> dict[str, str]:
    """file:line of each statement a count-driven classification rests on. A needle that is gone raises: the proof is then stale and the verdict cannot pass."""
    return {"complete": source_line(g3bundle, 'complete = (not matrix["integrity"]'), "design": source_line(g3bundle, "design = (len(trials) == 40"),
            "trip": source_line(ref, "if len(v) != 10 or any(not finite(x) for x in v): return None"), "status": source_line(ref, "if n < N_PAIRS: return 'unresolved'"),
            "minpairs": source_line(margins, "if len(diffs) < cal.MIN_VALID_PAIRS")}


def classify(reason: str, outcome: dict, found: dict, f0: dict) -> tuple[str, str]:
    """('count-driven' | 'defect', proof). Count-driven only when the condition is recomputed from receipts and the registered constant and the code line that makes it so is found."""
    P = _proofs()
    matrix, f1 = found["_matrix"], found["_f1"]
    trials = len(matrix["trials"])
    table = outcome["rowTable"]
    if reason == "I-matrix-incomplete":
        ok = trials < REGISTERED_MATRIX_TRIALS and not matrix["integrity"] and not matrix["invalid"] and bool(matrix["missing"]) and found["launchMatrix"]["invalid"] == []
        return ("count-driven", f"{trials} < {REGISTERED_MATRIX_TRIALS} admitted shell trials, none rejected; {P['complete']} needs `not matrix['missing']`") if ok else ("defect", "incomplete although nothing is missing, or something was rejected")
    if reason == "I-design-noncompliant":
        identities = [(t["backend"]["pid"], t["backend"]["start"]) for t in matrix["trials"].values() if isinstance(t.get("backend"), dict)]
        others = (len(set(identities)) == len(identities) and ref.schedule_sha(matrix["schedule"]) == f0["scheduleSha256"]
                  and all(sum(1 for s in matrix["schedule"] if s["cell"] == c and s["firstArm"] == "python") == 5 for c in ref.CELLS))
        return (("count-driven", f"only `len(trials) == 40` fails ({trials} admitted), the identity, schedule-hash and first-arm balance conjuncts hold: {P['design']}")
                if trials < REGISTERED_MATRIX_TRIALS and others else ("defect", "a design conjunct other than the trial count fails"))
    if reason == "I-independence-screen-failed":
        return (("count-driven", f"every canary series holds fewer than {ref.N_PAIRS} values, so `canary_trip` is None: {P['trip']}")
                if _canaries_short(matrix, f0) else ("defect", "a canary series is complete and the screen still failed"))
    if reason.startswith("I-row-unresolved:"):
        fact = reason.split(":", 1)[1]
        cls = next(c for c, f in decider.CLASS_FACTS.items() if f == fact)
        rows = [r for r in table if r["cls"] == cls]
        bad = [r["id"] for r in rows if r["status"] == "unresolved" and not (r["n"] < ref.N_PAIRS) and r["status"] != "uncalibrated"]
        adverse = [r["id"] for r in rows if r["status"] == "adverse"]
        return (("count-driven", f"every unresolved row of class {cls} has n < {ref.N_PAIRS}: {P['status']}") if not bad and not adverse
                else ("defect", f"rows unresolved with n >= {ref.N_PAIRS} or adverse: {(bad + adverse)[:5]}"))
    if reason == "I-uncalibrated-row":
        cal_rows = f1["calibration"]["rows"]
        bad = [rid for rid in f1["calibration"]["uncalibratedRows"] if (cal_rows.get(rid) or {}).get("validPairs", 0) >= cal.MIN_VALID_PAIRS]
        return (("count-driven", f"every uncalibrated row has fewer than {cal.MIN_VALID_PAIRS} valid adjacent pairs ({len(f1['calibration']['uncalibratedRows'])} rows): {P['minpairs']}")
                if not bad else ("defect", f"uncalibrated rows with enough pairs: {bad[:5]}"))
    return "defect", "not a reason the code can prove count-driven (adverse, quiet-gate, reader-fixture, noisy-guard, adopt or unknown)"


def verdict(out_dir: Path, f0_path: Path, f1_path: Path) -> dict:
    out_dir = Path(out_dir)
    f0 = _json(f0_path)
    found = admission(out_dir, f0, f1_path)
    outcome = _json(out_dir / "outcome.json")
    defects = admission_defects(found)
    detail = (outcome.get("evidenceDetail") or {}).get("matrix")
    if not (isinstance(detail, dict) and set(detail) == {"integrity", "invalid", "problems"}):
        defects.append("outcome.json does not surface evidenceDetail.matrix {integrity, invalid, problems}")
    elif detail["integrity"] or detail["invalid"] or detail["problems"]:
        defects.append(f"outcome.json evidenceDetail.matrix is not empty: {_short([json.dumps(detail)])}")
    reasons = []
    for reason in outcome["reasons"]:
        kind, proof = classify(reason, outcome, found, f0)
        reasons.append({"reason": reason, "classification": kind, "proof": proof})
        if kind != "count-driven":
            defects.append(f"reason {reason} is not provably count-driven: {proof}")
    public = {k: v for k, v in found.items() if not k.startswith("_")}
    return {"schema": SCHEMA, "passed": not defects, "defects": defects, "outcome": outcome["outcome"], "reasons": reasons, "facts": outcome["facts"], "admission": public,
            "note": "booleans, reason codes, counts and file:line proofs only; no timing, CPU or memory value"}
