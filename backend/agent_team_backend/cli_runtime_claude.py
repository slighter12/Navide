"""Compose native interpretation with Claude's retained discovery interface."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
import hashlib
import os
from pathlib import Path

from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, LogReader, TokenUsage, TurnCall,
    TurnUsage, tail_anchor, user_prompt_text,
)


PARSER_ID = "rust-claude-jsonl-v1"
#: The synchronous scan executors' wait for one observation (a bound on a blocked call, not a poll).
OBSERVE_RESULT_S = 20


def _match_session(path):
    """The live Claude session whose conversation file this is (Claude's session-stem rule)."""
    def match(sessions):
        from .cli_vendors.registry import vendor
        spec = vendor("claude")
        return next((s for s in sessions if s.agent_key == "claude"
                     and (s.metadata.get("explicit_session_id") or spec.resume_id_from_command(s.command)) == path.stem
                     and not s.closed and not s.hidden), None)
    return match


def _validate(args):
    """Claude's invariants beyond the shared ones: the session stem and activity high-water progress."""
    def validate(result, session):
        path = Path(args["path"])
        if result.get("native_session_id") != path.stem:
            raise RuntimeError("PROTOCOL_ERROR: wrong observation session")
        if result.get("error"):
            seen = result.get("seen")
            previous = set(args.get("seen", []))
            if not isinstance(seen, list) or not all(isinstance(key, str) for key in seen):
                raise RuntimeError("PROTOCOL_ERROR: invalid activity seen progress")
            markers = [key for key in seen if key.startswith("act_hw::")]
            old_water = max((int(key[8:]) for key in previous if key.startswith("act_hw::")), default=0)
            if (len(markers) != 1 or not markers[0][8:].isascii() or not markers[0][8:].isdecimal()
                    or int(markers[0][8:]) <= old_water
                    or {key for key in seen if not key.startswith(("act_hw::", "act_pos::"))}
                    != {key for key in previous if not key.startswith(("act_hw::", "act_pos::"))}
                    or result["events"]):
                raise RuntimeError("PROTOCOL_ERROR: invalid activity high-water progress")
    return validate


def _receipt(result):
    return {"native_session_id": result["native_session_id"], "checkpoint_offset": result["checkpoint"].get("offset"),
            "seen": result["seen"], "event_count": len(result["events"])}


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
        request = {"path": str(path), "mode": mode, "checkpoint": cursor, "seen": sorted(seen or set()),
                   "anchor_invalid": invalid}
        future = asyncio.run_coroutine_threadsafe(service.observe(
            "claude", PARSER_ID, request, expected=expected, match=_match_session(Path(path)),
            validate=_validate(request), receipt=_receipt), service._loop)
        try:
            result = future.result(timeout=OBSERVE_RESULT_S)
        except TimeoutError:
            future.cancel()
            raise
        if result.get("error"):
            return result  # Activity applies native high-water before propagating this failure.
        raw_anchor = result.pop("anchor_blob")
        result["checkpoint"]["anchor"] = hashlib.blake2b(raw_anchor, digest_size=8).hexdigest() if raw_anchor else ""
        if mode == "usage":
            # Each event gets its own cursor: the result's, ended at the event, with the
            # recent-key window as it stood after it.
            if result.get("usage_cursor_schema") != 2:
                raise RuntimeError("PROTOCOL_ERROR: incompatible Claude usage cursor")
            recent_keys = [] if result["usage_reset"] else cursor.get("recent_keys", [])
            recent = deque([str(key) for key in recent_keys][-64:], maxlen=64)
            for event in result["events"]:
                end = event.pop("cursor_end")
                recent.append(event["dedup_key"])
                event_checkpoint = dict(result["checkpoint"])
                event_checkpoint["offset"] = end
                event_checkpoint["recent_keys"] = list(recent)
                event["checkpoint"] = event_checkpoint
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
