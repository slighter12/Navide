"""G2 ticket 05 review finding 1: `env_remove` is applied last, also to the credential helper's variables.

The managed-credential path adds the helper's port and token to the pane environment before the create.
A caller that also lists one of those names in `env_remove` must find it absent from the CREATE the sidecar
is sent, and the structured `credential` launch must not bring it back (the launch object carries no
environment). The test drives `_create_async` and reads the outbound CREATE arguments.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_team_backend.cli_runtime import RustTerminalService, _RuntimeRefused
from agent_team_backend.credential_path_helper import PORT_ENV, TOKEN_ENV

HELPER = ["/opt/py/bin/python", "/opt/app/credential_path_helper.py", "--vendor", "kilo"]
PLAN = {"helper": HELPER, "unset": [PORT_ENV, TOKEN_ENV], "entrypoint": None}

pytestmark = pytest.mark.asyncio


async def _outbound_create(tmp_path, env_remove):
    service = RustTerminalService(lambda _frame: asyncio.sleep(0))
    sent: list[dict] = []

    async def started():
        return None

    async def request(op, args, *rest, **kwargs):
        assert op == "create"
        sent.append(args)
        raise _RuntimeRefused("stop after the CREATE was captured")

    service._ensure_started = started
    service._request = request
    service._loop = asyncio.get_running_loop()
    with pytest.raises(_RuntimeRefused):
        await service._create_async(
            pane_id="kilo-pane", agent_key="kilo", command=["/bin/bash", "-lc", "/x/bin/kilo --port 4000"],
            spawn_command=["/bin/sh", "-c", "true"], credential=PLAN, cwd=str(tmp_path),
            env={PORT_ENV: "4242", TOKEN_ENV: "t-secret", "KEEP_ME": "1"}, env_remove=env_remove, metadata={})
    assert len(sent) == 1
    return sent[0]


@pytest.mark.parametrize("removed", [PORT_ENV, TOKEN_ENV])
async def test_env_remove_wins_over_a_helper_variable_and_nothing_restores_it(tmp_path, removed):
    kept = TOKEN_ENV if removed == PORT_ENV else PORT_ENV
    args = await _outbound_create(tmp_path, [removed])
    assert removed not in args["env"]
    assert args["env"][kept] and args["env"]["KEEP_ME"] == "1"  # the capture sees the rest of the environment
    assert args["launch"]["shape"] == "credential" and args["launch"]["unset"] == [PORT_ENV, TOKEN_ENV]
    assert removed not in str(args["launch"].get("helper")), "the launch carries no environment"


async def test_without_env_remove_both_helper_variables_reach_the_create(tmp_path):
    args = await _outbound_create(tmp_path, [])
    assert args["env"][PORT_ENV] == "4242" and args["env"][TOKEN_ENV] == "t-secret"
