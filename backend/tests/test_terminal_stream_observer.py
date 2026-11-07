"""The Terminal Stream's one public observation seam.

A stream tells its observer what moves through it: bytes read from a session's master, bytes absorbed into the output
buffer, bytes dropped by the drop-oldest rule, bytes emitted in output frames, and the end of a resize drain. A harness
counts through this seam (`TerminalInterface.observe_stream`) and never patches a private method, so both services are
counted by the same code and a refactor of the stream's internals cannot change what is counted.
"""
from __future__ import annotations

import asyncio
import ctypes
import io
import re
import threading
from types import SimpleNamespace

import pytest

from agent_team_backend import cli_runtime_windows, terminals
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.terminals import StreamObserver, TerminalInterface, TerminalService


class Handle:
    """A master that returns its chunks, then would block."""

    def __init__(self, *chunks: bytes) -> None:
        self.chunks = list(chunks)

    def read(self, _max: int) -> bytes:
        if not self.chunks:
            raise BlockingIOError
        return self.chunks.pop(0)

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass


class Recorder(StreamObserver):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def read(self, session_id: str, nbytes: int) -> None:
        self.events.append(("read", session_id, nbytes))

    def absorbed(self, session_id: str, nbytes: int) -> None:
        self.events.append(("absorbed", session_id, nbytes))

    def dropped(self, session_id: str, nbytes: int) -> None:
        self.events.append(("dropped", session_id, nbytes))

    def emitted(self, session_id: str, nbytes: int) -> None:
        self.events.append(("emitted", session_id, nbytes))

    def drained(self, session_id: str) -> None:
        self.events.append(("drained", session_id))

    def total(self, kind: str) -> int:
        return sum(event[2] for event in self.events if event[0] == kind)

    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]


class Sink:
    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.frames: list = []

    async def __call__(self, frame) -> None:
        self.frames.append(frame)
        await self.gate.wait()
        await asyncio.sleep(0)


def session_with(handle: Handle) -> SimpleNamespace:
    return SimpleNamespace(id="s1", handle=handle, closed=False, pane_id="p", sequence=0, output_log_fp=None, agent_key="terminal")


def stream_with(sink: Sink, *chunks: bytes) -> tuple[TerminalService, Recorder, SimpleNamespace]:
    stream = TerminalService(sink)
    recorder = Recorder()
    stream.observe_stream(recorder)
    session = session_with(Handle(*chunks))
    stream.adopt_session(session)
    return stream, recorder, session


def test_the_interface_declares_the_seam_and_a_service_that_implements_nothing_refuses_it():
    assert hasattr(TerminalInterface, "observe_stream")
    with pytest.raises(NotImplementedError):
        TerminalInterface().observe_stream(Recorder())


async def test_a_readable_session_reports_what_it_read_what_it_absorbed_and_what_it_emitted():
    stream, recorder, session = stream_with(Sink(), b"abc", b"de")
    stream.read_ready(session)
    await stream.quiesce_output(session)
    assert recorder.total("read") == 5 and recorder.total("absorbed") == 5 and recorder.total("emitted") == 5
    assert recorder.kinds() == ["read", "read", "absorbed", "emitted"], "one absorb per drained batch, the emit after it"


async def test_the_resize_drain_reports_its_slurp_as_reads_and_its_end_as_a_watermark():
    stream, recorder, session = stream_with(Sink(), b"xy", b"z")
    async with stream.barrier(session):
        await stream.drain_behind_barrier(session)
    assert recorder.total("read") == 3 and recorder.total("emitted") == 3
    assert recorder.kinds() == ["read", "read", "emitted", "drained"], "the watermark is taken once everything is emitted"


async def test_the_resize_drain_of_the_in_process_service_reports_its_watermark_too():
    stream, recorder, session = stream_with(Sink(), b"xy")
    await stream.drain_output(session.id)
    assert recorder.kinds() == ["read", "emitted", "drained"]


