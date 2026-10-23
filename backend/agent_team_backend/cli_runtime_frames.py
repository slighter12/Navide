"""E1 wire v1 frame codec and the frame-scoped control-channel receiver.

Design: design/e1-runtime-protocol-v7.md sections 2 (frame format), 2.3 (codes and
directions) and 6.3 (the frame-scoped receiver). Nothing here knows about sessions: it
turns bytes (and at most one `SCM_RIGHTS` descriptor per `HAS_FD` frame) into frames.
"""
from __future__ import annotations

import enum
import json
import os
import socket
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

HEADER_LEN = 32
WIRE_VERSION = 2
FLAG_HAS_FD = 1

#: Python -> sidecar limits (2.1).
MAX_META_P2R = 4 * 1024 * 1024
MAX_BLOB_P2R = 1024 * 1024
#: Sidecar -> Python: `meta_len + blob_len` (2.1, MUST). Checked before the body is read.
MAX_FRAME_R2P = 65_536
#: The only `HAS_FD` frame is SESSION_OWNED; it stays this small (2.1).
MAX_HAS_FD_FRAME = 4096
#: At most this many bytes are consumed per readiness callback (6.3).
READ_BUDGET = 64 * 1024


class FrameType(enum.IntEnum):
    REQUEST = 1
    RESPONSE = 2
    ERROR = 3
    EVENT = 4


class code:  # noqa: N801 - a namespace of wire constants, spelled like the spec table
    CREATE = 0x0001
    ADOPT = 0x0002
    RESIZE = 0x0003
    KILL = 0x0004
    CANCEL = 0x0005
    PING = 0x0006
    SHUTDOWN = 0x0007
    OBSERVE = 0x0008
    TTY_EOF = 0x0009
    TTY_SERVICING = 0x000A  # Windows only: a protocol error on POSIX
    SESSION_OWNED = 0x0101
    SESSION_HANGUP = 0x0102
    SESSION_EXITED = 0x0103
    SESSION_CLEANED = 0x0104
    SESSION_OWNERSHIP = 0x0105
    OBSERVATION_PART = 0x0106
    SESSION_ROOT_LIVE = 0x0107
    FATAL = 0x01FF


CODE_NAMES = {value: name for name, value in vars(code).items() if name.isupper()}
_EVENT_CODES = {code.SESSION_OWNED, code.SESSION_HANGUP, code.SESSION_EXITED, code.SESSION_CLEANED,
                code.SESSION_OWNERSHIP, code.OBSERVATION_PART, code.SESSION_ROOT_LIVE, code.FATAL}
_REQUEST_CODES = set(range(code.CREATE, code.TTY_EOF + 1))
if sys.platform == "win32":  # TTY_SERVICING exists on Windows only; on POSIX it stays a protocol error
    _REQUEST_CODES.add(code.TTY_SERVICING)


class Direction(enum.Enum):
    #: Frames the sidecar receives.
    PYTHON_TO_RUST = "p2r"
    #: Frames Python receives.
    RUST_TO_PYTHON = "r2p"


class ProtocolError(Exception):
    """A frame or channel violation: the receiver stops reading and treats the runtime as lost."""

    def __init__(self, rule: str, message: str = ""):
        super().__init__(f"{rule}: {message}" if message else rule)
        self.rule = rule


class ChannelEof(Exception):
    """The peer closed the control channel (a partial frame at EOF is the same loss)."""


@dataclass(frozen=True)
class Header:
    frame_type: FrameType
    code: int
    flags: int
    correlation_id: int
    runtime_tag: int
    meta_len: int
    blob_len: int

    @property
    def body_len(self) -> int:
        return self.meta_len + self.blob_len


@dataclass
class Frame:
    frame_type: FrameType
    code: int
    flags: int
    correlation_id: int
    runtime_tag: int
    meta: dict[str, Any] = field(default_factory=dict)
    blob: bytes = b""
    #: Bytes this frame occupied on the wire (receipts only; not part of its identity).
    wire_size: int = field(default=0, compare=False)


