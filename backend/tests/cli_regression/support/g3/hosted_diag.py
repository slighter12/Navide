"""Rehearsal diagnostics of the hosted driver (never the registered run, never a measured step): what a failed pre-measurement step can say about itself without adding anything to the upload.

* `emit`: the one way a diagnostic line is written. It cannot raise on the console's encoding (a character the code page lacks is written as a backslash escape): rehearsal 1 lost the Windows
  `packaged-electron` error because a plain `print` of electron-builder's U+2A2F marker raised `UnicodeEncodeError` on a cp1252 stdout.
* `redacted_tail`: the last lines of a step's own log with every leak-check token shape replaced (`[redacted]`); `first_error_line`: the first line of that log that starts with electron-builder's error marker.
* `reason_lines`: the names, booleans and strings of a failed step's receipt (`packaged-smoke.json`, `smoke-verdict.json`), no number, so no measured value can leak. Lists of objects are descended into
  (the smoke verdict's per-attempt rows: kind, mode, vendor, passed, problems).
* `attempt_log_tails`: for a failed `smoke-verify` only. The smoke runner (`tests/cli/g3LaunchAttempt.ts`, frozen) ends its attempt in `catch { passed = false }`, but the attempt already wrote its backend logs into
  its own `navide-g3l-*` directory under the temporary directory. For every failed (mode, vendor) row of the verdict this prints the redacted tails of those logs. It only reads: nothing is started after a failed step.
* `stage_smoke`: the rehearsal's copy of `smoke-verdict.json` and `smoke-entries.json` for the upload, strings and booleans only.
* `probe_backend`: for a failed `packaged-smoke` only. The smoke runner (`tests/cli/g3Packaged.ts`, frozen) swallows the reason an attempt failed and shows only `passed=false`; this starts the same frozen
  backend once, the way that attempt does (isolated environment, `--port 0`, a newline on stdin), and reports the stage that stops: cannot start, exits before it serves, never writes its port and token files,
  or serves but refuses the websocket handshake. If all of those hold, the failing stage is `terminal.create` or the READY line. Neither the port nor the token is printed.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse

TAIL_LINES = 40
TOKEN_PATTERNS = (re.compile(r"(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}"), re.compile(r"github_pat_[A-Za-z0-9_]{20,}"))      # the leak-check token shapes
RECEIPTS = ("packaged-smoke.json", "smoke-verdict.json")      # the receipts of the two smoke steps; neither holds a timing value
PROBED_STEP = "packaged-smoke"
READY_COMMAND_ENV = "ACR_G3H_READY_COMMAND"      # `hosted_packaged.READY_COMMAND_ENV`, repeated: this module imports no harness module
READY_WAIT_S = 45.0
STOP_WAIT_S = 15.0


def emit(line: object, stream=None) -> None:
    """`print` that cannot raise on the console's encoding: what the stream's code page cannot hold is written as a backslash escape (`\\u2a2f`). `stream` defaults to `sys.stdout` at call time."""
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(str(line).encode(encoding, "backslashreplace").decode(encoding) + "\n")
    stream.flush()


def redact(text: str) -> str:
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def redacted_tail(text: str, lines: int = TAIL_LINES) -> list[str]:
    """The last `lines` lines of `text`, every token-shaped string replaced with `[redacted]`."""
    return [redact(line) for line in text.splitlines()[-lines:]]


ERROR_MARKER = "⨯"      # electron-builder's error marker; its error line is `  <marker> message`
ERROR_LINE_CHARS = 400


def first_error_line(text: str) -> str | None:
    """The first line of `text` that starts, after its whitespace, with electron-builder's error marker; redacted and capped. A long log cannot push it out of the tail it is printed ahead of."""
    for line in text.splitlines():
        if line.lstrip().startswith(ERROR_MARKER):
            return redact(line)[:ERROR_LINE_CHARS]
    return None


