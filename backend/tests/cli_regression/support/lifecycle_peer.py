"""Fixture CLI for lifecycle, resize and loss tests. Command-driven so a test controls timing.

Commands (one per line on its tty): ``flood N`` (N numbered 200-byte lines, then FLOOD_DONE),
``numbered N W`` (N lines stamped with the current window width, then MARKER), ``size``
(window size), ``noecho`` (stop the tty echoing input), ``closestdio`` (close fds 0/1/2 and keep running), ``exit``, ``child`` (a
descendant that holds the slave open; prints its pid), ``child-nohup`` (the same, ignoring SIGHUP),``detach`` (a descendant in its own
session that keeps running), ``trap-term`` (ignore SIGTERM), ``winch`` (print the window
size on every SIGWINCH), ``marker-file PATH`` (write PATH when everything printed so far was
flushed), ``stubborn`` (ignore SIGTERM and SIGHUP).
"""
import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import termios
import time


def size():
    packed = fcntl.ioctl(1, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _x, _y = struct.unpack("HHHH", packed)
    return [cols, rows]


# A fixture must not depend on how the test runner was launched: an ignored SIGHUP survives fork and exec, so a runner started
# with `nohup` (or from a shell that ignores it) would hand every descendant an ignored SIGHUP and the kernel's hangup at a session
# leader's exit would no longer end them (measured: E1 T4 failed deterministically under `nohup`, passed without it,
# `receipts/r4/t4-nohup.log` / `t4-plain.log`). The fixture's own disposition is therefore set explicitly; tests that need an
# ignoring CLI ask for it (`stubborn`).
signal.signal(signal.SIGHUP, signal.SIG_DFL)
STUBBORN = False
print("LIFE_READY " + json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "size": size()}), flush=True)
def lines():
    try:
        yield from sys.stdin
    except OSError:  # EIO after the hangup
        return


for line in lines():
    words = line.split()
    command = words[0] if words else ""
    if command == "flood":
        for number in range(int(words[1])):
            print(f"FLOOD {number:06d} " + "x" * 200, flush=True)
        print("FLOOD_DONE", flush=True)
    elif command == "numbered":
        for number in range(int(words[1])):
            print(f"LINE {number:04d} width={size()[0]}", flush=True)
        print("MARKER", flush=True)
    elif command == "size":
        print("SIZE " + json.dumps(size()), flush=True)
    elif command == "closestdio":
        for descriptor in (0, 1, 2):
            os.close(descriptor)
        deadline = time.monotonic() + (float(words[1]) if len(words) > 1 else 10**6)
        while time.monotonic() < deadline:
            time.sleep(0.05)
        sys.exit(0)
    elif command == "rawhex":
        os.write(1, bytes.fromhex(words[1]))
    elif command == "exit":
        sys.exit(0)
    elif command == "child":
        child = subprocess.Popen(["/bin/sleep", "300"])
        print("CHILD " + json.dumps({"pid": child.pid}), flush=True)
    elif command == "child-nohup":
        child = subprocess.Popen(["/bin/sleep", "300"], preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
        print("CHILD " + json.dumps({"pid": child.pid}), flush=True)
    elif command == "detach":
        child = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("DETACHED " + json.dumps({"pid": child.pid}), flush=True)
    elif command == "trap-term":
        signal.signal(signal.SIGTERM, lambda *_: print("GOT_TERM", flush=True))
        print("TRAPPING", flush=True)
    elif command == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        STUBBORN = True
        print("STUBBORN", flush=True)
    elif command == "winch":
        signal.signal(signal.SIGWINCH, lambda *_: print("WINCH " + json.dumps(size()), flush=True))
        print("WINCH_ARMED", flush=True)
    elif command == "noecho":
        attrs = termios.tcgetattr(0)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(0, termios.TCSANOW, attrs)
        print("NOECHO", flush=True)
    elif command == "stall":
        # ``stall SECONDS BYTES``: stop reading stdin (raw mode, so the tty input queue fills and a writer blocks),
        # then drain exactly BYTES and report. Used to make the master's write side block (input_blocked events).
        attrs = termios.tcgetattr(0)
        saved = list(attrs)
        attrs[3] &= ~(termios.ICANON | termios.ECHO)
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 1, 0
        termios.tcsetattr(0, termios.TCSANOW, attrs)
        print("STALLED", flush=True)
        time.sleep(float(words[1]))
        total = 0
        while total < int(words[2]):
            total += len(os.read(0, 65536))
        termios.tcsetattr(0, termios.TCSANOW, saved)
        print("DRAINED " + str(total), flush=True)
    elif command == "marker-file":
        open(words[1], "w").close()
        print("MARKED", flush=True)
    else:
        print("ECHO " + line.strip(), flush=True)
while STUBBORN:
    time.sleep(1)
