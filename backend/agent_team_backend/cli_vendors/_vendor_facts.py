"""Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit."""
from enum import StrEnum
from typing import NamedTuple


class Mode(StrEnum):
    """What a request asks of an observer; the wire spelling is the value."""
    USAGE = 'usage'
    SESSION_USAGE = 'session_usage'
    LEGACY = 'legacy'
    ACTIVITY = 'activity'
    TURNS = 'turns'
    IDENTIFY = 'identify'
    HAS_SESSION = 'has_session'


class CwdEncoding(StrEnum):
    """How a CLI spells a workspace path as one directory name."""
    ABSENT = 'absent'
    CLAUDE = 'claude'
    DROID = 'droid'
    URL_QUOTE = 'url_quote'
    PI = 'pi'
    MD5 = 'md5'


#: The request key a `has_session` lookup names its session in. Never an ownership key: the sidecar reads `session_id`,
#: `session_generation`, `pane_id` and `workspace_path` as who owns the observation.
WANTED_SESSION = 'wanted_session'


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


class LayoutFacts(NamedTuple):
    source: str
    usage: str
    store_file: str | None
    #: The tables the store file must all hold for this layout to apply (two schemas in one file). The sidecar says which
    #: layout it read a file with; the layouts of one file share their request fields and modes, so this side never opens
    #: the store to look.
    applies_when: tuple[str, ...]
    #: What the request carries for this file that the log does not: `cwd`, `session`, `base_epoch`.
    request: tuple[str, ...]
    #: The modes this layout answers.
    modes: tuple[Mode, ...]


class LegacyState(NamedTuple):
    """A seen-bag key a Python reader once wrote, and the typed state field the one native codec reads it into."""
    prefix: str
    field: str
    kind: str


class PythonFacts(NamedTuple):
    prompt_detail: str
    pane_scope: str
    attribution: str
    identity_source: str


class CompatFacts(NamedTuple):
    """Named compatibility rules: each keeps one behaviour of the baseline Python reader, defect or not."""
    #: Baseline: the baseline reader treated a log that is not there yet as nothing to read.
    absent_file_is_empty: bool
    #: Baseline: the baseline refused a turn breakdown the sidecar does not answer instead of splitting turns from the generic reads.
    no_python_turn_fallback: bool
    #: Baseline: a store query that failed was reported as the answer's error (the reader warns once and a usage pass delivers nothing) instead of refusing the observation.
    store_error_reported: bool


class CoreFacts(NamedTuple):
    modes: tuple[Mode, ...]
    legacy_state: tuple[LegacyState, ...]
    python: PythonFacts
    compat: CompatFacts


class ObserverFacts(NamedTuple):
    parser: str
    parser_id: str
    platforms: tuple[str, ...]
    layouts: tuple[LayoutFacts, ...]
    core: CoreFacts


class ResumeAliases(NamedTuple):
    programs: tuple[str, ...]
    flags: tuple[str, ...]


class Subcommand(NamedTuple):
    word: str
    aliases: tuple[str, ...]


class DataDirs(NamedTuple):
    env: tuple[str, ...]
    home: tuple[str, ...]
    append: tuple[str, ...]
    extra_env: tuple[str, ...]
    tilde: bool


class SessionRoot(NamedTuple):
    root_env: tuple[str, ...]
    base_env: tuple[str, ...]
    home: tuple[str, ...]
    fallbacks: tuple[str, ...]
    sub: tuple[str, ...]


class SessionPath(NamedTuple):
    segments: tuple[str, ...]
    cwd: CwdEncoding


class PermissionAliases(NamedTuple):
    spellings: tuple[str, ...]
    mode_flags: tuple[str, ...]
    config_keys: tuple[str, ...]


class Logs(NamedTuple):
    files: tuple[str, ...]
    cwd: CwdEncoding


class VendorFacts(NamedTuple):
    key: str
    composition: str
    command: str
    model_flag: str | None
    effort_flag: str | None
    known_efforts: tuple[str, ...] | None
    permission_flag: str | None
    resume_args: str | None
    session_id_flag: str | None
    login_args: str | None
    resume_aliases: ResumeAliases | None
    subcommand: Subcommand | None
    data_dirs: DataDirs | None
    session_root: SessionRoot | None
    session_path: SessionPath | None
    permission_aliases: PermissionAliases | None
    logs: Logs | None
    supports_session_resume: bool
    shutdown: ShutdownFacts | None
    capabilities: CapabilityStatus
    observer: ObserverFacts | None


