//! A row store's observation, built from the declared `store` block: table and column names (validated identifiers, quoted
//! in every query; the core builds fixed query shapes from them, there is no SQL language in the declaration) and key
//! formats.
//!
//! Components, chosen by the declaration, with no vendor code:
//! - the row cursor (`usage`): a watermark over the rows table, the rows still pending, an anchor that proves the store is
//!   the one the cursor saw, shrink detection and a page size that bounds one pass. A row is a JSON payload, declared scalar
//!   columns, or both; the declared usage pointers read it. A row is pending while its `pending_until` number is 0;
//! - the mark walks (`activity`): one per declared mark table, each with its own start rule, shrink rule and pending set.
//!   A mark is a part of a row (an owner), a row of a parent's group, or a bare row of its own table;
//! - the lookups: `has_session` (does the parent table hold a session), `identify` (which group holds a pane marker) and
//!   the ownership rule that makes a per-session log yield to a store that already holds its session.
//!
//! An adapter keeps one thing that is behaviour, not data: how a mark becomes events (`mark_event`), and, where a vendor
//! finishes a mark after first writing it, when it is final (`mark_pending`).
use super::adapters::{Binding, ColumnIs, JoinPolicy, KeyScope, Layout, MarkShrink, MarkStart, Marks, Mode, Parent, PendingAge, Role, Rows, Select, Store, TimeUnit, TopIs, Walk};
use super::sqlite::{blob_bytes, number, text, Cells, Sqlite};
use super::time::epoch_micros;
use super::usage::Verdict;
use super::{bound_key, cap_head_tail, json_len, Capability, Ctx, Ident, MarkState, Observer, PerTurn, Replay, Run, Scan, Source, UsageScan, FIELD_CHARS};
use serde::Serialize;
use serde_json::{json, Value};
use std::cell::Cell;
use std::collections::{BTreeSet, HashMap};
use std::io;

/// Rows a whole-store replay (`legacy`) may read in one observation, page after page. A larger store is refused.
pub(super) const REPLAY_ROWS: usize = 1_000_000;
/// Payload bytes a whole-store replay may read in one observation. A larger store is refused.
pub(super) const REPLAY_BYTES: usize = 256 * 1024 * 1024;
/// The unfinished rows (or marks) a cursor carries. A pass that would carry more is refused before it commits: an unfinished
/// row is never dropped from a successful checkpoint.
pub(super) const PENDING_MAX: usize = 1024;
// A replay revisits its pending ids on the next page beside the new rows: the page must have room for all of them first.
const _: () = assert!(PENDING_MAX < super::sqlite::PAGE_ROWS);
const OVER_THE_LIMIT: &str = "observation result exceeds the row or byte limit";

/// A row, typed: no column is reached by position.
pub struct Row {
    pub id: i64,
    /// The group the row belongs to (a session id); empty where the store has none.
    pub group: String,
    /// The group's working directory.
    pub dir: String,
    /// What the usage pointers read: the parsed payload and the declared columns, each a key.
    pub record: Value,
    /// The dedup key: the declared prefix and the row's key.
    pub dedup: String,
    anchor: String,
}

/// A parser's mark walks, typed. The generator emits one enum per parser on a row store (`<Parser>Walk`), one variant per
/// distinct mark table across its layouts, and each declared walk names its identity (`Marks::walk`: the crate-wide `Walk`
/// holding the parser's variant). An adapter is handed its mark as the variant of the walk it belongs to (`<Parser>Mark`, one
/// variant per walk, holding the mark typed by that walk's columns), so it matches a type and never a position or a table name,
/// and a column of another walk does not compile.
pub trait WalkId: Copy + 'static {
    /// The parser's mark, as the variant of its walk (it reads as the mark itself, for what every walk's mark has).
    type Marked<'a>: std::ops::Deref<Target = Mark>;

    /// The parser's own variant of a declared walk; none for another parser's walk, which the parser does not walk.
    fn of(walk: Walk) -> Option<Self>;

    /// A mark of this walk, typed by the walk's columns.
    fn mark(self, mark: &Mark) -> Self::Marked<'_>;
}

/// A walk's cells, typed: the generator emits one enum per walk (`<Parser><Table>Column`), one variant per declared cell, in the
/// order the core hands the cells over (the walk's columns, then its parent's). A cell is reached by its variant's position.
pub trait ColumnId: Copy {
    fn position(self) -> usize;
}

/// The columns of a walk that declares no cell: no variant, so no cell can be asked of its marks.
#[derive(Clone, Copy)]
pub enum NoColumn {}

impl ColumnId for NoColumn {
    fn position(self) -> usize {
        match self {}
    }
}

/// A mark of one walk, typed by that walk's column enum `C` (generated beside the walk): its cells are reached by a column of `C`
/// and no other. The rest of the mark (`id`, `group`, `payload`, ...) is the mark's own, through `Deref`.
pub struct WalkMark<'a, C> {
    mark: &'a Mark,
    columns: std::marker::PhantomData<C>,
}

impl<'a, C> WalkMark<'a, C> {
    /// Made by the walk's own `WalkId::mark`.
    pub(in crate::observe) fn new(mark: &'a Mark) -> Self {
        Self { mark, columns: std::marker::PhantomData }
    }
}

impl<C> std::ops::Deref for WalkMark<'_, C> {
    type Target = Mark;

    fn deref(&self) -> &Mark {
        self.mark
    }
}

/// A mark that has the cell `D`: only a mark typed by the column enum `D` does.
pub trait CellOf<D> {
    fn cell(&self, column: D) -> &Value;
}

impl<C: ColumnId> CellOf<C> for WalkMark<'_, C> {
    fn cell(&self, column: C) -> &Value {
        self.mark.cell_at(column.position())
    }
}

/// A mark, typed.
pub struct Mark {
    pub id: i64,
    /// The dedup key: the declared prefix and the mark's key (its id where the walk declares no key column).
    pub dedup: String,
    pub group: String,
    /// The key of the row the mark belongs to; empty where the walk has no owner.
    pub owner: String,
    pub dir: String,
    /// The mark's group is a top-level one: its parent's `up` cell is empty and the declared `top` column holds the declared
    /// value. A walk with no group has no parent above it.
    pub top_level: bool,
    /// The raw payload cell: JSON text, or a BLOB (`bytes`).
    pub payload: Value,
    pub owner_payload: Value,
    pub stamp: Value,
    /// The length the walk's `text_cap` cuts a reply's text to, where it declares one.
    text_cap: Option<usize>,
    cells: Vec<Value>,
}

impl Mark {
    /// The cell at a declared position (a column of the walk's own column enum: `CellOf`); `NULL` past the cells the walk selects.
    fn cell_at(&self, position: usize) -> &Value {
        self.cells.get(position).unwrap_or(&Value::Null)
    }

    /// The payload of a non-JSON mark (protobuf, a blob), as bytes.
    pub fn bytes(&self) -> Vec<u8> {
        blob_bytes(&self.payload).unwrap_or_default()
    }

    /// `text` cut to the walk's declared `text_cap` (head and tail), or whole where the walk declares none.
    pub fn capped(&self, text: &str) -> String {
        self.text_cap.map_or_else(|| text.to_owned(), |max| cap_head_tail(text, max))
    }
}

/// Where an activity pass stands when it hands a mark to the adapter.
pub struct Pass {
    /// The highest mark id this walk knows after the page: the page's newest mark, or the walk's last one if that is higher.
    pub newest: i64,
    /// The last stamp (microseconds since the epoch) the walk gave an event that was delivered, in this call or in any before it.
    floor: Option<i64>,
    /// The same, as far as the mark being built has taken it: it becomes the floor when the mark's events are delivered, and is dropped with
    /// the mark when the ledger refuses it (the mark is built again by the next pass and gets the same stamp).
    taken: Cell<Option<i64>>,
}

impl Pass {
    fn new(newest: i64, floor: Option<i64>) -> Self {
        Self { newest, floor, taken: Cell::new(floor) }
    }

