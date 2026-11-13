//! OpenClaw. One SQLite store with no cwd; observe stays on main's reader
//! until the SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "openclaw",
    label: "OpenClaw",
    kind: Kind::Agent,
    launch: Launch {
        program: "openclaw",
        // The TUI on the local embedded runtime (a bare `openclaw` attaches to a gateway).
        base_args: &["tui", "--local"],
        yolo_args: &[],
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--session", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["openclaw"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--session"],
            joined: Some(ValueShape::Prefix("agent:")),
            pick: Pick::First,
            value: ValueShape::Prefix("agent:"),
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
