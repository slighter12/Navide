//! Test support for the vendor parity tests: a store built from a recorded vendor schema, and the core driven over it through
//! the passes a golden recorded from the retired baseline observer.
use super::adapters;
use super::sqlite::testing::Fixture;
use serde_json::{json, Value};
use std::io;

/// The statements of the schema recorded off the real vendor: `phases.initial[*].schema` of a
/// `tests/fixtures/cli-regression/<vendor>.json` document, split into single statements.
pub fn recorded_schema(document: &str) -> Vec<String> {
    let document: Value = serde_json::from_str(document).unwrap();
    let schema = document["phases"]["initial"].as_array().unwrap().iter().find_map(|phase| phase["schema"].as_str()).unwrap();
    schema.split(';').map(str::trim).filter(|statement| !statement.is_empty()).map(str::to_owned).collect()
}

/// An empty store file with the recorded schema.
pub fn store(file: &str, document: &str) -> Fixture {
    let store = Fixture::bare(file);
    for statement in recorded_schema(document) {
        store.sql(&statement, &[]);
    }
    store
}

/// A request over `store`, with the wire fields every vendor shares and the vendor's own `extra` ones.
pub fn meta(path: &std::path::Path, mode: &str, seen: &[String], checkpoint: &Value, state: &Value, extra: Value) -> Value {
    let mut meta = json!({"path": path, "mode": mode, "seen": seen, "checkpoint": checkpoint, "state": state});
    meta.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
    meta
}

/// The observation by the core (the generated dispatch of the vendor's declaration).
pub fn core(vendor: &str, meta: &Value) -> io::Result<Value> {
    adapters::dispatch(adapters::find(vendor).unwrap(), meta)
}

// ---- complete goldens -------------------------------------------------------------------------------------------------
//
// A golden is the retired baseline observer's COMPLETE output document for every pass of a scenario: every event field, dedup
// key, checkpoint field and seen bag, as the observer wrote them (plus the raw anchor bytes a JSONL observer handed back beside
// its document). The golden is recorded and pinned by the blob hash of the source it came from; nothing regenerates it. The
// core's answer to the same pass is compared with the whole document after the test's own named transforms; a transform is an
// explicit, documented step in the test, never a projection that drops fields.

/// What one pass answered.
pub struct Answered {
    pub document: Value,
    /// The typed state the request carried into the pass (what the pass before it answered): scenario input, which the shared
    /// differences read where a legacy answer field only said what the pass changed.
    pub state_before: Value,
    /// The file the pass read, as it stood when the pass ran (`None` where it was not there): what the core's `identity` must
    /// say, and the bound of the resume fields it answers.
    pub file: Option<FileStat>,
    /// The request the pass was given (path, mode, bag, checkpoint, typed state and the scenario's own fields).
    pub asked: Value,
    /// A log's bytes as they stood when the pass ran (`None` for a store, or a log that was not there).
    pub bytes: Option<Vec<u8>>,
    /// What the scenario says of the adapter's log answers (see `LogExpect`).
    pub expect: LogExpect,
}

/// What a scenario over a log tells the shared shaping about its adapter's answers: the two things no declaration says.
#[derive(Clone, Debug, Default)]
pub struct LogExpect {
    /// The session id the log names for itself (`native_session_id`); empty for a log that names none.
    pub native_session: String,
    /// The resume fields the adapter keeps beside the four every log carries (`anchor`, `identity`, `kind`, `offset`): its
    /// `persist`. A replay of a log with cumulative usage adds the two running totals.
    pub resume_extras: Vec<&'static str>,
    /// The baseline answered the cursor of the read it made (the log's identity and a checkpoint, an empty one on each replayed event)
    /// where the core keeps its own state: a replay's is read by no caller, an activity pass's is the byte cursor the baseline kept in
    /// its own key of the bag, where the core keeps a position and a high-water mark. Left out of both documents: this vendor's named
    /// difference.
    pub baseline_cursor_replaced: bool,
    /// The legacy observer's activity document had neither `opened` nor `text_changed` (the flags `legacy_shape` spells for the vendors whose
    /// document had them).
    pub no_activity_flags: bool,
    /// Fields every usage event of the core carries that the legacy observer's event lacked, with the value the core gives them in this
    /// scenario (asserted, then removed): a vendor that names no model or version beside its counts answers them empty.
    pub event_extras: Vec<(&'static str, Value)>,
    /// The legacy observer kept no window of recent keys (a record's key is its event id or its place): the core's usage cursors keep their own
    /// (asserted, then removed from each event's checkpoint and the pass's).
    pub no_key_window: bool,
}

/// `dev`, `ino` and `size` of a file at one moment.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FileStat {
    pub dev: u64,
    pub ino: u64,
    pub size: u64,
}

impl FileStat {
    pub fn of(path: &std::path::Path) -> Option<Self> {
        use std::os::unix::fs::MetadataExt;
        std::fs::metadata(path).ok().map(|stat| Self { dev: stat.dev(), ino: stat.ino(), size: stat.len() })
    }

    pub fn identity(self) -> Value {
        json!({"dev": self.dev, "ino": self.ino, "size": self.size})
    }
}

/// Every string of `value` with each `(from, to)` swapped in turn.
fn swap(value: &Value, swaps: &[(String, String)]) -> Value {
    match value {
        Value::String(s) => Value::String(swaps.iter().filter(|(from, _)| !from.is_empty()).fold(s.clone(), |acc, (from, to)| acc.replace(from.as_str(), to))),
        Value::Array(items) => Value::Array(items.iter().map(|item| swap(item, swaps)).collect()),
        Value::Object(map) => Value::Object(map.iter().map(|(key, item)| (key.clone(), swap(item, swaps))).collect()),
        other => other.clone(),
    }
}

/// What differs per run, as `(real, placeholder)`: the directory that holds the scenario's files (`$ROOT`) and each file's
/// `dev:ino` (`$IDENTITY0`, `$IDENTITY1`, … in the order the files are named).
fn run_specifics(roots: &[&std::path::Path], files: &[&std::path::Path]) -> Vec<(String, String)> {
    use std::os::unix::fs::MetadataExt;
    let mut swaps: Vec<(String, String)> =
        roots.iter().enumerate().map(|(index, root)| (root.to_string_lossy().into_owned(), if index == 0 { "$ROOT".to_owned() } else { format!("$ROOT{index}") })).collect();
    for (index, file) in files.iter().enumerate() {
        let identity = std::fs::metadata(file).map(|stat| format!("{}:{}", stat.dev(), stat.ino())).unwrap_or_default();
        swaps.push((identity, format!("$IDENTITY{index}")));
    }
    // Longest first: a scenario's directories are numbered (`…-1`, `…-14`) and one name can be the start of another.
    swaps.sort_by_key(|(from, _)| std::cmp::Reverse(from.len()));
    swaps
}

/// Replaces what differs per run with a placeholder, so a golden is the same on every machine. A scenario over several
/// directories: the first is `$ROOT`, the others `$ROOT1`, `$ROOT2`, … in order.
pub fn mask_roots(value: &Value, roots: &[&std::path::Path], files: &[&std::path::Path]) -> Value {
    swap(value, &run_specifics(roots, files))
}

/// A scenario runs passes in order over one store and one request chain: `observe(label, mode, extra)` runs a pass and answers
/// its document. Each mode keeps its own checkpoint, seen bag and typed state from the pass before, as the Python side keeps a
/// separate bag per kind of read.
pub struct Chain<'a> {
    vendor: &'a str,
    carried: std::collections::BTreeMap<String, (Value, Vec<String>, Value)>,
    pub log: Vec<(String, String, Value, Answered)>,
    /// What every pass of this chain over a log is checked against (see `LogExpect`).
    pub expect: LogExpect,
}

