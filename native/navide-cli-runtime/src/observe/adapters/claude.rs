//! Claude's adapter: the record vocabulary of Claude's conversation JSONL and nothing else.
//!
//! The declaration carries the usage record (pointers), the modes, the compatibility rules and the Python facts; the core
//! carries the activity high-water mark and the fingerprinted resume. What stays here, and why it is not data:
//! - `activity`: a Claude record becomes several events by rules that read nested content (a tool use named
//!   `AskUserQuestion`, a task notification embedded in a queued text), which a field table cannot express.
//! - `TurnBreakdown`: a Claude turn is a graph (a prompt id groups assistant records, tool results and sidechains), so a
//!   turn is rebuilt from record links, not read from one field.
use crate::observe::{
    activity_event, text_of, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run, TurnBreakdown, TurnCall, TurnRow, Usage,
};
use serde_json::Value;
use std::collections::HashSet;
use std::io;

pub struct Claude;

impl Observer for Claude {
    /// Nothing beyond the core's high-water mark and position.
    type State = ();
}

fn content_text(content: &Value) -> String {
    if let Some(text) = content.as_str() {
        return text.to_owned();
    }
    content.as_array().map_or_else(String::new, |blocks| {
        blocks.iter().filter(|b| b["type"] == "text").map(|b| text_of(&b["text"])).filter(|s| !s.is_empty()).collect::<Vec<_>>().join("\n")
    })
}

impl JsonlObserver for Claude {
    fn identify(_: &mut JsonlFile, ctx: &Ctx, _: &Run<Self>) -> io::Result<Ident> {
        let path = ctx.req.path;
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_owned();
        let parent = path.parent().and_then(|p| p.file_name()).and_then(|s| s.to_str()).unwrap_or("");
        // Claude names a project directory after its working directory with `/` turned into `-`.
        let cwd = if parent.starts_with('-') { parent.replace('-', "/") } else { String::new() };
        Ok(Ident { vendor: ctx.facts.vendor.to_owned(), path: path.to_string_lossy().into_owned(), native: stem.clone(), stem, cwd, ..Ident::default() })
    }

    fn activity(_: &mut String, _: &mut (), ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let kind = rec["type"].as_str().unwrap_or("");
        let message = &rec["message"];
        let timestamp = text_of(&rec["timestamp"]);
        let mut emit = |event_type: &str, key: String, detail: &str, text: String| {
            out.push(activity_event(ident, &timestamp, event_type, key, detail, text));
        };
        if kind == "assistant" {
            emit("agent_active", format!("act:{line}"), "assistant", String::new());
            let blocks = message["content"].as_array().cloned().unwrap_or_default();
            if blocks.iter().any(|b| b["type"] == "tool_use" && b["name"] == "AskUserQuestion") {
                emit("agent_active", format!("q:{line}"), "assistant:question", String::new());
            }
            let stopped = blocks
                .iter()
                .filter(|b| b["type"] == "tool_use" && matches!(b["name"].as_str(), Some("TaskStop" | "KillShell" | "KillBash")))
                .map(|b| if b["input"]["task_id"].is_null() { text_of(&b["input"]["shell_id"]) } else { text_of(&b["input"]["task_id"]) })
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>();
            if !stopped.is_empty() {
                emit("agent_active", format!("bg:{line}"), "background:end", stopped.join("\n"));
            }
            if message["stop_reason"] == "end_turn" {
                emit("turn_complete", format!("turn:{line}"), "end_turn", content_text(&message["content"]));
            }
        } else if kind == "user" || kind == "tool_use" {
            let content = &message["content"];
            let pasted_images = content.as_array().is_some_and(|blocks| {
                blocks.iter().any(|b| b["type"] == "image") && !blocks.iter().any(|b| b["type"] == "tool_result")
            });
            let text = if content.is_string() || pasted_images { content_text(content) } else { String::new() };
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
                text_of(&result["backgroundTaskId"])
            } else if result["status"] == "async_launched" || (result["status"] == "forked" && result["background"] == true) {
                text_of(&result["agentId"])
            } else {
                String::new()
            };
            if kind == "user" && !task.is_empty() {
                emit("agent_active", format!("bg:{line}"), "background:start", task);
            }
        } else if kind == "queue-operation" && rec["operation"] == "enqueue" {
            let content = rec["content"].as_str().unwrap_or("");
            if content.contains("<task-notification>") && content.contains("<status>") {
                let mut ids = Vec::new();
                let mut remaining = content;
                while let Some((_, rest)) = remaining.split_once("<task-id>") {
                    let Some((id, after)) = rest.split_once("</task-id>") else { break };
                    if !id.is_empty() && !id.contains('<') {
                        ids.push(id.to_owned());
                    }
                    remaining = after;
                }
                if !ids.is_empty() {
                    emit("agent_active", format!("bg:{line}"), "background:end", ids.join("\n"));
                }
            }
        }
    }
}

/// One turn per prompt id: the usage of the assistant calls that answered it, built one record at a time.
#[derive(Default)]
pub struct Turns {
    out: Vec<TurnRow>,
    keys: Vec<String>,
    current: Option<usize>,
    seen: HashSet<String>,
}

impl TurnBreakdown for Claude {
    type Builder = Turns;

