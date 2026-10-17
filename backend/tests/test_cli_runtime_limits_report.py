"""Measured use of the advertised limits (E3 S15, E1 2.1/13.1/decision 14). Writes a JSON report when
`NAVIDE_E3_LIMITS_REPORT` names a file; always asserts that every measured value is inside its limit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from agent_team_backend import cli_runtime
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import PRODUCT, FramePeer
from tests.test_cli_runtime_lifecycle import LifeRig

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file(), reason="requires the built macOS product sidecar")]

REPORT: dict = {}


def finish() -> None:
    target = os.environ.get("NAVIDE_E3_LIMITS_REPORT")
    if target:
        Path(target).write_text(json.dumps(REPORT, indent=1, sort_keys=True))


async def test_the_largest_real_create_request_against_max_meta_p2r(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        await service._ensure_started()
        limit = service._limits["max_meta_p2r"]
        # What the application sends: the backend's whole environment (the largest realistic env: this very shell's
        # environment plus 200 hook/MCP style variables of 512 bytes each) and a command with a 64 KiB prompt argument.
        env = {**os.environ, **{f"NAVIDE_HOOK_VAR_{i:03d}": "v" * 512 for i in range(200)}}
        before = list(service._ledger.tx.get(cli_runtime.code.CREATE, [0, 0]))
        session = await service.create_async(pane_id="pane-limit", agent_key="terminal", cwd=str(rig.workspace), env=env, cols=100, rows=30,
                                             command=[base_python_executable(), "-c", "import time; time.sleep(30)", "p" * 65536])
        after = service._ledger.tx[cli_runtime.code.CREATE]
        size = after[1] - before[1]
        REPORT["create_request"] = {"environment_entries": len(env), "frame_bytes": size, "max_meta_p2r": limit, "fraction_of_limit": round(size / limit, 5)}
        assert size < limit
        await service.kill(session.id, force=True)
    finally:
        await rig.close()


async def test_the_largest_observation_result_against_max_observation_bytes(tmp_path):
    from tests.cli_regression.support import reader_benchmark as rb
    recorded_path = Path(__file__).resolve().parents[2] / ".scratch/agent-cli-runtime-evaluation/evidence/e3/reader-fixtures.json"
    if not recorded_path.is_file():
        pytest.skip("evidence-gated: needs the local E3 reader fixtures (.scratch is not part of the commit)")
    recorded = json.loads(recorded_path.read_text())
    peer = FramePeer(tmp_path)
    try:
        rows = {}
        for name in rb.FIXTURES:
            args = {"checkpoint": {}} if name == "claude" else {"checkpoint": {}, "seen": []}
            request = peer.send(cli_runtime.code.OBSERVE, {"vendor": name, "path": recorded[name]["path"], "mode": "usage", **args})
            final = peer.result(request, timeout=60)
            parts = final.meta["observation_parts"]
            document = json.loads(b"".join(peer.parts[request]))
            size = sum(len(p) for p in peer.parts[request])
            rows[name] = {"fixture_bytes": recorded[name]["bytes"], "events": len(document["events"]), "observation_parts": parts,
                          "observation_bytes": size, "bytes_per_event": round(size / len(document["events"]), 1)}
            assert size < peer.ready["limits"]["max_observation_bytes"] and parts < peer.ready["limits"]["max_observation_parts"]
        limits = peer.ready["limits"]
        for name, row in rows.items():
            row["fraction_of_bytes_limit"] = round(row["observation_bytes"] / limits["max_observation_bytes"], 4)
            row["fraction_of_parts_limit"] = round(row["observation_parts"] / limits["max_observation_parts"], 4)
            row["events_at_the_bytes_limit"] = int(limits["max_observation_bytes"] / row["bytes_per_event"])
        REPORT["observation"] = {"max_observation_bytes": limits["max_observation_bytes"], "max_observation_parts": limits["max_observation_parts"], **rows}
    finally:
        peer.close()


async def test_descriptor_cost_per_session_in_python_and_in_the_sidecar(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        await service._ensure_started()
        warm = await rig.life(service, "pane-fd-warm")
        await service.kill(warm.id, force=True)
        me, sidecar = psutil.Process(), psutil.Process(service._runtime.pid)
        await rig.until(lambda: sidecar.num_fds() <= 6, "warm-up released")
        base = (me.num_fds(), sidecar.num_fds())
        sessions = [await rig.life(service, f"pane-fd-{i}") for i in range(8)]
        loaded = (me.num_fds(), sidecar.num_fds())
        REPORT["descriptors_per_session"] = {"sessions": 8, "python_baseline": base[0], "python_loaded": loaded[0], "python_per_session": (loaded[0] - base[0]) / 8,
                                             "sidecar_baseline": base[1], "sidecar_loaded": loaded[1], "sidecar_per_session": (loaded[1] - base[1]) / 8}
        assert (loaded[0] - base[0]) / 8 <= 2 and (loaded[1] - base[1]) / 8 <= 2
        for session in sessions:
            await service.kill(session.id, force=True)
        await rig.until(lambda: me.num_fds() <= base[0] and sidecar.num_fds() <= base[1], "descriptor counts returned to baseline")
    finally:
        await rig.close()


async def test_the_loss_stage_times_against_the_proposed_bounds(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        sessions = [await rig.life(service, f"pane-loss-{i}") for i in range(4)]
        generation = service._runtime_generation
        started = time.monotonic()
        rig.kill_sidecar(service)
        await rig.recovered(service)
        total = time.monotonic() - started
        receipt = rig.receipts_of("runtime_invalidated", lost_generation=generation)[0]
        stages = {s["stage"]: s["elapsed_ms"] for s in receipt["stages"]}
        REPORT["loss"] = {"sessions": len(sessions), "total_ms": round(total * 1000), "stages_elapsed_ms": stages,
                          "proposed_bounds_s": {"TASK_DRAIN_S": cli_runtime.TASK_DRAIN_S, "LOSS_FLUSH_S": cli_runtime.LOSS_FLUSH_S,
                                                "SIDECAR_EXIT_S": cli_runtime.SIDECAR_EXIT_S, "PERSIST_WAIT_S": cli_runtime.PERSIST_WAIT_S},
                          "complete": receipt["complete"]}
        assert receipt["complete"] is True and total < cli_runtime.TASK_DRAIN_S + cli_runtime.SIDECAR_EXIT_S + 8 + cli_runtime.LOSS_FLUSH_S
    finally:
        await rig.close()


async def test_the_journal_sync_cost_per_create(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        await service._ensure_started()
        durations = []
        for index in range(12):
            started = time.monotonic()
            session = await rig.life(service, f"pane-sync-{index}")
            durations.append((time.monotonic() - started) * 1000)
            await service.kill(session.id, force=True)
        durations.sort()
        REPORT["create_latency_ms"] = {"runs": len(durations), "median": round(durations[len(durations) // 2], 1), "max": round(durations[-1], 1),
                                       "note": "whole create including the journal fsync, the handoff and the first output; one fsync is a fraction of it"}
    finally:
        await rig.close()
        finish()
