"""The host class record of a GitHub-hosted runner (design section 1 row 1): the same five fields as `hostclass.observe_host` (model, ncpu, memoryGiB, arch, os) for Linux and Windows.

`hostclass.observe_host` refuses every system but macOS and stays untouched. `runner_record` is the sibling `hostedRunner` record (image, runner label, CPU model, counter kinds), which the five-field F0
host class cannot carry. Files and commands are read through injected callables so the tests need no Linux or Windows machine; nothing here is measured during an attempt.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import hostclass

Runner = Callable[[list[str]], str]
FIELDS = ("model", "ncpu", "memoryGiB", "arch", "os")
POWERSHELL = "powershell"
CPU_QUERY = "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"
MEMORY_QUERY = "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"
WINDOWS_OS_QUERY = "(Get-CimInstance Win32_OperatingSystem | Select-Object -First 1 -ExpandProperty Caption) + ' ' + [System.Environment]::OSVersion.Version.ToString()"
RUNNER_ENV = ("ImageOS", "ImageVersion", "RUNNER_NAME", "RUNNER_OS", "RUNNER_ARCH")      # names only; no value here is a secret


def default_runner(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{argv[0]} exited {done.returncode}")
    return done.stdout


def default_read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _linux_model(cpuinfo: str) -> str:
    for line in cpuinfo.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "model name" and value.strip():
            return value.strip()
    return ""


def _linux_memory_bytes(meminfo: str) -> int:
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else 0


def _linux_os(os_release: str, kernel: str) -> str:
    pretty = ""
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
    return f"{pretty} (kernel {kernel})" if pretty and kernel else ""


def observe_host_hosted(*, system: Callable[[], str] = platform.system, machine: Callable[[], str] = platform.machine, release: Callable[[], str] = platform.release,
                        cpu_count: Callable[[], int | None] = os.cpu_count, runner: Runner = default_runner, read: Callable[[str], str] = default_read) -> dict:
    """{model, ncpu, memoryGiB, arch, os} of a Linux or Windows runner. A field that cannot be read raises: a host record with a hole is not a host record."""
    name = system()
    if name == "Linux":
        model = _linux_model(read("/proc/cpuinfo"))
        memory_bytes = _linux_memory_bytes(read("/proc/meminfo"))
        os_name = _linux_os(read("/etc/os-release"), release())
    elif name == "Windows":
        model = runner([POWERSHELL, "-NoProfile", "-Command", CPU_QUERY]).strip()
        memory_bytes = int(runner([POWERSHELL, "-NoProfile", "-Command", MEMORY_QUERY]).strip())
        os_name = runner([POWERSHELL, "-NoProfile", "-Command", WINDOWS_OS_QUERY]).strip()
    elif name == "Darwin":      # rf4c D13: the macOS observation is native (`sysctl`, `sw_vers`); delegate instead of writing a second one
        return hostclass.observe_host(runner if runner is not default_runner else hostclass.default_runner, cpu_count=cpu_count, machine=machine, system=system)
    else:
        raise RuntimeError(f"hosted host class is recorded on Linux, Windows and Darwin only: this system is {name!r}")
    ncpu = cpu_count()
    if not model or not os_name or not ncpu or memory_bytes <= 0:
        raise RuntimeError("the host class has an empty field")
    return {"model": model, "ncpu": int(ncpu), "memoryGiB": round(memory_bytes / 2**30, 3), "arch": machine(), "os": os_name}


ARCH_NAMES = {"x86_64": "x64", "amd64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}
OS_FAMILIES = (("macos", "macos"), ("darwin", "macos"), ("windows", "windows"), ("ubuntu", "linux"), ("linux", "linux"))


def platform_entry(f0: dict, *, measured: bool) -> dict:
    """F1 (rf4b): the ONE `platforms` entry of a hosted outcome, from F0's own `hostClass` and `hostedRunner` instead of the static macOS-local table (`g3.bundle.PLATFORMS`), which named the local platform
    set whatever ran. `platform` = `<os>-<arch>` (`linux-x64`, `windows-x64`, `macos-arm64`), `runner` = hosted, `decisionBearing` per the r2-hosted addendum E (a hosted adverse is decision-bearing for its
    platform), `measured` = whether the run reached a decision (False for an incomplete outcome). Raises when F0 does not say what ran: an outcome never guesses its platform."""
    host, runner = f0.get("hostClass") or {}, f0.get("hostedRunner")
    if not isinstance(runner, dict) or not isinstance(host, dict):
        raise RuntimeError("F0 has no hostClass and hostedRunner: the outcome cannot name its platform")
    family_text = str((runner.get("system") or (runner.get("image") or {}).get("RUNNER_OS") or host.get("os") or "")).lower()
    family = next((name for key, name in OS_FAMILIES if key in family_text), None)
    arch = ARCH_NAMES.get(str(host.get("arch") or "").lower())
    if family is None or arch is None:
        raise RuntimeError(f"F0 names no known platform (os {family_text!r}, arch {host.get('arch')!r})")
    return {"platform": f"{family}-{arch}", "runner": "hosted", "decisionBearing": True, "measured": measured, "comparableAcrossRunners": False}


def runner_record(host: dict, env: dict | None = None, *, system: Callable[[], str] = platform.system) -> dict:
    """The `hostedRunner` sibling record: image and runner identity from the job environment, the CPU model (hosted VM CPU models vary between runs), and the kind of the CPU and memory counters
    (the sampler reads a different counter on each OS, so numbers are never compared across platforms)."""
    env = os.environ if env is None else env
    counters = {"Linux": "rss from /proc/<pid>/statm", "Windows": "rss is the working set", "Darwin": "rss is ru_maxrss in bytes (the frozen macOS sampler)"}.get(system(), "unknown")
    return {"system": system(), "cpuModel": host["model"], "image": {name: env.get(name, "") for name in RUNNER_ENV}, "counterKind": counters,
            "stealTime": "linux: /proc/stat steal delta recorded by the quiet gate; windows and macos: no steal counter exists"}
