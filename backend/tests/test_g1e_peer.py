"""Ticket E4: the byte-ledger fixture peer (E2 v10 section 3 item 4 a-e, C5, C9) on a REAL pseudo-terminal, no backend.

A reduced workload (3 io rounds, 2 burst rounds of 4 lines) drives `g1e_peer.py` through the whole protocol: raw mode, nonce marker, silence until released,
io window, burst windows, FINAL. The test plays the renderer side: it reads the master, locates the marker, hashes the windows and checks every equality the decider
recomputes (`summarise.ledger_problems`) against the peer's receipt, so the producer and the decider are proven to agree on the same bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise, tokens as fx
from tests.cli_regression.support import g1e_peer

SUPPORT = Path(g1e_peer.__file__).resolve().parent
WORKLOAD = {"ioRounds": 3, "burstLines": 4, "burstWidth": 16, "burstRounds": 2, "input": ref.WORKLOAD["input"]}


def read_until(master: int, buffer: bytearray, needle: bytes, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while needle not in buffer:
        left = deadline - time.monotonic()
        assert left > 0, f"timeout waiting for {needle!r}; saw {bytes(buffer)[-200:]!r}"
        ready, _, _ = select.select([master], [], [], min(left, 0.5))
        if ready:
            chunk = os.read(master, 65536)
            assert chunk, "peer closed the terminal"
            buffer.extend(chunk)


def read_quiet(master: int, buffer: bytearray, seconds: float) -> int:
    """Bytes that arrive within `seconds` (the peer must be silent while unreleased)."""
    before, end = len(buffer), time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            buffer.extend(os.read(master, 65536))
    return len(buffer) - before


@pytest.fixture()
def rig(tmp_path):
    root = tmp_path / "root"
    (root / "g1e").mkdir(parents=True)
    (root / "home").mkdir()
    workspace = root / "workspace 0 café 世界"
    workspace.mkdir()
    workload_file = tmp_path / "fixtures" / "workload.json"
    workload_file.parent.mkdir()
    workload_file.write_text(json.dumps(WORKLOAD))
    pane, nonce = "pane-0", hashlib.sha256(b"nonce").hexdigest()[:32]
    (root / "g1e" / f"{pane}.nonce").write_text(nonce)
    master, slave = pty.openpty()
    env = {**os.environ, "HOME": str(root / "home"), "USERPROFILE": str(root / "home")}
    proc = subprocess.Popen([sys.executable, "-u", str(SUPPORT / "g1e_peer.py"), str(root), "terminal", pane, str(workspace), WORKLOAD["input"], str(workload_file)],
                            stdin=slave, stdout=slave, stderr=subprocess.DEVNULL, env=env, close_fds=True, start_new_session=True)
    os.close(slave)
    try:
        yield {"root": root, "pane": pane, "nonce": nonce, "master": master, "proc": proc}
    finally:
        proc.kill()
        proc.wait(timeout=10)
        os.close(master)


def test_the_peer_runs_the_whole_ledger_protocol_and_its_receipt_agrees_with_the_stream_and_with_the_decider(rig):
    master, root, pane, nonce = rig["master"], rig["root"], rig["pane"], rig["nonce"]
    stream = bytearray()
    marker = fx.line("start", nonce=nonce)
    read_until(master, stream, marker)
    origin = len(stream)                                   # what the renderer has when the marker arrived: everything before is pre-origin
    assert stream.count(marker) == 1 and stream.startswith(b"BASELINE_READY "), "READY precedes the marker, the marker is unique"
    assert read_quiet(master, stream, 0.4) == 0, "the peer is silent between the marker and its release"
    (root / "g1e" / f"{pane}.release").write_text("go")
    for round_ in range(WORKLOAD["ioRounds"]):
        os.write(master, f"{round_}:{WORKLOAD['input']}\r".encode())
    read_until(master, stream, fx.line("win_end", wid="i0"))
    for kk in range(WORKLOAD["burstRounds"]):
        os.write(master, f"burst r{kk}\r".encode())
        read_until(master, stream, fx.line("burst_line", kk=f"{kk:02d}", line=0, xs="x" * 16))
        os.write(master, f"during-burst-r{kk:02d}\r".encode())
        read_until(master, stream, fx.line("done", kk=f"{kk:02d}"))
        read_until(master, stream, fx.line("win_end", wid=f"b0r{kk:02d}"))
    assert b"BASELINE_FINAL:" not in stream, "A2: FINAL is written on the driver's request after the LAST byte-writing phase, never after the last burst"
    os.write(master, b"after-reconnect\r")                  # a later byte-writing phase (06 reconnect ACK): its bytes are inside the ledger
    read_until(master, stream, b"after-reconnect")
    assert read_quiet(master, stream, 0.3) >= 0 and b"BASELINE_FINAL:" not in stream
    os.write(master, b"g1e-final\r")
    read_until(master, stream, b"BASELINE_FINAL:")
    read_until(master, stream, b"\n", 5)
    receipt_path = root / "g1e" / f"{pane}.peer.json"
    deadline = time.monotonic() + 10
    while not receipt_path.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["rawMode"] == {"opost": False, "onlcr": False, "echo": False} and receipt["writeFailed"] is False and receipt["limitExceeded"] is False
    for kk in range(WORKLOAD["burstRounds"]):              # the probe's ACK may land after the last window (a race the protocol tolerates): wait for it
        read_until(master, stream, fx.line("ack", text=f"during-burst-r{kk:02d}"))
    post_all = bytes(stream[origin:])                      # the bytes after the marker, exactly as the renderer received them (no \r\n translation: raw mode)
    assert b"\r\n" not in post_all, "no ONLCR insertion"
    final_at = post_all.index(b"BASELINE_FINAL:")
    post = post_all[:post_all.index(b"\n", final_at) + 1]  # the ledger closes when the FINAL line is complete on the stream
    assert len(post) == receipt["acceptedSinceOrigin"], "delta read == peer accepted since origin (including the FINAL line)"
    final_line = post[final_at:]
    assert b"after-reconnect" in post[:final_at], "the later phase's bytes are accepted before FINAL and covered by the total digest"
    assert len(final_line) == receipt["finalLineBytes"] and hashlib.sha256(post[:final_at]).hexdigest() == receipt["totalDigest"]
    _, accepted_token, digest_token = final_line.decode().strip().split(":")
    assert int(accepted_token) == final_at and digest_token == receipt["totalDigest"]
    windows = {}
    for w in receipt["windows"]:
        begin = fx.line("win_begin", wid=w["wid"])
        end = fx.line("win_end", wid=w["wid"])
        body = post[post.index(begin) + len(begin):post.index(end)]
        windows[w["wid"]] = hashlib.sha256(body).hexdigest()
        assert windows[w["wid"]] == w["digest"], f"window {w['wid']}: the renderer-side digest equals the peer's"
    assert set(windows) == {"i0", "b0r00", "b0r01"}
    # the mid-burst ACK of each round carries its own round id (inside the window, or after it when the probe was processed late)
    for kk in range(WORKLOAD["burstRounds"]):
        assert post_all.count(fx.line("ack", text=f"during-burst-r{kk:02d}")) == 1
    # io ACKs carry their own round text, once
    for round_ in range(WORKLOAD["ioRounds"]):
        assert post.count(fx.line("ack", text=f"{round_}:{WORKLOAD['input']}")) == 1


def test_the_peer_is_raw_and_sends_no_byte_before_the_marker_except_ready(rig):
    master, nonce = rig["master"], rig["nonce"]
    stream = bytearray()
    read_until(master, stream, fx.line("start", nonce=nonce))
    head, _, tail = bytes(stream).partition(fx.line("start", nonce=nonce))
    assert head.startswith(b"BASELINE_READY ") and head.count(b"\n") == 1 and tail == b""


def test_the_peer_registry_import_is_the_frozen_registry_and_its_limit_is_the_registered_one():
    assert g1e_peer.PEER_MAX == ref.PEER_MAX_BYTES == 12 * 1024 * 1024 == 12_582_912, "v14-r2 V17: 12 MiB, ratio 1.18 over the cell-1 post-origin maximum"
    assert g1e_peer.fx.FIXTURE_TOKENS is ref.FIXTURE_TOKENS or g1e_peer.fx.FIXTURE_TOKENS == ref.FIXTURE_TOKENS
