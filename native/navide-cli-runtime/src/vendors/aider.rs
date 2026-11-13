//! Aider. Its history is Markdown, not records; observe stays on main's reader.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "aider",
    label: "Aider",
    kind: Kind::Cli,
    launch: Launch {
        program: "aider",
        base_args: &[],
        yolo_args: &["--yes-always"],
        // Any model string goes straight to litellm, so a wrong one cannot be caught.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    // Only a lossy `--restore-chat-history`, which names no session.
    resume: Resume { args: None, parse: None },
    interrupt: b"\x03",
    observe: None,
};
