"""Host sensors of a hosted runner that the unchanged g1e code reads through module attributes (design section 1 rows 8 and 14).

* `host_snapshot` - the same four keys as `g1e.assemble.host_snapshot` (`at`, `loadavg[3]`, `ncpu`, `processCount`) without `os.getloadavg`, which Windows does not have. The reader runners call
  `host_snapshot` through the `g1e.assemble` module attribute, so `hosted_seams.install` replaces that attribute (a documented, tested seam; no g1e byte changes).
* `holds` - "does this process still hold the log file": the `g1e.log_final.holds` boolean. The macOS version lists a process's open files with psutil and compares `(st_dev, st_ino)`. On Linux an
  unlinked file lists as `<path> (deleted)` and cannot be `stat`ed, so `holds_linux` compares the `(st_dev, st_ino)` of every `/proc/<pid>/fd/N` link target, which `stat` follows even for a deleted
  file. On Windows psutil's `open_files()` may not list every held handle; `holds_windows` uses the macOS check unless a better probe is injected, and the rehearsal must show the same booleans
  as on macOS for a known-held and a known-released file on each OS (a process that cannot be inspected still raises `InspectionError`: an unknown result invalidates the attempt, never a silent False).
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import log_final as lf

InspectionError = lf.InspectionError


def count_processes(system: str, *, run: Callable[[list[str]], str] | None = None, pids: Callable[[], list[int]] | None = None) -> int | None:
    """Linux: the line count of `ps -A -o pid=` (as on macOS: no process name or argument is read). Windows has no `ps`; psutil's pid list is the count."""
    try:
        if system == "Windows":
            if pids is None:
                import psutil
                pids = psutil.pids
            return len(pids())
        run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=10, check=True).stdout)
        return len(run(["ps", "-A", "-o", "pid="]).splitlines())
    except (OSError, subprocess.SubprocessError, ImportError):
        return None


def host_snapshot(*, system: str | None = None, loadavg: Callable[[], tuple] | None = None, wall: Callable[[], float] = time.time, ncpu: Callable[[], int | None] = os.cpu_count,
                  count: Callable[[str], int | None] = count_processes) -> dict:
    """One host sample (numbers only). Windows has no load average: it reports `[0.0, 0.0, 0.0]`, exactly what `os.loadavg()` gives the TS runner there."""
    import platform
    system = system or platform.system()
    if loadavg is None:
        loadavg = (lambda: (0.0, 0.0, 0.0)) if system == "Windows" else os.getloadavg
    return {"at": wall() * 1000.0, "loadavg": [float(x) for x in loadavg()], "ncpu": ncpu(), "processCount": count(system)}


def holds_linux(pid: int, log_identity: tuple[int, int], *, proc: Path = Path("/proc"), stat: Callable[[str], os.stat_result] = os.stat, listdir: Callable[[str], list[str]] = os.listdir) -> bool:
    """True when some descriptor of `pid` is the log file (device and inode), also when the file was unlinked or renamed."""
    fd_dir = str(Path(proc) / str(pid) / "fd")
    try:
        names = listdir(fd_dir)
    except OSError as error:
        raise InspectionError(f"inspection of pid {pid} failed: {type(error).__name__}") from None
    for name in names:
        try:
            info = stat(str(Path(fd_dir) / name))
        except OSError:
            continue                    # a descriptor that closed meanwhile, or a socket/pipe that is not a file
        if (info.st_dev, info.st_ino) == tuple(log_identity):
            return True
    return False


def holds_windows(pid: int, log_identity: tuple[int, int], *, probe: Callable[[int, tuple[int, int]], bool] | None = None) -> bool:
    """The psutil check of macOS unless a better probe is injected. Whether psutil lists the held handle of a known-held file is exactly what the rehearsal must show; until then this is unproven."""
    return (probe or lf.holds)(pid, log_identity)


def holds_for(system: str) -> Callable[..., bool]:
    return {"Linux": holds_linux, "Windows": holds_windows}.get(system, lf.holds)
