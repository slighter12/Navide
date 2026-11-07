"""Finite evaluated native observer integration roster, not capability/adoption claims."""
from .cli_runtime_native import native_reference
from .cli_vendors._vendor_facts import FACTS

# One generic reader serves every parser: every declared observer runs on the observation core. The roster is the declarations
# that have an `observer` block. The per-vendor names are the evaluation selector's.
ON_THE_CORE = tuple(sorted(key for key, facts in FACTS.items() if facts.observer))
globals().update({f'{key}_reference': native_reference for key in ON_THE_CORE})

__all__ = sorted(f'{key}_reference' for key in ON_THE_CORE) + ['install']

_installed: set[int] = set()


def install(vendors: dict) -> None:
    """Apply the evaluation selector to the registry in place. The registry sits below the runtime and holds plain
    specs; the application calls this once, before it builds the readers. A second call on the same registry is a no-op."""
    if id(vendors) in _installed:
        return
    for key, spec in list(vendors.items()):
        vendors[key] = native_reference(spec)
    _installed.add(id(vendors))