    /// The stamp of the next event of an adapter whose store holds no time of its own (microseconds since the epoch): the scan clock the
    /// request named, or 1 ms after the last stamp the walk gave, whichever is later. The pane's gate admits a turn only when its stamp is
    /// strictly greater than the last one it processed, so a stamp that is not after the walk's last one loses the turn. The floor is kept
    /// across the pages of a drain, across calls and across a store that was replaced or shrunk (the pane's gate does not reset with the
    /// store). A fresh walk stamps its n-th event `clock + n ms`, which is what the baseline's one read stamps.
    pub fn stamp(&self, scan_clock: f64) -> i64 {
        let clock = epoch_micros(scan_clock);
        let at = self.taken.get().map_or(clock, |last| clock.max(last.saturating_add(1_000)));
        self.taken.set(Some(at));
        at
    }
}

/// What an adapter knows of a mark's progress: whether the vendor is still writing it, and the time the mark itself carries
/// (epoch seconds) where its format holds one.
#[derive(Clone, Copy, Default)]
pub struct Progress {
    /// The mark is still being written, so it is read again until it is final (a pending mark already seen is not repeated).
    pub pending: bool,
    /// The mark's own time. A pending mark is dropped once its time is older than the walk's `pending_max_age` against the
    /// newest time any mark of the pass carries, the store's own clock.
    pub stamp: Option<f64>,
}

/// What a row store's observer supplies: behaviour only.
pub trait SqlObserver: Observer {
    /// The parser's mark walks: the generated `<Parser>Walk` enum.
    type Walk: WalkId;

    /// A mark (typed by its walk) as events. `siblings` fetches the payloads of every mark of the same owner when the event needs
    /// them (hook: a vendor rule about what a mark means).
    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: <Self::Walk as WalkId>::Marked<'_>, ident: &Ident, pass: &Pass, siblings: F) -> io::Result<Vec<Value>>;

    /// How far a mark is: still being written or final, and the time it carries (hook: a vendor rule about when a mark is
    /// final, and a time that only its format can read).
    fn mark_progress(_mark: <Self::Walk as WalkId>::Marked<'_>) -> Progress {
        Progress::default()
    }

    /// The text a pane marker is searched in, for a mark of the marker's walk; `None` leaves the mark out (hook: a vendor
    /// rule about which text is the user's own). The mark carries the group, the dir, the payload and the walk's columns.
    fn marker_text(mark: <Self::Walk as WalkId>::Marked<'_>) -> Option<String> {
        Some(text(&mark.payload))
    }

    /// Which of a file's applicable layouts (declaration order, the ones that name tables first) the store is read with, when
    /// more than one applies on this connection. Default: the first, the most specific. Hook, because a rule about which of two
    /// schemas a store really is in cannot be read off the tables it holds.
    fn choose_layout(_applicable: &[&'static Layout], _store: &Sqlite) -> usize {
        0
    }
}

/// A declared stamp (a number in the age's unit) as epoch seconds; none for a missing or zero one, whose age is unknown.
fn stamp_seconds(age: &PendingAge, stamp: &Value) -> Option<f64> {
    let at = number(stamp) as f64;
    let seconds = match age.unit {
        TimeUnit::Seconds => at,
        TimeUnit::Millis => at / 1000.0,
    };
    (seconds > 0.0).then_some(seconds)
}

/// Has a pending row or mark outlived its declared age? Age is measured against the newest time the store showed in this
/// pass (`reference`), never the machine's clock, so the answer depends on the store alone. A stamp that is missing or zero
/// never expires, and a store that stops receiving rows never ages its last pending ones: the bound on the pending set
/// still applies.
fn expired(age: &PendingAge, stamp: Option<f64>, reference: Option<f64>) -> bool {
    matches!((stamp, reference), (Some(at), Some(newest)) if newest - at > age.seconds as f64)
}

/// A pending id a pass dropped, and why. The pass result lists them so a drop is never silent.
fn dropped(table: &str, id: i64, why: &str) -> Value {
    json!({"table": table, "id": id, "why": why})
}

/// A JSON object from a text cell; `{}` where the cell is not one.
/// Python truthiness of a JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
        Value::Bool(true) => true,
    }
}

/// `str(v or "")`.
pub fn text_or_empty(value: &Value) -> String {
    if truthy(value) {
        text(value)
    } else {
        String::new()
    }
}

pub fn object(value: &Value) -> Value {
    value.as_str().and_then(|s| serde_json::from_str::<Value>(s).ok()).filter(Value::is_object).unwrap_or(json!({}))
}

/// The whole-store replay modes: the wire spells it `legacy` or `session_usage`, and a layout answers the ones it lists.
fn replays(mode: Mode) -> bool {
    matches!(mode, Mode::Legacy | Mode::SessionUsage)
}

/// Report a dropped pending id in the pass result (`dropped_pending`).
fn drop_report<O: Observer>(run: &mut Run<O>, entry: Value) {
    run.answer.report("dropped_pending", entry);
}

/// Report every id of a pending set a pass gave up wholesale (the store was replaced, re-anchored or shrank under it).
fn drop_all<O: Observer>(run: &mut Run<O>, table: &str, ids: &BTreeSet<i64>, why: &str) {
    for id in ids {
        drop_report(run, dropped(table, *id, why));
    }
}

fn selected(rule: &Select, record: &Value) -> bool {
    record.pointer(rule.pointer).and_then(Value::as_str) == Some(rule.equals)
}

// ---- the fixed query shapes, built from the declared identifiers -----------------------------------------------------

/// The declared walks that are this parser's, each with its typed identity. Every walk of a parser's layouts is the parser's own
/// (the generator spells them from its enum, and a test pins it); a walk of another parser's declaration is not walked.
fn parser_walks<O: SqlObserver>(marks: &'static [Marks]) -> impl Iterator<Item = (&'static Marks, O::Walk)> {
    marks.iter().filter_map(|walk| O::Walk::of(walk.walk).map(|id| (walk, id)))
}

/// A declared identifier, quoted (the generator accepts plain identifiers only, so a quote cannot occur). Backticks, not
/// double quotes: SQLite reads a double-quoted name that matches no column as a string literal, so an unqualified reference
/// to a column the store lacks would not fail to prepare, and the pass would run on the wrong data. A backtick-quoted name
/// has no such fallback.
pub(super) fn q(name: &str) -> String {
    format!("`{name}`")
}

fn join_kind(parent: &Parent) -> &'static str {
    match parent.join {
        JoinPolicy::Inner => "JOIN",
        JoinPolicy::Left => "LEFT JOIN",
    }
}

/// The `LIKE` pattern that matches `text` anywhere, with `%`, `_` and `\` taken literally (`ESCAPE '\'`).
fn contains(text: &str) -> String {
    let escaped: String = text.chars().flat_map(|c| if matches!(c, '%' | '_' | '\\') { vec!['\\', c] } else { vec![c] }).collect();
    format!("%{escaped}%")
}

fn extras(alias: &str, columns: &[&str]) -> String {
    columns.iter().map(|column| format!(",{alias}.{}", q(column))).collect()
}

impl Store {
    fn parent_dir(&self) -> String {
        self.parent.as_ref().map_or_else(|| "''".to_owned(), |p| format!("COALESCE(s.{},'')", q(p.dir)))
    }

    /// The row pages: `predicate` is `WHERE …` or empty. The cells are id, key, group, payload, dir, the declared columns,
    /// then the anchor columns.
    fn rows_sql(&self, r: &Rows, predicate: &str, limit: usize) -> String {
        let group = r.group.map_or_else(|| "''".to_owned(), |g| format!("m.{}", q(g)));
        let payload = r.payload.map_or_else(|| "NULL".to_owned(), |p| format!("m.{}", q(p)));
        let join = match (&self.parent, r.group) {
            (Some(p), Some(g)) => format!("{} {} s ON s.{}=m.{} ", join_kind(p), q(p.table), q(p.key), q(g)),
            _ => String::new(),
        };
        format!(
            "SELECT m.{id},m.{key},{group},{payload},{dir}{columns}{anchor} FROM {table} m {join}{predicate} ORDER BY m.{id} LIMIT {limit}",
            id = q(r.id),
            key = q(r.key),
            dir = self.parent_dir(),
            columns = extras("m", r.columns),
            anchor = extras("m", r.anchor),
            table = q(r.table),
        )
    }

