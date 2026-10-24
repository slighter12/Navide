"""Native observation of Aider (G2 ticket 06, family C), composed with Python discovery.

Discovery, the history file's namespace, marker scanning and the last-section reads stay Python; the line reading
of the Markdown history is native. The wrapper stripping of a prompt line (`user_prompt_text`) stays Python.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from .cli_runtime_observation import observe
from .cli_vendors import aider as _aider
from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, TokenUsage, activity_high_water, set_activity_high_water, user_prompt_text,
)


class NativeAiderReader(_aider.AiderLogReader):
    def _native(self, path, mode, **kwargs):
        path = Path(path)
        return observe(self.vendor, 'rust-aider-md-v1', path, mode, cwd=self.cwd_from_file(path),
                       namespace=_aider.history_namespace(path), **kwargs)

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(path, 'activity', high_water=activity_high_water(seen_keys),
                              pending=_aider._read_pending_text(seen_keys))
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'prompt':
                event.text = user_prompt_text(event.text)
        set_activity_high_water(seen_keys, result['high_water'])
        _aider._write_pending_text(seen_keys, result['pending'])
        return events


def reference_spec(spec):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell':
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=NativeAiderReader)
