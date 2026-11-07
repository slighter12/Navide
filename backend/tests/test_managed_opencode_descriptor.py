"""The managed-OpenCode descriptor of the isolated harness equals what the product builds.

`managed_opencode_admission._descriptor` runs before the kernel boundary, where the product cannot be imported, so it
reads the pure builder bodies (`wrap_plan`, `render_wrapper`, `helper_argv`, `_script_words` and the two parsers
`command_text` / `simple_command_args`) from source. These tests run that real code path and compare its forms with
the product's own functions, called the way `ws_handlers` calls them: a dependency added to any of those bodies that
the pre-boundary scope does not hold, or a change of the rendering, fails here and not in a hosted run.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team_backend import credential_launch, quota_failover
from agent_team_backend.cli_vendors.registry import VENDORS

SUPPORT = Path(__file__).parent / "cli_regression" / "support"
COMMANDS = ("opencode", "opencode -s ses_native_difference")

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the managed fixture is a POSIX shell chain")


def _descriptor(monkeypatch, root: Path) -> dict:
    monkeypatch.syspath_prepend(str(SUPPORT))
    import managed_opencode_admission

    return managed_opencode_admission._descriptor(root, sys.executable, COMMANDS)


def _product_helper() -> list[str]:
    spec = VENDORS["opencode"]
    names = tuple(dict.fromkeys((*quota_failover.credential_env_vars("opencode"),
                                 *(name for _, values in spec.account_switch.uncertain_env_by_scope for name in values))))
    actor = SimpleNamespace(_timeout=inspect.signature(credential_launch.CredentialLaunch.start).parameters["timeout"].default,
                            _path_inputs=spec.credential_path_env_vars)
    return credential_launch.CredentialLaunch.helper_argv(actor, login=False, vendor="opencode", credential_env=names)


def test_the_managed_descriptor_forms_are_the_products_plan_rendered(monkeypatch, tmp_path) -> None:
    document = _descriptor(monkeypatch, tmp_path)
    helper = _product_helper()
    spec = SimpleNamespace(key="opencode")
    assert document["commands"] == list(COMMANDS)
    assert len(document["forms"]) == len(COMMANDS)
    for command, form in zip(COMMANDS, document["forms"], strict=True):
        argv = ["/bin/bash", "-lc", command]
        plan = credential_launch.wrap_plan(argv, spec, helper)
        assert plan is not None and plan["helper"] == helper
        assert form == credential_launch.render_wrapper(argv, spec, plan)


def test_the_managed_descriptor_names_the_sources_the_lineage_check_reads(monkeypatch, tmp_path) -> None:
    document = _descriptor(monkeypatch, tmp_path)
    assert document["root"] == str(tmp_path)
    assert document["helper"] == document["sources"]["helper"]
    assert {"builder", "helper"} <= set(document["sources"])
    assert Path(document["sources"]["builder"]["path"]) == Path(credential_launch.__file__)


def test_the_unwrapped_command_shim_is_gone() -> None:
    assert not hasattr(credential_launch, "wrap_command")
