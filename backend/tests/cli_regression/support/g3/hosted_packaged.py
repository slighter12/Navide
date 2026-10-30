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


MATRIX_ENTRY = "tests/cli/g3Matrix.test.ts"
WINDOWS_PACKAGED_ENTRY = "tests/cli/g3hPackagedWindows.test.ts"
READY_COMMAND_ENV = "ACR_G3H_READY_COMMAND"


def packaged_entry(system: str) -> str:
    """The vitest entry of the `packaged-smoke` and `packaged-cold` steps: the unchanged `g3Matrix.test.ts` everywhere except Windows, whose hosted variant differs only in the READY command."""
    return WINDOWS_PACKAGED_ENTRY if system == "Windows" else MATRIX_ENTRY


def windows_ready_command(python: str) -> str:
    """The pane command of the Windows packaged attempt, as the string a Windows agent pane receives (no shell; the backend splits it with `CommandLineToArgvW`): `"<python>" -c "<script>"`.
    The script prints one `BASELINE_READY {"packaged":true}` line and sleeps 120 s, the macOS line's `printf ...; exec sleep 120`. It holds no double quote (the JSON quotes are `chr(34)`) and splits the token
    (`'BASELINE'+'_READY '`), so neither a window title nor an echo of the command line can contain the token the waiter looks for. The interpreter is the plan's own absolute path, not a PATH lookup."""
    script = "import time;print('BASELINE'+'_READY '+chr(123)+chr(34)+'packaged'+chr(34)+':true'+chr(125),flush=True);time.sleep(120)"
    return f'"{python}" -c "{script}"'


def electron_argv(system: str, output: str) -> list[str]:
    """The packaged-electron step: unsigned, `--dir`, into the same output directory the record reads (x64 only; no arm64 job exists)."""
    return ["bunx", "electron-builder", *layout(system).builder, "--dir", "--publish", "never", f"-c.directories.output={output}"]
