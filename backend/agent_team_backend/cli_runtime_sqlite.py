"""Native observation of the SQLite-store vendors goose, copilot and kilo (G2 ticket 06, family D).

Each `Native<Vendor>Reader` keeps discovery, the file-name gates, attribution and the sentinel keys of its Python
baseline reader and replaces only the store and file reading with `observe(...)` calls. Kilo is OpenCode's fork and
shares its native reader.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from . import osplat
from .cli_runtime_observation import observe
from .cli_runtime_opencode import NativeOpenCodeReader
from .cli_vendors import copilot as _copilot
from .cli_vendors import goose as _goose
from .cli_vendors.kilo import KiloLogReader
from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, TokenUsage, activity_high_water, set_activity_high_water, user_prompt_text,
)


def _selected(spec, reader):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell' or not osplat.has_native_vendor_observer():
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=reader)


class NativeKiloReader(NativeOpenCodeReader, KiloLogReader):
    """`kilo.db` has OpenCode's schema; the vendor key selects the store file and names the parser."""


class NativeGooseReader(_goose.GooseLogReader):
    def _native(self, path, mode, **kwargs):
        return observe(self.vendor, 'rust-goose-sqlite-v1', Path(path), mode, **kwargs)

    def has_session(self, session_id):
        session_id = session_id.strip()
        db = self._db_path()
        if not session_id or not db.is_file():
            return False
        return self._native(db, 'has_session', wanted_session=session_id)['found']

    def find_sessions_by_marker(self, markers):
        wanted = [marker for marker in markers if marker]
        db = self._db_path()
        if not wanted or not db.is_file():
            return {}
        return {marker: tuple(binding) for marker, binding in
                self._native(db, 'identify', markers=wanted)['markers'].items()}

    def parse_session_file(self, path, seen_keys):
        if path.name != _goose._DB_NAME:
            return []
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_incremental(self, path, checkpoint):
        if path.name != _goose._DB_NAME:
            return IncrementalParseResult([], dict(checkpoint))
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_activity(self, path, seen_keys):
        if path.name != _goose._DB_NAME:
            return []
        result = self._native(path, 'activity', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        return events


class NativeCopilotReader(_copilot.CopilotLogReader):
    """The store and `events.jsonl` are read natively; `workspace.yaml` (`yaml.safe_load`), the session directory
    name and the "the store owns this session" gate stay Python."""

    def _native(self, path, mode, **kwargs):
        path = Path(path)
        result = observe(self.vendor, 'rust-copilot-jsonl-v1', path, mode, cwd=self.cwd_from_file(path),
                         session=path.parent.name, **kwargs)
        if result.get('error'):
            _copilot._warn_db_once(path, result['error'])
        return result

    def _skipped(self, path):
        return path.name != _copilot._DB_NAME and (path.name != 'events.jsonl' or self._db_owns_session(path))

    def parse_incremental(self, path, checkpoint):
        if self._skipped(path):
            return IncrementalParseResult([], dict(checkpoint))
        result = self._native(path, 'usage', checkpoint=checkpoint)
        if result.get('error'):
            return IncrementalParseResult([], dict(checkpoint))
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        if self._skipped(path):
            return []
        result = self._native(path, 'session_usage', seen=seen_keys)
        if result.get('error'):
            return []
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        if self._skipped(path):
            return []
        if path.name == _copilot._DB_NAME:
            result = self._native(path, 'activity', seen=seen_keys)
            seen_keys.clear()
            seen_keys.update(result['seen'])
        else:
            result = self._native(path, 'activity', high_water=activity_high_water(seen_keys),
                                  last_text=_copilot._read_last_text(seen_keys))
            if result['opened']:
                set_activity_high_water(seen_keys, result['high_water'])
                if result['text_changed']:
                    _copilot._write_last_text(seen_keys, result['last_text'])
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        return events


def goose_reference(spec):
    return _selected(spec, NativeGooseReader)


def copilot_reference(spec):
    return _selected(spec, NativeCopilotReader)


def kilo_reference(spec):
    return _selected(spec, NativeKiloReader)
