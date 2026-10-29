"""The child of one hosted reader run (rehearsal 3, L-1). It is `reader_benchmark`'s own child (`python -m ...reader_benchmark run READER FIXTURE FIXTURES SIDECAR`) with one difference: the Rust arm on the `claude`
fixture asks for the whole stream the way the product delivers it, in passes.

Since product commit `329bfc75d` the sidecar charges every event, together with its final cursor, to one answer ledger (`observe/answer.rs`, `LEDGER_BYTES` = 3,637,248). The generated `claude` fixture has
2,400 usage events, which do not fit one answer, so the sidecar delivers them over several answers: a pass that stopped before the end of the readable log says `more` (`observe/jsonl.rs`: `run.answer.set("more", true)`:
"the caller asks again"), and the next request resumes from the checkpoint that pass left. `reader_benchmark._run_rust` asks once and takes the first answer, so its `events` were never the golden list.

Here the Rust arm asks again, from each pass's checkpoint, until an answer carries no `more`. `m0` is still strict equality with the golden list, nothing is filtered or retried, and the extra round trips are part
of the run's wall time and CPU (`m1`, `m2`): the benchmark's `m1` is "wall seconds to deliver the complete stream". The Python arm and the `opencode` Rust arm (one pass, `seen` instead of a checkpoint) call the
unchanged `reader_benchmark` code. `PASS_BOUND` is the bound the repo's own test uses (`test_cli_runtime_reader_benchmark.py`, 50 passes) for a stream that needs 2 or 3; a stream still saying `more` after
the bound raises (the child exits non-zero and the phase ends), it is never returned as a short stream, so the bound cannot hide a defect.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import psutil

from tests.cli_regression.support import reader_benchmark as rb

PASS_BOUND = 50
_ORIGINAL_RUN_RUST = rb._run_rust


def run_rust(fixture: str, path: Path, sidecar: Path, workdir: Path):
    """`reader_benchmark._run_rust` for the `claude` fixture over every pass the answer ledger needs; the same return value (events, the sidecar's CPU seconds, its resident set at the end)."""
    if fixture != "claude":
        return _ORIGINAL_RUN_RUST(fixture, path, sidecar, workdir)
    from tests.cli_regression.support.frame_peer import FramePeer
    peer = FramePeer(workdir, binary=sidecar)
    try:
        sidecar_process = psutil.Process(peer.process.pid)
        cpu0 = sum(sidecar_process.cpu_times()[:2])
        events, checkpoint, passes = [], {}, 0
        while True:
            if passes == PASS_BOUND:
                raise RuntimeError(f"the sidecar still had more to deliver after {PASS_BOUND} passes")
            document, _final = peer.observe(fixture, path, "usage", checkpoint=checkpoint)
            events += rb._normalise(document["events"])
            checkpoint, passes = document["checkpoint"], passes + 1
            if not document.get("more"):
                break
        return events, sum(sidecar_process.cpu_times()[:2]) - cpu0, sidecar_process.memory_info().rss
    finally:
        peer.close()


def one_run(reader: str, fixture: str, fixtures_json: Path, sidecar: Path) -> dict:
    """`reader_benchmark.one_run` with `run_rust` as the Rust arm."""
    rb._run_rust = run_rust
    return rb.one_run(reader, fixture, fixtures_json, sidecar)


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "run":
        print(json.dumps(one_run(sys.argv[2], sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5]))))
    else:
        raise SystemExit(__doc__)
