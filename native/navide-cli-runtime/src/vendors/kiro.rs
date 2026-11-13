//! Kiro CLI (AWS). No reader names a session Navide started.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "kiro",
    label: "Kiro CLI (AWS)",
    kind: Kind::Cli,
    launch: Launch {
        // `kiro` alone opens the IDE.
        program: "kiro-cli",
        base_args: &["chat", "--v3"],
        yolo_args: &["--trust-all-tools"],
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
