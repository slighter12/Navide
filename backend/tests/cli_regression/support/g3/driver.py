"""The G3 run driver: the end-to-end order of proposal section 8 item 3 with the section 4 per-attempt gates, as DATA (`build_plan` and the pre-F0 plans) plus a sequencer (`execute`) that never retries.

Pre-F0 (each plan executed once into its own record, in this order): `build_pre_plan` (release build that becomes the F0 sidecar, test-only fault-hooks harness, `sidecar-build.json`, the reader fixtures
and their descriptor), `build_packaged_plan` (U7: stage the F0 sidecar, package the `--dir` layout, record it, packaged smoke), `build_smoke_plan` (plumbing smoke, not evidence; may be repeated into a
fresh directory), `build_correctness_plan` (the 17 correctness files on the F0 sidecar itself). Then F0 is built. The cost plan refuses to start unless all of that is complete (`pre_run_problems`).

Cost plan: F0 verify -> quiet gate #1 -> Python-only warm-up + shell calibration -> launch calibration -> reader calibration -> assemble calibration -> F1 build -> F1 verify -> quiet gate #2 -> matrix
(two warm-ups, 40 pairs) -> agent-launch chains (8 warm-ups, 80 scored) -> quiet gate #3 -> reader measure -> packaged cold start (descriptive) -> seal -> python-arm identity -> decide. The quiet gate
is the unchanged g1e tool (`quiet_gate before`). Section 4 item 6 names the gate before warm-up, before the matrix and before the reader run; the launch chains follow the matrix directly.

`execute` claims the run atomically BEFORE the first step (`<record>.claim`, O_EXCL) and rewrites the record after every step, so a crash leaves a partial claim and a second call is refused (section 5.3:
one run, no retry). It records every step (name, argv, env, argv hash, exit, times, validity: no measured value), stops at the first non-zero exit and prints only `step: exit N` (blindness). When the run
stops early `on_stop` receives the record (the registered `inconclusive` outcome is derived from it, `incomplete.py`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import f0 as f0mod, fixtures as fixtures_mod, packaged

SCHEMA = "acr-g3-run/1"
RUN_NAME = "run-g3.json"
G3 = "tests.cli_regression.support.g3"
G1E = "tests.cli_regression.support.g1e"
PY = "backend/.venv/bin/python"
BLIND_STEPS = ("matrix", "launch-matrix", "reader-measure", "decide")
# step name -> (expected wall time in minutes, source of the estimate). G1-E (r12): calibration 24.5 min; matrix about 45 min (proposal 8.3); the rest are estimates marked as such.
EXPECTED_MINUTES = {"f0-verify": (0.1, "estimate"), "quiet-1": (0.5, "G1-E r12: passed on the 2nd poll; up to 120 min"), "calibrate-shell": (24.5, "G1-E r12"),
                    "calibrate-launch": (6.0, "estimate: 4 warm-ups + 40 attempts of a one-pane launch"), "calibrate-reader": (0.2, "G1-E r12: 20 runs in 9 s"),
                    "assemble-calibration": (0.2, "estimate"), "f1-build": (0.5, "estimate"), "f1-verify": (0.2, "estimate"), "quiet-2": (0.5, "up to 120 min"),
                    "matrix": (45.0, "proposal 8.3"), "launch-matrix": (14.0, "estimate: 8 warm-ups + 80 attempts"), "quiet-3": (0.5, "up to 120 min"), "reader-measure": (0.2, "estimate"),
                    "packaged-cold": (3.0, "estimate: 1 warm-up + 10 cold starts of the frozen backend"),
                    "assemble-bundle": (0.1, "estimate"), "seal": (0.1, "estimate"), "decide": (0.2, "estimate"),
                    "python-arm-identity": (0.2, "estimate: one import trace and a git listing"),
                    "release-build": (4.0, "estimate: cargo build --release --locked"), "harness-build": (4.0, "estimate: cargo build --release --locked --features fault-hooks"),
                    "link-probe": (0.1, "estimate: one hard link on a disposable path"), "record-build": (0.1, "estimate"), "reader-fixtures": (0.2, "G1-E r12: deterministic generators"), "correctness": (33.4, "mac-final-eddddb7-r3: 2004 s"),
                    "packaged-stage": (0.1, "estimate: a byte copy"), "packaged-toolchain": (0.1, "estimate: which bun, bunx, pnpm, node, uv, cargo"), "packaged-pyinstaller": (5.0, "estimate"), "packaged-cli-runtime": (4.0, "estimate: cargo release build"),
                    "packaged-build": (6.0, "estimate: bun run build"), "packaged-electron": (4.0, "estimate: electron-builder --dir"),
                    "packaged-record": (0.1, "estimate"), "packaged-smoke": (1.0, "estimate: one cold start, pass/fail only"), "rehearsal-stamp": (0.1, "estimate: rewrites the rehearsal's JSON files"), "rehearsal-verdict": (0.5, "estimate: re-admits every receipt of the rehearsal"),
                    "check-attempts": (4.0, "estimate: one Python-arm and one Rust-arm cell-1 attempt"), "check-report": (0.1, "estimate"),
                    "smoke-shell-launch": (3.0, "estimate: 4 launch attempts and 2 shell-pane attempts, not evidence"), "smoke-verify": (0.1, "estimate")}
PACKAGED_STEPS = ["packaged-toolchain", "packaged-pyinstaller", "packaged-cli-runtime", "packaged-stage", "packaged-build", "packaged-electron", "packaged-record", "packaged-smoke"]
PRE_RUN_NAME = "run-pre.json"
PACKAGED_RUN_NAME = "run-packaged.json"
CORRECTNESS_RUN_NAME = "run-correctness.json"
SMOKE_RUN_NAME = "run-smoke.json"
BUILD_RECORD_NAME = "sidecar-build.json"
PACKAGED_RECORD_NAME = "packaged-build.json"
# The 17 files of tests/cli/g2-linux-pytest.sh, the reconstruction of the mac-final-eddddb7-r3 correctness run (manager answer 3).
CORRECTNESS_FILES = ["test_cli_runtime_composition", "test_cli_runtime_composition_vendors", "test_cli_runtime_observe_vendors", "test_cli_runtime_observe_cost", "test_cli_runtime_recovery",
                     "test_cli_runtime_lifecycle", "test_cli_runtime_handoff", "test_cli_runtime_sidecar", "test_cli_runtime_managed_login", "test_cli_runtime_vendor_transport", "test_cli_runtime_review1",
                     "test_cli_runtime_observe", "test_cli_runtime_claude", "test_cli_runtime_claude_parity", "test_cli_runtime_observe_codex", "test_cli_create_meta_size",
                     "test_cli_runtime_credential_env_remove"]


class DriverError(Exception):
    pass


CORRECTNESS_BASETEMP = "/private/tmp/g3r3-ct"     # short on purpose: run 1's ~150-char basetemp overflowed a path-derived name (grok) and timed out an aider scenario; a fresh name for r3 (r2 left /private/tmp/g3r2-ct); must not exist before the run


def _step(name: str, argv: list[str], *, cwd: str, env: dict | None = None, outputs: list[str] | None = None, stdout: str | None = None) -> dict:
    return {"name": name, "argv": argv, "cwd": cwd, "env": env or {}, "outputs": outputs or [], "stdout": stdout, "expectedMinutes": EXPECTED_MINUTES[name][0]}


def sidecar_path(repo: str) -> str:
    return f"{repo}/native/navide-cli-runtime/target/release/navide-cli-runtime"


def harness_path(pre_out: str) -> str:
    return f"{pre_out}/harness-target/release/navide-cli-runtime"


def _helper(repo: str):
    return lambda *args: [f"{repo}/{PY}", "-B", "-m", f"{G3}.helper", *args]


def build_pre_plan(*, repo: str, pre_out: str) -> list[dict]:
    """Pre-F0 plan 1 (manager answer 3, review-1 finding 5): the release build that BECOMES the F0 sidecar, the test-only fault-hooks harness, `sidecar-build.json` {head, sha256, builtAt, builtAtEpoch,
    harnessSha256} and the reader fixtures with their descriptor (verified against the registered digests; F0 binds the descriptor). `pre_out` = OUT/g3/pre."""
    crate = f"{repo}/native/navide-cli-runtime"
    helper = _helper(repo)
    return [
        # review-6: the one-run claim is created by a hard link in the registered output's parent; prove that file system allows it BEFORE F0, on disposable names, with no candidate step.
        _step("link-probe", helper("link-probe", str(registered_out(repo).parent)), cwd=f"{repo}/backend"),
        _step("release-build", ["cargo", "build", "--release", "--locked"], cwd=crate, outputs=[sidecar_path(repo)]),
        _step("harness-build", ["cargo", "build", "--release", "--locked", "--features", "fault-hooks", "--target-dir", f"{pre_out}/harness-target"], cwd=crate, outputs=[harness_path(pre_out)]),
        _step("record-build", helper("record-build", repo, sidecar_path(repo), harness_path(pre_out), f"{pre_out}/{BUILD_RECORD_NAME}"), cwd=f"{repo}/backend", outputs=[f"{pre_out}/{BUILD_RECORD_NAME}"]),
        _step("reader-fixtures", helper("reader-fixtures-build", f"{pre_out}/reader-fixtures", f"{pre_out}/FIXTURES.json"), cwd=f"{repo}/backend", outputs=[f"{pre_out}/FIXTURES.json"]),
    ]


def build_packaged_plan(*, repo: str, pre_out: str, bun: str = "bunx") -> list[dict]:
    """Pre-F0 plan 2 (U7, review-1 finding 10; manager decision: no `build.sh`, which needs node and pnpm): the four steps of part A's eddddb7 macOS package, on bun, one command each:
    (1) PyInstaller frozen backend, (2) `bun run build:cli-runtime` (cargo release build, staged to `build/cli-runtime`), then the F0 sidecar is byte-copied over the staged file (`packaged-stage`; no
    rebuilt runtime can be packaged), (3) `bun run build`, (4) `electron-builder --mac --arm64 --dir` into `OUTPUT_REL`. `release:check` is skipped (a version check). Then (5) `packaged-build.json` names H, the
    frozen backend digest and build time and the packaged runtime digest, which must equal the sidecar's, (6) a pass/fail smoke of one cold start. The measurement is `packaged-cold` of the cost plan."""
    helper = _helper(repo)
    smoke_out = f"{pre_out}/packaged-smoke"
    record = f"{pre_out}/{PACKAGED_RECORD_NAME}"
    return [
        _step("packaged-toolchain", helper("package-toolchain"), cwd=f"{repo}/backend"),
        _step("packaged-pyinstaller", ["uv", "--directory", "backend", "run", "pyinstaller", "--noconfirm", "agent_team_backend.spec"], cwd=repo, outputs=[f"{repo}/backend/dist"]),
        _step("packaged-cli-runtime", ["bun", "run", "build:cli-runtime"], cwd=repo, outputs=[f"{repo}/{packaged.STAGE_REL}/{packaged.RUNTIME_NAME}"]),
        _step("packaged-stage", helper("packaged-stage", repo, sidecar_path(repo)), cwd=f"{repo}/backend", outputs=[f"{repo}/{packaged.STAGE_REL}/{packaged.RUNTIME_NAME}"]),
        _step("packaged-build", ["bun", "run", "build"], cwd=repo, outputs=[f"{repo}/out"]),
        _step("packaged-electron", ["bunx", "electron-builder", "--mac", "--arm64", "--dir", "--publish", "never", f"-c.directories.output={repo}/{packaged.OUTPUT_REL}"], cwd=repo,
              env={"CSC_IDENTITY_AUTO_DISCOVERY": "false"}, outputs=[f"{repo}/{packaged.APP_REL}"]),
        _step("packaged-record", helper("packaged-record", repo, sidecar_path(repo), f"{pre_out}/{PACKAGED_RECORD_NAME}"), cwd=f"{repo}/backend", outputs=[f"{pre_out}/{PACKAGED_RECORD_NAME}"]),
        _step("packaged-smoke", [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=repo,
              env={"ACR_G3": "packaged-smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_PACKAGED_BACKEND": str(packaged.backend_path(repo)), "ACR_G3_PACKAGED_RECORD": record,
                   "ACR_G3_SIDECAR": sidecar_path(repo)}, outputs=[f"{smoke_out}/packaged-smoke.json"]),
    ]


def build_smoke_plan(*, repo: str, smoke_out: str, bun: str = "bunx") -> list[dict]:
    """Pre-F0 plan 3: the plumbing smoke (not evidence), against the sidecar of `build_pre_plan`. A failed smoke is repeated into a fresh `smoke_out`."""
    sidecar = sidecar_path(repo)
    return [
        _step("smoke-shell-launch", [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=repo, env={"ACR_G3": "smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_SIDECAR": sidecar},
              outputs=[f"{smoke_out}/smoke-entries.json"]),
        _step("smoke-verify", _helper(repo)("smoke-verify", smoke_out, sidecar), cwd=f"{repo}/backend", outputs=[f"{smoke_out}/smoke-verdict.json"]),
    ]


def build_correctness_plan(*, repo: str, pre_out: str) -> list[dict]:
    """Pre-F0 plan 4: the correctness run on the F0 sidecar itself (same file, same SHA-256) plus the harness. The command is the reconstruction of mac-final-eddddb7-r3 from
    tests/cli/g2-linux-pytest.sh (the literal original was not recorded; manager answer 3 accepts it). The literal argv and env are recorded in the run record."""
    sidecar = sidecar_path(repo)
    argv = ["uv", "--project", "backend", "run", "pytest", *[f"backend/tests/{name}.py" for name in CORRECTNESS_FILES], "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit2",
            "--basetemp", CORRECTNESS_BASETEMP, "--junitxml", f"{pre_out}/correctness-junit.xml"]
    return [_step("correctness", argv, cwd=repo, env={"NAVIDE_CLI_RUNTIME_BIN": sidecar, "NAVIDE_T05_SIDECAR": sidecar, "NAVIDE_CLI_RUNTIME_HARNESS_BIN": harness_path(pre_out)},
                  outputs=[f"{pre_out}/correctness-junit.xml"])]


CHECK_OUT_REL = ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/g3r2/check"      # G3-r2 Phase A step 3: the real-artifact check writes only here
CHECK_RUN_NAME = "run-check.json"


def build_check_plan(*, repo: str, out: str, f0_path: str, bun: str = "bunx") -> list[dict]:
    """The real-artifact check (not evidence): one discarded Python-arm and one discarded Rust-arm shell attempt through the unchanged per-attempt checks against a scratch F0, then the pass/fail view of
    their receipts (`helper attempt-report`: no value). Runs the TS runner in `check` mode."""
    return [
        _step("check-attempts", [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=repo,
              env={"ACR_G3": "check", "ACR_G3_FREEZE": f0_path, "ACR_G3_SIDECAR": sidecar_path(repo), "ACR_G3_OUT": f"{out}/run"}, outputs=[f"{out}/run/shell/run.json"]),
        _step("check-report", _helper(repo)("attempt-report", f"{out}/run"), cwd=f"{repo}/backend", stdout=f"{out}/attempt-report.json", outputs=[f"{out}/attempt-report.json"]),
    ]


def check_out_problem(repo: str | Path, out: str | Path) -> str | None:
    real, base = Path(os.path.realpath(out)), Path(os.path.realpath(Path(repo) / CHECK_OUT_REL))
    if real == base or (real.parent == base and re.fullmatch(r"attempt-\d+", real.name)):
        return None
    return f"the real-artifact check writes only to {Path(repo) / CHECK_OUT_REL} or its attempt-<n> subdirectories, not {out}"


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pre_run_problems(f0: dict, repo: str, pre_out: Path, smoke_out: Path, last_code_commit_ts: int | None = None, check_artifact: bool = True) -> list[str]:
    """The guard of the cost plan: empty = the pre-run record is complete. Checks the four pre-F0 run records (every step at exit 0, so every correctness row passed), `sidecar-build.json` and
    `packaged-build.json` naming F0's head and F0's sidecar digest, F0's sidecar PATH equal to the one built and used by the correctness run (review-1 finding 8: equal bytes elsewhere do not
    count), the reader fixture descriptor bound into F0, and a passed plumbing smoke and packaged smoke."""
    pre_out, smoke_out = Path(pre_out), Path(smoke_out)
    out = []
    for record_name, names in ((PRE_RUN_NAME, ["link-probe", "release-build", "harness-build", "record-build", "reader-fixtures"]), (PACKAGED_RUN_NAME, PACKAGED_STEPS),
                               (CORRECTNESS_RUN_NAME, ["correctness"])):
        record = _load(pre_out / record_name)
        if not (isinstance(record, dict) and record.get("complete") is True and [s.get("name") for s in record.get("steps", [])] == names and all(s.get("exit") == 0 for s in record["steps"])):
            out.append(f"{record_name} is missing or not a complete, all-passing run of {names}")
    build = _load(pre_out / BUILD_RECORD_NAME)
    if not (isinstance(build, dict) and set(build) == {"head", "sha256", "builtAt", "builtAtEpoch", "harnessSha256"} and all(isinstance(build[k], str) and build[k] for k in ("head", "sha256", "builtAt", "harnessSha256"))
            and isinstance(build["builtAtEpoch"], (int, float)) and not isinstance(build["builtAtEpoch"], bool)):
        out.append(f"{BUILD_RECORD_NAME} is missing or incomplete")
    else:
        if build["head"] != f0.get("head"): out.append("the build record names another head than F0")
        if build["sha256"] != (f0.get("sidecar") or {}).get("sha256"): out.append("the build record's sidecar digest is not F0's sidecar digest")
        if packaged.predates(build["builtAtEpoch"], last_code_commit_ts): out.append("the sidecar's build time predates the last commit touching measured code (section 1.1: artifactIdentity would be predates-code)")
    if os.path.realpath((f0.get("sidecar") or {}).get("path", "")) != os.path.realpath(sidecar_path(repo)):
        out.append("F0's sidecar path is not the file the build and the correctness run used (same bytes at another path do not count)")
    packaged_record = _load(pre_out / PACKAGED_RECORD_NAME)
    out += [f"packaged build: {p}" for p in packaged.identity_problems(packaged_record, f0, last_code_commit_ts)]
    if check_artifact:                      # the bytes on disk now, not only the record (review-3 finding 4)
        out += [f"packaged artifact: {p}" for p in packaged.artifact_problems(repo, packaged_record, (f0.get("sidecar") or {}).get("sha256", ""), f0.get("head", ""), last_code_commit_ts)]
    descriptor = f0.get("readerFixtureDescriptor")
    if descriptor is None: out.append("F0 does not bind a reader fixture descriptor")
    elif os.path.realpath(str((descriptor or {}).get("path", ""))) != os.path.realpath(pre_out / "FIXTURES.json"):
        out.append("F0's reader fixture descriptor is not this run's own <pre>/FIXTURES.json (round 2 finding 2: a descriptor of another run does not count)")
    seed_source = pre_out / PACKAGED_RECORD_NAME
    if not seed_source.is_file(): out.append(f"{PACKAGED_RECORD_NAME} is missing: the F0 seed cannot be checked")
    elif f0.get("scheduleSeed") != hashlib.sha256(seed_source.read_bytes()).hexdigest()[:32]:
        out.append(f"F0's scheduleSeed is not the first 32 hex characters of sha256({PACKAGED_RECORD_NAME}) of this run (amendment 4)")
    verdict = _load(smoke_out / "smoke-verdict.json")
    if not (isinstance(verdict, dict) and verdict.get("passed") is True and verdict.get("notEvidence") is True):
        out.append("the plumbing smoke has no passed verdict")
    packaged_smoke = _load(pre_out / "packaged-smoke" / "packaged-smoke.json")
    if not (isinstance(packaged_smoke, dict) and packaged_smoke.get("passed") is True and packaged_smoke.get("notEvidence") is True):
        out.append("the packaged cold-start smoke has no passed result")
    return out


def build_plan(*, repo: str, out: str, sidecar: str, f0_path: str, build_record: str, g1e_bundle: str, reader_fixtures: str, packaged_backend: str, bun: str = "bunx") -> list[dict]:
    """The reviewed command sequence for the one real run, from absolute paths. `out` = OUT/g3/run (nothing is written outside it except what the tools write beside F0). `reader_fixtures` = the
    descriptor F0 binds; `packaged_backend` = the frozen backend of the packaged layout."""
    f1 = f"{out}/freeze-f1.json"
    cal = f"{out}/calibration"
    env_ts = {"ACR_G3_FREEZE": f0_path, "ACR_G3_SIDECAR": sidecar}
    ts = lambda *_: [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"]
    backend = f"{repo}/backend"
    helper = _helper(repo)
    quiet = lambda tag: [f"{repo}/{PY}", "-B", "-m", f"{G1E}.quiet_gate", "before", f"{out}/{tag}"]
    return [
        _step("f0-verify", helper("freeze-verify", repo, f0_path, sidecar), cwd=backend, stdout=f"{out}/logs/f0-verify.json"),
        _step("quiet-1", quiet("q1"), cwd=backend, outputs=[f"{out}/q1/quiet-01.json"]),
        _step("calibrate-shell", ts("calibrate", cal), cwd=repo, env={**env_ts, "ACR_G3": "calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/calibration/run.json"]),
        _step("calibrate-launch", ts("launch-calibrate", cal), cwd=repo, env={**env_ts, "ACR_G3": "launch-calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/launch-trials/run.json"]),
        _step("calibrate-reader", helper("reader-calibrate", repo, f0_path, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend,
              outputs=[f"{out}/bundle-reader/reader/calibration-margins.json"]),
        _step("assemble-calibration", helper("assemble-calibration", repo, f0_path, cal, f"{out}/bundle-reader/reader", f"{out}/calibration.json"), cwd=backend, outputs=[f"{out}/calibration.json"]),
        _step("f1-build", helper("f1-build", repo, f0_path, f"{out}/calibration.json", sidecar, f1), cwd=backend, outputs=[f1]),
        _step("f1-verify", helper("freeze-verify", repo, f0_path, sidecar, f1), cwd=backend, stdout=f"{out}/logs/f1-verify.json"),
        _step("quiet-2", quiet("q2"), cwd=backend, outputs=[f"{out}/q2/quiet-01.json"]),
        _step("matrix", ts("matrix", out), cwd=repo, env={**env_ts, "ACR_G3": "matrix", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/matrix/run.json"]),
        _step("launch-matrix", ts("launch", out), cwd=repo, env={**env_ts, "ACR_G3": "launch", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/launch-trials/run.json"]),
        _step("quiet-3", quiet("q3"), cwd=backend, outputs=[f"{out}/q3/quiet-01.json"]),
        _step("reader-measure", helper("reader-measure", repo, f0_path, f1, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend,
              outputs=[f"{out}/bundle-reader/reader.json"]),
        _step("packaged-cold", ts("packaged", out), cwd=repo, env={**env_ts, "ACR_G3": "packaged", "ACR_G3_OUT": f"{out}/m", "ACR_G3_PACKAGED_BACKEND": packaged_backend,
                                                                                  "ACR_G3_PACKAGED_RECORD": str(Path(build_record).with_name(PACKAGED_RECORD_NAME))}, outputs=[f"{out}/m/packaged-trials/run.json"]),
        _step("assemble-bundle", helper("assemble-bundle", repo, f0_path, f1, out, f"{out}/bundle"), cwd=backend, outputs=[f"{out}/bundle/freeze.json"]),
        _step("seal", helper("seal", f"{out}/bundle"), cwd=backend, stdout=f"{out}/logs/manifest.sha256"),
        _step("python-arm-identity", helper("python-arm-identity", repo, g1e_bundle, f"{out}/python-arm-identity.json"), cwd=backend, outputs=[f"{out}/python-arm-identity.json"]),
        _step("decide", helper("decide", repo, f0_path, f1, f"{out}/bundle", f"{out}/logs/manifest.sha256", f"{out}/outcome.json", build_record, g1e_bundle, f"{out}/python-arm-identity.json"), cwd=backend,
              outputs=[f"{out}/outcome.json"]),
    ]


def argv_hash(step: dict) -> str:
    return hashlib.sha256(json.dumps({k: step[k] for k in ("name", "argv", "cwd", "env")}, sort_keys=True).encode()).hexdigest()


def _literal(step: dict) -> dict:
    """The command as run: name, argv and the env the step adds."""
    return {"name": step["name"], "argv": step["argv"], "env": step["env"]}


def plan_sha256(plan: list[dict]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def default_runner(step: dict, stdout_path: Path | None) -> int:
    env = {**os.environ, **step["env"]}
    handle = open(stdout_path, "w", encoding="utf-8") if stdout_path else subprocess.DEVNULL
    try:
        return subprocess.run(step["argv"], cwd=step["cwd"], env=env, stdout=handle, stderr=subprocess.STDOUT if stdout_path else subprocess.DEVNULL, check=False).returncode
    finally:
        if stdout_path:
            handle.close()


def _stop_reason(step: dict, code: int) -> str:
    if step["name"].startswith("quiet") and code == 3:
        return "quiet-gate-timeout"
    if step["name"].startswith("calibrate-reader") or step["name"] == "reader-measure":
        if code == fixtures_mod.MISSING_EXIT:
            return "reader-fixture-missing"
    return "step-failed"


def _save(record_path: Path, record: dict) -> None:
    """Atomic rewrite: a reader never sees half a record."""
    temp = record_path.with_name(record_path.name + ".tmp")
    temp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, record_path)


REGISTERED_OUT_REL = ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/g3r2/run"      # OUT/g3r2/run: the ONE directory the cost run may write (review-3 finding 1; G3-r2 amendment 4 moved it from g3/run, which run 1 used)
REHEARSAL_OUT_REL = ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/g3r2/rehearsal"      # the only place a rehearsal may write (G3-r2 amendment 2)
R3_REGISTERED_OUT_REL = ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/g3r3/run"      # G3-r3 amendment: the follow-up run's own directory, with its own claim beside it
R3_REHEARSAL_PARENT_REL = ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/g3r3"      # a G3-r3 rehearsal writes to a `rehearsal*` directory here, or to its attempt-<n> subdirectory
REGISTERED_CLAIM_NAME = "cost-run.claim"


def registered_out(repo: str | Path) -> Path:
    return Path(repo) / REGISTERED_OUT_REL


def registered_claim(repo: str | Path, out: str | Path | None = None) -> Path:
    """The one-run claim of the cost run: a file BESIDE the registered output directory (the r3 one when `out` is the r3 directory), so it does not depend on the `out` a caller names otherwise."""
    if out is not None and os.path.realpath(out) == os.path.realpath(Path(repo) / R3_REGISTERED_OUT_REL):
        return (Path(repo) / R3_REGISTERED_OUT_REL).parent / REGISTERED_CLAIM_NAME
    return registered_out(repo).parent / REGISTERED_CLAIM_NAME


def out_problem(repo: str | Path, out: str | Path) -> str | None:
    if os.path.realpath(out) not in (os.path.realpath(registered_out(repo)), os.path.realpath(Path(repo) / R3_REGISTERED_OUT_REL)):
        return f"the cost run writes only to the registered output {registered_out(repo)} or {Path(repo) / R3_REGISTERED_OUT_REL}, not {out}"
    return None


def rehearsal_out(repo: str | Path) -> Path:
    return Path(repo) / REHEARSAL_OUT_REL


def rehearsal_out_problem(repo: str | Path, out: str | Path) -> str | None:
    """A rehearsal writes only to OUT/g3r2/rehearsal, or to `rehearsal/attempt-<n>` for a repeat (a used directory cannot be cleaned, so a repeat needs a fresh one)."""
    real, base = Path(os.path.realpath(out)), Path(os.path.realpath(rehearsal_out(repo)))
    if real == base or (real.parent == base and re.fullmatch(r"attempt-\d+", real.name)):
        return None
    r3 = Path(os.path.realpath(Path(repo) / R3_REHEARSAL_PARENT_REL))
    if real.parent == r3 and real.name.startswith("rehearsal") or real.parent.parent == r3 and real.parent.name.startswith("rehearsal") and re.fullmatch(r"attempt-\d+", real.name):
        return None
    return f"a rehearsal writes only to {rehearsal_out(repo)} or its attempt-<n> subdirectories, or to a rehearsal* directory of {Path(repo) / R3_REHEARSAL_PARENT_REL}, not {out}"


def rehearsal_plan(plan: list[dict], repo: str, out: str) -> list[dict]:
    """The rehearsal form of the registered cost plan: the same steps with `ACR_G3_REHEARSAL=1` added to the steps that run the TS attempt runners (they reduce their counts), and a last step that
    stamps every output `notEvidence: true`. `plan` itself is not changed: the registered plan, and its hash, are exactly what `build_plan` returns."""
    stamped = [{**step, "env": {**step["env"], "ACR_G3_REHEARSAL": "1"}} if "ACR_G3" in step["env"] else step for step in plan]
    verify = next(s for s in plan if s["name"] == "f0-verify")["argv"]
    f0_path = verify[verify.index("freeze-verify") + 2]
    # Round 2: the verdict runs BEFORE the stamp (stamping rewrites the sealed bundle's JSON files, which the verdict reads through the manifest) and fails the run (exit 2) on any defect.
    verdict = _step("rehearsal-verdict", _helper(repo)("rehearsal-verdict", f0_path, f"{out}/freeze-f1.json", out), cwd=f"{repo}/backend", outputs=[f"{out}/rehearsal-verdict.json"])
    return stamped + [verdict, _step("rehearsal-stamp", _helper(repo)("rehearsal-stamp", out), cwd=f"{repo}/backend")]


REHEARSAL_RECORD = {"notEvidence": True, "rehearsal": True}      # merged into the run record of a rehearsal


CLAIM_SCHEMA = "acr-g3-claim/1"


def write_claim(claim: Path, plan_hash: str) -> None:
    """Creates the claim ATOMICALLY WITH ITS CONTENT (the registered plan's hash): the body goes to a temp file first and is hard-linked to the claim name, which fails if the claim exists. A process
    death can therefore leave no claim or a whole one, never an empty one (review-5 finding 1)."""
    claim.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{claim.name}.", suffix=".tmp", dir=claim.parent)      # a unique name, created exclusively: a pre-existing path is never reopened (review-6 finding 1)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema": CLAIM_SCHEMA, "planSha256": plan_hash}) + "\n")
        os.link(temp, claim)
    finally:
        temp.unlink()


def claim_problems(claim: Path, plan: list[dict]) -> list[str]:
    """The claim must exist and carry the registered plan's hash."""
    body = _load(claim)
    if not isinstance(body, dict) or body.get("schema") != CLAIM_SCHEMA:
        return [f"{claim} is missing or is not a claim of this plan"]
    return [] if body.get("planSha256") == plan_sha256(plan) else ["the claim names another plan than the registered one"]


def claim_only_record(plan: list[dict]) -> dict:
    """The journal of a run that died between creating the claim and the first journal save: no step ran, the stop reason is `crash`."""
    return {"schema": SCHEMA, "planSha256": plan_sha256(plan), "steps": [], "complete": False, "stopReason": "crash"}


def recovery_problems(record: object, plan: list[dict], has_outcome: bool) -> list[str]:
    """A journal may be classified only if it is the registered run's: a record of this plan (same `planSha256`), not complete, with no outcome yet."""
    if not isinstance(record, dict):
        return ["the journal is not a run record"]
    out = []
    if record.get("planSha256") != plan_sha256(plan): out.append("the journal's plan identity is not the registered plan's")
    if record.get("complete") is True or has_outcome: out.append("nothing to recover: the run is complete or already has an outcome")
    return out


def execute(plan: list[dict], out: Path, *, runner: Callable[[dict, Path | None], int] = default_runner, now: Callable[[], float] = time.time, verify_f0: Callable[[], list[str]] | None = None,
            say: Callable[[str], None] = print, record_name: str = RUN_NAME, precondition: Callable[[], list[str]] | None = None, on_stop: Callable[[dict], None] | None = None,
            claim_path: Path | None = None, record_extra: dict | None = None) -> dict:
    """Runs `plan` once, in order. `verify_f0` (the F0 re-check) runs before EVERY step, a problem ends the run (`freeze-mismatch`). `precondition` is checked first: any problem refuses the run
    (`DriverError`) and writes nothing. The run is CLAIMED atomically before the first step (`claim_path`, default beside the record) and its record rewritten after every step: a crash, a kill or an
    exception leaves the claim, so any later call is refused (one run, no retry or resume). `on_stop(record)` is called for a run that did not complete, also after an exception (the claimed record
    is classified `crash`; nothing is retried)."""
    out = Path(out)
    if precondition:
        problems = precondition()
        if problems:
            raise DriverError("precondition not met: " + "; ".join(problems))
    (out / "logs").mkdir(parents=True, exist_ok=True)
    record_path, claim = out / record_name, Path(claim_path) if claim_path else out / f"{record_name}.claim"
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_claim(claim, plan_sha256(plan))
    except FileExistsError:
        raise DriverError(f"{claim} exists: one run only, never retried or resumed (a crashed run is not resumed either)") from None
    if record_path.exists():
        raise DriverError(f"{record_path} exists: one run only, never retried or resumed")
    record = {"schema": SCHEMA, "planSha256": plan_sha256(plan), "steps": [], "complete": False, "stopReason": None, **(record_extra or {})}
    _save(record_path, record)
    seen = set()
    try:
        for step in plan:
            if step["name"] in seen:
                raise DriverError(f"step {step['name']} appears twice in the plan")
            seen.add(step["name"])
            problems = verify_f0() if verify_f0 else []
            entry = {**_literal(step), "argvSha256": argv_hash(step), "exit": None, "validity": "invalid", "stopReason": None, "start": None, "end": None}
            record["steps"].append(entry)
            if problems:
                entry["stopReason"] = record["stopReason"] = "freeze-mismatch"
                break
            entry["start"] = now()
            entry["validity"] = "started"
            _save(record_path, record)                      # a crash from here on leaves the step `started`, never `clean`
            code = runner(step, Path(step["stdout"]) if step.get("stdout") else out / "logs" / f"{step['name']}.log")
            entry["end"] = now()
            ok = code == 0
            entry.update(exit=code, validity="clean" if ok else "invalid", stopReason=None if ok else "step-failed")
            _save(record_path, record)
            say(f"{step['name']}: exit {code}")          # the only console output of any step (blindness)
            if not ok:
                record["stopReason"] = _stop_reason(step, code)
                break
        else:
            record["complete"] = True
    except BaseException:
        record["stopReason"] = record["stopReason"] or "crash"
        _save(record_path, record)
        if on_stop is not None:
            try:
                on_stop(record)
            except Exception:                                  # the classification must never mask the original failure
                pass
        raise
    _save(record_path, record)
    if not record["complete"] and on_stop is not None:
        on_stop(record)
    return record


def f0_verifier(root: Path, f0_path: Path, pin_manifest: dict, sidecar: Path) -> Callable[[], list[str]]:
    return lambda: f0mod.verify_f0(Path(root), json.loads(Path(f0_path).read_text()), pin_manifest, sidecar)


def _run_plan(plan: list[dict], out: Path, record_name: str, **kwargs) -> int:
    record = execute(plan, out, record_name=record_name, **kwargs)
    return 0 if record["complete"] else 2


def main(argv: list[str]) -> int:
    """The Phase 2 entry (run through `g3/run_driver.py`): `build|packaged|correctness REPO PRE_OUT`, `smoke REPO SMOKE_OUT`, `cost REPO OUT F0 BUILD_RECORD G1E_BUNDLE PRE_OUT SMOKE_OUT`."""
    from tests.cli_regression.support.g3 import helper, incomplete
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "build" and len(args) == 2:
            return _run_plan(build_pre_plan(repo=args[0], pre_out=args[1]), Path(args[1]), PRE_RUN_NAME)
        if cmd == "packaged" and len(args) == 2:
            return _run_plan(build_packaged_plan(repo=args[0], pre_out=args[1]), Path(args[1]), PACKAGED_RUN_NAME)
        if cmd == "correctness" and len(args) == 2:
            return _run_plan(build_correctness_plan(repo=args[0], pre_out=args[1]), Path(args[1]), CORRECTNESS_RUN_NAME)
        if cmd == "smoke" and len(args) == 2:
            return _run_plan(build_smoke_plan(repo=args[0], smoke_out=args[1]), Path(args[1]), SMOKE_RUN_NAME)
        if cmd == "check" and len(args) == 3:       # G3-r2 Phase A step 3 (not evidence): `check REPO OUT F0`
            repo, out, f0_path = args
            if refusal := check_out_problem(repo, out):
                raise DriverError(refusal)
            return _run_plan(build_check_plan(repo=repo, out=out, f0_path=f0_path), Path(out), CHECK_RUN_NAME, record_extra=REHEARSAL_RECORD,
                             verify_f0=f0_verifier(Path(repo), Path(f0_path), helper.pin_manifest(Path(repo)), Path(sidecar_path(repo))))
        rehearsal = "--rehearsal" in args      # G3-r2 amendment 2: off by default; the registered plan and its hash are built without it
        args = [a for a in args if a != "--rehearsal"]
        if cmd in ("cost", "recover") and len(args) == 7:
            repo, out, f0_path, build_record, g1e_bundle, pre_out, smoke_out = args
            if rehearsal and cmd == "recover":
                raise DriverError("a rehearsal has nothing to recover: run it again into a fresh attempt-<n> directory")
            refusal = rehearsal_out_problem(repo, out) if rehearsal else out_problem(repo, out)
            if refusal:
                raise DriverError(refusal)
            if cmd == "recover" and not registered_claim(repo, out).is_file():       # review-4 finding 3: only a CLAIMED registration can be recovered
                raise DriverError(f"{registered_claim(repo, out)} does not exist: this journal was never claimed by the registered run")
            f0 = json.loads(Path(f0_path).read_text())
            sidecar = sidecar_path(repo)
            ts = helper.last_code_commit_ts(Path(repo))

            def write_incomplete(record: dict) -> None:
                build = json.loads(Path(build_record).read_text()) if Path(build_record).is_file() else None
                packaged_record = _load(Path(build_record).with_name(PACKAGED_RECORD_NAME))
                doc = incomplete.document(f0, record, run_dir=Path(out), build_record=build, last_code_commit_ts=ts, packaged_state=(Path(repo), packaged_record))
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / "outcome.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                if rehearsal:
                    helper.rehearsal_stamp(Path(out))

            plan = build_plan(repo=repo, out=out, sidecar=sidecar, f0_path=f0_path, build_record=build_record, g1e_bundle=g1e_bundle, reader_fixtures=f0["readerFixtureDescriptor"]["path"],
                              packaged_backend=str(packaged.backend_path(repo)))
            if rehearsal:
                plan = rehearsal_plan(plan, repo, out)
            if cmd == "recover":              # classify a claimed run that died without an outcome; runs nothing
                problems = claim_problems(registered_claim(repo, out), plan)
                journal = Path(out) / RUN_NAME
                record = json.loads(journal.read_text()) if journal.is_file() else claim_only_record(plan)      # claim only: the process died before the first journal save
                problems += recovery_problems(record, plan, (Path(out) / "outcome.json").exists())
                if problems:
                    raise DriverError("; ".join(problems))
                write_incomplete(record)
                return 0
            return _run_plan(plan, Path(out), RUN_NAME, verify_f0=f0_verifier(Path(repo), Path(f0_path), helper.pin_manifest(Path(repo)), Path(sidecar)),
                             precondition=lambda: pre_run_problems(f0, repo, Path(pre_out), Path(smoke_out), ts), on_stop=write_incomplete,
                             claim_path=None if rehearsal else registered_claim(repo, out), record_extra=REHEARSAL_RECORD if rehearsal else None)
    except DriverError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(main.__doc__, file=sys.stderr)
    return 64
