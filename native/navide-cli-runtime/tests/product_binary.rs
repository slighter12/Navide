//! R13: the product binary carries no harness code. The harness build (`--features fault-hooks`) is the only one that
//! contains fault injection and evaluation probes; this test reads the binary Cargo built for the features of this
//! test run and looks for the literals only that code owns.
//!
//! The same markers are looked for in the opposite direction under `--features fault-hooks`, so the check cannot pass
//! because a marker was renamed away from the code it is meant to detect.
use std::path::Path;

/// Literals owned by harness-only code, present on every platform.
const HARNESS_MARKERS: &[&str] = &[
    "fault: exit watcher could not start", // crate::fault::watch_exit_error
    "fault-graceful",                       // vendor_facts::FAULT_GRACEFUL
    "cleaned-silent",                       // crate::fault::cleaned_silent
    "resize-hold:",                         // crate::fault::resize_hold
    "--ready-protocol",                     // control::parse_args harness options
];

fn binary() -> Vec<u8> {
    let path = Path::new(env!("CARGO_BIN_EXE_navide-cli-runtime"));
    std::fs::read(path).unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()))
}

fn contains(haystack: &[u8], needle: &str) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle.as_bytes())
}

#[cfg(not(feature = "fault-hooks"))]
#[test]
fn the_product_binary_contains_no_harness_marker() {
    let bytes = binary();
    let found: Vec<&&str> = HARNESS_MARKERS
        .iter()
        .filter(|marker| contains(&bytes, marker))
        .collect();
    assert!(found.is_empty(), "harness code is in the product binary: {found:?}");
}

#[cfg(feature = "fault-hooks")]
#[test]
fn the_harness_binary_contains_every_marker() {
    let bytes = binary();
    let missing: Vec<&&str> = HARNESS_MARKERS
        .iter()
        .filter(|marker| !contains(&bytes, marker))
        .collect();
    assert!(
        missing.is_empty(),
        "a marker no longer identifies the harness build: {missing:?}"
    );
}
