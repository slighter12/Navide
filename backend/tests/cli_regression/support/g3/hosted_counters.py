"""The reader-counters server for Windows (design section 1 row 11): the same one-JSON-request-per-line protocol as `g1e_counters_gate.serve`, on a named pipe instead of `AF_UNIX`.

CPython does not expose `socket.AF_UNIX` on Windows, so the frozen `serve(gate, root/'gc.sock')` cannot run there. `serve_pipe(gate, path)` answers on `\\\\.\\pipe\\acr-g1e-counters-<hash of path>` (a proactor-loop pipe server in a daemon thread)
and writes the pipe name to `<root>/gc.pipe`. The answers come from the unchanged `CountersGate.answer`, so the Rust-arm proof (`python_pty_spawns`, `log_sent_calls`) is the same code on both systems.

Wiring (no frozen file edited), Windows only:
* server: `install_pipe_serve()` replaces `g1e_counters_gate.serve` (looked up by `install` as a module global) with `serve_pipe`. It runs in the backend process, which `hosted_seams` reaches by wrapping
  `BackendProcess.__init__` so the isolated backend env carries `PYTHONPATH=hosted_site;...` and `ACR_G3_HOSTED_COUNTERS=1` (the backend's own environment is rebuilt by `isolated_environment`, so the
  step environment alone does not reach it); `hosted_site/sitecustomize.py` then calls `install_pipe_serve()` at interpreter start.
* client: `hosted_site/hosted-net-preload.cjs` (`NODE_OPTIONS=--require ...`, win32 only) maps a `.../gc.sock` argument of `net.connect` to `pipe_path` of the same string. Both sides hash the same
  input (`str(root / 'gc.sock')`, which the frozen driver also publishes as `countersSocket`); the literal in `test_g3h_counters_wire.py` and `tests/cli/g3HostedPreload.test.ts` pins that.
Whether the variables reach the real backend and Node processes is rehearsal-only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import hosted_markers

PIPE_PREFIX = "\\\\.\\pipe\\acr-g1e-counters-"
PIPE_NAME_FILE = "gc.pipe"


def pipe_path(socket_path: str | Path) -> str:
    """A pipe name that is a pure function of the socket path the g1e driver would have used: one name per isolated root."""
    return PIPE_PREFIX + hashlib.sha256(str(socket_path).encode("utf-8")).hexdigest()[:24]


class PipeProtocol(asyncio.Protocol):
    """One request per line, one answer per line, exactly as the AF_UNIX handler: a bad request is an answer, never a crash of the backend."""
    def __init__(self, gate) -> None:
        self.gate = gate
        self.buffer = b""
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport
        hosted_markers.write("counters-connection")      # the first client that reached the pipe: the Rust-arm proof can only be read through it

    def reply(self, raw: bytes) -> dict:
        try:
            return self.gate.answer(json.loads(raw))
        except Exception as error:      # noqa: BLE001 - the same catch-all as the AF_UNIX handler
            return {"error": type(error).__name__}

    def data_received(self, data: bytes) -> None:
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.transport.write(json.dumps(self.reply(line)).encode() + b"\n")


def serve_pipe(gate, path: str | Path, *, new_loop: Callable[[], object] | None = None) -> threading.Thread:
    """The replacement of `g1e_counters_gate.serve` on Windows. `path` is the `gc.sock` path the caller would have used."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = pipe_path(path)
    (path.parent / PIPE_NAME_FILE).write_text(name, encoding="utf-8")
    loop = (new_loop or asyncio.ProactorEventLoop)()
    ready = threading.Event()
    failure: list[BaseException] = []

    servers: list = []

    def run() -> None:
        asyncio.set_event_loop(loop)
        try:
            servers.append(loop.run_until_complete(loop.start_serving_pipe(lambda: PipeProtocol(gate), name)))      # a coroutine: `ready` only after the pipe exists; it returns ONE PipeServer, not a sequence
        except BaseException as error:      # noqa: BLE001 - reported to the caller below
            failure.append(error)
            ready.set()
            return
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True, name="g1e-counters-pipe")
    thread.start()
    if not ready.wait(10) or failure:
        raise RuntimeError(f"the counters pipe {name} did not start: {failure[0] if failure else 'timeout'}")
    thread.servers, thread.loop = servers, loop      # kept alive and reachable: a collected pipe server stops listening
    return thread


def install_pipe_serve() -> Callable[[], None]:
    """In the backend process: `g1e_counters_gate.install` calls the module-level `serve(gate, root/'gc.sock')`; this makes that call `serve_pipe`. Returns the undo."""
    from tests.cli_regression.support import g1e_counters_gate
    original = g1e_counters_gate.serve
    g1e_counters_gate.serve = serve_pipe

    def undo() -> None:
        g1e_counters_gate.serve = original
    return undo
