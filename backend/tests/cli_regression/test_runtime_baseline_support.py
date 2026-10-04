"""Artifact boundary regressions for the isolated runtime workload."""
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from agent_team_backend import osplat
from .support.cli_shim import base_python_executable, install_cli_shim
from .support.isolation import isolated_environment
from .support.redacted_diagnostics import archive_text

pytestmark = [pytest.mark.cli_regression, pytest.mark.cli_shared]


def test_publication_scrubs_synthetic_auth_before_writing(tmp_path: Path):
    source = tmp_path / "private.log"
    source.write_text('ws://127.0.0.1:1/ws?t=SYNTHETIC_AUTH_SENTINEL\n'
                      'Authorization: Bearer SYNTHETIC_AUTH_SENTINEL\n'
                      'Cookie: session=SYNTHETIC_AUTH_SENTINEL\n'
                      '{"access_token":"SYNTHETIC_AUTH_SENTINEL"}\n')
    published = tmp_path / "published.log"
    archive_text(source, published)
    assert "SYNTHETIC_AUTH_SENTINEL" not in published.read_text()
    assert "<REDACTED>" in published.read_text()
    assert "SYNTHETIC_AUTH_SENTINEL" in source.read_text()


def test_synthetic_metadata_version_probe_uses_owned_executable(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = Path(__file__).with_name("support") / "baseline_peer.py"
    shim = install_cli_shim(bin_dir, "opencode", [base_python_executable(), "-u", str(script)])
    executable = osplat.paths.resolve_program("opencode", path=str(bin_dir))
    assert Path(executable).resolve() == shim.resolve()
    result = subprocess.run(osplat.paths.launch_argv(executable, ["--version"]),
                            env=isolated_environment(tmp_path), capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == "0.0.0-synthetic-baseline"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX login-shell discovery fixture")
def test_owned_discovery_shim_survives_real_login_path_refresh(tmp_path: Path):
    env = isolated_environment(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = Path(__file__).with_name("support") / "baseline_peer.py"
    shim = install_cli_shim(bin_dir, "opencode", [base_python_executable(), "-u", str(script)])
    for name in (".bash_profile", ".zprofile", ".profile"):
        (Path(env["HOME"]) / name).write_text(f'export PATH={shlex.quote(str(bin_dir))}:"$PATH"\n')
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, "-c",
        "from agent_team_backend import onboarding_deps as d; "
        "d._refresh_path_from_login_shell(force=True); "
        "print(d.resolve_executable(d.DEPS_BY_ID['opencode'], quick=True))"],
        env=env, capture_output=True, text=True, check=True, timeout=20)
    assert Path(result.stdout.strip()).resolve() == shim.resolve()
