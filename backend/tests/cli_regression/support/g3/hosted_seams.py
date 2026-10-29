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
import subprocess
import time
import uuid
from pathlib import PureWindowsPath
from typing import Callable

import psutil

from tests.cli_regression.support import backend_process, trial_proof
from tests.cli_regression.support.g1e import assemble as g1e_assemble, baseline_identity as bi, driver as g1e_driver, log_final, summarise, toolchain as tc
from tests.cli_regression.support.g3 import bun_toolchain, driver, f0 as f0mod, hosted_f0, hosted_host, hosted_markers, hosted_packaged, hosted_pins, hosted_reader, hosted_toolchain, packaged, pins, probe

ENV_FLAG = "ACR_G3_HOSTED"
_PACKAGED_NAMES = ("APP_REL", "BIN_REL", "BACKEND_NAME", "RUNTIME_NAME")

#: rw2 (2026-11-10): `eval/agent-cli-runtime` was rewritten once to take a home-directory path out of one test-support file (map `OUT/rewrite-map-48734e29-to-202084cf.txt`, 228 of 231 commits got a new SHA).
#: The frozen G1-E F0 records the E3 commit and its parent by their old names and `baseline_identity.py` (pinned bytes) holds those names, but a runner clones the rewritten branch, where the old
#: objects do not exist. This seam keeps the names (records, `summarise.freeze_problems` and the F0 rebuild compare them) and asks git for the rewritten images, whose Python-arm files are
#: byte-identical to the old commits' (the rewrite changed only `native_file_guard.py`). `U` (upstream's v0.2.38) was not rewritten.
REWRITE_IMAGES = {bi.E3_COMMIT: "bf2e74100575e2fbb5b60597011ee7f6240a694b", bi.E3_PARENT: "fcc0fdc85b65b9ef32a34ffde03b2596f1f430ae"}
_RESOLVE_REVISION, _CONTENT_AT = bi.resolve_revision, bi.content_at


def resolve_revision_rewritten(root, revision: str) -> str:
    """A registered E3 name must resolve through its image and is returned as the registered name; any other revision resolves as before."""
    if revision in REWRITE_IMAGES:
        _RESOLVE_REVISION(root, REWRITE_IMAGES[revision])
        return revision
    return _RESOLVE_REVISION(root, revision)


def content_at_rewritten(root, revision: str, path: str):
    return _CONTENT_AT(root, REWRITE_IMAGES.get(revision, revision), path)


SIDECAR_STEM = "navide-cli-runtime"


def windows_classify_component(original: Callable) -> Callable:
    """rh13 (A-B1): `runtime_baseline.classify_component` (pinned bytes; `g1e/driver.py:34,158` binds it by name and calls it at sample time) names a process `sidecar` only when `Path(executable).name ==
    "navide-cli-runtime"`. On Windows the sidecar's file is `navide-cli-runtime.exe`, so it was a `helper`, `summarise.coverage_problems` reported "the sidecar is missing from rows of the eligible
    input_output phase" and every Rust shell trial failed its structure check. A Windows executable whose stem is the sidecar's name is the sidecar; everything else (the backend first) is the original's answer.
    The only other name rules (`runtime_baseline.main`, `shell_baseline.main`) belong to the ticket-06/07 baseline tests, which no hosted step runs."""
    def classify(pid: int, backend_pid: int, executable: str) -> str:
        if pid != backend_pid and PureWindowsPath(executable).stem == SIDECAR_STEM:
            return "sidecar"
        return original(pid, backend_pid, executable)
    return classify


