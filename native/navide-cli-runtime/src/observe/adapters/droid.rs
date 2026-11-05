//! Droid's adapter: the record vocabulary of Droid's session JSONL and nothing else.
//!
//! The declaration carries the usage (the totals in the session's `<id>.settings.json`, read by the core's cumulative scan from the
//! declared sibling document), the modes, the compatibility rules and the Python facts; the core carries the high-water mark and the
//! open turn's reply text. What stays here, and why it is not data:
//! - `activity`: a message's role and a hook's bookkeeping entry decide whether it is a prompt, and a turn ends with its own record.
//!   Source: the retired `observe_droid.rs` (blob `685159a819e0d1da2f99eafa87d8aa456c8ff358`) lines 118-190, `cli_vendors/droid.py:365-475`.
use crate::observe::baseline::{join_text_blocks, str_or_empty};
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde_json::Value;
use std::io;

pub struct Droid;

impl Observer for Droid {
    type State = ();
}

impl JsonlObserver for Droid {
    /// The working directory and the session come with the request (the discovery reader reads `session_start`).
    fn identify(_: &mut JsonlFile, ctx: &Ctx, _: &Run<Self>) -> io::Result<Ident> {
        Ok(Ident::requested(ctx))
    }

    fn activity(last_text: &mut String, _: &mut (), ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let timestamp = str_or_empty(&rec["timestamp"]);
        let mut emit = |kind: &str, key: String, detail: &str, text: String| out.push(activity_event(ident, &timestamp, kind, key, detail, text));
        if rec["type"] == "agent_turn_outcome" {
            // Whatever the reason: a cancelled or errored turn has stopped too; the reason rides on `detail`.
            emit("turn_complete", format!("turn:{line}"), &str_or_empty(&rec["reason"]), std::mem::take(last_text));
            return;
        }
        let message = &rec["message"];
        if rec["type"] != "message" || !message.is_object() {
            return;
        }
        let (content, key) = (&message["content"], format!("act:{line}"));
        if str_or_empty(&message["role"]) == "assistant" {
            *last_text = join_text_blocks(content);
            return emit("agent_active", key, "assistant", String::new());
        }
        // A `user` message is a prompt, a tool result or a hook's bookkeeping entry: only a prompt carries text.
        let is_block = |kind: &str| content.as_array().is_some_and(|blocks| blocks.iter().any(|b| b.is_object() && b["type"] == kind));
        let prompt = message["role"] == "user" && str_or_empty(&message["hookEventName"]).is_empty() && !is_block("tool_result");
        if prompt {
            last_text.clear();
        }
        emit("agent_active", key, "user", if prompt { join_text_blocks(content) } else { String::new() });
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use crate::observe::sqlite::testing::Fixture;
    use serde_json::{json, Value};
    use std::cell::RefCell;
    use std::path::Path;

    // The record shapes are the Python reader's own tests: `backend/tests/vendors/test_droid.py:127-145` (`_session_start`, `_msg`),
    // `:430-437` (`_write_sidecar`); the outcome record is `:268`, the hook entry `:362`, the tool result `:349`.
    const LOG: &str = "sessions/-w/a.jsonl";

    fn message(role: &str, content: Value, extra: Value) -> Value {
        let mut message = json!({"role": role, "content": content});
        message.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
        json!({"type": "message", "id": "m", "timestamp": "2026-08-26T17:58:46.446Z", "message": message})
    }

    fn sidecar(root: &Path, usage: Value) {
        let body = json!({"model": "claude-opus-5", "tokenUsage": usage}).to_string();
        std::fs::write(root.join("sessions/-w/a.settings.json"), body).unwrap();
    }

    /// The scenario: the recorded request carries the sidecar's path the baseline observer needed (the core derives it from the log's).
    fn run() -> (Fixture, Log<'static>) {
        let dir = Fixture::bare("unused.db");
        let settings = dir.root.join("sessions/-w/a.settings.json");
        let mut log = Log::beside("droid", &dir.root, LOG, json!({"cwd": "/w", "session": "a", "settings_path": settings}));
        log.expects("", &[]).legacy_lacks_activity_flags().core_adds_to_events(&[("timestamp", json!("")), ("cli_version", json!(""))]);
        std::fs::create_dir_all(dir.root.join("sessions/-w")).unwrap();
        sidecar(&dir.root, json!({"inputTokens": 10, "cacheCreationTokens": 5, "cacheReadTokens": 7, "outputTokens": 3, "thinkingTokens": 9}));
        let text = |text: &str| json!([{"type": "text", "text": text}]);
        log.append(&[json!({"type": "session_start", "id": "a", "cwd": "/w"}), message("user", text("rename this pane"), json!({})), message("assistant", text("hi"), json!({}))]);
        log.append(&[json!({"type": "agent_turn_outcome", "turnId": "t1", "reason": "completed"})]).passes("first");
        // A hook entry, a tool result, an unknown record and a record that is no object; the totals grew.
        log.append(&[message("user", json!([]), json!({"hookEventName": "SessionStart"})), message("user", json!([{"type": "tool_result", "content": "x"}]), json!({}))]);
        log.append(&[json!({"type": "compaction_state"}), json!([1, 2]), message("assistant", json!([{"type": "tool_use", "name": "Execute"}]), json!({}))]);
        sidecar(&dir.root, json!({"inputTokens": 25, "outputTokens": 8}));
        log.passes("again").passes("nothing new");
        // A sidecar that shrank is a replacement, not a refund; a malformed one is nothing.
        sidecar(&dir.root, json!({"inputTokens": 10, "outputTokens": 5}));
        log.passes("shrunk");
        sidecar(&dir.root, json!({"inputTokens": 30, "outputTokens": 11}));
        log.passes("grown again");
        std::fs::write(dir.root.join("sessions/-w/a.settings.json"), "{not json").unwrap();
        log.passes("malformed");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"type":"agent_turn_outcome","reason":"comp"#).passes("half");
        log.append_text("leted\"}\n").passes("whole").replaced("replaced");
        (dir, log)
    }

    /// The difference that is Droid's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`): the replay's event key is
    /// `droid_cumulative::<session>::<input>::<output>` where the baseline's was `usage::<session>::<input>::<output>` (kept in the
    /// bag); the two fields the core's events carry (`timestamp`, `cli_version`, both empty here) and the two flags of the activity pass the
    /// baseline never answered (`opened`, `text_changed`) are named by the scenario.
    fn own(kept: &RefCell<(Vec<Value>, Vec<Value>)>, mode: &str, object: &mut serde_json::Map<String, Value>) {
        if mode == "session_usage" {
            let mut keys = Vec::new();
            for event in object["events"].as_array_mut().unwrap().iter_mut() {
                keys.push(json!(event["dedup_key"].as_str().unwrap().replacen("droid_cumulative::", "usage::", 1)));
                event["dedup_key"] = keys.last().unwrap().clone();
            }
            // The baseline's bag kept every event key for good; the core's holds the total that made them (`droid_tok::`). The baseline
            // rewrote that mark in place when it moved; the chain carries the union of the bags a legacy pass answered, so a pass that
            // wrote nothing answers every mark written before it.
            let (marks, usage) = &mut *kept.borrow_mut();
            usage.extend(keys);
            let seen = object["seen"].as_array_mut().unwrap();
            let mark = seen.drain(..).find(|key| key.as_str().unwrap().starts_with("droid_tok::"));
            if let Some(mark) = mark.filter(|mark| !marks.contains(mark)) {
                marks.push(mark.clone());
                seen.push(mark);
            } else {
                seen.extend(marks.iter().cloned());
            }
            seen.extend(usage.iter().cloned());
            seen.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            seen.dedup();
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let (_dir, log) = run();
        let kept = RefCell::default();
        parity::check_log_golden("droid", &log, &[], |_, mode, object, _| own(&kept, mode, object));
    }
}
