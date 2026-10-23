"""Windows counterpart of native_shell_peer.py: an ordinary console program under a ConPTY.

It answers the same line protocol as the POSIX peer for the principal-seam cases (ready line, ACK echo, split
output, burst, size, interrupt, spawn-tree-hup, exit) and keeps the same output shapes, so the TypeScript
assertions read both. Windows equivalents of POSIX facts:

* controlling terminal / foreground: the process holds a real console input handle and is in the console's
  process list (there is no tty name or process group on Windows);
* SIGINT: a Ctrl+C key (0x03) on the console input is turned into CTRL_C_EVENT by the console, which Python
  delivers as SIGINT;
* split UTF-8: the console takes text, not bytes, so the text is written one character per flush.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]

kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]

CTRL_C_EVENT = 0
STD_INPUT_HANDLE = -10
ENABLE_ECHO_INPUT = 0x0004
CREATE_NEW_PROCESS_GROUP = 0x00000200


def console_input_without_echo() -> bool:
    """Turn the console's input echo off (the POSIX peer clears ECHO) and nothing else: Ctrl+C handling is left
    exactly as the launch chain delivered it, so a chain that breaks it fails the interrupt case instead of
    being worked around. Returns whether stdin is a console."""
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(handle, mode.value & ~ENABLE_ECHO_INPUT))


def in_console_process_list() -> bool:
    ids = (wintypes.DWORD * 64)()
    count = kernel32.GetConsoleProcessList(ids, 64)
    return os.getpid() in set(ids[: min(count, 64)])


def main() -> int:
    ctty = console_input_without_echo()
    lock = threading.Lock()
    interrupted = threading.Event()

    def emit(text: str) -> None:
        with lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def dimensions() -> list[int]:
        size = os.get_terminal_size(sys.stdout.fileno())
        return [size.columns, size.lines]

    # A console control handler runs on a thread of its own, so it answers at once and needs neither the main
    # thread nor a signal check: Python's SIGINT handler only runs when the main thread next executes bytecode.
    def on_control(kind: int) -> bool:
        if kind != CTRL_C_EVENT:
            return False
        interrupted.set()  # before the answer: the reader must see it if its console read was aborted
        emit("INTERRUPTED")
        return True

    handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(on_control)
    kernel32.SetConsoleCtrlHandler(handler, True)
    emit("NATIVE_READY " + json.dumps({
        "pid": os.getpid(), "argv": sys.argv[1:], "cwd": os.getcwd(),
        "home": os.environ.get("HOME") or os.environ["USERPROFILE"],
        "providerKeys": sorted(key for key in os.environ if any(word in key.upper() for word in
                                 ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "SECRET_KEY"))),
        "isatty": sys.stdin.isatty(), "ctty": ctty, "foreground": ctty and in_console_process_list(),
        "dimensions": dimensions(),
    }, ensure_ascii=False))
    if ready_file := os.environ.get("NAVIDE_PEER_READY_FILE"):
        Path(ready_file).write_text(str(os.getpid()))  # the handler is installed from here on

    lines: queue.SimpleQueue = queue.SimpleQueue()

    def read_lines() -> None:
        while True:
            try:
                raw = sys.stdin.readline()
            except OSError:
                raw = ""
            if raw == "":
                # A Ctrl+C aborts the console read and surfaces as an empty read: that is not the end of input.
                # The control handler runs concurrently, so give it a bounded moment to say it was the cause.
                if interrupted.wait(2.0):
                    interrupted.clear()
                    continue
                lines.put(None)
                return
            lines.put(raw)

    threading.Thread(target=read_lines, daemon=True).start()
    worker = None

    def burst() -> None:
        for index in range(128):
            emit(f"BURST {index:04d} " + "x" * 2048)
        emit("BURST_DONE")

    while True:
        raw = lines.get()
        if raw is None:
            return 0
        line = raw.rstrip("\r\n")
        if line == "burst":
            worker = threading.Thread(target=burst)
            worker.start()
        elif line == "size":
            emit("SIZE " + json.dumps(dimensions()))
        elif line == "split":
            with lock:
                for char in "SPLIT café 世界\n":
                    sys.stdout.write(char)
                    sys.stdout.flush()
        elif line == "spawn-tree-hup":
            # One child in the root's process group and one in a group of its own, both outliving a plain signal.
            code = "import time; time.sleep(120)"
            children = [subprocess.Popen([sys.executable, "-c", code], creationflags=flags)
                        for flags in (0, CREATE_NEW_PROCESS_GROUP)]
            emit("TREE " + json.dumps({"pids": [child.pid for child in children]}))
        elif line == "exit":
            if worker:
                worker.join(timeout=10)
            emit("FINAL_TAIL café 世界")
            return 7
        else:
            emit("ACK " + json.dumps({"pid": os.getpid(), "text": line}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
