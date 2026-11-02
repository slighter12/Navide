"""`tests.runtime_loss_teardown.settle_lost_runtime` fails the teardown of a test whose runtime loss goes wrong.

The helper is the only thing that makes a fixture's kill of the sidecar observable: a loss that never starts, that does not finish in
bound, that raises, that ends with `CLEANUP_FAILED`, or a kill that does not take must each fail here, or a product leak would pass
every fixture that uses it. Each branch is driven with a small stand-in for the service and the sidecar, and a short bound set by
monkeypatching the helper module's constants; the shipped bounds are not changed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests import runtime_loss_teardown as teardown


class Sidecar:
    pid = 4242

    def __init__(self, *, exits: bool = True) -> None:
        self._exited = asyncio.Event()
        if exits:
            self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return -9


async def finished(error: BaseException | None = None) -> asyncio.Task:
    async def body() -> None:
        if error is not None:
            raise error

    task = asyncio.ensure_future(body())
    await asyncio.wait({task})
    return task


def service_for(*, generation: str = "g2", ready_ok: bool = True, retired: dict | None = None, invalidation=None, lost: str | None = None):
    """The four attributes the helper reads from a RustTerminalService."""
    return SimpleNamespace(_runtime_generation=generation, _ready_ok=ready_ok, _retired=dict(retired or {}),
                           _invalidation=invalidation, _lost=lost)


@pytest.fixture
def short_bounds(monkeypatch):
    monkeypatch.setattr(teardown, "SIDECAR_GONE_S", 0.05)
    monkeypatch.setattr(teardown, "LOSS_START_S", 0.05)
    monkeypatch.setattr(teardown, "LOSS_FINISH_S", 0.05)


async def test_a_loss_that_starts_and_finishes_clean_passes(short_bounds) -> None:
    service = service_for(retired={"g2": True}, invalidation=await finished())
    await teardown.settle_lost_runtime(service, Sidecar())


async def test_a_loss_that_starts_after_the_helper_began_waiting_passes(short_bounds, monkeypatch) -> None:
    monkeypatch.setattr(teardown, "LOSS_START_S", 5.0)
    service = service_for()

    async def start_the_loss_late() -> None:
        await asyncio.sleep(0.05)
        service._invalidation = await finished()
        service._retired["g2"] = True

    starter = asyncio.create_task(start_the_loss_late())
    await teardown.settle_lost_runtime(service, Sidecar())
    await starter


async def test_a_sidecar_that_was_never_ready_causes_no_loss_to_wait_for(short_bounds) -> None:
    await teardown.settle_lost_runtime(service_for(ready_ok=False), Sidecar())


async def test_a_kill_that_does_not_take_fails(short_bounds) -> None:
    with pytest.raises(AssertionError, match=r"pid 4242\) was still running"):
        await teardown.settle_lost_runtime(service_for(), Sidecar(exits=False))


async def test_a_loss_that_never_starts_fails(short_bounds) -> None:
    with pytest.raises(AssertionError, match="no loss started"):
        await teardown.settle_lost_runtime(service_for(), Sidecar())


async def test_an_older_generations_finished_loss_does_not_satisfy_the_check(short_bounds) -> None:
    """The service restarted into generation g2 after g1's loss finished; g2's sidecar was killed and no g2 loss began."""
    service = service_for(generation="g2", retired={"g1": True}, invalidation=await finished())
    with pytest.raises(AssertionError, match="no loss started"):
        await teardown.settle_lost_runtime(service, Sidecar())


async def test_a_loss_that_does_not_finish_in_bound_fails_and_is_cancelled(short_bounds) -> None:
    invalidation = asyncio.ensure_future(asyncio.sleep(3600))
    service = service_for(retired={"g2": None}, invalidation=invalidation)
    with pytest.raises(AssertionError, match="loss was still running"):
        await teardown.settle_lost_runtime(service, Sidecar())
    await asyncio.wait({invalidation}, timeout=1.0)
    assert invalidation.cancelled()


async def test_a_loss_that_raises_fails_with_its_own_exception(short_bounds) -> None:
    service = service_for(retired={"g2": False}, invalidation=await finished(RuntimeError("loss blew up")))
    with pytest.raises(RuntimeError, match="loss blew up"):
        await teardown.settle_lost_runtime(service, Sidecar())


async def test_a_loss_that_raises_fails_even_when_cleanup_may_fail(short_bounds) -> None:
    service = service_for(retired={"g2": False}, invalidation=await finished(RuntimeError("loss blew up")))
    with pytest.raises(RuntimeError, match="loss blew up"):
        await teardown.settle_lost_runtime(service, Sidecar(), cleanup_may_fail=True)


async def test_cleanup_failed_fails_unless_the_test_says_it_provokes_it(short_bounds) -> None:
    lost = "CLEANUP_FAILED: runtime loss cleanup incomplete (torn journal)"
    service = service_for(retired={"g2": False}, invalidation=await finished(), lost=lost)
    with pytest.raises(AssertionError, match="without a verified cleanup"):
        await teardown.settle_lost_runtime(service, Sidecar())
    await teardown.settle_lost_runtime(service, Sidecar(), cleanup_may_fail=True)
