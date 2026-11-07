// Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Integrated,
    VendorUnsupported,
    NotYetIntegrated,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Shutdown {
    pub graceful: bool,
    pub grace_ms: u64,
    pub defer_master_close: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capabilities {
    pub model: Status,
    pub effort: Status,
    pub skip_permissions: Status,
    pub resume_by_id: Status,
    pub session_id_at_launch: Status,
    pub login: Status,
}

// No vendor table is composed in Python today, so nothing constructs `Python`; `compose::admit` still matches it.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Composition {
    Rust,
    Python,
}

// How a CLI spells a workspace path as one directory name; read by the Python side.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CwdEncoding {
    Absent,
    Claude,
    Droid,
    UrlQuote,
    Pi,
    Md5,
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ResumeAliases {
    pub programs: &'static [&'static str],
    pub flags: &'static [&'static str],
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Subcommand {
    pub word: &'static str,
    pub aliases: &'static [&'static str],
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DataDirs {
    pub env: &'static [&'static str],
    pub home: &'static [&'static str],
    pub append: &'static [&'static str],
    pub extra_env: &'static [&'static str],
    pub tilde: bool,
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SessionRoot {
    pub root_env: &'static [&'static str],
    pub base_env: &'static [&'static str],
    pub home: &'static [&'static str],
    pub fallbacks: &'static [&'static str],
    pub sub: &'static [&'static str],
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SessionPath {
    pub segments: &'static [&'static str],
    pub cwd: CwdEncoding,
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PermissionAliases {
    pub spellings: &'static [&'static str],
    pub mode_flags: &'static [&'static str],
    pub config_keys: &'static [&'static str],
}

// The sidecar composes a launch from words; these records are read by the Python and TypeScript sides.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Logs {
    pub files: &'static [&'static str],
    pub cwd: CwdEncoding,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct VendorFacts {
    pub key: &'static str,
    pub composition: Composition,
    pub command: &'static str,
    pub model_flag: Option<&'static str>,
    pub effort_flag: Option<&'static str>,
    pub known_efforts: Option<&'static [&'static str]>,
    pub permission_flag: Option<&'static str>,
    pub resume_args: Option<&'static str>,
    pub session_id_flag: Option<&'static str>,
    pub login_args: Option<&'static str>,
    #[allow(dead_code)]
    pub resume_aliases: Option<ResumeAliases>,
    #[allow(dead_code)]
    pub subcommand: Option<Subcommand>,
    #[allow(dead_code)]
    pub data_dirs: Option<DataDirs>,
    #[allow(dead_code)]
    pub session_root: Option<SessionRoot>,
    #[allow(dead_code)]
    pub session_path: Option<SessionPath>,
    #[allow(dead_code)]
    pub permission_aliases: Option<PermissionAliases>,
    #[allow(dead_code)]
    pub logs: Option<Logs>,
    pub shutdown: Option<Shutdown>,
    pub capabilities: Capabilities,
}

/// A launch option a word can name (the generator's option table); the one list that says how each is spelled.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Opt {
    Model,
    Effort,
    SkipPermissions,
    Resume,
    SessionId,
    Login,
}

impl Opt {
    /// The option a word names by its wire spelling.
    pub fn parse(wire: &str) -> Option<Opt> {
        match wire {
            "model" => Some(Opt::Model),
            "effort" => Some(Opt::Effort),
            "skip_permissions" => Some(Opt::SkipPermissions),
            "resume" => Some(Opt::Resume),
            "session_id" => Some(Opt::SessionId),
            "login" => Some(Opt::Login),
            _ => None,
        }
    }

    /// The status the vendor's capability for this option must hold for it to be spelled.
    pub fn status(self, capabilities: &Capabilities) -> Status {
        match self {
            Opt::Model => capabilities.model,
            Opt::Effort => capabilities.effort,
            Opt::SkipPermissions => capabilities.skip_permissions,
            Opt::Resume => capabilities.resume_by_id,
            Opt::SessionId => capabilities.session_id_at_launch,
            Opt::Login => capabilities.login,
        }
    }

