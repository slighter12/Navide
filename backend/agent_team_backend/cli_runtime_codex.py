"""Bounded Codex native interpretation composed with existing discovery/policy."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from . import osplat
from .cli_runtime_observation import observe
from .log_readers.base import ActivityEvent, IncrementalParseResult, TokenUsage, user_prompt_text
from .cli_vendors.codex import CodexLogReader


class NativeCodexReader(CodexLogReader):
    """Reuse discovery mechanics; never reuse the legacy record interpreter."""

    def _native(self, path, mode, **kwargs):
        return observe(self.vendor, 'rust-codex-jsonl-v1', path, mode,
                       pane_id=self.pane_home_id(str(path)), **kwargs)

    def _cwd_from_meta(self, path):
        return self._native(path, 'identify')['cwd']

    def path_identity(self, usage):
        pane = self.pane_home_id(usage.file_path)
        if not pane:
            return None
        result = self._native(Path(usage.file_path), 'identify')
        return pane, result['native_session_id']

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(path, 'activity', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        if result.get('error'):
            raise RuntimeError(result['error'])
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user_message':
                event.text = user_prompt_text(event.text)
        return events

    def turns_for_session(self, path, session_id=''):
        raise RuntimeError('Native Codex turn breakdown not yet integrated; no Python parser fallback')


def reference_spec(spec):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell' or not osplat.has_native_vendor_observer():
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=NativeCodexReader)
