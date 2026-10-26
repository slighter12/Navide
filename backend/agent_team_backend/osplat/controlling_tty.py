"""A process's controlling-terminal device, read without a cache.

psutil caches its tty map at first use and misses ttys created later, so the
runtime receipts read the device straight from the kernel's own record.
"""
from __future__ import annotations

import os
import sys


def from_proc(pid: int) -> int | None:
    """Linux: ``tty_nr`` from ``/proc/<pid>/stat``, or None when there is none."""
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


def from_libproc(pid: int) -> int | None:
    """macOS: ``e_tdev`` from ``proc_pidinfo(PROC_PIDTBSDINFO)``, or None when there is none."""
    import ctypes
    import ctypes.util

    library = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
    size = 136  # sizeof(struct proc_bsdinfo)
    buffer = ctypes.create_string_buffer(size)
    if library.proc_pidinfo(pid, 3, 0, buffer, size) != size:
        return None
    device = int.from_bytes(buffer.raw[108:112], sys.byteorder)
    return None if device in (0, 0xFFFFFFFF) else device


def unsupported(pid: int) -> int | None:
    """Windows has no controlling terminal."""
    return None
