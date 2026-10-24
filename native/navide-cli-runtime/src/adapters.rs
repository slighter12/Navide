//! Finite native observer registration; vendor strategies stay in their modules.
#[cfg(unix)]
use serde_json::Value;
#[cfg(unix)]
use std::io;

#[cfg_attr(windows, allow(dead_code))] // observation is POSIX-only; launch admission is `compose::admit`
pub fn integrated(key: &str) -> bool {
    #[cfg(feature = "fault-hooks")]
    if key == FAULT_GRACEFUL {
        return true;
    }
    // Vendor observers read POSIX session files; none is integrated on Windows yet (E5 scope: shells).
    cfg!(unix)
        && (matches!(
            key,
            "claude" | "codex" | "grok" | "kimi" | "muse" | "pi" | "qwen"
        )
            || (cfg!(target_os = "macos") && key == "opencode"))
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
    #[cfg(feature = "fault-hooks")]
    if agent_key == FAULT_GRACEFUL {
        return Some(ShutdownSpec {
            graceful: true,
            grace_ms: 2000,
            defer_master_close: true,
        });
    }
    super::vendor_facts::find(agent_key)
        .and_then(|facts| facts.shutdown)
        .map(|declared| ShutdownSpec {
            graceful: declared.graceful,
            grace_ms: declared.grace_ms,
            defer_master_close: declared.defer_master_close,
        })
}

/// The largest graceful grace any registered adapter declares (derives `cleanup_ms`).
pub fn max_grace_ms() -> u64 {
    // Every declared vendor, by its own declaration (never a key literal in this shared function).
    super::vendor_facts::VENDORS
        .iter()
        .filter_map(|facts| shutdown(facts.key))
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
        Some("grok") => super::observe_grok::observe(args, expected),
        Some("kimi") => super::observe_kimi::observe(args, expected),
        Some("muse") => super::observe_muse::observe(args, expected),
        Some("pi") => super::observe_pi::observe(args, expected),
        Some("qwen") => super::observe_qwen::observe(args, expected),
        #[cfg(target_os = "macos")]
        Some("opencode") => super::opencode::observe(args).map(|document| (document, None)),
        _ => Err(io::Error::other("observer not integrated")),
    }
}
