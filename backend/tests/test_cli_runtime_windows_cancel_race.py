"""`WindowsMaster.finish_close` keeps cancelling a pump that missed the first cancel.

`CancelSynchronousIo` only reaches a call that is in progress at that instant (`ERROR_NOT_FOUND` otherwise). A pump
that announced its read (`_pump_blocking`) but was not scheduled into `ReadFile` before `begin_close`'s immediate
retries ran out would then block until the pipe breaks, which in the pre-close gate (7.3) is after the join: the close
reported incomplete although a further cancel would have ended it. These cases run everywhere: the Win32 calls are a
fake kernel whose `ReadFile` the test lets begin only after the first sweep of cancels is over, which is the
descheduled pump of the failure. The real interleaving needs Windows; the assumption made here is that a cancel
issued while the thread is inside `ReadFile` ends it with `ERROR_OPERATION_ABORTED`, which the Windows-only cases of
`test_cli_runtime_windows_close.py` check against real pipes.
"""
from __future__ import annotations

import asyncio
import ctypes
import threading
import time
import types

import pytest

from agent_team_backend import cli_runtime_windows as winrt

_ERROR_OPERATION_ABORTED = 995


class _FakeKernel:
    """Just enough of kernel32 for the pump, the writer and the cancel sweep."""

    def __init__(self, enter_read_after_cancels: int) -> None:
        self.enter_read_after_cancels = enter_read_after_cancels
        self.cancels = 0
        self.closed: list[int] = []
        self.wintypes = types.SimpleNamespace(DWORD=ctypes.c_ulong)
        self._lock = threading.Lock()
        self._enter = threading.Event()  # the pump may enter its read once this is set
        self._in_read = False
        self._cancelled = threading.Event()
        self._pump_tid = 0
        self._local = threading.local()

    # ---- what the module under test calls ----
    def thread_id(self) -> int:
        return threading.get_ident()

    def read(self, handle, buffer, size, got, overlapped) -> int:
        self._pump_tid = threading.get_ident()
        self._enter.wait(10)  # a pump that announced its read and was not scheduled into it yet
        with self._lock:
            self._in_read = True
        self._cancelled.wait(10)
        self._local.error = _ERROR_OPERATION_ABORTED
        return 0

    def write(self, handle, buffer, size, written, overlapped) -> int:  # pragma: no cover - the writer never gets data
        raise AssertionError("nothing is written")

    def open_thread(self, access, inherit, tid) -> int:
        return tid

    def cancel_io(self, handle) -> int:
        with self._lock:
            self.cancels += 1
            if self.cancels >= self.enter_read_after_cancels:
                self._enter.set()
            if handle == self._pump_tid and self._in_read and not self._cancelled.is_set():
                self._cancelled.set()
                return 1
        self._local.error = winrt._ERROR_NOT_FOUND
        return 0

    def close(self, handle) -> int:
        self.closed.append(handle)
        return 1


@pytest.fixture
def kernel(monkeypatch: pytest.MonkeyPatch) -> _FakeKernel:
    # Past the immediate retries of the first sweep, so the pump enters its read only after that sweep gave up.
    fake = _FakeKernel(enter_read_after_cancels=winrt._CANCEL_RETRIES)
    monkeypatch.setattr(winrt, "api", lambda: fake)
    monkeypatch.setattr(winrt, "ctypes", types.SimpleNamespace(
        create_string_buffer=ctypes.create_string_buffer, byref=ctypes.byref,
        get_last_error=lambda: getattr(fake._local, "error", 0)))
    return fake


def _announced(master: winrt.WindowsMaster) -> None:
    for _ in range(1000):
        if master._pump_blocking and master._pump_tid:
            return
        time.sleep(0.005)
    raise AssertionError("the pump never announced its read")


def test_a_pump_that_misses_the_first_cancel_is_cancelled_again_while_the_close_joins(kernel: _FakeKernel) -> None:
    loop = asyncio.new_event_loop()
    master = winrt.WindowsMaster("s1", 11, 12)
    try:
        master.start_pump(loop)
        _announced(master)

        master.begin_close()
        # The reproduction is real: every immediate retry found no call, and the pump then went into its read.
        assert kernel.cancels >= winrt._CANCEL_RETRIES
        assert master._pump is not None
        master._pump.join(0.2)
        assert master._pump.is_alive(), "the first sweep must have missed, or this case proves nothing"

        assert master.finish_close(time.monotonic() + 5.0) is True
        assert not master._pump.is_alive()
        assert [h for h in kernel.closed if h in (11, 12)] == [11, 12] and master.closed
    finally:
        kernel._enter.set()
        kernel._cancelled.set()
        loop.close()


def test_a_pump_that_never_ends_is_still_reported_not_closed_at_the_deadline(kernel: _FakeKernel, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repeats stay inside the caller's deadline: an uncancellable pump is the incomplete close of 7.3, not a hang."""
    monkeypatch.setattr(kernel, "cancel_io", lambda handle: (setattr(kernel._local, "error", winrt._ERROR_NOT_FOUND), 0)[1])
    loop = asyncio.new_event_loop()
    master = winrt.WindowsMaster("s2", 21, 22)
    try:
        master.start_pump(loop)
        _announced(master)
        started = time.monotonic()
        assert master.finish_close(started + 0.3) is False
        assert time.monotonic() - started < 2.0
        assert not [h for h in kernel.closed if h in (21, 22)] and not master.closed, "an unjoined worker's handles stay open"
    finally:
        kernel._enter.set()
        kernel._cancelled.set()
        loop.close()
