//! Antigravity's adapter: what a step row means, and nothing else.
//!
//! A conversation is one SQLite file whose `steps` table keeps `idx`, `step_type` and `status` as plain integers and the text
//! inside undocumented protobuf payloads, of which only the top-level fields read here are decoded. The declaration carries
//! the walk (by `idx`, planted at the newest step), the columns it reads and the request fields Python supplies per file (the
//! workspace `cwd`, the conversation id). Antigravity records no usage. What stays here, and why it is not data:
//! - `mark_progress`: an assistant step is written before it is done and is read again until its status says so, and its time
//!   is decoded from its protobuf metadata (the declared `pending_max_age` ages a step that never finishes against it);
//! - `mark_event`: what a step means by its type and status, and the text and time decoded from its protobuf;
//! - the protobuf decoding, which is the one part of the format that is code.
use crate::observe::adapters::{AntigravityMark, AntigravityStepsColumn, AntigravityWalk};
use crate::observe::sqlite::blob_bytes;
use crate::observe::time::iso_seconds;
use crate::observe::{activity_event, CellOf, Ident, Observer, Pass, Progress, SqlObserver};
use serde_json::Value;
use std::io;

pub struct Antigravity;

impl Observer for Antigravity {
    /// Nothing beyond the core's activity marks.
    type State = ();
}

const STEP_USER: i64 = 14;
const STEP_ASSISTANT: i64 = 15;
const STEP_QUESTION: i64 = 138;
const STATUS_DONE: i64 = 3;
const USER_PAYLOAD: u64 = 19;
const ASSISTANT_PAYLOAD: u64 = 20;
const META_END_TIMESTAMPS: [u64; 3] = [8, 7, 6];

/// `(value, next index)` of a varint; `None` for a truncated or oversized one.
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

/// Top-level `(field, value)` pairs of varint and length-delimited fields; stops at the first byte that does not parse.
/// Fixed-width fields are skipped, groups end the scan.
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

/// The first clean string among `inner` fields of `payload`'s `outer` submessage.
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

/// Epoch seconds of the first present end/start `Timestamp` in the metadata.
fn step_seconds(metadata: &[u8]) -> Option<f64> {
    META_END_TIMESTAMPS.iter().find_map(|field| {
        let stamp = pb_bytes(metadata, *field);
        (!stamp.is_empty()).then(|| pb_varint(stamp, 1).filter(|s| *s != 0)).flatten().map(|seconds| seconds as f64)
    })
}

/// ISO 8601 of a step's time, empty where the metadata holds none.
fn step_timestamp(metadata: &[u8]) -> String {
    step_seconds(metadata).map_or_else(String::new, iso_seconds)
}

/// Python `int(v or 0)` of an integer cell; `idx` is -1 for a conversation's first row, so nothing is clamped.
fn signed(value: &Value) -> i64 {
    value.as_i64().or_else(|| value.as_f64().map(|f| f as i64)).or_else(|| value.as_str().and_then(|s| s.trim().parse().ok())).unwrap_or(0)
}

fn cell_bytes(cell: &Value) -> Vec<u8> {
    blob_bytes(cell).unwrap_or_default()
}

impl SqlObserver for Antigravity {
    type Walk = AntigravityWalk;

