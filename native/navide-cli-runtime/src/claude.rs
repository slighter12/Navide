//! Claude JSONL interpretation; application policy and checkpoint storage stay outside.
use serde_json::{json, Value};
use std::collections::{HashSet, VecDeque};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::os::unix::fs::MetadataExt;
use std::path::Path;

fn string(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        _ => value.to_string(),
    }
}
fn count(value: &Value) -> u64 {
    match value {
        Value::Bool(v) => u64::from(*v),
        Value::Number(n) => n.as_f64().unwrap_or(0.0).max(0.0) as u64,
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0).max(0) as u64,
        _ => 0,
    }
}
fn text_blocks(content: &Value) -> String {
    if let Some(text) = content.as_str() {
        return text.to_owned();
    }
    content.as_array().map_or_else(String::new, |blocks| {
        blocks
            .iter()
            .filter(|b| b["type"] == "text")
            .map(|b| string(&b["text"]))
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join("\n")
    })
}
fn anchor(file: &mut File, end: u64) -> io::Result<Vec<u8>> {
    let start = end.saturating_sub(512);
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = vec![0; (end - start) as usize];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}
fn activity_fingerprint(file: &mut File, end: u64) -> io::Result<String> {
    let start = end.saturating_sub(32);
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = vec![0; (end - start) as usize];
    file.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}
