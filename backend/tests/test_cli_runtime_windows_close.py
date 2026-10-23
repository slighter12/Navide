"""The Windows terminal handle (`WindowsMaster`): service mode, and a generation's bounded close.

The service-mode cases run everywhere (they use no Win32 call). The close cases use real anonymous pipes with a
real blocked `ReadFile`, so they run on Windows only (hosted CI); a worker that cannot be cancelled is a thread
that ignores `CancelSynchronousIo`, which is what an unjoined worker is.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from agent_team_backend import cli_runtime_windows as winrt

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="real Win32 pipes and CancelSynchronousIo")


class _Recorder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_pause_reading_suppresses_delivery_until_service_mode_then_is_a_no_op():
    master = winrt.WindowsMaster("s1", None, None)
    master._loop = asyncio.get_running_loop()
    deliveries = _Recorder()
    master.start_reading(master._loop, deliveries)
    await asyncio.sleep(0)
    baseline = deliveries.calls

    master._chunks.append(b"x" * (2 * 1024 * 1024))  # well above any pause threshold
    master.pause_reading()
    master._wake()
    await asyncio.sleep(0)
    assert deliveries.calls == baseline, "a paused reader must not deliver before the root exits"

    master.enter_service_mode()  # the root exited: the final output must reach the Terminal Stream
    master.pause_reading()
    master._wake()
    await asyncio.sleep(0)
    assert deliveries.calls > baseline, "service mode must deliver even though the stream asked to pause"


@pytest.mark.asyncio
async def test_service_mode_delivers_the_final_output_through_the_real_stream_before_the_exit_event():
    """E5 final review 6: drive the real Terminal Stream (not a callback) with a final burst that sits behind a
    pause request, and account for every byte on the wire before `terminal.exit`."""
    from types import SimpleNamespace

    from agent_team_backend.terminals import TerminalService, TerminalSession

    sink_open = asyncio.Event()
    events: list = []

    async def emit(event) -> None:
        if isinstance(event, bytes):
            await sink_open.wait()  # a slow sink: the stream's drain is on the wire while the burst arrives
        events.append(event)

    svc = TerminalService(emit)
    loop = asyncio.get_running_loop()
    class _NoWin32Close(winrt.WindowsMaster):
        def close(self) -> None:  # the Win32 handle close is covered by the Windows-only cases below
            self._closed = True

    master = _NoWin32Close("s1", None, None)
    master._loop = loop
    proc = SimpleNamespace(returncode=0, pid=1, poll=lambda: 0)
    session = TerminalSession(id="s1", pane_id="p1", agent_key="terminal", command=["x"], cwd=".",
                              handle=master, proc=proc)
    svc._sessions["s1"] = session
    master.start_reading(loop, lambda: svc._on_readable(session))

    master._chunks.append(b"a" * 4096)
    master.buffered = 4096
    master._wake()
    for _ in range(200):  # the first flush is on the wire (blocked): the stream asked to pause
        await asyncio.sleep(0.01)
        if master._paused:
            break
    assert master._paused, "the stream never paused the reader"

    final = [bytes([65 + n]) * 65536 for n in range(32)]  # 2 MiB, well above any pause threshold
    for chunk in final:
        master._chunks.append(chunk)
    master.buffered += sum(map(len, final))
    master._wake()
    await asyncio.sleep(0.05)
    assert master.buffered == sum(map(len, final)), "a paused reader must not deliver before the root exits"

    master.enter_service_mode()  # the root exited: the final output must reach the Terminal Stream
    sink_open.set()
    for _ in range(500):
        await asyncio.sleep(0.01)
        if master.buffered == 0:
            break
    assert master.buffered == 0, "service mode left the final output undelivered"

    master.eof = True
    master._wake()
    sink_open.set()
    for _ in range(300):
        await asyncio.sleep(0.01)
        if any(not isinstance(e, bytes) for e in events):
            break

    exit_at = next((i for i, e in enumerate(events) if not isinstance(e, bytes)), None)
    assert exit_at is not None, "terminal.exit never reached the sink"
    header = 1 + 4 + 1 + len(b"s1") + 1 + len(b"p1")
    delivered = b"".join(e[header:] for e in events[:exit_at])
    dropped = svc._out_dropped.get("s1", 0)
    expected = b"a" * 4096 + b"".join(final)
    assert delivered == expected or len(delivered) + dropped == len(expected), (
        f"{len(delivered)} bytes delivered and {dropped} accounted as dropped of {len(expected)}")
    assert delivered.endswith(final[-1]), "the newest bytes carry the screen state and must arrive"


def _pipe() -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes
    read, write = wintypes.HANDLE(), wintypes.HANDLE()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assert kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 0)
    return int(read.value), int(write.value)


def _masters(count: int, kind: type[winrt.WindowsMaster]) -> tuple[list[winrt.WindowsMaster], list[int]]:
    """`count` masters whose pump is blocked in `ReadFile` (the writing end stays open and silent)."""
    loop = asyncio.new_event_loop()
    masters, keep = [], []
    for n in range(count):
        out_r, out_w = _pipe()
        in_r, in_w = _pipe()
        keep += [out_w, in_r]
        master = kind(f"s{n}", in_w, out_r)
        master.start_pump(loop)
        masters.append(master)
    return masters, keep


@windows_only
def test_a_generation_of_blocked_sessions_closes_under_one_deadline_and_closes_every_handle():
    masters, keep = _masters(4, winrt.WindowsMaster)
    time.sleep(0.2)  # let every pump enter its blocking read: the closes below must cancel real calls
    started = time.monotonic()

    stuck = winrt.close_together(masters, started + winrt.THREAD_CLOSE_S)

    assert stuck == [], "a cancelled blocking read must join"
    assert time.monotonic() - started < winrt.THREAD_CLOSE_S
    assert all(m.input_handle is None and m.output_handle is None and m.closed for m in masters)


class _Uncancellable(winrt.WindowsMaster):
    """Its pump ends only when released: a cancel does not reach it, like a worker stuck outside the I/O call."""
    release = threading.Event()

    def _pump_main(self) -> None:
        self.release.wait(30)


@windows_only
def test_unjoined_workers_share_one_deadline_and_stay_reported_not_closed():
    _Uncancellable.release = threading.Event()
    masters, keep = _masters(4, _Uncancellable)
    started = time.monotonic()
    try:
        stuck = winrt.close_together(masters, started + 1.0)
        elapsed = time.monotonic() - started

        assert stuck == masters, "every master with an unjoined worker must be reported"
        assert elapsed < 2.0, f"one generation-wide deadline, not one per session ({elapsed:.2f}s)"
        assert all(not m.closed and m.output_handle is not None for m in masters), "an unjoined worker's read end is not closed"
    finally:
        _Uncancellable.release.set()
    assert winrt.close_together(masters, time.monotonic() + winrt.THREAD_CLOSE_S) == [], "released workers join and close"
