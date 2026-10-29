"""Controlled external peer for the frozen workload; never contacts a provider."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from urllib.parse import parse_qs, urlparse


def claude_injection(extra: list[str], pane: str, root: Path) -> dict:
    """Validate the owned launch flags the backend appends for Claude and report which were present.

    Order is MCP config, optional managed-skills view directory, optional
    convenience settings file; every path must stay inside the isolated root.
    """
    assert len(extra) >= 2 and extra[0] == "--mcp-config"
    # Upstream (after the 06 baseline) passes the MCP config as a FILE path inside the isolated root instead of inline JSON; both shapes are accepted (E4 harness adaptation).
    argument = extra[1]
    if argument.lstrip().startswith("{"):
        config = json.loads(argument)
    else:
        assert Path(argument).is_relative_to(root), "MCP config file outside the isolated root"
        config = json.loads(Path(argument).read_text(encoding="utf-8"))
    endpoint = urlparse(config["mcpServers"]["navide"]["url"])
    query = parse_qs(endpoint.query)
    assert endpoint.hostname == "127.0.0.1" and query["pane"] == [pane] and query.get("t")
    rest = extra[2:]
    skills = bool(rest[:2]) and rest[0] == "--add-dir"
    if skills:
        assert Path(rest[1]).is_relative_to(root)
        rest = rest[2:]
    settings = bool(rest[:2]) and rest[0] == "--settings"
    if settings:
        assert Path(rest[1]).is_relative_to(root)
        rest = rest[2:]
    assert not rest, "unexpected injected Claude flags"
    return {"mcp": True, "skills": bool(skills), "settings": settings}


# G1-E (ticket E4): `g1e_peer.py` installs a factory here; without it this module behaves exactly as in method 8.
LEDGER_FACTORY = None


# All inputs are synthetic. This process deliberately imports no application
# modules: readers, persistence and lifecycle remain in the actual backend.
def main():
    if sys.argv[1:] == ["--version"]:
        print("0.0.0-synthetic-baseline", flush=True)
        return
    root, vendor, pane, workspace, quoted = sys.argv[1:6]
    root = Path(root)
    workload = json.loads(Path(sys.argv[6]).read_text(encoding="utf-8"))
    ledger = LEDGER_FACTORY(root, pane, workload, workspace) if LEDGER_FACTORY else None
    # A controlled peer owns its input mode like an interactive CLI. Kernel
    # echo must not splice test input into the independently emitted payload.
    if ledger is not None:
        ledger.make_raw()  # E2 v10 C9: raw mode (no OPOST/ONLCR/ECHO/ICANON/ISIG/ICRNL) before the first byte, flags re-read and reported
    elif os.name == "nt":
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
        injected = claude_injection(extra, pane, root)
    elif vendor == "opencode":
        assert len(extra) == 4 and extra[0] == "--port" and extra[2:] == ["--hostname", "127.0.0.1"]
        assert 0 < int(extra[1]) < 65536
        config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
        # The external peer proves retained injection without printing auth URLs.
        assert config.get("mcp")
        injected = {"mcp": True, "push": True}
    else:
        assert not extra
    ready = "BASELINE_READY " + json.dumps({
        "pid": os.getpid(), "argv": sys.argv[1:7], "cwd": os.getcwd(),
        "home": str(home), "providerKeys": provider_keys, "injected": injected, "echoDisabled": True,
    }, ensure_ascii=False, separators=(",", ":"))
    if ledger is not None:
        ledger.emit_ready(ready)
        ledger.handshake()  # start marker, then silent until the driver releases the peer
    else:
        print(ready, flush=True)
    lock = threading.Lock()
    worker = None
    connection = None

    def emit(text):
        if ledger is not None:
            ledger.emit(text.encode("utf-8") + b"\n")
            return
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
        for raw in (ledger.lines() if ledger is not None else sys.stdin):
            line = raw.rstrip("\r\n")
            if ledger is not None and ledger.handles(line):
                ledger.handle(line)
            elif line == "burst":
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
