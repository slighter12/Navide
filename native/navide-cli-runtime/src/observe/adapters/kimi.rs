//! Kimi's adapter: the record vocabulary of Kimi's `wire.jsonl` and nothing else.
//!
//! The declaration carries the usage (`usage.record` lines: the three input counters folded, the output, the model and the millisecond
//! time), the modes, the compatibility rules and the Python facts; the core carries the high-water mark and the open turn's reply text.
//! What stays here, and why it is not data:
//! - `position_key`: no field of a usage record is an id; it is named by its place (the line in a replay, the offset after it in an
//!   incremental pass), as `cli_vendors/kimi.py` keyed it.
//! - `identify`: the checkpoint remembers the working directory the first pass resolved, and a later pass keeps it.
//! - `activity` and `settle`: a turn is a state machine over the prompt, the loop events and the end/cancel records, and one that
//!   nobody ends closes after eight seconds of silence on the wall clock the request names.
//!   Source: the retired `observe_kimi.rs` (blob `986927e1004e5ca3efcd1cf4cb24d44b95753f34`) lines 213-355, `cli_vendors/kimi.py:300-480`.
use crate::observe::baseline::{cap_text, int_text, join_text_blocks, keep_resolved, py_int, str_or_empty};
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::io;

pub struct Kimi;

/// A turn left open: its number, the latest time any record of it carried (ms), and whether it has ended or been cancelled.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Turn {
    idx: i64,
    last_ms: i64,
    #[serde(default)]
    flushed: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    cancelled: bool,
}

impl Observer for Kimi {
    /// `None` until a turn begins (Python's `None` state).
    type State = Option<Turn>;
}

/// A turn nobody ended closes once the log has been quiet this long on the wall clock.
const TURN_IDLE_MS: i64 = 8_000;

