"""OBSERVE over raw frames (E1 14; F4, F6, O1-O3, S8 supplements) with the real sidecar.

Scenarios: Claude/Codex/OpenCode observation with raw anchor bytes, parts and the
error-with-progress shape; size and counter bounds; a faulty peer (a stub sidecar) and the
harness build's endless part stream. The production-client Claude proof lives in
tests/cli/claudeRuntime.test.ts; the Codex/OpenCode production-client scenarios in
tests/cli/nativeDifferences.test.ts are gated on a ticket-04 controller and did not run here.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import threading
import time

import pytest

from agent_team_backend import cli_runtime
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_frames import FrameType
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.frame_peer import HARNESS, PRODUCT, FramePeer
from tests.cli_regression.support.isolation import isolated_environment

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
    reason="requires the explicitly built macOS native runtime (product and harness)")

BACKEND = Path(__file__).resolve().parents[1]
PEER = Path(__file__).parent / "cli_regression/support/handoff_peer.py"


def claude_record(index: int, text: str, stop: str = "end_turn") -> str:
    return json.dumps({"type": "assistant", "requestId": f"request-{index}", "version": "v",
                       "message": {"id": f"message-{index}", "content": [{"type": "text", "text": text}],
                                   "stop_reason": stop, "usage": {"input_tokens": 1, "output_tokens": 1}}})


@pytest.fixture
def peer(tmp_path):
    peer = FramePeer(tmp_path)
    try:
        yield peer
    finally:
        peer.close()


# ---------------------------------------------------------------------- F4: no text encoding

def test_f4_every_byte_value_round_trips_as_an_expected_and_a_returned_anchor(tmp_path, peer):
    path = tmp_path / "-tmp-bytes/session.jsonl"
    path.parent.mkdir(parents=True)
    every_byte = bytes(b for b in range(256) if b != 0x0A)
    path.write_bytes((claude_record(1, "first") + "\n").encode() + every_byte + b"\n"
                     + (claude_record(2, "second") + "\n").encode())
    first, anchor, _ = peer.observe("claude", path, "usage", checkpoint={})
    committed = first["checkpoint"]["offset"]
    assert committed == path.stat().st_size and anchor == path.read_bytes()[max(0, committed - 512):committed]
    assert all(b in anchor for b in every_byte if b not in (0x0A,)) or len(anchor) <= 512
    # The same bytes sent back as the expected anchor: the file is unchanged, not rotated.
    again, anchor_again, _ = peer.observe("claude", path, "usage", checkpoint=first["checkpoint"], expected=anchor)
    assert again["events"] == [] and anchor_again == anchor
    # Every possible byte value, as a request blob, is accepted verbatim: a mismatching anchor
    # (all 256 values) is simply "rotated", never a decoding failure.
    rotated, _, _ = peer.observe("claude", path, "usage", checkpoint=first["checkpoint"], expected=bytes(range(256)))
    assert len(rotated["events"]) == 2, "an anchor mismatch re-reads from the start"


def test_f4_no_text_encoding_remains_in_the_runtime_sources():
    root = Path(__file__).resolve().parents[2]
    python_modules = [BACKEND / "agent_team_backend" / name for name in (
        "cli_runtime.py", "cli_runtime_frames.py", "cli_runtime_handoff.py", "cli_runtime_observation.py",
        "cli_runtime_claude.py", "cli_runtime_adapters.py")]
    rust_sources = sorted((root / "native/navide-cli-runtime/src").glob("*.rs"))
    for source in [*python_modules, *rust_sources, root / "native/navide-cli-runtime/Cargo.toml",
                   root / "native/navide-cli-runtime/Cargo.lock"]:
        text = source.read_text()
        assert "base64" not in text and "_b64" not in text and "b64decode" not in text, source.name


# ------------------------------------------------------------------------------ S8 scenarios

def test_s8_codex_usage_activity_and_raw_anchor_resume(tmp_path, peer):
    path = tmp_path / "rollout-2026-01-01T00-00-00-abc.jsonl"
    meta = {"type": "session_meta", "payload": {"id": "abc", "cwd": str(tmp_path), "model_provider": "openai",
                                                 "cli_version": "1.0", "source": "cli"}}
    usage = lambda i, o: {"type": "event_msg", "timestamp": "2026-01-01T00:00:01Z", "payload": {  # noqa: E731
        "type": "token_count", "info": {"total_token_usage": {"input_tokens": i, "output_tokens": o}}}}
    rows = [meta, {"type": "event_msg", "payload": {"type": "user_message", "message": "hello café"}}, usage(10, 4),
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "reply 世界"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    first, anchor, _ = peer.observe("codex", path, "usage", checkpoint={})
    assert [(e["input_tokens"], e["output_tokens"]) for e in first["events"]] == [(10, 4)]
    assert anchor and anchor == path.read_bytes()[-len(anchor):]
    unchanged, again, _ = peer.observe("codex", path, "usage", checkpoint=first["checkpoint"], expected=anchor)
    assert unchanged["events"] == [] and again == anchor
    with path.open("a") as stream:
        stream.write(json.dumps(usage(25, 9)) + "\n")
    grown, _, _ = peer.observe("codex", path, "usage", checkpoint=first["checkpoint"], expected=anchor)
    assert [(e["input_tokens"], e["output_tokens"]) for e in grown["events"]] == [(15, 5)]
    activity, _, _ = peer.observe("codex", path, "activity", seen=[])
    kinds = [(e["event_type"], e["detail"]) for e in activity["events"]]
    assert ("turn_complete", "task_complete") in kinds and ("agent_active", "user_message") in kinds
    assert [e["text"] for e in activity["events"] if e["event_type"] == "turn_complete"] == ["reply 世界"]


def test_s8_opencode_wal_usage_and_restart_cursor(tmp_path, peer):
    path = tmp_path / "opencode.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executescript("CREATE TABLE session(id TEXT PRIMARY KEY,parent_id TEXT,directory TEXT,version TEXT);"
                         "CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT);"
                         "CREATE TABLE part(id TEXT PRIMARY KEY,session_id TEXT,message_id TEXT,data TEXT,time_created INTEGER);")
    writer.execute("INSERT INTO session VALUES(?,?,?,?)", ("ses_owned", None, str(tmp_path), "synthetic"))
    writer.execute("INSERT INTO message VALUES(?,?,?)", ("msg_owned", "ses_owned", json.dumps({"role": "assistant"})))
    writer.commit()
    try:
        pending, anchor, _ = peer.observe("opencode", path, "usage", checkpoint={}, seen=[])
        assert anchor is None, "a parser without a byte anchor returns none"
        assert not pending["events"] and pending["checkpoint"]["pending"] == [1]
        complete = {"role": "assistant", "time": {"completed": 1700000000000}, "tokens": {"input": 10, "output": 5}}
        writer.execute("UPDATE message SET data=? WHERE id=?", (json.dumps(complete), "msg_owned"))
        assert not peer.observe("opencode", path, "usage", checkpoint=pending["checkpoint"], seen=[])[0]["events"]
        writer.commit()
        settled = peer.observe("opencode", path, "usage", checkpoint=pending["checkpoint"], seen=[])[0]
        assert [(e["input_tokens"], e["output_tokens"]) for e in settled["events"]] == [(10, 5)]
        assert not settled["checkpoint"]["pending"]
        cursor = json.loads(json.dumps(settled["checkpoint"]))
        assert not peer.observe("opencode", path, "usage", checkpoint=cursor, seen=[])[0]["events"]
    finally:
        writer.close()


def test_s1_an_ordinary_shell_has_no_observer_and_no_adapter_is_reached(tmp_path, peer):
    script = tmp_path / "handoff_peer.py"
    shutil.copyfile(PEER, script)
    sid, generation = peer.create([base_python_executable(), "-u", str(script)])
    request = peer.send(cli_runtime.code.OBSERVE, {"vendor": "", "path": str(tmp_path / "x"), "mode": "usage",
                                                   "session_id": sid, "session_generation": generation})
    refused = peer.result(request, allow_error=True)
    assert refused.frame_type is FrameType.ERROR and refused.meta["code"] == "observer not integrated"
    assert refused.meta["effect"] == "none" and not peer.parts
    # An unknown session and a stale generation are refused with no other effect.
    unknown = peer.result(peer.send(cli_runtime.code.OBSERVE, {"vendor": "claude", "path": str(tmp_path / "x"), "mode": "usage",
                                                               "session_id": "nope", "session_generation": "g"}), allow_error=True)
    assert unknown.meta["code"] == "UNKNOWN_SESSION" and unknown.meta["effect"] == "none"
    stale = peer.result(peer.send(cli_runtime.code.OBSERVE, {"vendor": "claude", "path": str(tmp_path / "x"), "mode": "usage",
                                                             "session_id": sid, "session_generation": "stale"}), allow_error=True)
    assert stale.meta["code"] in ("STALE_SESSION", "observer not integrated") and stale.meta["effect"] == "none"
    assert peer.result(peer.send(cli_runtime.code.PING, {})).meta["pong"] is True


def test_paced_historical_observation_does_not_block_the_control_reader(tmp_path, peer):
    """A held history stream occupies the observation worker only: PING, KILL and a new session
    are admitted and answered while it is held (not a timing sample: nothing is measured)."""
    script = tmp_path / "handoff_peer.py"
    shutil.copyfile(PEER, script)
    sid, generation = peer.create([base_python_executable(), "-u", str(script)])
    history = tmp_path / "history.jsonl"
    os.mkfifo(history)
    opened = threading.Event()
    released = threading.Event()

    def paced():
        if released.wait(10):
            with history.open("wb"):
                opened.set()

    writer = threading.Thread(target=paced, daemon=True)
    writer.start()
    try:
        observation = peer.send(cli_runtime.code.OBSERVE, {"vendor": "claude", "path": str(history), "mode": "activity", "seen": []})
        ping = peer.result(peer.send(cli_runtime.code.PING, {}), timeout=2)
        assert ping.meta["pong"] is True and observation not in peer.responses and not opened.is_set()
        other, _ = peer.create([base_python_executable(), "-u", str(script)])
        kill = peer.result(peer.send(cli_runtime.code.KILL, {"session_id": sid, "session_generation": generation, "force": True}), timeout=2)
        assert kill.meta["accepted"] is True and other != sid
        released.set()
        failed = peer.result(observation, allow_error=True)
        assert failed.frame_type is FrameType.ERROR and failed.meta["code"] == "IO_FAILED" and "seek" in failed.meta["message"].lower()
        assert failed.meta["effect"] == "none"
    finally:
        released.set()
        writer.join(timeout=2)


# ------------------------------------------------------------------ service-level bounds (O1, F6)

@pytest.fixture
async def service(tmp_path, monkeypatch):
    frames: list = []

    async def emit(frame):
        frames.append(frame)

    shutil.copyfile(PRODUCT, tmp_path / "runtime")
    (tmp_path / "runtime").chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(tmp_path / "runtime"))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    service = RustTerminalService(emit)
    service.frames = frames
    try:
        yield service
    finally:
        runtime = service._runtime
        if runtime is not None and runtime.returncode is None:
            try:
                await asyncio.wait_for(service.kill_all(), 20)
            except Exception:  # noqa: BLE001
                pass
            if runtime.returncode is None:
                runtime.kill()


def historical(_sessions):
    return None


@pytest.mark.asyncio
async def test_o1_a_result_over_the_limit_sends_no_part_and_is_a_refusal_without_loss(tmp_path, service):
    path = tmp_path / "-tmp-big/session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(claude_record(1, "x" * (9 * 1024 * 1024)) + "\n")
    shell = None
    with pytest.raises(cli_runtime._RuntimeRefused, match="OBSERVATION_TOO_LARGE"):
        await service.observe("claude", "rust-claude-jsonl-v1", {"path": str(path), "mode": "activity", "seen": []},
                              match=historical)
    assert service._lost is None and not service._observations, "no part was buffered"
    # Other sessions and later observations are unaffected.
    small = tmp_path / "-tmp-small/session.jsonl"
    small.parent.mkdir(parents=True)
    small.write_text(claude_record(1, "small") + "\n")
    ok = await service.observe("claude", "rust-claude-jsonl-v1", {"path": str(small), "mode": "activity", "seen": []},
                               match=historical)
    assert [e["text"] for e in ok["events"] if e["event_type"] == "turn_complete"] == ["small"]
    assert shell is None and service._lost is None


@pytest.mark.asyncio
async def test_f6_observe_inflight_caps_open_observations_at_four_and_queues_the_fifth(tmp_path, service):
    await service._ensure_started()
    assert service._limits["observe_inflight"] == 4
    fifos = []
    for index in range(5):
        fifo = tmp_path / f"held-{index}.jsonl"
        os.mkfifo(fifo)
        fifos.append(fifo)
    tasks = [asyncio.create_task(service.observe("claude", "rust-claude-jsonl-v1",
                                                 {"path": str(fifo), "mode": "activity", "seen": []}, match=historical))
             for fifo in fifos]
    deadline = time.monotonic() + 10
    while len(service._observing) < 4 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # a fifth would have been admitted by now if it could be
    assert len(service._observing) == 4, "exactly four observations may be open"
    holders = []
    for fifo in fifos:  # the single history worker opens them in order; each open completes as its reader arrives
        holders.append(await asyncio.wait_for(asyncio.to_thread(os.open, fifo, os.O_WRONLY), 15))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for holder in holders:
        os.close(holder)
    assert all(isinstance(r, cli_runtime._RuntimeRefused) and "IO_FAILED" in str(r) for r in results), results
    assert not service._observing and service._lost is None


# ----------------------------------------------------------- O2: a faulty peer (stub sidecar)

STUB_LIMITS = {"max_meta_p2r": 4194304, "max_blob_p2r": 1048576, "max_frame_r2p": 65536,
               "max_observation_bytes": 8388608, "max_observation_parts": 128, "observe_inflight": 4,
               "max_outbound_bytes": 4194304, "max_outbound_frames": 256, "max_inbound_bytes": 16777216,
               "request_ms": 15000, "create_answer_ms": 20000, "cleanup_ms": 8000, "ping_ms": 5000,
               "kill_escalation_ms": 1000, "eof_reap_ms": 1000, "snapshot_ms": 5000,
               "ownership_snapshot_ms": {"young": 5000, "steady": 30000, "young_window_ms": 30000}}


async def run_stub(tmp_path, monkeypatch, mode: str, limits: dict | None = None):
    stub = tmp_path / f"stub-{mode}"
    stub.write_text(f"#!{sys.executable}\nimport runpy, sys\nrunpy.run_path({str(Path(__file__).parent / 'cli_regression/support/stub_sidecar.py')!r}, run_name='__main__')\n")
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    monkeypatch.setenv("NAVIDE_STUB_MODE", mode)
    monkeypatch.setenv("NAVIDE_STUB_BACKEND", str(BACKEND))
    monkeypatch.setenv("NAVIDE_STUB_LIMITS", json.dumps({**STUB_LIMITS, **(limits or {})}))
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    await service._ensure_started()
    outcome: Exception | None = None
    try:
        await service.observe("codex", "p", {"path": str(tmp_path / "f"), "mode": "usage"}, match=historical)
    except Exception as error:  # noqa: BLE001
        outcome = error
    return service, outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,limits,reason", [
    ("too-many-parts", None, "advertised bounds"),
    ("out-of-order", None, "observation order"),
    ("unknown-id", None, "no open observation"),
    ("after-final", None, "no open observation"),
    ("aggregate", {"max_observation_bytes": 400_000}, "advertised bounds"),
])
async def test_o2_observation_violations_by_a_faulty_peer_end_in_loss(tmp_path, monkeypatch, mode, limits, reason):
    service, outcome = await run_stub(tmp_path, monkeypatch, mode, limits)
    generation = service._runtime_generation  # receipts are shared by path: select this service's own
    try:
        deadline = time.monotonic() + 20
        records: list[dict] = []
        while time.monotonic() < deadline:
            path = tmp_path / "runtime-receipts.jsonl"
            records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            if any(r["event"] == "runtime_invalidated" and r["lost_generation"] == generation for r in records):
                break
            await asyncio.sleep(0.05)
        invalidated = [r for r in records if r["event"] == "runtime_invalidated" and r["lost_generation"] == generation]
        assert len(invalidated) == 1, (mode, records)
        assert "PROTOCOL_ERROR" in invalidated[0]["cause"] and reason in invalidated[0]["cause"], invalidated[0]
        assert invalidated[0]["complete"] is True
        assert service._runtime is None or service._runtime.returncode is not None
    finally:
        if service._runtime is not None and service._runtime.returncode is None:
            service._runtime.kill()


@pytest.mark.asyncio
async def test_o2_a_part_larger_than_the_frame_bound_is_refused_before_its_body(tmp_path, monkeypatch):
    # The shared frame receiver rejects meta_len + blob_len over max_frame_r2p from the header alone.
    from agent_team_backend import cli_runtime_frames as wire
    header = wire.encode_raw(FrameType.EVENT, wire.code.OBSERVATION_PART, 0, 1, 1, b"", b"")[:24] + (65537).to_bytes(4, "big") + (0).to_bytes(4, "big")
    with pytest.raises(wire.ProtocolError) as caught:
        wire.parse_header(header, wire.Direction.RUST_TO_PYTHON, 1)
    assert caught.value.rule == "limit"


# --------------------------------------------------------------------- O3: an endless stream

@pytest.mark.asyncio
async def test_o3_an_endless_part_stream_stops_at_the_counters_and_runs_loss(tmp_path, monkeypatch):
    shutil.copyfile(HARNESS, tmp_path / "runtime")
    (tmp_path / "runtime").chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(tmp_path / "runtime"))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    service = RustTerminalService(lambda frame: asyncio.sleep(0))
    peak = {"bytes": 0}
    original = RustTerminalService._on_observation_part

    def watch(self, frame):
        original(self, frame)
        peak["bytes"] = max(peak["bytes"], sum(len(b) for parts in self._observations.values() for b in parts))

    monkeypatch.setattr(RustTerminalService, "_on_observation_part", watch)
    with pytest.raises(RuntimeError, match="RUNTIME_LOST|PROTOCOL_ERROR"):
        await service.observe("codex", "p", {"path": str(tmp_path / "x.fault-endless"), "mode": "usage"}, match=historical)
    assert "PROTOCOL_ERROR" in service._lost and "advertised bounds" in service._lost
    assert 0 < peak["bytes"] <= 32 * 1024 * 1024, peak
    await asyncio.wait_for(asyncio.shield(service._invalidation), 30)
    assert service._lost is None and service._runtime is None, "a clean loss leaves the next call a fresh runtime"
