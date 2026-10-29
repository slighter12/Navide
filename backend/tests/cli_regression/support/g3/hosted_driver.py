"""The hosted G3 run driver: the same step order as the macOS `cost` path (design 3.1, `g3/driver.py`), the platform parts substituted, the sequencer reused.

Reused unchanged by import: `execute` (claim, journal, no retry, blindness), `_step`'s shape, `argv_hash`, `plan_sha256`, `f0_verifier`, the claim and recovery rules, the macOS `pre_run_problems`.
Substituted: the interpreter path (`backend/.venv/bin/python` or `Scripts/python.exe`), the helper (`hosted_helper`), the quiet gate (`hosted_quiet_gate`), the packaged plan (`hosted_packaged`), the output
rules (`$RUNNER_TEMP/g3r2h/out` instead of the repository's `OUT/g3r2/run`) and the precondition: the in-job `correctness` step (the 21-row registry, `hosted_correctness`) writes the macOS
`run-correctness.json` on every platform, and the cost run also requires the sidecar binding of its receipt (and, on Linux and Windows, of the G2 binding). The reader-fixture descriptor is dropped on Windows
(no reader rows), which also drops the reader steps. The G1-E bundle is optional here: given as the
7th argument it adds the macOS `python-arm-identity` step and the G1-E precondition (the registered digest in $ACR_G3_G1E_MANIFEST_SHA256, never an argument), and `decide` derives the Python arm's
attribution from it; without it the Python arm is non-attributable (no bundle was given: never a crash) and the §4.8 identity record is written from the frozen G1-E pins by `python-arm-identity-f0` (rf4d E2).

    hosted_driver build|packaged|correctness REPO PRE_OUT        hosted_driver smoke REPO SMOKE_OUT
    hosted_driver cost|recover REPO OUT F0 BUILD_RECORD PRE_OUT SMOKE_OUT [G1E_BUNDLE] [--rehearsal]

Every path argument becomes absolute at entry, before the precondition runs (the G3-r3 void start). Every step runs with `ACR_G3_HOSTED=1` and the PYTHONPATH of `hosted_seams.hosted_environment`, so
the TS runners' own `python -m ...g3.helper` calls get the hosted seams through `sitecustomize`.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from pathlib import Path

from tests.cli_regression.support.g3 import hosted_posix_stubs

hosted_posix_stubs.install()      # before the import below: this entry process is not under the hosted startup (no `ACR_G3_HOSTED`, no `hosted_site`), and `g3.driver` -> `g3.f0` -> `g1e.quiet_gate` reads `os.getloadavg` at import
from tests.cli_regression.support.g3 import attribution, driver, f0 as f0mod, helper, hosted_checks, hosted_correctness, hosted_diag, hosted_hostclass, hosted_pack, hosted_packaged, hosted_reader, hosted_seams, hosted_toolchain, packaged  # noqa: E402

G3 = driver.G3
PRE_RUN_NAME, PACKAGED_RUN_NAME, SMOKE_RUN_NAME, RUN_NAME = driver.PRE_RUN_NAME, driver.PACKAGED_RUN_NAME, driver.SMOKE_RUN_NAME, driver.RUN_NAME
BUILD_RECORD_NAME, PACKAGED_RECORD_NAME = driver.BUILD_RECORD_NAME, driver.PACKAGED_RECORD_NAME
EXPECTED_MINUTES = {**driver.EXPECTED_MINUTES, "hosted-inputs-check": (0.1, "estimate: a manifest and an excerpt"), "python-arm-identity-f0": (0.3, "estimate: one import trace and the reference manifest, no bundle"),
                    **{f"settle-{n}": (0.5, "rh13 S3 (macOS only): ends at the second quiet sample, 30 s apart; at most 20 minutes") for n in (1, 2, 3)},
                    "conpty-probe": (4.0, "rh16 windows-4c estimate: two arms, one measured-peer session each (READY, one ACK, one 128-line burst round, FINAL, one resize, the kill and the log identity)")}
CONPTY_PROBE_RUN_NAME = "run-conpty-probe.json"
SETTLE_MAX_SECONDS = 20 * 60      # rh13 S3: the bound of each in-plan macOS settle step. The macOS job's `timeout-minutes` is 330 and the registered plan runs three of them (`rh13/report.md`, budget table): at the 30 minutes of the settle before the first gate the estimated worst case was 355.8 minutes, at 20 it is 325.8. 20 + the gate's 10-minute wait still covers the 24 minutes the first runner needed (rehearsals 2 and 3)
PYTHON_ARM_F0_NAME = "python-arm-identity-f0.json"      # the bundle-free §4.8 record (rf4d E2); the bundle form keeps `python-arm-identity.json`
RUNTIME_RECORD_NAME = "toolchain-runtime.json"      # written next to `rustc-vV.txt`, in the directory the registered run uploads whole
PACKAGED_STEPS = driver.PACKAGED_STEPS
READER_STEPS = ("calibrate-reader", "reader-measure")
READER_DESCRIPTOR_PROBLEMS = ("F0 does not bind a reader fixture descriptor", "F0's reader fixture descriptor is not this run's own")
HOSTED_ROOT_SEGMENT = "g3r2h"
REHEARSAL_SEGMENT = "g3r2"      # `helper.rehearsal_stamp` and the TS `rehearsalRefusal` accept only .../g3r2/rehearsal*


class Env:
    """The platform facts a plan needs, from one place."""
    def __init__(self, repo: str, system: str) -> None:
        self.repo, self.system = repo, system
        self.layout = hosted_packaged.layout(system)
        self.python = f"{repo}/{self.layout.python_rel}"
        self.sidecar = f"{repo}/{self.layout.sidecar_rel}"
        self.hosted_env = hosted_seams.hosted_environment(repo, {}, system)
        self.build_env = hosted_seams.hosted_environment(repo, {}, None)      # rh8 F1: the non-Windows form (no BUN_OPTIONS preload, no termios flag) for the two packaged build steps, on every platform
        self.node_client_env = hosted_seams.node_client_environment(repo, {}, system)

    def helper(self, *args: str) -> list[str]:
        return [self.python, "-B", "-m", f"{G3}.hosted_helper", *args]

    def module(self, name: str, *args: str) -> list[str]:
        return [self.python, "-B", "-m", f"{G3}.{name}", *args]

    def step(self, name: str, argv: list[str], *, cwd: str, env: dict | None = None, outputs: list[str] | None = None, stdout: str | None = None, build: bool = False) -> dict:
        node_clients = self.node_client_env if "vitest" in argv else {}      # the steps whose Node process opens the WebSocket to the backend; never the builds or Electron
        base = self.build_env if build else self.hosted_env      # `build=True` (packaged-build, packaged-electron only): the measurement seams of Windows are not given to the product's own build (rehearsal 2: `bun run build` ended with exit 0 and built nothing)
        return {"name": name, "argv": argv, "cwd": cwd, "env": {**base, **node_clients, **(env or {})}, "outputs": outputs or [], "stdout": stdout, "expectedMinutes": EXPECTED_MINUTES[name][0]}

    def packaged_variant(self, bun: list[str]) -> tuple[list[str], dict]:
        """The vitest argv and the extra environment of the two packaged attempts (`packaged-smoke`, `packaged-cold`): unchanged on Linux and macOS; on Windows the hosted variant with the Windows-native READY command."""
        argv = [*bun, "vitest", "run", "--project", "cli", hosted_packaged.packaged_entry(self.system)]
        return argv, ({hosted_packaged.READY_COMMAND_ENV: hosted_packaged.windows_ready_command(self.python)} if self.system == "Windows" else {})

    def harness(self, pre_out: str) -> str:
        return f"{pre_out}/harness-target/release/navide-cli-runtime{'.exe' if self.system == 'Windows' else ''}"


def build_pre_plan(env: Env, pre_out: str, out_parent: str) -> list[dict]:
    crate = f"{env.repo}/native/navide-cli-runtime"
    backend = f"{env.repo}/backend"
    return [
        env.step("link-probe", env.helper("link-probe", out_parent), cwd=backend),
        env.step("release-build", ["cargo", "build", "--release", "--locked"], cwd=crate, outputs=[env.sidecar]),
        env.step("harness-build", ["cargo", "build", "--release", "--locked", "--features", "fault-hooks", "--target-dir", f"{pre_out}/harness-target"], cwd=crate, outputs=[env.harness(pre_out)]),
        env.step("record-build", env.helper("record-build", env.repo, env.sidecar, env.harness(pre_out), f"{pre_out}/{BUILD_RECORD_NAME}"), cwd=backend, outputs=[f"{pre_out}/{BUILD_RECORD_NAME}"]),
        env.step("reader-fixtures", env.helper("reader-fixtures-build", f"{pre_out}/reader-fixtures", f"{pre_out}/FIXTURES.json"), cwd=backend, outputs=[f"{pre_out}/FIXTURES.json"]),
    ]


CLIENT = ["bunx", "--bun"]      # rf4c D2: the TS attempt clients run on Bun. A bare `bunx vitest` runs the package's `#!/usr/bin/env node` shebang on the image's Node (g3r5 toolchain-runtime.json); `--bun` does not


def build_packaged_plan(env: Env, pre_out: str, bun: list[str] = CLIENT) -> list[dict]:
    smoke_out, record = f"{pre_out}/packaged-smoke", f"{pre_out}/{PACKAGED_RECORD_NAME}"
    backend_exe = str(packaged.backend_path(env.repo))
    output = f"{env.repo}/{packaged.OUTPUT_REL}"
    smoke_argv, smoke_extra = env.packaged_variant(bun)
    return [
        env.step("packaged-toolchain", env.helper("package-toolchain"), cwd=f"{env.repo}/backend"),
        env.step("packaged-pyinstaller", ["uv", "--directory", "backend", "run", "pyinstaller", "--noconfirm", "agent_team_backend.spec"], cwd=env.repo, outputs=[f"{env.repo}/backend/dist"]),
        env.step("packaged-cli-runtime", ["bun", "run", "build:cli-runtime"], cwd=env.repo, outputs=[f"{env.repo}/{packaged.STAGE_REL}/{env.layout.runtime}"]),
        env.step("packaged-stage", env.helper("packaged-stage", env.repo, env.sidecar), cwd=f"{env.repo}/backend", outputs=[f"{env.repo}/{packaged.STAGE_REL}/{env.layout.runtime}"]),
        env.step("packaged-build", ["bun", "run", "build"], cwd=env.repo, outputs=[f"{env.repo}/out"], build=True),
        env.step("packaged-electron", hosted_packaged.electron_argv(env.system, output), cwd=env.repo, env={"CSC_IDENTITY_AUTO_DISCOVERY": "false"}, outputs=[f"{env.repo}/{packaged.APP_REL}"], build=True),
        env.step("packaged-record", env.helper("packaged-record", env.repo, env.sidecar, record), cwd=f"{env.repo}/backend", outputs=[record]),
        env.step("packaged-smoke", smoke_argv, cwd=env.repo,
                 env={"ACR_G3": "packaged-smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_PACKAGED_BACKEND": backend_exe, "ACR_G3_PACKAGED_RECORD": record, "ACR_G3_SIDECAR": env.sidecar, **smoke_extra},
                 outputs=[f"{smoke_out}/packaged-smoke.json"]),
    ]


def build_smoke_plan(env: Env, smoke_out: str, bun: list[str] = CLIENT) -> list[dict]:
    return [
        env.step("smoke-shell-launch", [*bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=env.repo, env={"ACR_G3": "smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_SIDECAR": env.sidecar},
                 outputs=[f"{smoke_out}/smoke-entries.json"]),
        env.step("smoke-verify", env.helper("smoke-verify", smoke_out, env.sidecar), cwd=f"{env.repo}/backend", outputs=[f"{smoke_out}/smoke-verdict.json"]),
    ]


def build_conpty_probe_plan(env: Env, probe_out: str, bun: list[str] = CLIENT) -> list[dict]:
    """rh16 windows-4c (Windows rehearsal only, NOT evidence): one measured-peer session per arm through the F0 sidecar, with the raw bytes recorded (`tests/cli/g3hConptyProbe.test.ts`). The step runs under the
    same hosted environment as every other TS attempt step (the ps shim, the termios and counters seams, the Bun preload), so what it sees is what the attempts will see."""
    return [env.step("conpty-probe", [*bun, "vitest", "run", "--project", "cli", "tests/cli/g3hConptyProbe.test.ts"], cwd=env.repo,
                     env={"ACR_G3_CONPTY_PROBE": "1", "ACR_G3_OUT": probe_out, "ACR_G3_SIDECAR": env.sidecar}, outputs=[f"{probe_out}/conpty-probe-summary.json"])]


def build_correctness_plan(env: Env, pre_out: str) -> list[dict]:
    """Every platform (rf4d E1): the 21 G1-E correctness rows, as `g1e/gates.py` `REGISTRY` runs them, on the F0 sidecar itself, with the per-row table and the collected-and-passed gate
    (`hosted_correctness`). One step, so the run record is the `run-correctness.json` (`["correctness"]`) the unchanged precondition reads. The step carries NO hosted flags: the product's tests run without the
    measurement seams. Linux and Windows run the G2 required cases from the workflow as well (`tests/cli/g2-*` are product files) and bind them to the same sidecar (`hosted_correctness.bind_g2`)."""
    out = [f"{pre_out}/{name}" for name in (hosted_correctness.JUNIT_NAME, hosted_correctness.VITEST_NAME, hosted_correctness.GATE_NAME, hosted_correctness.RECEIPT_NAME)]
    return [{"name": "correctness", "argv": [env.python, "-B", "-m", f"{G3}.hosted_correctness", "run", env.repo, pre_out, env.system], "cwd": f"{env.repo}/backend", "env": {}, "outputs": out, "stdout": None,
             "expectedMinutes": EXPECTED_MINUTES["correctness"][0]}]


def build_plan(env: Env, *, out: str, f0_path: str, build_record: str, reader_fixtures: str | None, bun: list[str] = CLIENT, g1e_bundle: str | None = None) -> list[dict]:
    """The cost plan: the macOS order with the hosted helper and quiet gate, and (Windows) without the reader steps. With `g1e_bundle` the plan is the macOS one's tail too: `python-arm-identity` after
    `seal`, and `decide` given the G1-E bundle and the report (the registered digest travels in $ACR_G3_G1E_MANIFEST_SHA256, not in the plan). Without it the step is `python-arm-identity-f0` instead
    (rf4d E2: the §4.8 record rebuilt from the frozen G1-E pins, no bundle needed) and `decide` takes no G1-E argument; the outcome then says the Python arm is non-attributable because no bundle was given."""
    f1, cal, sidecar, repo = f"{out}/freeze-f1.json", f"{out}/calibration", env.sidecar, env.repo
    env_ts = {"ACR_G3_FREEZE": f0_path, "ACR_G3_SIDECAR": sidecar}
    ts = [*bun, "vitest", "run", "--project", "cli", hosted_packaged.MATRIX_ENTRY]
    cold_argv, cold_extra = env.packaged_variant(bun)
    backend = f"{repo}/backend"
    quiet = lambda tag: env.module("hosted_quiet_gate", "before", f"{out}/{tag}")
    readers = hosted_reader.has_readers(env.system)

    def settle(number: int) -> list[dict]:
        """rh13 S3, macOS only: the rh9 settle sampler (`hosted_load_probe`, early end on the gate's own rule, `SETTLE_MAX_SECONDS` at most) before quiet gate `number`. Information only: the sampler's exit code is
        always 0, so it never ends the plan, and it decides nothing; the gate after it is unchanged and still decides."""
        if env.system != "Darwin":
            return []
        return [env.step(f"settle-{number}", env.module("hosted_load_probe", f"{out}/settle-{number}.json", "--max-seconds", str(SETTLE_MAX_SECONDS)), cwd=backend, outputs=[f"{out}/settle-{number}.json"])]

    plan = [
        env.step("f0-verify", env.helper("freeze-verify", repo, f0_path, sidecar), cwd=backend, stdout=f"{out}/logs/f0-verify.json"),
        *settle(1),
        env.step("quiet-1", quiet("q1"), cwd=backend, outputs=[f"{out}/q1/quiet-01.json"]),
        env.step("calibrate-shell", ts, cwd=repo, env={**env_ts, "ACR_G3": "calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/calibration/run.json"]),
        env.step("calibrate-launch", ts, cwd=repo, env={**env_ts, "ACR_G3": "launch-calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/launch-trials/run.json"]),
        *([env.step("calibrate-reader", env.helper("reader-calibrate", repo, f0_path, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend,
                    outputs=[f"{out}/bundle-reader/reader/calibration-margins.json"])] if readers else []),
        env.step("assemble-calibration", env.helper("assemble-calibration", repo, f0_path, cal, f"{out}/bundle-reader/reader", f"{out}/calibration.json"), cwd=backend, outputs=[f"{out}/calibration.json"]),
        env.step("f1-build", env.helper("f1-build", repo, f0_path, f"{out}/calibration.json", sidecar, f1), cwd=backend, outputs=[f1]),
        env.step("f1-verify", env.helper("freeze-verify", repo, f0_path, sidecar, f1), cwd=backend, stdout=f"{out}/logs/f1-verify.json"),
        *settle(2),
        env.step("quiet-2", quiet("q2"), cwd=backend, outputs=[f"{out}/q2/quiet-01.json"]),
        env.step("matrix", ts, cwd=repo, env={**env_ts, "ACR_G3": "matrix", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/matrix/run.json"]),
        env.step("launch-matrix", ts, cwd=repo, env={**env_ts, "ACR_G3": "launch", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/launch-trials/run.json"]),
        *settle(3),
        env.step("quiet-3", quiet("q3"), cwd=backend, outputs=[f"{out}/q3/quiet-01.json"]),
        *([env.step("reader-measure", env.helper("reader-measure", repo, f0_path, f1, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend, outputs=[f"{out}/bundle-reader/reader.json"])]
          if readers else []),
        env.step("packaged-cold", cold_argv, cwd=repo, env={**env_ts, "ACR_G3": "packaged", "ACR_G3_OUT": f"{out}/m", "ACR_G3_PACKAGED_BACKEND": str(packaged.backend_path(repo)),
                                                             "ACR_G3_PACKAGED_RECORD": str(Path(build_record).with_name(PACKAGED_RECORD_NAME)), **cold_extra}, outputs=[f"{out}/m/packaged-trials/run.json"]),
        env.step("assemble-bundle", env.helper("assemble-bundle", repo, f0_path, f1, out, f"{out}/bundle"), cwd=backend, outputs=[f"{out}/bundle/freeze.json"]),
        env.step("seal", env.helper("seal", f"{out}/bundle"), cwd=backend, stdout=f"{out}/logs/manifest.sha256"),
        *([env.step("python-arm-identity", env.helper("python-arm-identity", repo, g1e_bundle, f"{out}/python-arm-identity.json"), cwd=backend, outputs=[f"{out}/python-arm-identity.json"])]
          if g1e_bundle else [env.step("python-arm-identity-f0", env.helper("python-arm-identity-f0", repo, f"{out}/{PYTHON_ARM_F0_NAME}"), cwd=backend, outputs=[f"{out}/{PYTHON_ARM_F0_NAME}"])]),
        env.step("decide", env.helper("decide", repo, f0_path, f1, f"{out}/bundle", f"{out}/logs/manifest.sha256", f"{out}/outcome.json", build_record,
                                      *([g1e_bundle, f"{out}/python-arm-identity.json"] if g1e_bundle else [])), cwd=backend, outputs=[f"{out}/outcome.json"]),
    ]
    return plan


# ---------------------------------------------------------------------------------------------------------------------------- output rules
def registered_out(runner_temp: str | Path) -> Path:
    return Path(runner_temp) / HOSTED_ROOT_SEGMENT / "out"


def rehearsal_out(runner_temp: str | Path) -> Path:
    return Path(runner_temp) / REHEARSAL_SEGMENT / "rehearsal"


def claim_path(out: str | Path) -> Path:
    return Path(out).parent / driver.REGISTERED_CLAIM_NAME


def out_problem(out: str | Path, runner_temp: str | Path, rehearsal: bool) -> str | None:
    """The registered run writes only `$RUNNER_TEMP/g3r2h/out`; a rehearsal only `$RUNNER_TEMP/g3r2/rehearsal` or its `attempt-<n>` subdirectories."""
    real = Path(os.path.realpath(out))
    if rehearsal:
        base = Path(os.path.realpath(rehearsal_out(runner_temp)))
        if real == base or (real.parent == base and re.fullmatch(r"attempt-\d+", real.name)):
            return None
        return f"a hosted rehearsal writes only to {base} or its attempt-<n> subdirectories, not {out}"
    if real == Path(os.path.realpath(registered_out(runner_temp))):
        return None
    return f"the hosted cost run writes only to {registered_out(runner_temp)}, not {out}"


def pre_run_problems(f0: dict, repo: str, pre_out: Path, smoke_out: Path, system: str, last_code_commit_ts: int | None = None, *, g1e_bundle: str | None = None,
                     g1e_manifest_sha256: str | None = None) -> list[str]:
    """The macOS precondition, minus what a hosted job proves another way: on Windows the reader descriptor. EVERY platform keeps the correctness record (rf4d E1: the in-job `correctness` step runs the 21-row
    registry), and adds the sidecar binding: the correctness receipt, and on Linux and Windows the G2 binding, must name F0's sidecar by path and SHA-256 (`hosted_correctness.receipt_problems`). When the
    run names a G1-E bundle, the macOS G1-E check applies unchanged (the registered digest present, the bundle loads under it, its per-pair data reads), so a bad or unregistered bundle stops the run
    before any measurement."""
    problems = driver.pre_run_problems(f0, repo, pre_out, smoke_out, last_code_commit_ts, g1e_bundle=g1e_bundle, g1e_manifest_sha256=g1e_manifest_sha256)
    drop = list(READER_DESCRIPTOR_PROBLEMS) if system == "Windows" else []
    return [p for p in problems if not any(p.startswith(prefix) for prefix in drop)] + hosted_correctness.receipt_problems(f0, pre_out, system)


def absolutize(argv: list[str]) -> list[str]:
    """Every argument but the command and the `--rehearsal` flag is a path."""
    return [argv[0]] + [a if a == "--rehearsal" else os.path.abspath(a) for a in argv[1:]] if argv else []


def _run_plan(plan: list[dict], out: Path, record_name: str, **kwargs) -> int:
    return driver._run_plan(plan, out, record_name, **kwargs)


TAIL_LINES, TOKEN_PATTERNS, redacted_tail = hosted_diag.TAIL_LINES, hosted_diag.TOKEN_PATTERNS, hosted_diag.redacted_tail      # the redaction lives with the other rehearsal diagnostics
REHEARSAL_WORKFLOW = "G3 Hosted Rehearsal (not evidence)"      # `name:` of the rehearsal workflow, which runs build/packaged/smoke without the flag
BUILD_STEP = "packaged-build"
BUILD_PRODUCTS = ("out/main/index.js", "dist-plugins/mini-ide")      # what `bun run build` leaves in the repository, and electron-builder's `files` need (`package.json` `main`, `build.files`)


def node_runner_for(base_runner):
    """The runner of the extra Node attempt (`hosted_diag.node_diagnostic`): the real one only when the step runner is the real one. A test that substitutes the step runner must not start a process."""
    return hosted_diag.run_capture if base_runner is driver.default_runner else None


def diagnostic_runner(base, *, emit=hosted_diag.emit, node_run=None, environ=None):
    """`base` (the unchanged runner) that, when a step exits non-zero, prints the first line of that step's own log that starts with electron-builder's error marker (if any) and the redacted tail of the log
    to the console, then the reason fields of its receipt (and, for the packaged smoke, one probe of the frozen backend and, with `node_run`, one attempt through Node; for a failed `smoke-verify`, the
    redacted tails of the logs its failed attempts already wrote, read from the temporary directory `environ` names; `hosted_diag`). `emit` never raises on the console's encoding. Rehearsal diagnostics of the
    pre-measurement build steps only: the cost command never gets it (a measured step stays blind; its tail steps after `seal` get `tail_runner` instead, rh26 F-3) and nothing is written anywhere. The `smoke-verify` diagnostic only reads files; it starts no process.
    The step's exit code is never changed by a diagnostic: whatever a diagnostic raises (a file it cannot read, a temporary directory it cannot make, a process it cannot start) is caught, reported as one line that
    names the exception class and nothing else (no message, no path), and the base runner's code is returned. A `packaged-build` that exits 0 gets one line saying whether `out/main/index.js` and
    `dist-plugins/mini-ide` exist (and, if one is missing, the log tail); its exit code stays 0 (rh8 F2)."""
    def failed(error: Exception) -> None:
        try:
            emit(f"rehearsal diagnostics failed: {type(error).__name__}")
        except Exception:  # noqa: BLE001 - not even a closed console may change the step's exit code
            pass

    def log_section(step: dict, stdout_path: Path | None) -> None:
        if stdout_path is not None and Path(stdout_path).is_file():
            log = Path(stdout_path).read_text(encoding="utf-8", errors="replace")
            first = hosted_diag.first_error_line(log)
            if first is not None:
                emit(f"--- {step['name']}: first line of its log that starts with the electron-builder error marker (rehearsal diagnostics) ---")
                emit(first)
            emit(f"--- {step['name']}: last {TAIL_LINES} lines of its log (rehearsal diagnostics) ---")
            for line in redacted_tail(log):
                emit(line)

    def build_products(step: dict, stdout_path: Path | None) -> None:
        """rh8 F2: `bun run build` that exited 0 must have left `out/main/index.js` and `dist-plugins/mini-ide` in its working directory (the repository). One line says whether they exist; when either is
        missing, the redacted tail of the step's own log follows. A silent no-op build (rehearsal 2, Windows) therefore leaves a log, not a guess."""
        repo = Path(step["cwd"])
        found = {name: (repo / name).exists() for name in BUILD_PRODUCTS}
        emit(f"--- {step['name']}: exited 0; " + ", ".join(f"{name} {'exists' if present else 'MISSING'}" for name, present in found.items()) + " (rehearsal diagnostics) ---")
        if not all(found.values()):
            log_section(step, stdout_path)

    def run(step: dict, stdout_path: Path | None) -> int:
        code = base(step, stdout_path)
        if code == 0 and step["name"] == BUILD_STEP:
            try:
                build_products(step, stdout_path)
            except Exception as error:  # noqa: BLE001 - a diagnostic is information only
                failed(error)
        if code != 0:
            for section in (lambda: log_section(step, stdout_path), lambda: hosted_diag.step_reasons(step, emit, node_run, environ)):      # one section failing does not hide the other
                try:
                    section()
                except Exception as error:  # noqa: BLE001 - a diagnostic is information only
                    failed(error)
        return code
    return run


TAIL_STEPS = ("python-arm-identity-f0", "decide", "rehearsal-verdict", "rehearsal-stamp")      # rh26 F-3: the cost plan's rehearsal steps after `seal`; none of them measures anything
TAIL_STAGE = ("g3r2h-summary", "evidence", "cost-tail")      # under $RUNNER_TEMP: inside the directory every rehearsal workflow leak-checks and uploads (`g3r2h-summary`)
# rh27 (review A-F1 of rh26): the window of a tail step, sized for the longest print of `helper.py rehearsal-verdict` (`indent=1`, the defects before the reasons): 7 frame lines + at most 27 defects
# (13 admission conditions, the matrix detail and one per reason) + 4 lines per reason pair, at most 13 pairs (10 adverse reasons, or the adverse reader reason with 12 unmasked ones) = 86 lines.
# 200 lines keep every defect line of that print even with 114 more lines (stderr) after it. A line longer than TAIL_STEP_LINE_CHARS is cut there, after the redaction, and says how much was cut.
# Byte bound of `<step>.log`: the header line (under 200 bytes) + 200 x (2000 characters x at most 4 UTF-8 bytes + the cut note, under 48 bytes, + the newline) < 1,610,000 bytes; ASCII < 410,000.
TAIL_STEP_LINES, TAIL_STEP_LINE_CHARS = 200, 2000


def tail_runner(base, stage: Path, *, emit=hosted_diag.emit):
    """rh26 F-3 (rehearsal 5, R-3), the rehearsal cost run only: `base` (the unchanged runner) for every step. When a step of `TAIL_STEPS` exits non-zero, the redacted tail of its own log
    (`redacted_tail`: the leak check's token shapes replaced; rh27: the last `TAIL_STEP_LINES` lines, each cut at `TAIL_STEP_LINE_CHARS`) is printed to the console and written to `stage/<step>.log`. The rehearsal uploads `stage` (it lies in `g3r2h-summary`), after the
    workflow's leak check has read it. Before either, the tail goes through the pack's privacy gate (`hosted_pack.privacy_findings`, the local names): with a finding, only its kind and count are written,
    never the text. Any other step (a measured one) and any step that exits 0 gets nothing: a measured step stays blind. The exit code is never changed; a diagnostic that raises is one line that names
    the exception class."""
    def run(step: dict, stdout_path: Path | None) -> int:
        code = base(step, stdout_path)
        if code == 0 or step["name"] not in TAIL_STEPS:
            return code
        try:
            log = Path(stdout_path).read_text(encoding="utf-8", errors="replace") if stdout_path is not None and Path(stdout_path).is_file() else ""
            lines = [line if len(line) <= TAIL_STEP_LINE_CHARS else f"{line[:TAIL_STEP_LINE_CHARS]} [cut: {len(line) - TAIL_STEP_LINE_CHARS} more characters]"
                     for line in redacted_tail(log, TAIL_STEP_LINES)]
            findings = hosted_pack.privacy_findings([(step["name"], ("\n".join(lines) + "\n").encode("utf-8"))])
            if findings:
                lines = [f"the tail is withheld by the privacy gate: {finding['count']} {finding['kind']} match(es)" for finding in findings]
            text = [f"--- {step['name']}: exit {code}; the last {len(lines)} lines of its log (rehearsal diagnostics; after seal, nothing is measured) ---", *lines]
            for line in text:
                emit(line)
            Path(stage).mkdir(parents=True, exist_ok=True)
            (Path(stage) / f"{step['name']}.log").write_text("\n".join(text) + "\n", encoding="utf-8")
        except Exception as error:  # noqa: BLE001 - a diagnostic is information only
            try:
                emit(f"rehearsal diagnostics failed: {type(error).__name__}")
            except Exception:  # noqa: BLE001 - not even a closed console may change the step's exit code
                pass
        return code
    return run


def flushed_print(line: str) -> None:
    """rh26 F-2 (rehearsal 5, R-2): `say` of the cost plan. The driver's stdout is a pipe, so a plain `print` reaches the job log only when the driver exits; flushed, each `<step>: exit <code>` line is
    stamped when its step ends, which is what S-4 items 1 to 3 read."""
    print(line, flush=True)


def record_js_runtime(repo: str, destination: Path, *, record=hosted_toolchain.runtime_record) -> None:
    """Write which JavaScript runtime `bun run` and `bunx` really use on this runner (t14 item 3). F0's frozen toolchain record cannot hold bun, so this is a separate file; it is written outside the
    journaled plans (their step lists are pinned to the macOS order) and from the repository root, because bun reads `bunfig.toml` from the working directory. A runner without bun, or a command that runs on a PATH Node, stops the run here."""
    text = json.dumps(record(cwd=Path(repo)), indent=1, sort_keys=True) + "\n"
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text(text, encoding="utf-8")


def reported_precondition(check, say=print, record=None):
    """rh16 (rehearsal only; the registered run keeps the refusing precondition). A rehearsal is there to show every failure in one run, so a pre-run record that is not clean (a failed correctness step, say) is
    PRINTED and the cost run goes on: it is not evidence and is stamped so. Problems are fixed strings of `pre_run_problems`.
    rh17 (S-2): the problems are also handed to `record`, so that a rehearsal whose precondition would have refused the registered run does not end as a pass (`main` writes them where the verdict and the
    summary read them, and returns non-zero after the plan)."""
    def reported() -> list[str]:
        problems = list(check())
        for problem in problems:
            say(f"rehearsal precondition problem (the rehearsal goes on, it is not evidence): {problem}")
        if record is not None:
            record(problems)
        return []
    return reported


def main(argv: list[str], *, system: str | None = None, runner_temp: str | None = None, install=hosted_seams.install, run_plan=_run_plan, base_runner=driver.default_runner, emit=hosted_diag.emit,
         environ: dict | None = None, node_run=None) -> int:
    from tests.cli_regression.support.g3 import incomplete
    argv = absolutize(argv)
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    system = system or platform.system()
    try:
        install(system)
        rehearsal = "--rehearsal" in args
        args = [a for a in args if a != "--rehearsal"]
        if cmd in ("build", "packaged", "smoke", "correctness", "conpty-probe") and len(args) == 2:
            plan_env = Env(args[0], system)
            out = Path(args[1])
            diagnostics = rehearsal or (os.environ if environ is None else environ).get("GITHUB_WORKFLOW") == REHEARSAL_WORKFLOW
            kwargs = {"runner": diagnostic_runner(base_runner, emit=emit, node_run=node_run or node_runner_for(base_runner), environ=environ) if diagnostics else base_runner}
            if cmd == "build":
                return run_plan(build_pre_plan(plan_env, args[1], str(out.parent)), out, PRE_RUN_NAME, **kwargs)
            if cmd == "correctness":
                return run_plan(build_correctness_plan(plan_env, args[1]), out, driver.CORRECTNESS_RUN_NAME, **kwargs)
            if cmd == "conpty-probe":
                if system != "Windows" or not rehearsal:
                    raise driver.DriverError("the ConPTY probe is a Windows rehearsal step")
                return run_plan(build_conpty_probe_plan(plan_env, args[1]), out, CONPTY_PROBE_RUN_NAME, **kwargs)
            if cmd == "packaged":
                if run_plan is _run_plan:      # the real runner only: a test that substitutes the plan runner must not start bun
                    record_js_runtime(plan_env.repo, out.parent / RUNTIME_RECORD_NAME)
                return run_plan(build_packaged_plan(plan_env, args[1]), out, PACKAGED_RUN_NAME, **kwargs)
            return run_plan(build_smoke_plan(plan_env, args[1]), out, SMOKE_RUN_NAME, **kwargs)
        if cmd in ("cost", "recover") and len(args) in (6, 7):      # the 7th, optional argument is the sealed G1-E bundle; its registered digest is $ACR_G3_G1E_MANIFEST_SHA256
            repo, out, f0_path, build_record, pre_out, smoke_out = args[:6]
            g1e_bundle = args[6] if len(args) == 7 else None
            registered_digest = attribution.registered_manifest_sha256(environ)
            temp = runner_temp or os.environ["RUNNER_TEMP"]
            if refusal := out_problem(out, os.path.abspath(temp), rehearsal):
                raise driver.DriverError(refusal)
            if rehearsal and cmd == "recover":
                raise driver.DriverError("a rehearsal has nothing to recover: run it again into a fresh attempt-<n> directory")
            env = Env(repo, system)
            f0 = json.loads(Path(f0_path).read_text())
            last_commit = helper.last_code_commit_ts(Path(repo))

            def write_incomplete(record: dict) -> None:
                build = json.loads(Path(build_record).read_text()) if Path(build_record).is_file() else None
                packaged_record = driver._load(Path(build_record).with_name(PACKAGED_RECORD_NAME))
                doc = incomplete.document(f0, record, run_dir=Path(out), build_record=build, last_code_commit_ts=last_commit, packaged_state=(Path(repo), packaged_record))
                doc["platforms"] = [hosted_hostclass.platform_entry(f0, measured=False)]      # F1: an incomplete hosted outcome names its platform too, and says nothing was measured to a decision
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / "outcome.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                if rehearsal:
                    helper.rehearsal_stamp(Path(out))

            descriptor = (f0.get("readerFixtureDescriptor") or {}).get("path")
            plan = build_plan(env, out=out, f0_path=f0_path, build_record=build_record, reader_fixtures=descriptor, g1e_bundle=g1e_bundle)
            if rehearsal:
                plan = driver.rehearsal_plan(plan, repo, out)
                plan = [{**s, "argv": env.helper(*s["argv"][4:]), "env": {**s["env"], **env.hosted_env}} if s["name"] in ("rehearsal-verdict", "rehearsal-stamp") else s for s in plan]
            claim = claim_path(out)
            if cmd == "recover":
                if not claim.is_file():
                    raise driver.DriverError(f"{claim} does not exist: this journal was never claimed by the registered run")
                problems = driver.claim_problems(claim, plan)
                journal = Path(out) / RUN_NAME
                record = json.loads(journal.read_text()) if journal.is_file() else driver.claim_only_record(plan)
                problems += driver.recovery_problems(record, plan, (Path(out) / "outcome.json").exists())
                if problems:
                    raise driver.DriverError("; ".join(problems))
                write_incomplete(record)
                return 0
            precondition = lambda: pre_run_problems(f0, repo, Path(pre_out), Path(smoke_out), system, last_commit, g1e_bundle=g1e_bundle, g1e_manifest_sha256=registered_digest)
            found: list[str] = []      # rh17 (S-2): what the precondition of a rehearsal reported

            def record_precondition(problems: list[str]) -> None:
                """Before the first plan step: the record the `rehearsal-verdict` step and `hosted_checks` read."""
                found.extend(problems)
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / hosted_checks.PRECONDITION_NAME).write_text(json.dumps({"schema": hosted_checks.PRECONDITION_SCHEMA, "notEvidence": True, "problems": problems}, indent=1) + "\n", encoding="utf-8")

            if rehearsal:
                precondition = reported_precondition(precondition, record=record_precondition)
            tail = {"runner": tail_runner(base_runner, Path(os.path.abspath(temp)).joinpath(*TAIL_STAGE), emit=emit)} if rehearsal else {}      # rh26 F-3: the registered run keeps the default runner
            code = run_plan(plan, Path(out), RUN_NAME, verify_f0=driver.f0_verifier(Path(repo), Path(f0_path), helper.pin_manifest(Path(repo)), Path(env.sidecar)),
                            precondition=precondition,
                            on_stop=write_incomplete,
                            claim_path=None if rehearsal else claim, record_extra=driver.REHEARSAL_RECORD if rehearsal else None, say=flushed_print, **tail)
            return 2 if code == 0 and found else code      # a rehearsal whose precondition found a problem never ends 0, whatever its plan did
    except driver.DriverError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
