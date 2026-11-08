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


class _PerThreadState:
    def __init__(self) -> None:
        self.enter = threading.Event()  # the read may begin once this is set
        self.cancelled = threading.Event()
        self.in_read = False
        self.stuck = False
        self.cancels = 0


class _PerThreadKernel:
    """Like `_FakeKernel`, for several masters at once: each reading thread has its own state, a read on one of the
    `stuck_outputs` handles can never be cancelled, and any other read begins only after `enter_after` cancels aimed
    at its thread (the pump the first sweep missed)."""

    def __init__(self, stuck_outputs: set[int], enter_after: int) -> None:
        self.stuck_outputs = stuck_outputs
        self.enter_after = enter_after
        self.closed: list[int] = []
        self.wintypes = types.SimpleNamespace(DWORD=ctypes.c_ulong)
        self.release = threading.Event()  # test cleanup: lets every blocked read return
        self._lock = threading.Lock()
        self._states: dict[int, _PerThreadState] = {}
        self._local = threading.local()

    def _state(self, tid: int) -> _PerThreadState:
        with self._lock:
            return self._states.setdefault(tid, _PerThreadState())

    def thread_id(self) -> int:
        return threading.get_ident()

    def read(self, handle, buffer, size, got, overlapped) -> int:
        state = self._state(threading.get_ident())
        with self._lock:
            state.stuck = handle in self.stuck_outputs
        state.enter.wait(10)
        with self._lock:
            state.in_read = True
        (self.release if state.stuck else state.cancelled).wait(10)
        self._local.error = _ERROR_OPERATION_ABORTED
        return 0

    def write(self, handle, buffer, size, written, overlapped) -> int:  # pragma: no cover - the writer never gets data
        raise AssertionError("nothing is written")

    def open_thread(self, access, inherit, tid) -> int:
        return tid

    def cancel_io(self, handle) -> int:
        state = self._state(handle)
        with self._lock:
            state.cancels += 1
            if state.cancels >= self.enter_after:
                state.enter.set()
            if state.in_read and not state.stuck and not state.cancelled.is_set():
                state.cancelled.set()
                return 1
        self._local.error = winrt._ERROR_NOT_FOUND
        return 0

    def close(self, handle) -> int:
        self.closed.append(handle)
        return 1

    def free_everything(self) -> None:
        self.release.set()
        with self._lock:
            for state in self._states.values():
                state.enter.set()
                state.cancelled.set()


@pytest.fixture
def per_thread_kernel(monkeypatch: pytest.MonkeyPatch) -> _PerThreadKernel:
    fake = _PerThreadKernel(stuck_outputs={12}, enter_after=winrt._CANCEL_RETRIES)
    monkeypatch.setattr(winrt, "api", lambda: fake)
    monkeypatch.setattr(winrt, "ctypes", types.SimpleNamespace(
        create_string_buffer=ctypes.create_string_buffer, byref=ctypes.byref,
        get_last_error=lambda: getattr(fake._local, "error", 0)))
    return fake


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


def test_slow_cancel_calls_do_not_carry_finish_close_past_its_deadline(kernel: _FakeKernel, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeat sweep is up to `_CANCEL_RETRIES` cancel calls; none may be issued once the deadline passed, so the
    close ends within the deadline plus one call, not one sweep, whatever each call costs."""
    call_cost = 0.01
    deadline_s, margin_s = 0.3, 0.2
    real_cancel = kernel.cancel_io
    slow = threading.Event()

    def slow_cancel(handle) -> int:
        if slow.is_set():
            time.sleep(call_cost)
        return real_cancel(handle)

    monkeypatch.setattr(kernel, "cancel_io", slow_cancel)
    monkeypatch.setattr(kernel, "enter_read_after_cancels", 10 ** 9)  # a worker that never exits
    loop = asyncio.new_event_loop()
    master = winrt.WindowsMaster("s3", 31, 32)
    try:
        master.start_pump(loop)
        _announced(master)
        master.begin_close()  # the production order: step 1 for everything, then the bounded join
        slow.set()
        started = time.monotonic()
        assert master.finish_close(started + deadline_s) is False
        elapsed = time.monotonic() - started
        assert elapsed < deadline_s + margin_s, f"finish_close took {elapsed:.2f}s against a {deadline_s}s deadline"
        assert not master.closed
    finally:
        kernel._enter.set()
        kernel._cancelled.set()
        loop.close()


def test_slow_cancel_calls_do_not_carry_begin_close_made_by_finish_close_past_its_deadline(kernel: _FakeKernel, monkeypatch: pytest.MonkeyPatch) -> None:
    """`close()` runs `begin_close` inside `finish_close`: that first sweep is bound by the same deadline."""
    deadline_s, margin_s = 0.3, 0.2
    real_cancel = kernel.cancel_io

    def slow_cancel(handle) -> int:
        time.sleep(0.01)
        return real_cancel(handle)

    monkeypatch.setattr(kernel, "cancel_io", slow_cancel)
    monkeypatch.setattr(kernel, "enter_read_after_cancels", 10 ** 9)
    loop = asyncio.new_event_loop()
    master = winrt.WindowsMaster("s4", 41, 42)
    try:
        master.start_pump(loop)
        _announced(master)
        started = time.monotonic()
        assert master.finish_close(started + deadline_s) is False
        elapsed = time.monotonic() - started
        assert elapsed < deadline_s + margin_s, f"finish_close took {elapsed:.2f}s against a {deadline_s}s deadline"
    finally:
        kernel._enter.set()
        kernel._cancelled.set()
        loop.close()


def test_a_stuck_master_does_not_use_up_the_deadline_a_later_master_needs_for_a_repeat_cancel(per_thread_kernel: _PerThreadKernel) -> None:
    """Master 1 can never be cancelled; master 2 missed the first sweep and needs one more cancel. They share one
    deadline, so master 2 must get its repeat while master 1 is still being waited for."""
    deadline_s, margin_s = 1.0, 0.3
    loop = asyncio.new_event_loop()
    stuck_master = winrt.WindowsMaster("m1", 11, 12)
    late_master = winrt.WindowsMaster("m2", 21, 22)
    try:
        for master in (stuck_master, late_master):
            master.start_pump(loop)
            _announced(master)
        started = time.monotonic()
        stuck = winrt.close_together([stuck_master, late_master], started + deadline_s)
        elapsed = time.monotonic() - started
        assert stuck == [stuck_master]
        assert late_master.closed and [h for h in per_thread_kernel.closed if h in (21, 22)] == [21, 22]
        assert not stuck_master.closed and not [h for h in per_thread_kernel.closed if h in (11, 12)]
        assert elapsed < deadline_s + margin_s, f"close_together took {elapsed:.2f}s against a {deadline_s}s deadline"
    finally:
        per_thread_kernel.free_everything()
        loop.close()
