"""The Rust runtime starts in the background at the first client connection (ticket g2-t11, F3).

A request that arrives during that start waits for the same start and gets its outcome; a start that fails after
the sidecar process exists is settled (child reaped, causal failure kept) before the background task drops its
exception, so no request ever sees a half-started runtime.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import psutil
import pytest

from agent_team_backend import cli_runtime
from tests.cli_regression.support.frame_peer import PRODUCT
from tests.test_cli_runtime_review1 import stop_stub, stub_service

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32", reason="the POSIX runtime"),
]


def receipts(root: Path, event: str) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [record for record in records if record["event"] == event]


async def until(predicate, what: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.02)


def sidecars(backend) -> list[psutil.Process]:
    found = []
    for child in backend.python_process().children():
        try:
            if child.name().startswith("navide-cli-runtime"):
                found.append(child)
        except psutil.NoSuchProcess:
            pass
    return found


def rust_backend(root: Path, binary: Path):
    from tests.cli_regression.support.backend_process import BackendProcess
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(binary)
    return backend


@pytest.mark.skipif(not PRODUCT.is_file(), reason="requires the explicitly built POSIX native runtime")
async def test_the_first_connection_starts_the_runtime_and_the_first_create_uses_that_start(tmp_path):
    backend = rust_backend(tmp_path, PRODUCT)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with backend:
        async with backend.connect() as ws:
            await until(lambda: receipts(tmp_path, "ready"), "a ready receipt with no request sent")
            assert len(sidecars(backend)) == 1
            response = await backend.request(ws, "terminal.create", {
                "pane_id": "prewarm", "agent_key": "terminal", "command": backend.fake_command(),
                "cwd": str(workspace), "cols": 100, "rows": 30})
            session = response["payload"]["terminal_session_id"]
            await backend.expect_output(ws, session, "READY ")
            await backend.request(ws, "terminal.kill", {"terminal_session_id": session})
    assert len(receipts(tmp_path, "ready")) == 1, "the create used the background start, not a second one"


async def test_a_missing_runtime_still_refuses_the_first_create_and_starts_nothing(tmp_path):
    backend = rust_backend(tmp_path, tmp_path / "absent" / "navide-cli-runtime")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with backend:
        async with backend.connect() as ws:
            response = await backend.request(ws, "terminal.create", {
                "pane_id": "missing", "agent_key": "terminal", "command": backend.fake_command(),
                "cwd": str(workspace), "cols": 100, "rows": 30}, ok=False)
            assert response["ok"] is False
            assert "missing at explicit override" in json.dumps(response), response
            assert not sidecars(backend)
    assert not receipts(tmp_path, "ready")


class _IdentityDenied:
    """`psutil` as `cli_runtime` sees it, except that reading the spawned sidecar's identity is refused."""

    def __getattr__(self, name):
        return getattr(psutil, name)

    @staticmethod
    def Process(pid=None):  # noqa: N802 - mirrors psutil.Process
        raise psutil.AccessDenied(pid)


async def test_a_start_that_fails_after_the_spawn_is_settled_with_its_cause(tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "plain")
    monkeypatch.setattr(cli_runtime, "psutil", _IdentityDenied())
    try:
        with pytest.raises(RuntimeError, match="START_FAILED at identity"):
            await service._ensure_started()
        assert service._runtime.returncode is not None, "the half-started sidecar was reaped"
        with pytest.raises(RuntimeError, match="START_FAILED at identity"):
            await service._ensure_started()
        assert receipts(tmp_path, "startup_failed")
    finally:
        await stop_stub(service)


async def test_a_background_start_that_fails_after_the_spawn_refuses_the_first_create_with_its_cause(
        tmp_path, monkeypatch):
    service = stub_service(tmp_path, monkeypatch, "plain")
    monkeypatch.setattr(cli_runtime, "psutil", _IdentityDenied())
    kwargs = dict(agent_key="terminal", command=["/bin/cat"], cwd=str(tmp_path))
    try:
        service.prewarm()
        await asyncio.wait_for(service._prewarming, 20)  # the background start drops its failure
        with pytest.raises(RuntimeError, match="START_FAILED at identity"):
            await service.create_async(pane_id="pane-first", **kwargs)
        assert service._runtime.returncode is not None
        with pytest.raises(RuntimeError, match="START_FAILED at identity"):
            await service.create_async(pane_id="pane-second", **kwargs)
    finally:
        await stop_stub(service)