    fn anchor_sql(r: &Rows) -> String {
        let columns: Vec<String> = r.anchor.iter().map(|column| q(column)).collect();
        format!("SELECT {} FROM {} WHERE {} = ?", columns.join(","), q(r.table), q(r.id))
    }

    fn newest_sql(r: &Rows) -> String {
        format!("SELECT COALESCE(MAX({id}),0) FROM {table}", id = q(r.id), table = q(r.table))
    }

    /// The row table a usage cursor walks, and a mark's owner points into. The generator refuses a declaration that needs it
    /// and lacks it; this is the same check where the declaration is read.
    fn rows_table(&self) -> io::Result<&Rows> {
        self.rows.as_ref().ok_or_else(|| io::Error::other("the store declares no row table"))
    }

    fn versions_sql(&self) -> Option<String> {
        let p = self.parent.as_ref()?;
        Some(format!("SELECT {key},{version} FROM {table}", key = q(p.key), version = q(p.version?), table = q(p.table)))
    }

    /// The FROM clause of a walk or a marker lookup: the mark table `p`, its owner row `m`, its group's parent row `s`.
    fn walk_from(&self, marks: &Marks) -> io::Result<String> {
        let owner = match marks.owner {
            Some(owner) => {
                let rows = self.rows_table()?;
                format!("JOIN {} m ON m.{}=p.{} ", q(rows.table), q(rows.key), q(owner))
            }
            None => String::new(),
        };
        let parent = match (&self.parent, marks.group) {
            (Some(p), Some(group)) => format!("{} {} s ON s.{}=p.{} ", join_kind(p), q(p.table), q(p.key), q(group)),
            _ => String::new(),
        };
        Ok(format!("FROM {} p {owner}{parent}", q(marks.table)))
    }

    /// The cells of a walk page: id, key, group, owner, payload, owner payload, dir, up, stamp, then the walk's columns and
    /// the parent's columns.
    fn marks_sql(&self, marks: &Marks, pending: &BTreeSet<i64>) -> io::Result<String> {
        let cell = |column: Option<&str>, alias: &str, none: &str| column.map_or_else(|| none.to_owned(), |c| format!("{alias}.{}", q(c)));
        let owner_payload = match (marks.owner, self.rows.as_ref().and_then(|rows| rows.payload)) {
            (Some(_), Some(payload)) => format!("m.{}", q(payload)),
            _ => "NULL".to_owned(),
        };
        let up = self.parent.as_ref().and_then(|p| p.up).filter(|_| marks.group.is_some());
        let dir = if marks.group.is_some() { self.parent_dir() } else { "''".to_owned() };
        let parent_columns = self.parent.as_ref().filter(|_| marks.group.is_some()).map_or_else(String::new, |p| extras("s", p.columns));
        let ids: Vec<String> = pending.iter().map(i64::to_string).collect();
        let revisit = if ids.is_empty() { String::new() } else { format!(" OR p.{} IN ({})", q(marks.id), ids.join(",")) };
        Ok(format!(
            "SELECT p.{id},{key},{group},{owner},{payload},{owner_payload},{dir},{up},{stamp}{columns}{parent_columns} {from}WHERE p.{id}>?{revisit} ORDER BY p.{id} LIMIT {limit}",
            // The pending marks read again do not use up the page that new marks need.
            limit = marks.page + pending.len(),
            id = q(marks.id),
            key = cell(marks.key.or(Some(marks.id)), "p", "''"),
            group = cell(marks.group, "p", "''"),
            owner = cell(marks.owner, "p", "''"),
            payload = cell(marks.payload, "p", "NULL"),
            up = cell(up, "s", "NULL"),
            stamp = cell(marks.stamp, "p", "0"),
            columns = extras("p", marks.columns),
            from = self.walk_from(marks)?,
        ))
    }

    fn newest_mark_sql(&self, marks: &Marks) -> String {
        format!("SELECT MAX({id}) FROM {table}", id = q(marks.id), table = q(marks.table))
    }

    /// The payloads that carry a mark's reply: every mark of the same owner, or, where the walk declares `reply_from`, the one
    /// newest earlier row of the mark's group that matches it. The parameters are `reply_params`.
    fn siblings_sql(&self, marks: &Marks) -> Option<String> {
        if let Some(reply) = &marks.reply_from {
            return Some(format!(
                "SELECT {payload} FROM {table} WHERE {group}=? AND {matching}=? AND {order}<? ORDER BY {order} DESC LIMIT 1",
                payload = q(marks.payload?),
                table = q(marks.table),
                group = q(marks.group?),
                matching = q(reply.matching.column),
                order = q(reply.order),
            ));
        }
        Some(format!("SELECT {} FROM {} WHERE {}=? ORDER BY {}", q(marks.payload?), q(marks.table), q(marks.owner?), q(marks.id)))
    }

    fn reply_params(marks: &Marks, mark: &Mark) -> Vec<Value> {
        match &marks.reply_from {
            Some(reply) => vec![json!(mark.group), json!(reply.matching.equals), json!(number(mark.cell_at(reply.before)))],
            None => vec![json!(mark.owner)],
        }
    }

    /// A group's working directory as the store spells it, in the form the declaration names.
    fn spell_dir(&self, dir: String) -> String {
        self.parent.as_ref().map_or(dir.clone(), |p| super::dir_form(p.dir_form, &dir, cfg!(windows)))
    }

    fn session_sql(&self) -> Option<String> {
        let p = self.parent.as_ref()?;
        Some(format!("SELECT 1 FROM {table} WHERE {key}=?", table = q(p.table), key = q(p.key)))
    }

    /// The marks that may carry a pane marker, oldest first, from top-level groups only. The cells are group, payload, owner
    /// payload and dir; the parameters are the declared column conditions then the `LIKE` pattern.
    fn markers_sql(&self, marks: &Marks, marker: &super::adapters::Marker) -> io::Result<(String, Vec<Value>)> {
        let payload = marks.payload.ok_or_else(|| io::Error::other("the marker's walk declares no payload"))?;
        let mut conditions = Vec::new();
        let mut parameters = Vec::new();
        if let Some(Role::Column(ColumnIs { column, equals })) = marker.role {
            conditions.push(format!("p.{}=?", q(column)));
            parameters.push(json!(equals));
        }
        let parent = self.parent.as_ref().filter(|_| marks.group.is_some());
        if let Some(p) = parent {
            if let Some(up) = p.up {
                conditions.push(format!("s.{} IS NULL", q(up)));
            }
            if let Some(TopIs { column, equals, .. }) = p.top {
                conditions.push(format!("s.{}=?", q(column)));
                parameters.push(json!(equals));
            }
        }
        conditions.push(format!("p.{} LIKE ? ESCAPE '\\'", q(payload)));
        parameters.push(json!(contains(marker.text)));
        let group = marks.group.map_or_else(|| "''".to_owned(), |g| format!("p.{}", q(g)));
        let owner_payload = match (marks.owner, self.rows.as_ref().and_then(|rows| rows.payload)) {
            (Some(_), Some(column)) => format!("m.{}", q(column)),
            _ => "NULL".to_owned(),
        };
        let dir = if parent.is_some() { self.parent_dir() } else { "''".to_owned() };
        let order = marks.stamp.filter(|_| marker.order_by_stamp).unwrap_or(marks.id);
        let columns = extras("p", marks.columns) + &parent.map_or_else(String::new, |p| extras("s", p.columns));
        Ok((
            format!(
                "SELECT {group},p.{payload},{owner_payload},{dir}{columns} {from}WHERE {conditions} ORDER BY p.{order}",
                payload = q(payload),
                from = self.walk_from(marks)?,
                conditions = conditions.join(" AND "),
                order = q(order)
            ),
            parameters,
        ))
    }
}

