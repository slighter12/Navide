"""Fixture CLI for the handoff tests: lists its own descriptors, echoes input, can flood."""
import json
import os
import stat
import sys

fds = []
for name in os.listdir("/dev/fd"):
    fd = int(name)
    if fd <= 2:
        continue
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        continue  # the directory descriptor of this listing itself
    fds.append({"fd": fd, "socket": stat.S_ISSOCK(mode), "fifo": stat.S_ISFIFO(mode),
                "chr": stat.S_ISCHR(mode), "tty": os.isatty(fd)})
print("PEER_READY " + json.dumps({"pid": os.getpid(), "fds": fds, "stdin_tty": sys.stdin.isatty()}), flush=True)
for line in sys.stdin:
    text = line.strip()
    if text.startswith("flood "):
        for number in range(int(text.split()[1])):
            print(f"FLOOD {number:04d} " + "x" * 200, flush=True)
        print("FLOOD_DONE", flush=True)
    elif text == "exit":
        break
    else:
        print("ECHO " + text, flush=True)
