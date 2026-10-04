"""Disposable process entry: guards first, then the real backend main."""
from pathlib import Path
import os

from tests.cli_regression.support.isolation import install_external_guards

install_external_guards(Path(os.environ["NAVIDE_REGRESSION_ROOT"]))
if os.environ.get("NAVIDE_REGRESSION_KEYCHAIN_FIXTURE") == "1":
    from tests.cli_regression.support.fixture_keychain import runner
    from agent_team_backend import credential_vault
    credential_vault._default_security_runner = runner(Path(os.environ["NAVIDE_REGRESSION_ROOT"]))
if generation := os.environ.get("NAVIDE_BASELINE_TRACE"):
    from tests.cli_regression.support.baseline_diagnostics import install_trace
    install_trace(Path(os.environ["NAVIDE_REGRESSION_ROOT"]), generation)

if os.environ.get("NAVIDE_CONTROL_FENCE_GATE") == "1":
    from tests.cli_regression.support.control_fence_gate import install
    install(Path(os.environ["NAVIDE_REGRESSION_ROOT"]))

from agent_team_backend.__main__ import main

raise SystemExit(main())
