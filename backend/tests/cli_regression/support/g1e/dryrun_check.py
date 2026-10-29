"""Does the Python decider accept the receipts of a dry-run attempt, and reject a damaged copy? (E4 assignment 2: the end-to-end proof of the runner's output.)

A dry-run receipt is labelled `kind: dryrun`, `measuring: false` and is never admitted to a matrix (`summarise.load_matrix` refuses a non-measured receipt); this module applies the decider's
OWN recomputed per-trial checks (`structure_problems`, `consistency_problems`, `trial_problems`) to it, which do not look at `measuring`. No outcome is derived.

    python -m tests.cli_regression.support.g1e.dryrun_check DRYRUN_DIR [--damage NAME]
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e.bundle import Bundle, write_manifest

DAMAGES = {
    "ledger-byte": lambda t: t["ledgers"][0]["close"].update(read_total=t["ledgers"][0]["close"]["read_total"] + 1),
    "window-digest": lambda t: t["ledgers"][0]["stream"]["windows"][0].update(digest="0" * 64),
    "marker-twice": lambda t: t["ledgers"][0]["stream"].update(markerCount=2),
    "log-held": lambda t: t["logFinals"][0]["inventory"].update(backend=True),
    "log-unstable": lambda t: t["logFinals"][0]["reads"][1].update(sha256="f" * 64),
    "isolation": lambda t: t["backendEnd"].update(pid=t["backend"]["pid"] + 1),
    "ownership": lambda t: (t["proof"].__setitem__("ownershipFailure", True), t["proof"].__setitem__("ok", False)),
    "producer-flag": lambda t: t.update(evidenceValid=False),
    "toolchain-observed-node": lambda t: t["toolchain"]["observed"]["node"].update(sha256="0" * 64),
    "toolchain-sidecar": lambda t: t["toolchain"]["observed"]["sidecar"].update(sha256="0" * 64),
    "toolchain-record-missing": lambda t: t.pop("toolchain"),
    "ck-missing": lambda t: t.update(ckProof=None),
    "burst-endpoint": lambda t: t["burstChecks"][0].update(kind="invalid-semantic"),
    "control-ledger-type": lambda t: (t["controlLedger"] or {"tx": {}})["tx"].update(OUTPUT=1) if t["controlLedger"] else t.update(controlLedger={"tx": {"CREATE": 1}, "rx": {}, "unknownTypes": [], "controlBytes": 1, "streamBytes": 1}),
}


def check(directory: Path, damage: str | None = None) -> dict[str, list[str]]:
    freeze = json.loads((directory / "dryrun-freeze.json").read_text())
    temp = Path(tempfile.mkdtemp(prefix="g1e-dryrun-check-"))
    try:
        (temp / "trials").mkdir()
        shutil.copyfile(directory / "dryrun-freeze.json", temp / "freeze.json")
        for path in (directory / "trials").iterdir():
            shutil.copyfile(path, temp / "trials" / path.name)
        sha = write_manifest(temp)
        bundle = Bundle(temp, sha)
        results: dict[str, list[str]] = {}
        for path in sorted((temp / "trials").glob("trial-*.json")):
            if path.name.endswith("-resources.jsonl"):
                continue
            trial = json.loads(path.read_text())
            if damage:
                DAMAGES[damage](trial)
            resources = f"trials/{path.name[:-len('.json')]}-resources.jsonl"
            shape, _samples = summarise.structure_problems(trial, freeze, bundle, resources)
            problems = list(shape)
            if not shape:
                problems += summarise.consistency_problems(trial)
            if not problems:
                problems += summarise.trial_problems(trial, freeze, trial["selectedRuntime"], trial["count"])
            results[path.name] = problems
        return results
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main(argv: list[str]) -> int:
    directory = Path(argv[0])
    damage = argv[2] if len(argv) == 3 and argv[1] == "--damage" else None
    results = check(directory, damage)
    print(json.dumps(results, indent=1))
    clean = all(not problems for problems in results.values())
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
