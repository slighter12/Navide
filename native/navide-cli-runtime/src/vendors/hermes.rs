//! Hermes Agent. One SQLite store; observe stays on main's reader until the
//! SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "hermes",
    label: "Hermes Agent",
    kind: Kind::Agent,
    launch: Launch {
        program: "hermes",
        base_args: &["chat"],
        yolo_args: &["--yolo"],
        // Every pane launches through `--continue`, which refuses `-m` / `--reasoning`.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--continue", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["hermes"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["-c", "--continue"],
            // Only a Navide-started session name.
            joined: Some(ValueShape::Prefix("navide-")),
            pick: Pick::First,
            value: ValueShape::Prefix("navide-"),
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
