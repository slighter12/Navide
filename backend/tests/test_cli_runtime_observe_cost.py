"""Per-poll cost of the ported observers on matched fixtures (G2 ticket 06).

One poll is what the watcher does for a file that has not changed since the last poll: `parse_incremental` with the
saved checkpoint and `parse_activity` with the saved bag. The Python path is the baseline reader; the native path is
the same vendor's `Native<Vendor>Reader` with its `observe` bridge answered by a real sidecar over its frame socket
(the reader's own work, the request, the sidecar's read and the answer; not the backend's asyncio hop). Both read the
same fixture file on the same host. The test asserts that both paths answered, never a timing, and writes the
statistics to the file named by `NAVIDE_G2_COST_OUT` when it is set.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_aider, cli_runtime_blob, cli_runtime_droid, cli_runtime_jsonl
from agent_team_backend import cli_runtime_observation, cli_runtime_opencode, cli_runtime_sqlite
from agent_team_backend.cli_runtime_frames import FrameType, code
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.vendor_data import VendorData
from tests.test_cli_runtime_observe_vendors import PORTED, peer  # noqa: F401  (the sidecar fixture)

pytestmark = [pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only (D2)")]

POLLS = 40
WARMUP = 4
NATIVE = {
    "aider": cli_runtime_aider.NativeAiderReader,
    "antigravity": cli_runtime_blob.NativeAntigravityReader,
    "copilot": cli_runtime_sqlite.NativeCopilotReader,
    "cursor": cli_runtime_blob.NativeCursorReader,
    "droid": cli_runtime_droid.NativeDroidReader,
    "goose": cli_runtime_sqlite.NativeGooseReader,
    "grok": cli_runtime_jsonl.NativeGrokReader,
    "kilo": cli_runtime_sqlite.NativeKiloReader,
    "kimi": cli_runtime_jsonl.NativeKimiReader,
    "muse": cli_runtime_jsonl.NativeMuseReader,
    "opencode": cli_runtime_opencode.NativeOpenCodeReader,
    "pi": cli_runtime_jsonl.NativePiReader,
    "qwen": cli_runtime_jsonl.NativeQwenReader,
}


def _through(peer):
    """`cli_runtime_observation.observe` with the sidecar's answer read straight off a `FramePeer`."""
    def observe(vendor, parser, path, mode, *, checkpoint=None, seen=None, pane_id=None, **extra):
        cursor = dict(checkpoint or {})
        expected, invalid = b"", False
        offset = int(cursor.get("offset") or 0)
        if offset and cursor.get("anchor"):
            with Path(path).open("rb") as stream:
                if os.fstat(stream.fileno()).st_size < offset:
                    invalid = True
                else:
                    start = max(0, offset - 512)
                    stream.seek(start)
                    expected = stream.read(offset - start)
                    invalid = hashlib.blake2b(expected, digest_size=8).hexdigest() != cursor["anchor"]
        # `FramePeer.observe` owns a `session` parameter, which readers also use as a request key.
        meta = {"vendor": vendor, "path": str(path), "mode": mode, "checkpoint": cursor,
                "seen": sorted(seen or set()), "anchor_invalid": invalid, **extra}
        request = peer.send(code.OBSERVE, meta, expected)
        final = peer.result(request, allow_error=True)
        if final.frame_type is FrameType.ERROR and not final.meta.get("observation_parts"):
            raise AssertionError(final.meta)
        parts = peer.parts.pop(request, [])
        peer._sizes.pop(request, None)
        document = json.loads(b"".join(parts))
        raw = final.blob if final.meta.get("raw_anchor") else None
        if raw is not None and not document.get("error"):
            anchor = hashlib.blake2b(raw, digest_size=8).hexdigest() if raw else ""
            document["checkpoint"]["anchor"] = anchor
            for event in document["events"]:
                if "checkpoint" in event:
                    event["checkpoint"]["anchor"] = anchor
        return document
    return observe


def _poll(reader, path):
    """The warmed-up per-poll cost samples (seconds) of `reader` on an unchanged file."""
    checkpoint, bag, samples = {}, set(), []
    for number in range(WARMUP + POLLS):
        start = time.perf_counter()
        result = reader.parse_incremental(path, checkpoint)
        checkpoint = result.checkpoint
        reader.parse_activity(path, bag)
        elapsed = time.perf_counter() - start
        if number >= WARMUP:
            samples.append(elapsed)
    return samples


def _stats(samples):
    ordered = sorted(samples)
    return {"polls": len(ordered), "median_ms": round(statistics.median(ordered) * 1000, 3),
            "p95_ms": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] * 1000, 3)}


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", PORTED)
def test_per_poll_cost_is_measured_on_a_matched_fixture(vendor, tmp_path, monkeypatch, set_home, peer):
    data = VendorData(vendor, tmp_path / "data", monkeypatch, set_home)
    try:
        for phase in ("initial", "response", "complete"):
            data.apply(phase)
        baseline = VENDORS[vendor].make_log_reader()
        assert type(baseline) is not NATIVE[vendor], "the baseline must be the Python reader"
        python = _poll(baseline, data.path)
        for module in (cli_runtime_aider, cli_runtime_blob, cli_runtime_droid, cli_runtime_jsonl,
                       cli_runtime_opencode, cli_runtime_sqlite):
            monkeypatch.setattr(module, "observe", _through(peer))
        monkeypatch.setattr(cli_runtime_observation, "observe", _through(peer))
        native = _poll(NATIVE[vendor](), data.path)
    finally:
        data.close()
    assert len(python) == len(native) == POLLS
    row = {"vendor": vendor, "platform": sys.platform, "python": _stats(python), "rust": _stats(native)}
    if out := os.environ.get("NAVIDE_G2_COST_OUT"):
        with open(out, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