fn usage(rec: &Value, common: &Value, seen: &mut HashSet<String>) -> Option<Value> {
    if rec["type"] != "assistant" || !rec["message"]["usage"].is_object() {
        return None;
    }
    let msg = &rec["message"];
    let key = format!("{}::{}", string(&msg["id"]), string(&rec["requestId"]));
    if key == "::" || seen.contains(&key) {
        return None;
    }
    let raw = &msg["usage"];
    let cache_read = count(&raw["cache_read_input_tokens"]);
    let cache_creation = count(&raw["cache_creation_input_tokens"]);
    let input = count(&raw["input_tokens"]) + cache_read + cache_creation;
    let output = count(&raw["output_tokens"]);
    if input == 0 && output == 0 {
        return None;
    }
    seen.insert(key.clone());
    let mut event = common.clone();
    event["input_tokens"] = json!(input);
    event["output_tokens"] = json!(output);
    event["cache_read_tokens"] = json!(cache_read);
    event["cache_creation_tokens"] = json!(cache_creation);
    event["dedup_key"] = json!(key);
    event["timestamp"] = json!(string(&rec["timestamp"]));
    event["model"] = json!(string(&msg["model"]));
    event["cli_version"] = json!(string(&rec["version"]));
    Some(event)
}
fn activity(rec: &Value, line: u64, common: &Value, out: &mut Vec<Value>) {
    let kind = rec["type"].as_str().unwrap_or("");
    let msg = &rec["message"];
    let mut emit = |event_type: &str, key: String, detail: &str, text: String| {
        let mut event = common.clone();
        event["event_type"] = json!(event_type);
        event["dedup_key"] = json!(key);
        event["timestamp"] = json!(string(&rec["timestamp"]));
        event["detail"] = json!(detail);
        event["text"] = json!(text);
        out.push(event);
    };
    if kind == "assistant" {
        emit(
            "agent_active",
            format!("act:{line}"),
            "assistant",
            String::new(),
        );
        let blocks = msg["content"].as_array().cloned().unwrap_or_default();
        if blocks
            .iter()
            .any(|b| b["type"] == "tool_use" && b["name"] == "AskUserQuestion")
        {
            emit(
                "agent_active",
                format!("q:{line}"),
                "assistant:question",
                String::new(),
            );
        }
        let stopped = blocks
            .iter()
            .filter(|b| {
                b["type"] == "tool_use"
                    && matches!(
                        b["name"].as_str(),
                        Some("TaskStop" | "KillShell" | "KillBash")
                    )
            })
            .map(|b| {
                if b["input"]["task_id"].is_null() {
                    string(&b["input"]["shell_id"])
                } else {
                    string(&b["input"]["task_id"])
                }
            })
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>();
        if !stopped.is_empty() {
            emit(
                "agent_active",
                format!("bg:{line}"),
                "background:end",
                stopped.join("\n"),
            );
        }
        if msg["stop_reason"] == "end_turn" {
            emit(
                "turn_complete",
                format!("turn:{line}"),
                "end_turn",
                text_blocks(&msg["content"]),
            );
        }
    } else if kind == "user" || kind == "tool_use" {
        let content = &msg["content"];
        let pasted_images = content.as_array().is_some_and(|blocks| {
            blocks.iter().any(|b| b["type"] == "image")
                && !blocks.iter().any(|b| b["type"] == "tool_result")
        });
        let text = if content.is_string() || pasted_images {
            text_blocks(content)
        } else {
            String::new()
        };
        let text = text.trim();
        let prompt = if kind == "user"
            && !rec["isCompactSummary"].as_bool().unwrap_or(false)
            && !rec["isMeta"].as_bool().unwrap_or(false)
            && !text.starts_with('<')
        {
            text.to_owned()
        } else {
            String::new()
        };
        // Python converts this to the existing capped PromptText (including .full).
        emit("agent_active", format!("act:{line}"), kind, prompt);
        let result = &rec["toolUseResult"];
        let task = if !result["backgroundTaskId"].is_null() {
            string(&result["backgroundTaskId"])
        } else if result["status"] == "async_launched"
            || (result["status"] == "forked" && result["background"] == true)
        {
            string(&result["agentId"])
        } else {
            String::new()
        };
        if kind == "user" && !task.is_empty() {
            emit(
                "agent_active",
                format!("bg:{line}"),
                "background:start",
                task,
            );
        }
    } else if kind == "queue-operation" && rec["operation"] == "enqueue" {
        let content = rec["content"].as_str().unwrap_or("");
        if content.contains("<task-notification>") && content.contains("<status>") {
            let mut ids = Vec::new();
            let mut remaining = content;
            while let Some((_, rest)) = remaining.split_once("<task-id>") {
                if let Some((id, after)) = rest.split_once("</task-id>") {
                    if !id.is_empty() && !id.contains('<') {
                        ids.push(id.to_owned());
                    }
                    remaining = after;
                } else {
                    break;
                }
            }
            if !ids.is_empty() {
                emit(
                    "agent_active",
                    format!("bg:{line}"),
                    "background:end",
                    ids.join("\n"),
                );
            }
        }
    }
}
fn turns(records: &[(u64, Value)], sid: &str) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    let mut keys: Vec<String> = Vec::new();
    let mut current = None;
    let mut seen = HashSet::new();
    for (line, rec) in records {
        let msg = &rec["message"];
        if !msg.is_object() {
            continue;
        }
        let ts = string(&rec["timestamp"]);
        if rec["type"] == "user" && rec.get("toolUseResult").is_none() {
            let key = rec["promptId"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(String::from)
                .unwrap_or_else(|| format!("line:{line}"));
            let index = keys.iter().position(|k| k == &key).unwrap_or_else(|| {
                keys.push(key);
                out.push(json!({"turn_index":0,"session_id":sid,"started_at":if ts.is_empty(){None}else{Some(&ts)},
                    "ended_at":null,"prompt_excerpt":"","input":0,"cache_read":0,"cache_creation":0,"output":0,
                    "calls":[],"cli_version":""}));
                out.len() - 1
            });
            current = Some(index);
            let text = text_blocks(&msg["content"]);
            let prior = out[index]["prompt_excerpt"].as_str().unwrap_or("");
            if prior.is_empty()
                || (prior.starts_with('<')
                    && !text.trim().starts_with('<')
                    && !text.trim().is_empty())
            {
                out[index]["prompt_excerpt"] = json!(text
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
                    .chars()
                    .take(80)
                    .collect::<String>());
            }
            continue;
        }
        let Some(index) = current else {
            continue;
        };
        let Some(event) = usage(rec, &json!({}), &mut seen) else {
            continue;
        };
        let raw = &msg["usage"];
        let call = json!({"ts":if ts.is_empty(){None}else{Some(&ts)},"model":string(&msg["model"]),
            "input":count(&raw["input_tokens"]),"cache_read":event["cache_read_tokens"],
            "cache_creation":event["cache_creation_tokens"],"output":event["output_tokens"],"cli_version":string(&rec["version"])});
        for field in ["input", "cache_read", "cache_creation", "output"] {
            out[index][field] =
                json!(out[index][field].as_u64().unwrap_or(0) + call[field].as_u64().unwrap_or(0));
        }
        if !ts.is_empty() {
            out[index]["ended_at"] = json!(ts);
        }
        if call["cli_version"] != "" {
            out[index]["cli_version"] = call["cli_version"].clone();
        }
        out[index]["calls"].as_array_mut().unwrap().push(call);
    }
    out.retain(|t| !t["calls"].as_array().unwrap().is_empty());
    for (index, turn) in out.iter_mut().enumerate() {
        turn["turn_index"] = json!(index + 1);
    }
    out
}

