"""Compose native interpretation with Claude's retained discovery interface."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import os
from pathlib import Path

from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, LogReader, TokenUsage, TurnCall,
    TurnUsage, tail_anchor, user_prompt_text,
)


class NativeClaudeReader(LogReader):
    """Existing executor-based reader callers, one backend-owned sidecar."""
    vendor = "claude"
    turns_method = "exact"
    activity_resumes_by_line = True

    def __init__(self, discovery):
        self._discovery = discovery

    def project_dirs(self):
        return self._discovery.project_dirs()

    def session_files(self):
        return self._discovery.session_files()

    def session_files_for_workspace(self, workspace_path):
        return self._discovery.session_files_for_workspace(workspace_path)

    def cwd_from_file(self, path):
        return self._discovery.cwd_from_file(path)

    def pane_cwd_match(self, usage, pane_cwd, pane_id):
        return self._discovery.pane_cwd_match(usage, pane_cwd, pane_id)

    def _observe(self, path, mode, *, checkpoint=None, seen=None):
        # Discovery and application ownership remain in the existing app. All
        # these synchronous entrypoints already run on its reader executors.
        from .app import get_terminals
        from .cli_runtime import RustTerminalService
        service = get_terminals()
        if not isinstance(service, RustTerminalService) or not service._loop.is_running():
            raise RuntimeError("Selected native observation unavailable; no Python parser fallback")
        try:
            if asyncio.get_running_loop() is service._loop:
                raise RuntimeError("Native synchronous reader must run on the existing scan executor")
        except RuntimeError as error:
            if str(error) != "no running event loop":
                raise
        cursor = dict(checkpoint or {})
        expected = b""
        invalid = False
        offset = int(cursor.get("offset") or 0)
        if offset and cursor.get("anchor"):
            with Path(path).open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if stat.st_size < offset:
                    invalid = True
                else:
                    start = max(0, offset - 512)
                    stream.seek(start)
                    expected = stream.read(offset - start)
                    invalid = hashlib.blake2b(expected, digest_size=8).hexdigest() != cursor["anchor"]
        future = asyncio.run_coroutine_threadsafe(service.observe_claude({
            "path": str(path), "mode": mode, "checkpoint": cursor, "seen": sorted(seen or set()),
            "expected_anchor_b64": base64.b64encode(expected).decode(), "anchor_invalid": invalid,
        }), service._loop)
        try:
            result = future.result(timeout=20)
        except TimeoutError:
            future.cancel()
            raise
        if result.get("error"):
            return result  # Activity applies native high-water before propagating this failure.
        raw_anchor = base64.b64decode(result.pop("anchor_b64"), validate=True)
        anchor = hashlib.blake2b(raw_anchor, digest_size=8).hexdigest() if raw_anchor else ""
        result["checkpoint"]["anchor"] = anchor
        for event in result["events"]:
            if "checkpoint" in event:
                event["checkpoint"]["anchor"] = anchor
        return result

    def parse_incremental(self, path, checkpoint):
        result = self._observe(path, "usage", checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**e) for e in result["events"]], result["checkpoint"])

    def parse_session_file(self, path, seen_keys):
        if not Path(path).exists():
            return []
        result = self._observe(path, "session_usage", seen=seen_keys)
        seen_keys.update(result["seen"])
        return [TokenUsage(**e) for e in result["events"]]

    def parse_activity(self, path, seen_keys):
        if not Path(path).exists():
            return []
        result = self._observe(path, "activity", seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result["seen"])
        if result.get("error"):
            raise RuntimeError(result["error"])
        events = [ActivityEvent(**e) for e in result["events"]]
        for event in events:
            if event.detail == "user" and event.text:
                event.text = user_prompt_text(event.text)
        return events

    def turns_for_session(self, path, session_id=""):
        if not Path(path).exists():
            return []
        result = self._observe(path, "turns")
        turns = []
        for row in result["turns"]:
            row["calls"] = [TurnCall(**call) for call in row["calls"]]
            turns.append(TurnUsage(**row))
        return turns


def reference_spec(spec):
    """Only explicit isolated evaluation selects native Claude interpretation."""
    if os.environ.get("NAVIDE_ACR_EVAL_RUNTIME") != "rust-shell":
        return spec
    if not os.environ.get("NAVIDE_REGRESSION_ROOT"):
        raise RuntimeError("Rust reference integration requires the isolated regression harness")
    return replace(spec, make_log_reader=lambda: NativeClaudeReader(spec.make_log_reader()))
