//! Codex's adapter: the record vocabulary of Codex rollout JSONL and nothing else.
//!
//! The declaration carries the cumulative usage record (pointers), the modes, the compatibility rules and the Python
//! facts; the core carries the cumulative-to-delta arithmetic, the activity high-water mark and the fingerprinted resume.
//! What stays here, and why it is not data:
//! - `identify`: Codex names its session, working directory and model in a `session_meta` header record, and a
//!   sub-agent's rollout names its parent instead of a session of its own, so the file's identity is read, not declared.
//! - `activity`: a turn's reply is assembled from several records (`agent_message`, the assistant `response_item`), and a
//!   turn ends with an explicit `task_complete` or `turn_aborted` record.
//! - `persist`: the checkpoint carries what the header named, so a later pass need not reread it.
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde_json::{json, Map, Value};
use std::io;

pub struct Codex;

impl Observer for Codex {
    /// Nothing beyond the core's: the open turn's reply text is the core's `last_text`.
    type State = ();
}

fn text(value: &Value) -> String {
    value.as_str().map(str::to_owned).unwrap_or_default()
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

impl JsonlObserver for Codex {
    fn identify(file: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident> {
        // The saved identity describes the file it was saved for; a replaced file starts over.
        let carry = |key: &str| if run.replaced { String::new() } else { ctx.req.cursor.text(key) };
        let mut ident = Ident {
            vendor: ctx.facts.vendor.to_owned(),
            path: ctx.req.path.to_string_lossy().into_owned(),
            stem: ctx.req.path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_owned(),
            native: carry("session_id"),
            cwd: carry("cwd"),
            model: carry("model"),
            version: carry("cli_version"),
            base_epoch: 0.0,
            now_ms: 0,
            namespace: String::new(),
            section: String::new(),
            stamp: String::new(),
            wanted_session: String::new(),
            modified_ms: None,
        };
        // The immutable header sits in the first lines, even above a saved offset.
        if let Some(meta) = file.head(5)?.into_iter().find(|rec| rec["type"] == "session_meta") {
            let p = &meta["payload"];
            ident.cwd = text(&p["cwd"]);
            ident.model = if p["model_provider"].is_null() { text(&p["model"]) } else { text(&p["model_provider"]) };
            ident.version = text(&p["cli_version"]);
            let subagent = p["thread_source"] == "subagent" || !p["parent_thread_id"].is_null() || p["source"].get("subagent").is_some();
            ident.native = if subagent { String::new() } else { text(&p["id"]) };
        }
        Ok(ident)
    }

    fn activity(last_text: &mut String, _: &mut (), ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let p = &rec["payload"];
        // A timestamp that is not a string is empty here, as in the baseline (Claude's log renders any value).
        let timestamp = text(&rec["timestamp"]);
        let mut emit = |kind: &str, key: String, detail: &str, value: String| {
            out.push(activity_event(ident, &timestamp, kind, key, detail, value));
        };
        match rec["type"].as_str() {
            Some("assistant") => emit("agent_active", format!("act:{line}"), "assistant", String::new()),
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
                    *last_text = value;
                }
            }
            Some("event_msg") => {
                let kind = text(&p["type"]);
                if kind == "agent_message" && !text(&p["message"]).is_empty() {
                    *last_text = text(&p["message"]);
                }
                let prompt = typed_prompt(p);
                emit(
                    "agent_active",
                    format!("act:{line}"),
                    if prompt.is_some() { "user_message" } else { &kind },
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
                    emit("turn_complete", format!("turn:{line}"), &kind, reply);
                    last_text.clear();
                }
            }
            _ => {}
        }
    }

    fn persist(ident: &Ident, checkpoint: &mut Map<String, Value>) {
        for (key, value) in [("cwd", &ident.cwd), ("model", &ident.model), ("session_id", &ident.session().to_owned()), ("cli_version", &ident.version)] {
            checkpoint.insert(key.into(), json!(value));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe::adapters::{dispatch, find};

    fn run(path: &std::path::Path, mode: &str, seen: Value, state: Value) -> std::io::Result<Value> {
        let meta = json!({"vendor":"codex","path":path,"mode":mode,"checkpoint":{},"seen":seen,"state":state});
        dispatch(find("codex").unwrap(), &meta)
    }

    fn rollout(name: &str, body: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("codex-adapter-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("rollout.jsonl");
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn the_open_turns_reply_text_lives_in_the_typed_state_and_ends_with_the_turn() {
        let reply = json!({"type":"event_msg","timestamp":"t","payload":{"type":"agent_message","message":"partial reply"}});
        let done = json!({"type":"event_msg","timestamp":"t","payload":{"type":"task_complete"}});
        let path = rollout("lasttext", &format!("{reply}\n"));
        let first = run(&path, "activity", json!([]), json!({})).unwrap();
        assert_eq!((first["state"]["last_text"].clone(), first["state"]["water"].clone()), (json!("partial reply"), json!(1)));
        assert!(first["seen"].as_array().unwrap().iter().all(|key| !key.as_str().unwrap().contains("lasttext")));
        std::fs::write(&path, format!("{reply}\n{done}\n")).unwrap();
        let second = run(&path, "activity", first["seen"].clone(), first["state"].clone()).unwrap();
        let complete = second["events"].as_array().unwrap().iter().find(|event| event["event_type"] == "turn_complete").unwrap();
        assert_eq!(complete["text"], "partial reply");
        assert_eq!(second["state"]["last_text"], "", "the turn ended, so the text is spent");
        std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }

    /// The exact state object the Python codec builds from an old bag (`act_hw::`, `__lasttext__:`, `__cum__:`): the field
    /// names are the wire contract between the two languages, so a rename on either side fails here.
    #[test]
    fn the_state_the_python_codec_migrates_from_an_old_bag_continues_the_open_turn() {
        let done = json!({"type":"event_msg","timestamp":"t","payload":{"type":"task_complete"}});
        let skipped = json!({"type":"assistant","timestamp":"t"});
        let path = rollout("migrated", &format!("{skipped}\n{skipped}\n{skipped}\n{done}\n"));
        let migrated = json!({"water": 3, "last_text": "hello", "totals": {"input": 5, "output": 2}});
        let result = run(&path, "activity", json!([]), migrated).unwrap();
        let kinds: Vec<_> = result["events"].as_array().unwrap().iter().map(|event| event["event_type"].clone()).collect();
        assert_eq!(kinds, [json!("agent_active"), json!("turn_complete")], "lines 1-3 are below the migrated water");
        assert_eq!(result["events"][1]["text"], "hello", "the open turn's text came across the migration");
        assert_eq!(result["state"]["totals"], json!({"input": 5, "output": 2}), "unrelated fields ride through");
        std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn an_activity_timestamp_that_is_not_a_string_is_empty_as_in_the_baseline() {
        let record = json!({"type":"assistant","timestamp":{"not":"a string"}});
        let path = rollout("timestamp", &format!("{record}\n"));
        let result = run(&path, "activity", json!([]), json!({})).unwrap();
        assert_eq!(result["events"][0]["timestamp"], "");
        std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn a_sub_agent_rollout_names_no_session_of_its_own_and_identify_answers_from_the_header() {
        let meta = json!({"type":"session_meta","payload":{"id":"parent-id","cwd":"/w","model_provider":"p","cli_version":"9","parent_thread_id":"x"}});
        let path = rollout("subagent", &format!("{meta}\n"));
        let result = run(&path, "identify", json!([]), json!({})).unwrap();
        assert_eq!((result["native_session_id"].clone(), result["cwd"].clone()), (json!(""), json!("/w")));
        std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }
}
