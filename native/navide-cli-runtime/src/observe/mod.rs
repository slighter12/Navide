//! Observe: read a CLI's records and turn them into main's three outputs —
//! token usage (with a resumable checkpoint), activity events (with a
//! resumable walk state) and per-turn usage. Everything here is generic; a
//! vendor contributes only its `Observe` declaration.
pub mod activity;
pub mod bag;
pub mod jsonl;
pub mod sqlite;
pub mod turns;
pub mod usage;

use crate::decl::{Cli, Observe, Slot, SlotInit};
use crate::expr::{self, py_str, py_strip, truthy, Scope, Slots};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub use activity::{ActivityState, TurnState};

/// Slot names the core itself reads.
pub const CWD: &str = "cwd";
pub const SESSION: &str = "session_id";
/// Set by the core for a line walk that starts past line 0 (main's readers
/// test `high_water > 0` for "the header was read on an earlier poll").
pub const WALK_RESUMED: &str = "_walk_resumed";

#[derive(Debug)]
pub enum Error {
    Io(std::io::Error),
    /// A line that is not UTF-8 ended the walk (main raises there too).
    Decode { line: usize },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Io(err) => write!(f, "io: {err}"),
            Error::Decode { line } => write!(f, "line {line} is not UTF-8"),
        }
    }
}

impl From<std::io::Error> for Error {
    fn from(err: std::io::Error) -> Self {
        Error::Io(err)
    }
}

/// main's `TokenUsage`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct UsageEvent {
    pub vendor: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cwd: String,
    pub session_id: String,
    pub file_path: String,
    pub dedup_key: String,
    pub timestamp: String,
    pub model: String,
    pub checkpoint: Map<String, Value>,
    pub raw: Map<String, Value>,
    pub cache_read_tokens: i64,
    pub cache_creation_tokens: i64,
    pub cli_version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageRead {
    pub events: Vec<UsageEvent>,
    pub checkpoint: Map<String, Value>,
}

/// main's `ActivityEvent`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ActivityEvent {
    pub vendor: String,
    pub event_type: String,
    pub cwd: String,
    pub session_id: String,
    pub file_path: String,
    pub dedup_key: String,
    pub timestamp: String,
    pub detail: String,
    pub text: String,
    /// `PromptText.full` of a user-prompt `text` (the chat mirror's longer copy).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub full: String,
}

/// main's `TurnCall`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TurnCall {
    pub ts: Option<String>,
    pub model: String,
    pub input: i64,
    pub cache_read: i64,
    pub cache_creation: i64,
    pub output: i64,
    pub cli_version: String,
}

/// main's `TurnUsage` (without `profile_id`, which a reader never sets).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TurnUsage {
    pub turn_index: usize,
    pub session_id: String,
    pub started_at: Option<String>,
    pub ended_at: Option<String>,
    pub prompt_excerpt: String,
    pub input: i64,
    pub cache_read: i64,
    pub cache_creation: i64,
    pub output: i64,
    pub calls: Vec<TurnCall>,
    pub model_calls: Option<i64>,
    pub cli_version: String,
}

impl TurnUsage {
    pub(crate) fn new(session_id: String, started_at: Option<String>) -> Self {
        TurnUsage {
            turn_index: 0,
            session_id,
            started_at,
            ended_at: None,
            prompt_excerpt: String::new(),
            input: 0,
            cache_read: 0,
            cache_creation: 0,
            output: 0,
            calls: Vec::new(),
            model_calls: None,
            cli_version: String::new(),
        }
    }

    pub fn total(&self) -> i64 {
        self.input + self.cache_read + self.cache_creation + self.output
    }

    pub(crate) fn add_call(&mut self, call: TurnCall) {
        self.input += call.input;
        self.cache_read += call.cache_read;
        self.cache_creation += call.cache_creation;
        self.output += call.output;
        if !call.cli_version.is_empty() {
            self.cli_version = call.cli_version.clone();
        }
        self.calls.push(call);
    }
}

