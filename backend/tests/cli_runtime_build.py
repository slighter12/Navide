"""The Rust CLI runtime, built from this checkout's source, for the tests that run it.

A binary that was built earlier proves nothing about the source beside it, so
no test runs one: ``binary()`` has cargo bring the release build up to date
first (the build ``cli_runtime.binary()`` finds in a checkout). With nothing
changed that takes well under a second.

Whether a run builds it is decided by where cargo is expected:

* ``NAVIDE_CLI_RUNTIME_REQUIRED=1`` (CI's ``cli-runtime`` job): it must build.
  No cargo, or a build that fails, fails the test.
* ``NAVIDE_CLI_RUNTIME_REQUIRED=0`` (CI's backend jobs, which compile no
  Rust): the tests skip. The ``cli-runtime`` job is where they run.
* Unset (a developer's machine): built when cargo is installed, and a build
  that fails fails the test; skipped only when there is no cargo at all.
* Windows: skipped. The runtime is not built or shipped there (it has no file
  identity or PTY off Unix, so it observes nothing).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CRATE = Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime"
REQUIRED_ENV = "NAVIDE_CLI_RUNTIME_REQUIRED"

_built: Path | None = None


def cargo(*args: str, timeout: float = 1500.0) -> "subprocess.CompletedProcess[str]":
    """``cargo <args>`` on the crate, or the skip / failure this run calls for."""
    expected = os.environ.get(REQUIRED_ENV)
    if sys.platform == "win32":
        pytest.skip("navide-cli-runtime is not built on Windows")
    if expected == "0":
        pytest.skip(f"this run does not build navide-cli-runtime ({REQUIRED_ENV}=0)")
    exe = shutil.which("cargo")
    if exe is None:
        if expected == "1":
            pytest.fail(f"cargo was not found, and {REQUIRED_ENV}=1")
        pytest.skip("cargo is not installed: navide-cli-runtime cannot be built from source")
    done = subprocess.run([exe, args[0], "--locked", "--manifest-path", str(CRATE / "Cargo.toml"), *args[1:]],
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if done.returncode != 0:
        pytest.fail(f"cargo {args[0]} failed for navide-cli-runtime:\n{done.stderr[-4000:]}")
    return done


def binary() -> Path:
    """The release build, up to date with the source (built once per process)."""
    global _built
    if _built is None:
        cargo("build", "--release")
        exe = CRATE / "target" / "release" / "navide-cli-runtime"
        assert exe.is_file(), f"cargo built no {exe}"
        _built = exe
    return _built
