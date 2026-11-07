"""The reapers wait for a signalled process to disappear without polling the process table at a fixed short interval.

Each look at the table forks `ps`, so a 20 ms poll over the 3 s settle window costs about 150 forks per survivor. The
wait is a bounded backoff instead, and it returns as soon as every process is gone. A fake clock stands in for
`time` inside the registry only, so the test neither sleeps nor depends on how fast `ps` is.
"""
from __future__ import annotations

import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from agent_team_backend import osplat, pty_registry

pytestmark = pytest.mark.skipif(not hasattr(os, "setsid"), reason="POSIX process groups")

GENERATION = "settle-generation"
#: One fork of `ps` per look. The old fixed poll made ~150 of them in a 3 s settle window; a backoff makes a handful.
MAX_LOOKS_IN_SETTLE = 25


class FakeClock:
    def __init__(self, on_sleep=None) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self._on_sleep = on_sleep

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self._on_sleep:
            self._on_sleep(len(self.sleeps))

    def module(self) -> SimpleNamespace:
        return SimpleNamespace(time=time.time, monotonic=self.monotonic, sleep=self.sleep)


@pytest.fixture
def survivor():
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    yield proc
    pty_registry._FENCES.clear()  # a survivor fences runtime admission
    proc.kill()
    proc.wait()


@pytest.fixture
def table_reads(monkeypatch):
    reads: list[int] = []
    real = pty_registry._ps_table

    def counted():
        reads.append(1)
        return real()

    monkeypatch.setattr(pty_registry, "_ps_table", counted)
    return reads


def _stale(proc) -> None:
    pty_registry.register(proc.pid, ["sleep", "300"])
    entries = pty_registry._load()
    entries[str(proc.pid)]["owner"] = 1  # the recording backend looks dead
    entries[str(proc.pid)]["journal"] = True  # a journal-named identity: only these are verified after the signals
    pty_registry._save(entries)


def _owned(proc) -> None:
    pty_registry.register_owned(proc.pid, ["sleep", "300"], osplat.process_tree.start_time(proc.pid), GENERATION,
                                f"session-{proc.pid}", f"generation-{proc.pid}")


REAPERS = {
    "reap_stale": (_stale, lambda: pty_registry.reap_stale(grace=0.0, settle=3.0)),
    "reap_runtime": (_owned, lambda: pty_registry.reap_runtime(GENERATION, grace=0.0, settle=3.0)),
}


GRACE = 2.0
GRACEFUL = {
    "reap_stale": (_stale, lambda: pty_registry.reap_stale(grace=GRACE, settle=3.0)),
    "reap_runtime": (_owned, lambda: pty_registry.reap_runtime(GENERATION, grace=GRACE, settle=3.0)),
}


@pytest.mark.parametrize("reaper", sorted(GRACEFUL))
def test_a_target_that_is_gone_after_sigterm_costs_less_than_the_grace_and_is_never_sent_sigkill(
        reaper, survivor, monkeypatch):
    prepare, run = GRACEFUL[reaper]
    prepare(survivor)
    signals: list[tuple[list, bool]] = []
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: signals.append((list(targets), force)))

    def die_on_first_wait(count: int) -> None:
        if count == 1:
            survivor.kill()
            survivor.wait()

    clock = FakeClock(die_on_first_wait)
    monkeypatch.setattr(pty_registry, "time", clock.module())

    run()

    assert [force for _targets, force in signals] == [False], "SIGKILL went to a process that SIGTERM had already ended"
    assert clock.now - 1000.0 < GRACE / 4, f"waited {clock.now - 1000.0:.2f} s of a {GRACE} s grace for a process that was gone"


@pytest.mark.parametrize("reaper", sorted(GRACEFUL))
def test_a_target_that_outlives_the_grace_is_sent_sigkill_only_after_the_grace(reaper, survivor, monkeypatch):
    prepare, run = GRACEFUL[reaper]
    prepare(survivor)
    clock = FakeClock()
    monkeypatch.setattr(pty_registry, "time", clock.module())
    signals: list[tuple[list, bool, float]] = []

    def record(targets, *, force):
        signals.append((list(targets), force, clock.now - 1000.0))
        if force:
            survivor.kill()
            survivor.wait()

    monkeypatch.setattr(pty_registry, "_signal_each", record)

    run()

    forced = [(targets, at) for targets, force, at in signals if force]
    assert len(forced) == 1 and [pid for pid, _group in forced[0][0]] == [survivor.pid]
    assert forced[0][1] >= GRACE, "SIGKILL was sent before the grace was over"


@pytest.mark.parametrize("reaper", sorted(REAPERS))
def test_a_survivor_is_looked_for_a_bounded_number_of_times_over_the_settle_window(
        reaper, survivor, table_reads, monkeypatch):
    prepare, run = REAPERS[reaper]
    prepare(survivor)
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # nothing dies
    clock = FakeClock()
    monkeypatch.setattr(pty_registry, "time", clock.module())

    run()

    assert clock.now - 1000.0 >= 3.0, "the window is still the whole settle time"
    assert len(table_reads) <= MAX_LOOKS_IN_SETTLE, f"{len(table_reads)} process-table reads in a 3 s settle window"
    waits = clock.sleeps[:-1]  # the last wait is cut off at the end of the window
    assert waits == sorted(waits), "the wait between looks never shortens"
    assert max(clock.sleeps) <= 1.0, "one wait never swallows the window"


@pytest.mark.parametrize("reaper", sorted(REAPERS))
def test_the_wait_ends_when_the_process_is_gone_not_at_the_end_of_the_window(reaper, survivor, table_reads, monkeypatch):
    prepare, run = REAPERS[reaper]
    prepare(survivor)
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)

    def die_on_second_wait(count: int) -> None:
        if count == 2:
            survivor.kill()
            survivor.wait()

    clock = FakeClock(die_on_second_wait)
    monkeypatch.setattr(pty_registry, "time", clock.module())

    run()

    assert clock.now - 1000.0 < 1.0, f"waited {clock.now - 1000.0:.2f} s for a process that was gone after the second look"
    assert len(table_reads) <= 6
    assert str(survivor.pid) not in pty_registry._load(), "a verified-gone process leaves the registry"
