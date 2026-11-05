//! Qwen's adapter: the record vocabulary of Qwen's chat records and nothing else.
//!
//! The declaration carries the usage (`usage_fields`: the prompt count as the input, the candidates and thoughts as the output, the cache
//! read beside the input, the uuid as the key, the record's own model, version, timestamp and working directory), the modes, the
//! compatibility rules and the Python facts; the core carries the window of recent keys, the high-water mark and the open turn's reply text.
//! What stays here, and why it is not data:
//! - `activity` and `settle`: Qwen writes no end-of-turn record (`QuietTurn`), a user record is a prompt unless its subtype says another
//!   writer put it there, and a tool result or a notification record names the background tasks that started and ended.
//!   Source: the retired `observe_qwen.rs` (blob `d572862e9a705f5059e09d4a86605ce4c4f0d6a6`) lines 242-367, `cli_vendors/qwen.py:420-560`.
use crate::observe::baseline::{str_or_empty, QuietTurn};
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde_json::Value;
use std::io;

pub struct Qwen;

impl Observer for Qwen {
    /// `None` until a turn begins (Python's `None` state).
    type State = Option<QuietTurn>;
}

/// User records another writer put in the log: they are not the user's prompts.
const EXCLUDED_USER_SUBTYPES: [&str; 5] = ["mid_turn_user_message", "cron", "notification", "agent_mention", "agent_message"];
const BG_START: &str = "background:start";
const BG_END: &str = "background:end";

/// `"\n".join(p["text"] for p in message.parts if p is a dict with a str text)`.
fn parts_text(rec: &Value) -> String {
    let parts = rec["message"]["parts"].as_array().into_iter().flatten();
    parts.filter_map(|part| part["text"].as_str().filter(|_| part.is_object())).collect::<Vec<_>>().join("\n")
}

/// The `\S+` run that follows `prefix` at the very start of `output` (Python `re.match`).
fn token_after<'a>(output: &'a str, prefix: &str) -> Option<&'a str> {
    let rest = output.strip_prefix(prefix)?;
    let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
    (end > 0).then(|| &rest[..end])
}

fn background_started(rec: &Value) -> Vec<String> {
    let parts = rec["message"]["parts"].as_array().into_iter().flatten();
    parts
        .filter_map(|part| part["functionResponse"]["response"]["output"].as_str())
        .filter_map(|output| token_after(output, "Background agent launched successfully.\ntask_id: ").or_else(|| token_after(output, "Background shell started.\nid: ")))
        .map(str::to_owned)
        .collect()
}

/// Every `<tag>([^<]+)</tag>` capture, in order.
fn captures(body: &str, tag: &str) -> Vec<String> {
    let (open, close) = (format!("<{tag}>"), format!("</{tag}>"));
    let (mut found, mut rest) = (Vec::new(), body);
    while let Some(start) = rest.find(&open) {
        let after = &rest[start + open.len()..];
        let inner_end = after.find('<').unwrap_or(after.len());
        if inner_end > 0 && after[inner_end..].starts_with(&close) {
            found.push(after[..inner_end].to_owned());
            rest = &after[inner_end + close.len()..];
        } else {
            rest = &rest[start + 1..];
        }
    }
    found
}

/// The tasks a notification record says are no longer running (the first status decides).
fn background_finished(rec: &Value) -> Vec<String> {
    let body = parts_text(rec);
    if str_or_empty(&rec["subtype"]) != "notification" || !body.contains("<task-notification>") {
        return Vec::new();
    }
    match captures(&body, "status").first() {
        Some(status) if status != "running" => captures(&body, "task-id"),
        _ => Vec::new(),
    }
}

impl JsonlObserver for Qwen {
    fn identify(_: &mut JsonlFile, ctx: &Ctx, _: &Run<Self>) -> io::Result<Ident> {
        Ok(Ident::requested(ctx))
    }

    fn activity(last_text: &mut String, state: &mut Option<QuietTurn>, ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let kind = rec["type"].as_str().unwrap_or("");
        let ts = str_or_empty(&rec["timestamp"]);
        let prompt = kind == "user" && !EXCLUDED_USER_SUBTYPES.contains(&str_or_empty(&rec["subtype"]).as_str());
        if kind == "assistant" || prompt {
            let mut body = String::new();
            if prompt {
                QuietTurn::prompt(state, ident, &ts, last_text, out);
                body = parts_text(rec);
            } else {
                QuietTurn::reply(state, &ts, &parts_text(rec), last_text);
            }
            out.push(activity_event(ident, &ts, "agent_active", format!("act:{line}"), kind, body));
        }
        let (tasks, detail) = match kind {
            "tool_result" => (background_started(rec), BG_START),
            "user" => (background_finished(rec), BG_END),
            _ => return,
        };
        if !tasks.is_empty() {
            out.push(activity_event(ident, &ts, "agent_active", format!("bg:{line}"), detail, tasks.join("\n")));
        }
    }