impl JsonlObserver for Kimi {
    fn identify(_: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident> {
        let mut ident = Ident::requested(ctx);
        keep_resolved(&mut ident, &ctx.req.cursor, run.rotated, false);
        Ok(ident)
    }

    fn persist(ident: &Ident, checkpoint: &mut Map<String, Value>) {
        checkpoint.insert("cwd".into(), json!(ident.cwd));
    }

    fn position_key(ident: &Ident, replay: bool, line: u64, end: u64, _: &Value) -> Option<String> {
        let session = ident.session();
        Some(if replay { format!("kimi::{session}::L{line}") } else { format!("kimi::{session}::@{end}") })
    }

    fn activity(last_text: &mut String, state: &mut Option<Turn>, ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let tms = py_int(&rec["time"]);
        let stamp = int_text(&rec["time"]);
        let mut emit = |kind: &str, key: String, stamp: String, detail: &str, text: String| out.push(activity_event(ident, &stamp, kind, key, detail, text));
        let close = |turn: &Turn, last_text: &mut String| -> (String, String) { (format!("turn:{}", turn.idx), std::mem::take(last_text)) };
        match rec["type"].as_str().unwrap_or("") {
            "turn.prompt" => {
                if let Some(open) = state.as_ref().filter(|turn| !turn.flushed) {
                    let (key, text) = close(open, last_text);
                    emit("turn_complete", key, open.last_ms.to_string(), "boundary", text);
                }
                *state = Some(Turn { idx: state.as_ref().map_or(0, |turn| turn.idx + 1), last_ms: tms, ..Turn::default() });
                // Python applies `user_prompt_text` to the text of a `prompt` event.
                emit("agent_active", format!("act:{line}"), stamp, "prompt", join_text_blocks(&rec["input"]));
            }
            "usage.record" => {
                if let Some(open) = state.as_mut().filter(|turn| !turn.flushed) {
                    open.last_ms = open.last_ms.max(tms);
                }
                emit("agent_active", format!("act:{line}"), stamp, "usage", String::new());
            }
            "context.append_loop_event" => {
                if state.as_ref().is_some_and(|turn| turn.cancelled) {
                    return;
                }
                if let Some(open) = state.as_mut().filter(|turn| !turn.flushed) {
                    open.last_ms = open.last_ms.max(tms);
                }
                let part = &rec["event"]["part"];
                if rec["event"]["type"] == "content.part" && part.is_object() && matches!(part["type"].as_str(), Some("text" | "think")) {
                    let body = str_or_empty(&part["text"]).trim().to_owned();
                    if body.is_empty() {
                        return;
                    }
                    if state.as_ref().is_none_or(|turn| turn.flushed) {
                        *state = Some(Turn { idx: state.as_ref().map_or(0, |turn| turn.idx + 1), last_ms: tms, ..Turn::default() });
                    }
                    if part["type"] == "text" {
                        *last_text = cap_text(&body);
                    }
                }
            }
            "turn.ended" => {
                if let Some(open) = state.as_mut().filter(|turn| !turn.flushed) {
                    let (key, text) = close(open, last_text);
                    emit("turn_complete", key, open.last_ms.max(tms).to_string(), "turn-ended", text);
                    open.flushed = true;
                }
            }
            "turn.cancel" => {
                if let Some(open) = state.as_mut() {
                    if !open.flushed {
                        let (key, text) = close(open, last_text);
                        emit("turn_complete", key, open.last_ms.max(tms).to_string(), "cancel", text);
                        open.flushed = true;
                    }
                    open.cancelled = true;
                    last_text.clear();
                }
            }
            _ => {}
        }
    }

    fn settle(last_text: &mut String, state: &mut Option<Turn>, ident: &Ident, out: &mut Vec<Value>) {
        if let Some(open) = state.as_mut().filter(|turn| !turn.flushed && ident.now_ms - turn.last_ms >= TURN_IDLE_MS) {
            let key = format!("turn:{}", open.idx);
            out.push(activity_event(ident, &open.last_ms.to_string(), "turn_complete", key, "idle", std::mem::take(last_text)));
            open.flushed = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use serde_json::{json, Value};

    // The record shapes are the Python reader's own tests: `backend/tests/vendors/test_kimi.py:23-36` (`_usage`), `:213-216` (`_prompt`),
    // `:319-325` (`_text_part`), `:270-280` (the cancel record).
    const LOG: &str = "sessions/wd/s1/wire.jsonl";

    fn usage(input: i64, cache_read: i64, output: i64, time: i64) -> Value {
        json!({"type": "usage.record", "model": "kimi-code", "usage": {"inputOther": input, "output": output, "inputCacheRead": cache_read, "inputCacheCreation": 2}, "time": time})
    }

    fn prompt(text: &str, time: i64) -> Value {
        json!({"type": "turn.prompt", "input": [{"type": "text", "text": text}], "time": time})
    }

    fn part(kind: &str, text: &str, time: i64) -> Value {
        json!({"type": "context.append_loop_event", "event": {"type": "content.part", "part": {"type": kind, "text": text}}, "time": time})
    }

    fn mark(kind: &str, time: i64) -> Value {
        json!({"type": kind, "time": time})
    }

    /// The scenario: what Python supplies (the working directory, the session, the wall clock in milliseconds).
    fn scenario() -> Log<'static> {
        let mut log = Log::new("kimi", LOG, json!({"cwd": "/w", "session": "s1", "now_ms": 1500}));
        log.expects("", &["cwd"]).legacy_lacks_activity_flags().core_adds_to_events(&[("cli_version", json!(""))]).legacy_kept_no_key_window();
        log.append(&[prompt("rename this pane", 1000), usage(10, 5, 3, 1100), part("think", "hmm", 1150), part("text", "first", 1200)]).passes("first");
        // Records that count nothing and records of no kind; the turn ends with its own record, then the next one starts.
        log.append(&[usage(0, 0, 0, 1300), json!({"type": "unknown"}), usage(7, 0, 2, 1400), part("text", "  ", 1450), mark("turn.ended", 2000), prompt("again", 3000)])
            .append_text("this line is not JSON\n\n")
            .passes("again")
            .passes("nothing new");
        // A turn nobody ends closes after eight seconds of silence on the wall clock, and only once.
        log.supply("now_ms", json!(20_000)).passes("idle").passes("idle once");
        // A reply with no prompt opens a turn; a cancel closes it, and what follows a cancel is ignored until the next prompt.
        log.append(&[part("text", "reply", 21_000), mark("turn.cancel", 21_100), part("text", "ignored", 21_200), usage(4, 0, 1, 21_300), prompt("after", 22_000)])
            .supply("now_ms", json!(22_500))
            .passes("cancel")
            .append(&[mark("turn.cancel", 22_100)]);
        log.passes("cancel again");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"type":"turn.prompt","input":[{"type":"text","text":"plain"}],"time":23"#).passes("half");
        log.append_text("000}\n").passes("whole").replaced("replaced");
        // A record that is no object fails the passes that read the whole log.
        log.append_text("[1, 2]\n").passes("not an object");
        log
    }

    /// The differences that are Kimi's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`, and what the scenario names: the
    /// `cli_version` the core's usage events carry, the window of keys its cursors keep, the activity flags): the turn state is the answer's
    /// `state` (an object, in Python's member names), where the core spells it as the adapter's typed state and as a legacy key of the bag.
    fn own(mode: &str, object: &mut serde_json::Map<String, Value>, core: &Value) {
        if mode == "activity" {
            object.insert("state".into(), core["state"]["adapter"].clone());
            // The baseline answered a prompt's raw `input` (Python joins its text blocks); the core joins them, here a single text block.
            for event in object["events"].as_array_mut().unwrap().iter_mut().filter(|event| event["detail"] == "prompt") {
                event["input"] = json!([{"type": "text", "text": event["text"]}]);
                event["text"] = json!("");
            }
            object["seen"].as_array_mut().unwrap().retain(|key| !key.as_str().unwrap().starts_with("kimi_turn::"));
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        parity::check_log_golden("kimi", &scenario(), &[], |_, mode, object, core| own(mode, object, core));
    }
}
