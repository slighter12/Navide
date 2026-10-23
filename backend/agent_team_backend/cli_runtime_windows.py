"""The Windows half of the Rust runtime client (design/e1-runtime-protocol-v8b.md 5.2, 7, 11).

On Windows the sidecar owns a ConPTY per session and announces its two pipe ends by value in
`SESSION_OWNED` (a plain frame, no descriptor). This module holds what is specific to that:

* `pull_pipe_ends`: `DuplicateHandle` from the sidecar's *process handle* (never `OpenProcess(pid)`)
  and the 7.4 checks (both are pipes, the input end is a write end, the output end a read end, the two
  are different objects, neither is inheritable);
* `WindowsMaster`: the terminal handle the Terminal Stream runs over. A pump thread blocks in
  `ReadFile` and a writer thread in `WriteFile`; the event loop is told through
  `call_soon_threadsafe`. Closing cancels the blocked I/O (`CancelSynchronousIo`) and joins both
  threads under `THREAD_CLOSE_S`.

Only ctypes is used. Importing this module is safe on every platform: the Win32 bindings are created
lazily, on first use.
"""
from __future__ import annotations

import asyncio
import collections
import ctypes
import errno
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

#: How long a cancelled pump or writer may take to end (E1 7.4, G-W5).
THREAD_CLOSE_S = 2.0
#: Pending output a handle holds for the Terminal Stream (matches its own 5 MiB drop-oldest bound).
_READ_CHUNK = 64 * 1024
_WRITE_CAP = 256 * 1024

_FILE_TYPE_PIPE = 3
_DUPLICATE_SAME_ACCESS = 2
_HANDLE_FLAG_INHERIT = 1
_THREAD_TERMINATE = 0x0001


class PullRejected(Exception):
    """A pulled handle failed a 7.4 check; `check` numbers it in the order the design lists them."""

    def __init__(self, check: int, detail: str):
        super().__init__(f"binding check {check}: {detail}")
        self.check = check
        self.detail = detail


class _Api:
    """The kernel32/kernelbase entry points, bound once with exact signatures."""

    def __init__(self) -> None:
        from ctypes import wintypes
        self.wintypes = wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        base = ctypes.WinDLL("kernelbase", use_last_error=True)
        handle, dword, boolean = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
        pdword = ctypes.POINTER(dword)

        def bind(lib, name, restype, argtypes):
            function = getattr(lib, name)
            function.restype, function.argtypes = restype, argtypes
            return function

        self.duplicate = bind(k32, "DuplicateHandle", boolean,
                              [handle, handle, handle, ctypes.POINTER(handle), dword, boolean, dword])
        self.current_process = bind(k32, "GetCurrentProcess", handle, [])
        self.close = bind(k32, "CloseHandle", boolean, [handle])
        self.file_type = bind(k32, "GetFileType", dword, [handle])
        self.handle_info = bind(k32, "GetHandleInformation", boolean, [handle, pdword])
        self.peek = bind(k32, "PeekNamedPipe", boolean,
                         [handle, ctypes.c_void_p, dword, pdword, pdword, pdword])
        self.read = bind(k32, "ReadFile", boolean, [handle, ctypes.c_void_p, dword, pdword, ctypes.c_void_p])
        self.write = bind(k32, "WriteFile", boolean, [handle, ctypes.c_void_p, dword, pdword, ctypes.c_void_p])
        self.cancel_io = bind(k32, "CancelSynchronousIo", boolean, [handle])
        self.open_thread = bind(k32, "OpenThread", handle, [dword, boolean, dword])
        self.thread_id = bind(k32, "GetCurrentThreadId", dword, [])
        self.compare = bind(base, "CompareObjectHandles", boolean, [handle, handle])


_api: _Api | None = None


def api() -> _Api:
    global _api
    if _api is None:
        if sys.platform != "win32":
            raise RuntimeError("the Windows runtime client only runs on Windows")
        _api = _Api()
    return _api


def _last_error() -> OSError:
    code = ctypes.get_last_error()
    return OSError(errno.EIO, f"win32 error {code}", None, code)


