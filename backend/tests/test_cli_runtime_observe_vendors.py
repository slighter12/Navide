"""G2 ticket 06: every remaining vendor is observed through the Rust runtime.

RED by design until the Phase B batches land (`design/e1-runtime-protocol-v8b-g2-delta.md` section 11).

Principal seam: authenticated WebSocket -> Python handlers -> real sidecar -> native PTY (the
`vendor_transport.py` peer stands in for the CLI) -> production `LogWatcher`, sinks and persistence. The
vendor's synthetic catalog fixture (`tests/fixtures/cli-regression/<vendor>.json`) is written in two stages with
a backend restart between them, once with the Python readers and once Rust-selected, in the SAME root path on the
same host, so every path (and so every anchor) is identical between the two runs. Python reader tests
(`backend/tests/vendors/`) stay supplemental and satisfy none of these cases.

Membership comes from the registry, never from a count. Every wait is bounded; every test carries a
`pytest-timeout` mark (ticket 05's review required a real time bound).

Environment: `NAVIDE_CLI_RUNTIME_BIN` selects a sidecar build kept outside `native/*/target` (the Linux container).
A missing sidecar binary FAILS these tests; it never skips them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys

import pytest

from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.frame_peer import PRODUCT, FramePeer
from tests.cli_regression.support.vendor_data import MARKER, PANE, VendorData
from tests.cli_regression.test_launch_snapshots import BOOT_DIR
from tests.cli_regression.test_vendor_transport import _install, _records

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only (D2)")]

#: Vendors whose Rust observation exists at the base commit, on this platform (claude, codex; opencode on macOS).
BASE_OBSERVED = frozenset({"claude", "codex"} | ({"opencode"} if sys.platform == "darwin" else set()))
#: Preservation cases: no Python-baseline observer, kept `not-yet-integrated`.
PRESERVED = frozenset(key for key, spec in VENDORS.items() if spec.make_log_reader is None)
#: Registry members with a reader, minus what Rust observes at the base commit: the vendors this ticket ports.
PORTED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader and key not in BASE_OBSERVED)
#: The wait that stands for "the watcher has caught up" after a stage's writes (a file event arrives in well under it).
QUIET_S = 6.0
STAGE_BOUND_S = 60.0
EVENT_TYPES = {"session.detected": ("pane_id", "session_id", "vendor"),
               "agent.activity": ("vendor", "event_type", "detail", "text", "session_id", "pane_id", "workspace_path")}


@pytest.mark.timeout(30)
def test_roster_arithmetic_is_derived_from_the_registry():
    assert PRESERVED == {"mcode"}
    assert len(VENDORS) - len(BASE_OBSERVED) - len(PRESERVED) == len(PORTED)
    expected_ported = {"aider", "antigravity", "copilot", "cursor", "droid", "goose", "grok", "kilo", "kimi", "muse",
                       "pi", "qwen"} | ({"opencode"} if sys.platform == "linux" else set())
    assert set(PORTED) == expected_ported, "the live registry disagrees with ticket 06's ported set"


@pytest.mark.timeout(30)
def test_mcode_stays_not_yet_integrated():
    assert VENDORS["mcode"].make_log_reader is None
    assert facts.FACTS["mcode"].capabilities.resume_by_id == "not-yet-integrated"


@pytest.mark.timeout(30)
def test_goose_gaps_are_reported_unknown():
    capabilities = facts.FACTS["goose"].capabilities
    assert (capabilities.model, capabilities.effort, capabilities.login) == ("unknown", "unknown", "unknown")


# ----------------------------------------------------------------------------- the sidecar's own answer

@pytest.fixture
def peer(tmp_path):
    assert PRODUCT.is_file(), f"the native sidecar is not built: {PRODUCT}"
    peer = FramePeer(tmp_path)
    try:
        yield peer
    finally:
        peer.close()


@pytest.mark.timeout(120)
def test_rust_observer_roster_equals_registry_minus_preservation(tmp_path, peer):
    """Final state: the vendors the sidecar observes on THIS host (its own answer, with whatever platform gating is
    in force) are exactly the registry's readers minus the preservation set. OpenCode is no longer macOS-only."""
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    observed = set()
    for key in VENDORS:
        document, _anchor, final = peer.observe(key, path, "usage", checkpoint={}, allow_error=True)
        if document is not None or "observer not integrated" not in str(final.meta):
            observed.add(key)
    assert observed == {key for key, spec in VENDORS.items() if spec.make_log_reader}


