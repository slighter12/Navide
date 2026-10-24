//! Kimi `wire.jsonl` observation, derived from `cli_vendors/kimi.py` (G2 ticket 06, family A).
//!
//! Python keeps discovery, `state.json` (cwd), the sentinel keys of the watcher's `seen_keys` bag, the wall clock
//! (`now_ms` is passed in, so the eight-second idle branch can be exercised on its own) and the user-prompt text
//! (`join_text_blocks` + `user_prompt_text`, applied to the raw `input` this module returns).
use super::observe_jsonl::{cap_text, py_int, read_tail, str_or_empty, text};
use serde_json::{json, Map, Value};
use std::fs;
use std::io;

const TURN_IDLE_MS: i64 = 8_000;

/// Python `str(int(v))` or `""`.
fn ts(v: &Value) -> String {
    match v {
        Value::Bool(b) => i64::from(*b).to_string(),
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f.trunc() as i64))
            .map(|n| n.to_string())
            .unwrap_or_default(),
        Value::String(s) => s
            .trim()
            .parse::<i64>()
            .map(|n| n.to_string())
            .unwrap_or_default(),
        _ => String::new(),
    }
}

/// `(input, output)` with cache folded into input.
fn usage_tokens(usage: &Value) -> (i64, i64) {
    (
        py_int(&usage["inputOther"])
            + py_int(&usage["inputCacheRead"])
            + py_int(&usage["inputCacheCreation"]),
        py_int(&usage["output"]),
    )
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false)) && !str_or_empty(v).is_empty()
}

/// The lines of a text-mode read: `(1-based number, line with its terminator)`.
fn lines(path: &str) -> io::Result<Vec<(usize, Vec<u8>)>> {
    let bytes = fs::read(path)?;
    let mut out = Vec::new();
    let mut start = 0;
    while start < bytes.len() {
        let end = bytes[start..]
            .iter()
            .position(|b| *b == b'\n')
            .map_or(bytes.len(), |p| start + p + 1);
        out.push((out.len() + 1, bytes[start..end].to_vec()));
        start = end;
    }
    Ok(out)
}

fn parse_line(raw: &[u8]) -> Option<Result<Value, ()>> {
    let trimmed = String::from_utf8_lossy(raw);
    let trimmed = trimmed.trim();
    if trimmed.is_empty() {
        return None;
    }
    Some(serde_json::from_str::<Value>(trimmed).map_err(|_| ()))
}

fn state_flushed(state: &Map<String, Value>) -> bool {
    truthy(&state.get("flushed").cloned().unwrap_or(Value::Null))
}

