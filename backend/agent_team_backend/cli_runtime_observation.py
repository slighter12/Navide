"""Synchronous executor bridge for composed native observers, not app policy."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path


def observe(vendor, parser, path, mode, *, checkpoint=None, seen=None, pane_id=None, **extra):
    from .app import get_terminals
    from .cli_runtime import RustTerminalService
    service = get_terminals()
    if not isinstance(service, RustTerminalService) or not service._loop.is_running():
        raise RuntimeError('Selected native observation unavailable; no Python parser fallback')
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is service._loop:
        raise RuntimeError('Native synchronous observation requires the existing scan executor')
    cursor = dict(checkpoint or {})
    expected = b''
    invalid = False
    offset = int(cursor.get('offset') or 0)
    if offset and cursor.get('anchor'):
        with Path(path).open('rb') as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_size < offset:
                invalid = True
            else:
                start = max(0, offset - 512)
                stream.seek(start)
                expected = stream.read(offset - start)
                invalid = hashlib.blake2b(expected, digest_size=8).hexdigest() != cursor['anchor']
    request = {'path': str(path), 'mode': mode, 'checkpoint': cursor, 'seen': sorted(seen or set()),
               'anchor_invalid': invalid, **extra}
    future = asyncio.run_coroutine_threadsafe(service.observe(
        vendor, parser, request, expected=expected, match=_match_pane(vendor, pane_id), receipt=_receipt),
        service._loop)
    try:
        result = future.result(timeout=OBSERVE_RESULT_S)
    except TimeoutError:
        future.cancel()
        raise
    if 'anchor_blob' in result and not result.get('error'):
        raw = result.pop('anchor_blob')
        anchor = hashlib.blake2b(raw, digest_size=8).hexdigest() if raw else ''
        result['checkpoint']['anchor'] = anchor
        for event in result['events']:
            if 'checkpoint' in event:
                event['checkpoint']['anchor'] = anchor
    result.pop('anchor_blob', None)
    return result


#: The synchronous scan executors' wait for one observation (a bound on a blocked call, not a poll).
OBSERVE_RESULT_S = 20


def _match_pane(vendor, pane_id):
    """The live session an observation of `vendor` for `pane_id` belongs to, if any."""
    def match(sessions):
        return next((s for s in sessions if pane_id and s.pane_id == pane_id
                     and s.agent_key == vendor and not s.closed and not s.hidden), None)
    return match


def _receipt(result):
    return {**({'native_session_id': result['native_session_id']} if 'native_session_id' in result else {}),
            'checkpoint': result['checkpoint'], 'seen': result['seen'],
            'event_count': len(result['events']),
            'native_sessions': sorted({event['session_id'] for event in result['events']})}
