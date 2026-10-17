"""A raw wire-v1 peer for the real sidecar binary (supplemental tests only).

It speaks the E1 frames over a real socketpair using the production frame codec and the
frame-scoped receiver, so a test can drive the sidecar below `RustTerminalService` (observation,
framing faults, shutdown reports) without the Python runtime's own policies. It is not the
production client: principal proofs use the production service or the TypeScript WebSocket client.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import threading
import time
import uuid

import psutil

from agent_team_backend import cli_runtime_frames as wire
from agent_team_backend.cli_runtime_frames import Direction, FrameType, code

from .isolation import isolated_environment

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = ROOT / "native/navide-cli-runtime/target/release/navide-cli-runtime"
HARNESS = ROOT / "native/navide-cli-runtime/target-harness/release/navide-cli-runtime"


class FramePeer:
    def __init__(self, root: Path, *, binary: Path = PRODUCT, extra_args: tuple[str, ...] = ()):
        self.root = root
        self.binary = binary
        self.generation = uuid.uuid4().hex
        self.tag = wire.runtime_tag(self.generation)
        self.number = 0
        self.frames: queue.Queue = queue.Queue()
        self.events: list[wire.Frame] = []
        self.responses: dict[int, wire.Frame] = {}
        self.parts: dict[int, list[bytes]] = {}
        self.masters: dict[str, int] = {}
        self.last_part_count = 0
        self.last_part_frame_sizes: list[int] = []
        self._sizes: dict[int, list[int]] = {}
        self.error: Exception | None = None
        self.eof = threading.Event()
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.journal = root / "journal.jsonl"
        self.process = subprocess.Popen(
            [str(binary), "--wire", "1", "--runtime-generation", self.generation, "--owner-pid", str(os.getpid()),
             "--ownership-journal", str(self.journal), "--control-fd", str(child.fileno()), *extra_args],
            env=isolated_environment(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(child.fileno(),))
        child.close()
        self.sock = parent
        self.ready = json.loads(self.process.stdout.readline())
        assert self.ready["event"] == "ready" and self.ready["pid"] == self.process.pid, self.ready
        process = psutil.Process(self.process.pid)
        self.identity = {**self.ready, "created": process.create_time(), "executable": process.exe(),
                         "executableSha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        self.sock.setblocking(False)
        self._stop = threading.Event()
        self.receiver = wire.FrameReceiver(wire.socket_recv(self.sock), Direction.RUST_TO_PYTHON, self.tag, self._on_frame)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    # ------------------------------------------------------------------ receiving

    def _on_frame(self, frame: wire.Frame, fd: int | None) -> None:
        if fd is not None:
            self.masters[frame.meta["session_id"]] = fd
        self.frames.put(frame)

    def _read(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self.sock], [], [], 0.05)
            if not ready:
                continue
            try:
                self.receiver.on_readable()
            except wire.ChannelEof:
                self.eof.set()
                return
            except Exception as error:  # noqa: BLE001
                self.error = error
                self.eof.set()
                return

    def receive(self, timeout: float = 15) -> wire.Frame:
        frame = self.frames.get(timeout=timeout)
        if frame.frame_type in (FrameType.RESPONSE, FrameType.ERROR):
            self.responses[frame.correlation_id] = frame
        elif frame.code == code.OBSERVATION_PART:
            self.parts.setdefault(frame.correlation_id, []).append(frame.blob)
            self._sizes.setdefault(frame.correlation_id, []).append(frame.wire_size - wire.HEADER_LEN)
            self.events.append(frame)
        else:
            self.events.append(frame)
        return frame

    def wait_for(self, predicate, message: str = "condition", timeout: float = 15):
        deadline = time.monotonic() + timeout
        while True:
            found = predicate()
            if found:
                return found
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"timed out waiting for {message}")
            try:
                self.receive(timeout=min(left, 0.2))
            except queue.Empty:
                pass

    # -------------------------------------------------------------------- sending

    def send(self, request_code: int, meta: dict | None = None, blob: bytes = b"") -> int:
        self.number += 1
        raw = wire.encode(wire.Frame(FrameType.REQUEST, request_code, 0, self.number, self.tag, meta or {}, blob))
        self.sock.setblocking(True)
        try:
            self.sock.sendall(raw)
        finally:
            self.sock.setblocking(False)
        return self.number

    def result(self, request: int, *, allow_error: bool = False, timeout: float = 15) -> wire.Frame:
        self.wait_for(lambda: request in self.responses, f"answer {request}", timeout)
        frame = self.responses.pop(request)
        if not allow_error:
            assert frame.frame_type is FrameType.RESPONSE, frame
        return frame

    def create(self, argv: list[str], *, agent_key: str = "", pane: str | None = None) -> tuple[str, str]:
        sid, generation = str(uuid.uuid4()), uuid.uuid4().hex
        meta = {"session_id": sid, "session_generation": generation, "pane_id": pane or sid,
                "workspace_path": str(self.root), "argv": argv, "cwd": str(self.root),
                "env": isolated_environment(self.root), "cols": 100, "rows": 30, "agent_key": agent_key}
        self.result(self.send(code.CREATE, meta))
        return sid, generation

    def observe(self, vendor: str, path, mode: str, *, expected: bytes = b"", session=None,
                allow_error: bool = False, **args):
        """One observation. Returns (document, raw_anchor_or_None, final_frame)."""
        meta = {"vendor": vendor, "path": str(path), "mode": mode, **args}
        if session:
            meta.update(session_id=session[0], session_generation=session[1])
        request = self.send(code.OBSERVE, meta, expected)
        final = self.result(request, allow_error=True)
        if final.frame_type is FrameType.ERROR and not final.meta.get("observation_parts"):
            if allow_error:
                return None, None, final
            raise AssertionError(final.meta)
        parts = self.parts.pop(request, [])
        self.last_part_count, self.last_part_frame_sizes = len(parts), self._sizes.pop(request, [])
        assert len(parts) == final.meta["observation_parts"], (len(parts), final.meta)
        document = json.loads(b"".join(parts))
        anchor = final.blob if final.meta.get("raw_anchor") else None
        return document, anchor, final

    def part_sizes(self, request: int) -> list[int]:
        return [len(blob) for blob in self.parts.get(request, [])]

    # -------------------------------------------------------------------- closing

    def close(self) -> None:
        try:
            if self.process.poll() is None and not self.eof.is_set():
                request = self.send(code.SHUTDOWN, {})
                self.result(request, allow_error=True, timeout=20)
        finally:
            self._stop.set()
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            self.reader.join(timeout=2)
            for fd in self.masters.values():
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.sock.close()
