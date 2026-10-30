"""Rehearsal diagnostics of the hosted driver (never the registered run, never a measured step): what a failed pre-measurement step can say about itself without adding anything to the upload.

* `redacted_tail`: the last lines of a step's own log with every leak-check token shape replaced (`[redacted]`).
* `reason_lines`: the names, booleans and strings of a failed step's receipt (`packaged-smoke.json`, `smoke-verdict.json`), no number, so no measured value can leak.
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
import tempfile
import time
from pathlib import Path
from typing import Callable

TAIL_LINES = 40
TOKEN_PATTERNS = (re.compile(r"(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}"), re.compile(r"github_pat_[A-Za-z0-9_]{20,}"))      # the leak-check token shapes
RECEIPTS = ("packaged-smoke.json", "smoke-verdict.json")      # the receipts of the two smoke steps; neither holds a timing value
PROBED_STEP = "packaged-smoke"
READY_WAIT_S = 45.0
STOP_WAIT_S = 15.0


def redact(text: str) -> str:
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def redacted_tail(text: str, lines: int = TAIL_LINES) -> list[str]:
    """The last `lines` lines of `text`, every token-shaped string replaced with `[redacted]`."""
    return [redact(line) for line in text.splitlines()[-lines:]]


def reason_lines(doc: object, prefix: str = "", depth: int = 3) -> list[str]:
    """`name=value` for every boolean, string and list of strings in `doc` (nested objects as `outer.inner`); numbers are dropped."""
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


def terminal_stage(url: str, root: Path, *, open_session: Callable = _open_session, clock: Callable[[], float] = time.monotonic, wait_s: float = STAGE_WAIT_S, secrets: tuple[str, ...] = ()) -> list[str]:
    """The `terminal.create` of the smoke attempt, sent by hand, and which stage it reaches: the reply (ok, or the refusal's code and message), whether any `terminal.output` frame arrived, whether one held `BASELINE_READY`,
    any other event the backend pushed, and the first redacted lines of the pane output. Names and booleans only: no number of any kind (no timing, no sequence, no byte count)."""
    import uuid
    from datetime import datetime, timezone
    request_id = str(uuid.uuid4())
    create = {"id": request_id, "type": "terminal.create", "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {
        "pane_id": "packaged-pane", "agent_key": "terminal", "cwd": str(root), "command": ["/bin/bash", "-lc", READY_COMMAND], "cols": 100, "rows": 30, "create_generation": "packaged-generation",
        "metadata": {"workspace_path": str(root), "session_marker": "at-pane:packaged-pane"}}}
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
                  tail_lines: int = TAIL_LINES, thread_dump: bool = False) -> list[str]:
    """Starts the packaged backend like the smoke attempt does and reports, as lines, how far it gets. Booleans and exit codes only; the backend is stopped before returning.
    With `terminal` (the rehearsal diagnostic of `step_reasons`) it also sends the attempt's `terminal.create` once the handshake worked, and reports the stage that stops."""
    from tests.cli_regression.support.g3 import packaged
    backend = Path(backend)
    sidecar = backend.with_name(packaged.RUNTIME_NAME)
    out = [f"backend file exists={backend.is_file()} executable={os.access(backend, os.X_OK)}", f"staged sidecar beside it exists={sidecar.is_file()} executable={os.access(sidecar, os.X_OK)}"]
    with tempfile.TemporaryDirectory(prefix="navide-g3p-diag-") as raw:
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
                        stage = terminal(f"ws://127.0.0.1:{port}/ws?t={token}", root, secrets=tuple(secrets))
                        out += stage
                        if thread_dump and stage_timed_out(stage):      # the backend answered nothing, or the pane never printed READY: ask the frozen backend where its threads are
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
VERSION_COMMANDS = (("node", "--version"), ("bun", "--version"))
URL_PATTERN = re.compile(r"ws://[^\s\"']+")
LINUX_ENTRY = "tests/cli/g3Matrix.test.ts"      # `hosted_packaged.MATRIX_ENTRY`: the Linux `packaged-smoke` runs the unchanged entry; the Windows variant is closed out


def run_capture(argv: list[str], env: dict, cwd: str, timeout: float) -> tuple[int, str]:
    """`(exit code, stdout and stderr)` of one command. Raises `subprocess.TimeoutExpired` or `OSError`; `node_diagnostic` reports those by class only."""
    done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    return done.returncode, done.stdout + done.stderr


def _redact_line(line: str) -> str:
    return URL_PATTERN.sub("ws://[redacted]", redact(line))


def node_diagnostic(step: dict, emit: Callable[[str], None], run: Callable[..., tuple[int, str]] = run_capture) -> None:
    """After a failed Linux `packaged-smoke`: ONE extra attempt through Node, exactly as the step runs it (same command, same environment) but on `NODE_DIAG_ENTRY`, which prints the stages it reaches and the caught
    error. Also the Node and Bun versions the runner has. Only the lines the entry prefixes are shown, each redacted (token shapes, websocket URLs); no port, no token, no timing value. A run that cannot start or times out
    is reported by exception class alone."""
    argv = list(step["argv"])
    if not argv or argv[-1] != LINUX_ENTRY:
        return
    emit(f"--- {step['name']}: one attempt through Node, as the attempt does (rehearsal diagnostics) ---")
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
        emit(f"the Node attempt could not finish: {type(error).__name__}")
        return
    shown = [_redact_line(line) for line in text.splitlines() if line.startswith(NODE_DIAG_PREFIX)]
    for line in shown[:NODE_DIAG_LINES]:
        emit(line)
    emit(f"the Node attempt's own run ended with exit code={code}; diagnostic lines printed={'none' if not shown else 'yes'}")
    if not shown:      # the entry never ran (vitest could not start, the file did not load): the tail of the run is then the only explanation
        emit(f"--- the Node attempt printed no diagnostic line: last {NODE_DIAG_TAIL} lines of its own output ---")
        for line in redacted_tail(text, NODE_DIAG_TAIL):
            emit(_redact_line(line))


def step_reasons(step: dict, emit: Callable[[str], None], node_run: Callable[..., tuple[int, str]] | None = None) -> None:
    """After a failed step: the reason fields of its receipt, and (packaged smoke only) the backend probe and, when `node_run` is given, the Node attempt (`node_diagnostic`)."""
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
        for line in probe_backend(step["env"]["ACR_G3_PACKAGED_BACKEND"], terminal=terminal_stage, log_level=DIAG_LOG_LEVEL, tail_lines=DIAG_TAIL_LINES, thread_dump=True):
            emit(line)
        if node_run is not None:
            node_diagnostic(step, emit, node_run)
