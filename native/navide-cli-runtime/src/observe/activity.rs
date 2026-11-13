//! Activity: walk the records past the cursor, run the declaration's rules
//! over each, and keep the state main's readers keep in their `seen_keys`
//! sentinels (the line mark, slots such as the pending reply text, and the
//! inferred-turn tracker) in one `ActivityState`.
use super::jsonl::{self, LineResume};
use super::{ActivityEvent, Error, CWD, SESSION, WALK_RESUMED};
use super::sqlite::Param;
use crate::decl::{ActivitySpec, Clock, Cursor, Effect, Emit, EventKind, Format, Partition, Rows, Rule, Sqlite, TurnOp, TurnStamp};
use crate::expr::{self, Scope, Slots};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::path::Path;

/// The open (or last) inferred turn: main's `{idx, ts | last_ms, flushed, cancelled}`.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct TurnState {
    pub idx: i64,
    #[serde(default)]
    pub ts: String,
    #[serde(default)]
    pub ms: i64,
    #[serde(default)]
    pub flushed: bool,
    #[serde(default)]
    pub cancelled: bool,
}

/// Everything a file's walks carry from one call to the next.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct ActivityState {
    /// Line cursor: the highest line already walked (main's `act_hw::N`).
    #[serde(default)]
    pub hw: usize,
    /// Byte cursor: a JSONL tail checkpoint.
    #[serde(default)]
    pub cursor: Map<String, Value>,
    /// The declaration's `persist` slots.
    #[serde(default)]
    pub slots: Slots,
    #[serde(default)]
    pub turn: Option<TurnState>,
    /// Row cursor: the highest row id walked; `None` until the database is first seen.
    #[serde(default)]
    pub rows: Option<i64>,
    /// Partitioned row cursor: each partition's highest id walked, and
    /// whether the database's first sight anchored them.
    #[serde(default)]
    pub marks: std::collections::BTreeMap<String, i64>,
    #[serde(default)]
    pub anchored: bool,
    /// Rows still settling (`Rows::revisit`).
    #[serde(default)]
    pub pending: std::collections::BTreeSet<i64>,
    /// Persisted slots the last walk wrote (even with the value they had).
    #[serde(skip)]
    pub written: Vec<String>,
}

enum Flow {
    Go,
    Stop,
}

struct Ctx<'a> {
    spec: Option<&'static ActivitySpec>,
    vendor: &'a str,
    file_path: &'a str,
    slots: Slots,
    turn: Option<TurnState>,
    out: Vec<ActivityEvent>,
    written: Vec<String>,
    now: f64,
}

/// Where a record is: its line (line cursor) and the byte offset just past it.
#[derive(Clone, Copy)]
struct At {
    line: usize,
    end: u64,
}

