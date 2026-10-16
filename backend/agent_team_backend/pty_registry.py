"""On-disk registry of live PTY child process groups.

PTY children are spawned with start_new_session=True (own process group), so
a backend that dies without running its shutdown sweep (SIGKILL, crash) leaves
them behind as orphans. Every spawn is recorded here and removed on close; at
startup, entries left over from a previous run are identity-checked and their
process groups killed.

Identity is the process start time (osplat.process_tree.start_time — ps
lstart on POSIX, the creation time on Windows), not the command line: shells
spawned as `zsh -lc <cmd>` exec the final command, so the visible command
never matches the spawn argv, while pid+start-time survives exec and defeats
pid recycling. The registry keeps calling the field `lstart` so entries
written by earlier backend versions stay readable. Each entry also records the owning backend pid so a second
backend sharing the data dir never reaps a live sibling's children.

Entries additionally carry the root's last descendant snapshot (pid -> lstart,
persisted by terminals' snapshot loop): killpg on the root's process group
misses grandchildren that detached into their own group (e.g. MCP servers a
CLI spawns), so reap_stale identity-checks and kills those individually — for
dead-owner entries whose root is still alive AND for roots that already died
on their own while the backend was down (their orphans outlive them).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

from . import osplat
from .applog import app_data_dir
from .db import DB_FILENAME, Database

log = logging.getLogger(__name__)

_KV_KEY = "pty_registry"

# register/unregister run on executor threads (terminals.py keeps their
# process probe + db I/O off the event loop), so every load-modify-save must
# be atomic.
_lock = threading.Lock()

# Lazily-opened database handle. The app injects its shared instance at
# startup (set_database); when the resolved app-data dir changes (tests
# repoint AGENT_TEAM_DATA_DIR per test) a fresh handle is opened for it.
_db: Database | None = None


def set_database(db: Database | None) -> None:
    """Share the app's Database instance instead of opening a second one."""
    global _db
    _db = db


def _registry_path() -> Path:
    return app_data_dir() / "pty-registry.json"


def _get_db() -> Database:
    global _db
    path = app_data_dir() / DB_FILENAME
    db = _db
    if db is None or db.path != path:
        db = Database(path)
        _db = db
    return db


def _import_legacy(cur: object, data: object) -> None:
    if isinstance(data, dict):
        _get_db().kv_set(_KV_KEY, data, now=int(time.time()))


def _load(strict: bool = False) -> dict[str, dict]:
    """The registry entries. A read failure is logged and read as empty — except
    with `strict`, which re-raises it for a caller that must not mistake an
    unreadable store for an empty one."""
    try:
        db = _get_db()
        data = db.kv_get(_KV_KEY)
        if data is None:
            db.import_json(_KV_KEY, _registry_path(), _import_legacy)
            data = db.kv_get(_KV_KEY)
    except (sqlite3.Error, OSError) as err:
        log.warning("pty registry read failed: %s", err)
        if strict:
            raise
        return {}
    return data if isinstance(data, dict) else {}


def _save(entries: dict[str, dict]) -> None:
    try:
        _get_db().kv_set(_KV_KEY, entries, now=int(time.time()))
    except (sqlite3.Error, OSError) as err:
        log.warning("pty registry write failed: %s", err)


def register(pid: int, argv: list[str]) -> None:
    lstart = osplat.process_tree.start_time(pid)  # probe outside the lock (up to 5s)
    with _lock:
        entries = _load()
        entries[str(pid)] = {
            "argv0": argv[0],  # diagnostic only; identity is lstart
            "lstart": lstart,
            "owner": os.getpid(),
        }
        _save(entries)


def register_owned(pid: int, argv: list[str], lstart: str, runtime_generation: str,
                   session_id: str, session_generation: str) -> None:
    """Persist supplied native ownership; failure prevents candidate publication."""
    if not lstart:
        raise ValueError("native ownership requires a start identity")
    with _lock:
        db = _get_db()
        entries = db.kv_get(_KV_KEY) or {}
        entries[str(pid)] = {
            "argv0": argv[0], "lstart": lstart, "owner": os.getpid(),
            "runtime_generation": runtime_generation, "session_id": session_id,
            "session_generation": session_generation,
        }
        db.kv_set(_KV_KEY, entries, now=int(time.time()))


