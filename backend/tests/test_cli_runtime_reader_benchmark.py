"""D6 reader-benchmark tooling self-tests (E3 S14). Smoke only: one run per reader and fixture, and two calibration
runs to exercise the mechanics. The >= 10 calibration runs and the 10 measured pairs per fixture need the user's
authorization and are NOT run here."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from tests.cli_regression.support import reader_benchmark as rb
from tests.cli_regression.support import reader_fixtures as rf
from tests.cli_regression.support.frame_peer import PRODUCT

EVIDENCE = Path(__file__).resolve().parents[2] / ".scratch/agent-cli-runtime-evaluation/evidence/e3"
PINS = EVIDENCE / "reader-fixture-pins.json"
FIXTURES = EVIDENCE / "reader-fixtures.json"

needs_evidence = pytest.mark.skipif(not PINS.is_file() or not FIXTURES.is_file(), reason="the pinned fixtures are in the local evidence directory")
needs_sidecar = pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file(), reason="requires the built macOS product sidecar")


def test_the_fixtures_meet_the_size_floor_and_generation_is_deterministic(tmp_path):
    first = rf.build(tmp_path / "a")
    second = rf.build(tmp_path / "b")
    for name in rb.FIXTURES:
        assert first[name]["bytes"] >= rf.MIN_BYTES and first[name]["entries"] >= rf.MIN_ENTRIES
        assert first[name]["sha256"] == second[name]["sha256"] and first[name]["golden_sha256"] == second[name]["golden_sha256"]


@needs_evidence
def test_the_generated_fixtures_are_the_hash_pinned_ones_and_a_changed_byte_is_named(tmp_path):
    pinned = json.loads(PINS.read_text())
    built = rf.build(tmp_path)
    assert rf.verify(built, pinned) == []
    tampered = json.loads(json.dumps(built))
    tampered["opencode"]["sha256"] = "0" * 64
    assert rf.verify(tampered, pinned) == ["opencode.sha256"]
    # the evidence copies on disk still hash to their pins
    recorded = json.loads(FIXTURES.read_text())
    for name, pin in pinned.items():
        assert rf.sha256_file(Path(recorded[name]["path"])) == pin["sha256"]
        assert rf.sha256_file(Path(recorded[name]["golden"])) == pin["golden_sha256"]


def test_runs_alternate_their_order_by_index():
    assert [rb.order_for(i) for i in range(4)] == [("python", "rust"), ("rust", "python"), ("python", "rust"), ("rust", "python")]


def test_the_reader_status_preconditions_follow_e2_section_10():
    ok = {"claude": {"python": True, "rust": True}, "opencode": {"python": True, "rust": True}}
    assert rb.reader_status(ok, 2, 10, 10) == "evaluate"
    assert rb.reader_status(ok, 1, 10, 10) == "not_run"
    assert rb.reader_status(ok, 2, 9, 10) == "not_run"
    assert rb.reader_status(ok, 2, 10, 9) == "not_run"
    assert rb.reader_status({**ok, "claude": {"python": True, "rust": False}}, 2, 10, 10) == "not_run"


@needs_evidence
@needs_sidecar
@pytest.mark.parametrize("fixture", rb.FIXTURES)
@pytest.mark.parametrize("reader", rb.READERS)
def test_smoke_both_readers_equal_the_golden_list_in_a_fresh_process(tmp_path, reader, fixture):
    run = rb.run_in_fresh_process(reader, fixture, FIXTURES, PRODUCT, tmp_path)
    assert run["m0"] is True, f"{reader} on {fixture} differs from the golden list"
    assert run["events"] >= rf.MIN_ENTRIES and run["m1_wall_s"] > 0 and run["m2_cpu_s"] >= 0 and run["m3_peak_rss_increase_bytes"] >= 0
    assert (run["m2_sidecar_cpu_s"] > 0) == (reader == "rust") or reader == "python"
    import os
    assert run["pid"] != os.getpid(), "a fresh process per run"


@needs_evidence
def test_a_golden_mismatch_makes_m0_false(tmp_path):
    recorded = json.loads(FIXTURES.read_text())
    broken = json.loads(json.dumps(recorded))
    golden = json.loads(Path(recorded["claude"]["golden"]).read_text())
    golden[10][0] += 1
    broken_golden = tmp_path / "claude-golden-broken.json"
    broken_golden.write_text(json.dumps(golden))
    broken["claude"]["golden"] = str(broken_golden)
    descriptor = tmp_path / "fixtures-broken.json"
    descriptor.write_text(json.dumps(broken))
    assert rb.run_in_fresh_process("python", "claude", descriptor, Path("/nonexistent"), tmp_path)["m0"] is False


@needs_evidence
def test_the_calibration_runner_returns_frozen_statistics_from_python_only_runs(tmp_path):
    calibration = rb.calibrate("opencode", 2, FIXTURES, tmp_path)  # 2 runs: mechanics only; E2 needs >= 10, not run here
    assert calibration["runs"] == 2 and calibration["all_m0"] is True
    assert calibration["m1_median_s"] > 0 and calibration["m2_median_s"] >= 0