impl Ctx<'_> {
    fn scope<'s>(&'s self, record: &'s Value, at: At) -> Scope<'s> {
        Scope {
            record,
            line: at.line,
            offset: at.end,
            slots: &self.slots,
            cancelled: self.turn.as_ref().is_some_and(|t| t.cancelled),
            entry_key: "",
            now: self.now,
            emitted: self.out.len(),
        }
    }

    /// Every slot write goes through here, so the walk knows what it wrote.
    fn put(&mut self, name: &str, value: String) {
        self.slots.insert(name.to_owned(), value);
        if !self.written.iter().any(|w| w == name) {
            self.written.push(name.to_owned());
        }
    }

    fn record(&mut self, rules: &'static [Rule], record: &Value, at: At) {
        for rule in rules {
            if !expr::pred(&rule.when, &self.scope(record, at)) {
                continue;
            }
            if let Flow::Stop = self.apply_all(rule.effects, record, at) {
                return;
            }
        }
    }

    fn apply_all(&mut self, effects: &'static [Effect], record: &Value, at: At) -> Flow {
        for effect in effects {
            if let Flow::Stop = self.apply(effect, record, at) {
                return Flow::Stop;
            }
        }
        Flow::Go
    }

    fn apply(&mut self, effect: &'static Effect, record: &Value, at: At) -> Flow {
        match effect {
            Effect::Emit(emit) => self.emit(emit, record, at),
            Effect::Set(name, t) => {
                let value = expr::text(t, &self.scope(record, at));
                self.put(name, value);
            }
            Effect::SetOnce { flag, slot, text } => {
                if self.slots.get(*flag).is_none_or(String::is_empty) {
                    let value = expr::text(text, &self.scope(record, at));
                    self.put(slot, value);
                    self.put(flag, "1".to_owned());
                }
            }
            Effect::SetIfNonEmpty(name, t) => {
                let value = expr::text(t, &self.scope(record, at));
                if !value.is_empty() {
                    self.put(name, value);
                }
            }
            Effect::Append(name, t, max) => {
                let more = expr::text(t, &self.scope(record, at));
                if !more.is_empty() {
                    let joined = format!("{}{more}", self.slots.get(*name).map(String::as_str).unwrap_or_default());
                    self.put(name, expr::cap(&joined, *max));
                }
            }
            Effect::Turn(op) => self.turn_op(op, record, at),
            Effect::When(p, inner) => {
                if expr::pred(p, &self.scope(record, at)) {
                    return self.apply_all(inner, record, at);
                }
            }
            Effect::Stop => return Flow::Stop,
        }
        Flow::Go
    }

    fn emit(&mut self, emit: &Emit, record: &Value, at: At) {
        let s = self.scope(record, at);
        let text = expr::text(&emit.text, &s);
        if emit.require_text && text.is_empty() {
            return;
        }
        let event = ActivityEvent {
            vendor: self.vendor.to_owned(),
            event_type: kind_name(emit.kind).to_owned(),
            cwd: self.slots.get(CWD).cloned().unwrap_or_default(),
            session_id: match &emit.session {
                Some(t) => expr::text(t, &s),
                None => self.slots.get(SESSION).cloned().unwrap_or_default(),
            },
            file_path: self.file_path.to_owned(),
            dedup_key: expr::text(&emit.key, &s),
            timestamp: expr::text(&emit.timestamp, &s),
            detail: expr::text(&emit.detail, &s),
            full: if text.is_empty() { String::new() } else { expr::prompt_full(&emit.text, &s) },
            text,
        };
        self.out.push(event);
    }

    fn num(&self, n: &crate::decl::Num, record: &Value, at: At) -> i64 {
        expr::num(n, &self.scope(record, at)).unwrap_or(0)
    }

    fn open_turn(&mut self, ts: String, ms: i64) {
        let idx = self.turn.as_ref().map_or(0, |t| t.idx + 1);
        self.turn = Some(TurnState { idx, ts, ms, flushed: false, cancelled: false });
    }

    /// Close the open turn: one `turn_complete` carrying the pending reply.
    fn close_turn(&mut self, detail: &str, ms: Option<i64>) {
        let Some(spec) = self.spec else { return };
        let Some(turn) = self.turn.as_mut().filter(|t| !t.flushed) else { return };
        turn.flushed = true;
        let timestamp = match spec.turn_stamp {
            TurnStamp::Text => turn.ts.clone(),
            TurnStamp::Millis => ms.map_or(turn.ms, |m| turn.ms.max(m)).to_string(),
        };
        let idx = turn.idx;
        let text = self.slots.get(spec.turn_text).cloned().unwrap_or_default();
        self.put(spec.turn_text, String::new());
        self.out.push(ActivityEvent {
            vendor: self.vendor.to_owned(),
            event_type: kind_name(EventKind::TurnComplete).to_owned(),
            cwd: self.slots.get(CWD).cloned().unwrap_or_default(),
            session_id: self.slots.get(SESSION).cloned().unwrap_or_default(),
            file_path: self.file_path.to_owned(),
            dedup_key: format!("turn:{idx}"),
            timestamp,
            detail: detail.to_owned(),
            text,
            full: String::new(),
        });
    }

    fn turn_op(&mut self, op: &'static TurnOp, record: &Value, at: At) {
        match op {
            TurnOp::Open { boundary, ts, ms } => {
                self.close_turn(boundary, None);
                let (ts, ms) = (expr::text(ts, &self.scope(record, at)), self.num(ms, record, at));
                self.open_turn(ts, ms);
            }
            TurnOp::EnsureOpen { ts, ms } => {
                if self.turn.as_ref().is_none_or(|t| t.flushed) {
                    let (ts, ms) = (expr::text(ts, &self.scope(record, at)), self.num(ms, record, at));
                    self.open_turn(ts, ms);
                }
            }
            TurnOp::Touch { ts, ms } => {
                let ts = ts.as_ref().map(|t| expr::text(t, &self.scope(record, at)));
                let ms = ms.as_ref().map(|n| self.num(n, record, at));
                if let Some(turn) = self.turn.as_mut().filter(|t| !t.flushed) {
                    if let Some(ts) = ts {
                        turn.ts = ts;
                    }
                    if let Some(ms) = ms {
                        turn.ms = turn.ms.max(ms);
                    }
                }
            }
            TurnOp::Close { detail, ms } => {
                let ms = ms.as_ref().map(|n| self.num(n, record, at));
                self.close_turn(detail, ms);
            }
            TurnOp::Cancel { detail, ms } => {
                let ms = ms.as_ref().map(|n| self.num(n, record, at));
                self.close_turn(detail, ms);
                if let Some(turn) = self.turn.as_mut() {
                    turn.cancelled = true;
                    if let Some(spec) = self.spec {
                        self.put(spec.turn_text, String::new());
                    }
                }
            }
        }
    }
}

