//! Replays the parity fixtures (`tests/parity/*.json`) recorded from main's
//! Python readers by `backend/tests/test_cli_runtime_parity.py`: the same
//! bytes, mtimes and clock, read the way the log watcher reads them, must give
//! the same usage events and checkpoints, activity events and turns.
use navide_cli_runtime::observe::Observer;
use serde_json::{Map, Value};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

struct Dir(PathBuf);

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The fixture's normalisation: paths relative to the root, no `identity` (dev:inode).
fn norm(value: Value, root: &str) -> Value {
    match value {
        Value::Object(map) => Value::Object(map.into_iter().filter(|(k, _)| k != "identity").map(|(k, v)| (k, norm(v, root))).collect()),
        Value::Array(items) => Value::Array(items.into_iter().map(|v| norm(v, root)).collect()),
        Value::String(s) => Value::String(drop_identity(&s.replace(root, "<root>"))),
        other => other,
    }
}

/// A byte cursor serialised into a seen-keys bag key carries `"identity": "…", ` too.
fn drop_identity(s: &str) -> String {
    const KEY: &str = "\"identity\": \"";
    let Some(start) = s.find(KEY) else { return s.to_owned() };
    let rest = &s[start + KEY.len()..];
    match rest.find("\", ") {
        Some(end) => format!("{}{}", &s[..start], &rest[end + 3..]),
        None => s.to_owned(),
    }
}

fn base64(text: &str) -> Vec<u8> {
    let value = |c: u8| match c {
        b'A'..=b'Z' => c - b'A',
        b'a'..=b'z' => c - b'a' + 26,
        b'0'..=b'9' => c - b'0' + 52,
        b'+' => 62,
        b'/' => 63,
        _ => panic!("not base64: {c}"),
    };
    let digits: Vec<u8> = text.bytes().filter(|c| *c != b'=').map(value).collect();
    let mut out = Vec::with_capacity(digits.len() * 3 / 4);
    for chunk in digits.chunks(4) {
        let n = chunk.iter().enumerate().fold(0u32, |acc, (i, d)| acc | (u32::from(*d) << (18 - 6 * i)));
        out.extend_from_slice(&n.to_be_bytes()[1..chunk.len()]);
    }
    out
}

fn str_of<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or_else(|| panic!("fixture field {key}"))
}

fn replay(fixture: &Path) -> Vec<String> {
    let scenario: Value = serde_json::from_str(&std::fs::read_to_string(fixture).unwrap()).unwrap();
    let name = fixture.file_stem().unwrap().to_string_lossy().into_owned();
    let vendor = str_of(&scenario, "vendor");
    let cli = navide_cli_runtime::cli(vendor).unwrap_or_else(|| panic!("{name}: unknown vendor {vendor}"));
    let nanos = std::time::SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
    let dir = Dir(std::env::temp_dir().join(format!("navide-parity-{name}-{}-{nanos}", std::process::id())));
    std::fs::create_dir_all(&dir.0).unwrap();
    let root = dir.0.to_string_lossy().into_owned();
    let write_sidecars = |files: &Value| {
        for (rel, text) in files.as_object().into_iter().flatten() {
            let path = dir.0.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, text.as_str().unwrap()).unwrap();
        }
    };
    write_sidecars(&scenario["sidecars"]);
    let path = dir.0.join(str_of(&scenario, "file"));
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();

    let mut observer = Observer::new(cli).unwrap_or_else(|| panic!("{name}: {vendor} declares no observe"));
    let mut checkpoint = Map::new();
    let mut bag: Vec<String> = Vec::new();
    let mut failures = Vec::new();
    for (n, step) in scenario["steps"].as_array().unwrap().iter().enumerate() {
        write_sidecars(&step["sidecars"]);
        if str_of(step, "mode") == "db" {
            // The database file exactly as main's readers read it after the step.
            std::fs::write(&path, base64(str_of(step, "db"))).unwrap();
            let file = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
            file.set_modified(UNIX_EPOCH + Duration::from_secs_f64(step["mtime"].as_f64().unwrap())).unwrap();
        } else {
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .write(true)
                .append(str_of(step, "mode") == "append")
                .truncate(str_of(step, "mode") == "write")
                .open(&path)
                .unwrap();
            file.write_all(str_of(step, "text").as_bytes()).unwrap();
            file.set_modified(UNIX_EPOCH + Duration::from_secs_f64(step["mtime"].as_f64().unwrap())).unwrap();
        }
        observer.set_clock(step["now"].as_f64());
        let read = observer.usage(&path, &checkpoint).unwrap_or_else(|e| panic!("{name} step {n}: usage {e}"));
        checkpoint = read.checkpoint.clone();
        let usage = norm(serde_json::to_value(&read).unwrap(), &root);
        if usage != step["expect"]["usage"] {
            failures.push(format!("{name} step {n} usage\n  rust:   {usage}\n  python: {}", step["expect"]["usage"]));
        }
        let events = observer.activity_bag(&path, &mut bag).unwrap_or_else(|e| panic!("{name} step {n}: activity {e}"));
        let activity = norm(serde_json::to_value(&events).unwrap(), &root);
        if activity != step["expect"]["activity"] {
            failures.push(format!("{name} step {n} activity\n  rust:   {activity}\n  python: {}", step["expect"]["activity"]));
        }
        bag.sort();
        let got = norm(serde_json::to_value(&bag).unwrap(), &root);
        if got != step["expect"]["bag"] {
            failures.push(format!("{name} step {n} seen-keys bag\n  rust:   {got}\n  python: {}", step["expect"]["bag"]));
        }
    }
    // `null`: a recorded divergence (see the scenario's comment in the Python test).
    if scenario["turns"].is_null() {
        return failures;
    }
    let turns = norm(serde_json::to_value(observer.turns(&path, "").unwrap()).unwrap(), &root);
    if turns != scenario["turns"] {
        failures.push(format!("{name} turns\n  rust:   {turns}\n  python: {}", scenario["turns"]));
    }
    failures
}

#[test]
fn every_fixture_matches_mains_readers() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("parity");
    let mut fixtures: Vec<PathBuf> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().path()).filter(|p| p.extension().is_some_and(|e| e == "json")).collect();
    fixtures.sort();
    assert!(!fixtures.is_empty(), "no parity fixtures in {}", dir.display());
    let failures: Vec<String> = fixtures.iter().flat_map(|f| replay(f)).collect();
    assert!(failures.is_empty(), "{} mismatch(es):\n{}", failures.len(), failures.join("\n"));
}
