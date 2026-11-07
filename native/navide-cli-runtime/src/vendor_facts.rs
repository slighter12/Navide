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

// The declaration data, in its own generated file; the types and the functions stay here.
mod declarations;
pub use declarations::{SUBJECT_ALIASES, SUBJECT_COMPOSITION, VENDORS};

pub fn find(key: &str) -> Option<&'static VendorFacts> {
    VENDORS.iter().find(|facts| facts.key == key)
}

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
