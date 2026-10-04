"""Method 7: shell-only G0 quantities with actual combined runtime accounting."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import platform
import shlex
import shutil
import sys
import time
import uuid

import psutil

from .backend_process import BackendProcess
from .cli_shim import base_python_executable
from .runtime_baseline import rescue


async def main(root: Path, count: int, mode: str):
    assert count in (1, 4) and mode in ("python", "rust-shell")
    driver = psutil.Process()
    workload_path = Path(__file__).resolve().parents[4] / "tests/fixtures/cli-regression/runtime-workload.json"
    workload = json.loads(workload_path.read_text())
    identity = {"method": 7, "selectedRuntime": mode, "platform": platform.platform(),
                "machine": platform.machine(), "python": sys.version,
                "driver": {"pid": driver.pid, "created": driver.create_time()},
                "workloadSha256": hashlib.sha256(workload_path.read_bytes()).hexdigest(),
                "intervalMs": workload["resourceIntervalMs"]}
    (root / "identity.json").write_text(json.dumps(identity, indent=2))
    script = root / "baseline_peer.py"
    shutil.copyfile(Path(__file__).with_name("baseline_peer.py"), script)
    owner = BackendProcess(root, artifact_prefix="first-")
    owner.env["NAVIDE_ACR_EVAL_RUNTIME"] = mode
    async with owner:
        process = psutil.Process(owner.process.pid)
        identity["backend"] = {"pid": process.pid, "created": process.create_time(), "generation": "first"}
        (root / "identity.json").write_text(json.dumps(identity, indent=2))
        (root / "first-identity.json").write_text(json.dumps(identity["backend"]))
        owned = {process.pid: process}

        async def sample():
            with (root / "resources.jsonl").open("w") as stream:
                while True:
                    for child in process.children(recursive=True):
                        child.create_time()
                        previous = owned.get(child.pid)
                        if previous is None or not previous.is_running():
                            owned[child.pid] = child
                        owner.track_child(child.pid)
                    counters = []
                    for child in list(owned.values()):
                        try:
                            if not child.is_running():
                                continue
                            cpu = child.cpu_times()
                            command = child.cmdline()
                            category = "controlled_child" if str(script) in command else "runtime"
                            component = "backend" if child.pid == process.pid else (
                                "sidecar" if Path(child.exe()).name == "navide-cli-runtime" else "helper")
                            counters.append({"pid": child.pid, "created": child.create_time(),
                                             "category": category, "component": component,
                                             "cpuSeconds": cpu.user + cpu.system, "rssBytes": child.memory_info().rss,
                                             "name": child.name(), "executable": child.exe()})
                        except psutil.NoSuchProcess:
                            pass
                    stream.write(json.dumps({"wallMs": time.time_ns() / 1_000_000,
                                             "monotonicNs": time.monotonic_ns(), "processes": counters}) + "\n")
                    stream.flush()
                    await asyncio.sleep(workload["resourceIntervalMs"] / 1000)

        def peer(index, wrapped=True):
            pane = str(uuid.uuid5(uuid.NAMESPACE_URL, f"method7:terminal:{index}"))
            workspace = root / f"workspace {index} café 世界"
            workspace.mkdir(exist_ok=True)
            args = [str(root), "terminal", pane, str(workspace), workload["input"], str(workload_path)]
            argv = [base_python_executable(), "-u", str(script), *args]
            return {"pane": pane, "vendor": "terminal", "workspace": str(workspace), "command": argv,
                    "wrapped": wrapped, "commandLine": shlex.join(argv), "argv": args}

        endpoint = {"url": owner.url, "home": owner.env["HOME"], "shell": "/bin/bash",
                    "shells": [peer(-1, False), peer(-2)], "peers": [peer(i) for i in range(count)]}
        pending = root / "endpoint.pending.json"
        pending.write_text(json.dumps(endpoint))
        pending.replace(root / "endpoint.json")
        sampler = asyncio.create_task(sample())
        try:
            async with asyncio.timeout(180):
                await asyncio.to_thread(sys.stdin.readline)
        finally:
            sampler.cancel()
            try:
                await sampler
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    if sys.argv[1] == "--rescue":
        rescue(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
