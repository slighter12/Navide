"""Handshake and handoff proofs with the REAL native sidecar (E1 H1-H8, P1, P4-P7, P9-P11).

Supplemental to the production TypeScript seam (tests/cli/shellRuntime.test.ts): a real
RustTerminalService drives the actual sidecar and macOS PTYs. Faults come from the isolated
harness build of the sidecar (cargo feature `fault-hooks`, `target-harness/`, never the product
binary) or from stub executables that stand in for a sidecar at the handshake only. Fixture CLIs
are used; no real provider CLI runs.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time

import psutil
import pytest

from agent_team_backend import cli_runtime
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_handoff import MasterHandle
from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.isolation import isolated_environment

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "native/navide-cli-runtime/target/release/navide-cli-runtime"
HARNESS = ROOT / "native/navide-cli-runtime/target-harness/release/navide-cli-runtime"
PEER = Path(__file__).parent / "cli_regression/support/handoff_peer.py"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file() or not HARNESS.is_file(),
                       reason="requires the explicitly built macOS native runtime (product and harness)"),
]


class Rig:
    def __init__(self, root: Path, monkeypatch, binary: Path = PRODUCT):
        self.root, self.monkeypatch = root, monkeypatch
        self.frames: list[dict] = []
        self.output: dict[str, bytes] = {}
        self.services: list[RustTerminalService] = []
        self.pids: set[int] = set()
        self.binary = root / "runtime-under-test"
        shutil.copyfile(binary, self.binary)
        self.binary.chmod(0o755)
        monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(self.binary))
        monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(root))
        self.peer = root / "handoff_peer.py"
        shutil.copyfile(PEER, self.peer)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.env = isolated_environment(root)

    async def emit(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            sid_len = frame[5]
            sid = bytes(frame[6:6 + sid_len]).decode()
            pane_len = frame[6 + sid_len]
            self.output[sid] = self.output.get(sid, b"") + bytes(frame[7 + sid_len + pane_len:])
            return
        self.frames.append(frame)

    def service(self) -> RustTerminalService:
        service = RustTerminalService(self.emit)
        self.services.append(service)
        return service

    def receipts(self) -> list[dict]:
        path = self.root / "runtime-receipts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def exits(self) -> list[dict]:
        return [f["payload"] for f in self.frames if f.get("type") == "terminal.exit"]

    async def shell(self, service, pane: str, **extra):
        session = await service.create_async(
            pane_id=pane, agent_key="terminal", command=[base_python_executable(), "-u", str(self.peer)],
            cwd=str(self.workspace), cols=100, rows=30, env=self.env, **extra)
        self.pids.add(session.proc.pid)
        await self.until(lambda: b"PEER_READY " in self.output.get(session.id, b""), "peer ready")
        return session

    def peer_ready(self, session) -> dict:
        return json.loads(self.output[session.id].split(b"PEER_READY ")[1].splitlines()[0])

    @staticmethod
    async def until(predicate, message: str, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            await asyncio.sleep(0.01)

    async def close(self):
        for service in self.services:
            runtime = service._runtime
            if runtime is not None and runtime.returncode is None:
                try:
                    with suppress_all():
                        await asyncio.wait_for(service.kill_all(), 20)
                except Exception:  # noqa: BLE001
                    pass
            if runtime is not None and runtime.returncode is None:
                runtime.kill()
        for pid in self.pids:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


class suppress_all:  # noqa: N801 - a tiny context manager, spelled like contextlib.suppress
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    try:
        yield rig
    finally:
        await rig.close()


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, HARNESS)
    try:
        yield rig
    finally:
        await rig.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------- handshake (H1-H8)

async def test_h1_healthy_handshake_records_the_real_hashed_binary(rig):
    service = rig.service()
    await service._ensure_started()
    ready = next(r for r in rig.receipts() if r["event"] == "ready")
    assert ready["executableSha256"] == sha256(rig.binary) == service._runtime_identity["executableSha256"]
    assert (ready["protocol"], ready["wire"], ready["handoff"], ready["ownership_gate"]) == ("navide-acr", 2, "scm_rights", True)
    assert ready["pid"] == service._runtime.pid == ready["pid"] and ready["owner_pid"] == os.getpid()
    assert service._limits["cleanup_ms"] == 8000 and service._ready_ok
    ping = await service._request("ping")
    assert ping["pong"] is True


async def test_h2_missing_binary_refuses_without_a_child_or_a_python_pty(rig, monkeypatch):
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(rig.root / "missing-runtime"))
    service = rig.service()
    before = len(psutil.Process().children(recursive=True))
    with pytest.raises(FileNotFoundError):
        await service.create_async(pane_id="p", agent_key="terminal", command=["/bin/cat"], cwd=str(rig.workspace))
    assert len(psutil.Process().children(recursive=True)) == before and not service._sessions


async def test_h3_startup_fatal_is_an_explicit_failure_with_no_fallback(harness, monkeypatch):
    monkeypatch.setenv("NAVIDE_ACR_FAULT", "startup")
    service = harness.service()
    with pytest.raises(RuntimeError, match=r"START_FAILED: STARTUP_FAILED"):
        await service.create_async(pane_id="p", agent_key="terminal", command=["/bin/cat"], cwd=str(harness.workspace))
    assert service._runtime.returncode is not None and not service._sessions
    assert any(r["event"] == "startup_failed" for r in harness.receipts())
    assert not [r for r in harness.receipts() if r["event"] == "owned"]


async def test_h4_incompatible_wire_is_a_protocol_mismatch_and_the_sidecar_is_ended(harness, monkeypatch):
    monkeypatch.setenv("NAVIDE_ACR_FAULT", "protocol")
    service = harness.service()
    with pytest.raises(RuntimeError, match="PROTOCOL_MISMATCH at handshake"):
        await service._ensure_started()
    assert service._runtime.returncode is not None
    assert not [r for r in harness.receipts() if r["event"] == "owned"]


def ready_for(service, **changes) -> dict:
    """A well-formed ready record for the service's current spawn (mutated by `changes`)."""
    ready = {"event": "ready", "protocol": "navide-acr", "wire": 2, "version": "0.1.0",
             "runtime": "navide-cli-runtime", "runtime_generation": service._runtime_generation,
             "pid": service._runtime.pid, "owner_pid": os.getpid(), "platform": "macos", "arch": "aarch64",
             "pty_backend": "posix", "handoff": "scm_rights", "ownership_gate": True,
             "limits": dict(cli_runtime._LIMIT_CEILINGS, ownership_snapshot_ms=dict(cli_runtime._SNAPSHOT_CEILINGS))}
    ready.update(changes)
    return ready