@pytest.mark.timeout(60)
def test_mcode_answers_observer_not_integrated(tmp_path, peer):
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    document, _anchor, final = peer.observe("mcode", path, "usage", checkpoint={}, allow_error=True)
    assert document is None and "observer not integrated" in str(final.meta)


@pytest.mark.timeout(60)
@pytest.mark.parametrize("vendor", PORTED)
def test_sidecar_observes_each_ported_vendor(vendor, tmp_path, monkeypatch, set_home, peer):
    """At the base commit this is the answer `observer not integrated`; after the port, the vendor's own parser."""
    data = VendorData(vendor, tmp_path / "data", monkeypatch, set_home)
    try:
        data.apply("initial")
        document, _anchor, final = peer.observe(vendor, data.path, "usage", checkpoint={}, allow_error=True)
    finally:
        data.close()
    assert document is not None, final.meta
    assert document["parser"].startswith(f"rust-{vendor}-") and document["parser"].endswith("-v1"), document["parser"]
    assert document["mode"] == "usage" and "checkpoint" in document


# --------------------------------------------------------------------------------- the principal seam

def _isolate_passwd_home(backend: BackendProcess) -> None:
    """Vendors that redirect their home at spawn (grok, muse, qwen, kilo, antigravity) are refused by the harness's
    Rust-child guard unless the account database's home is the isolated one, as in tickets 04 and 05."""
    backend.env["NAVIDE_SNAPSHOT_PASSWD_HOME"] = str(backend.root / "home")
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])


def _backend(root: Path, runtime: str, stage: int, home: Path) -> BackendProcess:
    backend = BackendProcess(root, artifact_prefix=f"{runtime}-stage-{stage}-")
    _isolate_passwd_home(backend)
    if runtime == "rust":
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    backend.env["XDG_DATA_HOME"] = str(home / ".local" / "share")  # where `VendorData` writes the vendor stores
    backend.env["GOOSE_PATH_ROOT"] = str(home / "goose-root")
    return backend


async def _pump(backend: BackendProcess, ws, until, what: str) -> None:
    try:
        async with asyncio.timeout(STAGE_BOUND_S):
            while not until():
                await backend.receive(ws)
    except TimeoutError:
        raise AssertionError(f"timed out after {STAGE_BOUND_S}s waiting for {what}") from None


async def _settle(backend: BackendProcess, ws) -> None:
    """Drain events until none arrives for QUIET_S (a bounded wait on a stream, not a poll)."""
    async with asyncio.timeout(STAGE_BOUND_S):
        while True:
            try:
                async with asyncio.timeout(QUIET_S):
                    await backend.receive(ws)
            except TimeoutError:
                return


def _types(backend: BackendProcess, name: str) -> list[dict]:
    return [event["payload"] for event in backend.events if event.get("type") == name]


def _stream(events: list[dict]) -> list[list]:
    rows = []
    for event in events:
        keys = EVENT_TYPES.get(event.get("type"))
        if keys:
            payload = event["payload"]
            rows.append([event["type"], {key: payload.get(key) for key in keys}])
    return rows


async def _stage(root: Path, runtime: str, stage: int, vendor: str, data: VendorData, phases: list[str]) -> list[dict]:
    backend = _backend(root, runtime, stage, data.home)
    command = _install(backend, vendor)
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id=PANE, agent_key=vendor, command=command, cwd=str(data.workspace),
                                       metadata={"session_marker": MARKER})
        await backend.expect_output(ws, created["terminal_session_id"], "TRANSPORT_READY")
        for phase in phases:
            data.apply(phase)
            if phase == "initial" and MARKER in json.dumps(data.fixture["phases"]["initial"]):
                # The first sight of a store (which records a high-water mark and emits nothing for several
                # vendors) must happen before the next write, or that write is swallowed by it.
                await _pump(backend, ws, lambda: _types(backend, "session.detected"), "session.detected after the first write")
            elif phase == "complete":
                await _pump(backend, ws, lambda: any(
                    event["event_type"] == "turn_complete" for event in _types(backend, "agent.activity")),
                    "the turn_complete of the completion phase")
            await _settle(backend, ws)
        await _settle(backend, ws)
    return backend.events


