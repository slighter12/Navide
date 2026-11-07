"""One generic native reader for every parser on the observation core.

The sidecar does all the reading: it verifies the checkpoint's anchor, tails the log and answers with events, a
checkpoint, the dedup keys and a typed state. This side keeps what is Python's, and every choice it makes is read from
the vendor's declaration (`FACTS[vendor].observer.core`), never from a vendor name:

- `python.prompt_detail`, `python.pane_scope`, `python.attribution`, `python.identity_source`: the vocabulary and
  attribution of the discovery reader the observation is mixed into;
- `modes`: which replay mode and which turn breakdown the sidecar answers;
- `compat`: named rules that keep a behaviour of the baseline reader (each is pinned by a `test_compat_*` test).

The vendor's own discovery reader class supplies everything else, so a vendor on an existing source kind adds a
declaration and an adapter, not a reader.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path

from . import cli_runtime_hook
from .cli_runtime_observation import observe_core
from .cli_runtime_state import pack, unpack
from .cli_vendors._vendor_facts import FACTS, WANTED_SESSION, Mode
from .log_readers.base import ActivityEvent, IncrementalParseResult, TokenUsage, TurnCall, TurnUsage, user_prompt_text

_log = logging.getLogger(__name__)

#: How many times a paged `turns` breakdown may start over because the log changed between its pages.
_TURN_RESTARTS = 3


def _by_file_stem(vendor: str, path: Path):
    """The live session whose conversation file this is: the vendor names its session by the file stem."""
    def match(sessions):
        from .cli_vendors.registry import vendor as spec_of
        spec = spec_of(vendor)
        return next((s for s in sessions if s.agent_key == vendor
                     and (s.metadata.get('explicit_session_id') or spec.resume_id_from_command(s.command)) == path.stem
                     and not s.closed and not s.hidden), None)
    return match


def _checked(path: Path, core, layout, keys: list[str], state: dict, mode: Mode):
    """The answer checks every observation carries, from the declaration.

    A vendor that names its session by the file stem must answer with that stem. A failed activity pass over a log must
    still have moved its high-water mark forward without delivering events or touching a dedup key; a failed pass over a
    store has no line mark to move (`compat.store_error_reported` carries its error instead)."""
    def validate(result, session):
        if core.python.attribution == 'file_stem' and result.get('native_session_id') != path.stem:
            raise RuntimeError('PROTOCOL_ERROR: wrong observation session')
        if mode == Mode.ACTIVITY and result.get('error') and layout is not None and layout.source in ('jsonl', 'text'):
            seen, now = result.get('seen'), result.get('state')
            water = now.get('water') if isinstance(now, dict) else None
            if not isinstance(seen, list) or not all(isinstance(key, str) for key in seen):
                raise RuntimeError('PROTOCOL_ERROR: invalid activity seen progress')
            # An answer carries only the dedup keys it delivered; a failed pass delivered none.
            if (not isinstance(water, int) or isinstance(water, bool) or water <= int(state.get('water') or 0)
                    or seen or result['events']):
                raise RuntimeError('PROTOCOL_ERROR: invalid activity high-water progress')
    return validate


class NativeObservation:
    """The observed methods of a log reader; mixed in before the vendor's own discovery reader class."""

    def _observer(self):
        return FACTS[self.vendor].observer

    def _candidates(self, path):
        name = Path(path).name
        return [(slot, layout) for slot, layout in enumerate(self._observer().layouts) if layout.store_file in (name, None)]

    def _layout(self, path):
        """The layout this side asks the file's questions with: the first one named for it (or for no file). The layouts of one
        file differ only in the schema they read and share their request fields and modes (the generator refuses any that do
        not), so this side never opens the store to tell them apart: the sidecar chooses on its own connection and answers the
        layout it read the file with (`_chosen`)."""
        named = self._candidates(path)
        return named[0][1] if named else None

    def _chosen(self, path, result):
        """The layout the sidecar read the file with, once per observation, from its answer. A file with one candidate needs
        no answer; with several, an answer that names none of them is a protocol error."""
        named = self._candidates(path)
        if len(named) > 1:
            slot = result.get('layout')
            if slot not in {candidate for candidate, _ in named}:
                raise RuntimeError('PROTOCOL_ERROR: observation answered no layout of the file')
            return self._observer().layouts[slot]
        return named[0][1] if named else None

    def _schema_read(self, path, result):
        """What a file that may hold one of several schemas says about the schema it was read with is the baseline's read problem
        (`schema_problem`: `none_applies`, `partial`, `mixed`): the reader records it, logs it once, and a read that fits a layout
        clears it again."""
        if len(self._candidates(path)) < 2:
            return
        problem = result.get('schema_problem')
        if problem is None:
            self._read_ok('schema')
            return
        if problem.get('kind') == 'unknown':
            return  # the store could not list its tables (missing, locked): the baseline neither records nor clears a problem then
        self._report_schema(Path(path), problem)

    #: What the request carries for a file that the log does not hold. Each is the discovery reader's own knowledge (the
    #: workspace file next to the store, the session directory name, the store's scan clock); the layout's `request` names
    #: which of them its adapter reads, so a layout that names none costs none.
    def _request(self, layout, path, mode):
        # A lookup binds to the session a store file is, never to its working directory or scan clock.
        wanted = [field for field in layout.request if mode not in (Mode.IDENTIFY, Mode.HAS_SESSION) or field == 'session']
        method = {'cwd': 'cwd_from_file', 'session': 'session_id_from_path', 'base_epoch': '_store_epoch', 'cli_version': 'version_from_file',
                  'now_ms': '_now_ms', 'namespace': 'namespace_from_file'}
        return {field: getattr(self, method[field])(Path(path)) for field in wanted}

    def _now_ms(self, _path):
        """The wall clock a layout that ends a turn after silence reads (`request: now_ms`); the request carries it so a test sets it."""
        return int(time.time() * 1000)

    def _native(self, path, mode, *, seen=None, **extra):
        observer = self._observer()
        core = observer.core
        layout = self._layout(path)
        asked = self._request(layout, path, mode) if layout else {}
        scope = {'pane_id': self.pane_home_id(str(path))} if core.python.pane_scope == 'home_dir' else {}
        keys, state = unpack(seen, observer.parser_id, core.legacy_state) if seen is not None else ([], {})
        by_stem = core.python.attribution == 'file_stem'
        # `observe_core` refuses an over-cap request before the sidecar would drop the connection on it: explicit and logged.
        result = observe_core(
            self.vendor, path, mode, seen=keys, state=state, **scope, **asked, **extra,
            match=_by_file_stem(self.vendor, Path(path)) if by_stem else None,
            validate=_checked(Path(path), core, layout, keys, state, mode) if by_stem or mode == Mode.ACTIVITY else None)
        self._chosen(path, result)
        self._schema_read(path, result)
        problem = result.get('ownership_error')
        if problem and core.compat.store_error_reported:
            # The lookup of the store that owns this file's session failed, so the file is read as not owned (the baseline's
            # `_db_owns_session` answered False) and the discovery reader warns once, as the baseline's `_query` did.
            self._warn_store_error(Path(problem['store']), problem['error'])
        if result.get('legacy_cursor'):
            # The byte cursor an older reader left in the bag does not describe this log: the log is read from its start.
            _log.warning('%s: %s legacy cursor refused (%s); the log is read again from its start', self.vendor, Path(path).name,
                         result['legacy_cursor'])
        if result.get('dropped_pending'):
            # A pending row the pass gave up on (gone, replaced, expired, ...) is reported once per pass, never silently.
            _log.info('%s: %s dropped %d pending row(s): %s', self.vendor, Path(path).name, len(result['dropped_pending']),
                      ', '.join(f"{d['table']}#{d['id']} ({d['why']})" for d in result['dropped_pending']))
        if result.get('truncated'):
            # A mark or line whose events alone were over the whole pass budget was delivered with its text cut (head and
            # tail kept); the pass reports each once, and this reports them once.
            _log.info('%s: %s truncated %d over-budget record(s): %s', self.vendor, Path(path).name, len(result['truncated']),
                      ', '.join(f"{t['table']}#{t['id']} ({t['bytes']} bytes)" for t in result['truncated']))
        return result

    def _store_failed(self, path, result) -> bool:
        """compat.store_error_reported: the baseline reader warned once about a store it could not read and answered no data
        for the cycle, leaving its checkpoint and bag where they were. The discovery reader says how it warns."""
        if not (result.get('error') and self._observer().core.compat.store_error_reported):
            return False
        self._warn_store_error(Path(path), result['error'])
        return True

    def _keep(self, seen_keys, result):
        """Add what the pass answered to the caller's bag (kept even when the pass failed). Every answer carries only the dedup
        keys it delivered, so the bag the sidecar was asked with is never echoed back (a bag over 125,000 keys is not sent twice)."""
        observer = self._observer()
        keys = set(result['seen']) | set(unpack(seen_keys, observer.parser_id, observer.core.legacy_state)[0])
        seen_keys.clear()
        seen_keys.update(pack(keys, result.get('state'), observer.parser_id))

    def _missing(self, path) -> bool:
        # compat.absent_file_is_empty: the baseline reader treated a file that is not there yet as "nothing to read".
        return self._observer().core.compat.absent_file_is_empty and not Path(path).exists()

    def _events(self, result, cls):
        return [cls(**event) for event in result['events']]

    def _answers(self, path, *modes):
        """Does the declaration answer one of these modes for this file? A file no layout owns is nobody's to read (its
        siblings, a `-wal`); a mode its layout never answers is the discovery reader's own. A layout that records no usage
        answers the usage modes with no events when the sidecar is asked, but there is nothing to read, so the reader keeps
        those modes and its own checkpoint shape."""
        layout = self._layout(path)
        if layout is None:
            return False
        # A file the discovery reader says is not one it observes (Muse's subagent logs, which the parent already attributes) keeps the
        # discovery reader's own answer for it.
        if not getattr(self, 'observes_file', lambda _path: True)(Path(path)):
            return False
        usage_modes = {Mode.USAGE, Mode.SESSION_USAGE, Mode.LEGACY}
        return any(mode in layout.modes and not (layout.usage == 'none' and mode in usage_modes) for mode in modes)

    def parse_incremental(self, path, checkpoint):
        if not self._answers(path, Mode.USAGE):
            if self._layout(path) is None:
                return IncrementalParseResult([], dict(checkpoint))
            return super().parse_incremental(path, checkpoint)
        # A store the pass may not have read to the end (the answer ledger was spent, or the usage page came back full) says `more`: the
        # call goes on from the checkpoint that pass left, so the backlog is read now and not at the next change of the store. A failure
        # after an earlier page returns what the earlier pages delivered, with the checkpoint they left.
        events, cursor = [], checkpoint
        while True:
            try:
                result = self._native(path, Mode.USAGE, checkpoint=cursor)
            except Exception:
                if not events:
                    raise
                _log.warning('%s: %s usage failed after %d event(s); the next cycle reads on', self.vendor, Path(path).name,
                             len(events), exc_info=True)
                break
            if self._store_failed(path, result):
                break
            events += self._events(result, TokenUsage)
            cursor = result['checkpoint']
            if not result.get('more') or result.get('error'):
                break
        return IncrementalParseResult(events, dict(cursor))

    def parse_session_file(self, path, seen_keys):
        if self._missing(path):
            return []
        if not self._answers(path, Mode.LEGACY, Mode.SESSION_USAGE):
            return [] if self._layout(path) is None else super().parse_session_file(path, seen_keys)
        # A vendor that lists `legacy` replays a whole store against the seen bag with a mode of its own (the baseline did);
        # the mode list decides, there is no second flag to keep in step with it.
        replay = Mode.LEGACY if Mode.LEGACY in self._layout(path).modes else Mode.SESSION_USAGE
        observer = self._observer()
        # Each page is sent the bag as it was when the call began plus the replay's state: a request never grows with the pages
        # already delivered (a store of 150,000 rows would put its keys past the request cap). A page that re-reads a row an earlier
        # page of this call delivered (a replay that started over because the file changed) is dropped here by its key.
        began, state = unpack(seen_keys, observer.parser_id, observer.core.legacy_state)
        delivered: set[str] = set()
        events = []
        while True:
            try:
                result = self._native(path, replay, seen=pack(began, state, observer.parser_id))
            except Exception:
                if not events:
                    raise
                # The earlier pages are kept in the bag, so what they delivered is returned now and the next cycle reads on.
                _log.warning('%s: %s replay failed after %d event(s); the next cycle reads on', self.vendor, Path(path).name,
                             len(events), exc_info=True)
                return events
            if self._store_failed(path, result):
                return events
            self._keep(seen_keys, result)
            state = result.get('state') or {}
            events += [event for event in self._events(result, TokenUsage) if event.dedup_key not in delivered]
            delivered.update(result['seen'])
            if not result.get('more') or result.get('error'):
                return events

    def parse_activity(self, path, seen_keys):
        if self._missing(path):
            return []
        if not self._answers(path, Mode.ACTIVITY):
            return [] if self._layout(path) is None else super().parse_activity(path, seen_keys)
        if 'session' in self._layout(path).request and not self.session_id_from_path(Path(path)):
            return []  # a file that is no session of this vendor (a sibling of the store) has nothing to attribute
        # A pass the answer ledger stopped before the end of the log, or whose walk page came back full, says `more`: the call goes on,
        # with the bag the earlier pass left (`_keep`), so what is already there is read now and not at the next write. An error after an
        # earlier page returns what the earlier pages delivered (their progress is in the bag already, and a raise would lose their events
        # with it).
        events: list = []
        while True:
            try:
                result = self._native(path, Mode.ACTIVITY, seen=seen_keys)
            except Exception:
                if not events:
                    raise
                _log.warning('%s: %s activity failed after %d event(s); the next cycle reads on', self.vendor, Path(path).name,
                             len(events), exc_info=True)
                break
            self._keep(seen_keys, result)
            # compat.store_error_reported: a walk the store failed (a `turns` table not created yet) warns once, and the walks
            # it did read still deliver their events with the bag the pass kept; usage and replay deliver nothing then. The failure
            # does not end the call while the answer says `more` (the other walks go on, as the baseline's one read does).
            failed = self._store_failed(path, result)
            if not failed and result.get('error'):
                if not events:
                    raise RuntimeError(result['error'])
                _log.warning('%s: %s activity failed after %d event(s): %s', self.vendor, Path(path).name, len(events), result['error'])
                break
            events += self._events(result, ActivityEvent)
            if not result.get('more'):
                break
        detail = self._observer().core.python.prompt_detail
        for event in events:
            if event.detail == detail and event.text:
                event.text = user_prompt_text(event.text)
        return events

    def turns_for_session(self, path, session_id=''):
        core = self._observer().core
        if Mode.TURNS not in core.modes:
            # compat.no_python_turn_fallback: the baseline refused a breakdown the sidecar does not answer, rather
            # than splitting turns from the generic reads.
            if core.compat.no_python_turn_fallback:
                raise RuntimeError('Native turn breakdown not yet integrated; no Python parser fallback')
            return super().turns_for_session(path, session_id)
        if self._missing(path):
            return []
        # The breakdown comes in pages when its rows are over what one answer carries: a page that says `more` is followed by the
        # next with the state it left; a page that says `restart` found the log changed under it, so the rows held so far are
        # dropped and the breakdown starts over (a log that keeps changing is given up on, loudly, after `_TURN_RESTARTS`).
        observer = self._observer()
        raw: list[dict] = []
        state: dict = {}
        restarts = 0
        # The session the caller asks the breakdown of: a vendor whose log holds records of more than one (Grok) reads only that session's.
        asked = {WANTED_SESSION: session_id} if session_id else {}
        while True:
            result = self._native(path, Mode.TURNS, seen=pack([], state, observer.parser_id) if state else None, **asked)
            if result.get('restart'):
                restarts += 1
                if restarts > _TURN_RESTARTS:
                    raise RuntimeError(f'TURNS_UNSTABLE: {Path(path).name} changed under the breakdown {restarts} times')
                raw.clear()
            for piece in result['turns']:
                if 'continues' in piece:
                    # The rest of the list of the row the previous page ended in (a row too large for one answer comes in pieces).
                    raw[-1][piece['continues']] += piece['items']
                else:
                    raw.append(piece)
            if not result.get('more'):
                break
            state = result.get('state') or {}
        rows = []
        for row in raw:
            row['calls'] = [TurnCall(**call) for call in row['calls']]
            rows.append(TurnUsage(**row))
        return rows


