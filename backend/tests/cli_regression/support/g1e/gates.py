"""The 21 required correctness rows as runnable gates (E2 v10 section 7): command registry, runner and receipt writer.

Every row must be `pass-current` AT THE E4 HEAD AND SIDECAR HASH: a row's receipt (`acr-g1e-gate/1`) carries the HEAD, the sidecar hash, the exact commands and
their exit codes; the decider accepts `pass-current` only from such a receipt bound to this row, the frozen HEAD and the frozen sidecar, whose commands all exited 0
(`decider.gate_receipt_ok`). The runner runs commands BARE (no pipe, so the exit code is the command's own), records the exit code of each, and never retries:
a non-zero exit makes the row `failed` WITHOUT an attribution (attribution is a reviewer's act; absent attribution reads as the worst case, STOP S3, by the frozen rule).
`pass-accepted-older` does not exist (E2 v10 section 7). A row that cannot be run in this environment is `not-rerun` with the reason, never `failed` and never `pass-current`.

Rows whose proof is the matrix itself (`E-cleanup-no-difference`, `E-ordering-terminal-stream`) and the reader-benchmark row are DERIVED from the recomputed trial
receipts / the reader record by `derived_rows()`, in addition to their E1 tests.

KNOWN GAPS of the registry (recorded in `evidence/e4/e4-harness-handoff.md`): the two ticket-04 principals need the ticket-04 principal controller
(`NAVIDE_T04_WRITER_BINDING_DIR`; `tests/cli/nativeDifferences.test.ts` skips without it); "the 05 retained test" of three rows is mapped to the nearest existing
backend files (see `NOTES`), which the manager must confirm.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

BACKEND = ["uv", "--project", "backend", "run", "pytest"]
VITEST = ["pnpm", "test:run"]
CARGO = ["cargo", "test", "--manifest-path", "native/navide-cli-runtime/Cargo.toml", "--offline", "--locked"]

PY = lambda *names: [BACKEND + [f"backend/tests/{n}" for n in names]]
TS = lambda *names: [VITEST + [f"tests/cli/{n}" for n in names]]

#: row id -> list of commands (argv). The E1 ids a row binds are those of E2 v10 section 7; the files come from `evidence/e3/e3-e1-test-map.md`.
REGISTRY: dict[str, list[list[str]]] = {
    "02-shell-runtime": TS("shellRuntime.test.ts") + PY("test_cli_runtime_sidecar.py", "test_cli_runtime_handoff.py"),
    "02-bounds-failures": [CARGO] + PY("test_cli_runtime_frames.py", "test_cli_runtime_bridge.py", "test_cli_runtime_limits_report.py"),
    "03-claude-reference": TS("claudeRuntime.test.ts") + PY("test_cli_runtime_claude.py", "test_cli_runtime_claude_parity.py", "test_cli_runtime_observe.py"),
    "04-codex-principal": TS("nativeDifferences.test.ts"),
    "04-opencode-principal": TS("nativeDifferences.test.ts"),
    "05-cancel-cleanup-recovery": TS("runtimeRecovery.test.ts", "createCancelOwnership.test.ts", "rebuildOwnership.test.ts", "retirementRule.test.ts") + PY("test_cli_runtime_recovery.py", "test_terminal_create_cancellation.py"),
    "E-cleanup-no-difference": PY("test_cli_runtime_sidecar.py"),
    "E-failure-isolation": TS("runtimeRecovery.test.ts") + PY("test_cli_runtime_lifecycle.py", "test_cli_runtime_review1.py"),
    "E-sidecar-loss-uniform": TS("runtimeRecovery.test.ts") + PY("test_cli_runtime_lifecycle.py", "test_cli_runtime_review2.py", "test_cli_runtime_review3.py"),
    "E-handoff-binding": TS("shellRuntime.test.ts") + PY("test_cli_runtime_handoff.py", "test_cli_runtime_review1.py"),
    "E-ordering-terminal-stream": TS("shellRuntime.test.ts") + PY("test_cli_runtime_handoff.py", "test_g1e_peer.py"),
    "E-bounded-reader-backpressure": PY("test_terminals_reader_pause_bound.py", "test_cli_runtime_lifecycle.py"),
    "E-drop-oldest-integrity": PY("test_cli_runtime_receipts.py", "test_cli_runtime_f1.py"),
    "E-output-split-64k": PY("test_terminals_adaptive_flush.py", "test_cli_runtime_f1.py"),
    "E-log-replay-identity": PY("test_reattach_output_log.py", "test_cli_runtime_review2.py"),
    "E-cancel-handoff-no-leak": TS("createCancelOwnership.test.ts", "runtimeRecovery.test.ts") + PY("test_cli_runtime_handoff.py", "test_cli_runtime_review1.py", "test_cli_runtime_review2.py", "test_cli_runtime_sidecar.py"),
    "E-fd-hygiene": [CARGO] + PY("test_cli_runtime_f1.py", "test_cli_runtime_handoff.py", "test_cli_runtime_limits_report.py"),
    "E-stale-generation": PY("test_cli_runtime_lifecycle.py", "test_cli_runtime_review1.py"),
    "E-resize-baseline-ordering": TS("shellRuntime.test.ts") + PY("test_cli_runtime_lifecycle.py", "test_g1e_reference.py"),
    "E-ordinary-shell": TS("shellRuntime.test.ts") + PY("test_cli_runtime_observe.py"),
    "E-observation-equality": TS("claudeRuntime.test.ts") + PY("test_cli_runtime_observe.py", "test_cli_runtime_reader_benchmark.py"),
}
NOTES = {
    "E-drop-oldest-integrity": "the 'ticket-05 retained test' is not named by E2/E3; mapped to the byte-ledger receipt tests that exercise drop-oldest (`test_cli_runtime_receipts.py`, `test_cli_runtime_f1.py`)",
    "E-output-split-64k": "the 'ticket-05 retained test' is not named by E2/E3; mapped to `test_terminals_adaptive_flush.py` and the F1 ledger tests",
    "E-bounded-reader-backpressure": "the 'ticket-05 retained test' is mapped to `test_terminals_reader_pause_bound.py`",
    "04-codex-principal": "requires the ticket-04 principal controller (NAVIDE_T04_WRITER_BINDING_DIR); without it the test file skips, so the row is not-rerun",
    "04-opencode-principal": "requires the ticket-04 principal controller (NAVIDE_T04_WRITER_BINDING_DIR); without it the test file skips, so the row is not-rerun",
}
REQUIRES_ENV = {"04-codex-principal": "NAVIDE_T04_WRITER_BINDING_DIR", "04-opencode-principal": "NAVIDE_T04_WRITER_BINDING_DIR"}
NOT_REQUIRED_WITHOUT_READER = "E-observation-equality"


UNKNOWN_UV = {"realPath": None, "version": None, "sha256": None}


def observe_uv() -> dict:
    """uv's identity now (E2 v13 V8): real path behind the shim, `uv --version`, SHA-256. Informational: a failure to observe is recorded as unknown, never raised."""
    from tests.cli_regression.support.g1e import toolchain as tc
    try:
        return tc.observe_uv(tc.default_runner)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return dict(UNKNOWN_UV)


