"""Finite evaluated native observer integration roster, not capability/adoption claims."""
from .cli_runtime_aider import reference_spec as aider_reference
from .cli_runtime_blob import antigravity_reference, cursor_reference
from .cli_runtime_claude import reference_spec as claude_reference
from .cli_runtime_codex import reference_spec as codex_reference
from .cli_runtime_droid import reference_spec as droid_reference
from .cli_runtime_jsonl import grok_reference, kimi_reference, muse_reference, pi_reference, qwen_reference
from .cli_runtime_opencode import reference_spec as opencode_reference
from .cli_runtime_sqlite import copilot_reference, goose_reference, kilo_reference

__all__ = ['aider_reference', 'antigravity_reference', 'claude_reference', 'codex_reference', 'copilot_reference',
           'cursor_reference', 'droid_reference',
           'goose_reference', 'grok_reference', 'kilo_reference', 'kimi_reference', 'muse_reference',
           'opencode_reference', 'pi_reference', 'qwen_reference']
