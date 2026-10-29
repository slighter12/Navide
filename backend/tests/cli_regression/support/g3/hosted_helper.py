"""Command line of the hosted G3 run (the same sub-commands as `g3.helper`, plus `hosted-inputs-check`). Thin: it resolves every path argument to an absolute path, installs the hosted seams and hands the
command to the unchanged macOS helper; only the commands whose macOS body is platform-bound are replaced here.

    hosted_helper f0-build ROOT OUT SPEC_JSON     like the macOS command; the host class is the runner's, the F0 carries a `hostedRunner` record, documents/approval come from the hosted inputs
    hosted_helper hosted-inputs-check ROOT        {"problems": [...]} of the documents manifest and the approval excerpt (exit 0 = fine, 2 = problems)
    hosted_helper pins-verify ROOT                {"problems": [...]} of `pins.verify_pins` (exit 0 = every g1e file has its pinned bytes)
    hosted_helper f0-spec ROOT PRE_OUT OUT_JSON BRANCH   writes the `f0-build` spec: seed from packaged-build.json, the G3 approval line of the excerpt, the platform's sidecar
    hosted_helper hosted-report OUTCOME_JSON      prints `<id>  <status>[  observer=python]` per row (the observer annotation exists on Windows agent-launch rows only)
    hosted_helper decide ROOT F0 F1 BUNDLE MANIFEST_SHA_FILE OUT BUILD_RECORD [G1E_BUNDLE PYTHON_ARM_REPORT]   exit 0 = outcome written, 2 = evidence invalid; every G1-E comparison is `not-same-class`.
        With the last two arguments the Python arm's attribution is derived like the macOS run does (`helper.python_arm_attribution`, the registered digest in $ACR_G3_G1E_MANIFEST_SHA256); without them
        it is non-attributable with the reason "no G1-E bundle was given". Nothing raises for a missing, wrong or malformed digest or bundle.
    hosted_helper python-arm-identity ROOT G1E_BUNDLE OUT   delegated unchanged (the bundle must hash to $ACR_G3_G1E_MANIFEST_SHA256)
    hosted_helper python-arm-identity-f0 ROOT OUT   the §4.8 Python-arm identity record WITHOUT a bundle (rf4d E2): traces the runner's Python arm and rebuilds the frozen G1-E F0's `pythonArm` record
        (`g1e.freeze.build_python_arm`) from the pinned U and E3 revisions, compares it with the frozen digests, writes OUT. The V5 outcome stays "disclosed, not passed"; nothing is re-judged and no verdict is made.
    hosted_helper <any other g3.helper command> ...   delegated unchanged (see `g3/helper.py`)

Every path argument becomes absolute at entry, BEFORE any precondition runs: the macOS `pre_run_problems` once resolved F0 against the driver's cwd while the steps resolved it against theirs (the void
start of G3-r3). `-` (no sidecar) is kept. Launched as `python -B -m tests.cli_regression.support.g3.hosted_helper` or through `hosted_run_helper.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import hosted_posix_stubs

hosted_posix_stubs.install()      # before the imports below: this entry process is not under the hosted startup, and `g3.f0` -> `g1e.quiet_gate` reads `os.getloadavg` at import (Windows lacks it)
from tests.cli_regression.support.g1e import baseline_identity as bi, bundle as g1e_bundle, freeze as fz  # noqa: E402
from tests.cli_regression.support.g3 import assemble, attribution, bundle as g3bundle, f0 as f0mod, f1 as f1mod, helper, hosted_f0, hosted_hostclass, hosted_packaged, hosted_reader, hosted_rows, hosted_seams, launch, packaged, pins, regression  # noqa: E402

ALL = "all"
# command -> argument positions that are paths ("all" = every argument). Arguments not listed (modes, digests, counts) are left as they are.
PATH_ARGS: dict[str, object] = {
    "f0-build": ALL, "freeze-verify": ALL, "checked-inputs": ALL, "toolchain-check": ALL, "reader-calibrate": ALL, "reader-measure": ALL, "assemble-calibration": ALL, "f1-build": ALL,
    "assemble-bundle": ALL, "record-build": ALL, "launch-check": (0,), "isolated-env": ALL, "reader-fixtures-build": ALL, "packaged-stage": ALL, "packaged-verify": ALL, "link-probe": ALL,
    "packaged-record": ALL, "smoke-verify": ALL, "decide": ALL, "gen-shell-attempt": ALL, "attempt-report": ALL, "rehearsal-stamp": ALL, "rehearsal-verdict": ALL, "seal": ALL,
    "hosted-inputs-check": ALL, "pins-verify": ALL, "f0-spec": (0, 1, 2), "hosted-report": ALL, "python-arm-identity": ALL, "g1e-register": ALL, "g1e-host-class": ALL, "python-arm-identity-f0": ALL,
}
NO_PATH = "-"
PYTHON_ARM_IDENTITY_SCHEMA = "acr-g3h-python-arm-identity/1"
V5_DISCLOSURE = "evaluation-branch Python arm (V5 disclosed, not passed)"      # the outcome the frozen G1-E F0 carries (progress.md 2026-11-09 rf3 entry: both reviewers judged the 54 listed files; not re-judged here)
FROZEN_PYTHON_ARM = {      # `pythonArm` of the frozen G1-E F0 (evidence/e4/r7/f0.json, rf3 rebuild): U, the E3 parent and commit, and the digests of the traced file list and the reference manifest
    "upstreamMerge": "3720a3b150166870ef8d962ab9427d383e2a6a1d", "e3Parent": "09bf4d8666212c84abab5deda6780541e3312f2c", "e3Commit": "840b9d930c433e1a7443ebb84ef3b06d465f0476",
    "fileListSha256": "5dce73045db091ea49c9f4ef6757dbb9e6bbbf3e3c038606f9f7bb1375a7e839", "referenceManifestSha256": "84fd7c96b80b280a2fcb1f8d813518996b25ae7b8e1248c32eaa182198647e59", "tracedCount": 277, "e3SetCount": 5,
}


def python_arm_identity_f0(root: Path, python: str, *, trace: Callable[..., list[str]] = bi.trace_python_arm, build: Callable[..., dict] = fz.build_python_arm) -> dict:
    """The §4.8 identity record of the runner's Python arm without a G1-E bundle (rf4d E2). `trace` lists the in-repo modules the arm loads (paths are normalised to `/` so a Windows list is comparable);
    `build` is the frozen G1-E F0's own record builder, which uses the pinned U and E3 constants, so nothing here depends on a bundle. The record states the digests it found, the frozen ones and whether each
    is equal. A difference (another traced list on another platform, a changed file) is reported, never judged: the V5 outcome is the frozen F0's `disclosed_not_passed`, and the Python arm stays non-attributable
    (`decide` has no bundle). Nothing here writes a verdict."""
    files = sorted(path.replace("\\", "/") for path in trace(Path(root), python))
    arm = build(Path(root), files)
    found = {"upstreamMerge": arm["upstreamMerge"], "e3Parent": arm["e3Parent"], "e3Commit": arm["e3Commit"], "e3SetCount": len(arm["e3Set"]), "tracedCount": len(arm["fileList"]),
             "fileListSha256": arm["fileListSha256"], "referenceManifestSha256": arm["referenceManifestSha256"]}
    return {"schema": PYTHON_ARM_IDENTITY_SCHEMA, "outcome": V5_DISCLOSURE, "basis": "rebuilt on this runner with g1e.freeze.build_python_arm from the pinned U and E3 revisions; no G1-E bundle", "found": found,
            "frozen": dict(FROZEN_PYTHON_ARM), "equalsFrozen": {key: found[key] == FROZEN_PYTHON_ARM[key] for key in FROZEN_PYTHON_ARM}, "pythonArmAttributable": False,
            "reason": "no G1-E bundle was given: the G1-E comparison is not-same-class and nothing is attributed to the Python arm"}


def absolutize(command: str, args: list[str]) -> list[str]:
    """`args` with every path argument made absolute against the current directory (once, at entry)."""
    positions = PATH_ARGS.get(command)
    if positions is None:
        return list(args)
    return [os.path.abspath(arg) if (positions == ALL or index in positions) and arg != NO_PATH else arg for index, arg in enumerate(args)]


def absolute_spec(spec: dict) -> dict:
    """The path values inside the `f0-build` spec file, absolute as well."""
    out = dict(spec)
    for key in ("sidecar", "reader_descriptor"):
        if isinstance(out.get(key), str):
            out[key] = os.path.abspath(out[key])
    return out


def f0_build(root: Path, out: Path, spec: dict, *, host: dict | None = None, row_set: dict | None = None, build: Callable[..., dict] = hosted_f0.build_hosted_f0) -> str:
    """Writes the hosted F0 to `out` and returns its SHA-256."""
    spec = absolute_spec(spec)
    f0 = build(Path(root), host=host or hosted_hostclass.observe_host_hosted(), row_set=row_set, branch=spec["branch"], sidecar=Path(spec["sidecar"]), seed=spec["seed"], approval=spec["approval"],
               pin_manifest=helper.pin_manifest(Path(root)), reader_extra=spec.get("reader_extra"),
               reader_descriptor=Path(spec["reader_descriptor"]) if spec.get("reader_descriptor") else None)      # Windows registers no reader rows, so it builds no fixtures
    raw = json.dumps(f0, indent=1, sort_keys=True).encode() + b"\n"
    Path(out).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def f0_spec(root: Path, pre_out: Path, branch: str, system: str) -> dict:
    """The `f0-build` spec of a hosted job: the seed is the first 32 hex characters of sha256(`packaged-build.json`) (amendment 4, a new seed per job), the approval is the G3 line of the excerpt
    (the line the unchanged `approval_problems` wants: whole, dated, naming the proposal digest and U1-U8), the sidecar is the platform's, and the reader descriptor exists on Linux only."""
    pre_out = Path(pre_out)
    excerpt = (Path(root) / f0mod.LEDGER_PATH).read_text(encoding="utf-8").split("\n")
    quote = next((line for line in excerpt if "(G3 cost pre-registration" in line), None)
    if quote is None:
        raise ValueError("the approval excerpt has no G3 line")
    spec = {"branch": branch, "sidecar": str(Path(root) / hosted_packaged.layout(system).sidecar_rel),
            "seed": hashlib.sha256((pre_out / helper.packaged_mod_record_name).read_bytes()).hexdigest()[:32],
            "approval": {"ledger": f0mod.LEDGER_PATH, "date": quote[2:12], "quote": quote}}
    if hosted_reader.has_readers(system):
        spec["reader_descriptor"] = str(pre_out / "FIXTURES.json")
    return spec


