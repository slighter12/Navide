"""Synchronous executor bridge for composed native observers, not app policy."""
from __future__ import annotations

import asyncio
import base64
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
    future = asyncio.run_coroutine_threadsafe(service.observe_native(vendor, parser, {
        'path': str(path), 'mode': mode, 'checkpoint': cursor, 'seen': sorted(seen or set()),
        'expected_anchor_b64': base64.b64encode(expected).decode(), 'anchor_invalid': invalid, **extra,
    }, pane_id=pane_id), service._loop)
    try:
        result = future.result(timeout=20)
    except TimeoutError:
        future.cancel()
        raise
    if 'anchor_b64' in result and not result.get('error'):
        raw = base64.b64decode(result.pop('anchor_b64'), validate=True)
        anchor = hashlib.blake2b(raw, digest_size=8).hexdigest() if raw else ''
        result['checkpoint']['anchor'] = anchor
        for event in result['events']:
            if 'checkpoint' in event:
                event['checkpoint']['anchor'] = anchor
    return result
