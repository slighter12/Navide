"""A shell stand-in named `bash`: records the full argv it was started with, then becomes the real shell.

Installed as the launch's shell, it shows the exact outer argv `[shell, flag, script]` that the runtime
executed for a managed-credential launch, including the shell program itself, without changing what
runs: the real shell executes the same arguments.

Arguments: <destination> <real shell> <program as started> <arguments...>
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> None:
    destination, real_shell, program, *arguments = sys.argv[1:]
    Path(destination).write_text(json.dumps({"argv": [program, *arguments]}), encoding="utf-8")
    os.execv(real_shell, [real_shell, *arguments])


if __name__ == "__main__":
    main()
