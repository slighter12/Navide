"""The Linux unit of `resource.getrusage(...).ru_maxrss`, normalised to bytes inside the fresh reader measurement child (review round 2, finding A). Standard library only.

`reader_benchmark._peak_rss` returns `ru_maxrss` as is (bytes on macOS) and `one_run` subtracts the byte-valued `psutil` baseline from it. On Linux `ru_maxrss` is KiB, so the difference is usually negative, clamps to 0 and
`m3_peak_rss_increase_bytes` can never fail its margin. The pinned file stays unchanged; the child multiplies the KiB value by 1024 through `resource.getrusage`, which `_peak_rss` looks up at call time. The child is
`python -m tests.cli_regression.support.reader_benchmark run ...`, started with a rebuilt environment (`isolated_environment`, `PYTHONPATH` reset to `backend/`), so the parent's seams never reach it: only
`hosted_reader.fresh_process_runner` passes `ACR_G3_HOSTED_RSS_BYTES=1` and the hosted site to that child, and its `sitecustomize` calls `install()` (see `hosted_startup`). macOS never sets the flag.
"""
from __future__ import annotations

import sys
from typing import Callable

ENV = "ACR_G3_HOSTED_RSS_BYTES"
KIB = 1024


class BytesUsage:
    """A `getrusage` result whose `ru_maxrss` is in bytes; every other field is the real result's."""
    def __init__(self, real) -> None:
        self._real = real
        self.ru_maxrss = real.ru_maxrss * KIB

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def install(platform: str | None = None, resource_module=None) -> Callable[[], None] | None:
    """Replace `resource.getrusage` with the bytes-valued one on Linux. None = not Linux (nothing touched); otherwise the undo. A second call does not wrap twice."""
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return None
    if resource_module is None:
        import resource as resource_module
    original = resource_module.getrusage
    if getattr(original, "hostedBytes", False):
        return lambda: None

    def getrusage(who):
        return BytesUsage(original(who))
    getrusage.hostedBytes = True      # type: ignore[attr-defined]
    resource_module.getrusage = getrusage

    def undo() -> None:
        resource_module.getrusage = original
    return undo
