//! Goose observation, derived from `cli_vendors/goose.py` (G2 ticket 06, family D).
//!
//! One shared SQLite store (`sessions.db`) read through the same short-lived read-only connection OpenCode uses.
//! Python keeps discovery, the file-name gate and the user-prompt wrapper stripping; a failed query is "no new data
//! this cycle", as in the baseline.
use super::observe_jsonl::{py_int, str_or_empty, text as plain_text, utc_fields};
use super::opencode::{number, query};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

const ACTIVITY_PREFIX: &str = "goose_message::";
const MAX_MESSAGES_PER_PASS: i64 = 256;
/// `datetime(9999, 12, 31, 23, 59, 59)`: the last second Python's `fromtimestamp` accepts.
const LAST_ISO_SECOND: i64 = 253_402_300_799;

const USAGE_SQL: &str = "SELECT u.id, u.session_id, u.created_timestamp, COALESCE(u.model, ''), \
u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, COALESCE(s.working_dir, '') \
FROM usage_ledger u JOIN sessions s ON s.id = u.session_id WHERE u.id > ? ORDER BY u.id";

const MARKER_SQL: &str = "SELECT m.session_id, m.content_json, m.metadata_json, COALESCE(s.working_dir, '') \
FROM messages m JOIN sessions s ON s.id = m.session_id \
WHERE m.role = 'user' AND m.content_json LIKE '%at-pane:%' \
AND s.parent_session_id IS NULL AND s.session_type = 'user' ORDER BY m.id";

const ACTIVITY_SQL: &str = "SELECT m.id, m.session_id, m.role, m.content_json, m.metadata_json, \
m.created_timestamp, COALESCE(s.working_dir, ''), s.parent_session_id, s.session_type \
FROM messages m JOIN sessions s ON s.id = m.session_id WHERE m.id > ? ORDER BY m.id LIMIT ?";

/// `_epoch_to_iso(seconds)`: `YYYY-MM-DDTHH:MM:SS+00:00`, or `""` outside Python's range.
fn epoch_iso(seconds: i64) -> String {
    if !(0..=LAST_ISO_SECOND).contains(&seconds) {
        return String::new();
    }
    format!("{}+00:00", utc_fields(seconds))
}

fn truthy(v: &Value) -> bool {
    !str_or_empty(v).is_empty()
}

/// `_json`: the parsed value, or `None` when the text is not JSON.
fn parsed(raw: &Value) -> Option<Value> {
    serde_json::from_str(&str_or_empty(raw)).ok()
}

/// `_blocks`: the dict entries of a JSON list.
fn blocks(raw: &Value) -> Vec<Value> {
    match parsed(raw) {
        Some(Value::Array(items)) => items.into_iter().filter(Value::is_object).collect(),
        _ => Vec::new(),
    }
}