def unregister(pid: int) -> None:
    with _lock:
        entries = _load()
        if entries.pop(str(pid), None) is not None:
            _save(entries)


def update_descendants(snapshot: dict[int, dict[int, str]]) -> None:
    """Persist each live root's descendant snapshot (pid -> lstart) into its
    registry entry, so a backend that dies without its shutdown sweep leaves
    the next start's reap_stale enough to take the detached grandchildren
    down too. Blocking (lock + file I/O) — call via asyncio.to_thread. Only
    writes when something actually changed (the snapshot loop calls this
    every tick)."""
    with _lock:
        entries = _load()
        changed = False
        for root_pid, descendants in snapshot.items():
            info = entries.get(str(root_pid))
            if info is None:
                continue  # root already unregistered — nothing to attach to
            recorded = {str(p): ls for p, ls in descendants.items()}
            if info.get("descendants") != recorded:
                info["descendants"] = recorded
                changed = True
        if changed:
            _save(entries)


def _backend_alive(pid: int) -> bool:
    """Is `pid` a live agent_team_backend process (a sibling sharing this
    data dir)? A recycled pid running something else counts as dead."""
    out = osplat.process_tree.command_of(pid)
    if out is None:
        return True  # can't tell — err on the side of not touching its children
    return "agent_team_backend" in out


_MATCH, _GONE = "match", "gone"


def _lstart_eq(a: str, b: str) -> bool:
    """Whitespace-normalized lstart equality: ps pads day-of-month, and the
    snapshot side stores single-space-joined fields. Empty on either side is
    never a match — an unverifiable identity must never authorize a kill."""
    return bool(a) and bool(b) and " ".join(a.split()) == " ".join(b.split())


def _ps_table() -> "dict[int, tuple[int, str]] | None":
    """pid -> (group id, start-time identity) for every process, from ONE
    snapshot. One table read replaces the per-pid probes reap/scan used to
    run under the lock (N x descendants x 5s-timeout worst case). None means
    the probe itself failed — callers must treat that as 'cannot verify
    anything'."""
    snap = osplat.process_tree.snapshot()
    if not snap:
        return None
    return {pid: (info[1], info[2]) for pid, info in snap.items()}


def _classify_root(table: dict[int, tuple[int, str]], pid: int, info: dict) -> str:
    entry = table.get(pid)
    if entry is None:
        return _GONE
    pgid, lstart = entry
    if pgid != pid:
        return _GONE  # not a session/group leader → recycled pid
    return _MATCH if _lstart_eq(lstart, info.get("lstart") or "") else _GONE


def _match_descendants(
    table: dict[int, tuple[int, str]], info: dict
) -> tuple[list[int], list[int]]:
    """Recorded descendants still identity-matched by lstart, split into
    process-group leaders and plain pids. Leaders (MCP wrappers detached via
    setsid) are killed with killpg so children they spawned after the last
    persisted snapshot die with the group — mirroring the runtime reaper's
    subtree kill. A recycled or unverifiable pid never matches."""
    leaders: list[int] = []
    plain: list[int] = []
    for pid_s, recorded in (info.get("descendants") or {}).items():
        try:
            pid = int(pid_s)
        except (TypeError, ValueError):
            continue
        entry = table.get(pid)
        if entry is None:
            continue
        pgid, lstart = entry
        if not _lstart_eq(lstart, recorded):
            continue
        (leaders if pgid == pid else plain).append(pid)
    return leaders, plain


def _signal_each(targets: "list[tuple[int, bool]]", *, force: bool) -> None:
    """Signal each (pid, is_group) target; already-dead pids are skipped."""
    tree = osplat.process_tree
    for pid, group in targets:
        try:
            (tree.kill_group if group else tree.kill)(pid, force=force)
        except (ProcessLookupError, PermissionError):
            pass


