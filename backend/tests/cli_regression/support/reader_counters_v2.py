"""Reader counters v2 (t14 item 1): the version a registration pins for the hosted runs; `reader_counters.ReaderCounters` is the same counting without the one check below.

v1 counted a handle's reads by wrapping `handle.read`, and decided "this handle is already wrapped" from `id(handle)`; a freed handle's address could be reused, so a later handle
was never wrapped and its `read_total` stayed 0 while its bytes were still emitted (the hosted registered run's `python:4:6`). Both counters now hear every read from the Terminal
Stream itself (`StreamObserver.read`), so no handle is wrapped and that defect cannot occur.

What v2 keeps: a session that has emitted bytes but was never read from can no longer report counters. `snapshot` raises `ReaderCounterNotInstalled`, which the counters gate answers
as an error reply and the TS runner (`g1eAttempt.ts counters()`) turns into a failed attempt, instead of `read_total` 0. Everything else (the interval records, the equalities)
is v1's behavior, unchanged.
"""
from __future__ import annotations

from .reader_counters import ReaderCounters


class ReaderCounterNotInstalled(RuntimeError):
    pass


class ReaderCountersV2(ReaderCounters):
    def __init__(self) -> None:
        super().__init__()
        self.reader_installed: set[str] = set()

    def read(self, session_id: str, nbytes: int) -> None:
        self.reader_installed.add(session_id)
        super().read(session_id, nbytes)

    def snapshot(self, sid: str) -> dict[str, int]:
        entry = self._of(sid)
        if sid not in self.reader_installed and (entry["emitted_total"] or entry["dropped_oldest_total"]):
            raise ReaderCounterNotInstalled(f"session {sid}: {entry['emitted_total']} bytes were emitted but no read of its master was ever reported")
        return super().snapshot(sid)
