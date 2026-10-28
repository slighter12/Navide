"""LogWatcher: an activity error that made progress is followed by another poll.

A single append raises a single watch event. When the reader advances its
high-water past an offending record and then raises, the records after it are
still unread, and nothing else would trigger a poll for them (G2 ticket 10).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_team_backend.log_readers.base import (
    ActivityEvent,
    LogReader,
    activity_high_water,
    set_activity_high_water,
)
from agent_team_backend.log_readers.watcher import LogWatcher
from agent_team_backend.tokens_store import TokensStore


class _ErrorProgressReader(LogReader):
    """Raises on a non-object record after advancing past it, like the native Claude reader."""

    vendor = "claude"
    activity_resumes_by_line = True

    def __init__(self, root: Path) -> None:
        self.root = root

    def project_dirs(self) -> list[Path]:
        return [self.root]

    def session_files(self) -> list[Path]:
        return sorted(self.root.rglob("*.jsonl"))

    def parse_session_file(self, path: Path, seen_keys: set[str]) -> list:
        return []

    def claims_path(self, path: Path) -> bool:
        return str(path).startswith(str(self.root))

    def parse_activity(self, path: Path, seen_keys: set[str]) -> list[ActivityEvent]:
        high_water = activity_high_water(seen_keys)
        out: list[ActivityEvent] = []
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line_no <= high_water:
                continue
            record = json.loads(raw)
            set_activity_high_water(seen_keys, line_no)
            if not isinstance(record, dict):
                raise RuntimeError("record is not an object")
            out.append(ActivityEvent(
                vendor=self.vendor, event_type="turn_complete", cwd="/ws", session_id=path.stem,
                file_path=str(path), dedup_key=f"act:{line_no}", timestamp="",
            ))
        return out


@pytest.mark.asyncio
async def test_records_after_an_error_with_progress_are_read_without_another_file_event(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    path = root / "a.jsonl"
    path.write_text('{"type": "assistant"}\nnull\n{"type": "assistant"}\n', encoding="utf-8")
    delivered: list[str] = []

    async def sink(event: ActivityEvent) -> None:
        delivered.append(event.dedup_key)

    async def tokens(_usage: object) -> None:
        return None

    store = TokensStore(
        global_path=tmp_path / "tokens.json",
        workspace_base_dir=tmp_path / "workspaces",
        ingestion_state_path=tmp_path / "token-ingestion-state.json",
    )
    watcher = LogWatcher(sink=tokens, activity_sink=sink,  # type: ignore[arg-type]
                         checkpoint_provider=store.get_ingestion_checkpoint,
                         checkpoint_sink=store.advance_ingestion_checkpoint)
    watcher.add_reader(_ErrorProgressReader(root))
    drain = asyncio.create_task(watcher._drain_loop())
    try:
        # One event, as a single append raises; the follow-up poll must come from the watcher itself.
        watcher._queue.put_nowait((path, ""))
        async with asyncio.timeout(5):
            while delivered != ["act:3"]:
                await asyncio.sleep(0.01)
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)


@pytest.mark.asyncio
async def test_progress_made_before_an_error_is_persisted_when_the_bad_record_is_last(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    path = root / "a.jsonl"
    path.write_text('{"type": "assistant"}\nnull\n', encoding="utf-8")

    async def tokens(_usage: object) -> None:
        return None

    async def sink(_event: ActivityEvent) -> None:
        return None

    store = TokensStore(
        global_path=tmp_path / "tokens.json",
        workspace_base_dir=tmp_path / "workspaces",
        ingestion_state_path=tmp_path / "token-ingestion-state.json",
    )
    watcher = LogWatcher(sink=tokens, activity_sink=sink,  # type: ignore[arg-type]
                         checkpoint_provider=store.get_ingestion_checkpoint,
                         checkpoint_sink=store.advance_ingestion_checkpoint)
    watcher.add_reader(_ErrorProgressReader(root))
    # The follow-up poll finds nothing new and makes no progress, so only the error poll can persist.
    await watcher._process_realtime_path(path)
    stored = store.get_ingestion_checkpoint(str(path.resolve()), "@activity")
    assert "act_hw::2" in stored.get("keys", [])
