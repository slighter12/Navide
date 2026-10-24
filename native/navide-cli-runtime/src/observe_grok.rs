//! Grok `updates.jsonl` observation, derived from `cli_vendors/grok.py` (G2 ticket 06, family A).
//!
//! Discovery, cwd and session-id naming, the activity cursor sentinels and the user-prompt wrapper stripping stay in
//! Python. The checkpoint, the `seen` list and the activity cursor arrive in the request exactly as for codex.
use super::observe_jsonl::{cap_text, iso_utc, ordered_keys, py_int, py_num, read_tail, text};
use serde_json::{json, Value};
use std::io;

fn chunk_text(update: &Value) -> String {
    let blocks: Vec<&Value> = match &update["content"] {
        Value::Object(_) => vec![&update["content"]],
        Value::Array(items) => items.iter().collect(),
        _ => return String::new(),
    };
    blocks
        .into_iter()
        .filter(|b| b.is_object() && b["type"] == "text")
        .map(|b| match &b["text"] {
            Value::Null => String::new(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        })
        .collect()
}

struct Event<'a> {
    kind: String,
    event_id: String,
    session_id: String,
    update: &'a Value,
    timestamp: String,
}

fn decode(record: &Option<Value>) -> Option<Event<'_>> {
    let record = record.as_ref()?;
    let params = record["params"].as_object().map(|_| &record["params"])?;
    let update = params["update"].as_object().map(|_| &params["update"])?;
    let meta = &params["_meta"];
    let seconds = match meta.as_object().and_then(|m| m.get("agentTimestampMs")) {
        Some(ms) if !ms.is_null() => py_num(ms).map(|v| v / 1000.0).unwrap_or(0.0),
        _ => py_num(&record["timestamp"]).unwrap_or(0.0),
    };
    Some(Event {
        kind: text(&update["sessionUpdate"]),
        event_id: if meta.is_object() {
            text(&meta["eventId"])
        } else {
            String::new()
        },
        session_id: text(&params["sessionId"]),
        update,
        timestamp: iso_utc(seconds),
    })
}

impl Event<'_> {
    fn key(&self, prefix: &str, fallback: u64) -> String {
        let ident = if self.event_id.is_empty() {
            format!("{}@{fallback}", self.session_id)
        } else {
            self.event_id.clone()
        };
        format!("{prefix}:{ident}")
    }
}

fn model_of(raw: &[u8], usage: &Value) -> String {
    if usage["modelUsage"]
        .as_object()
        .is_some_and(|m| !m.is_empty())
    {
        return ordered_keys(raw, &["params", "update", "usage", "modelUsage"])
            .into_iter()
            .next()
            .unwrap_or_default();
    }
    String::new()
}

