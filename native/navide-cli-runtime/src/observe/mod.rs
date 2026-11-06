//! The observation core: one generic `observe::<Observer, Source, Usage, Capability>` and the mechanics every log
//! observer shares.
//!
//! The declaration (`shared/agent-cli/<vendor>.json`, `observer` block) names a vendor's layouts, the modes it answers,
//! the record pointers or table names of its source and its compatibility rules; `adapters/mod.rs` (generated) turns that
//! into typed facts and a static dispatch. The core owns everything that is not a vendor's record vocabulary:
//!
//! - the bounded open (`jsonl.rs`: a regular file only, `O_NOFOLLOW | O_NONBLOCK`, byte and line caps;
//!   `sqlite.rs`: a regular file, a percent-encoded URI, one connection per observation, row and byte caps);
//! - anchor verification and production (`blake2b.rs`; Python does not read or hash the file);
//! - the offset tail, the rotation rule, the fingerprinted resume of an activity pass, the high-water scan, the per-turn
//!   dedup window and the cumulative-to-delta component (`jsonl.rs`);
//! - the row cursor, the pending set, the activity marker and the lookups of a row store, built from the declared table
//!   and column names (`rows.rs`);
//! - usage extraction from the declared pointers (`usage.rs`);
//! - the answer ledger (`answer.rs`): every byte of an answer's variable parts (events with their final cursors, dedup keys,
//!   reports, `turns` rows) is charged to one ledger, the transport cap less a stated envelope; a producer stops before the item
//!   that would cross it and the answer says `more` with a typed resume position. An adapter never sees it;
//! - the persisted contract: the checkpoint, the dedup keys in `seen` and one typed `state` object (`Saved`), which an
//!   adapter never formats or parses as a string;
//! - the result envelope and the error shape.
//!
//! An adapter implements `Observer` plus the hook trait of its source family (`JsonlObserver`, `SqlObserver`) and
//! states, next to each hook, why the hook is not data.
pub mod adapters;
mod answer;
pub mod baseline;
mod blake2b;
mod jsonl;
mod rows;
pub mod sqlite;
pub mod time;
mod usage;

use adapters::{Core, Layout, Mode, ObserverFacts, RequestField};
use answer::{bound_key, json_len, Answer, CHECKPOINT_BYTES, CHECKPOINT_REQUEST_BYTES, FIELD_CHARS, FIXED_BYTES, LEDGER_LISTS, OPEN_TEXT_BYTES, STATE_BYTES};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::io;
use std::path::Path;

pub use answer::{ANSWER_BYTES, OWNERSHIP_BYTES};
pub use jsonl::{Decoded, JsonlFile, JsonlObserver, Line, TurnBreakdown, TurnCall, TurnRow};
pub use rows::{CellOf, ColumnId, Mark, NoColumn, Pass, Progress, SqlObserver, WalkId, WalkMark};
pub use sqlite::Sqlite;
pub use usage::{sum, Reading, Totals, Usage};

#[cfg(test)]
mod jsonl_tests;
#[cfg(test)]
pub mod parity;

// The fixed share holds up to its size whatever a store or a log puts in the fields it caps (t36j U6): the worst of each source kind, with
// every character at the six bytes of its longest JSON escape, summed against `FIXED_BYTES`. This sum is a hand-listed check of the fields
// named below, not a proof about every field: `Run::into_document` measures the fixed fields of each answer and is the backstop for a field
// added elsewhere that this sum does not list (t36k V6, A5). A path is at most `PATH_MAX` (4,096)
// characters; the text fields (`error`, the log's `native_session_id` and `cwd`, the `error` of `ownership_error`) are capped to
// `FIELD_CHARS`; the schema report to `SCHEMA_BYTES`; the sidecar's `ownership` context to `OWNERSHIP_BYTES`. The small rest is the
// parser id, mode, identity, layout slot, `more`, `restart`, `found`, `markers_suppressed` and the declared names of `skipped_cells`.
const _: () = {
    const PATH: usize = 4096 * 6;
    const CAPPED: usize = FIELD_CHARS * 6;
    const SMALL: usize = 4096;
    // A log: `path`, `error`, `native_session_id`, `cwd`, and the `ownership_error` of a store it yields to (a store `path` and an `error`).
    const LOG: usize = PATH + CAPPED + 2 * CAPPED + (PATH + CAPPED);
    // A store: `path`, `error`, `ownership_error` (a store `path` and an `error`), and the `schema_problem` tables.
    const STORE: usize = PATH + CAPPED + (PATH + CAPPED) + rows::SCHEMA_BYTES;
    let widest = if LOG > STORE { LOG } else { STORE };
    assert!(widest + SMALL + OWNERSHIP_BYTES <= FIXED_BYTES, "the worst fixed fields of a source kind outgrew the fixed share");
};

