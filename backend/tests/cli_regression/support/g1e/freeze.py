"""The G1-E freeze records and their run-time re-verification (E2 v14-r2 section 4, V14, V15; v13 section 4 and V10; v10 section 13; the 06 practice `runtimeFreeze.ts`).

Two stages (V10 item 1):

* **F0** (`build_freeze`, `freeze.json`, schema `acr-g1e-freeze/2`), recorded after the user's dated approval and before any calibration: the SHA-256 of the pre-registration (E2 v14-r2) and, as
  bound texts beside it, v13's, v10's and E1 v8b's (`ruleFiles`; v10's digest is also `preregistration.baseSha256`), the
  bound E1, schema, fixture-registry and log-policy digests, the schedule seed and hash, the registered workload, the sidecar hash, HEAD, branch and the tracked-diff hash, the hash of
  the frozen-inputs listing, **the locality scope (every file the locality check scans, path and bytes, tracked or untracked), the Python-arm file list with loaded-byte hashes,
  the baseline-reference manifest, `E3set` and `U` (V5), the toolchain identities of node, pnpm, the backend
  venv python and the sidecar (V7 item 1), uv's identity (V8, informational), the earlier runs this round carries stop facts from (V7 item 5), the user's approval record, the frozen quiet-gate
  script with its registered rule and parameters (v14-r2 V14) and the verdicts carried from an older F0 by byte equality (v14-r2 V15 item 3)**.
  Python-only calibration and reader calibration run under F0 and verify it before every attempt (`verify_freeze`).
* **F1** (`build_f1`, `freeze-f1.json`, schema `acr-g1e-freeze-f1/2`), recorded after calibration and before the matrix: F0's SHA-256 + a re-verification of every F0 item (`F0_ITEMS`, deep:
  every manifest reference is read back and the Python arm is traced again) + the calibration receipts (the warm-up among them) with the noisy and the uncalibrated rows + the absolute reader margins. ANY difference between F0
  and the world refuses F1 (`FreezeError`; a toolchain difference is `ToolchainMismatch`, handled by the V7 exception, because no candidate attempt has run yet). The matrix and the
  reader-measure run verify F1 (`verify_f1`) before every attempt. F1 only adds calibration outputs: it never changes n, a margin or a row.

The seed is chosen by the manager before F0, recorded in it and never derived from data. Nothing here measures; it only binds.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import baseline_identity as bi
from tests.cli_regression.support.g1e import locality_check as lc
from tests.cli_regression.support.g1e import quiet_gate as qg
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e import toolchain as tc
from tests.cli_regression.support.g1e.bundle import Bundle, BundleError

FROZEN_INPUTS = [
    "tests/fixtures/cli-regression/runtime-workload-g1e.json", "tests/fixtures/cli-regression/g1e-schedule-golden.json",
    "backend/tests/cli_regression/support/g1e_peer.py", "backend/tests/cli_regression/support/baseline_peer.py", "backend/tests/cli_regression/support/g1e_counters_gate.py",
    "backend/tests/cli_regression/support/backend_entry.py", "backend/tests/cli_regression/support/reader_counters.py",
    "backend/tests/cli_regression/support/g1e/reference.py", "backend/tests/cli_regression/support/g1e/tokens.py", "backend/tests/cli_regression/support/g1e/summarise.py",
    "backend/tests/cli_regression/support/g1e/decider.py", "backend/tests/cli_regression/support/g1e/bundle.py", "backend/tests/cli_regression/support/g1e/gates.py",
    "backend/tests/cli_regression/support/g1e/locality_check.py", "backend/tests/cli_regression/support/g1e/locality-allowlist.json",
    "backend/tests/cli_regression/support/g1e/e2-g1e-outcome.schema.json", "backend/tests/cli_regression/support/g1e/freeze.py",
    "backend/tests/cli_regression/support/g1e/driver.py", "backend/tests/cli_regression/support/g1e/helper.py", "backend/tests/cli_regression/support/g1e/log_final.py",
    "backend/tests/cli_regression/support/g1e/calibration.py", "backend/tests/cli_regression/support/g1e/assemble.py",
    "backend/tests/cli_regression/support/g1e/baseline_identity.py", "backend/tests/cli_regression/support/g1e/toolchain.py", "backend/tests/cli_regression/support/g1e/carry.py",
    "backend/tests/cli_regression/support/g1e/quiet_gate.py", "backend/tests/cli_regression/support/g1e/report.py", "backend/tests/cli_regression/support/g1e/cli.py",
    "backend/tests/cli_regression/support/g1e/__init__.py", "backend/tests/cli_regression/support/g1e/runlog.py", "backend/tests/cli_regression/support/g1e/dryrun_check.py",
    "tests/cli/g1eSchedule.ts", "tests/cli/g1eBurst.ts", "tests/cli/g1eLedger.ts", "tests/cli/g1eWait.ts", "tests/cli/g1eStream.ts", "tests/cli/g1eReceipt.ts", "tests/cli/g1eRun.ts",
    "tests/cli/g1eAttempt.ts", "tests/cli/g1eMatrix.test.ts",
]

#: The g1e-related untracked files that are deliberately NOT bound: nothing on a real-run path (driver, assemble, F0/F1, decider, report, matrix runner) imports or runs them.
#: A new file under the g1e package, `support/g1e_*.py` or `tests/cli/g1e*.ts` must be in `FROZEN_INPUTS` or here (`test_every_g1e_file_is_bound_or_listed_test_only`).
TEST_ONLY = {
    "backend/tests/cli_regression/support/g1e/synthetic.py",           # builders for synthetic bundles; imported only by backend/tests/test_g1e_*.py
    "tests/cli/g1eBurst.test.ts", "tests/cli/g1eLedger.test.ts", "tests/cli/g1eReceipt.test.ts", "tests/cli/g1eRun.test.ts",
    "tests/cli/g1eSchedule.test.ts", "tests/cli/g1eStream.test.ts", "tests/cli/g1eWait.test.ts",
}


class FreezeError(Exception):
    """A freeze record cannot be built, or an F0 item no longer verifies: no F1, no attempt."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class ToolchainMismatch(FreezeError):
    """The observed toolchain differs from F0's: the V7 stop reason `toolchain-mismatch` (its own exception path), not an ordinary freeze difference."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def git_output(root: Path, *args: str) -> bytes:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return done.stdout


def build_inputs(root: Path, files: list[str] | None = None) -> list[dict]:
    return [{"path": name, "sha256": sha256_file(Path(root) / name)} for name in (files if files is not None else FROZEN_INPUTS)]


def tracked_diff_sha256(root: Path) -> str:
    return hashlib.sha256(git_output(root, "diff", "HEAD", "--", ".")).hexdigest()


def build_python_arm(root: Path, files: list[str]) -> dict:
    """F0's V5 record for the traced Python-arm file list: loaded-byte hashes, `E3set`, `U`, the reference manifest."""
    record = bi.identity_record(Path(root), files)
    return {"upstreamMerge": record["upstreamMerge"], "e3Parent": record["e3Parent"], "e3Commit": record["e3Commit"], "e3Set": record["e3Set"],
            "fileList": record["files"], "fileListSha256": bi.manifest_sha256(record["files"]), "referenceManifest": record["manifest"], "referenceManifestSha256": record["manifestSha256"]}


