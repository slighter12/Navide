"""The frozen managed-OpenCode descriptor lifts `command_text` and `simple_command_args` out of `base.py`.

`managed_opencode_admission._descriptor` execs only those two function definitions in a scope that holds `Path`
and `shlex`, then calls `credential_launch.wrap_command` through them. These tests build that namespace the same
way, so a dependency added to either function that the namespace does not hold fails here and not in a hosted run.
"""

from __future__ import annotations

import ast
import shlex
from pathlib import Path

from agent_team_backend.cli_vendors import base
from agent_team_backend.cli_vendors._vendor_facts import FACTS

BASE_SOURCE = Path(base.__file__)


def frozen_namespace() -> dict:
    tree = ast.parse(BASE_SOURCE.read_text(encoding="utf-8"))
    names = ("command_text", "simple_command_args")
    selected = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    scope: dict = {"Path": Path, "shlex": shlex}
    exec(compile(module, str(BASE_SOURCE), "exec"), scope)  # noqa: S102 - the frozen harness does the same
    return scope


def test_the_frozen_namespace_parses_a_wrapped_command_through_the_two_lifted_functions() -> None:
    scope = frozen_namespace()
    assert scope["simple_command_args"](["/bin/bash", "-lc", "opencode -s ses_1"]) == ["opencode", "-s", "ses_1"]
    assert scope["simple_command_args"]("opencode") == ["opencode"]
    assert scope["simple_command_args"](["/bin/bash", "-x", "opencode"]) == []
    assert scope["simple_command_args"](["/bin/bash", "-lc", "opencode; rm -rf x"]) == []


def test_wrap_command_runs_through_the_frozen_namespace() -> None:
    from types import SimpleNamespace

    from agent_team_backend import credential_launch

    tree = ast.parse(Path(credential_launch.__file__).read_text(encoding="utf-8"))
    names = ("_script_words", "helper_argv", "wrap_command")
    selected = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} >= {"_script_words", "wrap_command"}
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *[n for n in selected if n.name in {"_script_words", "wrap_command"}]],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    scope: dict = {"Path": Path, "shlex": shlex, "simple_command_args": frozen_namespace()["simple_command_args"],
                   "PORT_ENV": "P", "TOKEN_ENV": "T"}
    exec(compile(module, str(credential_launch.__file__), "exec"), scope)  # noqa: S102
    wrapped = scope["wrap_command"](["/bin/bash", "-lc", "opencode -s ses_1"], SimpleNamespace(key="opencode"), ["helper"])
    assert wrapped is not None and wrapped[:2] == ["/bin/bash", "-lc"]


def test_a_text_only_command_under_an_unlisted_login_shell_still_names_its_resume_id() -> None:
    facts = FACTS["claude"]
    for shell in ("/bin/tcsh", "/usr/bin/nu", "/bin/mksh", "xonsh"):
        assert base.resume_id_from_command([shell, "-lc", "claude --resume abc-123"], facts) == "abc-123"
        assert base.resume_id_from_command([shell, "-ilc", "claude --resume abc-123"], facts) == "abc-123"
