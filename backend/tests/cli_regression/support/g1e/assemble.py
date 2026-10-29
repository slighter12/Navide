"""Assembly of the bundle records that come from runs rather than from the decider (E4 assignment 2): `calibration.json`, `reader.json`, the gate record and the sealed bundle.

    python -m tests.cli_regression.support.g1e.assemble calibration CALIBRATION_DIR BUNDLE_DIR    CALIBRATION_DIR holds `trials/trial-python-<cell>-<k>.json` (+ `-resources.jsonl`)
    python -m tests.cli_regression.support.g1e.assemble matrix MATRIX_DIR BUNDLE_DIR              copies the 40 trial receipts and their resource files into the bundle
    python -m tests.cli_regression.support.g1e.assemble reader-calibrate FIXTURES_JSON ROOT BUNDLE F0.json SIDECAR REPO
                                                                      >= 10 Python-only reader runs per fixture (fresh processes) -> reader/calibration-*.json; F0 is verified and then the
                                                                      toolchain is checked against F0 before EVERY run (V7 item 2): a difference invalidates that run and stops it
                                                                      (exit 4 and stop reason freeze-mismatch; exit 3 and toolchain-mismatch)
    python -m tests.cli_regression.support.g1e.assemble reader-measure FIXTURES_JSON SIDECAR ROOT BUNDLE F0.json F1.json REPO
                                                                      10 paired runs per fixture, both readers (fresh processes) -> reader.json; F1 and F0 are verified and then the toolchain
                                                                      is checked before every run (stop reasons freeze-mismatch / toolchain-mismatch), and the console shows ONLY per-run
                                                                      validity and the stop reason (blindness, V7 item 6); refused when ACR_G1E_KEEP=1
    python -m tests.cli_regression.support.g1e.assemble locality REPO BUNDLE F0.json                 runs the frozen locality check with the reference built from F0's referenceManifest and writes
                                                                      locality.json (+ locality/check.py, scope.txt, allowlist.json); the record states F0's manifest digest
    python -m tests.cli_regression.support.g1e.assemble freeze F0.json BUNDLE [F1.json]              copies F0 (freeze.json), its frozen-input listing (freeze-inputs.json) and F1 into the bundle
    python -m tests.cli_regression.support.g1e.assemble host-record BUNDLE                         writes host-record.json (v14-r2 V19 item 5: the host numbers of every receipt and reader run; information only)
    python -m tests.cli_regression.support.g1e.assemble seal BUNDLE                                writes bundle-manifest.json and prints its SHA-256 (the pinned digest)

Nothing here judges evidence: the calibration step reads the same per-trial scalars the decider uses (`summarise.trial_scalars`) from the Python-only receipts that passed the same recomputed
validity (`summarise.trial_problems`), and lists the rows `calibration.rows_calibration` finds noisy or uncalibrated (the chains in slot order); the decider re-derives everything it consumes
from the bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import calibration, reference as ref, summarise
from tests.cli_regression.support.g1e import freeze as fz
from tests.cli_regression.support.g1e import toolchain as tc
from tests.cli_regression.support.g1e.bundle import write_manifest
from tests.cli_regression.support.g1e.runlog import RunLog


def hash_ref(root: Path, name: str) -> dict:
    return {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}


def load_trial(path: Path) -> dict:
    trial = json.loads(path.read_text(encoding="utf-8"))
    resources = path.with_name(path.name[: -len(".json")] + "-resources.jsonl")
    trial["_samples"] = [json.loads(line) for line in resources.read_text().splitlines() if line.strip()]
    return trial


def _copy_receipt(path: Path, target: Path) -> None:
    """A receipt and its resources file go into the bundle together."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    shutil.copyfile(path.with_name(path.name[: -len(".json")] + "-resources.jsonl"), target.with_name(target.name[: -len(".json")] + "-resources.jsonl"))


