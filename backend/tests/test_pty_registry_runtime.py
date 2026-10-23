"""Identity-verified cleanup of ONE lost Agent CLI Runtime generation while its
owning backend stays alive. Real processes only; the failing process-table probe
is the single substituted piece."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from agent_team_backend import osplat, pty_registry

GENERATION = "lost-generation"

# Double fork so the process is reparented to launchd exactly like a PTY child
# whose sidecar died: nothing in this test process has to reap it.
_LAUNCHER = """
import os, sys
group_leader = sys.argv[1] == "leader"
r, w = os.pipe()
if os.fork() == 0:
    if os.fork() == 0:
        if group_leader:
            os.setsid()
        os.write(w, str(os.getpid()).encode())
        os.close(w)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        os.execvp("sleep", ["sleep", "300"])
    os._exit(0)
os.wait()
os.close(w)
sys.stdout.write(os.read(r, 32).decode())
"""

pytestmark = pytest.mark.skipif(not hasattr(os, "setsid"), reason="POSIX process groups")
_started: list[int] = []


def _orphan(group_leader: bool = True) -> int:
    out = subprocess.run([sys.executable, "-c", _LAUNCHER, "leader" if group_leader else "plain"],
                         capture_output=True, text=True, timeout=10, check=True)
    pid = int(out.stdout)
    _started.append(pid)
    return pid


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    pty_registry._FENCES.clear()
    for pid in _started:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
    _started.clear()


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return False


def _register(pid: int, *, generation: str = GENERATION, owner: int | None = None, lstart: str | None = None) -> None:
    pty_registry.register_owned(pid, ["fixture"], lstart or osplat.process_tree.start_time(pid), generation,
                                f"session-{pid}", f"session-generation-{pid}")
    if owner is not None:
        entries = pty_registry._load()
        entries[str(pid)]["owner"] = owner
        pty_registry._save(entries)


def test_reaps_root_group_and_snapshot_known_detached_descendant():
    root, detached = _orphan(), _orphan()
    _register(root)
    pty_registry.update_descendants({root: {detached: osplat.process_tree.start_time(detached)}})

    result = pty_registry.reap_runtime(GENERATION, grace=0.2)

    assert sorted(result["signalled"]) == sorted([root, detached])
    assert result["unresolved"] == [] and result["unverifiable"] is False
    assert _wait_gone(root) and _wait_gone(detached)
    assert pty_registry._load() == {}


def test_never_signals_another_generation_another_owner_or_unregistered_process():
    other_generation, other_owner, unknown = _orphan(), _orphan(), _orphan()
    _register(other_generation, generation="another-runtime")
    _register(other_owner, owner=1)  # recorded by a different backend
    # `unknown` is a live process this registry never recorded: no identity, no signal.

    result = pty_registry.reap_runtime(GENERATION, grace=0.2)

    assert result["signalled"] == [] and result["unresolved"] == []
    assert all(_alive(pid) for pid in (other_generation, other_owner, unknown))
    assert set(pty_registry._load()) == {str(other_generation), str(other_owner)}


def test_forged_or_recycled_identities_are_dropped_without_a_signal():
    # PID reuse cannot be forced on macOS: stand in for it with entries whose
    # pid now belongs to an unrelated live process (a group leader, so only the
    # start-time check protects it; and a non-leader, so the group check does).
    leader, plain, descendant_host, victim_descendant = _orphan(), _orphan(group_leader=False), _orphan(), _orphan()
    _register(leader, lstart="Thu Jan  1 00:00:00 1970")
    _register(plain)
    _register(descendant_host)
    pty_registry.update_descendants({descendant_host: {victim_descendant: "Thu Jan  1 00:00:00 1970"}})

    result = pty_registry.reap_runtime(GENERATION, grace=0.2)

    assert result["signalled"] == [descendant_host]  # the only identity-verified process
    assert _alive(leader) and _alive(plain) and _alive(victim_descendant)
    assert _wait_gone(descendant_host)
    assert set(pty_registry._load()) == set()


def test_an_unverifiable_process_table_signals_nothing_and_keeps_the_entries(monkeypatch):
    root = _orphan()
    _register(root)
    monkeypatch.setattr(pty_registry, "_ps_table", lambda: None)

    result = pty_registry.reap_runtime(GENERATION, grace=0.0)

    assert result == {"signalled": [], "unresolved": [], "unverifiable": True}
    assert _alive(root)
    assert str(root) in pty_registry._load()


def test_a_survivor_is_reported_unresolved_and_its_entry_kept(monkeypatch):
    root = _orphan()
    _register(root)
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # nothing dies

    result = pty_registry.reap_runtime(GENERATION, grace=0.0, settle=0.2)

    assert result["signalled"] == [root] and result["unresolved"] == [root]
    assert _alive(root) and str(root) in pty_registry._load()


def _fail_snapshots_from(monkeypatch, number: int) -> list[int]:
    """Real table reads until the `number`-th; every later read fails (the probe
    is the single substituted piece). Returns the live call counter."""
    calls: list[int] = []
    real = pty_registry._ps_table

    def table():
        calls.append(1)
        return real() if len(calls) < number else None

    monkeypatch.setattr(pty_registry, "_ps_table", table)
    return calls


@pytest.mark.parametrize("failing", [2, 3])
def test_a_later_failed_verification_snapshot_is_unverifiable_and_keeps_identities(monkeypatch, failing):
    """F3: a verification read that fails after the signals went out (the first
    re-check, or the settle loop) cannot certify a still-live process as gone."""
    root = _orphan()
    _register(root)
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # it stays alive
    calls = _fail_snapshots_from(monkeypatch, failing)

    result = pty_registry.reap_runtime(GENERATION, grace=0.0, settle=0.3)

    assert len(calls) >= failing
    assert result["unverifiable"] is True, result
    assert _alive(root)
    assert str(root) in pty_registry._load(), "identities of a possibly live process were dropped"


def test_an_unreadable_registry_is_unverifiable_not_empty(monkeypatch):
    """A registry READ failure (locked/corrupt store) with a live recorded process:
    reading nothing is not the same as there being nothing to clean."""
    root = _orphan()
    _register(root)
    signalled: list = []
    real_db = pty_registry._get_db

    def unreadable():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: signalled.append(targets))
    monkeypatch.setattr(pty_registry, "_get_db", unreadable)

    result = pty_registry.reap_runtime(GENERATION, grace=0.0, settle=0.2)

    monkeypatch.setattr(pty_registry, "_get_db", real_db)
    assert result["unverifiable"] is True, result
    assert result["signalled"] == [] and signalled == []
    assert _alive(root)
    assert str(root) in pty_registry._load(), "the recorded identity must survive an unreadable read"


def _journal(generation: str, *records: dict, raw_tail: str = "") -> Path:
    directory = pty_registry.journal_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{generation}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + raw_tail)
    return path


def _record(pid: int, owner: int, lstart: str | None = None) -> dict:
    return {"journal": 1, "pid": pid, "pgid": pid, "start_kind": "posix-lstart",
            "start_value": lstart or osplat.process_tree.start_time(pid), "argv0": "fixture",
            "session_id": f"session-{pid}", "session_generation": "sg", "runtime_generation": "dead-gen",
            "owner_pid": owner}


def test_restart_reap_cleans_a_journalled_root_the_backend_never_registered():
    root, unrelated = _orphan(), _orphan()
    path = _journal("dead-gen", _record(root, owner=1))  # owner 1: not a live backend
    assert str(root) not in pty_registry._load()

    signalled = pty_registry.reap_stale(grace=0.2)

    assert root in signalled and unrelated not in signalled
    assert _wait_gone(root) and _alive(unrelated)
    assert not path.exists(), "an ingested journal is dropped only once its identities are durable"


def test_restart_reap_never_signals_a_journalled_pid_whose_identity_does_not_match():
    recycled = _orphan()
    _journal("dead-gen", _record(recycled, owner=1, lstart="Thu Jan  1 00:00:00 1970"))

    signalled = pty_registry.reap_stale(grace=0.2)

    assert recycled not in signalled and _alive(recycled)


def test_a_live_backends_journal_is_left_alone():
    root = _orphan()
    path = _journal("live-gen", _record(root, owner=os.getpid()))

    signalled = pty_registry.reap_stale(grace=0.2)

    assert signalled == [] and _alive(root) and path.exists()
    assert str(root) not in pty_registry._load()


def test_a_torn_journal_is_unverifiable_kept_and_signals_nothing():
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1), raw_tail='{"journal":1,"pid":')

    assert pty_registry._ingest_stale_journals() is False
    assert pty_registry.reap_stale(grace=0.2) == []
    assert _alive(root) and path.exists(), "an unreadable journal must be kept for a later, readable attempt"


# ── startup fence (review-3 F1) and journal-derived identities kept until verified gone (F2) ──────

def _fence():
    return pty_registry.admission_fence()


def test_f1_torn_journal_at_startup_fences_admission_and_is_preserved():
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1), raw_tail='{"journal":1,"pid":')

    assert pty_registry.reap_stale(grace=0.2) == []

    assert _fence(), "a torn journal must refuse admission"
    assert _alive(root) and path.exists()


def test_f1_empty_journal_at_startup_fences_admission_and_is_preserved():
    path = _journal("dead-gen")  # created, never appended: an incomplete append is observable

    assert pty_registry.reap_stale(grace=0.2) == []

    assert _fence() and path.exists()


def test_f1_unreadable_journal_fences_and_a_retry_after_repair_cleans_and_lifts_the_fence():
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1))
    path.chmod(0)
    try:
        pty_registry.reap_stale(grace=0.2)
        assert _fence() and _alive(root) and path.exists()
    finally:
        path.chmod(0o600)

    assert pty_registry.reevaluate_fence(grace=0.2) is None  # the retry path: verified cleanup, then admission
    assert _wait_gone(root)
    assert not path.exists() and _fence() is None


def test_f1_failed_registry_write_during_ingestion_fences_and_preserves_the_journal(monkeypatch):
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1))
    real_db = pty_registry._get_db

    class Failing:
        def __getattr__(self, name):
            return getattr(real_db(), name)

        def kv_set(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(pty_registry, "_get_db", lambda: Failing())
    pty_registry.reap_stale(grace=0.2)
    monkeypatch.setattr(pty_registry, "_get_db", real_db)

    assert _fence() and _alive(root) and path.exists()


def test_f2_a_journalled_survivor_keeps_its_row_and_journal_and_fences_then_a_verified_retry_cleans(monkeypatch):
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1))
    real_signal = pty_registry._signal_each
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # nothing dies

    pty_registry.reap_stale(grace=0.1)

    assert _alive(root) and _fence(), "a survivor must keep the admission fence"
    assert path.exists() and str(root) in pty_registry._load(), "the journal-derived identity must be retained"
    monkeypatch.setattr(pty_registry, "_signal_each", real_signal)
    assert pty_registry.reevaluate_fence(grace=0.2) is None
    assert _wait_gone(root) and not path.exists() and str(root) not in pty_registry._load()


@pytest.mark.parametrize("failing", [2, 3])
def test_f2_a_failed_verification_snapshot_is_unresolved_and_keeps_identity_and_journal(monkeypatch, failing):
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1))
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)
    calls = _fail_snapshots_from(monkeypatch, failing)

    pty_registry.reap_stale(grace=0.0)

    assert len(calls) >= failing
    assert _alive(root) and _fence()
    assert path.exists() and str(root) in pty_registry._load()


def test_f2_a_journalled_root_that_is_really_gone_is_dropped_with_its_journal():
    root = _orphan()
    path = _journal("dead-gen", _record(root, owner=1))
    psutil.Process(root).kill()
    assert _wait_gone(root)

    pty_registry.reap_stale(grace=0.1)

    assert _fence() is None and not path.exists() and str(root) not in pty_registry._load()


# ── review-4 F1: a registry row and a journal naming the same PID with different identities ──────

def _stale_row(pid: int, *, lstart: str = "Thu Jan  1 00:00:00 1970", owner: int = 1,
               generation: str = "older-gen") -> None:
    entries = pty_registry._load()
    entries[str(pid)] = {"argv0": "stale", "lstart": lstart, "owner": owner, "runtime_generation": generation}
    pty_registry._save(entries)


def test_f1r4_journalled_live_root_with_an_older_same_pid_row_is_reaped_not_declared_clean():
    root, unrelated = _orphan(), _orphan()
    _stale_row(root)  # older identity (different start time), owner dead: an imperfect leftover
    path = _journal("dead-gen", _record(root, owner=1))  # the CURRENT root's real identity

    pty_registry.reap_stale(grace=0.2)

    assert _wait_gone(root), "the journalled root must be verified reaped, not silently dropped"
    assert _alive(unrelated), "no unrelated process may be signalled"
    assert _fence() is None and not path.exists() and str(root) not in pty_registry._load()


def test_f1r4_an_unresolvable_identity_mismatch_fences_and_keeps_the_journal_and_the_root():
    root, unrelated = _orphan(), _orphan()
    # The older row is owned by a LIVE backend (this one): it cannot be dropped, so the two identities
    # for one pid stay in conflict and nothing may be declared clean.
    _stale_row(root, owner=os.getpid(), generation="live-owner-gen")
    path = _journal("dead-gen", _record(root, owner=1))

    assert pty_registry.reap_stale(grace=0.2) == []

    assert _alive(root) and _alive(unrelated), "nothing may be signalled while the identities conflict"
    assert _fence() and path.exists(), "an unresolved mismatch must fence and keep the journal"
    row = pty_registry._load()[str(root)]
    assert row["runtime_generation"] == "live-owner-gen", "the existing proof is retained, not overwritten"


def test_f1r4_a_matching_row_is_not_a_conflict_and_the_normal_path_is_unchanged():
    root = _orphan()
    _register(root, generation="dead-gen", owner=1)  # the registered row IS the journalled identity
    _journal("dead-gen", _record(root, owner=1))

    pty_registry.reap_stale(grace=0.2)

    assert _wait_gone(root) and _fence() is None


def test_f1r4_a_pid_journalled_twice_in_one_generation_supersedes_to_the_later_identity():
    # pid cannot be shared by two live processes: the earlier birth was reaped before the later one.
    root = _orphan()
    older = {**_record(root, owner=1, lstart="Thu Jan  1 00:00:00 1970")}
    _journal("dead-gen", older, _record(root, owner=1))

    pty_registry.reap_stale(grace=0.2)

    assert _wait_gone(root) and _fence() is None


# ── review-5 N3: a PID collision must not erase an older row's recorded descendants ─────────────

def _older_row_with_descendant(root: int, descendant: int) -> None:
    """A dead backend's older row for the pid the journalled root now holds (forged older start
    time: this proves the branch, it does not force kernel PID recycling), with one REAL, live,
    independently identified detached descendant recorded by its own start time."""
    entries = pty_registry._load()
    entries[str(root)] = {"argv0": "older", "lstart": "Thu Jan  1 00:00:00 1970", "owner": 1,
                          "runtime_generation": "older-gen",
                          "descendants": {str(descendant): osplat.process_tree.start_time(descendant)}}
    pty_registry._save(entries)


def test_n3_a_collision_cleans_the_older_rows_recorded_descendant_and_the_journalled_root():
    root, descendant, unrelated = _orphan(), _orphan(), _orphan()
    _older_row_with_descendant(root, descendant)
    path = _journal("dead-gen", _record(root, owner=1))

    pty_registry.reap_stale(grace=0.2)

    assert _wait_gone(descendant), "the older row's recorded descendant was dropped with its row"
    assert _wait_gone(root), "the journalled root must be verified reaped"
    assert _alive(unrelated)
    assert _fence() is None and not path.exists() and pty_registry._load() == {}


def test_n3_a_surviving_recorded_descendant_keeps_its_row_the_journal_and_the_fence(monkeypatch):
    root, descendant, unrelated = _orphan(), _orphan(), _orphan()
    _older_row_with_descendant(root, descendant)
    path = _journal("dead-gen", _record(root, owner=1))
    monkeypatch.setattr(pty_registry, "_signal_each", lambda targets, *, force: None)  # nothing dies

    pty_registry.reap_stale(grace=0.0, settle=0.2)

    assert _alive(descendant) and _alive(root) and _alive(unrelated)
    assert _fence(), "an unverified recorded descendant must keep admission fenced"
    assert path.exists(), "the journal must stay while the collision is unresolved"
    row = pty_registry._load().get(str(root))
    assert row and str(descendant) in (row.get("descendants") or {}), "the descendant's identity was erased"


# ── a Windows root whose FILETIME cannot be proved equal is an unresolved fact, never a dropped one ──────

def _windows_record(pid: int) -> dict:
    return {**_record(pid, owner=1), "start_kind": "windows-filetime", "start_value": "133800000000000000"}


def _inspect(monkeypatch, verdict: str, lstart: str = ""):
    from agent_team_backend import cli_runtime_windows
    monkeypatch.setattr(cli_runtime_windows, "inspect_start", lambda pid, filetime: (verdict, lstart))
    return cli_runtime_windows


@pytest.mark.parametrize("verdict", ["differs", "unverifiable"])
def test_a_windows_root_that_cannot_be_proved_equal_is_kept_signals_nothing_and_fences(monkeypatch, verdict):
    root = _orphan()
    _inspect(monkeypatch, verdict)
    path = _journal("dead-gen", _windows_record(root))

    assert pty_registry._ingest_stale_journals() is False
    assert pty_registry.reap_stale(grace=0.2) == []

    assert _alive(root) and path.exists(), "the journal that names the root must be kept"
    assert _fence(), "the fence must not open while an unverifiable record exists"
    assert str(root) not in pty_registry._load(), "an unmatchable identity must not enter the registry"


def test_the_loss_path_sees_an_unverifiable_windows_record_instead_of_an_empty_journal(monkeypatch):
    root = _orphan()
    _inspect(monkeypatch, "differs")
    path = _journal("lost-gen", _windows_record(root))

    records = pty_registry.ingest_journal(path, os.getpid())

    assert [r["pid"] for r in records if r.get("unverifiable")] == [root]
    assert _alive(root) and str(root) not in pty_registry._load()


def test_a_windows_root_that_is_verifiably_absent_is_dropped_and_the_fence_opens(monkeypatch):
    root = _orphan()
    _inspect(monkeypatch, "absent")
    path = _journal("dead-gen", _windows_record(root))

    assert pty_registry.reap_stale(grace=0.2) == []

    assert _fence() is None and not path.exists()


def test_a_matching_windows_root_enters_the_registry_by_the_identity_the_inspection_read(monkeypatch):
    root = _orphan()
    _inspect(monkeypatch, "match", lstart=osplat.process_tree.start_time(root))
    _journal("dead-gen", _windows_record(root))

    signalled = pty_registry.reap_stale(grace=0.2)

    assert root in signalled and _wait_gone(root)
