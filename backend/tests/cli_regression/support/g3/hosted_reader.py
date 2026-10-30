"""The reader rows each hosted platform registers (addendum D): `claude` only on Linux, none on Windows. Everything downstream of the F0 registry (calibration, F1, bundle, decide) follows F0's
`rowRegistry.readerFixtures.registered`, so the one registry seam is `rows.reader_fixtures`; the G1-E reader runners hard-code both fixtures, so on Linux their fixture tuple and the `reader.json`
builder are replaced too. Margins, formulas and counts are the carried ones; only the fixture list differs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

from tests.cli_regression.support import reader_benchmark as rb, reader_fixtures as rf
from tests.cli_regression.support.g1e import assemble as g1e_assemble, calibration
from tests.cli_regression.support.g3 import fixtures as g3_fixtures, hosted_markers, hosted_rss, rows

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hosted_site")
READERS = {"Linux": ("claude",), "Windows": ()}
EMPTY_READER_RECORD = {"fixtures": [], "goldenEvents": {}, "calibrationRuns": 0, "runs": 0, "findings": [], "m0": {}, "rows": {}}
_ORIGINAL_FIXTURES = rows.reader_fixtures
_ORIGINAL_REGISTERED = dict(g3_fixtures.REGISTERED_SHA256)


def fixtures_for(system: str, original: Callable[..., dict] = _ORIGINAL_FIXTURES) -> Callable[..., dict]:
    """`rows.reader_fixtures` for `system`: the unchanged answer restricted to the platform's readers (an extra codex fixture is not applicable on a hosted class either)."""
    keep = READERS[system]

    def hosted(extra: list | None = None) -> dict:
        base = original(extra)
        registered = [fixture for fixture in base["registered"] if fixture in keep]
        return {**base, "registered": registered, "fixtures": [record for record in base["fixtures"] if record.get("name") in registered]}
    return hosted


def reader_record(names: tuple[str, ...], fixtures: list[dict], golden: dict, margins_ref: dict, margins: dict[str, dict[str, float]], calibration_runs: int, pairs: dict[str, list[dict]]) -> dict:
    """`calibration.reader_record` over `names` instead of the fixed claude + opencode pair (identical for both names)."""
    pick = {"m1_wall_ms": lambda run: run["m1_wall_s"] * 1000.0, "m2_cpu_s": lambda run: run["m2_cpu_s"], "m3_peak_rss_mib": lambda run: run["m3_peak_rss_increase_bytes"] / 1048576}
    out_rows: dict = {}
    m0 = {fx: {"python": True, "rust": True} for fx in names}
    for fixture in names:
        for pair in pairs[fixture]:
            m0[fixture]["python"] = m0[fixture]["python"] and bool(pair["python"]["m0"])
            m0[fixture]["rust"] = m0[fixture]["rust"] and bool(pair["rust"]["m0"])
        for metric, unit in calibration.READER_UNITS.items():
            rid = f"reader|{metric}|{fixture}"
            out_rows[rid] = {"id": rid, "unit": unit, "margin": {"mode": "absolute", "value": margins[fixture][metric], "frozenIn": margins_ref},
                             "pairs": [{"repeat": i, "python": pick[metric](p["python"]), "rust": pick[metric](p["rust"]), "valid": bool(p["python"]["m0"] and p["rust"]["m0"])} for i, p in enumerate(pairs[fixture])]}
    runs = min(len(pairs[fixture]) for fixture in names) if names else 0
    return {"fixtures": fixtures, "goldenEvents": golden, "calibrationRuns": calibration_runs, "runs": runs, "findings": [], "m0": m0, "rows": out_rows}


