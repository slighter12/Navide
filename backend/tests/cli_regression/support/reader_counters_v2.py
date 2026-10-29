"""Reader counters v2 (t14 item 1): `reader_counters.ReaderCounters` stays byte-identical (F0/F1 pinned it); this is the version a new registration pins instead.

Two defects of v1 (`reader_counters.py:34-45`) are closed:

* v1 decided "this handle is already wrapped" from `id(handle)`. A wrapped handle is a reference cycle (handle -> `read` -> bound method -> handle), so the cycle collector frees it at an
  arbitrary moment and CPython can give its address to a later handle. That handle was then never wrapped: its `read_total` stayed 0 while its bytes were still emitted (the hosted
  registered run's `python:4:6`). v2 marks the handle object itself.
* v1 wrapped a handle only in `_on_readable`. The resize drain (`_drain_output_behind_barrier`) also reads the handle, so bytes slurped there before the first readable callback were
  emitted but never counted. v2 wraps in both read paths.

A session that has emitted bytes but never had its reader installed can no longer report counters: `snapshot` raises `ReaderCounterNotInstalled`, which the counters gate answers as
an error reply and the TS runner (`g1eAttempt.ts counters()`) turns into a failed attempt, instead of `read_total` 0. Everything else (the emit / absorb / drain wrapping, the
interval records, the equalities) is v1's behavior, unchanged.
"""
from __future__ import annotations

import time

from .reader_counters import ReaderCounters

_MARK = "_acr_reader_counter_sid"


class ReaderCounterNotInstalled(RuntimeError):
    pass


class ReaderCountersV2(ReaderCounters):
    def __init__(self) -> None:
        super().__init__()
        self.reader_installed: set[str] = set()

    def attach(self, service) -> "ReaderCountersV2":
        counters = self
        original_on_readable = service._on_readable
        original_absorb = service._absorb_output
        original_emit = service._emit
        original_drain = service._drain_output_behind_barrier

        def ensure_reader(session) -> None:
            handle = session.handle
            if handle is None or getattr(handle, _MARK, None) == session.id:
                return
            inner = handle.read

            def read(max_bytes, inner=inner, sid=session.id):
                data = inner(max_bytes)  # an exception (EAGAIN/EIO) propagates exactly as before
                if data:
                    counters._of(sid)["read_total"] += len(data)
                return data

            handle.read = read
            setattr(handle, _MARK, session.id)
            counters.reader_installed.add(session.id)

        def on_readable(session) -> None:
            ensure_reader(session)
            return original_on_readable(session)

        def absorb(session, chunk, nbytes) -> None:
            before = service._out_dropped.get(session.id, 0)
            original_absorb(session, chunk, nbytes)
            entry = counters._of(session.id)
            entry["intervals"].append(("absorb", time.monotonic(), len(chunk)))
            after = service._out_dropped.get(session.id, 0)
            if after > before:
                entry["dropped_oldest_total"] += after - before
                entry["intervals"].append(("drop", time.monotonic(), after - before))

        async def emit(frame) -> None:
            if isinstance(frame, (bytes, bytearray)):
                sid_len = frame[5]
                sid = bytes(frame[6:6 + sid_len]).decode()
                payload = len(frame) - (7 + sid_len + frame[6 + sid_len])
                entry = counters._of(sid)
                entry["emit_seq"] += 1
                entry["emitted_total"] += payload
                entry["intervals"].append(("emit", time.monotonic(), payload, entry["emit_seq"]))
            return await original_emit(frame)

        async def drain(session):
            ensure_reader(session)
            result = await original_drain(session)
            counters._of(session.id)["resize_watermarks"].append(counters._of(session.id)["read_total"])
            return result

        service._on_readable, service._absorb_output, service._emit, service._drain_output_behind_barrier = on_readable, absorb, emit, drain
        return self

    def snapshot(self, sid: str) -> dict[str, int]:
        entry = self._of(sid)
        if sid not in self.reader_installed and (entry["emitted_total"] or entry["dropped_oldest_total"]):
            raise ReaderCounterNotInstalled(f"session {sid}: {entry['emitted_total']} bytes were emitted but no reader counter was installed on its handle")
        return super().snapshot(sid)