/// One CLI's observer. Holds what main's readers keep in memory between
/// calls (where each file's last activity walk stopped).
pub struct Observer {
    pub cli: &'static Cli,
    spec: &'static Observe,
    resume: HashMap<PathBuf, jsonl::LineResume>,
    clock: Option<f64>,
}

impl Observer {
    /// `None` when the CLI declares no observable records.
    pub fn new(cli: &'static Cli) -> Option<Self> {
        Some(Observer { cli, spec: cli.observe.as_ref()?, resume: HashMap::new(), clock: None })
    }

    /// Pin "now" (seconds since the epoch) for idle flushes; `None` = the
    /// system clock. The backend passes its own clock so both readers agree.
    pub fn set_clock(&mut self, now: Option<f64>) {
        self.clock = now;
    }

    fn now(&self) -> f64 {
        self.clock.unwrap_or_else(|| {
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
        })
    }

    fn accepts(&self, path: &Path) -> bool {
        self.spec.accept.as_ref().is_none_or(|accept| {
            path.file_name().and_then(|n| n.to_str()) == Some(accept.name)
                && accept.not_under.is_none_or(|(up, dir)| nth_dir(path, up).map(file_name).as_deref() != Some(dir))
        })
    }

    /// The file's slots before any record: the shared ones, shadowed by the
    /// section's own.
    fn slots(&self, path: &Path, section: &'static [Slot]) -> Slots {
        let mut slots = Slots::new();
        for slot in self.spec.slots.iter().filter(|s| !section.iter().any(|o| o.name == s.name)).chain(section) {
            slots.insert(slot.name.to_owned(), init(&slot.init, path));
        }
        slots
    }

    pub fn usage(&mut self, path: &Path, checkpoint: &Map<String, Value>) -> Result<UsageRead, Error> {
        let Some(spec) = self.spec.usage.as_ref() else {
            return Ok(UsageRead { events: Vec::new(), checkpoint: checkpoint.clone() });
        };
        if !self.accepts(path) {
            return Ok(UsageRead { events: Vec::new(), checkpoint: checkpoint.clone() });
        }
        let slots = self.slots(path, spec.slots);
        usage::read(spec, &self.spec.format, self.cli.key, path, slots, checkpoint, false)
    }

    pub fn activity(&mut self, path: &Path, state: &mut ActivityState) -> Result<Vec<ActivityEvent>, Error> {
        if !self.accepts(path) {
            return Ok(Vec::new());
        }
        let spec = &self.spec.activity;
        let slots = self.slots(path, spec.slots);
        let resume = self.resume.get(path).copied();
        let now = self.now();
        let (events, walked) = activity::walk(spec, &self.spec.format, self.cli.key, path, slots, state, resume, now)?;
        match walked {
            Some(resume) => self.resume.insert(path.to_owned(), resume),
            None => self.resume.remove(path),
        };
        Ok(events)
    }

    /// `activity` over the watcher's seen-keys bag: the state is read from it
    /// and written back to it (also when a decode error ends the walk, as
    /// main's `finally` does; not when the file cannot be read).
    pub fn activity_bag(&mut self, path: &Path, bag: &mut Vec<String>) -> Result<Vec<ActivityEvent>, Error> {
        let spec = &self.spec.activity;
        let mut state = bag::load(spec, bag);
        let result = self.activity(path, &mut state);
        if !matches!(result, Err(Error::Io(_))) {
            bag::store(spec, &state, bag);
        }
        result
    }