def _duplicate(process_handle: int, value: int) -> int:
    k = api()
    out = k.wintypes.HANDLE()
    if not k.duplicate(k.wintypes.HANDLE(process_handle), k.wintypes.HANDLE(value), k.current_process(),
                       ctypes.byref(out), 0, False, _DUPLICATE_SAME_ACCESS):
        raise _last_error()
    return int(out.value)


def pull_pipe_ends(process_handle: int, input_value: int, output_value: int) -> tuple[int, int]:
    """Duplicate the sidecar's two announced ends into this process and run the 7.4 checks.

    Returns `(input_write_end, output_read_end)`. Any failure closes whatever was duplicated and
    raises `PullRejected` (a failed `DuplicateHandle` is check 1)."""
    k = api()
    pulled: list[int] = []
    try:
        try:
            for value in (input_value, output_value):
                pulled.append(_duplicate(process_handle, value))
        except OSError as error:
            raise PullRejected(1, f"DuplicateHandle failed ({error.winerror})") from error  # type: ignore[attr-defined]
        inp, out = pulled
        if k.file_type(inp) != _FILE_TYPE_PIPE or k.file_type(out) != _FILE_TYPE_PIPE:
            raise PullRejected(2, "a pulled handle is not a pipe")
        available = k.wintypes.DWORD()
        # PeekNamedPipe is valid on a read end only: the output end must accept it, the input end not.
        if not k.peek(out, None, 0, None, ctypes.byref(available), None):
            raise PullRejected(3, "the output end is not a pipe read end")
        if k.peek(inp, None, 0, None, ctypes.byref(available), None):
            raise PullRejected(3, "the input end is not a pipe write end")
        if k.compare(inp, out):
            raise PullRejected(4, "the two ends are the same object")
        flags = k.wintypes.DWORD()
        for handle in (inp, out):
            if not k.handle_info(handle, ctypes.byref(flags)) or flags.value & _HANDLE_FLAG_INHERIT:
                raise PullRejected(5, "a pulled handle is inheritable")
        return inp, out
    except BaseException:
        for handle in pulled:
            k.close(handle)
        raise