def earlier_run_problems(earlier: list[tuple[Path, str]]) -> list[str]:
    """V7 item 4 (i), the recorded check: the re-freeze exception is only for a round whose stopped run was stopped BY the toolchain check; every earlier run is either complete or such a run.
    Any other stop reason makes the exception refused (a run stopped for any other reason is final)."""
    problems, stopped_by_toolchain = [], 0
    for directory, sha in earlier:
        try:
            bundle = Bundle(Path(directory), sha)
            run = bundle.read_json(summarise.RUN_NAME)
        except BundleError as error:
            problems.append(f"earlier run {sha[:12]}: {error}")
            continue
        bad = summarise.run_problems(run)
        if bad:
            problems += [f"earlier run {sha[:12]}: {m}" for m in bad]
        elif run["stopReason"] == tc.STOP_REASON:
            refusal = tc.exception_refusal(run)
            problems += [f"earlier run {sha[:12]}: {m}" for m in refusal]
            stopped_by_toolchain += 0 if refusal else 1
        elif not run["complete"]:
            problems.append(f"earlier run {sha[:12]} was stopped for {run['stopReason']!r}, not by the toolchain check: the exception is refused")
    if earlier and not problems and stopped_by_toolchain == 0:
        problems.append("no earlier run was stopped by the toolchain check: there is no exception to take")
    return problems