def _still_same(
    targets: "list[tuple[int, bool]]", table: dict[int, tuple[int, str]]
) -> "list[tuple[int, bool]]":
    """The targets whose start-time identity in a fresh table still matches
    the one in `table` (the snapshot the verdict was made on). A pid gone or
    recycled in between is dropped; a failed fresh probe drops everything —
    an identity that cannot be re-checked never authorizes a second kill."""
    fresh = _ps_table()
    if fresh is None:
        return []
    return [
        (pid, group)
        for pid, group in targets
        if pid in fresh and _lstart_eq(fresh[pid][1], table[pid][1])
    ]


def _collect_stale(
    entries: dict[str, dict], table: dict[int, tuple[int, str]]
) -> "tuple[list[int], list[int], list[int], dict[str, dict]]":
    """Shared verdict logic for scan_orphans and reap_stale: identity-matched
    roots, descendant group-leaders, plain descendants, and the entries to
    keep. A root pid still PRESENT in the table but unverifiable (recycled,
    or its register-time lstart probe failed) never authorizes a descendant
    kill — the root may be a live CLI and its recorded descendants its live
    servers; the entry is dropped without signalling anything."""
    me = os.getpid()
    roots: list[int] = []
    desc_group: list[int] = []
    desc_solo: list[int] = []
    keep: dict[str, dict] = {}
    for pid_s, info in entries.items():
        owner = info.get("owner")
        if isinstance(owner, int) and (owner == me or _backend_alive(owner)):
            keep[pid_s] = info  # a live backend (this one or a sibling) owns it
            continue
        root = int(pid_s)
        if _classify_root(table, root, info) == _MATCH:
            roots.append(root)
        elif root in table:
            continue  # present but unverifiable — never kill under a live root
        # Root matched (whole leftover) or truly absent (died on its own while
        # its backend was down — no EOF reap ran): recorded descendants that
        # still match their lstart are leaked orphans.
        g, p = _match_descendants(table, info)
        desc_group.extend(g)
        desc_solo.extend(p)
    return roots, desc_group, desc_solo, keep


def journal_dir() -> Path:
    """Where the native sidecar journals the identity of every root it spawns."""
    return app_data_dir() / "runtime-ownership"


def journal_path(runtime_generation: str) -> Path:
    return journal_dir() / f"{runtime_generation}.jsonl"


def start_journal(runtime_generation: str, owner_pid: int) -> Path:
    """Create the generation's journal, atomically and fsynced, with a header naming its
    owner, BEFORE the sidecar that appends to it exists. A restarted backend can then tell
    a live owner's journal from a dead one's even when no identity was appended yet, and a
    zero-byte journal can only mean an interrupted creation (an observable anomaly)."""
    directory = journal_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = journal_path(runtime_generation)
    pending = path.with_suffix(".jsonl.new")
    header = json.dumps({"journal": "header", "owner_pid": owner_pid,
                         "runtime_generation": runtime_generation}) + "\n"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, header.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(pending, path)
    return path


def _read_journal(path: Path) -> "tuple[int | None, list[dict]]":
    """(owner pid from the header or first record, every identity record). Strict: an
    unreadable file, an empty one, or a record that is not whole and well-formed raises,
    because the root it would have named may be alive."""
    records: list[dict] = []
    owner: int | None = None
    # Never block on, or follow, anything that is not a plain file (a FIFO would hang here).
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("ownership journal is not a regular file")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
            text = stream.read()
    finally:
        os.close(fd)
    if not text:
        raise ValueError("ownership journal is empty")
    for line in text.splitlines():
        record = json.loads(line)
        if isinstance(record, dict) and record.get("journal") == "header":
            if not isinstance(record.get("owner_pid"), int) or owner is not None or records:
                raise ValueError("malformed ownership journal header")
            owner = record["owner_pid"]
            continue
        if (not isinstance(record, dict) or record.get("journal") != 1
                or not isinstance(record.get("pid"), int) or not record.get("start_value")
                or not isinstance(record.get("owner_pid"), int)):
            raise ValueError("malformed ownership journal record")
        records.append(record)
        if owner is None:
            owner = record["owner_pid"]
    return owner, records


