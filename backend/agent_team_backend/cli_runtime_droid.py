"""Native observation of Droid (G2 ticket 06, family B), composed with Python discovery.

Discovery, the `session_start` cwd, the sidecar path and the sentinel keys of the seen bag stay Python. Usage
reaches the sink through the base `parse_incremental`, which hands the legacy seen set to `parse_session_file`.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from .cli_runtime_observation import observe
from .cli_vendors import droid as _droid
from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, TokenUsage, activity_high_water, set_activity_high_water, user_prompt_text,
)


class NativeDroidReader(_droid.DroidLogReader):
    def _native(self, path, mode, **kwargs):
        path = Path(path)
        return observe(self.vendor, 'rust-droid-jsonl-v1', path, mode, cwd=self.cwd_from_file(path),
                       session=path.stem, settings_path=str(_droid._session_settings_path(path)), **kwargs)

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        last_text = next((key[len(_droid._TEXT_MARK_PREFIX):] for key in seen_keys
                          if key.startswith(_droid._TEXT_MARK_PREFIX)), '')
        result = self._native(path, 'activity', high_water=activity_high_water(seen_keys), last_text=last_text)
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user' and event.text:
                event.text = user_prompt_text(event.text)
        set_activity_high_water(seen_keys, result['high_water'])
        seen_keys.difference_update({key for key in seen_keys if key.startswith(_droid._TEXT_MARK_PREFIX)})
        if result['last_text']:
            seen_keys.add(_droid._TEXT_MARK_PREFIX + result['last_text'])
        return events


def reference_spec(spec):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell':
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=NativeDroidReader)
