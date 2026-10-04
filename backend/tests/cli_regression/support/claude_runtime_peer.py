"""External Claude stand-in; no vendor libraries, provider or credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import time


def main():
    root = Path(sys.argv[1])
    args = sys.argv[2:]
    if "--version" in args:
        print("claude 99.0.0-regression")
        return
    record = {
        "argv": args, "cwd": os.getcwd(), "pid": os.getpid(),
        "home": os.environ.get("HOME"), "shell_rc": os.environ.get("REFERENCE_SHELL_RC"),
        "marker": os.environ.get("REFERENCE_MARKER"), "config_home": os.environ.get("CLAUDE_CONFIG_DIR"),
        "isatty": os.isatty(0),
        "ctty": os.tcgetpgrp(0) == os.getpgrp(),
    }
    destination = root / f"claude-child-{os.getpid()}.json"
    destination.write_text(json.dumps(record), encoding="utf-8")

    def stop(_signum, _frame):
        # Longer than the ordinary shell's grace: catches wrong policy even
        # when callers request force=True. Only this fixture's files are used.
        time.sleep(1.4)
        (root / f"claude-stopped-{os.getpid()}.json").write_text(json.dumps({
            "signal": "SIGTERM", "stdout_open": os.isatty(1),
        }))
        print("CLAUDE_GRACEFUL_DONE", flush=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, lambda *_: print("CLAUDE_INTERRUPTED", flush=True))
    print("CLAUDE_REFERENCE_READY " + json.dumps(record), flush=True)
    for line in sys.stdin:
        text = line.strip()
        if text == "exit":
            return
        if text == "login complete":
            path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
            path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "dummy-login-B", "refreshToken": "dummy-refresh-B", "expiresAt": 9999999999999}}))
            print("CLAUDE_LOGIN_WRITTEN", flush=True)
            continue
        if text.startswith("stage "):
            stage = text.split()[1]
            flag = "--resume" if "--resume" in args else "--session-id"
            session = args[args.index(flag) + 1]
            encoded = "".join(c if c.isascii() and c.isalnum() else "-" for c in os.getcwd().rstrip("/\\"))
            path = Path(os.environ["HOME"]) / ".claude/projects" / encoded / f"{session}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as stream:
                stream.write((root / f"stage-{stage}.bin").read_bytes())
            print("CLAUDE_STAGE " + stage, flush=True)
            continue
        print("CLAUDE_INPUT " + json.dumps(text), flush=True)


if __name__ == "__main__":
    main()