class JournalIdentityConflict(ValueError):
    """A journal and an existing registry row name the same pid with different identities
    (start time, owner or runtime generation). Neither is authoritative until the stale
    one is resolved, so the journal is kept and admission fenced; nothing is overwritten."""

    def __init__(self, message: str, pids: "list[str]"):
        super().__init__(message)
        self.pids = pids


def ingest_journal(path: Path, owner: int | None = None) -> list[dict]:
    """Make every identity a sidecar journalled durable in the registry (existing rows
    win, but are marked as journal-named), so the ordinary identity-verified paths can
    clean them. Raises when the journal cannot be read in full or the registry cannot
    be written. `owner` overrides the recorded owner pid."""
    try:
        _owner, records = _read_journal(path)
    except FileNotFoundError:
        return []  # the sidecar never spawned anything
    with _lock:
        db = _get_db()
        entries = db.kv_get(_KV_KEY) or {}
        changed = False
        # A pid cannot be shared by two live processes, so within one journal the later
        # record supersedes an earlier one (the earlier birth was reaped before it).
        latest = {record["pid"]: record for record in records}
        conflicting = [
            str(record["pid"]) for record in latest.values()
            if (existing := entries.get(str(record["pid"]))) is not None and not (
                _lstart_eq(existing.get("lstart") or "", record["start_value"])
                and existing.get("owner") == (owner if owner is not None else record["owner_pid"])
                and existing.get("runtime_generation") == (record.get("runtime_generation") or path.stem))
        ]
        if conflicting:
            raise JournalIdentityConflict(
                f"journal {path.name} and the registry name pid(s) {conflicting} with different identities",
                conflicting)
        for record in latest.values():
            key = str(record["pid"])
            if key in entries:
                if entries[key].get("journal") != path.name:
                    entries[key]["journal"] = path.name
                    changed = True
                continue
            entries[key] = {
                "argv0": record.get("argv0") or "", "lstart": record["start_value"],
                "owner": owner if owner is not None else record["owner_pid"],
                "runtime_generation": record.get("runtime_generation") or path.stem,
                "session_id": record.get("session_id"),
                "session_generation": record.get("session_generation"),
                "journal": path.name,
            }
            changed = True
        if changed:
            db.kv_set(_KV_KEY, entries, now=int(time.time()))
    return records


def _ingest_stale_journals(details: "dict | None" = None) -> bool:
    """Registry-ise the journals of sidecars whose backend is gone. The files are KEPT:
    they are deleted only once their identities are verified gone (see reap_stale).
    False when any journal could not be read in full or ingested (that file stays,
    nothing is guessed from it). `details["ingested"]` lists (path, pids) to settle."""
    directory = journal_dir()
    complete = True
    me = os.getpid()
    ingested: list = []
    conflicts: list = []
    collisions: set = set()
    for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else []:
        try:
            owner, records = _read_journal(path)
            if owner is not None and (owner == me or _backend_alive(owner)):
                continue  # a live backend (this one or a sibling) still owns it
            ingest_journal(path)
            ingested.append((path, {r["pid"] for r in records}))
        except JournalIdentityConflict as err:
            log.warning("ownership journal %s: %s", path.name, err)
            conflicts.append(path)
            collisions.update(err.pids)
            complete = False
        except (OSError, ValueError, sqlite3.Error) as err:
            log.warning("ownership journal %s unreadable: %s", path.name, type(err).__name__)
            complete = False
    if details is not None:
        details["ingested"] = ingested
        details["conflicts"] = conflicts
        details["collisions"] = collisions
    return complete


# Why a restarted backend refuses to admit a runtime, per data dir. Recomputed from the
# preserved journals and registry rows (the durable evidence) at every start and on every
# admission retry, so it can never be lost or stuck once the evidence is resolved.
_FENCES: dict[str, str] = {}


def admission_fence() -> "str | None":
    return _FENCES.get(str(app_data_dir()))


def _set_fence(reason: "str | None") -> None:
    key = str(app_data_dir())
    if reason is None:
        _FENCES.pop(key, None)
        return
    if _FENCES.get(key) != reason:
        log.error("runtime admission refused: %s (ownership journals and registry rows are kept; "
                  "see the native runtime README for the recovery path)", reason)
    _FENCES[key] = reason