fn usage_event(
    event: &Event<'_>,
    raw: &[u8],
    path: &str,
    cwd: &str,
    offset: u64,
    checkpoint: &Value,
) -> Option<Value> {
    let usage = &event.update["usage"];
    if !usage.is_object() {
        return None;
    }
    let (input, output) = (py_int(&usage["inputTokens"]), py_int(&usage["outputTokens"]));
    if input == 0 && output == 0 {
        return None;
    }
    Some(json!({"vendor":"grok","input_tokens":input,"output_tokens":output,"cwd":cwd,
        "session_id":event.session_id,"file_path":path,"dedup_key":event.key("usage", offset),
        "timestamp":event.timestamp,"model":model_of(raw, usage),"checkpoint":checkpoint}))
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity" | "turns") {
        return Err(io::Error::other("Grok observation mode not integrated"));
    }
    let cwd = text(&args["cwd"]);
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    // A whole-file read starts from nothing; the others resume from the saved cursor.
    let saved = if matches!(mode, "usage" | "activity") {
        args["checkpoint"].clone()
    } else {
        json!({})
    };
    let unchanged = |error: Option<String>| -> (Value, Option<Vec<u8>>) {
        (
            json!({"parser":"rust-grok-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":seen,"pending":text(&args["pending"]),"turns":[],"error":error}),
            None,
        )
    };
    // Python answers an unreadable transcript with no events and the checkpoint it was given.
    let Ok(tail) = read_tail(path, args, &saved, expected) else {
        return Ok(unchanged(None));
    };
    let mut events = Vec::new();
    let mut turns = Vec::new();
    let mut pending = if tail.rotated {
        String::new()
    } else {
        text(&args["pending"])
    };
    let mut turn_chunks: Vec<String> = Vec::new();
    let mut turn_started: Option<String> = None;
    let filter = text(&args["session_filter"]);
    for (offset, record, raw) in &tail.records {
        let Some(event) = decode(record) else {
            continue;
        };
        match mode {
            "usage" | "session_usage" => {
                if event.kind != "turn_completed" {
                    continue;
                }
                if mode == "usage" {
                    let mut cursor = Value::Object(tail.checkpoint.clone());
                    cursor["offset"] = json!(offset);
                    events.extend(usage_event(&event, raw, path, &cwd, *offset, &cursor));
                } else if let Some(usage) =
                    usage_event(&event, raw, path, &cwd, *offset, &json!({}))
                {
                    let key = text(&usage["dedup_key"]);
                    if !seen.contains(&key) {
                        seen.push(key);
                        events.push(usage);
                    }
                }
            }
            "activity" => {
                if event.kind == "agent_message_chunk" {
                    let piece = chunk_text(event.update);
                    if !piece.is_empty() {
                        pending = cap_text(&(pending.clone() + &piece));
                    }
                }
                if event.kind == "turn_completed" {
                    let detail = match event.update["stop_reason"] {
                        Value::Null => "end_turn".to_owned(),
                        ref v => match v.as_str() {
                            Some("") => "end_turn".to_owned(),
                            Some(s) => s.to_owned(),
                            None => v.to_string(),
                        },
                    };
                    events.push(json!({"vendor":"grok","event_type":"turn_complete","cwd":cwd,
                        "session_id":event.session_id,"file_path":path,"dedup_key":event.key("turn", *offset),
                        "timestamp":event.timestamp,"detail":detail,"text":pending}));
                    pending.clear();
                    continue;
                }
                if event.kind != "user_message_chunk" && event.kind != "agent_message_chunk" {
                    continue;
                }
                let user = event.kind == "user_message_chunk";
                events.push(json!({"vendor":"grok","event_type":"agent_active","cwd":cwd,
                    "session_id":event.session_id,"file_path":path,"dedup_key":event.key("act", *offset),
                    "timestamp":event.timestamp,"detail":if user {"user"} else {"assistant"},
                    // Python strips the injected wrappers (`user_prompt_text`) from the raw chunk.
                    "text":if user { chunk_text(event.update) } else { String::new() }}));
            }
            _ => {
                if !filter.is_empty() && event.session_id != filter {
                    continue;
                }
                if event.kind == "user_message_chunk" {
                    if turn_started.is_none() {
                        turn_started = Some(event.timestamp.clone()).filter(|t| !t.is_empty());
                    }
                    turn_chunks.push(chunk_text(event.update));
                    continue;
                }
                if event.kind != "turn_completed" || !event.update["usage"].is_object() {
                    continue;
                }
                let usage = &event.update["usage"];
                let parts: Vec<Value> = if usage["modelUsage"]
                    .as_object()
                    .is_some_and(|m| !m.is_empty())
                {
                    ordered_keys(raw, &["params", "update", "usage", "modelUsage"])
                        .into_iter()
                        .map(|model| json!([model, usage["modelUsage"][&model]]))
                        .collect()
                } else {
                    vec![json!(["", usage])]
                };
                turns.push(json!({"session_id":event.session_id,"started_at":turn_started,
                    "ended_at":Some(event.timestamp.clone()).filter(|t| !t.is_empty()),
                    "chunks":turn_chunks,
                    "model_calls":py_int(&usage["modelCalls"]),"parts":parts}));
                turn_chunks = Vec::new();
                turn_started = None;
            }
        }
    }
    let anchor = tail.raw_anchor.clone();
    Ok((
        json!({"parser":"rust-grok-jsonl-v1","path":path,"mode":mode,"identity":tail.identity,"events":events,
        "checkpoint":tail.checkpoint,"seen":seen,"pending":pending,"turns":turns,"error":null}),
        Some(anchor),
    ))
}
