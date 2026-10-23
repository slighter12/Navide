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


def _program(tmp_path):
    program = tmp_path / "kilo"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    program.chmod(0o700)
    return program


@pytest.mark.parametrize("case", VECTORS["string_cases"], ids=lambda case: case["id"])
def test_a_direct_string_is_wrapped_even_with_glob_or_tilde_characters(case, tmp_path):
    """Only the list form declines `~*?[]{}!` (credential_launch.py 42); a resolved direct string does not,
    so the Rust runtime must not refuse it either."""
    program, env = _program(tmp_path), {"PATH": str(tmp_path)}
    expected = [item.replace("{PROGRAM}", str(program)) for item in case["expected"]]
    assert credential_launch.wrap_command(case["command"], KILO, HELPER, env=env) == expected
    assert credential_launch.wrap_plan(case["command"], KILO, HELPER, env=env) == {
        "helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": str(program)}


@pytest.mark.parametrize("case", VECTORS["string_refused_cases"], ids=lambda case: case["id"])
def test_a_direct_string_is_declined_exactly_when_wrap_command_declines_it(case, tmp_path):
    _program(tmp_path)
    env = {"PATH": str(tmp_path)}
    assert credential_launch.wrap_command(case["command"], KILO, HELPER, env=env) is None
    assert credential_launch.wrap_plan(case["command"], KILO, HELPER, env=env) is None


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


@pytest.mark.parametrize("field", ["helper", "unset", "entrypoint", "inner"])
@pytest.mark.parametrize("bad", ["a\0b", "lone\ud800surrogate"], ids=["nul", "surrogate"])
def test_text_the_runtime_cannot_carry_is_refused_in_every_new_credential_field(field, bad):
    """Delta 2.7 covers the fields the credential shape adds: refused before dispatch (`pre-dispatch`)."""
    plan = {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": "/x/bin/kilo"}
    launch = cli_launch.build_launch("kilo", "/x/bin/kilo", "/x/bin/kilo --port 4000", credential=plan)
    cli_launch.check_text(launch)  # the clean launch passes
    if field == "inner":
        launch["inner"]["words"].append({"arg": bad})
    elif field == "unset":
        launch["unset"] = [bad]
    elif field == "helper":
        launch["helper"] = [*HELPER, bad]
    else:
        launch["entrypoint"] = bad
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.check_text(launch)
    assert refused.value.code == "CREATE_INVALID_TEXT" and refused.value.stage == "pre-dispatch"


def test_the_unset_names_are_exactly_the_two_helper_variables():
    plan = credential_launch.wrap_plan(["/bin/bash", "-lc", "/x/bin/kilo"], KILO, HELPER)
    assert plan["unset"] == [PORT_ENV, TOKEN_ENV]


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


def test_two_shell_flags_are_not_a_sole_flag():
    """`simple_command_args` admits `-c`, `-lc` and `-ilc` only as the SOLE flag, so `-l -c` is declined by
    its own rule, independently of the `-x` vector (which fails the flag set, not the count)."""
    command = ["/bin/bash", "-l", "-c", "/x/bin/kilo"]
    assert credential_launch.wrap_command(command, KILO, HELPER) is None
    assert credential_launch.wrap_plan(command, KILO, HELPER) is None


def _attested(path: str) -> dict:
    head, tail = cli_launch._cut(path)
    return {"len": len(path.encode()), "head": head, "tail": tail}


@pytest.mark.parametrize("form", ["script", "entrypoint"])
def test_the_credential_launch_attests_the_program_it_really_executes(form):
    """Script form: the inner shell. Entrypoint form: the helper's interpreter (HELPER[0])."""
    from agent_team_backend.cli_runtime import RustTerminalService
    if form == "script":
        base = final = ["/bin/bash", "-lc", "/x/bin/kilo"]
        plan = credential_launch.wrap_plan(final, KILO, HELPER)
        executed, other = "/bin/bash", HELPER[0]
    else:
        base = final = "/x/bin/kilo"
        plan = {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": "/x/bin/kilo"}
        executed, other = HELPER[0], "/x/bin/kilo"
    launch = cli_launch.build_launch("kilo", base, final, credential=plan)
    expected = RustTerminalService._expected_program(launch)
    assert expected == executed
    assert cli_launch.verify_argv0_attestation(_attested(executed), wrapped_shell=expected) == executed
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.verify_argv0_attestation(_attested(other), wrapped_shell=expected)
    assert refused.value.code == "ATTESTATION_INVALID"