/// The saved cursor, as Python persisted it. Unknown keys survive an observation untouched.
#[derive(Clone, Default)]
pub struct Cursor(pub Map<String, Value>);

impl Cursor {
    pub fn offset(&self) -> u64 {
        self.0.get("offset").and_then(Value::as_u64).unwrap_or(0)
    }
    pub fn identity(&self) -> Option<&str> {
        self.0.get("identity").and_then(Value::as_str)
    }
    pub fn anchor(&self) -> &str {
        self.0.get("anchor").and_then(Value::as_str).unwrap_or("")
    }
    pub fn text(&self, key: &str) -> String {
        self.0.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
    }
    /// What a reader kept under `key` as Python's `str(v or "")` spells it (a value that is not text is its JSON text).
    pub fn kept(&self, key: &str) -> String {
        baseline::str_or_empty(self.0.get(key).unwrap_or(&Value::Null))
    }
    pub fn count(&self, key: &str) -> u64 {
        self.0.get(key).and_then(Value::as_u64).unwrap_or(0)
    }
}

/// One observation request, parsed once from the wire meta.
pub struct Request<'a> {
    pub meta: &'a Value,
    pub path: &'a Path,
    /// `None` for a mode text no declaration defines.
    pub mode: Option<Mode>,
    pub cursor: Cursor,
    /// Dedup keys only. Run-to-run state travels in `state`.
    pub seen: Vec<String>,
    pub state: &'a Value,
}

impl<'a> Request<'a> {
    pub fn parse(meta: &'a Value) -> io::Result<Self> {
        let path = meta["path"].as_str().ok_or_else(|| io::Error::other("path required"))?;
        let mode = meta["mode"].as_str().ok_or_else(|| io::Error::other("mode required"))?;
        // The checkpoint is copied into the answer and into every event's cursor: one the core never produces (its own is a few
        // hundred bytes and a window of dedup keys) is refused, so it cannot make a cursor that fits no answer.
        if meta["checkpoint"].is_object() && meta["checkpoint"].to_string().len() > CHECKPOINT_REQUEST_BYTES {
            return Err(io::Error::other(format!("checkpoint exceeds {CHECKPOINT_REQUEST_BYTES} bytes: the core never produces one that large")));
        }
        Ok(Self {
            meta,
            path: Path::new(path),
            mode: Mode::parse(mode),
            cursor: Cursor(meta["checkpoint"].as_object().cloned().unwrap_or_default()),
            seen: meta["seen"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect(),
            state: &meta["state"],
        })
    }
}

/// Where an activity pass resumes: a byte offset, proved by a fingerprint of the bytes before it, and the number of
/// lines before it.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Position {
    pub line: u64,
    pub offset: u64,
    pub identity: String,
    pub fingerprint: String,
    /// What the vendor's `persist` wrote at this place of the log, for a replay position of a vendor whose records depend on context above
    /// them (`JsonlObserver::RESUMES_IN_CONTEXT`: Aider's section header). A resumed page takes it instead of reading the bytes before the
    /// position. Empty, and not saved, everywhere else.
    #[serde(default, skip_serializing_if = "Map::is_empty")]
    pub context: Map<String, Value>,
}

/// Where a reader that tracked a log by byte offset alone left off (kind `byte_cursor` of `core.legacy_state`: the Python tail reader's
/// offset, file identity and anchor). The Python side cannot read the file, so it carries the cursor as it is; the first activity pass of
/// the log proves it against the file and turns it into a `Position` and the line water (`JsonlFile::adopt`), or refuses it by name.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct LegacyCursor {
    pub offset: u64,
    pub identity: String,
    pub anchor: String,
}

/// Where an activity walk of a row store stands: the last mark delivered and the marks still pending (a mark the vendor
/// has not finished writing is read again until it is final).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct MarkState {
    pub last: i64,
    pub pending: BTreeSet<i64>,
    /// The last stamp (microseconds since the epoch) the walk gave an event (`Pass::stamp`), for an adapter whose store holds no time of its
    /// own (Cursor's blobs). It goes on across the pages of a drain and across calls, and a store that was replaced or shrunk re-anchors
    /// `last` and `pending` but not this: the pane's gate (strictly greater than the last stamp it processed) does not reset with the
    /// store. Not saved for a walk that never stamped.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stamped: Option<i64>,
}

