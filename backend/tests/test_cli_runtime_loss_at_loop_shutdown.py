"""A runtime loss whose invalidation fails because the loop is shutting down leaves no unretrieved exception.

pytest-asyncio and other runners shut the default executor down before they close the loop. An
invalidation still running then fails inside `asyncio.to_thread` ("Executor shutdown has been
called"). The failure must be settled by the invalidation itself: a done-callback that retrieves it
never runs when the loop closes in the same iteration, and the task is then logged as "Task
exception was never retrieved" when it is collected.
"""
from __future__ import annotations

import asyncio
import gc

from agent_team_backend.cli_runtime import RustTerminalService


def test_invalidation_failing_at_executor_shutdown_settles_without_an_exception(tmp_path):
    loop = asyncio.new_event_loop()
    reported: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: reported.append(context))

    async def lose_then_shut_the_executor_down() -> RustTerminalService:
        async def emit(_frame) -> None:
            return None

        service = RustTerminalService(emit)
        service._data_dir = tmp_path
        service._runtime_generation = "g" * 32
        service._ready_ok = True
        service._lose("RUNTIME_LOST: test")  # starts the invalidation task
        await loop.shutdown_default_executor()  # what a runner does before it closes the loop
        await asyncio.wait({service._invalidation})
        return service

    try:
        service = loop.run_until_complete(lose_then_shut_the_executor_down())
        invalidation = service._invalidation
        # The task itself carries no exception, whatever a callback does or does not get to run.
        assert invalidation.cancelled() or invalidation.exception() is None
        assert service._lost and service._lost.startswith("CLEANUP_FAILED")
    finally:
        loop.close()
    del invalidation, service
    gc.collect()

    messages = [context.get("message", "") for context in reported]
    assert not any("never retrieved" in message or "destroyed but it is pending" in message
                   for message in messages), messages
