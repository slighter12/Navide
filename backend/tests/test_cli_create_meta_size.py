"""G2 ticket 04 (Q1): the largest real CREATE meta of the matrix against the 4 MiB cap.

The environment travels whole (delta 2.6). The real service merges the machine's own environment,
so the measured size is the one a real launch on this machine sends. Set `NAVIDE_G2_META_REPORT` to
a file to record the figure.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from agent_team_backend import cli_runtime_frames as wire
from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_frames import FrameType, code
from tests.cli_regression.support.isolation import isolated_environment
from tests.test_cli_runtime_composition import NESTED_SETTINGS, NESTED_TOML

BINARY = Path(os.environ.get("NAVIDE_CLI_RUNTIME_BIN")
              or Path(__file__).resolve().parents[2] / "native/navide-cli-runtime/target/release/navide-cli-runtime")

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux") or not BINARY.is_file(),
                                 reason="requires the built POSIX native runtime")]


async def test_the_largest_matrix_create_meta_is_far_below_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIDE_CLI_RUNTIME_BIN", str(BINARY))
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", str(tmp_path))
    sizes: list[int] = []
    real = wire.encode

    def spy(frame, *args, **kwargs):
        encoded = real(frame, *args, **kwargs)
        if frame.frame_type is FrameType.REQUEST and frame.code == code.CREATE:
            sizes.append(len(encoded) - wire.HEADER_LEN - len(frame.blob))
        return encoded

    monkeypatch.setattr(wire, "encode", spy)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = RustTerminalService(lambda _frame: asyncio.sleep(0))
    # The longest matrix lines: nested JSON and TOML settings, wrapped by a login shell.
    lines = [f"true --settings {shlex.quote(NESTED_SETTINGS)}", f"true --config-override {shlex.quote(NESTED_TOML)}"]
    sessions = []
    try:
        for index, line in enumerate(lines):
            sessions.append(await service.create_async(
                pane_id=f"meta-{index}", agent_key="terminal", command=[shutil.which("bash"), "-lc", line],
                cwd=str(workspace), cols=100, rows=30, env=isolated_environment(tmp_path)))
    finally:
        for session in sessions:
            await service.kill(session.id, force=True)
    assert sizes, "no CREATE was observed"
    largest = max(sizes)
    report = os.environ.get("NAVIDE_G2_META_REPORT")
    if report:
        Path(report).write_text(json.dumps({"platform": sys.platform, "largest_create_meta_bytes": largest,
                                            "cap_bytes": wire.MAX_META_P2R, "creates": len(sizes)}) + "\n",
                                encoding="utf-8")
    assert largest < wire.MAX_META_P2R // 8, (largest, wire.MAX_META_P2R)
