"""Information-only record around the macOS `Rust tests` step (rh12, M-1 (B)). It decides nothing, changes no rule and never fails a job.

    python -B hosted_timer_probe.py before|after OUT_JSON

Rehearsal 2 and rehearsal 3 each lost one frozen `scan_board` timing test to a sleep that took far longer than asked (a 2 ms sleep took at least 20 ms; a 20 ms sleep took about 180 ms more than asked,
inferred), and neither run recorded the load or the timer around the step, so the cause of the stall was never observed. The workflow runs this once right before the step (`before`) and once right
after it, also when the step failed (`after`). Each run writes one document: one load sample (`load1 / load5 / load15`, the core count and the top five processes by CPU, command names only, no arguments,
no path: `hosted_load_probe.take`) and a timer-latency sample: for each requested duration (2 ms and 20 ms, the two sleeps of the tests that failed), `count` sleeps, each recorded as requested against
actual milliseconds, with the median and the largest overshoot. The file is written once, to `OUT_JSON.tmp` and renamed over `OUT_JSON`; the exit code is 0 whatever happens.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import hosted_load_probe as load_probe

SCHEMA = "acr-g3-timer-probe/1"
LABELS = ("before", "after")
#: rh16 (macos-2): 0.5 s and 1.2 s are the sleeps of the correctness step's tight Python cases (`test_cli_runtime_fast_exit_output.py` lines 59 and 106), so the record shows the overshoot at those durations too.
REQUESTED_S = (0.002, 0.020, 0.5, 1.2)
COUNT = 50
#: the long sleeps take 10 samples each (5 s and 12 s), so the whole record stays near 20 s; a caller that passes its own `count` gets it for every duration.
LONG_COUNTS = {0.5: 10, 1.2: 10}


def latency(requested_s: float, count: int = COUNT, *, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.perf_counter) -> dict:
    """`count` sleeps of `requested_s` seconds: each one's actual duration in milliseconds, the median and the largest overshoot over the requested duration."""
    actual = []
    for _ in range(count):
        started = clock()
        sleep(requested_s)
        actual.append((clock() - started) * 1000.0)
    requested_ms = requested_s * 1000.0
    return {"requestedMs": requested_ms, "count": count, "actualMs": [round(value, 3) for value in actual], "medianMs": round(statistics.median(actual), 3) if actual else None,
            "maxOvershootMs": round(max(value - requested_ms for value in actual), 3) if actual else None}


def run(destination: str | Path, label: str, *, load: Callable[[], tuple] | None = None, ps: Callable[[float], str] = load_probe.default_ps, wall: Callable[[], float] = time.time,
        ncpu: Callable[[], int | None] = os.cpu_count, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.perf_counter, count: int = COUNT,
        rule_loader: Callable[[], tuple] = load_probe.registered_rule, show: Callable[[str], None] = print) -> dict:
    """The record, written once to `destination` and returned. A sensor that fails is a field of the document, not an exception."""
    rule, _unavailable = rule_loader()
    document: dict = {"schema": SCHEMA, "notEvidence": True, "label": label,
                      "load": load_probe.take(load=load or os.getloadavg, ps=ps, wall=wall, ncpu=ncpu, rule=rule),
                      "latency": [latency(requested, LONG_COUNTS.get(requested, count) if count == COUNT else count, sleep=sleep, clock=clock) for requested in REQUESTED_S]}
    temporary = Path(f"{destination}.tmp")
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    except OSError as error:
        show(f"timer probe: could not write {type(error).__name__}")
    sample = document["load"]
    show(f"timer probe {label}: load1={sample.get('load1')} load5={sample.get('load5')} load15={sample.get('load15')} ncpu={sample.get('ncpu')} "
         + " ".join(f"sleep{entry['requestedMs']:g}ms:median={entry['medianMs']} max-overshoot={entry['maxOvershootMs']}" for entry in document["latency"]))
    return document


def main(argv: list[str], **injected) -> int:
    """Always 0: the record is information only."""
    try:
        if len(argv) == 2 and argv[0] in LABELS:
            run(argv[1], argv[0], **injected)
        else:
            print("usage: hosted_timer_probe.py before|after OUT_JSON", file=sys.stderr)
    except BaseException as error:  # noqa: BLE001 - nothing here may fail the job
        print(f"timer probe: stopped by {type(error).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