/// What the core remembers between passes, plus the adapter's own state. The wire carries it as one typed `state`
/// object; the Python side stores it as one opaque entry and never looks inside.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Saved<S> {
    /// The activity high-water mark: the highest line already delivered.
    pub water: u64,
    pub position: Option<Position>,
    /// A byte cursor a byte-offset reader left (`LegacyCursor`); the first activity pass spends it. Absent once spent.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub legacy_cursor: Option<LegacyCursor>,
    /// The cumulative totals the last whole-file replay saw.
    pub totals: Option<Totals>,
    /// The activity walks of a row store, by the mark table they walk. A table with no entry has not been walked yet.
    pub marks: BTreeMap<String, MarkState>,
    /// The reply text of the turn an activity pass left open: the last assistant text seen since the turn began, which the
    /// turn's end delivers and spends.
    pub last_text: String,
    /// Where a replay that stopped at the pass budget stands (`more` in the answer); absent between replays.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub replay: Option<Replay>,
    pub adapter: S,
}

impl<S: Default> Default for Saved<S> {
    fn default() -> Self {
        Self { water: 0, position: None, legacy_cursor: None, totals: None, marks: BTreeMap::new(), last_text: String::new(), replay: None, adapter: S::default() }
    }
}

/// Where a paged whole-store (or whole-log) replay stands between its pages: for a row store, the row it read up to, the rows it
/// found unfinished (revisited by the next page, so a row that completes between two pages is delivered), and the identity of the
/// store file those row ids belong to (a replay of another file starts over, keeping the dedup keys); for a log, the position
/// (proved by a fingerprint, as an activity position is) it stopped at. A replay that reaches the end clears it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Replay {
    Row { identity: String, last: i64, pending: BTreeSet<i64> },
    Log(Position),
    /// A `turns` answer that stopped at the ledger: the number of rows already delivered, the entries of the next row's list already
    /// delivered (a row too large for one answer comes in pieces), and the log they were built from (a log that changed meanwhile
    /// starts the breakdown over, and the answer says `restart`).
    Turns {
        delivered: usize,
        #[serde(default)]
        inner: usize,
        log: Position,
    },
}

/// `text` cut to at most `max` bytes, head and tail kept as `cap_head_tail` keeps them.
fn cap_bytes(text: &str, max: usize) -> String {
    const MIDDLE: &str = "\n…\n";
    if text.len() <= max {
        return text.to_owned();
    }
    let half = max.saturating_sub(MIDDLE.len()) / 2;
    let head = (0..=half).rev().find(|at| text.is_char_boundary(*at)).unwrap_or(0);
    let tail = (text.len() - half..=text.len()).find(|at| text.is_char_boundary(*at)).unwrap_or(text.len());
    format!("{}{MIDDLE}{}", &text[..head], &text[tail..])
}

/// An observer: the typed state it keeps beyond the core's. Hooks live on the source-family traits.
pub trait Observer: Sized {
    type State: Default + Clone + Serialize + DeserializeOwned;
}

/// Everything one observation reads that is not the log itself.
pub struct Ctx<'a> {
    pub req: &'a Request<'a>,
    pub facts: &'static ObserverFacts,
    pub core: &'static Core,
    pub layout: &'static Layout,
}

/// Who a record belongs to, for the fields every event repeats.
#[derive(Clone, Default)]
pub struct Ident {
    pub vendor: String,
    pub path: String,
    /// The file stem (the log's own session identifier).
    pub stem: String,
    /// The session id the log names for itself; empty until known (usage events fall back to the stem).
    pub native: String,
    pub cwd: String,
    pub model: String,
    pub version: String,
    /// The scan clock the request named for this file (epoch seconds), where the layout declares `base_epoch`.
    pub base_epoch: f64,
    /// The wall clock the request named (epoch milliseconds), where the layout declares `now_ms`: a vendor that ends a turn after silence
    /// reads it, so the decision is the request's and a test can set it.
    pub now_ms: i64,
    /// What names this file among the files of its kind in a session id the log builds for itself (Aider's history namespace), where the
    /// layout declares `namespace`.
    pub namespace: String,
    /// The part of the log being read (Aider's `# aider chat started at …` section): its name, which is the session its usage belongs to,
    /// and when it began, for a log whose records carry no time of their own. A scan that reads the log in sections keeps both from record
    /// to record and writes them into every cursor.
    pub section: String,
    pub stamp: String,
    /// The modification time of the log (epoch milliseconds) as the handle the pass holds open reported it, for a vendor that ends a turn
    /// after silence (`now_ms` less this). `None` where the platform gives none: the log has not been quiet.
    pub modified_ms: Option<i64>,
    /// The session a `turns` request asks the breakdown of (`wanted_session`), empty for any: only the breakdown sets it, from the request.
    pub wanted_session: String,
}

