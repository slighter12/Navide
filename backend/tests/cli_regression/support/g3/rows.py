"""The G3 row registry additions (proposal section 2.1, H4-H6). The 49 carried G1-E rows stay `g1e.reference.REG` (unchanged, margins carried, U1/U4); this module adds:

* agent-launch rows (H4): `lifecycle|lat:agent_create_to_first_usable_ms:<vendor>|cell<c>`, vendors claude (Rust observer on macOS) and aider (no observer), cells 1 and 4. The vendor is part of
  the metric so the id keeps the G1-E `class|metric|cell` shape and cannot collide with a G1-E id. The margin is derived from Python-only calibration (`margins.py`, floor 30 ms), not set here;
* one descriptive packaged cold-start row (U7): never decision-bearing, no margin, no status;
* combined RSS rows (H6): `res:<phase>:view:combined:rssMiB`, 3 phases x 2 cells, margin A(7.5 MiB) = backend 1.0 + sidecar budget 6.5 (both already approved), Python = backend alone,
  Rust = backend + sidecar;
* reader rows (H5): claude and opencode as in G1-E; codex only if a hashed, reviewed fixture exists before F0 (`reader_fixtures`), else "not measured (no fixture)" and not registered.
"""
from __future__ import annotations

import re

from tests.cli_regression.support.g1e import reference as ref

AGENT_LAUNCH_METRIC = "lat:agent_create_to_first_usable_ms"
AGENT_LAUNCH_VENDORS = {"claude": "rust", "aider": "none"}      # vendor -> the observer of the Rust arm on macOS
AGENT_LAUNCH_FLOOR_MS = 30.0
PACKAGED_ID = "descriptive|lat:packaged_cold_to_first_usable_ms|cell1"
COMBINED_PHASES = ("empty_started_idle", "live_idle", "input_output")
COMBINED_MARGIN_MIB = 7.5      # backend margin 1.0 + sidecar budget 6.5
assert COMBINED_MARGIN_MIB == ref.A(1.0)["value"] + ref.BUDGET[0][3]["value"]
READER_METRICS = ("m1_wall_ms", "m2_cpu_s", "m3_peak_rss_mib")
CODEX_FIXTURE = "codex"
HEX64 = re.compile(r"[0-9a-f]{64}")


def agent_launch_specs() -> list[dict]:
    """H4: 2 vendors x 2 cells = 4 paired rows with NO margin (derived and frozen in F1); `observer` records the vendor's Rust observer."""
    rows = []
    for vendor, observer in AGENT_LAUNCH_VENDORS.items():
        for cell in ref.CELLS:
            rows.append({"id": f"lifecycle|{AGENT_LAUNCH_METRIC}:{vendor}|cell{cell}", "cls": "agentLaunch", "metric": f"{AGENT_LAUNCH_METRIC}:{vendor}", "cell": cell, "kind": "parity",
                         "unit": "ms", "margin": None, "basis": "G3-formula", "source": "paired-delta", "vendor": vendor, "observer": observer, "floor": AGENT_LAUNCH_FLOOR_MS})
    return rows


def combined_rss_specs() -> list[dict]:
    """H6: 3 phases x 2 cells = 6 derived paired rows with the approved A(7.5 MiB) margin."""
    rows = []
    for phase in COMBINED_PHASES:
        for cell in ref.CELLS:
            metric = f"res:{phase}:view:combined:rssMiB"
            rows.append({"id": f"combinedRss|{metric}|cell{cell}", "cls": "combinedRss", "metric": metric, "cell": cell, "kind": "parity", "unit": "MiB", "margin": ref.A(COMBINED_MARGIN_MIB),
                         "basis": "A", "source": "paired-delta"})
    return rows


def combined_rss_pair(python_backend_mib: float, rust_backend_mib: float, rust_sidecar_mib: float) -> dict:
    """One pair of a combined RSS row: Python = the backend alone, Rust = backend + sidecar (never a different sum)."""
    for name, value in (("python backend", python_backend_mib), ("rust backend", rust_backend_mib), ("rust sidecar", rust_sidecar_mib)):
        if not ref.finite(value) or value < 0:
            raise ValueError(f"{name} RSS must be a finite non-negative number, got {value!r}")
    return {"python": float(python_backend_mib), "rust": float(rust_backend_mib) + float(rust_sidecar_mib)}


def packaged_descriptive_row() -> dict:
    """U7: the packaged cold-start row. It carries no margin and no status; `decisionBearing` is False and nothing in the decider reads it."""
    return {"id": PACKAGED_ID, "cls": "descriptive", "metric": "lat:packaged_cold_to_first_usable_ms", "cell": 1, "kind": "descriptive", "unit": "ms", "margin": None, "basis": None,
            "source": "single-arm", "decisionBearing": False}


def reader_fixture_registered(record: object) -> bool:
    """A codex (or any extra) reader fixture counts only as a record {name, sha256 (64 hex), reviewed: True, reviewReportSha256 (64 hex)} (proposal H5)."""
    return (isinstance(record, dict) and isinstance(record.get("name"), str) and bool(record["name"]) and isinstance(record.get("sha256"), str) and HEX64.fullmatch(record["sha256"]) is not None
            and record.get("reviewed") is True and isinstance(record.get("reviewReportSha256"), str) and HEX64.fullmatch(record["reviewReportSha256"]) is not None)


def reader_fixtures(extra: list | None = None) -> dict:
    """The reader fixtures F0 registers. claude and opencode are carried. `extra` = candidate fixture records; codex is registered iff one qualifies BEFORE F0, otherwise it is listed as
    `not measured (no fixture)`, absent from the registry, so its absence cannot trigger the `inconclusive` rule of 5.2. Any other extra name is refused (no silent new fixture)."""
    registered = ["claude", "opencode"]
    not_measured = []
    codex = next((r for r in (extra or []) if isinstance(r, dict) and r.get("name") == CODEX_FIXTURE), None)
    for record in extra or []:
        if not isinstance(record, dict) or record.get("name") != CODEX_FIXTURE:
            raise ValueError(f"unknown reader fixture {record!r}: only codex may be added")
    if codex is not None and reader_fixture_registered(codex):
        registered.append(CODEX_FIXTURE)
    else:
        not_measured.append({"fixture": CODEX_FIXTURE, "status": "not measured (no fixture)"})
    return {"registered": registered, "notMeasured": not_measured, "fixtures": [dict(r) for r in (extra or []) if r.get("name") in registered]}


def reader_row_ids(fixtures: list[str]) -> list[str]:
    """Reader row ids in G1-E order (fixture-major, m1..m3); claude and opencode reproduce `ref.READER_ROWS` exactly."""
    return [f"reader|{m}|{fx}" for fx in fixtures for m in READER_METRICS]


def reader_row_specs(fixtures: list[str]) -> list[dict]:
    units = {"m1_wall_ms": "ms", "m2_cpu_s": "s", "m3_peak_rss_mib": "MiB"}
    return [{"id": f"reader|{m}|{fx}", "cls": "reader", "metric": m, "fixture": fx, "kind": "parity", "unit": units[m], "source": "paired-delta"} for fx in fixtures for m in READER_METRICS]


def decision_ids(fixtures: list[str]) -> list[str]:
    """Every decision-bearing row id of a G3 F1: 49 carried (C1 included, report-only) + 4 agent launch + 6 combined RSS + 3 per registered reader fixture."""
    return [r["id"] for r in ref.REG] + [r["id"] for r in agent_launch_specs()] + [r["id"] for r in combined_rss_specs()] + reader_row_ids(fixtures)