async def test_h5_h7_ready_with_a_wrong_field_or_a_g1_candidate_protocol_is_a_mismatch(rig):
    service = rig.service()
    await service._ensure_started()
    service._verify_ready(ready_for(service))  # the control passes
    limits = lambda **c: {**ready_for(service)["limits"], **c}  # noqa: E731
    bad = [
        {"runtime_generation": "0" * 32}, {"pid": service._runtime.pid + 1}, {"owner_pid": os.getpid() + 1},
        {"handoff": "duplicate_handle"}, {"ownership_gate": False}, {"version": ""}, {"wire": 1},
        {"protocol": 1}, {"runtime": "other"}, {"wire": True},  # a G1-candidate sidecar answers protocol 1, no wire
        {"limits": limits(max_frame_r2p=wire_ceiling() + 1)}, {"limits": limits(request_ms=15_001)},
        {"limits": {k: v for k, v in limits().items() if k != "cleanup_ms"}}, {"limits": limits(cleanup_ms="8000")},
        {"limits": limits(ownership_snapshot_ms={"young": 1})}, {"limits": None},
    ]
    for changes in bad:
        with pytest.raises(RuntimeError, match="PROTOCOL_MISMATCH at handshake"):
            service._verify_ready(ready_for(service, **changes))


def wire_ceiling() -> int:
    return cli_runtime._LIMIT_CEILINGS["max_frame_r2p"]