def windows_trial_proof(original: Callable) -> Callable:
    """rh14 (R13-A1): the pinned driver's `ck_proof` (`g1e/driver.py:81`) calls `trial_proof.prove` without `windows=`, so the Windows sidecar's `handoff: "duplicate_handle"` failed item 1 ("wire/protocol
    identity", `trial_proof.py:44`) in the first Rust-arm attempt. `prove` already has the Windows form (the G2 shell driver passes `windows=True`, `shell_runtime_driver.py:108`): the handoff is the client-validated
    duplicate-handle receipt of each session, and a per-create kernel checkpoint on Windows is itself a failure. This default applies it to the calls that name no platform; an explicit `windows=` is left as it is.

    The driver also passes `driver_sessions` (the sessions the runner created), which `prove` accounts for by checkpoint receipts only (`trial_proof.py:56-61,77-80`): on Windows every create would fail "neither a CK nor
    a failed-create receipt" and no checkpoint could name a session. In Windows form that accounting is done here by the receipt Windows has, the validated duplicate-handle handoff, with the two messages' meaning
    kept (a create with no receipt; a receipt for a terminal the driver did not create); `prove` itself runs unchanged with `driver_sessions=None` and every other item as before.

    rh15 (R14-A2): `prove` counts the validated sessions as a set (`trial_proof.py:67,116`), so two validated handoffs for one create would pass. The POSIX original refuses the matching case, two CK receipts for
    one session (`trial_proof.py:57-59`). The Windows form refuses it too: more than one validated duplicate-handle `handoff_received` for a session fails `item 2: {n} validated duplicate-handle handoffs for
    one session`, with or without `driver_sessions` (the POSIX rule does not depend on it either). The product contract is one handoff per create (`test_cli_runtime_handoff.py:347`)."""
    def prove(records, **kwargs):
        kwargs.setdefault("windows", True)
        if not kwargs["windows"]:
            return original(records, **kwargs)
        driver_sessions = kwargs.get("driver_sessions")
        verdict = original(records, **{**kwargs, "driver_sessions": None})
        handoffs = [r["session_id"] for r in records if r.get("event") == "handoff_received" and r.get("validated") is True and r.get("kind") == "duplicate_handle"]
        handed = set(handoffs)
        added = [f"item 2: {handoffs.count(sid)} validated duplicate-handle handoffs for one session" for sid in sorted(handed) if handoffs.count(sid) > 1]
        if driver_sessions is not None:
            failed = {r["session_id"] for r in records if r.get("event") == "create_failed" and r.get("session_id")}
            added += [f"item 2: a validated duplicate-handle handoff for a terminal the driver did not create ({sid})" for sid in sorted(handed - set(driver_sessions))]
            added += [f"item 2: a driver create with neither a validated duplicate-handle handoff nor a failed-create receipt ({sid})" for sid in sorted(set(driver_sessions) - handed - failed)]
        if not added:
            return verdict
        failures = [*verdict["failures"], *added]
        return {**verdict, "ok": False, "failures": failures}
    return prove


def windows_ck_proof_problems(original: Callable) -> Callable:
    """rh14 (R13-A1): `summarise.ck_proof_problems` (pinned bytes) accounts for the created terminals as `ck + failed_creates == len(createdTerminals)`: one kernel checkpoint or one failed create each. Windows has no
    checkpoint receipt (`prove(windows=True)` fails one) and proves the handoff by the validated duplicate-handle receipt of every session, which `prove` counts as `items["2"]["handoffs"]`. On Windows, for a Rust-arm
    record that carries `handoffs`, the same accounting is applied with `handoffs` in the place of `ck`, by the unchanged original on a copy of the record; a record without `handoffs` (the proof was not the Windows
    one) is judged as before and still fails. Every other check of the original (the proof's own `ok`, `failures`, `applicable`, the Python arm's known-empty record) is untouched."""
    def problems(trial: dict, mode: str) -> list[str]:
        proof = trial.get("ckProof") if isinstance(trial, dict) else None
        items = proof.get("items") if isinstance(proof, dict) else None
        two = items.get("2") if isinstance(items, dict) else None
        handoffs = two.get("handoffs") if isinstance(two, dict) else None
        if mode == "rust-shell" and isinstance(handoffs, int) and not isinstance(handoffs, bool):
            trial = {**trial, "ckProof": {**proof, "items": {**items, "2": {**two, "ck": handoffs}}}}
        return original(trial, mode)
    return problems


