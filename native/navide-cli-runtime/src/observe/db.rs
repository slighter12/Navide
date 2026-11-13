//! The SQLite backend: one database for every session, read through one
//! connection per call. Usage follows a row-id watermark, per-key counters
//! or per-partition watermarks; activity follows a row-id watermark (with
//! held and settling rows) or one watermark per partition.
use super::activity::{At, Walk};
use super::sqlite::{Db, Param, Row};
use super::{bag, file, usage, ActivityState, Backend, Error, File, Memo, UsageRead};
use crate::decl::{Page, Partition, Pred, RowWalk, SqlUsage, SqlWalk, SqliteRecords, UsageRule, Vendor};
use crate::expr::{self, py_int, py_int_raw, py_str, py_str_opt, Scope, Slots};
use serde_json::{Map, Value};
use std::collections::HashSet;

/// One database for every session.
#[derive(Debug)]
pub struct Sqlite;

fn open<V: Vendor<Backend = Sqlite>>(file: &File<'_, V>) -> Result<Option<Db>, Error> {
    let spec = file.records();
    Db::open(file.path, spec.json, spec.binary, spec.requires)
}

fn row_scope<'a, V: Vendor>(row: &'a Row, slots: &'a Slots<V>) -> Scope<'a, V> {
    Scope { blobs: &row.blobs, ..Scope::of(&row.value, slots) }
}

fn holds<V: Vendor>(when: &Option<Pred<V>>, row: &Row) -> bool {
    when.as_ref().is_some_and(|p| expr::pred(p, &row_scope(row, &Slots::default())))
}

fn id_of(row: &Row, column: &str) -> i64 {
    row.value.get(column).and_then(py_int_raw).unwrap_or(0)
}

impl<V: Vendor<Backend = Sqlite>> Backend<V> for Sqlite {
    type Spec = SqliteRecords<V>;

    fn usage(file: &File<'_, V>, checkpoint: &Map<String, Value>, full: bool, memo: &mut Memo) -> Result<UsageRead, Error> {
        match &file.records().usage {
            SqlUsage::None => Ok(UsageRead::unchanged(checkpoint)),
            SqlUsage::Legacy => Ok(UsageRead { events: Vec::new(), checkpoint: usage::legacy(usage::legacy_seen(checkpoint)) }),
            SqlUsage::After { rules, sql, id } => after(file, rules, sql, id, checkpoint, full),
            SqlUsage::Counters { rules, sql, id, kind, map, columns } => counters(file, rules, (sql, id), (kind, map, columns), checkpoint, full),
            SqlUsage::Watermarks { rules, page, part, kind, map } => watermarks(file, rules, (page, part), (kind, map), checkpoint, full, memo),
        }
    }

    fn has_usage(spec: &Self::Spec) -> bool {
        !matches!(spec.usage, SqlUsage::None | SqlUsage::Legacy)
    }

    fn walk(walk: &mut Walk<'_, V>, state: &mut ActivityState, memo: &mut Memo) -> Result<(), Error> {
        walk.restore(state);
        let db = open(walk.file)?;
        match &walk.file.records().activity {
            SqlWalk::Rows(query) => walk_rows(walk, db.as_ref(), query, state),
            SqlWalk::Partitions { page, part, restart_below, .. } => walk_partitions(walk, db.as_ref(), (page, part), *restart_below, state, memo),
        }
        Ok(())
    }

    fn load(spec: &'static Self::Spec, bag: &[String], state: &mut ActivityState) {
        match &spec.activity {
            SqlWalk::Rows(query) => {
                // main's `_int(prev)`: a key that is there but not a number is 0.
                // (`-1` is "from the first row".)
                state.rows = bag::find(bag, query.key).map(|raw| py_int_raw(&Value::String(raw.to_owned())).unwrap_or(0));
                if let Some(revisit) = &query.revisit {
                    state.pending = bag::find(bag, revisit.key).and_then(|raw| serde_json::from_str(raw).ok()).unwrap_or_default();
                }
            }
            SqlWalk::Partitions { key, anchor, .. } => {
                state.marks = bag::marks(bag, key);
                state.anchored = bag.iter().any(|k| k == anchor);
            }
        }
    }