@dataclass
class WindowsMaster:
    """The terminal handle over a ConPTY's two pipe ends.

    It is created at `SESSION_OWNED` and starts pumping at once, so a CLI that finishes before the
    create is published keeps its output in a bounded buffer (drop-oldest, like the POSIX slot). The
    Terminal Stream's `start_reading` then attaches to the same object at publication."""

    session_id: str
    input_handle: int | None
    output_handle: int | None
    proc: object = None
    cap: int = 5 * 1024 * 1024
    _loop: asyncio.AbstractEventLoop | None = None
    _chunks: collections.deque = field(default_factory=collections.deque)
    buffered: int = 0
    dropped: int = 0
    read_total: int = 0
    eof: bool = False
    error: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _callback: Callable[[], None] | None = None
    _reading: bool = False
    _paused: bool = False
    _stopped: bool = False
    _scheduled: bool = False
    _pump: threading.Thread | None = None
    _pump_tid: int = 0
    _writer: threading.Thread | None = None
    _writer_tid: int = 0
    _outbox: collections.deque = field(default_factory=collections.deque)
    _outbox_bytes: int = 0
    _outbox_ready: threading.Condition | None = None
    _write_error: bool = False
    _on_writable: Callable[[], None] | None = None
    _closing: bool = False
    _closed: bool = False
    pump_join_ms: int = 0

    # ---- the pump -------------------------------------------------------------------------------

    def start_pump(self, loop: asyncio.AbstractEventLoop) -> None:
        """Begin reading the output pipe. Safe to call once; the pump ends at EOF, an error or close."""
        if self._pump is not None:
            return
        self._loop = loop
        self._outbox_ready = threading.Condition(self._lock)
        self._pump = threading.Thread(target=self._pump_main, name=f"conpty-pump-{self.session_id}", daemon=True)
        self._writer = threading.Thread(target=self._writer_main, name=f"conpty-write-{self.session_id}", daemon=True)
        self._pump.start()
        self._writer.start()

    def _wake(self) -> None:
        """From any thread: tell the loop (once until it ran) that there is something to read."""
        with self._lock:
            if self._scheduled or self._loop is None:
                return
            self._scheduled = True
        try:
            self._loop.call_soon_threadsafe(self._deliver)
        except RuntimeError:  # the loop is closed: nobody is left to tell
            pass

    def _deliver(self) -> None:
        with self._lock:
            self._scheduled = False
            callback = self._callback if self._reading and not self._paused and not self._stopped else None
        if callback is not None:
            callback()

    def _pump_main(self) -> None:
        k = api()
        self._pump_tid = int(k.thread_id())
        buffer = ctypes.create_string_buffer(_READ_CHUNK)
        got = k.wintypes.DWORD()
        while True:
            handle = self.output_handle
            if handle is None or self._closing:
                break
            if not k.read(handle, buffer, _READ_CHUNK, ctypes.byref(got), None):
                # ERROR_BROKEN_PIPE (109) is the ordinary end of the stream; a cancelled read
                # (ERROR_OPERATION_ABORTED 995) is our own close.
                code = ctypes.get_last_error()
                with self._lock:
                    if code == 109:
                        self.eof = True
                    elif not self._closing:
                        self.error = True
                break
            if got.value == 0:
                with self._lock:
                    self.eof = True
                break
            chunk = buffer.raw[: got.value]
            with self._lock:
                self.read_total += len(chunk)
                self._chunks.append(chunk)
                self.buffered += len(chunk)
                while self.buffered > self.cap and len(self._chunks) > 1:  # newest carry the screen state
                    old = self._chunks.popleft()
                    self.buffered -= len(old)
                    self.dropped += len(old)
            self._wake()
        self._wake()

    # ---- the TerminalHandle surface -------------------------------------------------------------

    @property
    def pid(self) -> int:
        return self.proc.pid  # type: ignore[attr-defined]

    def read(self, max_bytes: int) -> bytes | None:
        with self._lock:
            if self._chunks:
                chunk = self._chunks.popleft()
                if len(chunk) > max_bytes:
                    self._chunks.appendleft(chunk[max_bytes:])
                    chunk = chunk[:max_bytes]
                self.buffered -= len(chunk)
                return chunk
            if self.eof:
                return None
            if self.error:
                raise OSError(errno.EIO, "console read failed")
        raise BlockingIOError

    def take(self) -> tuple[list[bytes], int, bool, bool]:
        """What was buffered before publication (as the reads returned it), the bytes dropped for the
        bound, whether the stream ended and whether it failed. Mirrors `HandoffSlot.take`."""
        with self._lock:
            chunks, dropped = list(self._chunks), self.dropped
            self._chunks.clear()
            self.buffered, self.dropped = 0, 0
            return chunks, dropped, self.eof, self.error

    def start_reading(self, loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._stopped:
                return
            self._callback, self._reading, self._paused = callback, True, False
        self._wake()

    def pause_reading(self) -> None:
        with self._lock:
            self._paused = True

    def resume_reading(self) -> None:
        with self._lock:
            if self._stopped or self._callback is None:
                return
            self._paused = False
        self._wake()

    def stop_reading(self) -> None:
        with self._lock:
            self._stopped, self._reading, self._callback = True, False, None

    def foreground_group(self) -> int:
        return 0

    def resize(self, rows: int, cols: int) -> None:  # pragma: no cover - a control message here
        raise RuntimeError("resize is a control message on the Rust runtime path")

    # ---- writing --------------------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        """Queue bytes for the writer thread; a full queue is `BlockingIOError` until it drains."""
        with self._lock:
            if self._write_error or self.input_handle is None or self._closing:
                raise BrokenPipeError(errno.EPIPE, "console input closed")
            room = _WRITE_CAP - self._outbox_bytes
            if room <= 0:
                raise BlockingIOError
            accepted = bytes(data[:room])
            self._outbox.append(accepted)
            self._outbox_bytes += len(accepted)
            self._outbox_ready.notify()  # type: ignore[union-attr]
            return len(accepted)

    def watch_writable(self, loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
        with self._lock:
            self._on_writable = callback
        if self._loop is not None:
            self._loop.call_soon(self._maybe_writable)

    def unwatch_writable(self) -> None:
        with self._lock:
            self._on_writable = None

    def _maybe_writable(self) -> None:
        with self._lock:
            callback = self._on_writable if self._outbox_bytes < _WRITE_CAP else None
        if callback is not None:
            callback()

    def _writer_main(self) -> None:
        k = api()
        self._writer_tid = int(k.thread_id())
        written = k.wintypes.DWORD()
        while True:
            with self._lock:
                while not self._outbox and not self._closing:
                    self._outbox_ready.wait()  # type: ignore[union-attr]
                if self._closing:
                    return
                data = self._outbox.popleft()
                handle = self.input_handle
            if handle is None:
                return
            view = memoryview(data)
            while view:
                buffer = ctypes.create_string_buffer(bytes(view[: 64 * 1024]))
                size = min(len(view), 64 * 1024)
                if not k.write(handle, buffer, size, ctypes.byref(written), None):
                    with self._lock:
                        self._write_error = True
                    return
                view = view[written.value:]
            with self._lock:
                self._outbox_bytes -= len(data)
                callback = self._on_writable
            if callback is not None and self._loop is not None:
                try:
                    self._loop.call_soon_threadsafe(callback)
                except RuntimeError:
                    pass

    # ---- close ----------------------------------------------------------------------------------

    def close(self) -> None:
        """Close-once. Cancels the blocked pump and writer, joins them under `THREAD_CLOSE_S`, then closes
        both pulled handles. Closing the output read end is what satisfies the sidecar's pre-close gate
        (7.3) when the stream is not being serviced any more."""
        with self._lock:
            if self._closed:
                return
            self._closed = self._closing = True
            self._stopped, self._reading, self._callback = True, False, None
            self._on_writable = None
            if self._outbox_ready is not None:
                self._outbox_ready.notify_all()
        k = api()
        import time
        started = time.monotonic()
        for thread, tid in ((self._pump, self._pump_tid), (self._writer, self._writer_tid)):
            if thread is None:
                continue
            deadline = started + THREAD_CLOSE_S
            while thread.is_alive() and time.monotonic() < deadline:
                if tid:
                    thread_handle = k.open_thread(_THREAD_TERMINATE, False, tid)
                    if thread_handle:
                        k.cancel_io(thread_handle)
                        k.close(thread_handle)
                thread.join(0.01)
        self.pump_join_ms = int((time.monotonic() - started) * 1000)
        alive = any(t is not None and t.is_alive() for t in (self._pump, self._writer))
        if not alive:
            for name in ("input_handle", "output_handle"):
                handle = getattr(self, name)
                setattr(self, name, None)
                if handle is not None:
                    k.close(handle)

    @property
    def closed(self) -> bool:
        return self._closed


class StdoutFeed:
    """The sidecar's stdout as the `recv` the section 6.3 receiver reads from.

    The asyncio pump task appends what the stream returned; the receiver takes at most the bytes it
    asks for (so one read never holds bytes of two frames), `BlockingIOError` when nothing is
    buffered, and `b""` once the channel ended and was drained."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.eof = False

    def recv(self, need: int) -> tuple[bytes, list[int], int]:
        if self.buffer:
            data = bytes(self.buffer[:need])
            del self.buffer[:need]
            return data, [], 0
        if self.eof:
            return b"", [], 0
        raise BlockingIOError


def registry_start(pid: int, filetime: str) -> str:
    """The `lstart` the ownership registry stores for a Windows root: psutil's creation time, accepted only
    when it names the same instant as the sidecar's FILETIME (100 ns ticks since 1601). Empty if it does not."""
    import psutil
    try:
        created = psutil.Process(pid).create_time()
        expected = int(filetime) / 1e7 - 11644473600
    except (psutil.Error, ValueError):
        return ""
    return repr(float(created)) if abs(created - expected) < 0.05 else ""
