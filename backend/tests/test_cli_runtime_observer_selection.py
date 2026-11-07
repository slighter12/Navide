"""Which reader a vendor resolves to under the selected Rust runtime, on every operating system.

The sidecar's observation core compiles and runs on Windows as on POSIX, so the only gate between
the baseline Python reader and the native one is the explicit evaluation selector (`NAVIDE_ACR_EVAL_RUNTIME=rust-shell`
inside an isolated harness). The platform decides nothing: these cases hold on whatever host runs them, and the Windows CI
runs them in its required list. The product default is unchanged: with no selector every vendor keeps its baseline reader.
"""
from __future__ import annotations

import pytest

from agent_team_backend import cli_runtime_adapters as adapters, osplat
from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.registry import VENDORS

OBSERVED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader)
#: The platforms an observer can name: the generator's own list, so a third platform cannot be added without these cases noticing.
PLATFORMS = ("unix", "windows")


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
def test_the_product_default_keeps_the_baseline_reader_on_every_platform(key, monkeypatch):
    monkeypatch.delenv("NAVIDE_ACR_EVAL_RUNTIME", raising=False)
    spec = VENDORS[key]
    assert _reference(key)(spec) is spec
    assert not _is_native(spec.make_log_reader())


@pytest.mark.timeout(30)
@pytest.mark.parametrize("key", OBSERVED)
def test_native_observer_selects_the_native_reader_under_rust_shell(key, rust_shell):
    assert _is_native(_reference(key)(VENDORS[key]).make_log_reader())


@pytest.mark.timeout(30)
@pytest.mark.parametrize("platform_id", ["win32", "linux", "darwin"])
def test_the_selection_does_not_depend_on_the_platform_the_backend_runs_on(platform_id, rust_shell, monkeypatch):
    """Windows used to be the one platform whose sidecar refused every OBSERVE, so the registry kept the baseline reader there. The
    sidecar answers on all three now: nothing in the selection reads the platform, whatever it says it is."""
    monkeypatch.setattr(osplat, "platform_id", platform_id)
    assert not hasattr(osplat, "has_native_vendor_observer"), "a per-platform observer gate came back"
    assert all(_is_native(_reference(key)(VENDORS[key]).make_log_reader()) for key in OBSERVED)


@pytest.mark.timeout(30)
@pytest.mark.parametrize(("platform_id", "native"), [("win32", False), ("linux", True), ("darwin", True)])
def test_a_vendor_whose_declared_platforms_leave_out_this_one_keeps_the_baseline_reader(platform_id, native, rust_shell, monkeypatch):
    """The sidecar's roster is filtered by the platforms an observer declares, so the selection must not hand it a vendor it would refuse
    as not integrated: a layout declared for `unix` only is read natively on POSIX and by the baseline reader on Windows."""
    key = OBSERVED[0]
    unix_only = facts.FACTS[key].observer._replace(platforms=("unix",))
    monkeypatch.setitem(facts.FACTS, key, facts.FACTS[key]._replace(observer=unix_only))
    monkeypatch.setattr(osplat, "platform_id", platform_id)
    assert _is_native(_reference(key)(VENDORS[key]).make_log_reader()) is native


@pytest.mark.timeout(30)
@pytest.mark.parametrize("key", OBSERVED)
def test_every_declared_observer_runs_on_every_platform(key):
    """The declaration names each platform its layouts run on (the generator refuses an omission it has no named exception for), so the
    sidecar's roster on Windows is the roster on POSIX."""
    observer = facts.FACTS[key].observer
    assert observer is not None, f"{key} has a reader and declares no native observer"
    assert tuple(sorted(observer.platforms)) == tuple(sorted(PLATFORMS)), (key, observer.platforms)