fn state_int(state: &Map<String, Value>, key: &str) -> i64 {
    state
        .get(key)
        .map(|v| match v {
            Value::Number(n) => n
                .as_i64()
                .or_else(|| n.as_f64().map(|f| f.trunc() as i64))
                .unwrap_or(0),
            other => py_int(other),
        })
        .unwrap_or(0)
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Kimi observation mode not integrated"));
    }
    let cwd = text(&args["cwd"]);
    // `session_id` is the sidecar's own request key (a live session); the reader's id travels as `session`.
    let session_id = text(&args["session"]);
    let doc = |extra: Value| -> Value {
        let mut base = json!({"parser":"rust-kimi-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":args["seen"],"error":null});
        base.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        base
    };
    match mode {
        "usage" => {
            let tail = read_tail(path, args, &args["checkpoint"], expected)?;
            let cwd = if tail.rotated || text(&args["checkpoint"]["cwd"]).is_empty() {
                cwd
            } else {
                text(&args["checkpoint"]["cwd"])
            };
            let mut checkpoint = tail.checkpoint.clone();
            let mut events = Vec::new();
            for (end, rec, _) in &tail.records {
                let Some(rec) = rec else { continue };
                if rec["type"] != "usage.record" || !rec["usage"].is_object() {
                    continue;
                }
                let (input, output) = usage_tokens(&rec["usage"]);
                if input == 0 && output == 0 {
                    continue;
                }
                let mut cursor = checkpoint.clone();
                cursor.insert("offset".into(), json!(end));
                cursor.insert("cwd".into(), json!(cwd));
                events.push(json!({"vendor":"kimi","input_tokens":input,"output_tokens":output,"cwd":cwd,
                    "session_id":session_id,"file_path":path,"dedup_key":format!("kimi::{session_id}::@{end}"),
                    "timestamp":ts(&rec["time"]),"model":str_or_empty(&rec["model"]),"checkpoint":cursor}));
            }
            // A JSON line that is not an object is skipped here, as `parse_incremental`'s `rec is None` does.
            checkpoint.insert("cwd".into(), json!(cwd));
            Ok((
                json!({"parser":"rust-kimi-jsonl-v1","path":path,"mode":mode,"identity":tail.identity,
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
            let Ok(all) = lines(path) else {
                return Ok((doc(json!({})), None));
            };
            let mut events = Vec::new();
            let mut error = None;
            for (line_no, raw) in all {
                let rec = match parse_line(&raw) {
                    None | Some(Err(())) => continue,
                    Some(Ok(rec)) => rec,
                };
                if !rec.is_object() {
                    // Python's `rec.get(...)` raises on a JSON value that is not an object (recorded, preserved).
                    error = Some("Kimi record is not an object");
                    events.clear();
                    break;
                }
                if rec["type"] != "usage.record" || !rec["usage"].is_object() {
                    continue;
                }
                let key = format!("kimi::{session_id}::L{line_no}");
                if seen.contains(&key) {
                    continue;
                }
                let (input, output) = usage_tokens(&rec["usage"]);
                if input == 0 && output == 0 {
                    continue;
                }
                seen.push(key.clone());
                events.push(json!({"vendor":"kimi","input_tokens":input,"output_tokens":output,"cwd":cwd,
                    "session_id":session_id,"file_path":path,"dedup_key":key,
                    "timestamp":ts(&rec["time"]),"model":str_or_empty(&rec["model"])}));
            }
            Ok((doc(json!({"events":events,"seen":seen,"error":error})), None))
        }
        _ => activity(args, path, &cwd, &session_id, doc),
    }
}

fn activity(
    args: &Value,
    path: &str,
    cwd: &str,
    session_id: &str,
    doc: impl Fn(Value) -> Value,
) -> io::Result<(Value, Option<Vec<u8>>)> {
    let high_water = py_int(&args["high_water"]) as usize;
    let mut state: Option<Map<String, Value>> = args["state"].as_object().cloned();
    let mut last_text = text(&args["last_text"]);
    let now_ms = args["now_ms"].as_i64().unwrap_or(0);
    let Ok(all) = lines(path) else {
        return Ok((
            doc(json!({"high_water":high_water,"state":state,"last_text":last_text})),
            None,
        ));
    };
    let mut events: Vec<Value> = Vec::new();
    let mut last_line = high_water;
    let mut error = None;
    let make = |event_type: &str, key: String, stamp: String, detail: &str, body: String| {
        json!({"vendor":"kimi","event_type":event_type,"cwd":cwd,"session_id":session_id,"file_path":path,
            "dedup_key":key,"timestamp":stamp,"detail":detail,"text":body})
    };
    for (line_no, raw) in &all {
        let line_no = *line_no;
        if line_no <= high_water {
            continue;
        }
        let rec = match parse_line(raw) {
            None => continue,
            Some(Err(())) => {
                // An unterminated last line is still being written: leave the mark behind it.
                if !raw.ends_with(b"\n") {
                    break;
                }
                last_line = line_no;
                continue;
            }
            Some(Ok(rec)) => rec,
        };
        last_line = line_no;
        if !rec.is_object() {
            error = Some("Kimi record is not an object");
            break;
        }
        let key = format!("act:{line_no}");
        let rtype = rec["type"].as_str().unwrap_or("");
        let stamp = ts(&rec["time"]);
        let tms = py_int(&rec["time"]);
        match rtype {
            "turn.prompt" => {
                if let Some(open) = state.as_ref().filter(|s| !state_flushed(s)) {
                    events.push(make(
                        "turn_complete",
                        format!("turn:{}", state_int(open, "idx")),
                        ts(&json!(state_int(open, "last_ms"))),
                        "boundary",
                        last_text.clone(),
                    ));
                    last_text.clear();
                }
                let idx = state.as_ref().map_or(0, |s| state_int(s, "idx") + 1);
                state = Some(
                    json!({"idx":idx,"last_ms":tms,"flushed":false})
                        .as_object()
                        .cloned()
                        .unwrap(),
                );
                let mut event = make("agent_active", key, stamp, "prompt", String::new());
                // Python: `user_prompt_text(join_text_blocks(input, "text"))`.
                event["input"] = rec["input"].clone();
                events.push(event);
            }
            "usage.record" => {
                if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
                    let last = state_int(open, "last_ms").max(tms);
                    open.insert("last_ms".into(), json!(last));
                }
                events.push(make("agent_active", key, stamp, "usage", String::new()));
            }
            "context.append_loop_event" => {
                if state
                    .as_ref()
                    .is_some_and(|s| truthy(&s.get("cancelled").cloned().unwrap_or(Value::Null)))
                {
                    continue;
                }
                if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
                    let last = state_int(open, "last_ms").max(tms);
                    open.insert("last_ms".into(), json!(last));
                }
                let event = &rec["event"];
                if event.is_object() && event["type"] == "content.part" {
                    let part = &event["part"];
                    if part.is_object() && matches!(part["type"].as_str(), Some("text" | "think")) {
                        let body = str_or_empty(&part["text"]).trim().to_owned();
                        if !body.is_empty() {
                            if state.as_ref().map_or(true, |s| state_flushed(s)) {
                                let idx = state.as_ref().map_or(0, |s| state_int(s, "idx") + 1);
                                state = Some(
                                    json!({"idx":idx,"last_ms":tms,"flushed":false})
                                        .as_object()
                                        .cloned()
                                        .unwrap(),
                                );
                            }
                            if part["type"] == "text" {
                                last_text = cap_text(&body);
                            }
                        }
                    }
                }
            }
            "turn.ended" => {
                if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
                    let at = state_int(open, "last_ms").max(tms);
                    events.push(make(
                        "turn_complete",
                        format!("turn:{}", state_int(open, "idx")),
                        ts(&json!(at)),
                        "turn-ended",
                        last_text.clone(),
                    ));
                    open.insert("flushed".into(), json!(true));
                    last_text.clear();
                }
            }
            "turn.cancel" => {
                if let Some(open) = state.as_mut() {
                    if !state_flushed(open) {
                        let at = state_int(open, "last_ms").max(tms);
                        events.push(make(
                            "turn_complete",
                            format!("turn:{}", state_int(open, "idx")),
                            ts(&json!(at)),
                            "cancel",
                            last_text.clone(),
                        ));
                        open.insert("flushed".into(), json!(true));
                    }
                    open.insert("cancelled".into(), json!(true));
                    last_text.clear();
                }
            }
            _ => {}
        }
    }
    if error.is_none() {
        if let Some(open) = state.as_mut().filter(|s| !state_flushed(s)) {
            if now_ms - state_int(open, "last_ms") >= TURN_IDLE_MS {
                events.push(make(
                    "turn_complete",
                    format!("turn:{}", state_int(open, "idx")),
                    ts(&json!(state_int(open, "last_ms"))),
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
        doc(json!({"events":events,"high_water":last_line,"state":state,"last_text":last_text,"error":error})),
        None,
    ))
}
