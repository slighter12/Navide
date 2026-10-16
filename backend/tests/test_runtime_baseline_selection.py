"""Method 8 arm selection and sampled-process classification (test support only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.cli_regression.support import runtime_baseline
from tests.cli_regression.support.backend_process import BackendProcess


def test_rust_arm_sets_only_the_existing_selector(tmp_path: Path):
    backend = BackendProcess(tmp_path)
    before = dict(backend.env)
    runtime_baseline.select_runtime(backend, "rust-shell")
    assert backend.env == {**before, "NAVIDE_ACR_EVAL_RUNTIME": "rust-shell"}


def test_python_arm_leaves_the_selector_unset_as_in_method_6(tmp_path: Path):
    backend = BackendProcess(tmp_path)
    runtime_baseline.select_runtime(backend, "python")
    assert "NAVIDE_ACR_EVAL_RUNTIME" not in backend.env


def test_actual_runtime_names_are_distinct_and_cover_both_arms():
    assert runtime_baseline.ACTUAL_RUNTIME == {"python": "python-native-pty", "rust-shell": "rust-sidecar-native-pty"}


@pytest.mark.parametrize(
    ("pid", "executable", "expected"),
    [
        (10, "/venv/bin/python", "backend"),
        (11, "/repo/native/navide-cli-runtime/target/release/navide-cli-runtime", "sidecar"),
        (12, "/bin/bash", "helper"),
        (13, "/venv/bin/python", "helper"),
        (14, "/elsewhere/navide-cli-runtime-lookalike", "helper"),
    ],
)
def test_component_classification(pid: int, executable: str, expected: str):
    assert runtime_baseline.classify_component(pid, 10, executable) == expected


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        (["/venv/bin/python", "-u", "/x/support/baseline_peer.py", "--version"], True),
        (["/venv/bin/python", "-u", "/x/support/baseline_peer.py", "/root", "claude", "pane"], False),
        (["/bin/other", "--version"], False),
        ([], False),
    ],
)
def test_version_probe_detection(cmdline: list[str], expected: bool):
    assert runtime_baseline.is_version_probe(cmdline) is expected