impl<'a> Chain<'a> {
    pub fn new(vendor: &'a str) -> Self {
        Self { vendor, carried: Default::default(), log: Vec::new(), expect: LogExpect::default() }
    }

    /// Change the typed state the next pass of `mode` over `path` is given (how a scenario says "the log is another file" for
    /// a pass that resumes from its state, not from a checkpoint).
    pub fn edit_state(&mut self, path: &std::path::Path, mode: &str, edit: impl FnOnce(&mut Value)) {
        if let Some((_, _, state)) = self.carried.get_mut(&format!("{}:{mode}", path.to_string_lossy())) {
            edit(state);
        }
    }

    /// Change the checkpoint the next pass of `mode` over `path` is given: how a scenario says "the store was replaced" or
    /// "the row the cursor stood on is another row".
    pub fn edit_checkpoint(&mut self, path: &std::path::Path, mode: &str, edit: impl FnOnce(&mut Value)) {
        if let Some((checkpoint, _, _)) = self.carried.get_mut(&format!("{}:{mode}", path.to_string_lossy())) {
            edit(checkpoint);
        }
    }

    /// Put keys in the seen bag the next pass of `mode` over `path` is given (how a scenario says "this key was delivered
    /// before"); the bag the earlier passes carried stays.
    pub fn seed(&mut self, path: &std::path::Path, mode: &str, keys: &[&str]) {
        let entry = self.carried.entry(format!("{}:{mode}", path.to_string_lossy())).or_insert((json!({}), Vec::new(), Value::Null));
        entry.1.extend(keys.iter().map(|key| (*key).to_owned()));
    }

    /// Start the next pass of `mode` over `path` from the typed state Python migrates a bag the baseline observer wrote into
    /// before it calls the core.
    pub fn migrate(&mut self, path: &std::path::Path, mode: &str, typed_state: Value) {
        let entry = self.carried.entry(format!("{}:{mode}", path.to_string_lossy())).or_insert((json!({}), Vec::new(), Value::Null));
        entry.1 = Vec::new();
        entry.2 = typed_state;
    }

    /// Run one pass. `path` overrides the store the request names (a vendor with two layouts reads two files).
    pub fn pass(&mut self, label: &str, path: &std::path::Path, mode: &str, extra: Value) -> &Answered {
        let key = format!("{}:{mode}", path.to_string_lossy());
        let (checkpoint, seen_before, state) = self.carried.get(&key).cloned().unwrap_or((json!({}), Vec::new(), Value::Null));
        let request = meta(path, mode, &seen_before, &checkpoint, &state, extra.clone());
        let state_before = state.clone();
        let file = FileStat::of(path);
        // The log as it stood when the pass ran: a scenario appends to it (or rewrites it) afterwards, and the resume fields a
        // pass answers are proved against these bytes.
        let bytes = adapters::find(self.vendor)
            .and_then(|facts| facts.layout_for(path))
            .filter(|layout| is_log(layout.source))
            .and_then(|_| std::fs::read(path).ok());
        let answered = |document: Value| Answered {
            document,
            state_before: state_before.clone(),
            file,
            asked: request.clone(),
            bytes: bytes.clone(),
            expect: self.expect.clone(),
        };
        let answered = core(self.vendor, &request).map(&answered).unwrap_or_else(|error| answered(json!({"refused": error.to_string()})));
        // The caller's bag after the pass: the bag the pass was asked with and the keys it answered (the core answers only the keys
        // it delivered).
        let bag = bag_after(&seen_before, &answered.document);
        if answered.document["checkpoint"].is_object() {
            self.carried.insert(key, (answered.document["checkpoint"].clone(), bag, answered.document["state"].clone()));
        } else {
            self.carried.insert(key, (checkpoint, bag, answered.document["state"].clone()));
        }
        self.log.push((label.to_owned(), mode.to_owned(), extra, answered));
        &self.log.last().unwrap().3
    }
}

// ---- a declaration-driven log scenario -----------------------------------------------------------------------------------------
//
// A vendor whose observer is a JSONL log lists the passes a scenario has to run in its declaration (its layout's modes), and the
// shape of every scenario is the same: append some records, run every pass the layout lists, append more, run them again, leave
// a last line unterminated, rewrite what the cursor stood behind, replace the file. `Log` is that scenario: the adapter's test
// lists only its own records and its own differences, never the loop over modes, the chain, the file handling, the clock, the
// labels or the shaping of a log's answer (`legacy_shape` does that for every log, strictly).

/// The passes of a log, in the order a scenario runs them: usage (the cursor), the whole-log replay (`session_usage`, or `legacy`
/// for a vendor that names it so), activity, the turn breakdown and the identity lookup, each when the layout lists it.
const LOG_PASSES: [adapters::Mode; 6] =
    [adapters::Mode::Usage, adapters::Mode::SessionUsage, adapters::Mode::Legacy, adapters::Mode::Activity, adapters::Mode::Turns, adapters::Mode::Identify];

/// A scenario over one log of a vendor: the chain it runs on and the files it owns (or shares with a store the test also holds).
pub struct Log<'a> {
    pub chain: Chain<'a>,
    root: std::path::PathBuf,
    /// The temp directory this scenario made, when it was not given one to share.
    _owned: Option<Fixture>,
    log: std::path::PathBuf,
    passes: Vec<&'static str>,
    extra: Value,
}

/// Is this source a log (a JSONL file, or a text log read a line at a time by the same source) and not a row store?
fn is_log(source: adapters::SourceKind) -> bool {
    matches!(source, adapters::SourceKind::Jsonl | adapters::SourceKind::Text)
}

impl<'a> Log<'a> {
    /// A scenario over `relative` (a path under a temp directory the scenario owns), a file of `vendor`'s JSONL layout. `extra`
    /// is what Python supplies for it besides the path (`cwd`, `session`). The passes it runs are the ones the layout lists.
    pub fn new(vendor: &'a str, relative: &str, extra: Value) -> Self {
        let owned = Fixture::bare("unused.db");
        let root = owned.root.clone();
        let mut log = Self::beside(vendor, &root, relative, extra);
        log._owned = Some(owned);
        log
    }

    /// The same over a directory another fixture owns (a vendor whose store and logs live under one root).
    pub fn beside(vendor: &'a str, root: &std::path::Path, relative: &str, extra: Value) -> Self {
        let log = root.join(relative);
        let layout = adapters::find(vendor).unwrap().layout_for(&log).unwrap();
        assert!(is_log(layout.source), "{vendor}: `{}` is not a log", log.display());
        let passes = LOG_PASSES.iter().filter(|mode| layout.modes.contains(mode)).map(|mode| mode.as_str()).collect();
        Self { chain: Chain::new(vendor), root: root.to_owned(), _owned: None, log, passes, extra }
    }