impl Ident {
    /// Who the request says a store file's records belong to: the vendor, the path, and the fields the layout declares it
    /// reads (`cwd`, `session` as the log's own session identifier, `base_epoch`, `cli_version`). A field the layout does not declare stays
    /// empty, so an adapter cannot lean on a wire field its declaration never named.
    pub fn requested(ctx: &Ctx) -> Self {
        let mut ident = Self { vendor: ctx.facts.vendor.to_owned(), path: ctx.req.path.to_string_lossy().into_owned(), ..Self::default() };
        for field in ctx.layout.request {
            match field {
                RequestField::Cwd => ident.cwd = ctx.req.meta["cwd"].as_str().unwrap_or("").to_owned(),
                RequestField::Session => ident.stem = ctx.req.meta["session"].as_str().unwrap_or("").to_owned(),
                RequestField::BaseEpoch => ident.base_epoch = ctx.req.meta["base_epoch"].as_f64().unwrap_or(0.0),
                RequestField::CliVersion => ident.version = ctx.req.meta["cli_version"].as_str().unwrap_or("").to_owned(),
                RequestField::NowMs => ident.now_ms = ctx.req.meta["now_ms"].as_i64().unwrap_or(0),
                RequestField::Namespace => ident.namespace = ctx.req.meta["namespace"].as_str().unwrap_or("").to_owned(),
            }
        }
        ident
    }

    pub fn session(&self) -> &str {
        if self.native.is_empty() {
            &self.stem
        } else {
            &self.native
        }
    }
}

/// One observation in flight: the typed state, the dedup keys and what the scan has produced so far.
pub struct Run<O: Observer> {
    pub mode: Mode,
    pub saved: Saved<O::State>,
    pub ident: Ident,
    pub dedup: HashSet<String>,
    /// Everything the pass produces for the answer's variable parts, behind the ledger (`answer.rs`): nothing else writes to it.
    pub answer: Answer,
    pub checkpoint: Map<String, Value>,
    pub identity: Value,
    pub error: Option<String>,
    pub rotated: bool,
    pub replaced: bool,
}

impl<O: Observer> Run<O> {
    /// What is left of the ledger for the pass.
    pub(super) fn room(&self) -> usize {
        self.answer.room()
    }

    /// Report a cut once, in the answer's `truncated`: the source's name, the mark or line, and the bytes it had.
    pub(super) fn report_cut(&mut self, source: &str, id: i64, bytes: usize) {
        self.answer.report("truncated", json!({"table": source, "id": id, "bytes": bytes}));
    }

    /// Take a mark's (or line's) `events`, and the dedup `key` the answer carries for them, into the pass (see `Answer::take`). A key
    /// taken is remembered in the pass's dedup set.
    pub(super) fn take(&mut self, source: &str, id: i64, events: &mut Vec<Value>, key: Option<&str>) -> bool {
        let taken = self.answer.take(source, id, events, key);
        if let (true, Some(key)) = (taken, key) {
            self.dedup.insert(key.to_owned());
        }
        taken
    }

    /// Cut the open turn's reply text a hook left in `last_text` (head and tail kept) so that it serializes to at most
    /// `OPEN_TEXT_BYTES`, what the answer's share and a request can carry. Answers the bytes it had when it was cut; the caller reports
    /// the cut (`report_cut`) once the line it came from is taken.
    pub(super) fn bound_open_text(&mut self) -> Option<usize> {
        let bytes = self.saved.last_text.len();
        // A byte serializes to at most six, so a text under a sixth of the bound needs no measuring.
        if bytes <= OPEN_TEXT_BYTES / 6 || json_len(&self.saved.last_text) <= OPEN_TEXT_BYTES {
            return None;
        }
        let mut raw = OPEN_TEXT_BYTES;
        loop {
            let weight = json_len(&self.saved.last_text);
            if weight <= OPEN_TEXT_BYTES {
                return Some(bytes);
            }
            raw = (self.saved.last_text.len() / (weight / OPEN_TEXT_BYTES + 1)).min(raw.saturating_sub(1)).max(16);
            self.saved.last_text = cap_bytes(&self.saved.last_text, raw);
        }
    }