def pins_problems(root: Path) -> list[str]:
    """The g1e pin check (`pins.verify_pins`) on its own: the job runs it right after the overlay, before any harness code is used."""
    return pins.verify_pins(Path(root), helper.pin_manifest(Path(root)))


def hosted_regression_entries(table: list[dict], g1e: object, *, same_class: bool, python_arm_attributable: bool) -> list[dict]:
    """Design section 6, last paragraph: no G1-E data exist for a hosted class, so every comparison is `not-same-class` and report-only (an empty G1-E side, a different host class). The Python arm's
    attribution (`python_arm_attributable`, derived from the G1-E bundle by `helper.python_arm_attribution`) is passed through; with `same_host_class` false it cannot change a label."""
    return [regression.compare_row(row["id"], spec=None, g1e_d=[], g3_d=None, g1e_python_median=None, g3_python_median=None, g1e_e=None, g3_e=row.get("e"),
                                   same_host_class=False, python_arm_attributable=python_arm_attributable) for row in table]


def annotate_observers(doc: dict, system: str) -> dict:
    """Windows only: every agent-launch row of `rowTable` gets `observer: python` next to its unchanged id (the Python watcher observes both arms there); the same mapping is kept under
    `observerAnnotations`. Row ids, statuses and every decision input are untouched."""
    if system != "Windows":
        return doc
    ids = set(hosted_rows.agent_launch_ids())
    for row in doc.get("rowTable", []):
        if row["id"] in ids:
            row["observer"] = hosted_rows.WINDOWS_OBSERVER
    doc["observerAnnotations"] = {i: hosted_rows.WINDOWS_OBSERVER for i in sorted(ids)}
    return doc


