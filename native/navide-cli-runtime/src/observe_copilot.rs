//! Copilot observation, derived from `cli_vendors/copilot.py` (G2 ticket 06, family D).
//!
//! Two layouts, dispatched on the file name as the baseline does: the central `session-store.db` (1.0.78+) and the
//! per-session `events.jsonl` (1.0.75). Python keeps discovery, the `workspace.yaml` cwd (`cwd`, resolved by
//! `yaml.safe_load`), the session directory name (`session`), the "store owns this session" gate, the sentinel keys
//! of the activity bag and the user-prompt wrapper stripping. A failed store query reports `error` in the document;
//! Python raises the baseline's once-per-process warning from it and returns no data for the cycle.
use super::observe_jsonl::{py_int, read_tail, str_or_empty, text};
use super::opencode::{number, query_checked};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::fs;
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

const DB_NAME: &str = "session-store.db";
const CUM_PREFIX: &str = "__cum__:";
const USAGE_MARK_PREFIX: &str = "db_act_hw::usage:";
const TURN_MARK_PREFIX: &str = "db_act_hw::turn:";

const USAGE_SQL: &str = "SELECT a.id, a.session_id, a.model, a.input_tokens, a.output_tokens, a.created_at, \
COALESCE(s.cwd, '') FROM assistant_usage_events a LEFT JOIN sessions s ON s.id = a.session_id ORDER BY a.id";
const ANCHOR_SQL: &str = "SELECT session_id, model, input_tokens, output_tokens, created_at \
FROM assistant_usage_events WHERE id = ?";
const TURNS_SQL: &str = "SELECT t.id, t.session_id, t.user_message, t.assistant_response, t.timestamp, \
COALESCE(s.cwd, '') FROM turns t LEFT JOIN sessions s ON s.id = t.session_id ORDER BY t.id";

/// Python `str(v)` of one SQLite value, for the row fingerprint.
fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `_anchor`: the columns of `_ANCHOR_SQL` joined by `|`.
fn row_anchor(values: &[Value]) -> String {
    values.iter().map(py_str).collect::<Vec<_>>().join("|")
}

/// A store whose failed queries are remembered (the first one) instead of raised.
struct Store<'a> {
    path: &'a Path,
    error: RefCell<Option<String>>,
}

impl Store<'_> {
    fn query(&self, sql: &str, parameters: &[Value]) -> Option<Vec<Vec<Value>>> {
        match query_checked(self.path, sql, parameters) {
            Ok(rows) => Some(rows),
            Err(code) => {
                self.error
                    .borrow_mut()
                    .get_or_insert_with(|| format!("sqlite error {code} in `{}`", &sql[..sql.len().min(48)]));
                None
            }
        }
    }
}

fn read_mark(seen: &[String], prefix: &str) -> Option<i64> {
    seen.iter()
        .find_map(|key| key.strip_prefix(prefix))
        .and_then(|rest| rest.trim().parse().ok())
}

fn write_mark(seen: &mut Vec<String>, prefix: &str, value: i64) {
    seen.retain(|key| !key.starts_with(prefix));
    seen.push(format!("{prefix}{value}"));
}

/// `_db_watermark`: the id a table was read up to, back to 0 when the table shrank under it.
fn db_watermark(store: &Store, seen: &mut Vec<String>, prefix: &str, max_sql: &str) -> i64 {
    let Some(mark) = read_mark(seen, prefix) else { return 0 };
    let mark = mark.max(0);
    if mark == 0 {
        return 0;
    }
    let Some(top) = store.query(max_sql, &[]) else { return mark };
    if top.first().map_or(0, |row| number(&row[0])) < mark {
        write_mark(seen, prefix, 0);
        return 0;
    }
    mark
}

/// `_metrics_totals`: cumulative `(input, output)` of a metrics-bearing event's `data`.
fn metrics_totals(data: &Value) -> Option<(i64, i64)> {
    if let Some(metrics) = data["modelMetrics"].as_object().filter(|m| !m.is_empty()) {
        let (mut input, mut output, mut usable) = (0, 0, false);
        for per_model in metrics.values() {
            if per_model["usage"].is_object() {
                usable = true;
                input += py_int(&per_model["usage"]["inputTokens"]);
                output += py_int(&per_model["usage"]["outputTokens"]);
            }
        }
        if usable {
            return Some((input, output));
        }
    }
    let details = &data["tokenDetails"];
    details.is_object().then(|| {
        let bucket = |name: &str| {
            if details[name].is_object() {
                py_int(&details[name]["tokenCount"])
            } else {
                0
            }
        };
        (
            bucket("input") + bucket("cache_read") + bucket("cache_write"),
            bucket("output"),
        )
    })
}