/// `_text_of`: the stripped, non-empty text blocks joined by newlines.
fn text_of(blocks: &[Value]) -> String {
    blocks
        .iter()
        .filter(|b| b["type"] == "text")
        .map(|b| str_or_empty(&b["text"]).trim().to_owned())
        .filter(|t| !t.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn activity_event(path: &str, row: &[Value]) -> Value {
    let (id, session_id, role) = (number(&row[0]), str_or_empty(&row[1]), str_or_empty(&row[2]));
    let blocks = blocks(&row[3]);
    let meta = parsed(&row[4]).filter(Value::is_object).unwrap_or(json!({}));
    let kinds: BTreeSet<String> = blocks.iter().map(|b| str_or_empty(&b["type"])).collect();
    let make = |event_type: &str, detail: &str, body: String| {
        json!({"vendor":"goose","event_type":event_type,"cwd":str_or_empty(&row[6]),"session_id":session_id,
            "file_path":path,"dedup_key":format!("message:{id}"),"timestamp":epoch_iso(number(&row[5])),
            "detail":detail,"text":body})
    };
    if role == "assistant" {
        if let Some(tool) = blocks.iter().find(|b| b["type"] == "toolRequest") {
            let name = str_or_empty(&tool["toolCall"]["value"]["name"]);
            let name = if name.is_empty() { "unknown".to_owned() } else { name };
            return make("agent_active", &format!("tool:{name}"), String::new());
        }
        if kinds.contains("actionRequired") {
            return make("agent_active", "assistant:action-required", String::new());
        }
        let body = text_of(&blocks);
        if !body.is_empty() && meta["agentVisible"] != Value::Bool(false) {
            if truthy(&row[7]) || str_or_empty(&row[8]) != "user" {
                return make("agent_active", "subagent:done", String::new());
            }
            return make("turn_complete", "assistant", body);
        }
        return make("agent_active", "assistant", String::new());
    }
    if role == "user" {
        if kinds.contains("toolResponse") {
            return make("agent_active", "tool:result", String::new());
        }
        if kinds.contains("actionRequired") {
            return make("agent_active", "user:action-response", String::new());
        }
        if truthy(&meta["turnContext"]) || meta["userVisible"] == Value::Bool(false) {
            return make("agent_active", "user:context", String::new());
        }
        return make("agent_active", "user", text_of(&blocks));
    }
    let detail = if role.is_empty() { "message".to_owned() } else { role.clone() };
    make("agent_active", &detail, String::new())
}

pub fn observe(args: &Value) -> io::Result<Value> {
    let path_text = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let path = Path::new(path_text);
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity" | "has_session" | "identify") {
        return Err(io::Error::other("Goose observation mode not integrated"));
    }
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let mut result = json!({"parser":"rust-goose-sqlite-v1","path":path_text,"mode":mode,"identity":"",
        "events":[],"checkpoint":args["checkpoint"],"seen":seen,"markers":{},"found":false,"error":null});
    let usage_rows = |after: i64| query(path, USAGE_SQL, &[json!(after)]).unwrap_or_default();
    let usage_event = |row: &[Value]| -> Option<Value> {
        let (input, output) = (number(&row[4]), number(&row[5]));
        if input == 0 && output == 0 {
            return None;
        }
        let id = number(&row[0]);
        Some(json!({"vendor":"goose","input_tokens":input,"output_tokens":output,"cwd":str_or_empty(&row[8]),
            "session_id":str_or_empty(&row[1]),"file_path":path_text,"dedup_key":format!("usage:{id}"),
            "timestamp":epoch_iso(number(&row[2])),"model":str_or_empty(&row[3]),
            "cache_read_tokens":number(&row[6]),"cache_creation_tokens":number(&row[7]),"raw":{"row_id":id}}))
    };
    match mode {
        "has_session" => {
            let wanted = plain_text(&args["wanted_session"]);
            result["found"] = json!(query(path, "SELECT 1 FROM sessions WHERE id = ?", &[json!(wanted)])
                .is_some_and(|rows| !rows.is_empty()));
        }
        "identify" => {
            let wanted: Vec<&str> = args["markers"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .filter(|s| !s.is_empty())
                .collect();
            let mut found = Map::new();
            if !wanted.is_empty() {
                for row in query(path, MARKER_SQL, &[]).unwrap_or_default() {
                    if parsed(&row[2]).is_some_and(|meta| meta.is_object() && truthy(&meta["turnContext"])) {
                        continue;
                    }
                    let body = text_of(&blocks(&row[1]));
                    for marker in &wanted {
                        if !found.contains_key(*marker) && body.contains(*marker) {
                            found.insert(
                                (*marker).to_owned(),
                                json!([str_or_empty(&row[0]), str_or_empty(&row[3])]),
                            );
                        }
                    }
                }
            }
            result["markers"] = Value::Object(found);
        }
        "session_usage" => {
            let mut events = Vec::new();
            for row in usage_rows(0) {
                let Some(event) = usage_event(&row) else { continue };
                let key = plain_text(&event["dedup_key"]);
                if seen.contains(&key) {
                    continue;
                }
                seen.push(key);
                events.push(event);
            }
            result["events"] = json!(events);
            result["seen"] = json!(seen);
        }
        "usage" => {
            let Ok(stat) = path.metadata() else {
                return Ok(result);
            };
            let identity = format!("{}:{}", stat.dev(), stat.ino());
            let prior = plain_text(&args["checkpoint"]["identity"]);
            let replaced = !prior.is_empty() && prior != identity;
            let mut last = if replaced { 0 } else { py_int(&args["checkpoint"]["row_id"]) };
            let mut events = Vec::new();
            for row in usage_rows(last) {
                let Some(mut event) = usage_event(&row) else { continue };
                last = last.max(number(&row[0]));
                event["checkpoint"] = json!({"kind":"sqlite","row_id":last,"identity":identity});
                events.push(event);
            }
            result["events"] = json!(events);
            result["identity"] = json!(identity);
            result["checkpoint"] = json!({"kind":"sqlite","row_id":last,"identity":identity});
        }
        _ => {
            let previous = seen.iter().find_map(|key| key.strip_prefix(ACTIVITY_PREFIX).map(str::to_owned));
            let remember = |seen: &mut Vec<String>, id: i64| {
                seen.retain(|key| !key.starts_with(ACTIVITY_PREFIX));
                seen.push(format!("{ACTIVITY_PREFIX}{id}"));
            };
            match previous {
                // First sight of the store: everything in it is history; skip to the newest row.
                None => {
                    if let Some(rows) = query(path, "SELECT COALESCE(MAX(id), 0) FROM messages", &[]) {
                        remember(&mut seen, rows.first().map_or(0, |row| number(&row[0])));
                    }
                }
                Some(previous) => {
                    let rows = query(
                        path,
                        ACTIVITY_SQL,
                        &[json!(number(&json!(previous))), json!(MAX_MESSAGES_PER_PASS)],
                    )
                    .unwrap_or_default();
                    if let Some(last) = rows.last() {
                        remember(&mut seen, number(&last[0]));
                        result["events"] =
                            json!(rows.iter().map(|row| activity_event(path_text, row)).collect::<Vec<_>>());
                    }
                }
            }
            result["seen"] = json!(seen);
        }
    }
    Ok(result)
}
