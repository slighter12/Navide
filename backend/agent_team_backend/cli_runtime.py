"""Concrete, backend-owned Rust ordinary-shell bridge (evaluation slice)."""
from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import sys
import time
from typing import Any
from uuid import uuid4

import psutil

from . import osplat, pty_registry
from .applog import in_data_dir
from .ipc import make_event
from .terminals import TerminalService, _forget_live_log, _register_live_log

log = logging.getLogger(__name__)
_CAP = 5 * 1024 * 1024


def prepare_native_launch(argv: list[str], *, cwd: str, env: dict[str, str]) -> dict[str, str]:
    """Effective child launch boundary, also guarded by the disposable harness."""
    return env


def runtime_path() -> Path:
    if override := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError("Rust runtime binary missing at explicit override")
        return path
    name = osplat.paths.executable_candidates("navide-cli-runtime")[0]
    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable).parent / "bin" / name]
    else:
        candidates = [Path(__file__).resolve().parents[2] / "native/navide-cli-runtime/target/release" / name]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("Rust runtime binary missing; build native/navide-cli-runtime explicitly")


@dataclass
class RuntimeProcessView:
    """Retained identity/status, deliberately not a Python process handle."""
    pid: int
    returncode: int | None = None


@dataclass
class RuntimeSession:
    id: str
    pane_id: str
    agent_key: str | None
    command: list[str]
    cwd: str
    proc: RuntimeProcessView
    generation: str
    metadata: dict[str, Any]
    root: dict[str, Any]
    started_monotonic: float = field(default_factory=time.monotonic)
    sequence: int = 0
    closed: bool = False
    close_reason: str | None = None
    exit_code: int | None = None
    uptime_ms: int | None = None
    exit_signal: str | None = None
    output_log_fp: Any = None
    descendants: dict[int, str] = field(default_factory=dict)
    risk_context: Any = None
    vendor_parser_state: dict = field(default_factory=dict)
    received_seq: int = 0
    raw_offset: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_error: str | None = None
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RustTerminalService(TerminalService):
    """Reuse presentation, not Python native handles or process lifecycle."""

    def __init__(self, emit):
        super().__init__(emit)
        self._runtime: asyncio.subprocess.Process | None = None
        self._runtime_generation = ""
        self._start_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._admission = asyncio.Semaphore(256)
        self._pending: dict[str, asyncio.Future] = {}
        self._request_number = 0
        self._ready: asyncio.Future | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._event_tasks: set[asyncio.Task] = set()
        self._retained: dict[str, RuntimeSession] = {}
        self._owned: dict[str, asyncio.Future] = {}
        self._launches: dict[str, dict] = {}
        self._lost: str | None = None
        self._stopping = False
        self._flow_tasks: dict[str, asyncio.Task] = {}
        self._flow_desired: dict[str, bool] = {}
        self._ownership_pages: dict[str, list[dict]] = {}
        self._settled: deque[str] = deque()
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _receipt(self, record: dict) -> None:
        root = os.environ.get("NAVIDE_REGRESSION_ROOT")
        if root:
            with (Path(root) / "runtime-receipts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _task(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._event_tasks.add(task)
        def done(completed):
            self._event_tasks.discard(completed)
            if not completed.cancelled() and (error := completed.exception()):
                self._lost = f"RUNTIME_EVENT_FAILED: {error}"
                log.error("%s", self._lost)
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(RuntimeError(self._lost))
                for session in self._retained.values():
                    session.changed.set()
        task.add_done_callback(done)
        return task

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._lost:
                raise RuntimeError(self._lost)
            if self._runtime is not None:
                return
            path = runtime_path().resolve()
            self._runtime_generation = secrets.token_hex(16)
            argv = [str(path), "--protocol", "1", "--runtime-generation", self._runtime_generation,
                    "--owner-pid", str(os.getpid())]
            fault = os.environ.get("NAVIDE_ACR_FAULT")
            if fault == "startup":
                argv.append("--fail-startup")
            elif fault == "protocol":
                argv.extend(["--ready-protocol", "999"])
            self._ready = self._loop.create_future()
            try:
                self._runtime = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=65536,
                )
            except OSError as error:
                self._lost = f"START_FAILED at exec: {error.strerror}"
                raise RuntimeError(self._lost) from error
            process = psutil.Process(self._runtime.pid)
            self._runtime_identity = {"pid": process.pid, "created": process.create_time(),
                                      "executable": process.exe(), "executableSha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self._reader_task = self._task(self._read_protocol())
            self._stderr_task = self._task(self._read_stderr())
            try:
                ready = await asyncio.wait_for(asyncio.shield(self._ready), 5)
                expected = {"protocol": 1, "runtime_generation": self._runtime_generation,
                            "pid": self._runtime.pid, "owner_pid": os.getpid(),
                            "runtime": "navide-cli-runtime", "pty_backend": "posix"}
                if any(ready.get(key) != value for key, value in expected.items()) or not ready.get("version"):
                    raise RuntimeError("PROTOCOL_MISMATCH at handshake")
                self._receipt({**ready, **self._runtime_identity})
            except BaseException as error:
                self._lost = str(error) or "READY_TIMEOUT at handshake"
                if self._runtime.returncode is None:
                    self._runtime.terminate()
                    try:
                        await asyncio.wait_for(self._runtime.wait(), 2)
                    except TimeoutError:
                        self._runtime.kill()
                        await self._runtime.wait()
                self._receipt({"event": "startup_failed", "stage": "handshake", "error": self._lost,
                               **self._runtime_identity, "exit_code": self._runtime.returncode})
                raise RuntimeError(self._lost) from error

    async def _read_stderr(self) -> None:
        while chunk := await self._runtime.stderr.read(4096):
            log.warning("Rust runtime diagnostic: %s", chunk.decode("utf-8", "replace")[:4096])

    async def _read_protocol(self) -> None:
        try:
            first = True
            while line := await self._runtime.stdout.readline():
                if len(line) > 65536 or not line.endswith(b"\n"):
                    raise RuntimeError("PROTOCOL_ERROR: invalid line bound")
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("runtime_generation") != self._runtime_generation:
                    raise RuntimeError("PROTOCOL_ERROR: wrong runtime generation")
                if first:
                    first = False
                    if record.get("event") != "ready":
                        raise RuntimeError("START_FAILED before ready: " + str(record.get("error", {}).get("code")))
                    self._ready.set_result(record)
                    continue
                if request_id := record.get("id"):
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        future.set_result(record)
                    continue
                event = record.get("event")
                sid = record.get("session_id")
                if event == "owned":
                    self._receipt(record)
                    self._task(self._persist_owned(record))
                    continue
                session = self._retained.get(sid)
                if session is None or record.get("session_generation") != session.generation:
                    raise RuntimeError("PROTOCOL_ERROR: wrong session generation")
                if record.get("pane_id") != session.pane_id or record.get("workspace_path") != session.cwd:
                    raise RuntimeError("PROTOCOL_ERROR: wrong routing attribution")
                if event == "output":
                    data = base64.b64decode(record["data_b64"], validate=True)
                    seq, offset, gap = record["seq"], record["offset"], record["dropped_before"]
                    if not 0 < len(data) <= 16384 or seq <= session.received_seq or offset - session.raw_offset != gap:
                        raise RuntimeError("PROTOCOL_ERROR: output ordering/gap")
                    if gap:
                        self._out_dropped[sid] = self._out_dropped.get(sid, 0) + gap
                    self._absorb_output(session, data, len(data))
                    session.received_seq, session.raw_offset = seq, offset + len(data)
                    session.changed.set()
                elif event == "exit":
                    self._receipt(record)
                    self._task(self._finish_exit(session, record))
                elif event == "cleaned":
                    self._receipt(record)
                    self._task(self._finish_cleanup(session, record))
                elif event == "ownership":
                    pages = self._ownership_pages.setdefault(sid, [])
                    if record["part"] == 0:
                        pages.clear()
                    pages.extend(record["descendants"])
                    if record["final"]:
                        session.descendants = {entry["pid"]: entry["start_value"] for entry in pages}
                        self._ownership_pages.pop(sid, None)
                        self._task(asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.update_descendants,
                                                                  {session.proc.pid: session.descendants})))
                elif event in {"input_blocked", "input_unblocked"}:
                    fields = ("pending", "since_ms") if event == "input_blocked" else ("drained", "blocked_ms")
                    self._task(self._emit(make_event("terminal." + event, {
                        "terminal_session_id": sid, "pane_id": session.pane_id,
                        **{key: record[key] for key in fields},
                    })))
                else:
                    raise RuntimeError("PROTOCOL_ERROR: unknown event")
            if not self._stopping:
                raise RuntimeError("RUNTIME_LOST: protocol EOF")
        except Exception as error:  # noqa: BLE001
            self._lost = str(error)
            if self._ready and not self._ready.done():
                self._ready.set_exception(RuntimeError(self._lost))
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(self._lost))
            for session in self._retained.values():
                session.changed.set()
            log.error("Rust runtime unavailable: %s", self._lost)

    async def _persist_owned(self, record: dict) -> None:
        sid = record["session_id"]
        future = self._owned.get(sid)
        try:
            launch = self._launches[sid]
            root = record["root"]
            if root.get("start_value"):
                await asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.register_owned,
                    root["pid"], launch["argv"], root["start_value"], self._runtime_generation,
                    sid, record["session_generation"]))
            if future and not future.done():
                future.set_result(record)
        except Exception as error:  # noqa: BLE001
            if future and not future.done():
                future.set_exception(error)

    async def _request(self, op: str, args: dict | None = None, session: RuntimeSession | None = None) -> dict:
        if self._lost:
            raise RuntimeError(self._lost)
        async with self._admission:
            request_id = ""
            future = self._loop.create_future()
            try:
                async with self._send_lock:
                    if self._request_number >= 2**64 - 1:
                        raise RuntimeError("REQUEST_ID_EXHAUSTED")
                    self._request_number += 1
                    request_id = f"r{self._request_number}"
                    request = {"id": request_id, "runtime_generation": self._runtime_generation,
                               "op": op, "args": args or {}}
                    if session:
                        request.update(session_id=session.id, session_generation=session.generation)
                    encoded = json.dumps(request, ensure_ascii=False).encode() + b"\n"
                    if len(encoded) > 32 * 1024 * 1024:
                        raise RuntimeError("BAD_REQUEST: private request exceeds line bound")
                    self._pending[request_id] = future
                    self._runtime.stdin.write(encoded)
                    await self._runtime.stdin.drain()
                response = await asyncio.wait_for(asyncio.shield(future), 15)
                if not response.get("ok"):
                    error = response.get("error", {})
                    raise RuntimeError(f"{error.get('code')} at {error.get('stage')}: {error.get('message')}")
                return response["result"]
            except TimeoutError as error:
                raise RuntimeError(f"REQUEST_TIMEOUT: {op}") from error
            finally:
                self._pending.pop(request_id, None)
                if future.done() and not future.cancelled():
                    future.exception()

    def create(self, **kwargs):
        raise RuntimeError("Selected Rust runtime requires native async create; no Python fallback")

    async def create_async(self, **kwargs) -> RuntimeSession:
        if kwargs.get("agent_key") not in (None, "", "terminal"):
            raise RuntimeError("Rust agent runtime not yet integrated; no Python fallback")
        await self._ensure_started()
        argv = self._resolve_command(kwargs.get("spawn_command") or kwargs["command"])
        cwd = kwargs["cwd"]
        if not os.path.isdir(cwd):
            raise FileNotFoundError("cwd does not exist or is not a directory")
        cols, rows = kwargs.get("cols", 100), kwargs.get("rows", 30)
        env = {**os.environ, "TERM": os.environ.get("TERM", "xterm-256color"),
               "COLUMNS": str(cols), "LINES": str(rows), **(kwargs.get("env") or {})}
        for key in kwargs.get("env_remove") or []:
            env.pop(key, None)
        env = prepare_native_launch(argv, cwd=cwd, env=env)
        sid, generation = str(uuid4()), secrets.token_hex(16)
        args = {"session_id": sid, "session_generation": generation, "pane_id": kwargs["pane_id"],
                "workspace_path": cwd, "argv": argv, "cwd": cwd, "env": env, "cols": cols, "rows": rows}
        self._launches[sid] = args
        self._owned[sid] = self._loop.create_future()
        try:
            result = await self._request("create", args)
            root = result["root"]
            session = RuntimeSession(sid, kwargs["pane_id"], kwargs.get("agent_key"), self._resolve_command(kwargs["command"]),
                                     cwd, RuntimeProcessView(root["pid"]), generation, kwargs.get("metadata") or {}, root)
            try:
                await asyncio.wait_for(asyncio.shield(self._owned[sid]), 15)
            except Exception:
                # A known native spawn must not survive a failed application receipt.
                self._retained[sid] = session
                await self.kill(sid, force=True)
                raise
        finally:
            future = self._owned.pop(sid, None)
            self._launches.pop(sid, None)
            if future and future.done() and not future.cancelled():
                future.exception()
        if result["state"] == "exited":
            session.closed = True
            session.exit_code = root.get("early_status")
        if path := kwargs.get("output_log_file"):
            try:
                await asyncio.to_thread(Path(path).parent.mkdir, parents=True, exist_ok=True)
                session.output_log_fp = await asyncio.to_thread(open, path, "a", encoding="utf-8", buffering=1)
                _register_live_log(sid, path)
            except OSError as error:
                log.warning("cannot open runtime output log: %s", error)
        self._sessions[sid] = session
        self._retained[sid] = session
        return session

    async def activate_async(self, sid: str) -> None:
        await self._request("activate", session=self._retained[sid])

    async def write_async(self, sid: str, data: str) -> int:
        session = self._require(sid)
        async with self._write_locks.setdefault(sid, asyncio.Lock()):
            raw = data.encode("utf-8")
            pending = 0
            for offset in range(0, max(len(raw), 1), 16384):
                result = await self._request("write", {"data_b64": base64.b64encode(raw[offset:offset + 16384]).decode()}, session)
                pending = result["pending"]
            if data and len(data) <= 64:
                self._echo_probe.setdefault(sid, self._loop.time())
            return pending

    async def shell_in_foreground_async(self, sid: str) -> bool | None:
        session = self._sessions.get(sid)
        if session is None or session.closed:
            return None
        return (await self._request("state", session=session))["shell_at_prompt"]

    async def interrupt_async(self, sid: str) -> None:
        await self._request("interrupt", {"data_b64": "Aw=="}, self._require(sid))

    @asynccontextmanager
    async def control_async(self, sid: str, cols: int, rows: int, *, redraw: bool = False):
        """Own the native fence through the caller's public ACK and release."""
        session = self._sessions.get(sid)
        if session is None or session.closed:
            yield
            return
        await session.control_lock.acquire()
        opening = asyncio.create_task(self._request(
            "resize", {"cols": cols, "rows": rows, "redraw": redraw}, session))

        async def close():
            try:
                try:
                    result = await opening
                except Exception:  # Opening failed before a fence was installed.
                    return
                await self._request("release", {"barrier_id": result["barrier_id"]}, session)
            finally:
                session.control_lock.release()

        try:
            result = await asyncio.shield(opening)
            await self._watermark(session, result["through_seq"])
            await self.drain_output(sid)
            yield
        finally:
            # A second cancellation may abandon this await, not its owned
            # cleanup task or lock. B cannot start until A's release completes.
            await asyncio.shield(self._task(close()))

    async def _watermark(self, session: RuntimeSession, seq: int) -> None:
        async with asyncio.timeout(15):
            while session.received_seq < seq:
                if self._lost:
                    raise RuntimeError(self._lost)
                session.changed.clear()
                await session.changed.wait()

    def _flow(self, session: RuntimeSession, paused: bool) -> None:
        self._flow_desired[session.id] = paused
        if session.id in self._flow_tasks:
            return

        async def send():
            try:
                while session.id in self._flow_desired and not session.closed and not self._lost:
                    desired = self._flow_desired.pop(session.id)
                    await self._request("flow", {"paused": desired}, session)
            finally:
                self._flow_tasks.pop(session.id, None)
        self._flow_tasks[session.id] = self._task(send())

    def _flush_output(self, session: RuntimeSession) -> None:
        self._out_handles.pop(session.id, None)
        lock = self._resize_barriers.get(session.id)
        if session.id in self._drain_tasks or (lock and lock.locked()):
            return
        chunks = self._out_buffers.pop(session.id, [])
        self._out_buf_bytes.pop(session.id, None)
        if not chunks:
            return
        combined = b"".join(chunks)
        self._flow(session, True)
        self._mirror_flush_to_log(session, combined)

        async def drain():
            try:
                for piece in self._split_chunks(combined):
                    await self._emit(self._build_output_frame(session, piece))
            finally:
                self._drain_tasks.pop(session.id, None)
                self._flow(session, False)
                if self._out_buffers.get(session.id):
                    self._flush_output(session)
                elif not self._out_dropped.get(session.id):
                    self._out_dropped.pop(session.id, None)
        self._drain_tasks[session.id] = self._task(drain())

    async def drain_output(self, sid: str) -> None:
        session = self._retained.get(sid)
        if session is None:
            return
        lock = self._resize_barriers.setdefault(sid, asyncio.Lock())
        async with lock:
            if task := self._drain_tasks.get(sid):
                await asyncio.shield(task)
            if timer := self._out_handles.pop(sid, None):
                timer.cancel()
            chunks = self._out_buffers.pop(sid, [])
            self._out_buf_bytes.pop(sid, None)
            combined = b"".join(chunks)
            for piece in self._split_chunks(combined) if combined else []:
                await self._emit(self._build_output_frame(session, piece))
            self._mirror_flush_to_log(session, combined)

    async def _finish_exit(self, session: RuntimeSession, record: dict) -> None:
        first = record.get("dropped_tail_first_seq")
        target = first - 1 if record.get("dropped_tail_bytes") else record["last_output_seq"]
        await self._watermark(session, target)
        if record.get("dropped_tail_bytes"):
            self._out_dropped[session.id] = self._out_dropped.get(session.id, 0) + record["dropped_tail_bytes"]
            session.received_seq = record["last_output_seq"]
        await self.drain_output(session.id)
        session.closed, session.close_reason = True, record["reason"]
        session.proc.returncode = session.exit_code = record["exit_code"]
        session.uptime_ms = record["uptime_ms"]
        session.exit_signal = None
        if session.exit_code is not None and session.exit_code < 0:
            try:
                session.exit_signal = signal.Signals(-session.exit_code).name
            except ValueError:
                session.exit_signal = f"SIG{-session.exit_code}"
        if session.output_log_fp:
            if decoder := self._decoders.pop(session.id, None):
                session.output_log_fp.write(decoder.decode(b"", final=True))
            session.output_log_fp.close()
            session.output_log_fp = None
        _forget_live_log(session.id)
        self._sessions.pop(session.id, None)
        self._recent_chunks.pop(session.id, None)
        self._echo_probe.pop(session.id, None)
        self._resize_barriers.pop(session.id, None)
        self._flow_desired.pop(session.id, None)
        self._write_locks.pop(session.id, None)
        self._out_dropped.pop(session.id, None)
        await self._emit(make_event("terminal.exit", {
            "terminal_session_id": session.id, "pane_id": session.pane_id, "reason": session.close_reason,
            "exit_code": session.exit_code, "uptime_ms": session.uptime_ms, "signal": session.exit_signal,
            "startup_probe": session.metadata.get("startup_probe"),
        }))
        self._retire(session)

    def _retire(self, session: RuntimeSession) -> None:
        if not session.closed or not session.cleanup.is_set() or session.cleanup_error or session.id in self._settled:
            return
        self._settled.append(session.id)
        while len(self._settled) > 256:
            self._retained.pop(self._settled.popleft(), None)

    async def _finish_cleanup(self, session: RuntimeSession, record: dict) -> None:
        if not record["cleanup_complete"] or not record["root_reaped"] or record["survivors"] or record["unverifiable"]:
            session.cleanup_error = "CLEANUP_FAILED: native cleanup incomplete"
            self._lost = session.cleanup_error
        else:
            await asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.unregister, session.proc.pid))
        session.cleanup.set()
        self._retire(session)

    async def kill(self, sid: str, force: bool = False) -> None:
        session = self._retained.get(sid)
        if not session:
            return
        if not session.cleanup.is_set():
            await self._request("kill", {"force": force}, session)
            await asyncio.wait_for(session.cleanup.wait(), 12)
        if session.cleanup_error:
            raise RuntimeError(session.cleanup_error)
        # Exit task preserves the outward data-before-exit fence.
        async with asyncio.timeout(15):
            while sid in self._sessions:
                if self._lost:
                    raise RuntimeError(self._lost)
                await asyncio.sleep(0.005)

    async def wait_until_reaped(self, sid: str) -> bool:
        session = self._retained.get(sid)
        if session:
            await asyncio.wait_for(session.cleanup.wait(), 12)
            return not session.cleanup_error
        return True

    async def kill_all(self, grace: float = 1.0) -> None:
        if self._runtime is None:
            return
        if self._lost:
            if self._runtime.returncode is not None and not self._retained:
                return  # Failed startup was identity-bound and already reaped.
            raise RuntimeError(self._lost)
        self._stopping = True
        result = await self._request("shutdown")
        if not result["cleanup_complete"]:
            raise RuntimeError("CLEANUP_FAILED at shutdown")
        await asyncio.wait_for(self._runtime.wait(), 2)
        if self._runtime.returncode:
            raise RuntimeError("Rust runtime failed shutdown")
        if self._event_tasks:
            await asyncio.gather(*tuple(self._event_tasks))