def runtime_tag(generation: str) -> int:
    """First 8 bytes of the 128-bit runtime generation token (32 hex characters), big-endian."""
    if len(generation) != 32 or any(c not in "0123456789abcdefABCDEF" for c in generation):
        raise ValueError("runtime generation must be 32 hex characters")
    return int(generation[:16], 16)


def _code_valid(frame_type: FrameType, frame_code: int, direction: Direction) -> None:
    if direction is Direction.RUST_TO_PYTHON and frame_type in (FrameType.RESPONSE, FrameType.ERROR):
        if frame_code not in _REQUEST_CODES:
            raise ProtocolError("code", f"unexpected answer code {frame_code:#06x}")
    elif direction is Direction.RUST_TO_PYTHON and frame_type is FrameType.EVENT:
        if frame_code not in _EVENT_CODES:
            raise ProtocolError("code", f"unexpected event code {frame_code:#06x}")
    elif direction is Direction.PYTHON_TO_RUST and frame_type is FrameType.REQUEST:
        if frame_code not in _REQUEST_CODES:
            raise ProtocolError("code", f"unexpected request code {frame_code:#06x}")
    else:
        raise ProtocolError("direction", f"{frame_type.name} is not valid in {direction.value}")


def parse_header(data: bytes, direction: Direction, expected_tag: int) -> Header:
    """Validate a 32-byte header before any body byte is read."""
    if len(data) != HEADER_LEN:
        raise ProtocolError("limit", "short header")
    if data[:2] != b"NC":
        raise ProtocolError("magic")
    if data[2] != WIRE_VERSION:
        raise ProtocolError("version")
    try:
        frame_type = FrameType(data[3])
    except ValueError:
        raise ProtocolError("frame_type") from None
    frame_code, flags, correlation_id, tag, meta_len, blob_len = struct.unpack(">HHQQII", data[4:32])
    _code_valid(frame_type, frame_code, direction)
    if flags & ~FLAG_HAS_FD:
        raise ProtocolError("flags", "reserved bit set")
    if flags & FLAG_HAS_FD and not (direction is Direction.RUST_TO_PYTHON and frame_type is FrameType.EVENT
                                    and frame_code == code.SESSION_OWNED):
        raise ProtocolError("flags", "HAS_FD outside SESSION_OWNED")
    if tag != expected_tag:
        raise ProtocolError("runtime_tag")
    if direction is Direction.PYTHON_TO_RUST and frame_type is FrameType.REQUEST and correlation_id == 0:
        raise ProtocolError("correlation", "zero request id")
    if direction is Direction.PYTHON_TO_RUST:
        if meta_len > MAX_META_P2R or blob_len > MAX_BLOB_P2R:
            raise ProtocolError("limit", "request exceeds the advertised limits")
    elif meta_len + blob_len > MAX_FRAME_R2P:
        raise ProtocolError("limit", "frame exceeds max_frame_r2p")
    if flags & FLAG_HAS_FD and HEADER_LEN + meta_len + blob_len > MAX_HAS_FD_FRAME:
        raise ProtocolError("limit", "HAS_FD frame exceeds 4096 bytes")
    return Header(frame_type, frame_code, flags, correlation_id, tag, meta_len, blob_len)


