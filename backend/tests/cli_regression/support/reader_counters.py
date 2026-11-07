"""Reader counters for both arms (E3 S13; E2 v10 section 3 item 4 c/e): the Terminal Stream tells its observer what moves through it
(`terminals.StreamObserver`), and `attach` registers these counters as that observer through the terminal service interface
(`TerminalInterface.observe_stream`), so the Python arm and the Rust arm are counted by the same code and nothing in the product is
patched or reached by a private name.

Per session: `read_total` (bytes read from the session's master), `emitted_total` (payload bytes of the binary frames handed to the
sink), `dropped_oldest_total` (bytes the Terminal Stream's drop-oldest rule discarded), interval records (absorb / emit / drop, with a
per-session emit sequence number) and the resize drain's watermark `R` (`read_total` once the drain has emitted everything).
Counts only; never payload bytes.
"""
from __future__ import annotations

import time
from typing import Any

from agent_team_backend.terminals import StreamObserver


class ReaderCounters(StreamObserver):
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def _of(self, sid: str) -> dict[str, Any]:
        return self.sessions.setdefault(sid, {"read_total": 0, "emitted_total": 0, "dropped_oldest_total": 0,
                                              "emit_seq": 0, "intervals": [], "resize_watermarks": []})

    def attach(self, service) -> "ReaderCounters":
        """Count the Terminal Stream `service` presents sessions through (either arm's terminal service)."""
        service.observe_stream(self)
        return self

    def read(self, session_id: str, nbytes: int) -> None:
        self._of(session_id)["read_total"] += nbytes

    def absorbed(self, session_id: str, nbytes: int) -> None:
        self._of(session_id)["intervals"].append(("absorb", time.monotonic(), nbytes))

    def dropped(self, session_id: str, nbytes: int) -> None:
        entry = self._of(session_id)
        entry["dropped_oldest_total"] += nbytes
        entry["intervals"].append(("drop", time.monotonic(), nbytes))

    def emitted(self, session_id: str, nbytes: int) -> None:
        entry = self._of(session_id)
        entry["emit_seq"] += 1
        entry["emitted_total"] += nbytes
        entry["intervals"].append(("emit", time.monotonic(), nbytes, entry["emit_seq"]))

    def drained(self, session_id: str) -> None:
        entry = self._of(session_id)
        entry["resize_watermarks"].append(entry["read_total"])

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
