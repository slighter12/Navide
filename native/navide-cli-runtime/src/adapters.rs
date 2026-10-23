//! Finite native observer registration; vendor strategies stay in their modules.
#[cfg(unix)]
use serde_json::Value;
#[cfg(unix)]
use std::io;

pub fn integrated(key: &str) -> bool {
    #[cfg(feature = "fault-hooks")]
    if key == FAULT_GRACEFUL {
        return true;
    }
    // Vendor observers read POSIX session files; none is integrated on Windows yet (E5 scope: shells).
    cfg!(unix)
        && (matches!(key, "claude" | "codex") || (cfg!(target_os = "macos") && key == "opencode"))
}

/// Harness build only: a test declaration (not a vendor name) that declares a graceful shutdown
/// with a deferred master close, so declaration-driven policy is proven without a vendor key (S3).
#[cfg(feature = "fault-hooks")]
pub const FAULT_GRACEFUL: &str = "fault-graceful";

/// A vendor's declared shutdown behaviour (language-neutral declaration, ADR 0002). Shared
/// lifecycle code reads this, never a vendor key (E1 13.1, 13.3 site 6).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ShutdownSpec {
    pub graceful: bool,
    pub grace_ms: u64,
    pub defer_master_close: bool,
}

/// The declared shutdown of the adapter registered for `agent_key`, if it declares one. An
/// ordinary shell (`""`/`"terminal"`) and every vendor without a declaration have none.
pub fn shutdown(agent_key: &str) -> Option<ShutdownSpec> {
    use super::claude_facts as claude;
    #[cfg(feature = "fault-hooks")]
    if agent_key == FAULT_GRACEFUL {
        return Some(ShutdownSpec {
            graceful: true,
            grace_ms: 2000,
            defer_master_close: true,
        });
    }
    match agent_key {
        key if key == claude::KEY => Some(ShutdownSpec {
            graceful: claude::GRACEFUL,
            grace_ms: claude::GRACE_MS,
            defer_master_close: claude::DEFER_MASTER_CLOSE,
        }),
        _ => None,
    }
}

/// The largest graceful grace any registered adapter declares (derives `cleanup_ms`).
pub fn max_grace_ms() -> u64 {
    use super::claude_facts as claude;
    // The registered adapters, named by their own declarations (never a key literal in this shared function).
    [claude::KEY]
        .iter()
        .filter_map(|key| shutdown(key))
        .filter(|spec| spec.graceful)
        .map(|spec| spec.grace_ms)
        .max()
        .unwrap_or(0)
}

/// One observation: the result document and, for parsers whose checkpoint anchors a byte range,
/// the raw anchor bytes (E1 14: bytes on the wire, never text-encoded). `expected` is the
/// request blob (the anchor Python read from the file; empty when there is none).
#[cfg(unix)]
pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    match args["vendor"].as_str() {
        Some("claude") => super::claude::observe(args, expected),
        Some("codex") => super::codex::observe(args, expected),
        #[cfg(target_os = "macos")]
        Some("opencode") => super::opencode::observe(args).map(|document| (document, None)),
        _ => Err(io::Error::other("observer not integrated")),
    }
}
