"""P9: a scan executor's synchronous wait for one observation is bounded, and giving up cancels the request.

The wait is the one place a worker thread blocks on the service's loop. A service that never answers must cost the
caller `OBSERVE_RESULT_S`, not a thread forever, and the abandoned request must not stay alive on the loop.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time

import pytest

from agent_team_backend import app, cli_runtime_observation
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.terminals import NATIVE_OBSERVATION_UNAVAILABLE, TerminalInterface, TerminalService


class SilentService:
    """A service whose loop runs but whose observation is never answered."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def observation_loop(self):
        return self._loop

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


def test_a_service_with_no_loop_to_schedule_on_refuses_with_the_one_message(monkeypatch):
    """The bridge asks the service where observations run (`observation_loop`), and never reads its attributes. The
    in-process service has none, and says so with the same sentence its own `observe` refuses with."""
    monkeypatch.setattr(app, "get_terminals", lambda: TerminalInterface())

    with pytest.raises(RuntimeError, match=re.escape(NATIVE_OBSERVATION_UNAVAILABLE)):
        cli_runtime_observation._submit("claude", "parser", {}, match=lambda _sessions: None, receipt=None)

    with pytest.raises(RuntimeError, match=re.escape(NATIVE_OBSERVATION_UNAVAILABLE)):
        asyncio.run(TerminalInterface().observe("claude", "parser", {}, match=lambda _sessions: None))


def test_the_rust_service_schedules_observations_on_its_own_loop():
    async def build():
        service = RustTerminalService(lambda frame: asyncio.sleep(0))
        return service, service.observation_loop(), asyncio.get_running_loop()

    service, loop, running = asyncio.run(build())
    assert loop is running
    assert TerminalService.observation_loop is TerminalInterface.observation_loop, "the in-process service has no loop to offer"
