"""A line-driven CLI for the Windows runtime tests: it runs under a ConPTY and answers on stdout.

Commands (one per line): `say <text>` echoes `ECHO <text>`; `uni` prints non-ASCII text; `size` prints the
console size; `flood <n>` prints n numbered lines; `quit` exits 0. It prints `WP_READY {json}` first.
"""
import json
import os
import sys

print("WP_READY " + json.dumps({"pid": os.getpid()}), flush=True)
for line in sys.stdin:
    command, _, rest = line.strip().partition(" ")
    if command == "say":
        print("ECHO " + rest, flush=True)
    elif command == "uni":
        print("UNI café 世界 \U0001f600", flush=True)
    elif command == "size":
        size = os.get_terminal_size()
        print(f"SIZE {size.columns}x{size.lines}", flush=True)
    elif command == "flood":
        for number in range(int(rest)):
            print(f"FLOOD {number}", flush=True)
    elif command == "quit":
        break
