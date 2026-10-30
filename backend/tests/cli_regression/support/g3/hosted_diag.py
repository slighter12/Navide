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


def probe_backend(backend: str | Path, *, popen: Callable = subprocess.Popen, connect: Callable[[str], None] = _ws_connect, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                  wait_s: float = READY_WAIT_S, environment: Callable[[Path], dict] = _isolated_environment) -> list[str]:
    """Starts the packaged backend like the smoke attempt does and reports, as lines, how far it gets. Booleans and exit codes only; the backend is stopped before returning."""
    from tests.cli_regression.support.g3 import packaged
    backend = Path(backend)
    sidecar = backend.with_name(packaged.RUNTIME_NAME)
    out = [f"backend file exists={backend.is_file()} executable={os.access(backend, os.X_OK)}", f"staged sidecar beside it exists={sidecar.is_file()} executable={os.access(sidecar, os.X_OK)}"]
    with tempfile.TemporaryDirectory(prefix="navide-g3p-diag-") as raw:
        root = Path(raw)
        log = root / "backend.log"
        env = {**environment(root), "NAVIDE_REGRESSION_ROOT": str(root)}
        try:
            with log.open("wb") as sink:
                process = popen([str(backend), "--port", "0", "--log-level", "warning"], cwd=root, env=env, stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.STDOUT)
        except OSError as error:
            return out + [f"backend could not be started: {type(error).__name__} errno={error.errno}"]
        port_file, token_file = root / "data" / "backend-port", root / "data" / "backend-ws-token"
        secrets: list[str] = []
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
        tail = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for secret in secrets:
            tail = tail.replace(secret, "[redacted]") if secret else tail
        out.append(f"--- backend log, last {TAIL_LINES} lines ---")
        out += redacted_tail(tail)
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


def step_reasons(step: dict, emit: Callable[[str], None]) -> None:
    """After a failed step: the reason fields of its receipt, and (packaged smoke only) the backend probe."""
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
        for line in probe_backend(step["env"]["ACR_G3_PACKAGED_BACKEND"]):
            emit(line)
