//! Token usage from a JSONL tail, in main's checkpoint shape. Two modes:
//! one event per usage record (deduplicated through a bounded key window),
//! or one event per read carrying the delta of running totals.
use super::jsonl::{self, Tail};
use super::{Error, UsageEvent, UsageRead};
use crate::decl::{RawValue, Reading, Restore, UsageMode, UsageSpec};
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
pub fn read(spec: &'static UsageSpec, vendor: &str, path: &Path, mut slots: Slots, checkpoint: &Map<String, Value>, full: bool) -> Result<UsageRead, Error> {
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
    }
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