    fn store(spec: &'static Self::Spec, state: &ActivityState, bag: &mut Vec<String>) {
        match &spec.activity {
            SqlWalk::Rows(query) => {
                if let Some(revisit) = &query.revisit {
                    // `json.dumps(sorted(ids))`.
                    let ids: Vec<String> = state.pending.iter().map(i64::to_string).collect();
                    bag::replace(bag, revisit.key, (!ids.is_empty()).then(|| format!("[{}]", ids.join(", "))));
                }
                if let Some(id) = state.rows {
                    bag::replace(bag, query.key, Some(id.to_string()));
                }
            }
            SqlWalk::Partitions { key, anchor, .. } => {
                // Only the marks that moved are rewritten, as main remembers them.
                let before = bag::marks(bag, key);
                for (part, id) in &state.marks {
                    if before.get(part) != Some(id) {
                        bag::replace(bag, &format!("{key}{part}::"), Some(id.to_string()));
                    }
                }
                if state.anchored && !bag.iter().any(|k| k == anchor) {
                    bag.push((*anchor).to_owned());
                }
            }
        }
    }
}

/// Usage rows after a row-id watermark. The watermark follows the rows that
/// gave an event (main advances it per event, so an all-zero row is read
/// again next cycle); a replaced database starts over. `full`: every row,
/// deduplicated by key (main's `parse_session_file`).
fn after<V: Vendor<Backend = Sqlite>>(
    file: &File<'_, V>,
    rules: &'static [UsageRule<V>],
    sql: &str,
    id: &str,
    checkpoint: &Map<String, Value>,
    full: bool,
) -> Result<UsageRead, Error> {
    let Ok(meta) = std::fs::metadata(file.path) else { return Ok(UsageRead::unchanged(checkpoint)) };
    let identity = file::identity(&meta);
    let prior = py_str_opt(checkpoint.get("identity"));
    let replaced = !prior.is_empty() && prior != identity;
    let mut last = if replaced || full { 0 } else { py_int(checkpoint.get("row_id")) };
    let cursor = |last: i64| {
        let mut cp = Map::new();
        cp.insert("kind".into(), "sqlite".into());
        cp.insert("row_id".into(), last.into());
        cp.insert("identity".into(), identity.clone().into());
        cp
    };
    let mut slots = file.slots(&[]);
    let mut events = Vec::new();
    let mut seen = HashSet::new();
    let rows = open(file)?.and_then(|db| db.rows(sql, &[Param::Int(last)])).unwrap_or_default();
    for row in &rows {
        let at = At::row(&row.blobs);
        let Some(reading) = usage::apply_rules(file, rules, &row.value, at, &mut slots) else { continue };
        let s = usage::scope(&row.value, at, &slots);
        let Some((input, output)) = usage::counts(reading, &s) else { continue };
        if input == 0 && output == 0 {
            continue;
        }
        let key = expr::text(&reading.key, &s);
        if full && !seen.insert(key.clone()) {
            continue;
        }
        last = last.max(py_int(row.value.get(id)));
        events.push(usage::event(file, reading, &s, (input, output), key, cursor(last)));
    }
    Ok(UsageRead { events, checkpoint: cursor(last) })
}

fn counters<V: Vendor<Backend = Sqlite>>(
    file: &File<'_, V>,
    rules: &'static [UsageRule<V>],
    (sql, id): (&str, &str),
    (kind, map, columns): (&str, &str, &[&str]),
    checkpoint: &Map<String, Value>,
    full: bool,
) -> Result<UsageRead, Error> {
    let Some(rows) = open(file)?.and_then(|db| db.rows(sql, &[])) else { return Ok(UsageRead::unchanged(checkpoint)) };
    let empty = Map::new();
    let last = match checkpoint.get(map) {
        Some(Value::Object(last)) if !full && checkpoint.get("kind").and_then(Value::as_str) == Some(kind) => last,
        _ => &empty,
    };
    // `int(v or 0)`.
    let int = |v: Option<&Value>| v.filter(|v| expr::truthy(v)).and_then(py_int_raw).unwrap_or(0);
    let mut slots = file.slots(&[]);
    let (mut events, mut state, mut seen) = (Vec::new(), Map::new(), HashSet::new());
    for mut row in rows {
        let key = py_str_opt(row.value.get(id));
        let now: Vec<i64> = columns.iter().map(|c| int(row.value.get(*c))).collect();
        let base: Vec<i64> = match last.get(&key) {
            Some(Value::Array(prev)) if prev.len() == columns.len() => prev.iter().map(|v| py_int_raw(v).unwrap_or(0)).collect(),
            _ => vec![0; columns.len()],
        };
        let delta: Map<String, Value> = columns.iter().zip(now.iter().zip(&base)).map(|(c, (n, b))| ((*c).to_owned(), (n - b).max(0).into())).collect();
        let grew = delta.values().any(|d| d.as_i64() != Some(0));
        let joined = now.iter().map(i64::to_string).collect::<Vec<_>>().join(":");
        state.insert(key, now.into());
        if let Value::Object(object) = &mut row.value {
            object.insert("delta".into(), delta.into());
            object.insert("counters".into(), joined.into());
        }
        if !grew {
            continue;
        }
        let at = At::row(&row.blobs);
        let Some(reading) = usage::apply_rules(file, rules, &row.value, at, &mut slots) else { continue };
        let s = usage::scope(&row.value, at, &slots);
        let Some(read) = usage::counts(reading, &s) else { continue };
        let dedup = expr::text(&reading.key, &s);
        if full && !seen.insert(dedup.clone()) {
            continue;
        }
        events.push(usage::event(file, reading, &s, read, dedup, Map::new()));
    }
    let mut next = Map::new();
    next.insert("kind".into(), kind.into());
    next.insert(map.into(), state.into());
    Ok(UsageRead { events, checkpoint: next })
}

