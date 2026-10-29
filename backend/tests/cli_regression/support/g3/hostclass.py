"""The host class record of F0 (U8): model, ncpu, memory, architecture and OS build. Read once, before F0; never measured during an attempt.

macOS only (`sysctl`, `sw_vers`); any other system returns an explicit `unsupported` marker instead of a guess (H8 port audit lists this). `runner` is `argv -> stdout text`, injected by the tests.
"""
from __future__ import annotations

import os
import platform
import subprocess
from typing import Callable

Runner = Callable[[list[str]], str]
FIELDS = ("model", "ncpu", "memoryGiB", "arch", "os")


def default_runner(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{argv[0]} exited {done.returncode}")
    return done.stdout


def observe_host(runner: Runner = default_runner, *, cpu_count: Callable[[], int | None] = os.cpu_count, machine: Callable[[], str] = platform.machine,
                 system: Callable[[], str] = platform.system) -> dict:
    """{model, ncpu, memoryGiB, arch, os}. A field that cannot be read raises: a host record with a hole is not a host record."""
    if system() != "Darwin":
        raise RuntimeError(f"host class is recorded on macOS only (H8): this system is {system()!r}")
    model = runner(["sysctl", "-n", "hw.model"]).strip()
    memory_bytes = int(runner(["sysctl", "-n", "hw.memsize"]).strip())
    version = runner(["sw_vers", "-productVersion"]).strip()
    build = runner(["sw_vers", "-buildVersion"]).strip()
    ncpu = cpu_count()
    if not model or not version or not build or not ncpu or memory_bytes <= 0:
        raise RuntimeError("the host class has an empty field")
    return {"model": model, "ncpu": int(ncpu), "memoryGiB": round(memory_bytes / 2**30, 3), "arch": machine(), "os": f"macOS {version} ({build})"}