/// `_read_cumulative`: `(in, out)` from the first parsable `__cum__:in=N,out=M` key.
fn read_cumulative(seen: &[String]) -> (i64, i64) {
    for key in seen {
        let Some(body) = key.strip_prefix(CUM_PREFIX) else { continue };
        let mut parts = std::collections::HashMap::new();
        let mut valid = true;
        for pair in body.split(',') {
            match pair.split_once('=') {
                Some((name, value)) if !value.contains('=') => {
                    parts.insert(name, value);
                }
                _ => valid = false,
            }
        }
        let number_of = |name: &str| match parts.get(name) {
            None => Some(0),
            Some(value) => value.trim().parse::<i64>().ok(),
        };
        if let (true, Some(input), Some(output)) = (valid, number_of("in"), number_of("out")) {
            return (input, output);
        }
    }
    (0, 0)
}

fn write_cumulative(seen: &mut Vec<String>, input: i64, output: i64) {
    seen.retain(|key| !key.starts_with(CUM_PREFIX));
    seen.push(format!("{CUM_PREFIX}in={input},out={output}"));
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path_text = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let path = Path::new(path_text);
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Copilot observation mode not integrated"));
    }
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let base = |extra: Value, seen: &[String], error: Option<String>| -> Value {
        let mut doc = json!({"parser":"rust-copilot-jsonl-v1","path":path_text,"mode":mode,"identity":"",
            "events":[],"checkpoint":args["checkpoint"],"seen":seen,"error":error});
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
    };
    if path.file_name().and_then(|name| name.to_str()) == Some(DB_NAME) {
        return observe_store(args, path_text, mode, &mut seen, base).map(|doc| (doc, None));
    }
    observe_events(args, path_text, mode, &mut seen, expected, base)
}