    fn settle(last_text: &mut String, state: &mut Option<QuietTurn>, ident: &Ident, out: &mut Vec<Value>) {
        QuietTurn::settle(last_text, state, ident, out);
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use serde_json::{json, Value};

    // The record shapes are the Python reader's own tests: an assistant record with `usageMetadata` (`backend/tests/vendors/test_qwen.py:19-45`,
    // `_rec`, `_assistant`), a user record with `message.parts` (`:46-53`, `_user`), a tool result with a `functionResponse` (`:608-618`, the
    // launch texts at `:641-645`), a notification record with a task notification (`:665-672`).
    const LOG: &str = "projects/-w/chats/s1.jsonl";
    /// Far in the future of any file's modification time, and before all of them.
    const LATE: (i64, f64) = (4_000_000_000_000, 4_000_000_000.0);
    const EARLY: (i64, f64) = (0, 0.0);

    fn assistant(uuid: &str, text: &str, counts: [i64; 4], ts: &str) -> Value {
        let [prompt, candidates, thoughts, cached] = counts;
        json!({"type": "assistant", "uuid": uuid, "timestamp": ts, "cwd": "/w", "model": "qwen-model", "version": "1.2.3", "message": {"parts": [{"text": text}]},
            "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": candidates, "thoughtsTokenCount": thoughts, "cachedContentTokenCount": cached}})
    }

    fn user(text: &str, subtype: Option<&str>, ts: &str) -> Value {
        let mut record = json!({"type": "user", "uuid": format!("u-{ts}"), "timestamp": ts, "cwd": "/w", "message": {"parts": [{"text": text}]}});
        if let Some(subtype) = subtype {
            record["subtype"] = json!(subtype);
        }
        record
    }

    fn launched(output: &str, ts: &str) -> Value {
        json!({"type": "tool_result", "timestamp": ts, "cwd": "/w", "message": {"parts": [{"functionResponse": {"response": {"output": output}}}]}})
    }

    fn at(log: &mut Log, clock: (i64, f64)) {
        log.supply("now_ms", json!(clock.0)).supply("now", json!(clock.1));
    }

    fn scenario() -> Log<'static> {
        let mut log = Log::new("qwen", LOG, json!({"cwd": "/w", "session": "s1"}));
        log.expects("", &[]).legacy_lacks_activity_flags();
        at(&mut log, EARLY);
        log.append(&[user("rename this pane", None, "t1"), assistant("a1", "first", [10, 5, 2, 3], "t2"), launched("Background agent launched successfully.\ntask_id: bg1\n", "t3")]).passes("first");
        // The same uuid again, records that count nothing, prompts another writer put there, a finished background task, a line that is no JSON.
        log.append(&[assistant("a1", "again", [10, 5, 2, 3], "t4"), assistant("a2", "", [0, 0, 0, 0], "t4"), user("tick", Some("cron"), "t5"),
            json!({"type": "user", "subtype": "notification", "timestamp": "t6", "message": {"parts": [{"text": "<task-notification><task-id>bg1</task-id><status>completed</status></task-notification>"}]}}),
            launched("Background shell started.\nid: sh2", "t7"), user("next", None, "t8"), assistant("a3", "  second ", [4, 1, 0, 0], "t9")])
            .append_text("this line is not JSON\n\n")
            .passes("more")
            .passes("nothing new");
        // A turn nobody ends closes once the file has been quiet long enough, and only once.
        at(&mut log, LATE);
        log.passes("idle").passes("idle once");
        // A reply with no prompt opens a turn; a prompt closes it.
        at(&mut log, EARLY);
        log.append(&[assistant("a4", "reply", [2, 2, 0, 0], "t10"), user("bye", None, "t11")]).passes("boundary");
        log.replaced("replaced");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"type":"user","timestamp":"t12","message":{"parts":[{"text":"pla"#).passes("half");
        log.append_text("in\"}]}}\n").passes("whole");
        // A record that is no object fails the passes that read the whole log.
        log.append_text("[1, 2]\n").passes("not an object");
        log
    }

    /// The differences that are Qwen's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`): the usage events name the
    /// cache creation (none) beside the cache read; the turn state is the answer's `state` (an object, in Python's member names), where
    /// the core spells it as the adapter's typed state and as a legacy key of the bag.
    fn own(mode: &str, object: &mut serde_json::Map<String, Value>, core: &Value) {
        if mode == "activity" {
            object.insert("state".into(), core["state"]["adapter"].clone());
            object["seen"].as_array_mut().unwrap().retain(|key| !key.as_str().unwrap().starts_with("qwen_turn::"));
            return;
        }
        for event in object["events"].as_array_mut().unwrap() {
            assert_eq!(event.as_object_mut().unwrap().remove("cache_creation_tokens"), Some(json!(0)));
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        parity::check_log_golden("qwen", &scenario(), &[], |_, mode, object, core| own(mode, object, core));
    }
}