def uv_record(start: dict, end: dict) -> dict:
    """`gates/uv.json`: uv's identity at the start and at the end of the gate run, and whether they differ. A difference is STATED in the report and changes no gate result (V8)."""
    return {"schema": "acr-g1e-uv/1", "start": start, "end": end, "differs": start != end, "informationalOnly": True}


def run_gates(root: Path, out: Path, head: str, sidecar_sha256: str, *, reader_required: bool, rows: list[str] | None = None,
              runner: Callable[..., subprocess.CompletedProcess] = subprocess.run, env: dict[str, str] | None = None,
              uv_observer: Callable[[], dict] = observe_uv) -> dict:
    """Run the commands of every row (or `rows`), write `gates/<row>.json` receipts and per-command logs under `out`, and return the `gates.json` content.
    Commands run bare; the exit code is the command's own. Nothing is retried; a row's commands stop at the first non-zero exit (the row is then `failed`).
    uv's identity is recorded at the start and at the end into `gates/uv.json` (V8): it never enters a row, a receipt or a result."""
    environment = dict(os.environ if env is None else env)
    result: dict = {}
    (out / "gates").mkdir(parents=True, exist_ok=True)
    uv_start = uv_observer()
    for row, commands in REGISTRY.items():
        if rows is not None and row not in rows:
            continue
        if row == NOT_REQUIRED_WITHOUT_READER and not reader_required:
            result[row] = {"status": "not-required"}
            continue
        needed = REQUIRES_ENV.get(row)
        if needed and not environment.get(needed):
            result[row] = {"status": "not-rerun", "note": NOTES[row]}
            continue
        executed, failed = [], False
        for index, argv in enumerate(commands):
            done = runner(argv, cwd=root, env=environment, capture_output=True, text=True)
            log = out / "gates" / f"{row}-{index}.log"
            log.write_text((done.stdout or "") + (done.stderr or ""), encoding="utf-8")
            executed.append({"argv": argv, "exit": int(done.returncode)})
            if done.returncode != 0:
                failed = True
                break
        receipt = {"schema": "acr-g1e-gate/1", "row": row, "head": head, "sidecarSha256": sidecar_sha256, "commands": executed, "result": "fail" if failed else "pass"}
        path = out / "gates" / f"{row}.json"
        raw = json.dumps(receipt, indent=1, sort_keys=True).encode("utf-8") + b"\n"
        path.write_bytes(raw)
        if failed:
            result[row] = {"status": "failed", "note": f"{executed[-1]['argv'][-1]} exited {executed[-1]['exit']}"}   # attribution is left to the reviewers: absent = worst case
        else:
            result[row] = {"status": "pass-current", "receipt": {"path": f"gates/{row}.json", "sha256": hashlib.sha256(raw).hexdigest()}}
    (out / "gates" / "uv.json").write_text(json.dumps(uv_record(uv_start, uv_observer()), indent=1, sort_keys=True) + "\n")
    return {"schema": "acr-g1e-gates/1", "rows": result}


