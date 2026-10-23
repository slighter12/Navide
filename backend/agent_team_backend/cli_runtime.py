"""Concrete, backend-owned Rust runtime bridge: control channel, handoff and lifecycle (evaluation)."""
from __future__ import annotations

import asyncio
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
import socket
import sys
import time
from typing import Any
from uuid import uuid4

import psutil

from . import osplat, pty_registry
from .applog import in_data_dir
from . import cli_runtime_frames as wire
from .cli_runtime_frames import ChannelEof, Direction, FrameType, ProtocolError, code
from . import cli_runtime_receipts as evaluation
from .cli_runtime_handoff import HandoffRejected, HandoffSlot, MasterHandle, check_master
from . import cli_runtime_windows as winrt
from .ipc import make_event
from .terminals import _BUF_CAP, TerminalService, _clean_for_log, _forget_live_log, _register_live_log

log = logging.getLogger(__name__)
_CAP = 5 * 1024 * 1024
# Named bounds for the processes that cannot advertise one (E1 13.1, 13.3 row 10). The sidecar's
# own bounds arrive in `ready.limits`; these cover what runs before it can speak, and what the
# sidecar has no view of.
READY_DEADLINE = 5.0       # `ready` must arrive within this (STT's READY_TIMEOUT_S pattern)
READY_MAX_LINE = 16_384    # the handshake line's byte bound
TASK_DRAIN_S = 2.0         # loss: awaiting cancelled event tasks (the old wait was unbounded)
SIDECAR_EXIT_S = 5.0       # loss: awaiting the sidecar's exit
PERSIST_WAIT_S = 5.0       # loss: in-flight journal/registry writes
LOSS_FLUSH_S = 2.0         # loss: one overall bound on the baseline output settlement
#: Reported in the `handoff_received` receipt only (no decision is taken on it here; the one platform decision of the
#: handoff, `TIOCPTYGNAME` vs `TIOCGPTN`, lives in `cli_runtime_handoff.slave_name_of`).
RECEIPT_PLATFORM = sys.platform
WINDOWS = sys.platform == "win32"
EXPIRED_IDS = 1024         # ids of expired requests remembered for one tolerated late answer (E1 v8 10.1, 14)
LAUNCH_BLOCK_S = 30.0      # launch coordination policy, not a runtime bound
HANDSHAKE_TEARDOWN_S = 2.0 # a rejected handshake: the sidecar is asked to end, then killed after this
READY_EXIT_S = 5.0         # a sidecar that closed stdout before `ready`: its exit status is read within this
OWNED_RECEIPT_S = 15.0     # the application's receipt of `SESSION_OWNED` (persisting the root's identity) within this
#: Python never accepts an advertised limit above its own compiled-in ceiling (3.1).
_LIMIT_CEILINGS = {
    "max_meta_p2r": wire.MAX_META_P2R, "max_blob_p2r": wire.MAX_BLOB_P2R,
    "max_frame_r2p": wire.MAX_FRAME_R2P, "max_observation_bytes": 8_388_608,
    "max_observation_parts": 128, "observe_inflight": 4, "max_outbound_bytes": 4_194_304,
    "max_outbound_frames": 256, "max_inbound_bytes": 16_777_216, "request_ms": 15_000,
    "create_answer_ms": 20_000, "cleanup_ms": 8_000, "ping_ms": 5_000,
    "kill_escalation_ms": 1_000, "eof_reap_ms": 1_000, "snapshot_ms": 5_000,
}
_SNAPSHOT_CEILINGS = {"young": 5_000, "steady": 30_000, "young_window_ms": 30_000}
_OPS = {"create": code.CREATE, "adopt": code.ADOPT, "resize": code.RESIZE, "kill": code.KILL,
        "cancel": code.CANCEL, "ping": code.PING, "shutdown": code.SHUTDOWN,
        "observe": code.OBSERVE, "tty_eof": code.TTY_EOF, "tty_servicing": code.TTY_SERVICING}
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

    def poll(self) -> int | None:
        return self.returncode


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
    # The handed-off master (None until publication) and the three independent lifecycle facts
    # of E1 5.5: stream end (this side), root exit (`SESSION_EXITED`), verified cleanup.
    handle: Any = None
    stream_ending: bool = False
    stream_reason: str = "exit"
    stream_done: bool = False
    exit_record: dict | None = None
    root_live: bool = False
    exit_published: bool = False
    # True from the moment the one `terminal.exit` is handed to the sink (loss presents the rest).
    exit_started: bool = False
    ended_monotonic: float = 0.0
    launch_keys: set = field(default_factory=set)
    joined: asyncio.Event = field(default_factory=asyncio.Event)
    tty: str = ""

    @property
    def hidden(self) -> bool:
        return self.abandoned or not self.published


