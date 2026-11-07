"""LogWatcher: a file that changes while it is being walked is walked again.

A walk reads the file as it is when it runs. A commit or append that lands after
that read is only seen by a later walk, and the later walk is normally queued by
the file event the change raises. That event can be missing: a SQLite WAL commit
made a few milliseconds after the store's previous event produced none in the
real-observer run (test_copilot_store_real_observer), and before one-entry-per-file
coalescing the redundant entries of earlier events happened to cover for it. The
watcher therefore compares the file's signature before and after a walk and
queues it again when it moved, instead of trusting that an event will.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_team_backend.log_readers.base import ActivityEvent, LogReader
from agent_team_backend.log_readers.watcher import LogWatcher


class _MutatingReader(LogReader):
    """Changes the file under walk during its first parse, as a writer racing the walk does."""

    vendor = "claude"

    def __init__(self, root: Path, mutate) -> None:  # noqa: ANN001
        self.root = root
        self.mutate = mutate
        self.parse_calls = 0

    def project_dirs(self) -> list[Path]:
        return [self.root]

    def session_files(self) -> list[Path]:
        return []

    def parse_session_file(self, path: Path, seen_keys: set[str]) -> list:
        return []

    def claims_path(self, path: Path) -> bool:
        return str(path).startswith(str(self.root))

    def parse_activity(self, path: Path, seen_keys: set[str]) -> list[ActivityEvent]:
        self.parse_calls += 1
        if self.parse_calls == 1:
            self.mutate(path)
        return []


def _append(path: Path) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{}\n")


def _append_within_the_same_tick(path: Path) -> None:
    # A file system with a coarse timestamp (ext4, some Windows volumes) gives an append inside the same
    # tick the mtime it already had; os.utime puts that back so the size is the only thing that moved.
    stat = path.stat()
    _append(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == stat.st_mtime_ns


def _wal_commit(path: Path) -> None:
    # The main file is untouched until a checkpoint; the commit lands in the -wal.
    wal = path.with_name(path.name + "-wal")
    wal.write_bytes(wal.read_bytes() + b"commit")


async def _activity(_event: ActivityEvent) -> None:
    return None


async def _tokens(_usage: object) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "mutate"),
    [("t.jsonl", _append), ("t.jsonl", _append_within_the_same_tick), ("store.db", _wal_commit)],
    ids=["append", "append-with-frozen-mtime", "wal-commit"],
)
async def test_a_change_during_the_walk_queues_the_file_again_without_a_file_event(
        tmp_path: Path, name: str, mutate) -> None:  # noqa: ANN001
    root = tmp_path / "root"
    root.mkdir()
    target = root / name
    target.write_text("{}\n", encoding="utf-8")
    if name.endswith(".db"):
        target.with_name(name + "-wal").write_bytes(b"wal")
    reader = _MutatingReader(root, mutate)
    watcher = LogWatcher(sink=_tokens, activity_sink=_activity)  # type: ignore[arg-type]
    watcher.add_reader(reader)
    watcher._loop = asyncio.get_running_loop()  # noqa: SLF001
    drain = asyncio.create_task(watcher._drain_loop())  # noqa: SLF001
    try:
        # One entry, as coalescing leaves it; the change raises no event.
        watcher._enqueue_realtime(target)  # noqa: SLF001
        deadline = asyncio.get_running_loop().time() + 5
        while reader.parse_calls < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        # The walk that raced the change, and the one that follows it. A third would mean a walk
        # that changes nothing keeps queueing itself.
        assert reader.parse_calls == 2
    finally:
        drain.cancel()


def test_the_sweep_gate_sees_an_append_that_left_the_mtime_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "t.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    watcher = LogWatcher(sink=_tokens, activity_sink=_activity)  # type: ignore[arg-type]
    assert watcher._changed_since_last_scan([target]) == [target]  # noqa: SLF001
    assert watcher._changed_since_last_scan([target]) == []  # noqa: SLF001
    _append_within_the_same_tick(target)
    assert watcher._changed_since_last_scan([target]) == [target]  # noqa: SLF001
