"""Evaluation-only receipts of the Rust runtime path (E3 S13; E2 v10 section 3 items 2 and 5).

Everything that writes a file is gated by `NAVIDE_REGRESSION_ROOT` (the evaluation receipt directory), like the
runtime's other receipts, and is a no-op otherwise. **No receipt carries a descriptor number** (E1 5.2): the CK
records kernel facts about the descriptor (mode, device identity, slave name), never its integer.
"""
from __future__ import annotations

import os
import stat
import sys
from typing import Any

import psutil

from . import cli_runtime_frames as wire
from .cli_runtime_handoff import slave_name_of


def controlling_terminal_device(pid: int) -> int | None:
    """Read the root's controlling-terminal device directly, or return None when it has none.

    psutil caches its tty map at first use and misses ttys created later.
    """
    if sys.platform == "linux":
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stat_file:
                fields = stat_file.read().rsplit(")", 1)[1].split()
            encoded = int(fields[4])  # tty_nr is field 7; fields[0] is state (field 3).
            if encoded == 0:
                return None
            major = (encoded >> 8) & 0xFFF
            minor = (encoded & 0xFF) | ((encoded >> 12) & 0xFFF00)
            return os.makedev(major, minor)
        except (OSError, IndexError, ValueError):
            return None
    import ctypes
    import ctypes.util
    library = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
    size = 136  # sizeof(struct proc_bsdinfo)
    buffer = ctypes.create_string_buffer(size)
    if library.proc_pidinfo(pid, 3, 0, buffer, size) != size:
        return None
    device = int.from_bytes(buffer.raw[108:112], sys.byteorder)
    return None if device in (0, 0xFFFFFFFF) else device


def enabled() -> bool:
    return bool(os.environ.get("NAVIDE_REGRESSION_ROOT"))


def checkpoint(fd: int, session_id: str, pane_id: str, generation: str, sidecar_tty: str, root: dict) -> dict:
    """The per-create kernel checkpoint (CK), taken inline when the claimed descriptor arrives, before the
    create reply can be released. Reads and writes nothing on the descriptor (`fstat` and one `ioctl` that
    names the slave)."""
    record: dict[str, Any] = {"event": "ck", "session_id": session_id, "pane_id": pane_id, "session_generation": generation,
                              "sidecar_tty": sidecar_tty, "root": {k: root.get(k) for k in ("pid", "pgid", "start_kind", "start_value")}}
    try:
        info = os.fstat(fd)
        record["char_device"] = stat.S_ISCHR(info.st_mode)
        record["is_socket"] = stat.S_ISSOCK(info.st_mode)
        record["master_rdev"] = info.st_rdev
    except OSError as error:
        record.update(char_device=False, is_socket=False, master_rdev=None, fstat_errno=error.errno)
    try:
        record["slave_name"] = slave_name_of(fd)  # a master answers; a slave or a socket does not
        record["is_master"] = True
    except OSError:
        record["slave_name"], record["is_master"] = None, False
    pid = root.get("pid")
    alive, ctty_path, ctty_rdev = False, None, None
    try:
        process = psutil.Process(pid)

        def running() -> bool:
            return process.status() != psutil.STATUS_ZOMBIE and process.is_running()

        before = running()
        if before:
            ctty_rdev = controlling_terminal_device(pid)
            if ctty_rdev is not None:
                ctty_path = f"dev:{ctty_rdev}"
        # A root that exits while it is being observed releases its controlling terminal first: it counts as
        # alive for rule (b) only if it was running both before and after the read (no event to wait on, two reads).
        alive = before and running()
    except (psutil.Error, OSError, TypeError):
        pass
    record.update(root_alive=alive, root_ctty=ctty_path, root_ctty_rdev=ctty_rdev)
    try:
        record["slave_rdev"] = os.stat(record["slave_name"]).st_rdev if record["slave_name"] else None
    except OSError:
        record["slave_rdev"] = None
    # Pass rule (E2 3 item 2): (a) both sides name the same slave, (b) that slave is the root's controlling
    # terminal when the root is alive, (c) the descriptor is a master (a character device, not a socket).
    record["pass_a"] = record["slave_name"] is not None and record["slave_name"] == sidecar_tty
    record["pass_b"] = (not alive) or (ctty_rdev is not None and ctty_rdev == record["slave_rdev"])
    record["pass_c"] = bool(record["char_device"] and record["is_master"] and not record["is_socket"])
    record["pass"] = bool(record["pass_a"] and record["pass_b"] and record["pass_c"])
    return record


class ControlLedger:
    """Frames and bytes per frame type, both directions, in the Python client of the sidecar socket (E2 3 item 5)."""

    def __init__(self) -> None:
        self.tx: dict[int, list[int]] = {}
        self.rx: dict[int, list[int]] = {}
        self.late_parts = self.late_bytes = self.late_answers = 0  # tolerated late traffic of expired requests (E1 v8)

    def late_part(self, size: int) -> None:
        self.late_parts += 1
        self.late_bytes += size

    def late_answer(self) -> None:
        self.late_answers += 1

    @staticmethod
    def _add(table: dict[int, list[int]], key: int, size: int) -> None:
        entry = table.setdefault(key, [0, 0])
        entry[0] += 1
        entry[1] += size

    def sent(self, frame_code: int, size: int) -> None:
        self._add(self.tx, frame_code, size)

    def received(self, frame: wire.Frame) -> None:
        self._add(self.rx, frame.code, frame.wire_size)

    def snapshot(self) -> dict:
        names = {value: name for name, value in vars(wire.code).items() if isinstance(value, int) and not name.startswith("_")}
        known = set(names)

        def table(entries: dict[int, list[int]]) -> dict:
            return {names.get(key, f"unknown-{key}"): {"frames": value[0], "bytes": value[1]} for key, value in sorted(entries.items())}

        return {"tx": table(self.tx), "rx": table(self.rx),
                "unknown_types": sorted(k for k in (*self.tx, *self.rx) if k not in known),
                "tx_bytes": sum(v[1] for v in self.tx.values()), "rx_bytes": sum(v[1] for v in self.rx.values()),
                "late_observation_parts": self.late_parts, "late_observation_bytes": self.late_bytes, "late_answers": self.late_answers}
