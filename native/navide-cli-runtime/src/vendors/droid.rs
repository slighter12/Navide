//! Droid CLI (Factory). Observe stays on main's reader this round.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "droid",
    label: "Droid CLI (Factory)",
    kind: Kind::Cli,
    launch: Launch {
        program: "droid",
        base_args: &[],
        yolo_args: &["--auto", "high"],
        // The interactive command accepts an unknown `--model` and ignores it.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["droid"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume", "-r"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