    /// A state that does not parse refuses the pass: resetting it would replay every activity line or re-emit a whole
    /// cumulative total, and the caller keeps its bag when the pass is refused. No state at all is a first pass.
    pub fn new(req: &Request, mode: Mode, replaced: bool) -> io::Result<Self> {
        let saved = if req.state.is_null() {
            Saved::default()
        } else {
            serde_json::from_value(req.state.clone()).map_err(|error| io::Error::other(format!("observation state does not parse: {error}")))?
        };
        Ok(Self {
            mode,
            saved,
            ident: Ident::default(),
            dedup: req.seen.iter().cloned().collect(),
            answer: Answer::default(),
            checkpoint: req.cursor.0.clone(),
            identity: Value::Null,
            error: None,
            rotated: false,
            replaced,
        })
    }

    /// The document. The checkpoint and the state are the shares the ledger reserved for them (`answer.rs`); one that outgrew its
    /// share is a defect of the core and is refused here, never sent as an answer the transport would drop.
    fn into_document(self, req: &Request, facts: &ObserverFacts) -> io::Result<Value> {
        let state = serde_json::to_value(&self.saved).unwrap_or(Value::Null);
        let (checkpoint, state_bytes) = (Value::Object(self.checkpoint), state.to_string().len());
        if checkpoint.to_string().len() > CHECKPOINT_BYTES || state_bytes > OPEN_TEXT_BYTES + STATE_BYTES {
            return Err(io::Error::other("the checkpoint or the state outgrew the share the answer reserves for it"));
        }
        let (events, keys, extra) = self.answer.into_parts();
        let error = self.error.map(|message| cap_head_tail(&message, FIELD_CHARS));
        // The fixed share: everything the ledger does not charge (the lists it does are `LEDGER_LISTS`: `turns`, `truncated`,
        // `dropped_pending` and `markers`). It is also the backstop for a fixed field the const sum above does not list.
        let fixed: Map<String, Value> = [("parser", json!(facts.parser_id)), ("path", json!(req.path)), ("mode", json!(self.mode.as_str())),
            ("identity", self.identity.clone()), ("error", json!(error))]
            .into_iter()
            .map(|(name, value)| (name.to_owned(), value))
            .chain(extra.iter().filter(|(name, _)| !LEDGER_LISTS.contains(&name.as_str())).map(|(name, value)| (name.clone(), value.clone())))
            .collect();
        if Value::Object(fixed).to_string().len() > FIXED_BYTES - OWNERSHIP_BYTES {
            return Err(io::Error::other("the fixed fields outgrew the share the answer reserves for them"));
        }
        let mut document = json!({"parser": facts.parser_id, "path": req.path, "mode": self.mode.as_str(),
            "identity": self.identity, "events": events, "checkpoint": checkpoint, "seen": keys, "state": state, "error": error});
        if let Some(object) = document.as_object_mut() {
            object.extend(extra);
        }
        Ok(document)
    }
}

/// What a usage scan owes the checkpoint it leaves.
pub enum Owed {
    Nothing,
    /// The delta of a cumulative scan: saved totals, latest totals, and the line that carried them.
    Delta { previous: Totals, latest: Totals, last: Option<(u64, String)> },
}

/// The offset a usage scan reached and what it owes.
pub struct Scan {
    pub committed: u64,
    pub owed: Owed,
}

/// A log source: opened under the core's bounds, then driven. `U` is the declared usage component and `X` the capabilities
/// the declared modes select; the generated dispatch names both, so a declaration that asks for something the source or the
/// adapter cannot do does not compile.
pub trait Source<O: Observer>: Sized {
    fn open(ctx: &Ctx) -> io::Result<Self>;
    /// The answer for a path that is not this layout's file: nothing observed, the checkpoint kept.
    fn declined(ctx: &Ctx, mode: Mode) -> io::Result<Run<O>>;
    fn drive<U: UsageScan<O, Self>, X: Capability<O, Self>>(&mut self, ctx: &Ctx, mode: Mode) -> io::Result<Run<O>>;
    /// The layout of this file among those declared for its name. A source that can look at what it holds (a store checks the
    /// table a layout `applies_when`) says so from its own open handle; the rest take the first, which is the only one.
    fn layout(&self, ctx: &Ctx) -> &'static Layout {
        ctx.layout
    }
}

/// How a layout's usage is read: per turn or cumulative. A combination no source implements (cumulative usage in a row
/// store) has no impl, so it cannot be dispatched.
pub trait UsageScan<O: Observer, S>: Sized {
    /// Whether the checkpoint this scan writes carries a window of recent keys (per turn) rather than running totals. The checkpoint-weight
    /// pins read it, so they do not tell the scans apart by type name.
    #[cfg_attr(not(test), allow(dead_code))]
    const WINDOWED: bool;
    fn scan(source: &mut S, ctx: &Ctx, run: &mut Run<O>, start: u64) -> io::Result<Scan>;
}

