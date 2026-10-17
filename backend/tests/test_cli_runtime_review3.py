"""Review 3 of the whole E3 ticket (evidence/e3/reviewer-e3-3.md): the non-blocking items that changed code.

N-4 (pre-publication chunks keep the reader's granularity), N-6 (`snapshot_ms` is validated like every other limit),
N-12 (no vendor literal in the shared adapter code, covered by the rules guard).
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_handoff import HandoffSlot
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT
from tests.test_cli_runtime_review1 import stop_stub, stub_service

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built macOS native runtime (product and harness)"),
]


async def test_n4_the_slot_hands_over_the_chunks_as_the_reads_returned_them():
    read_end, write_end = os.pipe()
    os.set_blocking(read_end, False)
    slot = HandoffSlot("n4", read_end)
    slot.start_reading(asyncio.get_running_loop(), 5 * 1024 * 1024)
    try:
        written = 0
        for chunk in (b"one\n", b"two-two\n", b"three-three-three\n"):
            os.write(write_end, chunk)
            written += len(chunk)
            for _ in range(400):  # bounded, so a regression fails instead of hanging
                await asyncio.sleep(0.005)
                if slot.read_total >= written:
                    break
            assert slot.read_total == written
        chunks, dropped, eof, errored = slot.take()
        assert chunks == [b"one\n", b"two-two\n", b"three-three-three\n"] and dropped == 0 and not eof and not errored
        assert slot.take()[0] == [], "take() empties the buffer"
    finally:
        slot.stop_reading()
        os.close(read_end)
        os.close(write_end)


async def test_n4_publication_absorbs_one_entry_per_read(tmp_path, monkeypatch):
    """Through the real sidecar and the production service: the CLI writes in separate flushes while its create answer is
    held; at publication `_absorb_output` runs once per buffered read, not once for a joined blob."""
    from tests.test_cli_runtime_lifecycle import LifeRig
    from tests.cli_regression.support.cli_shim import base_python_executable
    rig = LifeRig(tmp_path, monkeypatch, HARNESS)
    try:
        service = rig.service()
        absorbed: list[int] = []
        original = RustTerminalService._absorb_output

        def counting(self, session, chunk, nbytes):
            absorbed.append(nbytes)
            return original(self, session, chunk, nbytes)

        monkeypatch.setattr(RustTerminalService, "_absorb_output", counting)
        release = tmp_path / "release-n4"
        program = ("import sys, time\nfor n in range(4):\n    sys.stdout.write('part %d\\n' % n * 50); sys.stdout.flush(); time.sleep(0.15)\n")
        task = asyncio.create_task(service.create_async(
            pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
            cols=100, rows=30, command=[base_python_executable(), "-c", program]))
        await rig.until(lambda: rig.receipts_of("exit"), "the CLI finished while the answer is held", 20)
        release.touch()
        session = await asyncio.wait_for(task, 20)
        await rig.until(lambda: rig.sink.exits(session.id), "terminal.exit", 20)
        handed = rig.receipts_of("preread_handed_over", session_id=session.id)[0]
        assert len(absorbed) >= 4 and sum(absorbed) == handed["bytes"], (absorbed, handed)
        assert max(absorbed) < handed["bytes"], "never one joined blob"
    finally:
        await rig.close()


async def test_n6_snapshot_ms_is_validated_like_every_other_advertised_limit(tmp_path, monkeypatch):
    (tmp_path / "over").mkdir()
    over = stub_service(tmp_path / "over", monkeypatch, "late-ok", snapshot_ms=6_000)
    try:
        with pytest.raises(RuntimeError, match="PROTOCOL_MISMATCH"):
            await over._ensure_started()
    finally:
        await stop_stub(over)
    (tmp_path / "absent").mkdir()
    absent = stub_service(tmp_path / "absent", monkeypatch, "late-ok", snapshot_ms=None)
    try:
        with pytest.raises(RuntimeError, match="PROTOCOL_MISMATCH"):
            await absent._ensure_started()
    finally:
        await stop_stub(absent)
    (tmp_path / "ok").mkdir()
    fine = stub_service(tmp_path / "ok", monkeypatch, "late-ok", snapshot_ms=5_000)
    try:
        await fine._ensure_started()
        assert fine._limits["snapshot_ms"] == 5_000
    finally:
        await stop_stub(fine)
