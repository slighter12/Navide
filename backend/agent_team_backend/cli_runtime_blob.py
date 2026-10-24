"""Native observation of the BLOB-store vendors cursor and antigravity (G2 ticket 06, family E).

Both keep discovery, workspace resolution and attribution of their Python baseline readers; only the store reads move
to `observe(...)`. Neither vendor records token usage, so `parse_session_file` and `parse_incremental` stay the
baseline's empty answers.
"""
from __future__ import annotations

from pathlib import Path

from .cli_runtime_observation import observe
from .cli_runtime_sqlite import _selected
from .cli_vendors import antigravity as _antigravity
from .cli_vendors import cursor as _cursor
from .log_readers.base import ActivityEvent, user_prompt_text


def _activity(result, seen_keys):
    seen_keys.clear()
    seen_keys.update(result['seen'])
    events = [ActivityEvent(**event) for event in result['events']]
    for event in events:
        if event.detail == 'user':
            event.text = user_prompt_text(event.text)
    return events


class NativeCursorReader(_cursor.CursorLogReader):
    def _native(self, path, mode, **kwargs):
        return observe(self.vendor, 'rust-cursor-sqlite-v1', Path(path), mode, **kwargs)

    def parse_activity(self, path, seen_keys):
        session_id = self.session_id_from_path(path)
        if not session_id:
            return []
        result = self._native(path, 'activity', seen=seen_keys, cwd=self.cwd_from_file(path), session=session_id,
                              base_epoch=self._store_epoch(path))
        return _activity(result, seen_keys)

    def find_sessions_by_marker(self, markers):
        wanted = [marker for marker in markers if marker]
        found = {}
        if not wanted:
            return found
        for db in self.session_files():
            if len(found) == len(wanted):
                break
            session_id = self.session_id_from_path(db)
            for marker in self._native(db, 'identify', markers=wanted)['markers']:
                found.setdefault(marker, (session_id, ''))
        return found


class NativeAntigravityReader(_antigravity.AntigravityLogReader):
    def _native(self, path, mode, **kwargs):
        return observe(self.vendor, 'rust-antigravity-sqlite-v1', Path(path), mode, **kwargs)

    def parse_activity(self, path, seen_keys):
        session_id = self.session_id_from_path(path)
        if not session_id:
            return []
        result = self._native(path, 'activity', seen=seen_keys, cwd=self.cwd_from_file(path), session=session_id)
        return _activity(result, seen_keys)


def cursor_reference(spec):
    return _selected(spec, NativeCursorReader)


def antigravity_reference(spec):
    return _selected(spec, NativeAntigravityReader)