/// Each record (or row) carries its own usage and is deduplicated by key.
pub struct PerTurn;
/// The log reports running totals; the observation emits the delta since the saved totals.
pub struct Cumulative;
/// The layout records no usage (declared `none`). A usage mode its mode list names is answered with no events and the
/// checkpoint and seen bag as they came, which is what the baseline observers of such vendors answered; one the list omits
/// is refused before it reaches this scan.
pub struct NoUsage;

impl<O: Observer, S> UsageScan<O, S> for NoUsage {
    const WINDOWED: bool = false;
    fn scan(_: &mut S, _: &Ctx, _: &mut Run<O>, _: u64) -> io::Result<Scan> {
        Ok(Scan { committed: 0, owed: Owed::Nothing })
    }
}

/// A mode that is not a scan of the usage or activity records. The generated dispatch selects a capability only for the
/// modes a parser declares; `answer` says whether it handled the mode.
pub trait Capability<O: Observer, S>: Sized {
    fn answer(source: &mut S, ctx: &Ctx, run: &mut Run<O>) -> io::Result<bool>;
}

/// No mode beyond the scans.
pub struct NoCapability;
/// The `turns` mode: a per-prompt breakdown of the session's usage (needs `TurnBreakdown`).
pub struct ByTurns;
/// The `has_session` mode: does the store hold a session (built from the declared parent table).
pub struct BySession;
/// The `identify` mode on a row store: which session holds a pane marker (built from the declared marker).
pub struct ByMarker;

impl<O: Observer, S> Capability<O, S> for NoCapability {
    fn answer(_: &mut S, _: &Ctx, _: &mut Run<O>) -> io::Result<bool> {
        Ok(false)
    }
}

impl<O: Observer, S, A: Capability<O, S>, B: Capability<O, S>> Capability<O, S> for (A, B) {
    fn answer(source: &mut S, ctx: &Ctx, run: &mut Run<O>) -> io::Result<bool> {
        Ok(A::answer(source, ctx, run)? || B::answer(source, ctx, run)?)
    }
}

/// The one observation entry. `facts` is the vendor's declaration; `S`, `U` and `X` are what it declares.
pub fn observe<O, S, U, X>(req: &Request, facts: &'static ObserverFacts) -> io::Result<Value>
where
    O: Observer,
    S: Source<O>,
    U: UsageScan<O, S>,
    X: Capability<O, S>,
{
    let core = &facts.core;
    // The layout of the file decides which modes are answerable: a vendor's other file may answer more or fewer. The layouts
    // of one file differ only in the schema they read, so the first names the modes; the schema is chosen once the store is open.
    let layout = facts.layout_for(req.path).unwrap_or(&facts.layouts[0]);
    let mode = req
        .mode
        .filter(|mode| layout.modes.contains(mode))
        .ok_or_else(|| io::Error::other(core.messages.unsupported_mode))?;
    let ctx = Ctx { req, facts, core, layout };
    run_source::<O, S, U, X>(&ctx, mode, S::open)
}

/// Open the source (a path that is not the layout's own file is declined) and drive it.
fn run_source<O, S, U, X>(ctx: &Ctx, mode: Mode, open: impl FnOnce(&Ctx) -> io::Result<S>) -> io::Result<Value>
where
    O: Observer,
    S: Source<O>,
    U: UsageScan<O, S>,
    X: Capability<O, S>,
{
    let own = ctx.layout.store_file.is_none_or(|file| ctx.req.path.file_name().and_then(|name| name.to_str()) == Some(file));
    // A layout that yields to a store (Copilot's per-session log) is declined when that store already holds its session.
    let ownership = if own { rows::defers(ctx) } else { rows::Ownership { owned: false, problem: None } };
    let run = if own && !ownership.owned {
        let mut source = open(ctx)?;
        // The layout is chosen on the observation's own connection: the store is opened once, for deciding and for reading.
        let ctx = Ctx { layout: source.layout(ctx), ..*ctx };
        let mut run = source.drive::<U, X>(&ctx, mode)?;
        if let Some(problem) = ownership.problem {
            run.answer.set("ownership_error", problem);
        }
        // A file that may hold more than one schema answers which layout it was read with, so the caller never opens the
        // store to find out. A file with one layout has nothing to say.
        if ctx.facts.candidates(ctx.req.path).nth(1).is_some() {
            if let Some(slot) = ctx.facts.layouts.iter().position(|layout| std::ptr::eq(layout, ctx.layout)) {
                run.answer.set("layout", json!(slot));
            }
        }
        run
    } else {
        S::declined(ctx, mode)?
    };
    run.into_document(ctx.req, ctx.facts)
}

/// The same observation over a source the caller opened (the tests open one with small bounds).
#[cfg(test)]
pub fn observe_with<O, S, U, X>(ctx: &Ctx, mode: Mode, source: S) -> io::Result<Value>
where
    O: Observer,
    S: Source<O>,
    U: UsageScan<O, S>,
    X: Capability<O, S>,
{
    run_source::<O, S, U, X>(ctx, mode, |_| Ok(source))
}

/// Test helpers: a declaration copied from a real vendor with one thing changed, so a rule can be proven in both states.
#[cfg(test)]
pub mod testing {
    use super::adapters::{self, Layout, ObserverFacts};

    /// `vendor`'s declaration with its first layout replaced (when given) and `water_survives_replacement` set (when
    /// given). Leaked: a test declaration lives for the process.
    pub fn facts_with(vendor: &str, layout: Option<Layout>, water_survives_replacement: Option<bool>) -> &'static ObserverFacts {
        let real = adapters::find(vendor).unwrap();
        let mut first = layout.unwrap_or(real.layouts[0]);
        if let (Some(keep), Some(rules)) = (water_survives_replacement, first.rules.as_mut()) {
            rules.water_survives_replacement = keep;
        }
        let mut layouts = real.layouts.to_vec();
        layouts[0] = first;
        facts_over(vendor, layouts)
    }

    /// `vendor`'s declaration over other layouts (each names the modes it answers): how a test declares a store shape.
    pub fn facts_over(vendor: &str, layouts: Vec<Layout>) -> &'static ObserverFacts {
        let real = adapters::find(vendor).unwrap();
        Box::leak(Box::new(ObserverFacts { layouts: Box::leak(layouts.into_boxed_slice()), ..*real }))
    }
}

