//! GitHub Copilot CLI. Its events.jsonl and session-store.db reconcile against
//! each other; observe stays on main's reader.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "copilot",
    label: "GitHub Copilot CLI",
    kind: Kind::Cli,
    launch: Launch {
        yolo_args: &["--yolo"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--reasoning-effort", "{}"]),
        efforts: &["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        env_strip: &["COPILOT_HOME"],
        ..Launch::of("copilot")
    },
    resume: Resume {
        // The `=` form: the flag is scoped to an id that already exists.
        args: Some(&["--resume={id}"]),
        parse: Some(ResumeParse {
            programs: &["copilot"],
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
};

crate::unobserved!(CLI);
