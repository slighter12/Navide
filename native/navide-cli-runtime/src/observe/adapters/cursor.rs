//! Cursor's adapter: what a message blob means, and nothing else.
//!
//! `store.db` keeps content-addressed `blobs` rows: some are plain JSON chat messages, the rest protobuf nodes that are never
//! decoded. The declaration carries the walk (by `rowid`, keyed by the blob's `id`), the raw marker scan and the request
//! fields Python supplies per file (`cwd` from `meta.json`, the session directory name, the scan clock). Cursor stores no
//! usage. What stays here, and why it is not data:
//! - `mark_event`: a JSON message blob is a finished reply (the loop's and the messaging protocol's end markers sit on its last
//!   line, so it is capped head-and-tail) or a typed prompt (the text inside `<user_query>`, unless it is Navide's own pane
//!   marker); a blob that is not a JSON object, and any other role, says nothing.
use crate::observe::adapters::{CursorMark, CursorWalk};
use crate::observe::rows::text_or_empty;
use crate::observe::sqlite::blob_bytes;
use crate::observe::time::iso_micros;
use crate::observe::{activity_event, Ident, Observer, Pass, SqlObserver};
use serde_json::Value;
use std::io;

pub struct Cursor;

impl Observer for Cursor {
    /// Nothing beyond the core's activity marks.
    type State = ();
}

/// A `blobs.data` cell decoded as a JSON object, or `None` (protobuf nodes are skipped).
fn json_message(cell: &Value) -> Option<Value> {
    let parsed = match blob_bytes(cell) {
        Some(bytes) => serde_json::from_slice::<Value>(&bytes).ok(),
        None => cell.as_str().and_then(|raw| serde_json::from_str::<Value>(raw).ok()),
    };
    parsed.filter(Value::is_object)
}