FACTS: dict[str, VendorFacts] = {
    'aider': VendorFacts(
        key='aider',
        composition='rust',
        command='aider',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yes-always',
        resume_args=None,
        session_id_flag=None,
        login_args=None,
        resume_aliases=None,
        subcommand=None,
        data_dirs=None,
        session_root=None,
        session_path=None,
        logs=None,
        permission_aliases=None,
        supports_session_resume=False,
        shutdown=None,
        capabilities=CapabilityStatus(model='not-yet-integrated', effort='unknown', skip_permissions='integrated', resume_by_id='vendor-unsupported', session_id_at_launch='vendor-unsupported', login='unknown'),
        observer=ObserverFacts(parser='aider', parser_id='rust-aider-md-v1', platforms=('unix',), layouts=(LayoutFacts(source='text', usage='per_turn', store_file=None, applies_when=(), request=('cwd', 'namespace'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='aider_text::', field='last_text', kind='text'), LegacyState(prefix='aider_walk::', field='adapter', kind='json'),), python=PythonFacts(prompt_detail='prompt', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'antigravity': VendorFacts(
        key='antigravity',
        composition='rust',
        command='agy',
        model_flag='--model',
        effort_flag='--effort',
        known_efforts=('low', 'medium', 'high'),
        permission_flag='--dangerously-skip-permissions',
        resume_args='--conversation {id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=(), home=('.gemini', 'antigravity-cli'), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=(), home=('.gemini', 'antigravity-cli'), fallbacks=(), sub=('conversations',)),
        session_path=SessionPath(segments=('{id}.db',), cwd=CwdEncoding.ABSENT),
        logs=Logs(files=('*.db',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
        observer=ObserverFacts(parser='antigravity', parser_id='rust-antigravity-sqlite-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='none', store_file=None, applies_when=(), request=('cwd', 'session'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='agy_idx::', field='marks.steps.last', kind='signed'), LegacyState(prefix='agy_pending::', field='marks.steps.pending', kind='set'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'claude': VendorFacts(
        key='claude',
        composition='rust',
        command='claude',
        model_flag='--model',
        effort_flag='--effort',
        known_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag='--dangerously-skip-permissions',
        resume_args='--resume {id}',
        session_id_flag='--session-id',
        login_args='auth login',
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('CLAUDE_CONFIG_DIR',), home=('.claude',), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('CLAUDE_CONFIG_DIR',), home=('.claude',), fallbacks=('.config/claude',), sub=('projects',)),
        session_path=SessionPath(segments=('{cwd}', '{id}.jsonl'), cwd=CwdEncoding.CLAUDE),
        logs=Logs(files=('{cwd}/*.jsonl',), cwd=CwdEncoding.CLAUDE),
        permission_aliases=PermissionAliases(spellings=('--allow-dangerously-skip-permissions', '--permission-mode bypassPermissions'), mode_flags=('--permission-mode',), config_keys=()),
        supports_session_resume=True,
        shutdown=ShutdownFacts(graceful=True, grace_s=3.0, defer_master_close=True),
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='integrated', login='integrated'),
        observer=ObserverFacts(parser='claude', parser_id='rust-claude-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=(), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.TURNS)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.TURNS), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='file_stem', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'codex': VendorFacts(
        key='codex',
        composition='rust',
        command='codex',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--dangerously-bypass-approvals-and-sandbox',
        resume_args='resume {id}',
        session_id_flag=None,
        login_args='login',
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('CODEX_HOME',), home=('.codex',), append=(), extra_env=('CODEX_SQLITE_HOME',), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=(), home=('.codex',), fallbacks=(), sub=('sessions',)),
        session_path=None,
        logs=Logs(files=('**/rollout-*.jsonl',), cwd=CwdEncoding.ABSENT),
        permission_aliases=PermissionAliases(spellings=('--yolo', '--full-auto'), mode_flags=('-a', '--ask-for-approval', '-s', '--sandbox'), config_keys=('approval_policy', 'sandbox_mode')),
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='not-yet-integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
        observer=ObserverFacts(parser='codex', parser_id='rust-codex-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='cumulative', store_file=None, applies_when=(), request=(), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.IDENTIFY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.IDENTIFY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='__lasttext__:', field='last_text', kind='text'), LegacyState(prefix='__cum__:', field='totals', kind='pair'),), python=PythonFacts(prompt_detail='user_message', pane_scope='home_dir', attribution='none', identity_source='header'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=True, store_error_reported=False))),
    ),
    'copilot': VendorFacts(
        key='copilot',
        composition='rust',
        command='copilot',
        model_flag='--model',
        effort_flag='--reasoning-effort',
        known_efforts=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag='--yolo',
        resume_args='--resume={id}',
        session_id_flag=None,
        login_args='login',
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('COPILOT_HOME',), home=('.copilot',), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('COPILOT_HOME',), home=('.copilot',), fallbacks=(), sub=()),
        session_path=SessionPath(segments=('session-state', '{id}', 'events.jsonl'), cwd=CwdEncoding.ABSENT),
        logs=Logs(files=('session-store.db', 'session-state/*/events.jsonl'), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='not-yet-integrated', login='integrated'),
        observer=ObserverFacts(parser='copilot', parser_id='rust-copilot-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='per_turn', store_file='session-store.db', applies_when=(), request=(), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)), LayoutFacts(source='jsonl', usage='cumulative', store_file='events.jsonl', applies_when=(), request=('cwd', 'session'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='__lasttext__:', field='last_text', kind='text'), LegacyState(prefix='__cum__:', field='totals', kind='pair'), LegacyState(prefix='db_act_hw::usage:', field='marks.assistant_usage_events.last', kind='int'), LegacyState(prefix='db_act_hw::turn:', field='marks.turns.last', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=True))),
    ),
    'cursor': VendorFacts(
        key='cursor',
        composition='rust',
        command='agent',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--force',
        resume_args='--resume={id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=ResumeAliases(programs=('cursor-agent',), flags=()),
        subcommand=None,
        data_dirs=DataDirs(env=(), home=('.cursor', 'chats'), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=(), home=('.cursor', 'chats'), fallbacks=(), sub=()),
        session_path=None,
        logs=Logs(files=('{cwd}/*/store.db',), cwd=CwdEncoding.MD5),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
        observer=ObserverFacts(parser='cursor', parser_id='rust-cursor-sqlite-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='none', store_file='store.db', applies_when=(), request=('cwd', 'session', 'base_epoch'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.IDENTIFY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.IDENTIFY), legacy_state=(LegacyState(prefix='cursor_blob::', field='marks.blobs.last', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='store'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'droid': VendorFacts(
        key='droid',
        composition='rust',
        command='droid',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag='--auto high',
        resume_args='--resume {id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=ResumeAliases(programs=(), flags=('-r',)),
        subcommand=None,
        data_dirs=DataDirs(env=('FACTORY_HOME_OVERRIDE',), home=('.factory',), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('FACTORY_HOME_OVERRIDE',), home=('.factory',), fallbacks=(), sub=('sessions',)),
        session_path=SessionPath(segments=('{cwd}', '{id}.jsonl'), cwd=CwdEncoding.DROID),
        logs=Logs(files=('*.jsonl', '{cwd}/*.jsonl'), cwd=CwdEncoding.DROID),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='vendor-unsupported', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
        observer=ObserverFacts(parser='droid', parser_id='rust-droid-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='cumulative', store_file=None, applies_when=(), request=('cwd', 'session'), modes=(Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='droid_text::', field='last_text', kind='text'), LegacyState(prefix='droid_tok::', field='totals', kind='colon_pair'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'goose': VendorFacts(
        key='goose',
        composition='rust',
        command='goose',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='session -r --session-id {id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=None,
        subcommand=Subcommand(word='session', aliases=('s',)),
        data_dirs=None,
        session_root=None,
        session_path=None,
        logs=Logs(files=('sessions.db',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='unknown', effort='unknown', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='vendor-unsupported', login='unknown'),
        observer=ObserverFacts(parser='goose', parser_id='rust-goose-sqlite-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='per_turn', store_file='sessions.db', applies_when=(), request=(), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY), legacy_state=(LegacyState(prefix='goose_message::', field='marks.messages.last', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='store'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'grok': VendorFacts(
        key='grok',
        composition='rust',
        command='grok',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='-r {id}',
        session_id_flag='--session-id',
        login_args='login',
        resume_aliases=ResumeAliases(programs=(), flags=('--resume',)),
        subcommand=None,
        data_dirs=DataDirs(env=('GROK_HOME',), home=('.grok',), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('GROK_HOME',), home=('.grok',), fallbacks=(), sub=('sessions',)),
        session_path=SessionPath(segments=('{cwd}', '{id}', 'updates.jsonl'), cwd=CwdEncoding.URL_QUOTE),
        logs=Logs(files=('{cwd}/*/updates.jsonl',), cwd=CwdEncoding.URL_QUOTE),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='not-yet-integrated', resume_by_id='integrated', session_id_at_launch='integrated', login='integrated'),
        observer=ObserverFacts(parser='grok', parser_id='rust-grok-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=('cwd',), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.TURNS)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY, Mode.TURNS), legacy_state=(LegacyState(prefix='grok_text::', field='last_text', kind='json_text'), LegacyState(prefix='grok_act::', field='legacy_cursor', kind='byte_cursor'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'kilo': VendorFacts(
        key='kilo',
        composition='rust',
        command='kilo',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--auto',
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='auth login',
        resume_aliases=ResumeAliases(programs=(), flags=('-s',)),
        subcommand=None,
        data_dirs=DataDirs(env=('XDG_DATA_HOME',), home=('.local', 'share'), append=('kilo',), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('XDG_DATA_HOME',), home=('.local', 'share'), fallbacks=(), sub=('kilo',)),
        session_path=None,
        logs=Logs(files=('kilo.db',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
        observer=ObserverFacts(parser='opencode', parser_id='rust-kilo-sqlite-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='per_turn', store_file='kilo.db', applies_when=('session_v2', 'session_message'), request=(), modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY)), LayoutFacts(source='sqlite', usage='per_turn', store_file='kilo.db', applies_when=(), request=(), modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY), legacy_state=(LegacyState(prefix='opencode_part::', field='marks.part.last', kind='int'), LegacyState(prefix='opencode_message::', field='marks.session_message.last', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='store'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'kimi': VendorFacts(
        key='kimi',
        composition='rust',
        command='kimi',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yolo',
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='login',
        resume_aliases=ResumeAliases(programs=(), flags=('-S',)),
        subcommand=None,
        data_dirs=DataDirs(env=('KIMI_CODE_HOME',), home=('.kimi-code',), append=(), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('KIMI_CODE_HOME',), home=('.kimi-code',), fallbacks=(), sub=('sessions',)),
        session_path=None,
        logs=Logs(files=('wd_*/session_*/agents/main/wire.jsonl',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
        observer=ObserverFacts(parser='kimi', parser_id='rust-kimi-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=('cwd', 'session', 'now_ms'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='kimi_text::', field='last_text', kind='text'), LegacyState(prefix='kimi_turn::', field='adapter', kind='json'),), python=PythonFacts(prompt_detail='prompt', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'mcode': VendorFacts(
        key='mcode',
        composition='rust',
        command='mcode',
        model_flag=None,
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args=None,
        session_id_flag=None,
        login_args='login',
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('MINIMAX_DATA_DIR', 'MAVIS_DATA_DIR'), home=('.minimax',), append=(), extra_env=(), tilde=False),
        session_root=None,
        session_path=None,
        logs=None,
        permission_aliases=None,
        supports_session_resume=False,
        shutdown=None,
        capabilities=CapabilityStatus(model='vendor-unsupported', effort='vendor-unsupported', skip_permissions='vendor-unsupported', resume_by_id='not-yet-integrated', session_id_at_launch='unknown', login='integrated'),
        observer=None,
    ),
    'muse': VendorFacts(
        key='muse',
        composition='rust',
        command='muse',
        model_flag='--model',
        effort_flag='--reasoning-effort',
        known_efforts=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
        permission_flag='--disable-approval',
        resume_args='resume {id}',
        session_id_flag=None,
        login_args='login',
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('XDG_DATA_HOME',), home=('.local', 'share'), append=('muse',), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('XDG_DATA_HOME',), home=('.local', 'share'), fallbacks=(), sub=('muse', 'sessions')),
        session_path=None,
        logs=Logs(files=('*/*/*/*/session.jsonl',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='vendor-unsupported', login='integrated'),
        observer=ObserverFacts(parser='muse', parser_id='rust-muse-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=('cwd', 'session', 'cli_version'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='muse_text::', field='last_text', kind='text'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'opencode': VendorFacts(
        key='opencode',
        composition='rust',
        command='opencode',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag=None,
        resume_args='--session {id}',
        session_id_flag=None,
        login_args='auth login',
        resume_aliases=ResumeAliases(programs=(), flags=('-s',)),
        subcommand=None,
        data_dirs=DataDirs(env=('XDG_DATA_HOME',), home=('.local', 'share'), append=('opencode',), extra_env=(), tilde=False),
        session_root=SessionRoot(root_env=(), base_env=('XDG_DATA_HOME',), home=('.local', 'share'), fallbacks=(), sub=('opencode',)),
        session_path=None,
        logs=Logs(files=('opencode.db',), cwd=CwdEncoding.ABSENT),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='unknown', login='integrated'),
        observer=ObserverFacts(parser='opencode', parser_id='rust-opencode-sqlite-v1', platforms=('unix',), layouts=(LayoutFacts(source='sqlite', usage='per_turn', store_file='opencode.db', applies_when=('session_v2', 'session_message'), request=(), modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY)), LayoutFacts(source='sqlite', usage='per_turn', store_file='opencode.db', applies_when=(), request=(), modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.LEGACY, Mode.ACTIVITY, Mode.HAS_SESSION, Mode.IDENTIFY), legacy_state=(LegacyState(prefix='opencode_part::', field='marks.part.last', kind='int'), LegacyState(prefix='opencode_message::', field='marks.session_message.last', kind='int'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='store'), compat=CompatFacts(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'pi': VendorFacts(
        key='pi',
        composition='rust',
        command='pi',
        model_flag='--model',
        effort_flag='--thinking',
        known_efforts=('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
        permission_flag=None,
        resume_args='--session-id {id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=None,
        subcommand=None,
        data_dirs=DataDirs(env=('PI_CODING_AGENT_DIR',), home=('.pi', 'agent'), append=(), extra_env=('PI_CODING_AGENT_SESSION_DIR',), tilde=False),
        session_root=SessionRoot(root_env=('PI_CODING_AGENT_SESSION_DIR',), base_env=('PI_CODING_AGENT_DIR',), home=('.pi', 'agent'), fallbacks=(), sub=('sessions',)),
        session_path=None,
        logs=Logs(files=('{cwd}/*.jsonl',), cwd=CwdEncoding.PI),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='integrated', skip_permissions='vendor-unsupported', resume_by_id='integrated', session_id_at_launch='not-yet-integrated', login='unknown'),
        observer=ObserverFacts(parser='pi', parser_id='rust-pi-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=('cwd', 'session', 'now_ms'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='pi_text::', field='last_text', kind='text'), LegacyState(prefix='pi_turn::', field='adapter', kind='json'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
    'qwen': VendorFacts(
        key='qwen',
        composition='rust',
        command='qwen',
        model_flag='--model',
        effort_flag=None,
        known_efforts=None,
        permission_flag='--yolo',
        resume_args='--resume {id}',
        session_id_flag=None,
        login_args=None,
        resume_aliases=ResumeAliases(programs=(), flags=('-r',)),
        subcommand=None,
        data_dirs=DataDirs(env=('QWEN_HOME',), home=('.qwen',), append=(), extra_env=('QWEN_RUNTIME_DIR',), tilde=True),
        session_root=SessionRoot(root_env=(), base_env=('QWEN_RUNTIME_DIR',), home=('.qwen',), fallbacks=(), sub=('projects',)),
        session_path=SessionPath(segments=('{cwd}', 'chats', '{id}.jsonl'), cwd=CwdEncoding.CLAUDE),
        logs=Logs(files=('{cwd}/chats/*.jsonl', '{cwd}/chats/archive/*.jsonl'), cwd=CwdEncoding.CLAUDE),
        permission_aliases=None,
        supports_session_resume=True,
        shutdown=None,
        capabilities=CapabilityStatus(model='integrated', effort='vendor-unsupported', skip_permissions='integrated', resume_by_id='integrated', session_id_at_launch='unknown', login='unknown'),
        observer=ObserverFacts(parser='qwen', parser_id='rust-qwen-jsonl-v1', platforms=('unix',), layouts=(LayoutFacts(source='jsonl', usage='per_turn', store_file=None, applies_when=(), request=('cwd', 'session', 'now_ms'), modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY)),), core=CoreFacts(modes=(Mode.USAGE, Mode.SESSION_USAGE, Mode.ACTIVITY), legacy_state=(LegacyState(prefix='act_hw::', field='water', kind='int'), LegacyState(prefix='qwen_text::', field='last_text', kind='text'), LegacyState(prefix='qwen_turn::', field='adapter', kind='json'),), python=PythonFacts(prompt_detail='user', pane_scope='none', attribution='none', identity_source='file_stem'), compat=CompatFacts(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False))),
    ),
}

#: Non-vendor subjects (the ordinary shell) and how their launch is composed.
SUBJECT_COMPOSITION: dict[str, str] = {'': 'rust', 'terminal': 'rust'}
