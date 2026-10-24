//! Cursor observation, derived from `cli_vendors/cursor.py` (G2 ticket 06, family E).
//!
//! `store.db` keeps content-addressed `blobs` rows: some are plain JSON chat messages, the rest protobuf nodes that are
//! never decoded. Python keeps discovery, the `meta.json` cwd (`cwd`), the session directory name (`session`), the
//! scan clock (`base_epoch`, meta.json's `updatedAtMs` or the wall clock) and the user-prompt wrapper stripping.
//! Cursor stores no usage, so `usage` and `session_usage` answer with no events.
use super::observe_jsonl::{epoch_iso, join_text_blocks, py_int, str_or_empty, text};
use super::opencode::{blob_bytes, number, query};
use serde_json::{json, Map, Value};
use std::io;
use std::path::Path;

const ACTIVITY_PREFIX: &str = "cursor_blob::";
const MAX_BLOBS_PER_PASS: i64 = 512;
const MAX_TURN_TEXT: usize = 8000;
const MAX_BLOB_BYTES: usize = 262_144;
const MAX_BLOBS_PER_DB: usize = 512;

/// Head-and-tail cap: the loop's and the messaging protocol's end markers sit on the reply's last line.
fn cap_text(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= MAX_TURN_TEXT {
        return value.to_owned();
    }
    let half = MAX_TURN_TEXT / 2;
    format!(
        "{}\n…\n{}",
        chars[..half].iter().collect::<String>(),
        chars[chars.len() - half..].iter().collect::<String>()
    )
}

/// `_json_message`: a `blobs.data` cell decoded as a JSON object, or `None` (protobuf nodes are skipped).
fn json_message(cell: &Value) -> Option<Value> {
    let parsed = match blob_bytes(cell) {
        Some(bytes) => serde_json::from_slice::<Value>(&bytes).ok(),
        None => cell.as_str().and_then(|raw| serde_json::from_str::<Value>(raw).ok()),
    };
    parsed.filter(Value::is_object)
}

/// `_USER_QUERY_RE.search(...).group(1).strip()`: what sits between the first `<user_query>` and the `</user_query>`
/// that follows it.
fn user_query(body: &str) -> Option<String> {
    let start = body.find("<user_query>")? + "<user_query>".len();
    let end = start + body[start..].find("</user_query>")?;
    Some(body[start..end].trim().to_owned())
}

fn is_hex_run(s: &str, lengths: &[usize]) -> Option<usize> {
    let mut at = 0;
    for (index, length) in lengths.iter().enumerate() {
        if index > 0 {
            if s[at..].starts_with('-') {
                at += 1;
            } else {
                return None;
            }
        }
        let run = s.get(at..at + length)?;
        if !run.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        at += length;
    }
    Some(at)
}

/// `_MARKER_ONLY_RE`: the whole body is Navide's own `at-pane:<uuid>` marker, optionally wrapped in its comment.
fn marker_only(body: &str) -> bool {
    let unwrapped = body
        .strip_prefix("<!--")
        .map(str::trim_start)
        .and_then(|rest| rest.strip_prefix("agent-team-session:"))
        .map(str::trim_start);
    [unwrapped, Some(body)].into_iter().flatten().any(|candidate| {
        let Some(rest) = candidate.strip_prefix("at-pane:") else { return false };
        let Some(used) = is_hex_run(rest, &[8, 4, 4, 4, 12]) else { return false };
        let tail = &rest[used..];
        tail.is_empty() || tail.trim_start().strip_prefix("-->").is_some_and(str::is_empty)
    })
}