/// Per-partition watermarks: every partition whose newest id is above its
/// mark is read from the mark on, and each row read moves the mark (with or
/// without a reading). Any unreadable query leaves the checkpoint as it was.
fn watermarks<V: Vendor<Backend = Sqlite>>(
    file: &File<'_, V>,
    rules: &'static [UsageRule<V>],
    (query, part): (&Page<V>, &Partition<V>),
    (kind, map): (&str, &str),
    checkpoint: &Map<String, Value>,
    full: bool,
    memo: &mut Memo,
) -> Result<UsageRead, Error> {
    let mut marks: Map<String, Value> = match checkpoint.get(map) {
        Some(Value::Object(last)) if !full && checkpoint.get("kind").and_then(Value::as_str) == Some(kind) => {
            last.iter().filter(|(_, v)| v.is_i64()).map(|(k, v)| (k.clone(), v.clone())).collect()
        }
        _ => Map::new(),
    };
    let Some(db) = open(file)? else { return Ok(UsageRead::unchanged(checkpoint)) };
    let Some(tops) = tops(&db, part) else { return Ok(UsageRead::unchanged(checkpoint)) };
    let mut slots = file.slots(&[]);
    let (mut events, mut seen) = (Vec::new(), HashSet::new());
    for (name, top) in tops {
        let after = marks.get(&name).and_then(Value::as_i64).unwrap_or(-1);
        if top <= after {
            continue;
        }
        let Some(rows) = page(&db, file, query, part, &name, after, memo) else { return Ok(UsageRead::unchanged(checkpoint)) };
        for row in &rows {
            marks.insert(name.clone(), id_of(row, query.id).into());
            let at = At::row(&row.blobs);
            let Some(reading) = usage::apply_rules(file, rules, &row.value, at, &mut slots) else { continue };
            let s = usage::scope(&row.value, at, &slots);
            let Some(read) = usage::counts(reading, &s) else { continue };
            let key = expr::text(&reading.key, &s);
            if full && !seen.insert(key.clone()) {
                continue;
            }
            events.push(usage::event(file, reading, &s, read, key, Map::new()));
        }
    }
    let mut next = Map::new();
    next.insert("kind".into(), kind.into());
    next.insert(map.into(), marks.into());
    Ok(UsageRead { events, checkpoint: next })
}

/// Every partition with its newest id, in the query's order (main iterates
/// its `GROUP BY` result); rows without either are skipped.
fn tops<V: Vendor>(db: &Db, part: &Partition<V>) -> Option<Vec<(String, i64)>> {
    let rows = db.rows(part.tops, &[])?;
    Some(
        rows.iter()
            .filter_map(|row| {
                let name = row.value.get("part").filter(|v| !v.is_null())?;
                let top = row.value.get("top").and_then(py_int_raw)?;
                Some((py_str(name), top))
            })
            .collect(),
    )
}

