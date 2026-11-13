//! Token usage from a JSONL tail, in main's checkpoint shape. Two modes:
//! one event per usage record (deduplicated through a bounded key window),
//! or one event per read carrying the delta of running totals.
use super::jsonl::{self, Tail};
use super::sqlite::Param;
use super::{Error, UsageEvent, UsageRead};
use crate::decl::{Format, RawValue, Reading, Restore, Sqlite, UsageMode, UsageSpec};
use crate::expr::{self, py_int, py_str_opt, Scope, Slots};
use serde_json::{Map, Value};
use std::collections::{HashSet, VecDeque};
use std::path::Path;

fn scope<'a>(record: &'a Value, offset: u64, slots: &'a Slots) -> Scope<'a> {
    Scope { record, line: 0, offset, slots, cancelled: false, entry_key: "" }
}

/// The first rule whose `when` holds: its slot effects run, and its reading
/// (when it has one) is returned.
fn apply_rules(spec: &'static UsageSpec, record: &Value, offset: u64, slots: &mut Slots) -> Option<&'static Reading> {
    let rule = spec.rules.iter().find(|r| expr::pred(&r.when, &scope(record, offset, slots)))?;
    super::activity::apply_slot_effects(rule.effects, record, offset, slots);
    rule.reading.as_ref()
}

fn raw_map(reading: &Reading, s: &Scope) -> Map<String, Value> {
    let mut raw = Map::new();
    for (path, value) in reading.raw {
        let value: Value = match value {
            RawValue::Num(n) => expr::num(n, s).unwrap_or(0).into(),
            RawValue::Text(t) => expr::text(t, s).into(),
        };
        let (last, parents) = path.split_last().expect("raw paths are non-empty");
        let mut node = &mut raw;
        for key in parents {
            node = node
                .entry(key.to_string())
                .or_insert_with(|| Value::Object(Map::new()))
                .as_object_mut()
                .expect("raw parents are objects");
        }
        node.insert(last.to_string(), value);
    }
    raw
}

fn event(vendor: &str, path: &Path, reading: &Reading, s: &Scope, input: i64, output: i64, key: String, checkpoint: Map<String, Value>) -> UsageEvent {
    UsageEvent {
        vendor: vendor.to_owned(),
        input_tokens: input,
        output_tokens: output,
        cwd: expr::text(&reading.cwd, s),
        session_id: expr::text(&reading.session_id, s),
        file_path: path.to_string_lossy().into_owned(),
        dedup_key: key,
        timestamp: expr::text(&reading.timestamp, s),
        model: expr::text(&reading.model, s),
        checkpoint,
        raw: raw_map(reading, s),
        cache_read_tokens: expr::num(&reading.cache_read, s).unwrap_or(0),
        cache_creation_tokens: expr::num(&reading.cache_creation, s).unwrap_or(0),
        cli_version: expr::text(&reading.cli_version, s),
    }
}

fn persist_into(spec: &UsageSpec, slots: &Slots, checkpoint: &mut Map<String, Value>) {
    for name in spec.persist {
        let value = slots.get(*name).cloned().unwrap_or_default();
        let value = if spec.flags.contains(name) { Value::Bool(!value.is_empty()) } else { value.into() };
        checkpoint.insert((*name).to_owned(), value);
    }
}

/// `full`: main's `parse_session_file` pass (every record from the top, an
/// unbounded dedup set, no rewrite bookkeeping), used by inferred turns.
pub fn read(
    spec: &'static UsageSpec,
    format: &'static Format,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    checkpoint: &Map<String, Value>,
    full: bool,
) -> Result<UsageRead, Error> {
    if let Format::Sqlite(db) = format {
        return rows(spec, db, vendor, path, slots, checkpoint, full);
    }
    if let UsageMode::Snapshot { suffix, mark } = spec.mode {
        return Ok(snapshot(spec, vendor, path, slots, checkpoint, suffix, mark));
    }
    let tail = jsonl::read_tail(path, checkpoint)?;
    let restore = match spec.restore {
        Restore::UnlessRotated => !tail.rotated,
        Restore::UnlessReplaced => !tail.replaced,
    };
    if restore {
        for name in spec.persist {
            let value = match checkpoint.get(*name) {
                Some(v) if spec.flags.contains(name) => if expr::truthy(v) { "1".to_owned() } else { String::new() },
                other => py_str_opt(other),
            };
            if !value.is_empty() {
                slots.insert((*name).to_owned(), value);
            }
        }
    }
    match spec.mode {
        UsageMode::PerRecord { window, keep_on_rotation, credited } => {
            Ok(per_record(spec, vendor, path, slots, checkpoint, tail, window, keep_on_rotation, credited && !full, full))
        }
        UsageMode::Cumulative => cumulative(spec, vendor, path, slots, checkpoint, tail, restore),
        UsageMode::Snapshot { .. } => unreachable!("read above"),
        // tests/declarations.rs: only a SQLite declaration uses Counters.
        UsageMode::Counters { .. } | UsageMode::Watermarks { .. } => unreachable!("a SQLite mode"),
    }
}

/// Usage rows after a row-id watermark. The watermark follows the rows that
/// gave an event (main advances it per event, so an all-zero row is read
/// again next cycle); a replaced database starts over. `full`: every row,
/// deduplicated by key (main's `parse_session_file`).
fn rows(spec: &'static UsageSpec, db: &'static Sqlite, vendor: &str, path: &Path, mut slots: Slots, checkpoint: &Map<String, Value>, full: bool) -> Result<UsageRead, Error> {
    if let UsageMode::Counters { kind, map, columns } = spec.mode {
        return counters(spec, db, vendor, path, slots, checkpoint, full, (kind, map, columns));
    }
    if let UsageMode::Watermarks { kind, map } = spec.mode {
        return watermarks(spec, db, vendor, path, slots, checkpoint, full, (kind, map));
    }
    let (Some(query), Ok(meta)) = (db.usage.as_ref(), std::fs::metadata(path)) else {
        return Ok(UsageRead { events: Vec::new(), checkpoint: checkpoint.clone() });
    };
    let identity = jsonl::identity(&meta);
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
    let mut events = Vec::new();
    let mut seen = HashSet::new();
    for row in super::sqlite::rows(path, db, query.sql, &[Param::Int(last)])?.unwrap_or_default() {
        let Some(reading) = apply_rules(spec, &row, 0, &mut slots) else { continue };
        let s = scope(&row, 0, &slots);
        let (Some(input), Some(output)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) else { continue };
        if input == 0 && output == 0 {
            continue;
        }
        let key = expr::text(&reading.key, &s);
        if full && !seen.insert(key.clone()) {
            continue;
        }
        last = last.max(py_int(row.get(query.id)));
        events.push(event(vendor, path, reading, &s, input, output, key, cursor(last)));
    }
    Ok(UsageRead { events, checkpoint: cursor(last) })
}

#[allow(clippy::too_many_arguments)]
fn counters(
    spec: &'static UsageSpec,
    db: &'static Sqlite,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    checkpoint: &Map<String, Value>,
    full: bool,
    (kind, map, columns): (&'static str, &'static str, &'static [&'static str]),
) -> Result<UsageRead, Error> {
    let unchanged = || Ok(UsageRead { events: Vec::new(), checkpoint: checkpoint.clone() });
    let Some(query) = db.usage.as_ref() else { return unchanged() };
    let Some(rows) = super::sqlite::rows(path, db, query.sql, &[])? else { return unchanged() };
    let empty = Map::new();
    let last = match checkpoint.get(map) {
        Some(Value::Object(last)) if !full && checkpoint.get("kind").and_then(Value::as_str) == Some(kind) => last,
        _ => &empty,
    };
    // `int(v or 0)`.
    let int = |v: Option<&Value>| v.filter(|v| expr::truthy(v)).and_then(expr::py_int_raw).unwrap_or(0);
    let (mut events, mut state, mut seen) = (Vec::new(), Map::new(), HashSet::new());
    for mut row in rows {
        let key = expr::py_str_opt(row.get(query.id));
        let now: Vec<i64> = columns.iter().map(|c| int(row.get(*c))).collect();
        let base: Vec<i64> = match last.get(&key) {
            Some(Value::Array(prev)) if prev.len() == columns.len() => prev.iter().map(|v| expr::py_int_raw(v).unwrap_or(0)).collect(),
            _ => vec![0; columns.len()],
        };
        let delta: Map<String, Value> = columns.iter().zip(now.iter().zip(&base)).map(|(c, (n, b))| ((*c).to_owned(), (n - b).max(0).into())).collect();
        let grew = delta.values().any(|d| d.as_i64() != Some(0));
        let joined = now.iter().map(i64::to_string).collect::<Vec<_>>().join(":");
        state.insert(key, now.into());
        if let Value::Object(object) = &mut row {
            object.insert("delta".into(), delta.into());
            object.insert("counters".into(), joined.into());
        }
        if !grew {
            continue;
        }
        let Some(reading) = apply_rules(spec, &row, 0, &mut slots) else { continue };
        let s = scope(&row, 0, &slots);
        let (Some(input), Some(output)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) else { continue };
        let dedup = expr::text(&reading.key, &s);
        if full && !seen.insert(dedup.clone()) {
            continue;
        }
        events.push(event(vendor, path, reading, &s, input, output, dedup, Map::new()));
    }
    let mut next = Map::new();
    next.insert("kind".into(), kind.into());
    next.insert(map.into(), state.into());
    Ok(UsageRead { events, checkpoint: next })
}

