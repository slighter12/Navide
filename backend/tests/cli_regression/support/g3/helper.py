"""Command line of the G3 run (everything the driver's steps and the TS runners call). Thin: each command wraps a tested module.

    helper constants
    helper f0-build ROOT OUT ARGS_JSON            ARGS: {branch, sidecar, seed, approval, reader_extra?}; writes OUT, prints its SHA-256
    helper freeze-verify ROOT F0 SIDECAR [F1]     prints {"problems": [...]} (F0 alone, or F1 and the F0 it names)
    helper toolchain-check ROOT F0 SIDECAR        the g1e toolchain check against F0's toolchain
    helper checked-inputs F0                      {"checkedInputs": n}: the frozen inputs verify_f0 re-checks before every attempt (a receipt's freezeCheckedInputs)
    helper reader-calibrate ROOT F0 SIDECAR FIXTURES_JSON READER_ROOT BUNDLE
    helper reader-measure ROOT F0 F1 SIDECAR FIXTURES_JSON READER_ROOT BUNDLE
    helper assemble-calibration ROOT F0 CAL_DIR READER_DIR OUT_JSON
    helper f1-build ROOT F0 CALIBRATION_JSON SIDECAR OUT
    helper assemble-bundle ROOT F0 F1 RUN_OUT BUNDLE
    helper seal BUNDLE                            prints the manifest SHA-256
    helper attempt-report DIR                     G3-r2: pass/fail fields of the warm-up receipts under DIR (no value); the real-artifact check reads receipts only through this
    helper rehearsal-verdict F0 F1 RUN_OUT       G3-r2 round 2: re-admits every receipt of a rehearsal through the decide code and classifies each outcome reason as count-driven (with a code-line proof) or defect; exit 0 = pass
    helper rehearsal-stamp DIR                  G3-r2: marks every JSON object under DIR (which must be under g3r2/rehearsal*) `notEvidence: true` and writes DIR/NOT-EVIDENCE.json
    helper record-build ROOT SIDECAR HARNESS OUT  writes sidecar-build.json {head, sha256, builtAt, builtAtEpoch, harnessSha256}
    helper reader-fixtures-build OUT_DIR DESCRIPTOR   builds the registered reader fixtures (cwd = backend/) and their descriptor
    helper launch-check RECEIPT MODE VENDOR CELL REPEAT FREEZE_SHA SIDECAR_SHA WARMUP01   {"problems": [...]} of one launch receipt (recomputed in Python)
    helper isolated-env DIR                       prints the isolated environment of a harness run
    helper packaged-stage ROOT SIDECAR            byte-copies the F0 sidecar to build/cli-runtime
    helper packaged-record ROOT SIDECAR OUT       writes packaged-build.json {head, backend and runtime digests, build time}
    helper smoke-verify DIR SIDECAR               checks DIR/smoke-entries.json, writes smoke-verdict.json, exit 0 = passed
    helper python-arm-identity ROOT G1E_BUNDLE OUT   reruns the unchanged G1-E baseline-identity tool against the G1-E reference and writes the report
    helper decide ROOT F0 F1 BUNDLE MANIFEST_SHA_FILE OUT BUILD_RECORD G1E_BUNDLE PYTHON_ARM_REPORT   exit 0 = outcome written, 2 = evidence invalid (no outcome)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from tests.cli_regression.support.g1e import assemble as g1e_assemble
from tests.cli_regression.support.g1e import bundle as g1e_bundle
from tests.cli_regression.support.g1e import freeze as fz
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import toolchain as tc
from tests.cli_regression.support.g3 import assemble, attribution, bundle as g3bundle, f0 as f0mod, f1 as f1mod, fixtures as fixtures_mod, hostclass, launch, packaged, pins, rows, smoke

packaged_mod_record_name = "packaged-build.json"      # beside sidecar-build.json (driver.PACKAGED_RECORD_NAME; the driver imports this module lazily, so the name is repeated here and tested equal)
PIN_PATH ="backend/tests/cli_regression/support/g3/g1e-pins.json"
G1E_BUNDLE = ".scratch/agent-cli-runtime-evaluation/evidence/e4/r14/bundle"


def _json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pin_manifest(root: Path) -> dict:
    return _json(Path(root) / PIN_PATH)


def _sidecar(arg: str) -> Path | None:
    return None if arg == "-" else Path(arg)


def verify(root: Path, f0_path: Path, sidecar: Path | None, f1_path: Path | None = None) -> list[str]:
    f0 = _json(f0_path)
    manifest = pin_manifest(root)
    if f1_path is None:
        return f0mod.verify_f0(root, f0, manifest, sidecar)
    return f1mod.verify_f1(root, _json(f1_path), f0, _sha(f0_path), manifest, sidecar)


def verifier(root: Path, f0_path: Path, sidecar: str, f1_path: Path | None = None):
    return lambda: verify(Path(root), Path(f0_path), _sidecar(sidecar), Path(f1_path) if f1_path else None)


def artifact_identity(f0: dict, sidecar_now_sha256: str, build_record: dict | None, last_code_commit_ts: int | None) -> str:
    """Section 1.1: `digest-mismatch` if the binary now is not the frozen one, or the build record for H does not name it; `predates-code` if the recorded build time is before the last commit that
    touches measured code; else `ok`."""
    frozen = f0["sidecar"]["sha256"]
    if sidecar_now_sha256 != frozen or not build_record or build_record.get("sha256") != frozen or build_record.get("head") != f0["head"]:
        return "digest-mismatch"
    if packaged.predates(build_record.get("builtAtEpoch"), last_code_commit_ts):
        return "predates-code"
    return "ok"


def final_identity(root: Path, f0: dict, sidecar_now_sha256: str, build_record: dict | None, last_code_commit_ts: int | None, packaged_record: dict | None) -> str:
    """The registered `artifactIdentity` of the final decision (section 1.1, review-4 finding 2): the sidecar AND the packaged backend and runtime, all rehashed NOW (after the last attempt), against the
    H build records and F0. A missing packaged record is a mismatch (the cost run cannot start without one)."""
    sidecar = artifact_identity(f0, sidecar_now_sha256, build_record, last_code_commit_ts)
    problems = packaged.artifact_problems(root, packaged_record, f0["sidecar"]["sha256"], f0["head"], last_code_commit_ts)
    return packaged.merge_identity(sidecar, packaged.identity_status(problems))


def copy_freeze(f0_path: Path, bundle: Path, f1_path: Path) -> None:
    """The G3 freeze records into the bundle: F0 as `freeze.json`, F1 as `freeze-f1.json`. The unchanged g1e `copy_freeze` also copies `<F0>.inputs.json`, a companion file only the g1e F0 contract has
    (review-1 finding 4); G3's F0 binds its inputs inside the record itself, so there is no companion file to copy."""
    shutil.copyfile(f0_path, bundle / "freeze.json")
    shutil.copyfile(f1_path, bundle / "freeze-f1.json")


