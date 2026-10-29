"""`python_arm_attributable` (manager answer 5): derived by code from a rerun of the G1-E baseline-identity tool, never entered by hand.

The G1-E tool (`g1e.baseline_identity`) is used UNCHANGED: `trace_python_arm`, `identity_record` (the listing of Python-arm files whose loaded bytes differ from the baseline reference), and
`classify` (two distinct reviewers' `behaviour-neutral` verdicts, each given on the file's loaded bytes, for every listed file). The reviewer verdicts are the ones the G1-E bundle recorded
(`baseline-identity.json`); `classify` itself rejects a verdict whose `loadedSha256` is not the file's loaded bytes now, so a file edited since G1-E is `changed`. The baseline reference is the G1-E
one: the freshly built manifest must equal the manifest G1-E froze, else the report says so and the value is false.

`python_arm_attributable` is true only for status `identical` or `refactor_reviewed` with a matching reference. `attributable(report)` recomputes it from the report's facts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import baseline_identity as bi

SCHEMA = "acr-g3-python-arm-identity/1"
ATTRIBUTABLE_STATUSES = ("identical", "refactor_reviewed")


def g1e_verdicts(g1e_record: dict) -> dict[str, list[dict]]:
    """{path: verdicts} as the G1-E record stored them (each carries its `loadedSha256`)."""
    return {c["path"]: list(c.get("verdicts") or []) for c in g1e_record.get("changedFiles", [])}


def build_report(record: dict, g1e_record: dict, g1e_manifest: list[dict]) -> dict:
    """The report from an identity record (`bi.identity_record`) against the G1-E record and its reference manifest. Pure."""
    status = bi.classify(record, g1e_verdicts(g1e_record))
    verdicts = g1e_verdicts(g1e_record)
    unreviewed = [c["path"] for c in record["changed"] if bi.classify({"changed": [c]}, {c["path"]: verdicts.get(c["path"], [])}) != "refactor_reviewed"]
    return {"schema": SCHEMA, "status": status, "upstreamMerge": record["upstreamMerge"], "e3Parent": record["e3Parent"], "e3Commit": record["e3Commit"], "traced": len(record["files"]),
            "manifestSha256": record["manifestSha256"], "g1eManifestSha256": bi.manifest_sha256(g1e_manifest),
            "referenceMatchesG1E": record["manifestSha256"] == bi.manifest_sha256(g1e_manifest),
            "listed": [{k: v for k, v in c.items() if k != "diff"} for c in record["changed"]], "unreviewed": unreviewed}


def attributable(report: object) -> bool:
    """True only if the report shows no Python-arm change that is not `refactor_reviewed`. Recomputed from the report's facts; a malformed report is false."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return False
    return report.get("status") in ATTRIBUTABLE_STATUSES and report.get("referenceMatchesG1E") is True and report.get("unreviewed") == []


def run(root: Path, g1e_dir: Path, out: Path, *, python: str | None = None, trace: Callable[..., list[str]] = bi.trace_python_arm,
        identity: Callable[..., dict] = bi.identity_record) -> dict:
    """Trace the Python arm of `root`, take the listing against the G1-E reference and write the report. Raises (the step fails and the run stops) when the tool cannot run unchanged."""
    g1e_dir = Path(g1e_dir)
    g1e_record = json.loads((g1e_dir / "baseline-identity.json").read_text(encoding="utf-8"))
    g1e_manifest = json.loads((g1e_dir / g1e_record["referenceManifest"]["path"]).read_text(encoding="utf-8"))
    files = trace(Path(root), python or sys.executable)
    record = identity(Path(root), files, g1e_record["e3Set"])
    report = build_report(record, g1e_record, g1e_manifest)
    Path(out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return report
