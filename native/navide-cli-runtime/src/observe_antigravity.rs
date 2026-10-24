//! Antigravity observation, derived from `cli_vendors/antigravity.py` (G2 ticket 06, family E).
//!
//! A conversation is one SQLite file whose `steps` table keeps `idx`, `step_type` and `status` as plain integers and
//! the text inside undocumented protobuf payloads, of which only the top-level fields the baseline reads are decoded.
//! Python keeps discovery, the workspace cwd (`cwd`, extracted from `trajectory_metadata_blob`), the conversation id
//! (`session`) and the user-prompt wrapper stripping. Antigravity records no usage, so `usage` and `session_usage`
//! answer with no events.
use super::observe_jsonl::{epoch_iso, text};
use super::opencode::{blob_bytes, query};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io;
use std::path::Path;

const IDX_PREFIX: &str = "agy_idx::";
const PENDING_PREFIX: &str = "agy_pending::";
const MAX_STEPS_PER_PASS: i64 = 256;
const STEP_USER: i64 = 14;
const STEP_ASSISTANT: i64 = 15;
const STEP_QUESTION: i64 = 138;
const STATUS_DONE: i64 = 3;
const USER_PAYLOAD: u64 = 19;
const ASSISTANT_PAYLOAD: u64 = 20;
const META_END_TIMESTAMPS: [u64; 3] = [8, 7, 6];

/// `_varint`: `(value, next index)`; `None` for a truncated or oversized varint.
fn varint(buf: &[u8], mut at: usize) -> (Option<u128>, usize) {
    let (mut shift, mut value) = (0u32, 0u128);
    while at < buf.len() {
        let byte = buf[at];
        at += 1;
        value |= u128::from(byte & 0x7F) << shift;
        if byte & 0x80 == 0 {
            return (Some(value), at);
        }
        shift += 7;
        if shift > 63 {
            return (None, at);
        }
    }
    (None, at)
}

enum Field<'a> {
    Varint(u128),
    Bytes(&'a [u8]),
}

/// `_pb_scan`: top-level `(field, value)` pairs of varint and length-delimited fields; stops at the first byte that
/// does not parse. Fixed-width fields are skipped, groups end the scan.
fn pb_scan(blob: &[u8]) -> Vec<(u64, Field<'_>)> {
    let mut out = Vec::new();
    let (mut at, end) = (0, blob.len());
    while at < end {
        let (key, next) = varint(blob, at);
        at = next;
        let Some(key) = key else { break };
        let (field, wire) = ((key >> 3) as u64, key & 7);
        match wire {
            0 => {
                let (value, next) = varint(blob, at);
                at = next;
                let Some(value) = value else { break };
                out.push((field, Field::Varint(value)));
            }
            2 => {
                let (length, next) = varint(blob, at);
                at = next;
                let Some(length) = length else { break };
                if at as u128 + length > end as u128 {
                    break;
                }
                out.push((field, Field::Bytes(&blob[at..at + length as usize])));
                at += length as usize;
            }
            5 => at += 4,
            1 => at += 8,
            _ => break,
        }
    }
    out
}

fn pb_bytes(blob: &[u8], field: u64) -> &[u8] {
    pb_scan(blob)
        .into_iter()
        .find_map(|(f, value)| match value {
            Field::Bytes(bytes) if f == field => Some(bytes),
            _ => None,
        })
        .unwrap_or(&[])
}

fn pb_varint(blob: &[u8], field: u64) -> Option<u128> {
    pb_scan(blob).into_iter().find_map(|(f, value)| match value {
        Field::Varint(v) if f == field => Some(v),
        _ => None,
    })
}

/// `_pb_text`: the first clean string among `inner` fields of `payload`'s `outer` submessage.
fn pb_text(payload: &[u8], outer: u64, inner: &[u64]) -> String {
    let blob = pb_bytes(payload, outer);
    if blob.is_empty() {
        return String::new();
    }
    for field in inner {
        for (f, value) in pb_scan(blob) {
            let Field::Bytes(bytes) = value else { continue };
            if f != *field {
                continue;
            }
            let Ok(decoded) = std::str::from_utf8(bytes) else { continue };
            if decoded.chars().any(|c| c < ' ' && !matches!(c, '\t' | '\n' | '\r')) {
                continue;
            }
            if !decoded.trim().is_empty() {
                return decoded.trim().to_owned();
            }
        }
    }
    String::new()
}

/// `_step_timestamp`: ISO 8601 from the first present end/start `Timestamp` in the metadata.
fn step_timestamp(metadata: &[u8]) -> String {
    for field in META_END_TIMESTAMPS {
        let stamp = pb_bytes(metadata, field);
        if stamp.is_empty() {
            continue;
        }
        if let Some(seconds) = pb_varint(stamp, 1).filter(|s| *s != 0) {
            return epoch_iso(seconds as f64);
        }
    }
    String::new()
}

/// Python `int(v or 0)` of an integer cell: the baseline never clamps (`idx` is -1 for a conversation's first row).
fn signed(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|f| f as i64))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
}

fn cell_bytes(cell: &Value) -> Vec<u8> {
    blob_bytes(cell).unwrap_or_default()
}

fn pending_json(pending: &BTreeSet<i64>) -> String {
    format!("[{}]", pending.iter().map(i64::to_string).collect::<Vec<_>>().join(", "))
}