async def test_bytes_dropped_under_a_stalled_drain_are_reported_after_the_absorb_that_dropped_them():
    sink = Sink()
    sink.gate.clear()
    stream, recorder, session = stream_with(sink)
    chunk = b"x" * (256 * 1024)
    stream.hand_over(session, [b"x" * terminals._BUF_CAP], 0)  # a full buffer flushes at once, and that drain stalls on the sink
    await asyncio.sleep(0)
    for _ in range(40):
        stream.hand_over(session, [chunk], 0)
    assert recorder.total("dropped") > 0
    first_drop = recorder.kinds().index("dropped")
    assert recorder.kinds()[first_drop - 1] == "absorbed", "the absorb is recorded first, then the drop it caused"
    sink.gate.set()
    await stream.quiesce_output(session)
    assert recorder.total("absorbed") == recorder.total("emitted") + recorder.total("dropped")


async def test_a_hand_over_reports_the_bytes_taken_and_the_bytes_dropped_once_and_what_it_absorbs():
    """K2: a reader that ran before the stream attached handed over three reads (6 bytes) and 7 dropped bytes: the observer hears
    one read of everything the master gave up (6 + 7 = 13), one drop of 7, one absorb per chunk, then the emit of the 6 kept."""
    stream, recorder, session = stream_with(Sink())
    stream.hand_over(session, [b"abc", b"de", b"f"], 7)
    await stream.quiesce_output(session)
    assert recorder.events[:2] == [("read", "s1", 13), ("dropped", "s1", 7)]
    assert recorder.kinds() == ["read", "dropped", "absorbed", "absorbed", "absorbed", "emitted"]
    assert recorder.total("read") == recorder.total("emitted") + recorder.total("dropped")


async def test_a_hand_over_of_nothing_reports_nothing():
    stream, recorder, session = stream_with(Sink())
    stream.hand_over(session, [], 0)
    await stream.quiesce_output(session)
    assert recorder.events == []


async def test_a_stream_nobody_observes_holds_no_observer_and_still_emits_every_byte():
    """K3: with no observer attached the stream does no notification work at all (it holds none to call), and its output is the
    same. No throughput claim: this shows the state and the behavior, not a measurement."""
    sink = Sink()
    stream = TerminalService(sink)
    session = session_with(Handle(b"abc", b"de"))
    stream.adopt_session(session)
    assert stream._observer is None, "nothing attached: nothing to notify"
    stream.read_ready(session)
    await stream.quiesce_output(session)
    session.handle.chunks.append(b"hi")  # unread: the barrier's slurp reads it and its emit sends it (the two barrier sites)
    async with stream.barrier(session):
        await stream.drain_behind_barrier(session)
    stream.hand_over(session, [b"fg"], 3)
    await stream.quiesce_output(session)
    assert [len(frame) - terminals_frame_header_size() for frame in sink.frames] == [5, 2, 2]
    assert stream._observer is None


async def test_an_observer_attached_late_hears_only_what_moves_after_it():
    stream = TerminalService(Sink())
    session = session_with(Handle(b"abc"))
    stream.adopt_session(session)
    stream.read_ready(session)
    await stream.quiesce_output(session)
    recorder = Recorder()
    stream.observe_stream(recorder)
    assert recorder.events == [], "there is no replay of what moved before"
    session.handle.chunks.append(b"de")
    stream.read_ready(session)
    await stream.quiesce_output(session)
    assert (recorder.total("read"), recorder.total("emitted")) == (2, 2)


def terminals_frame_header_size() -> int:
    """The bytes a binary output frame carries before its payload (the frame is the header and the payload)."""
    return len(TerminalService(Sink())._build_output_frame(session_with(Handle()), b""))


async def test_the_rust_service_presents_the_seam_of_the_stream_it_composes():
    sink = Sink()
    service = RustTerminalService(sink)
    recorder = Recorder()
    service.observe_stream(recorder)
    session = session_with(Handle(b"abcd"))
    service.stream.adopt_session(session)
    service.stream.read_ready(session)
    await service.stream.quiesce_output(session)
    assert (recorder.total("read"), recorder.total("emitted")) == (4, 4)


class PipeKernel:
    """The two Win32 entry points the pump uses, over a list of reads. When the last one has been handed out the pump is told
    to close, so it ends without an EOF (the stream then keeps the session open, as it does for a CLI that is still running)."""

    def __init__(self, master, *reads: bytes) -> None:
        self.master = master
        self.reads = list(reads)
        self.wintypes = SimpleNamespace(DWORD=ctypes.c_ulong)

    def thread_id(self) -> int:
        return 1

    def read(self, _handle, buffer, _size, got, _overlapped) -> int:
        data = self.reads.pop(0)
        ctypes.memmove(buffer, data, len(data))
        got._obj.value = len(data)
        if not self.reads:
            self.master._closing = True
        return 1


