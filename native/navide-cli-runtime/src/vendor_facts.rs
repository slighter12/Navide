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
pub struct SessionPath {
    pub segments: &'static [&'static str],
    pub cwd: CwdEncoding,
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
    pub session_path: Option<SessionPath>,
    #[allow(dead_code)]
    pub logs: Option<Logs>,
    pub shutdown: Option<Shutdown>,
    pub capabilities: Capabilities,
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
        session_path: None,
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
        session_path: Some(SessionPath { segments: &["conversations", "{id}.db"], cwd: CwdEncoding::Absent }),
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
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}.jsonl"], cwd: CwdEncoding::Claude }),
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
        session_path: None,
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
        session_path: Some(SessionPath { segments: &["session-state", "{id}", "events.jsonl"], cwd: CwdEncoding::Absent }),
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
        session_path: None,
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
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}.jsonl"], cwd: CwdEncoding::Droid }),
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
        session_path: None,
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
        session_path: Some(SessionPath { segments: &["{cwd}", "{id}", "updates.jsonl"], cwd: CwdEncoding::UrlQuote }),
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
        session_path: None,
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
        session_path: None,
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
        session_path: None,
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
        session_path: None,
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
        session_path: None,
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
        session_path: None,
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
        session_path: Some(SessionPath { segments: &["projects", "{cwd}", "chats", "{id}.jsonl"], cwd: CwdEncoding::Claude }),
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