def reason_lines(doc: object, prefix: str = "", depth: int = 3) -> list[str]:
    """`name=value` for every boolean, string and list of strings in `doc` (nested objects as `outer.inner`, the objects of a list as `name[0].inner`); numbers are dropped."""
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    for key, value in doc.items():
        name = f"{prefix}{key}"
        if isinstance(value, (bool, str)):
            out.append(redact(f"{name}={value}"))
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            out.append(redact(f"{name}={value}"))
        elif isinstance(value, dict) and depth > 1:
            out += reason_lines(value, f"{name}.", depth - 1)
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value) and depth > 1:
            for index, item in enumerate(value):
                out += reason_lines(item, f"{name}[{index}].", depth - 1)
    return out


def _ws_connect(url: str) -> None:
    from websockets.sync.client import connect
    with connect(url, open_timeout=10, close_timeout=2):
        pass


def _isolated_environment(root: Path) -> dict:
    from tests.cli_regression.support.isolation import isolated_environment
    return isolated_environment(root)


READY_COMMAND = "printf 'BASELINE_READY {\"packaged\":true}\\n'; exec sleep 120"      # `tests/cli/g3Packaged.ts` READY_COMMAND, the same string the attempt sends
STAGE_WAIT_S = 20.0      # how long the terminal stage listens after `terminal.create`; the attempt waits 60 s, a pane that is going to answer answers well inside this
OUTPUT_LINES = 8
DIAG_LOG_LEVEL = "debug"      # the attempt starts the backend at `warning`; the diagnostic start wants terminal and sidecar spawn messages
DIAG_TAIL_LINES = 60


SHUTDOWN_DIAG_ENV = "NAVIDE_SHUTDOWN_DIAG"      # `agent_team_backend/__main__.py` `_register_shutdown_diag`: SIGUSR1 dumps every thread's stack to stderr
DUMP_WAIT_S = 2.0
DUMP_LINES = 150


def stage_timed_out(lines: list[str]) -> bool:
    """The terminal stage hung: no reply at all, or a reply that created the pane but no READY line. A refusal names itself and needs no dump."""
    text = "\n".join(lines)
    return "terminal.create reply: none within the wait" in text or ("terminal.create reply: ok=True" in text and "BASELINE_READY seen=False" in text)


def output_frame(raw: bytes) -> tuple[str, bytes] | None:
    """`(terminal_session_id, data)` of a binary `terminal.output` frame (`src/shared/wsClient.ts` `parseTerminalOutputFrame`), None for anything else."""
    if len(raw) < 7 or raw[0] != 0x01:
        return None
    sid_len = raw[5]
    offset = 6 + sid_len
    if len(raw) < offset + 1:
        return None
    pane_len = raw[offset]
    start = offset + 1 + pane_len
    return (raw[6:offset].decode("utf-8", "replace"), raw[start:]) if len(raw) >= start else None


def _open_session(url: str):
    from websockets.sync.client import connect
    return connect(url, open_timeout=10, close_timeout=2)


