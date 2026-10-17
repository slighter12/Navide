"""D6 reader benchmark driver (E3 S14; E2 v10 section 10, combination (a)).

Builds tooling only. It runs the existing Python log reader and the sidecar's `OBSERVE` (real frames, real
sidecar binary) over the hash-pinned fixtures, one fresh sanitized process per run, a warm file cache, order
alternating by run index, and reports per run: `m0` (normalised event stream equals the golden list), `m1` (wall
seconds to deliver the complete stream), `m2` (CPU seconds of the reader process by `cpu_times` delta; for the Rust
arm the client and the sidecar are reported separately and summed), `m3` (peak resident-memory increase).

Calibration (>= 10 Python-only runs per fixture, E2 4.5) and measured runs are NOT executed by E3: they need the
user's authorization. `calibrate` and `run_pairs` exist and are exercised only by the smoke self-tests (one run).

One run (a child process):  python -m tests.cli_regression.support.reader_benchmark run <reader> <fixture> <fixtures.json> <sidecar>
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

READERS = ("python", "rust")
FIXTURES = ("claude", "opencode")
CHILD_TIMEOUT_S = 300  # an upper bound on one child run, not a measured quantity


def _peak_rss() -> int:
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS


def _normalise(events) -> list[list[int]]:
    return [[e["input_tokens"], e["output_tokens"]] if isinstance(e, dict) else [e.input_tokens, e.output_tokens] for e in events]


def _warm(path: Path) -> None:
    with path.open("rb") as stream:
        while stream.read(1 << 20):
            pass


def _run_python(fixture: str, path: Path) -> list[list[int]]:
    if fixture == "claude":
        from agent_team_backend.cli_vendors.claude import ClaudeLogReader
        return _normalise(ClaudeLogReader().parse_incremental(path, {}).events)
    from agent_team_backend.cli_vendors.opencode import OpencodeLogReader
    return _normalise(OpencodeLogReader().parse_incremental(path, {}).events)


def _run_rust(fixture: str, path: Path, sidecar: Path, workdir: Path):
    from tests.cli_regression.support.frame_peer import FramePeer
    peer = FramePeer(workdir, binary=sidecar)
    try:
        sidecar_process = psutil.Process(peer.process.pid)
        cpu0 = sum(sidecar_process.cpu_times()[:2])
        args = {"checkpoint": {}} if fixture == "claude" else {"checkpoint": {}, "seen": []}
        document, _anchor, _final = peer.observe(fixture, path, "usage", **args)
        events = _normalise(document["events"])
        return events, sum(sidecar_process.cpu_times()[:2]) - cpu0, sidecar_process.memory_info().rss
    finally:
        peer.close()


def one_run(reader: str, fixture: str, fixtures_json: Path, sidecar: Path) -> dict:
    """Executed inside the fresh child process. Returns the run record (JSON-serialisable)."""
    descriptor = json.loads(fixtures_json.read_text())[fixture]
    path = Path(descriptor["path"])
    golden = json.loads(Path(descriptor["golden"]).read_text())
    _warm(path)
    me = psutil.Process()
    rss0, cpu0, started = me.memory_info().rss, sum(me.cpu_times()[:2]), time.perf_counter()
    sidecar_cpu, sidecar_rss = 0.0, 0
    if reader == "python":
        events = _run_python(fixture, path)
    else:
        with tempfile.TemporaryDirectory() as workdir:
            events, sidecar_cpu, sidecar_rss = _run_rust(fixture, path, sidecar, Path(workdir))
    wall = time.perf_counter() - started
    client_cpu = sum(me.cpu_times()[:2]) - cpu0
    return {"reader": reader, "fixture": fixture, "m0": events == golden, "events": len(events), "m1_wall_s": wall,
            "m2_cpu_s": client_cpu + sidecar_cpu, "m2_client_cpu_s": client_cpu, "m2_sidecar_cpu_s": sidecar_cpu,
            "m3_peak_rss_increase_bytes": max(0, _peak_rss() - rss0), "sidecar_final_rss_bytes": sidecar_rss, "pid": os.getpid()}


def child_environment(root: Path) -> dict:
    from tests.cli_regression.support.isolation import isolated_environment
    return isolated_environment(root)


def run_in_fresh_process(reader: str, fixture: str, fixtures_json: Path, sidecar: Path, root: Path) -> dict:
    backend = Path(__file__).resolve().parents[3]
    env = {**child_environment(root), "PYTHONPATH": str(backend), "NAVIDE_REGRESSION_ROOT": str(root)}
    completed = subprocess.run(
        [sys.executable, "-m", "tests.cli_regression.support.reader_benchmark", "run", reader, fixture, str(fixtures_json), str(sidecar)],
        capture_output=True, text=True, cwd=backend, env=env, timeout=CHILD_TIMEOUT_S, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"benchmark child failed ({completed.returncode}): {completed.stderr[-400:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def order_for(index: int) -> tuple[str, str]:
    """Alternating order by run index: even runs Python first, odd runs Rust first."""
    return ("python", "rust") if index % 2 == 0 else ("rust", "python")


def run_pairs(fixture: str, runs: int, fixtures_json: Path, sidecar: Path, root: Path) -> list[dict]:
    """`runs` paired runs of one fixture (a pair = the same run index), each reader in its own fresh process."""
    pairs = []
    for index in range(runs):
        pair = {"run": index, "order": order_for(index)}
        for reader in pair["order"]:
            pair[reader] = run_in_fresh_process(reader, fixture, fixtures_json, sidecar, root)
        pairs.append(pair)
    return pairs


def calibrate(fixture: str, runs: int, fixtures_json: Path, root: Path) -> dict:
    """Python-only calibration runs of the existing reader (>= 10 required by E2 4.5); returns the frozen statistics."""
    records = [run_in_fresh_process("python", fixture, fixtures_json, Path("/nonexistent"), root) for _ in range(runs)]
    return {"fixture": fixture, "runs": runs, "m1_median_s": statistics.median(r["m1_wall_s"] for r in records),
            "m2_median_s": statistics.median(r["m2_cpu_s"] for r in records),
            "m3_median_bytes": statistics.median(r["m3_peak_rss_increase_bytes"] for r in records), "all_m0": all(r["m0"] for r in records)}


def reader_status(m0: dict[str, dict[str, bool]], fixtures: int, calibration_runs: int, measured_runs: int) -> str:
    """The `not_run` preconditions of E2 10 that the driver can decide (row verdicts are the decider's, not the driver's)."""
    if fixtures < 2 or calibration_runs < 10 or measured_runs < 10:
        return "not_run"
    if any(not value for fixture in m0.values() for value in fixture.values()):
        return "not_run"
    return "evaluate"


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "run":
        print(json.dumps(one_run(sys.argv[2], sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5]))))
    else:
        raise SystemExit(__doc__)
