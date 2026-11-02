"""The end of a test that killed its sidecar: the loss it causes must start, finish in bound, and finish clean.

`RustTerminalService._lose` starts `_invalidate` as a task. A fixture that kills the sidecar and returns lets the loop
close with that task pending (the "Task was destroyed but it is pending" warnings of ticket g2-t13b). A fixture that
only waits, and ignores how the wait ended, would hide the product leak this waiting exists to expose: a loss that
never starts, that hangs, that raises, or that ends with `CLEANUP_FAILED`. Every one of those fails the teardown here.
"""
from __future__ import annotations

import asyncio
import time

from agent_team_backend import cli_runtime

# The bounds of the loss sequence (cleanup_ms is 8 s; the second LOSS_FLUSH_S is the exit-queue wait), plus slack.
LOSS_FINISH_S = (cli_runtime.TASK_DRAIN_S + cli_runtime.SIDECAR_EXIT_S + cli_runtime.PERSIST_WAIT_S
                 + 8.0 + 2 * cli_runtime.LOSS_FLUSH_S + 15.0)
LOSS_START_S = 10.0  # the reader notices the channel's EOF within one loop turn; this is slack, not an expectation
SIDECAR_GONE_S = 15.0  # after the fixture's kill


async def settle_lost_runtime(service, runtime, *, cleanup_may_fail: bool = False) -> None:
    """Call after the fixture killed `runtime` (or found it dead). Raises AssertionError, or the loss's own exception.

    `cleanup_may_fail` is for a test that itself provokes an incomplete cleanup (a torn journal, a surviving root) and
    says so on the fixture; the loss must still start and finish in bound and must not raise."""
    expected = service._ready_ok or service._invalidation is not None  # a sidecar that was never ready causes no loss
    try:
        await asyncio.wait_for(runtime.wait(), SIDECAR_GONE_S)
    except TimeoutError:
        raise AssertionError(f"the sidecar (pid {runtime.pid}) was still running {SIDECAR_GONE_S:.0f}s after the kill") from None
    if not expected:
        return
    deadline = time.monotonic() + LOSS_START_S
    while service._invalidation is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    invalidation = service._invalidation
    if invalidation is None:
        raise AssertionError(f"the sidecar is gone but no loss started within {LOSS_START_S:.0f}s")
    _done, pending = await asyncio.wait({invalidation}, timeout=LOSS_FINISH_S)
    if pending:
        invalidation.cancel()
        raise AssertionError(f"the loss was still running {LOSS_FINISH_S:.0f}s after the sidecar was gone")
    if not invalidation.cancelled() and (error := invalidation.exception()) is not None:
        raise error
    if service._lost and "CLEANUP_FAILED" in service._lost and not cleanup_may_fail:
        raise AssertionError(f"the loss ended without a verified cleanup: {service._lost}")
