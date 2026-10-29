"""Interpreter-start hook of the hosted port. Without the flags this file does nothing and imports nothing.

* `ACR_G3_HOSTED=1`: the hosted seams are installed before any g3 module runs (see `hosted_seams`).
* `ACR_G3_HOSTED_TERMIOS=1` on Windows: only the `termios` shim is installed (stdlib + ctypes, no harness module), which is all a measured peer process loads from this port.
* `ACR_G3_HOSTED_COUNTERS=1` on Windows: only the counters pipe server (the isolated backend process).
* `ACR_G3_HOSTED_RSS_BYTES=1` on Linux: only the bytes-valued `ru_maxrss` (the fresh reader measurement child).
Each install that worked leaves an installed marker (`hosted_markers`, written by `hosted_startup`), so a hook that did not load is named in the rehearsal summary.
"""
import os

try:
    if any(os.environ.get(flag) == "1" for flag in ("ACR_G3_HOSTED", "ACR_G3_HOSTED_TERMIOS", "ACR_G3_HOSTED_COUNTERS", "ACR_G3_HOSTED_RSS_BYTES")):
        from tests.cli_regression.support.g3 import hosted_startup
        hosted_startup.install_all()
except BaseException as error:  # noqa: BLE001 - a half-installed port must end the process, not run as the macOS one
    import sys
    print(f"hosted seams could not be installed: {type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
    os._exit(97)
