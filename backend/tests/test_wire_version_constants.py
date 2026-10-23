"""G2 ticket 04: wire version 2 is spelled the same in Python, Rust and the golden frame vectors."""
from __future__ import annotations

import json
from pathlib import Path
import re

from agent_team_backend import cli_runtime_frames

NATIVE = Path(__file__).resolve().parents[2] / "native/navide-cli-runtime"
EXPECTED = 2


def test_python_and_rust_constants_and_every_vector_agree():
    assert cli_runtime_frames.WIRE_VERSION == EXPECTED
    rust = re.search(r"pub const WIRE_VERSION: u8 = (\d+);", (NATIVE / "src/frame.rs").read_text())
    assert rust and int(rust.group(1)) == EXPECTED
    vectors = json.loads((NATIVE / "tests/vectors/frame-vectors.json").read_text())
    frames = [*vectors["valid"], *vectors["invalid"]]
    assert frames
    for vector in frames:
        header = bytes.fromhex(vector["hex"])[:3]
        if vector["name"] == "bad version":  # the one vector that is wrong on purpose
            assert header[2] != EXPECTED, vector["name"]
        else:
            assert header[2] == EXPECTED, vector["name"]
