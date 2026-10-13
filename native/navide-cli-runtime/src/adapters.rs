//! Finite native observer registration; vendor strategies stay in their modules.
use serde_json::Value;
use std::io;

pub fn integrated(key: &str) -> bool {
    matches!(key, "claude" | "codex") || (cfg!(target_os = "macos") && key == "opencode")
}

pub fn observe(args: &Value) -> io::Result<Value> {
    match args["vendor"].as_str() {
        Some("claude") => super::claude::observe(args),
        Some("codex") => super::codex::observe(args),
        #[cfg(target_os = "macos")]
        Some("opencode") => super::opencode::observe(args),
        _ => Err(io::Error::other("observer not integrated")),
    }
}