class NativeStore:
    """What a store source adds to the reader: asking the store whether it holds a session or a pane marker."""

    def _stores(self):
        """The store files this reader looks in: the ones it names itself, else the ones discovery lists."""
        return self._db_paths() if hasattr(self, '_db_paths') else self.session_files()

    def _asks(self, mode) -> bool:
        return any(mode in layout.modes for layout in self._observer().layouts)

    def has_session(self, session_id):
        if not self._asks(Mode.HAS_SESSION):
            return super().has_session(session_id)  # a vendor whose sessions are files knows them by name
        session_id = session_id.strip()
        if not session_id:
            return False
        # The lookup's target is `wanted_session`: `session_id` is the sidecar's live-session handle (an ownership key).
        return any(path.is_file() and self._native(path, Mode.HAS_SESSION, **{WANTED_SESSION: session_id})['found']
                   for path in self._stores())

    def find_sessions_by_marker(self, markers):
        if not self._asks(Mode.IDENTIFY):
            return super().find_sessions_by_marker(markers)
        markers = [marker for marker in markers if marker]
        if not markers:
            return {}
        found: dict = {}
        for path in self._stores():
            if len(found) == len(markers):
                break
            if not (path.is_file() and self._answers(path, Mode.IDENTIFY)):
                continue
            while True:
                # Only the markers not yet bound are asked, so the store that holds a marker first keeps it (the baseline's rule).
                result = self._native(path, Mode.IDENTIFY, markers=[marker for marker in markers if marker not in found])
                bound = {marker: tuple(binding) for marker, binding in result['markers'].items()}
                found.update(bound)
                # The bindings that did not fit one answer are counted by the sidecar; ask again for the rest, so the call returns what
                # the baseline's one unbounded lookup returns. Every answer binds at least one marker (a binding is at most about
                # 100 KiB against a ledger of 3.6 MB); one that binds none is reported, and the call stops there.
                if not result.get('markers_suppressed'):
                    break
                if not bound:
                    _log.warning('%s: %s left %d marker(s) unbound: not one more binding fits the answer', self.vendor, Path(path).name,
                                 result['markers_suppressed'])
                    break
        return found