STUB = """#!@PYTHON@
import sys, time
mode = @MODE@
out = sys.stdout.buffer
if mode == "exit-before-ready":
    sys.exit(3)
elif mode == "never-ready":
    time.sleep(60)
elif mode == "oversize":
    out.write(b"x" * 20000); out.flush(); time.sleep(60)
elif mode == "eof-mid-line":
    out.write(b'{"event":"rea'); out.flush(); sys.exit(0)
elif mode == "invalid-utf8":
    out.write(b'{"event":"\\xff"}\\n'); out.flush(); time.sleep(60)
elif mode == "not-object":
    out.write(b"[1]\\n"); out.flush(); time.sleep(60)
elif mode == "other-event":
    out.write(b'{"event":"hello"}\\n'); out.flush(); time.sleep(60)
elif mode == "not-json":
    out.write(b"hello\\n"); out.flush(); time.sleep(60)
"""


@pytest.mark.parametrize("mode,message", [
    ("exit-before-ready", "START_FAILED before ready"),
    ("eof-mid-line", "START_FAILED before ready"),
    ("oversize", "PROTOCOL_MISMATCH at handshake: oversize line"),
    ("invalid-utf8", "PROTOCOL_MISMATCH at handshake"),
    ("not-object", "PROTOCOL_MISMATCH at handshake: not an object"),
    ("not-json", "PROTOCOL_MISMATCH at handshake"),
    ("other-event", "PROTOCOL_MISMATCH at handshake: first line is not ready"),
])
async def test_h6_h8_every_handshake_byte_rule_fails_explicitly_and_ends_the_stub(rig, monkeypatch, mode, message):
    stub = rig.root / f"stub-{mode}"
    stub.write_text(STUB.replace("@PYTHON@", sys.executable).replace("@MODE@", repr(mode)))
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    service = rig.service()
    with pytest.raises(RuntimeError, match=message):
        await service._ensure_started()
    assert service._runtime.returncode is not None, "the stub must be ended by its own handle"
    failed = [r for r in rig.receipts() if r["event"] == "startup_failed"]
    assert failed and failed[0]["stage"] == "handshake" and not service._sessions


async def test_h6_ready_that_never_arrives_is_a_timeout(rig, monkeypatch):
    stub = rig.root / "stub-never"
    stub.write_text(STUB.replace("@PYTHON@", sys.executable).replace("@MODE@", repr("never-ready")))
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    monkeypatch.setattr(cli_runtime, "READY_DEADLINE", 0.5)  # the named bound itself is under test
    service = rig.service()
    with pytest.raises(RuntimeError, match="READY_TIMEOUT at handshake"):
        await service._ensure_started()
    assert service._runtime.returncode is not None


async def test_h8_bytes_after_ready_on_stdout_are_a_protocol_error(rig, monkeypatch):
    stub = rig.root / "stub-after"
    stub.write_text(f"""#!{sys.executable}
import json, os, sys, time
generation = sys.argv[sys.argv.index("--runtime-generation") + 1]
ready = {{"event": "ready", "protocol": "navide-acr", "wire": 2, "version": "0", "runtime": "navide-cli-runtime",
         "runtime_generation": generation, "pid": os.getpid(), "owner_pid": os.getppid(), "handoff": "scm_rights",
         "ownership_gate": True, "limits": {json.dumps(dict(cli_runtime._LIMIT_CEILINGS, ownership_snapshot_ms=dict(cli_runtime._SNAPSHOT_CEILINGS)))}}}
sys.stdout.write(json.dumps(ready) + "\\n"); sys.stdout.flush()
time.sleep(0.3)
sys.stdout.write("a second line\\n"); sys.stdout.flush()
time.sleep(60)
""")
    stub.chmod(0o755)
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(stub))
    service = rig.service()
    await service._ensure_started()
    generation = service._runtime_generation  # receipts are shared by path: select this service's own
    await rig.until(lambda: any(r["event"] == "runtime_invalidated" and r["lost_generation"] == generation
                                for r in rig.receipts()), "loss after stray stdout bytes")
    invalidated = next(r for r in rig.receipts() if r["event"] == "runtime_invalidated" and r["lost_generation"] == generation)
    assert "stdout carried bytes after ready" in invalidated["cause"] and invalidated["complete"] is True
    if service._runtime is not None and service._runtime.returncode is None:
        service._runtime.kill()


