"""The one codec between the watcher's seen bag and the sidecar's typed observation state.

The watcher keeps one flat set of strings per file. The sidecar keeps two things for a file: the dedup keys it has
delivered, and a typed state (the high-water mark, the resume position, running totals, a row marker, the adapter's own
fields). The state travels as one opaque entry of the bag, `native_state::<parser_id>::<json>`, so the watcher can store,
sweep and persist it like any other key without knowing what is in it. Nothing else in the bag is state.

A bag written before this codec existed carries the state as sentinel keys (`act_hw::7`, `__lasttext__:...`). The vendor's
declaration lists those prefixes (`core.legacy_state`); this module is the only reader of that table. A legacy key is
read into its typed field once and dropped from the keys, so the next pass writes only the state entry.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

ENTRY = 'native_state::'


def _entry_prefix(parser_id: str) -> str:
    return f'{ENTRY}{parser_id}::'


def _set(state: dict, field: str, value: Any) -> None:
    node = state
    *parents, leaf = field.split('.')
    for name in parents:
        child = node.get(name)
        if not isinstance(child, dict):
            child = node[name] = {}
        node = child
    node[leaf] = value


#: The ranges the sidecar's typed state reads: a position or high-water mark is a signed 64-bit integer, a running total an
#: unsigned one. A legacy value outside them would make the sidecar refuse the whole state, so it is never migrated.
INT_MAX = 2**63 - 1
TOTAL_MAX = 2**64 - 1


class StateError(ValueError):
    """A state entry of this parser that cannot be read. The pass is refused and the caller's bag is left as it was:
    treating it as a first pass would replay every activity line or re-emit a whole cumulative total."""


def _decimal(raw: str, limit: int) -> int | None:
    """A plain ASCII decimal within 0..limit, or None."""
    if not (raw.isascii() and raw.isdecimal()):
        return None
    value = int(raw)
    return value if value <= limit else None


def _signed(raw: str) -> int | None:
    """An optionally negative ASCII decimal within a signed 64-bit integer, or None."""
    magnitude = _decimal(raw[1:] if raw.startswith('-') else raw, INT_MAX + 1)
    if magnitude is None:
        return None
    value = -magnitude if raw.startswith('-') else magnitude
    return value if value <= INT_MAX else None


def _id_set(raw: str) -> list[int] | None:
    """A JSON list of signed 64-bit integers (the spelling of a pending set), sorted, or None."""
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(loaded, list):
        return None
    ids = [_signed(str(item)) if isinstance(item, int) and not isinstance(item, bool) else None for item in loaded]
    return None if None in ids else sorted(set(ids))


#: The longest identity (`<dev>:<ino>`) or anchor (16 hex digits) a byte cursor may carry: the sidecar's state is bounded, and a longer
#: text is no cursor of ours.
_CURSOR_TEXT_MAX = 64


def _byte_cursor(raw: str) -> dict | None:
    """The offset, file identity and anchor of the byte cursor a tail reader kept (`log_readers.base.read_jsonl_tail`: a JSON object with
    `offset`, `identity`, `anchor` and `kind`), or None where the text is no such cursor. The sidecar proves it against the file."""
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    offset, identity, anchor = loaded.get('offset', 0), loaded.get('identity', ''), loaded.get('anchor', '')
    if not (isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= INT_MAX):
        return None
    if not all(isinstance(text, str) and len(text) <= _CURSOR_TEXT_MAX for text in (identity, anchor)):
        return None
    return {'offset': offset, 'identity': identity, 'anchor': anchor}


def _legacy_value(kind: str, raw: str) -> Any:
    """The typed value a legacy sentinel's text stands for, or None where the sentinel was not readable or is outside
    the range the sidecar reads."""
    if kind == 'text':
        return raw
    if kind == 'int':
        return _decimal(raw, INT_MAX)
    if kind == 'signed':
        return _signed(raw)
    if kind == 'set':
        return _id_set(raw)
    if kind == 'json':
        try:
            loaded = json.loads(raw)
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None
    if kind == 'json_text':
        try:
            loaded = json.loads(raw)
        except ValueError:
            return None
        return loaded if isinstance(loaded, str) else None
    if kind == 'byte_cursor':
        return _byte_cursor(raw)
    if kind == 'colon_pair':
        halves = raw.split(':')
        parts = dict(zip(('in', 'out'), halves)) if len(halves) == 2 else {}
    else:
        parts = dict(part.split('=', 1) for part in raw.split(',') if '=' in part)
    totals = {'input': _decimal(parts.get('in', ''), TOTAL_MAX), 'output': _decimal(parts.get('out', ''), TOTAL_MAX)}
    return None if None in totals.values() else totals


def unpack(seen: Iterable[str] | None, parser_id: str, legacy) -> tuple[list[str], dict]:
    """The dedup keys and the typed state a seen bag holds.

    A state entry is read as it is; one that does not parse raises `StateError` (the pass is refused, the bag is kept). A
    legacy sentinel replaces its field (an integer takes the largest when a bag holds several, because the watcher could
    lower one); an unreadable or out-of-range sentinel is dropped, since it was never a dedup key."""
    prefix = _entry_prefix(parser_id)
    state: dict = {}
    migrated: dict[str, Any] = {}
    keys: list[str] = []
    for key in sorted(seen or ()):
        if key.startswith(prefix):
            try:
                loaded = json.loads(key[len(prefix):])
            except ValueError as error:
                raise StateError(f'observation state does not parse: {error}') from error
            if not isinstance(loaded, dict):
                raise StateError('observation state does not parse: not an object')
            state = loaded
            continue
        rule = next((rule for rule in legacy if key.startswith(rule.prefix)), None)
        if rule is None:
            keys.append(key)
            continue
        value = _legacy_value(rule.kind, key[len(rule.prefix):])
        if value is None:
            continue
        if rule.kind in ('int', 'signed'):
            value = max(value, migrated.get(rule.field, value))
        migrated[rule.field] = value
    for field, value in migrated.items():
        _set(state, field, value)
    return keys, state


def pack(keys: Iterable[str], state: dict | None, parser_id: str) -> set[str]:
    """The seen bag for the dedup keys and state a pass answered with."""
    bag = set(keys)
    if state:
        bag.add(_entry_prefix(parser_id) + json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
    return bag
