"""G2 ticket 07: the Windows word grammar the structured launch is quoted with (delta 2.3, Windows rows).

Python quotes the arguments an injector appends with `subprocess.list2cmdline` (`osplat/_windows.py`
`quote_arg`); the sidecar composes the same words with its own Windows quoter. Expected values are written out in
the vector file Rust reads too. On a Windows host the quoted line is also read back by the OS's own
`CommandLineToArgvW`, the reference `osplat/_windows.py` uses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

VECTORS = json.loads((Path(__file__).parent / "data" / "g2_windows_vectors.json").read_text(encoding="utf-8"))


def test_quote_matches_the_shared_list2cmdline_vectors():
    for vector in VECTORS["quote"]:
        assert subprocess.list2cmdline([vector["input"]]) == vector["quoted"], vector["input"]


@pytest.mark.skipif(os.name != "nt", reason="CommandLineToArgvW exists on Windows only")
def test_list2cmdline_round_trips_through_the_operating_systems_parser():
    from agent_team_backend.osplat import _windows

    for vector in VECTORS["quote"]:
        line = subprocess.list2cmdline(["prog", vector["input"], "tail"])
        assert _windows._split_command_line(line) == ["prog", vector["input"], "tail"], vector["input"]