def exception_record(earlier: list[tuple[Path, str]]) -> dict:
    """The recorded check of V7 item 4 (i), written beside F0 when a round restarts: which earlier run stopped for which reason and whether the exception is allowed."""
    rows = []
    for directory, sha in earlier:
        try:
            run = Bundle(Path(directory), sha).read_json(summarise.RUN_NAME)
            rows.append({"manifestSha256": sha, "kind": run.get("kind"), "stopReason": run.get("stopReason"), "complete": run.get("complete")})
        except BundleError as error:
            rows.append({"manifestSha256": sha, "error": str(error)})
    problems = earlier_run_problems(earlier)
    return {"schema": "acr-g1e-exception-check/1", "runs": rows, "allowed": not problems, "problems": problems}


#: Where the rule files live, relative to the repository root (local evidence, untracked). F0 hashes the files itself; nothing is taken from the caller.
DEFAULT_RULE_PATHS = {
    "v14r2": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v14-r2.md",
    "v13": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v13.md",
    "v10": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v10.md",
    "e1v8b": ".scratch/agent-cli-runtime-evaluation/design/e1-runtime-protocol-v8b.md",
}


def ledger_path(root: Path, rules_root: Path | None = None, ledger: Path | None = None) -> Path:
    """The progress ledger file: `ledger` if given, else the repository's (`rules_root` or `root`)."""
    return Path(ledger) if ledger else Path(rules_root or root) / summarise.LEDGER_RELATIVE


def ledger_text(root: Path, rules_root: Path | None = None, ledger: Path | None = None) -> str | None:
    """The progress ledger as it is NOW (verification time); None when it cannot be read."""
    try:
        return ledger_path(root, rules_root, ledger).read_text(encoding="utf-8")
    except OSError:
        return None


def approval_check(root: Path, approval, rules_root: Path | None = None, ledger: Path | None = None) -> list[str]:
    """The one approval check (`summarise.approval_problems`) every consumer of F0 makes: the ledger is read NOW, from the file `ledger_path` names, and `approval['ledger']` is resolved against the
    repository root (`rules_root` or `root`; `root` when a ledger file is given explicitly)."""
    base = Path(root) if ledger else Path(rules_root or root)
    return summarise.approval_problems(approval, ledger_text(root, rules_root, ledger), ledger_path=ledger_path(root, rules_root, ledger), base=base)


def locality_scope_problems(root: Path, recorded) -> list[str]:
    """F0's frozen locality scope against the tree NOW: the path set must be the same (a file added to, or removed or renamed out of, the scope) and every scoped file must have F0's bytes.
    Tracked and untracked files alike: the scope is read from the working tree, not from git."""
    if not (isinstance(recorded, dict) and isinstance(recorded.get("files"), list) and all(isinstance(f, dict) and {"path", "sha256"} <= set(f) for f in recorded["files"])):
        return ["locality scope: F0 holds no usable scope record"]
    try:
        now = lc.scope_record(Path(root))
    except (OSError, lc.ScopeError) as error:
        return [f"locality scope: the scope cannot be read from the tree: {error}"]
    frozen, current = {f["path"]: f["sha256"] for f in recorded["files"]}, {f["path"]: f["sha256"] for f in now["files"]}
    out = [f"locality scope: {path} is in the scope now and was not at F0 (added)" for path in sorted(current.keys() - frozen.keys())]
    out += [f"locality scope: {path} was in the scope at F0 and is not now (removed, or renamed out of the scope)" for path in sorted(frozen.keys() - current.keys())]
    out += [f"locality scope: the bytes of {path} differ from F0's" for path in sorted(frozen.keys() & current.keys()) if frozen[path] != current[path]]
    if not out and now["sha256"] != recorded.get("sha256"):
        out.append("locality scope: the scope digest differs from F0's")
    return out


