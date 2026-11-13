//! Devin CLI (Cognition). No reader names a session Navide started.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "devin",
    label: "Devin CLI (Cognition)",
    kind: Kind::Cli,
    launch: Launch {
        program: "devin",
        base_args: &[],
        yolo_args: &["--permission-mode", "dangerous"],
        // `--model` is documented but unchecked against an invalid value.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume { args: None, parse: None },
    interrupt: b"\x03",
    observe: None,
};
