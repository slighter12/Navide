"""Information-only load sampler of the hosted macOS rehearsal (rh1, RC-3; rh2: the registered-equivalent answer; rh3: the file contract and the step timeout as the hard bound). It decides nothing, changes no rule and never fails a job.

    python -B hosted_load_probe.py OUT_JSON [--max-seconds N]

`--max-seconds N` (rh12) is the only option: the bound of the run, at most the 30 minutes that stay the default for every step that gives none.

Rehearsal 1's macOS quiet gate timed out with load1 58 -> 1.9 and load5 29 -> 10 over its ten minutes, and the log said nothing about where that load came from or when it settles. This runs right before the
gate and, every 30 s for at most 30 minutes, records `load1 / load5 / load15`, the core count and the top five processes by CPU (the command name only: no arguments, no path). The quiet rule, the limit
(a fraction of the core count), the number of consecutive polls and the wait are READ from `hosted_quiet_gate` (the registered gate), not repeated here; the sampler stops early after the gate's number of
consecutive quiet samples.

File contract (rh3): OUT_JSON is, at every instant, one complete and valid document. It is written before the first sample and again after every sample, each time to `OUT_JSON.tmp` and then renamed over
OUT_JSON (an atomic replace), so a process killed at any point leaves the last complete document.

Hard bound (rh3): the workflow step's `timeout-minutes` (31) is the bound that cannot be bypassed: it kills the process whatever a sensor is doing (`os.getloadavg` cannot be limited in process). The sampler's own
deadline stays 30 minutes, so in the normal case it ends by itself a minute before the step could. A cut by the step timeout can lose only the sample in progress, and the document then says `stop: "running"`
(the last completed sample's value). Inside that bound: ONE monotonic deadline (`max_s` from the start). `ps` gets the time that is left as its timeout, every sleep is cut to the time left, no sample is
started after the deadline, and a sample whose sensors finished after it is dropped (counted in `lateSamplesDropped`), so no kept sample has `elapsedS` above `maxS`. Every sensor failure is a field of its
sample; the exit code is 0 whatever happens.

`registeredEquivalentQuiet` answers the question the rehearsal exists for: would the REGISTERED gate (which starts at this point of the job and waits `waitS` seconds, not this sampler's thirty minutes) have
passed? `metWithinWait` is true when the gate's number of consecutive quiet samples occurred at `elapsedS` <= `waitS`; `firstMetAtS` is the `elapsedS` at which the rule was first met (or null), also after the wait.
Standard library only (the gate module is imported by name at run time; if it cannot be imported the sampler records that and never decides anything). macOS `ps` syntax.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple

SCHEMA = "acr-g3-load-probe/2"
INTERVAL_S = 30
MAX_S = 30 * 60
PS_TIMEOUT_S = 20.0
TOP = 5
NAME_CHARS = 40
GATE_MODULE = "tests.cli_regression.support.g3.hosted_quiet_gate"
TOKEN = re.compile(r"(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")


class Rule(NamedTuple):
    """The registered gate's darwin rule, as `hosted_quiet_gate` defines it."""
    description: str
    limit: Callable[[int | None], float]
    quiet: Callable[[dict], bool]
    consecutive: int
    wait_s: float


def registered_rule() -> tuple[Rule | None, str | None]:
    """`(rule, None)` read from `hosted_quiet_gate`, or `(None, <exception class>)` when that module cannot be imported (the sampler then records the loads and decides nothing)."""
    try:
        gate = importlib.import_module(GATE_MODULE)
        return Rule(gate.RULES["darwin"], gate.darwin_limit, lambda sample: gate.is_quiet("darwin", sample), int(gate.CONSECUTIVE_POLLS), float(gate.MAX_WAIT_S)), None
    except Exception as error:  # noqa: BLE001 - information only
        return None, type(error).__name__


def default_ps(timeout: float = PS_TIMEOUT_S) -> str:
    """`<cpu percent> <command name>` per process, no header (`-c`: the command name alone, not its path or arguments). `timeout` is the time the sampler has left."""
    done = subprocess.run(["ps", "-Aco", "pcpu=,comm="], capture_output=True, text=True, errors="replace", timeout=max(0.0, timeout), check=False)
    return done.stdout


def top_processes(ps_text: str, count: int = TOP) -> list[dict]:
    """The `count` busiest rows of `ps` output, busiest first, as `{"name", "cpuPercent"}`. The name is the last path component, printable, capped, token shapes replaced; a row that does not parse is skipped."""
    rows: list[tuple[float, str]] = []
    for line in ps_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            percent = float(parts[0])
        except ValueError:
            continue
        name = re.sub(r"[^\x20-\x7e]", "?", parts[1].strip().replace("\\", "/").rsplit("/", 1)[-1])[:NAME_CHARS]
        rows.append((percent, TOKEN.sub("[redacted]", name)))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [{"name": name, "cpuPercent": percent} for percent, name in rows[:count]]