// ---- vocabulary shared by every adapter -----------------------------------------------------------------------------

/// A group's working directory in the form its store's `dir_form` names.
/// `windows` says whether the platform is Windows: a store that spells a Windows path with forward slashes
/// (`WindowsNative`) is handed back in the platform's own spelling, a drive path (`C:/`) or a UNC path (`//`) only.
pub fn dir_form(form: adapters::DirForm, dir: &str, windows: bool) -> String {
    let drive = matches!(dir.as_bytes(), [letter, b':', b'/', ..] if letter.is_ascii_alphabetic());
    if form == adapters::DirForm::WindowsNative && windows && (drive || dir.starts_with("//")) {
        dir.replace('/', "\\")
    } else {
        dir.to_owned()
    }
}

/// An activity event (the same fields for every vendor).
pub fn activity_event(ident: &Ident, timestamp: &str, event_type: &str, key: String, detail: &str, text: String) -> Value {
    json!({"vendor": ident.vendor, "session_id": ident.stem, "cwd": ident.cwd, "file_path": ident.path,
        "timestamp": timestamp, "event_type": event_type, "dedup_key": key, "detail": detail, "text": text})
}

/// An activity event of a row store, whose rows carry their own working directory and session (a file-based log gets both from
/// the request): `activity_event` with `cwd` and `session_id` taken from the mark's group.
pub fn mark_activity(ident: &Ident, mark: &Mark, timestamp: &str, event_type: &str, detail: &str, text: String) -> Value {
    let mut event = activity_event(ident, timestamp, event_type, mark.dedup.clone(), detail, text);
    event["cwd"] = json!(mark.dir);
    event["session_id"] = json!(mark.group);
    event
}

/// The `text` blocks of a message's content, each stripped, the non-empty ones joined by newlines.
pub fn text_blocks<'a>(blocks: impl IntoIterator<Item = &'a Value>) -> String {
    let parts: Vec<String> = blocks
        .into_iter()
        .filter(|block| block["type"] == "text")
        .map(|block| rows::text_or_empty(&block["text"]).trim().to_owned())
        .filter(|part| !part.is_empty())
        .collect();
    parts.join("\n")
}

/// `text` cut to `max` characters, keeping the head and the tail: the loop's and the messaging protocol's end markers sit on a
/// reply's last line, so the tail must survive.
pub fn cap_head_tail(text: &str, max: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= max {
        return text.to_owned();
    }
    let half = max / 2;
    format!("{}\n…\n{}", chars[..half].iter().collect::<String>(), chars[chars.len() - half..].iter().collect::<String>())
}

