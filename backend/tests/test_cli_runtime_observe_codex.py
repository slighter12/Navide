"""G2 ticket 10: Codex observed by the Rust sidecar over the principal seam on POSIX.

Codex is one of the vendors the sidecar observed before ticket 06 (with Claude, and OpenCode on macOS), so
`test_cli_runtime_observe_vendors.py` does not parametrize it. Its principal-seam proof was the controller-gated,
eval-only `tests/cli/nativeDifferences.test.ts`, which is macOS-only and not committed. This case runs the same
two-stage story as `test_cli_runtime_observe_windows.py` (fixture written in two stages, backend restart between them,
the pane bound by the real SessionStart hook command) with the Rust sidecar selected, and asserts that the observation
was native: observation receipts exist and the launch was composed by Rust.
"""
from __future__ import annotations

import sys

import pytest

from tests.cli_regression.support.vendor_data import PANE, VendorData
from tests.test_cli_runtime_observe_windows import _durable, _receipts, _stage

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX Rust observation")]


@pytest.mark.timeout(900)
async def test_codex_launched_and_observed_by_the_rust_sidecar_over_the_principal_seam(tmp_path, monkeypatch, set_home):
    root = tmp_path / "pane-root"
    root.mkdir()
    data = VendorData("codex", root, monkeypatch, set_home)
    try:
        completion_phase = "complete" if data.fixture["phases"].get("complete") else "response"
        events = await _stage(root, 1, "codex", data, ["initial", "response"], completion_phase, native=True)
        events += await _stage(root, 2, "codex", data, ["complete"], completion_phase, native=True)
        durable = _durable(root, data.workspace, data.path.resolve())
        receipts = _receipts(root)
        expected = data.fixture["expected"]
        fixture = data.fixture
        workspace = str(data.workspace)
    finally:
        data.close()

    stream = [(event["type"], event["payload"]) for event in events if event.get("type") in ("session.detected", "agent.activity")]
    completions = [row for kind, row in stream if kind == "agent.activity" and row["event_type"] == "turn_complete"]
    assert len(completions) == 1, [(kind, row.get("event_type")) for kind, row in stream]
    assert completions[0]["text"].strip() == expected["reply"]
    assert completions[0]["workspace_path"] == workspace

    detected = [row for kind, row in stream if kind == "session.detected"]
    assert detected, "the session was never reported"
    assert all(row["pane_id"] == PANE and row["session_id"] == fixture["sessionId"] and row["vendor"] == "codex"
               for row in detected), detected
    foreign = [row["pane_id"] for kind, row in stream if kind == "agent.activity" and row["pane_id"] not in (None, "", PANE)]
    assert not foreign, f"activity attributed to another pane: {foreign}"

    assert durable["totals"] == expected["tokens"], durable
    assert durable["usage_checkpoint"] and durable["activity_checkpoint"]

    # Native, not Python-observed: the sidecar parsed the Codex files, and the launch was composed by Rust.
    observed = [row for row in receipts if "parser" in row]
    assert observed, "no native observation receipt: the watcher read the files in Python"
    assert {row.get("mode") for row in observed} >= {"usage", "activity"}, observed
    owned = [row for row in receipts if row.get("event") == "owned"]
    assert owned and all(row.get("argv_source") == "rust" for row in owned), owned
