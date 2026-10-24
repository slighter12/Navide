"""Native SQLite interpretation; reuse existing discovery/attribution metadata."""
from __future__ import annotations

import os
from dataclasses import replace

from .cli_runtime_observation import observe
from .cli_vendors.opencode import OpencodeLogReader
from .log_readers.base import ActivityEvent, IncrementalParseResult, TokenUsage, user_prompt_text


class NativeOpenCodeReader(OpencodeLogReader):
    def _native(self, path, mode, **kwargs):
        # OpenCode and its fork Kilo share this reader; the vendor key names the parser and the store file.
        return observe(self.vendor, f'rust-{self.vendor}-sqlite-v1', path, mode, **kwargs)

    def has_session(self, session_id):
        session_id = session_id.strip()
        if not session_id:
            return False
        return any(path.is_file() and self._native(path, 'has_session', session_id=session_id)['found']
                   for path in self._db_paths())

    def find_sessions_by_marker(self, markers):
        markers = [marker for marker in markers if marker]
        if not markers:
            return {}
        found = {}
        for path in self._db_paths():
            if path.is_file():
                for marker, binding in self._native(path, 'identify', markers=markers)['markers'].items():
                    found.setdefault(marker, tuple(binding))
        return found

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**row) for row in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'legacy', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**row) for row in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(path, 'activity', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        events = [ActivityEvent(**row) for row in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        return events


def reference_spec(spec):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell':
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=NativeOpenCodeReader)
