"""Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit."""
from typing import NamedTuple


class ShutdownFacts(NamedTuple):
    graceful: bool
    grace_s: float
    defer_master_close: bool


class CapabilityStatus(NamedTuple):
    model: str
    effort: str
    skip_permissions: str
    resume_by_id: str
    session_id_at_launch: str
    login: str


class VendorFacts(NamedTuple):
    key: str
    command: str
    model_flag: str | None
    effort_flag: str | None
    known_efforts: tuple[str, ...] | None
    permission_flag: str | None
    resume_args: str | None
    session_id_flag: str | None
    login_args: str | None
    supports_session_resume: bool
    shutdown: ShutdownFacts | None
    capabilities: CapabilityStatus


FACTS: dict[str, VendorFacts] = {
    'aider': VendorFacts(
        key='aider',
        command='aider',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yes-always',
        resume_args=None,
        session_id_flag=None,
        login_args=None,
        supports_session_resume=False,
        shutdown=None,
        capabilities=CapabilityStatus(model='not-yet-integrated', effort='unknown', skip_permissions='integrated', resume_by_id='vendor-unsupported', session_id_at_launch='vendor-unsupported', login='unknown'),
    ),
    'antigravity': VendorFacts(
        key='antigravity',
        command='agy',
        model_flag='--model',
        effort_flag='--effort',
        known_efforts=('low', 'medium', 'high'),
        permission_flag='--dangerously-skip-permissions',
        resume_args='--conversation {id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
    ),
    'claude': VendorFacts(
        key='claude',
        command='claude',
        model_flag='--model',
        effort_flag='--effort',
        known_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag='--dangerously-skip-permissions',
        resume_args='--resume {id}',
        session_id_flag='--session-id',
        login_args='auth login',
        supports_session_resume=True,
        shutdown=ShutdownFacts(graceful=True, grace_s=3.0, defer_master_close=True),
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='integrated', login='integrated'),
    ),
    'codex': VendorFacts(
        key='codex',
        command='codex',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--dangerously-bypass-approvals-and-sandbox',
        resume_args='resume {id}',
        session_id_flag=None,
        login_args='login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='not-yet-integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
    ),
    'copilot': VendorFacts(
        key='copilot',
        command='copilot',
        model_flag='--model',
        effort_flag='--reasoning-effort',
        known_efforts=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag='--yolo',
        resume_args='--resume={id}',
        session_id_flag=None,
        login_args='login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='not-yet-integrated', login='integrated'),
    ),
    'cursor': VendorFacts(
        key='cursor',
        command='agent',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--force',
        resume_args='--resume={id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
    ),
    'droid': VendorFacts(
        key='droid',
        command='droid',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag='--auto high',
        resume_args='--resume {id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='vendor-unsupported', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
    ),
    'goose': VendorFacts(
        key='goose',
        command='goose',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='session -r --session-id {id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='unknown', effort='unknown', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='vendor-unsupported', login='unknown'),
    ),
    'grok': VendorFacts(
        key='grok',
        command='grok',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='-r {id}',
        session_id_flag='--session-id',
        login_args='login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='not-yet-integrated', resume_by_id='integrated', session_id_at_launch='integrated', login='integrated'),
    ),
    'kilo': VendorFacts(
        key='kilo',
        command='kilo',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--auto',
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='auth login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
    ),
    'kimi': VendorFacts(
        key='kimi',
        command='kimi',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yolo',
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
    ),
    'mcode': VendorFacts(
        key='mcode',
        command='mcode',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args=None,
        session_id_flag=None,
        login_args='login',
        supports_session_resume=False,
        shutdown=None,
        capabilities=CapabilityStatus(model='vendor-unsupported', effort='vendor-unsupported', skip_permissions='vendor-unsupported', resume_by_id='not-yet-integrated', session_id_at_launch='unknown', login='integrated'),
    ),
    'muse': VendorFacts(
        key='muse',
        command='muse',
        model_flag='--model',
        effort_flag='--reasoning-effort',
        known_efforts=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
        permission_flag='--disable-approval',
        resume_args='resume {id}',
        session_id_flag=None,
        login_args='login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='vendor-unsupported', login='integrated'),
    ),
    'opencode': VendorFacts(
        key='opencode',
        command='opencode',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='auth login',
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
    ),
    'pi': VendorFacts(
        key='pi',
        command='pi',
        model_flag='--model',
        effort_flag='--thinking',
        known_efforts=('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag=None,
        resume_args='--session-id {id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='not-yet-integrated', login='unknown'),
    ),
    'qwen': VendorFacts(
        key='qwen',
        command='qwen',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yolo',
        resume_args='--resume {id}',
        session_id_flag=None,
        login_args=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
    ),
}