    /// The declared flag (or, for a template, the text holding `{id}`), `None` where the vendor declares none.
    pub fn flag(self, facts: &VendorFacts) -> Option<&'static str> {
        match self {
            Opt::Model => facts.model_flag,
            Opt::Effort => facts.effort_flag,
            Opt::SkipPermissions => facts.permission_flag,
            Opt::Resume => facts.resume_args,
            Opt::SessionId => facts.session_id_flag,
            Opt::Login => facts.login_args,
        }
    }

    /// Does the option carry a value after its flag (in place of `{id}` for a template)?
    pub const fn takes_value(self) -> bool {
        match self {
            Opt::Model => true,
            Opt::Effort => true,
            Opt::SkipPermissions => false,
            Opt::Resume => true,
            Opt::SessionId => true,
            Opt::Login => false,
        }
    }

    /// Is the declared text a template whose `{id}` the value fills?
    pub const fn is_template(self) -> bool {
        match self {
            Opt::Model => false,
            Opt::Effort => false,
            Opt::SkipPermissions => false,
            Opt::Resume => true,
            Opt::SessionId => false,
            Opt::Login => false,
        }
    }

    /// The only values the vendor accepts for the option (an empty list when it declares none), `None` for any value.
    pub fn values(self, facts: &VendorFacts) -> Option<&'static [&'static str]> {
        match self {
            Opt::Model => None,
            Opt::Effort => Some(facts.known_efforts.unwrap_or(&[])),
            Opt::SkipPermissions => None,
            Opt::Resume => None,
            Opt::SessionId => None,
            Opt::Login => None,
        }
    }
}

