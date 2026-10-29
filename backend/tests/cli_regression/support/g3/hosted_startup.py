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


def _seams() -> object:
    from tests.cli_regression.support.g3 import hosted_seams
    return hosted_seams.install_from_env()


def _rss_bytes() -> object:
    from tests.cli_regression.support.g3 import hosted_rss
    return hosted_rss.install()


def install_all(env: dict | None = None, os_name: str | None = None, *, seams: Callable[[], object] = _seams, termios: Callable[[], object] = _termios, counters: Callable[[], object] = _counters,
                rss_bytes: Callable[[], object] = _rss_bytes, write: Callable[..., bool] = hosted_markers.write) -> list[str]:
    """The marker kinds written, in install order. The Linux reader child (`ACR_G3_HOSTED_RSS_BYTES=1`, posix) normalises `ru_maxrss` to bytes and writes `reader-rss-bytes` only if that install happened."""
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    done: list[str] = []
    if env.get("ACR_G3_HOSTED_TERMIOS") == "1" and os_name == "nt":
        termios()
        write("termios-shim")
        done.append("termios-shim")
    if env.get("ACR_G3_HOSTED_COUNTERS") == "1" and os_name == "nt":
        counters()
        write("counters-server")
        done.append("counters-server")
    if env.get("ACR_G3_HOSTED_RSS_BYTES") == "1" and os_name == "posix" and rss_bytes() is not None:
        write("reader-rss-bytes")
        done.append("reader-rss-bytes")
    if env.get("ACR_G3_HOSTED") == "1" and seams() is not None:
        write("python-seams")
        done.append("python-seams")
    return done