    /// What the shared shaping holds the adapter's log answers to (`LogExpect`).
    pub fn expects(&mut self, native_session: &str, resume_extras: &[&'static str]) -> &mut Self {
        self.chain.expect = LogExpect { native_session: native_session.to_owned(), resume_extras: resume_extras.to_vec(), ..LogExpect::default() };
        self
    }

    /// The legacy activity document had no `opened` and no `text_changed` (`LogExpect::no_activity_flags`).
    pub fn legacy_lacks_activity_flags(&mut self) -> &mut Self {
        self.chain.expect.no_activity_flags = true;
        self
    }

    /// Fields the core's usage events carry that the legacy events lacked, and their value here (`LogExpect::event_extras`).
    pub fn core_adds_to_events(&mut self, extras: &[(&'static str, Value)]) -> &mut Self {
        self.chain.expect.event_extras = extras.to_vec();
        self
    }

    /// The legacy observer kept no window of recent keys (`LogExpect::no_key_window`).
    pub fn legacy_kept_no_key_window(&mut self) -> &mut Self {
        self.chain.expect.no_key_window = true;
        self
    }

    /// The baseline answered a cursor of its own where the core keeps another state (`LogExpect::baseline_cursor_replaced`).
    pub fn baseline_cursor_replaced(&mut self) -> &mut Self {
        self.chain.expect.baseline_cursor_replaced = true;
        self
    }

    /// A mode the layout lists that the legacy observer never answered, so the golden holds none of its passes (Grok's `turns`, added on the
    /// core: the Python reader answered it, and it is the oracle of `backend/tests/test_cli_runtime_grok_turns.py` and the matrix cell). The
    /// scenario does not run it; the golden stays as the legacy observer left it.
    pub fn beyond_golden(&mut self, mode: adapters::Mode) -> &mut Self {
        self.passes.retain(|pass| *pass != mode.as_str());
        self
    }

    /// What Python supplies from now on for `field` (the wall clock a layout reads, a working directory that changed).
    pub fn supply(&mut self, field: &str, value: Value) -> &mut Self {
        self.extra.as_object_mut().unwrap().insert(field.into(), value);
        self
    }

    /// The directory the scenario's files are under (`$ROOT` in a golden).
    pub fn root(&self) -> &std::path::Path {
        &self.root
    }

    /// The log itself.
    pub fn path(&self) -> &std::path::Path {
        &self.log
    }

    /// Append records, one line each.
    pub fn append(&mut self, records: &[Value]) -> &mut Self {
        for record in records {
            self.append_text(&format!("{record}\n"));
        }
        self
    }

    /// Append text as it is (a line that is no JSON, or one still being written: no terminating newline).
    pub fn append_text(&mut self, text: &str) -> &mut Self {
        use std::io::Write;
        std::fs::create_dir_all(self.log.parent().unwrap()).unwrap();
        std::fs::OpenOptions::new().create(true).append(true).open(&self.log).unwrap().write_all(text.as_bytes()).unwrap();
        self
    }

    /// Run one pass over the log as it stands.
    pub fn pass(&mut self, label: &str, mode: &str) -> &mut Self {
        self.chain.pass(label, &self.log.clone(), mode, self.extra.clone());
        self
    }

    /// Run every pass the layout lists, once, over the log as it stands.
    pub fn passes(&mut self, label: &str) -> &mut Self {
        for mode in self.passes.clone() {
            self.pass(&format!("{label}: {mode}"), mode);
        }
        self
    }

    /// The log is another file than the one the cursor stood on (a usage cursor and an activity position both name the old one),
    /// then every pass again.
    pub fn replaced(&mut self, label: &str) -> &mut Self {
        let log = self.log.clone();
        self.chain.edit_checkpoint(&log, "usage", |checkpoint| checkpoint["identity"] = json!("1:1"));
        self.chain.edit_state(&log, "activity", |state| {
            if state["position"].is_object() {
                state["position"]["identity"] = json!("1:1");
            }
        });
        self.passes(label)
    }

    /// The bytes the cursor stands behind are not the ones it saw: `from` becomes `to` (the same length, so the file is as long
    /// as it was), then every pass again.
    pub fn rewritten(&mut self, label: &str, from: &str, to: &str) -> &mut Self {
        assert_eq!(from.len(), to.len(), "a rewrite keeps the file as long as it was");
        let text = std::fs::read_to_string(&self.log).unwrap();
        assert!(text.contains(from), "`{from}` is in the log");
        std::fs::write(&self.log, text.replacen(from, to, 1)).unwrap();
        self.passes(label)
    }

    /// The files whose `dev:ino` the golden masks, behind the ones the test names first (a store before its log).
    pub fn files<'f>(&'f self, before: &[&'f std::path::Path]) -> Vec<&'f std::path::Path> {
        before.iter().copied().chain(std::iter::once(self.log.as_path())).collect()
    }
}

/// `check_golden` over a log scenario: its root, its files (behind the ones named first) and the adapter's own differences.
pub fn check_log_golden(vendor: &str, log: &Log, before: &[&std::path::Path], own: impl Fn(&Answered, &str, &mut serde_json::Map<String, Value>, &Value)) {
    check_golden(vendor, &log.chain, &[log.root()], &log.files(before), own);
}

/// The recorded golden of `vendor`.
pub fn golden_file(vendor: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/agent-cli-observe-goldens").join(format!("{vendor}.json"))
}

// ---- the shared named differences --------------------------------------------------------------------------------------------
//
// The core's answer is turned into the legacy document's shape by `legacy_shape`, driven by the vendor's declaration (which
// layout answered, which modes it lists, which legacy bag keys its typed state replaced). The differences every vendor shares are
// held here once; a vendor passes only the ones that are its own. No step reads the expected document: each is a function of the
// core's answer, the typed state the pass was given (scenario input) and the declaration.

/// One family of bag keys the typed state replaced: an entry of `observer.core.legacy_state` in `shared/agent-cli/<vendor>.json`.
struct Spelling {
    prefix: String,
    field: String,
    kind: String,
}

fn declared_spellings(vendor: &str) -> Vec<Spelling> {
    let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../shared/agent-cli").join(format!("{vendor}.json"));
    let declaration: Value = serde_json::from_str(&std::fs::read_to_string(file).unwrap()).unwrap();
    declaration["observer"]["core"]["legacy_state"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| Spelling { prefix: entry["prefix"].as_str().unwrap().to_owned(), field: entry["field"].as_str().unwrap().to_owned(), kind: entry["kind"].as_str().unwrap().to_owned() })
        .collect()
}

/// The stamp floors a vendor's walks may keep, as dotted paths (`marks.<table>.stamped`): one per declared walk position
/// (`marks.<table>.last`), and only where a layout asks for the store's clock (`request` has `base_epoch`).
fn declared_stamp_floors(vendor: &str) -> Vec<String> {
    let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../shared/agent-cli").join(format!("{vendor}.json"));
    let declaration: Value = serde_json::from_str(&std::fs::read_to_string(file).unwrap()).unwrap();
    let clocked = declaration["observer"]["layouts"].as_array().unwrap().iter().any(|layout| layout["request"].as_array().into_iter().flatten().any(|field| field == "base_epoch"));
    let walks = declared_spellings(vendor).into_iter().filter_map(|entry| entry.field.strip_suffix(".last").filter(|table| table.starts_with("marks.")).map(|table| format!("{table}.stamped")));
    if clocked { walks.collect() } else { Vec::new() }
}

/// The typed state at a dotted field (`marks.steps.last`); `Null` where the state holds nothing there.
fn at<'a>(state: &'a Value, field: &str) -> &'a Value {
    field.split('.').fold(state, |node, step| &node[step])
}

/// The legacy bag key the typed state replaced for one declared entry, spelled as the legacy observer spelled it. A pending
/// set the core dropped rows from (`dropped`: `(table, id)`) is spelled with them: the legacy observer kept its sentinel for good.
fn spell(entry: &Spelling, state: &Value, dropped: &[(String, i64)]) -> Option<String> {
    let value = at(state, &entry.field);
    match entry.kind.as_str() {
        "int" | "signed" => value.as_i64().map(|number| format!("{}{number}", entry.prefix)),
        "text" => value.as_str().filter(|text| !text.is_empty()).map(|text| format!("{}{text}", entry.prefix)),
        "pair" => value.as_object().map(|totals| format!("{}in={},out={}", entry.prefix, totals["input"], totals["output"])),
        "colon_pair" => value.as_object().map(|totals| format!("{}{}:{}", entry.prefix, totals["input"], totals["output"])),
        // The member order is the core's (sorted); the legacy spelling's insertion order is a named difference of the vendor's test.
        "json" => value.as_object().filter(|members| !members.is_empty()).map(|members| format!("{}{}", entry.prefix, serde_json::to_string(members).unwrap())),
        "json_text" => value.as_str().filter(|text| !text.is_empty()).map(|text| format!("{}{}", entry.prefix, serde_json::to_string(text).unwrap())),
        // The Python tail reader's cursor in its own spelling (`json.dumps(.., sort_keys=True)`: sorted members, `", "` and `": "` separators).
        "byte_cursor" => value.as_object().map(|cursor| {
            format!("{}{{\"anchor\": {}, \"identity\": {}, \"kind\": \"jsonl\", \"offset\": {}}}", entry.prefix, cursor["anchor"], cursor["identity"], cursor["offset"])
        }),
        "set" => {
            let table = entry.field.split('.').nth(1).unwrap_or_default();
            let mut ids: Vec<i64> = value.as_array().into_iter().flatten().filter_map(Value::as_i64).chain(dropped.iter().filter(|(gone, _)| gone == table).map(|(_, id)| *id)).collect();
            ids.sort_unstable();
            (!ids.is_empty()).then(|| format!("{}[{}]", entry.prefix, ids.iter().map(i64::to_string).collect::<Vec<_>>().join(", ")))
        }
        other => panic!("the declaration names a legacy state kind `{other}` this driver does not spell"),
    }
}

/// Every leaf of the typed state (an object's members, recursively; a scalar or an array is one leaf) is consumed: named by a
/// declared spelling (the field itself, or a path above it) or by one of the named exceptions below, each asserted where it is
/// not spelled, so a typed field is never silently dropped.
/// - `adapter`: what the vendor's own `Observer::State` keeps. These four parsers keep none (`()`), so it is null.
/// - `position`: a log's resume position (kind, offset, identity, anchor and the rest). The legacy observer answered no such field
///   and Python's `parse_session_file` and `parse_activity` read only `seen`, `state` and the events of an answer
///   (`test_cli_runtime_native_reads_only_the_answers_bag_and_events.py` pins that), so the vendor's test asserts its content where
///   it removes it. A store has none (null).
/// - `water` and `last_text`: the line mark and the open turn's text, which the legacy observer answered as fields of a LOG's
///   activity pass (`high_water`, `last_text`: consumed there). Any other pass carries them unchanged.
/// - `marks.<table>.pending`: the unfinished-row list the legacy observers had no notion of; empty (the documented resume contract).
/// - `marks.<table>.stamped` (only a declared walk of a layout that asks for the store's clock, `declared_stamp_floors`): the last stamp a
///   walk gave an event, for a store that holds no time (Cursor's). The legacy observer restarted
///   its 1 ms offset at every call; the core keeps a floor so a stamp is never at or before one already given (t36k V1, a named difference).
fn assert_state_consumed(state: &Value, before: &Value, spellings: &[Spelling], stamp_floors: &[String], log_activity: bool, store: bool) {
    fn leaves(path: &str, node: &Value, out: &mut Vec<String>) {
        match node.as_object() {
            Some(members) if !members.is_empty() => members.iter().for_each(|(name, member)| leaves(&if path.is_empty() { name.clone() } else { format!("{path}.{name}") }, member, out)),
            _ => out.push(path.to_owned()),
        }
    }
    let mut paths = Vec::new();
    leaves("", state, &mut paths);
    for path in paths.into_iter().filter(|path| !path.is_empty() && path != "marks") {
        let at_path = |root: &Value| path.split('.').fold(root, |node, step| &node[step]).clone();
        if spellings.iter().any(|entry| path == entry.field || path.starts_with(&format!("{}.", entry.field))) {
            continue;
        }
        match path.as_str() {
            "adapter" | "totals" => assert_eq!(at_path(state), Value::Null, "`{path}`: these parsers keep none that a spelling does not name"),
            "position" => assert!(!store || at_path(state).is_null(), "a store has no resume position: {state}"),
            // The members of a log's position are asserted once, whole, below.
            other if other.starts_with("position.") => {}
            "water" | "last_text" if log_activity => {}
            "water" | "last_text" => {
                let default = if path == "water" { json!(0) } else { json!("") };
                let was = at_path(before);
                assert_eq!(at_path(state), if was.is_null() { default } else { was }, "a pass that is no log's activity carries `{path}` unchanged");
            }
            other if other.starts_with("marks.") && other.ends_with(".pending") => assert_eq!(at_path(state), json!([]), "`{other}`: no unfinished marks in the contract"),
            other if stamp_floors.iter().any(|floor| floor == other) => assert!(at_path(state).is_i64(), "`{other}`: the last stamp a walk gave, in microseconds"),
            other => panic!("the typed state's `{other}` is consumed by no declared spelling and is no named exception: {state}"),
        }
    }
}

/// The bytes a log's anchor covers (the core's `ANCHOR_BYTES`) and its position's fingerprint covers (`FINGERPRINT_BYTES`).
const ANCHOR_BYTES: usize = 512;
const FINGERPRINT_BYTES: usize = 32;

/// The anchor the core spells for the bytes that end at `offset`: blake2b-8 in hex of the (up to) 512 bytes before it; empty at 0.
fn anchor_of(bytes: &[u8], offset: usize) -> String {
    if offset == 0 {
        return String::new();
    }
    crate::observe::blake2b::hex_digest(&bytes[offset.saturating_sub(ANCHOR_BYTES)..offset], 8)
}

/// The fingerprint of a position: the hex of the (up to) 32 bytes before `offset`.
fn fingerprint_of(bytes: &[u8], offset: usize) -> String {
    bytes[offset.saturating_sub(FINGERPRINT_BYTES)..offset].iter().map(|byte| format!("{byte:02x}")).collect()
}

/// A log's `dev:ino` as the core spells it in a resume field.
fn text_identity(file: FileStat) -> Value {
    json!(format!("{}:{}", file.dev, file.ino))
}

/// An offset a resume field names, against the log as it stood at the pass: inside it, and just past a line the log had terminated.
/// `open_tail`: a whole-log replay reads a last line nobody terminated yet, so its offset may stand at the end of the log; a
/// resumable pass (a usage cursor, an activity position) stops before such a line.
fn offset_in(resume: &Value, bytes: &[u8], file: FileStat, open_tail: bool) -> usize {
    let offset = resume["offset"].as_u64().unwrap_or_else(|| panic!("an offset: {resume}")) as usize;
    assert!(offset as u64 <= file.size && offset <= bytes.len(), "the offset is inside the log: {resume}");
    assert!(offset == 0 || bytes[offset - 1] == b'\n' || (open_tail && offset == bytes.len()), "the offset stands just past a terminated line: {resume}");
    offset
}

/// What a log's cursor fields (`identity`, `offset`, `anchor`) say against the log itself as it stood at the pass: the file's
/// `dev:ino`, an offset just past a terminated line, and the anchor that is the digest of the bytes behind that offset.
pub fn assert_cursor_is_the_logs(cursor: &Value, bytes: &[u8], file: FileStat, open_tail: bool) {
    assert_eq!(cursor["identity"], text_identity(file), "the cursor names the log it was read from: {cursor}");
    let offset = offset_in(cursor, bytes, file, open_tail);
    let anchor = cursor["anchor"].as_str().unwrap_or_else(|| panic!("an anchor: {cursor}"));
    assert_anchor_form(anchor);
    assert_eq!(anchor, anchor_of(bytes, offset), "the anchor digests the bytes behind the offset: {cursor}");
}

/// What a log's typed resume position says, against the log itself: exactly its four members, the identity, an offset just past a
/// terminated line, the fingerprint (the hex of the bytes behind the offset) and the count of lines before the offset.
fn assert_position_is_the_logs(position: &Value, bytes: &[u8], file: FileStat) {
    let mut members: Vec<&str> = position.as_object().expect("a position is an object").keys().map(String::as_str).collect();
    members.sort_unstable();
    assert_eq!(members, ["fingerprint", "identity", "line", "offset"], "a position names exactly these members: {position}");
    assert_eq!(position["identity"], text_identity(file), "the position names the log it was read from: {position}");
    let offset = offset_in(position, bytes, file, false);
    assert_eq!(position["fingerprint"], json!(fingerprint_of(bytes, offset)), "the fingerprint is the hex of the bytes before the offset");
    assert_eq!(position["line"], json!(bytes[..offset].iter().filter(|byte| **byte == b'\n').count()), "the line count is the lines before the offset");
}

/// The shared shaping of a LOG's answer (`legacy_shape` calls it under `layout.source == Jsonl`), each difference named and
/// asserted where it is removed:
/// - `native_session_id` and `cwd`: the core's JSONL answer names the log's own session and working directory for the reader; the
///   legacy observer's document had neither. The session is the scenario's (`LogExpect`), the working directory the request's;
/// - a usage pass's cursor and each event's checkpoint are proved against the log as it stood at the pass (`assert_cursor_is_the_logs`:
///   identity, offset, and the anchor as the digest of the bytes behind that event's offset), then the anchors are blanked: the
///   core hashes the anchor itself, the legacy observer left the digest to Python (the golden holds it empty; the digest is also
///   checked against the legacy bytes in `check_golden`);
/// - an activity or replay pass answered the checkpoint it was given; the core answers the resume fields of the log too. Python
///   never reads them from these two passes (`test_cli_runtime_native_reads_only_the_answers_bag_and_events.py`), so they are not
///   carried into the golden's document; they are asserted whole first, bound to the mode: the four every log carries and the
///   adapter's (`LogExpect::resume_extras`), plus the two running totals in a replay of cumulative usage;
/// - a replay's events carried no checkpoint; the core's carry the log's resume fields, proved like a usage event's, then removed.
fn shape_log(object: &mut serde_json::Map<String, Value>, answered: &Answered, mode: &str, layout: &adapters::Layout) {
    let (expect, usage) = (&answered.expect, layout.usage);
    // The working directory is the request's where the layout requests it, else the log's own (a header names it).
    let requests_cwd = layout.request.contains(&adapters::RequestField::Cwd);
    let cwd_is_right = |cwd: &Value| if requests_cwd { Some(cwd) == answered.asked.get("cwd") } else { cwd.is_string() };
    if let Some(named) = object.remove("native_session_id") {
        assert_eq!(named, json!(expect.native_session), "the session id the log names for itself");
    }
    if let Some(cwd) = object.remove("cwd") {
        assert!(cwd_is_right(&cwd), "the log's working directory is the request's, or the log's own: {cwd}");
    }
    let (bytes, file) = (answered.bytes.as_deref().expect("the log was there"), answered.file.expect("the log was there"));
    let replay = mode == "session_usage" || mode == "legacy";
    let mut last = 0;
    for event in object.get_mut("events").and_then(Value::as_array_mut).into_iter().flatten() {
        let Some(checkpoint) = event.get("checkpoint").filter(|checkpoint| checkpoint.is_object()).cloned() else { continue };
        assert_cursor_is_the_logs(&checkpoint, bytes, file, replay);
        let offset = checkpoint["offset"].as_u64().unwrap();
        assert!(offset >= last, "the events' offsets never go back: {checkpoint}");
        last = offset;
        if replay {
            event.as_object_mut().unwrap().remove("checkpoint");
        } else {
            event["checkpoint"]["anchor"] = json!("");
        }
    }
    let checkpoint = object.get_mut("checkpoint").and_then(Value::as_object_mut).expect("a log answers a checkpoint");
    if mode == "usage" {
        assert_cursor_is_the_logs(&Value::Object(checkpoint.clone()), bytes, file, false);
        assert!(last <= checkpoint["offset"].as_u64().unwrap(), "no event stands past the cursor");
        checkpoint.insert("anchor".into(), json!(""));
        return;
    }
    let mut expected: Vec<&str> = ["anchor", "identity", "kind", "offset"].into_iter().chain(expect.resume_extras.iter().copied()).collect();
    if replay && usage == adapters::UsageMode::Cumulative {
        expected.extend(["input_total", "output_total"]);
    }
    expected.sort_unstable();
    let mut keys: Vec<&str> = checkpoint.keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(keys, expected, "the resume fields of a {mode} pass are exactly these");
    let resume = Value::Object(checkpoint.clone());
    assert_cursor_is_the_logs(&resume, bytes, file, replay);
    assert!(resume["kind"].as_str().is_some_and(|kind| !kind.is_empty()), "{resume}");
    for key in &expect.resume_extras {
        if *key == "cwd" {
            assert!(cwd_is_right(&resume["cwd"]), "the resume cwd is the request's, or the log's own: {resume}");
        } else {
            assert!(resume[*key].is_string(), "`{key}` is spelled: {resume}");
        }
    }
    assert!(["input_total", "output_total"].iter().all(|key| resume.get(*key).is_none_or(Value::is_u64)), "{resume}");
    checkpoint.clear();
}

/// What the scenario says the legacy observer's document lacked (`LogExpect::event_extras`, `no_key_window`), each removed from the core's
/// where it is asserted to be what the scenario names: the same removal every adapter that needs one would otherwise spell for itself.
fn legacy_lacks(object: &mut serde_json::Map<String, Value>, mode: &str, expect: &LogExpect) {
    if mode == "activity" {
        return;
    }
    for event in object.get_mut("events").and_then(Value::as_array_mut).into_iter().flatten() {
        for (field, value) in &expect.event_extras {
            assert_eq!(event.as_object_mut().unwrap().remove(*field).as_ref(), Some(value), "every usage event of the core names `{field}`");
        }
        if expect.no_key_window && mode == "usage" {
            assert!(event["checkpoint"].as_object_mut().unwrap().remove("recent_keys").is_some(), "an incremental event's cursor keeps its window of keys");
        }
    }
    if expect.no_key_window && mode == "usage" {
        assert!(object["checkpoint"].as_object_mut().unwrap().remove("recent_keys").is_some(), "the pass's cursor keeps its window of keys");
    }
}

/// A row cursor the legacy observer wrote without the unfinished-row list the core's pending rule keeps. The documented resume
/// contract is that the list is empty for a store that has no pending rows, so the removal is asserted, not assumed.
fn drop_empty_pending(checkpoint: &mut serde_json::Map<String, Value>) {
    if checkpoint.contains_key("row_id") {
        assert_eq!(checkpoint.remove("pending"), Some(json!([])), "a store without pending rows keeps an empty list");
    }
}

/// The digest form of an anchor (the documented resume contract): empty, or 16 hex digits.
pub fn assert_anchor_form(anchor: &str) {
    assert!(anchor.is_empty() || (anchor.len() == 16 && anchor.chars().all(|c| c.is_ascii_hexdigit())), "{anchor}");
}

/// The core hashes the log's anchor itself; the legacy observer handed its raw bytes back beside its document and Python hashed
/// them. Equal digests are the proof, checked against the recorded bytes (`raw_anchor_hex`), apart from any transform.
pub fn assert_anchor_digest(core: &Value, raw_anchor_hex: &str) {
    let raw: Vec<u8> = (0..raw_anchor_hex.len() / 2).map(|at| u8::from_str_radix(&raw_anchor_hex[at * 2..at * 2 + 2], 16).unwrap()).collect();
    let expected = if raw.is_empty() { String::new() } else { crate::observe::blake2b::hex_digest(&raw, 8) };
    assert_eq!(core["checkpoint"]["anchor"], json!(expected), "the anchor digests the same bytes");
}

/// The core's answer in the legacy document's shape: the differences every vendor shares, then the vendor's own
/// (`own`: the shaped document's object and the core's answer). A refusal is the same words in both observers.
///
/// The shared differences, each one named:
/// - the file's identity: the core names it `{dev, ino, size}`; the legacy observer wrote the text `dev:ino` for a store and only
///   in a `usage` pass of a layout that records usage (an answer that failed, or any other pass, named no file). The core names it
///   in every log mode, because `identify` needs it; it is still checked equal to the file's `dev:ino`, and refused for a store;
/// - the dedup keys: the core answers only the keys the pass delivered, and the caller adds them to its bag (`bag_after`); the
///   legacy observer answered the whole bag. The bag the caller holds after a pass is the same;
/// - a whole-log replay's resume offset may sit at a last line nobody terminated yet (`offset_in`'s `open_tail`): the replay reads
///   that line, as the whole-log read does. The consumers of the offset (`app.py:2262`, `tokens_store.py:1393-1397,1465-1468`)
///   resume a usage cursor, which stays bound to terminated lines;
/// - an over-budget record is delivered cut and reported (`truncated`): a reply's text, the open turn's text kept in the state
///   (`table: "last_text"`), and the core event fields that are not text (`timestamp`, `detail`, `cwd`, `session_id`,
///   `file_path`, `model`) capped to a fixed bound. The legacy observers delivered such a record whole, or refused it;
/// - the lookup fields `found` and `markers`: the core's answer carries them always, constant when the layout lists no such mode
///   (asserted constant before they are removed); the legacy observers of those vendors had none;
/// - a row cursor's unfinished-row list: empty for a store without pending rows (asserted), absent from the legacy cursor;
/// - `dropped_pending`: a pending row the store no longer holds is dropped by the core and reported; the legacy observer kept its
///   sentinel for good, so the dropped rows are spelled back into the bag;
/// - the typed state, spelled as the legacy bag keys it replaced (`core.legacy_state`); for the activity pass of a log, the line
///   mark and the open turn's text were answer fields (`high_water`, `last_text`) the legacy observer's caller wrote into its
///   bag, with `opened` (the log was there to open) and `text_changed` (the open turn's text differs from the pass before's).
pub fn legacy_shape(vendor: &str, answered: &Answered, mode: &str, own: impl FnOnce(&mut serde_json::Map<String, Value>, &Value)) -> Value {
    let core = &answered.document;
    let mut shaped = core.clone();
    if shaped.get("refused").is_some() {
        return shaped;
    }
    // The layout the answer says it read the file with (a file with one layout says nothing: it is the file's own).
    let facts = adapters::find(vendor).unwrap();
    let layout = core["layout"].as_u64().map_or_else(|| facts.layout_for(std::path::Path::new(core["path"].as_str().unwrap())).unwrap(), |slot| &facts.layouts[slot as usize]);
    let (store, log_activity) = (layout.source == adapters::SourceKind::Sqlite, is_log(layout.source) && mode == "activity");
    let object = shaped.as_object_mut().unwrap();
    let state = object.remove("state").unwrap_or(Value::Null);
    let identity = &core["identity"];
    // Asserted before it is dropped or respelled: the identity names the file the pass read, as it stood when the pass ran.
    // A log's usage document spells it as the text `dev:ino` (the form its checkpoint carries); every other answer as `{dev, ino, size}`.
    // The text form is a log's own spelling (the `dev:ino` its cursor carries, and what its `identify` pass answers); a store
    // names its file `{dev, ino, size}` in every pass. The log's answer is the text in EVERY mode the core answers it, which is
    // wider than the legacy document's usage-only form (that one is `named`, below).
    let is_log = is_log(layout.source);
    match (identity, answered.file) {
        (Value::Null, _) => {}
        (Value::String(text), Some(file)) if is_log => assert_eq!(text, &format!("{}:{}", file.dev, file.ino), "a log's identity is its `dev:ino` ({mode} of {})", core["path"]),
        (Value::String(text), _) => panic!("the text identity {text:?} is a log's form, never a store's ({mode} of {})", core["path"]),
        (named, Some(file)) => assert_eq!(named, &file.identity(), "the identity is the file's `{{dev, ino, size}}` at the pass ({mode} of {})", core["path"]),
        (named, None) => panic!("an identity {named} was named for a file that was not there"),
    }
    let named = mode == "usage" && !core["error"].is_string() && layout.usage != adapters::UsageMode::None;
    object.insert(
        "identity".into(),
        if !named {
            json!("")
        } else if store {
            json!(format!("{}:{}", identity["dev"], identity["ino"]))
        } else {
            identity.clone()
        },
    );
    for (field, mode_of, constant) in [("found", adapters::Mode::HasSession, json!(false)), ("markers", adapters::Mode::Identify, json!({}))] {
        if !layout.modes.contains(&mode_of) && object.contains_key(field) {
            assert_eq!(object.remove(field), Some(constant), "a layout that lists no such lookup answers `{field}` constant");
        }
    }
    if store {
        if let Some(checkpoint) = object.get_mut("checkpoint").and_then(Value::as_object_mut) {
            drop_empty_pending(checkpoint);
        }
        for event in object.get_mut("events").and_then(Value::as_array_mut).into_iter().flatten() {
            if let Some(checkpoint) = event.get_mut("checkpoint").and_then(Value::as_object_mut) {
                drop_empty_pending(checkpoint);
            }
        }
    }
    let dropped: Vec<(String, i64)> = object
        .remove("dropped_pending")
        .map(|gone| gone.as_array().unwrap().iter().map(|entry| (entry["table"].as_str().unwrap().to_owned(), entry["id"].as_i64().unwrap())).collect())
        .unwrap_or_default();
    let asked_with: Vec<String> = answered.asked["seen"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect();
    let mut bag = bag_after(&asked_with, core);
    for entry in declared_spellings(vendor) {
        // The line mark and the open turn's text were never bag keys of the legacy observer: its activity pass answered them as
        // fields (below) and its other passes carried neither.
        if !["water", "last_text"].contains(&entry.field.as_str()) {
            bag.extend(spell(&entry, &state, &dropped));
        }
    }
    bag.sort();
    object.insert("seen".into(), json!(bag));
    assert_state_consumed(&state, &answered.state_before, &declared_spellings(vendor), &declared_stamp_floors(vendor), log_activity, store);
    if let (Some(file), false) = (answered.file, state["position"].is_null()) {
        assert_position_is_the_logs(&state["position"], answered.bytes.as_deref().expect("a position names a log"), file);
    }
    if log_activity {
        let text = |value: &Value| value.as_str().unwrap_or_default().to_owned();
        object.insert("high_water".into(), state["water"].clone());
        object.insert("last_text".into(), state["last_text"].clone());
        if !answered.expect.no_activity_flags {
            // The log was there to open: the core named its identity.
            object.insert("opened".into(), json!(!identity.is_null()));
            // The open turn's text changed in this pass: a turn end consumed it (it resets to empty, which the states before and
            // after may not show when the turn began in the same pass), or the state's text differs from the state the pass began with.
            let turn_ended = core["events"].as_array().into_iter().flatten().any(|event| event["event_type"] == "turn_complete");
            object.insert("text_changed".into(), json!(turn_ended || text(&state["last_text"]) != text(&answered.state_before["last_text"])));
        }
    }
    if is_log {
        shape_log(object, answered, mode, layout);
        legacy_lacks(object, mode, &answered.expect);
    }
    own(object, core);
    shaped
}

/// The text of an error up to the statement it names: the codes are compared, the statement text is the core's own query.
fn error_code(document: &mut Value) {
    if let Some(error) = document.get("error").and_then(Value::as_str) {
        let code: String = error.chars().take_while(|c| *c != '`').collect();
        document["error"] = json!(code);
    }
}

/// The request a golden recorded for a pass, as the scenario sends it now. A lookup request recorded before the lookup key was
/// renamed also carried an ownership key (`session_id`) beside `wanted_session`; the sidecar reads that for itself (a live
/// session handle), so a scenario no longer sends it. It is dropped from the recorded request after asserting it only repeated
/// the lookup: scenario input, never a part of a compared document.
fn recorded_request(recorded: &Value) -> Value {
    let mut request = recorded.clone();
    if let Some(repeat) = request.as_object_mut().and_then(|object| object.remove("session_id")) {
        assert_eq!(Some(&repeat), request.get("wanted_session"), "the recorded `session_id` only repeated the lookup");
    }
    request
}

/// Compare the core's answer to every pass of `chain` with the vendor's golden, WHOLE, after the named differences: the shared
/// ones (`legacy_shape`) and the vendor's own (`own`: the pass's answer, its mode, the shaped document's object and the core's
/// document). What this adds, once for every vendor: the passes are the golden's (label, mode, recorded request); a pass the
/// core refused for a log that is not there yet is the empty document the golden recorded (`compat.absent_file_is_empty`: Python
/// never asks); a log's usage anchor digests the bytes the legacy observer handed back; the seen bags are compared sorted and an
/// error by its code.
pub fn check_golden(vendor: &str, chain: &Chain, roots: &[&std::path::Path], files: &[&std::path::Path], own: impl Fn(&Answered, &str, &mut serde_json::Map<String, Value>, &Value)) {
    let golden: Value = serde_json::from_str(&std::fs::read_to_string(golden_file(vendor)).unwrap()).unwrap();
    let passes = golden["passes"].as_array().unwrap();
    assert_eq!(chain.log.len(), passes.len(), "the scenario runs the passes the golden holds");
    for ((label, mode, extra, answered), pass) in chain.log.iter().zip(passes) {
        assert_eq!((label.as_str(), mode.as_str(), &mask_roots(extra, roots, files)), (pass["label"].as_str().unwrap(), pass["mode"].as_str().unwrap(), &recorded_request(&pass["extra"])));
        let mut expected = pass["document"].clone();
        if answered.document.get("refused").is_some() && expected.get("refused").is_none() {
            assert_eq!(expected["events"], json!([]), "pass `{label}`");
            // A pass the baseline answered with an `error` and no events (a record that is no object): the core refuses the pass with
            // the baseline's own message and leaves the caller's bag where it was, where the baseline's bag had taken the keys of the
            // events it threw away (a named difference of the shared shaping: the events are no longer lost).
            if let Some(message) = expected["error"].as_str() {
                assert_eq!(answered.document["refused"].as_str(), Some(message), "pass `{label}`: the refusal names the baseline's error");
                continue;
            }
            assert!(adapters::find(vendor).unwrap().core.compat.absent_file_is_empty, "pass `{label}`: the core refused what the baseline observer answered");
            continue;
        }
        let jsonl_usage = mode == "usage" && answered.document.get("refused").is_none() && answered.document["path"].as_str().is_some_and(|path| path.ends_with(".jsonl"));
        if jsonl_usage {
            assert_anchor_digest(&answered.document, pass["raw_anchor"].as_str().unwrap());
        }
        let mut shaped = legacy_shape(vendor, answered, mode, |object, core| own(answered, mode, object, core));
        for document in [&mut shaped, &mut expected] {
            if chain.expect.baseline_cursor_replaced && mode != "usage" {
                for key in ["checkpoint", "identity"] {
                    document.as_object_mut().unwrap().remove(key);
                }
                for event in document["events"].as_array_mut().into_iter().flatten() {
                    event.as_object_mut().unwrap().remove("checkpoint");
                }
            }
            error_code(document);
            if let Some(seen) = document.get_mut("seen").and_then(Value::as_array_mut) {
                seen.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            }
        }
        assert_eq!(mask_roots(&shaped, roots, files), expected, "pass `{label}`");
    }
}

pub fn events(document: &Value) -> Vec<Value> {
    document["events"].as_array().cloned().unwrap_or_default()
}

pub fn seen(document: &Value) -> Vec<String> {
    document["seen"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect()
}

/// The caller's seen bag after a pass: the keys it asked with and the keys the answer carries, sorted, each once. The sidecar's
/// answer carries only the keys the pass delivered, and the caller adds them to its bag.
pub fn bag_after(asked_with: &[String], document: &Value) -> Vec<String> {
    let mut bag: Vec<String> = asked_with.iter().cloned().chain(seen(document)).collect();
    bag.sort();
    bag.dedup();
    bag
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The blob a golden records as the retired observer it was recorded from (`legacy_source.blob`), and the blob the adapter's
    /// module doc names as its source: the two are the same object, so a reader who follows either lands on the same code. The
    /// goldens are not edited; the `generation` field of each names a regeneration test that no longer exists (the goldens are frozen
    /// records, `restore-legacy-observers.patch` is how they are re-recorded).
    #[test]
    fn each_golden_records_the_blob_its_adapter_names_as_its_source() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let mut checked = Vec::new();
        for entry in std::fs::read_dir(root.join("../../tests/fixtures/agent-cli-observe-goldens")).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().is_none_or(|ext| ext != "json") {
                continue;
            }
            let vendor = path.file_stem().unwrap().to_str().unwrap().to_owned();
            let golden: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
            let recorded = golden["legacy_source"]["blob"].as_str().unwrap_or_else(|| panic!("{vendor}: the golden names no legacy_source.blob"));
            let adapter = root.join("src/observe/adapters").join(format!("{vendor}.rs"));
            let Ok(source) = std::fs::read_to_string(&adapter) else { continue };
            let named: Vec<&str> = source.lines().take_while(|line| line.starts_with("//!")).filter_map(|line| line.split_once("(blob `")).map(|(_, rest)| rest.split('`').next().unwrap()).collect();
            if named.is_empty() {
                continue;
            }
            assert_eq!(named, [recorded], "{vendor}: the adapter's module doc names a different blob than its golden records");
            checked.push(vendor);
        }
        checked.sort();
        assert_eq!(checked, ["aider", "droid", "grok", "kimi", "muse", "pi", "qwen"], "the adapters whose module doc names a source blob");
    }

    #[test]
    fn a_stamp_floor_is_state_only_where_the_declaration_names_a_walk_and_a_layout_that_asks_for_the_stores_clock() {
        assert_eq!(declared_stamp_floors("cursor"), ["marks.blobs.stamped"]);
        assert!(declared_stamp_floors("copilot").is_empty() && declared_stamp_floors("opencode").is_empty(), "no stamp clock, no floor");
        let cursor = declared_spellings("cursor");
        let walk = json!({"marks": {"blobs": {"last": 4, "pending": [], "stamped": 1786844146562000_i64}}});
        assert_state_consumed(&walk, &Value::Null, &cursor, &declared_stamp_floors("cursor"), false, true);
    }

    #[test]
    #[should_panic(expected = "consumed by no declared spelling")]
    fn a_stamped_leaf_of_another_walk_is_no_stamp_floor() {
        let other = json!({"marks": {"blobs": {"last": 4, "pending": []}, "other": {"stamped": 5}}});
        assert_state_consumed(&other, &Value::Null, &declared_spellings("cursor"), &declared_stamp_floors("cursor"), false, true);
    }

    #[test]
    #[should_panic(expected = "consumed by no declared spelling")]
    fn a_stamped_leaf_of_a_vendor_whose_layout_has_no_stamp_clock_is_no_stamp_floor() {
        let part = json!({"marks": {"part": {"last": 1, "pending": [], "stamped": 5}}});
        assert_state_consumed(&part, &Value::Null, &declared_spellings("opencode"), &declared_stamp_floors("opencode"), false, true);
    }

    #[test]
    fn a_log_scenario_runs_the_passes_its_declaration_lists_and_a_line_still_being_written_is_answered_when_first_seen_and_not_again() {
        let line = |kind: &str, content: &str| json!({"type": kind, "timestamp": "2026-10-20T16:00:00.000Z", "data": {"content": content}});
        let mut scenario = Log::new("copilot","session-state/log1/events.jsonl", json!({"cwd": "/w", "session": "log1"}));
        scenario.append(&[line("user.message", "hello"), line("assistant.message", "answer"), json!({"type": "assistant.turn_end", "data": {}})]).passes("first");
        scenario.passes("again");
        scenario.append_text(&line("assistant.message", "half").to_string()).passes("half");
        scenario.append_text("\n").passes("whole").replaced("replaced");
        let labels: Vec<&str> = scenario.chain.log.iter().map(|(label, ..)| label.as_str()).collect();
        assert_eq!(&labels[..3], ["first: usage", "first: session_usage", "first: activity"], "the passes the layout lists, in order");
        let events = |label: &str| scenario.chain.log.iter().find(|(name, ..)| name == label).map(|(_, _, _, answered)| answered.document["events"].as_array().unwrap().clone()).unwrap();
        assert!(events("first: activity").len() >= 2, "the records are read");
        assert!(events("again: activity").is_empty(), "nothing twice");
        assert_eq!(events("half: activity").len(), 1, "a line still being written is answered when first seen");
        assert!(events("whole: activity").is_empty(), "and not again once its newline is there");
    }

    // ---- the strict log assertions each refuse what they claim to refuse --------------------------------------------------------

    fn stat(bytes: &[u8]) -> (std::path::PathBuf, FileStat) {
        let root = Fixture::bare("unused.db");
        let path = root.root.join("log.jsonl");
        std::fs::write(&path, bytes).unwrap();
        std::mem::forget(root);
        (path.clone(), FileStat::of(&path).unwrap())
    }

    #[test]
    fn a_position_with_the_right_members_and_values_is_accepted() {
        let bytes = b"{\"a\":1}\n{\"b\":2}\n";
        let (_, file) = stat(bytes);
        let position = json!({"line": 2, "offset": 16, "identity": text_identity(file), "fingerprint": fingerprint_of(bytes, 16)});
        assert_position_is_the_logs(&position, bytes, file);
    }

    #[test]
    #[should_panic(expected = "a position names exactly these members")]
    fn a_position_with_a_stray_member_is_refused() {
        let bytes = b"{\"a\":1}\n";
        let (_, file) = stat(bytes);
        let position = json!({"line": 1, "offset": 8, "identity": text_identity(file), "fingerprint": fingerprint_of(bytes, 8), "extra": 1});
        assert_position_is_the_logs(&position, bytes, file);
    }

    #[test]
    #[should_panic(expected = "the line count is the lines before the offset")]
    fn a_position_that_miscounts_its_lines_is_refused() {
        let bytes = b"{\"a\":1}\n{\"b\":2}\n";
        let (_, file) = stat(bytes);
        let position = json!({"line": 1, "offset": 16, "identity": text_identity(file), "fingerprint": fingerprint_of(bytes, 16)});
        assert_position_is_the_logs(&position, bytes, file);
    }

    #[test]
    #[should_panic(expected = "the anchor digests the bytes behind the offset")]
    fn a_cursor_whose_anchor_digests_other_bytes_than_those_behind_its_offset_is_refused() {
        let bytes = b"{\"a\":1}\n{\"b\":2}\n";
        let (_, file) = stat(bytes);
        let cursor = json!({"identity": text_identity(file), "offset": 8, "anchor": anchor_of(bytes, 16)});
        assert_cursor_is_the_logs(&cursor, bytes, file, false);
    }

    #[test]
    #[should_panic(expected = "just past a terminated line")]
    fn a_cursor_that_stands_inside_a_line_is_refused() {
        let bytes = b"{\"a\":1}\n";
        let (_, file) = stat(bytes);
        let cursor = json!({"identity": text_identity(file), "offset": 3, "anchor": anchor_of(bytes, 3)});
        assert_cursor_is_the_logs(&cursor, bytes, file, true);
    }

    // ---- worked example begin ----------------------------------------------------------------------------------------------------
    //
    // A new log adapter's whole parity scenario, on the builder: its records, its passes, a line still being written, a rewrite
    // behind the cursor, a replaced file. Every answer is shaped and held to the log by `legacy_shape` (identity, position,
    // resume fields, per-event cursors and anchors); an adapter with a golden ends with `check_log_golden` instead of the last
    // loop here. Codex is the example because its declaration has no golden: the point is the size of what a vendor writes.

    fn codex_line(kind: &str, payload: Value) -> Value {
        json!({"type": kind, "timestamp": "2026-10-20T16:00:00.000Z", "payload": payload})
    }

    fn codex_total(input: u64, output: u64) -> Value {
        codex_line("event_msg", json!({"type": "token_count", "info": {"total_token_usage": {"input_tokens": input, "output_tokens": output}}}))
    }

    #[test]
    fn a_new_log_adapters_parity_scenario_is_a_few_dozen_lines_on_the_builder() {
        let header = codex_line("session_meta", json!({"id": "s1", "cwd": "/w", "model_provider": "p", "cli_version": "9"}));
        let (prompt, reply, done) = (
            codex_line("event_msg", json!({"type": "user_message", "message": "hello"})),
            codex_line("event_msg", json!({"type": "agent_message", "message": "the reply"})),
            codex_line("event_msg", json!({"type": "task_complete"})),
        );
        let mut log = Log::new("codex","sessions/rollout.jsonl", json!({}));
        log.expects("s1", &["cli_version", "cwd", "model", "session_id"]);
        log.append(&[header, codex_total(10, 1), prompt, reply, codex_total(25, 4), done]).passes("first").passes("again");
        log.append_text(codex_total(30, 6).to_string().as_str()).passes("half");
        log.append_text("\n").passes("whole");
        log.rewritten("rewritten", "\"p\"", "\"q\"").replaced("replaced");
        let (labels, modes): (Vec<String>, Vec<String>) = log.chain.log.iter().map(|(label, mode, ..)| (label.clone(), mode.clone())).unzip();
        assert_eq!(&modes[..4], ["usage", "session_usage", "activity", "identify"], "the four passes the declaration lists, in order");
        assert_eq!(labels.len(), 4 * 6, "every step runs them all");
        for (label, mode, _, answered) in &log.chain.log {
            let shaped = legacy_shape("codex", answered, mode, |_, _| {});
            assert!(shaped.get("events").is_some(), "{label} is shaped");
        }
        let events = |label: &str| log.chain.log.iter().find(|(name, ..)| name == label).map(|(_, _, _, answered)| answered.document["events"].as_array().unwrap().clone()).unwrap();
        assert_eq!(events("first: usage").len(), 1, "the totals 10 and 25 are one delta");
        assert!(events("again: usage").is_empty() && events("again: activity").is_empty(), "nothing twice");
        assert!(events("first: activity").iter().any(|event| event["event_type"] == "turn_complete" && event["text"] == "the reply"));
        assert_eq!(events("half: usage").len(), 0, "a total on a line still being written is not read yet");
        assert_eq!(events("whole: usage").len(), 1, "and is read once its newline is there");
    }

    // ---- worked example end ------------------------------------------------------------------------------------------------------

    #[test]
    fn the_worked_example_is_within_the_150_lines_a_new_adapters_parity_test_is_held_to() {
        let source = include_str!("parity.rs");
        let (begin, end) = (source.find("// ---- worked example begin").unwrap(), source.find("// ---- worked example end").unwrap());
        let lines = source[begin..end].lines().count();
        assert!(lines <= 150, "the example is {lines} lines");
    }
}
