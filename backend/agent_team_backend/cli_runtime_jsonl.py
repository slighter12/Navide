"""Native observation of the per-session JSONL vendors (G2 ticket 06, family A), composed with Python discovery.

Each `Native<Vendor>Reader` keeps discovery, `claims_path`, workspace and pane matching and the attribution flags of
its Python baseline reader, and replaces only the file-reading methods with `observe(...)` calls.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

from . import osplat
from .cli_runtime_observation import observe
from .cli_vendors import grok as _grok
from .cli_vendors import kimi as _kimi
from .cli_vendors import muse as _muse
from .cli_vendors import pi as _pi
from .cli_vendors import qwen as _qwen
from .log_readers.base import (
    ActivityEvent, IncrementalParseResult, TokenUsage, TurnCall, TurnUsage, activity_high_water, join_text_blocks,
    merge_prompt_excerpt, set_activity_high_water, user_prompt_text,
)


def _selected(spec, reader):
    if os.environ.get('NAVIDE_ACR_EVAL_RUNTIME') != 'rust-shell' or not osplat.has_native_vendor_observer():
        return spec
    if not os.environ.get('NAVIDE_REGRESSION_ROOT'):
        raise RuntimeError('Rust native differences require the isolated regression harness')
    return replace(spec, make_log_reader=reader)


class NativeGrokReader(_grok.GrokLogReader):
    """Reuse discovery mechanics; never reuse the legacy record interpreter."""

    def _native(self, path, mode, **kwargs):
        return observe(self.vendor, 'rust-grok-jsonl-v1', path, mode, cwd=self.cwd_from_file(Path(path)), **kwargs)

    def parse_incremental(self, path, checkpoint):
        if path.name != _grok._TRANSCRIPT:
            return IncrementalParseResult([], dict(checkpoint))
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        if path.name != _grok._TRANSCRIPT:
            return []
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        if path.name != _grok._TRANSCRIPT:
            return []
        cursor = _grok._read_sentinel(seen_keys, _grok._ACT_CURSOR_PREFIX)
        pending = str(_grok._read_sentinel(seen_keys, _grok._ACT_TEXT_PREFIX) or '')
        result = self._native(path, 'activity', checkpoint=cursor if isinstance(cursor, dict) else {}, pending=pending)
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        _grok._write_sentinel(seen_keys, _grok._ACT_CURSOR_PREFIX, result['checkpoint'])
        _grok._write_sentinel(seen_keys, _grok._ACT_TEXT_PREFIX, result['pending'])
        return events

    def turns_for_session(self, path, session_id=''):
        if path.name != _grok._TRANSCRIPT:
            return []
        turns = []
        for raw in self._native(path, 'turns', session_filter=session_id)['turns']:
            excerpt = ''
            for chunk in raw['chunks']:
                excerpt = merge_prompt_excerpt(excerpt, chunk)
            turn = TurnUsage(turn_index=0, session_id=raw['session_id'], started_at=raw['started_at'],
                             ended_at=raw['ended_at'], prompt_excerpt=excerpt, model_calls=raw['model_calls'] or None)
            for model, counts in raw['parts']:
                if not isinstance(counts, dict):
                    continue
                cache_read = _grok._int(counts.get('cachedReadTokens'))
                cache_creation = _grok._int(counts.get('cacheCreationTokens'))
                turn.add_call(TurnCall(
                    ts=raw['ended_at'], model=str(model),
                    input=max(0, _grok._int(counts.get('inputTokens')) - cache_read - cache_creation),
                    cache_read=cache_read, cache_creation=cache_creation, output=_grok._int(counts.get('outputTokens'))))
            if turn.total == 0:
                continue
            turns.append(turn)
        for number, turn in enumerate(turns, 1):
            turn.turn_index = number
        return turns


class NativeKimiReader(_kimi.KimiLogReader):
    """Discovery, `state.json` cwd and the sentinel keys stay Python; the record reading is native."""

    def _native(self, path, mode, **kwargs):
        path = Path(path)
        return observe(self.vendor, 'rust-kimi-jsonl-v1', path, mode, cwd=self.cwd_from_file(path),
                       session=self._session_id(path), **kwargs)

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        if result.get('error'):
            raise RuntimeError(result['error'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(
            path, 'activity', high_water=activity_high_water(seen_keys), state=_kimi._read_turn_state(seen_keys),
            last_text=_kimi._read_last_text(seen_keys), now_ms=int(time.time() * 1000))
        events = []
        for event in result['events']:
            raw_input = event.pop('input', None)
            if event['detail'] == 'prompt':
                event['text'] = user_prompt_text(join_text_blocks(raw_input, 'text'))
            events.append(ActivityEvent(**event))
        state = result['state']
        # Python's insertion order, which is what its sentinel key spells out.
        ordered = {key: state[key] for key in ('idx', 'last_ms', 'flushed', 'cancelled') if key in state} if state else None
        set_activity_high_water(seen_keys, result['high_water'])
        _kimi._write_turn_state(seen_keys, ordered)
        _kimi._write_last_text(seen_keys, result['last_text'])
        if result.get('error'):
            raise RuntimeError(result['error'])
        return events


class NativeMuseReader(_muse.MuseLogReader):
    """Discovery, the metadata head read and the sentinel keys stay Python; the record reading is native."""

    def _native(self, path, mode, **kwargs):
        path = Path(path)
        return observe(self.vendor, 'rust-muse-jsonl-v1', path, mode, cwd=self.cwd_from_file(path),
                       cli_version=self.version_from_file(path), session=self.session_id_from_path(path), **kwargs)

    def parse_incremental(self, path, checkpoint):
        if not _muse._is_main_session_file(path):
            return IncrementalParseResult([], dict(checkpoint))
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        if not _muse._is_main_session_file(path):
            return []
        result = self._native(path, 'activity', high_water=activity_high_water(seen_keys),
                              last_text=_muse._read_sentinel(seen_keys, _muse._TEXT_PREFIX))
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        set_activity_high_water(seen_keys, result['high_water'])
        _muse._write_sentinel(seen_keys, _muse._TEXT_PREFIX, result['last_text'])
        return events


def _turn_state(seen_keys, prefix):
    """The `{idx, flushed, ts}` turn state a pi/qwen reader keeps in its seen_keys bag, or None."""
    raw = _pi._read_sentinel(seen_keys, prefix)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _turn_state_text(state):
    """`json.dumps(state)` in Python's insertion order, which is what its sentinel key spells out."""
    return json.dumps({key: state[key] for key in ('idx', 'flushed', 'ts') if key in state}) if state is not None else ''


