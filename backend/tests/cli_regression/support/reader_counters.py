"""Reader counters for both arms (E3 S13; E2 v10 section 3 item 4 c/e): installed from the harness by wrapping the
Terminal Stream's base methods on a service instance, so the Python arm and the Rust arm are counted by the same
code and `terminals.py` stays byte-identical (the zero-edit policy).

Per session: `read_total` (bytes `handle.read` returned), `emitted_total` (payload bytes of the binary frames handed
to the sink), `dropped_oldest_total` (bytes the Terminal Stream's drop-oldest rule discarded), interval records
(absorb / emit / drop, with a per-session emit sequence number) and the resize slurp watermark `R` (`read_total`
right after `_drain_output_behind_barrier`'s slurp). Counts only; never payload bytes.
"""
from __future__ import annotations

import time
from typing import Any


class ReaderCounters:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def _of(self, sid: str) -> dict[str, Any]:
        return self.sessions.setdefault(sid, {"read_total": 0, "emitted_total": 0, "dropped_oldest_total": 0,
                                              "emit_seq": 0, "intervals": [], "resize_watermarks": []})

    def attach(self, service) -> "ReaderCounters":
        counters = self
        wrapped_handles: set[int] = set()
        original_on_readable = service._on_readable
        original_absorb = service._absorb_output
        original_emit = service._emit
        original_drain = service._drain_output_behind_barrier

        def on_readable(session) -> None:
            handle = session.handle
            if id(handle) not in wrapped_handles and handle is not None:
                wrapped_handles.add(id(handle))
                inner = handle.read

                def read(max_bytes, inner=inner, sid=session.id):
                    data = inner(max_bytes)  # an exception (EAGAIN/EIO) propagates exactly as before
                    if data:
                        counters._of(sid)["read_total"] += len(data)
                    return data

                handle.read = read
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
            result = await original_drain(session)
            counters._of(session.id)["resize_watermarks"].append(counters._of(session.id)["read_total"])
            return result

        service._on_readable, service._absorb_output, service._emit, service._drain_output_behind_barrier = on_readable, absorb, emit, drain
        return self

    def snapshot(self, sid: str) -> dict[str, int]:
        """The counters now (the ledger origin when taken after the start marker; `delta` gives the window)."""
        entry = self._of(sid)
        return {k: entry[k] for k in ("read_total", "emitted_total", "dropped_oldest_total")}

    @staticmethod
    def delta(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
        return {k: b[k] - a[k] for k in a}

    def equalities_hold(self, sid: str, origin: dict[str, int] | None = None) -> bool:
        now = self.snapshot(sid)
        d = self.delta(origin, now) if origin else now
        return d["read_total"] == d["emitted_total"] + d["dropped_oldest_total"]


class PythonPtyGuard:
    """Counts uses of the Python arm's PTY spawn (the Rust arm must have none: E2 3 item 2, E1 15 (d))."""

    def __init__(self, monkeypatch) -> None:
        from agent_team_backend import osplat
        self.spawns = 0
        backend = osplat.terminal_backend
        original = backend.spawn

        def counted(*args, **kwargs):
            self.spawns += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(backend, "spawn", counted)
