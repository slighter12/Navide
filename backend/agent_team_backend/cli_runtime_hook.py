"""The one seam between the Rust runtime bridge and the isolated evaluation harness.

The bridge holds a `HarnessHook` and calls it where the harness needs to see or guard something: a receipt, a frame
counted, the sidecar's start arguments, the launch environment, a kernel checkpoint of a new session's descriptor, the
digest of the sidecar binary, and the log watcher's rescan interval. The hook in this module is the product's and does
nothing; every method answers the value the product would have used anyway.

The product never installs another one. `from_environment` returns the harness's implementation
(`cli_runtime_receipts.RegressionHook`) only when the process runs under the isolated regression root, which only the
harness sets. The harness reaches its guards by the names it always used: the isolation module assigns
`cli_runtime.prepare_native_launch`, a driver sets `NAVIDE_ACR_FAULT`, `NAVIDE_LOG_RESCAN_S` or `NAVIDE_ACR_EVAL_RUNTIME`,
and a counter gate reads `control_ledger()` from the service.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from .log_readers.watcher import DEFAULT_RESCAN_S

#: The evaluation selector: `rust-shell` runs the Rust runtime, anything else (or nothing) the in-process service.
SELECTOR_ENV = "NAVIDE_ACR_EVAL_RUNTIME"
#: The isolated regression root. Only the harness sets it; receipts are written under it.
ISOLATION_ENV = "NAVIDE_REGRESSION_ROOT"
RUST_SELECTION = "rust-shell"
PYTHON_SELECTION = "python"


class Ledger(Protocol):
    """The control-plane ledger: what the bridge reports about its frames to the hook, in one place. The product's
    ledger and the harness's both satisfy it, so a member added to one cannot be silently dropped by the other."""

    def sent(self, frame_code: int, size: int) -> None: ...

    def received(self, frame: Any) -> None: ...

    def late_part(self, size: int) -> None: ...

    def late_answer(self) -> None: ...

    def snapshot(self) -> dict: ...


class NullLedger:
    """The control-plane ledger of the product: it counts nothing and has no snapshot."""

    def sent(self, frame_code: int, size: int) -> None:
        pass

    def received(self, frame: Any) -> None:
        pass

    def late_part(self, size: int) -> None:
        pass

    def late_answer(self) -> None:
        pass

    def snapshot(self) -> dict:
        return {}


class HarnessHook:
    """What the product runs with."""

    def __init__(self) -> None:
        self.ledger: Ledger = NullLedger()

    def receipt(self, build: Callable[[], dict]) -> None:
        """One structured record of the runtime's lifecycle for the harness's receipt file. The caller hands over how
        to build the record, not the record, so a hook that keeps none builds none."""

    def create_checkpoint(self, fd: int, session_id: str, pane_id: str, generation: str, sidecar_tty: str,
                          root: dict) -> dict | None:
        """The receipt record of the kernel facts of a new session's handed-over descriptor, taken before the create's
        answer is released; None when the harness takes none."""
        return None

    def launch_env(self, argv: list[str], *, cwd: str, env: dict[str, str]) -> dict[str, str]:
        """The environment a session's program is launched with, after the harness's guard has seen it."""
        return env

    def runtime_argv(self) -> list[str]:
        """Arguments the harness adds to the sidecar's command line."""
        return []

    def executable_digest(self, path: Path) -> str | None:
        """The SHA-256 of the sidecar binary the harness started, for its receipt of the handshake."""
        return None

    def rescan_interval_s(self) -> float:
        """The log watcher's safety-net sweep interval."""
        return DEFAULT_RESCAN_S


def from_environment(environ: Mapping[str, str] | None = None) -> HarnessHook:
    """The harness's hook under the isolated regression root, the product's everywhere else."""
    environ = os.environ if environ is None else environ
    root = environ.get(ISOLATION_ENV)
    if not root:
        return HarnessHook()
    from .cli_runtime_receipts import RegressionHook

    return RegressionHook(Path(root))


def runtime_selection(environ: Mapping[str, str] | None = None) -> str:
    """The runtime the evaluation selector names; `python` when it names none."""
    environ = os.environ if environ is None else environ
    return environ.get(SELECTOR_ENV, PYTHON_SELECTION)


def require_isolated_harness(selection: str, environ: Mapping[str, str] | None = None) -> None:
    """Refuse any selection but the in-process service unless it is the Rust runtime under the isolated harness."""
    environ = os.environ if environ is None else environ
    if selection != PYTHON_SELECTION and (selection != RUST_SELECTION or not environ.get(ISOLATION_ENV)):
        raise RuntimeError("Rust evaluation requires the isolated regression harness")
