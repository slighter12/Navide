"""F-1 (assignment 3): a CLI that finishes before the create is published keeps its output (E1 v8 8 item 4).

Measured mechanism (receipts/f1/, own processes): on macOS the last close of a slave blocks the closer for
~0.6 s while the master has unread output, then flushes that output. The master must therefore be read within
that window, which the Terminal Stream's reader (attached at publication) cannot promise when publication is
late: the claimed slot reads it from the claim (v8). The first two tests record the sidecar-side facts the
design relies on (a reader late by up to 0.5 s still gets everything; only the CLI holds the slave); they pass
on the v7 sidecar too (no sidecar change was needed, see evidence/e3/f1-options.md).

Raw frames against the real sidecar for those two, the production service for the rest.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import select
import sys
import time

import pytest

from agent_team_backend.cli_runtime_frames import code
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT, FramePeer
from tests.test_cli_runtime_lifecycle import LifeRig

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
    reason="requires the explicitly built macOS native runtime (product and harness)")


def read_until_end(fd: int, timeout: float = 5) -> bytes:
    os.set_blocking(fd, False)
    data = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return data
        if not chunk:
            return data
        data += chunk
    raise AssertionError(f"no end of stream within {timeout}s; got {data!r}")


@pytest.mark.parametrize("late", [0.0, 0.3, 0.5])
def test_f1_a_reader_that_is_late_but_inside_the_drain_window_keeps_everything(tmp_path, late):
    """The sidecar side of F-1: nothing the sidecar does flushes the CLI's output early."""
    peer = FramePeer(tmp_path)
    try:
        sid, generation = peer.create(["/bin/echo", "FASTOUT"])
        time.sleep(late)  # the first read comes late, but inside the kernel's drain window
        assert read_until_end(peer.masters[sid]) == b"FASTOUT\r\n"
        os.close(peer.masters.pop(sid))
        peer.result(peer.send(code.TTY_EOF, {"session_id": sid, "session_generation": generation, "reason": "eof"}))
        cleaned = peer.wait_for(lambda: next((e for e in peer.events if e.code == code.SESSION_CLEANED), None), "SESSION_CLEANED", 20)
        assert cleaned.meta["cleanup_complete"] is True and cleaned.meta["root_reaped"] is True
    finally:
        peer.close()