def report_lines(doc: dict) -> list[str]:
    """The row lines of the report: `<id>  <status>`, plus `  observer=<name>` where the row carries one."""
    return [f"{row['id']}  {row['status']}" + (f"  observer={row['observer']}" if "observer" in row else "") for row in doc["rowTable"]]


def decide(root: Path, f0_path: Path, f1_path: Path, bundle_dir: Path, manifest_sha256: str, *, sidecar_now_sha256: str, build_record: dict | None, last_code_commit_ts: int | None,
           packaged_record: dict | None = None, system: str | None = None, g1e_dir: Path | None = None, python_arm_report: dict | None = None, g1e_manifest_sha256: str | None = None) -> dict:
    """`helper.decide` of the macOS run. The identity rules, the row table and the facts are the unchanged ones; the G1-E comparison stays `not-same-class` (no G1-E data exist for a hosted class). The Python
    arm's attribution goes through the macOS path (`helper.python_arm_attribution`, the REGISTERED digest `g1e_manifest_sha256` as the only digest): given `g1e_dir` and `python_arm_report` it is derived
    from the sealed bundle; given neither it is non-attributable with the reason "no G1-E bundle was given". It never raises for a missing, wrong or malformed bundle or digest."""
    f0, f1 = helper._json(f0_path), helper._json(f1_path)
    sealed = g1e_bundle.Bundle(Path(bundle_dir), manifest_sha256)
    matrix, shell = g3bundle.shell_matrix(sealed, f0, helper._sha(f1_path))
    launch_receipts, launch_invalid, launch_missing = assemble.launch_matrix_receipts(Path(bundle_dir), f0, helper._sha(f1_path))
    reader_record = sealed.read_json("reader.json") if "reader.json" in sealed.files else {"rows": {}}
    shell_run = sealed.read_json("run.json")
    identity = helper.final_identity(Path(root), f0, sidecar_now_sha256, build_record, last_code_commit_ts, packaged_record)
    table, c1 = g3bundle.row_table(f1, shell, launch.pairs_by_row(launch_receipts), g3bundle.combined_rows(matrix), g3bundle.reader_rows(reader_record))
    flags, detail = g3bundle.matrix_flags(f0, matrix, shell_run, artifact_identity=identity, quiet_timed_out=False, reader_fixture_missing=False, launch_complete=not launch_invalid and not launch_missing)
    source = {"head": f0["head"], "tree": f0["trackedDiffSha256"], "artifacts": [{"name": "navide-cli-runtime", "sha256": f0["sidecar"]["sha256"], "builtAt": (build_record or {}).get("builtAt", ""),
                                                                                  "predatesCode": packaged.predates((build_record or {}).get("builtAtEpoch"), last_code_commit_ts)},
                                                                                 *packaged.artifact_entries(packaged_record, build_record, last_code_commit_ts)], "artifactIdentity": identity}
    arm = helper.python_arm_attribution(g1e_dir, g1e_manifest_sha256, python_arm_report if python_arm_report is not None else {})
    original = g3bundle.regression_entries
    g3bundle.regression_entries = hosted_regression_entries      # `build_document` calls it by module attribute
    try:
        doc = g3bundle.build_document(f0=f0, f1=f1, table=table, c1=c1, flags=flags, source=source, g1e={}, python_arm_attributable=arm["attributable"], g1e_host=arm["host"])
    finally:
        g3bundle.regression_entries = original
    doc["descriptive"] = {"packagedColdStart": packaged.summary(Path(bundle_dir) / "packaged-trials", packaged.identity_problems(packaged_record, f0, last_code_commit_ts))}
    doc["evidenceDetail"] = {**detail, "launchInvalid": launch_invalid, "launchMissing": launch_missing, "hostedClass": True, "pythonArmIdentity": arm["identity"], "g1eHostClass": arm["host"]}
    doc["platforms"] = [hosted_hostclass.platform_entry(f0, measured=True)]      # F1: the outcome names the platform that ran, not the static macOS-local table
    return annotate_observers(doc, system or platform.system())


