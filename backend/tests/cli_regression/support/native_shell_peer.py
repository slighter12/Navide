"""Owned ordinary-shell peer: independent tty, byte, argv and lifecycle oracle."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import termios
import threading
import time


def main():
    mode = termios.tcgetattr(0)
    mode[3] &= ~termios.ECHO
    termios.tcsetattr(0, termios.TCSANOW, mode)
    lock = threading.Lock()

    def emit(text):
        with lock:
            data = (text + "\n").encode("utf-8")
            while data:
                written = os.write(sys.stdout.fileno(), data)
                if written <= 0:
                    raise OSError("stdout write made no progress")
                data = data[written:]

    def dimensions():
        size = os.get_terminal_size(0)
        return [size.columns, size.lines]

    resizes = []
    # Signal handlers must not reenter a stdout lock/BufferedWriter mid-ACK.
    signal.signal(signal.SIGWINCH, lambda *_: resizes.append(dimensions()))
    signal.signal(signal.SIGINT, lambda *_: emit("INTERRUPTED"))
    with open("/dev/tty", "rb") as tty:
        ctty = os.isatty(tty.fileno())
    emit("NATIVE_READY " + json.dumps({
        "pid": os.getpid(), "argv": sys.argv[1:], "cwd": os.getcwd(), "home": os.environ["HOME"],
        "providerKeys": sorted(key for key in os.environ if any(word in key.upper() for word in
                                 ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "SECRET_KEY"))),
        "isatty": os.isatty(0), "ctty": ctty, "foreground": os.tcgetpgrp(0) == os.getpgrp(),
        "dimensions": dimensions(),
    }, ensure_ascii=False))
    worker = None

    def burst():
        for index in range(128):
            emit(f"BURST {index:04d} " + "x" * 2048)
        emit("BURST_DONE")

    for raw in sys.stdin:
        line = raw.rstrip("\r\n")
        if line == "burst":
            worker = threading.Thread(target=burst)
            worker.start()
        elif line == "winch-short-write":
            def winch():
                os.kill(os.getpid(), signal.SIGWINCH)
                Path("winch-sent.tmp").write_text(json.dumps({"pid": os.getpid(), "signal": "SIGWINCH"}))
                Path("winch-sent.tmp").replace("winch-sent.json")

            timer = threading.Timer(0.02, winch)
            timer.start()
            emit("WINCH_FRAME " + "x" * 65536)
            timer.join()
            emit("WINCH_DONE")
        elif line == "size":
            if resizes:
                emit("WINCH " + json.dumps(resizes[-1]))
            emit("SIZE " + json.dumps(dimensions()))
        elif line == "split":
            with lock:
                for byte in "SPLIT café 世界\n".encode():
                    os.write(1, bytes([byte]))
        elif line == "block-input":
            raw_mode = termios.tcgetattr(0)
            raw_mode[3] &= ~termios.ICANON
            raw_mode[6][termios.VMIN] = 1
            raw_mode[6][termios.VTIME] = 0
            termios.tcsetattr(0, termios.TCSANOW, raw_mode)
            emit("INPUT_STOPPED")
            time.sleep(1.2)
            received = b""
            while len(received) < 16384:
                received += os.read(0, 16384 - len(received))
            emit("INPUT_DRAINED " + hashlib.sha256(received).hexdigest())
            termios.tcsetattr(0, termios.TCSANOW, mode)
        elif line == "flood-exit":
            with lock:
                for _ in range(64):
                    sys.stdout.buffer.write(b"x" * (256 * 1024))
                    sys.stdout.buffer.flush()
            return 7
        elif line == "spawn-tree":
            code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)"
            children = [subprocess.Popen([sys.executable, "-c", code], start_new_session=detached)
                        for detached in (False, True)]
            emit("TREE " + json.dumps({"pids": [child.pid for child in children]}))
        elif line in {"fence-A", "fence-B"}:
            observed = {"pid": os.getpid(), "text": line, "dimensions": dimensions()}
            emit("FENCE " + json.dumps(observed))
            Path(f"{line}.json").write_text(json.dumps(observed))
        elif line == "exit":
            if worker:
                worker.join(timeout=10)
            emit("FINAL_TAIL café 世界")
            return 7
        else:
            emit("ACK " + json.dumps({"pid": os.getpid(), "text": line}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
