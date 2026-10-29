"""The G1-E outcome document in prose (E4 acceptance: "record continue, stop or inconclusive with limitations"): a deterministic Markdown rendering of a `Decision`.

Everything printed is read from the derived document; nothing is added by hand. The text states what the registered table says and no more: a CONTINUE recommends E5 and further
evaluation only, never adoption or G2, and records whether continuation authority exists; STOP and INCONCLUSIVE list their rule ids. The ticket 06 STOP stays history (not rewritten).
"""
from __future__ import annotations

from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e.decider import Decision

RECOMMENDATION = {
    "continue": ("CONTINUE recommends ticket E5 (Linux and Windows CI functional verification) and further evaluation ONLY. It is not adoption and not G2; E5 additionally needs "
                 "recorded user authorization of hosted Actions (D18) and a joint review pass. " + ref.CONTINUE_SCOPE),
    "stop": "STOP: the registered rule found a stop reason (listed below). The ticket 06 STOP stays as history and is not rewritten.",
    "inconclusive": "INCONCLUSIVE: no stop reason applies, but the evidence does not satisfy every CONTINUE condition (listed below). The ticket 06 STOP stays as history.",
}


def _noisy_sections(doc: dict) -> list[str]:
    """"Noisy rows (reported with their error, not decision-bearing)" (v14-r2 V11 item 4): k, then per noisy row the label, the calibration noise, the matrix result, the band reading and the spread statistics."""
    entries = doc["noisyReport"]
    lines = ["", "## Noisy rows (reported with their error, not decision-bearing)", "", f"k = {len(entries)}" + (" (number of noisy rows; the other paired rows and the one-arm budget rows are decision-bearing)" if entries else " (no row is noisy)")]
    for entry in entries:
        cal, mx = entry["calibration"], entry["matrix"]
        lines += ["", f"### `{entry['id']}`", "", f"- label: {entry['label']}",
                  "- calibration noise: " + "; ".join(f"{key} {cal[key]}" for key in ("qHat", "qHatAllPairs", "calValues", "calMedAbs", "calP90Abs", "marginAtMedian", "marginOverCalMedAbs")),
                  "- matrix result: " + "; ".join(f"{key} {mx[key]}" for key in ("validPairs", "exceedCount", "d", "dExcess", "medianD", "medianExcess", "bandD", "bandExcess", "rawRange", "matrixPythonMedAbs")),
                  f"- band reading: {entry['bandReading']} (a description of where the excess band [d'(2), d'(9)] lies against 0, not a verdict)",
                  "- spread statistics (report-only, never decision-bearing): " + "; ".join(f"{key} {mx[key]}" for key in ("meanD", "sdD", "pythonMean", "pythonSd", "rustMean", "rustSd"))]
    return lines


def _c1_sections(doc: dict) -> list[str]:
    c1, interpretation = doc["c1Report"], doc["interpretation"]
    lines = ["", "## C1 report (kill/reap, cell 4)", "", f"- row: `{c1['id']}`; status: `{c1['status']}`",
             "- differences: " + "; ".join(f"{key} {c1[key]}" for key in ("validPairs", "exceedCount", "d", "dExcess", "medianD", "medianExcess", "bandD", "bandExcess", "rawRange")),
             "- spread statistics (report-only, never decision-bearing): " + "; ".join(f"{key} {c1[key]}" for key in ("meanD", "sdD", "pythonMean", "pythonSd", "rustMean", "rustSd"))]
    lines += ["", "## Interpretation", "", f"- {interpretation['continueScope']}"]
    if "killReap" in interpretation:
        lines.append(f"- {interpretation['killReap']}")
    return lines


