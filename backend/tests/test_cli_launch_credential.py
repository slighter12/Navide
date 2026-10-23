"""G2 ticket 05, delta addendum 10.2: the managed-credential outer wrapper as a structured launch shape.

`credential_launch.wrap_command` stays the Windows/`argv` renderer and the oracle. `wrap_plan` applies
exactly its predicates but returns typed data, and `cli_launch.build_launch(..., credential=plan)` wraps
the ordinary launch object in the `credential` shape that the Rust runtime renders.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
from types import SimpleNamespace

import pytest

from agent_team_backend import cli_launch, credential_launch
from agent_team_backend.credential_path_helper import PORT_ENV, TOKEN_ENV

VECTORS = json.loads((Path(__file__).parent / "data" / "g2_credential_wrap_vectors.json").read_text(encoding="utf-8"))
HELPER = VECTORS["helper"]
KILO = SimpleNamespace(key="kilo")


def test_the_vectors_are_the_live_wrap_command_output():
    """The vectors are the oracle both runtimes must reproduce byte for byte; they must not drift."""
    for case in VECTORS["script_cases"]:
        command = [case["shell"], case["flags"][0], case["line"]]
        assert credential_launch.wrap_command(command, KILO, HELPER) == case["expected"], case["id"]
    for case in VECTORS["refused_cases"]:
        assert credential_launch.wrap_command([case["shell"], case["flags"][0], case["line"]], KILO, HELPER) is None, case["id"]
    assert VECTORS["unset"] == [PORT_ENV, TOKEN_ENV]


@pytest.mark.parametrize("case", VECTORS["script_cases"], ids=lambda case: case["id"])
def test_wrap_plan_wraps_exactly_when_wrap_command_does(case):
    plan = credential_launch.wrap_plan([case["shell"], case["flags"][0], case["line"]], KILO, HELPER)
    assert plan == {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": None}


@pytest.mark.parametrize("case", VECTORS["refused_cases"], ids=lambda case: case["id"])
def test_wrap_plan_declines_what_wrap_command_declines(case):
    assert credential_launch.wrap_plan([case["shell"], case["flags"][0], case["line"]], KILO, HELPER) is None


def test_wrap_plan_for_a_string_command_carries_the_resolved_program(tmp_path):
    program = tmp_path / "kilo"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    program.chmod(0o700)
    env = {"PATH": str(tmp_path)}
    command = "kilo --port 4000"
    expected = credential_launch.wrap_command(command, KILO, HELPER, env=env)
    assert expected == [*HELPER, "--entrypoint", str(program), "--", str(program), "--port", "4000"]
    assert credential_launch.wrap_plan(command, KILO, HELPER, env=env) == {
        "helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": str(program)}
    assert credential_launch.wrap_plan(command, KILO, HELPER, env={"PATH": str(tmp_path / "nowhere")}) is None
    assert credential_launch.wrap_plan("other --port 4000", KILO, HELPER, env=env) is None


def test_build_launch_wraps_the_ordinary_launch_in_the_credential_shape():
    plan = {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": None}
    base = ["/bin/bash", "-lc", "/x/bin/kilo --model fixture-model"]
    final = ["/bin/bash", "-lc", "/x/bin/kilo --model fixture-model --port 4000 --hostname 127.0.0.1"]
    launch = cli_launch.build_launch("kilo", base, final, credential=plan)
    assert launch == {
        "shape": "credential", "helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV],
        "inner": {"shape": "wrapped", "shell": "/bin/bash", "flags": ["-lc"], "words": [
            {"lit": "/x/bin/kilo"}, {"opt": "model", "value": "fixture-model"},
            {"arg": "--port"}, {"arg": "4000"}, {"arg": "--hostname"}, {"arg": "127.0.0.1"}]}}
    assert "entrypoint" not in launch


def test_build_launch_for_a_string_command_names_the_entrypoint():
    plan = {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": "/x/bin/kilo"}
    launch = cli_launch.build_launch("kilo", "/x/bin/kilo", "/x/bin/kilo --port 4000", credential=plan)
    assert launch["shape"] == "credential" and launch["entrypoint"] == "/x/bin/kilo"
    assert launch["inner"] == {"shape": "direct", "form": "string",
                               "words": [{"lit": "/x/bin/kilo"}, {"arg": "--port"}, {"arg": "4000"}]}


def test_a_managed_credential_launch_of_a_rust_subject_is_admitted():
    """Replaces the ticket 04 refusal (delta 5): the structured outer form carries the wrapper."""
    assert cli_launch.admit_composition("claude", platform="posix", managed_credential_path=True) == "rust"


def test_the_wrapper_arguments_survive_the_credential_shape_exactly():
    """The words of the inner launch render to the line the oracle embeds, whatever the quoting."""
    line = "/x/bin/kilo --note '世界 café' --port 4000"
    base = ["/bin/bash", "-lc", "/x/bin/kilo"]
    final = ["/bin/bash", "-lc", line]
    plan = credential_launch.wrap_plan(final, KILO, HELPER)
    launch = cli_launch.build_launch("kilo", base, final, credential=plan)
    assert cli_launch.render(launch["inner"]["words"], "kilo") == line
    assert shlex.split(line)[0] == "/x/bin/kilo"