fn kind_name(kind: EventKind) -> &'static str {
    match kind {
        EventKind::AgentActive => "agent_active",
        EventKind::TurnComplete => "turn_complete",
        EventKind::SubagentTurnEnd => "subagent_turn_end",
    }
}

/// Run slot-only effects (`Set`, `When`, …) outside a walk: usage and turns
/// keep per-file values with the same vocabulary. Emits and turn operations
/// have nothing to act on there and are ignored.
pub(crate) fn apply_slot_effects(effects: &'static [Effect], record: &Value, offset: u64, slots: &mut Slots) {
    let mut ctx = Ctx { spec: None, vendor: "", file_path: "", slots: std::mem::take(slots), turn: None, out: Vec::new(), written: Vec::new(), now: 0.0 };
    ctx.apply_all(effects, record, At { line: 0, end: offset });
    *slots = ctx.slots;
}

/// Run every matching rule's slot effects over one record (turns use this).
pub(crate) fn apply_slot_rules(rules: &'static [Rule], record: &Value, line: usize, slots: &mut Slots) {
    let mut ctx = Ctx { spec: None, vendor: "", file_path: "", slots: std::mem::take(slots), turn: None, out: Vec::new(), written: Vec::new(), now: 0.0 };
    ctx.record(rules, record, At { line, end: 0 });
    *slots = ctx.slots;
}