def bun_targets() -> list[tuple[object, str, object]]:
    """D3: the G3 Bun toolchain adapter over the pinned G1-E toolchain functions G3 reaches, on every platform. `{bun, python, sidecar}` replaces `{node, pnpm, python, sidecar}` in the observation, the
    comparison, the per-attempt check, F0's freeze record and `summarise`'s two key-set checks; the G1-E files keep their bytes."""
    return [(tc, "observe", bun_toolchain.observe), (tc, "compare", bun_toolchain.compare), (tc, "check", bun_toolchain.check), (tc, "record_for_freeze", bun_toolchain.record_for_freeze),
            (summarise, "toolchain_record_problems", bun_toolchain.toolchain_record_problems), (summarise, "check_record_problems", bun_toolchain.check_record_problems)]


def targets(system: str) -> list[tuple[object, str, object]]:
    """(module, attribute, replacement) for `system`. Every platform: the documents manifest, the Bun toolchain adapter, the reader counters v2 wrappers. Linux replaces what its runner lacks (the macOS open-files
    list, the reader fixtures to claude); Windows also the venv layout and the readiness probe. Darwin (rf4c D13) is the platform the g1e/g3 code was written for: its termios, loadavg, RSS sampler, `lf.holds`
    and reader registry are the native ones, so it takes only the common seams."""
    out = [(f0mod, "doc_records", hosted_f0.manifest_doc_records), (tc, "runner_in", hosted_toolchain.runner_in), (g1e_assemble, "host_snapshot", hosted_host.host_snapshot),
           *bun_targets(), (bi, "resolve_revision", resolve_revision_rewritten), (bi, "content_at", content_at_rewritten),
           (log_final, "holds",hosted_host.holds_for(system)), (driver, "sidecar_path", lambda repo, _rel=hosted_packaged.layout(system).sidecar_rel: f"{repo}/{_rel}"),
           *hosted_reader.targets(system)]
    if system == "Windows":
        out += [(pins, "pinned_paths", hosted_pins.posix_keys(pins.pinned_paths)),      # the manifest keys are POSIX; `str(relative_to)` is not on Windows
                (tc, "observe_python", lambda root, runner: hosted_toolchain.observe_python(root, runner, windows=True)), (probe, "probe_ready_version", hosted_toolchain.probe_ready_version_stdio),
                (g1e_driver, "classify_component", windows_classify_component(g1e_driver.classify_component)),      # rh13 A-B1: the sampler names the sidecar by its file name, which ends in `.exe` on Windows
                (trial_proof, "prove", windows_trial_proof(trial_proof.prove)),      # rh14 R13-A1: the driver's CK proof is the Windows one (duplicate-handle handoff, no CK receipt)
                (summarise, "ck_proof_problems", windows_ck_proof_problems(summarise.ck_proof_problems))]      # rh14 R13-A1: the decider-side accounting counts the handoffs where POSIX counts the checkpoints
    out += [(backend_process.BackendProcess, "__init__", wrap_backend_init(backend_process.BackendProcess.__init__, system)),      # both systems: the backend needs the reader counters v2 hook
            (backend_process.BackendProcess, "__aenter__", wrap_backend_enter(backend_process.BackendProcess.__aenter__)),      # and must prove it, per process
            (backend_process.BackendProcess, "python_process", wrap_python_process(backend_process.BackendProcess.python_process))]
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
        env[TERMIOS_FLAG] = "1"      # panes inherit the backend env (terminals.py) and the measured peer (`g1e_peer.py`) imports `termios`, which Windows lacks; the backend's own marker never satisfies the peer check (HOOK_ROLES)
    if os.environ.get(hosted_markers.ENV):      # the rebuilt backend environment would otherwise lose the marker directory
        env[hosted_markers.ENV] = os.environ[hosted_markers.ENV]


def wrap_backend_init(original: Callable, system: str = "Windows") -> Callable:
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        backend_env_additions(self.env, system)
    return init


class BackendIdentityError(AssertionError):
    """The measured backend's identity could not be established (rh13, W-R1): its interpreter did not write this start's `reader-counters-v2` marker. The message names the pid the attempt started, the pid
    `python_process()` chose and how many children the started process had, so a launcher topology the seam does not model (more than one child) is not mistaken for a missing hook."""


