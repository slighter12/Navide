//! Muse's adapter: the record vocabulary of Muse's `session.jsonl` and nothing else.
//!
//! The declaration carries the usage (the `goal_usage_attribution` records of the main requester, their microsecond clock and the build
//! version the discovery reader names), the modes, the compatibility rules and the Python facts; the core carries the high-water mark and
//! the open turn's reply text. What stays here, and why it is not data:
//! - `activity`: which record is a prompt, which kinds prove work and which ends the turn is Muse's vocabulary.
//!   Source: the retired `observe_muse.rs` (blob `6dfdc6793d7d9150043aa524695dceed9f56f9fa`) lines 200-270, `cli_vendors/muse.py:551-670`.
use crate::observe::baseline::{cap_text, str_or_empty};
use crate::observe::time::iso_json_micros;
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde_json::Value;
use std::io;

pub struct Muse;

/// The kinds of `runtime.session` event that prove the agent is working (a whitelist: the rest is the task graph's bookkeeping).
const ACTIVE_KINDS: [&str; 6] = ["started", "assistant_message_committed", "side_effect_intent", "model_completed", "completed", "failed"];

impl Observer for Muse {
    type State = ();
}

impl JsonlObserver for Muse {
    /// The working directory, the session (the directory name) and the build version come with the request.
    fn identify(_: &mut JsonlFile, ctx: &Ctx, _: &Run<Self>) -> io::Result<Ident> {
        Ok(Ident::requested(ctx))
    }

    fn activity(last_text: &mut String, _: &mut (), ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let stamp = iso_json_micros(&rec["recorded_at"]);
        let mut emit = |detail: &str, key: String, kind: &str, text: String| out.push(activity_event(ident, &stamp, kind, key, detail, text));
        // A submitted turn is the user's prompt; slash commands, cancels and other intake kinds are not.
        let command = &rec["payload"]["record"]["command"];
        if rec["payload_type"] == "runtime.command_intake.received" && command.is_object() && command["kind"] == "turn_submit" {
            return emit("user", format!("act:{line}"), "agent_active", str_or_empty(&command["prompt"]));
        }
        let event = &rec["payload"]["event"];
        if rec["payload_type"] != "runtime.session" || !event.is_object() {
            return;
        }
        let kind = str_or_empty(&event["kind"]);
        if kind == "assistant_message_committed" {
            let reply = str_or_empty(&event["text"]).trim().to_owned();
            if !reply.is_empty() {
                *last_text = cap_text(&reply);
            }
        }
        if ACTIVE_KINDS.contains(&kind.as_str()) {
            emit(&kind, format!("act:{line}"), "agent_active", String::new());
        }
        if kind == "terminal" {
            // Any terminal value ends the turn (the pane is idle again whether the run completed or failed); the value rides on `detail`.
            emit(&str_or_empty(&event["terminal"]), format!("turn:{line}"), "turn_complete", std::mem::take(last_text));
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use serde_json::{json, Value};

    // The record shapes are the Python reader's own tests: `backend/tests/vendors/test_muse.py:38-113` (`_envelope`, `_intake`, `_run`,
    // `_usage_row`, `_assistant`, `_terminal`, `_diagnostic`).
    const LOG: &str = "sessions/2026/08/12/s1/session.jsonl";
    const T0: i64 = 1_786_491_820_000_000;

    fn envelope(seq: i64, payload_type: &str, payload: Value) -> Value {
        json!({"schema_version": 1, "id": format!("env-{seq}"), "sequence": seq, "recorded_at": T0 + seq, "payload_type": payload_type, "payload": payload})
    }

    fn run(seq: i64, event: Value) -> Value {
        envelope(seq, "runtime.session", json!({"kind": "run", "event": event}))
    }

    fn usage(id: &str, seq: i64, input: i64, output: i64, requester: &str) -> Value {
        run(seq, json!({"kind": "goal_usage_attribution", "record": {"usage_id": id, "quantity": {"input_tokens": input, "output_tokens": output},
            "owner": {"requester_kind": requester}}}))
    }

    fn intake(seq: i64, kind: &str, prompt: &str) -> Value {
        envelope(seq, "runtime.command_intake.received", json!({"kind": "command_intake", "record": {"command": {"kind": kind, "prompt": prompt}}}))
    }

    fn event(seq: i64, kind: &str, extra: Value) -> Value {
        let mut event = json!({"kind": kind});
        event.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap());
        run(seq, event)
    }

    /// The scenario: what Python supplies (the working directory and build version from the metadata head, the session directory).
    fn scenario() -> Log<'static> {
        let mut log = Log::new("muse", LOG, json!({"cwd": "/w", "session": "s1", "cli_version": "1.2.3"}));
        log.expects("", &[]).legacy_lacks_activity_flags().core_adds_to_events(&[("model", json!(""))]);
        log.append(&[envelope(1, "runtime.session.metadata", json!({"kind": "metadata", "record": {"workspace_root": "/w"}})),
            intake(2, "turn_submit", "rename this pane"), event(3, "started", json!({})), usage("u1", 4, 100, 20, "main"),
            event(5, "assistant_message_committed", json!({"text": "  hello  "})), event(6, "context_block_diagnostic", json!({})),
            event(7, "terminal", json!({"terminal": "completed"}))])
            .passes("first");
        // A repeated id, a record that counts nothing, a subagent's usage, an intake that is no turn, a reply over the cap, a line that is
        // no JSON, a record that is no object, a blank line and an empty reply that leaves the earlier text where it was.
        log.append(&[usage("u1", 8, 100, 20, "main"), usage("u0", 9, 0, 0, "main"), usage("u2", 10, 7, 3, "main"), usage("sub", 11, 50, 5, "subagent"),
            intake(12, "slash_command", "/help"), event(13, "side_effect_intent", json!({})),
            event(14, "assistant_message_committed", json!({"text": "R".repeat(5000)})), event(15, "assistant_message_committed", json!({"text": "   "}))])
            .append_text("this line is not JSON\n[1, 2]\n\n");
        log.append(&[event(16, "terminal", json!({"terminal": "failed"})), event(17, "model_completed", json!({})), event(18, "completed", json!({}))])
            .passes("again")
            .passes("nothing new");
        // More distinct ids than the usage window keeps: the first ones are forgotten by `usage`, never by `session_usage`.
        let many: Vec<Value> = (0..70).map(|n| usage(&format!("w{n}"), 20 + n, 1 + n, 1, "main")).collect();
        log.append(&many).passes("window").append(&[usage("u1", 100, 100, 20, "main")]).passes("an id the window forgot");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"recorded_at":1786491820000200,"payload_type":"runtime.session","payload":{"event":{"kind":"term"#).passes("half");
        log.append_text("inal\",\"terminal\":\"completed\"}}}\n").passes("whole").replaced("replaced");
        log
    }

    /// Muse has no differences of its own: the `model` field the core's usage events carry (empty here) and the two flags its activity
    /// answers name are the shared ones, named by the scenario.
    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        parity::check_log_golden("muse", &scenario(), &[], |_, _, _, _| {});
    }
}
