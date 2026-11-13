//! Muse Code (Meta). Observe stays on main's reader this round.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "muse",
    label: "Muse Code (Meta)",
    kind: Kind::Cli,
    launch: Launch {
        program: "muse",
        base_args: &[],
        yolo_args: &["--disable-approval"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--reasoning-effort", "{}"]),
        efforts: &["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["muse"],
            tokens: Tokens::Whitespace,
            subcommand: &["resume"],
            id_after_subcommand: true,
            flags: &[],
            joined: None,
            pick: Pick::First,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x03",
    observe: None,
};
