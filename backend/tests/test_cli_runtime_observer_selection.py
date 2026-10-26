"""G2 ticket 09 repair 1: which reader a vendor resolves to under the selected Rust runtime.

The Windows sidecar refuses every OBSERVE request (`native/navide-cli-runtime/src/windows.rs:1244`), so there the
registry must keep the baseline Python reader (spec D2); elsewhere `rust-shell` selects the native reader. The seam
is `osplat.has_native_vendor_observer`, which every `reference_spec` swap consults.
"""
from __future__ import annotations

import sys

import pytest

from agent_team_backend import cli_runtime_adapters as adapters, osplat
from agent_team_backend.cli_vendors.registry import VENDORS

OBSERVED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader)


def _reference(key):
    return getattr(adapters, f"{key}_reference")


def _is_native(reader) -> bool:
    return type(reader).__name__.startswith("Native")


@pytest.fixture
def rust_shell(monkeypatch):
    monkeypatch.setenv("NAVIDE_ACR_EVAL_RUNTIME", "rust-shell")
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", "selection-seam")


@pytest.mark.timeout(30)
@pytest.mark.parametrize("key", OBSERVED)
def test_no_native_observer_keeps_the_baseline_reader_under_rust_shell(key, rust_shell, monkeypatch):
    monkeypatch.setattr(osplat, "has_native_vendor_observer", lambda: False)
    spec = VENDORS[key]
    assert _reference(key)(spec) is spec
    assert not _is_native(spec.make_log_reader())


@pytest.mark.timeout(30)
@pytest.mark.parametrize("key", OBSERVED)
def test_native_observer_selects_the_native_reader_under_rust_shell(key, rust_shell, monkeypatch):
    monkeypatch.setattr(osplat, "has_native_vendor_observer", lambda: True)
    assert _is_native(_reference(key)(VENDORS[key]).make_log_reader())


@pytest.mark.timeout(30)
def test_the_platform_answers_that_only_windows_has_no_native_observer():
    assert osplat.has_native_vendor_observer() is (sys.platform != "win32")