class OwnerProcess:
    """The backend's `Popen` as the measured code reads it when a launcher stands in front of the interpreter (rehearsal 3, W-R1). A Windows venv's `python.exe` is a launcher whose only child is the real
    interpreter, and the runtime receipts name the interpreter as `owner_pid`. The pinned G1-E driver (`g1e/driver.py:127-128,200`) and the generated shell attempt (`g3ShellAttempt.ts:206,404`) read the
    backend's pid from `backend.process.pid` and match the `ready` receipt on it, so on Windows no `ready` receipt matched. `pid` is the interpreter's: the pid the receipts and the process tree already call the
    backend on a POSIX host, where there is no launcher.

    Two processes are one backend here, and no state may leave either untracked (rh13: a launcher that dies while its interpreter lives must not hide the interpreter). Both are held, the interpreter by a
    birth-checked `psutil` handle. The backend is running while the launcher OR the interpreter is; an exit code exists only once both have ended and is the launcher's (the `Popen`'s own code, what the unwrapped
    backend reported); `wait` waits for both; `kill`, `terminate` and `send_signal` act on the interpreter and then on the launcher. Everything else (the pipes, `args`, ...) is the launcher's `Popen`."""

    def __init__(self, launcher, interpreter: psutil.Process):
        self._launcher = launcher
        self.interpreter = interpreter
        self.pid = interpreter.pid
        self.launcher_pid = launcher.pid

    def _interpreter_alive(self) -> bool:
        try:
            return self.interpreter.is_running() and self.interpreter.status() != psutil.STATUS_ZOMBIE      # a reaped or reused pid is gone; a zombie has ended
        except psutil.NoSuchProcess:
            return False

    def poll(self):
        code = self._launcher.poll()
        return None if code is None or self._interpreter_alive() else code

    @property
    def returncode(self):
        code = self._launcher.returncode
        return None if code is None or self._interpreter_alive() else code

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        code = self._launcher.wait(timeout=timeout)
        while self._interpreter_alive():
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self._launcher.args, timeout)
            time.sleep(0.02)
        return code

    def _on_both(self, on_interpreter: Callable, on_launcher: Callable) -> None:
        try:
            on_interpreter(self.interpreter)
        except psutil.NoSuchProcess:
            pass
        on_launcher()

    def kill(self) -> None:
        self._on_both(lambda process: process.kill(), self._launcher.kill)

    def terminate(self) -> None:
        self._on_both(lambda process: process.terminate(), self._launcher.terminate)

    def send_signal(self, sig) -> None:
        self._on_both(lambda process: process.send_signal(sig), lambda: self._launcher.send_signal(sig))

    def __getattr__(self, name):      # only what this object does not set itself
        return getattr(self._launcher, name)


def wrap_python_process(original: Callable) -> Callable:
    """`BackendProcess.python_process` once `process` is an `OwnerProcess`: the interpreter it holds. The original would look at that interpreter's children and could take a lone python child
    (a pane's peer, which is the base interpreter too) for the interpreter."""
    def python_process(self):
        if isinstance(self.process, OwnerProcess):
            return self.process.interpreter
        return original(self)
    return python_process


def child_count(process) -> int | None:
    try:
        return len(psutil.Process(process.pid).children())
    except psutil.Error:
        return None


