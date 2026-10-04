"""Controlled external peer for the frozen workload; never contacts a provider."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from urllib.parse import parse_qs, urlparse


# All inputs are synthetic. This process deliberately imports no application
# modules: readers, persistence and lifecycle remain in the actual backend.
def main():
    if sys.argv[1:] == ["--version"]:
        print("0.0.0-synthetic-baseline", flush=True)
        return
    root, vendor, pane, workspace, quoted = sys.argv[1:6]
    root = Path(root)
    workload = json.loads(Path(sys.argv[6]).read_text(encoding="utf-8"))
    # A controlled peer owns its input mode like an interactive CLI. Kernel
    # echo must not splice test input into the independently emitted payload.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        handle = kernel.GetStdHandle(-10)
        mode = wintypes.DWORD()
        assert kernel.GetConsoleMode(handle, ctypes.byref(mode))
        assert kernel.SetConsoleMode(handle, mode.value & ~0x0004)
    else:
        import termios
        mode = termios.tcgetattr(sys.stdin)
        mode[3] &= ~termios.ECHO
        termios.tcsetattr(sys.stdin, termios.TCSANOW, mode)
    fixture_root = Path(sys.argv[6]).parent
    home = Path(os.environ["USERPROFILE" if os.name == "nt" else "HOME"])
    assert home.is_relative_to(root), "peer escaped the isolated home"
    assert Path(workspace).is_relative_to(root), "peer escaped the isolated workspace"
    provider_keys = sorted(key for key in os.environ if any(
        word in key.upper() for word in ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "SECRET_KEY")
    ))
    injected = {}
    extra = sys.argv[7:]
    if vendor == "claude":
        assert len(extra) in (2, 4) and extra[0] == "--mcp-config"
        config = json.loads(extra[1])
        endpoint = urlparse(config["mcpServers"]["navide"]["url"])
        query = parse_qs(endpoint.query)
        assert endpoint.hostname == "127.0.0.1" and query["pane"] == [pane] and query.get("t")
        if len(extra) == 4:
            assert extra[2] == "--add-dir" and Path(extra[3]).is_relative_to(root)
        injected = {"mcp": True, "skills": len(extra) == 4}
    elif vendor == "opencode":
        assert len(extra) == 4 and extra[0] == "--port" and extra[2:] == ["--hostname", "127.0.0.1"]
        assert 0 < int(extra[1]) < 65536
        config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
        # The external peer proves retained injection without printing auth URLs.
        assert config.get("mcp")
        injected = {"mcp": True, "push": True}
    else:
        assert not extra
    print("BASELINE_READY " + json.dumps({
        "pid": os.getpid(), "argv": sys.argv[1:7], "cwd": os.getcwd(),
        "home": str(home), "providerKeys": provider_keys, "injected": injected, "echoDisabled": True,
    }, ensure_ascii=False, separators=(",", ":")), flush=True)
    lock = threading.Lock()
    worker = None
    connection = None

    def emit(text):
        with lock:
            print(text, flush=True)

    def burst():
        for index in range(workload["burstLines"]):
            emit(f"BASELINE_BURST {index:04d} " + "x" * workload["burstWidth"])
        emit("BASELINE_BURST_DONE")

    def observe(phases):
        nonlocal connection
        fixture = json.loads((fixture_root / f"{vendor}.json").read_text(encoding="utf-8"))
        session_id = pane if vendor == "claude" else f"ses_{pane}"
        # Claude's real encoder flattens every non-alphanumeric character.
        # This independent external writer follows the recorded store shape.
        encoded_cwd = "".join(char if char.isascii() and char.isalnum() else "-" for char in workspace.rstrip("/\\"))
        values = {
            "${HOME}": str(home), "${WORKSPACE}": workspace,
            "${CLAUDE_CWD}": encoded_cwd, "${MARKER}": f"at-pane:{pane}",
            fixture["sessionId"]: session_id,
        }
        text = json.dumps(fixture, ensure_ascii=False)
        for key, value in values.items():
            # Replace JSON string contents, preserving quotes/backslashes.
            text = text.replace(json.dumps(key)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1])
        fixture = json.loads(text)
        path = Path(fixture["path"])
        if vendor == "opencode":
            path = Path(os.environ["XDG_DATA_HOME"]) / "opencode" / "opencode.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        assert path.is_relative_to(root)
        for phase in phases:
            for operation in fixture["phases"][phase]:
                if operation["kind"] == "jsonl":
                    with path.open("a" if operation.get("append") else "w", encoding="utf-8") as stream:
                        for record in operation["records"]:
                            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                else:
                    if connection is None:
                        connection = sqlite3.connect(path, timeout=10)
                        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                    if operation.get("schema"):
                        connection.executescript(operation["schema"].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "))
                    for statement in operation.get("statements", []):
                        # Distinct rows in the shared native vendor DB.
                        sql = statement["sql"]
                        for identity in ("u1", "a1", "p1", "p2", "p3"):
                            sql = sql.replace(f"'{identity}'", f"'{identity}_{pane}'")
                        params = [json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                                  for value in statement.get("params", [])]
                        connection.execute(sql, params)
                    connection.commit()
        emit("BASELINE_STORE " + json.dumps({"path": str(path), "phase": list(phases)}, ensure_ascii=False))
        emit("BASELINE_OBSERVED_WRITE")

    try:
        for raw in sys.stdin:
            line = raw.rstrip("\r\n")
            if line == "burst":
                worker = threading.Thread(target=burst)
                worker.start()
            elif line == "observe-initial":
                observe(("initial",))
                emit("BASELINE_INITIAL_WRITE")
            elif line == "observe":
                observe(("response", "complete"))
            elif line == "close-store":
                # Diagnostic counterfactual only: prove native WAL close ->
                # watcher -> actual reader/sink -> broadcast, not a baseline fix.
                if connection:
                    connection.close()
                    connection = None
                emit("BASELINE_STORE_CLOSED")
            else:
                emit("BASELINE_ACK " + json.dumps({"pid": os.getpid(), "text": line},
                                                  ensure_ascii=False, separators=(",", ":")))
    finally:
        if worker:
            worker.join(timeout=10)
        if connection:
            connection.close()


if __name__ == "__main__":
    main()