def derived_rows(matrix: dict) -> dict[str, dict]:
    """Matrix-derived facts for the two rows whose proof is the matrix: {row: {ok, why}} from the recomputed trial validity (summarise.load_matrix output)."""
    out = {}
    trials = matrix["trials"]
    cleanup_ok = len(trials) == 40 and all((t.get("phaseOutcomes") or {}).get("cleanup") == "passed" for t in trials.values())
    rust = [t for (arm, _c, _r), t in trials.items() if arm == "rust-shell"]
    owned_eq = all(isinstance(t.get("proof"), dict) and not (t["proof"].get("ownershipFailure") or any((t["proof"].get("ownership") or {}).values())) for t in rust)
    out["E-cleanup-no-difference"] = {"ok": cleanup_ok and owned_eq and not matrix["missing"], "why": "40/40 cleanup passed and owned = cleaned with no survivors in every Rust trial"}
    ledgers_ok = not matrix["invalid"] and not matrix["missing"] and all(not p for p in matrix["problems"].values())
    out["E-ordering-terminal-stream"] = {"ok": ledgers_ok, "why": "every trial's byte ledger and burst endpoint checks recomputed clean"}
    return out


def main(argv: list[str]) -> int:
    """`gates.py run ROOT OUT HEAD SIDECAR_SHA256 [--reader-required] [--row ID ...]`: runs every row's commands bare, writes `OUT/gates/*.json` and `OUT/gates.json`; exits 0 iff the file was written."""
    import sys
    if len(argv) < 5 or argv[0] != "run":
        print(main.__doc__, file=sys.stderr)
        return 64
    root, out, head, sidecar, *rest = argv[1:]
    rows = [rest[i + 1] for i, word in enumerate(rest) if word == "--row"] or None
    record = run_gates(Path(root), Path(out), head, sidecar, reader_required="--reader-required" in rest, rows=rows, runner=lambda *a, **k: subprocess.run(*a, **k),
                         uv_observer=lambda: observe_uv())
    (Path(out) / "gates.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({row: entry["status"] for row, entry in record["rows"].items()}, indent=1))
    uv = json.loads((Path(out) / "gates" / "uv.json").read_text())
    if uv["differs"]:
        print("NOTE uv's identity differs between the start and the end of the gate run (informational; no gate result changed): see gates/uv.json")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
