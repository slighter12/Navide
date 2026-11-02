"""The real shutdown paths of the Rust runtime leave no pending loss task behind (ticket g2-t13b, item 2).

`RustTerminalService._invalidate` runs the runtime-loss cleanup. A backend that exits, normally or with its sidecar
already dead, must not print "Task was destroyed but it is pending", "Executor shutdown has been called" or a
never-awaited cleanup coroutine: those lines mean a loss was still being retired when the event loop closed.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import sys

import pytest

from tests.cli_regression.support.frame_peer import PRODUCT
from tests.test_cli_runtime_prewarm import rust_backend, sidecars, until

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32" or not PRODUCT.is_file(),
                       reason="requires the explicitly built POSIX native runtime"),
]

TEARDOWN_MARKERS = ("Task was destroyed", "Executor shutdown", "was never awaited", "never retrieved")


def teardown_lines(log: Path) -> list[str]:
    return [line for line in log.read_text(encoding="utf-8").splitlines()
            if any(marker in line for marker in TEARDOWN_MARKERS)]


async def open_session(backend, ws, workspace: Path) -> str:
    response = await backend.request(ws, "terminal.create", {
        "pane_id": "shutdown", "agent_key": "terminal", "command": backend.fake_command(),
        "cwd": str(workspace), "cols": 100, "rows": 30})
    session = response["payload"]["terminal_session_id"]
    await backend.expect_output(ws, session, "READY ")
    return session


async def test_a_backend_that_shuts_down_with_a_live_session_leaves_no_pending_loss_task(tmp_path):
    backend = rust_backend(tmp_path, PRODUCT)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with backend:
        async with backend.connect() as ws:
            await open_session(backend, ws, workspace)
    assert teardown_lines(backend.log_path) == []


async def test_a_backend_that_shuts_down_right_after_its_sidecar_was_killed_leaves_no_pending_loss_task(tmp_path):
    backend = rust_backend(tmp_path, PRODUCT)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    failure = None
    try:
        async with backend:
            async with backend.connect() as ws:
                await open_session(backend, ws, workspace)
                (sidecar,) = sidecars(backend)
                os.kill(sidecar.pid, signal.SIGKILL)
                await until(lambda: not sidecar.is_running(), "the sidecar to be gone", 5)
    except AssertionError as error:  # the harness's own survivor checks; the log below is what this test asserts on
        failure = error
    assert teardown_lines(backend.log_path) == [], failure
    assert failure is None, failure
