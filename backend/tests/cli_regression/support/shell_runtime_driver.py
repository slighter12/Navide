"""Isolated owner for the production TypeScript shell-runtime wire tests."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import shlex
import shutil

import psutil

from .cli_shim import base_python_executable

from .backend_process import BackendProcess


async def main(root: Path, mode: str, fault: str = ""):
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = mode
    if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    if fault == "fences":
        backend.env["NAVIDE_CONTROL_FENCE_GATE"] = "1"
    if fault in {"startup", "protocol"}:
        # Fault hooks exist only in the isolated harness build (cargo feature `fault-hooks`),
        # never in the product binary: this scenario runs the harness build explicitly.
        backend.env["NAVIDE_ACR_FAULT"] = fault
        harness = Path(__file__).resolve().parents[4] / "native/navide-cli-runtime/target-harness/release/navide-cli-runtime"
        target = root / "harness-runtime"
        shutil.copyfile(harness, target)
        target.chmod(0o755)
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(target)
    elif fault == "missing":
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(root / "missing-runtime")
    elif fault == "exec":
        source = Path(__file__).resolve().parents[4] / "native/navide-cli-runtime/target/release/navide-cli-runtime"
        target = root / "nonexecutable-runtime"
        shutil.copyfile(source, target)
        target.chmod(0o600)
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(target)
    peer = root / "native_shell_peer.py"
    shutil.copyfile(Path(__file__).with_name("native_shell_peer.py"), peer)
    arguments = ["café 世界 quote' \" \\ ; | &", "second argument"]
    command = [base_python_executable(), "-u", str(peer), *arguments]
    stopped = asyncio.Event()
    async with backend:
        identity = psutil.Process(backend.process.pid)
        owned = {identity.pid: identity}

        async def monitor():
            with (root / "process-identities.jsonl").open("w") as stream:
                while not stopped.is_set():
                    for process in identity.children(recursive=True):
                        process.create_time()
                        owned.setdefault(process.pid, process)
                        backend.track_child(process.pid)
                    records = []
                    for process in owned.values():
                        try:
                            if process.is_running():
                                records.append({"pid": process.pid, "created": process.create_time(),
                                                "parent": process.ppid(), "executable": process.exe()})
                        except psutil.NoSuchProcess:
                            pass
                    stream.write(json.dumps(records) + "\n")
                    stream.flush()
                    await asyncio.sleep(0.02)

        watcher = asyncio.create_task(monitor())
        pending = root / "wire.pending.json"
        pending.write_text(json.dumps({"url": backend.url, "command": command, "arguments": arguments,
                                       "wrapped": ["/bin/bash", "-ilc", shlex.join(command)],
                                       "backend": {"pid": identity.pid, "created": identity.create_time()}}))
        pending.replace(root / "wire.json")
        try:
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            stopped.set()
            await watcher
    if not fault:
        # The per-trial proof (E2 section 3 items 1-3) on THIS principal trial's own receipts, after the backend and the
        # sidecar are gone so every receipt is flushed. The driver's exit code carries the verdict: the TypeScript test
        # asserts `code === 0`, so a principal trial whose receipts do not prove the real sidecar and the handoff fails.
        # (Item 5, the control-plane ledger, is recorded by the E4 matrix harness; there is no ledger receipt here.)
        from .trial_proof import load, prove
        proof = prove(load(root))
        (root / "trial-proof.json").write_text(json.dumps(proof, indent=1))
        if not proof["ok"] or not proof["items"]["2"]["ck"]:
            raise SystemExit("trial proof failed: " + "; ".join(proof["failures"] or ["no CK receipt: the handoff was not proven"]))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
