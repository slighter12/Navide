"""The registered outcome of a run that stopped early (review-1 finding 9, review-3 finding 2; proposal 5.2). Everything is derived by code from the saved run record and receipts, nothing is entered
by hand:

* `survivorsRustArm`: a Rust-arm shell trial (warm-ups included) with an ownership failure (the unchanged `summarise.ownership_failure`), or a Rust-arm launch receipt with a surviving pid.
* `isolationBreach`: a shell trial whose backend identity changed (the unchanged `summarise.isolation_breach`), or a launch receipt with `isolationBreach`.
* `artifactIdentity`: the sidecar file is REHASHED now and compared with F0 and `sidecar-build.json` (`helper.artifact_identity`).
* `matrixComplete`: the `matrix` and `launch-matrix` steps both ended clean in the run record (a stop after them is a stop after a completed matrix).
* `quietGateTimedOut` / `readerFixtureMissing`: the stop reason.

`decider.decide` then applies section 5.2 with adverse before inconclusive. No row status can be derived from a run that did not produce the rows, so every class status is `unresolved`. A record
that is not complete and carries no stop reason (the process died or raised mid-step) is classified `crash`; it is never resumed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g3 import bundle as g3bundle, decider, packaged

QUIET = "quiet-gate-timeout"
FIXTURE = "reader-fixture-missing"
CRASH = "crash"
MATRIX_STEPS = ("matrix", "launch-matrix")


def _json_files(directory: Path):
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "run.json":
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict):
            yield body


def packaged_identity(repo: Path, record: object, f0: dict, run_dir: Path | None, last_code_commit_ts: int | None) -> str:
    """The packaged artifact's identity for the early outcome (review-4 finding 1): the app's backend and runtime are REHASHED now against the packaged-build record and F0's sidecar, and a mismatch
    the packaged loop itself saw (`stopReason artifact-mismatch` in the saved packaged run) counts even if the bytes were restored since."""
    problems = packaged.artifact_problems(repo, record, (f0.get("sidecar") or {}).get("sha256", ""), f0.get("head", ""), last_code_commit_ts)
    if run_dir is not None:
        try:
            saved = json.loads((Path(run_dir) / "m" / "packaged-trials" / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if isinstance(saved, dict) and saved.get("stopReason") == "artifact-mismatch":
            problems.append("the packaged run stopped on an artifact mismatch")
    return packaged.identity_status(problems)


def evidence(run_dir: Path | None, f0: dict, run_record: dict, build_record: dict | None, last_code_commit_ts: int | None, packaged_state: tuple[Path, object] | None = None) -> dict:
    """The derived non-row facts of a stopped run (see the module doc). `run_dir` = OUT/g3/run; None = nothing was saved. `packaged_state` = (repo, packaged-build record or None): the packaged
    identity is merged into `artifactIdentity`."""
    from tests.cli_regression.support.g3 import helper
    survivors = breach = False
    if run_dir is not None:
        for trial in _json_files(Path(run_dir) / "m" / "matrix" / "trials"):
            if trial.get("selectedRuntime") == "rust-shell" and summarise.ownership_failure(trial):
                survivors = True
            if "backend" in trial and summarise.isolation_breach(trial):
                breach = True
        for receipt in _json_files(Path(run_dir) / "m" / "launch-trials"):
            if receipt.get("selectedRuntime") == "rust-shell" and any(isinstance(p, int) and p > 0 for p in receipt.get("survivors") or []):
                survivors = True
            if receipt.get("isolationBreach") is True:
                breach = True
    sidecar = Path((f0.get("sidecar") or {}).get("path", ""))
    now = hashlib.sha256(sidecar.read_bytes()).hexdigest() if sidecar.is_file() else ""
    identity = helper.artifact_identity(f0, now, build_record, last_code_commit_ts)
    if packaged_state is not None:
        identity = packaged.merge_identity(identity, packaged_identity(packaged_state[0], packaged_state[1], f0, run_dir, last_code_commit_ts))
    done = {s.get("name") for s in run_record.get("steps", []) if s.get("exit") == 0}
    return {"survivorsRustArm": survivors, "isolationBreach": breach, "artifactIdentity": identity, "matrixComplete": all(name in done for name in MATRIX_STEPS)}


def facts(stop_reason: str | None, derived: dict | None = None) -> dict:
    derived = derived or {}
    return {"matrixComplete": derived.get("matrixComplete", False), "designCompliant": False, "independenceScreen": "pass", "artifactIdentity": derived.get("artifactIdentity", "ok"),
            "survivorsRustArm": derived.get("survivorsRustArm", False), "isolationBreach": derived.get("isolationBreach", False),
            **{fact: "unresolved" for fact in decider.STATUS_FACTS}, "noisyAdverse": False, "noisyGuard": False, "uncalibratedRow": False,
            "readerFixtureMissing": stop_reason == FIXTURE, "quietGateTimedOut": stop_reason == QUIET}


def stop_reason(run_record: dict) -> str:
    return run_record.get("stopReason") or CRASH


def document(f0: dict, run_record: dict, *, run_dir: Path | None = None, build_record: dict | None = None, last_code_commit_ts: int | None = None,
             packaged_state: tuple[Path, object] | None = None) -> dict:
    """The `acr-g3-cost-outcome/1` document of an incomplete run. Refuses a record that is complete (that run has a sealed bundle and goes through `decide`)."""
    if run_record.get("complete") is True:
        raise ValueError("the run is complete: use the full decider")
    stop = stop_reason(run_record)
    derived = evidence(run_dir, f0, run_record, build_record, last_code_commit_ts, packaged_state)
    artifacts = []
    if build_record:
        artifacts.append({"name": "navide-cli-runtime", "sha256": f0["sidecar"]["sha256"], "builtAt": build_record.get("builtAt", ""), "predatesCode": packaged.predates(build_record.get("builtAtEpoch"), last_code_commit_ts)})
    if packaged_state is not None:
        artifacts += packaged.artifact_entries(packaged_state[1], build_record, last_code_commit_ts)
    doc = decider.build_outcome(facts=facts(stop, derived), source={"head": f0["head"], "tree": f0["trackedDiffSha256"], "artifacts": artifacts, "artifactIdentity": derived["artifactIdentity"]},
                                host_class=f0["hostClass"], calibration={}, regression=[], mechanisms=[], platforms=g3bundle.PLATFORMS, rows=[], c1=None)
    doc["incomplete"] = {"stopReason": stop, "derived": derived, "steps": [{"name": s.get("name"), "exit": s.get("exit"), "validity": s.get("validity")} for s in run_record.get("steps", [])]}
    return doc
