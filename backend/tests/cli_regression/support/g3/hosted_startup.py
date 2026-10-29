"""What `hosted_site/sitecustomize.py` does at interpreter start, as a function with injectable parts (so it is tested on any machine).

* `ACR_G3_HOSTED_TERMIOS=1` on Windows: only the `termios` shim (stdlib + ctypes, no harness module), which is all a measured peer process loads from this port;
* `ACR_G3_HOSTED_COUNTERS=1` on Windows: only the counters pipe server (the isolated backend process);
* `ACR_G3_HOSTED_RSS_BYTES=1` on Linux: only the bytes-valued `resource.getrusage` (`hosted_rss`, stdlib only), set by `hosted_reader.fresh_process_runner` for the fresh reader measurement child;
* `ACR_G3_HOSTED=1`: the hosted seams of the running OS (`hosted_seams.install_from_env`).
After each install that worked, the process writes its installed marker (`hosted_markers`). An install that raises propagates: `sitecustomize` ends the process with exit 97, a half-installed port never runs.
"""
from __future__ import annotations

import os
from typing import Callable

from tests.cli_regression.support.g3 import hosted_markers


def _termios() -> object:
    from tests.cli_regression.support.g3 import hosted_termios
    return hosted_termios.install()


def _counters() -> object:
    from tests.cli_regression.support.g3 import hosted_counters
    return hosted_counters.install_pipe_serve()


def _reader_v2() -> object:
    from tests.cli_regression.support.g3 import hosted_counters
    return hosted_counters.install_reader_v2()


def _seams() -> object:
    from tests.cli_regression.support.g3 import hosted_seams
    return hosted_seams.install_from_env()


def _posix_names() -> object:
    from tests.cli_regression.support.g3 import hosted_posix_stubs
    return hosted_posix_stubs.install()


def _rss_bytes() -> object:
    from tests.cli_regression.support.g3 import hosted_rss
    return hosted_rss.install()


BACKEND_ROLE = "backend_entry"      # `hosted_checks.HOOK_ROLES["counters-server"]`, repeated: this module imports no harness module. The role of `python -m ...backend_entry`, which is how `BackendProcess` starts the backend


def install_all(env: dict | None = None, os_name: str | None = None, *, seams: Callable[[], object] = _seams, termios: Callable[[], object] = _termios, counters: Callable[[], object] = _counters,
                rss_bytes: Callable[[], object] = _rss_bytes, write: Callable[..., bool] = hosted_markers.write, posix_names: Callable[[], object] = _posix_names,
                reader_v2: Callable[[], object] = _reader_v2, role: Callable[[], str] | None = None) -> list[str]:
    """The marker kinds written, in install order. The Linux reader child (`ACR_G3_HOSTED_RSS_BYTES=1`, posix) normalises `ru_maxrss` to bytes and writes `reader-rss-bytes` only if that install happened.
    `role` (rh1, RC-2): the counters pipe server and the reader counters v2 belong to the backend process alone. A pane inherits the backend's environment but runs the BASE interpreter (`base_python_executable()`),
    which has none of the venv's packages: installing either hook there imports `agent_team_backend` and ends the pane with exit 97 (`No module named 'psutil'`) before its READY line, which failed the smoke's
    claude and aider attempts. With `role` given (`sitecustomize` passes `hosted_markers.process_role`) those two installs run only when it returns `BACKEND_ROLE`; without it every flag installs as before."""
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    done: list[str] = []
    backend = role is None or role() == BACKEND_ROLE
    if os_name == "nt":      # before any harness module is imported: unchanged g1e modules read POSIX-only names at import time (`hosted_posix_stubs`); no marker, nothing to report when none was missing
        posix_names()
    if env.get("ACR_G3_HOSTED_TERMIOS") == "1" and os_name == "nt":
        termios()
        write("termios-shim")
        done.append("termios-shim")
    if env.get("ACR_G3_HOSTED_COUNTERS") == "1" and os_name == "nt" and backend:
        counters()
        write("counters-server")
        done.append("counters-server")
    if env.get("ACR_G3_HOSTED_READER_V2") == "1" and backend:      # the isolated backend process, both systems
        reader_v2()
        write("reader-counters-v2")
        done.append("reader-counters-v2")
    if env.get("ACR_G3_HOSTED_RSS_BYTES") == "1" and os_name == "posix" and rss_bytes() is not None:
        write("reader-rss-bytes")
        done.append("reader-rss-bytes")
    if env.get("ACR_G3_HOSTED") == "1" and seams() is not None:
        write("python-seams")
        done.append("python-seams")
    return done
