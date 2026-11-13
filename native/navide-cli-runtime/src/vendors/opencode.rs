//! OpenCode (Anomaly). One SQLite store; observe stays on main's reader.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "opencode",
    label: "OpenCode (Anomaly)",
    kind: Kind::Cli,
    // Only `opencode run` has a permission bypass; the TUI has none.
    launch: Launch { model: Some(&["--model", "{}"]), ..Launch::of("opencode") },
    resume: Resume {
        args: Some(&["--session", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["opencode"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--session", "-s"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
};

crate::unobserved!(CLI);
