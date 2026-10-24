//! Qwen chat-record observation, derived from `cli_vendors/qwen.py` (G2 ticket 06, family A).
//!
//! Python keeps discovery, the first-line cwd read (`cwd`), the `qwen_turn::`/`qwen_text::`/high-water sentinels,
//! the wall clock (`now`, passed in) and the user-prompt wrapper stripping. A JSON line that is not an object makes
//! Python's `rec.get(...)` raise in the whole-file and activity paths; the sidecar reports that as `error` (recorded
//! as inherited, preserved) and the bridge raises after persisting what Python's `finally` persists.
use super::observe_jsonl::{cap_text, py_int, read_tail, str_or_empty, text};
use serde_json::{json, Map, Value};
use std::fs;
use std::io;
use std::time::UNIX_EPOCH;

const TURN_IDLE_SECONDS: f64 = 8.0;
const RECENT_KEYS_WINDOW: usize = 64;
const EXCLUDED_USER_SUBTYPES: [&str; 5] = [
    "mid_turn_user_message",
    "cron",
    "notification",
    "agent_mention",
    "agent_message",
];
const BG_START: &str = "background:start";
const BG_END: &str = "background:end";
const NOT_AN_OBJECT: &str = "Qwen record is not an object";

fn usage_tokens(usage: &Value) -> (i64, i64) {
    (
        py_int(&usage["promptTokenCount"]),
        py_int(&usage["candidatesTokenCount"]) + py_int(&usage["thoughtsTokenCount"]),
    )
}

