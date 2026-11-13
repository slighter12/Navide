"""Settings → CLI Agents → "Rust CLI runtime (experimental)": what the switch changes.

Off (the default, and whenever the setting is absent) means exactly main's
behaviour: every log reader runs its own Python code, and nothing here starts a
process.

On routes three reader calls — ``parse_incremental``, ``parse_activity`` and
``turns_for_session`` — of each CLI whose records the Rust runtime declares
(``native/navide-cli-runtime``) to one long-lived ``navide-cli-runtime serve``
process. Every other reader method, and every CLI the runtime does not observe,
stays on Python. Any failure — no binary, a dead process, an error answer —
falls back to the Python reader for that call, so turning the switch on can
never stop usage or activity from being read.

The two sides share their state, so the switch can flip either way
mid-session: usage checkpoints have one format, and the runtime reads and
writes an activity walk's seen-keys bag in the key formats main's readers use
(the ``act_hw::N`` line mark, each reader's pending-reply and turn-tracker
keys), as each CLI's declaration names them.

Applied at backend startup from the stored setting and again by
``ui.settings.set`` whenever the key changes.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .log_readers.base import (
    ActivityEvent,
    IncrementalParseResult,
    PromptText,
    TokenUsage,
    TurnCall,
    TurnUsage,
)

log = logging.getLogger("agent_team_backend.cli_runtime")

SETTING_KEY = "rustCliRuntime"
BINARY_ENV = "NAVIDE_CLI_RUNTIME_BIN"

_enabled = False
_lock = threading.Lock()
_client: "_Client | None" = None
_observed: frozenset[str] | None = None


def enabled() -> bool:
    return _enabled


def apply(on: bool) -> None:
    """Make the backend match the switch. Idempotent; never raises."""
    global _enabled
    _enabled = bool(on)
    if not _enabled:
        _stop()
    log.info("Rust CLI runtime (experimental) %s", "on" if _enabled else "off")


def apply_settings(settings: Any) -> None:
    """apply() from a ui_settings document (absent or not true = off)."""
    apply(isinstance(settings, dict) and settings.get(SETTING_KEY) is True)


def binary() -> Path | None:
    """The runtime executable: $NAVIDE_CLI_RUNTIME_BIN, else the repo's release build."""
    env = os.environ.get(BINARY_ENV)
    if env:
        path = Path(env)
    else:
        name = "navide-cli-runtime.exe" if sys.platform == "win32" else "navide-cli-runtime"
        path = Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime" / "target" / "release" / name
    return path if path.is_file() else None