/// The anchor of a row: its anchor cells as Python's `str()` spells each (a NULL is `None`, a flag `True` or `False`) joined by
/// `|`, as the baseline readers fingerprint a row. An anchor is only ever compared with another made the same way, so one over the key
/// bound (a row with a megabyte-long cell among its anchor cells) travels as the key's digest: the cursor is copied into every event
/// and into the checkpoint, whose share it must fit.
fn anchor_of_cells(cells: &[Value]) -> String {
    bound_key(cells.iter().map(|cell| match cell {
        Value::Null => "None".to_owned(),
        Value::Bool(flag) => if *flag { "True" } else { "False" }.to_owned(),
        other => text(other),
    }).collect::<Vec<_>>().join("|"))
}

fn row_of(store: &Store, r: &Rows, cells: &Cells) -> Row {
    let key = key_text(&cells[1]);
    let (columns, anchors) = (&cells[5..5 + r.columns.len()], &cells[5 + r.columns.len()..]);
    let mut record = if r.payload.is_some() { object(&cells[3]) } else { json!({}) };
    for (name, cell) in r.columns.iter().zip(columns) {
        record[*name] = cell.clone();
    }
    Row { id: number(&cells[0]), dedup: bound_key(format!("{}{key}", store.row_prefix)), group: text(&cells[2]), dir: store.spell_dir(text(&cells[4])), record, anchor: anchor_of_cells(anchors) }
}

/// A dedup key's key part as Python's `str()` spells it: a NULL key is `None`, as the baseline readers key it.
fn key_text(cell: &Value) -> String {
    if cell.is_null() {
        "None".to_owned()
    } else {
        text(cell)
    }
}

/// Whether a walk's group is a top-level one: the parent's `up` cell is empty, and its declared `top` column (one of the
/// parent's columns, which follow the walk's own in `tail`, at the position the facts were generated with) holds the declared
/// value. No group, no parent to be under.
fn top_level(store: &Store, walk: &Marks, up: &Value, tail: &[Value]) -> bool {
    let Some(parent) = store.parent.as_ref().filter(|_| walk.group.is_some()) else { return true };
    let top = parent.top.is_none_or(|top| tail.get(walk.columns.len() + top.at).is_some_and(|cell| text(cell) == top.equals));
    text_or_empty(up).is_empty() && top
}

/// A row of the marker lookup as a mark: group, payload, owner payload and dir, then the walk's columns.
fn marker_mark(store: &Store, walk: &Marks, cells: &Cells) -> Mark {
    Mark {
        id: 0,
        dedup: String::new(),
        // The binding a lookup answers is a store cell of any size: it is capped where it is read (the answer's fixed share assumes it).
        group: cap_field(text(&cells[0])),
        owner: String::new(),
        payload: cells[1].clone(),
        owner_payload: cells[2].clone(),
        dir: cap_field(store.spell_dir(text(&cells[3]))),
        // The lookup selects top-level groups only (`markers_sql`).
        top_level: true,
        stamp: Value::Null,
        text_cap: walk.text_cap,
        cells: cells[4..].to_vec(),
    }
}

/// `session` is the log's own session the request named, which a walk with `key_scope: session` puts in every dedup key.
fn mark_of(store: &Store, walk: &Marks, session: &str, cells: &Cells) -> Mark {
    let id = cells[0].as_i64().unwrap_or_else(|| number(&cells[0]));
    let tail = &cells[9..];
    let scope = match walk.key_scope {
        KeyScope::None => String::new(),
        KeyScope::Session => format!("{session}:"),
    };
    Mark {
        id,
        dedup: bound_key(format!("{}{scope}{}", walk.prefix, key_text(&cells[1]))),
        text_cap: walk.text_cap,
        group: text(&cells[2]),
        owner: text(&cells[3]),
        payload: cells[4].clone(),
        owner_payload: cells[5].clone(),
        dir: store.spell_dir(text(&cells[6])),
        top_level: top_level(store, walk, &cells[7], tail),
        stamp: cells[8].clone(),
        cells: tail.to_vec(),
    }
}

fn store_of(ctx: &Ctx) -> io::Result<&'static Store> {
    ctx.layout.store.as_ref().ok_or_else(|| io::Error::other("the layout declares no store"))
}

/// The row cursor as it is saved; a layout that tags its cursors (`cursor_tag`) says so in `schema`.
fn cursor(row: i64, pending: &impl Serialize, identity: &str, mark: &str, tag: Option<&str>) -> Value {
    let mut cursor = json!({"kind": "sqlite", "row_id": row, "pending": pending, "identity": identity, "anchor": mark});
    if let Some(tag) = tag {
        cursor["schema"] = json!(tag);
    }
    cursor
}

/// What the row cursor has so far.
struct Place {
    watermark: i64,
    mark: String,
    pending: BTreeSet<i64>,
}

impl Place {
    /// The cursor has read `row`: the watermark moves up to it, with the anchor that proves it.
    fn advance(&mut self, row: &Row) {
        if row.id > self.watermark {
            self.watermark = row.id;
            self.mark = row.anchor.clone();
        }
    }
}

/// What `deliver` did with a page.
struct Handed {
    /// The id of the last row it read, delivered or not (rows come in ascending order); none where it read none.
    last: Option<i64>,
    /// It stopped at a row that would cross the pass budget; that row and the rest of the page were not read.
    stopped: bool,
}

impl Sqlite {
    /// One page of rows, or `None` where the store could not be read.
    fn page(&self, store: &Store, r: &Rows, predicate: &str) -> io::Result<Option<Vec<Row>>> {
        Ok(self.rows(&store.rows_sql(r, predicate, self.page), &[])?.map(|cells| cells.iter().map(|row| row_of(store, r, row)).collect()))
    }

    fn anchor_at(&self, r: &Rows, id: i64) -> io::Result<Option<String>> {
        Ok(self.rows(&Store::anchor_sql(r), &[json!(id)])?.and_then(|rows| rows.first().map(|row| anchor_of_cells(row))))
    }

    /// The parent's version per group, where the store declares one.
    fn versions(&self, store: &Store) -> io::Result<HashMap<String, String>> {
        let Some(sql) = store.versions_sql() else { return Ok(HashMap::new()) };
        Ok(self.rows(&sql, &[])?.unwrap_or_default().into_iter().filter(|row| !text(&row[0]).is_empty()).map(|row| (text(&row[0]), text(&row[1]))).collect())
    }

