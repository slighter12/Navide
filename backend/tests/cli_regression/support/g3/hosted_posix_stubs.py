"""POSIX-only names that unchanged g1e code reads at IMPORT time, made present on Windows (stdlib only, so it can run before any harness module is imported).

`g1e/quiet_gate.py` binds `os.getloadavg` as a default argument (`run_gate(..., getloadavg=os.getloadavg)`), which raises `AttributeError` when the module is imported on Windows. The Windows quiet gate
never calls it (it samples `cpu_percent`; `hosted_quiet_gate` injects its own sensors), so the stub only has to exist and, if something does call it, fail the way an unsupported platform does: `OSError`.
Installed by `hosted_quiet_gate` itself (that step sets no hosted flag) and by `hosted_startup` for every other hosted process. A name the interpreter already has is left alone.
"""
from __future__ import annotations

import os
import types


def getloadavg_unavailable() -> tuple[float, float, float]:
    raise OSError("os.getloadavg is not available on this platform")


STUBS = {"getloadavg": getloadavg_unavailable}


def install(os_module: types.ModuleType = os) -> list[str]:
    """The names added to `os_module` (empty on a platform that has them all)."""
    added = []
    for name, stub in STUBS.items():
        if not hasattr(os_module, name):
            setattr(os_module, name, stub)
            added.append(name)
    return added
