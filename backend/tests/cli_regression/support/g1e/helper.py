"""Command-line helpers the TypeScript runner calls (one JSON document on stdout, exit 0; any failure exits non-zero with a bounded message and no document).

    log-final ARM LOG BACKEND_PID SIDECAR_PID|- T_EXIT_MS     -> {"record": <log_final_check arguments>, "siblingExists": bool}
    log-identity LOG PEER_BYTES_FILE NONCE WID...             -> the `logIdentity` ledger record
    constants                                                 -> the in-force fixture-registry and log-policy digests
    freeze-verify ROOT FREEZE.json INPUTS.json SIDECAR|- [F1.json] [--dryrun]
                                                              -> {"problems": [...]}; with F1.json the matrix form (F1 well-formed, names FREEZE.json, F0 still verifies), else the F0 form;
                                                                 the approval is checked against the ledger now. `--dryrun` admits a dry-run F0 (its own schema id, no approval) and nothing else
    toolchain-check ROOT FREEZE.json SIDECAR|-                -> the V7 per-attempt record {recorded, observed, match, mismatches, uv}; the toolchain is compared with F0's
    sidecar-probe BINARY                                      -> {"readyVersion": ...} from one start-and-shutdown readiness probe (V7 item 1; not a measurement)
    freeze-build  ROOT OUT.json ARGS.json                     -> writes F0 (see `freeze.build_freeze`) and OUT.inputs.json; prints F0's SHA-256. ARGS: the F0 inputs plus `sidecar_path`,
                                                                 optional `ready_version` (else probed), optional `earlier_runs` [[dir, sha]] (the exception check runs first) and optional `carried_from`
                                                                 (the path of an older F0 json with its `.inputs.json` beside it: the verdicts that stand by byte equality are recorded, v14-r2 V15 item 3; absent -> `carriedVerdicts: null`).
                                                                 The pre-registration F0 binds is the E2 v14-r2 document (`freeze.DEFAULT_RULE_PATHS`, overridable by `rule_paths`), with v13, v10 and E1 v8b as bound texts.
                                                                 `user_approval` must be the user's whole dated approval line (`summarise.approval_problems`: ledger = the ledger file read, quote = one whole
                                                                 line `- DATE: USER APPROVED E2 v14-r2 ...` carrying the v14-r2 digest, date = the line's, 2026-10-21 .. today), checked before anything else;
                                                                 only `"dryrun": true` builds a dry-run F0 (own schema id, accepted nowhere else). F0 also freezes the locality scope (paths and bytes)
                                                                 and the quiet-gate script with its registered parameters (v14-r2 V14)
    freeze-f1-build ROOT F0.json OUT.json CALIBRATION.json SIDECAR|- [READER_MARGINS.json]
                                                              -> writes F1 after re-verifying every F0 item (deep) and the toolchain; prints F1's SHA-256
    exception-check DIR:SHA...                                -> the recorded V7 item 4 (i) check for the listed earlier runs
    baseline-identity-compose ROOT F0.json BUNDLE_DIR VERDICTS_A.json REPORT_A VERDICTS_B.json REPORT_B
                                                              -> writes BUNDLE_DIR/baseline-identity.json and BUNDLE_DIR/baseline/* from the two V5 reviewer verdict files (nothing is replaced);
                                                                 prints {"status", "changedFiles"}
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from tests.cli_regression.support.g1e import baseline_identity as bi
from tests.cli_regression.support.g1e import freeze as fz
from tests.cli_regression.support.g1e import log_final as lf
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e import toolchain as tc


def _earlier(spec: list) -> list[tuple[Path, str]]:
    return [(Path(directory), sha) for directory, sha in spec]


def main(argv: list[str]) -> int:
    try:
        command, args = argv[0], argv[1:]
        if command == "log-final" and len(args) == 5:
            arm, log, backend, sidecar, t_exit = args
            record = lf.log_final(arm, Path(log), backend_pid=int(backend), sidecar_pid=None if sidecar == "-" else int(sidecar), t_exit_ms=float(t_exit))
            print(json.dumps({"record": record, "siblingExists": lf.sibling_exists(Path(log))}))
            return 0
        if command == "log-identity" and len(args) >= 4:
            log, peer_file, nonce, *wids = args
            text = Path(log).read_bytes().decode("utf-8", "strict")
            peer = Path(peer_file).read_bytes().decode("utf-8", "strict")
            print(json.dumps(lf.log_identity(text, peer, nonce, wids)))
            return 0
        if command == "constants" and not args:
            from tests.cli_regression.support.g1e import reference as ref
            print(json.dumps({"fixtureRegistrySha256": ref.fixture_registry_digest(), "logPolicySha256": ref.log_policy_digest()}))
            return 0
        if command == "freeze-verify" and len([a for a in args if a != "--dryrun"]) in (4, 5):
            dryrun = "--dryrun" in args
            args = [a for a in args if a != "--dryrun"]
            root, freeze_path, inputs_path, sidecar = args[:4]
            f0 = json.loads(Path(freeze_path).read_text())
            inputs = json.loads(Path(inputs_path).read_text())
            sidecar_path = None if sidecar == "-" else Path(sidecar)
            if len(args) == 5:
                f1 = json.loads(Path(args[4]).read_text())
                problems = fz.verify_f1(Path(root), f1, f0, hashlib.sha256(Path(freeze_path).read_bytes()).hexdigest(), inputs, sidecar_path)
            else:
                problems = fz.verify_freeze(Path(root), f0, inputs, sidecar_path, dryrun=dryrun)
            print(json.dumps({"problems": problems}))
            return 0
        if command == "toolchain-check" and len(args) == 3:
            root, freeze_path, sidecar = args
            f0 = json.loads(Path(freeze_path).read_text())
            print(json.dumps(tc.check(Path(root), f0["toolchain"], None if sidecar == "-" else sidecar)))
            return 0
        if command == "sidecar-probe" and len(args) == 1:
            with tempfile.TemporaryDirectory(prefix="g1e-probe-") as scratch:
                print(json.dumps({"readyVersion": tc.probe_ready_version(args[0], Path(scratch))}))
            return 0
        if command == "freeze-build" and len(args) == 3:
            root, out, arguments = args
            spec = json.loads(Path(arguments).read_text())
            root_path = Path(root)
            dryrun = bool(spec.pop("dryrun", False))
            if not dryrun and (problems := fz.approval_check(root_path, spec.get("user_approval"))):   # up front: no probe, no trace, no file
                print("approval: " + "; ".join(problems), file=sys.stderr)
                return 2
            sidecar_path = spec.pop("sidecar_path")
            ready_version = spec.pop("ready_version", None)
            earlier = _earlier(spec.pop("earlier_runs", []))
            if earlier and (problems := fz.earlier_run_problems(earlier)):
                print("\n".join(problems), file=sys.stderr)
                return 2
            if ready_version is None:
                with tempfile.TemporaryDirectory(prefix="g1e-probe-") as scratch:
                    ready_version = tc.probe_ready_version(sidecar_path, Path(scratch))
            toolchain, uv = tc.record_for_freeze(root_path, sidecar_path, ready_version)
            python_arm = fz.build_python_arm(root_path, bi.trace_python_arm(root_path, sys.executable))
            inputs = fz.build_inputs(root_path)
            freeze = fz.build_freeze(root_path, inputs=inputs, python_arm=python_arm, toolchain=toolchain, uv=uv, earlier_runs=earlier, dryrun=dryrun, **spec)
            raw = json.dumps(freeze, indent=1, sort_keys=True).encode() + b"\n"
            Path(out).write_bytes(raw)
            Path(out).with_suffix(".inputs.json").write_text(json.dumps(inputs, indent=1) + "\n")
            if earlier:
                Path(out).with_suffix(".exception-check.json").write_text(json.dumps(fz.exception_record(earlier), indent=1, sort_keys=True) + "\n")
            print(hashlib.sha256(raw).hexdigest())
            return 0
        if command == "freeze-f1-build" and len(args) in (5, 6):
            root, f0_path, out, calibration_path, sidecar = args[:5]
            f0_raw = Path(f0_path).read_bytes()
            f0 = json.loads(f0_raw)
            inputs = json.loads(Path(f0_path).with_suffix(".inputs.json").read_text())
            calibration = json.loads(Path(calibration_path).read_text())
            margins = json.loads(Path(args[5]).read_text())["margins"] if len(args) == 6 else None
            sidecar_path = None if sidecar == "-" else Path(sidecar)
            f1 = fz.build_f1(Path(root), f0, hashlib.sha256(f0_raw).hexdigest(), inputs, sidecar_path, calibration, margins, retrace=lambda: bi.trace_python_arm(Path(root), sys.executable),
                             observe=lambda: tc.check(Path(root), f0["toolchain"], sidecar_path))
            raw = json.dumps(f1, indent=1, sort_keys=True).encode() + b"\n"
            Path(out).write_bytes(raw)
            print(hashlib.sha256(raw).hexdigest())
            return 0
        if command == "baseline-identity-compose" and len(args) == 7:
            root, f0_path, bundle_dir, verdicts_a, report_a, verdicts_b, report_b = args
            record = bi.compose_into(Path(root), Path(f0_path), Path(bundle_dir), [(Path(verdicts_a), Path(report_a)), (Path(verdicts_b), Path(report_b))])
            print(json.dumps({"status": record["status"], "changedFiles": [c["path"] for c in record["changedFiles"]]}))
            return 0
        if command == "exception-check" and args:
            print(json.dumps(fz.exception_record(_earlier([a.rsplit(":", 1) for a in args])), indent=1, sort_keys=True))
            return 0
    except fz.ToolchainMismatch as error:
        print(f"{tc.STOP_REASON}: {str(error)[:300]}", file=sys.stderr)
        return 3
    except fz.FreezeError as error:
        print(f"FreezeError: {str(error)[:600]}", file=sys.stderr)
        return 2
    except (lf.InspectionError, OSError, ValueError, KeyError, UnicodeDecodeError, RuntimeError) as error:
        print(f"{type(error).__name__}: {str(error)[:200]}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