def reevaluate_fence(grace: float = 1.0) -> "str | None":
    """Retry the startup cleanup of a fenced data dir; returns the fence still standing."""
    if admission_fence() is None:
        return None
    reap_stale(grace)
    return admission_fence()


def scan_orphans() -> list[int]:
    """Pids of live processes recorded by a now-dead backend run — exactly the
    ones reap_stale would kill, detached descendants included. Read-only:
    never signals a process or rewrites the registry, so it is safe to poll
    for a "how many leftovers?" status check (the pileup that silently
    exhausted RAM was invisible until now)."""
    with _lock:
        entries = _load()
        if not entries:
            return []
        table = _ps_table()
        if table is None:
            return []  # cannot verify anything right now
        roots, desc_group, desc_solo, _keep = _collect_stale(entries, table)
        return roots + desc_group + desc_solo


def reap_stale(grace: float = 1.0, settle: float = 3.0, _retry: bool = True) -> list[int]:
    """Kill process groups (and recorded detached descendants) left by a dead
    backend run.

    Blocking (process snapshot + grace sleep) — call via asyncio.to_thread. Entries owned by
    a live sibling backend are left untouched; if the ps snapshot itself fails
    everything is kept for the next startup; everything else is killed or
    confirmed gone and dropped. Returns the pids that were signalled.

    Holds the registry lock for its whole run: _save(keep) rewrites the file
    from the entries loaded at the top, so an interleaved register would be
    lost if the load→save window were left open.

    Roots a dead backend's sidecar journalled (even ones the backend never registered) are
    brought into the registry first. Identities a journal names are held to a stricter
    rule than legacy rows: they, and their journal, are dropped only after a verified final
    recheck shows them gone; a survivor, a failed snapshot, an unreadable/torn/empty journal
    or a failed registry write leaves the evidence in place and fences runtime admission
    (`admission_fence`), which a later call re-evaluates.
    """
    details: dict = {}
    problems: list[str] = []
    if not _ingest_stale_journals(details):
        problems.append("an ownership journal is unreadable, torn, empty or could not be ingested")
    ingested: list = details.get("ingested", [])
    conflicts: list = details.get("conflicts", [])
    collisions: set = details.get("collisions", set())
    with _lock:
        try:
            entries = _load(strict=True)
        except (sqlite3.Error, OSError):
            _set_fence("the process registry is unreadable")
            return []
        journal_rows = {pid_s for pid_s, info in entries.items() if info.get("journal")}
        if not entries:
            _settle_journals(ingested, entries, problems)
            return []
        table = _ps_table()
        if table is None:
            # Cannot verify identities — keep everything for the next startup
            # rather than signalling blind.
            log.warning("pty reap skipped: process snapshot failed")
            if journal_rows:
                problems.append("the process table could not be read")
            _settle_journals(ingested, entries, problems)
            return []
        roots, desc_group, desc_solo, keep = _collect_stale(entries, table)
        # A row a journal collides with is dropped only once EVERY identity it records is
        # verified gone. A pid now held by a process with a different birth proves the row's
        # root dead (a pid has one birth), so its recorded descendants are leftovers to clean
        # — _collect_stale skips them for a present root. A row whose root start is unknown
        # cannot be proven dead: it stays, and the conflict keeps admission fenced.
        row_targets: dict[str, set[int]] = {}
        unprovable: set[str] = set()
        for pid_s in sorted(collisions & entries.keys() - keep.keys()):
            info, root = entries[pid_s], int(pid_s)
            row_lstart = info.get("lstart") or ""
            leaders, plain = _match_descendants(table, info)
            live = table.get(root)
            if live is not None and not _lstart_eq(live[1], row_lstart):
                if not row_lstart:
                    unprovable.add(pid_s)
                    continue
                desc_group.extend(leaders)
                desc_solo.extend(plain)
            row_targets[pid_s] = ({root} if root in roots else set()) | set(leaders) | set(plain)
        # Roots and descendant group-leaders take their whole group (children
        # spawned after the last persisted snapshot die with it); plain
        # descendants are signalled individually.
        targets = [(pid, True) for pid in roots + desc_group] + [
            (pid, False) for pid in desc_solo
        ]
        unresolved: list[int] = []
        unverifiable = False
        watched: list = []
        if targets:
            _signal_each(targets, force=False)
            time.sleep(grace)
            # The grace is long enough for a pid to be recycled — on Windows
            # by this backend's own first panes — so the force round is
            # re-verified against a fresh table instead of re-sent blind.
            _signal_each(_still_same(targets, table), force=True)
            log.info(
                "reaped %d orphaned PTY process group(s) %s and %d detached descendant(s) %s",
                len(roots), roots, len(desc_group) + len(desc_solo), desc_group + desc_solo,
            )
            verify = {pid for pids in row_targets.values() for pid in pids}
            watched = [t for t in targets if str(t[0]) in journal_rows or t[0] in verify]
            if watched:
                remaining = _recheck(watched, table)
                deadline = time.monotonic() + settle
                while remaining:
                    time.sleep(0.02)
                    remaining = _recheck(remaining, table)
                    if remaining is None or time.monotonic() >= deadline:
                        break
                if remaining is None:
                    unverifiable = True
                else:
                    unresolved = [pid for pid, _group in remaining]
        # Journal-named identities that are not verifiably gone stay recorded.
        retained = {
            pid_s: info for pid_s, info in entries.items()
            if pid_s in journal_rows and pid_s not in keep
            and int(pid_s) in {t[0] for t in watched}
            and (unverifiable or int(pid_s) in unresolved)
        }
        for pid_s in unprovable:
            retained[pid_s] = entries[pid_s]
        for pid_s, pids in row_targets.items():
            if pids and (unverifiable or pids & set(unresolved)):
                retained[pid_s] = entries[pid_s]  # its recorded identities survive with it
        if unprovable:
            problems.append(f"{len(unprovable)} colliding registry row(s) cannot be proven stale")
        if unverifiable:
            problems.append("a final verification snapshot could not be read")
        elif unresolved:
            problems.append(f"{len(unresolved)} journal-named or colliding process(es) survived cleanup")
        if not _try_save({**keep, **retained}):
            problems.append("the registry could not be written")
            retained = {pid_s: True for pid_s in journal_rows}  # nothing is settled
        _settle_journals(ingested, {**keep, **retained}, problems)
        signalled = roots + desc_group + desc_solo
    if conflicts and _retry:
        # The legacy pass above may have dropped the stale same-pid row (present but unverifiable,
        # owner dead). Retry once so the journal's own identity is then ingested and verified.
        # A conflict that survives (e.g. the row belongs to a live owner) stays fenced, journal intact.
        return signalled + reap_stale(grace, settle, _retry=False)
    return signalled


