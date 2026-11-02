"""t14 item 6 / t17b: a client that connects while the backend's port file exists but uvicorn is not serving yet.

`__main__.main` writes `backend-port` right after `_hold_free_port` binds the socket. The socket used to be only bound: uvicorn calls `listen()` on it from `Server.startup`, after the
app lifespan has started (`uvicorn/server.py:107` runs `lifespan.startup()` before `loop.create_server`), and the handover line is read in between. Upstream `d8b652631` ("hold the
free port until uvicorn serves on it") stopped another process from taking the port but did not make the held socket accept, so a client inside that window was refused (Linux:
ECONNREFUSED; macOS: the SYN is dropped, so a timeout). `_hold_free_port` now listens, so the connection is queued and uvicorn serves it.

Both launches are covered. `--port 0` is how the tests start the backend; the desktop app always passes an explicit port (`src/main/backend.ts`, `findFreePort`), so the explicit-port
path must hold the port the same way. A port that cannot be bound fails before the port file is written.

The window is held open deterministically here by not sending the handover line. In a real launch the window is the time from the port file to `listen()`: about a millisecond on
the t14 machine, longer on a slow or busy host.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from tests.test_main_graceful_shutdown import _read_listen_port, _wait_healthy
from tests.test_main_parent_watch import _isolated_backend_env


def _free_port() -> int:
    """A port nothing holds right now, chosen the way the app does (`findFreePort`): bind port 0, read it, close."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("explicit_port", [False, True], ids=["port-0", "explicit-port"])
def test_a_client_that_connects_before_uvicorn_serves_is_accepted(tmp_path, explicit_port: bool) -> None:
    requested = _free_port() if explicit_port else 0
    backend = subprocess.Popen(
        [sys.executable, "-m", "agent_team_backend", "--port", str(requested), "--log-level", "info"],
        env=_isolated_backend_env(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        assert backend.stdin is not None
        port_file = tmp_path / "backend-port"
        deadline = time.monotonic() + 60
        text = ""
        while time.monotonic() < deadline and not text:
            text = port_file.read_text(encoding="utf-8").strip() if port_file.exists() else ""
            time.sleep(0.001)
        assert text, "the port file never appeared"
        port = int(text)
        if explicit_port:
            assert port == requested
        # The backend now waits for the handover line on stdin: the port file exists and uvicorn has not started serving.
        try:
            early = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        except OSError as err:
            early = err
        backend.stdin.write("\n")
        backend.stdin.flush()
        _read_listen_port(backend)
        _wait_healthy(port)  # the control: the same port serves once uvicorn runs
        assert isinstance(early, socket.socket), f"early connect to port {port} failed: {early!r}"
        early.close()
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait(timeout=10)


def test_a_port_that_is_already_taken_exits_non_zero_before_the_port_file_is_written(tmp_path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupant:
        occupant.bind(("127.0.0.1", 0))
        occupant.listen(1)
        taken = occupant.getsockname()[1]
        backend = subprocess.Popen(
            [sys.executable, "-m", "agent_team_backend", "--port", str(taken), "--log-level", "info"],
            env=_isolated_backend_env(tmp_path),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            # The backend must not wait for the handover line: it cannot serve on that port, so it ends on its own.
            code = backend.wait(timeout=30)
            error_output = backend.stderr.read() if backend.stderr else ""
        finally:
            if backend.poll() is None:
                backend.kill()
                backend.wait(timeout=10)
    assert code != 0
    assert not (tmp_path / "backend-port").exists(), "the port file already named a port the backend never held"
    assert str(taken) in error_output, "the failure names the port it could not bind"