fn sqlite_activity(format: &'static Format) -> Option<&'static crate::decl::Rows> {
    match format {
        Format::Sqlite(db) => db.activity.as_ref(),
        Format::Jsonl => None,
    }
}

/// Rows after one database-wide watermark.
fn walk_rows(ctx: &mut Ctx, spec: &'static ActivitySpec, path: &Path, db: &Sqlite, query: &Rows, state: &mut ActivityState) -> Result<(), Error> {
    let newest = || super::sqlite::scalar(path, db, query.newest);
    let Some(after) = state.rows else {
        // First sight: everything already in the database is history.
        state.rows = newest()?;
        return Ok(());
    };
    match if query.restart_below { newest()? } else { Some(after) } {
        None => {}
        Some(top) if top < after => state.rows = Some(top),
        Some(_) => {
            let pending = serde_json::to_string(&state.pending).unwrap_or_default();
            let params = [Param::Int(after), Param::Int(query.page), Param::Text(&pending)];
            let params = if query.revisit.is_some() { &params[..] } else { &params[..2] };
            let mut rows = super::sqlite::rows(path, db, query.sql, params)?.unwrap_or_default();
            if rows.last().is_some_and(|row| super::sqlite::held(query, row)) {
                rows.pop();
            }
            let id = |row: &Value| row.get(query.id).and_then(expr::py_int_raw).unwrap_or(0);
            let Some(newest) = rows.last().map(|last| after.max(id(last))) else { return Ok(()) };
            state.rows = Some(newest);
            for row in &mut rows {
                let at = id(row);
                if let Some(revisit) = &query.revisit {
                    if expr::pred(&revisit.when, &Scope::of(row, &Slots::new())) {
                        // Still settling: reported once, then quiet until it settles.
                        if !state.pending.insert(at) {
                            continue;
                        }
                    } else {
                        state.pending.remove(&at);
                    }
                }
                if let (Some(column), Value::Object(object)) = (query.last_as, &mut *row) {
                    object.insert(column.to_owned(), (at == newest).into());
                }
                ctx.record(spec.rules, row, At { line: 0, end: 0 });
            }
        }
    }
    Ok(())
}

/// Rows after each partition's watermark. First sight anchors every
/// partition then present at its newest id; one that appears later is read
/// from its start.
fn walk_partitions(ctx: &mut Ctx, spec: &'static ActivitySpec, path: &Path, db: &Sqlite, query: &Rows, part: &Partition, state: &mut ActivityState) -> Result<(), Error> {
    let Some(tops) = super::sqlite::tops(path, db, part)? else { return Ok(()) };
    if !state.anchored {
        state.marks.extend(tops);
        state.anchored = true;
        return Ok(());
    }
    for (name, top) in tops {
        let after = state.marks.get(&name).copied().unwrap_or(-1);
        if top < after && query.restart_below {
            state.marks.insert(name, top);
            continue;
        }
        if top <= after {
            continue;
        }
        for row in super::sqlite::page(path, db, query, part, &name, after)?.unwrap_or_default() {
            state.marks.insert(name.clone(), row.get(query.id).and_then(expr::py_int_raw).unwrap_or(0));
            ctx.record(spec.rules, &row, At { line: 0, end: 0 });
        }
    }
    Ok(())
}

/// One walk of `path` from `state`. Returns the events and where a line walk
/// can pick the file up again (main keeps that in reader memory).
#[allow(clippy::too_many_arguments)]
pub fn walk(
    spec: &'static ActivitySpec,
    format: &'static Format,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    state: &mut ActivityState,
    resume: Option<LineResume>,
    now: f64,
) -> Result<(Vec<ActivityEvent>, Option<LineResume>), Error> {
    let file_path = path.to_string_lossy().into_owned();
    let mut walked = None;
    let mut decode_error = None;
    let ctx = match spec.cursor {
        Cursor::Lines => {
            for name in spec.persist {
                if let Some(value) = state.slots.get(*name) {
                    slots.insert((*name).to_owned(), value.clone());
                }
            }
            if state.hw > 0 {
                slots.insert(WALK_RESUMED.to_owned(), "1".to_owned());
            }
            let mut ctx = Ctx { spec: Some(spec), vendor, file_path: &file_path, slots, turn: state.turn.take(), out: Vec::new(), written: Vec::new(), now };
            let end = jsonl::walk_lines(path, state.hw, resume, |line| {
                ctx.record(spec.rules, line.value, At { line: line.number, end: line.end });
            });
            let end = match end {
                Ok(end) => end,
                Err(err) => {
                    state.turn = ctx.turn;
                    return Err(err.into());
                }
            };
            state.hw = state.hw.max(end.last_line);
            walked = end.resume;
            decode_error = end.decode_error;
            ctx
        }
        Cursor::Bytes => {
            let tail = jsonl::read_tail(path, &state.cursor)?;
            if !tail.rotated {
                for name in spec.persist {
                    if let Some(value) = state.slots.get(*name) {
                        slots.insert((*name).to_owned(), value.clone());
                    }
                }
            }
            let mut ctx = Ctx { spec: Some(spec), vendor, file_path: &file_path, slots, turn: state.turn.take(), out: Vec::new(), written: Vec::new(), now };
            for (end, record) in &tail.records {
                if let Some(record) = record {
                    ctx.record(spec.rules, record, At { line: 0, end: *end });
                }
            }
            state.cursor = tail.checkpoint;
            ctx
        }
        Cursor::Rows => {
            let mut ctx = Ctx { spec: Some(spec), vendor, file_path: &file_path, slots, turn: state.turn.take(), out: Vec::new(), written: Vec::new(), now };
            let (Format::Sqlite(db), Some(query)) = (format, sqlite_activity(format)) else {
                unreachable!("a Rows cursor is declared with a SQLite activity query")
            };
            match &query.partition {
                Some(part) => walk_partitions(&mut ctx, spec, path, db, query, part, state)?,
                None => walk_rows(&mut ctx, spec, path, db, query, state)?,
            }
            ctx
        }
    };
    let mut ctx = ctx;
    if decode_error.is_none() {
        if let Some(idle) = &spec.idle {
            let quiet_ms = match idle.clock {
                Clock::FileMtime => std::fs::metadata(path)
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map_or(0.0, |mtime| (now - mtime.as_secs_f64()) * 1000.0),
                Clock::TurnMillis => {
                    let since = ctx.turn.as_ref().map_or(0, |t| t.ms);
                    ((now * 1000.0) as i64 - since) as f64
                }
            };
            if ctx.turn.as_ref().is_some_and(|t| !t.flushed) && quiet_ms >= idle.after_ms as f64 {
                ctx.close_turn(idle.detail, None);
            }
        }
    }
    state.turn = ctx.turn.take();
    state.slots = spec
        .persist
        .iter()
        .filter_map(|name| ctx.slots.get(*name).filter(|v| !v.is_empty()).map(|v| ((*name).to_owned(), v.clone())))
        .collect();
    state.written = ctx.written.into_iter().filter(|name| spec.persist.contains(&name.as_str())).collect();
    if let Some(line) = decode_error {
        return Err(Error::Decode { line });
    }
    Ok((ctx.out, walked))
}