# --------------------------------------------------------------------- one session (P1, P5, P6)

async def test_p1_p5_p6_a_published_session_runs_over_the_handed_off_master(rig):
    service = rig.service()
    session = await rig.shell(service, "pane-p1")
    assert isinstance(session.handle, MasterHandle) and session.handle.fd is not None  # (c) the handle type
    fd = session.handle.fd
    assert not os.get_inheritable(fd), "P6: non-inheritable"
    assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK, "P6: non-blocking"
    assert stat.S_ISCHR(os.fstat(fd).st_mode) and os.isatty(fd)
    ready = rig.peer_ready(session)
    assert ready["stdin_tty"] is True
    # P5: neither the control socket nor a master is open in the CLI (only 0/1/2 plus nothing else).
    assert not [d for d in ready["fds"] if d["socket"]], ready["fds"]
    assert ready["fds"] == [], f"the CLI inherited descriptors: {ready['fds']}"
    # Input over the handed-off master, output through the Terminal Stream, UTF-8 split included.
    await service.write_async(session.id, "héllo 世界\r")
    await rig.until(lambda: "ECHO héllo 世界".encode() in rig.output[session.id], "echo through the handoff")
    receipts = rig.receipts()
    received = [r for r in receipts if r["event"] == "handoff_received" and r["session_id"] == session.id]
    assert len(received) == 1 and received[0]["validated"] is True and received[0]["kind"] == "scm_rights"
    assert 0 < received[0]["owned_frame_bytes"] <= 4096
    assert any(r["event"] == "handle_attached" and r["handle"] == "MasterHandle" for r in receipts)
    assert set(received[0]) == {"event", "kind", "platform", "validated", "session_id", "session_generation",
                                "owned_frame_bytes"}, "no descriptor values in receipts"
    await service.kill(session.id, force=True)
    assert session.handle.fd is None and session.closed and session.id not in service._sessions
    assert [e["terminal_session_id"] for e in rig.exits()] == [session.id]


async def test_p1_an_oversized_handoff_is_refused_before_any_process_exists(rig):
    service = rig.service()
    await service._ensure_started()
    before = len(psutil.Process(service._runtime.pid).children())
    args = {"session_id": "s-big", "session_generation": "a" * 32, "pane_id": "p", "workspace_path": "/w" * 2100,
            "launch": {"shape": "direct", "form": "list", "elements": ["/bin/cat"], "words": []}, "cwd": str(rig.workspace), "env": {"PATH": "/usr/bin:/bin"}, "cols": 80, "rows": 24,
            "agent_key": "terminal"}
    with pytest.raises(cli_runtime._RuntimeRefused, match=r"BAD_REQUEST at handoff_size"):
        await service._request("create", args)
    assert len(psutil.Process(service._runtime.pid).children()) == before


# ------------------------------------------------------------------------ P4: descriptor hygiene