def take(*, load: Callable[[], tuple], ps: Callable[[float], str], wall: Callable[[], float], ncpu: Callable[[], int | None], rule: Rule | None = None, ps_timeout: float = PS_TIMEOUT_S) -> dict:
    """One sample. A sensor that raises leaves its fields out and names the exception class in `error`. `ps` is called with `ps_timeout`, the time the sampler has left (at most `PS_TIMEOUT_S`)."""
    sample: dict = {"at": wall()}
    errors: list[str] = []
    try:
        sample["load1"], sample["load5"], sample["load15"] = (float(value) for value in load())
    except Exception as error:  # noqa: BLE001 - information only: the failure is a field
        errors.append(f"load: {type(error).__name__}")
    try:
        cores = ncpu()
        if cores:
            sample["ncpu"] = cores
            if rule is not None:
                sample["limit"] = rule.limit(cores)
    except Exception as error:  # noqa: BLE001
        errors.append(f"ncpu: {type(error).__name__}")
    try:
        sample["top"] = top_processes(ps(min(PS_TIMEOUT_S, ps_timeout)))
    except Exception as error:  # noqa: BLE001
        errors.append(f"ps: {type(error).__name__}")
    if errors:
        sample["error"] = errors
    sample["quiet"] = bool(rule is not None and all(key in sample for key in ("load1", "load5", "limit")) and rule.quiet(sample))
    return sample


def registered_equivalent(samples: list[dict], rule: Rule | None, unavailable: str | None = None) -> dict:
    """What the registered gate would have said about these samples: the gate's number of consecutive quiet samples, at `elapsedS` <= its wait."""
    if rule is None:
        return {"available": False, "reason": unavailable or "no rule", "metWithinWait": None, "firstMetAtS": None}
    streak, first = 0, None
    for sample in samples:
        streak = streak + 1 if sample["quiet"] else 0
        if streak >= rule.consecutive:
            first = sample["elapsedS"]
            break
    return {"available": True, "rule": rule.description, "waitS": rule.wait_s, "consecutive": rule.consecutive, "metWithinWait": first is not None and first <= rule.wait_s, "firstMetAtS": first}


def run(destination: str | Path, *, load: Callable[[], tuple] = os.getloadavg, ps: Callable[[float], str] = default_ps, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time, ncpu: Callable[[], int | None] = os.cpu_count, interval_s: float = INTERVAL_S, max_s: float = MAX_S, show: Callable[[str], None] = print,
        rule_loader: Callable[[], tuple[Rule | None, str | None]] = registered_rule) -> dict:
    """Samples at t = 0, `interval_s`, ... until the gate's number of consecutive quiet samples or `max_s`, rewriting `destination` after every sample; returns the document."""
    rule, unavailable = rule_loader()
    start = clock()
    deadline = start + max_s
    samples: list[dict] = []
    quiet_run = 0
    stop = "max-duration"
    document: dict = {"schema": SCHEMA, "notEvidence": True, "intervalS": interval_s, "maxS": max_s, "quietRule": rule.description if rule else None, "stop": "running", "lateSamplesDropped": 0, "samples": samples,
                      "registeredEquivalentQuiet": registered_equivalent(samples, rule, unavailable)}

    def write() -> None:
        """The file on disk is always one complete document: it is written beside the destination and renamed over it, so a kill at any instant leaves the previous document or the new one."""
        document["registeredEquivalentQuiet"] = registered_equivalent(samples, rule, unavailable)
        temporary = Path(f"{destination}.tmp")
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, destination)
        except OSError as error:
            show(f"load probe: could not write {type(error).__name__}")

    write()      # a step cut during the first sample still leaves a valid document (no samples, stop "running")
    while True:
        remaining = deadline - clock()
        if remaining < 0:      # a sleep that ran past the deadline: no sample is started after it
            stop = "max-duration"
            break
        sample = take(load=load, ps=ps, wall=wall, ncpu=ncpu, rule=rule, ps_timeout=remaining)
        elapsed = clock() - start
        if elapsed > max_s:      # the sensors finished after the deadline: the sample is not kept
            document["lateSamplesDropped"] += 1
            stop = "max-duration"
            break
        sample["elapsedS"] = round(elapsed, 1)
        samples.append(sample)
        quiet_run = quiet_run + 1 if sample["quiet"] else 0
        if rule is not None and quiet_run >= rule.consecutive:
            stop = "consecutive-quiet-samples"
        elif clock() - start + interval_s > max_s:      # the next sample would not finish inside the bound
            stop = "max-duration"
        else:
            stop = ""
        document["stop"] = stop or "running"
        write()
        show(f"load probe: t={sample['elapsedS']} load1={sample.get('load1')} load5={sample.get('load5')} load15={sample.get('load15')} ncpu={sample.get('ncpu')} quiet={sample['quiet']}")
        if stop:
            return document
        sleep(max(0.0, min(interval_s, deadline - clock())))
    document["stop"] = stop
    write()
    return document


def max_seconds(argv: list[str]) -> float | None:
    """The bound of this run: `MAX_S` for `OUT_JSON`, the number of seconds after `--max-seconds` for `OUT_JSON --max-seconds N` (rh12: the settle step before the Rust tests ends by itself after 15 minutes), `None` for any other shape."""
    if len(argv) == 1:
        return MAX_S
    if len(argv) == 3 and argv[1] == "--max-seconds":
        try:
            seconds = float(argv[2])
        except ValueError:
            return None
        return seconds if 0 < seconds <= MAX_S else None
    return None


def main(argv: list[str], **injected) -> int:
    """Always 0: the sampler is information only."""
    try:
        bound = max_seconds(argv)
        if bound is not None:
            run(argv[0], max_s=bound, **injected)
        else:
            print("usage: hosted_load_probe.py OUT_JSON [--max-seconds N]", file=sys.stderr)
    except BaseException as error:  # noqa: BLE001 - nothing here may fail the job
        print(f"load probe: stopped by {type(error).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
