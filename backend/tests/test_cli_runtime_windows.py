"""The Rust runtime on Windows through the production client (`RustTerminalService`), real sidecar, real ConPTY.

Hosted-only (windows-2025, build 26100, Windows 11 24H2 or later by decision; pre-24H2 is out of scope).
Each case is also a negative control for the harness: a missing binary must fail the create, not skip it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from agent_team_backend import cli_runtime
from agent_team_backend.cli_runtime import RustTerminalService

ROOT = Path(__file__).resolve().parents[2]
PEER = Path(__file__).parent / "cli_regression/support/windows_peer.py"
BINARY = Path(os.environ.get("NAVIDE_CLI_RUNTIME_BIN") or ROOT / "native/navide-cli-runtime/target/release/navide-cli-runtime.exe")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "win32", reason="the Windows runtime client only runs on Windows"),
]


class Sink:
    """The WebSocket sink: records the output bytes and the exit payload of every session."""

    def __init__(self):
        self.out: dict[str, bytearray] = {}
        self.exits: dict[str, list[dict]] = {}
        self.order: list[tuple[str, str]] = []

    async def __call__(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            sid_len = frame[5]
            sid = bytes(frame[6:6 + sid_len]).decode()
            payload = bytes(frame[7 + sid_len + frame[6 + sid_len]:])
            self.out.setdefault(sid, bytearray()).extend(payload)
            self.order.append(("out", sid))
        elif frame.get("type") == "terminal.exit":
            sid = frame["payload"].get("terminal_session_id", "")
            self.exits.setdefault(sid, []).append(frame["payload"])
            self.order.append(("exit", sid))

    def text(self, sid: str) -> str:
        return bytes(self.out.get(sid, b"")).decode("utf-8", "replace")


async def until(predicate, message: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.02)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tests.cli_regression.support.isolation import isolated_environment
    if not BINARY.is_file():
        pytest.fail(f"the Windows runtime binary is missing: {BINARY}")  # fail, never skip
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(BINARY))
    env = isolated_environment(tmp_path)
    monkeypatch.setenv("AGENT_TEAM_DATA_DIR", env["AGENT_TEAM_DATA_DIR"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = Sink()

    class Rig:
        _service = None

        @property
        def service(self):
            # Built on first use, inside the running test loop: the service binds the loop it is created in.
            if self._service is None:
                self._service = RustTerminalService(sink)
            return self._service

    made = Rig()
    made.sink, made.env, made.workspace = sink, env, workspace
    yield made


async def start(rig, pane: str):
    session = await rig.service.create_async(
        pane_id=pane, agent_key="terminal", command=[sys.executable, "-u", str(PEER)],
        cwd=str(rig.workspace), cols=100, rows=30, env=rig.env)
    await until(lambda: "WP_READY" in rig.sink.text(session.id), f"{pane} ready")
    return session


async def stop(rig):
    if rig._service is None:
        return
    runtime = rig.service._runtime
    if runtime is not None and runtime.returncode is None:
        runtime.kill()
        await runtime.wait()


async def test_windows_service_runs_concurrent_isolated_sessions_with_unicode_resize_and_exit(rig):
    try:
        names = [f"w{n}" for n in range(4)]
        sessions = await asyncio.gather(*(start(rig, name) for name in names))
        for session, name in zip(sessions, names):
            await rig.service.write_async(session.id, f"say MARK-{name}-one\r")
            await rig.service.write_async(session.id, f"say MARK-{name}-two\r")
            await rig.service.write_async(session.id, "uni\r")
        for session, name in zip(sessions, names):
            await until(lambda s=session, n=name: f"MARK-{n}-two" in rig.sink.text(s.id), f"{name} marker")
            text = rig.sink.text(session.id)
            assert text.index(f"MARK-{name}-one") < text.index(f"MARK-{name}-two"), "ordered"
            await until(lambda s=session: "café 世界" in rig.sink.text(s.id), f"{name} unicode")
            for other in names:
                if other != name:
                    assert f"MARK-{other}-" not in text, f"{name} saw {other}'s bytes"
        first = sessions[0]
        async with rig.service.control_async(first.id, 120, 40):
            pass
        await rig.service.write_async(first.id, "size\r")
        await until(lambda: "SIZE 120x40" in rig.sink.text(first.id), "resize reached the console")
        for session in sessions:
            await rig.service.write_async(session.id, "quit\r")
        for session in sessions:
            await until(lambda s=session: rig.sink.exits.get(s.id), f"{session.id} exit published")
            assert len(rig.sink.exits[session.id]) == 1
            assert rig.sink.exits[session.id][0].get("exit_code") == 0
            last_out = max(i for i, (kind, sid) in enumerate(rig.sink.order) if kind == "out" and sid == session.id)
            exit_at = rig.sink.order.index(("exit", session.id))
            assert exit_at > last_out, "output followed terminal.exit"
    finally:
        await stop(rig)


async def test_windows_sidecar_death_with_sessions_fails_visibly_and_recovers_on_a_fresh_runtime(rig):
    try:
        session = await start(rig, "dead")
        pid = rig.service._runtime_identity["pid"]
        root = session.proc.pid
        psutil.Process(pid).kill()
        await until(lambda: rig.service._invalidation is not None, "loss started")
        async with asyncio.timeout(cli_runtime.TASK_DRAIN_S + cli_runtime.SIDECAR_EXIT_S + cli_runtime.PERSIST_WAIT_S + 20):
            await asyncio.shield(rig.service._invalidation)
        assert not psutil.pid_exists(root) or psutil.Process(root).status() == psutil.STATUS_ZOMBIE, "the root outlived the sidecar"
        rig.service._lost = None  # a fresh runtime generation is created on demand
        rig.service._runtime = None
        again = await start(rig, "after")
        await rig.service.write_async(again.id, "say AFTER-OK\r")
        await until(lambda: "ECHO AFTER-OK" in rig.sink.text(again.id), "fresh runtime works")
    finally:
        await stop(rig)


async def test_windows_missing_binary_fails_the_create_instead_of_skipping(tmp_path, monkeypatch):
    from tests.cli_regression.support.isolation import isolated_environment
    env = isolated_environment(tmp_path)
    monkeypatch.setenv("AGENT_TEAM_DATA_DIR", env["AGENT_TEAM_DATA_DIR"])
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(tmp_path / "missing-runtime.exe"))
    service = RustTerminalService(Sink())
    with pytest.raises(Exception):
        await service.create_async(pane_id="x", agent_key="terminal", command=[sys.executable, "-c", "pass"],
                                   cwd=str(tmp_path), cols=80, rows=24, env=env)