def terminal_stage(url: str, root: Path, *, open_session: Callable = _open_session, clock: Callable[[], float] = time.monotonic, wait_s: float = STAGE_WAIT_S, secrets: tuple[str, ...] = (),
                   ready_command: str | None = None) -> list[str]:
    """The `terminal.create` of the smoke attempt, sent by hand, and which stage it reaches: the reply (ok, or the refusal's code and message), whether any `terminal.output` frame arrived, whether one held `BASELINE_READY`,
    any other event the backend pushed, and the first redacted lines of the pane output. Names and booleans only: no number of any kind (no timing, no sequence, no byte count).
    `ready_command` is the Windows-native pane command string (`ACR_G3H_READY_COMMAND`): a Windows agent pane takes a plain command line and no shell, so the POSIX `bash -lc printf ...; exec sleep` list cannot start there."""
    import uuid
    from datetime import datetime, timezone
    request_id = str(uuid.uuid4())
    body = {
        "pane_id": "packaged-pane", "agent_key": "terminal", "cwd": str(root), "command": ready_command if ready_command else ["/bin/bash", "-lc", READY_COMMAND], "cols": 100, "rows": 30, "create_generation": "packaged-generation",
        "metadata": {"workspace_path": str(root), "session_marker": "at-pane:packaged-pane"}}
    create = {"id": request_id, "type": "terminal.create", "timestamp": datetime.now(timezone.utc).isoformat(), "payload": body}
    reply: str | None = None
    session_id = ""
    chunks: list[bytes] = []
    events: list[str] = []
    out: list[str] = ["--- terminal stage: the same terminal.create as the attempt (rehearsal diagnostics, no numbers) ---"]
    try:
        with open_session(url) as ws:
            ws.send(json.dumps(create))
            deadline = clock() + wait_s
            while clock() < deadline:
                try:
                    message = ws.recv(timeout=max(0.1, deadline - clock()))
                except TimeoutError:
                    break
                if isinstance(message, (bytes, bytearray)):
                    frame = output_frame(bytes(message))
                    if frame is not None:
                        session_id = session_id or frame[0]
                        chunks.append(frame[1])
                    continue
                doc = json.loads(message)
                if doc.get("id") == request_id:
                    error = doc.get("error") or {}
                    reply = "ok=True" if doc.get("ok") else f"ok=False error={error.get('code')}: {str(error.get('message'))[:150]}"
                    if doc.get("ok"):
                        session_id = str((doc.get("payload") or {}).get("terminal_session_id") or session_id)
                elif str(doc.get("type")) not in events:
                    events.append(str(doc.get("type")))
                if b"BASELINE_READY" in b"".join(chunks) and reply is not None:
                    break
    except Exception as error:  # noqa: BLE001 - every failure is the finding
        out.append(f"terminal stage connection failed: {type(error).__name__}: {str(error)[:150]}")
    text = b"".join(chunks).decode("utf-8", "replace")
    out.append(f"terminal.create reply: {reply if reply is not None else 'none within the wait'}")
    out.append(f"terminal.output frame arrived={bool(chunks)}")
    out.append(f"BASELINE_READY seen={'BASELINE_READY' in text}")
    out.append(f"other events pushed by the backend: {events if events else 'none'}")
    out.append(f"--- first {OUTPUT_LINES} lines of the pane output ---")
    for line in text.splitlines()[:OUTPUT_LINES]:
        for secret in secrets:
            line = line.replace(secret, "[redacted]") if secret else line
        out.append(redact(line)[:200])
    return out


