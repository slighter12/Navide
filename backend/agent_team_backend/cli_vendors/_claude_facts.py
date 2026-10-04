"""Generated from shared/agent-cli/claude.json; do not edit."""
from typing import NamedTuple


class ClaudeFacts(NamedTuple):
    key: str
    command: str
    model_flag: str
    effort_flag: str
    known_efforts: tuple[str, ...]
    permission_flag: str
    resume_flag: str
    session_id_flag: str
    login_args: str
    graceful: bool
    grace_s: float
    defer_master_close: bool


CLAUDE = ClaudeFacts(
    key='claude',
    command='claude',
    model_flag='--model',
    effort_flag='--effort',
    known_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
    permission_flag='--dangerously-skip-permissions',
    resume_flag='--resume',
    session_id_flag='--session-id',
    login_args='auth login',
    graceful=True,
    grace_s=3.0,
    defer_master_close=True,
)