def rule_file_records(rules_root: Path, rule_paths: dict[str, str]) -> tuple[dict, list[str]]:
    """{name: {path, sha256}} computed from the bytes on disk, and the problems (a missing file, a digest that is not the registered one)."""
    records, problems = {}, []
    for name, registered in summarise.RULE_FILES.items():
        relative = rule_paths.get(name)
        path = Path(rules_root) / relative if relative else None
        if path is None or not path.is_file():
            problems.append(f"rule file {name} is missing at {relative!r}")
            continue
        digest = sha256_file(path)
        records[name] = {"path": relative, "sha256": digest}
        if digest != registered:
            problems.append(f"rule file {name} at {relative} hashes to {digest[:12]}..., not the registered {registered[:12]}...")
    return records, problems


def quiet_gate_record(root: Path) -> dict:
    """F0's quiet-gate record (v14-r2 V14 item 5): the frozen script, hashed from the tree F0 is built in, and the registered rule and parameters, TAKEN from the constants of `quiet_gate.py` (no second copy)."""
    path = Path(root) / summarise.QUIET_GATE_PATH
    if not path.is_file():
        raise FreezeError([f"quiet gate script is missing at {summarise.QUIET_GATE_PATH!r}"])
    return {"script": {"path": summarise.QUIET_GATE_PATH, "sha256": sha256_file(path)}, "rule": qg.RULE, "loadLimit": qg.LIMIT, "intervalS": qg.INTERVAL_S, "maxWaitMin": qg.MAX_WAIT_S // 60}


def carried_verdicts(older_f0: Path | str, python_arm: dict, inputs: list[dict]) -> dict:
    """The verdicts F0 carries from an older F0 (v14-r2 V15 item 3), built ONLY from byte equality. `older_f0` is the path of the older F0 json, its inputs listing sits beside it
    (`.inputs.json`; it must hash to the older F0's `inputsSha256`) and its Python-arm record must be well formed. Carried:
    * every Python-arm file that is LISTED (loaded bytes differ from the reference) and whose loaded hash and reference hash are the same in both F0s (`pythonArm`, sorted);
    * the locality allowlist, iff its digest among the F0 inputs is the same in both (`allowlist`, else `None`: its entries then need new verdicts).
    A file or entry that differs is simply not carried (two new verdicts are needed for it)."""
    older_path = Path(older_f0)
    try:
        raw = older_path.read_bytes()
        older = json.loads(raw)
        older_inputs = json.loads(older_path.with_suffix(".inputs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FreezeError([f"older F0 {older_path} cannot be read with its inputs listing: {error}"]) from None
    if not (isinstance(older, dict) and isinstance(older_inputs, list) and all(isinstance(e, dict) and set(e) == {"path", "sha256"} for e in older_inputs)
            and canonical_sha256(older_inputs) == older.get("inputsSha256")):
        raise FreezeError([f"older F0 {older_path}: its inputs listing does not hash to the F0's inputsSha256"])
    if summarise.python_arm_problems(older.get("pythonArm")) or summarise.python_arm_problems(python_arm):
        raise FreezeError([f"older F0 {older_path}: a Python-arm record (the older F0's or this one's) is malformed, so nothing can be carried"])

    def digests(arm: dict) -> dict[str, tuple[str, str]]:
        reference = {m["path"]: m["sha256"] for m in arm["referenceManifest"]}
        return {f["path"]: (f["sha256"], reference[f["path"]]) for f in arm["fileList"]}
    before, now = digests(older["pythonArm"]), digests(python_arm)
    files = [{"path": path, "loadedSha256": loaded, "referenceSha256": reference} for path, (loaded, reference) in sorted(now.items()) if loaded != reference and before.get(path) == (loaded, reference)]
    digest = lambda listing: next((e["sha256"] for e in listing if e["path"] == summarise.ALLOWLIST_PATH), None)
    allowlist = {"path": summarise.ALLOWLIST_PATH, "sha256": digest(inputs)} if digest(inputs) is not None and digest(inputs) == digest(older_inputs) else None
    return {"fromF0Sha256": hashlib.sha256(raw).hexdigest(), "pythonArm": files, "allowlist": allowlist}


def build_freeze(root: Path, *, branch: str, sidecar_sha256: str, evidence_revision: str, method_revision: str, seed: str,
                 user_approval: dict, reader_required: bool, calibration: str, vendors: list[str], inputs: list[dict], python_arm: dict, toolchain: dict, uv: dict,
                 earlier_runs: list[tuple[Path, str]] | None = None, git: Callable[..., bytes] = git_output, rules_root: Path | None = None,
                 rule_paths: dict[str, str] | None = None, dryrun: bool = False, ledger: Path | None = None, carried_from: Path | str | None = None) -> dict:
    """F0. The digests of the v14-r2, v13, v10 and E1 v8b files are COMPUTED here from the files (`rule_paths` under `rules_root`, default the repository root) and must be the registered ones;
    v14-r2 is the pre-registration F0 binds (`preregistration`), the others are bound texts. The digests of the v13 section 4 synthetic examples are computed from the examples in force.
    `earlier_runs` = the sealed runs of earlier rounds (empty for the first round): each must be
    complete or stopped by the toolchain check, otherwise the exception is refused. The approval must be a REAL one (its quote occurs verbatim in the ledger, `approval_problems`) or no F0
    is built; `dryrun=True` builds a dry-run F0 with its own schema id instead, which no verification or decision accepts without an explicit dry-run flag.
    `carried_from` = the path of an older F0 json (its `.inputs.json` beside it): the verdicts that stand by byte equality are recorded (`carried_verdicts`); without it `carriedVerdicts` is `None`."""
    if not dryrun and (problems := approval_check(root, user_approval, rules_root, ledger)):
        raise FreezeError(problems)
    earlier_runs = list(earlier_runs or [])
    if earlier_runs and (problems := earlier_run_problems(earlier_runs)):
        raise FreezeError(problems)
    rule_files, problems = rule_file_records(Path(rules_root) if rules_root else Path(root), rule_paths or DEFAULT_RULE_PATHS)
    if problems:
        raise FreezeError(problems)
    try:
        locality_scope = lc.scope_record(Path(root))
    except lc.ScopeError as error:
        raise FreezeError([str(error)]) from None
    quiet_gate = quiet_gate_record(Path(root))
    carried = carried_verdicts(carried_from, python_arm, inputs) if carried_from is not None else None
    head = git(Path(root), "rev-parse", "HEAD").decode().strip()
    pre = rule_files[summarise.PREREGISTRATION_RULE]
    freeze = {
        "schema": summarise.F0_DRYRUN_SCHEMA if dryrun else summarise.F0_SCHEMA, "head": head, "branch": branch, "sidecarSha256": sidecar_sha256, "methodRevision": method_revision,
        "evidenceRevision": evidence_revision,
        "preregistration": {"document": pre["path"], "sha256": pre["sha256"], "baseSha256": summarise.V10_SHA256, "userApproval": user_approval},
        "ruleFiles": rule_files, "examples": dict(ref.example_digests()),
        "e1ProtocolSha256": ref.FROZEN["e1_sha256"], "schemaSha256": ref.FROZEN["schema_sha256"], "fixtureRegistrySha256": ref.fixture_registry_digest(),
        "logPolicySha256": ref.log_policy_digest(), "scheduleSeed": seed, "scheduleSha256": ref.schedule_sha(ref.make_schedule(seed)),
        "workload": dict(summarise.REGISTERED_WORKLOAD), "inputsSha256": canonical_sha256(inputs),
        "trackedDiffSha256": hashlib.sha256(git(Path(root), "diff", "HEAD", "--", ".")).hexdigest(),
        "options": {"readerRequired": reader_required, "calibration": calibration, **ref.noisy_options(calibration)}, "vendors": vendors,
        "earlierRuns": sorted(sha for _, sha in earlier_runs), "pythonArm": python_arm, "toolchain": toolchain, "uv": uv,
        "localityScope": locality_scope, "quietGate": quiet_gate, "carriedVerdicts": carried,
    }
    if problems := summarise.freeze_problems(freeze, "0" * 64, dryrun=dryrun):
        raise FreezeError(problems)
    return freeze


def verify_freeze(root: Path, freeze: dict, inputs: list[dict], sidecar_path: Path | None, git: Callable[..., bytes] = git_output, *, deep: bool = False,
                  retrace: Callable[[], list[str]] | None = None, rules_root: Path | None = None, dryrun: bool = False, ledger: Path | None = None) -> list[str]:
    """Every check of F0 before an attempt; an empty list means the run may start. Anything else must stop the run.
    Cheap form (every attempt): static consistency, inputs, HEAD, tracked diff, sidecar, the Python-arm file list re-hashed from the working tree (loaded bytes) and the manifest digest.
    `deep` (F1 build): additionally every reference-manifest entry is read back from its revision and checked against its digest, `E3set` is recomputed from the two commits, and (with
    `retrace`) the Python arm is traced again and must give exactly the frozen file list. The toolchain is NOT checked here: a toolchain difference is `toolchain.check`'s own stop reason."""
    problems = summarise.freeze_problems(freeze, "0" * 64, dryrun=dryrun)
    root = Path(root)
    if not dryrun:       # at VERIFICATION time, not only when F0 was built: the approval quote must occur in the ledger now
        approval = freeze.get("preregistration", {}).get("userApproval") if isinstance(freeze.get("preregistration"), dict) else None
        problems += approval_check(root, approval, rules_root, ledger)
    if canonical_sha256(inputs) != freeze.get("inputsSha256"):
        problems.append("the inputs listing does not hash to the frozen inputsSha256")
    for entry in inputs:
        path = root / entry["path"]
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            problems.append(f"frozen input {entry['path']} is missing or changed")
    problems += locality_scope_problems(root, freeze.get("localityScope"))      # the scope the locality check scans, path set and bytes (tracked or not)
    gate = freeze.get("quietGate")                                               # v14-r2 V14: the frozen quiet-gate script, re-read from the tree (the parameters are summarise's static check)
    script = gate.get("script") if isinstance(gate, dict) else None
    if isinstance(script, dict) and isinstance(script.get("path"), str):
        script_path = root / script["path"]
        if not script_path.is_file() or sha256_file(script_path) != script.get("sha256"):
            problems.append("quiet gate script is missing or its bytes differ from the digest F0 recorded")
        listed = next((e for e in inputs if e["path"] == script["path"]), None)
        if listed is not None and listed["sha256"] != script.get("sha256"):
            problems.append("quiet gate script digest differs from the frozen inputs listing")
    carried = freeze.get("carriedVerdicts")                                      # v14-r2 V15 item 3: the carried allowlist equality, against the inputs F0 freezes now
    carried_allowlist = carried.get("allowlist") if isinstance(carried, dict) else None
    if isinstance(carried_allowlist, dict) and next((e["sha256"] for e in inputs if e["path"] == carried_allowlist.get("path")), None) != carried_allowlist.get("sha256"):
        problems.append("carried verdicts: the carried allowlist digest is not the allowlist digest among the F0 inputs")
    try:
        if git(root, "rev-parse", "HEAD").decode().strip() != freeze.get("head"):
            problems.append("HEAD differs from the frozen HEAD")
        if hashlib.sha256(git(root, "diff", "HEAD", "--", ".")).hexdigest() != freeze.get("trackedDiffSha256"):
            problems.append("the tracked diff differs from the frozen one")
    except RuntimeError as error:
        problems.append(str(error))
    if sidecar_path is not None:
        if not sidecar_path.is_file() or sha256_file(sidecar_path) != freeze.get("sidecarSha256"):
            problems.append("the sidecar binary is missing or does not hash to the frozen sidecar hash")
    rules = freeze.get("ruleFiles")
    if isinstance(rules, dict):                          # the rule bytes on disk, recomputed (v14-r2 section 4: F0 binds this document, v13, v10 and E1 v8b)
        for name in summarise.RULE_FILES:
            record = rules.get(name)
            path = Path(rules_root or root) / record["path"] if isinstance(record, dict) and isinstance(record.get("path"), str) else None
            if path is None or not path.is_file() or sha256_file(path) != record.get("sha256"):
                problems.append(f"rule file {name} is missing or its bytes differ from the digest F0 bound")
    if freeze.get("examples") != ref.example_digests():
        problems.append("the synthetic examples in force differ from the examples F0 listed by digest")
    arm = freeze.get("pythonArm")
    if isinstance(arm, dict) and not summarise.python_arm_problems(arm):
        for now in bi.python_arm_file_list(root, [f["path"] for f in arm["fileList"]]):
            frozen = next(f for f in arm["fileList"] if f["path"] == now["path"])
            if now["sha256"] != frozen["sha256"]:
                problems.append(f"the loaded bytes of Python-arm file {now['path']} differ from F0's")
        if deep:
            try:
                for entry in arm["referenceManifest"]:
                    bi.reference_content(root, entry)
                if bi.e3_set(root, [f["path"] for f in arm["fileList"]], arm["e3Parent"], arm["e3Commit"]) != arm["e3Set"]:
                    problems.append("E3set recomputed from the two commits differs from F0's")
            except RuntimeError as error:
                problems.append(f"reference manifest: {error}")
            if retrace is not None and retrace() != [f["path"] for f in arm["fileList"]]:
                problems.append("the Python arm traced again does not load exactly F0's file list")
    return problems


def build_f1(root: Path, f0: dict, f0_sha: str, inputs: list[dict], sidecar_path: Path | None, calibration: dict, reader_margins: dict | None, *,
             git: Callable[..., bytes] = git_output, retrace: Callable[[], list[str]] | None = None, observe: Callable[[], dict] | None = None,
             rules_root: Path | None = None, ledger: Path | None = None) -> dict:
    """F1 = F0's SHA-256 + a deep re-verification of every F0 item + the calibration output + the absolute reader margins. Any difference refuses F1, a fake approval included."""
    if problems := verify_freeze(root, f0, inputs, sidecar_path, git, deep=True, retrace=retrace, rules_root=rules_root, ledger=ledger):
        raise FreezeError(problems)
    if observe is not None:
        record = observe()
        if not record["match"]:
            raise ToolchainMismatch([f"{m['tool']}.{m['field']}: recorded {m['recorded']!r}, observed {m['observed']!r}" for m in record["mismatches"]])
    f1 = {"schema": summarise.F1_SCHEMA, "f0Sha256": f0_sha, "reverified": {"ok": True, "items": list(summarise.F0_ITEMS)},
          "calibration": copy.deepcopy(calibration), "readerMargins": copy.deepcopy(reader_margins)}
    if problems := summarise.f1_problems(f1, f0_sha, f0):
        raise FreezeError(problems)
    return f1


def verify_f1(root: Path, f1: dict, f0: dict, f0_sha: str, inputs: list[dict], sidecar_path: Path | None, git: Callable[..., bytes] = git_output, rules_root: Path | None = None,
              ledger: Path | None = None) -> list[str]:
    """Before every matrix attempt and reader-measure run: F1 is well-formed, names this F0, and F0 still verifies (approval included)."""
    return summarise.f1_problems(f1, f0_sha, f0) + verify_freeze(root, f0, inputs, sidecar_path, git, rules_root=rules_root, ledger=ledger)
