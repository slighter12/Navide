"""Receipts on disk -> chains and pairs. Reads the files the attempt runners wrote; computes nothing about margins or outcomes (calibrate.py, bundle.py do). Pure over a directory.

Layout written by the TS runners (`tests/cli/g3Matrix.test.ts`, `g3LaunchAttempt.ts`) and read here:
* shell calibration: `<dir>/trials/warmup-python-00.json`, `<dir>/trials/trial-python-<cell>-<slot>.json` (+ `-resources.jsonl`), exactly the g1e runner's files;
* launch calibration: `<dir>/launch-trials/trial-python-<vendor>-<cell>-<slot>.json`, warm-up `warmup-python-<vendor>-<cell>.json`;
* launch matrix: `<dir>/launch-trials/trial-<mode>-<vendor>-<cell>-<repeat>.json`, warm-ups `warmup-<mode>-<vendor>-<cell>.json`;
* reader calibration: `<bundle>/reader/calibration-runs-<fixture>.json` (g1e `reader_calibration_record`).
An invalid receipt is never dropped silently: it is reported and its slot holds no value.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.cli_regression.support.g1e import assemble as g1e_assemble
from tests.cli_regression.support.g1e import calibration as cal
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g3 import bundle as g3bundle, calibrate, launch


def shell_chains(calibration_dir: Path, f0: dict) -> tuple[dict[int, list], list[dict]]:
    """{cell: ten scalar dicts in slot order (None = invalid or missing)}, plus the invalid attempts with their first problems."""
    view = g3bundle.freeze_view(f0)
    chains: dict[int, list] = {cell: [None] * cal.CHAIN_LENGTH for cell in ref.CELLS}
    invalid = []
    for path in sorted((Path(calibration_dir) / "trials").glob("trial-python-*-*.json")):
        trial = g1e_assemble.load_trial(path)
        problems = summarise.trial_problems({k: v for k, v in trial.items() if k != "_samples"}, view, "python", trial["count"])
        if problems:
            invalid.append({"file": path.name, "problems": problems[:5]})
        elif summarise.is_int(trial.get("slot")) and trial["slot"] < cal.CHAIN_LENGTH and trial["count"] in chains:
            chains[trial["count"]][trial["slot"]] = summarise.trial_scalars(trial)
    return chains, invalid


def _read(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def launch_calibration_chains(launch_dir: Path, f0: dict, freeze_sha256: str) -> tuple[dict[tuple[str, int], list], list[dict]]:
    """{(vendor, cell): ten launch times in slot order}, plus the invalid attempts."""
    chains = {key: [None] * launch.PAIRS for key in launch.chain_keys()}
    invalid = []
    for vendor, cell in launch.chain_keys():
        for slot in range(launch.PAIRS):
            path = Path(launch_dir) / "launch-trials" / f"trial-python-{vendor}-{cell}-{slot}.json"
            receipt = _read(path)
            problems = launch.receipt_problems(receipt, mode="python", vendor=vendor, cell=cell, repeat=slot, freeze_sha256=freeze_sha256, sidecar_sha256=f0["sidecarSha256"])
            if receipt is None:
                invalid.append({"file": path.name, "problems": ["missing or unreadable"]})
            elif problems or receipt.get("slot") != slot or receipt.get("kind") != "calibration":
                invalid.append({"file": path.name, "problems": (problems or ["slot or kind differs"])[:5]})
            else:
                chains[(vendor, cell)][slot] = launch.launch_ms(receipt)
    return chains, invalid


def launch_matrix_receipts(launch_dir: Path, f0: dict, freeze_sha256: str) -> tuple[dict, list[dict], list[dict]]:
    """({(mode, vendor, cell, repeat): receipt}, invalid attempts, missing attempts): only valid scored receipts are returned."""
    out, invalid, missing = {}, [], []
    for vendor, cell in launch.chain_keys():
        for repeat in range(launch.PAIRS):
            for mode in launch.ARMS:
                path = Path(launch_dir) / "launch-trials" / f"trial-{mode}-{vendor}-{cell}-{repeat}.json"
                receipt = _read(path)
                if receipt is None:
                    missing.append({"file": path.name})
                    continue
                problems = launch.receipt_problems(receipt, mode=mode, vendor=vendor, cell=cell, repeat=repeat, freeze_sha256=freeze_sha256, sidecar_sha256=f0["sidecarSha256"])
                if problems:
                    invalid.append({"file": path.name, "problems": problems[:5]})
                else:
                    out[(mode, vendor, cell, repeat)] = receipt
    return out, invalid, missing


def reader_calibration_runs(reader_dir: Path, fixtures: list[str]) -> dict[str, list[dict]]:
    return {fx: _read(Path(reader_dir) / f"calibration-runs-{fx}.json") or [] for fx in fixtures}


def build_calibration(f0: dict, *, shell_dir: Path, launch_dir: Path, reader_dir: Path, calibration_freeze_sha256: str) -> tuple[dict, dict]:
    """(calibration record for F1, evidence detail with every invalid attempt). Calibration receipts are bound to F0 (F1 does not exist yet)."""
    fixtures = f0["rowRegistry"]["readerFixtures"]["registered"]
    shell, shell_invalid = shell_chains(shell_dir, f0)
    chains, launch_invalid = launch_calibration_chains(launch_dir, f0, calibration_freeze_sha256)
    record = calibrate.build_calibration(shell, chains, reader_calibration_runs(reader_dir, fixtures), fixtures)
    return record, {"shellInvalid": shell_invalid, "launchInvalid": launch_invalid}