pub fn observe(args: &Value) -> io::Result<Value> {
    let path_text = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let path = Path::new(path_text);
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Antigravity observation mode not integrated"));
    }
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let mut result = json!({"parser":"rust-antigravity-sqlite-v1","path":path_text,"mode":mode,"identity":"",
        "events":[],"checkpoint":args["checkpoint"],"seen":seen,"error":null});
    if mode != "activity" {
        return Ok(result);
    }
    let (cwd, session_id) = (text(&args["cwd"]), text(&args["session"]));
    let remember = |seen: &mut Vec<String>, idx: i64| {
        seen.retain(|key| !key.starts_with(IDX_PREFIX));
        seen.push(format!("{IDX_PREFIX}{idx}"));
    };
    let previous = seen.iter().find_map(|key| key.strip_prefix(IDX_PREFIX).map(str::to_owned));
    // First sight: the rows already there are history; skip to the last step.
    let Some(previous) = previous else {
        if let Some(rows) = query(path, "SELECT MAX(idx) FROM steps", &[]) {
            if let Some(newest) = rows.first().filter(|row| !row[0].is_null()) {
                remember(&mut seen, signed(&newest[0]));
                result["seen"] = json!(seen);
            }
        }
        return Ok(result);
    };
    let previous = previous.trim().parse::<i64>().unwrap_or(0);
    let mut pending: BTreeSet<i64> = seen
        .iter()
        .find_map(|key| key.strip_prefix(PENDING_PREFIX))
        .and_then(|raw| serde_json::from_str::<Vec<i64>>(raw).ok())
        .unwrap_or_default()
        .into_iter()
        .collect();
    let clause = if pending.is_empty() {
        String::new()
    } else {
        format!(" OR idx IN ({})", pending.iter().map(|_| "?").collect::<Vec<_>>().join(","))
    };
    let sql = format!(
        "SELECT idx, step_type, status, metadata, step_payload FROM steps WHERE idx > ?{clause} ORDER BY idx LIMIT ?"
    );
    let mut parameters = vec![json!(previous)];
    parameters.extend(pending.iter().map(|id| json!(id)));
    parameters.push(json!(MAX_STEPS_PER_PASS));
    let rows: Vec<Vec<Value>> = query(path, &sql, &parameters)
        .unwrap_or_default()
        .into_iter()
        .filter(|row| !row[0].is_null())
        .collect();
    let Some(last) = rows.last() else {
        return Ok(result);
    };
    let newest = previous.max(signed(&last[0]));
    remember(&mut seen, newest);
    let mut events = Vec::new();
    for row in &rows {
        let (idx, step_type, status) = (signed(&row[0]), signed(&row[1]), signed(&row[2]));
        if step_type == STEP_ASSISTANT && status != STATUS_DONE {
            if pending.contains(&idx) {
                continue; // still streaming; do not repeat the active event
            }
            pending.insert(idx);
        } else {
            pending.remove(&idx);
        }
        let (metadata, payload) = (cell_bytes(&row[3]), cell_bytes(&row[4]));
        let make = |event_type: &str, detail: &str, body: String| {
            json!({"vendor":"antigravity","event_type":event_type,"cwd":cwd,"session_id":session_id,
                "file_path":path_text,"dedup_key":format!("step:{session_id}:{idx}"),
                "timestamp":step_timestamp(&metadata),"detail":detail,"text":body})
        };
        events.push(match step_type {
            STEP_USER => make("agent_active", "user", pb_text(&payload, USER_PAYLOAD, &[2])),
            STEP_QUESTION => make("turn_complete", "assistant:question", String::new()),
            STEP_ASSISTANT => {
                let reply = pb_text(&payload, ASSISTANT_PAYLOAD, &[1, 8]);
                if !reply.is_empty() && status == STATUS_DONE && idx == newest {
                    make("turn_complete", "assistant", reply)
                } else if pb_text(&payload, ASSISTANT_PAYLOAD, &[3]).is_empty() {
                    make("agent_active", "assistant", String::new())
                } else {
                    make("agent_active", "assistant:thinking", String::new())
                }
            }
            other => make("agent_active", &format!("step-{other}"), String::new()),
        });
    }
    // One sentinel stays under the watcher's persisted-key count limit.
    seen.retain(|key| !key.starts_with(PENDING_PREFIX));
    if !pending.is_empty() {
        seen.push(format!("{PENDING_PREFIX}{}", pending_json(&pending)));
    }
    result["events"] = json!(events);
    result["seen"] = json!(seen);
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_payload_text_field_must_be_clean_prose() {
        // field 19 { field 2 = "hello" } wrapped as protobuf
        let mut inner = vec![0x12, 5];
        inner.extend_from_slice(b"hello");
        // The key (19 << 3 | 2 = 154) is a two-byte varint.
        let mut payload = vec![0x9a, 0x01, inner.len() as u8];
        payload.extend(inner);
        assert_eq!(pb_text(&payload, USER_PAYLOAD, &[2]), "hello");
        assert_eq!(pb_text(&payload, USER_PAYLOAD, &[1]), "");
    }

    #[test]
    fn a_truncated_length_ends_the_scan() {
        assert!(pb_scan(&[0x0a, 0x7f, b'x']).is_empty());
        assert_eq!(pending_json(&BTreeSet::from([2, 5])), "[2, 5]");
    }
}