/// Per-partition watermarks: every partition whose newest id is above its
/// mark is read from the mark on, and each row read moves the mark (with or
/// without a reading). Any unreadable query leaves the checkpoint as it was.
#[allow(clippy::too_many_arguments)]
fn watermarks(
    spec: &'static UsageSpec,
    db: &'static Sqlite,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    checkpoint: &Map<String, Value>,
    full: bool,
    (kind, map): (&'static str, &'static str),
) -> Result<UsageRead, Error> {
    let unchanged = || Ok(UsageRead { events: Vec::new(), checkpoint: checkpoint.clone() });
    let Some((query, part)) = db.usage.as_ref().and_then(|q| Some((q, q.partition.as_ref()?))) else { return unchanged() };
    let mut marks: Map<String, Value> = match checkpoint.get(map) {
        Some(Value::Object(last)) if !full && checkpoint.get("kind").and_then(Value::as_str) == Some(kind) => {
            last.iter().filter(|(_, v)| v.is_i64()).map(|(k, v)| (k.clone(), v.clone())).collect()
        }
        _ => Map::new(),
    };
    let Some(tops) = super::sqlite::tops(path, db, part)? else { return unchanged() };
    let (mut events, mut seen) = (Vec::new(), HashSet::new());
    for (name, top) in tops {
        let after = marks.get(&name).and_then(Value::as_i64).unwrap_or(-1);
        if top <= after {
            continue;
        }
        let Some(rows) = super::sqlite::page(path, db, query, part, &name, after)? else { return unchanged() };
        for row in rows {
            marks.insert(name.clone(), row.get(query.id).and_then(expr::py_int_raw).unwrap_or(0).into());
            let Some(reading) = apply_rules(spec, &row, 0, &mut slots) else { continue };
            let s = scope(&row, 0, &slots);
            let (Some(input), Some(output)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) else { continue };
            let key = expr::text(&reading.key, &s);
            if full && !seen.insert(key.clone()) {
                continue;
            }
            events.push(event(vendor, path, reading, &s, input, output, key, Map::new()));
        }
    }
    let mut next = Map::new();
    next.insert("kind".into(), kind.into());
    next.insert(map.into(), marks.into());
    Ok(UsageRead { events, checkpoint: next })
}