def calibration_record(calibration_dir: Path, bundle: Path, freeze: dict) -> dict:
    """Copies the calibration receipts into `bundle/calibration/` (the discarded warm-up `warmup-python-00` first) and writes `calibration.json`: {receipts, noisyRows, uncalibratedRows}. The rows
    are computed over each cell's chain IN SLOT ORDER (the `slot` each receipt carries); a calibration attempt that is not valid is listed, never silently dropped, and the warm-up enters no list."""
    chains: dict[int, list] = {cell: [None] * calibration.CHAIN_LENGTH for cell in ref.CELLS}
    receipts, invalid = [], []
    for path in sorted((calibration_dir / "trials").glob("warmup-python-*.json")):
        _copy_receipt(path, bundle / "calibration" / path.name)
        receipts.append(hash_ref(bundle, f"calibration/{path.name}"))
    for path in sorted((calibration_dir / "trials").glob("trial-python-*-*.json")):
        trial = load_trial(path)
        problems = summarise.trial_problems({k: v for k, v in trial.items() if k != "_samples"}, freeze, "python", trial["count"])
        _copy_receipt(path, bundle / "calibration" / path.name)
        receipts.append(hash_ref(bundle, f"calibration/{path.name}"))
        if problems:
            invalid.append({"file": path.name, "problems": problems[:5]})
        elif summarise.is_int(trial.get("slot")) and trial["slot"] < calibration.CHAIN_LENGTH and trial["count"] in chains:
            chains[trial["count"]][trial["slot"]] = summarise.trial_scalars(trial)
    report = calibration.rows_calibration(chains)
    copy_run_record(calibration_dir, bundle)
    record = {"receipts": receipts, "noisyRows": report["noisyRows"], "uncalibratedRows": report["uncalibratedRows"]}
    (bundle / "calibration.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    (bundle / "calibration-report.json").write_text(json.dumps({"rows": report["rows"], "invalidAttempts": invalid}, indent=1, sort_keys=True) + "\n")
    return record


def copy_matrix(matrix_dir: Path, bundle: Path) -> int:
    """The trial receipts go to `bundle/trials/`; the two discarded matrix warm-ups (`trials/warmup-*` in the run directory) go to `bundle/warmup/`, where they are never read as trials."""
    count = 0
    for path in sorted((matrix_dir / "trials").glob("trial-*")):
        target = bundle / "trials" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        count += 1
    for path in sorted((matrix_dir / "trials").glob("warmup-*")):
        target = bundle / "warmup" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    copy_run_record(matrix_dir, bundle)
    return count


HOST_RECORD_SCHEMA = "acr-g1e-host-record/1"
HOST_NUMBERS = ("at", "loadavg", "ncpu", "processCount")
HOST_FOLDERS = ("calibration", "warmup", "trials")


def host_snapshot() -> dict:
    """One host sample (numbers only): the clock, the load averages, the CPU count and the number of processes (`ps -A -o pid=`, a line count: no process name and no argument is read).
    A reader run takes it just before and just after the run, never inside it."""
    import subprocess
    import time
    try:
        count = len(subprocess.run(["ps", "-A", "-o", "pid="], capture_output=True, text=True, timeout=10, check=True).stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        count = None
    return {"at": time.time() * 1000.0, "loadavg": list(os.getloadavg()), "ncpu": os.cpu_count(), "processCount": count}


def host_record(bundle: Path) -> dict:
    """The host record (v14-r2 V19 item 5): a table of the numbers every attempt receipt carries (`ambientBefore`, `ambientAfter`) plus the reader runs' snapshots, so a reader of the evidence can see
    the machine's state. INFORMATION ONLY: nothing in the decider reads this file and no outcome, status or classification depends on a number in it."""
    attempts = []
    for folder in HOST_FOLDERS:
        for path in sorted((bundle / folder).glob("*.json")):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                continue
            pick = lambda point: {k: receipt[point][k] for k in HOST_NUMBERS if isinstance(receipt.get(point), dict) and k in receipt[point]}
            attempts.append({"name": f"{folder}/{path.stem}", "kind": receipt.get("kind"), "warmup": bool(receipt.get("warmup")), "before": pick("ambientBefore"), "after": pick("ambientAfter")})
    reader = [{"source": f"reader/{path.name}", "runs": json.loads(path.read_text(encoding="utf-8"))} for path in sorted((bundle / "reader").glob("host-*.json"))]
    return {"schema": HOST_RECORD_SCHEMA, "attempts": sorted(attempts, key=lambda a: a["name"]), "reader": reader}


def write_host_record(bundle: Path) -> dict:
    record = host_record(Path(bundle))
    (Path(bundle) / "host-record.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def copy_freeze(f0_path: Path, bundle: Path, f1_path: Path | None = None) -> None:
    """The freeze records of this run go into its bundle: F0 as `freeze.json`, the frozen-input listing F0's `inputsSha256` hashes as `freeze-inputs.json` (the decider binds the locality re-run
    to it), and F1 (if any) as `freeze-f1.json`."""
    shutil.copyfile(f0_path, bundle / "freeze.json")
    shutil.copyfile(Path(f0_path).with_suffix(".inputs.json"), bundle / "freeze-inputs.json")
    if f1_path is not None:
        shutil.copyfile(f1_path, bundle / "freeze-f1.json")


def copy_run_record(source: Path, bundle: Path) -> None:
    """The runner's `run.json` (validity and stop reason only) goes into the bundle of THIS run; one bundle per run."""
    if (source / "run.json").is_file():
        shutil.copyfile(source / "run.json", bundle / "run.json")


def _copy_ref(src: Path, bundle: Path, name: str) -> dict:
    target = bundle / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return hash_ref(bundle, name)


def make_verifier(repo: Path, f0_path: Path, sidecar: str | None, f1_path: Path | None = None) -> Callable[[], list[str]]:
    """The freeze check a reader run makes before it starts, as the TS matrix runner does before an attempt: F0 for the reader calibration (`verify_freeze`), F1 and the F0 it names for the
    reader measure (`verify_f1`). Returns the problems (empty = the run may start); the files are re-read every call, so a change between two runs is seen."""
    def verify() -> list[str]:
        f0_raw = Path(f0_path).read_bytes()
        f0 = json.loads(f0_raw)
        inputs = json.loads(Path(f0_path).with_suffix(".inputs.json").read_text())
        sidecar_path = None if sidecar in (None, "-") else Path(sidecar)
        if f1_path is None:
            return fz.verify_freeze(Path(repo), f0, inputs, sidecar_path)
        return fz.verify_f1(Path(repo), json.loads(Path(f1_path).read_text()), f0, hashlib.sha256(f0_raw).hexdigest(), inputs, sidecar_path)
    return verify


def execute_checked(kind: str, plan: list, toolchain: dict, repo: Path, sidecar: str | None, *, verify: Callable[[], list[str]], check=tc.check, host: Callable[[], dict] | None = None,
                    hosts: list | None = None) -> tuple[list, list[dict], RunLog]:
    """Runs `plan` = [(name, thunk)] in order. BEFORE every run the freeze is verified (`verify`: F0, or F1 and F0; V10 item 1) and then the toolchain is checked against F0 (V7 item 2) and the
    check record is kept. A freeze difference invalidates that run (stop reason `freeze-mismatch`), a toolchain mismatch too (`toolchain-mismatch`); in both the thunk is NOT run and the whole run
    stops. A thunk that raises invalidates its run (`attempt-invalid`). The failure text is never kept or printed (blindness).
    With `host` (a snapshot function) and `hosts` (the list to fill) a snapshot is taken just before and just after each run, outside it (v14-r2 V19 item 5: information only)."""
    log, results, checks = RunLog(kind), [], []
    for name, thunk in plan:
        if verify():
            log.record(name, "invalid", "freeze-mismatch")
            break
        record = check(repo, toolchain, sidecar)
        checks.append(record)
        if not record["match"]:
            log.record(name, "invalid", tc.STOP_REASON)
            break
        before = host() if host is not None else None
        try:
            results.append((name, thunk()))
        except Exception:                                                        # noqa: BLE001  the detail would be a candidate result
            log.record(name, "invalid", "attempt-invalid")
            break
        if host is not None and hosts is not None:
            hosts.append({"name": name, "before": before, "after": host()})
        log.record(name, "clean")
    return results, checks, log


def write_run_files(bundle: Path, log: RunLog, checks: list[dict], checks_name: str, expected: int, hosts: list | None = None, hosts_name: str | None = None) -> None:
    """`run.json` (validity and stop reason only) and `reader/<checks_name>` (one toolchain-check record per run, written before the run it guards: identities and the match result); with `hosts`
    also `reader/<hosts_name>` (the host snapshots around each run: numbers only)."""
    (bundle / "reader").mkdir(parents=True, exist_ok=True)
    (bundle / "run.json").write_text(json.dumps(log.to_record(expected), indent=1, sort_keys=True) + "\n")
    (bundle / "reader" / checks_name).write_text(json.dumps(checks, indent=1, sort_keys=True) + "\n")
    if hosts is not None and hosts_name is not None:
        (bundle / "reader" / hosts_name).write_text(json.dumps(hosts, indent=1, sort_keys=True) + "\n")


def _stop_code(log: RunLog) -> int:
    return 3 if log.stopped == tc.STOP_REASON else 4


def run_reader_calibration(fixtures_json: Path, root: Path, bundle: Path, repo: Path, f0_path: Path, sidecar: str, *, run_one=None, check=tc.check,
                           verify: Callable[[], list[str]] | None = None, host: Callable[[], dict] | None = None) -> int:
    """>= 10 Python-only reader runs per fixture, each in a fresh process. Before EVERY run: F0 is verified, then the toolchain (exit 4 for a freeze difference or a failed run, 3 for a toolchain stop)."""
    from tests.cli_regression.support import reader_benchmark as rb
    run_one = run_one or rb.run_in_fresh_process
    descriptors = json.loads(Path(fixtures_json).read_text())
    toolchain = json.loads(Path(f0_path).read_text())["toolchain"]
    plan = [(f"{fixture}:{k}", lambda fixture=fixture: run_one("python", fixture, fixtures_json, Path("/nonexistent"), root))
            for fixture in rb.FIXTURES for k in range(calibration.READER_MIN_RUNS)]
    hosts: list = []
    results, checks, log = execute_checked("reader-calibration", plan, toolchain, repo, sidecar, verify=verify or make_verifier(repo, f0_path, sidecar), check=check, host=host or host_snapshot, hosts=hosts)
    write_run_files(bundle, log, checks, "toolchain-calibration.json", len(plan), hosts, "host-calibration.json")
    if log.stopped:
        print("\n".join(log.lines()))
        return _stop_code(log)
    runs = {fixture: [result for name, result in results if name.startswith(fixture + ":")] for fixture in rb.FIXTURES}
    print(json.dumps(reader_calibration_record(descriptors, runs, bundle)))
    return 0


def run_reader_measure(fixtures_json: Path, sidecar: str, root: Path, bundle: Path, repo: Path, f0_path: Path, f1_path: Path, *, run_one=None, check=tc.check,
                       verify: Callable[[], list[str]] | None = None, host: Callable[[], dict] | None = None) -> int:
    """10 paired runs per fixture, both readers, each in a fresh process. Before EVERY run: F1 and the F0 it names are verified, then the toolchain. The console shows ONLY per-run validity and
    the stop reason (blindness, V7 item 6)."""
    from tests.cli_regression.support import reader_benchmark as rb
    run_one = run_one or rb.run_in_fresh_process
    descriptors = json.loads(Path(fixtures_json).read_text())
    margins = json.loads((bundle / "reader" / "calibration-margins.json").read_text())["margins"]
    toolchain = json.loads(Path(f0_path).read_text())["toolchain"]
    runs = calibration.READER_MIN_RUNS
    plan = [(f"{fixture}:{index}:{reader}", lambda reader=reader, fixture=fixture: run_one(reader, fixture, fixtures_json, Path(sidecar), root))
            for fixture in rb.FIXTURES for index in range(runs) for reader in rb.order_for(index)]
    hosts: list = []
    results, checks, log = execute_checked("reader-measure", plan, toolchain, repo, sidecar, verify=verify or make_verifier(repo, f0_path, sidecar, f1_path), check=check, host=host or host_snapshot, hosts=hosts)
    write_run_files(bundle, log, checks, "toolchain-measure.json", len(plan), hosts, "host-measure.json")
    print("\n".join(log.lines()))
    if log.stopped:
        return _stop_code(log)
    by_name = dict(results)
    pairs = {fixture: [{"run": index, "order": rb.order_for(index), **{reader: by_name[f"{fixture}:{index}:{reader}"] for reader in rb.order_for(index)}} for index in range(runs)] for fixture in rb.FIXTURES}
    print(json.dumps({"runs": reader_measure_record(descriptors, pairs, margins, runs, bundle)["runs"]}))
    return 0


def reader_calibration_record(descriptors: dict, runs: dict[str, list[dict]], bundle: Path) -> dict:
    """Freezes the absolute reader margins from the Python-only runs (`calibration.reader_margins`; fewer than 10 runs or any `m0` false is an error) and stores the raw runs."""
    margins = calibration.reader_margins(runs)
    out = bundle / "reader"
    out.mkdir(parents=True, exist_ok=True)
    for fixture, records in runs.items():
        (out / f"calibration-runs-{fixture}.json").write_text(json.dumps(records, indent=1, sort_keys=True) + "\n")
    (out / "calibration-margins.json").write_text(json.dumps({"margins": margins, "runs": {fixture: len(records) for fixture, records in runs.items()},
                                                               "fixtures": {fixture: descriptors[fixture]["sha256"] for fixture in runs}}, indent=1, sort_keys=True) + "\n")
    return margins


def reader_measure_record(descriptors: dict, pairs: dict[str, list[dict]], margins: dict, calibration_runs: int, bundle: Path) -> dict:
    """`reader.json` from the paired runs: the hash-pinned fixtures and the golden lists are copied into the bundle and referenced; counts and statuses are the decider's to derive."""
    fixtures = [_copy_ref(Path(descriptors[name]["path"]), bundle, f"reader/fixture-{name}.jsonl") for name in ("claude", "opencode")]
    golden = {name: json.loads(Path(descriptors[name]["golden"]).read_text()) for name in ("claude", "opencode")}
    (bundle / "reader").mkdir(parents=True, exist_ok=True)
    (bundle / "reader" / "golden.json").write_text(json.dumps(golden, separators=(",", ":")))
    golden_ref = hash_ref(bundle, "reader/golden.json")
    for fixture, records in pairs.items():
        (bundle / "reader" / f"runs-{fixture}.json").write_text(json.dumps(records, indent=1, sort_keys=True) + "\n")
    record = calibration.reader_record(fixtures, golden_ref, hash_ref(bundle, "reader/calibration-margins.json"), margins, calibration_runs, pairs)
    (bundle / "reader.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def locality_record(repo: Path, bundle: Path, freeze: dict, allowlist: Path | None = None) -> dict:
    """`locality.json`: the frozen check's verdict on the tree, with the script, the scope list and the allowlist as hash-referenced files. The reference content of every Python-scope file is
    built mechanically from F0's `referenceManifest` (V6 (a)) and the record states the manifest digest it was made with; the decider re-runs the check and compares."""
    from tests.cli_regression.support.g1e import locality_check as lc
    arm = freeze["pythonArm"]
    allowlist_path = Path(allowlist) if allowlist else Path(lc.__file__).with_name("locality-allowlist.json")      # the real allowlist next to the frozen script; a scratch tree of a test names its own
    allowlist, _ = lc.load_allowlist(allowlist_path)
    result = lc.check(repo, allowlist, lc.reference_texts(Path(repo), arm["referenceManifest"]))
    (bundle / "locality").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lc.__file__, bundle / "locality" / "check.py")
    shutil.copyfile(allowlist_path, bundle / "locality" / "allowlist.json")
    (bundle / "locality" / "scope.txt").write_text(lc.scope_text(lc.scope_record(Path(repo))))
    findings = lc.findings_lines(result)
    record = {"violation": bool(findings), "checkScript": hash_ref(bundle, "locality/check.py"), "scopeList": hash_ref(bundle, "locality/scope.txt"),
              "allowlist": hash_ref(bundle, "locality/allowlist.json"), "findings": findings, "referenceManifestSha256": arm["referenceManifestSha256"]}
    (bundle / "locality.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "calibration":
        freeze = json.loads(Path(argv[2], "freeze.json").read_text())
        print(json.dumps(calibration_record(Path(argv[1]), Path(argv[2]), freeze)))
        return 0
    if len(argv) == 3 and argv[0] == "matrix":
        print(copy_matrix(Path(argv[1]), Path(argv[2])))
        return 0
    if len(argv) == 7 and argv[0] == "reader-calibrate":
        return run_reader_calibration(Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[6]), Path(argv[4]), argv[5])
    if len(argv) == 8 and argv[0] == "reader-measure":
        if os.environ.get("ACR_G1E_KEEP") == "1":
            print("ACR_G1E_KEEP is refused for the reader-measure run (a kept root is measured data outside the sealed receipts)", file=sys.stderr)
            return 64
        return run_reader_measure(Path(argv[1]), argv[2], Path(argv[3]), Path(argv[4]), Path(argv[7]), Path(argv[5]), Path(argv[6]))
    if len(argv) == 4 and argv[0] == "locality":
        record = locality_record(Path(argv[1]), Path(argv[2]), json.loads(Path(argv[3]).read_text()))
        print(json.dumps({"violation": record["violation"], "findings": len(record["findings"])}))
        return 0
    if len(argv) in (3, 4) and argv[0] == "freeze":
        copy_freeze(Path(argv[1]), Path(argv[2]), Path(argv[3]) if len(argv) == 4 else None)
        return 0
    if len(argv) == 2 and argv[0] == "host-record":
        write_host_record(Path(argv[1]))
        return 0
    if len(argv) == 2 and argv[0] == "seal":
        print(write_manifest(Path(argv[1])))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