    /// The row cursor. `legacy` replays every row against the seen bag instead, page after page.
    fn usage<O: Observer>(&self, ctx: &Ctx, run: &mut Run<O>) -> io::Result<()> {
        let store = store_of(ctx)?;
        let rows = store.rows_table()?;
        let fields = ctx.layout.usage_fields.as_ref().ok_or_else(|| io::Error::other("the layout declares no usage fields"))?;
        let legacy = replays(run.mode);
        let saved = &ctx.req.cursor;
        let replaced = saved.identity().is_some_and(|id| !id.is_empty() && id != self.identity);
        let saved_pending: BTreeSet<i64> = saved.0.get("pending").and_then(Value::as_array).into_iter().flatten().map(number).filter(|v| *v > 0).collect();
        let mut place = Place {
            watermark: if replaced { 0 } else { number(saved.0.get("row_id").unwrap_or(&Value::Null)) },
            mark: if replaced { String::new() } else { saved.text("anchor") },
            pending: if replaced { BTreeSet::new() } else { saved_pending.clone() },
        };
        if replaced {
            // A different store file: the rows the old cursor was waiting for are not in it.
            drop_all(run, rows.table, &saved_pending, "replaced");
        }
        if let Some(tag) = rows.cursor_tag.filter(|tag| !legacy && place.watermark > 0 && saved.text("schema") != *tag) {
            // A mark taken on the file's other schema means nothing in this table, and the rows it copied are already
            // credited: start at the newest row (a store that cannot answer leaves the checkpoint as it came).
            let Some(newest) = self.rows(&Store::newest_sql(rows), &[])?.and_then(|cells| cells.first().map(|row| number(&row[0]))) else { return Ok(()) };
            drop_all(run, rows.table, &place.pending, &format!("untagged (not a `{tag}` cursor)"));
            place.pending.clear();
            place.watermark = newest;
            place.mark = self.anchor_at(rows, newest)?.unwrap_or_default();
        }
        if place.watermark > 0 && !place.mark.is_empty() {
            if let Some(found) = self.anchor_at(rows, place.watermark)? {
                if found != place.mark {
                    place.watermark -= 1;
                    place.mark.clear();
                    drop_all(run, rows.table, &place.pending, "reanchored");
                    place.pending.clear();
                }
            }
        }
        let id = q(rows.id);
        let versions_of = |rows_empty: bool| if rows_empty { Ok(HashMap::new()) } else { self.versions(store) };
        if legacy {
            // A replay resumes where its last page stopped, and stops at the row that would cross the pass budget with `more` and its
            // position instead of answering the whole store. The position is the row it read up to, the rows still unfinished (the
            // next page revisits them) and the identity of the file those ids belong to: a replay saved for another file starts at
            // the first row again (the keys already delivered are in the seen bag, so it only costs a re-read).
            let (mut after, mut revisit) = match &run.saved.replay {
                Some(Replay::Row { identity, last, pending }) if *identity == self.identity => (Some(*last), pending.clone()),
                _ => (None, BTreeSet::new()),
            };
            place.pending.extend(&revisit);
            let (mut read, mut bytes, mut versions) = (0usize, 0usize, None);
            loop {
                let predicate = after.map_or_else(String::new, |behind| {
                    let ids = revisit.iter().map(i64::to_string).collect::<Vec<_>>().join(",");
                    if revisit.is_empty() { format!("WHERE m.{id} > {behind}") } else { format!("WHERE m.{id} > {behind} OR m.{id} IN ({ids})") }
                });
                let Some(page) = self.page(store, rows, &predicate)? else { return Ok(()) };
                let full = page.len() == self.page;
                let present: BTreeSet<i64> = page.iter().map(|row| row.id).collect();
                let newest = page.last().map(|row| row.id).or(after);
                read += page.len();
                bytes += page.iter().map(|row| row.record.to_string().len()).sum::<usize>();
                if read > self.replay_rows || bytes > self.replay_bytes {
                    return Err(io::Error::other(OVER_THE_LIMIT));
                }
                let versions = match &versions {
                    Some(loaded) => loaded,
                    None => versions.insert(versions_of(page.is_empty())?),
                };
                let handed = self.deliver(run, store, rows, fields, &mut place, versions, page)?;
                // An unfinished row the store no longer holds cannot complete.
                for id in revisit.iter().filter(|id| !present.contains(id)) {
                    place.pending.remove(id);
                    drop_report(run, dropped(rows.table, *id, "gone"));
                }
                revisit.clear();
                if handed.stopped {
                    while place.pending.len() > PENDING_MAX {
                        let Some(oldest) = place.pending.pop_first() else { break };
                        drop_report(run, dropped(rows.table, oldest, "overflow"));
                    }
                    let last = handed.last.max(after).unwrap_or(0);
                    run.saved.replay = Some(Replay::Row { identity: self.identity.clone(), last, pending: place.pending.clone() });
                    run.answer.set("more", json!(true));
                    return Ok(());
                }
                if !full {
                    run.saved.replay = None;
                    return Ok(());
                }
                after = newest;
            }
        }
        let mut predicate = format!("WHERE m.{id} > {}", place.watermark);
        if !place.pending.is_empty() {
            let ids = place.pending.iter().map(i64::to_string).collect::<Vec<_>>().join(",");
            predicate.push_str(&format!(" OR m.{id} IN ({ids})"));
        }
        let Some(page) = self.page(store, rows, &predicate)? else { return Ok(()) };
        if page.is_empty() && place.watermark > 0 {
            let newest = self.rows(&Store::newest_sql(rows), &[])?.and_then(|cells| cells.first().map(|row| number(&row[0])));
            if let Some(newest) = newest.filter(|newest| *newest < place.watermark) {
                place.watermark = newest;
                drop_all(run, rows.table, &place.pending, "shrunk");
                place.pending.clear();
                place.mark = self.anchor_at(rows, newest)?.unwrap_or_default();
            }
        }
        let versions = versions_of(page.is_empty())?;
        // The rows this page returned: a pending id the store no longer holds is not among them.
        let present: BTreeSet<i64> = page.iter().map(|row| row.id).collect();
        let reached = page.last().map(|row| row.id);
        let full = page.len() >= self.page;
        // A page that came back full may have rows behind it (a store's baseline reads them all in one call), and so may a pass the
        // budget stopped (the cursor stays at the last row delivered): either way the caller goes on.
        if self.deliver(run, store, rows, fields, &mut place, &versions, page)?.stopped || full {
            run.answer.set("more", json!(true));
        }
        // Pending ids sort before the watermark, so a page that reached past an id (or was not full) would have returned
        // that id if the row still existed: a deleted row cannot complete.
        let gone: Vec<i64> = place.pending.iter().copied().filter(|id| !present.contains(id) && (!full || reached.is_some_and(|last| *id < last))).collect();
        for id in gone {
            place.pending.remove(&id);
            drop_report(run, dropped(rows.table, id, "gone"));
        }
        if place.pending.len() > PENDING_MAX {
            return Err(io::Error::other("observation has more pending rows than the checkpoint can carry"));
        }
        run.checkpoint = cursor(place.watermark, &place.pending, &self.identity, &place.mark, rows.cursor_tag).as_object().cloned().unwrap_or_default();
        Ok(())
    }

    /// Hand each row of a page to the declared usage pointers; an unfinished row stays pending. Each row's events are weighed
    /// against the pass budget as the row is read: at the row that would cross it the page stops (nothing of that row is taken, so
    /// the watermark stays at the last row delivered) and the caller answers `more`. A single row over the whole budget is cut
    /// and reported (`truncated`) by the weighing.
    #[allow(clippy::too_many_arguments)]
    fn deliver<O: Observer>(
        &self,
        run: &mut Run<O>,
        store: &Store,
        table: &Rows,
        fields: &super::adapters::UsageFields,
        place: &mut Place,
        versions: &HashMap<String, String>,
        rows: Vec<Row>,
    ) -> io::Result<Handed> {
        let legacy = replays(run.mode);
        // The store's own clock: the newest time any row of this page carries. A pending row is old against it, not against
        // the machine's clock, so the same store answers the same way whenever it is read.
        let stamp_of = |age: &PendingAge, row: &Row| age.pointer.and_then(|pointer| row.record.pointer(pointer)).and_then(|stamp| stamp_seconds(age, stamp));
        let reference = table.pending_max_age.as_ref().and_then(|age| rows.iter().filter_map(|row| stamp_of(age, row)).max_by(f64::total_cmp));
        let mut handed = Handed { last: None, stopped: false };
        for row in rows {
            if legacy && run.dedup.contains(&row.dedup) {
                handed.last = Some(row.id);
                continue;
            }
            let usage = match fields.of_row(&row.record, row.dedup.clone(), table.pending_until) {
                Verdict::Pending => {
                    place.advance(&row);
                    // A row the vendor never finishes (OpenCode killed mid-stream) is dropped once it is older than the
                    // declared age, and the drop is reported; it is never kept as a permanent stall.
                    let old = table.pending_max_age.as_ref().is_some_and(|age| expired(age, stamp_of(age, &row), reference));
                    if old {
                        place.pending.remove(&row.id);
                        drop_report(run, dropped(table.table, row.id, "expired"));
                    } else {
                        place.pending.insert(row.id);
                    }
                    handed.last = Some(row.id);
                    continue;
                }
                Verdict::Skip => None,
                Verdict::Usage(usage) => Some(usage),
            };
            let mut taken = Vec::new();
            if let Some(usage) = usage {
                let mut event = json!({"vendor": run.ident.vendor, "input_tokens": usage.input, "output_tokens": usage.output,
                    "cwd": row.dir, "session_id": row.group, "file_path": run.ident.path, "dedup_key": row.dedup,
                    "timestamp": usage.timestamp, "model": usage.model});
                if store.parent.as_ref().is_some_and(|p| p.version.is_some()) {
                    event["cli_version"] = json!(versions.get(&row.group).cloned().unwrap_or_default());
                }
                if let Some((read, creation)) = usage.cache {
                    event["cache_read_tokens"] = json!(read);
                    event["cache_creation_tokens"] = json!(creation);
                }
                // The field the baseline's event carries for its row (`TokenUsage.raw`), where the declaration names it.
                if let Some(name) = table.raw_id {
                    event["raw"] = Value::Object(serde_json::Map::from_iter([(name.to_owned(), json!(row.id))]));
                }
                if !legacy {
                    // The cursor this row leaves: the place after the row, which is not taken until the row's events are.
                    let ahead = row.id > place.watermark;
                    let pending: Vec<i64> = place.pending.iter().copied().filter(|id| *id != row.id).collect();
                    let (watermark, mark) = if ahead { (row.id, &row.anchor) } else { (place.watermark, &place.mark) };
                    event["checkpoint"] = cursor(watermark, &pending, &self.identity, mark, table.cursor_tag);
                }
                taken.push(event);
            }
            // The row's event (with its final cursor) and, in a replay, the key the answer carries for it, even where the row makes no
            // event: both are weighed before anything of the row is committed.
            if (!taken.is_empty() || legacy) && !run.take(table.table, row.id, &mut taken, legacy.then_some(row.dedup.as_str())) {
                handed.stopped = true;
                return Ok(handed);
            }
            place.advance(&row);
            place.pending.remove(&row.id);
            handed.last = Some(row.id);
        }
        Ok(handed)
    }