def render(decision: Decision) -> str:
    doc = decision.document
    lines = [f"# G1-E outcome: {decision.outcome.upper()}", "", RECOMMENDATION[decision.outcome], "", "## Reasons (registered rule ids)", ""]
    lines += [f"- `{reason}`" for reason in decision.reasons]
    lines += ["", "## Decision facts (all derived)", "", "| fact | value |", "|---|---|"] + [f"| {name} | `{value}` |" for name, value in sorted(decision.facts.items())]
    ev = doc["evaluated"]
    lines += ["", "## Evaluated", "", f"- HEAD `{ev['head']}` on `{ev['branch']}`; sidecar `{ev['sidecarSha256']}`", f"- method `{ev['methodRevision']}`, evidence revision `{ev['evidenceRevision']}`",
              f"- freeze `{ev['freezeSha256']}`; receipts manifest `{ev['receiptsManifestSha256']}`; E1 `{ev['e1ProtocolSha256']}`"]
    m = doc["matrix"]
    lines += ["", "## Matrix", "", f"- accepted {m['accepted']} of {m['attempted']}; complete: {m['complete']}",
              f"- evidence-invalid trials: {m['evidenceInvalidTrials'] or 'none'}; missing slots: {m['missingSlots'] or 'none'}; integrity failures: {len(m['integrityFailures'])}"]
    lines += [f"  - {text}" for text in m["integrityFailures"][:20]]
    lines += ["", "## Rows (49)", "", "| row | status | valid pairs | exceed | median excess |", "|---|---|---|---|---|"]
    for rid, row in sorted(doc["rows"].items()):
        lines.append(f"| `{rid}` | {ref.NOISY_LABEL if row['status'] == 'noisy' else row['status']} | {row['validPairs']} | {row['exceedCount']} | {row['medianExcess']} |")
    lines += _noisy_sections(doc) + _c1_sections(doc)
    lines += ["", "## Correctness (21 required rows)", "", "| row | status |", "|---|---|"]
    lines += [f"| `{cid}` | {row['status']}{' (' + row['note'] + ')' if row.get('note') else ''} |" for cid, row in sorted(doc["correctness"].items())]
    rb = doc["readerBenchmark"]
    lines += ["", f"## Reader benchmark: `{rb['status']}`", "", f"- runs {rb['runs']}, calibration runs {rb['calibrationRuns']}"]
    baseline, locality, maintainability = doc["baselineIdentity"], doc["locality"], doc["maintainability"]
    lines += ["", "## Derivation", "", f"- path: `{doc['derivation']['path']}`" + (" (no matrix attempt exists: facts that need matrix pairs are `not_derivable`)" if doc["derivation"]["path"] == "no-matrix" else ""),
              f"- earlier runs carried: {[(run['kind'], run['manifestSha256'][:12], run['stopReason'], run['stopFacts']) for run in doc['derivation']['earlierRuns']] or 'none'}"]
    lines += ["", "## Baseline identity, locality, maintainability", "",
              f"- baseline identity: `{baseline['status']}` ({len(baseline['changedFiles'])} listed files with verdicts)" if baseline else "- baseline identity: `not_derivable` (no record)",
              f"- locality violation: {locality['violation']} ({len(locality['findings'])} findings)" if locality else "- locality violation: `not_derivable` (no record)",
              f"- maintainability complete: {maintainability['complete']}" if maintainability else "- maintainability complete: `not_derivable` (no record)"]
    lines += ["", "## Report-only (not decision facts)", "", f"- resize acknowledgement, paired median Rust - Python: {doc['reportOnly']['resizeAckMs']}"]
    lines += ["", "## Limitations", ""] + [f"- {text}" for text in doc["limitations"]]
    review = doc["jointReview"]
    lines += ["", "## Gates", "", f"- joint review: `{review['status']}` ({len(review['reports'])} reports)", f"- continuation authority recorded: {doc['continuationAuthority']['recorded']}",
              f"- ticket E5 gate open: {doc['gates']['ticketE5']['open']} (missing: {doc['gates']['ticketE5']['missing'] or 'nothing'})",
              f"- G2 gate open: {doc['gates']['g2']['open']} (missing: {doc['gates']['g2']['missing']})", "- Both gates are necessary, not sufficient; this outcome is not adoption.", ""]
    return "\n".join(lines)
