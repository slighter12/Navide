//! Pi session-file observation, derived from `cli_vendors/pi.py` (G2 ticket 06, family A).
//!
//! Python keeps discovery, the line-1 header read (`session`, `cwd`), the `pi_turn::`/`pi_text::`/high-water
//! sentinels, the wall clock (`now`, passed in) and the user-prompt wrapper stripping. Pi rewrites its file in
//! place, so the usage path carries the recent-id window and the durable `credited_count`.
use super::observe_jsonl::{
    cap_text, join_text_blocks, py_int, read_tail, str_or_empty, text,
};
use serde_json::{json, Map, Value};
use std::fs;
use std::io;
use std::time::UNIX_EPOCH;

const RECENT_KEYS_WINDOW: usize = 256;
const TURN_IDLE_SECONDS: f64 = 8.0;

/// `(input, output)` with cache folded into input.
fn usage_tokens(usage: &Value) -> (i64, i64) {
    (
        py_int(&usage["input"]) + py_int(&usage["cacheRead"]) + py_int(&usage["cacheWrite"]),
        py_int(&usage["output"]),
    )
}

fn usage_of(rec: &Value) -> Option<&Value> {
    let message = &rec["message"];
    let usage = if message.is_object() {
        &message["usage"]
    } else {
        &rec["usage"]
    };
    usage.is_object().then_some(usage)
}

fn model_of(rec: &Value) -> String {
    let message = &rec["message"];
    if message.is_object() && !str_or_empty(&message["model"]).is_empty() {
        return str_or_empty(&message["model"]);
    }
    str_or_empty(&rec["model"])
}

fn keep_window(recent: &mut Vec<String>) {
    if recent.len() > RECENT_KEYS_WINDOW {
        recent.drain(..recent.len() - RECENT_KEYS_WINDOW);
    }
}

fn state_flushed(state: &Map<String, Value>) -> bool {
    state.get("flushed").is_some_and(|v| !matches!(v, Value::Null | Value::Bool(false)))
}

fn state_idx(state: &Map<String, Value>) -> i64 {
    state.get("idx").map_or(0, |v| v.as_i64().unwrap_or_else(|| py_int(v)))
}

fn new_state(idx: i64, ts: &str) -> Map<String, Value> {
    json!({"idx":idx,"flushed":false,"ts":ts}).as_object().cloned().unwrap()
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Pi observation mode not integrated"));
    }
    // `session_id` is the sidecar's own request key (a live session); the reader's id travels as `session`.
    let (session_id, file_cwd) = (text(&args["session"]), text(&args["cwd"]));
    let base = |extra: Value| -> Value {
        let mut doc = json!({"parser":"rust-pi-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":args["seen"],"error":null});
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
    };
    match mode {
        "usage" => {
            let tail = read_tail(path, args, &args["checkpoint"], expected)?;
            let old = &args["checkpoint"];
            let mut recent: Vec<String> = old["recent_keys"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|k| k.as_str().map_or_else(|| k.to_string(), str::to_owned))
                .collect();
            keep_window(&mut recent);
            let prior_known = !old["credited_count"].is_null();
            let prior = py_int(&old["credited_count"]);
            let mut skip_remaining = if tail.rotated { prior } else { 0 };
            let mut credited = if tail.rotated { 0 } else { prior };
            let legacy_reread = tail.rotated && !prior_known;
            let cached = !text(&old["session_id"]).is_empty() && !tail.rotated;
            let (session_id, cwd) = if cached {
                (text(&old["session_id"]), text(&old["cwd"]))
            } else {
                (session_id, file_cwd)
            };
            let mut events = Vec::new();
            for (end, rec, _) in &tail.records {
                let Some(rec) = rec else { continue };
                if rec["type"] == "session" {
                    continue;
                }
                let Some(usage) = usage_of(rec) else { continue };
                let dedup_key = str_or_empty(&rec["id"]);
                if dedup_key.is_empty() {
                    continue;
                }
                let (input, output) = usage_tokens(usage);
                if input == 0 && output == 0 {
                    continue;
                }
                if legacy_reread || skip_remaining > 0 {
                    skip_remaining = (skip_remaining - 1).max(0);
                    credited += 1;
                    recent.push(dedup_key);
                    keep_window(&mut recent);
                    continue;
                }
                if recent.contains(&dedup_key) {
                    continue;
                }
                credited += 1;
                recent.push(dedup_key.clone());
                keep_window(&mut recent);
                let mut cursor = tail.checkpoint.clone();
                cursor.insert("offset".into(), json!(end));
                cursor.insert("recent_keys".into(), json!(recent));
                cursor.insert("session_id".into(), json!(session_id));
                cursor.insert("cwd".into(), json!(cwd));
                cursor.insert("credited_count".into(), json!(credited));
                events.push(json!({"vendor":"pi","input_tokens":input,"output_tokens":output,"cwd":cwd,
                    "session_id":session_id,"file_path":path,"dedup_key":dedup_key,
                    "timestamp":str_or_empty(&rec["timestamp"]),"model":model_of(rec),"checkpoint":cursor}));
            }
            let mut checkpoint = tail.checkpoint.clone();
            checkpoint.insert("recent_keys".into(), json!(recent));
            checkpoint.insert("session_id".into(), json!(session_id));
            checkpoint.insert("cwd".into(), json!(cwd));
            checkpoint.insert("credited_count".into(), json!(credited));
            Ok((
                json!({"parser":"rust-pi-jsonl-v1","path":path,"mode":mode,"identity":tail.identity,
                "events":events,"checkpoint":checkpoint,"seen":args["seen"],"error":null}),
                Some(tail.raw_anchor),
            ))
        }
        "session_usage" => {
            let mut seen: Vec<String> = args["seen"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect();
            let Ok(bytes) = fs::read(path) else {
                return Ok((base(json!({})), None));
            };
            let mut events = Vec::new();
            for raw in bytes.split(|b| *b == b'\n') {
                let Ok(rec) = serde_json::from_slice::<Value>(raw) else {
                    continue;
                };
                if !rec.is_object() || rec["type"] == "session" {
                    continue;
                }
                let Some(usage) = usage_of(&rec) else { continue };
                let dedup_key = str_or_empty(&rec["id"]);
                if dedup_key.is_empty() || seen.contains(&dedup_key) {
                    continue;
                }
                let (input, output) = usage_tokens(usage);
                if input == 0 && output == 0 {
                    continue;
                }
                seen.push(dedup_key.clone());
                events.push(json!({"vendor":"pi","input_tokens":input,"output_tokens":output,"cwd":file_cwd,
                    "session_id":session_id,"file_path":path,"dedup_key":dedup_key,
                    "timestamp":str_or_empty(&rec["timestamp"]),"model":model_of(&rec)}));
            }
            Ok((base(json!({"events":events,"seen":seen})), None))
        }
        _ => activity(args, path, &file_cwd, &session_id, base),
    }
}