fn observe_store(
    args: &Value,
    path_text: &str,
    mode: &str,
    seen: &mut Vec<String>,
    base: impl Fn(Value, &[String], Option<String>) -> Value,
) -> io::Result<Value> {
    let path = Path::new(path_text);
    let store = Store { path, error: RefCell::new(None) };
    let failed = |store: &Store| store.error.borrow().clone();
    let usage_event = |row: &[Value], checkpoint: Value| -> Option<Value> {
        let (input, output) = (py_int(&row[3]), py_int(&row[4]));
        if input == 0 && output == 0 {
            return None;
        }
        Some(json!({"vendor":"copilot","input_tokens":input,"output_tokens":output,"cwd":str_or_empty(&row[6]),
            "session_id":str_or_empty(&row[1]),"file_path":path_text,
            "dedup_key":format!("copilot_usage::{}", py_str(&row[0])),"timestamp":str_or_empty(&row[5]),
            "model":str_or_empty(&row[2]),"checkpoint":checkpoint}))
    };
    match mode {
        "session_usage" => {
            let Some(rows) = store.query(USAGE_SQL, &[]) else {
                return Ok(base(json!({}), seen, failed(&store)));
            };
            let mut events = Vec::new();
            for row in rows {
                let key = format!("copilot_usage::{}", py_str(&row[0]));
                if seen.contains(&key) {
                    continue;
                }
                seen.push(key);
                events.extend(usage_event(&row, json!({})));
            }
            Ok(base(json!({"events":events,"seen":seen}), seen, None))
        }
        "usage" => {
            let Ok(stat) = path.metadata() else {
                return Ok(base(json!({}), seen, None));
            };
            let checkpoint = &args["checkpoint"];
            let identity = format!("{}:{}", stat.dev(), stat.ino());
            let prior = str_or_empty(&checkpoint["identity"]);
            let replaced = !prior.is_empty() && prior != identity;
            let mut last = if replaced { 0 } else { py_int(&checkpoint["row_id"]) };
            let mut anchor = if replaced { String::new() } else { str_or_empty(&checkpoint["anchor"]) };
            if last > 0 && !anchor.is_empty() {
                // The same dev:ino is not proof of the same store: re-anchor just below a mismatching row.
                if let Some(current) = store.query(ANCHOR_SQL, &[json!(last)]) {
                    if current.first().is_some_and(|row| row_anchor(row) != anchor) {
                        last -= 1;
                        anchor.clear();
                    }
                }
            }
            let sql = USAGE_SQL.replace("ORDER BY a.id", &format!("WHERE a.id > {last} ORDER BY a.id"));
            let Some(rows) = store.query(&sql, &[]) else {
                return Ok(base(json!({}), seen, failed(&store)));
            };
            if rows.is_empty() && last > 0 {
                if let Some(top) = store.query("SELECT COALESCE(MAX(id), 0) FROM assistant_usage_events", &[]) {
                    let max = top.first().map_or(0, |row| number(&row[0]));
                    if max < last {
                        // Watermark dropped: re-anchor, never rescan.
                        last = max;
                        anchor = store
                            .query(ANCHOR_SQL, &[json!(last)])
                            .and_then(|current| current.first().map(|row| row_anchor(row)))
                            .unwrap_or_default();
                    }
                }
            }
            let mut events = Vec::new();
            let mut next = last;
            for row in &rows {
                next = next.max(number(&row[0]));
                anchor = row_anchor(&row[1..6]);
                let cursor = json!({"kind":"sqlite","row_id":next,"identity":identity,"anchor":anchor});
                events.extend(usage_event(row, cursor));
            }
            Ok(base(
                json!({"events":events,"identity":identity,
                    "checkpoint":{"kind":"sqlite","row_id":next,"identity":identity,"anchor":anchor}}),
                seen,
                failed(&store),
            ))
        }
        _ => {
            let mut events = Vec::new();
            let usage_mark = db_watermark(
                &store,
                seen,
                USAGE_MARK_PREFIX,
                "SELECT COALESCE(MAX(id), 0) FROM assistant_usage_events",
            );
            let usage_sql = USAGE_SQL.replace("ORDER BY a.id", "WHERE a.id > ? ORDER BY a.id");
            let usage_rows = store.query(&usage_sql, &[json!(usage_mark)]);
            let mut next_usage = usage_mark;
            for row in usage_rows.iter().flatten() {
                next_usage = next_usage.max(number(&row[0]));
                events.push(json!({"vendor":"copilot","event_type":"agent_active","cwd":str_or_empty(&row[6]),
                    "session_id":str_or_empty(&row[1]),"file_path":path_text,
                    "dedup_key":format!("db_act:usage:{}", py_str(&row[0])),"timestamp":str_or_empty(&row[5]),
                    "detail":"assistant","text":""}));
            }
            if usage_rows.is_some() {
                write_mark(seen, USAGE_MARK_PREFIX, next_usage);
            }
            let turn_mark = db_watermark(&store, seen, TURN_MARK_PREFIX, "SELECT COALESCE(MAX(id), 0) FROM turns");
            let turns_sql = TURNS_SQL.replace("ORDER BY t.id", "WHERE t.id > ? ORDER BY t.id");
            let turn_rows = store.query(&turns_sql, &[json!(turn_mark)]);
            let mut next_turn = turn_mark;
            for row in turn_rows.iter().flatten() {
                next_turn = next_turn.max(number(&row[0]));
                let (id, session, cwd, stamp) = (
                    py_str(&row[0]),
                    str_or_empty(&row[1]),
                    str_or_empty(&row[5]),
                    str_or_empty(&row[4]),
                );
                events.push(json!({"vendor":"copilot","event_type":"agent_active","cwd":cwd,"session_id":session,
                    "file_path":path_text,"dedup_key":format!("db_act:turn:{id}"),"timestamp":stamp,
                    "detail":"user","text":str_or_empty(&row[2])}));
                events.push(json!({"vendor":"copilot","event_type":"turn_complete","cwd":cwd,"session_id":session,
                    "file_path":path_text,"dedup_key":format!("db_turn:{id}"),"timestamp":stamp,
                    "detail":"turn_row","text":str_or_empty(&row[3])}));
            }
            if turn_rows.is_some() {
                write_mark(seen, TURN_MARK_PREFIX, next_turn);
            }
            Ok(base(json!({"events":events,"seen":seen}), seen, failed(&store)))
        }
    }
}

