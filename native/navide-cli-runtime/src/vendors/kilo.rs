//! Kilo Code CLI (an opencode fork). One SQLite store; observe stays on
//! main's reader.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "kilo",
    label: "Kilo Code CLI",
    kind: Kind::Cli,
    launch: Launch { yolo_args: &["--auto"], model: Some(&["--model", "{}"]), env_strip: &["KILO_CONFIG_DIR", "KILO_CONFIG"], ..Launch::of("kilo") },
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
};

crate::unobserved!(CLI);
