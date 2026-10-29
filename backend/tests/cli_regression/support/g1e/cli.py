"""Command line of the G1-E decider.

    python -m tests.cli_regression.support.g1e.cli seal BUNDLE_DIR              write bundle-manifest.json for the files under BUNDLE_DIR, print its SHA-256
    python -m tests.cli_regression.support.g1e.cli decide BUNDLE_DIR MANIFEST_SHA256 --repo F0_TREE [--earlier RUN_DIR:RUN_MANIFEST_SHA256]... [--out outcome.json] [--report outcome.md]
        --repo is the F0 tree: the frozen locality check is RE-RUN there against F0's manifest and the bundle's allowlist (no outcome without it).
        --earlier names a sealed earlier run under this pre-registration (E2 v14-r2; the carry rule is v13 V7 item 5, unchanged); the runs given must be exactly the runs F0 names, and their stop facts are carried.

`decide` exits 0 when an outcome (continue, stop or inconclusive) was derived and printed, and exits 2 with the problems on stderr when the evidence is invalid:
in that case there is NO outcome. `seal` is the manager's step "write the receipts manifest immediately after the matrix, never regenerated"; the pinned digest
it prints is what `decide` must be given.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.cli_regression.support.g1e import bundle, decider, report


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "seal" and len(argv) == 2:
        print(bundle.write_manifest(Path(argv[1])))
        return 0
    if len(argv) >= 3 and argv[0] == "decide":
        pairs = list(zip(argv[3::2], argv[4::2]))
        options = dict(pair for pair in pairs if pair[0] != "--earlier")
        earlier = [pair[1].rsplit(":", 1) for pair in pairs if pair[0] == "--earlier"]
        if len(argv[3:]) % 2 or set(options) - {"--out", "--report", "--repo"} or any(len(e) != 2 for e in earlier):
            print(__doc__, file=sys.stderr)
            return 64
        try:
            decision = decider.decide(Path(argv[1]), argv[2], [(Path(directory), sha) for directory, sha in earlier], repo=options.get("--repo"))
        except decider.EvidenceInvalid as error:
            print("EVIDENCE INVALID: no outcome", file=sys.stderr)
            for problem in error.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        if "--out" in options:
            Path(options["--out"]).write_text(json.dumps(decision.document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        if "--report" in options:
            Path(options["--report"]).write_text(report.render(decision), encoding="utf-8")
        print(json.dumps({"outcome": decision.outcome, "reasons": decision.reasons, "facts": decision.facts}, indent=1, sort_keys=True))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
