// Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit.
#![allow(dead_code)]

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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct VendorFacts {
    pub key: &'static str,
    pub command: &'static str,
    pub shutdown: Option<Shutdown>,
    pub capabilities: Capabilities,
}

pub const VENDORS: &[VendorFacts] = &[
    VendorFacts {
        key: "aider",
        command: "aider",
        shutdown: None,
        capabilities: Capabilities { model: Status::NotYetIntegrated, effort: Status::Unknown, skip_permissions: Status::Integrated, resume_by_id: Status::VendorUnsupported, session_id_at_launch: Status::VendorUnsupported, login: Status::Unknown },
    },
    VendorFacts {
        key: "antigravity",
        command: "agy",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "claude",
        command: "claude",
        shutdown: Some(Shutdown { graceful: true, grace_ms: 3000, defer_master_close: true }),
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Integrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "codex",
        command: "codex",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::NotYetIntegrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "copilot",
        command: "copilot",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::NotYetIntegrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "cursor",
        command: "agent",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "droid",
        command: "droid",
        shutdown: None,
        capabilities: Capabilities { model: Status::VendorUnsupported, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
    VendorFacts {
        key: "goose",
        command: "goose",
        shutdown: None,
        capabilities: Capabilities { model: Status::Unknown, effort: Status::Unknown, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::VendorUnsupported, login: Status::Unknown },
    },
    VendorFacts {
        key: "grok",
        command: "grok",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::NotYetIntegrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Integrated, login: Status::Integrated },
    },
    VendorFacts {
        key: "kilo",
        command: "kilo",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "kimi",
        command: "kimi",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "mcode",
        command: "mcode",
        shutdown: None,
        capabilities: Capabilities { model: Status::VendorUnsupported, effort: Status::VendorUnsupported, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::NotYetIntegrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "muse",
        command: "muse",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::VendorUnsupported, login: Status::Integrated },
    },
    VendorFacts {
        key: "opencode",
        command: "opencode",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Integrated },
    },
    VendorFacts {
        key: "pi",
        command: "pi",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::Integrated, skip_permissions: Status::VendorUnsupported, resume_by_id: Status::Integrated, session_id_at_launch: Status::NotYetIntegrated, login: Status::Unknown },
    },
    VendorFacts {
        key: "qwen",
        command: "qwen",
        shutdown: None,
        capabilities: Capabilities { model: Status::Integrated, effort: Status::VendorUnsupported, skip_permissions: Status::Integrated, resume_by_id: Status::Integrated, session_id_at_launch: Status::Unknown, login: Status::Unknown },
    },
];

pub fn find(key: &str) -> Option<&'static VendorFacts> {
    VENDORS.iter().find(|facts| facts.key == key)
}
