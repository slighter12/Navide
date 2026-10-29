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

from tests.cli_regression.support import backend_process, runtime_baseline, trial_proof
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
    The only other name rules (`runtime_baseline.main`, `shell_baseline.main`) belong to the ticket-06/07 baseline tests, which no hosted step runs.

    rh19 (diag2, D2-4): the attempt starts the driver as `python -m ...g1e.driver` (`g3ShellAttempt.ts:113`), so its code runs as `__main__`, a second copy of the module that takes the function with
    `from ...runtime_baseline import classify_component` (`g1e/driver.py:34`). A seam at `g1e.driver.classify_component` alone changes the imported copy, which the running driver never reads: the first
    Rust-arm attempt on Windows sampled no `sidecar` at all (`sidecarPids: []`). The wrapper is therefore installed at `runtime_baseline.classify_component` too, the name `__main__` imports from, before
    `__main__` runs (`hosted_site/sitecustomize.py`); the seam on the imported driver stays for a caller that imports it."""
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


READ_TOTAL_MESSAGE = "delta read_total != peer accepted since origin (including the FINAL line)"
LEDGER_BELOW_MESSAGE = "delta read_total < peer accepted since origin plus one byte per line the peer wrote: the reader read fewer bytes than a console emits for the peer's lines (Windows form)"
LEDGER_ABOVE_MESSAGE = "delta read_total > peer accepted since origin plus one byte per line the peer wrote plus the declared slack: the reader read bytes the peer did not write (Windows form)"
#: rh18 (SF-2), declared: how many bytes beyond `accepted + lines` the reader may count on Windows. The four real sessions of the diagnostic run needed none; the slack is there for a console control
#: sequence that those 1 MB streams did not show. It is far below one repaint frame (42 KB and more) and below any duplicate of 4 KB or more.
LEDGER_SLACK_BYTES = 4096
#: The lines one measured peer writes between its origin and the end of its FINAL line, besides its io ACK lines and its burst lines (`g1e_peer.py`, `baseline_peer.py`; the attempt sends
#: `observe-initial`, `observe` and `after-reconnect` once per peer, then `g1e-final`). Named one by one, so that the count is the length of what is named.
WINDOW_FRAME_LINES = ("BASELINE_WIN:<wid>:BEGIN", "BASELINE_WIN:<wid>:END")
BURST_ROUND_OTHER_LINES = (*WINDOW_FRAME_LINES, "BASELINE_ACK during-burst-r<kk>", "BASELINE_DONE:r<kk>")
LINES_AFTER_THE_BURSTS = ("BASELINE_STORE (observe-initial)", "BASELINE_OBSERVED_WRITE (observe-initial)", "BASELINE_INITIAL_WRITE", "BASELINE_STORE (observe)", "BASELINE_OBSERVED_WRITE (observe)",
                          "BASELINE_ACK after-reconnect", "BASELINE_FINAL")


def registered_lines(cell: int, work: dict | None = None) -> int:
    """How many lines one measured peer of `cell` writes from its origin through its FINAL line under the registered workload: the io window (its ACKs and its two frame lines), every burst round (its
    lines and the four others), and the lines after the bursts. 2,829 in cell 4 and 6,789 in cell 1."""
    work = summarise.REGISTERED_WORKLOAD if work is None else work
    return work["ioRounds"] + len(WINDOW_FRAME_LINES) + summarise.burst_rounds(work, cell) * (work["burstLines"] + len(BURST_ROUND_OTHER_LINES)) + len(LINES_AFTER_THE_BURSTS)


def ledger_bound_problem(delta: int, accepted: int, lines: int, slack: int = LEDGER_SLACK_BYTES) -> str | None:
    """The Windows form of the read-total rule for a session in which the peer wrote `lines` lines: `accepted + lines <= delta <= accepted + lines + slack`. None when it holds."""
    if delta < accepted + lines:
        return LEDGER_BELOW_MESSAGE
    return LEDGER_ABOVE_MESSAGE if delta > accepted + lines + slack else None


def _reader_counts(rec) -> tuple[int, int] | None:
    """(the reader's `read_total` delta, what the peer accepted since the origin) of a ledger record; None when the record does not carry the three counts as integers (the original then reports the
    record's shape and nothing is added)."""
    try:
        values = (rec["close"]["read_total"], rec["origin"]["read_total"], rec["peer"]["acceptedSinceOrigin"])
    except (TypeError, KeyError):
        return None
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    return values[0] - values[1], values[2]


def windows_ledger_problems(original: Callable) -> Callable:
    """rh17 (B-1): `summarise.ledger_problems` (pinned bytes; `trial_problems` reads the name at call time) requires the reader's `read_total` delta to EQUAL what the peer accepted since the origin. A Windows
    console is not a byte pipe: it ends every line with CR LF, so the reader, which counts what the console emits, reads more than the peer wrote, in both arms (`OUT/diag1/results.md`: +134 bytes of
    265,564 on the backend's console, exactly its CR bytes).
    rh18 (SF-2, the rh17 review): rh17 turned the equality into a lower bound, which a reader that counts every byte twice passed. The data supports an exact rule: with no repaint before the FINAL line
    (the attempt asks for no size change before it on Windows) the delta was `accepted + lines` in 4 of 4 real sessions, on both console hosts. On Windows the one equality is therefore
    `accepted + L <= delta <= accepted + L + LEDGER_SLACK_BYTES`, with `L` the registered line count of the cell (`registered_lines`). A delta outside it fails under its own message, on either side.
    Every other rule of the original is untouched and still decides: read == emitted + dropped, no drop, the pre-origin bound, the FINAL count and digest (the peer's own count and SHA-256 of every
    byte, compared with the stream), every window digest, the log identity and the log file."""
    def problems(rec, peer_index, cell):
        out = original(rec, peer_index, cell)
        counts = _reader_counts(rec)
        if counts is None:
            return out
        bound = ledger_bound_problem(*counts, registered_lines(cell))
        return [message for message in out if message != READ_TOTAL_MESSAGE] + ([bound] if bound else [])
    return problems


def windows_log_identity(original: Callable) -> Callable:
    """rh17 (B-2): `log_final.log_identity` (pinned bytes; the g1e helper calls it by module attribute) compares the WHOLE log after the start marker with the peer's text, which ends with the FINAL line. On a
    POSIX pty nothing follows the FINAL line. On Windows the inbox console the sidecar uses paints its whole screen again after every size change (`OUT/diag1/results.md`: 7 of 7 size changes, 54 to 56 KB each),
    the attempt's 20 resize probes come after the FINAL line, and the product's log mirror keeps the painted rows as text (it removes control sequences, not text). So on Windows the log is read up to the end
    of its first FINAL line after the marker, for both arms alike; everything the original checks (the marker once, the FINAL line present, the text and every window against the peer's) is then checked on
    that text, unchanged. Before the FINAL line the attempt asks for no size change on Windows (`attempt_copy.CONPTY_SUBSTITUTIONS`), so nothing is painted twice there."""
    def log_identity(log_text: str, peer_text: str, nonce: str, window_ids: list[str]) -> dict:
        marker = log_text.find(f"BASELINE_LEDGER_START:{nonce}")
        final = log_final._FINAL.search(log_text, marker) if marker >= 0 else None
        return original(log_text if final is None else log_text[:final.end()], peer_text, nonce, window_ids)
    return log_identity


#: rh19 (D2-5): how wide the two identity numbers of a file are once the runner has read them (`statSync` in the attempt, through `attempt_copy.IDENTITY_SUBSTITUTION`): the volume serial number as
#: 32 bits, the file index as 64.
WINDOWS_DEV_BITS, WINDOWS_INO_BITS = 32, 64


def windows_read_once(original: Callable) -> Callable:
    """rh19 (diag2, D2-5; predicted from the two tools' own definitions, not observed: the first Windows attempt stopped one call earlier). `summarise.ledger_problems` (pinned) requires the log file's
    `[dev, ino]` at the close to equal `inodeAtCreate`. The close pair is Python's (`log_final.read_once`, `os.stat`), the create pair is the runner's (`statSync` in the attempt). On POSIX the two tools
    report the same numbers. On Windows Python 3.12 reports `st_dev` as the volume's 64-bit serial number and `st_ino` as a file identifier of up to 128 bits, while the runner's `stat` reports the
    32-bit serial number (its low half) and a 64-bit file index, so one file had two identities and every Windows trial would fail "log file irregular, replaced, rotated (sibling), or over the limit".
    Both sides are therefore reduced to the same form: here `dev` to its low 32 bits and `ino` to its low 64, in the attempt `dev` to its low 32 bits. A pair that already was in that form is unchanged,
    so the seam is right whichever width a tool reports. Both reads of one check go through here, so the pinned "the reads agree" rule (`reference.log_final_check`) compares like with like, and the
    open-files check (`log_final.identity`, `holds`) keeps Python's own numbers on both of its sides. What the reduction cannot tell apart: two volumes whose serial numbers share their low 32 bits."""
    def read_once(path):
        record = original(path)
        return {**record, "dev": record["dev"] & ((1 << WINDOWS_DEV_BITS) - 1), "ino": record["ino"] & ((1 << WINDOWS_INO_BITS) - 1)}
    return read_once


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
                (runtime_baseline, "classify_component", windows_classify_component(runtime_baseline.classify_component)),      # rh19 D2-4: the name the driver imports from when it runs as `__main__`
                (log_final, "read_once", windows_read_once(log_final.read_once)),      # rh19 D2-5: the log file's device number in the form the runner's `stat` reports it
                (trial_proof, "prove", windows_trial_proof(trial_proof.prove)),      # rh14 R13-A1: the driver's CK proof is the Windows one (duplicate-handle handoff, no CK receipt)
                (summarise, "ck_proof_problems", windows_ck_proof_problems(summarise.ck_proof_problems)),      # rh14 R13-A1: the decider-side accounting counts the handoffs where POSIX counts the checkpoints
                (summarise, "ledger_problems", windows_ledger_problems(summarise.ledger_problems)),      # rh17 B-1, rh18 SF-2: a console emits one more byte per line than the peer wrote; on Windows the equality is `accepted + L <= delta <= accepted + L + slack`
                (log_final, "log_identity", windows_log_identity(log_final.log_identity))]      # rh17 B-2: the log is read up to its FINAL line; the inbox console repaints after it
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