def probe_backend(backend: str | Path, *, popen: Callable = subprocess.Popen, connect: Callable[[str], None] = _ws_connect, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                  wait_s: float = READY_WAIT_S, environment: Callable[[Path], dict] = _isolated_environment, terminal: Callable[..., list[str]] | None = None, log_level: str = "warning",
                  tail_lines: int = TAIL_LINES, thread_dump: bool = False, ready_command: str | None = None) -> list[str]:
    """Starts the packaged backend like the smoke attempt does and reports, as lines, how far it gets. Booleans and exit codes only; the backend is stopped before returning.
    With `terminal` (the rehearsal diagnostic of `step_reasons`) it also sends the attempt's `terminal.create` once the handshake worked, and reports the stage that stops."""
    from tests.cli_regression.support.g3 import packaged
    backend = Path(backend)
    sidecar = backend.with_name(packaged.RUNTIME_NAME)
    out = [f"backend file exists={backend.is_file()} executable={os.access(backend, os.X_OK)}", f"staged sidecar beside it exists={sidecar.is_file()} executable={os.access(sidecar, os.X_OK)}"]
    with tempfile.TemporaryDirectory(prefix="navide-g3p-diag-", ignore_cleanup_errors=True) as raw:      # Windows refuses to delete a log a child still holds; that must not discard the lines collected so far
        root = Path(raw)
        log = root / "backend.log"
        env = {**environment(root), "NAVIDE_REGRESSION_ROOT": str(root), **({SHUTDOWN_DIAG_ENV: "1"} if thread_dump else {})}      # the flag exists only in this diagnostic backend; the attempt's own backend never gets it
        try:
            with log.open("wb") as sink:
                process = popen([str(backend), "--port", "0", "--log-level", log_level], cwd=root, env=env, stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.STDOUT)
        except OSError as error:
            return out + [f"backend could not be started: {type(error).__name__} errno={error.errno}"]
        port_file, token_file = root / "data" / "backend-port", root / "data" / "backend-ws-token"
        secrets: list[str] = []
        dump_from: int | None = None
        try:
            process.stdin.write(b"\n")
            process.stdin.flush()
            deadline = clock() + wait_s
            while clock() < deadline and process.poll() is None and not (port_file.exists() and token_file.exists()):
                sleep(0.2)
            served = port_file.exists() and token_file.exists()
            code = process.poll()
            if served:
                port, token = port_file.read_text().strip(), token_file.read_text().strip()
                secrets = [port, token]
                out.append("backend served: port and token files present=True")
                try:
                    connect(f"ws://127.0.0.1:{port}/ws?t={token}")
                    out.append("websocket handshake ok=True")
                    if terminal is not None:
                        stage = terminal(f"ws://127.0.0.1:{port}/ws?t={token}", root, secrets=tuple(secrets), **({"ready_command": ready_command} if ready_command else {}))
                        out += stage
                        if thread_dump and stage_timed_out(stage) and not hasattr(signal, "SIGUSR1"):      # Windows has no SIGUSR1: say so instead of ending the probe in an AttributeError that reads as a handshake failure
                            out.append("--- no backend thread dump: this platform has no SIGUSR1 ---")
                        elif thread_dump and stage_timed_out(stage):      # the backend answered nothing, or the pane never printed READY: ask the frozen backend where its threads are
                            dump_from = log.stat().st_size if log.is_file() else 0
                            process.send_signal(signal.SIGUSR1)
                            sleep(DUMP_WAIT_S)
                            raw_dump = log.read_bytes()[dump_from:].decode("utf-8", errors="replace") if log.is_file() else ""
                            for secret in secrets:
                                raw_dump = raw_dump.replace(secret, "[redacted]") if secret else raw_dump
                            out.append("--- backend thread dump (SIGUSR1, rehearsal diagnostics) ---")
                            out += redacted_tail(raw_dump, DUMP_LINES) if raw_dump.strip() else ["no dump was written (the backend did not register the SIGUSR1 handler, or it is blocked below Python)"]
                except Exception as error:  # noqa: BLE001 - every failure is the finding
                    message = str(error)
                    for secret in secrets:
                        message = message.replace(secret, "[redacted]") if secret else message
                    out.append(f"websocket handshake ok=False: {type(error).__name__}: {redact(message)[:150]}")
            elif code is not None:
                out.append(f"backend exited before it served: exit code={code}")
            else:
                out.append("backend still running, port and token files present=False")
        finally:
            _stop(process, sleep, clock)
        tail = log.read_bytes()[:dump_from].decode("utf-8", errors="replace") if log.is_file() and dump_from is not None else (log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")      # the dump has its own section
        for secret in secrets:
            tail = tail.replace(secret, "[redacted]") if secret else tail
        out.append(f"--- backend log (level {log_level}), last {tail_lines} lines ---")
        out += redacted_tail(tail, tail_lines)
    return out


def _stop(process, sleep, clock) -> None:
    try:
        process.stdin.write(b"shutdown\n")
        process.stdin.flush()
    except (OSError, ValueError):
        pass      # already gone
    try:
        process.wait(timeout=STOP_WAIT_S)
    except Exception:  # noqa: BLE001 - a backend that does not stop is killed
        process.kill()


NODE_DIAG_ENTRY = "tests/cli/g3hPackagedDiag.test.ts"      # mirrors `runPackagedAttempt` and says which stage it reached (rehearsal-4: the Python probe reached READY, the Node attempt did not)
NODE_DIAG_SELECTOR = "packaged-diag"      # the `ACR_G3` value that makes the entry run its attempt
NODE_DIAG_PREFIX = "g3h-diag "      # every line the entry means to print starts with this; nothing else of the vitest output is shown
NODE_DIAG_LINES = 80
NODE_DIAG_TAIL = 15
NODE_DIAG_TIMEOUT_S = 300
VERSION_COMMANDS = (("bun", "--version"),)      # rf4c D2: the TS clients run on plain Bun, and no Node is required (or asked for) on the runner
URL_PATTERN = re.compile(r"ws://[^\s\"']+")
LINUX_ENTRY = "tests/cli/g3Matrix.test.ts"      # `hosted_packaged.MATRIX_ENTRY`: the Linux `packaged-smoke` runs the unchanged entry
WINDOWS_ENTRY = "tests/cli/g3hPackagedWindows.test.ts"      # `hosted_packaged.WINDOWS_PACKAGED_ENTRY`: the Windows entry; its READY command rides in the step env (`ACR_G3H_READY_COMMAND`), which the diagnostic inherits
DIAGNOSED_ENTRIES = (LINUX_ENTRY, WINDOWS_ENTRY)


def run_capture(argv: list[str], env: dict, cwd: str, timeout: float) -> tuple[int, str]:
    """`(exit code, stdout and stderr)` of one command. Raises `subprocess.TimeoutExpired` or `OSError`; `node_diagnostic` reports those by class only."""
    done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    return done.returncode, done.stdout + done.stderr


def _redact_line(line: str) -> str:
    return URL_PATTERN.sub("ws://[redacted]", redact(line))


def node_diagnostic(step: dict, emit: Callable[[str], None], run: Callable[..., tuple[int, str]] = run_capture) -> None:
    """After a failed `packaged-smoke` (Linux or Windows entry): ONE extra attempt of the TS client (on Bun, `bunx --bun vitest`), exactly as the step runs it (same command, same environment) but on `NODE_DIAG_ENTRY`,
    which prints the stages it reaches and the caught error. Also the Bun version the runner has. (The names keep "node" from when the clients ran on Node.) Only the lines the entry prefixes are shown, each redacted (token shapes, websocket URLs); no port, no token, no timing value. A run that cannot start or times out
    is reported by exception class alone."""
    argv = list(step["argv"])
    if not argv or argv[-1] not in DIAGNOSED_ENTRIES:
        return
    emit(f"--- {step['name']}: one diagnostic attempt of the TS client, as the attempt does (rehearsal diagnostics) ---")
    env = {**os.environ, **step["env"], "ACR_G3": NODE_DIAG_SELECTOR}
    for command in VERSION_COMMANDS:
        try:
            _, text = run(list(command), env, step["cwd"], 30)
            emit(f"{' '.join(command)}: {_redact_line(text.strip().splitlines()[0]) if text.strip() else 'no output'}")
        except Exception as error:  # noqa: BLE001 - the finding is that the runtime is missing
            emit(f"{' '.join(command)}: could not run ({type(error).__name__})")
    try:
        code, text = run([*argv[:-1], NODE_DIAG_ENTRY], env, step["cwd"], NODE_DIAG_TIMEOUT_S)
    except Exception as error:  # noqa: BLE001 - every failure is the finding
        emit(f"the diagnostic attempt could not finish: {type(error).__name__}")
        return
    shown = [_redact_line(line) for line in text.splitlines() if line.startswith(NODE_DIAG_PREFIX)]
    for line in shown[:NODE_DIAG_LINES]:
        emit(line)
    emit(f"the diagnostic attempt's own run ended with exit code={code}; diagnostic lines printed={'none' if not shown else 'yes'}")
    if not shown:      # the entry never ran (vitest could not start, the file did not load): the tail of the run is then the only explanation
        emit(f"--- the diagnostic attempt printed no diagnostic line: last {NODE_DIAG_TAIL} lines of its own output ---")
        for line in redacted_tail(text, NODE_DIAG_TAIL):
            emit(_redact_line(line))


SMOKE_VERIFY_STEP = "smoke-verify"
VERDICT_NAME = "smoke-verdict.json"
ENTRIES_NAME = "smoke-entries.json"
LAUNCH_MODES = ("python", "rust-shell")
LAUNCH_VENDORS = ("claude", "aider", "shell")
ATTEMPT_DIR_PREFIX = "navide-g3l-"      # `tests/cli/g3LaunchAttempt.ts` `runLaunchAttempt`: each attempt makes `mkdtempSync(join(tmpdir(), 'navide-g3l-'))`, NOT a directory under the smoke step's output
ATTEMPT_RECEIPT = "receipt.json"      # written there when the attempt ends; holds `selectedRuntime` (the mode) and `vendor`
ATTEMPT_LOGS: tuple[tuple[str, str | None, str], ...] = (      # (what, path below the attempt's directory or None, why there is none)
    ("launch driver output", None, "no such log exists: the attempt reads and discards the driver's stdout and stderr (`g3LaunchAttempt.ts`) and the driver writes none"),
    ("backend output", "launch-backend.log", ""),      # `BackendProcess(root, artifact_prefix="launch-")`: the backend's stdout and stderr
    ("backend application log", "data/logs/backend.log", ""),      # the backend's own timestamped log
    ("pane output", None, "no such log exists: the pane has no log path (`launch_driver.py` `logPath` is None) and its output only travels over the websocket to the attempt"),
)
SEPARATOR_UNIT = r"(?:\\u00(?:2f|5c)|\\x(?:2f|5c)|%(?:2f|5c)|[\\/])"      # one path separator in any spelling: `/`, `\`, `%2F`, `%5C`, `\u002f`, `\u005c`, `\x2f`, `\x5c` (any case, under `(?i)`); a JSON / C escaped `\\` is two units
SEPARATOR = rf"(?:{SEPARATOR_UNIT})++"      # a run of one or more of them, POSSESSIVE (Python 3.11+; the backend requires 3.12): a run that was taken whole is never given back one unit at a time
SEPARATOR_START = r"(?<![\\/])(?<!%(?:2f|5c))(?<!\\x(?:2f|5c))(?<!\\u00(?:2f|5c))"      # a separator-led match may only begin at the START of a run: one fixed-width look-behind per spelling width (1, 3, 4 and 6 characters). Without it a run of n separators is scanned from each of its n positions (quadratic)
ACCOUNT_SEGMENT = rf"(?:(?!{SEPARATOR_UNIT})[^\r\n])++"      # everything up to the next separator (in any spelling) or the end of the line, spaces included
HOME_PATH = re.compile(      # `/home/<account>`, `/Users/<account>`, `C:\Users\<account>` (any separator spelling, also JSON-escaped, URL-encoded, `\u005c`; a drive letter may be followed by no separator), `/root`, and `~<account>`. Linear in the length of the line
    rf"(?i)(?:(?:(?<![a-z0-9])[a-z]:(?:{SEPARATOR})?+|{SEPARATOR_START}{SEPARATOR})(?:home|users){SEPARATOR}{ACCOUNT_SEGMENT}|{SEPARATOR_START}{SEPARATOR}root(?![\w.-])|(?<![\w~])~(?!{SEPARATOR_UNIT})(?!\s){ACCOUNT_SEGMENT})"
)
DIAG_LINE_MAX_CHARS = 4000      # one log line is redacted whole, then its REDACTED text is cut to 4000 characters: the cap bounds what is printed (a backend traceback line, a long path or a JSON record fits); the linear pattern above bounds the time
HOME_LITERALS = (("HOME", "[home]"), ("USERPROFILE", "[home]"), ("USER", "[user]"), ("USERNAME", "[user]"))      # environment values that are replaced wherever they occur, before `HOME_PATH`
LITERAL_MIN_LENGTH = 3      # a value of one or two characters ("/" as a HOME) would wreck every line, so it is not replaced literally


def home_literals(environ: dict) -> list[tuple[str, str]]:
    """`(value, replacement)` for the HOME, USERPROFILE, USER and USERNAME values of `environ` longer than two characters (a HOME without its trailing separator), longest first. Never printed."""
    pairs: dict[str, str] = {}
    for name, replacement in HOME_LITERALS:
        value = str(environ.get(name) or "")
        value = value.rstrip("\\/") if replacement == "[home]" else value
        if len(value) >= LITERAL_MIN_LENGTH:
            pairs.setdefault(value, replacement)
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


def redact_attempt_line(line: str, secrets: list[str], literals: list[tuple[str, str]] | tuple[tuple[str, str], ...] = ()) -> str:
    """One log line with the attempt's port and token, the literal home and account values, every leak-check token shape and every home-directory path (the whole account segment) replaced."""
    for secret in secrets:
        line = line.replace(secret, "[redacted]") if secret else line
    forms: dict[str, str] = {}
    for value, replacement in literals:      # each value as it is, JSON-escaped (`\` → `\\`) and URL-encoded (with and without `/` kept)
        for form in (value, value.replace("\\", "\\\\"), quote(value, safe=""), quote(value, safe="/")):
            forms.setdefault(form, replacement)
    for form, replacement in sorted(forms.items(), key=lambda pair: len(pair[0]), reverse=True):
        line = line.replace(form, replacement)
    return HOME_PATH.sub("[home]", redact(line))


def failed_pairs(verdict: object) -> list[tuple[str, str]]:
    """`(mode, vendor)` of every row of the smoke verdict that did not pass, in the order the smoke ran them; rows without a known mode and vendor never name a directory."""
    rows = verdict.get("entries") if isinstance(verdict, dict) else None
    pairs: list[tuple[str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("passed") is not True and row.get("mode") in LAUNCH_MODES and row.get("vendor") in LAUNCH_VENDORS and (row["mode"], row["vendor"]) not in pairs:
            pairs.append((row["mode"], row["vendor"]))
    return pairs


def attempt_roots(environ: dict) -> list[Path]:
    """Where the smoke runner's `os.tmpdir()` pointed: the first of TMPDIR, TMP, TEMP (POSIX) or TEMP, TMP (Windows) that is set, else Node's own fallback."""
    for name in ("TEMP", "TMP") if os.name == "nt" else ("TMPDIR", "TMP", "TEMP"):
        if environ.get(name):
            return [Path(environ[name])]
    return [Path(environ.get("SystemRoot") or environ.get("windir") or "C:\\Windows") / "temp"] if os.name == "nt" else [Path("/tmp")]


def find_attempt(roots: list[Path], mode: str, vendor: str) -> Path | None:
    """The newest `navide-g3l-*` directory under `roots` whose `receipt.json` names this mode and vendor."""
    best: tuple[float, Path] | None = None
    for root in roots:
        try:
            candidates = [path for path in root.glob(f"{ATTEMPT_DIR_PREFIX}*") if path.is_dir()]
        except OSError:
            continue
        for path in candidates:
            try:
                receipt = json.loads((path / ATTEMPT_RECEIPT).read_text(encoding="utf-8"))
                stamp = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            if isinstance(receipt, dict) and receipt.get("selectedRuntime") == mode and receipt.get("vendor") == vendor and (best is None or stamp > best[0]):
                best = (stamp, path)
    return best[1] if best else None


def attempt_secrets(attempt: Path) -> list[str]:
    """The port and the token of the attempt's backend, from the `endpoint.json` its launch driver wrote (empty when it wrote none)."""
    try:
        parsed = urlparse(str(json.loads((attempt / "endpoint.json").read_text(encoding="utf-8"))["url"]))
        return [str(parsed.port or ""), *parse_qs(parsed.query).get("t", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def attempt_log_tails(step: dict, emit: Callable[[str], None], *, environ: dict | None = None, tail_lines: int = DIAG_TAIL_LINES) -> None:
    """After a failed `smoke-verify`: for EVERY failed (mode, vendor) row of its verdict, the redacted tail of that attempt's own logs, which the failed attempt already wrote on the runner. The smoke runner
    (`tests/cli/g3LaunchAttempt.ts`, frozen) ends an attempt in `catch { passed = false }`, but the attempt's launch-driver backend wrote `launch-backend.log` and its application log into the attempt's own
    directory (`navide-g3l-*` under the temporary directory, found by its `receipt.json`). Nothing is started: this only reads. A log that does not exist is one line saying so."""
    verdict_path = next((Path(output) for output in step.get("outputs", []) if Path(output).name == VERDICT_NAME), None)
    if verdict_path is None or not verdict_path.is_file():
        return
    try:
        pairs = failed_pairs(json.loads(verdict_path.read_text(encoding="utf-8")))
    except ValueError:
        return
    if not pairs:
        emit(f"--- {step['name']}: no failed attempt in the verdict, so no attempt logs to show (rehearsal diagnostics) ---")
        return
    merged = {**(os.environ if environ is None else environ), **step.get("env", {})}
    roots = attempt_roots(merged)
    literals = home_literals(merged)
    for mode, vendor in pairs:
        emit(f"--- {step['name']}: the logs the failed attempt wrote, mode={mode} vendor={vendor} (rehearsal diagnostics, redacted, nothing is started) ---")
        attempt = find_attempt(roots, mode, vendor)
        if attempt is None:
            emit(f"no {ATTEMPT_DIR_PREFIX}* directory with a {ATTEMPT_RECEIPT} for this attempt was found")
            continue
        secrets = attempt_secrets(attempt)
        for what, relative, why in ATTEMPT_LOGS:
            if relative is None:
                emit(f"{what}: {why}")
                continue
            try:
                text = (attempt / relative).read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                emit(f"{what}: missing ({relative})")
                continue
            except OSError as error:
                emit(f"{what}: could not be read ({type(error).__name__})")
                continue
            emit(f"--- {what} ({relative}), last {tail_lines} lines ---")
            for line in text.splitlines()[-tail_lines:]:
                redacted = redact_attempt_line(line, secrets, literals)      # redact the WHOLE line first (the pattern is linear, so its time is bounded), then cut the redacted text: a secret that straddles the cap is matched whole
                emit(redacted[:DIAG_LINE_MAX_CHARS] + (f" [... {len(redacted) - DIAG_LINE_MAX_CHARS} characters cut]" if len(redacted) > DIAG_LINE_MAX_CHARS else ""))


def plain(value: object) -> object:
    """`value` with every number dropped (also from lists) and every string redacted: booleans, strings, null, lists and objects of those stay."""
    def kept(item: object) -> bool:
        return isinstance(item, bool) or not isinstance(item, (int, float))
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items() if kept(item)}
    if isinstance(value, list):
        return [plain(item) for item in value if kept(item)]
    return redact(value) if isinstance(value, str) else value


def stage_smoke(source: str | Path, destination: str | Path) -> list[str]:
    """Copy `smoke-verdict.json` and `smoke-entries.json` from `source` to `destination` for the rehearsal upload, strings and booleans only (no pid, no count, no number). Returns one status line per file."""
    out: list[str] = []
    for name in (VERDICT_NAME, ENTRIES_NAME):
        path = Path(source) / name
        if not path.is_file():
            out.append(f"missing {name}")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            out.append(f"{name} is not JSON")
            continue
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination) / name).write_text(json.dumps(plain(document), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        out.append(f"staged {name}")
    return out


def step_reasons(step: dict, emit: Callable[[str], None], node_run: Callable[..., tuple[int, str]] | None = None, environ: dict | None = None) -> None:
    """After a failed step: the reason fields of its receipt, and (packaged smoke only) the backend probe and, when `node_run` is given, the Node attempt (`node_diagnostic`); after a failed `smoke-verify`, the
    tails of the logs every failed attempt already wrote (`attempt_log_tails`; `environ` says where the temporary directory is, `os.environ` by default). No process is started for it."""
    for output in step.get("outputs", []):
        path = Path(output)
        if path.name in RECEIPTS and path.is_file():
            emit(f"--- {step['name']}: receipt {path.name} (rehearsal diagnostics, no numbers) ---")
            try:
                for line in reason_lines(json.loads(path.read_text(encoding="utf-8"))):
                    emit(line)
            except ValueError:
                emit("the receipt is not JSON")
    if step["name"] == PROBED_STEP:
        emit("--- packaged-smoke: one start of the frozen backend, as the attempt does (rehearsal diagnostics) ---")
        for line in probe_backend(step["env"]["ACR_G3_PACKAGED_BACKEND"], terminal=terminal_stage, log_level=DIAG_LOG_LEVEL, tail_lines=DIAG_TAIL_LINES, thread_dump=True,
                                    ready_command=step.get("env", {}).get(READY_COMMAND_ENV)):
            emit(line)
        if node_run is not None:
            node_diagnostic(step, emit, node_run)
    if step["name"] == SMOKE_VERIFY_STEP:
        attempt_log_tails(step, emit, environ=environ)


if __name__ == "__main__":      # the rehearsal workflows' stage step: `hosted_diag.py stage-smoke <smoke dir> <stage dir>`; a missing file is a status line, never a failure of the step
    if len(sys.argv) == 4 and sys.argv[1] == "stage-smoke":
        for status in stage_smoke(sys.argv[2], sys.argv[3]):
            print(status)
        raise SystemExit(0)
    print("usage: hosted_diag.py stage-smoke SMOKE_DIR STAGE_DIR", file=sys.stderr)
    raise SystemExit(64)
