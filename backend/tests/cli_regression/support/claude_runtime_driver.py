"""Disposable backend owner for the production-TS-client Claude reference cases."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shlex
import sys

import psutil

from .backend_process import BackendProcess
from .cli_shim import base_python_executable, install_cli_shim


async def main(root: Path, fault: str):
    workspace = root / "workspace reference café 世界"
    workspace.mkdir(exist_ok=True)
    sid = "22222222-2222-4222-8222-222222222222"
    for stage in (1, 2):
        backend = BackendProcess(root, artifact_prefix=f"stage-{stage}-")
        backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
        if fault == "missing":
            backend.env["NAVIDE_CLI_RUNTIME_BIN"] = str(root / "missing-runtime")
        script = Path(__file__).with_name("claude_runtime_peer.py")
        executable = install_cli_shim(root, "claude", [base_python_executable(), "-u", str(script), str(root)])
        (root / "home/.bash_profile").write_text(
            f"export PATH={shlex.quote(str(executable.parent))}:$PATH\nexport REFERENCE_SHELL_RC=login\n")
        (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
        stopped = asyncio.Event()
        async with backend:
            process = psutil.Process(backend.process.pid)
            owned = {process.pid: process}

            async def monitor():
                with (root / f"stage-{stage}-process-identities.jsonl").open("w") as stream:
                    while not stopped.is_set():
                        for child in process.children(recursive=True):
                            child.create_time()
                            owned.setdefault(child.pid, child)
                            backend.track_child(child.pid)
                        records = []
                        for child in owned.values():
                            try:
                                if child.is_running():
                                    records.append({"pid": child.pid, "created": child.create_time(),
                                                    "parent": child.ppid(), "executable": child.exe()})
                            except psutil.NoSuchProcess:
                                pass
                        stream.write(json.dumps(records) + "\n")
                        stream.flush()
                        await asyncio.sleep(0.02)

            watcher = asyncio.create_task(monitor())
            pending = root / f"endpoint-{stage}.pending.json"
            pending.write_text(json.dumps({"url": backend.url, "shell": "/bin/bash", "workspace": str(workspace),
                "home": str(root / "home"), "sessionId": sid,
                "backend": {"pid": process.pid, "created": process.create_time()}}))
            pending.replace(root / f"endpoint-{stage}.json")
            try:
                command = (await asyncio.to_thread(sys.stdin.readline)).strip()
            finally:
                stopped.set()
                await watcher
        # Inspect only this stopped backend's durable application store. This
        # is persistence evidence, never a replacement runtime/reader endpoint.
        if not fault:
            from agent_team_backend.db import Database
            from agent_team_backend.log_readers.base import encode_claude_cwd
            from agent_team_backend.tokens_store import TokensStore
            path = root / "home/.claude/projects" / encode_claude_cwd(str(workspace)) / f"{sid}.jsonl"
            db = Database(root / "data/navide.db")
            try:
                store = TokensStore(db=db)
                (root / f"stage-{stage}-durable.json").write_text(json.dumps({
                    "path": str(path), "usage": store.get_ingestion_checkpoint(str(path), None),
                    "activity": store.get_ingestion_checkpoint(str(path), "@activity"),
                    "totals": store.snapshot(str(workspace))["global"]["all_time"],
                    "file_size": path.stat().st_size if path.exists() else None,
                }, indent=2))
            finally:
                db.close()
        (root / f"stage-{stage}-closed").write_text("closed")
        if fault or command != "restart":
            break


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else ""))
