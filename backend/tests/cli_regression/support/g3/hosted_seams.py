"""Where the hosted port meets the unchanged macOS code (design section 1, "rule for extending without editing g1e").

The macOS g1e/g3 modules read the platform parts through module attributes at call time (`f0.doc_records`, `toolchain.runner_in`, `toolchain.observe_python`, `probe.probe_ready_version`,
`assemble.host_snapshot`, `log_final.holds`, the `packaged` layout constants). `install(system)` replaces exactly those attributes with the hosted versions and returns an `undo` callable; no
file of g1e or g3 changes (their bytes stay pinned). The TS runners start `python -m ...g3.helper` themselves, so a process cannot be handed the seams by a caller: `hosted_site/sitecustomize.py`
calls `install_from_env()` at interpreter start when `ACR_G3_HOSTED=1` (the hosted driver sets that and `PYTHONPATH` for every step). A failure there ends the process with exit 97: a half-installed port
must never run as the macOS one.
"""
from __future__ import annotations

import os
import platform
import uuid
from typing import Callable

from tests.cli_regression.support import backend_process
from tests.cli_regression.support.g1e import assemble as g1e_assemble, log_final, toolchain as tc
from tests.cli_regression.support.g3 import driver, f0 as f0mod, hosted_f0, hosted_host, hosted_markers, hosted_packaged, hosted_pins, hosted_reader, hosted_toolchain, packaged, pins, probe

ENV_FLAG = "ACR_G3_HOSTED"
_PACKAGED_NAMES = ("APP_REL", "BIN_REL", "BACKEND_NAME", "RUNTIME_NAME")


def targets(system: str) -> list[tuple[object, str, object]]:
    """(module, attribute, replacement) for `system`. Linux replaces what its runner lacks (mise, the g1e doc paths, the macOS open-files list); Windows also the venv layout and the readiness probe."""
    out = [(f0mod, "doc_records", hosted_f0.manifest_doc_records), (tc, "runner_in", hosted_toolchain.runner_in), (g1e_assemble, "host_snapshot", hosted_host.host_snapshot),
           (log_final, "holds", hosted_host.holds_for(system)), (driver, "sidecar_path", lambda repo, _rel=hosted_packaged.layout(system).sidecar_rel: f"{repo}/{_rel}"),
           *hosted_reader.targets(system)]
    if system == "Windows":
        out += [(pins, "pinned_paths", hosted_pins.posix_keys(pins.pinned_paths)),      # the manifest keys are POSIX; `str(relative_to)` is not on Windows
                (tc, "observe_python", lambda root, runner: hosted_toolchain.observe_python(root, runner, windows=True)), (probe, "probe_ready_version", hosted_toolchain.probe_ready_version_stdio)]
    out += [(backend_process.BackendProcess, "__init__", wrap_backend_init(backend_process.BackendProcess.__init__, system)),      # both systems: the backend needs the reader counters v2 hook
            (backend_process.BackendProcess, "__aenter__", wrap_backend_enter(backend_process.BackendProcess.__aenter__))]      # and must prove it, per process
    return out


COUNTERS_FLAG = "ACR_G3_HOSTED_COUNTERS"
READER_V2_FLAG = "ACR_G3_HOSTED_READER_V2"
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hosted_site")
NODE_PRELOAD = os.path.join(SITE_DIR, "hosted-net-preload.cjs")


def backend_env_additions(env: dict, system: str = "Windows") -> None:
    """The isolated backend env (rebuilt by `isolated_environment`, PYTHONPATH reset to backend/) gets the hosted site first and the flags that install, in the backend process, the reader counters v2
    (both systems) and the pipe server (Windows only)."""
    env["PYTHONPATH"] = os.pathsep.join(part for part in (SITE_DIR, env.get("PYTHONPATH", "")) if part)
    env[READER_V2_FLAG] = "1"
    if system == "Windows":
        env[COUNTERS_FLAG] = "1"
    if os.environ.get(hosted_markers.ENV):      # the rebuilt backend environment would otherwise lose the marker directory
        env[hosted_markers.ENV] = os.environ[hosted_markers.ENV]


def wrap_backend_init(original: Callable, system: str = "Windows") -> Callable:
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        backend_env_additions(self.env, system)
    return init


