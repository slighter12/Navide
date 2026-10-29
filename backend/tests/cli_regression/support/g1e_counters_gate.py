"""Receipts-only reader-counter interface for the isolated backend process (E2 v10 section 3 item 4 c/e, section 7 resize rows; E3 `ReaderCounters`).

`NAVIDE_G1E_COUNTERS=1` makes `backend_entry.py` call `install(root)`: every `TerminalService` (the Python arm's and the Rust arm's, which subclasses it) gets the SAME
`ReaderCounters` wrapper, so both arms are counted by one piece of code and `terminals.py` stays untouched. A daemon thread serves the counters on a Unix socket
`<root>/gc.sock` (owner-only; AF_UNIX paths are limited to 103 bytes on macOS, so the name is short and `serve` refuses a longer path up front): one JSON request per line, one JSON answer per line. Counts and intervals only; never payload bytes, never descriptor numbers.

    {"op": "snapshot", "session": sid}   -> {"read_total", "emitted_total", "dropped_oldest_total"}
    {"op": "intervals", "session": sid}  -> {"intervals": [...], "resize_watermarks": [...]}
    {"op": "control_ledger"}             -> the Rust service's frame ledger, or null on the Python arm
    {"op": "sessions"}                   -> the session ids seen
    {"op": "usage"}                      -> {"python_pty_spawns", "log_sent_calls"}: uses of the Python PTY spawn (the Rust arm must have none, E1 15 (d)) and of
                                            `terminal.log_sent` (a measured session must have none, E2 4f)
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

from .reader_counters import ReaderCounters


class CountersGate:
    def __init__(self) -> None:
        self.counters = ReaderCounters()
        self.services: list = []
        self.usage = {"python_pty_spawns": 0, "log_sent_calls": 0}

    def attach(self, service) -> None:
        self.counters.attach(service)
        self.services.append(service)

    def answer(self, request: dict) -> dict:
        op = request.get("op")
        if op == "snapshot":
            sid = request["session"]
            if sid not in self.counters.sessions:
                return {"error": "unknown session"}
            return self.counters.snapshot(sid)
        if op == "intervals":
            sid = request["session"]
            entry = self.counters.sessions.get(sid)
            if entry is None:
                return {"error": "unknown session"}
            return {"intervals": [list(i) for i in entry["intervals"]], "resize_watermarks": list(entry["resize_watermarks"])}
        if op == "control_ledger":
            for service in self.services:
                ledger = getattr(service, "control_ledger", None)
                if callable(ledger):
                    return {"ledger": ledger()}
            return {"ledger": None}
        if op == "usage":
            return dict(self.usage)
        if op == "sessions":
            return {"sessions": sorted(self.counters.sessions)}
        return {"error": "unknown op"}


def serve(gate: CountersGate, path: Path) -> threading.Thread:
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(os.fsencode(str(path))) > 103:
        raise ValueError(f"AF_UNIX path of {len(os.fsencode(str(path)))} bytes exceeds the 103-byte limit: {path}")
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.listen(8)

    def handle(connection: socket.socket) -> None:
        with connection, connection.makefile("rwb") as stream:
            for raw in stream:
                try:
                    reply = gate.answer(json.loads(raw))
                except Exception as error:  # a bad request is an answer, never a crash of the backend
                    reply = {"error": type(error).__name__}
                stream.write(json.dumps(reply).encode() + b"\n")
                stream.flush()

    def loop() -> None:
        while True:
            connection, _ = server.accept()
            threading.Thread(target=handle, args=(connection,), daemon=True).start()

    thread = threading.Thread(target=loop, daemon=True, name="g1e-counters")
    thread.start()
    return thread


def install(root: Path) -> CountersGate:
    from agent_team_backend import terminals

    gate = CountersGate()
    original = terminals.TerminalService.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        gate.attach(self)

    terminals.TerminalService.__init__ = init
    from agent_team_backend import osplat
    backend, original_spawn, original_log_sent = osplat.terminal_backend, osplat.terminal_backend.spawn, terminals.TerminalService.log_sent

    def counted_spawn(*args, **kwargs):
        gate.usage["python_pty_spawns"] += 1
        return original_spawn(*args, **kwargs)

    def counted_log_sent(self, *args, **kwargs):
        gate.usage["log_sent_calls"] += 1
        return original_log_sent(self, *args, **kwargs)

    backend.spawn = counted_spawn
    terminals.TerminalService.log_sent = counted_log_sent
    serve(gate, root / "gc.sock")
    return gate


def request(path: Path, payload: dict) -> dict:
    """Client helper (tests and the Python driver): one request, one answer."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(path))
        client.sendall(json.dumps(payload).encode() + b"\n")
        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            buffer += chunk
    return json.loads(buffer)
