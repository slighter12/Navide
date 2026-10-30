"""The hosted G3 run driver: the same step order as the macOS `cost` path (design 3.1, `g3/driver.py`), the platform parts substituted, the sequencer reused.

Reused unchanged by import: `execute` (claim, journal, no retry, blindness), `_step`'s shape, `argv_hash`, `plan_sha256`, `f0_verifier`, the claim and recovery rules, the macOS `pre_run_problems`.
Substituted: the interpreter path (`backend/.venv/bin/python` or `Scripts/python.exe`), the helper (`hosted_helper`), the quiet gate (`hosted_quiet_gate`), the packaged plan (`hosted_packaged`), the output
rules (`$RUNNER_TEMP/g3r2h/out` instead of the repository's `OUT/g3r2/run`) and the precondition: the cost job's `needs: correctness` is the correctness precondition (a separate runner), so the
macOS `run-correctness.json` requirement is dropped here, and so is the reader-fixture descriptor on Windows (no reader rows). Dropped steps: `python-arm-identity`; Windows also drops the reader steps.

    hosted_driver build|packaged REPO PRE_OUT        hosted_driver smoke REPO SMOKE_OUT
    hosted_driver cost|recover REPO OUT F0 BUILD_RECORD PRE_OUT SMOKE_OUT [--rehearsal]

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

from tests.cli_regression.support.g3 import driver, f0 as f0mod, helper, hosted_packaged, hosted_seams, packaged

G3 = driver.G3
PRE_RUN_NAME, PACKAGED_RUN_NAME, SMOKE_RUN_NAME, RUN_NAME = driver.PRE_RUN_NAME, driver.PACKAGED_RUN_NAME, driver.SMOKE_RUN_NAME, driver.RUN_NAME
BUILD_RECORD_NAME, PACKAGED_RECORD_NAME = driver.BUILD_RECORD_NAME, driver.PACKAGED_RECORD_NAME
EXPECTED_MINUTES = {**driver.EXPECTED_MINUTES, "hosted-inputs-check": (0.1, "estimate: a manifest and an excerpt")}
PACKAGED_STEPS = driver.PACKAGED_STEPS
READER_STEPS = ("calibrate-reader", "reader-measure")
CORRECTNESS_PROBLEM = "run-correctness.json is missing"
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

    def helper(self, *args: str) -> list[str]:
        return [self.python, "-B", "-m", f"{G3}.hosted_helper", *args]

    def module(self, name: str, *args: str) -> list[str]:
        return [self.python, "-B", "-m", f"{G3}.{name}", *args]

    def step(self, name: str, argv: list[str], *, cwd: str, env: dict | None = None, outputs: list[str] | None = None, stdout: str | None = None) -> dict:
        return {"name": name, "argv": argv, "cwd": cwd, "env": {**self.hosted_env, **(env or {})}, "outputs": outputs or [], "stdout": stdout, "expectedMinutes": EXPECTED_MINUTES[name][0]}

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


def build_packaged_plan(env: Env, pre_out: str, bun: str = "bunx") -> list[dict]:
    smoke_out, record = f"{pre_out}/packaged-smoke", f"{pre_out}/{PACKAGED_RECORD_NAME}"
    backend_exe = str(packaged.backend_path(env.repo))
    output = f"{env.repo}/{packaged.OUTPUT_REL}"
    return [
        env.step("packaged-toolchain", env.helper("package-toolchain"), cwd=f"{env.repo}/backend"),
        env.step("packaged-pyinstaller", ["uv", "--directory", "backend", "run", "pyinstaller", "--noconfirm", "agent_team_backend.spec"], cwd=env.repo, outputs=[f"{env.repo}/backend/dist"]),
        env.step("packaged-cli-runtime", ["bun", "run", "build:cli-runtime"], cwd=env.repo, outputs=[f"{env.repo}/{packaged.STAGE_REL}/{env.layout.runtime}"]),
        env.step("packaged-stage", env.helper("packaged-stage", env.repo, env.sidecar), cwd=f"{env.repo}/backend", outputs=[f"{env.repo}/{packaged.STAGE_REL}/{env.layout.runtime}"]),
        env.step("packaged-build", ["bun", "run", "build"], cwd=env.repo, outputs=[f"{env.repo}/out"]),
        env.step("packaged-electron", hosted_packaged.electron_argv(env.system, output), cwd=env.repo, env={"CSC_IDENTITY_AUTO_DISCOVERY": "false"}, outputs=[f"{env.repo}/{packaged.APP_REL}"]),
        env.step("packaged-record", env.helper("packaged-record", env.repo, env.sidecar, record), cwd=f"{env.repo}/backend", outputs=[record]),
        env.step("packaged-smoke", [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=env.repo,
                 env={"ACR_G3": "packaged-smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_PACKAGED_BACKEND": backend_exe, "ACR_G3_PACKAGED_RECORD": record, "ACR_G3_SIDECAR": env.sidecar},
                 outputs=[f"{smoke_out}/packaged-smoke.json"]),
    ]


def build_smoke_plan(env: Env, smoke_out: str, bun: str = "bunx") -> list[dict]:
    return [
        env.step("smoke-shell-launch", [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"], cwd=env.repo, env={"ACR_G3": "smoke", "ACR_G3_OUT": smoke_out, "ACR_G3_SIDECAR": env.sidecar},
                 outputs=[f"{smoke_out}/smoke-entries.json"]),
        env.step("smoke-verify", env.helper("smoke-verify", smoke_out, env.sidecar), cwd=f"{env.repo}/backend", outputs=[f"{smoke_out}/smoke-verdict.json"]),
    ]


def build_plan(env: Env, *, out: str, f0_path: str, build_record: str, reader_fixtures: str | None, bun: str = "bunx") -> list[dict]:
    """The cost plan: the macOS order with the hosted helper and quiet gate, without `python-arm-identity`, and (Windows) without the reader steps."""
    f1, cal, sidecar, repo = f"{out}/freeze-f1.json", f"{out}/calibration", env.sidecar, env.repo
    env_ts = {"ACR_G3_FREEZE": f0_path, "ACR_G3_SIDECAR": sidecar}
    ts = [bun, "vitest", "run", "--project", "cli", "tests/cli/g3Matrix.test.ts"]
    backend = f"{repo}/backend"
    quiet = lambda tag: env.module("hosted_quiet_gate", "before", f"{out}/{tag}")
    readers = env.system == "Linux"
    plan = [
        env.step("f0-verify", env.helper("freeze-verify", repo, f0_path, sidecar), cwd=backend, stdout=f"{out}/logs/f0-verify.json"),
        env.step("quiet-1", quiet("q1"), cwd=backend, outputs=[f"{out}/q1/quiet-01.json"]),
        env.step("calibrate-shell", ts, cwd=repo, env={**env_ts, "ACR_G3": "calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/calibration/run.json"]),
        env.step("calibrate-launch", ts, cwd=repo, env={**env_ts, "ACR_G3": "launch-calibrate", "ACR_G3_OUT": cal}, outputs=[f"{cal}/launch-trials/run.json"]),
        *([env.step("calibrate-reader", env.helper("reader-calibrate", repo, f0_path, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend,
                    outputs=[f"{out}/bundle-reader/reader/calibration-margins.json"])] if readers else []),
        env.step("assemble-calibration", env.helper("assemble-calibration", repo, f0_path, cal, f"{out}/bundle-reader/reader", f"{out}/calibration.json"), cwd=backend, outputs=[f"{out}/calibration.json"]),
        env.step("f1-build", env.helper("f1-build", repo, f0_path, f"{out}/calibration.json", sidecar, f1), cwd=backend, outputs=[f1]),
        env.step("f1-verify", env.helper("freeze-verify", repo, f0_path, sidecar, f1), cwd=backend, stdout=f"{out}/logs/f1-verify.json"),
        env.step("quiet-2", quiet("q2"), cwd=backend, outputs=[f"{out}/q2/quiet-01.json"]),
        env.step("matrix", ts, cwd=repo, env={**env_ts, "ACR_G3": "matrix", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/matrix/run.json"]),
        env.step("launch-matrix", ts, cwd=repo, env={**env_ts, "ACR_G3": "launch", "ACR_G3_F1": f1, "ACR_G3_OUT": f"{out}/m"}, outputs=[f"{out}/m/launch-trials/run.json"]),
        env.step("quiet-3", quiet("q3"), cwd=backend, outputs=[f"{out}/q3/quiet-01.json"]),
        *([env.step("reader-measure", env.helper("reader-measure", repo, f0_path, f1, sidecar, reader_fixtures, f"{out}/reader-root", f"{out}/bundle-reader"), cwd=backend, outputs=[f"{out}/bundle-reader/reader.json"])]
          if readers else []),
        env.step("packaged-cold", ts, cwd=repo, env={**env_ts, "ACR_G3": "packaged", "ACR_G3_OUT": f"{out}/m", "ACR_G3_PACKAGED_BACKEND": str(packaged.backend_path(repo)),
                                                      "ACR_G3_PACKAGED_RECORD": str(Path(build_record).with_name(PACKAGED_RECORD_NAME))}, outputs=[f"{out}/m/packaged-trials/run.json"]),
        env.step("assemble-bundle", env.helper("assemble-bundle", repo, f0_path, f1, out, f"{out}/bundle"), cwd=backend, outputs=[f"{out}/bundle/freeze.json"]),
        env.step("seal", env.helper("seal", f"{out}/bundle"), cwd=backend, stdout=f"{out}/logs/manifest.sha256"),
        env.step("decide", env.helper("decide", repo, f0_path, f1, f"{out}/bundle", f"{out}/logs/manifest.sha256", f"{out}/outcome.json", build_record), cwd=backend, outputs=[f"{out}/outcome.json"]),
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


def pre_run_problems(f0: dict, repo: str, pre_out: Path, smoke_out: Path, system: str, last_code_commit_ts: int | None = None) -> list[str]:
    """The macOS precondition, minus what a hosted job proves another way: the correctness record (the `correctness` job is a `needs:` of the cost job) and, on Windows, the reader descriptor."""
    problems = driver.pre_run_problems(f0, repo, pre_out, smoke_out, last_code_commit_ts)
    drop = [CORRECTNESS_PROBLEM] + (list(READER_DESCRIPTOR_PROBLEMS) if system == "Windows" else [])
    return [p for p in problems if not any(p.startswith(prefix) for prefix in drop)]


def absolutize(argv: list[str]) -> list[str]:
    """Every argument but the command and the `--rehearsal` flag is a path."""
    return [argv[0]] + [a if a == "--rehearsal" else os.path.abspath(a) for a in argv[1:]] if argv else []


def _run_plan(plan: list[dict], out: Path, record_name: str, **kwargs) -> int:
    return driver._run_plan(plan, out, record_name, **kwargs)


TAIL_LINES = 40
TOKEN_PATTERNS = (re.compile(r"(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}"), re.compile(r"github_pat_[A-Za-z0-9_]{20,}"))      # the leak-check token shapes
REHEARSAL_WORKFLOW = "G3 Hosted Rehearsal (not evidence)"      # `name:` of the rehearsal workflow, which runs build/packaged/smoke without the flag


def redacted_tail(text: str, lines: int = TAIL_LINES) -> list[str]:
    """The last `lines` lines of `text`, every token-shaped string replaced with `[redacted]`."""
    out = text.splitlines()[-lines:]
    for pattern in TOKEN_PATTERNS:
        out = [pattern.sub("[redacted]", line) for line in out]
    return out


def diagnostic_runner(base, *, emit=print):
    """`base` (the unchanged runner) that, when a step exits non-zero, prints the redacted tail of that step's own log to the console. Rehearsal diagnostics of the pre-measurement build steps only:
    the cost command never gets it (a measured step stays blind) and nothing is written anywhere."""
    def run(step: dict, stdout_path: Path | None) -> int:
        code = base(step, stdout_path)
        if code != 0 and stdout_path is not None and Path(stdout_path).is_file():
            emit(f"--- {step['name']}: last {TAIL_LINES} lines of its log (rehearsal diagnostics) ---")
            for line in redacted_tail(Path(stdout_path).read_text(encoding="utf-8", errors="replace")):
                emit(line)
        return code
    return run


def main(argv: list[str], *, system: str | None = None, runner_temp: str | None = None, install=hosted_seams.install, run_plan=_run_plan, base_runner=driver.default_runner, emit=print,
         environ: dict | None = None) -> int:
    from tests.cli_regression.support.g3 import incomplete
    argv = absolutize(argv)
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    system = system or platform.system()
    try:
        install(system)
        rehearsal = "--rehearsal" in args
        args = [a for a in args if a != "--rehearsal"]
        if cmd in ("build", "packaged", "smoke") and len(args) == 2:
            plan_env = Env(args[0], system)
            out = Path(args[1])
            diagnostics = rehearsal or (os.environ if environ is None else environ).get("GITHUB_WORKFLOW") == REHEARSAL_WORKFLOW
            kwargs = {"runner": diagnostic_runner(base_runner, emit=emit) if diagnostics else base_runner}
            if cmd == "build":
                return run_plan(build_pre_plan(plan_env, args[1], str(out.parent)), out, PRE_RUN_NAME, **kwargs)
            if cmd == "packaged":
                return run_plan(build_packaged_plan(plan_env, args[1]), out, PACKAGED_RUN_NAME, **kwargs)
            return run_plan(build_smoke_plan(plan_env, args[1]), out, SMOKE_RUN_NAME, **kwargs)
        if cmd in ("cost", "recover") and len(args) == 6:
            repo, out, f0_path, build_record, pre_out, smoke_out = args
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
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / "outcome.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                if rehearsal:
                    helper.rehearsal_stamp(Path(out))

            descriptor = (f0.get("readerFixtureDescriptor") or {}).get("path")
            plan = build_plan(env, out=out, f0_path=f0_path, build_record=build_record, reader_fixtures=descriptor)
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
            return run_plan(plan, Path(out), RUN_NAME, verify_f0=driver.f0_verifier(Path(repo), Path(f0_path), helper.pin_manifest(Path(repo)), Path(env.sidecar)),
                            precondition=lambda: pre_run_problems(f0, repo, Path(pre_out), Path(smoke_out), system, last_commit), on_stop=write_incomplete,
                            claim_path=None if rehearsal else claim, record_extra=driver.REHEARSAL_RECORD if rehearsal else None)
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