class NativePiReader(_pi.PiLogReader):
    """Discovery, the line-1 header and the sentinel keys stay Python; the record reading is native."""

    def _native(self, path, mode, **kwargs):
        path = Path(path)
        header = self._header(path)
        return observe(self.vendor, 'rust-pi-jsonl-v1', path, mode, cwd=str(header.get('cwd') or ''),
                       session=str(header.get('id') or '') or self._fallback_id(path), **kwargs)

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(
            path, 'activity', high_water=activity_high_water(seen_keys), state=_turn_state(seen_keys, _pi._STATE_PREFIX),
            last_text=_pi._read_sentinel(seen_keys, _pi._TEXT_PREFIX), now=time.time())
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        set_activity_high_water(seen_keys, result['high_water'])
        _pi._write_sentinel(seen_keys, _pi._STATE_PREFIX, _turn_state_text(result['state']))
        _pi._write_sentinel(seen_keys, _pi._TEXT_PREFIX, result['last_text'])
        return events


class NativeQwenReader(_qwen.QwenLogReader):
    """Discovery, the first-line cwd and the sentinel keys stay Python; the record reading is native."""

    def _native(self, path, mode, **kwargs):
        path = Path(path)
        return observe(self.vendor, 'rust-qwen-jsonl-v1', path, mode, cwd=self.cwd_from_file(path),
                       session=path.stem, **kwargs)

    def parse_incremental(self, path, checkpoint):
        result = self._native(path, 'usage', checkpoint=checkpoint)
        return IncrementalParseResult([TokenUsage(**event) for event in result['events']], result['checkpoint'])

    def parse_session_file(self, path, seen_keys):
        result = self._native(path, 'session_usage', seen=seen_keys)
        seen_keys.clear()
        seen_keys.update(result['seen'])
        if result.get('error'):
            raise RuntimeError(result['error'])
        return [TokenUsage(**event) for event in result['events']]

    def parse_activity(self, path, seen_keys):
        result = self._native(
            path, 'activity', high_water=activity_high_water(seen_keys),
            state=_turn_state(seen_keys, _qwen._STATE_PREFIX),
            last_text=_qwen._read_sentinel(seen_keys, _qwen._TEXT_PREFIX), now=time.time())
        events = [ActivityEvent(**event) for event in result['events']]
        for event in events:
            if event.detail == 'user':
                event.text = user_prompt_text(event.text)
        set_activity_high_water(seen_keys, result['high_water'])
        _qwen._write_sentinel(seen_keys, _qwen._STATE_PREFIX, _turn_state_text(result['state']))
        _qwen._write_sentinel(seen_keys, _qwen._TEXT_PREFIX, result['last_text'])
        if result.get('error'):
            raise RuntimeError(result['error'])
        return events


def grok_reference(spec):
    return _selected(spec, NativeGrokReader)


def kimi_reference(spec):
    return _selected(spec, NativeKimiReader)


def muse_reference(spec):
    return _selected(spec, NativeMuseReader)


def pi_reference(spec):
    return _selected(spec, NativePiReader)


def qwen_reference(spec):
    return _selected(spec, NativeQwenReader)
