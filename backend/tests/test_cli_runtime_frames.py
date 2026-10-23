"""E1 wire v1 frame codec and frame-scoped receiver (F1, F2, F3, P2, P6 supplements).

Supplement tests: they isolate the codec and the descriptor-association algorithm of
design/e1-runtime-protocol-v7.md section 6.3. The principal seam tests are elsewhere.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import tty
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_frames as frames
from agent_team_backend.cli_runtime_frames import (
    ChannelEof, Direction, FrameReceiver, FrameType, ProtocolError, code,
)

VECTORS = json.loads((Path(__file__).resolve().parents[2]
                      / "native/navide-cli-runtime/tests/vectors/frame-vectors.json").read_text())
TAG = int(VECTORS["runtime_tag"], 16)


def direction(name: str) -> Direction:
    return {"p2r": Direction.PYTHON_TO_RUST, "r2p": Direction.RUST_TO_PYTHON}[name]


def owned_frame(correlation_id: int = 0, *, has_fd: bool = True, meta: bytes = b'{"a":1}') -> bytes:
    return frames.encode_raw(FrameType.EVENT, code.SESSION_OWNED, frames.FLAG_HAS_FD if has_fd else 0,
                             correlation_id, TAG, meta, b"")


def plain_frame(correlation_id: int = 7) -> bytes:
    return frames.encode_raw(FrameType.RESPONSE, code.PING, 0, correlation_id, TAG, b'{"pong":true}', b"")


# ---------------------------------------------------------------------------- F1

def test_golden_vectors_encode_byte_for_byte_and_decode():
    for vector in VECTORS["valid"]:
        expected = bytes.fromhex(vector["hex"])
        encoded = frames.encode_raw(
            FrameType(vector["frame_type"]), vector["code"], vector["flags"], vector["correlation_id"],
            TAG, vector["meta"].encode(), bytes.fromhex(vector["blob"]))
        assert encoded == expected, vector["name"]
        header = frames.parse_header(expected[:32], direction(vector["direction"]), TAG)
        frame = frames.decode_body(header, expected[32:])
        assert frame.correlation_id == vector["correlation_id"]
        assert frame.blob == bytes.fromhex(vector["blob"])
        assert frame.meta == (json.loads(vector["meta"]) if vector["meta"] else {})
        # The public encoder (compact JSON meta) round-trips to an equal frame.
        again = frames.encode(frame)
        header2 = frames.parse_header(again[:32], direction(vector["direction"]), TAG)
        assert frames.decode_body(header2, again[32:]) == frame


def test_section_2_6_worked_example():
    assert frames.encode_raw(FrameType.REQUEST, code.PING, 0, 1, 0x0102030405060708, b"{}", b"").hex() == (
        "4e430201000600000000000000000001" "0102030405060708" "0000000200000000" "7b7d")


def test_runtime_tag_is_first_eight_bytes_big_endian():
    assert frames.runtime_tag("0102030405060708090a0b0c0d0e0f10") == 0x0102030405060708
    for bad in ("short", "zz02030405060708090a0b0c0d0e0f10", ""):
        with pytest.raises(ValueError):
            frames.runtime_tag(bad)


# ------------------------------------------------------------------------- F2/F3

@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda v: v["name"])
def test_invalid_vectors_are_rejected_by_the_declared_rule(vector):
    raw = bytes.fromhex(vector["hex"])
    with pytest.raises(ProtocolError) as caught:
        header = frames.parse_header(raw[:32], direction(vector["direction"]), TAG)
        assert len(raw) >= 32 + header.body_len, "header accepted without its body"
        frames.decode_body(header, raw[32:32 + header.body_len])
    assert caught.value.rule == vector["why"]


def test_frame_split_at_every_byte_boundary_reassembles():
    raw = frames.encode_raw(FrameType.EVENT, code.SESSION_EXITED, 0, 0, TAG, b'{"x":"\xc3\xa9"}', b"\x00\xff\x01")
    for cut in range(len(raw) + 1):
        script = Script([raw[:cut], raw[cut:]] if 0 < cut < len(raw) else [raw])
        got = []
        receiver = FrameReceiver(script.recv, Direction.RUST_TO_PYTHON, TAG, lambda f, fd: got.append((f, fd)))
        with pytest.raises(ChannelEof):
            receiver.on_readable()
        assert [f.blob for f, _ in got] == [b"\x00\xff\x01"], cut


# ---------------------------------------------------------------------- fake kernel

class Script:
    """A byte stream with descriptor attachments, delivered under a chunking plan.

    A descriptor is attached to the read whose range contains the attachment offset (the worst
    case: a read that spans it still receives it), which is what the receiver must never allow
    by asking for at most the bytes left in the current frame.
    """

    def __init__(self, chunks, attachments=None, ctrunc_reads=(), plan=None):
        self.stream = b"".join(chunks)
        self.limits = [len(c) for c in chunks]
        self.attachments = dict(attachments or {})
        self.ctrunc_reads = set(ctrunc_reads)
        self.pos = 0
        self.reads = 0
        self.requested: list[int] = []
        self.plan = list(plan) if plan else None

    def recv(self, need: int):
        self.requested.append(need)
        index = self.reads
        self.reads += 1
        if self.pos >= len(self.stream):
            return b"", [], 0
        if self.plan:
            size = min(self.plan.pop(0), need)
        else:
            size = need
            boundary = 0
            for limit in self.limits:
                boundary += limit
                if boundary > self.pos:
                    size = min(size, boundary - self.pos)
                    break
        size = min(size, len(self.stream) - self.pos)
        data = self.stream[self.pos:self.pos + size]
        fds = []
        for offset in sorted(self.attachments):
            if self.pos <= offset < self.pos + size:
                fds.extend(self.attachments.pop(offset))
        self.pos += size
        flags = socket.MSG_CTRUNC if index in self.ctrunc_reads else 0
        return data, fds, flags


def pipe_fd() -> int:
    read_end, write_end = os.pipe()
    os.close(write_end)
    return read_end


def is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def run(script, tag=TAG):
    got: list[tuple] = []
    claimed: list[int] = []

    def on_frame(frame, fd):
        got.append((frame, fd))
        if fd is not None:
            claimed.append(fd)

    receiver = FrameReceiver(script.recv, Direction.RUST_TO_PYTHON, tag, on_frame)
    try:
        receiver.on_readable()
    except (ChannelEof, ProtocolError) as error:
        return got, claimed, error
    return got, claimed, None


def close_all(fds):
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


# ------------------------------------------------------------------------------ P2

def test_p2_no_fd_frame_followed_by_has_fd_frame_delivered_together():
    a, b = plain_frame(), owned_frame()
    fd = pipe_fd()
    # One stream, descriptor attached to the first byte of the second frame; the whole stream is
    # available at once, so only the receiver's bounded reads keep the frames apart.
    script = Script([a + b], attachments={len(a): [fd]})
    got, claimed, error = run(script)
    assert isinstance(error, ChannelEof)
    assert [(f.code, fd_ is not None) for f, fd_ in got] == [(code.PING, False), (code.SESSION_OWNED, True)]
    assert is_open(claimed[0]) and not os.get_inheritable(claimed[0])
    assert max(script.requested) <= 32 + len(b), "a read must never exceed the current frame's remainder"
    close_all(claimed)


def test_p2_two_has_fd_frames_back_to_back():
    a, b = owned_frame(), owned_frame()
    f1, f2 = pipe_fd(), pipe_fd()
    got, claimed, error = run(Script([a + b], attachments={0: [f1], len(a): [f2]}))
    assert isinstance(error, ChannelEof)
    assert [fd for _, fd in got] == [f1, f2]
    close_all(claimed)


@pytest.mark.parametrize("size", [1, 2, 3, 7, 31, 32, 33])
def test_p2_has_fd_header_split_across_reads_with_descriptor_on_first(size):
    raw = owned_frame()
    fd = pipe_fd()
    got, claimed, error = run(Script([raw], attachments={0: [fd]}, plan=[size] * len(raw)))
    assert isinstance(error, ChannelEof)
    assert len(got) == 1 and got[0][1] == fd
    close_all(claimed)


def test_p2_descriptor_on_a_non_first_read_is_a_protocol_error_and_closed():
    raw = owned_frame()
    fd = pipe_fd()
    got, claimed, error = run(Script([raw], attachments={5: [fd]}, plan=[3, 3, 3, 100]))
    assert isinstance(error, ProtocolError) and not got
    assert not is_open(fd)


def test_p2_descriptor_on_a_no_fd_frame_is_a_protocol_error_and_closed():
    raw = plain_frame()
    fd = pipe_fd()
    got, claimed, error = run(Script([raw], attachments={0: [fd]}))
    assert isinstance(error, ProtocolError) and error.rule == "flags" and not got
    assert not is_open(fd)


def test_p2_has_fd_frame_without_a_descriptor_is_a_protocol_error():
    got, claimed, error = run(Script([owned_frame()]))
    assert isinstance(error, ProtocolError) and not got


def test_p2_two_descriptors_in_one_message_close_both():
    f1, f2 = pipe_fd(), pipe_fd()
    got, claimed, error = run(Script([owned_frame()], attachments={0: [f1, f2]}))
    assert isinstance(error, ProtocolError) and not got
    assert not is_open(f1) and not is_open(f2)


def test_p2_control_truncation_is_a_protocol_error():
    fd = pipe_fd()
    got, claimed, error = run(Script([owned_frame()], attachments={0: [fd]}, ctrunc_reads={0}))
    assert isinstance(error, ProtocolError) and error.rule == "ctrunc" and not got
    assert not is_open(fd)


def test_p2_descriptor_alongside_eof_is_closed():
    fd = pipe_fd()

    class Eof:
        def recv(self, need):
            return b"", [fd], 0

    receiver = FrameReceiver(Eof().recv, Direction.RUST_TO_PYTHON, TAG, lambda f, d: None)
    with pytest.raises((ChannelEof, ProtocolError)):
        receiver.on_readable()
    assert not is_open(fd)


def test_protocol_error_closes_a_pending_descriptor_and_poisons_the_receiver():
    fd = pipe_fd()
    bad = bytearray(owned_frame())
    bad[0] = 0  # magic
    script = Script([bytes(bad)], attachments={0: [fd]})
    receiver = FrameReceiver(script.recv, Direction.RUST_TO_PYTHON, TAG, lambda f, d: None)
    with pytest.raises(ProtocolError):
        receiver.on_readable()
    assert not is_open(fd)
    with pytest.raises(ProtocolError):
        receiver.on_readable()


def test_header_of_oversized_frame_is_rejected_before_the_body_is_requested():
    header = frames.encode_raw(FrameType.EVENT, code.SESSION_EXITED, 0, 0, TAG, b"", b"")[:24] + struct.pack(">II", 65537, 0)
    script = Script([header])
    receiver = FrameReceiver(script.recv, Direction.RUST_TO_PYTHON, TAG, lambda f, d: None)
    with pytest.raises(ProtocolError) as caught:
        receiver.on_readable()
    assert caught.value.rule == "limit" and script.requested == [32]


def test_has_fd_frame_over_4096_bytes_is_a_protocol_error():
    fd = pipe_fd()
    big = owned_frame(meta=json.dumps({"p": "x" * 4100}).encode())
    got, claimed, error = run(Script([big], attachments={0: [fd]}))
    assert isinstance(error, ProtocolError) and error.rule == "limit"
    assert not is_open(fd)


def test_at_most_64_kib_are_consumed_per_callback():
    frame = plain_frame()
    count = (64 * 1024) // len(frame) + 20
    script = Script([frame * count])
    got = []
    receiver = FrameReceiver(script.recv, Direction.RUST_TO_PYTHON, TAG, lambda f, d: got.append(f))
    receiver.on_readable()  # returns at the budget without hitting EOF
    assert len(got) < count, "the callback must stop at its budget instead of draining the stream"
    assert 64 * 1024 <= script.pos < 64 * 1024 + len(frame)


# --------------------------------------------------------------- real kernel (P2/P6)

def send_with_fd(sock: socket.socket, payload: bytes, fd: int | None):
    if fd is None:
        sock.sendall(payload)
        return
    sent = sock.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
    if sent < len(payload):
        sock.sendall(payload[sent:])


def test_real_socketpair_associates_each_descriptor_with_its_frame():
    sender, receiver_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    receiver_sock.setblocking(False)
    master, slave = os.openpty()
    tty.setraw(slave)  # a cooked slave would hold the byte until a newline
    pipes = [pipe_fd() for _ in range(3)]
    try:
        send_with_fd(sender, plain_frame(1), None)
        send_with_fd(sender, owned_frame(meta=b'{"n":0}'), pipes[0])
        send_with_fd(sender, owned_frame(meta=b'{"n":1}'), pipes[1])
        send_with_fd(sender, plain_frame(2), None)
        send_with_fd(sender, owned_frame(meta=b'{"n":2}'), master)
        sender.close()
        got = []
        receiver = FrameReceiver(frames.socket_recv(receiver_sock), Direction.RUST_TO_PYTHON, TAG,
                                 lambda f, fd: got.append((f, fd)))
        with pytest.raises(ChannelEof):
            while True:
                receiver.on_readable()
        assert [f.meta.get("n") for f, fd in got if fd is not None] == [0, 1, 2]
        for _, fd in got:
            if fd is not None:
                assert not os.get_inheritable(fd), "P6: the received descriptor is non-inheritable"
        # The received descriptor is a duplicate of the sent master (P1 shape).
        os.write(got[-1][1], b"x")
        assert os.read(slave, 1) == b"x"
        close_all([fd for _, fd in got if fd is not None])
    finally:
        close_all([master, slave, *pipes])
        receiver_sock.close()


def test_real_socketpair_short_reads_of_a_has_fd_frame():
    sender, receiver_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    receiver_sock.setblocking(False)
    fd = pipe_fd()
    raw = owned_frame()
    sender.sendmsg([raw[:3]], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
    got = []
    receiver = FrameReceiver(frames.socket_recv(receiver_sock), Direction.RUST_TO_PYTHON, TAG,
                             lambda f, d: got.append((f, d)))
    receiver.on_readable()
    assert not got
    sender.sendall(raw[3:])
    receiver.on_readable()
    assert len(got) == 1 and got[0][1] is not None
    close_all([got[0][1], fd])
    sender.close()
    receiver_sock.close()