def test_f1_after_the_create_only_the_cli_holds_the_slave(tmp_path):
    """A direct observation: once a create is answered, no sidecar copy of the slave is left (lsof)."""
    import subprocess
    peer = FramePeer(tmp_path)
    try:
        script = tmp_path / "idle.py"
        script.write_text("import time\ntime.sleep(60)\n")
        sid, _generation = peer.create([base_python_executable(), str(script)])
        owned = next(e for e in peer.events if e.code == code.SESSION_OWNED)
        holders = subprocess.run(["lsof", "-F", "p", owned.meta["pty"]["tty"]], capture_output=True, text=True).stdout
        pids = {int(line[1:]) for line in holders.splitlines() if line.startswith("p")}
        assert pids == {owned.meta["root"]["pid"]}, f"slave held by {pids}, sidecar is {peer.process.pid}"
    finally:
        peer.close()


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, HARNESS)
    try:
        yield rig
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_f1_a_create_answer_held_far_beyond_the_drain_window_still_delivers_the_full_output(harness):
    """8 item 4: the slot reads its master from the claim on. Without that reader the CLI's exit blocks ~0.6 s
    and then loses what it wrote (the unpublished record is invisible to the Terminal Stream's reader)."""
    service = harness.service()
    release = harness.root / "release-answer"
    produced = "".join(f"LINE-{n:04d}\n" for n in range(300))
    task = asyncio.create_task(service.create_async(
        pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(harness.workspace), env=harness.env,
        cols=100, rows=30,
        command=[base_python_executable(), "-c", f"import sys; sys.stdout.write({produced!r}); sys.stdout.flush()"]))
    await harness.until(lambda: harness.receipts_of("exit"), "SESSION_EXITED arrived while the answer is still held")
    await asyncio.sleep(1.2)  # well past the ~0.6 s kernel drain window
    assert not task.done()
    release.touch()
    session = await asyncio.wait_for(task, 20)
    await harness.until(lambda: harness.sink.exits(session.id), "terminal.exit", 20)
    text = harness.sink.text(session.id).replace("\r\n", "\n")
    assert text == produced, f"{len(text)} of {len(produced)} bytes"
    handed = harness.receipts_of("preread_handed_over", session_id=session.id)[0]
    assert handed["bytes"] == len(produced) + produced.count("\n") and handed["dropped"] == 0 and handed["stream_ended"] is True
    harness.sink.assert_exit_after_last_byte(session.id)
    assert harness.sink.exits(session.id)[0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_f1_the_pre_publication_buffer_is_bounded_and_its_drops_are_accounted(harness):
    from agent_team_backend.terminals import _BUF_CAP
    service = harness.service()
    release = harness.root / "release-answer-flood"
    log = harness.root / "flood.log"
    total_lines = 120_000  # ~ 12 MB of output: more than twice the 5 MiB bound
    task = asyncio.create_task(service.create_async(
        pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(harness.workspace), env=harness.env,
        cols=100, rows=30, output_log_file=str(log),
        command=[base_python_executable(), "-c",
                 f"import sys\nfor n in range({total_lines}): sys.stdout.write('FLOOD %06d ' % n + 'x' * 88 + '\\n')\nsys.stdout.write('DONE\\n'); sys.stdout.flush()"]))
    await harness.until(lambda: harness.receipts_of("exit"), "SESSION_EXITED arrived while the answer is still held", 60)
    slot = next(iter(service._slots.values()))
    assert slot.buffered <= _BUF_CAP + 64 * 1024, f"{slot.buffered} bytes buffered against a {_BUF_CAP} bound"
    assert slot.dropped > 0
    release.touch()
    session = await asyncio.wait_for(task, 30)
    await harness.until(lambda: harness.sink.exits(session.id), "terminal.exit", 60)
    delivered = len(harness.sink.raw(session.id))
    handed = harness.receipts_of("preread_handed_over", session_id=session.id)[0]
    produced = total_lines * len("FLOOD 000000 " + "x" * 88 + "\r\n") + len("DONE\r\n")
    import re as _re
    got = harness.sink.raw(session.id)
    # Exact accounting: every byte the slot took from the master was delivered or counted as dropped.
    assert handed["read_total"] == handed["bytes"] + handed["dropped"], handed  # E1 v8b P12: on the receipt's own fields
    assert delivered <= handed["bytes"], "the renderer cannot have received more than the slot handed over"
    # Content: what was delivered is the newest part of the output, in order, with nothing missing after the cut.
    numbers = [int(n) for n in _re.findall(rb"FLOOD (\d{6}) ", got)]
    assert numbers == list(range(numbers[0], total_lines)), "delivered lines are contiguous up to the last one"
    assert numbers[0] > 0 and handed["dropped"] > 0, "the oldest output is what the bound dropped"
    # The kernel's own output processing may add a bare CR under backpressure (measured: '\r\r\n'); nothing else.
    assert handed["read_total"] >= produced and handed["read_total"] - produced < produced // 1000, handed
    assert harness.sink.text(session.id).rstrip().endswith("DONE"), "the newest output (the tail) is what survives"
    assert "bytes of output dropped" in log.read_text(encoding="utf-8"), "the existing drop marker is logged"


@pytest.mark.asyncio
async def test_f1_a_session_killed_before_publication_still_ends_while_its_output_is_being_read(harness):
    service = harness.service()
    release = harness.root / "release-never"
    task = asyncio.create_task(service.create_async(
        pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(harness.workspace), env=harness.env,
        command=[base_python_executable(), "-c", "import sys,time\nwhile True:\n    sys.stdout.write('y' * 4096); sys.stdout.flush(); time.sleep(0.001)"]))
    await harness.until(lambda: service._slots, "claimed, unpublished")
    sid = next(iter(service._slots))
    await asyncio.wait_for(service.kill(sid, force=True), 30)
    assert service._retained[sid].cleanup.is_set() and not service._retained[sid].cleanup_error
    release.touch()
    with pytest.raises(Exception):
        await asyncio.wait_for(task, 30)


@pytest.mark.asyncio
async def test_f1_through_the_production_service_fast_exit_clis_keep_their_full_output(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch, PRODUCT)
    try:
        service = rig.service()
        lost = []
        for index in range(25):
            session = await service.create_async(pane_id=f"pane-{index}", agent_key="terminal", command=["/bin/echo", f"FASTOUT-{index}"],
                                                 cwd=str(rig.workspace), cols=100, rows=30, env=rig.env)
            await rig.until(lambda s=session: rig.sink.exits(s.id), "terminal.exit", 20)
            if f"FASTOUT-{index}" not in rig.sink.text(session.id):
                lost.append(index)
        assert lost == [], f"output lost for {len(lost)} of 25 fast-exit sessions"
    finally:
        await rig.close()