    /// The mark walks. A walk reads the marks its vendor's agent produced since the last delivery; a first pass either
    /// plants the walk past the newest mark or starts it at the oldest, as the walk declares.
    fn activity<O: SqlObserver>(&self, ctx: &Ctx, run: &mut Run<O>) -> io::Result<()> {
        let store = store_of(ctx)?;
        if store.marks.is_empty() {
            return Err(io::Error::other("the store declares no marks"));
        }
        // Whether each walk has nothing more to read this pass. A walk that waits for an earlier one reads nothing (and plants
        // nothing) until that one's page comes back short, so its events never land before the earlier walk's last.
        let mut drained: Vec<(Walk, bool)> = Vec::with_capacity(store.marks.len());
        for (walk, id) in parser_walks::<O>(store.marks) {
            let waiting = walk.after.is_some_and(|earlier| drained.iter().any(|(declared, done)| *declared == earlier && !done));
            let done = if waiting { false } else { self.walk::<O>(run, store, walk, id)? };
            drained.push((walk.walk, done));
        }
        Ok(())
    }

    fn newest_mark(&self, store: &Store, walk: &Marks) -> io::Result<Option<Option<i64>>> {
        Ok(self.rows(&store.newest_mark_sql(walk), &[])?.map(|rows| rows.first().and_then(|row| row[0].as_i64())))
    }

    /// One pass of one walk. The answer is whether the walk has read all there is: `false` when its page came back full or the
    /// pass stopped at the byte budget, so more marks remain.
    fn walk<O: SqlObserver>(&self, run: &mut Run<O>, store: &Store, walk: &Marks, id: O::Walk) -> io::Result<bool> {
        let mut state = match run.saved.marks.get(walk.table) {
            Some(state) => state.clone(),
            None => match walk.start {
                MarkStart::Oldest => MarkState::default(),
                start => {
                    let Some(newest) = self.newest_mark(store, walk)? else { return Ok(true) };
                    let planted = match (newest, start) {
                        (Some(newest), _) => Some(newest),
                        (None, MarkStart::NewestOrZero) => Some(0),
                        (None, _) => None,
                    };
                    if let Some(last) = planted {
                        run.saved.marks.insert(walk.table.to_owned(), MarkState { last, ..MarkState::default() });
                    }
                    return Ok(true);
                }
            },
        };
        if walk.shrink != MarkShrink::Keep && state.last > 0 {
            if let Some(newest) = self.newest_mark(store, walk)? {
                let newest = newest.unwrap_or(0);
                if newest < state.last {
                    drop_all(run, walk.table, &state.pending, "shrunk");
                    state.pending.clear();
                    match walk.shrink {
                        MarkShrink::Newest => {
                            state.last = newest;
                            run.saved.marks.insert(walk.table.to_owned(), state);
                            return Ok(true);
                        }
                        _ => state.last = 0,
                    }
                }
            }
        }
        let sql = store.marks_sql(walk, &state.pending)?;
        let Some(cells) = self.rows(&sql, &[json!(state.last)])? else { return Ok(true) };
        let marks: Vec<Mark> = cells.iter().map(|cell| mark_of(store, walk, &run.ident.stem, cell)).collect();
        let present: BTreeSet<i64> = marks.iter().map(|mark| mark.id).collect();
        let (mut reached, mut full) = (marks.last().map(|mark| mark.id), cells.len() >= walk.page + state.pending.len());
        let mut pass = Pass::new(state.last.max(reached.unwrap_or(0)), state.stamped);
        // What each mark says of its own progress, and the store's own clock: the newest time any mark of the page carries.
        let progress: Vec<Progress> = marks.iter().map(|mark| O::mark_progress(id.mark(mark))).collect();
        let reference = progress.iter().filter_map(|step| step.stamp).max_by(f64::total_cmp);
        for (index, (mark, step)) in marks.iter().zip(&progress).enumerate() {
            // A mark yields events unless it is pending and was already reported (still being written), whatever its age.
            let emits = !step.pending || !state.pending.contains(&mark.id);
            let siblings = || -> io::Result<Vec<Value>> {
                let Some(sql) = store.siblings_sql(walk) else { return Ok(Vec::new()) };
                let payloads = self.rows(&sql, &Store::reply_params(walk, mark))?.unwrap_or_default();
                Ok(payloads.into_iter().map(|row| row[0].clone()).collect())
            };
            let mut events = if emits { O::mark_event(id.mark(mark), &run.ident, &pass, siblings)? } else { Vec::new() };
            if !run.take(walk.table, mark.id, &mut events, None) {
                // The pass budget holds events already and these would cross it: stop at the last mark delivered, and leave
                // this one (not yet committed) for the next pass; the caller asks again, it does not wait for the store to change.
                (reached, full) = (index.checked_sub(1).map(|before| marks[before].id), true);
                run.answer.set("more", json!(true));
                break;
            }
            if mark.id > state.last {
                state.last = mark.id;
            }
            if step.pending {
                let fresh = state.pending.insert(mark.id);
                if walk.pending_max_age.as_ref().is_some_and(|age| expired(age, step.stamp, reference)) {
                    state.pending.remove(&mark.id);
                    drop_report(run, dropped(walk.table, mark.id, "expired"));
                    if !fresh {
                        continue; // reported when first seen
                    }
                } else if !fresh {
                    continue; // still being written, and already reported
                }
            } else {
                state.pending.remove(&mark.id);
            }
            pass.floor = pass.taken.get();
            // The ledger is spent: stop at this mark; the next pass reads on from it.
            if run.answer.full() {
                (reached, full) = (Some(mark.id), true);
                run.answer.set("more", json!(true));
                break;
            }
        }
        // A pending mark the store no longer holds cannot complete (the page returns every pending id it still has).
        let gone: Vec<i64> = state.pending.iter().copied().filter(|id| !present.contains(id) && (!full || reached.is_some_and(|last| *id < last))).collect();
        for id in gone {
            state.pending.remove(&id);
            drop_report(run, dropped(walk.table, id, "gone"));
        }
        if state.pending.len() > PENDING_MAX {
            return Err(io::Error::other("observation has more pending marks than the checkpoint can carry"));
        }
        // A page that came back full (or a pass the ledger stopped) may have marks behind it: the caller goes on, so the baseline's
        // one unbounded read is one call here too. The last stamp given goes on with the walk, drained or not.
        state.stamped = pass.floor;
        if full {
            run.answer.set("more", json!(true));
        }
        run.saved.marks.insert(walk.table.to_owned(), state);
        Ok(!full)
    }
}

impl<O: SqlObserver> Source<O> for Sqlite {
    fn open(ctx: &Ctx) -> io::Result<Self> {
        Self::open_with(ctx, super::sqlite::PAGE_ROWS)
    }

    fn declined(ctx: &Ctx, mode: Mode) -> io::Result<Run<O>> {
        Self::unreadable(super::sqlite::PAGE_ROWS).blank_run(ctx, mode)
    }