def _durable(root: Path, workspace: Path, path: Path) -> dict:
    from agent_team_backend.db import Database
    from agent_team_backend.tokens_store import TokensStore
    database = Database(root / "data" / "navide.db")
    try:
        store = TokensStore(db=database)
        usage = store.get_ingestion_checkpoint(str(path), None)
        activity = store.get_ingestion_checkpoint(str(path), "@activity")
        totals = store.snapshot(str(workspace))["global"]["all_time"]
    finally:
        database.close()
    def strip(mark):
        """Drop `dev:ino`, which differs between the two runs' files: the `identity` key of a checkpoint and the
        `"identity": "<dev>:<ino>"` a vendor embeds in a sentinel string of the activity bag."""
        masked = {key: value for key, value in (mark or {}).items() if key != "identity"}
        if "keys" in masked:
            masked["keys"] = [re.sub(r'"identity": "\d+:\d+"', '"identity": "-"', key) for key in masked["keys"]]
        return masked
    return {"usage_checkpoint": strip(usage), "activity_checkpoint": strip(activity),
            "totals": {key: totals[key] for key in ("input", "output") if key in totals}}


def _receipts(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    if not path.exists():
        return []
    return [row for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()) if "parser" in row]


async def _scenario(root: Path, runtime: str, vendor: str, monkeypatch, set_home) -> dict:
    root.mkdir(parents=True)
    data = VendorData(vendor, root, monkeypatch, set_home)
    try:
        events = await _stage(root, runtime, 1, vendor, data, ["initial", "response"])
        # The writer stays open across the restart, as a live CLI's does; the new backend resumes from its checkpoints.
        events += await _stage(root, runtime, 2, vendor, data, ["complete"])
        path = data.path.resolve()
        return {"stream": _stream(events), "durable": _durable(root, data.workspace, path),
                "receipts": _receipts(root), "path": path, "expected": data.fixture["expected"]}
    finally:
        data.close()


@pytest.mark.timeout(900)
@pytest.mark.parametrize("vendor", PORTED)
async def test_rust_selected_run_equals_python_selected_run(vendor, tmp_path, monkeypatch, set_home):
    root = tmp_path / "pane-root"
    python = await _scenario(root, "python", vendor, monkeypatch, set_home)
    shutil.rmtree(root)  # the same absolute paths for the Rust run, so anchors are comparable
    rust = await _scenario(root, "rust", vendor, monkeypatch, set_home)

    # The control: the harness sees the baseline's whole story (one completion, the fixture's totals).
    completions = [row for kind, row in python["stream"] if kind == "agent.activity" and row["event_type"] == "turn_complete"]
    assert len(completions) == 1, python["stream"]
    assert [python["durable"]["totals"].get("input"), python["durable"]["totals"].get("output")] == python["expected"]["tokens"]
    assert not python["receipts"], "the Python run must not use the native observer"

    # The Rust run really observed this vendor's file through the sidecar (a Python read under `rust-shell` would pass
    # the stream comparison unnoticed).
    parsers = {row["parser"] for row in rust["receipts"] if Path(row["path"]).resolve() == rust["path"]}
    assert any(name.startswith(f"rust-{vendor}-") and name.endswith("-v1") for name in parsers), parsers
    # The fixture's `executescript` commits its CREATE TABLEs one by one, so a poll between two of them finds a table
    # missing. The baseline's store read answers that with "no data this cycle" and one WARNING; the native path
    # reports the same failed query in the document (`sqlite error 1`) and Python raises the same warning. Anything
    # else is an error.
    errors = [row for row in rust["receipts"] if row.get("error") and "sqlite error 1 in" not in row["error"]]
    assert not errors, "a native observation reported an error"

    # Each sink's stream is compared in order (duplicates, losses and reordering within it all fail). The two sinks'
    # interleaving is not: `session.detected` (the watcher's session sink) and `agent.activity` (its activity sink)
    # are independent deliveries of one file event, and on Linux the baseline itself delivers a store's first
    # activity rows before the session binding where the native path binds first.
    for kind in EVENT_TYPES:
        assert [row for name, row in rust["stream"] if name == kind] == [
            row for name, row in python["stream"] if name == kind], f"ordered {kind} streams differ"
    assert rust["durable"] == python["durable"], "persisted totals or checkpoints differ after the restart"


# ---------------------------------------- re-wake / message, interrupt and login exclusion, per ported vendor

def _rust_backend(root: Path) -> BackendProcess:
    backend = BackendProcess(root)
    _isolate_passwd_home(backend)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    return backend