def run_pump(monkeypatch, master, *reads: bytes) -> None:
    """Run the real `_pump_main` to the end of `reads` over a fake kernel (no Windows needed for the pump's own bookkeeping)."""
    monkeypatch.setattr(cli_runtime_windows, "api", lambda: PipeKernel(master, *reads))
    master._closing = False
    pump = threading.Thread(target=master._pump_main)
    pump.start()
    pump.join(10)
    assert not pump.is_alive()


def windows_stream(sink: Sink, cap: int) -> tuple[TerminalService, Recorder, SimpleNamespace, cli_runtime_windows.WindowsMaster]:
    """A stream attached to a Windows master whose pump bound is `cap`; the stream has paused the pump's delivery."""
    master = cli_runtime_windows.WindowsMaster(session_id="s1", input_handle=None, output_handle=1, cap=cap)
    master._loop = asyncio.get_running_loop()
    stream = TerminalService(sink)
    recorder = Recorder()
    stream.observe_stream(recorder)
    session = session_with(master)
    session.output_log_fp = io.StringIO()
    stream.adopt_session(session)
    master.start_reading(master._loop, lambda: stream.read_ready(session))
    master.pause_reading()
    return stream, recorder, session, master


async def delivered(stream: TerminalService, session: SimpleNamespace, master) -> None:
    master.resume_reading()
    for _ in range(100):
        await asyncio.sleep(0)
        if not master._chunks:
            break
    await stream.quiesce_output(session)


async def test_l1_pump_bytes_dropped_while_the_stream_was_paused_are_read_dropped_and_marked_in_the_log(monkeypatch):
    """L1 (Windows): the pump keeps reading while the stream has paused delivery and drops the oldest chunks above its bound.
    Those bytes were taken from the pipe, so the observer hears them as one read and one drop, and the log marks the gap."""
    chunk = 64 * 1024
    stream, recorder, session, master = windows_stream(Sink(), cap=2 * chunk)
    run_pump(monkeypatch, master, *[bytes([65 + n]) * chunk for n in range(10)])
    assert master.dropped == 8 * chunk and master.read_total == 10 * chunk, "the pump did drop, and took everything"
    await delivered(stream, session, master)
    assert recorder.total("read") == master.read_total, "read is what the pump took from the pipe"
    assert recorder.total("dropped") == 8 * chunk
    assert recorder.total("read") == recorder.total("emitted") + recorder.total("dropped")
    assert f"[navide: {8 * chunk} bytes of output dropped]" in session.output_log_fp.getvalue()
    assert master.dropped == 0, "a drop is reported once"
    master.resume_reading()
    await asyncio.sleep(0)
    assert recorder.total("dropped") == 8 * chunk, "and not again by a later delivery"


async def test_l1_a_drop_before_publication_and_a_drop_after_it_are_each_counted_once(monkeypatch):
    """The hand-over reports what `take()` returned; the pump's later drops come through the read path. Nothing is counted twice."""
    chunk = 64 * 1024
    stream, recorder, session, master = windows_stream(Sink(), cap=2 * chunk)
    run_pump(monkeypatch, master, *[b"a" * chunk for _ in range(6)])  # 4 chunks dropped before the hand-over
    taken, dropped, _eof, _errored = master.take()
    assert dropped == 4 * chunk
    stream.hand_over(session, taken, dropped)
    run_pump(monkeypatch, master, *[b"b" * chunk for _ in range(6)])  # 4 more dropped after it, delivery still paused
    await delivered(stream, session, master)
    assert master.read_total == 12 * chunk
    assert recorder.total("read") == master.read_total, "each byte the pump took is read once, before and after the hand-over"
    assert recorder.total("dropped") == 8 * chunk
    assert recorder.total("read") == recorder.total("emitted") + recorder.total("dropped")
    marked = [int(n) for n in re.findall(r"\[navide: (\d+) bytes of output dropped\]", session.output_log_fp.getvalue())]
    assert sum(marked) == 8 * chunk, "the log's gaps add up to every dropped byte"


def test_the_stream_observer_has_one_method_per_event_and_no_more():
    assert {name for name in vars(StreamObserver) if not name.startswith("_")} == {"read", "absorbed", "dropped", "emitted", "drained"}
    assert terminals.StreamObserver is StreamObserver