    /// Among the layouts named for this file, the one this connection reads it with: the layouts that apply here (every table of
    /// an `applies_when` is held; a layout that names none applies when its parent table is held, or has no parent), the ones
    /// that name tables first, whatever order they are declared in. One that applies is the answer; several go to the adapter's
    /// `choose_layout`; none leaves the first layout that names no table, so its own query reports what is missing.
    fn layout(&self, ctx: &Ctx) -> &'static Layout {
        let applicable = self.applicable(ctx);
        match applicable.len() {
            0 => ctx.facts.candidates(ctx.req.path).find(|layout| layout.applies_when.is_empty()).unwrap_or(ctx.layout),
            1 => applicable[0],
            _ => applicable.get(O::choose_layout(&applicable, self)).copied().unwrap_or(applicable[0]),
        }
    }

    fn drive<U: UsageScan<O, Self>, X: Capability<O, Self>>(&mut self, ctx: &Ctx, mode: Mode) -> io::Result<Run<O>> {
        let mut run = self.blank_run::<O>(ctx, mode)?;
        if self.db.is_none() {
            // A store that is missing or cannot be opened says nothing about its schema: the caller neither records a problem
            // nor clears the one it holds.
            if ctx.facts.candidates(ctx.req.path).nth(1).is_some() {
                run.answer.set("schema_problem", serde_json::to_value(SchemaProblem::Unknown).unwrap_or(Value::Null));
            }
            return Ok(run);
        }
        // A file that may hold more than one schema says what it holds against them (`SchemaProblem`), so the caller can report
        // the file as the baseline's read problem. Held in none of them, or unreadable, the answer is blank (the checkpoint and
        // state as given) in every mode; the other two kinds are read normally, with the problem beside the events.
        if let Some(problem) = self.schema_problem(ctx) {
            let blank = matches!(problem, SchemaProblem::NoneApplies { .. } | SchemaProblem::Unknown);
            run.answer.set("schema_problem", serde_json::to_value(problem).unwrap_or(Value::Null));
            if blank {
                return Ok(run);
            }
        }
        match mode {
            Mode::Activity => self.activity::<O>(ctx, &mut run)?,
            // `usage` is the cursor and `legacy`/`session_usage` the replay; a layout with no usage has `NoUsage` here, which
            // refuses all three (the layout's mode list already refused them first).
            Mode::Usage | Mode::Legacy | Mode::SessionUsage => {
                U::scan(self, ctx, &mut run, 0)?;
            }
            Mode::HasSession | Mode::Identify => {
                if !X::answer(self, ctx, &mut run)? {
                    return Err(io::Error::other(ctx.core.messages.unsupported_mode));
                }
            }
            Mode::Turns => return Err(io::Error::other(ctx.core.messages.unsupported_mode)),
        }
        let skipped = self.skipped();
        if !skipped.is_empty() {
            run.answer.set("skipped_cells", json!(skipped));
        }
        run.error = self.failure();
        Ok(run)
    }
}

/// The most table names a `SchemaProblem::NoneApplies` names, the most characters of each, and the most serialized bytes of all of them
/// (an escape-heavy name weighs six bytes a character, so characters and names alone do not bound the list).
const SCHEMA_TABLES: usize = 256;
const SCHEMA_NAME_CHARS: usize = 256;
pub(super) const SCHEMA_BYTES: usize = 64 * 1024;

/// The first of `names` (as the store lists them) that fit the three bounds above, each cut to its head and tail.
fn named_within_bounds(names: Vec<String>) -> Vec<String> {
    let mut spent = 0;
    names
        .into_iter()
        .take(SCHEMA_TABLES)
        .map(|name| cap_head_tail(&name, SCHEMA_NAME_CHARS))
        .take_while(|name| {
            spent += json_len(name) + 1;
            spent <= SCHEMA_BYTES
        })
        .collect()
}

/// A text a store cell supplied, cut to `FIELD_CHARS` characters (head and tail kept) when longer.
fn cap_field(text: String) -> String {
    if text.chars().nth(FIELD_CHARS).is_some() {
        cap_head_tail(&text, FIELD_CHARS)
    } else {
        text
    }
}

/// What a store file that may hold more than one schema says about the schema it was read with. A file with one layout has
/// nothing to say, and neither has one read with the first layout that applies.
#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SchemaProblem {
    /// No layout applies: the file holds none of the schemas, and these are the tables it does hold (sorted).
    NoneApplies { tables: Vec<String> },
    /// The layout read is one that names no tables, while the `applies_when` of another is held only in part: the tables it holds
    /// and the ones it lacks (sorted), so the schema that is half there goes unread.
    Partial { held: Vec<String>, missing: Vec<String> },
    /// Several layouts apply and the adapter chose one that is not the first: the slot of the layout read (the others go unread).
    Mixed { chosen: usize },
    /// The store could not say which tables it holds (it is missing, locked or busy): its schema is unknown, so nothing is
    /// recorded about it and nothing recorded is cleared.
    Unknown,
}

impl Sqlite {
    /// The problem this store has against the layouts named for its file, if any (`SchemaProblem`), read with `ctx.layout`.
    fn schema_problem(&self, ctx: &Ctx) -> Option<SchemaProblem> {
        let candidates: Vec<&'static Layout> = ctx.facts.candidates(ctx.req.path).collect();
        if candidates.len() < 2 {
            return None;
        }
        let applicable = self.applicable(ctx);
        let Some(first) = applicable.first() else {
            // The tables of a store that is none of ours are only named in a warning: a foreign file can hold any number of any
            // length, so the list is bounded (the answer's fixed share assumes it).
            let tables = named_within_bounds(self.tables());
            return Some(if self.schema_unknown() { SchemaProblem::Unknown } else { SchemaProblem::NoneApplies { tables } });
        };
        if self.schema_unknown() {
            return Some(SchemaProblem::Unknown);
        }
        if !std::ptr::eq(*first, ctx.layout) {
            let chosen = ctx.facts.layouts.iter().position(|layout| std::ptr::eq(layout, ctx.layout))?;
            return Some(SchemaProblem::Mixed { chosen });
        }
        if !ctx.layout.applies_when.is_empty() {
            return None;
        }
        let (mut held, mut missing) = (Vec::new(), Vec::new());
        for layout in candidates.iter().filter(|layout| !layout.applies_when.is_empty() && !applicable.iter().any(|a| std::ptr::eq(*a, **layout))) {
            let (here, lacking): (Vec<&str>, Vec<&str>) = layout.applies_when.iter().partition(|table| self.holds(table));
            if !here.is_empty() {
                held.extend(here.into_iter().map(str::to_owned));
                missing.extend(lacking.into_iter().map(str::to_owned));
            }
        }
        held.sort();
        missing.sort();
        if self.schema_unknown() {
            return Some(SchemaProblem::Unknown);
        }
        (!held.is_empty()).then_some(SchemaProblem::Partial { held, missing })
    }

    /// The layouts named for this file that apply to the store this connection holds, the ones that name tables first.
    pub(super) fn applicable(&self, ctx: &Ctx) -> Vec<&'static Layout> {
        let (mut named, mut plain) = (Vec::new(), Vec::new());
        for layout in ctx.facts.candidates(ctx.req.path) {
            if layout.applies_when.is_empty() {
                if layout.store.as_ref().and_then(|store| store.parent.as_ref()).is_none_or(|parent| self.holds(parent.table)) {
                    plain.push(layout);
                }
            } else if layout.applies_when.iter().all(|table| self.holds(table)) {
                named.push(layout);
            }
        }
        named.extend(plain);
        named
    }

    /// The answer of an observation that has read nothing yet: the store's identity, no markers, no session found. The
    /// declaration's vendor wins over whatever the wire says.
    fn blank_run<O: Observer>(&self, ctx: &Ctx, mode: Mode) -> io::Result<Run<O>> {
        let mut run = Run::<O>::new(ctx.req, mode, false)?;
        run.identity = self.document_identity.clone();
        run.ident = Ident::requested(ctx);
        run.answer.set("markers", json!({}));
        run.answer.set("found", json!(false));
        Ok(run)
    }
}

