"""Finite evaluated native integration roster, not capability/adoption claims."""
from .cli_runtime_claude import reference_spec as claude_reference
from .cli_runtime_codex import reference_spec as codex_reference
from .cli_runtime_opencode import reference_spec as opencode_reference

NATIVE_VENDORS = ('claude', 'codex', 'opencode')

__all__ = ['NATIVE_VENDORS', 'claude_reference', 'codex_reference', 'opencode_reference']