async def _spawn_ready(backend: BackendProcess, ws, vendor: str, command, **kwargs) -> dict:
    created = await backend.create(ws, pane_id=vendor, agent_key=vendor, command=command, **kwargs)
    await backend.expect_output(ws, created["terminal_session_id"], "TRANSPORT_READY")
    return created


async def _wait_records(backend: BackendProcess, vendor: str, ready, what: str) -> list[dict]:
    """The peer's audit file is written by another process: wait, bounded, until `ready(records)` holds."""
    try:
        async with asyncio.timeout(10):
            while not ready(records := _records(backend, vendor)):
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError(f"the {vendor} peer never recorded {what}: {_records(backend, vendor)}") from None
    return records


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", [key for key in PORTED if VENDORS[key].push_channel is not None])
async def test_message_reaches_only_the_intended_ported_pane(vendor, tmp_path):
    """The intended peer's own record is the evidence: an HTTP submission with the payload (and, for kilo, its
    authorization) or the input-file line. `agent_msg.push` returning ok only shows admission."""
    other = "mcode" if vendor != "mcode" else "codex"
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    envelope = "first\nmessage 測試"
    async with backend, backend.connect() as ws:
        await _spawn_ready(backend, ws, vendor, command)
        await _spawn_ready(backend, ws, other, other_command)
        spawn = _records(backend, vendor)[0]
        stale = await backend.request(ws, "agent_msg.push", {"pane_id": f"{vendor}-stale", "text": "nobody"})
        assert stale["payload"]["ok"] is False
        delivered = await backend.request(ws, "agent_msg.push", {"pane_id": vendor, "text": envelope})
        assert delivered["payload"]["ok"] is True, delivered["payload"]
        if "--port" in spawn["argv"]:
            records = await _wait_records(
                backend, vendor, lambda rows: any(r["kind"] == "http" and r["path"].endswith("submit-prompt") for r in rows),
                "the submit-prompt request")
            http = [r for r in records if r["kind"] == "http"]
            assert [r["path"] for r in http] == ["/tui/append-prompt", "/tui/submit-prompt"]
            assert http[0]["body"] == {"text": envelope}
            assert http[1]["submitted"] == envelope and http[1]["composer"] == ""
            assert spawn["password"] is (vendor == "kilo")
            assert all(r["authorized"] and r["has_auth"] is (vendor == "kilo") for r in http)
        elif "--input-file" in spawn["argv"]:
            records = await _wait_records(backend, vendor, lambda rows: any(r["kind"] == "file" for r in rows),
                                          "the input-file line")
            assert [r["value"] for r in records if r["kind"] == "file"] == [{"type": "submit", "text": envelope}]
        else:
            raise AssertionError(f"{vendor} declares a push channel the peer has no observable arm for: {spawn['argv']}")
        assert not [row for row in _records(backend, other) if row["kind"] in ("http", "file")]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", PORTED)
async def test_interrupt_reaches_only_the_ported_vendor_process(vendor, tmp_path):
    """No ported vendor declares an interrupt key, so each gets the default (SIGINT, recorded by the target peer as
    3 in every mode, HTTP and input-file included); mcode is the bystander whose own `^C` handling stays untouched."""
    assert VENDORS[vendor].interrupt_key is None
    other = "mcode"
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    async with backend, backend.connect() as ws:
        created = await _spawn_ready(backend, ws, vendor, command)
        await _spawn_ready(backend, ws, other, other_command)
        await backend.request(ws, "terminal.interrupt", {"terminal_session_id": created["terminal_session_id"]})
        await _wait_records(backend, vendor, lambda rows: {"kind": "interrupt", "value": 3} in rows,
                            "the interrupt")
        assert not [row for row in _records(backend, other) if row["kind"] == "interrupt"]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", [key for key in PORTED if VENDORS[key].login_command_args is not None
                                    and VENDORS[key].live_file_from_context is None])
async def test_login_exclusion_holds_for_the_ported_vendor(vendor, tmp_path):
    backend = _rust_backend(tmp_path)
    command = _install(backend, vendor)
    command[-1] += " --fixture-repl-only"
    async with backend, backend.connect() as ws:
        await _spawn_ready(backend, ws, vendor, command, is_login=True)
        response = await backend.request(ws, "agent_msg.push", {"pane_id": vendor, "text": "must not deliver"})
        assert response["payload"]["ok"] is False
        assert response["payload"]["reason"] == "no-channel"


# ------------------------------------------------------------------------------------------------ copilot specifics

