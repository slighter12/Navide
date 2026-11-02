"""t14 item 6: a client that connects while the backend's port file exists but uvicorn is not serving yet.

`__main__.main` writes `backend-port` right after `_hold_free_port` binds the socket (`__main__.py:227-242`). The socket is only bound: uvicorn calls `listen()` on it from
`Server.startup`, after the app lifespan has started (`uvicorn/server.py:107` runs `lifespan.startup()` before `loop.create_server`), and the handover line is read in between
(`__main__.py:262`). Upstream `d8b652631` ("hold the free port until uvicorn serves on it") stops another process from taking the port; it does not make the held socket accept.

The window is held open deterministically here by not sending the handover line. In a real launch the window is the time from the port file to `listen()`: about a millisecond on this
machine (`.scratch/.../t14-harness/item6-timeline.log`), longer on a slow or busy host. A connection inside it is refused (Linux: ECONNREFUSED; macOS: the SYN is dropped, so a timeout).
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from tests.test_main_graceful_shutdown import _read_listen_port, _wait_healthy
from tests.test_main_parent_watch import _isolated_backend_env


class EarlyConnectRefused(Exception):
    """The one expected failure: the early client was refused while the port file already existed."""


# Only EarlyConnectRefused is the expected failure; a missing port file or a backend that never serves fails the test.
@pytest.mark.xfail(strict=True, raises=EarlyConnectRefused, reason="product finding (t14 item 6): the port file is written before uvicorn listens, so an early client is refused; not fixed in the eval stage")
def test_a_client_that_connects_before_uvicorn_serves_is_accepted(tmp_path) -> None:
    backend = subprocess.Popen(
        [sys.executable, "-m", "agent_team_backend", "--port", "0", "--log-level", "info"],
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
        # The backend now waits for the handover line on stdin: the port file exists, the socket is bound, and nothing listens.
        try:
            early = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        except OSError as err:
            early = err
        backend.stdin.write("\n")
        backend.stdin.flush()
        _read_listen_port(backend)
        _wait_healthy(port)  # the control: the same port serves once uvicorn runs
        if not isinstance(early, socket.socket):
            raise EarlyConnectRefused(f"early connect to port {port} failed: {type(early).__name__}")
        early.close()
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait(timeout=10)
