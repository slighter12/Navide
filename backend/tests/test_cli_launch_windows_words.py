"""G2 ticket 07: the Windows word grammar of the structured launch (delta 2.3, Windows rows).

Expected values are written out in the vector file Rust reads too. On a Windows host the splitter is also
compared with the OS's own `CommandLineToArgvW`, the reference `osplat/_windows.py` uses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_team_backend import cli_launch

VECTORS = json.loads((Path(__file__).parent / "data" / "g2_windows_vectors.json").read_text(encoding="utf-8"))
PROGRAM = "C:\\Program Files\\x\\prog.cmd"


def test_quote_matches_the_shared_list2cmdline_vectors():
    for vector in VECTORS["quote"]:
        assert cli_launch.quote_windows(vector["input"]) == vector["quoted"], vector["input"]
        assert subprocess.list2cmdline([vector["input"]]) == vector["quoted"], vector["input"]


def test_scan_matches_the_shared_command_line_vectors():
    for vector in VECTORS["split"]:
        found = cli_launch.scan_windows(vector["line"])
        assert [value for _, _, value in found] == vector["words"], vector["line"]


def test_a_quoted_element_reads_back_as_itself_behind_a_program():
    for vector in VECTORS["quote"]:
        words = [value for _, _, value in cli_launch.scan_windows("prog " + vector["quoted"])]
        assert words == ["prog", vector["input"]], vector["input"]


@pytest.mark.skipif(os.name != "nt", reason="CommandLineToArgvW exists on Windows only")
def test_scan_equals_the_operating_systems_command_line_parser():
    from agent_team_backend.osplat import _windows

    for vector in VECTORS["split"]:
        assert [value for _, _, value in cli_launch.scan_windows(vector["line"])] == _windows._split_command_line(
            vector["line"]), vector["line"]


def test_nested_quotes_and_a_space_in_the_path_survive_tokenising_and_rendering():
    base = f'{cli_launch.quote_windows(PROGRAM)} --model {cli_launch.quote_windows("say \"hi\" \\")}'
    launch = cli_launch.build_launch("claude", base, base, platform="windows")
    assert launch["shape"] == "direct" and launch["form"] == "string"
    assert {"opt": "model", "value": 'say "hi" \\'} in launch["words"]
    assert cli_launch.render(launch["words"], "claude", cli_launch.WINDOWS) == base
    assert [value for _, _, value in cli_launch.scan_windows(base)] == [PROGRAM, "--model", 'say "hi" \\']


def test_a_backslash_run_before_a_quote_is_kept_in_an_injector_suffix():
    base = cli_launch.quote_windows(PROGRAM)
    final = base + " " + subprocess.list2cmdline(["--dir", "C:\\a b\\\\", "x\\\"y"])
    launch = cli_launch.build_launch("claude", base, final, platform="windows")
    assert launch["words"][-3:] == [{"arg": "--dir"}, {"arg": "C:\\a b\\\\"}, {"arg": 'x\\"y'}]
    assert cli_launch.render(launch["words"], "claude", cli_launch.WINDOWS) == final


def test_a_list_form_appends_the_suffix_to_its_last_element():
    base = ["pwsh.exe", "-NoLogo", "-Command", "& 'C:\\x\\prog.cmd'"]
    final = [*base[:-1], base[-1] + " " + subprocess.list2cmdline(["a b"])]
    launch = cli_launch.build_launch("claude", base, final, platform="windows")
    assert launch == {"shape": "direct", "form": "list", "elements": base, "words": [{"arg": "a b"}]}
