"""`terminal.exit(runtime_lost)` reports the session's real age (ticket g2-t13b, item 3).

The renderer prints `uptime_ms` as "Process ended (runtime_lost) <n>ms after spawn". For a lost runtime the figure
is `now - started_monotonic` of the session (`cli_runtime._invalidate`). These tests let real time pass between the
create and the loss, then bound the reported figure by what the test itself measured: never below the time the
session was known to be alive, never above the time since its create began.
"""
from __future__ import annotations

import asyncio
import sys
import time

import pytest

from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT
from tests.test_cli_runtime_lifecycle import LifeRig, LOSS_BOUND_S, harness  # noqa: F401 - the fault-hook build's rig

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32" or not PRODUCT.is_file(),
                       reason="requires the explicitly built POSIX native runtime"),
]

AGE_S = 3.0


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    rig = LifeRig(tmp_path, monkeypatch)
    try:
        yield rig
    finally:
        await rig.close()


async def lose_and_report(rig: LifeRig, service, session, alive_since: float, began: float) -> int:
    """Kill the sidecar, wait for the loss to present the one exit, and check the figure against the test's own clock."""
    alive_for_ms = (time.monotonic() - alive_since) * 1000
    rig.kill_sidecar(service)
    await rig.until(lambda: rig.sink.exits(session.id), "the runtime_lost exit", LOSS_BOUND_S + 15)
    seen_at = time.monotonic()
    (exit_event,) = rig.sink.exits(session.id)
    assert exit_event["reason"] == "runtime_lost"
    uptime = exit_event["uptime_ms"]
    assert isinstance(uptime, int)
    assert uptime >= alive_for_ms, f"{uptime}ms reported, the session was known alive for {alive_for_ms:.0f}ms"
    assert uptime <= (seen_at - began) * 1000, f"{uptime}ms reported, more than the session can have existed"
    await rig.recovered(service)
    return uptime


async def test_a_lost_runtime_reports_the_age_of_the_session_it_took(rig):
    service = rig.service()
    began = time.monotonic()
    session = await rig.life(service, "aged")
    alive_since = time.monotonic()
    await asyncio.sleep(AGE_S)
    uptime = await lose_and_report(rig, service, session, alive_since, began)
    assert uptime >= AGE_S * 1000


async def test_a_reattach_does_not_restart_the_session_clock(rig):
    """`terminal.reattach` rebinds a reconnecting renderer through `control_async(..., redraw=True)` on the same
    session object; the age must keep counting from the create."""
    service = rig.service()
    began = time.monotonic()
    session = await rig.life(service, "reattached")
    alive_since = time.monotonic()
    await asyncio.sleep(AGE_S / 2)
    async with service.control_async(session.id, 120, 40, redraw=True):
        pass
    await asyncio.sleep(AGE_S / 2)
    uptime = await lose_and_report(rig, service, session, alive_since, began)
    assert uptime >= AGE_S * 1000


async def test_a_session_of_the_next_generation_is_aged_from_its_own_create(rig):
    """After a clean loss the next create starts a fresh runtime: its sessions count from their own start, not from
    the lost generation's sessions and not from zero."""
    service = rig.service()
    began = time.monotonic()
    first = await rig.life(service, "first")
    alive_since = time.monotonic()
    await asyncio.sleep(AGE_S)
    await lose_and_report(rig, service, first, alive_since, began)
    began = time.monotonic()
    second = await rig.life(service, "second")
    alive_since = time.monotonic()
    await asyncio.sleep(AGE_S)
    uptime = await lose_and_report(rig, service, second, alive_since, began)
    assert AGE_S * 1000 <= uptime < (time.monotonic() - began) * 1000


@pytest.mark.skipif(not HARNESS.is_file(), reason="requires the fault-hook build of the runtime")
async def test_a_session_whose_create_answer_is_held_is_aged_from_its_spawn_not_from_its_publication(harness):
    """The handoff seam: the sidecar reports the session (`owned`, with its descriptor) and the session is published to
    the renderer only when the create is answered, here AGE_S later. A session object built at publication would report
    only the time since then; the age must count from the spawn."""
    service = harness.service()
    release = harness.root / "release-answer"
    began = time.monotonic()
    task = asyncio.create_task(service.create_async(
        pane_id=f"fault:delay-response:{release}", agent_key="terminal", cwd=str(harness.workspace), env=harness.env,
        cols=100, rows=30, command=[base_python_executable(), "-u", str(harness.peer)]))
    await harness.until(lambda: service._retained, "the sidecar's owned event (the session exists, unpublished)", 20)
    alive_since = time.monotonic()
    assert not task.done(), "the answer is still held"
    await asyncio.sleep(AGE_S)
    release.touch()
    session = await asyncio.wait_for(task, 20)
    uptime = await lose_and_report(harness, service, session, alive_since, began)
    assert uptime >= AGE_S * 1000
