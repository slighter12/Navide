"""P9: a scan executor's synchronous wait for one observation is bounded, and giving up cancels the request.

The wait is the one place a worker thread blocks on the service's loop. A service that never answers must cost the
caller `OBSERVE_RESULT_S`, not a thread forever, and the abandoned request must not stay alive on the loop.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent_team_backend import app, cli_runtime_observation


class SilentService:
    """A service whose loop runs but whose observation is never answered."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    async def observe(self, vendor, parser, request, *, match, validate=None, receipt=None):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


@pytest.fixture
def silent(monkeypatch):
    service = SilentService()
    monkeypatch.setattr(app, "get_terminals", lambda: service)
    yield service
    service.stop()


def test_a_service_that_never_answers_costs_the_caller_the_bound_and_the_request_is_cancelled(silent, monkeypatch):
    monkeypatch.setattr(cli_runtime_observation, "OBSERVE_RESULT_S", 0.3)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        cli_runtime_observation._submit("claude", "parser", {}, match=lambda _sessions: None, receipt=None)

    assert 0.25 <= time.monotonic() - started < 3.0, "the wait ends at the bound, not before and not much after"
    assert silent.started.is_set()
    assert silent.cancelled.wait(timeout=5), "the abandoned observation is still running on the service's loop"


def test_the_bound_is_twenty_seconds():
    assert cli_runtime_observation.OBSERVE_RESULT_S == 20