pub fn text_of(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_request_without_a_path_or_mode_is_refused_before_any_open() {
        assert_eq!(Request::parse(&json!({"mode": "usage"})).err().unwrap().to_string(), "path required");
        assert_eq!(Request::parse(&json!({"path": "/x"})).err().unwrap().to_string(), "mode required");
        assert!(Request::parse(&json!({"path": "/x", "mode": "bogus"})).unwrap().mode.is_none());
    }

    #[test]
    fn a_checkpoint_the_core_could_not_have_produced_is_refused_before_any_open() {
        let fits = json!({"path": "/x", "mode": "usage", "checkpoint": {"unrelated": "c".repeat(CHECKPOINT_REQUEST_BYTES / 2)}});
        assert!(Request::parse(&fits).is_ok());
        let over = json!({"path": "/x", "mode": "usage", "checkpoint": {"unrelated": "c".repeat(CHECKPOINT_REQUEST_BYTES)}});
        assert!(Request::parse(&over).err().unwrap().to_string().contains("checkpoint exceeds"));
    }

    fn codex_run(request: &Request) -> Run<crate::observe::adapters::codex::Codex> {
        Run::new(request, Mode::Usage, false).unwrap()
    }

    /// t36i T3: the fixed fields are a share of the answer like the others. One past its share is a defect of the core and is refused
    /// where the document is assembled; every capped field at its worst (all six-byte escapes) fits.
    #[test]
    fn a_fixed_field_past_its_share_is_refused_and_the_capped_fields_at_their_worst_fit() {
        let meta = json!({"path": "/x", "mode": "usage"});
        let request = Request::parse(&meta).unwrap();
        let facts = adapters::find("codex").unwrap();
        let mut over = codex_run(&request);
        over.answer.set("native_session_id", json!("a".repeat(answer::FIXED_BYTES + 1)));
        let refused = over.into_document(&request, facts).err().expect("a fixed field over its share is refused");
        assert!(refused.to_string().contains("fixed"), "{refused}");

        let hot = "\u{1}".repeat(FIELD_CHARS);
        let mut worst = codex_run(&request);
        worst.answer.set("native_session_id", json!(cap_head_tail(&hot, FIELD_CHARS)));
        worst.answer.set("cwd", json!(cap_head_tail(&hot, FIELD_CHARS)));
        worst.answer.set("ownership_error", json!({"store": "\u{1}".repeat(4096), "error": cap_head_tail(&hot, FIELD_CHARS)}));
        worst.error = Some("\u{1}".repeat(100_000));
        let document = worst.into_document(&request, facts).expect("the worst the caps let through fits the share");
        assert!(document.to_string().len() < answer::FIXED_BYTES + 4096, "and it is the fixed share that carries it: {}", document.to_string().len());
    }

    #[test]
    fn the_cursor_reads_typed_fields_and_keeps_unknown_keys() {
        let cursor = Cursor(json!({"offset": 7, "identity": "1:2", "anchor": "ab", "extra": 1}).as_object().unwrap().clone());
        assert_eq!((cursor.offset(), cursor.identity(), cursor.anchor()), (7, Some("1:2"), "ab"));
        assert_eq!(cursor.0["extra"], 1);
    }

    #[test]
    fn the_saved_state_round_trips_and_a_state_that_does_not_parse_refuses_the_pass() {
        let saved: Saved<()> =
            serde_json::from_value(json!({"water": 4, "marks": {"part": {"last": 9, "pending": [3]}}, "totals": {"input": 1, "output": 2}})).unwrap();
        assert_eq!((saved.water, saved.marks["part"].last, saved.totals), (4, 9, Some(Totals { input: 1, output: 2 })));
        let back: Saved<()> = serde_json::from_value(serde_json::to_value(&saved).unwrap()).unwrap();
        assert_eq!((back.water, back.position, back.marks["part"].pending.len()), (4, None, 1));
        let meta = json!({"path": "/x", "mode": "usage", "state": {"water": "not a number"}});
        let request = Request::parse(&meta).unwrap();
        let refused = Run::<crate::observe::adapters::codex::Codex>::new(&request, Mode::Usage, false).err().unwrap();
        assert!(refused.to_string().contains("state does not parse"), "{refused}");
        let first = json!({"path": "/x", "mode": "usage", "state": null});
        let request = Request::parse(&first).unwrap();
        assert_eq!(Run::<crate::observe::adapters::codex::Codex>::new(&request, Mode::Usage, false).unwrap().saved.water, 0);
    }
}
