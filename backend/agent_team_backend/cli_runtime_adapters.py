"""Finite evaluated native observer integration roster, not capability/adoption claims."""
from .cli_runtime_native import native_reference
from .cli_vendors._vendor_facts import FACTS

# One generic reader serves every parser: every declared observer runs on the observation core. The roster is the declarations
# that have an `observer` block. The per-vendor names are the evaluation selector's.
ON_THE_CORE = tuple(sorted(key for key, facts in FACTS.items() if facts.observer))
globals().update({f'{key}_reference': native_reference for key in ON_THE_CORE})

__all__ = sorted(f'{key}_reference' for key in ON_THE_CORE) + ['native_reference']