/// `expected` is the raw anchor bytes Python read from the file (the request blob). Returns the
/// result document and the raw anchor to return as the answer's blob (`None` when this parser
/// returns no anchor).
pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = Path::new(
        args["path"]
            .as_str()
            .ok_or_else(|| io::Error::other("path required"))?,
    );
    let mode = args["mode"]
        .as_str()
        .ok_or_else(|| io::Error::other("mode required"))?;
    if !matches!(mode, "usage" | "activity" | "session_usage" | "turns") {
        return Err(io::Error::other("invalid observation mode"));
    }
    let mut file = File::open(path)?;
    let stat = file.metadata()?;
    let identity = format!("{}:{}", stat.dev(), stat.ino());
    let sid = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    let parent = path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let cwd = if parent.starts_with('-') {
        parent.replace('-', "/")
    } else {
        String::new()
    };
    let common = json!({"vendor":"claude","session_id":sid,"cwd":cwd,"file_path":path});
    let mut checkpoint = args["checkpoint"].as_object().cloned().unwrap_or_default();
    let offset = checkpoint
        .get("offset")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let rotated = offset > 0
        && (args["anchor_invalid"] == true
            || args["checkpoint"]["identity"] != identity
            || stat.len() < offset
            || (!expected.is_empty() && anchor(&mut file, offset)? != expected));
    let activity_position = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter_map(|key| key.strip_prefix("act_pos::"))
        .find_map(|value| {
            let (line, rest) = value.split_once(':')?;
            let (position, rest) = rest.split_once(':')?;
            let (saved_identity, fingerprint) = rest.rsplit_once(':')?;
            let line = line.parse::<u64>().ok()?;
            let position = position.parse::<u64>().ok()?;
            (saved_identity == identity && position <= stat.len()).then_some((
                line,
                position,
                fingerprint.to_owned(),
            ))
        });
    let activity_position = match activity_position {
        Some((line, position, fingerprint))
            if activity_fingerprint(&mut file, position)? == fingerprint =>
        {
            Some((line, position))
        }
        _ => None,
    };
    let start = if mode == "usage" && !rotated {
        offset
    } else if mode == "activity" {
        activity_position.map_or(0, |(_, position)| position)
    } else {
        0
    };
    file.seek(SeekFrom::Start(start))?;
    let mut reader = BufReader::new(file);
    let mut position = start;
    let mut committed = start;
    let mut recent: VecDeque<String> = if rotated {
        VecDeque::new()
    } else {
        args["checkpoint"]["recent_keys"]
            .as_array()
            .map(|keys| keys.iter().map(string).collect())
            .unwrap_or_default()
    };
    if recent.len() > 64 {
        recent.drain(..recent.len() - 64);
    }
    let mut seen: HashSet<String> = recent.iter().cloned().collect();
    if mode == "session_usage" {
        seen = args["seen"]
            .as_array()
            .map(|a| a.iter().map(string).collect())
            .unwrap_or_default();
    }
    let mut high_water = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .find_map(|s| {
            s.strip_prefix("act_hw::")
                .and_then(|s| s.parse::<u64>().ok())
        })
        .unwrap_or(0);
    let initial_water = high_water;
    let mut events = Vec::new();
    let mut records = Vec::new();
    let mut line_no = activity_position.map_or(0, |(line, _)| line);
    let mut activity_committed = start;
    let mut activity_committed_line = line_no;
    let mut activity_error = None;
    loop {
        let mut raw = Vec::new();
        if reader.read_until(b'\n', &mut raw)? == 0 {
            break;
        }
        line_no += 1;
        position += raw.len() as u64;
        let complete = raw.ends_with(b"\n");
        if mode == "activity" && complete {
            activity_committed = position;
            activity_committed_line = line_no;
        }
        if mode == "usage" && !complete {
            break;
        }
        if mode == "usage" {
            committed = position;
        }
        let text = match std::str::from_utf8(&raw) {
            Ok(text) => text.trim(),
            Err(_) if mode == "usage" => continue,
            Err(error) => return Err(io::Error::other(error)),
        };
        if text.is_empty() || (mode == "activity" && line_no <= initial_water) {
            continue;
        }
        let rec: Value = match serde_json::from_str(text) {
            Ok(rec) => rec,
            Err(_) => {
                if mode == "activity" {
                    if !complete {
                        break;
                    }
                    high_water = line_no;
                }
                continue;
            }
        };
        if !rec.is_object() {
            if mode == "usage" {
                continue;
            }
            if mode == "activity" {
                // The inherited reader advances before .get and commits the
                // high-water in finally, but does not return prior events.
                high_water = line_no;
                events.clear();
                activity_error = Some("Claude record is not an object");
                break;
            }
            return Err(io::Error::other("Claude record is not an object"));
        }
        match mode {
            "activity" => {
                high_water = line_no;
                activity(&rec, line_no, &common, &mut events);
            }
            "turns" => records.push((line_no, rec)),
            _ => {
                if let Some(mut event) = usage(&rec, &common, &mut seen) {
                    if mode == "usage" {
                        recent.push_back(string(&event["dedup_key"]));
                        if recent.len() > 64 {
                            if let Some(expired) = recent.pop_front() {
                                seen.remove(&expired);
                            }
                        }
                        event["cursor_end"] = json!(position);
                    }
                    events.push(event);
                }
            }
        }
    }
    let file = reader.into_inner();
    let mut file = file;
    let raw_anchor = if committed > 0 {
        anchor(&mut file, committed)?
    } else {
        Vec::new()
    };
    checkpoint.insert("kind".into(), json!("jsonl"));
    checkpoint.insert("identity".into(), json!(identity));
    checkpoint.insert("offset".into(), json!(committed));
    checkpoint.insert("recent_keys".into(), json!(recent));
    let mut next_seen = args["seen"].as_array().cloned().unwrap_or_default();
    if mode == "activity" && (high_water > initial_water || activity_committed > start) {
        next_seen.retain(|v| {
            !v.as_str()
                .is_some_and(|s| s.starts_with("act_hw::") || s.starts_with("act_pos::"))
        });
        if high_water > 0 {
            next_seen.push(json!(format!("act_hw::{high_water}")));
        }
        let fingerprint = activity_fingerprint(&mut file, activity_committed)?;
        next_seen.push(json!(format!(
            "act_pos::{activity_committed_line}:{activity_committed}:{identity}:{fingerprint}"
        )));
    } else if mode == "session_usage" {
        next_seen = seen.into_iter().map(Value::String).collect();
    }
    Ok((
        json!({"parser":"rust-claude-jsonl-v1","path":path,"native_session_id":sid,"cwd":cwd,"identity":identity,
        "mode":mode,"events":events,"checkpoint":checkpoint,"seen":next_seen,"error":activity_error,
        "usage_reset":rotated,"usage_cursor_schema":2,
        "turns":if mode == "turns" { turns(&records, sid) } else { Vec::new() }}),
        Some(raw_anchor),
    ))
}
