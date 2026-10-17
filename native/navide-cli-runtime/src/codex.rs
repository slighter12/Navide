//! Bounded Codex rollout semantics derived from the existing Python reader.
use serde_json::{json, Value};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::os::unix::fs::MetadataExt;
use std::path::Path;

fn text(v: &Value) -> String {
    v.as_str().map(str::to_owned).unwrap_or_default()
}
fn count(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0)
        .max(0)
}
fn anchor(file: &mut File, end: u64) -> io::Result<Vec<u8>> {
    let start = end.saturating_sub(512);
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = vec![0; (end - start) as usize];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}
fn typed_prompt(payload: &Value) -> Option<String> {
    if payload["type"] == "user_message" {
        return Some(text(&payload["message"]));
    }
    if payload["type"] == "item_completed" && payload["item"]["type"] == "UserMessage" {
        return Some(
            payload["item"]["content"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|b| b["type"] == "text")
                .map(|b| text(&b["text"]))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    None
}
/// See `claude::observe`: `expected` is the raw anchor request blob; the raw anchor is returned.
pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "activity" | "identify" | "session_usage") {
        return Err(io::Error::other("Codex observation mode not integrated"));
    }
    let mut file = File::open(path)?;
    let stat = file.metadata()?;
    let identity = format!("{}:{}", stat.dev(), stat.ino());
    let mut checkpoint = args["checkpoint"].as_object().cloned().unwrap_or_default();
    let old = Value::Object(checkpoint.clone());
    let offset = old["offset"].as_u64().unwrap_or(0);
    let replaced = old["identity"].as_str().is_some_and(|v| v != identity);
    let rotated = offset > 0
        && (replaced
            || stat.len() < offset
            || args["anchor_invalid"] == true
            || (!expected.is_empty() && anchor(&mut file, offset)? != expected));
    let start = if mode == "usage" && !rotated {
        offset
    } else {
        0
    };
    let mut sid = if replaced {
        String::new()
    } else {
        text(&old["session_id"])
    };
    let mut cwd = if replaced {
        String::new()
    } else {
        text(&old["cwd"])
    };
    let mut model = if replaced {
        String::new()
    } else {
        text(&old["model"])
    };
    let mut version = if replaced {
        String::new()
    } else {
        text(&old["cli_version"])
    };
    // Identification and activity need the immutable header even above a saved mark.
    let mut header = BufReader::new(file.try_clone()?);
    header.seek(SeekFrom::Start(0))?;
    for _ in 0..5 {
        let mut line = String::new();
        if header.read_line(&mut line)? == 0 {
            break;
        }
        if let Ok(rec) = serde_json::from_str::<Value>(&line) {
            if rec["type"] == "session_meta" {
                let p = &rec["payload"];
                cwd = text(&p["cwd"]);
                model = if p["model_provider"].is_null() {
                    text(&p["model"])
                } else {
                    text(&p["model_provider"])
                };
                version = text(&p["cli_version"]);
                if p["thread_source"] == "subagent"
                    || !p["parent_thread_id"].is_null()
                    || p["source"].get("subagent").is_some()
                {
                    sid.clear();
                } else {
                    sid = text(&p["id"]);
                }
                break;
            }
        }
    }
    file.seek(SeekFrom::Start(start))?;
    let stem = Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let initial_water = seen
        .iter()
        .find_map(|s| {
            s.strip_prefix("act_hw::")
                .and_then(|v| v.parse::<u64>().ok())
        })
        .unwrap_or(0);
    let mut high_water = initial_water;
    let mut last_text = seen
        .iter()
        .find_map(|s| s.strip_prefix("__lasttext__:"))
        .unwrap_or("")
        .to_owned();
    let mut previous = if replaced {
        (0, 0)
    } else {
        (count(&old["input_total"]), count(&old["output_total"]))
    };
    if mode == "session_usage" {
        previous = seen
            .iter()
            .find_map(|s| {
                s.strip_prefix("__cum__:in=")
                    .and_then(|v| v.split_once(",out="))
                    .and_then(|(a, b)| Some((a.parse().ok()?, b.parse().ok()?)))
            })
            .unwrap_or((0, 0));
    }
    let mut latest = previous;
    let mut latest_event = None;
    let mut committed = start;
    let mut latest_end = start;
    let mut reader = BufReader::new(file);
    let mut position = start;
    let mut events = Vec::new();
    let mut line_no = 0;
    let mut error = None;
    loop {
        if mode == "identify" {
            break;
        }
        let mut raw = Vec::new();
        if reader.read_until(b'\n', &mut raw)? == 0 {
            break;
        }
        line_no += 1;
        position += raw.len() as u64;
        let complete = raw.ends_with(b"\n");
        if mode == "usage" && !complete {
            break;
        }
        if mode == "usage" {
            committed = position;
        }
        let rec = serde_json::from_slice::<Value>(&raw);
        if raw.iter().all(u8::is_ascii_whitespace)
            || (mode == "activity" && line_no <= initial_water)
        {
            continue;
        }
        let rec = match rec {
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
        if mode == "activity" {
            high_water = line_no;
        }
        if !rec.is_object() {
            if mode == "activity" {
                events.clear();
                error = Some("Codex record is not an object");
                break;
            }
            continue;
        }
        let p = &rec["payload"];
        if mode != "activity" {
            if rec["type"] == "event_msg"
                && p["type"] == "token_count"
                && p["info"]["total_token_usage"].is_object()
            {
                let t = &p["info"]["total_token_usage"];
                latest = (
                    count(&t["input_tokens"]) + count(&t["cached_input_tokens"]),
                    count(&t["output_tokens"]) + count(&t["reasoning_output_tokens"]),
                );
                latest_event = Some(rec);
                latest_end = position;
            }
            continue;
        }
        let common = json!({"vendor":"codex","session_id":stem,"cwd":cwd,"file_path":path,"timestamp":text(&rec["timestamp"])});
        let mut emit = |kind: &str, key: String, detail: &str, value: String| {
            let mut event = common.clone();
            event["event_type"] = json!(kind);
            event["dedup_key"] = json!(key);
            event["detail"] = json!(detail);
            event["text"] = json!(value);
            events.push(event);
        };
        match rec["type"].as_str() {
            Some("assistant") => emit(
                "agent_active",
                format!("act:{line_no}"),
                "assistant",
                String::new(),
            ),
            Some("response_item") if p["role"] == "assistant" && p["type"] == "message" => {
                let value = p["content"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|b| b["type"] == "output_text")
                    .map(|b| text(&b["text"]))
                    .collect::<Vec<_>>()
                    .join("\n");
                if !value.is_empty() {
                    last_text = value;
                }
            }
            Some("event_msg") => {
                let kind = text(&p["type"]);
                if kind == "agent_message" && !text(&p["message"]).is_empty() {
                    last_text = text(&p["message"]);
                }
                let prompt = typed_prompt(p);
                emit(
                    "agent_active",
                    format!("act:{line_no}"),
                    if prompt.is_some() {
                        "user_message"
                    } else {
                        &kind
                    },
                    prompt.unwrap_or_default(),
                );
                if matches!(kind.as_str(), "task_complete" | "turn_aborted") {
                    let reply = if kind == "turn_aborted" {
                        String::new()
                    } else if last_text.is_empty() {
                        text(&p["last_agent_message"])
                    } else {
                        last_text.clone()
                    };
                    emit("turn_complete", format!("turn:{line_no}"), &kind, reply);
                    last_text.clear();
                }
            }
            _ => {}
        }
    }
    let mut file = reader.into_inner();
    checkpoint.extend(json!({"kind":"jsonl","offset":committed,"identity":identity,"input_total":latest.0,"output_total":latest.1,
        "cwd":cwd,"model":model,"session_id":if sid.is_empty(){stem}else{&sid},"cli_version":version}).as_object().unwrap().clone());
    if mode == "usage" || mode == "session_usage" {
        if let Some(rec) = latest_event {
            if latest.0 >= previous.0 && latest.1 >= previous.1 && latest != previous {
                let mut cursor = checkpoint.clone();
                cursor.insert("offset".into(), json!(latest_end));
                events.push(json!({"vendor":"codex","input_tokens":latest.0-previous.0,"output_tokens":latest.1-previous.1,"cwd":cwd,
                    "session_id":if sid.is_empty(){stem}else{&sid},"file_path":path,"dedup_key":format!("codex_cumulative::{}::{}::{}",if sid.is_empty(){stem}else{&sid},latest.0,latest.1),
                    "timestamp":text(&rec["timestamp"]),"model":model,"cli_version":version,"checkpoint":cursor}));
            }
        }
        if mode == "session_usage" {
            seen.retain(|s| !s.starts_with("__cum__:"));
            seen.push(format!("__cum__:in={},out={}", latest.0, latest.1));
        }
    }
    if mode == "activity" && high_water > initial_water {
        seen.retain(|s| !s.starts_with("act_hw::") && !s.starts_with("__lasttext__:"));
        seen.push(format!("act_hw::{high_water}"));
        seen.push(format!("__lasttext__:{last_text}"));
    }
    let raw_anchor = if committed > 0 {
        anchor(&mut file, committed)?
    } else {
        Vec::new()
    };
    Ok((
        json!({"parser":"rust-codex-jsonl-v1","path":path,"mode":mode,"identity":identity,"native_session_id":sid,
        "cwd":cwd,"events":events,"checkpoint":checkpoint,"seen":seen,"error":error}),
        Some(raw_anchor),
    ))
}
