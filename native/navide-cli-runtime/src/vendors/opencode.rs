//! OpenCode (Anomaly). One SQLite store; observe stays on main's reader
//! until the SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "opencode",
    label: "OpenCode (Anomaly)",
    kind: Kind::Cli,
    launch: Launch {
        program: "opencode",
        base_args: &[],
        // Only `opencode run` has a permission bypass; the TUI has none.
        yolo_args: &[],
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
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
    observe: None,
};
