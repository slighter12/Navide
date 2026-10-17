"""The handoff half of the Rust runtime (design/e1-runtime-protocol-v7.md 5.2, 6.2–6.4, 8).

A PTY master arrives with `SESSION_OWNED`. This module holds what is specific to it and not to
the control channel: the binding checks of a received descriptor against its frame, and the
handle wrapper the Terminal Stream runs over (close-once, terminal `stop_reading`).

POSIX only at runtime; importing it is safe everywhere (nothing OS-specific is imported at
module level), because `cli_runtime` is imported by modules that load on every platform.
"""
from __future__ import annotations

import asyncio
import errno
import os
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: macOS `TIOCPTYGNAME` (`_IOC(IOC_OUT, 't', 83, 128)`); Linux asks `TIOCGPTN` instead.
_TIOCPTYGNAME = 0x40807453
_TIOCGPTN = 0x80045430


class HandoffRejected(Exception):
    """A received descriptor failed a binding check against the frame that carried it."""

    def __init__(self, check: int, detail: str):
        super().__init__(f"binding check {check}: {detail}")
        self.check = check
        self.detail = detail


def slave_name_of(fd: int) -> str:
    """The slave device name of a PTY master, asked of the master descriptor itself."""
    import fcntl
    if sys.platform == "darwin":
        raw = fcntl.ioctl(fd, _TIOCPTYGNAME, bytes(128))
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    import struct
    number = struct.unpack("I", fcntl.ioctl(fd, _TIOCGPTN, bytes(4)))[0]
    return f"/dev/pts/{number}"


def check_master(fd: int, expected_tty: Any, claimed: set[str]) -> str:
    """The three checks of 6.4, without reading from or writing to the descriptor.

    1. type: a character device and a tty; 2. the descriptor's own slave name equals the `tty`
    the sidecar took from its own master; 3. no other live or pending session holds that name.
    Returns the verified tty name.
    """
    if not isinstance(expected_tty, str) or not expected_tty:
        raise HandoffRejected(2, "the frame names no tty")
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode) or not os.isatty(fd):
            raise HandoffRejected(1, "not a character-device tty")
    except OSError as error:
        raise HandoffRejected(1, f"fstat failed ({errno.errorcode.get(error.errno, error.errno)})") from error
    try:
        actual = slave_name_of(fd)
    except OSError as error:
        raise HandoffRejected(2, f"not a PTY master ({errno.errorcode.get(error.errno, error.errno)})") from error
    if actual != expected_tty:
        raise HandoffRejected(2, "the descriptor's slave name differs from the frame's")
    if expected_tty in claimed:
        raise HandoffRejected(3, "the tty is already held by another session")
    return expected_tty


@dataclass
class HandoffSlot:
    """A claimed descriptor owned by the runtime, not by whichever task awaits the create (I2).

    Publication moves the descriptor into the session's handle (`fd` becomes None); every path
    that ends without publication closes it at the point section 10.1/11 define.

    From the moment the binding checks pass until publication (or the end of an unpublished session), the
    slot **reads its master into a bounded buffer** (E1 v8 8 item 4). macOS drains a closing slave for at most
    ~0.6 s and then flushes whatever the master has not read (measured, `receipts/f1/`), so output cannot be
    left waiting for the Terminal Stream's reader: that reader attaches only at publication, which can be late.
    """

    session_id: str
    fd: int | None
    tty: str = ""
    buffer: list = field(default_factory=list)
    buffered: int = 0
    dropped: int = 0
    read_total: int = 0  # every byte this reader ever took from the master (receipts only)
    eof: bool = False
    error: bool = False
    reading: bool = False
    _loop: asyncio.AbstractEventLoop | None = None
    _cap: int = 0

    def start_reading(self, loop: asyncio.AbstractEventLoop, cap: int) -> None:
        """Attach the pre-publication reader. `cap` is the Terminal Stream's own 5 MiB drop-oldest bound."""
        if self.fd is None or self.reading:
            return
        self._loop, self._cap, self.reading = loop, cap, True
        loop.add_reader(self.fd, self._on_readable)

    def stop_reading(self) -> None:
        if not self.reading or self.fd is None or self._loop is None:
            self.reading = False
            return
        self.reading = False
        try:
            self._loop.remove_reader(self.fd)
        except (ValueError, KeyError, OSError):
            pass

    def _on_readable(self) -> None:
        fd = self.fd
        if fd is None:
            return
        taken = 0
        while taken < _PREREAD_DRAIN_MAX:
            try:
                chunk = os.read(fd, _PREREAD_CHUNK)
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno == errno.EIO:
                    self.eof = True
                else:
                    self.error = True
                self.stop_reading()
                return
            if not chunk:
                self.eof = True
                self.stop_reading()
                return
            taken += len(chunk)
            self.read_total += len(chunk)
            self.buffer.append(chunk)
            self.buffered += len(chunk)
            while self.buffered > self._cap and len(self.buffer) > 1:  # drop-oldest, newest carry the screen state
                old = self.buffer.pop(0)
                self.buffered -= len(old)
                self.dropped += len(old)

    def take(self) -> tuple[list[bytes], int, bool, bool]:
        """The chunks buffered so far (as the reads returned them, so the Terminal Stream's rate heuristics see the
        same entries its own reader would have produced), the bytes dropped for the bound, and whether the stream ended."""
        chunks, dropped = self.buffer, self.dropped
        self.buffer, self.buffered, self.dropped = [], 0, 0
        return chunks, dropped, self.eof, self.error