    fn mark_progress(mark: AntigravityMark<'_>) -> Progress {
        let AntigravityMark::Steps(mark) = mark;
        Progress {
            pending: signed(mark.cell(AntigravityStepsColumn::StepType)) == STEP_ASSISTANT && signed(mark.cell(AntigravityStepsColumn::Status)) != STATUS_DONE,
            stamp: step_seconds(&cell_bytes(mark.cell(AntigravityStepsColumn::Metadata))),
        }
    }

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: AntigravityMark<'_>, ident: &Ident, pass: &Pass, _siblings: F) -> io::Result<Vec<Value>> {
        let AntigravityMark::Steps(mark) = mark;
        let (idx, step_type, status) = (mark.id, signed(mark.cell(AntigravityStepsColumn::StepType)), signed(mark.cell(AntigravityStepsColumn::Status)));
        let (metadata, payload) = (cell_bytes(mark.cell(AntigravityStepsColumn::Metadata)), mark.bytes());
        // The dedup key names the conversation (the walk's `key_scope: session`), so the core spells it.
        let make = |event_type: &str, detail: &str, body: String| {
            activity_event(ident, &step_timestamp(&metadata), event_type, mark.dedup.clone(), detail, body)
        };
        Ok(vec![match step_type {
            STEP_USER => make("agent_active", "user", pb_text(&payload, USER_PAYLOAD, &[2])),
            STEP_QUESTION => make("turn_complete", "assistant:question", String::new()),
            STEP_ASSISTANT => {
                let reply = pb_text(&payload, ASSISTANT_PAYLOAD, &[1, 8]);
                if !reply.is_empty() && status == STATUS_DONE && idx == pass.newest {
                    make("turn_complete", "assistant", reply)
                } else if pb_text(&payload, ASSISTANT_PAYLOAD, &[3]).is_empty() {
                    make("agent_active", "assistant", String::new())
                } else {
                    make("agent_active", "assistant:thinking", String::new())
                }
            }
            other => make("agent_active", &format!("step-{other}"), String::new()),
        }])
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, core, events, meta, Chain};
    use crate::observe::sqlite::testing::Fixture;
    use serde_json::{json, Value};
    use std::path::Path;

    // The schema recorded off agy: tests/fixtures/cli-regression/antigravity.json, phases.initial[0].schema.
    const RECORDED: &str = include_str!("../../../../../tests/fixtures/cli-regression/antigravity.json");

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    fn varint(mut value: u64) -> Vec<u8> {
        let mut out = Vec::new();
        loop {
            let byte = (value & 0x7f) as u8;
            value >>= 7;
            if value == 0 {
                out.push(byte);
                return out;
            }
            out.push(byte | 0x80);
        }
    }

    fn bytes_field(field: u64, body: &[u8]) -> Vec<u8> {
        let mut out = varint(field << 3 | 2);
        out.extend(varint(body.len() as u64));
        out.extend_from_slice(body);
        out
    }

    fn varint_field(field: u64, value: u64) -> Vec<u8> {
        let mut out = varint(field << 3);
        out.extend(varint(value));
        out
    }

    /// The metadata of a step: an end time (`Timestamp` seconds) under field 8.
    fn at(seconds: u64) -> Vec<u8> {
        bytes_field(8, &varint_field(1, seconds))
    }

    /// An assistant payload: field 20 holding the reply (1), the alternative reply (8) and the thinking (3).
    fn reply(parts: &[(u64, &str)]) -> Vec<u8> {
        bytes_field(20, &parts.iter().flat_map(|(field, text)| bytes_field(*field, text.as_bytes())).collect::<Vec<_>>())
    }

    fn typed(text: &str) -> Vec<u8> {
        bytes_field(19, &bytes_field(2, text.as_bytes()))
    }

    fn step(store: &Fixture, idx: i64, kind: i64, status: i64, metadata: &[u8], payload: &[u8]) {
        store.sql(&format!("INSERT INTO steps (idx, step_type, status, metadata, step_payload) VALUES ({idx}, {kind}, {status}, X'{}', X'{}')", hex(metadata), hex(payload)), &[]);
    }

    fn finish(store: &Fixture, idx: i64, text: &str) {
        store.sql(&format!("UPDATE steps SET status = 3, step_payload = X'{}' WHERE idx = {idx}", hex(&reply(&[(1, text)]))), &[]);
    }

    fn observe(store: &Fixture, state: &Value) -> Value {
        core("antigravity", &meta(&store.path, "activity", &[], &json!({}), state, json!({"cwd": "/work/a", "session": "conv-1"}))).unwrap()
    }

    fn project(document: &Value) -> Value {
        json!(events(document).iter().map(|e| json!([e["event_type"], e["detail"], e["dedup_key"], e["timestamp"], e["text"]])).collect::<Vec<_>>())
    }

    // ---- the golden: the legacy observer's complete documents, compared with the core's -----------------------------------
    //
    // `golden/antigravity.json` holds, for every pass of `run`, the document the legacy observer (blob and command recorded in
    // the file) answered, in full, with the run-specific paths masked. The core's answer to the same pass is turned into the
    // legacy document's shape by `legacy_shape` and the two are compared WHOLE; each step of `legacy_shape` is one named
    // difference between the observers, and nothing else is removed or rewritten.

    fn supplied() -> Value {
        json!({"cwd": "/work/a", "session": "conv-1"})
    }

    /// The scenario every observer is run through: an empty store, first sight with a step at idx -1, a pass of every kind of
    /// step, a pending step read again, a pending step that vanished, a migrated `agy_idx::`/`agy_pending::` bag, the usage modes
    /// that answer nothing and the lookups the vendor never had.
    fn run() -> (Vec<Fixture>, Chain<'static>) {
        let store = parity::store("conversation.db", RECORDED);
        let path = store.path.clone();
        let mut chain = Chain::new("antigravity");
        // An empty store plants nothing, so the next sight of it is a first sight again.
        chain.pass("activity: an empty store", &path, "activity", supplied());

        // History, with the conversation's first row at idx -1: first sight plants the walk at the newest step.
        step(&store, -1, 14, 3, &at(1792513500), &typed("history"));
        step(&store, 0, 15, 3, &at(1792513501), &reply(&[(1, "history reply")]));
        chain.pass("activity: first sight of a store with history", &path, "activity", supplied());

        // One of each kind of step. Only the newest reply is a turn end; a reply under thinking or without text is activity;
        // a text with a control character is skipped for the next clean one; a step with no readable time has none.
        step(&store, 1, 14, 3, &at(1792513601), &typed("hello"));
        step(&store, 2, 15, 1, &at(1792513602), &reply(&[(3, "thinking")]));
        step(&store, 3, 7, 3, &[], &[]);
        step(&store, 4, 15, 3, &at(1792513604), &reply(&[(1, "an answer, not the newest")]));
        step(&store, 5, 138, 3, &at(1792513605), &[]);
        step(&store, 6, 15, 3, &at(1792513606), &reply(&[(8, "  alternative  ")]));
        step(&store, 7, 15, 2, &at(1792513607), &reply(&[]));
        step(&store, 8, 15, 3, &[0xff], &reply(&[(1, "bad \u{1}text"), (1, "good")]));
        chain.pass("activity: a pass of every kind of step", &path, "activity", supplied());

        // A pending step is read again without repeating its event; one that finishes (and is not the newest) is reported once.
        finish(&store, 2, "finished later");
        step(&store, 9, 15, 3, &at(1792513609), &reply(&[(1, "final")]));
        chain.pass("activity: a pending step finishes and a reply arrives", &path, "activity", supplied());
        chain.pass("activity: nothing new", &path, "activity", supplied());

        // A pending step the store no longer holds could never finish.
        step(&store, 10, 15, 0, &[], &reply(&[]));
        chain.pass("activity: a new pending step", &path, "activity", supplied());
        store.sql("DELETE FROM steps WHERE idx = 10", &[]);
        step(&store, 11, 14, 3, &at(1792513611), &typed("next"));
        chain.pass("activity: a pending step that is gone", &path, "activity", supplied());

        // The legacy bag (`agy_idx::9`, `agy_pending::[7]`), migrated to typed state, continues exactly where the baseline would.
        let migrated = parity::store("conversation.db", RECORDED);
        step(&migrated, 7, 15, 1, &at(1792513607), &reply(&[]));
        step(&migrated, 9, 15, 3, &at(1792513609), &reply(&[(1, "final")]));
        finish(&migrated, 7, "seven");
        chain.migrate(&migrated.path, "activity", json!({"marks": {"steps": {"last": 9, "pending": [7]}}}));
        chain.pass("activity: a migrated bag", &migrated.path, "activity", supplied());

        // Antigravity records no usage: both usage modes answer an empty document and keep the bag; no lookup is answered.
        chain.seed(&path, "usage", &["kept"]);
        chain.seed(&path, "session_usage", &["kept"]);
        chain.pass("usage answers nothing", &path, "usage", supplied());
        chain.pass("session_usage answers nothing", &path, "session_usage", supplied());
        chain.pass("identify is refused", &path, "identify", supplied());
        chain.pass("has_session is refused", &path, "has_session", supplied());
        (vec![store, migrated], chain)
    }

    // Every difference between the two observers' answers for Antigravity is one the vendors share (`parity::legacy_shape`):
    // the file's identity, the lookups it has none of, the pending step the store no longer holds, and the typed state
    // (`agy_idx::`, `agy_pending::`) spelled as the legacy bag keys. None is its own.

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let (stores, chain) = run();
        let roots: Vec<&Path> = stores.iter().map(|store| store.root.as_path()).collect();
        let files: Vec<&Path> = stores.iter().map(|store| store.path.as_path()).collect();
        parity::check_golden("antigravity", &chain, &roots, &files, |_, _, _, _| {});
    }

    #[test]
    fn a_payload_text_field_must_be_clean_prose_and_a_truncated_length_ends_the_scan() {
        let store = parity::store("conversation.db", RECORDED);
        step(&store, 1, 14, 3, &at(1792513601), &typed("hello"));
        let planted = observe(&store, &json!(null));
        // A user step whose declared length runs past its payload reads as no text; one with a control character likewise.
        step(&store, 2, 14, 3, &at(1792513602), &[0x9a, 0x01, 0x7f, b'x']);
        step(&store, 3, 14, 3, &at(1792513603), &typed("ctrl\u{2} char"));
        let pass = observe(&store, &planted["state"]);
        assert_eq!(
            project(&pass),
            json!([
                ["agent_active", "user", "step:conv-1:2", "2026-10-20T16:26:42+00:00", ""],
                ["agent_active", "user", "step:conv-1:3", "2026-10-20T16:26:43+00:00", ""],
            ])
        );
    }
}