pub const VENDORS: &[VendorFacts] = &[
    VendorFacts {
        key: "aider",
        composition: Composition::Rust,
        command: "aider",
        model_flag: None,
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--yes-always"),
        resume_args: None,
        session_id_flag: None,
        login_args: None,
        resume_aliases: None,
        subcommand: None,
        data_dirs: None,
        session_root: None,
        session_path: None,
        permission_aliases: None,
        logs: None,
        shutdown: None,
        capabilities: Capabilities { model: Status::NotYetIntegrated, effort: Status::Unknown, skip_permissions: Status::Integrated, resume_by_id: Status::VendorUnsupported, session_id_at_launch: Status::VendorUnsupported, login: Status::Unknown },
    },
    VendorFacts {
        key: "antigravity",
        composition: Composition::Rust,
        command: "agy",
        model_flag: Some("--model"),
        effort_flag: Some("--effort"),
        known_efforts: Some(&["low", "medium", "high"]),
        permission_flag: Some("--dangerously-skip-permissions"),
        resume_args: Some("--conversation {id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &[], home: &[".gemini", "antigravity-cli"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &[], home: &[".gemini", "antigravity-cli"], fallbacks: &[], sub: &["conversations"] }),
        session_path: Some(SessionPath { segments: &["{id}.db"], cwd: CwdEncoding::Absent }),
        permission_aliases: None,
        logs: Some(Logs { files: &["*.db"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "claude",
        composition: Composition::Rust,
        command: "claude",
        model_flag: Some("--model"),
        effort_flag: Some("--effort"),
        known_efforts: Some(&["low", "medium", "high", "xhigh", "max"]),
        permission_flag: Some("--dangerously-skip-permissions"),
        resume_args: Some("--resume {id}"),
        session_id_flag: Some("--session-id"),
        login_args: Some("auth login"),
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["CLAUDE_CONFIG_DIR"], home: &[".claude"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["CLAUDE_CONFIG_DIR"], home: &[".claude"], fallbacks: &[".config/claude"], sub: &["projects"] }),
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}.jsonl"], cwd: CwdEncoding::Claude }),
        permission_aliases: Some(PermissionAliases { spellings: &["--allow-dangerously-skip-permissions", "--permission-mode bypassPermissions"], mode_flags: &["--permission-mode"], config_keys: &[] }),
        logs: Some(Logs { files: &["{cwd}/*.jsonl"], cwd: CwdEncoding::Claude }),
        shutdown: Some(Shutdown { graceful: true, grace_ms: 3000, defer_master_close: true }),
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Integrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "codex",
        composition: Composition::Rust,
        command: "codex",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--dangerously-bypass-approvals-and-sandbox"),
        resume_args: Some("resume {id}"),
        session_id_flag: None,
        login_args: Some("login"),
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["CODEX_HOME"], home: &[".codex"], append: &[], extra_env: &["CODEX_SQLITE_HOME"], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &[], home: &[".codex"], fallbacks: &[], sub: &["sessions"] }),
        session_path: None,
        permission_aliases: Some(PermissionAliases { spellings: &["--yolo", "--full-auto"], mode_flags: &["-a", "--ask-for-approval", "-s", "--sandbox"], config_keys: &["approval_policy", "sandbox_mode"] }),
        logs: Some(Logs { files: &["**/rollout-*.jsonl"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::NotYetIntegrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "copilot",
        composition: Composition::Rust,
        command: "copilot",
        model_flag: Some("--model"),
        effort_flag: Some("--reasoning-effort"),
        known_efforts: Some(&["none", "minimal", "low", "medium", "high", "xhigh", "max"]),
        permission_flag: Some("--yolo"),
        resume_args: Some("--resume={id}"),
        session_id_flag: None,
        login_args: Some("login"),
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["COPILOT_HOME"], home: &[".copilot"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["COPILOT_HOME"], home: &[".copilot"], fallbacks: &[], sub: &[] }),
        session_path: Some(SessionPath { segments: &["session-state", "{id}", "events.jsonl"], cwd: CwdEncoding::Absent }),
        permission_aliases: None,
        logs: Some(Logs { files: &["session-store.db", "session-state/*/events.jsonl"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::NotYetIntegrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "cursor",
        composition: Composition::Rust,
        command: "agent",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--force"),
        resume_args: Some("--resume={id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: Some(ResumeAliases { programs: &["cursor-agent"], flags: &[] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &[], home: &[".cursor", "chats"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &[], home: &[".cursor", "chats"], fallbacks: &[], sub: &[] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["{cwd}/*/store.db"], cwd: CwdEncoding::Md5 }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "droid",
        composition: Composition::Rust,
        command: "droid",
        model_flag: None,
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--auto high"),
        resume_args: Some("--resume {id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["-r"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["FACTORY_HOME_OVERRIDE"], home: &[".factory"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["FACTORY_HOME_OVERRIDE"], home: &[".factory"], fallbacks: &[], sub: &["sessions"] }),
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}.jsonl"], cwd: CwdEncoding::Droid }),
        permission_aliases: None,
        logs: Some(Logs { files: &["*.jsonl", "{cwd}/*.jsonl"], cwd: CwdEncoding::Droid }),
        shutdown: None,
        capabilities: Capabilities { model: Status::VendorUnsupported, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "goose",
        composition: Composition::Rust,
        command: "goose",
        model_flag: None,
        effort_flag: None,
        known_efforts: None,
        permission_flag: None,
        resume_args: Some("session -r --session-id {id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: None,
        subcommand: Some(Subcommand { word: "session", aliases: &["s"] }),
        data_dirs: None,
        session_root: None,
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["sessions.db"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Unknown, effort: Status::Unknown, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::VendorUnsupported, login: Status::Unknown },
    },
    VendorFacts {
        key: "grok",
        composition: Composition::Rust,
        command: "grok",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: None,
        resume_args: Some("-r {id}"),
        session_id_flag: Some("--session-id"),
        login_args: Some("login"),
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["--resume"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["GROK_HOME"], home: &[".grok"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["GROK_HOME"], home: &[".grok"], fallbacks: &[], sub: &["sessions"] }),
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}", "updates.jsonl"], cwd: CwdEncoding::UrlQuote }),
        permission_aliases: None,
        logs: Some(Logs { files: &["{cwd}/*/updates.jsonl"], cwd: CwdEncoding::UrlQuote }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::NotYetIntegrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Integrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "kilo",
        composition: Composition::Rust,
        command: "kilo",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--auto"),
        resume_args: Some("--session {id}"),
        session_id_flag: None,
        login_args: Some("auth login"),
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["-s"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["XDG_DATA_HOME"], home: &[".local", "share"], append: &["kilo"], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["XDG_DATA_HOME"], home: &[".local", "share"], fallbacks: &[], sub: &["kilo"] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["kilo.db"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "kimi",
        composition: Composition::Rust,
        command: "kimi",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--yolo"),
        resume_args: Some("--session {id}"),
        session_id_flag: None,
        login_args: Some("login"),
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["-S"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["KIMI_CODE_HOME"], home: &[".kimi-code"], append: &[], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["KIMI_CODE_HOME"], home: &[".kimi-code"], fallbacks: &[], sub: &["sessions"] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["wd_*/session_*/agents/main/wire.jsonl"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "mcode",
        composition: Composition::Rust,
        command: "mcode",
        model_flag: None,
        effort_flag: None,
        known_efforts: None,
        permission_flag: None,
        resume_args: None,
        session_id_flag: None,
        login_args: Some("login"),
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["MINIMAX_DATA_DIR", "MAVIS_DATA_DIR"], home: &[".minimax"], append: &[], extra_env: &[], tilde: false }),
        session_root: None,
        session_path: None,
        permission_aliases: None,
        logs: None,
        shutdown: None,
        capabilities: Capabilities { model: Status::VendorUnsupported, effort: Status::VendorUnsupported, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::NotYetIntegrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "muse",
        composition: Composition::Rust,
        command: "muse",
        model_flag: Some("--model"),
        effort_flag: Some("--reasoning-effort"),
        known_efforts: Some(&["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]),
        permission_flag: Some("--disable-approval"),
        resume_args: Some("resume {id}"),
        session_id_flag: None,
        login_args: Some("login"),
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["XDG_DATA_HOME"], home: &[".local", "share"], append: &["muse"], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["XDG_DATA_HOME"], home: &[".local", "share"], fallbacks: &[], sub: &["muse", "sessions"] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["*/*/*/*/session.jsonl"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::VendorUnsupported, login: Status::Integrated },
    },
    VendorFacts {
        key: "opencode",
        composition: Composition::Rust,
        command: "opencode",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: None,
        resume_args: Some("--session {id}"),
        session_id_flag: None,
        login_args: Some("auth login"),
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["-s"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["XDG_DATA_HOME"], home: &[".local", "share"], append: &["opencode"], extra_env: &[], tilde: false }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["XDG_DATA_HOME"], home: &[".local", "share"], fallbacks: &[], sub: &["opencode"] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["opencode.db"], cwd: CwdEncoding::Absent }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "pi",
        composition: Composition::Rust,
        command: "pi",
        model_flag: Some("--model"),
        effort_flag: Some("--thinking"),
        known_efforts: Some(&["off", "minimal", "low", "medium", "high", "xhigh", "max"]),
        permission_flag: None,
        resume_args: Some("--session-id {id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: None,
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["PI_CODING_AGENT_DIR"], home: &[".pi", "agent"], append: &[], extra_env: &["PI_CODING_AGENT_SESSION_DIR"], tilde: false }),
        session_root: Some(SessionRoot { root_env: &["PI_CODING_AGENT_SESSION_DIR"], base_env: &["PI_CODING_AGENT_DIR"], home: &[".pi", "agent"], fallbacks: &[], sub: &["sessions"] }),
        session_path: None,
        permission_aliases: None,
        logs: Some(Logs { files: &["{cwd}/*.jsonl"], cwd: CwdEncoding::Pi }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::NotYetIntegrated, login: Status::Unknown },
    },
    VendorFacts {
        key: "qwen",
        composition: Composition::Rust,
        command: "qwen",
        model_flag: Some("--model"),
        effort_flag: None,
        known_efforts: None,
        permission_flag: Some("--yolo"),
        resume_args: Some("--resume {id}"),
        session_id_flag: None,
        login_args: None,
        resume_aliases: Some(ResumeAliases { programs: &[], flags: &["-r"] }),
        subcommand: None,
        data_dirs: Some(DataDirs { env: &["QWEN_HOME"], home: &[".qwen"], append: &[], extra_env: &["QWEN_RUNTIME_DIR"], tilde: true }),
        session_root: Some(SessionRoot { root_env: &[], base_env: &["QWEN_RUNTIME_DIR"], home: &[".qwen"], fallbacks: &[], sub: &["projects"] }),
        session_path: Some(SessionPath { segments: &["{cwd}", "chats", "{id}.jsonl"], cwd: CwdEncoding::Claude }),
        permission_aliases: None,
        logs: Some(Logs { files: &["{cwd}/chats/*.jsonl", "{cwd}/chats/archive/*.jsonl"], cwd: CwdEncoding::Claude }),
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
];

pub fn find(key: &str) -> Option<&'static VendorFacts> {
    VENDORS.iter().find(|facts| facts.key == key)
}

/// Non-vendor subjects (the ordinary shell): how their launch is composed.
pub const SUBJECT_ALIASES: &[&str] = &["", "terminal"];
pub const SUBJECT_COMPOSITION: Composition = Composition::Rust;

/// Harness build only: a test declaration (not a vendor name) that declares a graceful shutdown
/// with a deferred master close, so declaration-driven policy is proven without a vendor key (S3).
/// `adapters::shutdown` gives it its shutdown; `compose::admit` asks `is_fault_declaration` so that
/// shared code never spells the key.
#[cfg(feature = "fault-hooks")]
pub const FAULT_GRACEFUL: &str = "fault-graceful";

#[cfg(feature = "fault-hooks")]
pub fn is_fault_declaration(key: &str) -> bool {
    key == FAULT_GRACEFUL
}
