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
import os
from pathlib import Path
import re
import shutil
import sys

import pytest

from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.frame_peer import PRODUCT, FramePeer
from tests.cli_regression.support.vendor_data import MARKER, PANE, VendorData
from tests.cli_regression.test_launch_snapshots import BOOT_DIR
from tests.cli_regression.test_vendor_transport import _install, _records

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only (D2)")]

ADAPTERS_RS = Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime" / "src" / "adapters.rs"
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


def test_roster_arithmetic_is_derived_from_the_registry():
    assert PRESERVED == {"mcode"}
    assert len(VENDORS) - len(BASE_OBSERVED) - len(PRESERVED) == len(PORTED)
    expected_ported = {"aider", "antigravity", "copilot", "cursor", "droid", "goose", "grok", "kilo", "kimi", "muse",
                       "pi", "qwen"} | ({"opencode"} if sys.platform == "linux" else set())
    assert set(PORTED) == expected_ported, "the live registry disagrees with ticket 06's ported set"


def test_rust_observer_roster_equals_registry_minus_preservation():
    """Final state: the vendors the sidecar observes are exactly the registry's readers minus mcode, on both
    POSIX platforms (OpenCode is no longer macOS-only)."""
    source = ADAPTERS_RS.read_text(encoding="utf-8")
    observed = set(re.findall(r'Some\("([a-z]+)"\)', source[source.index("pub fn observe"):]))
    assert "target_os" not in source, "an observer is still gated to one platform"
    assert observed == {key for key, spec in VENDORS.items() if spec.make_log_reader}


def test_mcode_stays_not_yet_integrated():
    assert VENDORS["mcode"].make_log_reader is None
    assert facts.FACTS["mcode"].capabilities.resume_by_id == "not-yet-integrated"
    assert "mcode" not in re.findall(r'Some\("([a-z]+)"\)', ADAPTERS_RS.read_text(encoding="utf-8"))


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
    strip = lambda mark: {key: value for key, value in (mark or {}).items() if key != "identity"}  # noqa: E731 (dev:ino differs per file)
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
    assert not [row for row in rust["receipts"] if row.get("error")], "a native observation reported an error"

    assert rust["stream"] == python["stream"], "ordered sink streams differ (order, duplicates or losses)"
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


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", [key for key in PORTED if VENDORS[key].push_channel is not None])
async def test_message_reaches_only_the_intended_ported_pane(vendor, tmp_path):
    other = "mcode" if vendor != "mcode" else "codex"
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    async with backend, backend.connect() as ws:
        await _spawn_ready(backend, ws, vendor, command)
        await _spawn_ready(backend, ws, other, other_command)
        stale = await backend.request(ws, "agent_msg.push", {"pane_id": f"{vendor}-stale", "text": "nobody"})
        assert stale["payload"]["ok"] is False
        delivered = await backend.request(ws, "agent_msg.push", {"pane_id": vendor, "text": "first\nmessage"})
        assert delivered["payload"]["ok"] is True, delivered["payload"]
        assert not [row for row in _records(backend, other) if row["kind"] in ("http", "file")]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", PORTED)
async def test_interrupt_reaches_only_the_ported_vendor_process(vendor, tmp_path):
    """No ported vendor declares an interrupt key, so each gets the default (SIGINT, observed by the peer as 3);
    mcode is the bystander whose own `^C` handling must stay untouched."""
    assert VENDORS[vendor].interrupt_key is None
    other = "mcode"
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    async with backend, backend.connect() as ws:
        created = await _spawn_ready(backend, ws, vendor, command)
        await _spawn_ready(backend, ws, other, other_command)
        await backend.request(ws, "terminal.interrupt", {"terminal_session_id": created["terminal_session_id"]})
        if VENDORS[vendor].push_channel is None:
            await backend.expect_output(ws, created["terminal_session_id"], "INTERRUPT_3")
            assert {"kind": "interrupt", "value": 3} in _records(backend, vendor)
        # A vendor launched in HTTP or input-file mode (kilo, qwen) has a peer that serves requests instead of reading
        # the terminal, so the interrupt is not observable there: the accepted request and an untouched bystander
        # are the whole assertion for it.
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
