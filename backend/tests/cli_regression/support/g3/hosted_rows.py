"""The rows each hosted platform registers (design section 2, addendum D/E). Pure selection over the unchanged registries (`g1e.reference.REG`, `g3.rows`); nothing here edits a row, margin or formula.

* Linux x64: the 49 carried rows, the 3 claude reader rows (no Rust opencode observer on Linux, so the opencode reader rows are not applicable), 4 agent-launch rows, 6 combined RSS rows = 62.
* Windows x64: the 49 carried rows, 4 agent-launch rows and 6 combined RSS rows = 59, no reader rows. There is no reduced set: the byte-exact `input_output` phase is hard-wired in the pinned attempt runner
  and receipt validators, so a Windows attempt either passes the unchanged checks (the rehearsal must show it) or Windows is "Not measured (port not verified)".

The agent-launch rows keep their ids on every platform; on Windows the hosted decide output adds an `observer` annotation (`python`, both arms) next to each id (see `hosted_helper.observer_annotations`).
"""
from __future__ import annotations

import hashlib
import json

from tests.cli_regression.support.g3 import rows

READER_CLAUDE = ["claude"]
WINDOWS_OBSERVER = "python"


def decision_ids(system: str) -> list[str]:
    """Every decision-bearing id of the platform's F1 (C1 included, report-only, as on macOS)."""
    if system not in ("Linux", "Windows"):
        raise ValueError(f"no hosted row set for {system!r}")
    from tests.cli_regression.support.g1e import reference as ref
    reader = rows.reader_row_ids(READER_CLAUDE) if system == "Linux" else []
    return [spec["id"] for spec in ref.REG] + [spec["id"] for spec in rows.agent_launch_specs()] + [spec["id"] for spec in rows.combined_rss_specs()] + reader


def agent_launch_ids() -> list[str]:
    return [spec["id"] for spec in rows.agent_launch_specs()]


def row_set_record(system: str) -> dict:
    """The record that goes into F0's `hostedRunner`: the count, the ids' digest and what is not applicable on the platform."""
    ids = decision_ids(system)
    not_applicable = ["reader|*|opencode"] if system == "Linux" else ["reader|*|claude", "reader|*|opencode"]
    return {"system": system, "mode": "full", "count": len(ids), "idsSha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest(), "notApplicable": not_applicable,
            "agentLaunchObserver": WINDOWS_OBSERVER if system == "Windows" else "as-macos",
            "packagedReadyCommand": "windows-native" if system == "Windows" else "as-macos"}      # the descriptive packaged row's pane command: Windows runs `g3hPackagedWindows.test.ts` (a Windows-native READY command), never the macOS row