/// `int()` of each half of a `"<in>:<out>"` mark; (0, 0) when it is not one.
fn mark_totals(value: &str) -> (i64, i64) {
    let parts: Vec<&str> = value.split(':').collect();
    let int = |s: &str| expr::py_int_raw(&Value::String(s.to_owned()));
    match parts.as_slice() {
        [a, b] => match (int(a), int(b)) {
            (Some(a), Some(b)) => (a, b),
            _ => (0, 0),
        },
        _ => (0, 0),
    }
}

fn snapshot(spec: &'static UsageSpec, vendor: &str, path: &Path, mut slots: Slots, checkpoint: &Map<String, Value>, suffix: &str, mark: &str) -> UsageRead {
    let mut seen: std::collections::BTreeSet<String> = checkpoint
        .get("legacy_seen")
        .and_then(Value::as_array)
        .map(|keys| keys.iter().map(python_str).collect())
        .unwrap_or_default();
    let mut events = Vec::new();
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let doc = std::fs::read_to_string(path.with_file_name(format!("{stem}{suffix}")))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .filter(Value::is_object);
    if let Some(doc) = doc {
        if let Some(reading) = apply_rules(spec, &doc, 0, &mut slots) {
            let s = scope(&doc, 0, &slots);
            let totals = (expr::num(&reading.input, &s).unwrap_or(0), expr::num(&reading.output, &s).unwrap_or(0));
            let marked = seen.iter().find_map(|k| k.strip_prefix(mark)).map_or((0, 0), mark_totals);
            let (delta_in, delta_out) = (totals.0 - marked.0, totals.1 - marked.1);
            let set_mark = |seen: &mut std::collections::BTreeSet<String>| {
                seen.retain(|k| !k.starts_with(mark));
                seen.insert(format!("{mark}{}:{}", totals.0, totals.1));
            };
            if delta_in < 0 || delta_out < 0 {
                set_mark(&mut seen);
            } else if delta_in != 0 || delta_out != 0 {
                slots.insert("input_total".into(), totals.0.to_string());
                slots.insert("output_total".into(), totals.1.to_string());
                let s = scope(&doc, 0, &slots);
                let key = expr::text(&reading.key, &s);
                if seen.insert(key.clone()) {
                    set_mark(&mut seen);
                    events.push(event(vendor, path, reading, &s, delta_in, delta_out, key, Map::new()));
                }
            }
        }
    }
    let mut next = Map::new();
    next.insert("kind".into(), "legacy".into());
    next.insert("legacy_seen".into(), seen.into_iter().collect::<Vec<_>>().into());
    UsageRead { events, checkpoint: next }
}