impl<O: Observer> UsageScan<O, Sqlite> for PerTurn {
    fn scan(db: &mut Sqlite, ctx: &Ctx, run: &mut Run<O>, _: u64) -> io::Result<Scan> {
        db.usage::<O>(ctx, run)?;
        Ok(Scan { committed: 0, owed: super::Owed::Nothing })
    }
}

impl<O: Observer> Capability<O, Sqlite> for super::BySession {
    fn answer(db: &mut Sqlite, ctx: &Ctx, run: &mut Run<O>) -> io::Result<bool> {
        if run.mode != Mode::HasSession {
            return Ok(false);
        }
        let sql = store_of(ctx)?.session_sql().ok_or_else(|| io::Error::other("the store declares no parent table"))?;
        // A request with no session to ask about finds none; it is not a malformed query.
        let Some(session) = ctx.req.meta[super::adapters::WANTED_SESSION].as_str().filter(|id| !id.is_empty()) else { return Ok(true) };
        // A store that holds two schemas (an upgraded one) may hold a valid session on either side, and refusing it would block
        // a resume that works: every parent table the store holds is asked, of every layout the file may be read with (a half-built
        // OpenCode 2 side beside the 1.x tables included), not only the one the store is read with.
        let mut found = false;
        if ctx.facts.candidates(ctx.req.path).nth(1).is_some() {
            for layout in ctx.facts.candidates(ctx.req.path) {
                let held = layout.store.as_ref().and_then(|store| store.parent.as_ref()).is_some_and(|parent| db.holds(parent.table));
                if let (true, Some(sql)) = (held, layout.store.as_ref().and_then(Store::session_sql)) {
                    found = found || db.rows(&sql, &[json!(session)])?.is_some_and(|rows| !rows.is_empty());
                }
            }
        } else {
            found = db.rows(&sql, &[json!(session)])?.is_some_and(|rows| !rows.is_empty());
        }
        run.answer.set("found", json!(found));
        Ok(true)
    }
}

impl<O: SqlObserver> Capability<O, Sqlite> for super::ByMarker {
    fn answer(db: &mut Sqlite, ctx: &Ctx, run: &mut Run<O>) -> io::Result<bool> {
        if run.mode != Mode::Identify {
            return Ok(false);
        }
        let store = store_of(ctx)?;
        let marker = store.marker.as_ref().ok_or_else(|| io::Error::other("the store declares no marker"))?;
        let (walk, walk_of) = parser_walks::<O>(store.marks).find(|(walk, _)| walk.table == marker.on).ok_or_else(|| io::Error::other("the marker names no mark walk"))?;
        let wanted: Vec<_> = ctx.req.meta["markers"].as_array().into_iter().flatten().filter_map(Value::as_str).filter(|s| !s.is_empty()).collect();
        // The markers found so far, and where each is bound: the group (and dir) of the row that holds it, or the session the request
        // named. A binding is charged to the answer ledger (`Answer::bind`) like the other variable parts: the request names as many
        // markers as the caller has unbound, and each binding carries up to two capped cells of the store, so they are not a fixed
        // share. One that does not fit is counted in the answer (`markers_suppressed`), and the lookup still answers.
        let mut found: BTreeSet<&str> = BTreeSet::new();
        let stem = run.ident.stem.clone();
        let bound = |group: &str, dir: &str| match marker.binding {
            Binding::StoreRows => json!([group, dir]),
            Binding::PathSession => json!([stem, ""]),
        };
        if !wanted.is_empty() {
            let payload = walk.payload.ok_or_else(|| io::Error::other("the marker's walk declares no payload"))?;
            if let Some(within) = marker.within {
                // A raw scan: the first `rows` rows' first `bytes` bytes, searched for the marker text byte by byte.
                let sql = format!("SELECT substr({payload},1,{bytes}) FROM {table} LIMIT {rows}", payload = q(payload), bytes = within.bytes, table = q(walk.table), rows = within.rows);
                for row in db.rows(&sql, &[])?.unwrap_or_default() {
                    let bytes = blob_bytes(&row[0]).or_else(|| row[0].as_str().map(|s| s.as_bytes().to_vec()));
                    let Some(bytes) = bytes else { continue };
                    for wanted_text in &wanted {
                        if !found.contains(*wanted_text) && bytes.windows(wanted_text.len()).any(|window| window == wanted_text.as_bytes()) {
                            found.insert(*wanted_text);
                            run.answer.bind(wanted_text, bound("", ""));
                        }
                    }
                }
            } else {
                let (sql, parameters) = store.markers_sql(walk, marker)?;
                for row in db.rows(&sql, &parameters)?.unwrap_or_default() {
                    if let Some(Role::Pointer(role)) = &marker.role {
                        if !selected(role, &object(&row[2])) {
                            continue;
                        }
                    }
                    let mark = marker_mark(store, walk, &row);
                    let body = match marker.pointer {
                        // The text the user typed is one field of the payload, and only that field is searched.
                        Some(pointer) => Some(text_or_empty(object(&mark.payload).pointer(pointer).unwrap_or(&Value::Null))),
                        None => O::marker_text(walk_of.mark(&mark)),
                    };
                    let Some(body) = body else { continue };
                    for wanted_text in &wanted {
                        if !found.contains(*wanted_text) && body.contains(*wanted_text) {
                            found.insert(*wanted_text);
                            run.answer.bind(wanted_text, bound(&mark.group, &mark.dir));
                        }
                    }
                }
            }
        }
        Ok(true)
    }
}

/// What the ownership rule found: whether the store owns the session, and why the lookup failed when it did.
pub(super) struct Ownership {
    pub owned: bool,
    /// The store the lookup read and what went wrong: any failure of the lookup (no sessions table, other columns, busy, not a
    /// database) means "not owned", and the answer carries it as `ownership_error` so the reader can say so once, as the
    /// baseline's `_query` did.
    pub problem: Option<Value>,
}

impl Ownership {
    const NOT_OWNED: Self = Self { owned: false, problem: None };

    fn failed(store: &std::path::Path, error: &str) -> Self {
        Self { owned: false, problem: Some(json!({"store": store.to_string_lossy(), "error": super::cap_head_tail(error, super::answer::FIELD_CHARS)})) }
    }
}

/// The ownership rule: does this layout yield to a store that already holds the session its log's directory is named for?
/// A store that is missing, unreadable or does not know the session means no: the per-session log is read. A store that was
/// there but could not answer is reported with the answer.
pub(super) fn defers(ctx: &Ctx) -> Ownership {
    let Some(rule) = ctx.layout.defers_to else { return Ownership::NOT_OWNED };
    let session = ctx.req.path.parent().and_then(|dir| dir.file_name()).and_then(|name| name.to_str());
    let store_path = ctx.req.path.ancestors().nth(rule.up).map(|dir| dir.join(rule.file));
    let sql = ctx
        .facts
        .layouts
        .iter()
        .find(|layout| layout.store_file == Some(rule.file))
        .and_then(|layout| layout.store.as_ref())
        .and_then(Store::session_sql);
    let (Some(session), Some(store_path), Some(sql)) = (session, store_path, sql) else { return Ownership::NOT_OWNED };
    if !std::fs::symlink_metadata(&store_path).is_ok_and(|stat| stat.is_file()) {
        return Ownership::NOT_OWNED;
    }
    // Any failure of the lookup is "not owned" (the log is read), as the baseline's `_db_owns_session` answered False: a store
    // that is busy, a half-built one with no table of sessions yet, a table whose columns are not the declared ones, a file
    // that is no database. Each is reported, never silent, as the baseline's `_query` warned once.
    let store = match Sqlite::open_reporting(&store_path, super::sqlite::PAGE_ROWS) {
        Ok(store) => store,
        Err(error) => return Ownership::failed(&store_path, &error.to_string()),
    };
    match store.rows(&sql, &[json!(session)]) {
        Ok(Some(rows)) => Ownership { owned: !rows.is_empty(), problem: None },
        Ok(None) => store.failure().map_or(Ownership::NOT_OWNED, |error| Ownership::failed(&store_path, &error)),
        Err(error) => Ownership::failed(&store_path, &error.to_string()),
    }
}

#[cfg(test)]
#[path = "rows_tests.rs"]
mod tests;
