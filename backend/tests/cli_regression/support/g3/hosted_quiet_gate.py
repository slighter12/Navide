"""The quiet gate of a hosted runner (design section 3.4, addendum D): same receipt schema as the macOS gate (`acr-g1e-quiet/1`, `quiet-NN.json`), its own rule.

    python -m tests.cli_regression.support.g3.hosted_quiet_gate before DIR   exit 0 on PASS, 3 on TIMEOUT (nothing is started then)

Linux: `load1 <= 1.0 and load5 <= 1.0`. Windows: the mean `cpu_percent(interval=1)` over a 30 s window `<= 25 %` (a poll IS the window, so polls are back to back, never a sleep, and a window that cannot end inside the cap is not started).
Both on two consecutive polls 30 s apart, for at most 10 minutes (a fresh VM does not become quieter by waiting,
and three 120-minute waits would break the 6 h job bound). Steal time (Linux, `/proc/stat` deltas) and the process count are recorded as information only; Windows has no steal counter. The gate takes a
"before" sample and cannot see a neighbour that starts later: the calibration `q̂` stays the noise measure. Clock, sleep and the sensors are injected.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import quiet_gate as qg

LINUX_LIMIT = 1.0
WINDOWS_CPU_LIMIT = 25.0
MAX_WAIT_S = 10 * 60
INTERVAL_S = qg.INTERVAL_S
WINDOW_S = 30      # Windows: one poll = the mean of 30 one-second `cpu_percent(interval=1)` samples
assert WINDOW_S == INTERVAL_S      # back-to-back windows are polls 30 s apart
CONSECUTIVE_POLLS = qg.CONSECUTIVE_POLLS
RULES = {"linux": "load1 <= 1.0 and load5 <= 1.0 on 2 consecutive polls, every 30 s, for at most 10 minutes (hosted runner)",
         "windows": "mean cpu_percent(interval=1) over 30 s <= 25 on 2 consecutive polls, every 30 s, for at most 10 minutes (hosted runner)"}
LIMITS = {"linux": LINUX_LIMIT, "windows": WINDOWS_CPU_LIMIT}


def platform_of(system: str) -> str:
    return {"Linux": "linux", "Windows": "windows"}[system]


def is_quiet(platform: str, poll: dict) -> bool:
    if platform == "linux":
        return poll["load1"] <= LINUX_LIMIT and poll["load5"] <= LINUX_LIMIT
    return poll["cpuPercent"] <= WINDOWS_CPU_LIMIT


def _run(platform: str, polls: list[dict]) -> int:
    run = 0
    for poll in reversed(polls):
        if not is_quiet(platform, poll):
            break
        run += 1
    return run


def verdict(platform: str, polls: list[dict], elapsed_s: float) -> str:
    """PASS / WAIT / TIMEOUT. A pass at the deadline still passes: the last poll decides before the deadline does."""
    if _run(platform, polls) >= CONSECUTIVE_POLLS:
        return "PASS"
    return "TIMEOUT" if elapsed_s >= MAX_WAIT_S else "WAIT"


def parse_steal(proc_stat: str) -> tuple[int, int] | None:
    """(steal jiffies, all jiffies) from the first line of /proc/stat, or None when the line has no steal column."""
    fields = proc_stat.splitlines()[0].split() if proc_stat.strip() else []
    if len(fields) < 9 or fields[0] != "cpu":
        return None
    values = [int(v) for v in fields[1:9]]
    return values[7], sum(values)


def default_load() -> tuple[float, float, float]:
    return os.getloadavg()


def default_cpu_percent() -> float:
    import psutil
    return float(psutil.cpu_percent(interval=1))


def default_proc_stat() -> str:
    return Path("/proc/stat").read_text(encoding="utf-8")


def run_gate(directory: Path | str, platform: str, *, load: Callable[[], tuple] = default_load, cpu_percent: Callable[[], float] = default_cpu_percent, proc_stat: Callable[[], str] = default_proc_stat,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time, ncpu: Callable[[], int | None] = os.cpu_count) -> dict:
    """Polls at t = 0, 30, 60, ... until PASS or TIMEOUT, then writes the numbered `quiet-NN.json` and returns it. A sensor that raises writes nothing and is not a pass."""
    start = clock()
    polls: list[dict] = []
    last_steal = None
    state = "WAIT"
    while True:
        if platform == "windows" and clock() - start + WINDOW_S > MAX_WAIT_S:      # a window that cannot finish inside the cap is not started
            state = "TIMEOUT"
            break
        poll: dict = {"at": wall()}
        if platform == "linux":
            load1, load5, load15 = (float(x) for x in load())
            poll.update(load1=load1, load5=load5, load15=load15)
            steal = parse_steal(proc_stat())
            if steal is not None and last_steal is not None:
                poll["stealJiffiesDelta"], poll["allJiffiesDelta"] = steal[0] - last_steal[0], steal[1] - last_steal[1]      # information only
            last_steal = steal
        else:
            samples = [float(cpu_percent()) for _ in range(WINDOW_S)]      # design 3.4: the mean of `cpu_percent(interval=1)` over 30 s; the window itself is the 30 s between polls
            poll.update(cpuPercent=sum(samples) / len(samples), windowS=WINDOW_S, sampleMaxPercent=max(samples))
        polls.append(poll)
        state = verdict(platform, polls, clock() - start)
        if state != "WAIT":
            break
        if platform == "linux":
            sleep(INTERVAL_S)
    receipt = {"schema": qg.QUIET_SCHEMA, "rule": RULES[platform], "limit": LIMITS[platform], "intervalS": INTERVAL_S, "maxWaitS": MAX_WAIT_S, "consecutive": CONSECUTIVE_POLLS, "verdict": state,
               "polls": polls, "ncpu": ncpu(), "hosted": True, "stealRecorded": platform == "linux"}
    qg.write_numbered(directory, "quiet", receipt)
    return receipt


def main(argv: list[str], *, system: Callable[[], str] | None = None, **injected) -> int:
    if len(argv) == 2 and argv[0] == "before":
        import platform as platform_module
        receipt = run_gate(argv[1], platform_of((system or platform_module.system)()), **injected)
        print(json.dumps(receipt))
        return 0 if receipt["verdict"] == "PASS" else 3
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
