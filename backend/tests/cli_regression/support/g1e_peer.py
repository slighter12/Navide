"""Byte-ledger fixture peer for the G1-E measured windows (E2 v10 section 3 item 4 a-e, C5, C9). Launcher + the ledger writer.

`python -u g1e_peer.py <root> <vendor> <pane> <workspace> <input> <workload.json> [flags]` is `baseline_peer.py` with `baseline_peer.LEDGER` installed:
every measured peer byte goes through ONE function `emit(buf)` under one lock; the slave is put in raw mode (no OPOST/ONLCR/ECHO/ICANON/ISIG/ICRNL) before the first
byte and the re-read flags are reported; after raw mode and before any measured byte the peer writes exactly `BASELINE_LEDGER_START:<nonce>\\n` and then stays silent
until the driver releases it through a side file. The nonce and the release travel over private files that never touch the terminal:
    <root>/g1e/<pane>.nonce      written by the driver before the create (32 lowercase hex characters)
    <root>/g1e/<pane>.release    written by the driver after it snapshotted the reader counters
    <root>/g1e/<pane>.peer.json  written by the peer (receipt: windows, digests, accepted counts, raw-mode flags)
Fixture strings come only from the registry `FIXTURE_TOKENS` of `g1e/tokens.py` (imported here); there is no other spelling.
FINAL is written when the driver sends the input line `g1e-final` after the LAST byte-writing phase (observation and reconnect included; assignment 2, A2), so the ledger and the log identity cover the whole stream.
Short writes are counted exactly as accepted; EINTR retries; EAGAIN waits for writability; any other error or a zero return is `writeFailed`.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import sys
import threading
import time
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent
sys.path.insert(0, str(SUPPORT.parents[3]))   # backend/ : `tests.cli_regression...` importable from the peer process
from tests.cli_regression.support.g1e import tokens as fx  # noqa: E402  (stdlib-only registry of the fixture byte strings; no application module is imported)

PEER_MAX = 12 * 1024 * 1024  # = reference.PEER_MAX_BYTES (a test asserts the equality; the peer does not import jsonschema)


class LedgerPeer:
    def __init__(self, root: Path, pane: str, workload: dict, peer_index: int) -> None:
        self.root, self.pane, self.workload, self.peer = root, pane, workload, peer_index
        self.dir = root / "g1e"
        self.lock = threading.Lock()
        self.fd_out = 1
        self.accepted = 0            # bytes accepted since the start marker (the ledger origin)
        self.total = hashlib.sha256()
        self.write_failed = False
        self.limit_exceeded = False
        self.raw_flags: dict = {}
        self.windows: list[dict] = []
        self._open: dict | None = None
        self.io_rounds_done = 0
        self.burst_done = 0
        self.origin_marker_bytes = 0
        self.final_line_bytes = 0
        self.final_written = False
        self.peer_origin = 0
        self.started = False

    # ---------------------------------------------------------------- raw mode and handshake
    def make_raw(self) -> None:
        import termios
        mode = termios.tcgetattr(0)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = mode
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
        oflag &= ~(termios.OPOST | getattr(termios, "ONLCR", 0))   # cfmakeraw clears OPOST only; the reported ONLCR bit must be clear as well
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB)
        cflag |= termios.CS8
        cc[termios.VMIN], cc[termios.VTIME] = 1, 0
        termios.tcsetattr(0, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        again = termios.tcgetattr(0)       # re-read: what the kernel really holds
        self.raw_flags = {"opost": bool(again[1] & termios.OPOST), "onlcr": bool(again[1] & getattr(termios, "ONLCR", 0)), "echo": bool(again[3] & termios.ECHO)}
        if any(self.raw_flags.values()):
            raise SystemExit("peer cannot run raw: not a measured peer")

    def handshake(self) -> None:
        nonce = (self.dir / f"{self.pane}.nonce").read_text().strip()
        if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            raise SystemExit("bad nonce")
        marker = fx.line("start", nonce=nonce)
        self._write_all(marker, counted=False)
        self.origin_marker_bytes = len(marker)
        self.started = True
        while not (self.dir / f"{self.pane}.release").exists():   # silent until released
            time.sleep(0.002)
        self.peer_origin = self.accepted

    # ---------------------------------------------------------------- the single writer
    def _write_all(self, buf: bytes, counted: bool = True) -> None:
        off = 0
        while off < len(buf):
            try:
                n = os.write(self.fd_out, buf[off:])
            except InterruptedError:
                continue
            except BlockingIOError:
                select.select([], [self.fd_out], [])
                continue
            except OSError:
                self.write_failed = True
                raise
            if n <= 0:
                self.write_failed = True
                raise OSError("zero write")
            if counted:
                self.accepted += n
                self.total.update(buf[off:off + n])
                if self._open is not None:
                    self._open["hash"].update(buf[off:off + n])
            off += n
        if counted and self.accepted > PEER_MAX:
            self.limit_exceeded = True
            raise SystemExit("peer byte limit exceeded")

    def emit(self, buf: bytes) -> None:
        with self.lock:
            self._write_all(buf)

    def emit_line(self, name: str, **kw) -> None:
        self.emit(fx.line(name, **kw))

    def _begin(self, wid: str) -> None:
        """BEGIN line and window opening are ONE locked step: a probe ACK from the input thread must fall either before BEGIN or inside the window, never in between."""
        with self.lock:
            self._write_all(fx.line("win_begin", wid=wid))
            self._open = {"wid": wid, "start": self.accepted, "hash": hashlib.sha256()}

    def _end(self) -> None:
        """Window closing and the END line are ONE locked step for the same reason."""
        with self.lock:
            window = self._open
            self._open = None
            self.windows.append({"wid": window["wid"], "start": window["start"], "end": self.accepted, "digest": window["hash"].hexdigest()})
            self._write_all(fx.line("win_end", wid=window["wid"]))

    # ---------------------------------------------------------------- the workload
    def io_round(self, text: str) -> None:
        """`<round>:<input>`: the io window opens before the first ACK and closes after the last registered round."""
        if self.io_rounds_done == 0:
            self._begin(f"i{self.peer}")
        self.emit_line("ack", text=text)
        self.io_rounds_done += 1
        if self.io_rounds_done == self.workload["ioRounds"]:
            self._end()

    def burst_round(self, kk: int) -> None:
        self._begin(f"b{self.peer}r{kk:02d}")
        for index in range(self.workload["burstLines"]):
            self.emit_line("burst_line", kk=f"{kk:02d}", line=index, xs="x" * self.workload["burstWidth"])
        self.emit_line("done", kk=f"{kk:02d}")
        self._end()
        self.burst_done += 1

    def mid_burst_ack(self, kk: int) -> None:
        self.emit_line("ack", text=fx.INPUTS["mid_burst"].format(kk=f"{kk:02d}"))

    def final(self) -> None:
        """The FINAL token's accepted count and digest are read and the line is written under ONE lock: nothing can slip in between."""
        with self.lock:
            digest = self.total.hexdigest()
            self._final_digest = digest
            accepted_before = self.accepted
            line = fx.line("final", accepted=accepted_before, digest=digest)
            self._write_all(line)
            self.final_line_bytes = len(line)
            self.final_written = True
            accepted_total = self.accepted
        self.write_receipt(accepted_total)

    def write_receipt(self, accepted_total: int) -> None:
        receipt = {"schema": "acr-g1e-peer/1", "peerIndex": self.peer, "pane": self.pane, "rawMode": self.raw_flags, "peerAcceptedAtOrigin": self.peer_origin,
                   "markerLineBytes": self.origin_marker_bytes, "acceptedSinceOrigin": accepted_total, "finalLineBytes": self.final_line_bytes,
                   "totalDigest": self.total_digest_at_final, "writeFailed": self.write_failed, "limitExceeded": self.limit_exceeded,
                   "windows": [{"wid": w["wid"], "digest": w["digest"], "start": w["start"], "end": w["end"]} for w in self.windows]}
        tmp = self.dir / f"{self.pane}.peer.json.tmp"
        tmp.write_text(json.dumps(receipt), encoding="utf-8")
        tmp.replace(self.dir / f"{self.pane}.peer.json")

    @property
    def total_digest_at_final(self) -> str:
        return self._final_digest

    # ---------------------------------------------------------------- input (raw mode has no line discipline)
    def lines(self):
        """Yield input lines: the driver sends `<text>\\r`; raw mode delivers the bytes unmodified, so split on CR/LF here."""
        pending = b""
        while True:
            chunk = os.read(0, 65536)
            if not chunk:
                break
            pending += chunk
            while True:
                cut = min((i for i in (pending.find(b"\r"), pending.find(b"\n")) if i >= 0), default=-1)
                if cut < 0:
                    break
                text, pending = pending[:cut], pending[cut + 1:]
                if text:
                    yield text.decode("utf-8", "replace")

    FINAL_REQUEST = "g1e-final"   # client -> peer, after the last byte-writing phase (A2); an input of the harness, not a registry token: it never reaches the stream

    def handles(self, line: str) -> bool:
        return line == self.FINAL_REQUEST or self._io_text(line) or self._burst_start(line) is not None or self._mid(line) is not None

    @staticmethod
    def _io_text(line: str) -> bool:
        head, sep, _tail = line.partition(":")
        return bool(sep) and head.isdigit()

    @staticmethod
    def _burst_start(line: str) -> int | None:
        return int(line[7:]) if line.startswith("burst r") and line[7:].isdigit() else None

    @staticmethod
    def _mid(line: str) -> int | None:
        return int(line[len("during-burst-r"):]) if line.startswith("during-burst-r") and line[len("during-burst-r"):].isdigit() else None

    def handle(self, line: str) -> None:
        if line == self.FINAL_REQUEST:
            self.final()
            return
        kk = self._burst_start(line)
        if kk is not None:
            threading.Thread(target=self.burst_round, args=(kk,), daemon=False).start()
            return
        mid = self._mid(line)
        if mid is not None:
            self.mid_burst_ack(mid)
            return
        self.io_round(line)

    _final_digest = ""

    def emit_ready(self, payload: str) -> None:
        """The READY line precedes the start marker: a pre-origin byte, never counted."""
        with self.lock:
            self._write_all(payload.encode("utf-8") + b"\n", counted=False)


def peer_index_of(pane: str, workspace: str) -> int:
    """The workspace directory is `workspace <index> café 世界`; the peer index is that index (the driver's own numbering)."""
    return int(Path(workspace).name.split(" ")[1])


def factory(root: str, pane: str, workload: dict, workspace: str) -> LedgerPeer:
    return LedgerPeer(Path(root), pane, workload, peer_index_of(pane, workspace))


def main() -> None:
    import baseline_peer
    baseline_peer.LEDGER_FACTORY = factory
    baseline_peer.main()


if __name__ == "__main__":
    main()