MEASURED_CODE_PATHS = ["backend", "native", "tests/cli", "src/shared"]


def last_code_commit_ts(root: Path) -> int | None:
    """Committer time of the last commit touching measured code (section 1.1), or None when no commit does."""
    import subprocess
    commit = subprocess.run(["git", "log", "-1", "--format=%ct", "--", *MEASURED_CODE_PATHS], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    return int(commit) if commit else None


def packaged_verify(root: Path, sidecar: Path, record_path: Path) -> list[str]:
    """Problems of the packaged app on disk right now (rehash of the backend and the runtime, head, backend file time); empty = measurable."""
    head = fz.git_output(Path(root), "rev-parse", "HEAD").decode().strip()
    try:
        record = _json(record_path)
    except (OSError, ValueError):
        record = None
    return packaged.artifact_problems(root, record, _sha(sidecar), head, last_code_commit_ts(Path(root)))


def package_toolchain_problems(which=shutil.which) -> list[str]:
    """Pre-F0 only: bun, pnpm and node must resolve, because the repo's `build` scripts call pnpm and node under bun (review-3 finding 5)."""
    return [f"{tool} does not resolve on PATH" for tool in ("bun", "bunx", "pnpm", "node", "uv", "cargo") if which(tool) is None]


def launch_check(receipt_path: Path, *, mode: str, vendor: str, cell: int, repeat: int, freeze_sha256: str, sidecar_sha256: str, warmup: bool) -> list[str]:
    """The Python recomputation of one launch receipt's validity, called by the TS runner BEFORE it records the attempt (review-1 finding 2)."""
    return launch.receipt_problems(_json(receipt_path), mode=mode, vendor=vendor, cell=cell, repeat=repeat, freeze_sha256=freeze_sha256, sidecar_sha256=sidecar_sha256, warmup=warmup)


def reader_fixture_gate(f0_path: Path, descriptor: Path) -> bool:
    """False (-> exit 5, `reader-fixture-missing`) when F0 binds no descriptor, the step was given another descriptor than F0's, or the descriptor, a fixture or a golden is missing or changed."""
    record = _json(f0_path).get("readerFixtureDescriptor")
    return not fixtures_mod.f0_problems(record) and os.path.realpath(descriptor) == os.path.realpath(record["path"])


def assemble_bundle(root: Path, f0_path: Path, f1_path: Path, run_out: Path, bundle: Path) -> None:
    """Everything of the run into one bundle: F0 as freeze.json, F1, calibration trials, the matrix, the launch receipts, the reader record, run records and the host record."""
    run_out, bundle = Path(run_out), Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    copy_freeze(Path(f0_path), bundle, Path(f1_path))
    g1e_assemble.copy_matrix(run_out / "m" / "matrix", bundle)
    for source, name in ((run_out / "calibration" / "calibration" / "trials", "calibration"), (run_out / "m" / "launch-trials", "launch-trials"), (run_out / "calibration" / "launch-trials", "launch-calibration"),
                         (run_out / "m" / "packaged-trials", "packaged-trials")):
        if source.is_dir():
            shutil.copytree(source, bundle / name, dirs_exist_ok=True)
    reader = run_out / "bundle-reader"
    if (reader / "reader").is_dir():
        shutil.copytree(reader / "reader", bundle / "reader", dirs_exist_ok=True)
    if (reader / "reader.json").is_file():
        shutil.copyfile(reader / "reader.json", bundle / "reader.json")
    for name in ("run-g3.json", "calibration.json"):
        if (run_out / name).is_file():
            shutil.copyfile(run_out / name, bundle / name)
    g1e_assemble.write_host_record(bundle)


def record_build(root: Path, sidecar: Path, harness: Path, out: Path) -> dict:
    """`sidecar-build.json` {head, sha256, builtAt, builtAtEpoch, harnessSha256}: the release build of this session at H. The build time is the sidecar file's modification time (the second factor of
    the artifact identity; the binding one is the digest equality with F0's sidecar). The harness is a test-only binary and not a measured artifact."""
    built = Path(sidecar).stat().st_mtime
    record = {"head": fz.git_output(Path(root), "rev-parse", "HEAD").decode().strip(), "sha256": _sha(sidecar), "builtAt": datetime.fromtimestamp(built, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "builtAtEpoch": int(built), "harnessSha256": _sha(harness)}
    Path(out).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return record


def attempt_report(directory: Path) -> list[dict]:
    """Pass/fail view of the warm-up receipts under `directory` (G3-r2 real-artifact check): which per-attempt checks accepted. Only booleans, the arm and the stop reason are returned; no latency,
    CPU or memory value is read into the result."""
    out = []
    for path in sorted([*Path(directory).glob("**/trials/warmup-*.json"), *Path(directory).glob("**/trials/trial-*.json")]):
        receipt = _json(path)
        toolchain = receipt.get("toolchain") or {}
        ready = toolchain.get("readyVersion")
        proof = receipt.get("proof") or {}
        out.append({"file": path.name, "mode": receipt.get("selectedRuntime"), "passed": receipt.get("passed"), "evidenceValid": receipt.get("evidenceValid"), "stopReason": receipt.get("stopReason"),
                    "toolchainMatch": toolchain.get("match"), "readyVersionCheck": (ready or {}).get("match") if isinstance(ready, dict) else None, "runtimeProofOk": proof.get("ok"),
                    "cleanup": (receipt.get("phaseOutcomes") or {}).get("cleanup"),
                    "failure": str(receipt["failure"])[:600] if receipt.get("failure") else None,      # the check's own words (what was refused); a check message carries no measured value
                    "proofFailures": [str(text)[:300] for text in (proof.get("failures") or [])][:12]})
    return out


REHEARSAL_SEGMENTS =("g3r2", "rehearsal")      # a stamped directory must lie under .../g3r2/rehearsal* (the registered outputs are never stamped)
STAMP_MARKER = "NOT-EVIDENCE.json"


def rehearsal_stamp(directory: Path) -> dict:
    """G3-r2 amendment 2: marks everything a rehearsal wrote as not evidence. Every `*.json` file whose top level is an object gets `"notEvidence": true`; any other file (a JSON list, `*.jsonl`, logs) is
    listed as unstamped in `NOT-EVIDENCE.json` at the root, which is itself the marker of the whole directory. Refuses any directory not under g3r2/rehearsal*."""
    directory = Path(directory)
    parts = Path(os.path.realpath(directory)).parts
    if not any(parts[i] in (REHEARSAL_SEGMENTS[0], "g3r3") and parts[i + 1].startswith(REHEARSAL_SEGMENTS[1]) for i in range(len(parts) - 1)):
        raise ValueError(f"{directory} is not under g3r2/rehearsal: only a rehearsal's output is stamped")
    stamped, unstamped = 0, []
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink() and p.name != STAMP_MARKER):
        relative = str(path.relative_to(directory))
        if path.suffix != ".json":
            unstamped.append(relative)
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unstamped.append(relative)
            continue
        if not isinstance(document, dict):
            unstamped.append(relative)
            continue
        if document.get("notEvidence") is not True:
            document["notEvidence"] = True
            path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        stamped += 1
    marker = {"notEvidence": True, "rehearsal": True, "stamped": stamped, "unstamped": unstamped,
              "meaning": "G3-r2 rehearsal output. No value in this directory is timing, CPU or memory evidence and none was read or used."}
    (directory / STAMP_MARKER).write_text(json.dumps(marker, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def decide(root: Path, f0_path: Path, f1_path: Path, bundle_dir: Path, manifest_sha256: str, g1e_dir: Path, *, sidecar_now_sha256: str, build_record: dict | None,
           last_code_commit_ts: int | None, python_arm_report: dict, packaged_record: dict | None = None) -> dict:
    """The outcome document of a sealed bundle. Raises ValueError (no outcome) when the evidence cannot yield facts. `python_arm_attributable` is derived from `python_arm_report`
    (`attribution.attributable`), never passed in by hand."""
    f0, f1 = _json(f0_path), _json(f1_path)
    sealed = g1e_bundle.Bundle(Path(bundle_dir), manifest_sha256)
    matrix, shell = g3bundle.shell_matrix(sealed, f0, _sha(f1_path))
    launch_receipts, launch_invalid, launch_missing = assemble.launch_matrix_receipts(Path(bundle_dir), f0, _sha(f1_path))
    reader_record = sealed.read_json("reader.json")
    shell_run = sealed.read_json("run.json")
    identity = final_identity(Path(root), f0, sidecar_now_sha256, build_record, last_code_commit_ts, packaged_record)
    table, c1 = g3bundle.row_table(f1, shell, launch.pairs_by_row(launch_receipts), g3bundle.combined_rows(matrix), g3bundle.reader_rows(reader_record))
    flags, detail = g3bundle.matrix_flags(f0, matrix, shell_run, artifact_identity=identity, quiet_timed_out=False, reader_fixture_missing=False,
                                          launch_complete=not launch_invalid and not launch_missing)
    g1e = g3bundle.g1e_pairs(Path(g1e_dir), _sha(Path(g1e_dir) / "bundle-manifest.json"))
    source = {"head": f0["head"], "tree": f0["trackedDiffSha256"], "artifacts": [{"name": "navide-cli-runtime", "sha256": f0["sidecar"]["sha256"], "builtAt": (build_record or {}).get("builtAt", ""),
                                                                                  "predatesCode": packaged.predates((build_record or {}).get("builtAtEpoch"), last_code_commit_ts)},
                                                                                 *packaged.artifact_entries(packaged_record, build_record, last_code_commit_ts)], "artifactIdentity": identity}
    doc = g3bundle.build_document(f0=f0, f1=f1, table=table, c1=c1, flags=flags, source=source, g1e=g1e, python_arm_attributable=attribution.attributable(python_arm_report))
    # U7: the packaged cold-start row, descriptive only. It enters no row, status, fact or reason (the decider above never saw it).
    doc["descriptive"] = {"packagedColdStart": packaged.summary(Path(bundle_dir) / "packaged-trials", packaged.identity_problems(packaged_record, f0, last_code_commit_ts))}
    doc["evidenceDetail"] = {**detail, "launchInvalid": launch_invalid, "launchMissing": launch_missing, "pythonArmIdentity": {k: python_arm_report.get(k) for k in ("status", "referenceMatchesG1E", "unreviewed")}}
    return doc


def main(argv: list[str]) -> int:
    try:
        cmd, args = argv[0], argv[1:]
        if cmd == "constants" and not args:
            print(json.dumps({"fixtureRegistrySha256": ref.fixture_registry_digest(), "logPolicySha256": ref.log_policy_digest()}))
            return 0
        if cmd == "launch-calibration-plan" and len(args) == 1:
            print(json.dumps(launch.calibration_plan(args[0])))
            return 0
        if cmd == "f0-build" and len(args) == 3:
            root, out, spec_path = Path(args[0]), Path(args[1]), args[2]
            spec = _json(spec_path)
            f0 = f0mod.build_f0(root, branch=spec["branch"], sidecar=Path(spec["sidecar"]), seed=spec["seed"], approval=spec["approval"], pin_manifest=pin_manifest(root),
                                host=hostclass.observe_host(), reader_extra=spec.get("reader_extra"), reader_descriptor=Path(spec["reader_descriptor"]))
            raw = json.dumps(f0, indent=1, sort_keys=True).encode() + b"\n"
            out.write_bytes(raw)
            print(hashlib.sha256(raw).hexdigest())
            return 0
        if cmd == "freeze-verify" and len(args) in (3, 4):
            print(json.dumps({"problems": verify(Path(args[0]), Path(args[1]), _sidecar(args[2]), Path(args[3]) if len(args) == 4 else None)}))
            return 0
        if cmd == "checked-inputs" and len(args) == 1:
            print(json.dumps({"checkedInputs": f0mod.checked_input_count(_json(args[0]))}))
            return 0
        if cmd == "toolchain-check" and len(args) == 3:
            print(json.dumps(tc.check(Path(args[0]), _json(args[1])["toolchain"], None if args[2] == "-" else args[2])))
            return 0
        if cmd == "reader-calibrate" and len(args) == 6:
            root, f0_path, sidecar, fixtures, reader_root, bundle = args
            if not reader_fixture_gate(Path(f0_path), Path(fixtures)):
                return fixtures_mod.MISSING_EXIT
            return g1e_assemble.run_reader_calibration(Path(fixtures), Path(reader_root), Path(bundle), Path(root), Path(f0_path), sidecar, verify=verifier(Path(root), Path(f0_path), sidecar))
        if cmd == "reader-measure" and len(args) == 7:
            root, f0_path, f1_path, sidecar, fixtures, reader_root, bundle = args
            if not reader_fixture_gate(Path(f0_path), Path(fixtures)):
                return fixtures_mod.MISSING_EXIT
            return g1e_assemble.run_reader_measure(Path(fixtures), sidecar, Path(reader_root), Path(bundle), Path(root), Path(f0_path), Path(f1_path),
                                                   verify=verifier(Path(root), Path(f0_path), sidecar, Path(f1_path)))
        if cmd == "assemble-calibration" and len(args) == 5:
            root, f0_path, cal_dir, reader_dir, out = args
            f0 = _json(f0_path)
            record, detail = assemble.build_calibration(f0, shell_dir=Path(cal_dir) / "calibration", launch_dir=Path(cal_dir), reader_dir=Path(reader_dir), calibration_freeze_sha256=_sha(f0_path))
            Path(out).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            Path(out).with_suffix(".invalid.json").write_text(json.dumps(detail, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            return 0
        if cmd == "f1-build" and len(args) == 5:
            root, f0_path, calibration_path, sidecar, out = args
            f0 = _json(f0_path)
            f1 = f1mod.build_f1(Path(root), f0, _sha(f0_path), pin_manifest(Path(root)), _sidecar(sidecar), _json(calibration_path),
                                observe=lambda: tc.check(Path(root), f0["toolchain"], None if sidecar == "-" else sidecar))
            raw = json.dumps(f1, indent=1, sort_keys=True).encode() + b"\n"
            Path(out).write_bytes(raw)
            print(hashlib.sha256(raw).hexdigest())
            return 0
        if cmd == "assemble-bundle" and len(args) == 5:
            assemble_bundle(Path(args[0]), Path(args[1]), Path(args[2]), Path(args[3]), Path(args[4]))
            return 0
        if cmd == "record-build" and len(args) == 4:
            record_build(Path(args[0]), Path(args[1]), Path(args[2]), Path(args[3]))
            return 0
        if cmd == "launch-check" and len(args) == 8:
            path, mode, vendor, cell, repeat, freeze_sha, sidecar_sha, warmup = args
            print(json.dumps({"problems": launch_check(Path(path), mode=mode, vendor=vendor, cell=int(cell), repeat=int(repeat), freeze_sha256=freeze_sha, sidecar_sha256=sidecar_sha, warmup=warmup == "1")}))
            return 0
        if cmd == "isolated-env" and len(args) == 1:
            from tests.cli_regression.support.isolation import isolated_environment
            print(json.dumps(isolated_environment(Path(args[0]))))
            return 0
        if cmd == "reader-fixtures-build" and len(args) == 2:
            fixtures_mod.build(Path.cwd().parent, Path(args[0]), Path(args[1]))
            return 0
        if cmd == "packaged-stage" and len(args) == 2:
            packaged.stage_runtime(Path(args[0]), Path(args[1]))
            return 0
        if cmd == "packaged-verify" and len(args) == 3:
            problems = packaged_verify(Path(args[0]), Path(args[1]), Path(args[2]))
            print(json.dumps({"problems": problems}))
            return 0 if not problems else 2
        if cmd == "link-probe" and len(args) == 1:
            problems = packaged.link_probe(Path(args[0]))
            print(json.dumps({"problems": problems}))
            return 0 if not problems else 2
        if cmd == "package-toolchain" and not args:
            problems = package_toolchain_problems()
            print(json.dumps({"problems": problems}))
            return 0 if not problems else 2
        if cmd == "packaged-record" and len(args) == 3:
            packaged.record(Path(args[0]), fz.git_output(Path(args[0]), "rev-parse", "HEAD").decode().strip(), Path(args[1]), Path(args[2]))
            return 0
        if cmd == "smoke-verify" and len(args) == 2:
            return 0 if smoke.verify_dir(Path(args[0]), _sha(args[1]))["passed"] else 2
        if cmd == "python-arm-identity" and len(args) == 3:
            attribution.run(Path(args[0]), Path(args[1]), Path(args[2]))
            return 0
        if cmd == "decide" and len(args) == 9:
            import subprocess
            root, f0_path, f1_path, bundle, manifest_file, out, build_record_path, g1e_dir, arm_report_path = args
            f0 = _json(f0_path)
            commit = last_code_commit_ts(Path(root))
            doc = decide(Path(root), Path(f0_path), Path(f1_path), Path(bundle), Path(manifest_file).read_text().strip(), Path(g1e_dir), sidecar_now_sha256=_sha(f0["sidecar"]["path"]),
                         build_record=_json(build_record_path), last_code_commit_ts=commit, python_arm_report=_json(arm_report_path),
                         packaged_record=_json(Path(build_record_path).with_name(packaged_mod_record_name)) if Path(build_record_path).with_name(packaged_mod_record_name).is_file() else None)
            Path(out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"outcome": doc["outcome"], "reasons": doc["reasons"]}))
            return 0
        if cmd == "gen-shell-attempt" and len(args) == 1:
            from tests.cli_regression.support.g3 import attempt_copy
            attempt_copy.write(Path(args[0]))
            print(json.dumps({"problems": attempt_copy.check(Path(args[0]))}))
            return 0
        if cmd == "attempt-report" and len(args) == 1:
            print(json.dumps(attempt_report(Path(args[0])), indent=1))
            return 0
        if cmd == "rehearsal-stamp" and len(args) == 1:
            print(json.dumps({k: v for k, v in rehearsal_stamp(Path(args[0])).items() if k in ("stamped", "unstamped")}))
            return 0
        if cmd == "rehearsal-verdict" and len(args) == 3:
            from tests.cli_regression.support.g3 import rehearsal_verdict
            result = rehearsal_verdict.verdict(Path(args[2]), Path(args[0]), Path(args[1]))
            (Path(args[2]) / "rehearsal-verdict.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"passed": result["passed"], "defects": result["defects"], "reasons": [(r["reason"], r["classification"]) for r in result["reasons"]]}, indent=1))
            return 0 if result["passed"] else 2
        if cmd == "seal" and len(args) == 1:
            print(g1e_bundle.write_manifest(Path(args[0])))
            return 0
    except (f0mod.F0Error, f1mod.F1Error) as error:
        print("; ".join(error.problems), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, RuntimeError, g1e_bundle.BundleError) as error:
        print(f"{type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