fn activity(
    args: &Value,
    path: &str,
    cwd: &str,
    session_id: &str,
    base: impl Fn(Value) -> Value,
) -> io::Result<(Value, Option<Vec<u8>>)> {
    let high_water = py_int(&args["high_water"]) as usize;
    let mut state: Option<Map<String, Value>> = args["state"].as_object().cloned();
    let mut last_text = text(&args["last_text"]);
    let now = args["now"].as_f64().unwrap_or(0.0);
    let Ok(bytes) = fs::read(path) else {
        return Ok((
            base(json!({"high_water":high_water,"state":state,"last_text":last_text})),
            None,
        ));
    };
    let mut events: Vec<Value> = Vec::new();
    let mut last_line = high_water;
    let complete = |state: &Map<String, Value>, detail: &str, body: &str| {
        json!({"vendor":"pi","event_type":"turn_complete","cwd":cwd,"session_id":session_id,"file_path":path,
            "dedup_key":format!("turn:{}", state_idx(state)),"timestamp":str_or_empty(&state.get("ts").cloned().unwrap_or(Value::Null)),
            "detail":detail,"text":body})
    };
    let (mut start, mut line_no) = (0, 0);
    while start < bytes.len() {
        let end = bytes[start..]
            .iter()
            .position(|b| *b == b'\n')
            .map_or(bytes.len(), |p| start + p + 1);
        let raw_line = &bytes[start..end];
        start = end;
        line_no += 1;
        let line = String::from_utf8_lossy(raw_line);
        let trimmed = line.trim();
        if trimmed.is_empty() || line_no <= high_water {
            continue;
        }
        let Ok(rec) = serde_json::from_str::<Value>(trimmed) else {
            if !raw_line.ends_with(b"\n") {
                break;
            }
            last_line = line_no;
            continue;
        };
        last_line = line_no;
        if !rec.is_object() || rec["type"] != "message" {
            continue;
        }
        let message = &rec["message"];
        let role = if message.is_object() {
            str_or_empty(&message["role"])
        } else {
            String::new()
        };
        if role != "user" && role != "assistant" {
            continue;
        }
        let entry_ts = str_or_empty(&rec["timestamp"]);
        let mut body = String::new();
        if role == "user" {
            if let Some(open) = state.as_ref().filter(|s| !state_flushed(s)) {
                events.push(complete(open, "boundary", &last_text));
                last_text.clear();
            }
            let idx = state.as_ref().map_or(0, |s| state_idx(s) + 1);
            state = Some(new_state(idx, &entry_ts));
            // Python: `user_prompt_text(join_text_blocks(content, "text"))`.
            body = join_text_blocks(&message["content"]);
        } else {
            if state.as_ref().map_or(true, |s| state_flushed(s)) {
                let idx = state.as_ref().map_or(0, |s| state_idx(s) + 1);
                state = Some(new_state(idx, &entry_ts));
            }
            if let Some(open) = state.as_mut() {
                open.insert("ts".into(), json!(entry_ts));
            }
            let reply = join_text_blocks(&message["content"]).trim().to_owned();
            if !reply.is_empty() {
                last_text = cap_text(&reply);
            }
        }
        events.push(json!({"vendor":"pi","event_type":"agent_active","cwd":cwd,"session_id":session_id,
            "file_path":path,"dedup_key":format!("act:{line_no}"),"timestamp":entry_ts,"detail":role,"text":body}));
    }
    if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
        let quiet_for = fs::metadata(path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map_or(0.0, |t| now - t.as_secs_f64());
        if quiet_for >= TURN_IDLE_SECONDS {
            events.push(complete(open, "idle", &last_text));
            open.insert("flushed".into(), json!(true));
            last_text.clear();
        }
    }
    Ok((
        base(json!({"events":events,"high_water":last_line,"state":state,"last_text":last_text})),
        None,
    ))
}
