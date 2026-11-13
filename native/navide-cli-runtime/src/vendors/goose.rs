//! Goose. One SQLite store; observe stays on main's reader until the SQLite
//! backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "goose",
    label: "Goose (AAIF)",
    kind: Kind::Cli,
    launch: Launch {
        program: "goose",
        base_args: &[],
        // Approval is the GOOSE_MODE setting, not a flag.
        yolo_args: &[],
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["session", "-r", "--session-id", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["goose"],
            tokens: Tokens::Whitespace,
            subcommand: &["session", "s"],
            id_after_subcommand: false,
            flags: &["--session-id"],
            joined: Some(ValueShape::NoDash),
            pick: Pick::First,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