/// The partition's label: remembered once resolved; else looked up, and a
/// partition no query labels keeps its own name (and is reported).
fn label<V: Vendor>(db: &Db, file: &File<'_, V>, part: &Partition<V>, name: &str, memo: &mut Memo) -> String {
    let key = (file.path.to_owned(), name.to_owned());
    if let Some(found) = memo.labels.get(&key) {
        return found.clone();
    }
    let slots = Slots::<V>::default();
    for (sql, text) in part.label {
        let Some(rows) = db.rows(sql, &[Param::Text(name)]) else { continue };
        let Some(first) = rows.first() else { continue };
        let value = expr::text(text, &row_scope(first, &slots));
        if !value.is_empty() {
            memo.labels.insert(key, value.clone());
            return value;
        }
    }
    memo.note(part.unlabelled, part.unlabelled.replace("{path}", &file.path_text()).replace("{part}", name));
    name.to_owned()
}

/// One page of a partition's rows after `after`, a held last row dropped and
/// every row labelled. `None`: not readable this cycle.
fn page<V: Vendor>(db: &Db, file: &File<'_, V>, query: &Page<V>, part: &Partition<V>, name: &str, after: i64, memo: &mut Memo) -> Option<Vec<Row>> {
    let label = label(db, file, part, name, memo);
    let mut rows = db.rows(query.sql, &[Param::Text(name), Param::Int(after), Param::Int(query.size)])?;
    if rows.last().is_some_and(|row| holds(&query.hold_last, row)) {
        rows.pop();
    }
    for row in &mut rows {
        if let Value::Object(object) = &mut row.value {
            object.insert(part.label_as.to_owned(), label.clone().into());
        }
    }
    Some(rows)
}

/// Rows after one database-wide watermark.
fn walk_rows<V: Vendor>(walk: &mut Walk<'_, V>, db: Option<&Db>, query: &RowWalk<V>, state: &mut ActivityState) {
    let newest = || db.and_then(|db| db.scalar(query.newest));
    let Some(after) = state.rows else {
        // First sight: everything already in the database is history.
        state.rows = newest();
        return;
    };
    match if query.restart_below { newest() } else { Some(after) } {
        None => {}
        Some(top) if top < after => state.rows = Some(top),
        Some(_) => {
            let pending = serde_json::to_string(&state.pending).unwrap_or_default();
            let params = [Param::Int(after), Param::Int(query.page), Param::Text(&pending)];
            let params = if query.revisit.is_some() { &params[..] } else { &params[..2] };
            let mut rows = db.and_then(|db| db.rows(query.sql, params)).unwrap_or_default();
            if rows.last().is_some_and(|row| holds(&query.hold_last, row)) {
                rows.pop();
            }
            let Some(newest) = rows.last().map(|last| after.max(id_of(last, query.id))) else { return };
            state.rows = Some(newest);
            for row in &mut rows {
                let at = id_of(row, query.id);
                if let Some(revisit) = &query.revisit {
                    if expr::pred(&revisit.when, &row_scope(row, &Slots::default())) {
                        // Still settling: reported once, then quiet until it settles.
                        if !state.pending.insert(at) {
                            continue;
                        }
                    } else {
                        state.pending.remove(&at);
                    }
                }
                if let (Some(column), Value::Object(object)) = (query.last_as, &mut row.value) {
                    object.insert(column.to_owned(), (at == newest).into());
                }
                walk.record(&row.value, At::row(&row.blobs));
            }
        }
    }
}

/// Rows after each partition's watermark. First sight anchors every
/// partition then present at its newest id; one that appears later is read
/// from its start.
fn walk_partitions<V: Vendor>(
    walk: &mut Walk<'_, V>,
    db: Option<&Db>,
    (query, part): (&Page<V>, &Partition<V>),
    restart_below: bool,
    state: &mut ActivityState,
    memo: &mut Memo,
) {
    let Some(db) = db else { return };
    let Some(tops) = tops(db, part) else { return };
    if !state.anchored {
        state.marks.extend(tops);
        state.anchored = true;
        return;
    }
    for (name, top) in tops {
        let after = state.marks.get(&name).copied().unwrap_or(-1);
        if top < after && restart_below {
            state.marks.insert(name, top);
            continue;
        }
        if top <= after {
            continue;
        }
        for row in page(db, walk.file, query, part, &name, after, memo).unwrap_or_default() {
            state.marks.insert(name.clone(), id_of(&row, query.id));
            walk.record(&row.value, At::row(&row.blobs));
        }
    }
}
