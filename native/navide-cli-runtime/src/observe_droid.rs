//! Droid observation, derived from `cli_vendors/droid.py` (G2 ticket 06, family B).
//!
//! Tokens are the delta of the totals in the session's `<id>.settings.json` against a `droid_tok::in:out` mark in
//! `seen`; activity is read from the session JSONL by line. Python keeps discovery, the sidecar path
//! (`settings_path`), the `session_start` cwd read (`cwd`), the stem (`session`), the sentinel keys of the seen
//! bag and the user-prompt wrapper stripping.
use super::observe_jsonl::{join_text_blocks, py_int, str_or_empty, text};
use serde_json::{json, Value};
use std::fs;
use std::io;

const TOKEN_MARK_PREFIX: &str = "droid_tok::";

fn token_mark(seen: &[String]) -> (i64, i64) {
    for key in seen {
        if let Some(rest) = key.strip_prefix(TOKEN_MARK_PREFIX) {
            let mut parts = rest.split(':');
            return match (parts.next(), parts.next(), parts.next()) {
                (Some(a), Some(b), None) => match (a.trim().parse(), b.trim().parse()) {
                    (Ok(a), Ok(b)) => (a, b),
                    _ => (0, 0),
                },
                _ => (0, 0),
            };
        }
    }
    (0, 0)
}

fn truthy(v: &Value) -> bool {
    !str_or_empty(v).is_empty()
}

fn has_block(content: &Value, block_type: &str) -> bool {
    content
        .as_array()
        .is_some_and(|blocks| blocks.iter().any(|b| b.is_object() && b["type"] == block_type))
}

pub fn observe(args: &Value, _expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Droid observation mode not integrated"));
    }
    // `session_id` is the sidecar's own request key (a live session); the reader's id travels as `session`.
    let (cwd, session_id) = (text(&args["cwd"]), text(&args["session"]));
    // `usage` is the base reader's legacy cursor: the seen set rides in `checkpoint.legacy_seen`.
    let source = if mode == "usage" {
        &args["checkpoint"]["legacy_seen"]
    } else {
        &args["seen"]
    };
    let mut seen: Vec<String> = source
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let base = |extra: Value, seen: &[String]| -> Value {
        let mut doc = json!({"parser":"rust-droid-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":seen,"error":null});
        if mode == "usage" {
            let mut sorted = seen.to_vec();
            sorted.sort();
            doc["checkpoint"] = json!({"kind":"legacy","legacy_seen":sorted});
        }
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
    };
    if mode != "activity" {
        let sidecar = text(&args["settings_path"]);
        // A missing, unreadable or malformed sidecar yields no events (Python returns `[]`).
        let Ok(doc) = fs::read_to_string(&sidecar)
            .map_err(|_| ())
            .and_then(|raw| serde_json::from_str::<Value>(&raw).map_err(|_| ()))
        else {
            return Ok((base(json!({}), &seen), None));
        };
        let usage = &doc["tokenUsage"];
        if !doc.is_object() || !usage.is_object() {
            return Ok((base(json!({}), &seen), None));
        }
        let total_in = py_int(&usage["inputTokens"])
            + py_int(&usage["cacheCreationTokens"])
            + py_int(&usage["cacheReadTokens"]);
        // `thinkingTokens` is deliberately not added (inherited, unsettled in the Python comment).
        let total_out = py_int(&usage["outputTokens"]);
        let (seen_in, seen_out) = token_mark(&seen);
        let (delta_in, delta_out) = (total_in - seen_in, total_out - seen_out);
        let set_mark = |seen: &mut Vec<String>| {
            seen.retain(|k| !k.starts_with(TOKEN_MARK_PREFIX));
            seen.push(format!("{TOKEN_MARK_PREFIX}{total_in}:{total_out}"));
        };
        if delta_in < 0 || delta_out < 0 {
            set_mark(&mut seen);
            return Ok((base(json!({}), &seen), None));
        }
        if delta_in == 0 && delta_out == 0 {
            return Ok((base(json!({}), &seen), None));
        }
        let key = format!("usage::{session_id}::{total_in}::{total_out}");
        if seen.contains(&key) {
            return Ok((base(json!({}), &seen), None));
        }
        seen.push(key.clone());
        set_mark(&mut seen);
        let event = json!({"vendor":"droid","input_tokens":delta_in,"output_tokens":delta_out,"cwd":cwd,
            "session_id":session_id,"file_path":path,"dedup_key":key,"model":str_or_empty(&doc["model"])});
        return Ok((base(json!({"events":[event]}), &seen), None));
    }

    let high_water = py_int(&args["high_water"]) as usize;
    let mut last_text = text(&args["last_text"]);
    let Ok(bytes) = fs::read(path) else {
        return Ok((base(json!({"high_water":high_water,"last_text":last_text}), &seen), None));
    };
    let mut events: Vec<Value> = Vec::new();
    let mut last_line = high_water;
    let make = |event_type: &str, key: String, ts: &str, detail: &str, body: String| {
        json!({"vendor":"droid","event_type":event_type,"cwd":cwd,"session_id":session_id,"file_path":path,
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
            continue;
        }
        let ts = str_or_empty(&rec["timestamp"]);
        if rec["type"] == "agent_turn_outcome" {
            events.push(make(
                "turn_complete",
                format!("turn:{line_no}"),
                &ts,
                &str_or_empty(&rec["reason"]),
                last_text.clone(),
            ));
            last_text.clear();
            continue;
        }
        if rec["type"] != "message" || !rec["message"].is_object() {
            continue;
        }
        let msg = &rec["message"];
        let role = str_or_empty(&msg["role"]);
        let content = &msg["content"];
        let key = format!("act:{line_no}");
        if role == "assistant" {
            last_text = join_text_blocks(content);
            events.push(make("agent_active", key, &ts, "assistant", String::new()));
            continue;
        }
        let mut body = String::new();
        if role == "user" && !truthy(&msg["hookEventName"]) && !has_block(content, "tool_result") {
            last_text.clear();
            // Python: `user_prompt_text(join_text_blocks(content, "text"))`.
            body = join_text_blocks(content);
        }
        events.push(make("agent_active", key, &ts, "user", body));
    }
    Ok((
        base(json!({"events":events,"high_water":last_line,"last_text":last_text}), &seen),
        None,
    ))
}