def decode_body(header: Header, body: bytes) -> Frame:
    if len(body) != header.body_len:
        raise ProtocolError("limit", "body length mismatch")
    meta_bytes, blob = body[:header.meta_len], body[header.meta_len:]
    if meta_bytes:
        try:
            meta = json.loads(meta_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProtocolError("meta", "not UTF-8 JSON") from None
        if not isinstance(meta, dict):
            raise ProtocolError("meta", "not an object")
    else:
        meta = {}
    return Frame(header.frame_type, header.code, header.flags, header.correlation_id, header.runtime_tag,
                 meta, bytes(blob), HEADER_LEN + header.body_len)


def encode_raw(frame_type: int, frame_code: int, flags: int, correlation_id: int, tag: int,
               meta: bytes, blob: bytes) -> bytes:
    return (b"NC" + bytes([WIRE_VERSION, int(frame_type)])
            + struct.pack(">HHQQII", frame_code, flags, correlation_id, tag, len(meta), len(blob))
            + meta + blob)


def encode(frame: Frame) -> bytes:
    meta = json.dumps(frame.meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encode_raw(frame.frame_type, frame.code, frame.flags, frame.correlation_id,
                      frame.runtime_tag, meta, frame.blob)


RecvFn = Callable[[int], "tuple[bytes, list[int], int]"]


def socket_recv(sock: socket.socket) -> RecvFn:
    """`recv(need) -> (data, descriptors, msg_flags)` over `recvmsg`; raises BlockingIOError."""
    space = socket.CMSG_SPACE(struct.calcsize("i"))

    def recv(need: int) -> tuple[bytes, list[int], int]:
        data, ancillary, flags, _address = sock.recvmsg(need, space)
        fds: list[int] = []
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                count = len(payload) // struct.calcsize("i")
                fds.extend(struct.unpack(f"{count}i", payload[:count * struct.calcsize("i")]))
        return data, fds, flags

    return recv


def _close(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


class FrameReceiver:
    """The section 6.3 receiver. Every read asks for at most the bytes left in the current frame,
    so one read never holds bytes of two frames and a descriptor belongs to the frame whose first
    byte that read returned.

    `on_frame(frame, fd)` receives the frame and, in the same synchronous step, ownership of the
    descriptor (`None` for a frame without one); the callee closes it. Any protocol error closes
    every descriptor the receiver still holds, and later calls raise again.
    """

    def __init__(self, recv: RecvFn, direction: Direction, expected_tag: int,
                 on_frame: Callable[[Frame, int | None], None]):
        self._recv = recv
        self._direction = direction
        self._tag = expected_tag
        self._on_frame = on_frame
        self._frame_offset = 0
        self._need = HEADER_LEN
        self._buf = bytearray()
        self._header: Header | None = None
        self._pending_fd: int | None = None
        self._failed: Exception | None = None

    def close(self) -> None:
        """Runtime end: release a descriptor that was received but never claimed."""
        _close(self._pending_fd)
        self._pending_fd = None

    def _fail(self, error: Exception, *fds: int) -> None:
        for fd in fds:
            _close(fd)
        self.close()
        self._failed = error
        raise error

    def on_readable(self) -> None:
        if self._failed is not None:
            raise self._failed
        consumed = 0
        while consumed < READ_BUDGET:
            try:
                data, fds, flags = self._recv(self._need)
            except BlockingIOError:
                return
            except OSError as error:
                self._fail(ChannelEof(f"control channel error: {error.strerror}"))
            if flags & socket.MSG_CTRUNC:
                self._fail(ProtocolError("ctrunc", "ancillary data truncated"), *fds)
            for fd in fds:
                os.set_inheritable(fd, False)
            if len(fds) > 1 or (fds and (self._frame_offset != 0 or self._pending_fd is not None)):
                self._fail(ProtocolError("descriptor", "unexpected descriptor count or placement"), *fds)
            if not data:
                self._fail(ChannelEof("control channel EOF"), *fds)
            if fds:
                self._pending_fd = fds[0]
            self._buf.extend(data)
            self._need -= len(data)
            self._frame_offset += len(data)
            consumed += len(data)
            try:
                if self._header is None and self._need == 0:
                    self._header = parse_header(bytes(self._buf), self._direction, self._tag)
                    if self._pending_fd is not None and not self._header.flags & FLAG_HAS_FD:
                        raise ProtocolError("flags", "descriptor on a frame without HAS_FD")
                    self._buf.clear()
                    self._need = self._header.body_len
                if self._header is not None and self._need == 0:
                    header, body, fd = self._header, bytes(self._buf), self._pending_fd
                    if header.flags & FLAG_HAS_FD and fd is None:
                        raise ProtocolError("descriptor", "HAS_FD frame without a descriptor")
                    frame = decode_body(header, body)
                    self._pending_fd = None
                    self._header, self._frame_offset, self._need = None, 0, HEADER_LEN
                    self._buf.clear()
                    self._on_frame(frame, fd)  # takes ownership of `fd` at once
            except ProtocolError as error:
                self._fail(error)