class _Client:
    """One ``serve`` process: a JSON request per line in, a JSON answer per line out."""

    def __init__(self, exe: Path) -> None:
        self._proc = subprocess.Popen(
            [str(exe), "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._next = 0

    def call(self, op: str, **fields: Any) -> tuple[bool, Any]:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._next += 1
        self._proc.stdin.write(json.dumps({"id": self._next, "op": op, **fields}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise OSError("navide-cli-runtime exited")
        answer = json.loads(line)
        if answer.get("id") != self._next:
            raise OSError("navide-cli-runtime answered out of order")
        return bool(answer.get("ok")), answer.get("result") if answer.get("ok") else answer.get("error")

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            self._proc.kill()


def _stop() -> None:
    global _client, _observed
    with _lock:
        if _client is not None:
            _client.close()
        _client, _observed = None, None


def _call(vendor: str, op: str, **fields: Any) -> tuple[bool, Any] | None:
    """The runtime's answer for a CLI it observes; None means "use Python"."""
    global _client, _observed
    if not _enabled:
        return None
    with _lock:
        try:
            if _client is None:
                exe = binary()
                if exe is None:
                    return None
                _client = _Client(exe)
                ok, observed = _client.call("observes")
                _observed = frozenset(observed) if ok else frozenset()
            if vendor not in (_observed or ()):
                return None
            return _client.call(op, vendor=vendor, **fields)
        except Exception as err:  # noqa: BLE001 — the Python reader takes over
            log.warning("navide-cli-runtime unavailable, using the Python reader: %s", err)
            if _client is not None:
                _client.close()
            _client, _observed = None, None
            return None


# ---- the three routed reader calls ------------------------------------------


def _usage(vendor: str, original: Callable[..., IncrementalParseResult], path: Path, checkpoint: dict) -> IncrementalParseResult:
    answer = _call(vendor, "usage", path=str(path), checkpoint=checkpoint or {})
    if answer is None or not answer[0]:
        return original(path, checkpoint)
    result = answer[1]
    events = [
        TokenUsage(
            vendor=e["vendor"],
            input_tokens=e["input_tokens"],
            output_tokens=e["output_tokens"],
            cwd=e["cwd"],
            session_id=e["session_id"],
            file_path=e["file_path"],
            dedup_key=e["dedup_key"],
            timestamp=e["timestamp"],
            model=e["model"],
            checkpoint=e["checkpoint"],
            raw=e["raw"],
            cache_read_tokens=e["cache_read_tokens"],
            cache_creation_tokens=e["cache_creation_tokens"],
            cli_version=e["cli_version"],
        )
        for e in result["events"]
    ]
    return IncrementalParseResult(events, result["checkpoint"])


def _prompt(text: str, full: str) -> str:
    if not full:
        return text
    out = PromptText(text)
    out.full = full
    return out


def _activity(vendor: str, original: Callable[..., list[ActivityEvent]], path: Path, seen_keys: set[str]) -> list[ActivityEvent]:
    answer = _call(vendor, "activity", path=str(path), bag=sorted(seen_keys), now=time.time())
    if answer is None or not answer[0]:
        return original(path, seen_keys)
    result = answer[1]
    seen_keys.clear()
    seen_keys.update(result["bag"])
    if result.get("error"):
        # main's walk raises here too, after its state is saved.
        raise UnicodeDecodeError("utf-8", b"", 0, 1, str(result["error"]))
    return [
        ActivityEvent(
            vendor=e["vendor"],
            event_type=e["event_type"],
            cwd=e["cwd"],
            session_id=e["session_id"],
            file_path=e["file_path"],
            dedup_key=e["dedup_key"],
            timestamp=e["timestamp"],
            detail=e["detail"],
            text=_prompt(e["text"], e.get("full", "")),
        )
        for e in result["events"]
    ]


def _turns(vendor: str, original: Callable[..., list[TurnUsage]], path: Path, session_id: str = "") -> list[TurnUsage]:
    answer = _call(vendor, "turns", path=str(path), session_id=session_id or "", now=time.time())
    if answer is None or not answer[0]:
        return original(path, session_id)
    out: list[TurnUsage] = []
    for t in answer[1]:
        turn = TurnUsage(
            turn_index=t["turn_index"],
            session_id=t["session_id"],
            started_at=t["started_at"],
            ended_at=t["ended_at"],
            prompt_excerpt=t["prompt_excerpt"],
            input=t["input"],
            cache_read=t["cache_read"],
            cache_creation=t["cache_creation"],
            output=t["output"],
            calls=[TurnCall(**c) for c in t["calls"]],
            model_calls=t["model_calls"],
            cli_version=t["cli_version"],
        )
        out.append(turn)
    return out


def wrap(reader: Any) -> Any:
    """Route the reader's three record calls through the switch. Returns the reader.

    The bound methods are replaced on the instance, so the reader keeps its
    class, its attributes and every other method; each call checks the switch.
    """
    vendor = str(getattr(reader, "vendor", "") or "")
    if not vendor:
        return reader
    for name, route in (("parse_incremental", _usage), ("parse_activity", _activity), ("turns_for_session", _turns)):
        original = getattr(reader, name)

        def routed(*args: Any, _route=route, _original=original, **kwargs: Any) -> Any:
            return _route(vendor, _original, *args, **kwargs)

        setattr(reader, name, routed)
    return reader