#: The same read sizes as the Terminal Stream's reader (`terminals._READ_CHUNK_BYTES`/`_READ_DRAIN_MAX_BYTES`).
_PREREAD_CHUNK = 64 * 1024
_PREREAD_DRAIN_MAX = 256 * 1024


class MasterHandle:
    """The `TerminalHandle` the Terminal Stream runs over for a handed-off master.

    It owns its descriptor and invalidates it atomically with the close (E1 8, close-once): a
    second `close()` is a no-op and never a raw close on a reused number. `stop_reading()` is
    terminal: afterwards neither `start_reading` nor `resume_reading` registers a reader, so the
    Terminal Stream's pause/resume bookkeeping cannot restart one that `_finish_stream` stopped.
    Resize is a control message on this path and is not offered here.
    """

    def __init__(self, fd: int, proc: Any):
        self.fd: int | None = fd
        self.proc = proc
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_readable: Callable[[], None] | None = None
        self._write_loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _owned(self) -> int:
        if self.fd is None:
            raise OSError(errno.EBADF, "handle closed")
        return self.fd

    def read(self, max_bytes: int) -> bytes | None:
        return os.read(self._owned(), max_bytes) or None

    def write(self, data: bytes) -> int:
        return os.write(self._owned(), data)

    def resize(self, rows: int, cols: int) -> None:  # pragma: no cover - never used on this path
        raise RuntimeError("resize is a control message on the Rust runtime path")

    def foreground_group(self) -> int:
        if self.fd is None:
            return 0
        try:
            group = os.tcgetpgrp(self.fd)
        except OSError:
            return 0
        return group if group > 0 else 0

    def start_reading(self, loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
        if self._stopped or self.fd is None:
            return
        self._loop, self._on_readable = loop, callback
        loop.add_reader(self.fd, callback)

    def pause_reading(self) -> None:
        if self._loop is None or self.fd is None:
            return
        try:
            self._loop.remove_reader(self.fd)
        except (ValueError, KeyError):
            pass

    def resume_reading(self) -> None:
        if self._stopped or self._loop is None or self._on_readable is None or self.fd is None:
            return
        self._loop.add_reader(self.fd, self._on_readable)

    def stop_reading(self) -> None:
        self._stopped = True
        self.pause_reading()
        self._on_readable = None

    def watch_writable(self, loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
        if self.fd is None:
            return
        self._write_loop = loop
        loop.add_writer(self.fd, callback)

    def unwatch_writable(self) -> None:
        if self._write_loop is None or self.fd is None:
            return
        try:
            self._write_loop.remove_writer(self.fd)
        except (ValueError, KeyError, OSError):
            pass

    def close(self) -> None:
        """Take the owned number and invalidate it in one synchronous step, then close it."""
        if self.fd is None:
            return
        self.stop_reading()
        self.unwatch_writable()
        fd, self.fd = self.fd, None
        try:
            os.close(fd)
        except OSError:
            pass