def main(argv: list[str], *, delegate: Callable[[list[str]], int] = helper.main, install: Callable[[str], object] = hosted_seams.install) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 64
    command, args = argv[0], absolutize(argv[0], argv[1:])
    try:
        install(platform.system())
        if command == "hosted-inputs-check" and len(args) == 1:
            problems = hosted_f0.inputs_problems(Path(args[0]))
            print(json.dumps({"problems": problems}))
            return 0 if not problems else 2
        if command == "pins-verify" and len(args) == 1:
            problems = pins_problems(Path(args[0]))
            print(json.dumps({"problems": problems}))
            return 0 if not problems else 2
        if command == "hosted-report" and len(args) == 1:
            print("\n".join(report_lines(helper._json(args[0]))))
            return 0
        if command == "python-arm-identity-f0" and len(args) == 2:
            record = python_arm_identity_f0(Path(args[0]), sys.executable)
            Path(args[1]).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"equalsFrozen": record["equalsFrozen"]}, sort_keys=True))
            return 0
        if command == "f0-spec" and len(args) == 4:
            Path(args[2]).write_text(json.dumps(f0_spec(Path(args[0]), Path(args[1]), args[3], platform.system()), indent=1, sort_keys=True) + "\n", encoding="utf-8")
            return 0
        if command == "f0-build" and len(args) == 3:
            print(f0_build(Path(args[0]), Path(args[1]), helper._json(args[2]), row_set=hosted_rows.row_set_record(platform.system())))
            return 0
        if command == "decide" and len(args) in (7, 9):      # 9 = with the G1-E bundle and the Python-arm report, as the macOS command
            root, f0_path, f1_path, bundle, manifest_file, out, build_record_path = args[:7]
            f0 = helper._json(f0_path)
            packaged_path = Path(build_record_path).with_name(helper.packaged_mod_record_name)
            report = None
            if len(args) == 9:
                try:
                    report = helper._json(args[8])
                except (OSError, ValueError):      # a missing or unreadable report is a refusal inside the outcome, not a crash
                    report = {}
            doc = decide(Path(root), Path(f0_path), Path(f1_path), Path(bundle), Path(manifest_file).read_text().strip(), sidecar_now_sha256=helper._sha(f0["sidecar"]["path"]),
                         build_record=helper._json(build_record_path), last_code_commit_ts=helper.last_code_commit_ts(Path(root)),
                         packaged_record=helper._json(packaged_path) if packaged_path.is_file() else None,
                         g1e_dir=Path(args[7]) if len(args) == 9 else None, python_arm_report=report, g1e_manifest_sha256=attribution.registered_manifest_sha256())
            Path(out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"outcome": doc["outcome"], "reasons": doc["reasons"]}))
            return 0
    except (f0mod.F0Error, f1mod.F1Error) as error:
        print("; ".join(error.problems), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, RuntimeError, g1e_bundle.BundleError) as error:
        print(f"{type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
        return 2
    if command in ("decide", "f0-build", "pins-verify", "f0-spec", "hosted-report"):      # a wrong argument count of a replaced command never reaches the macOS body (`python-arm-identity` is not replaced: it is delegated)
        print(__doc__, file=sys.stderr)
        return 64
    code = delegate([command, *args])
    if command == "assemble-bundle" and code == 0 and len(args) == 5:      # a registry without readers (Windows) has no `reader.json`, which the unchanged rehearsal admission reads
        hosted_reader.ensure_reader_record(Path(args[4]), helper._json(args[1])["rowRegistry"]["readerFixtures"]["registered"])
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