pub fn observe(args: &Value) -> io::Result<Value> {
    let path_text = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let path = Path::new(path_text);
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity" | "identify") {
        return Err(io::Error::other("Cursor observation mode not integrated"));
    }
    let mut seen: Vec<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let mut result = json!({"parser":"rust-cursor-sqlite-v1","path":path_text,"mode":mode,"identity":"",
        "events":[],"checkpoint":args["checkpoint"],"seen":seen,"markers":{},"error":null});
    match mode {
        "identify" => {
            let wanted: Vec<&str> = args["markers"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .filter(|m| !m.is_empty())
                .collect();
            let sql = format!("SELECT substr(data, 1, {MAX_BLOB_BYTES}) FROM blobs LIMIT {MAX_BLOBS_PER_DB}");
            let mut found = Map::new();
            for row in query(path, &sql, &[]).unwrap_or_default() {
                let bytes = blob_bytes(&row[0]).or_else(|| row[0].as_str().map(|s| s.as_bytes().to_vec()));
                let Some(bytes) = bytes else { continue };
                for marker in &wanted {
                    if !found.contains_key(*marker)
                        && bytes.windows(marker.len()).any(|window| window == marker.as_bytes())
                    {
                        found.insert((*marker).to_owned(), json!(true));
                    }
                }
            }
            result["markers"] = Value::Object(found);
        }
        "activity" => {
            let (cwd, session_id) = (text(&args["cwd"]), text(&args["session"]));
            let remember = |seen: &mut Vec<String>, id: i64| {
                seen.retain(|key| !key.starts_with(ACTIVITY_PREFIX));
                seen.push(format!("{ACTIVITY_PREFIX}{id}"));
            };
            let Some(newest) = query(path, "SELECT COALESCE(MAX(rowid), 0) FROM blobs", &[]) else {
                return Ok(result); // unreadable this cycle: no data, no state change
            };
            let top = newest.first().map_or(0, |row| number(&row[0]));
            let previous = seen.iter().find_map(|key| key.strip_prefix(ACTIVITY_PREFIX).map(str::to_owned));
            let Some(previous) = previous else {
                // First sight: what is stored is history, not activity happening now.
                remember(&mut seen, top);
                result["seen"] = json!(seen);
                return Ok(result);
            };
            let watermark = py_int(&json!(previous));
            if top < watermark {
                // The store shrank: re-anchor rather than rescan.
                remember(&mut seen, top);
                result["seen"] = json!(seen);
                return Ok(result);
            }
            let rows = query(
                path,
                "SELECT rowid, id, data FROM blobs WHERE rowid > ? ORDER BY rowid LIMIT ?",
                &[json!(watermark), json!(MAX_BLOBS_PER_PASS)],
            )
            .unwrap_or_default();
            let Some(last) = rows.last() else {
                return Ok(result);
            };
            remember(&mut seen, number(&last[0]));
            let base = args["base_epoch"].as_f64().unwrap_or(0.0);
            let mut events: Vec<Value> = Vec::new();
            for row in &rows {
                let Some(message) = json_message(&row[2]) else { continue };
                let role = str_or_empty(&message["role"]);
                let content = &message["content"];
                let key = format!("blob:{}", if row[1].is_null() { "None".to_owned() } else { text(&row[1]) });
                // Each event is nudged 1 ms past the previous one: the messaging gate admits a turn only when its
                // stamp is strictly greater than the last processed one.
                let stamp = epoch_iso(base + events.len() as f64 * 0.001);
                let make = |event_type: &str, detail: &str, body: String| {
                    json!({"vendor":"cursor","event_type":event_type,"cwd":cwd,"session_id":session_id,
                        "file_path":path_text,"dedup_key":key,"timestamp":stamp,"detail":detail,"text":body})
                };
                if role == "assistant" {
                    events.push(make("turn_complete", "assistant", cap_text(&join_text_blocks(content))));
                } else if role == "user" {
                    let Some(typed) = user_query(&join_text_blocks(content)) else { continue };
                    if marker_only(&typed) {
                        continue;
                    }
                    events.push(make("agent_active", "user", typed));
                }
            }
            result["events"] = json!(events);
            result["seen"] = json!(seen);
        }
        _ => {}
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_prompt_is_what_sits_inside_user_query() {
        assert_eq!(
            user_query("<timestamp>x</timestamp>\n<user_query>\nReply ok\n</user_query>"),
            Some("Reply ok".to_owned())
        );
        assert_eq!(user_query("<user_info>context only</user_info>"), None);
    }

    #[test]
    fn only_a_body_that_is_entirely_the_marker_is_navides_own() {
        let id = "abcd1234-1111-4111-8111-111111111111";
        assert!(marker_only(&format!("at-pane:{id}")));
        assert!(marker_only(&format!("<!-- agent-team-session: at-pane:{id} -->")));
        assert!(!marker_only(&format!("at-pane:{id} work")));
        assert!(!marker_only(&format!("see at-pane:{id}")));
        assert!(!marker_only("at-pane:not-a-uuid"));
    }
}
