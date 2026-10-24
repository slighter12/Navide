//! Muse `session.jsonl` observation, derived from `cli_vendors/muse.py` (G2 ticket 06, family A).
//!
//! Python keeps discovery, the subagent-log exclusion, the `runtime.session.metadata` head read (`cwd`, build
//! version; passed in as `cwd` and `cli_version`), the `muse_text::` and high-water sentinels and the
//! user-prompt wrapper stripping.
use super::observe_jsonl::{cap_text, py_int, read_tail, str_or_empty, text};
use serde_json::{json, Value};
use std::fs;
use std::io;

const RECENT_KEYS_MAX: usize = 64;
const ACTIVE_KINDS: [&str; 6] = [
    "started",
    "assistant_message_committed",
    "side_effect_intent",
    "model_completed",
    "completed",
    "failed",
];

/// `datetime.fromtimestamp(micros / 1_000_000, timezone.utc).isoformat()`, or `""` when unusable.
fn iso_from_micros(v: &Value) -> String {
    let micros = match v {
        Value::Bool(b) => i64::from(*b),
        Value::Number(n) => match n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)) {
            Some(m) => m,
            None => return String::new(),
        },
        Value::String(s) => match s.trim().parse::<i64>() {
            Ok(m) => m,
            Err(_) => return String::new(),
        },
        _ => return String::new(),
    };
    let seconds = micros as f64 / 1_000_000.0;
    let whole = seconds.floor();
    let mut fraction = ((seconds - whole) * 1e6).round_ties_even() as i64;
    let mut whole = whole as i64;
    if fraction >= 1_000_000 {
        fraction -= 1_000_000;
        whole += 1;
    }
    if !(-62_135_596_800..=253_402_300_799).contains(&whole) {
        return String::new();
    }
    // Python's `isoformat()`: microseconds only when non-zero, then the `+00:00` offset.
    let base = super::observe_jsonl::utc_fields(whole);
    if fraction == 0 {
        format!("{base}+00:00")
    } else {
        format!("{base}.{fraction:06}+00:00")
    }
}

fn run_event(rec: &Value) -> Option<&Value> {
    if rec["payload_type"] != "runtime.session" {
        return None;
    }
    let event = &rec["payload"]["event"];
    event.is_object().then_some(event)
}

fn turn_prompt(rec: &Value) -> Option<String> {
    if rec["payload_type"] != "runtime.command_intake.received" {
        return None;
    }
    let command = &rec["payload"]["record"]["command"];
    if !command.is_object() || command["kind"] != "turn_submit" {
        return None;
    }
    Some(str_or_empty(&command["prompt"]))
}

fn main_usage(event: &Value) -> Option<(String, i64, i64)> {
    if event["kind"] != "goal_usage_attribution" {
        return None;
    }
    let record = &event["record"];
    if !record.is_object() || record["owner"]["requester_kind"] != "main" {
        return None;
    }
    let quantity = &record["quantity"];
    if !quantity.is_object() {
        return None;
    }
    let usage_id = str_or_empty(&record["usage_id"]);
    if usage_id.is_empty() {
        return None;
    }
    let (input, output) = (py_int(&quantity["input_tokens"]), py_int(&quantity["output_tokens"]));
    (input != 0 || output != 0).then_some((usage_id, input, output))
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Muse observation mode not integrated"));
    }
    let (cwd, cli_version, session_id) = (
        text(&args["cwd"]),
        text(&args["cli_version"]),
        // `session_id` is the sidecar's own request key (a live session); the reader's id travels as `session`.
        text(&args["session"]),
    );
    let usage_event = |id: &str, input: i64, output: i64, rec: &Value, checkpoint: Option<Value>| {
        let mut event = json!({"vendor":"muse","input_tokens":input,"output_tokens":output,"cwd":cwd,
            "session_id":session_id,"file_path":path,"dedup_key":id,
            "timestamp":iso_from_micros(&rec["recorded_at"]),"cli_version":cli_version});
        if let Some(checkpoint) = checkpoint {
            event["checkpoint"] = checkpoint;
        }
        event
    };
    let base = |extra: Value| -> Value {
        let mut doc = json!({"parser":"rust-muse-jsonl-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":args["seen"],"error":null});
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
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
                if recent.len() > RECENT_KEYS_MAX {
                    recent.drain(..recent.len() - RECENT_KEYS_MAX);
                }
            };
            keep(&mut recent);
            let mut events = Vec::new();
            for (end, rec, _) in &tail.records {
                let Some(rec) = rec else { continue };
                let Some(event) = run_event(rec) else { continue };
                let Some((id, input, output)) = main_usage(event) else {
                    continue;
                };
                if recent.contains(&id) {
                    continue;
                }
                recent.push(id.clone());
                keep(&mut recent);
                let mut cursor = tail.checkpoint.clone();
                cursor.insert("offset".into(), json!(end));
                cursor.insert("recent_keys".into(), json!(recent));
                events.push(usage_event(&id, input, output, rec, Some(Value::Object(cursor))));
            }
            let mut checkpoint = tail.checkpoint.clone();
            checkpoint.insert("recent_keys".into(), json!(recent));
            Ok((
                json!({"parser":"rust-muse-jsonl-v1","path":path,"mode":mode,"identity":tail.identity,
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
                if !rec.is_object() {
                    continue;
                }
                let Some(event) = run_event(&rec) else { continue };
                let Some((id, input, output)) = main_usage(event) else {
                    continue;
                };
                if seen.contains(&id) {
                    continue;
                }
                seen.push(id.clone());
                events.push(usage_event(&id, input, output, &rec, None));
            }
            Ok((base(json!({"events":events,"seen":seen})), None))
        }
        _ => {
            let high_water = py_int(&args["high_water"]) as usize;
            let mut last_text = text(&args["last_text"]);
            let Ok(bytes) = fs::read(path) else {
                return Ok((base(json!({"high_water":high_water,"last_text":last_text})), None));
            };
            let mut events: Vec<Value> = Vec::new();
            let mut last_line = high_water;
            let make = |event_type: &str, key: String, stamp: &str, detail: &str, body: String| {
                json!({"vendor":"muse","event_type":event_type,"cwd":cwd,"session_id":session_id,
                "file_path":path,"dedup_key":key,"timestamp":stamp,"detail":detail,"text":body})
            };
            let mut start = 0;
            let mut line_no = 0;
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
                let key = format!("act:{line_no}");
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
                let stamp = iso_from_micros(&rec["recorded_at"]);
                if let Some(prompt) = turn_prompt(&rec) {
                    events.push(make("agent_active", key, &stamp, "user", prompt));
                    continue;
                }
                let Some(event) = run_event(&rec) else { continue };
                let kind = str_or_empty(&event["kind"]);
                if kind == "assistant_message_committed" {
                    let reply = str_or_empty(&event["text"]).trim().to_owned();
                    if !reply.is_empty() {
                        last_text = cap_text(&reply);
                    }
                }
                if ACTIVE_KINDS.contains(&kind.as_str()) {
                    events.push(make("agent_active", key, &stamp, &kind, String::new()));
                }
                if kind == "terminal" {
                    events.push(make(
                        "turn_complete",
                        format!("turn:{line_no}"),
                        &stamp,
                        &str_or_empty(&event["terminal"]),
                        last_text.clone(),
                    ));
                    last_text.clear();
                }
            }
            Ok((
                base(json!({"events":events,"high_water":last_line,"last_text":last_text})),
                None,
            ))
        }
    }
}
