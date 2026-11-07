"""Synchronous executor bridge for composed native observers, not app policy."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

#: The synchronous scan executors' wait for one observation (a bound on a blocked call, not a poll).
OBSERVE_RESULT_S = 20


def _submit(vendor, parser, request, *, match, validate=None, receipt):
    """Send one OBSERVE request from a scan executor and wait for its answer."""
    from .app import get_terminals
    service = get_terminals()
    if not service._loop.is_running():
        raise RuntimeError('Selected native observation unavailable; no Python parser fallback')
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is service._loop:
        raise RuntimeError('Native synchronous observation requires the existing scan executor')
    future = asyncio.run_coroutine_threadsafe(service.observe(
        vendor, parser, request, match=match, validate=validate, receipt=receipt), service._loop)
    try:
        return future.result(timeout=OBSERVE_RESULT_S)
    except TimeoutError:
        future.cancel()
        raise


#: What the frame adds to a request's meta beyond the request itself (the vendor, the session handle): the guard keeps this much free.
_META_MARGIN = 4096


def request_of(path, mode, *, checkpoint=None, seen=None, state=None, **extra) -> dict:
    """The meta of one observation request."""
    return {'path': str(path), 'mode': mode, 'checkpoint': dict(checkpoint or {}), 'seen': sorted(seen or set()),
            'state': dict(state or {}), **extra}


def admit(vendor, request) -> None:
    """Refuse, loudly, a request the sidecar would drop the connection on: its meta over `MAX_META_P2R`. A bag that large is a
    defect of the caller that kept it (the replays send the bag as it was when their call began, and no caller keeps one across
    calls), so the item is stopped and reported, never read partially."""
    from .cli_runtime_frames import MAX_META_P2R
    size = len(json.dumps({'vendor': vendor, **request}, ensure_ascii=False, separators=(',', ':')).encode())
    if size > MAX_META_P2R - _META_MARGIN:
        message = (f'REQUEST_TOO_LARGE: {vendor} {request["mode"]} request for {Path(request["path"]).name} is {size} bytes of meta; '
                   f'the cap is {MAX_META_P2R} (seen: {len(request["seen"])} keys)')
        _log.error(message)
        raise RuntimeError(message)


def observe_core(vendor, path, mode, *, checkpoint=None, seen=None, state=None, pane_id=None, match=None,
                 validate=None, receipt=None, **extra):
    """One observation by a parser on the native core: the sidecar verifies the checkpoint's anchor itself, so this
    side neither opens the file nor hashes it, and the answer's checkpoint already carries its anchor. `seen` holds
    dedup keys only; the typed state travels beside it (see `cli_runtime_state`)."""
    from .cli_vendors._vendor_facts import FACTS
    request = request_of(path, mode, checkpoint=checkpoint, seen=seen, state=state, **extra)
    # The one request this sends is the one that is checked: no caller can reach the sidecar around the guard.
    admit(vendor, request)
    return _submit(vendor, FACTS[vendor].observer.parser_id, request, match=match or _match_pane(vendor, pane_id),
                   validate=validate, receipt=receipt or _receipt)


def _match_pane(vendor, pane_id):
    """The live session an observation of `vendor` for `pane_id` belongs to, if any."""
    def match(sessions):
        return next((s for s in sessions if pane_id and s.pane_id == pane_id
                     and s.agent_key == vendor and not s.closed and not s.hidden), None)
    return match


def _receipt(result):
    return {**({'native_session_id': result['native_session_id']} if 'native_session_id' in result else {}),
            'checkpoint': result['checkpoint'], 'seen': result['seen'], 'state': result.get('state'),
            **({'dropped_pending': result['dropped_pending']} if result.get('dropped_pending') else {}),
            'event_count': len(result['events']),
            'native_sessions': sorted({event['session_id'] for event in result['events']})}