class RustTerminalService(TerminalService):
    """Reuse presentation, not Python native handles or process lifecycle."""

    _sending: asyncio.Future | None = None  # the frame write in progress, finished even if its caller left

    def __init__(self, emit):
        super().__init__(emit)
        self._runtime: asyncio.subprocess.Process | None = None
        self._runtime_generation = ""
        self._start_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._admission = asyncio.Semaphore(256)
        self._pending: dict[int, asyncio.Future] = {}
        # request id -> the code its answer must carry, until it is answered (E1 2.3, 2.4).
        self._request_codes: dict[int, int] = {}
        # ids whose waiter gave up (deadline, cancellation): id -> (code, prevented session or None).
        # Exactly one late answer to such an id is tolerated; any other unmatched answer is a protocol error.
        self._expired: dict[int, tuple[int, str | None]] = {}
        self._late_counters: dict[int, list[int]] = {}  # expired OBSERVE id -> [parts, bytes] seen so far
        # sids whose create was settled `prevented` (E1 10.1): a later SESSION_OWNED is a protocol error.
        self._prevented: set[str] = set()
        # byte-accounted admission of queued requests against max_inbound_bytes (E1 1.4)
        self._inbound_used = 0
        self._inbound_waiters: list[asyncio.Future] = []
        self._loss_t0 = 0.0
        self._loss_stages: list[dict] = []
        self._request_number = 0
        self._receipt_root = os.environ.get("NAVIDE_REGRESSION_ROOT")
        self._ledger = evaluation.ControlLedger()  # frames and bytes per type (E2 3 item 5); counts only
        self._sock: socket.socket | None = None
        self._receiver: wire.FrameReceiver | None = None
        self._stdout_task: asyncio.Task | None = None
        # sid -> the claimed master of a session not yet published (or never to be), I2.
        self._slots: dict[str, HandoffSlot | winrt.WindowsMaster] = {}
        self._feed: winrt.StdoutFeed | None = None
        # slave names held by live or pending sessions of this generation (6.4 check 3).
        self._claimed_ttys: set[str] = set()
        # sid -> the binding check that failed for a received master.
        self._handoff_failed: dict[str, HandoffRejected] = {}
        self._ready: asyncio.Future | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._event_tasks: set[asyncio.Task] = set()
        self._retained: dict[str, RuntimeSession] = {}
        self._owned: dict[str, asyncio.Future] = {}
        self._launches: dict[str, dict] = {}
        self._lost: str | None = None
        self._stopping = False
        self._ownership_pages: dict[str, list[dict]] = {}
        self._settled: deque[str] = deque()
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._observations: dict[int, list[bytes]] = {}
        self._late_tasks: set[asyncio.Task] = set()  # event tasks that outlived TASK_DRAIN_S (E1 11)
        self._probing = False
        self._observing: dict[int, list[int]] = {}  # open OBSERVE ids -> [parts, bytes]
        self._observe_slots = asyncio.Semaphore(4)  # replaced by ready.limits.observe_inflight
        self._ready_ok = False
        self._limits: dict = {"request_ms": 15_000, "create_answer_ms": 20_000, "cleanup_ms": 8_000, "ping_ms": 5_000,
                              "max_observation_bytes": 8_388_608, "max_observation_parts": 128, "observe_inflight": 4,
                              "max_inbound_bytes": 16_777_216}
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
        root = self._receipt_root  # fixed at construction: a late task of this service never writes elsewhere
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
        if self._ready_ok:
            self._retire_generation()
        else:
            self._stop_dispatch()
        if self._ready and not self._ready.done():
            self._ready.set_exception(RuntimeError(message))
        for future in (*self._pending.values(), *self._owned.values()):
            if not future.done():
                future.set_exception(RuntimeError(message))
        for session in self._retained.values():
            session.changed.set()
        for waiter in self._inbound_waiters:  # requests waiting for byte admission re-check and fail
            if not waiter.done():
                waiter.set_result(None)
        if self._ready_ok and (self._invalidation is None or self._invalidation.done()):
            self._invalidation = asyncio.create_task(self._invalidate(message))
            self._invalidation.add_done_callback(lambda t: t.cancelled() or t.exception())

    def _stop_dispatch(self) -> None:
        """No further control frame is applied (E1 11 step 1)."""
        if self._sock is not None:
            with suppress(Exception):
                self._loop.remove_reader(self._sock.fileno())
        if self._receiver is not None:
            self._receiver.close()

    def _retire_generation(self) -> None:
        """E1 11 steps 1-2. Nothing here awaits anything. Called synchronously by `_lose`, so the
        retirement flag is set before any other task can run and read it."""
        generation = self._runtime_generation
        if generation in self._retired:
            return
        self._loss_t0 = time.monotonic()
        self._loss_stages = []
        self._stage("retired")
        self._retired[generation] = None  # the retirement flag read by every Rust-path task (E1 8)
        self._stop_dispatch()
        # Request cancellation (not awaited) of every control-protocol event task, abandoned task
        # and `_finish_stream` of the generation; the Terminal Stream's own `_drain` tasks are not
        # cancelled here, they are settled by 8.1.
        current = asyncio.current_task()
        for task in (*self._event_tasks, *self._abandoned_tasks):
            if task is not current:
                task.cancel()
        # Step 2: close every Python handle of the generation, every close through the close-once
        # wrapper (a stream already closed normally is a no-op), every unpublished slot, the channel.
        for retained in self._retained.values():
            if retained.handle is not None:
                retained.handle.close()
        for slot_id in list(self._slots):
            self._close_slot(slot_id)
        self._claimed_ttys.clear()
        if self._sending is not None:
            self._sending.cancel()  # an unfinished frame dies with the channel it was written to
        if self._sock is not None:
            with suppress(OSError):
                self._sock.close()
            self._sock = None
        if WINDOWS and self._runtime is not None and self._runtime.stdin is not None:
            with suppress(Exception):
                self._runtime.stdin.close()  # the sidecar sees the channel closed (uniform loss)
        self._stage("handles_closed")
        if self._runtime is not None and self._runtime.returncode is None:
            with suppress(ProcessLookupError):
                self._runtime.kill()  # through the owned process handle, never by name or pid alone

    def _stage(self, name: str, **counts) -> None:
        """One stage transition of the loss sequence, with its elapsed time and numeric counts only
        (never names, paths or payloads), for the `runtime_invalidated` receipt."""
        self._loss_stages.append({"stage": name, "elapsed_ms": int((time.monotonic() - self._loss_t0) * 1000), **counts})

    async def _quiesce_stream(self, session: RuntimeSession) -> None:
        """E1 8.1 step 2: let the baseline machinery finish what it already holds. No new bytes
        arrive (the handle is closed), so this ends unless the sink stalls (then the bound cuts it)."""
        while True:
            drain = self._drain_tasks.get(session.id)
            barrier = self._resize_barriers.get(session.id)
            if drain is not None:
                await asyncio.wait({drain})
            elif barrier is not None and barrier.locked():
                async with barrier:
                    pass
            elif self._out_buffers.get(session.id):
                if handle := self._out_handles.pop(session.id, None):
                    handle.cancel()
                self._flush_output(session)  # starts the drain that empties what queued behind
            else:
                return

    def _settle_stream(self, session: RuntimeSession) -> dict:
        """E1 8.1 steps 3-4, synchronously: untaken bytes are counted and marked, then the stream
        half of the baseline close, then the log is closed (decoder tail first)."""
        drain = self._drain_tasks.get(session.id)
        barrier = self._resize_barriers.get(session.id)
        in_flight = drain is not None or (barrier is not None and barrier.locked())
        chunks = self._out_buffers.pop(session.id, None) or []
        self._out_buf_bytes.pop(session.id, None)
        untaken = sum(len(chunk) for chunk in chunks)
        if untaken:
            self._out_dropped[session.id] = self._out_dropped.get(session.id, 0) + untaken
        if self._out_dropped.get(session.id):
            self._mirror_flush_to_log(session, b"")  # the existing marker, through the one decoder
        if drain is not None:
            drain.cancel()  # requested, not awaited
        self._close_stream_state(session)
        if session.output_log_fp:
            with suppress(Exception):
                session.output_log_fp.close()
            session.output_log_fp = None
        return {"session_id": session.id, "in_flight_unknown": in_flight, "untaken_dropped": untaken}

    def _present_lost(self, session: RuntimeSession) -> asyncio.Task:
        """Queue the one `terminal.exit(runtime_lost)`. Its task is not an event task of the next
        generation: a wedged sink can only keep it waiting, never fail a fresh runtime."""
        async def present() -> None:
            try:
                await self._publish_exit(session)
            except Exception as error:  # noqa: BLE001
                log.warning("runtime_lost exit not delivered for %s: %s", session.id, error)
        task = asyncio.create_task(present())
        self._late_tasks.add(task)
        task.add_done_callback(self._late_tasks.discard)
        return task

    async def _invalidate(self, cause: str) -> None:
        """Retire a lost runtime generation (E1 11): steps 1-2 already ran synchronously. Step 3:
        three bounded waits run concurrently; step 4 identity-verified cleanup; step 5 present each
        lost session once; step 6 the fence opens only on a verified cleanup."""
        generation, runtime = self._runtime_generation, self._runtime
        self._retire_generation()  # idempotent: a loss that began elsewhere has already done it
        lost = [s for s in self._retained.values() if not s.hidden and not s.exit_started]
        complete = False
        result = {"signalled": [], "unresolved": [], "unverifiable": True}
        unknown = 0
        journal_unreadable = False
        settlements: list[dict] = []
        sidecar_gone = False
        try:
            current = asyncio.current_task()
            old = [t for t in (*self._event_tasks, *self._abandoned_tasks) if t is not current]

            async def drain_tasks() -> None:  # (a)
                pending: set = set()
                if old:
                    _done, pending = await asyncio.wait(old, timeout=TASK_DRAIN_S)
                    self._late_tasks.update(pending)  # kept referenced; the fence stops them acting
                    if pending:
                        log.warning("loss: %d event tasks still pending after TASK_DRAIN_S", len(pending))
                self._stage("tasks_settled", tasks=len(old), still_pending=len(pending))

            async def settle_streams() -> None:  # (c)
                quiesce = {asyncio.ensure_future(self._quiesce_stream(s)): s for s in lost}
                if quiesce:
                    await asyncio.wait(quiesce, timeout=LOSS_FLUSH_S)
                for task in quiesce:
                    task.cancel()  # only a quiescence waiter: the baseline drains are not touched
                settlements.extend(self._settle_stream(s) for s in lost)
                self._stage("streams_settled", sessions=len(lost), cut=len([x for x in settlements if x["in_flight_unknown"]]))

            async def cleanup() -> None:  # (b)
                nonlocal complete, result, unknown, journal_unreadable, sidecar_gone
                if runtime is not None:
                    try:
                        await asyncio.wait_for(runtime.wait(), SIDECAR_EXIT_S)
                        sidecar_gone = True
                    except TimeoutError:
                        log.error("loss: the sidecar did not exit within SIDECAR_EXIT_S")
                else:
                    sidecar_gone = True
                self._stage("sidecar_exited", exited=int(sidecar_gone))
                writing = [f for f, (_pid, gen) in self._persisting.items() if gen == generation]
                unsettled_writes = 0
                if writing:
                    _done, still = await asyncio.wait(writing, timeout=PERSIST_WAIT_S)  # never cancelled
                    unsettled_writes = len(still)
                self._stage("registry_writes_settled", writes=len(writing), unsettled=unsettled_writes)
                if not sidecar_gone:
                    return  # nothing may be signalled while a spawn could still overlap it
                # Launches whose create went out and whose identity is not durable: the sidecar
                # reported an identity (clean it by that identity) or never did (a child may exist
                # that nothing names: fence, never guess a pid).
                pending = [e for e in self._dispatched.values() if e["generation"] == generation]
                sid_of = {id(e): sid for sid, e in self._dispatched.items()}
                known = [e["identity"] for e in pending if e["identity"] and e["identity"]["lstart"]]
                # The sidecar's own durable journal names every root it spawned, even one whose
                # `owned` event Python never read. Read strictly: an unreadable journal is an
                # unverifiable generation, never an empty one.
                journal = in_data_dir(self._data_dir, pty_registry.journal_path, generation)()
                journalled: set[str] = set()
                try:
                    records = await asyncio.to_thread(in_data_dir(
                        self._data_dir, pty_registry.ingest_journal, journal, os.getpid()))
                    journalled = {r.get("session_id") for r in records}
                except (OSError, ValueError, sqlite3.Error) as error:
                    log.error("ownership journal unreadable: %s", type(error).__name__)
                    journal_unreadable = True
                self._stage("journal_read", ok=int(not journal_unreadable), records=len(journalled))
                unknown = 0 if self._ownership_gate and not journal_unreadable else len(
                    [e for e in pending if not (e["identity"] and e["identity"]["lstart"])
                     and sid_of.get(id(e)) not in journalled])
                reaping = asyncio.to_thread(in_data_dir(
                    self._data_dir, pty_registry.reap_runtime, generation, 1.0, 3.0, known))
                result = await asyncio.wait_for(reaping, self._limits["cleanup_ms"] / 1000)
                self._stage("reaped", signalled=len(result["signalled"]), unresolved=len(result["unresolved"]),
                            unverifiable=int(bool(result["unverifiable"])))
                if journal_unreadable:
                    result = {**result, "unverifiable": True}
                complete = (not result["unresolved"] and not result["unverifiable"]
                            and not unknown and not self._cleanup_failed)

            await asyncio.gather(drain_tasks(), settle_streams(), cleanup())
            presented = []
            for session in lost:
                session.cleanup_error = None if complete else "CLEANUP_FAILED: runtime loss cleanup incomplete"
                session.exit_published = True  # claimed before the awaitable publication (at most once)
                session.close_reason = "runtime_lost"
                session.proc.returncode = session.exit_code = None
                session.exit_signal = None
                session.uptime_ms = int((time.monotonic() - session.started_monotonic) * 1000)
                presented.append(self._present_lost(session))
                self._receipt({"event": "exit_published", "session_id": session.id, "reason": "runtime_lost"})
                session.joined.set()
                session.cleanup.set()
                self._retire(session)
            if presented:
                await asyncio.wait(presented, timeout=LOSS_FLUSH_S)  # queued to the sink; never promised delivered
            self._stage("presented", sessions=len(presented))
            for session in self._retained.values():  # a naturally exited one whose cleaned ack was lost
                if not session.hidden and not session.cleanup.is_set():
                    session.cleanup_error = None if complete else "CLEANUP_FAILED: runtime loss cleanup incomplete"
                    session.cleanup.set()
        except BaseException as error:  # noqa: BLE001
            self._lost = f"CLEANUP_FAILED: runtime loss cleanup did not finish ({type(error).__name__})"
            raise
        finally:
            for record in settlements:
                self._receipt({"event": "loss_settled", **record})
            self._receipt({"event": "runtime_invalidated", "lost_generation": generation, "cause": cause[:200],
                           "sessions": len(lost), "signalled": len(result["signalled"]),
                           "unresolved": len(result["unresolved"]) + unknown,
                           "unknown_launches": unknown, "unverifiable": result["unverifiable"],
                           "sidecar_exited": sidecar_gone, "complete": complete, "stages": list(self._loss_stages)})
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
        self._request_codes.clear()
        self._expired.clear()
        self._late_counters.clear()
        self._observations.clear()
        self._observing.clear()
        self._ownership_pages.clear()
        self._runtime = self._ready = self._reader_task = self._stderr_task = None
        self._receiver = self._stdout_task = None
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
            if fence := await asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.reevaluate_fence)):
                self._runtime_generation = ""
                raise RuntimeError(f"CLEANUP_FAILED: ownership unresolved at startup ({fence})")
            try:
                journal = await asyncio.to_thread(in_data_dir(
                    self._data_dir, pty_registry.start_journal, self._runtime_generation, os.getpid()))
            except OSError as error:
                self._runtime_generation = ""
                raise RuntimeError(f"START_FAILED at ownership journal: {error.strerror}") from error
            if WINDOWS:
                parent = child = None  # the control channel is the sidecar's stdin and stdout
            else:
                parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            argv = [str(path), "--wire", str(wire.WIRE_VERSION), "--runtime-generation", self._runtime_generation,
                    "--owner-pid", str(os.getpid()), "--ownership-journal", str(journal)]
            if child is not None:
                argv.extend(["--control-fd", str(child.fileno())])
            fault = os.environ.get("NAVIDE_ACR_FAULT")
            if fault == "startup":
                argv.append("--fail-startup")
            elif fault == "protocol":
                argv.extend(["--ready-wire", "999"])
            self._ready = self._loop.create_future()
            try:
                self._runtime = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=READY_MAX_LINE,
                    **({} if child is None else {"pass_fds": (child.fileno(),)}),
                )
            except OSError as error:
                for end in (parent, child):
                    if end is not None:
                        end.close()
                self._lost = f"START_FAILED at exec: {error.strerror}"
                raise RuntimeError(self._lost) from error
            # Python must hold only its own end, or it could never observe the sidecar's death.
            if child is not None:
                child.close()
            process = psutil.Process(self._runtime.pid)
            self._runtime_identity = {"pid": process.pid, "created": process.create_time(),
                                      "executable": process.exe(), "executableSha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self._stderr_task = self._task(self._read_stderr())
            try:
                ready = await self._read_ready()
                self._receipt({**ready, **self._runtime_identity})
                self._ownership_gate = True  # verified in `_verify_ready`
                # The reader runs before the first request is sent (3.3).
                tag = wire.runtime_tag(self._runtime_generation)
                if WINDOWS:
                    self._feed = winrt.StdoutFeed()
                    self._receiver = wire.FrameReceiver(self._feed.recv, Direction.RUST_TO_PYTHON, tag, self._on_frame)
                    self._stdout_task = self._task(self._pump_stdout())
                else:
                    parent.setblocking(False)
                    self._sock = parent
                    self._receiver = wire.FrameReceiver(wire.socket_recv(parent), Direction.RUST_TO_PYTHON, tag, self._on_frame)
                    self._loop.add_reader(parent.fileno(), self._on_socket_readable)
                    self._stdout_task = self._task(self._watch_stdout())
                self._ready_ok = True
            except BaseException as error:
                # Expected teardown after a rejected handshake must not turn
                # its causal failure into a later protocol-EOF failure.
                if parent is not None:
                    parent.close()
                self._stopping = True
                self._lost = str(error) or "READY_TIMEOUT at handshake"
                if self._runtime.returncode is None:
                    self._runtime.terminate()
                    try:
                        await asyncio.wait_for(self._runtime.wait(), HANDSHAKE_TEARDOWN_S)
                    except TimeoutError:
                        self._runtime.kill()
                        await self._runtime.wait()
                self._receipt({"event": "startup_failed", "stage": "handshake", "error": self._lost,
                               **self._runtime_identity, "exit_code": self._runtime.returncode})
                raise RuntimeError(self._lost) from error

    async def _read_ready(self) -> dict:
        """The handshake line, under READY_DEADLINE and the byte rules of E1 3.0."""
        try:
            line = await asyncio.wait_for(self._runtime.stdout.readuntil(b"\n"), READY_DEADLINE)
        except TimeoutError:
            raise RuntimeError("READY_TIMEOUT at handshake") from None
        except asyncio.LimitOverrunError:
            raise RuntimeError("PROTOCOL_MISMATCH at handshake: oversize line") from None
        except asyncio.IncompleteReadError:
            status = await asyncio.wait_for(self._runtime.wait(), READY_EXIT_S)
            raise RuntimeError(f"START_FAILED before ready (exit status {status})") from None
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RuntimeError("PROTOCOL_MISMATCH at handshake: not a JSON line") from None
        if not isinstance(record, dict):
            raise RuntimeError("PROTOCOL_MISMATCH at handshake: not an object")
        if record.get("event") == "fatal":
            error = record.get("error") if isinstance(record.get("error"), dict) else {}
            raise RuntimeError(f"START_FAILED: {error.get('code')}")
        if record.get("event") != "ready":
            raise RuntimeError("PROTOCOL_MISMATCH at handshake: first line is not ready")
        self._verify_ready(record)
        return record

    def _verify_ready(self, ready: dict) -> None:
        """3.1: in this order; any failure is PROTOCOL_MISMATCH, never a retry, never a Python PTY."""
        mismatch = RuntimeError("PROTOCOL_MISMATCH at handshake")
        expected = (("protocol", "navide-acr"), ("wire", wire.WIRE_VERSION), ("runtime", "navide-cli-runtime"),
                    ("runtime_generation", self._runtime_generation), ("pid", self._runtime.pid),
                    ("owner_pid", os.getpid()), ("handoff", "duplicate_handle" if WINDOWS else "scm_rights"),
                    ("ownership_gate", True))
        for key, value in expected:
            if ready.get(key) != value or type(ready.get(key)) is not type(value):
                raise mismatch
        if not isinstance(ready.get("version"), str) or not ready["version"]:
            raise mismatch
        limits = ready.get("limits")
        if not isinstance(limits, dict):
            raise mismatch
        for key, ceiling in _LIMIT_CEILINGS.items():
            value = limits.get(key)
            if type(value) is not int or not 0 < value <= ceiling:
                raise mismatch
        snapshot = limits.get("ownership_snapshot_ms")
        if not isinstance(snapshot, dict):
            raise mismatch
        for key, ceiling in _SNAPSHOT_CEILINGS.items():
            value = snapshot.get(key)
            if type(value) is not int or not 0 < value <= ceiling:
                raise mismatch
        self._limits = limits
        self._observe_slots = asyncio.Semaphore(limits["observe_inflight"])

    async def _read_stderr(self) -> None:
        while chunk := await self._runtime.stderr.read(4096):
            log.warning("Rust runtime diagnostic: %s", chunk.decode("utf-8", "replace")[:4096])

    async def _watch_stdout(self) -> None:
        """POSIX: stdout carries the ready line and nothing after it."""
        if await self._runtime.stdout.read(1):
            self._lose("PROTOCOL_ERROR: stdout carried bytes after ready")

    async def _pump_stdout(self) -> None:
        """Windows: the sidecar's stdout carries every frame after `ready`; feed the receiver."""
        feed = self._feed
        while True:
            data = await self._runtime.stdout.read(65536)
            if data:
                feed.buffer.extend(data)
            else:
                feed.eof = True
            # One read is at most the receiver's per-call budget, so a single call drains it.
            if not self._lost:
                self._on_socket_readable()
            if feed.eof or self._lost:
                return

    async def _send_windows(self, encoded: bytes) -> None:
        stdin = self._runtime.stdin
        stdin.write(encoded)
        await stdin.drain()

    def _on_socket_readable(self) -> None:
        try:
            self._receiver.on_readable()
        except ChannelEof as error:
            self._lose(f"RUNTIME_LOST: {error}")
        except ProtocolError as error:
            self._lose(f"PROTOCOL_ERROR: {error}")
        except Exception as error:  # noqa: BLE001
            text = str(error)
            self._lose(text if text.startswith("PROTOCOL_ERROR") else f"PROTOCOL_ERROR: {type(error).__name__}: {text}")

    def _on_frame(self, frame: wire.Frame, fd: int | None) -> None:
        """One control frame, applied synchronously on the loop thread (a descriptor, if any, is
        claimed in this same step, before any await)."""
        meta = frame.meta
        generation = self._runtime_generation  # this frame's task: the generation every fence below uses
        self._ledger.received(frame)
        if frame.frame_type in (FrameType.RESPONSE, FrameType.ERROR):
            self._on_answer(frame)
            return
        event = frame.code
        if event == code.FATAL:
            raise RuntimeError(f"PROTOCOL_ERROR: sidecar reported {meta.get('code')}: {str(meta.get('message'))[:200]}")
        if event == code.OBSERVATION_PART:
            self._on_observation_part(frame)
            return
        sid = meta.get("session_id")
        if event == code.SESSION_OWNED:
            if WINDOWS:
                self._on_owned_windows(frame)
            else:
                self._on_owned(frame, fd)
            return
        session = self._retained.get(sid)
        if session is None or meta.get("session_generation") != session.generation:
            raise RuntimeError("PROTOCOL_ERROR: wrong session generation")
        if event != code.SESSION_ROOT_LIVE and (
                meta.get("pane_id") != session.pane_id or meta.get("workspace_path") != session.cwd):
            raise RuntimeError("PROTOCOL_ERROR: wrong routing attribution")
        if event == code.SESSION_EXITED:
            self._receipt({"event": "exit", **meta})
            self._on_exited(session, meta, generation)
        elif event == code.SESSION_HANGUP:
            self._receipt({"event": "hangup", "session_id": sid, "session_generation": session.generation})
            if session.handle is not None:
                self._close(session, reason="hangup")
        elif event == code.SESSION_ROOT_LIVE:
            session.root_live = True
            self._receipt({"event": "root_live", "session_id": sid, "session_generation": session.generation})
            self._maybe_publish_exit(session, generation)
        elif event == code.SESSION_CLEANED:
            self._receipt({"event": "cleaned", **meta})
            self._task(self._finish_cleanup(session, meta))
        elif event == code.SESSION_OWNERSHIP:
            pages = self._ownership_pages.setdefault(sid, [])
            if meta["part"] == 0:
                pages.clear()
            pages.extend(meta["descendants"])
            if meta["final"]:
                session.descendants = {entry["pid"]: entry["start_value"] for entry in pages}
                self._ownership_pages.pop(sid, None)
                self._task(asyncio.to_thread(in_data_dir(self._data_dir, pty_registry.update_descendants,
                                                          {session.proc.pid: session.descendants})))
        else:
            raise RuntimeError("PROTOCOL_ERROR: unknown event")

    def _on_late_observation_part(self, frame: wire.Frame, expired: tuple[int, str | None]) -> None:
        """E1 v8 14: a part for an id whose `OBSERVE` expired locally is **ignored and counted**, never
        buffered: an observation cannot be cancelled at the sidecar, so it may still be sending. The part
        is held to the same order, 64 KiB and per-observation part/byte counters the open observation had (an
        endless stream still ends at the first part beyond them: protocol error -> loss). A part for an id that
        was never opened, answered, evicted from the expired table, or that was not an `OBSERVE` is not
        here: it is a protocol error."""
        if expired[0] != code.OBSERVE:
            raise RuntimeError("PROTOCOL_ERROR: observation part for an id that is not an OBSERVE")
        counters = self._late_counters.get(frame.correlation_id)
        if counters is None:  # expired before it had counters (cannot happen for an admitted observe)
            counters = self._late_counters[frame.correlation_id] = [0, 0]
        if frame.meta.get("part") != counters[0]:
            raise RuntimeError("PROTOCOL_ERROR: observation order")
        if len(frame.blob) > wire.MAX_FRAME_R2P - 16:
            raise RuntimeError("PROTOCOL_ERROR: observation part over the frame bound")
        counters[0] += 1
        counters[1] += len(frame.blob)
        if counters[0] > self._limits["max_observation_parts"] or counters[1] > self._limits["max_observation_bytes"]:
            raise RuntimeError("PROTOCOL_ERROR: observation exceeds the advertised bounds")
        self._ledger.late_part(len(frame.blob))

    def _on_answer(self, frame: wire.Frame) -> None:
        """A RESPONSE/ERROR must carry the code and id of an open request (E1 2.3, 2.4). An answer for an id
        nobody asked, a repeat of an answered id, or the wrong code is a protocol error. The one
        exception is the single late answer to a request whose waiter already gave up."""
        cid = frame.correlation_id
        expected = self._request_codes.get(cid)
        if expected is None:
            late = self._expired.pop(cid, None)
            if late is None:
                raise RuntimeError(f"PROTOCOL_ERROR: unsolicited or duplicate answer for request {cid}")
            late_code, prevented_sid = late
            self._late_counters.pop(cid, None)
            self._ledger.late_answer()
            if frame.code != late_code:
                raise RuntimeError(f"PROTOCOL_ERROR: answer code {frame.code} for request {cid} (asked {late_code})")
            if prevented_sid is not None and not (
                    frame.frame_type is FrameType.ERROR and frame.meta.get("code") == "CREATE_CANCELLED"):
                raise RuntimeError("PROTOCOL_ERROR: a create settled `prevented` was answered otherwise")
            return
        if frame.code != expected:
            raise RuntimeError(f"PROTOCOL_ERROR: answer code {frame.code} for request {cid} (asked {expected})")
        future = self._pending.get(cid)
        if future is None or future.done():
            raise RuntimeError(f"PROTOCOL_ERROR: answer for request {cid} that is not open")
        del self._request_codes[cid]
        future.set_result(frame)

    def _forget_request(self, request_id: int, prevented_sid: str | None = None) -> None:
        """The waiter for `request_id` is done. An unanswered id moves to the expired table (E1 v8 10.1,
        14): bounded to `EXPIRED_IDS` ids, oldest evicted. An expired `OBSERVE` keeps the counters it had, so
        its late parts are held to the same per-observation limits as an open one."""
        if not request_id:
            return
        self._pending.pop(request_id, None)
        if (op_code := self._request_codes.pop(request_id, None)) is not None:
            self._expired[request_id] = (op_code, prevented_sid)
            if op_code == code.OBSERVE and (counters := self._observing.get(request_id)) is not None:
                self._late_counters[request_id] = list(counters)
            while len(self._expired) > EXPIRED_IDS:
                evicted = next(iter(self._expired))
                del self._expired[evicted]
                self._late_counters.pop(evicted, None)

    def _on_observation_part(self, frame: wire.Frame) -> None:
        """E1 14 bounds, enforced as each part arrives: a part for an id that is not an open
        OBSERVE (unknown, or already answered), out of order, over 64 KiB, or beyond the part or
        byte counters is a protocol error, so memory never exceeds the advertised bound."""
        expired = self._expired.get(frame.correlation_id)
        if expired is not None:
            self._on_late_observation_part(frame, expired)
            return
        counters = self._observing.get(frame.correlation_id)
        future = self._pending.get(frame.correlation_id)
        if counters is None or future is None or future.done():
            raise RuntimeError("PROTOCOL_ERROR: observation part for no open observation")
        if frame.meta.get("part") != counters[0]:
            raise RuntimeError("PROTOCOL_ERROR: observation order")
        if len(frame.blob) > wire.MAX_FRAME_R2P - 16 or frame.wire_size - wire.HEADER_LEN > wire.MAX_FRAME_R2P:
            raise RuntimeError("PROTOCOL_ERROR: observation part over the frame bound")
        counters[0] += 1
        counters[1] += len(frame.blob)
        if counters[0] > self._limits["max_observation_parts"] or counters[1] > self._limits["max_observation_bytes"]:
            raise RuntimeError("PROTOCOL_ERROR: observation exceeds the advertised bounds")
        self._observations.setdefault(frame.correlation_id, []).append(frame.blob)

    def _runtime_process_handle(self) -> int:
        """The sidecar's process handle, the source of every `DuplicateHandle` pull (never an OpenProcess by pid)."""
        popen = self._runtime._transport.get_extra_info("subprocess")
        return int(popen._handle)

    def _on_owned_windows(self, frame: wire.Frame) -> None:
        self._on_owned(frame, None)

    def _on_owned(self, frame: wire.Frame, fd: int | None) -> None:
        """`SESSION_OWNED`: validate it against the launch it claims to answer, claim the descriptor into a
        HandoffSlot, run the binding checks (6.4) and register the provisional session, all before the
        next await (I2, I3). Nothing is claimed or persisted for an event that does not match."""
        meta = frame.meta
        sid = meta.get("session_id")

        def reject(reason: str):
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
            raise RuntimeError(f"PROTOCOL_ERROR: SESSION_OWNED {reason}")

        launch = self._launches.get(sid) if isinstance(sid, str) else None
        entry = self._dispatched.get(sid) if isinstance(sid, str) else None
        if launch is None or entry is None or sid in self._slots or sid in self._prevented:
            reject("for a session this runtime did not create, already owns or settled as prevented")
        create_id = meta.get("create_id")
        if type(create_id) is not int or create_id != entry.get("request_id"):
            reject("names a different create")
        if (meta.get("session_generation") != launch["session_generation"] or meta.get("pane_id") != launch["pane_id"]
                or meta.get("workspace_path") != launch["workspace_path"]):
            reject("carries the wrong session generation, pane or workspace")
        root, pty = meta.get("root"), meta.get("pty")
        if (not isinstance(root, dict) or any(type(root.get(k)) is not int or root[k] <= 0 for k in ("pid", "pgid"))
                or not isinstance(root.get("start_kind"), str) or not isinstance(root.get("start_value"), str)
                or not root["start_value"] or not isinstance(pty, dict)
                or (not WINDOWS and not isinstance(pty.get("tty"), str))
                or (WINDOWS and any(type(pty.get(k)) is not int or pty[k] <= 0 for k in ("input", "output")))):
            reject("is malformed")
        if WINDOWS:
            # The registry and the orphan sweep compare psutil's creation time; the sidecar names the same
            # instant as a FILETIME. Reconcile the two once, here, or refuse the session (fail closed).
            reconciled = winrt.registry_start(root["pid"], root["start_value"])
            if not reconciled:
                reject("names a root whose creation time cannot be reconciled")
            root = {**root, "start_value": reconciled}
        failure: HandoffRejected | None = None
        if WINDOWS:
            try:
                ends = winrt.pull_pipe_ends(self._runtime_process_handle(), pty["input"], pty["output"])
            except winrt.PullRejected as rejected:
                failure = HandoffRejected(rejected.check, rejected.detail)
            else:
                slot = winrt.WindowsMaster(str(sid), *ends)
                slot.tty = f"conpty:{sid}"  # a ConPTY has no device name; unique per session
                self._slots[sid] = slot
        else:
            slot = HandoffSlot(str(sid), fd)
            self._slots[sid] = slot
            os.set_blocking(fd, False)
        build = self._provisional.get(sid)
        if sid not in self._retained and build is not None:
            self._retained[sid] = build(root)
        entry = self._dispatched.get(sid)
        if entry is not None:
            entry["identity"] = {"pid": root["pid"], "lstart": root.get("start_value")}
        if self._receipt_root and not WINDOWS:  # the per-create kernel checkpoint, inline, before any later step can run
            self._receipt(evaluation.checkpoint(fd, str(sid), launch["pane_id"], launch["session_generation"],
                                                str(pty.get("tty")), root))
        if not WINDOWS:
            try:
                slot.tty = check_master(fd, pty.get("tty"), self._claimed_ttys)
            except HandoffRejected as rejected:
                failure = rejected
        if failure is not None:
            rejected = failure
            # Fail closed: close our duplicate, kill the root named in this frame by identity, no
            # ADOPT. Nothing was read from or written to any descriptor.
            self._handoff_failed[sid] = rejected
            self._close_slot(sid)
            self._receipt({"event": "handoff_rejected", "session_id": sid, "session_generation": meta.get("session_generation"),
                           "check": rejected.check, "detail": rejected.detail})
            if (session := self._retained.get(sid)) is not None:
                self._task(self._kill_rejected(session))
        else:
            self._claimed_ttys.add(slot.tty)
            # E1 v8 8 item 4: read the master from now on (bounded), so a CLI that finishes before the
            # create is published keeps its output, and an unpublished session being killed can exit.
            if WINDOWS:
                slot.cap = _BUF_CAP
                slot.start_pump(self._loop)
            else:
                slot.start_reading(self._loop, _BUF_CAP)
            self._receipt({"event": "handoff_received", "kind": "duplicate_handle" if WINDOWS else "scm_rights", "platform": RECEIPT_PLATFORM,
                           "validated": True, "session_id": sid, "session_generation": meta.get("session_generation"),
                           "owned_frame_bytes": frame.wire_size})
        self._receipt({"event": "owned", "runtime_generation": self._runtime_generation,
                       **{k: v for k, v in meta.items() if k != "pty"}})
        self._task(self._persist_owned({"session_id": sid, "session_generation": meta["session_generation"], "root": root}))

    async def _kill_rejected(self, session: RuntimeSession) -> None:
        with suppress(Exception):
            await self._request("kill", {"force": True}, session)

    def _close_slot(self, sid: str) -> None:
        """Close a claimed-but-unpublished descriptor (once) and free its tty name."""
        slot = self._slots.pop(sid, None)
        if slot is None:
            return
        if WINDOWS:
            slot.close()
        elif slot.fd is not None:
            slot.stop_reading()
            fd, slot.fd = slot.fd, None
            with suppress(OSError):
                os.close(fd)
        self._claimed_ttys.discard(slot.tty)

    def _on_exited(self, session: RuntimeSession, record: dict, generation: str) -> None:
        """Root exit only (5.5): nothing about the stream, the descendants or the cleanup."""
        # Only the root-exit fact is recorded. An unpublished record keeps its stream open: E1 5.2 I3 lets
        # this event precede the CREATE answer, and the reader that attaches at publication must still
        # see the output, the EOF and run `_finish_stream`.
        session.exit_record = record
        session.proc.returncode = record.get("exit_code")
        self._maybe_publish_exit(session, generation)

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

    async def _request(self, op: str, args: dict | None = None, session: RuntimeSession | None = None,
                       *, blob: bytes = b"", deadline: float | None = None) -> dict:
        if self._lost:
            raise RuntimeError(self._lost)
        if op == "observe":
            async with self._observe_slots:  # at most `observe_inflight` open observations (E1 14)
                return await self._request_admitted(op, args, session, blob, deadline)
        return await self._request_admitted(op, args, session, blob, deadline)

    async def _inbound_acquire(self, size: int) -> None:
        """Byte-accounted admission against `max_inbound_bytes` (E1 1.4): waits, never drops. A request
        larger than the whole budget (not possible within the frame bounds) is admitted alone."""
        cap = self._limits["max_inbound_bytes"]
        while self._inbound_used and self._inbound_used + size > cap:
            if self._lost:  # an admission wait is loss-aware: it never outlives the generation
                raise RuntimeError(self._lost)
            waiter = self._loop.create_future()
            self._inbound_waiters.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._inbound_waiters:
                    self._inbound_waiters.remove(waiter)
        if self._lost:
            raise RuntimeError(self._lost)
        self._inbound_used += size

    def _inbound_release(self, size: int) -> None:
        self._inbound_used -= size
        for waiter in self._inbound_waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def _request_admitted(self, op: str, args: dict | None, session: RuntimeSession | None,
                                blob: bytes, deadline: float | None) -> dict:
        async with self._admission:
            meta = dict(args or {})
            if session:
                meta.update(session_id=session.id, session_generation=session.generation)
            tag = wire.runtime_tag(self._runtime_generation)
            size = len(wire.encode(wire.Frame(FrameType.REQUEST, _OPS[op], 0, 1, tag, meta, blob)))
            if size - wire.HEADER_LEN > wire.MAX_META_P2R + wire.MAX_BLOB_P2R:
                raise RuntimeError("BAD_REQUEST: private request exceeds the frame bound")
            await self._inbound_acquire(size)
            request_id = 0
            if self._lost:  # lost while waiting for byte admission
                self._inbound_release(size)
                raise RuntimeError(self._lost)
            keep = False  # a traced create's caller or resolver releases its pending entry
            future = self._loop.create_future()
            try:
                async with self._send_lock:
                    if self._sending is not None:
                        await asyncio.wait({self._sending})  # a frame whose caller left is finished first
                        if self._lost:
                            raise RuntimeError(self._lost)
                    if self._request_number >= 2**64 - 1:
                        raise RuntimeError("REQUEST_ID_EXHAUSTED")
                    self._request_number += 1
                    request_id = self._request_number
                    encoded = wire.encode(wire.Frame(FrameType.REQUEST, _OPS[op], 0, request_id, tag, meta, blob))
                    self._pending[request_id] = future
                    self._request_codes[request_id] = _OPS[op]
                    if op == "observe":
                        self._observing[request_id] = [0, 0]  # parts, bytes
                    if (on_dispatch := _ON_DISPATCH.get()) is not None:
                        on_dispatch(request_id, future)
                        keep = True
                    # E1 framing: a frame goes out whole or the channel is lost. A caller's
                    # cancellation never cuts the write (only a retirement does, closing the
                    # channel with it); the next sender waits for it under the send lock.
                    send = self._sending = asyncio.ensure_future(
                        self._send_windows(encoded) if WINDOWS else self._loop.sock_sendall(self._sock, encoded))

                    def sent(task: asyncio.Task) -> None:
                        if self._sending is task:
                            self._sending = None
                        if not task.cancelled() and task.exception() is None:
                            self._ledger.sent(_OPS[op], len(encoded))
                    send.add_done_callback(sent)
                    await asyncio.shield(send)
                if deadline is None:
                    deadline = self._limits[{"ping": "ping_ms", "create": "create_answer_ms"}.get(op, "request_ms")] / 1000
                frame = await asyncio.wait_for(asyncio.shield(future), deadline)
                if frame.frame_type is FrameType.ERROR:
                    error = frame.meta
                    if op == "observe" and error.get("code") == "IO_FAILED" and self._observations.get(request_id) is not None:
                        return self._observation_error(request_id, frame)
                    if op == "create" and error.get("code") == "CLEANUP_FAILED":
                        # E1 10.4: a gated child that could not be verified reaped ends the generation.
                        self._lose(f"CLEANUP_FAILED: create {request_id}: {str(error.get('message'))[:200]}")
                    refused = _RuntimeRefused(f"{error.get('code')} at {error.get('stage')}: {error.get('message')}")
                    refused.meta = error  # effect, steps and survivors for the caller that reports them
                    raise refused
                result = frame.meta
                if op == "observe" and "observation_parts" in result:
                    parts = self._observations.get(request_id, [])
                    if result["observation_parts"] != len(parts):
                        raise RuntimeError("PROTOCOL_ERROR: incomplete observation")
                    document = json.loads(b"".join(parts))
                    if result.get("raw_anchor"):
                        document["anchor_blob"] = frame.blob
                    return document
                return result
            except TimeoutError as error:
                if op not in ("ping", "create"):  # a create is governed by the single rule of E1 10.1
                    self._probe_after_timeout()
                raise RuntimeError(f"REQUEST_TIMEOUT: {op}") from error
            finally:
                if not keep:
                    self._forget_request(request_id)
                self._observations.pop(request_id, None)
                self._observing.pop(request_id, None)
                self._inbound_release(size)
                if future.done() and not future.cancelled():
                    future.exception()

    def control_ledger(self) -> dict:
        """The control-plane ledger so far (counts only); a receipt `control_ledger` is written with it."""
        snapshot = self._ledger.snapshot()
        self._receipt({"event": "control_ledger", "runtime_generation": self._runtime_generation, **snapshot})
        return snapshot

    def _probe_after_timeout(self) -> None:
        """E1 10.5: a request's expiry fails that request only; then one PING decides. The runtime
        is declared lost only if the PING is also unanswered within `ping_ms` (a wedged sidecar)."""
        if self._probing or self._lost:
            return
        self._probing = True

        async def probe() -> None:
            try:
                await self._request("ping")
            except RuntimeError as error:
                if not self._lost and str(error).startswith("REQUEST_TIMEOUT"):
                    self._lose("RUNTIME_LOST: no answer to PING after a request deadline")
            finally:
                self._probing = False

        task = asyncio.create_task(probe())
        self._late_tasks.add(task)
        task.add_done_callback(self._late_tasks.discard)

    def _observation_error(self, request_id: int, frame: wire.Frame) -> dict:
        """An activity error that carries progress (10.4): validated, then applied by the reader."""
        error = frame.meta
        result = json.loads(b"".join(self._observations.get(request_id, [])))
        if (result.get("mode") != "activity" or not result.get("error")
                or result["error"] != error.get("message") or error.get("stage") != "io"):
            raise RuntimeError("PROTOCOL_ERROR: invalid activity error progress")
        result["error"] = f"{error['code']} at {error['stage']}: {error['message']}"
        return result

    async def observe(self, vendor: str, parser: str, args: dict, *, match, expected: bytes = b"",
                      validate=None, receipt=None) -> dict:
        """One vendor-generic observation (E1 14). Everything vendor-specific is supplied by the
        adapter module: `match(sessions) -> session | None` attributes it to a live session,
        `validate(result, session)` checks vendor invariants, `receipt(result)` adds vendor fields.
        The expected anchor travels as raw bytes (`expected`); a returned raw anchor is left in
        `result["anchor_blob"]` (present only when the parser returns one)."""
        await self._ensure_started()
        session = match(tuple(self._retained.values()))
        result = await self._request("observe", {"vendor": vendor, **args}, session, blob=expected)
        if (result.get("parser") != parser or result.get("path") != args["path"]
                or result.get("mode") != args["mode"]):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation parser/path/mode")
        context = result["ownership"]
        if session and any(context.get(key) != value for key, value in (
                ("session_id", session.id), ("session_generation", session.generation),
                ("pane_id", session.pane_id), ("workspace_path", session.cwd))):
            raise RuntimeError("PROTOCOL_ERROR: wrong observation ownership")
        if not session and any(context.get(key) is not None for key in (
                "session_id", "session_generation", "pane_id", "workspace_path")):
            raise RuntimeError("PROTOCOL_ERROR: historical observation claimed process ownership")
        if validate is not None:
            validate(result, session)
        self._receipt({**context, "runtime_generation": self._runtime_generation,
                       **{key: result[key] for key in ("parser", "path", "identity", "mode") if key in result},
                       **(receipt(result) if receipt is not None else {}),
                       **({"error": result["error"]} if result.get("error") else {})})
        return result

    def create(self, **kwargs):
        raise RuntimeError("Selected Rust runtime requires native async create; no Python fallback")

    async def create_async(self, **kwargs) -> RuntimeSession:
        try:
            return await self._create_async(**kwargs)
        except BaseException as failure:
            if not getattr(failure, "_failure_receipted", False):
                # Refused before anything was dispatched (a fence, a missing cwd, a cancel before the send):
                # still exactly one structured failed-create receipt (E2 3 item 2).
                self._receipt({"event": "create_failed", "session_id": None, "pane_id": kwargs.get("pane_id"),
                               "session_generation": None, "error": type(failure).__name__, "code": None, "stage": "pre-dispatch",
                               "effect": "none", "message": str(failure)[:200], "root": None})
            raise

    async def _create_async(self, **kwargs) -> RuntimeSession:
        from .cli_runtime_adapters import NATIVE_VENDORS
        if kwargs.get("agent_key") not in (None, "", "terminal", *NATIVE_VENDORS):
            raise RuntimeError("Rust agent runtime not yet integrated; no Python fallback")
        cancelled = kwargs.pop("cancelled", None)
        cancel_record = kwargs.pop("cancel_record", None)
        await self._ensure_started()
        keys = self._launch_keys(kwargs)
        for held in self._retained.values():
            if held.root_live and not held.cleanup.is_set() and held.launch_keys & keys:
                # E1 5.5 R-c (iv): refused at once; waiting could never resolve it by itself.
                raise RuntimeError(
                    "CREATE_BLOCKED: the previous CLI of this pane closed its terminal but is still running "
                    f"(pid {held.proc.pid}, {int(time.monotonic() - held.ended_monotonic)} s since its terminal ended); "
                    "release or rebuild the pane to stop it")
        await self._await_unresolved(keys)
        await self._ensure_started()  # the awaited launch may have ended in a loss that retired the generation
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
                                  RuntimeProcessView(root["pid"]), generation, metadata, root, published=False,
                                  launch_keys=keys)

        self._provisional[sid] = provisional
        dispatched: dict = {}
        native: dict = {}

        def on_dispatch(request_id: str, future: asyncio.Future) -> None:
            # The dispatch boundary: after admission and the send lock, with the bytes
            # not yet written. A cancellation that arrived while waiting is honoured here,
            # in the same synchronous step that records the dispatch for the canceller.
            if cancelled is not None and cancelled():
                raise CreateCancelled("create cancelled at the dispatch boundary")
            dispatched.update(request_id=request_id, future=future, t0=time.monotonic())
            self._dispatched[sid] = {"generation": self._runtime_generation, "identity": None,
                                     "request_id": request_id}
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
            if (rejected := self._handoff_failed.pop(sid, None)) is not None:
                # The descriptor failed its binding check: its root is being killed by identity and
                # no ADOPT was sent. The create fails once that cleanup is verified.
                try:
                    await asyncio.wait_for(session.cleanup.wait(), self._limits["cleanup_ms"] / 1000)
                except TimeoutError:
                    pass
                settle("cleaned" if session.cleanup.is_set() and not session.cleanup_error else "cleanup-failed")
                raise RuntimeError(f"HANDOFF_FAILED: binding check {rejected.check}: {rejected.detail}")
            try:
                await asyncio.wait_for(asyncio.shield(self._owned[sid]), OWNED_RECEIPT_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A known native spawn must not survive a failed application receipt.
                await self.kill(sid, force=True)
                settle("cleaned")
                raise
            await honour_cancel(session)
            if path := kwargs.get("output_log_file"):
                try:
                    await asyncio.to_thread(Path(path).parent.mkdir, parents=True, exist_ok=True)
                    session.output_log_fp = await asyncio.to_thread(open, path, "a", encoding="utf-8", buffering=1)
                    _register_live_log(sid, path)
                except OSError as error:
                    log.warning("cannot open runtime output log: %s", error)
            await honour_cancel(session)
            # Publication: no await between the last cancellation check and the hand-over. The
            # reader attaches here, not before: until now the CLI's output waited in the kernel
            # buffer and nothing escaped routing (E1 8 item 4).
            slot = self._slots.pop(sid, None)
            if slot is None or (WINDOWS and slot.closed) or (not WINDOWS and slot.fd is None):
                raise RuntimeError("HANDOFF_FAILED: no descriptor was received for this session")
            if WINDOWS:
                slot.proc = session.proc
                session.handle = slot  # the pump keeps running; the Terminal Stream attaches to it
                session.tty = slot.tty
            else:
                slot.stop_reading()
                session.handle = MasterHandle(slot.fd, session.proc)
                session.tty, slot.fd = slot.tty, None
            session.handle.start_reading(self._loop, lambda s=session: self._on_readable(s))
            self._receipt({"event": "handle_attached", "session_id": sid,
                           "session_generation": generation, "handle": type(session.handle).__name__})
            self._task(self._adopt(session))
            session.published = True
            self._sessions[sid] = session
            # What the pre-publication reader collected enters the Terminal Stream exactly as if its own
            # reader had read it (same absorb, same log mirror, same 5 MiB drop-oldest accounting), and a
            # stream that already ended is closed the way `_on_readable` would have closed it.
            chunks, dropped, eof, errored = slot.take()
            if dropped:
                self._out_dropped[session.id] = self._out_dropped.get(session.id, 0) + dropped
            handed = 0
            for chunk in chunks:  # one absorb per read, exactly as `_on_readable` would have done
                self._absorb_output(session, chunk, len(chunk))
                handed += len(chunk)
            if eof or errored:
                self._close(session, reason="error" if errored else "exit")
            self._receipt({"event": "preread_handed_over", "session_id": sid, "session_generation": generation,
                           "bytes": handed, "dropped": dropped, "read_total": slot.read_total,
                           "stream_ended": bool(eof or errored)})
            settle("published")
            return session
        except BaseException as failure:
            # One structured failed-create receipt per create that did not produce a published session
            # (E2 3 item 2): its stage, the refusal's code/effect when the sidecar answered, and the root if one
            # was ever identified. Counts and identifiers only.
            meta = getattr(failure, "meta", None) or {}
            self._receipt({"event": "create_failed", "session_id": sid, "pane_id": kwargs.get("pane_id"),
                           "session_generation": generation, "error": type(failure).__name__,
                           "code": meta.get("code"), "stage": meta.get("stage"), "effect": meta.get("effect"),
                           "message": str(failure)[:200], "root": (session.root if session is not None else None)})
            with suppress(Exception):
                failure._failure_receipted = True
            raise
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
                    self._forget_request(dispatched["request_id"])

    async def _adopt(self, session: RuntimeSession) -> None:
        """ADOPT (5.3): a lifecycle marker; Python does not wait for it before presenting."""
        with suppress(Exception):
            await self._request("adopt", {}, session)
        if WINDOWS:
            # 7.3: the pump is reading continuously, so the console may close once the root exits.
            with suppress(Exception):
                await self._request("tty_servicing", {}, session)

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
            async with asyncio.timeout(LAUNCH_BLOCK_S):
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
        prevented = False
        try:
            if result is None and stub.proc.pid:
                # E1 10.1: SESSION_OWNED was received (the root is identified and journalled; its
                # descriptor sits in a HandoffSlot until the verified cleanup) but the answer is
                # missing: one-session verified cleanup decides, never a wait for the answer.
                result = {"root": stub.root}
            if result is None:
                # Nothing identifies the create. A CANCEL that answers `prevented` is a proof that the gate
                # never opened (10.1); without it the create's own answer may still come, but only until
                # `create_answer_ms` after its dispatch. After that the generation cannot be trusted.
                state = None
                with suppress(Exception):
                    state = (await self._request("cancel", {"target_id": dispatched["request_id"]},
                                                 deadline=self._limits["ping_ms"] / 1000)).get("state")
                if state == "prevented":
                    outcome = "prevented"
                    self._prevented.add(sid)
                    prevented = True
                    self._retained.pop(sid, None)
                    self._dispatched.pop(sid, None)
                    return
                left = self._limits["create_answer_ms"] / 1000 - (time.monotonic() - dispatched["t0"])
                try:
                    response = await asyncio.wait_for(asyncio.shield(dispatched["future"]), max(left, 0))
                except TimeoutError:
                    outcome = "runtime-loss"
                    if not self._lost:
                        self._lose(f"RUNTIME_LOST: create {dispatched['request_id']} was never identified or answered "
                                   "within create_answer_ms")
                    return
                except Exception:  # runtime lost: its invalidation owns the cleanup
                    outcome = "runtime-lost"
                    return
                if response.frame_type is FrameType.ERROR:
                    outcome = "refused"
                    self._retained.pop(sid, None)
                    self._dispatched.pop(sid, None)
                    return
                result = response.meta
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
            self._forget_request(dispatched.get("request_id", 0), sid if prevented else None)
            self._launches.pop(sid, None)
            future = self._owned.pop(sid, None)
            if future and future.done() and not future.cancelled():
                future.exception()
            if outcome in ("cleaned", "refused", "prevented"):
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
            await asyncio.wait_for(session.cleanup.wait(), self._limits["cleanup_ms"] / 1000)
        return not session.cleanup_error

    async def kill_verified(self, sid: str) -> bool:
        """kill(), reporting whether the root's cleanup was verified (False when unknown)."""
        session = self._retained.get(sid) or self._sessions.get(sid)
        if session is None:
            return False
        await self.kill(sid, force=True)
        return session.cleanup.is_set() and not session.cleanup_error

    # The Terminal Stream is the baseline's: the existing `isinstance(..., RustTerminalService)`
    # branches in the handlers reach it through these thin shims (the baseline files stay
    # unmodified, E2 9). Input, interrupt and foreground queries run over the handed-off master.
    async def activate_async(self, sid: str) -> None:
        return None  # the reader attaches at publication (E1 8); there is nothing to activate

    async def write_async(self, sid: str, data: str) -> int:
        self._require(sid)
        return self.write(sid, data)

    async def shell_in_foreground_async(self, sid: str) -> bool | None:
        return self.shell_in_foreground(sid)

    async def interrupt_async(self, sid: str) -> None:
        self.interrupt(sid)

    @asynccontextmanager
    async def control_async(self, sid: str, cols: int, rows: int, *, redraw: bool = False):
        """The resize barrier (E1 9.3): the baseline's drain, with the sidecar's acknowledgement
        inside the lock this code owns. The public acknowledgement is sent in the `yield`, still
        under the lock, so output absorbed after the slurp is emitted after it (B2)."""
        session = self._sessions.get(sid)
        if session is None or session.closed:
            yield
            return
        async with session.control_lock:
            barrier = self._resize_barriers.setdefault(session.id, asyncio.Lock())
            async with barrier:
                await self._drain_output_behind_barrier(session)
                await self._request("resize", {"cols": cols, "rows": rows, "redraw": redraw}, session)
                yield
            if not session.closed and not barrier.locked() and self._out_buffers.get(session.id):
                self._flush_output(session)

    # ---- stream end (E1 8): the baseline `_close` replaced by an ordered, awaited version -------

    def _close(self, session: RuntimeSession, *, reason: str) -> None:
        """Called from `_on_readable` and the SESSION_HANGUP handler. Nothing is flushed,
        discarded, closed, published or popped here: `_finish_stream` does it, in order."""
        if session.stream_ending:
            return
        session.stream_ending = True
        session.stream_reason = {"exit": "eof", "killed": "eof", "shutdown": "eof"}.get(reason, reason)
        if session.handle is not None:
            session.handle.stop_reading()  # terminal: no reader or pause timer can restart it
        self._task(self._finish_stream(session))

    def _retired_generation(self, generation: str) -> bool:
        return generation in self._retired

    def _close_stream_state(self, session: RuntimeSession) -> None:
        """The stream half of the baseline `_close`, in the baseline's order (E1 8 step 4, 8.1 step 4):
        mark closed, drop input state, cancel the batch and pause timers, flush the log decoder's tail
        into the log. The handle is closed by the caller; the log by the publication or loss path."""
        session.closed = True
        self._unwatch_writable(session)
        self._in_buffers.pop(session.id, None)
        self._recent_chunks.pop(session.id, None)
        self._echo_probe.pop(session.id, None)
        self._end_input_block(session)
        if handle := self._out_handles.pop(session.id, None):
            handle.cancel()
        if timer := self._pause_timers.pop(session.id, None):
            timer.cancel()
        decoder = self._decoders.pop(session.id, None)
        if decoder and session.output_log_fp:
            if tail := decoder.decode(b"", final=True):
                try:
                    session.output_log_fp.write(_clean_for_log(tail))
                except Exception as error:  # noqa: BLE001
                    log.warning("output log write failed: %s", error)
        self._resize_barriers.pop(session.id, None)

    async def _finish_stream(self, session: RuntimeSession) -> None:
        """Drain everything retained, then close our master, then `TTY_EOF`, then try to publish
        the exit. The retirement fence is checked after every awaited boundary and before every
        close, send or publication: once the generation is retired, loss owns the session."""
        generation = self._runtime_generation
        async with session.control_lock:  # an in-progress resize finishes or fails first
            if self._retired_generation(generation):
                return
            barrier = self._resize_barriers.setdefault(session.id, asyncio.Lock())
            async with barrier:  # the inherited `_flush_output` defers while it is held
                if self._retired_generation(generation):
                    return
                await self._drain_output_behind_barrier(session)  # the unchanged baseline helper
                if self._retired_generation(generation):
                    return
                # `closed` is still false until here, so none of the baseline's discard paths ran.
                self._close_stream_state(session)
                session.handle.close()  # the first moment our master closes
                self._claimed_ttys.discard(session.tty)
        if self._retired_generation(generation):
            return
        session.ended_monotonic = time.monotonic()
        with suppress(Exception):  # a lost sidecar is handled by loss, never by this task
            await self._request("tty_eof", {"reason": session.stream_reason}, session)
        if self._retired_generation(generation):
            return
        session.stream_done = True
        self._maybe_publish_exit(session, generation)

    def _maybe_publish_exit(self, session: RuntimeSession, generation: str) -> None:
        """R-b: the one `terminal.exit` once the stream ended AND the root exited (or is
        root-live). Output before the exit is ordered before it by construction. `generation` is the
        one the calling task captured at its start (E1 8), never the service's current one."""
        if session.exit_published or not session.stream_done or session.hidden:
            return
        if self._retired_generation(generation):
            return  # loss owns every session of a retired generation
        record = session.exit_record
        if record is None and not session.root_live:
            return
        session.exit_published = True
        if record is not None:
            session.close_reason = "error" if session.stream_reason == "error" else record["reason"]
            session.proc.returncode = session.exit_code = record.get("exit_code")
            session.uptime_ms = record.get("uptime_ms")
        else:  # root-live: the exit is published with exit_code null, as the baseline does
            session.close_reason = "error" if session.stream_reason == "error" else "exit"
            session.proc.returncode = session.exit_code = None
            session.uptime_ms = max(0, round((time.monotonic() - session.started_monotonic) * 1000))
        session.exit_signal = None
        if session.exit_code is not None and session.exit_code < 0:
            try:
                session.exit_signal = signal.Signals(-session.exit_code).name
            except ValueError:
                session.exit_signal = f"SIG{-session.exit_code}"
        if session.stream_reason == "error":
            self._task(self._kill_after_error(session))
        self._task(self._publish_and_retire(session, generation))

    async def _kill_after_error(self, session: RuntimeSession) -> None:
        """R-f: a read error closed the PTY while the root may still run: put it down."""
        with suppress(Exception):
            await self._request("kill", {"force": True}, session)

    async def _publish_and_retire(self, session: RuntimeSession, generation: str) -> None:
        """The retirement fence (E1 8, 11): once the generation is retired, loss owns the session. A task
        that had not started publishing does nothing (loss presents the exit); one that did start is the
        session's one exit and is neither repeated nor allowed to settle the session afterwards."""
        if self._retired_generation(generation):
            return
        try:
            await self._publish_exit(session)
        finally:
            session.joined.set()
        if self._retired_generation(generation):
            return
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
        self._write_locks.pop(session.id, None)
        self._out_dropped.pop(session.id, None)
        if session.hidden:
            return  # the renderer never learned of this create
        session.exit_started = True  # from here the exit is the sink's; loss must not present a second one
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
        generation = self._runtime_generation
        if self._retired_generation(generation):
            return  # loss owns every session of a retired generation
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
                _done, unsettled = await asyncio.wait(writing, timeout=PERSIST_WAIT_S)
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
        if session.handle is None:
            # A create that was never published (cancelled, abandoned, rejected, lost answer):
            # its master is released only now, after the verified cleanup (E1 10.1).
            self._close_slot(session.id)
            session.joined.set()
        session.cleanup.set()
        self._retire(session)

    async def kill(self, sid: str, force: bool = False) -> None:
        session = self._retained.get(sid)
        if not session:
            return
        if not session.cleanup.is_set():
            await self._request("kill", {"force": force}, session)
            try:
                await asyncio.wait_for(session.cleanup.wait(), self._limits["cleanup_ms"] / 1000)
            except TimeoutError:
                # E1 10.4: SESSION_CLEANED not arriving within cleanup_ms is `unknown` -> runtime loss.
                self._lose(f"CLEANUP_FAILED: no SESSION_CLEANED for {session.id} within cleanup_ms")
                raise
        if session.cleanup_error:
            raise RuntimeError(session.cleanup_error)
        # The exit task preserves the outward data-before-exit fence: wait for the join.
        async with asyncio.timeout(self._limits["request_ms"] / 1000):
            await session.joined.wait()
        if self._lost:
            raise RuntimeError(self._lost)

    async def wait_until_reaped(self, sid: str) -> bool:
        session = self._retained.get(sid)
        if session:
            try:
                await asyncio.wait_for(session.cleanup.wait(), self._limits["cleanup_ms"] / 1000)
            except TimeoutError:
                return False  # the cleanup acknowledgement never settled: not reaped
            return not session.cleanup_error
        return True

    def live_session_ids_for_pane(self, pane_id: str) -> list[str]:
        """Keyed on verified cleanup, not on `closed` (E1 5.5 R-c): a root-live session closed its
        terminal but still runs, and the pane's settlement must reach it."""
        if not pane_id:
            return []
        return [s.id for s in self._retained.values()
                if s.pane_id == pane_id and not s.cleanup.is_set() and not s.hidden]

    async def kill_all(self, grace: float = 1.0) -> None:
        if self._invalidation is not None and not self._invalidation.done():
            await asyncio.wait({self._invalidation})
        if self._runtime is None:
            return
        if self._lost:
            if self._runtime.returncode is not None and not self._retained:
                return  # Failed startup was identity-bound and already reaped.
            raise RuntimeError(self._lost)
        # App shutdown sweeps PTYs before its existing watcher/checkpoint drain. Keep
        # observation alive through that drain; the backend-owned channel EOF terminates the
        # sidecar when the backend itself exits.
        owned = tuple(self._retained.values())
        async with asyncio.timeout(self._limits["cleanup_ms"] / 1000):
            # Every session whose SESSION_CLEANED has not arrived, root-live ones included.
            await asyncio.gather(*(self.kill(s.id) for s in owned if not s.cleanup.is_set()))
            await asyncio.gather(*(s.cleanup.wait() for s in owned))
            while pending := self._event_tasks - {self._reader_task, self._stderr_task, self._stdout_task}:
                await asyncio.gather(*tuple(pending))
        if any(s.cleanup_error for s in owned):
            raise RuntimeError("CLEANUP_FAILED at shutdown")
        if self._lost:
            raise RuntimeError(self._lost)
        # Every session ended and its cleanup was acknowledged: nothing this generation
        # spawned is left to name, so its ownership journal has served its purpose.
        with suppress(OSError):
            in_data_dir(self._data_dir, pty_registry.journal_path, self._runtime_generation)().unlink(missing_ok=True)
