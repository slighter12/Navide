"""Finite evaluated native observer integration roster, not capability/adoption claims."""
from .cli_runtime_aider import reference_spec as aider_reference
from .cli_runtime_claude import reference_spec as claude_reference
from .cli_runtime_codex import reference_spec as codex_reference
from .cli_runtime_droid import reference_spec as droid_reference
from .cli_runtime_jsonl import grok_reference, kimi_reference, muse_reference, pi_reference, qwen_reference
from .cli_runtime_opencode import reference_spec as opencode_reference

__all__ = ['aider_reference', 'claude_reference', 'codex_reference', 'droid_reference', 'grok_reference', 'kimi_reference',
           'muse_reference', 'opencode_reference', 'pi_reference', 'qwen_reference']
