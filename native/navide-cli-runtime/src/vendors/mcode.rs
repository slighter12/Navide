//! MiniMax Code. Strict about unknown flags; no session Navide can name.
use crate::decl::*;

pub static CLI: Cli = Cli {
    key: "mcode",
    label: "MiniMax Code",
    kind: Kind::Cli,
    launch: Launch {
        program: "mcode",
        base_args: &[],
        // Unattended mode is the `permissionMode` setting, not a flag.
        yolo_args: &[],
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
