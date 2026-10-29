"""The packaged plan of a hosted runner (design section 1 row 19): the `electron-builder --dir` layout of `linux-unpacked` and `win-unpacked` instead of the macOS `.app`.

`g3.packaged` keeps its layout in four module constants (`APP_REL`, `BIN_REL`, `BACKEND_NAME`, `RUNTIME_NAME`) that every function reads at call time. `install(system)` sets them for the running platform, so
`record`, `artifact_problems`, `backend_path` and the unchanged identity rules (rehash after the last attempt, a missing packaged record is a mismatch) work on the hosted layout without a byte of change.
The frozen backend and the staged sidecar sit in `<app>/resources/bin`, as `tests/cli/g2-package.mjs` lists them.
"""
from __future__ import annotations

from dataclasses import dataclass

from tests.cli_regression.support.g3 import packaged


@dataclass(frozen=True)
class Layout:
    unpacked: str
    backend: str
    runtime: str
    python_rel: str
    sidecar_rel: str
    builder: tuple[str, ...]


LAYOUTS = {
    "Linux": Layout("linux-unpacked", "agent_team_backend", "navide-cli-runtime", "backend/.venv/bin/python", "native/navide-cli-runtime/target/release/navide-cli-runtime", ("--linux", "--x64")),
    "Windows": Layout("win-unpacked", "agent_team_backend.exe", "navide-cli-runtime.exe", "backend/.venv/Scripts/python.exe", "native/navide-cli-runtime/target/release/navide-cli-runtime.exe",
                      ("--win", "--x64", "-c.win.signAndEditExecutable=false")),
}
BIN_REL = "resources/bin"


def layout(system: str) -> Layout:
    try:
        return LAYOUTS[system]
    except KeyError:
        raise RuntimeError(f"no hosted packaged layout for {system!r}: Linux and Windows only") from None


def install(system: str, module=packaged) -> Layout:
    """Point the `g3.packaged` constants at the hosted layout of `system`. Call once at process start, before any packaged function."""
    chosen = layout(system)
    module.APP_REL = f"{module.OUTPUT_REL}/{chosen.unpacked}"
    module.BIN_REL = BIN_REL
    module.BACKEND_NAME = chosen.backend
    module.RUNTIME_NAME = chosen.runtime
    return chosen


def electron_argv(system: str, output: str) -> list[str]:
    """The packaged-electron step: unsigned, `--dir`, into the same output directory the record reads (x64 only; no arm64 job exists)."""
    return ["bunx", "electron-builder", *layout(system).builder, "--dir", "--publish", "never", f"-c.directories.output={output}"]
