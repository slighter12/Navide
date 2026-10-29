"""The quiet machine as a measured gate (E2 v14-r2 V14). A pure decision over load samples plus the runner that takes them.

    python -m tests.cli_regression.support.g1e.quiet_gate before DIR   polls `os.getloadavg()` every 30 s and writes `DIR/quiet-NN.json`; exit 0 on PASS, 3 on TIMEOUT (nothing is started then)
    python -m tests.cli_regression.support.g1e.quiet_gate after DIR    writes ONE sample `DIR/load-after-NN.json` (information only: it never changes a decision)

The gate passes at the first moment `load1 <= 4.0` and `load5 <= 4.0` hold on two consecutive polls (`load15` is not consulted); no pass within 120 minutes is a TIMEOUT. Receipts are numbered and
created exclusively: an existing receipt is never overwritten. The script is an F0 frozen input. Clock, sleep and the load reader are injected so the tests never wait.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

LIMIT = 4.0
CONSECUTIVE_POLLS = 2
INTERVAL_S = 30
MAX_WAIT_S = 120 * 60
RULE = "load1 <= 4.0 and load5 <= 4.0 on 2 consecutive polls, every 30 s, for at most 120 minutes"
QUIET_SCHEMA = "acr-g1e-quiet/1"
AFTER_SCHEMA = "acr-g1e-load-after/1"


def _quiet(poll: tuple) -> bool:
    return poll[0] <= LIMIT and poll[1] <= LIMIT


def quiet_run(polls: list) -> int:
    """How many polls at the END of `polls` are quiet in a row."""
    run = 0
    for poll in reversed(polls):
        if not _quiet(poll):
            break
        run += 1
    return run


def first_pass_index(polls: list) -> int | None:
    """The index of the poll at which the gate first passes (the second of the first two consecutive quiet polls), or None."""
    run = 0
    for index, poll in enumerate(polls):
        run = run + 1 if _quiet(poll) else 0
        if run >= CONSECUTIVE_POLLS:
            return index
    return None


def verdict(polls: list, elapsed_s: float) -> str:
    """PASS / WAIT / TIMEOUT over the polls so far and the seconds since the first poll. A pass at the deadline still passes: the last poll decides before the deadline does."""
    if quiet_run(polls) >= CONSECUTIVE_POLLS:
        return "PASS"
    return "TIMEOUT" if elapsed_s >= MAX_WAIT_S else "WAIT"


def write_numbered(directory: Path | str, prefix: str, obj: object, number: int | None = None) -> Path:
    """`<prefix>-NN.json` created EXCLUSIVELY. An explicit `number` that is taken raises `FileExistsError`; otherwise the first unused number is taken."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=1, sort_keys=True) + "\n"
    candidate = number if number is not None else 1
    while True:
        path = directory / f"{prefix}-{candidate:02d}.json"
        try:
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(raw)
            return path
        except FileExistsError:
            if number is not None:
                raise
            candidate += 1


def run_gate(directory: Path | str, *, getloadavg: Callable[[], tuple] = os.getloadavg, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             wall: Callable[[], float] = time.time, ncpu: Callable[[], int | None] = os.cpu_count) -> dict:
    """Polls at t = 0, 30, 60, ... until PASS or TIMEOUT, then writes the numbered `quiet-NN.json` and returns it. A load reader that raises writes nothing and is not a pass."""
    start = clock()
    polls: list[dict] = []
    raw: list[tuple] = []
    while True:
        sample = tuple(float(x) for x in getloadavg())
        raw.append(sample)
        polls.append({"at": wall(), "load1": sample[0], "load5": sample[1], "load15": sample[2]})
        state = verdict(raw, clock() - start)
        if state != "WAIT":
            break
        sleep(INTERVAL_S)
    receipt = {"schema": QUIET_SCHEMA, "rule": RULE, "limit": LIMIT, "intervalS": INTERVAL_S, "maxWaitS": MAX_WAIT_S, "consecutive": CONSECUTIVE_POLLS, "verdict": state, "polls": polls, "ncpu": ncpu()}
    write_numbered(directory, "quiet", receipt)
    return receipt


def write_load_after(directory: Path | str, *, getloadavg: Callable[[], tuple] = os.getloadavg, wall: Callable[[], float] = time.time,
                     ncpu: Callable[[], int | None] = os.cpu_count) -> dict:
    """One load sample after the run, numbered like the gate receipts. Information only."""
    load1, load5, load15 = (float(x) for x in getloadavg())
    record = {"schema": AFTER_SCHEMA, "at": wall(), "load1": load1, "load5": load5, "load15": load15, "ncpu": ncpu()}
    write_numbered(directory, "load-after", record)
    return record


def main(argv: list[str], **injected) -> int:
    if len(argv) == 2 and argv[0] == "before":
        receipt = run_gate(argv[1], **injected)
        print(json.dumps(receipt))
        return 0 if receipt["verdict"] == "PASS" else 3
    if len(argv) == 2 and argv[0] == "after":
        write_load_after(argv[1], **{k: v for k, v in injected.items() if k in ("getloadavg", "wall", "ncpu")})
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
