"""A codex stand-in that performs codex's own outward action: fire the SessionStart hook it was launched with.

The real codex runs the command of its `-c hooks.SessionStart=[...]` override with the SessionStart payload on stdin.
This peer reads that same override from its argv, runs that same command with the payload read from
`codex-session-start.json` in the root directory, and so reaches the backend's authenticated
`/hooks/codex/session-start` endpoint over the transport and auth the product wired (port file, auth header file and
per-launch token in the pane environment). It then reports ready and idles until its terminal is torn down.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import tomllib


def main() -> None:
    root_text, *args = sys.argv[1:]
    if "--version" in args:
        print("codex 99.0.0-regression")
        return
    root = Path(root_text)
    definition = next((arg for arg in args if arg.startswith("hooks.SessionStart=")), None)
    if definition is None:
        print("HOOK_NOT_WIRED", flush=True)
    else:
        command = tomllib.loads(definition)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        payload = (root / "codex-session-start.json").read_text(encoding="utf-8")
        result = subprocess.run(command, shell=True, input=payload, text=True, encoding="utf-8",
                                capture_output=True, timeout=30, check=False)
        (root / "codex-hook-result.json").write_text(
            json.dumps({"returncode": result.returncode, "stderr": result.stderr[-500:]}), encoding="utf-8")
    print("TRANSPORT_READY", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
