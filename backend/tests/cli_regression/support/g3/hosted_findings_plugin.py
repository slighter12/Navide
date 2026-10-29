"""rh24: the pytest plugin of the Darwin correctness run. It observes and changes nothing: it writes the bytes the gate needs to verify a declared finding (`hosted_correctness.KNOWN_FINDINGS`).

The frozen r2 case's failure message quotes only the first difference of the flood stream, so the gate could not see the rest of the stream (review B High, review A F-1). When a call of a declared case
FAILS, this plugin reads the failing test frame's locals from the exception's traceback and writes one record per node id into `correctness-findings-evidence.json` beside the JUnit file:

* r2 (`test_cli_runtime_lifecycle.py:624-672`): `raw[base:]`, the received stream (as text, one character per byte, so the workflows' token scan reads it), with `expected`'s length and SHA-256, `count`,
  `slow` and the drop counter's total; the received stream is kept only when its JSON text is at most `MAX_RECEIVED` bytes (above it the record says so and holds no stream, and the gate cannot
  accept it; rh25: the bound is on the encoded text, so it holds for any bytes);
* b10 (`test_cli_runtime_control_failures.py:547-558`): the refusal's `code` and `effect` (`caught.value.meta`), the effect half of line 555 that the assertion message does not show (review A F-4);
  rh25: an effect that is not a string is written as its JSON scalar or null, with `effectType`, never as a repr.

Each record also holds pytest's own crash message, the text JUnit writes as the failure's `message`. The plugin judges nothing: the gate rebuilds the expected stream itself and checks the bytes.

It is a `wrapper` of `pytest_runtest_makereport` that returns the report it was given, untouched, and swallows any error of its own, so it cannot change an outcome. It is loaded only by the Darwin
plan's pytest call (`hosted_correctness.pytest_argv`, `-p` with this module's name; the call runs from the checkout's root, where `backend` is a namespace package). Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

NAME = "correctness-findings-evidence.json"
SCHEMA = "acr-g3h-findings-evidence/1"
MAX_RECEIVED = 2 * 1024 * 1024      # rh25 (review A F-2 of rh24): the r2 stream is kept only when its JSON text in the record (`json.dumps`, quotes and escapes included) is at most this many bytes, for any bytes the stream holds; the slow variant's pure doubling is about 1.09 MB of it
ENVELOPE_BOUND = 4096      # rh25: the record document is at most 2 * MAX_RECEIVED + ENVELOPE_BOUND bytes (two r2 variants at the bound, the b10 record, the keys); `test_g3h_rh25.py` builds it
DECLARED = {("test_cli_runtime_lifecycle.py", "test_r2_flood_during_resize_preserves_order_accounts_drops_and_emits_late_bytes_after_the_ack"): "r2",
            ("test_cli_runtime_control_failures.py", "test_b10_an_exit_watcher_failure_after_gate_release_kills_and_reaps_before_answering"): "b10"}
_records: dict[str, dict] = {}


def _frame(item, call):
    """The innermost traceback frame of the test function itself (an async case's coroutine frame included)."""
    found, tb = None, call.excinfo.tb if call.excinfo is not None else None
    path = str(Path(str(item.path)).resolve())
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_name == getattr(item, "originalname", item.name) and str(Path(code.co_filename).resolve()) == path:
            found = tb.tb_frame
        tb = tb.tb_next
    return found


def _r2(local: dict, frame) -> dict:
    raw, base, expected = local.get("raw"), local.get("base"), local.get("expected")
    if not (isinstance(raw, bytes) and isinstance(base, int) and isinstance(expected, bytes)):
        return {"error": "the frame does not hold `raw`, `base` and `expected` as bytes, int and bytes"}
    received = raw[base:]
    counter = frame.f_globals.get("DropCounter")
    text = received.decode("latin-1")
    kept = len(received) + 2 <= MAX_RECEIVED and len(json.dumps(text)) <= MAX_RECEIVED      # the bytes `_write` writes for it (`json.dumps` escapes a string the same way inside the document); each byte is at least one
    return {"count": local.get("count") if isinstance(local.get("count"), int) else None, "slow": local.get("slow") if isinstance(local.get("slow"), bool) else None,
            "dropped": getattr(counter, "total", None), "expectedBytes": len(expected), "expectedSha256": hashlib.sha256(expected).hexdigest(), "receivedBytes": len(received),
            "receivedSha256": hashlib.sha256(received).hexdigest(), "receivedTooLarge": not kept, "received": text if kept else None}


def _b10(local: dict, frame) -> dict:
    caught = local.get("caught")
    meta = getattr(getattr(caught, "value", None), "meta", None)
    if not isinstance(meta, dict):
        return {"error": "the frame does not hold `caught` with a refusal's `meta`"}
    effect = meta.get("effect")
    if isinstance(effect, str):
        return {"code": meta.get("code") if isinstance(meta.get("code"), str) else repr(meta.get("code")), "effect": effect}
    # rh25 (review A F-3 of rh24): never a repr; a JSON scalar as it is, anything else as null, with its type, so the gate refuses it as `effect-not-str`
    return {"code": meta.get("code") if isinstance(meta.get("code"), str) else repr(meta.get("code")), "effect": effect if isinstance(effect, (int, float, bool)) or effect is None else None,
            "effectType": "missing" if "effect" not in meta else type(effect).__name__}


def _write(directory: Path) -> None:
    target = directory / NAME
    temporary = directory / (NAME + ".part")
    temporary.write_text(json.dumps({"schema": SCHEMA, "records": _records}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _observe(item, call, report) -> None:
    if call.when != "call" or not report.failed:
        return
    case = DECLARED.get((Path(str(item.path)).name, getattr(item, "originalname", item.name)))
    junit = getattr(item.config.option, "xmlpath", None)
    if case is None or not junit:
        return
    crash = getattr(report.longrepr, "reprcrash", None)
    record = {"case": case, "nodeid": item.nodeid, "message": crash.message if crash is not None else str(report.longrepr)}
    frame = _frame(item, call)
    if frame is None:
        record["error"] = "no traceback frame of the test function"
    else:
        try:
            record.update((_r2 if case == "r2" else _b10)(dict(frame.f_locals), frame))
        except Exception as error:      # the record says why; the outcome is never touched
            record["error"] = f"reading the frame failed: {error.__class__.__name__}"
    _records[item.nodeid] = record
    _write(Path(junit).resolve().parent)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    try:
        _observe(item, call, report)
    except Exception:      # never an outcome of the plugin's own
        pass
    return report