def measure_record_for(names: tuple[str, ...]) -> Callable[..., dict]:
    """`g1e.assemble.reader_measure_record` for the platform's fixtures: the same files and hash references, the fixtures of `names` only."""
    def measure_record(descriptors: dict, pairs: dict[str, list[dict]], margins: dict, calibration_runs: int, bundle: Path) -> dict:
        fixtures = [g1e_assemble._copy_ref(Path(descriptors[name]["path"]), bundle, f"reader/fixture-{name}.jsonl") for name in names]
        golden = {name: json.loads(Path(descriptors[name]["golden"]).read_text()) for name in names}
        (bundle / "reader").mkdir(parents=True, exist_ok=True)
        (bundle / "reader" / "golden.json").write_text(json.dumps(golden, separators=(",", ":")))
        golden_ref = g1e_assemble.hash_ref(bundle, "reader/golden.json")
        for fixture, records in pairs.items():
            (bundle / "reader" / f"runs-{fixture}.json").write_text(json.dumps(records, indent=1, sort_keys=True) + "\n")
        record = reader_record(names, fixtures, golden_ref, g1e_assemble.hash_ref(bundle, "reader/calibration-margins.json"), margins, calibration_runs, pairs)
        (bundle / "reader.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        return record
    return measure_record


def fresh_process_runner(site_dir: str = SITE_DIR) -> Callable[..., dict]:
    """`reader_benchmark.run_in_fresh_process` for Linux: the same child command, working directory, timeout and rebuilt environment, plus what the child needs to see the hosted unit seam. The child's
    `PYTHONPATH` is reset to `backend/` by the original, so the parent's seams never reach it; here it names the hosted site first (its `sitecustomize` installs `hosted_rss` and writes the
    `reader-rss-bytes` marker), sets `ACR_G3_HOSTED_RSS_BYTES=1`, and keeps the marker directory that `isolated_environment` would drop. Unchanged on every other point (a test compares the two)."""
    def run_in_fresh_process(reader: str, fixture: str, fixtures_json: Path, sidecar: Path, root: Path) -> dict:
        backend = Path(rb.__file__).resolve().parents[3]
        env = {**rb.child_environment(root), "PYTHONPATH": os.pathsep.join((site_dir, str(backend))), "NAVIDE_REGRESSION_ROOT": str(root), hosted_rss.ENV: "1"}
        if os.environ.get(hosted_markers.ENV):
            env[hosted_markers.ENV] = os.environ[hosted_markers.ENV]
        completed = rb.subprocess.run(
            [sys.executable, "-m", "tests.cli_regression.support.reader_benchmark", "run", reader, fixture, str(fixtures_json), str(sidecar)],
            capture_output=True, text=True, cwd=backend, env=env, timeout=rb.CHILD_TIMEOUT_S, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"benchmark child failed ({completed.returncode}): {completed.stderr[-400:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])
    return run_in_fresh_process


def registered_for(names: tuple[str, ...], original: dict[str, str] = _ORIGINAL_REGISTERED) -> dict[str, str]:
    """`g3.fixtures.REGISTERED_SHA256` restricted to `names`: the same registered digests, only the fixtures the platform registers (`descriptor_problems` and `f0_record` read it at call time)."""
    return {name: digest for name, digest in original.items() if name in names}


def build_for(names: tuple[str, ...]) -> Callable[[Path], dict]:
    """`reader_fixtures.build` for `names`: the unchanged generators and descriptor entries, only the fixtures of `names`. The opencode database header (bytes 96-99) records the SQLite library
    version of the machine that wrote it, so its digest is the registered one on the registering host only; a Linux runner registers and builds the claude fixture and never writes the database."""
    generators = {"claude": rf.generate_claude, "opencode": rf.generate_opencode}

    def build(directory: Path) -> dict:
        out = {}
        for name in names:
            path, golden = generators[name](directory / name)
            golden_path = directory / f"{name}-golden.json"
            golden_path.write_text(json.dumps(golden, separators=(",", ":")))
            out[name] = {"path": str(path), "golden": str(golden_path), "bytes": path.stat().st_size, "entries": len(golden), "sha256": rf.sha256_file(path), "golden_sha256": rf.sha256_file(golden_path)}
            assert out[name]["bytes"] >= rf.MIN_BYTES and out[name]["entries"] >= rf.MIN_ENTRIES, out[name]
        return out
    return build


def targets(system: str) -> list[tuple[object, str, object]]:
    """(module, attribute, replacement) for `hosted_seams`: the registry on every platform, the G1-E reader runners (and the Linux unit seam of their fresh child) where there are readers to run,
    and the reader fixture registration and builder of `g3.fixtures` for the same readers."""
    out: list[tuple[object, str, object]] = [(rows, "reader_fixtures", fixtures_for(system))]
    if READERS[system]:
        out += [(rb, "FIXTURES", READERS[system]), (g1e_assemble, "reader_measure_record", measure_record_for(READERS[system])), (rb, "run_in_fresh_process", fresh_process_runner()),
                (g3_fixtures, "REGISTERED_SHA256", registered_for(READERS[system])), (rf, "build", build_for(READERS[system]))]
    return out


def ensure_reader_record(bundle: Path, registered: list[str]) -> bool:
    """A registry without readers (Windows) has no `reader.json` to copy, yet the unchanged rehearsal admission reads one: write the valid empty record, once, if none exists. True = written."""
    path = Path(bundle) / "reader.json"
    if registered or path.is_file():
        return False
    path.write_text(json.dumps(EMPTY_READER_RECORD, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True