    fn record(turns: &mut Turns, ident: &Ident, line: u64, rec: &Value, _: &[u8], usage: Option<Usage>) {
        let message = &rec["message"];
        if !message.is_object() {
            return;
        }
        let stamp = Some(text_of(&rec["timestamp"])).filter(|ts| !ts.is_empty());
        if rec["type"] == "user" && rec.get("toolUseResult").is_none() {
            let key = rec["promptId"].as_str().filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| format!("line:{line}"));
            let index = turns.keys.iter().position(|k| k == &key).unwrap_or_else(|| {
                turns.keys.push(key);
                turns.out.push(TurnRow::new(ident.stem.clone(), stamp.clone()));
                turns.out.len() - 1
            });
            turns.current = Some(index);
            let text = content_text(&message["content"]);
            let prior = turns.out[index].prompt_excerpt.as_str();
            if prior.is_empty() || (prior.starts_with('<') && !text.trim().starts_with('<') && !text.trim().is_empty()) {
                turns.out[index].prompt_excerpt = text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(80).collect();
            }
            return;
        }
        let (Some(index), Some(usage)) = (turns.current, usage) else { return };
        if !turns.seen.insert(usage.key.clone()) {
            return;
        }
        let (read, creation) = usage.cache.unwrap_or((0, 0));
        turns.out[index].add_call(TurnCall {
            ts: stamp,
            model: usage.model,
            input: usage.own_input,
            cache_read: read,
            cache_creation: creation,
            output: usage.output,
            cli_version: usage.version,
        });
    }

    fn finish(mut turns: Turns) -> Vec<Value> {
        turns.out.retain(|turn| !turn.calls.is_empty());
        TurnRow::numbered(turns.out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe::adapters::{dispatch, find};
    use serde_json::json;

    fn observe_file(name: &str, body: &str, mode: &str, seen: Value, state: Value) -> std::io::Result<Value> {
        let dir = std::env::temp_dir().join(format!("claude-adapter-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("session.jsonl");
        std::fs::write(&path, body).unwrap();
        let meta = json!({"vendor":"claude","path":path,"mode":mode,"checkpoint":{},"seen":seen,"state":state});
        let document = dispatch(find("claude").unwrap(), &meta);
        std::fs::remove_dir_all(&dir).unwrap();
        document
    }

    #[test]
    fn an_activity_pass_over_a_non_object_record_keeps_its_progress_and_reports_the_error() {
        let done = json!({"type":"assistant","message":{"stop_reason":"end_turn","content":[{"type":"text","text":"x"}]}});
        let body = format!("{}\nnull\n{done}\n", json!({"type":"assistant","message":{"stop_reason":"tool_use","content":[]}}));
        let first = observe_file("null", &body, "activity", json!(["unrelated"]), json!({})).unwrap();
        assert_eq!(first["error"], "Claude record is not an object");
        assert_eq!(first["events"], json!([]), "the events before the bad record are not delivered");
        assert_eq!(first["seen"], json!([]), "no dedup key was delivered (the caller's bag stays as it is; the answer does not echo it)");
        assert_eq!(first["state"]["water"], 2, "the high-water moved past the bad record");
    }

    #[test]
    fn a_usage_pass_skips_a_non_object_record_instead_of_failing() {
        let body = format!("null\n{}\n", json!({"type":"assistant","requestId":"r","message":{"id":"m","usage":{"input_tokens":2,"output_tokens":1}}}));
        let result = observe_file("usage", &body, "usage", json!([]), json!({})).unwrap();
        assert_eq!(result["events"].as_array().unwrap().len(), 1);
        assert!(result["error"].is_null());
    }

    #[test]
    fn a_full_session_pass_fails_on_a_non_object_record_as_the_baseline_does() {
        let refused = observe_file("full", "null\n", "session_usage", json!([]), json!({})).unwrap_err();
        assert_eq!(refused.to_string(), "Claude record is not an object");
    }

    #[test]
    fn a_mode_the_declaration_does_not_list_is_refused_with_the_declared_message() {
        let refused = observe_file("identify", "{}\n", "identify", json!([]), json!({})).unwrap_err();
        assert_eq!(refused.to_string(), "invalid observation mode");
        let unknown = observe_file("unknown", "{}\n", "bogus", json!([]), json!({})).unwrap_err();
        assert_eq!(unknown.to_string(), "invalid observation mode");
    }

    #[test]
    fn the_turn_breakdown_groups_the_assistant_calls_under_their_prompt() {
        let user = json!({"type":"user","promptId":"p1","timestamp":"t0","message":{"content":"hello  world"}});
        let call = |id: &str, tokens: u64| json!({"type":"assistant","requestId":id,"timestamp":"t1","version":"1.2",
            "message":{"id":id,"model":"m","usage":{"input_tokens":tokens,"cache_read_input_tokens":1,"output_tokens":2}}});
        let body = format!("{user}\n{}\n{}\n{}\n", call("a", 3), call("a", 3), call("b", 4));
        let result = observe_file("turns", &body, "turns", json!([]), json!({})).unwrap();
        let turn = &result["turns"][0];
        assert_eq!((turn["turn_index"].clone(), turn["prompt_excerpt"].clone(), turn["input"].clone()), (json!(1), json!("hello world"), json!(7)));
        assert_eq!(turn["calls"].as_array().unwrap().len(), 2, "the repeated call key is counted once");
        assert_eq!((turn["cache_read"].clone(), turn["output"].clone(), turn["cli_version"].clone()), (json!(2), json!(4), json!("1.2")));
    }
}
