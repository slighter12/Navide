//! Pi's adapter: the record vocabulary of Pi's session files and nothing else.
//!
//! The declaration carries the usage (`usage_fields` over the view below: the three input counters folded, the output, the model, the
//! entry id as the key), the modes, the compatibility rules and the Python facts; the core carries the window of recent keys, the
//! `credited_count` a rewritten file is read against, the high-water mark and the open turn's reply text. What stays here, and why it
//! is not data:
//! - `usage_view`: Pi spells the usage in two places (`message.usage`, or top level on a compaction entry) and the model in two, so the
//!   record is reshaped once into the fields the declaration names.
//! - `identify` / `persist`: the checkpoint remembers the session and working directory the first pass resolved, and a later pass keeps
//!   them unless the file was rewritten.
//! - `activity` and `settle`: Pi writes no end-of-turn record, so a turn is closed by the next user message or, for the latest one, by the
//!   file having been quiet for eight seconds (the file's modification time against the wall clock the request names).
//!   Source: the retired `observe_pi.rs` (blob `5e8a522b7ae7927098ad423f9cabe94fefca81d3`) lines 189-290, `cli_vendors/pi.py:428-561`.
use crate::observe::baseline::{join_text_blocks, keep_resolved, str_or_empty, QuietTurn};
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde_json::{json, Map, Value};
use std::borrow::Cow;
use std::io;

pub struct Pi;

impl Observer for Pi {
    /// `None` until a turn begins (Python's `None` state).
    type State = Option<QuietTurn>;
}

impl JsonlObserver for Pi {
    /// Pi rewrites its file in place (version migrations, `/tree` branch operations), so the window is wide and the cursor counts what it
    /// credited (`cli_vendors/pi.py:76-78, 337-349`).
    const RECENT: usize = 256;
    const CREDITED: bool = true;