/// `"\n".join(p["text"] for p in message.parts if p is a dict with a str text)`.
fn parts_text(rec: &Value) -> String {
    rec["message"]["parts"]
        .as_array()
        .map(|parts| {
            parts
                .iter()
                .filter_map(|p| p["text"].as_str().filter(|_| p.is_object()))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

/// The `\S+` run that follows `prefix` at the very start of `output` (Python `re.match`).
fn token_after<'a>(output: &'a str, prefix: &str) -> Option<&'a str> {
    let rest = output.strip_prefix(prefix)?;
    let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
    (end > 0).then(|| &rest[..end])
}

fn background_started(rec: &Value) -> Vec<String> {
    let Some(parts) = rec["message"]["parts"].as_array() else {
        return Vec::new();
    };
    parts
        .iter()
        .filter_map(|p| p["functionResponse"]["response"]["output"].as_str())
        .filter_map(|output| {
            token_after(output, "Background agent launched successfully.\ntask_id: ")
                .or_else(|| token_after(output, "Background shell started.\nid: "))
        })
        .map(str::to_owned)
        .collect()
}

/// Every `<tag>([^<]+)</tag>` capture, in order.
fn captures(body: &str, tag: &str) -> Vec<String> {
    let (open, close) = (format!("<{tag}>"), format!("</{tag}>"));
    let mut found = Vec::new();
    let mut rest = body;
    while let Some(start) = rest.find(&open) {
        let after = &rest[start + open.len()..];
        let inner_end = after.find('<').unwrap_or(after.len());
        if inner_end > 0 && after[inner_end..].starts_with(&close) {
            found.push(after[..inner_end].to_owned());
            rest = &after[inner_end + close.len()..];
        } else {
            rest = &rest[start + 1..];
        }
    }
    found
}

fn background_finished(rec: &Value) -> Vec<String> {
    if str_or_empty(&rec["subtype"]) != "notification" {
        return Vec::new();
    }
    let body = parts_text(rec);
    if !body.contains("<task-notification>") {
        return Vec::new();
    }
    match captures(&body, "status").first() {
        Some(status) if status != "running" => captures(&body, "task-id"),
        _ => Vec::new(),
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
        return Err(io::Error::other("Qwen observation mode not integrated"));
    }
    // `session_id` is the sidecar's own request key (a live session); the reader's id travels as `session`.
    let (session_id, file_cwd) = (text(&args["session"]), text(&args["cwd"]));
    let base = |extra: Value| -> Value {
        let mut doc = json!({"parser":"rust-qwen-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":args["seen"],"error":null});
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
    };
    let usage_event = |rec: &Value, input: i64, output: i64, usage: &Value, cwd: String, key: &str| {
        json!({"vendor":"qwen","input_tokens":input,"output_tokens":output,"cwd":cwd,"session_id":session_id,
            "file_path":path,"dedup_key":key,"timestamp":str_or_empty(&rec["timestamp"]),
            "model":str_or_empty(&rec["model"]),"cli_version":str_or_empty(&rec["version"]),
            "cache_read_tokens":py_int(&usage["cachedContentTokenCount"])})
    };
    match mode {
        "usage" => {
            let tail = read_tail(path, args, &args["checkpoint"], expected)?;
            let mut recent: Vec<String> = if tail.rotated {
                Vec::new()
            } else {
                args["checkpoint"]["recent_keys"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|k| k.as_str().map_or_else(|| k.to_string(), str::to_owned))
                    .collect()
            };
            let keep = |recent: &mut Vec<String>| {
                if recent.len() > RECENT_KEYS_WINDOW {
                    recent.drain(..recent.len() - RECENT_KEYS_WINDOW);
                }
            };
            keep(&mut recent);
            let mut events = Vec::new();
            for (end, rec, _) in &tail.records {
                let Some(rec) = rec else { continue };
                if rec["type"] != "assistant" || !rec["usageMetadata"].is_object() {
                    continue;
                }
                let key = str_or_empty(&rec["uuid"]);
                if key.is_empty() || recent.contains(&key) {
                    continue;
                }
                let (input, output) = usage_tokens(&rec["usageMetadata"]);
                if input == 0 && output == 0 {
                    continue;
                }
                recent.push(key.clone());
                keep(&mut recent);
                let mut cursor = tail.checkpoint.clone();
                cursor.insert("offset".into(), json!(end));
                cursor.insert("recent_keys".into(), json!(recent));
                let mut event = usage_event(
                    rec,
                    input,
                    output,
                    &rec["usageMetadata"],
                    str_or_empty(&rec["cwd"]),
                    &key,
                );
                event["checkpoint"] = Value::Object(cursor);
                events.push(event);
            }
            let mut checkpoint = tail.checkpoint.clone();
            checkpoint.insert("recent_keys".into(), json!(recent));
            Ok((
                json!({"parser":"rust-qwen-jsonl-v1","path":path,"mode":mode,"identity":tail.identity,
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
            let mut error = None;
            for raw in bytes.split(|b| *b == b'\n') {
                if raw.iter().all(u8::is_ascii_whitespace) {
                    continue;
                }
                let Ok(rec) = serde_json::from_slice::<Value>(raw) else {
                    continue;
                };
                if !rec.is_object() {
                    error = Some(NOT_AN_OBJECT);
                    events.clear();
                    break;
                }
                if rec["type"] != "assistant" || !rec["usageMetadata"].is_object() {
                    continue;
                }
                let key = str_or_empty(&rec["uuid"]);
                if key.is_empty() || seen.contains(&key) {
                    continue;
                }
                let (input, output) = usage_tokens(&rec["usageMetadata"]);
                if input == 0 && output == 0 {
                    continue;
                }
                seen.push(key.clone());
                let cwd = Some(str_or_empty(&rec["cwd"]))
                    .filter(|c| !c.is_empty())
                    .unwrap_or_else(|| file_cwd.clone());
                events.push(usage_event(&rec, input, output, &rec["usageMetadata"], cwd, &key));
            }
            Ok((base(json!({"events":events,"seen":seen,"error":error})), None))
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
    let mut error = None;
    let make = |event_type: &str, key: String, ts: &str, detail: &str, body: String| {
        json!({"vendor":"qwen","event_type":event_type,"cwd":cwd,"session_id":session_id,"file_path":path,
            "dedup_key":key,"timestamp":ts,"detail":detail,"text":body})
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
        if !rec.is_object() {
            error = Some(NOT_AN_OBJECT);
            break;
        }
        let rtype = rec["type"].as_str().unwrap_or("");
        let ts = str_or_empty(&rec["timestamp"]);
        let is_user_prompt =
            rtype == "user" && !EXCLUDED_USER_SUBTYPES.contains(&str_or_empty(&rec["subtype"]).as_str());
        if rtype == "assistant" || is_user_prompt {
            let mut body = String::new();
            if is_user_prompt {
                if let Some(open) = state.as_ref().filter(|s| !state_flushed(s)) {
                    events.push(make(
                        "turn_complete",
                        format!("turn:{}", state_idx(open)),
                        &str_or_empty(&open.get("ts").cloned().unwrap_or(Value::Null)),
                        "boundary",
                        last_text.clone(),
                    ));
                    last_text.clear();
                }
                let idx = state.as_ref().map_or(0, |s| state_idx(s) + 1);
                state = Some(new_state(idx, &ts));
                // Python: `user_prompt_text(_parts_text(rec))`.
                body = parts_text(&rec);
            } else {
                if state.as_ref().map_or(true, |s| state_flushed(s)) {
                    let idx = state.as_ref().map_or(0, |s| state_idx(s) + 1);
                    state = Some(new_state(idx, &ts));
                }
                if let Some(open) = state.as_mut() {
                    open.insert("ts".into(), json!(ts));
                }
                let reply = parts_text(&rec).trim().to_owned();
                if !reply.is_empty() {
                    last_text = cap_text(&reply);
                }
            }
            events.push(make("agent_active", format!("act:{line_no}"), &ts, rtype, body));
        }
        if rtype == "tool_result" {
            let started = background_started(&rec);
            if !started.is_empty() {
                events.push(make("agent_active", format!("bg:{line_no}"), &ts, BG_START, started.join("\n")));
            }
        } else if rtype == "user" {
            let finished = background_finished(&rec);
            if !finished.is_empty() {
                events.push(make("agent_active", format!("bg:{line_no}"), &ts, BG_END, finished.join("\n")));
            }
        }
    }
    if error.is_none() {
        if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
            let quiet_for = fs::metadata(path)
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map_or(0.0, |t| now - t.as_secs_f64());
            if quiet_for >= TURN_IDLE_SECONDS {
                events.push(make(
                    "turn_complete",
                    format!("turn:{}", state_idx(open)),
                    &str_or_empty(&open.get("ts").cloned().unwrap_or(Value::Null)),
                    "idle",
                    last_text.clone(),
                ));
                open.insert("flushed".into(), json!(true));
                last_text.clear();
            }
        }
    }
    if error.is_some() {
        events.clear();
    }
    Ok((
        base(json!({"events":events,"high_water":last_line,"state":state,"last_text":last_text,"error":error})),
        None,
    ))
}