class NativeHeaderIdentity:
    """A log whose header names its working directory and session; the sidecar reads it."""

    def _cwd_from_meta(self, path):
        return self._native(path, Mode.IDENTIFY)['cwd']

    def path_identity(self, usage):
        pane = self.pane_home_id(usage.file_path)
        if not pane:
            return None
        return pane, self._native(Path(usage.file_path), Mode.IDENTIFY)['native_session_id']


#: The mixin an `identity_source` adds; `file_stem` adds none (the discovery reader already names the session so).
_IDENTITY = {'store': NativeStore, 'header': NativeHeaderIdentity, 'file_stem': None}


def native_reader(discovery: type, vendor: str) -> type:
    source = _IDENTITY[FACTS[vendor].observer.core.python.identity_source]
    return type(f'Native{discovery.__name__}', (NativeObservation, *((source,) if source else ()), discovery), {})


def native_reference(spec):
    """Only explicit isolated evaluation selects native interpretation; then every vendor with a declared observer
    reads through the sidecar, on every operating system (the sidecar's observation core is the same on each)."""
    facts = FACTS.get(spec.key)
    if not (facts and facts.observer):
        return spec
    selected = cli_runtime_hook.runtime_selection()
    if selected != cli_runtime_hook.RUST_SELECTION:
        return spec
    cli_runtime_hook.require_isolated_harness(selected)
    return replace(spec, make_log_reader=native_reader(spec.make_log_reader, spec.key))
