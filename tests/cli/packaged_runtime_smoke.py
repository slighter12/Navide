"""G2 ticket 08: drive a packaged layout through the principal seam.

authenticated WebSocket -> frozen (PyInstaller) backend -> the runtime staged beside it -> PTY.

The layout is `<root>/bin/agent_team_backend[.exe]` plus whatever package.json stages beside it
(`tests/cli/packagedLayout.mjs` builds it from package.json). The backend is the frozen executable, never the
source tree, and its environment carries no Cargo, no checkout path and no `NAVIDE_CLI_RUNTIME_BIN`, so nothing can
rescue a broken lookup. Scenarios:

  positive    shell pane + agent pane (shim, nested arguments), PTY I/O, resize, exit, cleanup, runtime identity
  missing     the staged runtime is removed
  wrong-arch  the staged runtime is replaced by an executable header for a machine this host cannot run
  protocol    the staged runtime is replaced by a test-only stub that presents another wire version

Run: uv --project backend run python -I tests/cli/packaged_runtime_smoke.py --layout DIR --scenario NAME --out DIR
Stdlib + websockets + psutil only. Exit 0 only when the scenario's expectation held.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil
import websockets

WINDOWS = os.name == "nt"
RUNTIME = "navide-cli-runtime.exe" if WINDOWS else "navide-cli-runtime"
BACKEND = "agent_team_backend.exe" if WINDOWS else "agent_team_backend"
ARGUMENTS = ["café 世界 quote' \" \\ ; | &", "second argument"]
FAIL_BOUND_S = 40.0

PEER = r'''
import json, os, sys

if sys.argv[1:] == ["--version"]:  # the backend's startup probe
    print("aider 0.0.0-fixture")
    sys.exit(0)

def size():
    s = os.get_terminal_size(sys.__stdout__.fileno())
    return [s.columns, s.lines]

print("AGENT_READY " + json.dumps({"pid": os.getpid(), "argv": sys.argv[1:], "cwd": os.getcwd(), "size": size(),
                                  "isatty": sys.stdin.isatty()}, ensure_ascii=False), flush=True)
for raw in sys.stdin:
    line = raw.strip()
    if line == "size":
        print("AGENT_SIZE " + json.dumps(size()), flush=True)
    elif line == "exit":
        print("AGENT_FINAL café 世界", flush=True)
        sys.exit(7)
    else:
        print("AGENT_ACK " + json.dumps(line, ensure_ascii=False), flush=True)
'''


def foreign_machine_executable(path: Path) -> None:
    """An executable header for a machine type this host's kernel cannot run (not a real build)."""
    if sys.platform == "darwin":
        # Mach-O 64-bit magic, cputype POWERPC64 (0x01000012): `exec` fails with "Bad CPU type in executable".
        header = bytes.fromhex("cffaedfe" "12000001" "00000000" "02000000") + bytes(512)
    elif sys.platform.startswith("linux"):
        # ELF64 little endian, ET_EXEC, e_machine 0x0002 (SPARC): ENOEXEC, no binfmt handler on a stock runner.
        header = (b"\x7fELF\x02\x01\x01" + bytes(9) + (2).to_bytes(2, "little") + (2).to_bytes(2, "little")
                  + (1).to_bytes(4, "little") + bytes(40) + (64).to_bytes(2, "little") + bytes(2)
                  + (56).to_bytes(2, "little") + bytes(10)) + bytes(512)
    else:
        # PE with Machine 0x0200 (IA64): CreateProcess fails with ERROR_BAD_EXE_FORMAT.
        pe = 0x80
        dos = b"MZ" + bytes(0x3A) + pe.to_bytes(4, "little") + bytes(pe - 0x40)
        coff = (0x0200).to_bytes(2, "little") + (0).to_bytes(2, "little") + bytes(12) + (0xF0).to_bytes(2, "little") + (0x22).to_bytes(2, "little")
        header = dos + b"PE\0\0" + coff + bytes(0xF0) + bytes(512)
    path.write_bytes(header)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Backend:
    def __init__(self, resources: Path, root: Path, extra_env: dict[str, str]):
        self.resources, self.root = resources, root
        self.data = root / "data"
        home = root / "home"
        for directory in (self.data, home, root / "tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        # Only OS inputs: no PATH entry from the checkout, no Cargo, no NAVIDE_CLI_RUNTIME_BIN.
        system = [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"), os.environ.get("SystemRoot", r"C:\Windows")] if WINDOWS \
            else ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        self.shims = root / "shims"
        self.shims.mkdir(exist_ok=True)
        self.env = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL"}}
        self.env.update(PATH=os.pathsep.join([str(self.shims), *system]), HOME=str(home), USERPROFILE=str(home),
                        TEMP=str(root / "tmp"), TMP=str(root / "tmp"), TMPDIR=str(root / "tmp"),
                        APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"),
                        AGENT_TEAM_DATA_DIR=str(self.data), NAVIDE_REGRESSION_ROOT=str(root),
                        NAVIDE_ACR_EVAL_RUNTIME="rust-shell", PYTHONUTF8="1", **extra_env)
        self.process: subprocess.Popen | None = None
        self.url = ""

    def start(self) -> None:
        log = (self.root / "backend.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(self.resources / "bin" / BACKEND), "--port", "0", "--log-level", "info"],
                                        env=self.env, cwd=self.root, stdin=subprocess.PIPE, stdout=log,
                                        stderr=subprocess.STDOUT, text=True)
        self.process.stdin.write("\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit(f"backend exited early ({self.process.returncode}); see {self.root / 'backend.log'}")
            try:
                port = (self.data / "backend-port").read_text().strip()
                if port.isdecimal():
                    socket.create_connection(("127.0.0.1", int(port)), timeout=1).close()
                    token = (self.data / "backend-ws-token").read_text().strip()
                    if token:
                        self.url = f"ws://127.0.0.1:{port}/ws?t={token}"
                        return
            except OSError:
                pass
            time.sleep(0.1)
        raise SystemExit("backend never became ready")

    def stop(self) -> int | None:
        if self.process is None or self.process.poll() is not None:
            return None if self.process is None else self.process.returncode
        try:
            self.process.stdin.write("shutdown\n")
            self.process.stdin.flush()
            return self.process.wait(timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            self.process.kill()
            return self.process.wait(timeout=10)


class Wire:
    def __init__(self, ws):
        self.ws, self.output, self.exits, self.waiting = ws, {}, {}, {}

    async def pump(self) -> None:
        async for frame in self.ws:
            if isinstance(frame, bytes):
                if frame[0] != 1:
                    continue
                sid_len = frame[5]
                sid = frame[6:6 + sid_len].decode()
                pane_len = frame[6 + sid_len]
                self.output[sid] = self.output.get(sid, "") + frame[7 + sid_len + pane_len:].decode("utf-8", "replace")
                continue
            message = json.loads(frame)
            if message.get("type") == "terminal.exit":
                payload = message.get("payload") or {}
                self.exits[payload.get("terminal_session_id")] = payload
            elif message.get("id") in self.waiting:
                self.waiting.pop(message["id"]).set_result(message)

    async def request(self, kind: str, payload: dict, timeout: float = 30.0) -> dict:
        request_id = str(uuid.uuid4())
        self.waiting[request_id] = asyncio.get_running_loop().create_future()
        await self.ws.send(json.dumps({"id": request_id, "type": kind, "payload": payload}))
        return await asyncio.wait_for(self.waiting[request_id], timeout)

    async def until(self, predicate, what: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out: {what}")
            await asyncio.sleep(0.05)


def shell_commands(marker: str) -> dict:
    if WINDOWS:
        return {"command": [os.environ.get("ComSpec", "cmd.exe")], "echo": f"set /a NAVIDE_X=40+2\r\necho MARK_%NAVIDE_X%_{marker}\r\n",
                "expect": f"MARK_42_{marker}", "size": "mode con\r\n", "size_re": r"Columns:\s+120", "exit": "exit 7\r\n"}
    return {"command": ["/bin/sh"], "echo": f"echo MARK_$((40+2))_{marker}\r", "expect": f"MARK_42_{marker}",
            "size": "stty size\r", "size_re": r"\b40 120\b", "exit": "exit 7\r"}


def install_agent_fixture(shims: Path, fixtures: Path) -> Path:
    peer = fixtures / "agent_peer.py"
    peer.write_text(PEER, encoding="utf-8")
    python = getattr(sys, "_base_executable", None) or sys.executable
    if WINDOWS:
        shim = shims / "aider.cmd"
        shim.write_text(f'@echo off\r\n"{python}" -u "{peer}" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="utf-8")
    else:
        shim = shims / "aider"
        shim.write_text(f'#!/bin/sh\nexec "{python}" -u "{peer}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return shim


async def positive(backend: Backend, runtime: Path, result: dict) -> None:
    fixtures = backend.root / "fixtures"
    fixtures.mkdir(exist_ok=True)
    install_agent_fixture(backend.shims, fixtures)
    workspace = backend.root / "workspace café"
    workspace.mkdir()
    created_pids: list[int] = []
    async with websockets.connect(backend.url, proxy=None, max_size=4 * 1024 * 1024) as ws:
        wire = Wire(ws)
        pump = asyncio.create_task(wire.pump())
        try:
            # 1. Shell pane: interactive system shell, PTY input and output, actual resize, exit status.
            shell = shell_commands(uuid.uuid4().hex[:8])
            created = await wire.request("terminal.create", {
                "pane_id": "packaged-shell", "agent_key": "terminal", "command": shell["command"], "cwd": str(workspace),
                "cols": 100, "rows": 30, "create_generation": "packaged-shell-1"})
            assert created.get("ok"), created
            sid, pid = created["payload"]["terminal_session_id"], created["payload"]["pid"]
            created_pids.append(pid)
            await wire.request("terminal.input", {"terminal_session_id": sid, "data": shell["echo"]})
            await wire.until(lambda: shell["expect"] in wire.output.get(sid, "").replace(shell["echo"], ""), "shell PTY echo")
            assert (await wire.request("terminal.resize", {"terminal_session_id": sid, "cols": 120, "rows": 40})).get("ok")
            await wire.request("terminal.input", {"terminal_session_id": sid, "data": shell["size"]})
            await wire.until(lambda: re.search(shell["size_re"], wire.output.get(sid, "")) is not None, "shell resize observed")
            await wire.request("terminal.input", {"terminal_session_id": sid, "data": shell["exit"]})
            await wire.until(lambda: sid in wire.exits, "shell exit")
            result["shell"] = {"exit_code": wire.exits[sid].get("exit_code"), "pid": pid}
            assert wire.exits[sid].get("exit_code") == 7, wire.exits[sid]

            # 2. Agent pane: a vendor key started through a shim found by name, nested arguments intact.
            command = ["aider", *ARGUMENTS]
            created = await wire.request("terminal.create", {
                "pane_id": "packaged-agent", "agent_key": "aider", "command": command, "cwd": str(workspace),
                "cols": 100, "rows": 30, "create_generation": "packaged-agent-1",
                "metadata": {"workspace_path": str(workspace), "session_marker": "at-pane:packaged-agent"}})
            assert created.get("ok"), created
            asid, apid = created["payload"]["terminal_session_id"], created["payload"]["pid"]
            created_pids.append(apid)
            await wire.until(lambda: "AGENT_READY " in wire.output.get(asid, ""), "agent peer ready")
            ready = json.loads(wire.output[asid].split("AGENT_READY ")[1].split("\n")[0].strip())
            assert ready["argv"] == ARGUMENTS, ready
            assert Path(ready["cwd"]).resolve() == workspace.resolve(), ready
            assert ready["isatty"] is True and ready["size"] == [100, 30], ready
            await wire.request("terminal.input", {"terminal_session_id": asid, "data": "ping café\r"})
            await wire.until(lambda: "AGENT_ACK " in wire.output[asid] and "ping café" in wire.output[asid].split("AGENT_ACK ")[-1], "agent PTY I/O")
            assert (await wire.request("terminal.resize", {"terminal_session_id": asid, "cols": 120, "rows": 40})).get("ok")
            await wire.request("terminal.input", {"terminal_session_id": asid, "data": "size\r"})
            await wire.until(lambda: "AGENT_SIZE [120, 40]" in wire.output[asid], "agent resize observed")
            await wire.request("terminal.input", {"terminal_session_id": asid, "data": "exit\r"})
            await wire.until(lambda: asid in wire.exits, "agent exit")
            await wire.until(lambda: "AGENT_FINAL café 世界" in wire.output[asid], "agent last output before exit")
            assert wire.exits[asid].get("exit_code") == 7, wire.exits[asid]
            result["agent"] = {"argv": ready["argv"], "exit_code": 7, "pid": ready["pid"], "shim": "aider"}

            # 3. Runtime identity: the ready receipt names the staged binary and the live sidecar process.
            receipts = [json.loads(line) for line in (backend.root / "runtime-receipts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            readies = [r for r in receipts if r.get("event") == "ready"]
            assert len(readies) == 1, f"expected one runtime start, got {len(readies)}"
            receipt = readies[0]
            staged = sha256(runtime)
            assert receipt["executableSha256"] == staged, (receipt["executableSha256"], staged)
            assert Path(receipt["executable"]).resolve() == runtime.resolve(), (receipt["executable"], str(runtime))
            result["identity"] = {"executableSha256": receipt["executableSha256"], "stagedSha256": staged,
                                  "executable": "<layout>/bin/" + runtime.name, "runtime_pid": receipt["pid"]}
            runtime_pid = receipt["pid"]
        finally:
            pump.cancel()
    # 4. Cleanup: backend shutdown ends the sidecar and every pane child.
    code = backend.stop()
    time.sleep(1.0)
    survivors = [pid for pid in [runtime_pid, *created_pids, ready["pid"]] if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE]
    result["cleanup"] = {"backend_exit_code": code, "survivors": survivors}
    assert code == 0, f"backend exit code {code}"
    assert not survivors, f"survivors after shutdown: {survivors}"


async def negative(backend: Backend, expected: str, result: dict) -> None:
    workspace = backend.root / "workspace"
    workspace.mkdir()
    started = time.monotonic()
    async with websockets.connect(backend.url, proxy=None, max_size=4 * 1024 * 1024) as ws:
        wire = Wire(ws)
        pump = asyncio.create_task(wire.pump())
        try:
            failures = []
            for attempt in (1, 2):  # the second create proves the failure is not retried into a fallback
                response = await wire.request("terminal.create", {
                    "pane_id": f"packaged-neg-{attempt}", "agent_key": "terminal", "command": ["/bin/sh"] if not WINDOWS else ["cmd.exe"],
                    "cwd": str(workspace), "cols": 100, "rows": 30, "create_generation": f"neg-{attempt}"}, timeout=FAIL_BOUND_S)
                assert response.get("ok") is not True, f"create succeeded: {response}"
                failures.append(json.dumps(response, ensure_ascii=False))
        finally:
            pump.cancel()
    elapsed = time.monotonic() - started
    result["failures"] = [re.sub(r"[A-Za-z]:\\[^\"]*|/[^\" ]*navide[^\"]*", "<path>", text) for text in failures]
    result["elapsed_s"] = round(elapsed, 2)
    assert elapsed < FAIL_BOUND_S, f"failure took {elapsed:.1f}s"
    assert all(re.search(expected, text) for text in failures), (expected, failures)
    children = [child.name() for child in psutil.Process(backend.process.pid).children(recursive=True)]
    result["children_after_failure"] = children
    assert not any("sh" == name or name.startswith("cmd") for name in children), f"a PTY child exists: {children}"
    code = backend.stop()
    result["backend_exit_code"] = code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--layout", type=Path, required=True, help="resources dir with bin/ (from packagedLayout.mjs)")
    parser.add_argument("--scenario", choices=["positive", "missing", "wrong-arch", "protocol"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    root = args.out.resolve() / args.scenario
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    resources = root / "resources"
    shutil.copytree(args.layout, resources, symlinks=True)
    runtime = resources / "bin" / RUNTIME
    extra: dict[str, str] = {}
    expected = ""
    if args.scenario == "missing":
        runtime.unlink()
        expected = "Rust runtime binary missing from the packaged app"
    elif args.scenario == "wrong-arch":
        foreign_machine_executable(runtime)
        expected = "START_FAILED at exec"
    elif args.scenario == "protocol":
        # A separate stub, built here with plain rustc; no fault switch exists in the runtime or the lookup.
        stub = Path(__file__).with_name("fixtures") / "protocol_stub.rs"
        subprocess.run(["rustc", "--edition", "2021", "-O", str(stub), "-o", str(runtime)], check=True)
        runtime.chmod(0o755)
        expected = "PROTOCOL_MISMATCH"
    result: dict = {"scenario": args.scenario, "platform": sys.platform, "machine": platform.machine(),
                    "runtime_staged": runtime.is_file(), "expected": expected or "pass"}
    backend = Backend(resources, root, extra)
    outcome = "pass"
    try:
        backend.start()
        if args.scenario == "positive":
            asyncio.run(positive(backend, runtime, result))
        else:
            asyncio.run(negative(backend, expected, result))
    except BaseException as error:  # noqa: BLE001 - the verdict is the report
        outcome = "fail"
        result["error"] = f"{type(error).__name__}: {error}"[:2000]
    finally:
        if backend.process is not None and backend.process.poll() is None:
            backend.stop()
    result["outcome"] = outcome
    (args.out / f"{args.scenario}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"PACKAGED-SMOKE scenario={args.scenario} outcome={outcome} platform={sys.platform} machine={platform.machine()}")
    if outcome != "pass":
        print(result.get("error", ""))
    return 0 if outcome == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