def wrap_backend_enter(original: Callable) -> Callable:
    """Fail closed: a measured backend whose own interpreter did not write the `reader-counters-v2` marker (the hook was never found, so the gate would build the frozen v1 counters) stops the attempt here.
    The marker is matched by the backend's own pid (`python_process()`, the real interpreter behind a Windows venv launcher); a marker of another process does not count. When that interpreter is not the
    process the attempt started (a launcher stands in front, W-R1), `process` becomes an `OwnerProcess` so that the pid the unchanged callers read is the receipts' `owner_pid`, and the interpreter is
    tracked as the backend's own child from the start, as it was when the launcher was the root."""
    async def enter(self):
        nonce = uuid.uuid4().hex      # one token per start: the backend's marker must carry it, so a marker left under a reused pid by an earlier backend cannot vouch for this one
        self.env[hosted_markers.NONCE_ENV] = nonce
        entered = await original(self)
        owner = self.python_process()
        if not hosted_markers.written_by("reader-counters-v2", owner.pid, nonce):
            problem = BackendIdentityError(f"the backend did not install reader counters v2: refusing to measure with the frozen v1 counters (started pid {self.process.pid}, "
                                           f"python_process() pid {owner.pid}, children of the started process {child_count(self.process)})")
            try:
                await self.__aexit__(None, None, None)
            except Exception as cleanup:      # the identity failure is the finding; the cleanup's own complaint is chained, never in its place
                raise problem from cleanup
            raise problem
        if owner.pid != self.process.pid:
            self.track_child(self.process.pid)      # rh14 (R13-A3): the launcher too, so the survivor reaper covers a launcher that outlives its killed interpreter (`__aexit__` kills the interpreter's tree, `self.process.pid` being the interpreter's)
            self.process = OwnerProcess(self.process, owner)
            self.track_child(owner.pid)
        return entered
    return enter


def install(system: str) -> Callable[[], None]:
    if system not in hosted_packaged.LAYOUTS:
        raise RuntimeError(f"hosted seams exist for Linux, Windows and Darwin only, not {system!r}")
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


BUN_OPTIONS_ENV = "BUN_OPTIONS"


def bun_options_preload(path: str) -> str:
    """`--preload <path>` as a BUN_OPTIONS fragment (rf4c D4). The TS attempt clients run on plain Bun, which does NOT read `NODE_OPTIONS=--require` (`test_g3h_bun_preload.py`), but does read
    `BUN_OPTIONS`, in the process and in the `fork` children vitest runs its tests in. Bun's parser has no quoting: a quoted path is silently dropped (exit 0, nothing preloaded) and an unescaped space splits
    the path. A space is therefore escaped with a backslash, and the path's own backslashes become forward slashes first (a Windows path such as `D:\\a\\Navide` keeps every character and Bun accepts
    `D:/a/Navide`; rehearsal 4 lost the backslashes of a NODE_OPTIONS path)."""
    return "--preload " + path.replace("\\", "/").replace(" ", "\\ ")


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
        existing = (base if base is not None else os.environ).get(BUN_OPTIONS_ENV, "")
        out[TERMIOS_FLAG] = "1"
        out[BUN_OPTIONS_ENV] = " ".join(part for part in (existing, bun_options_preload(os.path.join(site, "hosted-net-preload.cjs"))) if part)      # maps the counters socket path to its pipe
    return out


NODE_DEFER_FLAG = "ACR_G3_HOSTED_WS_DEFER"


def node_client_environment(repo: str, base: dict | None = None, system: str | None = None) -> dict:
    """What the steps that run a TS WebSocket client (the vitest steps, on Bun) add: the preload that creates the real WebSocket only once its port accepts (`hosted-net-preload.cjs`), and the flag that arms it.
    Kept apart from `hosted_environment` because that one reaches every step, including Electron and electron-builder; the backend writes its port file before it listens, and a WebSocket handshake that failed
    before the TCP connection existed was never retried (Node 22's undici, rehearsal 5), so the defer is carried to every platform whose port file can precede its listener. The preload reaches Bun through
    `BUN_OPTIONS` (D4: Bun ignores `NODE_OPTIONS --require`). Windows already loads it in `hosted_environment` for the counters pipe, so it needs only the flag."""
    if system == "Windows":
        return {NODE_DEFER_FLAG: "1"}
    if system not in ("Linux", "Darwin"):
        return {}
    existing = (base if base is not None else os.environ).get(BUN_OPTIONS_ENV, "")
    preload = os.path.join(repo, "backend", "tests", "cli_regression", "support", "g3", "hosted_site", "hosted-net-preload.cjs")
    return {NODE_DEFER_FLAG: "1", BUN_OPTIONS_ENV: " ".join(part for part in (existing, bun_options_preload(preload)) if part)}
