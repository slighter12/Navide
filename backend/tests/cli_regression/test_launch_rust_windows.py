"""Windows launch through the Rust runtime, compared with the pinned Python-path baseline.

Every row launches a subject through the real sidecar (ConPTY, native handles) with the
child-observing recorder and requires the child's argv, cwd and environment to equal the pinned
snapshot of the Python path captured on the same architecture (windows_baseline/<arch>/, from
test_launch_snapshots_windows.py at the revision named in the evidence). No macOS or Linux snapshot
is read.

Windows composition is still Python's (delta 3.1 transition), so each owned receipt says
`argv_source: python`; the rows prove a Rust-selected, child-observed launch for every subject,
including every vendor that has no Rust observer (observation is Python-side on Windows, D2).
They cannot satisfy a required `argv_source: rust` case, and this file does not claim that.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform

import pytest

from .test_launch_snapshots_windows import SHELL_FORMS, SUBJECTS, _observe

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "nt", reason="Windows only")]

ARCH = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
PINNED = Path(__file__).parent / "windows_baseline" / ARCH
#: Variables this harness sets itself for the backend.
HARNESS_ENV = ("NAVIDE_ACR_EVAL_RUNTIME", "NAVIDE_CLI_RUNTIME_BIN")


def _owned(root: Path) -> list[dict]:
    path = root / "runtime-receipts.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [record for record in records if record.get("event") == "owned"]


@pytest.mark.parametrize("form", SHELL_FORMS)
@pytest.mark.parametrize("subject", SUBJECTS)
async def test_rust_runtime_launch_equals_the_pinned_python_baseline(subject, form, tmp_path):
    assert os.environ.get("NAVIDE_CLI_RUNTIME_BIN"), "the Rust sidecar under test must be named"
    root = tmp_path / "row"
    snapshot = await _observe(subject, form, root, rust=True)
    assert snapshot.get("refused") is None, snapshot.get("refused")
    pinned = PINNED / f"{subject}.{form}.json"
    assert pinned.is_file(), f"no pinned Windows baseline for {subject}/{form} ({ARCH})"
    expected = json.loads(pinned.read_text(encoding="utf-8"))
    for gone in HARNESS_ENV:
        snapshot["child_env"].pop(gone, None)
        expected["child_env"].pop(gone, None)
    for key in ("child_argv", "child_cwd", "child_env", "submitted_command"):
        assert snapshot[key] == expected[key], f"{subject}/{form}: {key} differs from the pinned Python-path baseline"
    owned = _owned(root)
    assert len(owned) == 1, owned  # the launch went through the Rust sidecar, once
    assert owned[0].get("argv_source") == "python", owned[0]  # the transition row, stated, not hidden
