//! Declared shutdown behaviour. Observation lives in `observe` (the roster is generated from the declarations).
/// Harness build only: the test declaration of a graceful shutdown (owned by `vendor_facts`).
#[cfg(feature = "fault-hooks")]
pub const FAULT_GRACEFUL: &str = crate::vendor_facts::FAULT_GRACEFUL;

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
    crate::vendor_facts::find(agent_key)
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
    crate::vendor_facts::VENDORS
        .iter()
        .filter_map(|facts| shutdown(facts.key))
        .filter(|spec| spec.graceful)
        .map(|spec| spec.grace_ms)
        .max()
        .unwrap_or(0)
}
