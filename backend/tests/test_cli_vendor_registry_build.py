"""The vendor registry is built, not mutated: the evaluation selector's wrap produces a copy, and the registry other
modules read never changes under them."""
from __future__ import annotations

import pytest

from agent_team_backend import cli_runtime_adapters
from agent_team_backend.cli_vendors import registry


def test_build_returns_a_fresh_registry_of_the_same_vendors_each_time():
    first, second = registry.build(), registry.build()
    assert first is not second and first is not registry.VENDORS
    assert list(first) == list(registry.VENDORS) == [spec.key for spec in registry._ALL]  # noqa: SLF001
    assert all(first[key] is registry.VENDORS[key] for key in first), "without a wrap the specs are the plain ones"


def test_build_applies_the_wrap_to_every_spec_once_and_leaves_the_registry_alone():
    seen: list[str] = []
    before = dict(registry.VENDORS)

    def wrap(spec):
        seen.append(spec.key)
        return spec

    registry.build(wrap=wrap)
    registry.build(wrap=wrap)

    assert seen == [spec.key for spec in registry._ALL] * 2, "one wrap per spec per build, no memo of earlier builds"  # noqa: SLF001
    assert registry.VENDORS == before


@pytest.mark.parametrize("key", sorted(k for k, spec in registry.VENDORS.items() if spec.make_log_reader))
def test_the_selectors_wrap_changes_only_the_copy(key, monkeypatch):
    monkeypatch.setenv("NAVIDE_ACR_EVAL_RUNTIME", "rust-shell")
    monkeypatch.setenv("NAVIDE_REGRESSION_ROOT", "selection-seam")
    plain = registry.VENDORS[key]

    wrapped = registry.build(wrap=cli_runtime_adapters.native_reference)

    assert registry.VENDORS[key] is plain, "the shared registry was rewritten"
    assert not type(plain.make_log_reader()).__name__.startswith("Native")
    if key in cli_runtime_adapters.ON_THE_CORE:
        assert type(wrapped[key].make_log_reader()).__name__.startswith("Native")


def test_there_is_no_in_place_install_to_call_twice():
    assert not hasattr(cli_runtime_adapters, "install")