#[allow(clippy::too_many_arguments)]
fn per_record(
    spec: &'static UsageSpec,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    checkpoint: &Map<String, Value>,
    tail: Tail,
    window: usize,
    keep_on_rotation: bool,
    credited: bool,
    full: bool,
) -> UsageRead {
    let mut recent: VecDeque<String> = if tail.rotated && !keep_on_rotation {
        VecDeque::new()
    } else {
        let kept: Vec<String> = checkpoint
            .get("recent_keys")
            .and_then(Value::as_array)
            .map(|keys| keys.iter().map(python_str).collect())
            .unwrap_or_default();
        kept[kept.len().saturating_sub(window)..].iter().cloned().collect()
    };
    let mut all_seen: HashSet<String> = HashSet::new();
    let prior_raw = checkpoint.get("credited_count").filter(|v| !v.is_null());
    let prior = py_int(prior_raw);
    let mut skip_remaining = if tail.rotated { prior } else { 0 };
    let mut credited_count = if tail.rotated { 0 } else { prior };
    let legacy_reread = tail.rotated && prior_raw.is_none();
    let mut events = Vec::new();
    let remember = |recent: &mut VecDeque<String>, key: &str| {
        recent.push_back(key.to_owned());
        while recent.len() > window {
            recent.pop_front();
        }
    };
    for (end, record) in &tail.records {
        let Some(record) = record else { continue };
        let Some(reading) = apply_rules(spec, record, *end, &mut slots) else { continue };
        let s = scope(record, *end, &slots);
        let key = expr::text(&reading.key, &s);
        if (reading.key_required && key.is_empty()) || (!reading.key_invalid.is_empty() && key == reading.key_invalid) {
            continue;
        }
        let (Some(input), Some(output)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) else { continue };
        if input == 0 && output == 0 {
            continue;
        }
        if full {
            if !all_seen.insert(key.clone()) {
                continue;
            }
        } else {
            if credited && (legacy_reread || skip_remaining > 0) {
                skip_remaining = (skip_remaining - 1).max(0);
                credited_count += 1;
                remember(&mut recent, &key);
                continue;
            }
            if window > 0 && recent.contains(&key) {
                continue;
            }
            if credited {
                credited_count += 1;
            }
            if window > 0 {
                remember(&mut recent, &key);
            }
        }
        let mut cp = tail.checkpoint.clone();
        cp.insert("offset".into(), (*end).into());
        if window > 0 {
            cp.insert("recent_keys".into(), recent.iter().cloned().collect::<Vec<_>>().into());
        }
        persist_into(spec, &slots, &mut cp);
        if credited {
            cp.insert("credited_count".into(), credited_count.into());
        }
        events.push(event(vendor, path, reading, &s, input, output, key, cp));
    }
    let mut next = tail.checkpoint;
    if window > 0 {
        next.insert("recent_keys".into(), recent.into_iter().collect::<Vec<_>>().into());
    }
    persist_into(spec, &slots, &mut next);
    if credited {
        next.insert("credited_count".into(), credited_count.into());
    }
    UsageRead { events, checkpoint: next }
}

