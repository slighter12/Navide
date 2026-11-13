//! Kilo Code CLI (an opencode fork). One SQLite store; observe stays on
//! main's reader until the SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "kilo",
    label: "Kilo Code CLI",
    kind: Kind::Cli,
    launch: Launch {
        program: "kilo",
        base_args: &[],
        yolo_args: &["--auto"],
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--session", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["kilo"],
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
