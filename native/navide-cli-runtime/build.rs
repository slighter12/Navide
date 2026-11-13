//! Generates the vendor registry from `src/vendors/*.rs`, so a new CLI is one
//! new file there and never an edit to shared code. Each file must export
//! `pub static CLI: crate::decl::Cli`.
use std::fmt::Write as _;
use std::path::Path;

fn main() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("src").join("vendors");
    println!("cargo:rerun-if-changed={}", dir.display());
    let mut keys: Vec<String> = std::fs::read_dir(&dir)
        .expect("src/vendors is readable")
        .filter_map(|entry| {
            let path = entry.ok()?.path();
            let stem = path.file_stem()?.to_str()?.to_owned();
            (path.extension()? == "rs" && !stem.starts_with('_')).then_some(stem)
        })
        .collect();
    keys.sort();
    let mut out = String::new();
    for key in &keys {
        let file = dir.join(format!("{key}.rs"));
        println!("cargo:rerun-if-changed={}", file.display());
        writeln!(out, "#[path = {:?}]\npub mod {key};", file.display().to_string()).unwrap();
    }
    out.push_str("/// Every declared CLI, sorted by key.\npub static ALL: &[&crate::decl::Cli] = &[\n");
    for key in &keys {
        writeln!(out, "    &{key}::CLI,").unwrap();
    }
    out.push_str("];\n");
    let target = Path::new(&std::env::var("OUT_DIR").unwrap()).join("vendors.rs");
    std::fs::write(target, out).unwrap();
}