fn observe_events(
    args: &Value,
    path_text: &str,
    mode: &str,
    seen: &mut Vec<String>,
    expected: &[u8],
    base: impl Fn(Value, &[String], Option<String>) -> Value,
) -> io::Result<(Value, Option<Vec<u8>>)> {
    let (cwd, session_id) = (text(&args["cwd"]), text(&args["session"]));
    let cumulative_event = |input: i64, output: i64, total_in: i64, total_out: i64, stamp: &str, model: &str,
                            version: &str, cwd: &str, checkpoint: Option<Value>| {
        let mut event = json!({"vendor":"copilot","input_tokens":input,"output_tokens":output,"cwd":cwd,
            "session_id":session_id,"file_path":path_text,
            "dedup_key":format!("copilot_cumulative::{session_id}::{total_in}::{total_out}"),
            "timestamp":stamp,"model":model,"cli_version":version});
        if let Some(checkpoint) = checkpoint {
            event["checkpoint"] = checkpoint;
        }
        event
    };
    match mode {
        "usage" => {
            let tail = read_tail(path_text, args, &args["checkpoint"], expected)?;
            let checkpoint = &args["checkpoint"];
            let prior = str_or_empty(&checkpoint["identity"]);
            let replaced = tail.rotated && !prior.is_empty() && prior != tail.identity;
            let carried = |name: &str| if replaced { String::new() } else { str_or_empty(&checkpoint[name]) };
            let (prev_in, prev_out) = if replaced {
                (0, 0)
            } else {
                (py_int(&checkpoint["input_total"]), py_int(&checkpoint["output_total"]))
            };
            let (mut latest_in, mut latest_out) = (prev_in, prev_out);
            let (mut model, mut cwd_now, mut version) = (carried("model"), carried("cwd"), carried("cli_version"));
            if cwd_now.is_empty() {
                cwd_now = cwd.clone();
            }
            let mut latest: Option<&Value> = None;
            let mut latest_end = py_int(&tail.checkpoint["offset"]);
            for (end, record, _) in &tail.records {
                let Some(record) = record else { continue };
                let data = &record["data"];
                if !data.is_object() {
                    continue;
                }
                let found = str_or_empty(&data["copilotVersion"]);
                if !found.is_empty() {
                    version = found;
                }
                let Some((total_in, total_out)) = metrics_totals(data) else { continue };
                (latest_in, latest_out) = (total_in, total_out);
                latest = Some(record);
                latest_end = *end as i64;
                let current = str_or_empty(&data["currentModel"]);
                if !current.is_empty() {
                    model = current;
                }
            }
            let (next_in, next_out) = (prev_in.max(latest_in), prev_out.max(latest_out));
            let mut next = tail.checkpoint.clone();
            next.insert("input_total".into(), json!(next_in));
            next.insert("output_total".into(), json!(next_out));
            next.insert("cwd".into(), json!(cwd_now));
            next.insert("model".into(), json!(model));
            next.insert("cli_version".into(), json!(version));
            let (delta_in, delta_out) = (latest_in - prev_in, latest_out - prev_out);
            let mut events = Vec::new();
            if let Some(record) = latest {
                if delta_in >= 0 && delta_out >= 0 && !(delta_in == 0 && delta_out == 0) {
                    let mut cursor = next.clone();
                    cursor.insert("offset".into(), json!(latest_end));
                    events.push(cumulative_event(
                        delta_in,
                        delta_out,
                        next_in,
                        next_out,
                        &str_or_empty(&record["timestamp"]),
                        &model,
                        &version,
                        &cwd_now,
                        Some(Value::Object(cursor)),
                    ));
                }
            }
            Ok((
                json!({"parser":"rust-copilot-jsonl-v1","path":path_text,"mode":mode,"identity":tail.identity,
                    "events":events,"checkpoint":next,"seen":args["seen"],"error":null}),
                Some(tail.raw_anchor),
            ))
        }
        "session_usage" => {
            let Ok(bytes) = fs::read(path_text) else {
                return Ok((base(json!({}), seen, None), None));
            };
            let (prev_in, prev_out) = read_cumulative(seen);
            let (mut latest_in, mut latest_out) = (prev_in, prev_out);
            let (mut latest_stamp, mut found, mut model, mut version) = (String::new(), false, String::new(), String::new());
            for raw in String::from_utf8_lossy(&bytes).split('\n') {
                let raw = raw.trim();
                let Ok(record) = serde_json::from_str::<Value>(raw) else { continue };
                let data = &record["data"];
                if raw.is_empty() || !data.is_object() {
                    continue;
                }
                let current = str_or_empty(&data["copilotVersion"]);
                if !current.is_empty() {
                    version = current;
                }
                let Some((total_in, total_out)) = metrics_totals(data) else { continue };
                (latest_in, latest_out) = (total_in, total_out);
                found = true;
                latest_stamp = str_or_empty(&record["timestamp"]);
                let current = str_or_empty(&data["currentModel"]);
                if !current.is_empty() {
                    model = current;
                }
            }
            if !found {
                return Ok((base(json!({}), seen, None), None));
            }
            let (delta_in, delta_out) = (latest_in - prev_in, latest_out - prev_out);
            if delta_in < 0 || delta_out < 0 {
                write_cumulative(seen, latest_in, latest_out);
                return Ok((base(json!({"seen":seen}), seen, None), None));
            }
            if delta_in == 0 && delta_out == 0 {
                return Ok((base(json!({}), seen, None), None));
            }
            write_cumulative(seen, latest_in, latest_out);
            let event = cumulative_event(delta_in, delta_out, latest_in, latest_out, &latest_stamp, &model, &version, &cwd, None);
            Ok((base(json!({"events":[event],"seen":seen}), seen, None), None))
        }
        _ => {
            let high_water = py_int(&args["high_water"]) as usize;
            let mut last_text = text(&args["last_text"]);
            let Ok(bytes) = fs::read(path_text) else {
                return Ok((
                    base(json!({"high_water":high_water,"last_text":last_text,"opened":false,"text_changed":false}), seen, None),
                    None,
                ));
            };
            let mut events: Vec<Value> = Vec::new();
            let (mut last_line, mut text_changed) = (high_water, false);
            let make = |event_type: &str, key: String, stamp: &str, detail: &str, body: String| {
                json!({"vendor":"copilot","event_type":event_type,"cwd":cwd,"session_id":session_id,
                    "file_path":path_text,"dedup_key":key,"timestamp":stamp,"detail":detail,"text":body})
            };
            let content = String::from_utf8_lossy(&bytes);
            for (index, raw_line) in content.split_inclusive('\n').enumerate() {
                let line_no = index + 1;
                let raw = raw_line.trim();
                if raw.is_empty() || line_no <= high_water {
                    continue;
                }
                let Ok(record) = serde_json::from_str::<Value>(raw) else {
                    // An unterminated final line is still being written: leave the mark behind it.
                    if !raw_line.ends_with('\n') {
                        break;
                    }
                    last_line = line_no;
                    continue;
                };
                last_line = line_no;
                let record_type = str_or_empty(&record["type"]);
                let data = if record["data"].is_object() { &record["data"] } else { &Value::Null };
                let stamp = str_or_empty(&record["timestamp"]);
                let key = format!("act:{line_no}");
                if record_type == "user.message" {
                    events.push(make("agent_active", key, &stamp, "user", str_or_empty(&data["content"])));
                } else if record_type == "assistant.message" {
                    let body = str_or_empty(&data["content"]);
                    if !body.is_empty() {
                        last_text = body;
                        text_changed = true;
                    }
                    events.push(make("agent_active", key, &stamp, "assistant", String::new()));
                } else if record_type.starts_with("tool.") {
                    events.push(make("agent_active", key, &stamp, &record_type, String::new()));
                } else if record_type == "assistant.turn_end" {
                    events.push(make(
                        "turn_complete",
                        format!("turn:{line_no}"),
                        &stamp,
                        "turn_end",
                        last_text.clone(),
                    ));
                    last_text.clear();
                    text_changed = true;
                }
            }
            Ok((
                base(
                    json!({"events":events,"high_water":last_line,"last_text":last_text,"opened":true,
                        "text_changed":text_changed}),
                    seen,
                    None,
                ),
                None,
            ))
        }
    }
}
