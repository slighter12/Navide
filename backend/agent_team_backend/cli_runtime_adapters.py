"""Finite evaluated native observer integration roster, not capability/adoption claims."""
from .cli_runtime_claude import reference_spec as claude_reference
from .cli_runtime_codex import reference_spec as codex_reference
from .cli_runtime_opencode import reference_spec as opencode_reference

__all__ = ['claude_reference', 'codex_reference', 'opencode_reference']
