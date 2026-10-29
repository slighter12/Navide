"""The log final check and the log text identity (E2 v10 section 3 item 4 f and g): measured against real files and real processes; the verdict is the reference checker's.

`holds(pid, identity)` answers one real boolean from the process's own descriptor table (psutil's macOS `proc_pidinfo` open-files list, each entry compared by `(st_dev, st_ino)`
with the log's identity); a process that cannot be inspected raises `InspectionError` (an unknown result invalidates the trial, never a silent False). Only the held flag is
recorded: no path or content of any other file leaves this module.
Documented difference from the frozen text: the table lists vnode PATHS (psutil); identity is re-derived by `stat` of each path, so a descriptor whose path was unlinked cannot be
matched. The log is never unlinked in a measured session (it lives in the attempt's data directory until the attempt is deleted), and a sibling/replacement check covers rotation.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from pathlib import Path

import psutil


class InspectionError(Exception):
    """A required inspection could not be made; the trial is invalid."""


def now_ms() -> float:
    return time.time_ns() / 1_000_000


def identity(path: Path) -> tuple[int, int]:
    try:
        info = os.stat(path)
    except OSError as error:
        raise InspectionError(f"log identity unavailable: {type(error).__name__}") from None
    return (info.st_dev, info.st_ino)


def holds(pid: int, log_identity: tuple[int, int]) -> bool:
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            raise InspectionError("process is a zombie")
        files = process.open_files()
    except psutil.Error as error:
        raise InspectionError(f"inspection of pid {pid} failed: {type(error).__name__}") from None
    for entry in files:
        try:
            info = os.stat(entry.path)
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == tuple(log_identity):
            return True
    return False


def is_live(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error as error:
        raise InspectionError(f"liveness of pid {pid} unknown: {type(error).__name__}") from None


def read_once(path: Path) -> dict:
    started = now_ms()
    try:
        info = os.stat(path)
        data = Path(path).read_bytes()
    except OSError as error:
        raise InspectionError(f"log unreadable: {type(error).__name__}") from None
    return {"t_ms": started, "dev": info.st_dev, "ino": info.st_ino, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest(), "regular": stat.S_ISREG(info.st_mode)}


def log_final(arm: str, path: Path, *, backend_pid: int, sidecar_pid: int | None, t_exit_ms: float, gap_ms: float = 250) -> dict:
    """The arguments of `reference.log_final_check`: inventory first, then two reads at least `gap_ms` apart; every timestamp is taken after `t_exit_ms` by the caller's order."""
    log_id = identity(path)
    t_inventory = now_ms()
    inventory = {"backend": holds(backend_pid, log_id)}
    sidecar_live = None
    if arm == "rust-shell":
        if sidecar_pid is None:
            raise InspectionError("the Rust arm needs the sidecar pid")
        sidecar_live = is_live(sidecar_pid)
        if sidecar_live:
            inventory["sidecar"] = holds(sidecar_pid, log_id)
    first = read_once(path)
    time.sleep(max(0.0, (first["t_ms"] + gap_ms - now_ms()) / 1000.0) + 0.002)   # a harness gap required by the frozen rule (two reads >= 250 ms apart), not a wait for state
    second = read_once(path)
    return {"arm": arm, "sidecar_live": sidecar_live, "inventory": inventory, "reads": [first, second], "t_exit_ms": t_exit_ms, "t_inventory_ms": t_inventory}


def sibling_exists(path: Path) -> bool:
    path = Path(path)
    return any(entry.name != path.name and entry.name.startswith(path.name) for entry in path.parent.iterdir())


_WS = re.compile(r"\s+")
_FINAL = re.compile(r"BASELINE_FINAL:[0-9]{1,10}:[0-9a-f]{64}")


def ws_strip(text: str) -> str:
    return _WS.sub("", text)


def _between(text: str, begin: str, end: str) -> str | None:
    start = text.find(begin)
    if start < 0:
        return None
    start += len(begin)
    stop = text.find(end, start)
    return None if stop < 0 else text[start:stop]


def log_identity(log_text: str, peer_text: str, nonce: str, window_ids: list[str]) -> dict:
    """`peer_text` is what the peer accepted AFTER its start marker line (through the FINAL line), as text. The log is located by the whitespace-free marker `BASELINE_LEDGER_START:<nonce>`."""
    marker = f"BASELINE_LEDGER_START:{nonce}"
    count = log_text.count(marker)
    start = log_text.find(marker)
    pre_origin = len(log_text[:start].encode("utf-8")) if start >= 0 else len(log_text.encode("utf-8"))
    after = log_text[start + len(marker):] if count == 1 else ""
    windows_equal = count == 1
    for wid in window_ids:
        mine = _between(after, f"BASELINE_WIN:{wid}:BEGIN", f"BASELINE_WIN:{wid}:END")
        theirs = _between(peer_text, f"BASELINE_WIN:{wid}:BEGIN", f"BASELINE_WIN:{wid}:END")
        if mine is None or theirs is None or ws_strip(mine) != ws_strip(theirs):
            windows_equal = False
    return {"startMarkerCount": count, "finalPresent": bool(_FINAL.search(after)), "textEqual": count == 1 and ws_strip(after) == ws_strip(peer_text),
            "windowsEqual": windows_equal, "preOriginLogBytes": pre_origin}
