//! Cursor CLI. Its store.db holds protobuf blobs; observe stays on main's
//! reader until the SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "cursor",
    label: "Cursor CLI",
    kind: Kind::Cli,
    launch: Launch {
        program: "agent",
        base_args: &[],
        yolo_args: &["--force"],
        // Effort is part of the model id (`gpt-5.3-codex-high`).
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume={id}"]),
        parse: Some(ResumeParse {
            programs: &["agent", "cursor-agent"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume"],
            joined: Some(ValueShape::Any),
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
