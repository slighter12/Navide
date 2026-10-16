"""Concrete, backend-owned Rust ordinary-shell bridge (evaluation slice)."""
from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import sqlite3
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
# Set by create_async around its create request: told the request id and answer
# future the moment the request is on the wire, so an abandoned create is traceable.
_ON_DISPATCH: ContextVar = ContextVar("cli_runtime_on_dispatch", default=None)


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


class CreateCancelled(RuntimeError):
    """The caller cancelled this create while it was still being coordinated; nothing was sent."""


class _RuntimeRefused(RuntimeError):
    """The sidecar answered a request with an explicit error: it did not act on it."""


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
    # A create whose caller never received its acknowledgement: tracked only to
    # reach verified cleanup; never presented to a renderer.
    abandoned: bool = False
    # False from the sidecar's `owned` event until create_async hands the session to its
    # caller: events are accepted (never a protocol error), nothing is presented.
    published: bool = True

    @property
    def hidden(self) -> bool:
        return self.abandoned or not self.published


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
        self._observations: dict[str, list[bytes]] = {}
        self._ready_ok = False
        self._invalidation: asyncio.Task | None = None
        self._abandoned_tasks: set[asyncio.Task] = set()
        # sid -> (pane/resume keys, resolved event) of creates the caller abandoned.
        self._unresolved: dict[str, tuple[set[str], asyncio.Event]] = {}
        # sid -> launches whose create was dispatched and whose spawn identity is not
        # durable yet: a child may exist that no registry entry names.
        self._dispatched: dict[str, dict] = {}
        self._persisting: dict[asyncio.Future, tuple[int, str]] = {}
        # generation -> None while its invalidation runs, True once it ended complete,
        # False once it ended incomplete (a surviving identity must stay recorded).
        self._retired: dict[str, bool | None] = {}
        # (generation, pid) of roots whose death the sidecar verified while their durable
        # registration was still in flight: a late registration of exactly that identity is undone.
        self._verified_dead: set[tuple[str, int]] = set()
        self._registered: dict[str, set[int]] = {}
        self._cleanup_failed = False
        self._ownership_gate = False
        # sid -> builder of the provisional session registered at the `owned` event
        self._provisional: dict[str, Any] = {}

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
                self._lose(f"RUNTIME_EVENT_FAILED: {error}")
        task.add_done_callback(done)
        return task

    def _lose(self, message: str) -> None:
        """Record a runtime failure, fail every wait on it and, for a runtime
        that had completed its handshake, start invalidating its generation."""
        self._lost = message
        log.error("Rust runtime unavailable: %s", message)
        if self._ready and not self._ready.done():
            self._ready.set_exception(RuntimeError(message))
        for future in (*self._pending.values(), *self._owned.values()):
            if not future.done():
                future.set_exception(RuntimeError(message))
        for session in self._retained.values():
            session.changed.set()
        if self._ready_ok and (self._invalidation is None or self._invalidation.done()):
            self._invalidation = asyncio.create_task(self._invalidate(message))
            self._invalidation.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _invalidate(self, cause: str) -> None:
        """Retire a lost runtime generation: end the sidecar, its tasks and every
        wait on it, clean its recorded processes by verified identity, present
        each lost session once, and only then (clean) allow a fresh runtime."""
        generation, runtime = self._runtime_generation, self._runtime
        self._retired[generation] = None
        lost = [s for s in self._retained.values() if not s.closed and not s.hidden]
        complete = False
        result = {"signalled": [], "unresolved": [], "unverifiable": True}
        unknown = 0
        journal_unreadable = False
        try:
            if runtime is not None and runtime.returncode is None:
                with suppress(ProcessLookupError):
                    runtime.kill()
            if runtime is not None:
                await asyncio.wait_for(runtime.wait(), 5)
            current = asyncio.current_task()
            old = [t for t in (*self._event_tasks, *self._abandoned_tasks) if t is not current]
            for task in old:
                task.cancel()
            await asyncio.gather(*old, return_exceptions=True)
            writing = [f for f, (_pid, gen) in self._persisting.items() if gen == generation]
            if writing:
                await asyncio.wait(writing, timeout=5)  # normally milliseconds; never cancelled
            # Launches whose create went out and whose identity is not durable: the
            # sidecar reported an identity (clean it by that identity) or never did
            # (a child may exist that nothing names: fence, never guess a pid).
            pending = [e for e in self._dispatched.values() if e["generation"] == generation]
            sid_of = {id(e): sid for sid, e in self._dispatched.items()}
            known = [e["identity"] for e in pending if e["identity"] and e["identity"]["lstart"]]
            # The sidecar's own durable journal names every root it spawned, even one whose
            # `owned` event Python never read. Read strictly: an unreadable journal is
            # an unverifiable generation, never an empty one.
            journal = in_data_dir(self._data_dir, pty_registry.journal_path, generation)()
            journalled: set[str] = set()
            try:
                records = await asyncio.to_thread(in_data_dir(
                    self._data_dir, pty_registry.ingest_journal, journal, os.getpid()))
                journalled = {r.get("session_id") for r in records}
            except (OSError, ValueError, sqlite3.Error) as error:
                log.error("ownership journal unreadable: %s", type(error).__name__)
                journal_unreadable = True
            # With the gate, a dispatched launch that neither reported an identity nor appears in the
            # (strictly readable, or absent) journal was never released: no target ever executed.
            unknown = 0 if self._ownership_gate and not journal_unreadable else len(
                [e for e in pending if not (e["identity"] and e["identity"]["lstart"])
                 and sid_of.get(id(e)) not in journalled])
            result = await asyncio.to_thread(in_data_dir(
                self._data_dir, pty_registry.reap_runtime, generation, 1.0, 3.0, known))
            if journal_unreadable:
                result = {**result, "unverifiable": True}
            complete = (not result["unresolved"] and not result["unverifiable"]
                        and not unknown and not self._cleanup_failed)
            for session in lost:
                session.cleanup_error = None if complete else "CLEANUP_FAILED: runtime loss cleanup incomplete"
                with suppress(Exception):
                    await asyncio.wait_for(self.drain_output(session.id), 5)
                session.closed, session.close_reason = True, "runtime_lost"
                session.proc.returncode = session.exit_code = None
                session.exit_signal = None
                session.uptime_ms = int((time.monotonic() - session.started_monotonic) * 1000)
                await self._publish_exit(session)
                session.cleanup.set()
                self._retire(session)
            for session in self._retained.values():  # a naturally exited one whose cleaned ack was lost
                if not session.hidden and not session.cleanup.is_set():
                    session.cleanup_error = None if complete else "CLEANUP_FAILED: runtime loss cleanup incomplete"
                    session.cleanup.set()
        except BaseException as error:  # noqa: BLE001
            self._lost = f"CLEANUP_FAILED: runtime loss cleanup did not finish ({type(error).__name__})"
            raise
        finally:
            self._receipt({"event": "runtime_invalidated", "lost_generation": generation, "cause": cause[:200],
                           "sessions": len(lost), "signalled": len(result["signalled"]),
                           "unresolved": len(result["unresolved"]) + unknown,
                           "unknown_launches": unknown, "unverifiable": result["unverifiable"],
                           "complete": complete})
            self._retired[generation] = complete
            if complete:
                # Only a verified-clean generation drops its durable records.
                for pid in self._registered.pop(generation, set()):
                    with suppress(Exception):
                        await asyncio.to_thread(in_data_dir(
                            self._data_dir, pty_registry.unregister_owned, pid, generation))
                with suppress(OSError):
                    in_data_dir(self._data_dir, pty_registry.journal_path, generation)().unlink(missing_ok=True)
            for _keys, event in self._unresolved.values():
                event.set()
            self._unresolved.clear()
            for sid in [sid for sid, e in self._dispatched.items() if e["generation"] == generation]:
                self._dispatched.pop(sid, None)
        for sid in [sid for sid, s in self._retained.items() if s.hidden]:
            self._retained.pop(sid, None)
        self._launches.clear()
        self._owned.clear()
        if not complete:
            self._lost = f"CLEANUP_FAILED: runtime loss cleanup incomplete ({cause[:200]})"
            return
        # Clean: the next explicit operation starts a fresh generation.
        self._pending.clear()
        self._observations.clear()
        self._ownership_pages.clear()
        self._flow_tasks.clear()
        self._flow_desired.clear()
        self._runtime = self._ready = self._reader_task = self._stderr_task = None
        self._ready_ok = False
        self._stopping = False
        self._lost = None

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._invalidation is not None and not self._invalidation.done():
                await asyncio.wait({self._invalidation})  # a fresh generation waits for cleanup
            if self._lost:
                raise RuntimeError(self._lost)
            if self._runtime is not None:
                return
            path = runtime_path().resolve()
            self._runtime_generation = secrets.token_hex(16)
            argv = [str(path), "--protocol", "1", "--runtime-generation", self._runtime_generation,
                    "--owner-pid", str(os.getpid())]
            if fence := await asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.reevaluate_fence)):
                self._runtime_generation = ""
                raise RuntimeError(f"CLEANUP_FAILED: ownership unresolved at startup ({fence})")
            try:
                journal = await asyncio.to_thread(in_data_dir(
                    self._data_dir, pty_registry.start_journal, self._runtime_generation, os.getpid()))
            except OSError as error:
                self._runtime_generation = ""
                raise RuntimeError(f"START_FAILED at ownership journal: {error.strerror}") from error
            argv.extend(["--ownership-journal", str(journal)])
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
                # A gated sidecar never lets a root execute before its journal record is durable.
                self._ownership_gate = ready.get("ownership_gate") is True
                self._ready_ok = True
            except BaseException as error:
                # Expected teardown after a rejected handshake must not turn
                # its causal failure into a later protocol-EOF failure.
                self._stopping = True
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
                if record.get("event") == "observation":
                    request_id = record["observation_id"]
                    if request_id not in self._pending:
                        raise RuntimeError("PROTOCOL_ERROR: unowned observation")
                    parts = self._observations.setdefault(request_id, [])
                    if record["part"] != len(parts):
                        raise RuntimeError("PROTOCOL_ERROR: observation order")
                    parts.append(base64.b64decode(record["data_b64"], validate=True))
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
                    if (entry := self._dispatched.get(sid)) is not None:
                        entry["identity"] = {"pid": record["root"]["pid"], "lstart": record["root"].get("start_value")}
                    # `owned` precedes every other event for this session: from here on a
                    # cancel-driven exit before publication is a known (hidden) session.
                    if sid not in self._retained and (build := self._provisional.get(sid)) is not None:
                        self._retained[sid] = build(record["root"])
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
                    if session.hidden:  # never presented: keep ordering, drop the bytes
                        session.received_seq, session.raw_offset = seq, offset + len(data)
                        session.changed.set()
                        continue
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
            self._lose(str(error))

    async def _persist_owned(self, record: dict) -> None:
        sid = record["session_id"]
        future = self._owned.get(sid)
        try:
            launch = self._launches[sid]
            root = record["root"]
            if root.get("start_value"):
                # The registry write runs in a thread that cancellation cannot stop:
                # it is tracked so a retired generation can undo a late entry.
                persist = asyncio.ensure_future(asyncio.to_thread(in_data_dir(
                    self._data_dir, pty_registry.register_owned,
                    root["pid"], launch["argv"], root["start_value"], self._runtime_generation,
                    sid, record["session_generation"])))
                self._persisting[persist] = (root["pid"], self._runtime_generation)
                persist.add_done_callback(self._persisted)
                await asyncio.shield(persist)
                self._dispatched.pop(sid, None)  # identity is now durable
            if future and not future.done():
                future.set_result(record)
        except Exception as error:  # noqa: BLE001
            if future and not future.done():
                future.set_exception(error)

    def _persisted(self, persist: asyncio.Future) -> None:
        pid, generation = self._persisting.pop(persist, (0, ""))
        if persist.cancelled() or persist.exception() is not None:
            return
        if (generation, pid) in self._verified_dead:
            # A late registration of a root whose death was verified: undo exactly that row.
            self._verified_dead.discard((generation, pid))
            self._loop.run_in_executor(None, in_data_dir(
                self._data_dir, pty_registry.unregister_owned, pid, generation))
            return
        self._registered.setdefault(generation, set()).add(pid)
        if self._retired.get(generation) is not True:
            # Live, still being invalidated (its sweep covers this) or ended
            # INCOMPLETE: a registered identity of a possibly surviving child is
            # exactly what a restarted backend needs, so it is never undone.
            return
        # Finished after a COMPLETE cleanup: the row would only be stale
        # (identity-bound to that generation, never by pid alone).
        self._loop.run_in_executor(None, in_data_dir(
            self._data_dir, pty_registry.unregister_owned, pid, generation))

    async def _request(self, op: str, args: dict | None = None, session: RuntimeSession | None = None) -> dict:
        if self._lost:
            raise RuntimeError(self._lost)
        async with self._admission:
            request_id = ""
            keep = False  # a traced create's caller or resolver releases its pending entry
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
                    if (on_dispatch := _ON_DISPATCH.get()) is not None:
                        on_dispatch(request_id, future)
                        keep = True
                    self._runtime.stdin.write(encoded)
                    await self._runtime.stdin.drain()
                response = await asyncio.wait_for(asyncio.shield(future), 15)
                result = response.get("result", {})
                if op == "observe" and "observation_parts" in result:
                    parts = self._observations.get(request_id, [])
                    if result["observation_parts"] != len(parts):
                        raise RuntimeError("PROTOCOL_ERROR: incomplete observation")
                    result = json.loads(b"".join(parts))
                    if not response.get("ok"):
                        error = response.get("error", {})
                        if (result.get("mode") != "activity" or not result.get("error")
                                or result["error"] != error.get("message")
                                or error.get("code") != "IO_FAILED" or error.get("stage") != "io"):
                            raise RuntimeError("PROTOCOL_ERROR: invalid activity error progress")
                        result["error"] = f"{error['code']} at {error['stage']}: {error['message']}"
                        return result  # Validated/applied by the activity reader before raising.
                if not response.get("ok"):
                    error = response.get("error", {})
                    raise _RuntimeRefused(f"{error.get('code')} at {error.get('stage')}: {error.get('message')}")
                return result
            except TimeoutError as error:
                raise RuntimeError(f"REQUEST_TIMEOUT: {op}") from error
            finally:
                if not keep:
                    self._pending.pop(request_id, None)
                self._observations.pop(request_id, None)
                if future.done() and not future.cancelled():
                    future.exception()

    async def observe_claude(self, args: dict) -> dict:
        await self._ensure_started()
        path = Path(args["path"])
        from .cli_vendors.registry import vendor
        spec = vendor("claude")
        session = next((s for s in self._retained.values() if s.agent_key == "claude"
                        and (s.metadata.get("explicit_session_id") or spec.resume_id_from_command(s.command)) == path.stem
                        and not s.closed and not s.hidden), None)
        result = await self._request("observe", {"vendor": "claude", **args}, session)
        if (result.get("parser") != "rust-claude-jsonl-v1" or result.get("path") != str(path)
                or result.get("mode") != args["mode"] or result.get("native_session_id") != path.stem):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation parser/path/mode/session")
        context = result["ownership"]
        if session and (context.get("session_id") != session.id or context.get("session_generation") != session.generation
                        or context.get("pane_id") != session.pane_id or context.get("workspace_path") != session.cwd):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation ownership")
        if not session and any(context.get(key) is not None for key in (
                "session_id", "session_generation", "pane_id", "workspace_path")):
            raise RuntimeError("PROTOCOL_ERROR: historical observation claimed process ownership")
        if result.get("error"):
            seen = result.get("seen")
            previous = set(args.get("seen", []))
            if not isinstance(seen, list) or not all(isinstance(key, str) for key in seen):
                raise RuntimeError("PROTOCOL_ERROR: invalid activity seen progress")
            markers = [key for key in seen if key.startswith("act_hw::")]
            old_water = max((int(key[8:]) for key in previous if key.startswith("act_hw::")), default=0)
            if (len(markers) != 1 or not markers[0][8:].isascii() or not markers[0][8:].isdecimal()
                    or int(markers[0][8:]) <= old_water
                    or set(seen) - set(markers) != {key for key in previous if not key.startswith("act_hw::")}
                    or result["events"]):
                raise RuntimeError("PROTOCOL_ERROR: invalid activity high-water progress")
        self._receipt({**context, "runtime_generation": self._runtime_generation,
                       **{key: result[key] for key in ("parser", "path", "native_session_id", "identity", "mode")},
                       "checkpoint_offset": result["checkpoint"].get("offset"),
                       "seen": result["seen"], "event_count": len(result["events"]),
                       **({"error": result["error"]} if result.get("error") else {})})
        return result

    async def observe_native(self, vendor: str, parser: str, args: dict, *, pane_id=None) -> dict:
        await self._ensure_started()
        session = next((s for s in self._retained.values() if pane_id and s.pane_id == pane_id
                        and s.agent_key == vendor and not s.closed and not s.hidden), None)
        result = await self._request("observe", {"vendor": vendor, **args}, session)
        if (result.get("parser") != parser or result.get("path") != args["path"]
                or result.get("mode") != args["mode"]):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation parser/path/mode")
        context = result["ownership"]
        if session and any(context.get(key) != expected for key, expected in (
                ("session_id", session.id), ("session_generation", session.generation),
                ("pane_id", session.pane_id), ("workspace_path", session.cwd))):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation ownership")
        if not session and any(context.get(key) is not None for key in (
                "session_id", "session_generation", "pane_id", "workspace_path")):
            raise RuntimeError("PROTOCOL_ERROR: historical observation claimed process ownership")
        self._receipt({**context, "runtime_generation": self._runtime_generation,
                       **{key: result[key] for key in ("parser", "path", "identity", "mode")},
                       **({"native_session_id": result["native_session_id"]} if "native_session_id" in result else {}),
                       "checkpoint": result["checkpoint"], "seen": result["seen"],
                       "event_count": len(result["events"]),
                       "native_sessions": sorted({event["session_id"] for event in result["events"]}),
                       **({"error": result["error"]} if result.get("error") else {})})
        return result

    def create(self, **kwargs):
        raise RuntimeError("Selected Rust runtime requires native async create; no Python fallback")

    async def create_async(self, **kwargs) -> RuntimeSession:
        from .cli_runtime_adapters import NATIVE_VENDORS
        if kwargs.get("agent_key") not in (None, "", "terminal", *NATIVE_VENDORS):
            raise RuntimeError("Rust agent runtime not yet integrated; no Python fallback")
        cancelled = kwargs.pop("cancelled", None)
        cancel_record = kwargs.pop("cancel_record", None)
        await self._ensure_started()
        keys = self._launch_keys(kwargs)
        await self._await_unresolved(keys)
        if cancelled is not None and cancelled():
            raise CreateCancelled("create cancelled before it was dispatched")
        argv = self._resolve_command(kwargs.get("spawn_command") or kwargs["command"])
        cwd = kwargs["cwd"]
        if not os.path.isdir(cwd):
            raise FileNotFoundError("cwd does not exist or is not a directory")
        cols, rows = kwargs.get("cols", 100), kwargs.get("rows", 30)
        env = {**os.environ, "TERM": os.environ.get("TERM", "xterm-256color"),
               "COLUMNS": str(cols), "LINES": str(rows), **(kwargs.get("env") or {})}
        for key in kwargs.get("env_remove") or []:
            env.pop(key, None)
        from . import detached_servers, resource_limits

        metadata = kwargs.get("metadata")
        metadata = metadata if metadata is not None else {}
        pane_mark = detached_servers.new_mark() if resource_limits.track_detached(resource_limits.current()) else ""
        if pane_mark:
            env[detached_servers.MARK_ENV] = pane_mark
            metadata["pane_mark"] = pane_mark
        env = prepare_native_launch(argv, cwd=cwd, env=env)
        sid, generation = str(uuid4()), secrets.token_hex(16)
        args = {"session_id": sid, "session_generation": generation, "pane_id": kwargs["pane_id"],
                "workspace_path": cwd, "argv": argv, "cwd": cwd, "env": env, "cols": cols, "rows": rows,
                "agent_key": kwargs.get("agent_key") or ""}
        self._launches[sid] = args
        self._owned[sid] = self._loop.create_future()
        command_view = self._resolve_command(kwargs["command"])

        def provisional(root: dict) -> RuntimeSession:
            return RuntimeSession(sid, kwargs["pane_id"], kwargs.get("agent_key"), command_view, cwd,
                                  RuntimeProcessView(root["pid"]), generation, metadata, root, published=False)

        self._provisional[sid] = provisional
        dispatched: dict = {}
        native: dict = {}

        def on_dispatch(request_id: str, future: asyncio.Future) -> None:
            # The dispatch boundary: after admission and the send lock, with the bytes
            # not yet written. A cancellation that arrived while waiting is honoured here,
            # in the same synchronous step that records the dispatch for the canceller.
            if cancelled is not None and cancelled():
                raise CreateCancelled("create cancelled at the dispatch boundary")
            dispatched.update(request_id=request_id, future=future)
            self._dispatched[sid] = {"generation": self._runtime_generation, "identity": None}
            if cancel_record is not None:
                native.update(request_id=request_id, future=future, gated=self._ownership_gate,
                              outcome=None, settled=asyncio.Event())
                cancel_record["native"] = native

        def settle(outcome: str) -> None:
            if native:
                native["outcome"] = outcome
                native["settled"].set()

        async def honour_cancel(session: RuntimeSession) -> None:
            """Before publication: a cancel that arrived after the answer is honoured by
            cleaning the created root by its verified identity; it is never published."""
            if cancelled is None or not cancelled():
                return
            try:
                verified = await self._clean_verified(session)
            except Exception:  # noqa: BLE001
                verified = False
            settle("cleaned" if verified else "cleanup-failed")
            if not verified:
                if not self._lost:
                    self._cleanup_failed = True
                    self._lose(f"CLEANUP_FAILED: cancelled create {sid} could not be cleaned up")
                raise CreateCancelled("create cancelled, but its root could not be verified cleaned")
            raise CreateCancelled("create cancelled after the root was released; it was cleaned")

        abandon = None
        result = None
        session = None
        try:
            token = _ON_DISPATCH.set(on_dispatch)
            try:
                result = await self._request("create", args)
            except _RuntimeRefused as refused:
                self._dispatched.pop(sid, None)
                settle("refused")  # an error answer: no target executed
                if "CREATE_CANCELLED" in str(refused):
                    raise CreateCancelled("create cancelled by the runtime before it released the root") from refused
                raise  # the sidecar answered and did not spawn
            except BaseException:
                # Cancelled, timed out or lost after the request went out: the
                # sidecar may still spawn. Never leave that child untracked.
                if dispatched and not self._lost:
                    abandon = {"result": None}
                raise
            finally:
                _ON_DISPATCH.reset(token)
            root = result["root"]
            session = self._retained.get(sid)
            if session is None or session.published:
                session = provisional(root)
                self._retained[sid] = session
            session.root, session.proc.pid = root, root["pid"]
            try:
                await asyncio.wait_for(asyncio.shield(self._owned[sid]), 15)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A known native spawn must not survive a failed application receipt.
                await self.kill(sid, force=True)
                settle("cleaned")
                raise
            await honour_cancel(session)
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
            await honour_cancel(session)
            # Publication: no await between the last cancellation check and the hand-over.
            session.published = True
            self._sessions[sid] = session
            settle("published")
            return session
        finally:
            self._provisional.pop(sid, None)
            if (abandon is None and session is not None and not session.published
                    and not session.cleanup.is_set()):
                # Left after the answer without publication or a finished cleanup (e.g. the
                # caller's task was cancelled): drive the created root to verified cleanup.
                abandon = {"result": result}
            if abandon is not None:
                self._abandon_launch(sid, generation, kwargs, cwd, metadata, keys, dispatched,
                                     abandon["result"], native)
            else:
                future = self._owned.pop(sid, None)
                self._launches.pop(sid, None)
                if future and future.done() and not future.cancelled():
                    future.exception()
                if dispatched:
                    self._pending.pop(dispatched["request_id"], None)

    def _launch_keys(self, kwargs: dict) -> set[str]:
        """What an unresolved earlier launch must not overlap: the pane, and the
        conversation a resume command targets."""
        keys = {f"pane:{kwargs['pane_id']}"}
        agent_key = kwargs.get("agent_key") or ""
        from .cli_vendors.registry import vendor
        spec = vendor(agent_key) if agent_key else None
        if spec is not None and spec.resume_id_from_command is not None:
            with suppress(Exception):
                if resume_id := spec.resume_id_from_command(self._resolve_command(kwargs["command"])):
                    keys.add(f"resume:{agent_key}:{resume_id}")
        return keys

    async def _await_unresolved(self, keys: set[str]) -> None:
        """Block a launch while an earlier abandoned launch of the same pane or
        conversation has not reached verified cleanup. No automatic retry."""
        try:
            async with asyncio.timeout(30):
                while True:
                    waiting = [event for held, event in self._unresolved.values()
                               if held & keys and not event.is_set()]
                    if not waiting:
                        if self._lost:  # released by a failed cleanup, not by a resolved one
                            raise RuntimeError(self._lost)
                        return
                    await waiting[0].wait()
        except TimeoutError as error:
            raise RuntimeError("CREATE_BLOCKED: an earlier launch of this pane or session is not yet resolved") from error

    def _abandon_launch(self, sid: str, generation: str, kwargs: dict, cwd: str, metadata: dict,
                        keys: set[str], dispatched: dict, result: dict | None, native: dict | None = None) -> None:
        """Own a create nobody is waiting for. The stub is registered before any
        sidecar event for this session can be read, so none is mistaken for a
        protocol error; a shielded task drives it to verified cleanup."""
        root = result["root"] if result else {}
        stub = self._retained.get(sid)
        if stub is None or stub.published:
            stub = RuntimeSession(sid, kwargs["pane_id"], kwargs.get("agent_key"), self._resolve_command(kwargs["command"]),
                                  cwd, RuntimeProcessView(root.get("pid", 0)), generation, metadata, root)
            self._retained[sid] = stub
        stub.abandoned = True
        if native:
            native["outcome"] = "abandoned"  # settled by the resolver
        event = asyncio.Event()
        self._unresolved[sid] = (keys, event)
        task = asyncio.create_task(self._resolve_abandoned(stub, dispatched, result, event, native))
        self._abandoned_tasks.add(task)
        task.add_done_callback(self._abandoned_tasks.discard)

    async def _resolve_abandoned(self, stub: RuntimeSession, dispatched: dict, result: dict | None,
                                 event: asyncio.Event, native: dict | None = None) -> None:
        sid, outcome = stub.id, "unknown"
        try:
            if result is None:
                with suppress(Exception):  # an optimization; the create's own answer decides
                    await self._request("cancel", {"target_id": dispatched["request_id"]})
                try:
                    response = await asyncio.wait_for(asyncio.shield(dispatched["future"]), 20)
                except TimeoutError:
                    self._lose(f"ABANDONED_CREATE_UNRESOLVED: no answer to create {dispatched['request_id']}")
                    outcome = "unanswered"
                    return
                except Exception:  # runtime lost: its invalidation owns the cleanup
                    outcome = "runtime-lost"
                    return
                if not response.get("ok"):
                    outcome = "refused"
                    self._retained.pop(sid, None)
                    self._dispatched.pop(sid, None)
                    return
                result = response["result"]
                stub.root = result["root"]
                stub.proc.pid = result["root"]["pid"]
            await self.kill(sid, force=True)
            outcome = "cleaned" if stub.cleanup.is_set() and not stub.cleanup_error else "cleanup-failed"
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            outcome = "cleanup-failed"
            log.error("abandoned create %s cleanup failed: %s", sid, error)
            if not self._lost:
                # The child may still be running: retire the runtime through the
                # verified path and keep the replacement barrier closed.
                self._cleanup_failed = True
                self._lose(f"CLEANUP_FAILED: abandoned create {sid} could not be cleaned up")
        finally:
            self._receipt({"event": "abandoned_create", "session_id": sid, "session_generation": stub.generation,
                           "create_id": dispatched.get("request_id"), "outcome": outcome})
            if native:
                native["outcome"] = f"abandon-{outcome}"
                native["settled"].set()
            self._pending.pop(dispatched.get("request_id"), None)
            self._launches.pop(sid, None)
            future = self._owned.pop(sid, None)
            if future and future.done() and not future.cancelled():
                future.exception()
            if outcome in ("cleaned", "refused"):
                self._unresolved.pop(sid, None)
                event.set()
            # Any other outcome leaves the barrier to the runtime invalidation, which
            # opens it only together with the explicit fence (`_lost`).

    async def cancel_dispatched(self, record: dict) -> str:
        """The renderer's cancel for a create whose transaction is `record` (its flag is
        already set). "proven" only when the target can never execute: nothing went on the
        wire (the dispatch boundary checks the flag in the step that records the dispatch),
        or the sidecar decided `prevented` atomically with its gate. Otherwise "pending":
        the caller waits for the transaction's own disposition (see `create_async`)."""
        native = record.get("native")
        if not native:
            return "proven"
        if native["outcome"] is None and not native["future"].done() and not self._lost:
            try:
                state = (await self._request("cancel", {"target_id": native["request_id"]})).get("state")
            except Exception as error:  # noqa: BLE001 - an unanswered cancel proves nothing
                log.warning("native cancel for create failed: %s", type(error).__name__)
                state = None
            if state == "prevented" and native["gated"]:
                return "proven"
        return "pending"

    async def _clean_verified(self, session: RuntimeSession) -> bool:
        """End a session's root and report whether the sidecar verified its cleanup."""
        if not session.cleanup.is_set():
            with suppress(Exception):  # an already closing session still acknowledges below
                await self._request("kill", {"force": True}, session)
            await asyncio.wait_for(session.cleanup.wait(), 12)
        return not session.cleanup_error

    async def kill_verified(self, sid: str) -> bool:
        """kill(), reporting whether the root's cleanup was verified (False when unknown)."""
        session = self._retained.get(sid) or self._sessions.get(sid)
        if session is None:
            return False
        await self.kill(sid, force=True)
        return session.cleanup.is_set() and not session.cleanup_error

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
        await self._publish_exit(session)
        self._retire(session)

    async def _publish_exit(self, session: RuntimeSession) -> None:
        """Release a closed session's presentation state and tell the renderer once."""
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
        if session.hidden:
            return  # the renderer never learned of this create
        await self._emit(make_event("terminal.exit", {
            "terminal_session_id": session.id, "pane_id": session.pane_id, "reason": session.close_reason,
            "exit_code": session.exit_code, "uptime_ms": session.uptime_ms, "signal": session.exit_signal,
            "startup_probe": session.metadata.get("startup_probe"),
        }))

    def _retire(self, session: RuntimeSession) -> None:
        if not session.closed or not session.cleanup.is_set() or session.cleanup_error or session.id in self._settled:
            return
        self._settled.append(session.id)
        while len(self._settled) > 256:
            self._retained.pop(self._settled.popleft(), None)

    async def _finish_cleanup(self, session: RuntimeSession, record: dict) -> None:
        if not record["cleanup_complete"] or not record["root_reaped"] or record["survivors"] or record["unverifiable"]:
            session.cleanup_error = "CLEANUP_FAILED: native cleanup incomplete"
            # Same path as every other runtime loss: fail the waits, present every
            # live session and keep the fresh-runtime fence closed.
            self._cleanup_failed = True
            self._lose(session.cleanup_error)
        else:
            # A registration still in flight for this root would re-add it after the unregister.
            writing = [f for f, (pid, _gen) in self._persisting.items() if pid == session.proc.pid]
            if writing:
                _done, unsettled = await asyncio.wait(writing, timeout=15)
                if unsettled:
                    # The write runs in a thread nothing can stop: unregistering now would be
                    # undone when it lands. The root's death IS verified (native `cleaned`), so
                    # that one identity is marked for `_persisted` to undo the late row; the
                    # cleanup is still reported incomplete and the fence stays closed.
                    session.cleanup_error = "CLEANUP_FAILED: ownership registration did not settle"
                    for future in unsettled:
                        if (identity := self._persisting.get(future)) is not None:
                            self._verified_dead.add((identity[1], identity[0]))
                    self._cleanup_failed = True
                    self._lose(session.cleanup_error)
                    session.cleanup.set()
                    return
            await asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.unregister, session.proc.pid))
            self._dispatched.pop(session.id, None)
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
            try:
                await asyncio.wait_for(session.cleanup.wait(), 12)
            except TimeoutError:
                return False  # the cleanup acknowledgement never settled: not reaped
            return not session.cleanup_error
        return True

    async def kill_all(self, grace: float = 1.0) -> None:
        if self._invalidation is not None and not self._invalidation.done():
            await asyncio.wait({self._invalidation})
        if self._runtime is None:
            return
        if self._lost:
            if self._runtime.returncode is not None and not self._retained:
                return  # Failed startup was identity-bound and already reaped.
            raise RuntimeError(self._lost)
        # App shutdown sweeps PTYs before its existing watcher/checkpoint
        # drain. Keep observation alive through that drain; the backend-owned
        # pipe EOF terminates the sidecar when the backend itself exits.
        owned = tuple(self._retained.values())
        async with asyncio.timeout(15):
            await asyncio.gather(*(self.kill(s.id) for s in owned if not s.closed))
            # Natural exit can remove _sessions before its cleaned ACK arrives.
            # The event is set only after successful ownership unregister.
            await asyncio.gather(*(s.cleanup.wait() for s in owned))
            while pending := self._event_tasks - {self._reader_task, self._stderr_task}:
                await asyncio.gather(*tuple(pending))
        if any(s.cleanup_error for s in owned):
            raise RuntimeError("CLEANUP_FAILED at shutdown")
        if self._lost:
            raise RuntimeError(self._lost)
        # Every session ended and its cleanup was acknowledged: nothing this generation
        # spawned is left to name, so its ownership journal has served its purpose.
        with suppress(OSError):
            in_data_dir(self._data_dir, pty_registry.journal_path, self._runtime_generation)().unlink(missing_ok=True)