def _try_save(entries: dict) -> bool:
    try:
        _get_db().kv_set(_KV_KEY, entries, now=int(time.time()))
        return True
    except (sqlite3.Error, OSError) as err:
        log.warning("pty registry write failed: %s", err)
        return False


def _settle_journals(ingested: list, remaining_rows: dict, problems: list) -> None:
    """Delete each ingested journal whose identities are all settled and set or clear the fence."""
    for path, pids in ingested:
        if problems or any(str(pid) in remaining_rows for pid in pids):
            continue
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
    if problems:
        _set_fence("; ".join(problems))
    else:
        _set_fence(None)


def _recheck(targets: "list[tuple[int, bool]]", table: dict[int, tuple[int, str]]):
    """Targets whose start-time identity still matches in a FRESH table, or None
    when that table cannot be read (unlike `_still_same`, which treats a failed
    read as "nothing left" — acceptable for narrowing a signal, never for
    certifying that processes are gone)."""
    fresh = _ps_table()
    if fresh is None:
        return None
    return [(pid, group) for pid, group in targets
            if pid in fresh and _lstart_eq(fresh[pid][1], table[pid][1])]


def reap_runtime(runtime_generation: str, grace: float = 1.0, settle: float = 3.0,
                 known: "list[dict] | None" = None) -> dict:
    """Identity-verified cleanup of ONE lost Agent CLI Runtime generation while
    this backend stays alive.

    Only entries recorded by this backend under exactly `runtime_generation`
    are considered; another generation, another backend's entries and every
    unrecorded process are never signalled. Roots follow reap_stale's rules
    (group leader AND matching start time), descendants are the last persisted
    snapshot's pids that still match their start time; a recycled or
    unverifiable pid never authorizes a signal. The force round is re-verified
    against a fresh table. A failed process snapshot signals nothing and keeps
    every entry. Processes born after the last persisted descendant snapshot
    are unknown and therefore not signalled.

    Blocking (process snapshots + grace sleep) — call via asyncio.to_thread.
    Returns only numeric identities: `signalled` pids, `unresolved` pids still
    matching their identity after the settle window (their entries are kept),
    and `unverifiable` when no verdict could be made.

    `known` lists identities (`pid`, `lstart`) the lost runtime itself reported for
    this generation but whose registry write may not be durable; they are cleaned
    by exactly the same identity rules.
    """
    me = os.getpid()
    with _lock:
        try:
            entries = _load(strict=True)
        except (sqlite3.Error, OSError):
            # Nothing readable is not nothing recorded: signal nothing, verdict unknown.
            return {"signalled": [], "unresolved": [], "unverifiable": True}
        mine = {
            pid_s: info for pid_s, info in entries.items()
            if info.get("owner") == me and info.get("runtime_generation") == runtime_generation
        }
        synthesized: dict[str, dict] = {}
        for identity in known or ():
            if identity.get("lstart") and str(identity["pid"]) not in mine:
                synthesized[str(identity["pid"])] = {
                    "lstart": identity["lstart"], "owner": me, "runtime_generation": runtime_generation}
        mine.update(synthesized)
        if not mine:
            return {"signalled": [], "unresolved": [], "unverifiable": False}
        table = _ps_table()
        if table is None:
            log.warning("runtime reap skipped: process snapshot failed")
            if synthesized:  # an identity only this call knew must not be lost with it
                _save({**entries, **synthesized})
            return {"signalled": [], "unresolved": [], "unverifiable": True}
        roots: list[int] = []
        desc_group: list[int] = []
        desc_solo: list[int] = []
        for pid_s, info in mine.items():
            root = int(pid_s)
            if _classify_root(table, root, info) == _MATCH:
                roots.append(root)
            elif root in table:
                continue  # present but unverifiable: never authorizes a descendant kill
            leaders, plain = _match_descendants(table, info)
            desc_group.extend(leaders)
            desc_solo.extend(plain)
        targets = [(pid, True) for pid in roots + desc_group] + [(pid, False) for pid in desc_solo]
        signalled = roots + desc_group + desc_solo
        unresolved: list[int] = []
        unverifiable = False
        if targets:
            _signal_each(targets, force=False)
            time.sleep(grace)
            remaining = _recheck(targets, table)
            if remaining is not None and remaining:
                _signal_each(remaining, force=True)
                deadline = time.monotonic() + settle
                while remaining:
                    remaining = _recheck(remaining, table)
                    if not remaining or time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
            if remaining is None:
                # A verification read that failed is not a negative observation:
                # these identities may still be live, so keep every entry.
                unverifiable = True
            else:
                unresolved = [pid for pid, _group in remaining]
        keep = {pid_s: info for pid_s, info in {**entries, **synthesized}.items()
                if pid_s not in mine or unverifiable or int(pid_s) in unresolved}
        _save(keep)
        return {"signalled": signalled, "unresolved": unresolved, "unverifiable": unverifiable}


def unregister_owned(pid: int, runtime_generation: str) -> None:
    """Drop `pid`'s entry only if it still belongs to `runtime_generation` (a late
    registration from a retired generation; never touches another generation's pid)."""
    with _lock:
        entries = _load()
        if (entries.get(str(pid)) or {}).get("runtime_generation") == runtime_generation:
            entries.pop(str(pid))
            _save(entries)