def wrap_backend_enter(original: Callable) -> Callable:
    """Fail closed: a measured backend whose own interpreter did not write the `reader-counters-v2` marker (the hook was never found, so the gate would build the frozen v1 counters) stops the attempt here.
    The marker is matched by the backend's own pid (`python_process()`, the real interpreter behind a Windows venv launcher); a marker of another process does not count."""
    async def enter(self):
        nonce = uuid.uuid4().hex      # one token per start: the backend's marker must carry it, so a marker left under a reused pid by an earlier backend cannot vouch for this one
        self.env[hosted_markers.NONCE_ENV] = nonce
        entered = await original(self)
        pid = self.python_process().pid
        if not hosted_markers.written_by("reader-counters-v2", pid, nonce):
            await self.__aexit__(None, None, None)
            raise AssertionError(f"the backend (pid {pid}) did not install reader counters v2: refusing to measure with the frozen v1 counters")
        return entered
    return enter


def install(system: str) -> Callable[[], None]:
    if system not in hosted_packaged.LAYOUTS:
        raise RuntimeError(f"hosted seams exist for Linux and Windows only, not {system!r}")
    chosen = targets(system)
    saved = [(module, name, getattr(module, name)) for module, name, _ in chosen] + [(packaged, name, getattr(packaged, name)) for name in _PACKAGED_NAMES]
    hosted_packaged.install(system)
    for module, name, replacement in chosen:
        setattr(module, name, replacement)

    def undo() -> None:
        for module, name, original in saved:
            setattr(module, name, original)
    return undo


def install_from_env(env: dict | None = None, system: Callable[[], str] = platform.system) -> Callable[[], None] | None:
    env = os.environ if env is None else env
    return install(system()) if env.get(ENV_FLAG) == "1" else None


TERMIOS_FLAG = "ACR_G3_HOSTED_TERMIOS"


def node_options_require(path: str) -> str:
    """`--require "<path>"` as a NODE_OPTIONS fragment. Node tokenizes NODE_OPTIONS itself (`ParseNodeOptionsEnvVar`): inside double quotes a backslash escapes the next character and is dropped,
    so `D:\\a\\Navide` arrived as `D:aNavide` (rehearsal 4). Backslashes and quotes are escaped here so every character of the path arrives."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'--require "{escaped}"'


def hosted_environment(repo: str, base: dict | None = None, system: str | None = None) -> dict:
    """The environment every hosted step adds: the flag and a PYTHONPATH that holds `hosted_site` (for sitecustomize) and `backend/` (so `tests.cli_regression` imports at interpreter start).
    On Windows it also asks for the `termios` shim, which the frozen measured peer needs and which is all a peer process loads from this port."""
    site = os.path.join(repo, "backend", "tests", "cli_regression", "support", "g3", "hosted_site")
    existing = (base if base is not None else os.environ).get("PYTHONPATH", "")
    path = os.pathsep.join(part for part in (site, os.path.join(repo, "backend"), existing) if part)
    out = {ENV_FLAG: "1", "PYTHONPATH": path}
    if os.environ.get("RUNNER_TEMP"):      # installed markers of the children (hosted_markers); a runner has RUNNER_TEMP, a developer machine has none
        out[hosted_markers.ENV] = os.path.join(os.environ["RUNNER_TEMP"], "g3r2h", "markers")
    if system == "Windows":
        existing_node = (base if base is not None else os.environ).get("NODE_OPTIONS", "")
        out[TERMIOS_FLAG] = "1"
        out["NODE_OPTIONS"] = " ".join(part for part in (existing_node, node_options_require(os.path.join(site, "hosted-net-preload.cjs"))) if part)      # maps the counters socket path to its pipe
    return out


NODE_DEFER_FLAG = "ACR_G3_HOSTED_WS_DEFER"


def node_client_environment(repo: str, base: dict | None = None, system: str | None = None) -> dict:
    """What the steps that run a Node WebSocket client (the vitest steps) add on Linux: the preload that creates the real WebSocket only once its port accepts (`hosted-net-preload.cjs`), and the flag that arms it.
    Kept apart from `hosted_environment` because that one reaches every step, including Electron and electron-builder; the backend writes its port file before it listens, and Node 22's WebSocket never retries
    a handshake that failed before the TCP connection existed (rehearsal 5)."""
    if system != "Linux":
        return {}
    existing = (base if base is not None else os.environ).get("NODE_OPTIONS", "")
    preload = os.path.join(repo, "backend", "tests", "cli_regression", "support", "g3", "hosted_site", "hosted-net-preload.cjs")
    return {NODE_DEFER_FLAG: "1", "NODE_OPTIONS": " ".join(part for part in (existing, node_options_require(preload)) if part)}
