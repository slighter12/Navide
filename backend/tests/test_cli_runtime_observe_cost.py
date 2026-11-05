"""Per-poll cost of the ported observers through a `FramePeer` proxy, on matched fixtures (G2 ticket 06).

One poll is what the watcher does for a file: `parse_incremental` with the saved checkpoint and `parse_activity` with
the saved bag. The Python path is the baseline reader; the native path is the same vendor's `Native<Vendor>Reader`
with its `observe_core` replaced by a copy that talks to a real sidecar over a `FramePeer` frame socket. That is a
PROXY for the production bridge: it counts the reader's own work, the request, the sidecar's read and the answer, and
leaves out `RustTerminalService.observe`'s asyncio hop, its pane matching and its receipt work. Both paths read the
same fixture file on the same host.

Two series per vendor and path, each after 20 warm-up polls and over 200 measured ones (the accepted section 11.7
protocol): an UNCHANGED file, and a file with one record APPENDED before every poll (the append is not timed; a SQLite store
declares an `append` template whose `${N}` is a fresh id per poll, and its record count must grow by exactly one). Each
series reports the checkpoint's key count and the activity bag's size at its end. The test asserts that both paths
answered, never a timing, and appends one JSON row per vendor to the file named by `NAVIDE_G2_COST_OUT` when set.
No speedup is claimed from these numbers.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import sys
import time

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_runtime_frames import FrameType, code
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.vendor_data import VendorData
from tests.test_cli_runtime_observe_vendors import PORTED, peer  # noqa: F401  (the sidecar fixture)

pytestmark = [pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only (D2)")]

POLLS = 200
WARMUP = 20
NATIVE = {
    "aider": cli_runtime_native.native_reader(VENDORS["aider"].make_log_reader, "aider"),
    "antigravity": cli_runtime_native.native_reader(VENDORS["antigravity"].make_log_reader, "antigravity"),
    "copilot": cli_runtime_native.native_reader(VENDORS["copilot"].make_log_reader, "copilot"),
    "cursor": cli_runtime_native.native_reader(VENDORS["cursor"].make_log_reader, "cursor"),
    "droid": cli_runtime_native.native_reader(VENDORS["droid"].make_log_reader, "droid"),
    "goose": cli_runtime_native.native_reader(VENDORS["goose"].make_log_reader, "goose"),
    "grok": cli_runtime_native.native_reader(VENDORS["grok"].make_log_reader, "grok"),
    "kilo": cli_runtime_native.native_reader(VENDORS["kilo"].make_log_reader, "kilo"),
    "kimi": cli_runtime_native.native_reader(VENDORS["kimi"].make_log_reader, "kimi"),
    "muse": cli_runtime_native.native_reader(VENDORS["muse"].make_log_reader, "muse"),
    "opencode": cli_runtime_native.native_reader(VENDORS["opencode"].make_log_reader, "opencode"),
    "pi": cli_runtime_native.native_reader(VENDORS["pi"].make_log_reader, "pi"),
    "qwen": cli_runtime_native.native_reader(VENDORS["qwen"].make_log_reader, "qwen"),
}


def _through_core(peer):
    """`cli_runtime_observation.observe_core` with the sidecar's answer read straight off a `FramePeer`: the sidecar
    verifies the checkpoint's anchor itself."""
    def observe_core(vendor, path, mode, *, checkpoint=None, seen=None, pane_id=None, match=None, validate=None,
                     receipt=None, **extra):
        meta = {"vendor": vendor, "path": str(path), "mode": mode, "checkpoint": dict(checkpoint or {}),
                "seen": sorted(seen or set()), **extra}
        request = peer.send(code.OBSERVE, meta)
        final = peer.result(request, allow_error=True)
        if final.frame_type is FrameType.ERROR and not final.meta.get("observation_parts"):
            raise AssertionError(final.meta)
        parts = peer.parts.pop(request, [])
        peer._sizes.pop(request, None)
        return json.loads(b"".join(parts))
    return observe_core


def _poll(reader, path, append=None):
    """The warmed-up per-poll cost samples (seconds) of `reader`, and the state they ended with. `append`, when given,
    writes one record before every poll (outside the timed part)."""
    checkpoint, bag, samples = {}, set(), []
    for number in range(WARMUP + POLLS):
        if append is not None:
            append()
        start = time.perf_counter()
        result = reader.parse_incremental(path, checkpoint)
        checkpoint = result.checkpoint
        reader.parse_activity(path, bag)
        elapsed = time.perf_counter() - start
        if number >= WARMUP:
            samples.append(elapsed)
    return samples, {"checkpoint_keys": len(checkpoint), "activity_keys": len(bag)}


def _stats(samples):
    ordered = sorted(samples)
    return {"polls": len(ordered), "median_ms": round(statistics.median(ordered) * 1000, 3),
            "p95_ms": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] * 1000, 3)}


def _total_rows(path):
    connection = sqlite3.connect(path)
    try:
        tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return sum(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables)
    finally:
        connection.close()


def _appender(data):
    """The write before each poll: one new record of the vendor's own shape. A text store gets the completion line
    again. A SQLite store with an `append` template gets a fresh id per call (`${N}`) and must grow by exactly one
    record; one without (Goose, whose message id is not unique) re-runs its completion inserts, each of which must add
    a row."""
    sqlite_store = any(operation["kind"] == "sqlite" for operation in data.fixture["phases"].get("complete", []))
    inserts = sum(statement["sql"].lstrip().upper().startswith("INSERT") for operation in
                  data.fixture["phases"].get("complete", []) for statement in operation.get("statements", []))
    state = {"calls": 0}

    def append():
        state["calls"] += 1
        if "append" in data.fixture:
            before = data.count()
            data.append(state["calls"])
            assert data.count() == before + 1, "the append must add exactly one record"
        elif sqlite_store:
            before = _total_rows(data.path)
            data.apply("complete")
            assert _total_rows(data.path) == before + inserts, "every insert of the completion write must add a row"
        else:
            data.apply("complete")
    return append


def _series(vendor, root, monkeypatch, set_home, make_reader, appended):
    """One series on a fresh copy of the vendor's fixture: (samples, final state)."""
    root.mkdir(parents=True)
    data = VendorData(vendor, root, monkeypatch, set_home)
    try:
        for phase in ("initial", "response", "complete"):
            data.apply(phase)
        return _poll(make_reader(), data.path, _appender(data) if appended else None)
    finally:
        data.close()


@pytest.mark.timeout(900)
@pytest.mark.parametrize("vendor", PORTED)
def test_per_poll_cost_through_a_framepeer_proxy_is_measured(vendor, tmp_path, monkeypatch, set_home, peer):
    assert type(VENDORS[vendor].make_log_reader()) is not NATIVE[vendor], "the baseline must be the Python reader"
    monkeypatch.setattr(cli_runtime_native, "observe_core", _through_core(peer))
    row = {"vendor": vendor, "platform": sys.platform, "bridge": "FramePeer proxy", "warmup": WARMUP, "polls": POLLS}
    for series, appended in (("unchanged", False), ("appended", True)):
        python, python_state = _series(vendor, tmp_path / f"python-{series}", monkeypatch, set_home,
                                       VENDORS[vendor].make_log_reader, appended)
        native, native_state = _series(vendor, tmp_path / f"rust-{series}", monkeypatch, set_home,
                                       NATIVE[vendor], appended)
        assert len(python) == len(native) == POLLS
        row[series] = {"python": {**_stats(python), **python_state}, "rust": {**_stats(native), **native_state}}
    if out := os.environ.get("NAVIDE_G2_COST_OUT"):
        with open(out, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
