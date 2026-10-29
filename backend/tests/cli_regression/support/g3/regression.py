"""The descriptive G1-E-to-G3 comparison (proposal section 3 and 4 item 8, U8). No decision weight: a label never alters the cost outcome.

Per row, from the ten paired differences `d_r` of each session (`g1e_d`, `g3_d`) and the Python-arm median of each (`g1e_python_median`, `g3_python_median`):

* `new` - the row has no G1-E counterpart (H4, H6, codex reader);
* `not-same-class` - the host class differs (U8: `ncpu` and architecture must match): every comparison is report-only;
* `not-attributable` - the Python-arm baseline changed without `refactor_reviewed` (4 item 8, all rows), or the Python-arm median moved by more than the row margin between sessions
  (`machine-state-changed`, section 3 item 4), or either side has fewer than ten valid pairs;
* otherwise, with `B1 = [d(2), d(9)]` of G1-E and `B3` of G3: `regressed` iff `lower(B3) > upper(B1)`, `improved` iff `upper(B3) < lower(B1)`, else `no-resolvable-change`.

Precedence: new, not-same-class, not-attributable, then the band rule. The row margin for the drift test is the one the row had in G1-E at the G1-E Python median
(`ref.margin_for`; an absolute margin is the number itself).
"""
from __future__ import annotations

from tests.cli_regression.support.g1e import reference as ref

LABELS = ("regressed", "improved", "no-resolvable-change", "not-attributable", "new", "not-same-class")
ARCH_ALIASES = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "amd64": "x64", "x64": "x64"}


def normalise_arch(arch: str) -> str:
    """`aarch64-apple-darwin` and `arm64` are the same architecture; `x86_64` and `amd64` too. An unknown value stays itself, so it only matches itself."""
    head = str(arch).lower().split("-", 1)[0]
    return ARCH_ALIASES.get(head, head)


def same_class(g1e_host: dict, g3_host: dict) -> bool:
    """U8: the same machine class iff `ncpu` and architecture match (model, memory and OS build were not recorded in G1-E)."""
    return g1e_host.get("ncpu") == g3_host.get("ncpu") and normalise_arch(g1e_host.get("arch", "")) == normalise_arch(g3_host.get("arch", ""))


def host_class_record(g3_host: dict, g1e_host: dict) -> dict:
    """The `hostClass` section of the outcome: the G3 host, `sameAsG1E`, and what G1-E did not record."""
    return {**g3_host, "sameAsG1E": same_class(g1e_host, g3_host), "notRecordedInG1E": ["model", "memoryGiB", "os"]}


def _median(values: list) -> float:
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def compare_row(row_id: str, *, spec: dict | None, g1e_d: list | None, g3_d: list | None, g1e_python_median: float | None, g3_python_median: float | None, g1e_e: int | None,
                g3_e: int | None, same_host_class: bool, python_arm_attributable: bool) -> dict:
    """One `regressionSinceG1E` entry. `spec` is the registry row (for its margin) or None for a new row; a new row passes `g1e_d=None`."""
    entry = {"row": row_id, "comparable": False, "g1eBand": None, "g3Band": None, "g1eE": g1e_e, "g3E": g3_e, "label": "new", "pythonArmDrift": None, "machineStateChanged": False,
             "g1eMedianD": None, "g3MedianD": None, "reason": None}
    if g1e_d is None:
        return entry
    entry["g1eBand"], entry["g3Band"] = ref.band(g1e_d), (ref.band(g3_d) if g3_d is not None else None)
    entry["g1eMedianD"] = _median(g1e_d) if g1e_d else None
    entry["g3MedianD"] = _median(g3_d) if g3_d else None
    if g1e_python_median is not None and g3_python_median is not None:
        entry["pythonArmDrift"] = g3_python_median - g1e_python_median
    if not same_host_class:
        return {**entry, "label": "not-same-class", "reason": "not-same-class"}
    margin = ref.margin_for(spec, g1e_python_median) if spec is not None and g1e_python_median is not None else None
    if margin is not None and entry["pythonArmDrift"] is not None and abs(entry["pythonArmDrift"]) > margin:
        entry["machineStateChanged"] = True
    if not python_arm_attributable:
        return {**entry, "label": "not-attributable", "reason": "unreviewed-python-arm"}
    if entry["machineStateChanged"]:
        return {**entry, "label": "not-attributable", "reason": "machine-state-changed"}
    if entry["g1eBand"] is None or entry["g3Band"] is None:
        return {**entry, "label": "not-attributable", "reason": "n-below-10"}
    entry["comparable"] = True
    b1, b3 = entry["g1eBand"], entry["g3Band"]
    entry["label"] = "regressed" if b3[0] > b1[1] else ("improved" if b3[1] < b1[0] else "no-resolvable-change")
    return entry


def reader_note() -> str:
    return "The reader code changed after G1-E (Claude reader fix, OpenCode WAL fix): a reader change since G1-E mixes algorithm and language."