async def test_p4_descriptor_counts_return_to_baseline_after_create_kill_cycles(rig):
    service = rig.service()
    await service._ensure_started()
    me, sidecar = psutil.Process(), psutil.Process(service._runtime.pid)
    warm = await rig.shell(service, "pane-warm")  # steady state: lazily opened stores are open now
    await service.kill(warm.id, force=True)
    await rig.until(lambda: sidecar.num_fds() <= 4, "warm-up session released")
    base_me, base_sidecar = me.num_fds(), sidecar.num_fds()
    for cycle in range(6):
        session = await rig.shell(service, f"pane-cycle-{cycle}")
        assert me.num_fds() > base_me and sidecar.num_fds() > base_sidecar
        await service.kill(session.id, force=True)
    await rig.until(lambda: me.num_fds() <= base_me and sidecar.num_fds() <= base_sidecar,
                    f"fd counts did not return: python {me.num_fds()}/{base_me} sidecar {sidecar.num_fds()}/{base_sidecar}")
    assert not service._slots and not service._claimed_ttys


# --------------------------------------------------------------- P7 (lost answers), P10, P11

async def test_p7a_a_caller_cancelled_after_dispatch_is_resolved_by_verified_cleanup(rig):
    service = rig.service()
    started = asyncio.Event()
    original = service._on_owned

    def on_owned(frame, fd):
        original(frame, fd)
        started.set()

    service._on_owned = on_owned
    task = asyncio.create_task(rig.shell(service, "pane-7a"))
    await asyncio.wait_for(started.wait(), 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await rig.until(lambda: any(r["event"] == "abandoned_create" for r in rig.receipts()), "abandon resolved", 30)
    outcome = next(r for r in rig.receipts() if r["event"] == "abandoned_create")
    assert outcome["outcome"] == "cleaned"
    assert not service._slots and not service._claimed_ttys and not service._unresolved and service._lost is None
    assert not rig.exits(), "an abandoned create is never presented to a renderer"


async def test_p7b_a_missing_create_answer_after_owned_is_settled_by_one_session_cleanup(harness):
    service = harness.service()
    task = asyncio.create_task(service.create_async(
        pane_id="fault:suppress-response", agent_key="terminal",
        command=[base_python_executable(), "-u", str(harness.peer)], cwd=str(harness.workspace), env=harness.env))
    await harness.until(lambda: any(r["event"] == "handoff_received" for r in harness.receipts()), "SESSION_OWNED received")
    task.cancel()  # the answer never comes; the caller gives up
    with pytest.raises(asyncio.CancelledError):
        await task
    await harness.until(lambda: any(r["event"] == "abandoned_create" for r in harness.receipts()), "abandon resolved", 30)
    assert next(r for r in harness.receipts() if r["event"] == "abandoned_create")["outcome"] == "cleaned"
    assert not service._slots and service._lost is None  # one-session cleanup, not generation loss
    ping = await service._request("ping")
    assert ping["pong"] is True


async def make_fifo_journal(service) -> Path:
    await service._ensure_started()
    journal = Path(pty_journal(service))
    journal.unlink()
    os.mkfifo(journal)
    return journal


def pty_journal(service) -> str:
    from agent_team_backend import pty_registry
    from agent_team_backend.applog import in_data_dir
    return str(in_data_dir(service._data_dir, pty_registry.journal_path, service._runtime_generation)())


async def test_p7d_p10_cancel_before_the_gate_is_prevented_and_leaves_no_root_and_no_descriptor(rig):
    service = rig.service()
    journal = await make_fifo_journal(service)  # the journal append blocks: the gate stays closed
    ran = rig.root / "i-ran"
    record: dict = {}
    task = asyncio.create_task(service.create_async(
        pane_id="pane-prevent", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
        command=[sys.executable, "-c", f"open({str(ran)!r}, 'w').close()"], cancel_record=record))
    await rig.until(lambda: record.get("native"), "create dispatched")
    await asyncio.sleep(0.3)  # the sidecar's create worker is now blocked opening the FIFO
    state = (await service._request("cancel", {"target_id": record["native"]["request_id"]}))["state"]
    assert state == "prevented"
    reader = os.open(journal, os.O_RDONLY | os.O_NONBLOCK)  # lets the blocked append finish and fail
    try:
        with pytest.raises(Exception) as caught:
            await asyncio.wait_for(task, 20)
        assert "CREATE_CANCELLED" in str(caught.value) or "OWNERSHIP_JOURNAL_FAILED" in str(caught.value) or isinstance(caught.value, cli_runtime.CreateCancelled)
    finally:
        os.close(reader)
    await asyncio.sleep(0.3)
    assert not ran.exists(), "the gated target ran although the create was prevented"
    assert not [r for r in rig.receipts() if r["event"] == "owned"] and not service._slots


async def test_p11_a_journal_that_cannot_be_appended_refuses_the_create_and_the_target_never_runs(rig):
    service = rig.service()
    await service._ensure_started()
    journal = Path(pty_journal(service))
    journal.unlink()
    journal.mkdir()  # an unwritable journal
    ran = rig.root / "i-ran-11"
    with pytest.raises(cli_runtime._RuntimeRefused, match="OWNERSHIP_JOURNAL_FAILED"):
        await service.create_async(pane_id="pane-11", agent_key="terminal", cwd=str(rig.workspace), env=rig.env,
                                   command=[sys.executable, "-c", f"open({str(ran)!r}, 'w').close()"])
    await asyncio.sleep(0.3)
    assert not ran.exists()
    assert not [r for r in rig.receipts() if r["event"] in ("owned", "handoff_received")] and not service._slots


# ------------------------------------------------------------------- P9: binding checks

@pytest.mark.parametrize("kind,check", [("wrong-master", 2), ("non-tty", 1), ("wrong-name", 2)])
async def test_p9_a_single_wrong_descriptor_fails_closed_and_unrelated_sessions_keep_running(harness, kind, check):
    service = harness.service()
    other = await harness.shell(service, "pane-bystander")
    await service.write_async(other.id, "flood 300\r")  # B is busy while A's handoff is rejected
    with pytest.raises(RuntimeError, match=rf"HANDOFF_FAILED: binding check {check}"):
        await harness.shell(service, f"fault:{kind}")
    rejected = [r for r in harness.receipts() if r["event"] == "handoff_rejected"]
    assert len(rejected) == 1 and rejected[0]["check"] == check
    assert set(rejected[0]) == {"event", "session_id", "session_generation", "check", "detail"}, "no descriptor values"
    await harness.until(lambda: b"FLOOD_DONE" in harness.output[other.id], "bystander output completes", 20)
    text = harness.output[other.id].decode()
    numbers = [int(line.split()[1]) for line in text.splitlines() if line.startswith("FLOOD ")]
    assert numbers == list(range(300)), "the bystander's output was disturbed"
    assert other.id in service._sessions and not other.closed
    assert not service._slots and len(service._claimed_ttys) == 1
    assert not [s for s in service._sessions if s != other.id]
    await service.write_async(other.id, "still alive\r")
    await harness.until(lambda: b"ECHO still alive" in harness.output[other.id], "bystander still answers")


async def test_p9c_a_tty_already_held_by_a_claimed_session_fails_check_3(harness):
    service = harness.service()
    first = await harness.shell(service, "pane-first")
    with pytest.raises(RuntimeError, match="HANDOFF_FAILED: binding check 3"):
        await harness.shell(service, "fault:duplicate-tty")
    assert first.id in service._sessions and not first.closed
    await service.write_async(first.id, "after\r")
    await harness.until(lambda: b"ECHO after" in harness.output[first.id], "first session unaffected")


async def test_p9e_a_consistently_swapped_pair_is_accepted_documented_limitation_not_a_pass_criterion(harness):
    service = harness.service()
    # The CLI's output goes to the real master, which only the sidecar holds: nothing is read here.
    session = await service.create_async(
        pane_id="fault:consistent-swap", agent_key="terminal", command=[base_python_executable(), "-u", str(harness.peer)],
        cwd=str(harness.workspace), cols=100, rows=30, env=harness.env)
    # The frame carried a decoy PTY's master AND that decoy's tty name: every check passes. This
    # test records that the protocol does NOT detect this class (E1 6.4, decision 18).
    assert [r for r in harness.receipts() if r["event"] == "handoff_received" and r["session_id"] == session.id]
    assert not [r for r in harness.receipts() if r["event"] == "handoff_rejected"]
    assert session.handle is not None and session.tty != ""


async def test_p9_thirty_two_simultaneous_creates_deliver_each_marker_only_to_its_own_cli(rig):
    service = rig.service()
    sessions = await asyncio.gather(*(rig.shell(service, f"pane-many-{index}") for index in range(32)))
    assert len({s.tty for s in sessions}) == 32 and len({s.handle.fd for s in sessions}) == 32
    for index, session in enumerate(sessions):
        await service.write_async(session.id, f"marker-{index}\r")
    for index, session in enumerate(sessions):
        await rig.until(lambda s=session, i=index: f"ECHO marker-{i}".encode() in rig.output[s.id], f"marker {index}")
    for index, session in enumerate(sessions):
        echoed = [line[5:] for line in rig.output[session.id].decode().splitlines() if line.startswith("ECHO ")]
        assert echoed == [f"marker-{index}"], echoed  # only its own input reached this CLI
    await asyncio.gather(*(service.kill(s.id, force=True) for s in sessions))


def test_the_binding_checks_never_read_or_write_the_descriptor():
    """P9: 'no byte is read from or written to any descriptor' by the checks — by construction."""
    import inspect

    from agent_team_backend import cli_runtime_handoff

    source = inspect.getsource(cli_runtime_handoff.check_master) + inspect.getsource(cli_runtime_handoff.slave_name_of)
    for forbidden in ("os.read", "os.write", ".recv", ".send", "readv", "pread"):
        assert forbidden not in source


# --------------------------------------------------------------------- P3 (real sidecar, faulty send)

async def test_p3_a_short_first_send_still_associates_the_descriptor_with_its_frame(harness):
    service = harness.service()
    bystander = await harness.shell(service, "pane-before")
    session = await harness.shell(service, "fault:short-send")  # the sidecar's first sendmsg accepts 3 bytes
    assert session.handle.fd is not None and session.tty != bystander.tty
    await service.write_async(session.id, "short ok\r")
    await harness.until(lambda: b"ECHO short ok" in harness.output[session.id], "echo over the short-sent master")
    assert b"ECHO short ok" not in harness.output[bystander.id]
    assert not [r for r in harness.receipts() if r["event"] == "handoff_rejected"]


async def test_p3_zero_progress_on_the_first_send_is_channel_loss_with_no_misassociated_descriptor(harness):
    service = harness.service()
    with pytest.raises(RuntimeError, match="RUNTIME_LOST|PROTOCOL_ERROR|CLEANUP_FAILED"):
        await service.create_async(
            pane_id="fault:zero-send", agent_key="terminal", command=[base_python_executable(), "-u", str(harness.peer)],
            cwd=str(harness.workspace), cols=100, rows=30, env=harness.env)
    generation = service._runtime_generation  # receipts are shared by path: select this service's own
    await harness.until(lambda: any(r["event"] == "runtime_invalidated" and r["lost_generation"] == generation
                                    for r in harness.receipts()), "loss handled", 40)
    invalidated = next(r for r in harness.receipts() if r["event"] == "runtime_invalidated" and r["lost_generation"] == generation)
    assert invalidated["complete"] is True and invalidated["unresolved"] == 0, invalidated
    assert not service._slots and not service._claimed_ttys and not service._sessions
    assert not [r for r in harness.receipts() if r["event"] in ("handoff_received", "handoff_rejected")]
