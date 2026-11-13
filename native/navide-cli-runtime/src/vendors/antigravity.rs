//! Antigravity CLI (`agy`). One SQLite database per conversation; observe
//! stays on main's reader until the SQLite backend exists.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "antigravity",
    label: "Antigravity CLI (Google)",
    kind: Kind::Cli,
    launch: Launch {
        program: "agy",
        base_args: &[],
        yolo_args: &["--dangerously-skip-permissions"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--effort", "{}"]),
        efforts: &["low", "medium", "high"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--conversation", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["agy"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--conversation"],
            joined: Some(ValueShape::NoDash),
            pick: Pick::First,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