COPILOT_SESSION = "12345678-1234-4234-8234-123456789abc"
#: One cumulative metrics record, the only shape the 1.0.75 `events.jsonl` branch turns into usage.
COPILOT_METRICS = {"timestamp": "2026-09-29T10:00:00Z", "data": {
    "copilotVersion": "1.0.75", "currentModel": "fixture",
    "modelMetrics": {"fixture": {"usage": {"inputTokens": 12, "outputTokens": 3}}}}}
#: A double-quoted YAML scalar with a `\u` escape, an escaped quote and an escaped backslash: only a real YAML parser
#: resolves it, so a hand-written `cwd:` line parser would name a different workspace than the baseline does.
COPILOT_YAML = f'id: {COPILOT_SESSION}\ncwd: "/work/spa\\u00e9ce \\"quoted\\" \\\\ tail"\n'
COPILOT_CWD = '/work/spaéce "quoted" \\ tail'


@pytest.mark.timeout(60)
def test_copilot_events_cwd_is_resolved_by_python_and_agrees_in_rust(tmp_path, monkeypatch, peer):
    """`workspace.yaml` stays read by Python's `yaml.safe_load` (discovery and attribution); the resolved `cwd` is a
    request key for the Rust `events.jsonl` branch, whose event must carry exactly the baseline's value."""
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "copilot-home"))
    session = tmp_path / "copilot-home" / "session-state" / COPILOT_SESSION
    session.mkdir(parents=True)
    events = session / "events.jsonl"
    events.write_text(json.dumps(COPILOT_METRICS) + "\n", encoding="utf-8")
    (session / "workspace.yaml").write_text(COPILOT_YAML, encoding="utf-8")

    reader = CopilotLogReader()
    resolved = reader.cwd_from_file(events)
    assert resolved == COPILOT_CWD, "the fixture must exercise YAML quoting and escapes"
    baseline = reader.parse_session_file(events, set())
    assert [(usage.input_tokens, usage.output_tokens, usage.cwd) for usage in baseline] == [(12, 3, COPILOT_CWD)]

    document, _anchor, final = peer.observe("copilot", events, "usage", checkpoint={}, cwd=resolved, allow_error=True)
    assert document is not None, final.meta
    assert [(event["input_tokens"], event["output_tokens"], event["cwd"]) for event in document["events"]] == [
        (12, 3, COPILOT_CWD)]


@pytest.mark.timeout(120)
def test_copilot_unreadable_store_warns_once_in_python_and_in_the_native_path(tmp_path, monkeypatch, caplog, peer):
    """The baseline answers a failed store read with no data for that cycle and one WARNING through `_warn_db_once`.
    The Rust path keeps both: the sidecar reports the failure in the document, Python warns through the same function
    and returns no data. Three failing cycles leave one warning; a re-armed flag warns again (so the count is a
    real observation, not silence)."""
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "copilot-home"))
    root = tmp_path / "copilot-home"
    root.mkdir()
    db = root / "session-store.db"
    db.write_bytes(b"not sqlite")

    def warnings() -> list[logging.LogRecord]:
        return [row for row in caplog.records if row.levelno >= logging.WARNING]

    def cycles(reader, count: int) -> None:
        for _ in range(count):
            assert reader.parse_incremental(db, {}).events == []
            assert reader.parse_activity(db, set()) == []

    # The control: the baseline's own behaviour.
    _reset_db_warning()
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.copilot"):
        cycles(CopilotLogReader(), 3)
    assert len(warnings()) == 1 and "session-store" in warnings()[0].getMessage()
    caplog.clear()

    documents = {}
    for mode in ("usage", "activity"):
        document, _anchor, final = peer.observe("copilot", db, mode, checkpoint={}, seen=[], allow_error=True)
        assert document is not None, final.meta
        assert document["events"] == [] and document["error"], document
        documents[mode] = document

    from agent_team_backend import cli_runtime_sqlite
    from agent_team_backend.cli_runtime_sqlite import NativeCopilotReader

    def answer(_vendor, _parser, _path, mode, **_kwargs):
        return documents[mode]

    monkeypatch.setattr(cli_runtime_sqlite, "observe", answer)
    _reset_db_warning()
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.copilot"):
        cycles(NativeCopilotReader(), 3)
        assert len(warnings()) == 1 and "session-store" in warnings()[0].getMessage()
        _reset_db_warning()
        cycles(NativeCopilotReader(), 1)
    assert len(warnings()) == 2