    pub fn turns(&self, path: &Path, session_id: &str) -> Result<Vec<TurnUsage>, Error> {
        if !self.accepts(path) {
            return Ok(Vec::new());
        }
        let spec = &self.spec.turns;
        match spec.method {
            crate::decl::TurnMethod::Unsupported => Ok(Vec::new()),
            crate::decl::TurnMethod::Exact => turns::exact(spec, path, self.slots(path, spec.slots), session_id),
            crate::decl::TurnMethod::Inferred => {
                let usage = match self.spec.usage.as_ref() {
                    Some(u) => usage::read(u, &self.spec.format, self.cli.key, path, self.slots(path, u.slots), &Map::new(), true)?.events,
                    None => Vec::new(),
                };
                let activity_spec = &self.spec.activity;
                let mut fresh = ActivityState::default();
                let slots = self.slots(path, activity_spec.slots);
                let (activity, _) = activity::walk(activity_spec, &self.spec.format, self.cli.key, path, slots, &mut fresh, None, self.now())?;
                Ok(turns::inferred(&usage, &activity, session_id))
            }
        }
    }
}

fn file_name(path: &Path) -> String {
    path.file_name().and_then(|n| n.to_str()).unwrap_or_default().to_owned()
}

fn nth_dir(path: &Path, n: usize) -> Option<&Path> {
    path.ancestors().nth(n)
}

/// `urllib.parse.unquote`: `%XX` escapes decoded as UTF-8, invalid bytes replaced.
fn unquote(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        let hex = |c: u8| (c as char).to_digit(16);
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex(b[i + 1]), hex(b[i + 2])) {
                out.push((h * 16 + l) as u8);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn init(init_spec: &SlotInit, path: &Path) -> String {
    match init_spec {
        SlotInit::Empty => String::new(),
        SlotInit::FileStem => path.file_stem().and_then(|s| s.to_str()).unwrap_or_default().to_owned(),
        SlotInit::DirName(n) => nth_dir(path, *n).map(file_name).unwrap_or_default(),
        SlotInit::StemAfterUnderscore => {
            let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or_default();
            stem.split_once('_').map(|(_, rest)| rest.to_owned()).unwrap_or_default()
        }
        SlotInit::Header { lines, when, field, first_line } => {
            let empty = Slots::new();
            let mut found = String::new();
            let _ = jsonl::all_lines(path, *lines, |line, stripped, value| {
                if stripped.is_empty() {
                    return true;
                }
                match value.filter(|v| v.is_object()) {
                    Some(record) => {
                        let scope = Scope { record, line, offset: 0, slots: &empty, cancelled: false, entry_key: "" };
                        if expr::pred(when, &scope) {
                            found = expr::text(field, &scope);
                            return false;
                        }
                        !first_line
                    }
                    None => !first_line,
                }
            });
            found
        }
        SlotInit::SidecarJson { up, file, keys } => nth_dir(path, *up)
            .and_then(|dir| std::fs::read_to_string(dir.join(file)).ok())
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .and_then(|value| {
                let object = value.as_object()?;
                keys.iter().filter_map(|k| object.get(*k)).find(|v| truthy(v)).map(py_str)
            })
            .unwrap_or_default(),
        SlotInit::GroupDir { up, override_file } => {
            let Some(dir) = nth_dir(path, *up) else { return String::new() };
            let marker = dir.join(override_file);
            if marker.is_file() {
                if let Ok(text) = std::fs::read_to_string(&marker) {
                    return py_strip(&text).to_owned();
                }
            }
            unquote(&file_name(dir))
        }
        SlotInit::DashDir => {
            let name = nth_dir(path, 1).map(file_name).unwrap_or_default();
            if name.starts_with('-') { name.replace('-', "/") } else { String::new() }
        }
        SlotInit::FirstOf(options) => options.iter().map(|o| init(o, path)).find(|v| !v.is_empty()).unwrap_or_default(),
    }
}

/// The observer for every declared CLI that has one, by key.
pub fn observers() -> HashMap<&'static str, Observer> {
    crate::vendors::ALL.iter().filter_map(|cli| Some((cli.key, Observer::new(cli)?))).collect()
}

#[cfg(test)]
mod tests {
    use super::unquote;

    #[test]
    fn unquote_matches_urllib() {
        assert_eq!(unquote("%2FUsers%2Fme%2Fa%20b"), "/Users/me/a b");
        assert_eq!(unquote("100%"), "100%");
        assert_eq!(unquote("%zz%e4%b8%ad"), "%zz中");
    }
}