    fn usage_view<'a>(record: &'a Value, _: &[u8]) -> Cow<'a, Value> {
        let message = &record["message"];
        let (usage, model) = if message.is_object() {
            let named = str_or_empty(&message["model"]);
            (&message["usage"], if named.is_empty() { str_or_empty(&record["model"]) } else { named })
        } else {
            (&record["usage"], str_or_empty(&record["model"]))
        };
        if record["type"] == "session" || !usage.is_object() {
            return Cow::Owned(Value::Null);
        }
        Cow::Owned(json!({"kind": "usage", "id": str_or_empty(&record["id"]), "usage": usage, "model": model, "timestamp": str_or_empty(&record["timestamp"])}))
    }

    fn identify(_: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident> {
        let mut ident = Ident::requested(ctx);
        keep_resolved(&mut ident, &ctx.req.cursor, run.rotated, true);
        Ok(ident)
    }

    fn persist(ident: &Ident, checkpoint: &mut Map<String, Value>) {
        checkpoint.insert("session_id".into(), json!(ident.session()));
        checkpoint.insert("cwd".into(), json!(ident.cwd));
    }

    fn activity(last_text: &mut String, state: &mut Option<QuietTurn>, ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let message = &rec["message"];
        let role = if message.is_object() { str_or_empty(&message["role"]) } else { String::new() };
        if rec["type"] != "message" || (role != "user" && role != "assistant") {
            return;
        }
        let ts = str_or_empty(&rec["timestamp"]);
        let mut body = String::new();
        if role == "user" {
            QuietTurn::prompt(state, ident, &ts, last_text, out);
            body = join_text_blocks(&message["content"]);
        } else {
            QuietTurn::reply(state, &ts, &join_text_blocks(&message["content"]), last_text);
        }
        out.push(activity_event(ident, &ts, "agent_active", format!("act:{line}"), &role, body));
    }

    fn settle(last_text: &mut String, state: &mut Option<QuietTurn>, ident: &Ident, out: &mut Vec<Value>) {
        QuietTurn::settle(last_text, state, ident, out);
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use serde_json::{json, Value};

    // The record shapes are the Python reader's own tests: `backend/tests/vendors/test_pi.py:25-79` (`_header`, `_user`, `_assistant`,
    // `_compaction`).
    const LOG: &str = "sessions/--w--/2026_s1.jsonl";
    /// Far in the future of any file's modification time, and before all of them.
    const LATE: (i64, f64) = (4_000_000_000_000, 4_000_000_000.0);
    const EARLY: (i64, f64) = (0, 0.0);

    fn header() -> Value {
        json!({"type": "session", "version": 3, "id": "s1", "timestamp": "2026-07-27T10:00:00.000Z", "cwd": "/w"})
    }

    fn user(id: &str, text: &str, ts: &str) -> Value {
        json!({"type": "message", "id": id, "timestamp": ts, "message": {"role": "user", "content": text}})
    }

    fn assistant(id: &str, text: &str, counts: [i64; 4], ts: &str) -> Value {
        let [input, output, read, write] = counts;
        json!({"type": "message", "id": id, "timestamp": ts, "message": {"role": "assistant", "model": "pi-model", "content": [{"type": "text", "text": text}],
            "usage": {"input": input, "output": output, "cacheRead": read, "cacheWrite": write}}})
    }

    fn compaction(id: &str, input: i64, output: i64, ts: &str) -> Value {
        json!({"type": "compaction", "id": id, "timestamp": ts, "model": "compactor", "usage": {"input": input, "output": output}})
    }

    /// The wall clock Python supplies: the legacy observer reads it in seconds (`now`), the core in milliseconds (`now_ms`).
    fn at(log: &mut Log, clock: (i64, f64)) {
        log.supply("now_ms", json!(clock.0)).supply("now", json!(clock.1));
    }

    fn scenario() -> Log<'static> {
        let mut log = Log::new("pi", LOG, json!({"cwd": "/w", "session": "s1"}));
        log.expects("", &["session_id", "cwd"]).legacy_lacks_activity_flags().core_adds_to_events(&[("cli_version", json!(""))]);
        at(&mut log, EARLY);
        log.append(&[header(), user("u1", "rename this pane", "t1"), assistant("a1", "first", [10, 5, 3, 2], "t2"), compaction("c1", 7, 2, "t3")]).passes("first");
        // The same entry again, entries that count nothing (no tokens, no id, no usage object), a new turn, and a line that is no JSON.
        log.append(&[assistant("a1", "again", [10, 5, 3, 2], "t4"), assistant("a2", "", [0, 0, 0, 0], "t4"), assistant("", "no id", [1, 1, 0, 0], "t4"),
            json!({"type": "message", "id": "n1", "timestamp": "t4", "message": {"role": "assistant", "content": "no usage"}}),
            user("u2", "next", "t5"), assistant("a3", "  second ", [4, 0, 0, 1], "t6")])
            .append_text("this line is not JSON\n\n")
            .passes("more")
            .passes("nothing new");
        // A turn nobody ends closes once the file has been quiet long enough, and only once.
        at(&mut log, LATE);
        log.passes("idle").passes("idle once");
        // A reply with no prompt opens a turn; a user message closes it.
        at(&mut log, EARLY);
        log.append(&[assistant("a4", "reply", [2, 2, 0, 0], "t7"), user("u3", "bye", "t8")]).passes("boundary");
        // A rewritten file is read from its start again: what the cursor credited is not delivered twice, what follows is. A cursor that
        // never recorded a count credits the whole file and delivers nothing.
        log.replaced("replaced").append(&[assistant("a5", "after", [3, 3, 0, 0], "t9")]).passes("new entry");
        let path = log.path().to_owned();
        log.chain.edit_checkpoint(&path, "usage", |checkpoint| {
            checkpoint.as_object_mut().unwrap().remove("credited_count");
            checkpoint["identity"] = json!("1:1");
        });
        log.append(&[assistant("a6", "last", [9, 9, 0, 0], "t10")]).passes("no count");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"type":"message","id":"u4","timestamp":"t11","message":{"role":"user","content":"pla"#).passes("half");
        log.append_text("in\"}}\n").passes("whole");
        // Records that are no objects are skipped.
        log.append_text("[1, 2]\n").passes("not an object");
        log
    }

    /// The differences that are Pi's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`, and what the scenario names: the
    /// `cli_version` the core's usage events carry, the activity flags): the turn state is the answer's `state` (an object, in Python's
    /// member names), where the core spells it as the adapter's typed state and as a legacy key of the bag.
    fn own(mode: &str, object: &mut serde_json::Map<String, Value>, core: &Value) {
        if mode == "activity" {
            object.insert("state".into(), core["state"]["adapter"].clone());
            object["seen"].as_array_mut().unwrap().retain(|key| !key.as_str().unwrap().starts_with("pi_turn::"));
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        parity::check_log_golden("pi", &scenario(), &[], |_, mode, object, core| own(mode, object, core));
    }
}