/// Python `str(k)` of a JSON value read back from a checkpoint.
fn python_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_owned(),
        other => expr::py_str(other),
    }
}

fn stamped(spec: &UsageSpec, checkpoint: &Map<String, Value>) -> bool {
    spec.stamp.iter().all(|(key, want)| checkpoint.get(*key).and_then(Value::as_f64) == Some(*want as f64))
}

/// The totals of the last totals record wholly before `offset` (a checkpoint
/// written before its stamp existed stored them in an older basis).
fn totals_before(spec: &'static UsageSpec, path: &Path, offset: u64) -> (i64, i64) {
    let Ok(data) = std::fs::read(path) else { return (0, 0) };
    let head = &data[..(offset as usize).min(data.len())];
    let mut found = (0, 0);
    let mut scratch = Slots::new();
    for raw in head.split(|b| *b == b'\n' || *b == b'\r') {
        let Some(record) = std::str::from_utf8(raw).ok().and_then(|t| serde_json::from_str::<Value>(t).ok()) else { continue };
        let Some(rule) = spec.rules.iter().find(|r| expr::pred(&r.when, &scope(&record, 0, &scratch))) else { continue };
        if let Some(reading) = &rule.reading {
            let s = scope(&record, 0, &scratch);
            if let (Some(i), Some(o)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) {
                found = (i, o);
            }
        }
        scratch.clear();
    }
    found
}

fn cumulative(
    spec: &'static UsageSpec,
    vendor: &str,
    path: &Path,
    mut slots: Slots,
    checkpoint: &Map<String, Value>,
    tail: Tail,
    restore: bool,
) -> Result<UsageRead, Error> {
    let (mut prev_in, mut prev_out) =
        if restore { (py_int(checkpoint.get("input_total")), py_int(checkpoint.get("output_total"))) } else { (0, 0) };
    if (prev_in != 0 || prev_out != 0) && !spec.stamp.is_empty() && !stamped(spec, checkpoint) {
        (prev_in, prev_out) = totals_before(spec, path, py_int(checkpoint.get("offset")) as u64);
    }
    let (mut latest_in, mut latest_out) = (prev_in, prev_out);
    let mut latest: Option<(&Value, &'static Reading, u64)> = None;
    for (end, record) in &tail.records {
        let Some(record) = record else { continue };
        let Some(reading) = apply_rules(spec, record, *end, &mut slots) else { continue };
        let s = scope(record, *end, &slots);
        let (Some(i), Some(o)) = (expr::num(&reading.input, &s), expr::num(&reading.output, &s)) else { continue };
        (latest_in, latest_out) = (i, o);
        latest = Some((record, reading, *end));
    }
    let mut next = tail.checkpoint.clone();
    next.insert("input_total".into(), latest_in.into());
    next.insert("output_total".into(), latest_out.into());
    persist_into(spec, &slots, &mut next);
    for (key, value) in spec.stamp {
        next.insert((*key).to_owned(), (*value).into());
    }
    let Some((record, reading, end)) = latest else {
        return Ok(UsageRead { events: Vec::new(), checkpoint: next });
    };
    let (delta_in, delta_out) = (latest_in - prev_in, latest_out - prev_out);
    if delta_in < 0 || delta_out < 0 || (delta_in == 0 && delta_out == 0) {
        return Ok(UsageRead { events: Vec::new(), checkpoint: next });
    }
    slots.insert("input_total".into(), latest_in.to_string());
    slots.insert("output_total".into(), latest_out.to_string());
    let mut cp = next.clone();
    cp.insert("offset".into(), end.into());
    let s = scope(record, end, &slots);
    let key = expr::text(&reading.key, &s);
    let event = event(vendor, path, reading, &s, delta_in, delta_out, key, cp);
    Ok(UsageRead { events: vec![event], checkpoint: next })
}
