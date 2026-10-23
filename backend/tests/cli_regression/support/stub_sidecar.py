"""A stand-in sidecar that speaks the wire-v1 handshake and then misbehaves on cue.

Supplemental only: it exists to put protocol violations that the real sidecar will never emit onto
the production receiver (`RustTerminalService`) and prove the receiver's side of the contract
(E1 O2, F3 Python side as a full path). Usage: ``stub_sidecar.py <mode>`` with the real argument
vector of the sidecar after it (the mode is carried in NAVIDE_STUB_MODE).
"""
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.environ["NAVIDE_STUB_BACKEND"])
from agent_team_backend import cli_runtime_frames as wire  # noqa: E402
from agent_team_backend.cli_runtime_frames import Direction, FrameType, code  # noqa: E402

args = sys.argv[1:]
generation = args[args.index("--runtime-generation") + 1]
fd = int(args[args.index("--control-fd") + 1])
mode = os.environ["NAVIDE_STUB_MODE"]
tag = wire.runtime_tag(generation)
limits = json.loads(os.environ["NAVIDE_STUB_LIMITS"])
ready = {"event": "ready", "protocol": "navide-acr", "wire": 2, "version": "stub", "runtime": "navide-cli-runtime",
         "runtime_generation": generation, "pid": os.getpid(), "owner_pid": os.getppid(), "handoff": "scm_rights",
         "ownership_gate": True, "limits": limits}
sys.stdout.write(json.dumps(ready) + "\n")
sys.stdout.flush()
sock = socket.socket(fileno=fd)


def read_exact(count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            sys.exit(0)
        data += chunk
    return data


def next_request():
    header = wire.parse_header(read_exact(32), Direction.PYTHON_TO_RUST, tag)
    return wire.decode_body(header, read_exact(header.body_len))


def part(cid, number, blob, tag=tag):
    return wire.encode_raw(FrameType.EVENT, code.OBSERVATION_PART, 0, cid, tag, json.dumps({"part": number}).encode(), blob)


def final(cid, parts):
    return wire.encode_raw(FrameType.RESPONSE, code.OBSERVE, 0, cid, tag, json.dumps({"observation_parts": parts}).encode(), b"")


late = {"done": False}


def answer(frame_type, request_code, cid, meta):
    return wire.encode_raw(frame_type, request_code, 0, cid, tag, json.dumps(meta).encode(), b"")


while True:
    request = next_request()
    if request.code == code.PING:
        if mode == "wrong-code":  # answers a PING with a KILL response
            sock.sendall(answer(FrameType.RESPONSE, code.KILL, request.correlation_id, {"accepted": True}))
            continue
        if mode == "late-answer" and not late["done"]:
            late["done"] = True
            time.sleep(1.0)  # answered after the Python side gave up on it
        if mode == "duplicate-answer":
            one = answer(FrameType.RESPONSE, code.PING, request.correlation_id, {"pong": True, "bookkeeping": {}})
            sock.sendall(one + one)
            continue
        if mode == "unsolicited":
            sock.sendall(answer(FrameType.RESPONSE, code.PING, request.correlation_id + 5000, {"pong": True}))
            continue
        sock.sendall(wire.encode_raw(FrameType.RESPONSE, code.PING, 0, request.correlation_id, tag,
                                     b'{"pong":true,"bookkeeping":{}}', b""))
        continue
    if mode in ("create-silent", "create-prevented", "create-silent-no-cancel"):
        # A CREATE that is never answered and never identified (no SESSION_OWNED). PING stays answered.
        if request.code == code.CANCEL:
            if mode == "create-prevented":
                sock.sendall(answer(FrameType.RESPONSE, code.CANCEL, request.correlation_id, {"state": "prevented"}))
            elif mode == "create-silent":
                sock.sendall(answer(FrameType.RESPONSE, code.CANCEL, request.correlation_id, {"state": "released"}))
        continue
    if request.code == code.CREATE and mode == "create-cleanup-failed":
        meta = {"code": "CLEANUP_FAILED", "stage": "spawn", "message": "a gated child could not be verified reaped",
                "effect": "partial"}
        sock.sendall(wire.encode_raw(FrameType.ERROR, code.CREATE, 0, request.correlation_id, tag, json.dumps(meta).encode(), b""))
        continue
    if request.code != code.OBSERVE:
        continue
    cid = request.correlation_id
    if mode == "too-many-parts":
        for number in range(limits["max_observation_parts"] + 1):
            sock.sendall(part(cid, number, b"{}"))
    elif mode == "out-of-order":
        sock.sendall(part(cid, 0, b"{}") + part(cid, 2, b"{}"))
    elif mode == "unknown-id":
        sock.sendall(part(cid + 1000, 0, b"{}"))
    elif mode == "after-final":
        sock.sendall(part(cid, 0, b"{}") + final(cid, 1) + part(cid, 1, b"{}"))
    elif mode == "aggregate":
        # Parts that individually fit, and in count stay under the part bound, but whose total is over
        # the byte bound the sidecar advertised (a lowered max_observation_bytes in the stub's ready).
        for number in range(8):
            sock.sendall(part(cid, number, b"x" * 60_000))
    elif mode in ("late-parts", "late-parts-endless"):
        # An observation the Python side has given up on (its deadline is shorter than this sleep): the sidecar
        # cannot cancel it, so it keeps sending. Valid, ordered parts; the endless variant never stops.
        time.sleep(1.0)
        count = 3 if mode == "late-parts" else limits["max_observation_parts"] + 5
        for number in range(count):
            sock.sendall(part(cid, number, b"{}"))
        if mode == "late-parts":
            sock.sendall(final(cid, 3))
        continue
    elif mode == "late-ok":
        sock.sendall(part(cid, 0, b'{"parser":"p"}') + final(cid, 1))
    time.sleep(30)