/// The text blocks of a message's content, joined by newlines (a plain string is its own text).
fn joined(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter(|b| b.is_object() && b["type"] == "text")
            .map(|b| text_or_empty(&b["text"]))
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

/// What sits between the first `<user_query>` and the `</user_query>` that follows it.
fn user_query(body: &str) -> Option<String> {
    let start = body.find("<user_query>")? + "<user_query>".len();
    let end = start + body[start..].find("</user_query>")?;
    Some(body[start..end].trim().to_owned())
}

/// Hex runs of the given lengths joined by `-`; the length used, or `None` where the text is not that shape.
fn hex_runs(s: &str, lengths: &[usize]) -> Option<usize> {
    let mut at = 0;
    for (index, length) in lengths.iter().enumerate() {
        if index > 0 {
            if !s[at..].starts_with('-') {
                return None;
            }
            at += 1;
        }
        let run = s.get(at..at + length)?;
        if !run.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        at += length;
    }
    Some(at)
}

/// The whole body is Navide's own `at-pane:<uuid>` marker, optionally wrapped in its comment.
fn marker_only(body: &str) -> bool {
    let unwrapped = body.strip_prefix("<!--").map(str::trim_start).and_then(|rest| rest.strip_prefix("agent-team-session:")).map(str::trim_start);
    [unwrapped, Some(body)].into_iter().flatten().any(|candidate| {
        let Some(rest) = candidate.strip_prefix("at-pane:") else { return false };
        let Some(used) = hex_runs(rest, &[8, 4, 4, 4, 12]) else { return false };
        let tail = &rest[used..];
        tail.is_empty() || tail.trim_start().strip_prefix("-->").is_some_and(str::is_empty)
    })
}

impl SqlObserver for Cursor {
    type Walk = CursorWalk;

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: CursorMark<'_>, ident: &Ident, pass: &Pass, _siblings: F) -> io::Result<Vec<Value>> {
        let CursorMark::Blobs(mark) = mark;
        let Some(message) = json_message(&mark.payload) else { return Ok(Vec::new()) };
        let role = text_or_empty(&message["role"]);
        // The blobs hold no time: the core stamps each event from the scan clock, 1 ms past the walk's last stamp where the clock has not
        // moved past it (the messaging gate admits a turn only when its stamp is strictly greater than the last processed one).
        let make = |event_type: &str, detail: &str, body: String| {
            activity_event(ident, &iso_micros(pass.stamp(ident.base_epoch)), event_type, mark.dedup.clone(), detail, body)
        };
        let content = &message["content"];
        Ok(match role.as_str() {
            "assistant" => vec![make("turn_complete", "assistant", mark.capped(&joined(content)))],
            "user" => match user_query(&joined(content)) {
                Some(typed) if !marker_only(&typed) => vec![make("agent_active", "user", typed)],
                _ => Vec::new(),
            },
            _ => Vec::new(),
        })
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, core, events, meta, seen, Chain};
    use crate::observe::sqlite::testing::Fixture;
    use crate::observe::time::{epoch_micros, iso_micros, iso_seconds};
    use serde_json::{json, Value};
    use std::path::Path;

    // The schema recorded off cursor-agent: tests/fixtures/cli-regression/cursor.json, phases.initial[0].schema.
    const RECORDED: &str = include_str!("../../../../../tests/fixtures/cli-regression/cursor.json");
    const ID: &str = "abcd1234-1111-4111-8111-111111111111";

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    /// A blob row: a JSON message, stored as the CLI stores it (a BLOB), or any bytes.
    fn blob(store: &Fixture, id: Option<&str>, data: &[u8]) {
        let id = id.map_or("NULL".to_owned(), |id| format!("'{id}'"));
        store.sql(&format!("INSERT INTO blobs (id, data) VALUES ({id}, X'{}')", hex(data)), &[]);
    }

    fn message(store: &Fixture, id: &str, role: &str, content: Value) {
        blob(store, Some(id), json!({"role": role, "content": content}).to_string().as_bytes());
    }

    /// What Python supplies per file: the working directory, the session directory name and the scan clock.
    fn supplied() -> Value {
        json!({"cwd": "/work/c", "session": "chat-1", "base_epoch": 1792513600.0})
    }

    fn observe(store: &Fixture, mode: &str, state: &Value, extra: Value) -> Value {
        let mut extra = extra;
        extra.as_object_mut().unwrap().extend(supplied().as_object().cloned().unwrap());
        core("cursor", &meta(&store.path, mode, &[], &json!({}), state, extra)).unwrap()
    }

    fn project(document: &Value) -> Value {
        json!(events(document).iter().map(|e| json!([e["event_type"], e["detail"], e["dedup_key"], e["timestamp"], e["text"], e["cwd"], e["session_id"]])).collect::<Vec<_>>())
    }

    // ---- the golden: the legacy observer's complete documents, compared with the core's -----------------------------------
    //
    // `golden/cursor.json` holds, for every pass of `run`, the document the legacy observer (blob and command recorded in the
    // file) answered, in full, with the run-specific paths masked. The core's answer to the same pass is turned into the legacy
    // document's shape by `legacy_shape` and the two are compared WHOLE. Every step of `legacy_shape` is one named difference
    // between the observers; nothing else is removed or rewritten.

    fn with(extra: Value) -> Value {
        let mut all = supplied();
        all.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap());
        all
    }

    /// The scenario every observer is run through: first sight, a pass of every kind of blob, a shrunken store, the marker scan
    /// with its row and byte bounds, the usage modes that answer nothing and the lookup the vendor never had.
    fn run() -> (Vec<Fixture>, Chain<'static>) {
        let store = parity::store("store.db", RECORDED);
        let path = store.path.clone();
        let mut chain = Chain::new("cursor");
        message(&store, "h1", "user", json!([{"type": "text", "text": "<user_query>history</user_query>"}]));
        message(&store, "h2", "assistant", json!([{"type": "text", "text": "history reply"}]));
        // First sight: the store's history is skipped, the walk planted at the newest rowid.
        chain.pass("activity: first sight of a store with history", &path, "activity", supplied());

        message(&store, "a", "assistant", json!([{"type": "text", "text": "line one"}, {"type": "image"}, {"type": "text", "text": "line two"}]));
        blob(&store, Some("proto"), &[0x0a, 0x04, 0xff, 0xfe]);
        message(&store, "b", "user", json!("<timestamp>x</timestamp>\n<user_query>\n  Reply ok\n</user_query>"));
        message(&store, "c", "user", json!([{"type": "text", "text": format!("<user_query>at-pane:{ID}</user_query>")}]));
        message(&store, "c2", "user", json!([{"type": "text", "text": format!("<user_query><!-- agent-team-session: at-pane:{ID} --></user_query>")}]));
        message(&store, "c3", "user", json!([{"type": "text", "text": format!("<user_query>at-pane:{ID} then work</user_query>")}]));
        message(&store, "d", "user", json!([{"type": "text", "text": "<user_info>context only</user_info>"}]));
        message(&store, "e", "system", json!("never written"));
        blob(&store, None, json!({"role": "assistant", "content": "null id"}).to_string().as_bytes());
        blob(&store, Some("list"), b"[1,2]");
        message(&store, "g", "assistant", json!(7));
        message(&store, "f", "assistant", json!("x".repeat(9000)));
        chain.pass("activity: a pass of every kind of blob", &path, "activity", supplied());
        chain.pass("activity: nothing new", &path, "activity", supplied());

        // A shrunken store re-anchors at its newest row and says nothing; then it continues from there.
        store.sql("DELETE FROM blobs", &[]);
        message(&store, "only", "assistant", json!("one"));
        chain.pass("activity: a shrunken store", &path, "activity", supplied());
        message(&store, "next", "assistant", json!("two"));
        chain.pass("activity: one reply after the shrink", &path, "activity", supplied());

        // Cursor records no usage: both usage modes answer an empty document and keep the bag; a lookup is refused.
        chain.seed(&path, "usage", &["kept"]);
        chain.seed(&path, "session_usage", &["kept"]);
        chain.pass("usage answers nothing", &path, "usage", supplied());
        chain.pass("session_usage answers nothing", &path, "session_usage", supplied());
        chain.pass("has_session is refused", &path, "has_session", supplied());

        // identify: the markers in the first blobs, bound to the session the request names.
        let marked = parity::store("store.db", RECORDED);
        message(&marked, "m", "user", json!("<user_query>at-pane:one-marker</user_query>"));
        blob(&marked, Some("raw"), b"\x00\x01binary at-pane:two-marker\xff");
        chain.pass("identify: markers in a message and in raw bytes", &marked.path, "identify", with(json!({"markers": ["at-pane:one-marker", "at-pane:two-marker", "at-pane:none", ""]})));
        chain.pass("identify: no markers", &marked.path, "identify", with(json!({"markers": []})));

        // Only the first 512 blobs are searched, and only the first 262144 bytes of each.
        let big = parity::store("store.db", RECORDED);
        let mut head = vec![b'.'; 262_144];
        head.extend_from_slice(b"at-pane:beyond-the-bytes");
        blob(&big, Some("long"), &head);
        for index in 0..511 {
            blob(&big, Some(&format!("p{index}")), b"\x00");
        }
        blob(&big, Some("late"), b"at-pane:beyond-the-rows");
        chain.pass("identify: markers beyond the bounds", &big.path, "identify", with(json!({"markers": ["at-pane:beyond-the-bytes", "at-pane:beyond-the-rows"]})));
        (vec![store, marked, big], chain)
    }

    /// The difference that is Cursor's own, on top of the shared ones (`parity::legacy_shape`: the file's identity, the session
    /// lookup Cursor has none of, the typed state `cursor_blob::` spelled as its legacy bag key).
    fn own(answered: &parity::Answered, mode: &str, object: &mut serde_json::Map<String, Value>, _: &Value) {
        if mode == "activity" {
            // Difference (t36k V1): the legacy observer restarted its 1 ms offset at every call (`cursor.py:447-455`), so a call stamped
            // `clock + n ms` for its n-th event whatever the calls before it had stamped, and the pane's gate lost a turn stamped at or
            // before one it had accepted. The core goes on from the last stamp it gave, and follows the clock where that is later: the
            // n-th event of a pass is stamped exactly `max(clock, stamped + 1 ms) + n ms`, `stamped` being the floor the pass was given
            // (t36b W1 / A1). Asserted on the core's side first, then restarted at the clock, which is the legacy document's shape.
            let clock = supplied()["base_epoch"].as_f64().unwrap();
            let first = answered.state_before["marks"]["blobs"]["stamped"].as_i64().map_or(epoch_micros(clock), |floor| epoch_micros(clock).max(floor + 1000));
            for (place, event) in object["events"].as_array_mut().unwrap().iter_mut().enumerate() {
                assert_eq!(event["timestamp"], json!(iso_micros(first + place as i64 * 1000)), "event {place}: exactly max(clock, stamped + 1 ms) + {place} ms");
                event["timestamp"] = json!(iso_seconds(clock + place as f64 * 0.001));
            }
        }
        if mode == "identify" {
            // Difference: the legacy observer answered `true` per found marker and Python bound it to the session the request
            // names; the core answers that binding itself, `[session, ""]`.
            for (_, binding) in object["markers"].as_object_mut().unwrap().iter_mut() {
                assert_eq!(*binding, json!(["chat-1", ""]));
                *binding = json!(true);
            }
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let (stores, chain) = run();
        let roots: Vec<&Path> = stores.iter().map(|store| store.root.as_path()).collect();
        let files: Vec<&Path> = stores.iter().map(|store| store.path.as_path()).collect();
        parity::check_golden("cursor", &chain, &roots, &files, own);
    }

    /// The legacy `cursor_blob::N` mark, migrated to typed state, continues where the baseline would.
    #[test]
    fn a_legacy_blob_mark_migrated_to_typed_state_continues_where_the_baseline_would() {
        let store = parity::store("store.db", RECORDED);
        message(&store, "only", "assistant", json!("one"));
        message(&store, "next", "assistant", json!("two"));
        let migrated = observe(&store, "activity", &json!({"marks": {"blobs": {"last": 1}}}), json!({}));
        assert_eq!(project(&migrated), json!([["turn_complete", "assistant", "blob:next", "2026-10-20T16:26:40+00:00", "two", "/work/c", "chat-1"]]));
        assert_eq!(seen(&migrated), Vec::<String>::new());
    }
}
