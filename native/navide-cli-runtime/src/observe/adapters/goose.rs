//! Goose's adapter: what a message row means, and nothing else.
//!
//! The declaration carries the ledger and session tables, the usage columns (input excludes the cache, which the event
//! reports beside it), the message walk and the marker (a user row of a top-level interactive session); the core builds the
//! queries and owns the cursor, the walk and the lookups. What stays here, and why it is not data:
//! - `mark_event`: a message is a tool request, an action, a reply or context by the blocks in its content and the flags in
//!   its metadata, and a reply is a turn end only when the session is a top-level interactive one;
//! - `marker_text`: the marker is searched in the text blocks of a row, and a row the CLI injected as turn context is left
//!   out.
use crate::observe::adapters::{GooseMark, GooseMessagesColumn, GooseWalk};
use crate::observe::rows::{object, text_or_empty, truthy};
use crate::observe::sqlite::{number, text};
use crate::observe::time::iso_seconds;
use crate::observe::{mark_activity, text_blocks, CellOf, Ident, Observer, Pass, SqlObserver};
use serde_json::Value;
use std::collections::BTreeSet;
use std::io;

pub struct Goose;

impl Observer for Goose {
    /// Nothing beyond the core's activity marks.
    type State = ();
}

/// The object entries of a JSON list cell; nothing for any other cell.
fn blocks(cell: &Value) -> Vec<Value> {
    match text(cell).as_str() {
        "" => Vec::new(),
        raw => match serde_json::from_str::<Value>(raw) {
            Ok(Value::Array(items)) => items.into_iter().filter(Value::is_object).collect(),
            _ => Vec::new(),
        },
    }
}

impl SqlObserver for Goose {
    type Walk = GooseWalk;

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: GooseMark<'_>, ident: &Ident, _pass: &Pass, _siblings: F) -> io::Result<Vec<Value>> {
        let GooseMark::Messages(mark) = mark;
        let role = text(mark.cell(GooseMessagesColumn::Role));
        let blocks = blocks(&mark.payload);
        let meta = object(mark.cell(GooseMessagesColumn::MetadataJson));
        let kinds: BTreeSet<String> = blocks.iter().map(|b| text_or_empty(&b["type"])).collect();
        let make = |event_type: &str, detail: &str, body: String| {
            mark_activity(ident, &mark, &iso_seconds(number(&mark.stamp) as f64), event_type, detail, body)
        };
        let event = match role.as_str() {
            "assistant" => {
                if let Some(tool) = blocks.iter().find(|b| b["type"] == "toolRequest") {
                    let name = text_or_empty(&tool["toolCall"]["value"]["name"]);
                    make("agent_active", &format!("tool:{}", if name.is_empty() { "unknown" } else { &name }), String::new())
                } else if kinds.contains("actionRequired") {
                    make("agent_active", "assistant:action-required", String::new())
                } else {
                    let body = text_blocks(&blocks);
                    if !body.is_empty() && meta["agentVisible"] != Value::Bool(false) {
                        // A sub-agent finished; its parent is still working.
                        if !mark.top_level {
                            make("agent_active", "subagent:done", String::new())
                        } else {
                            make("turn_complete", "assistant", body)
                        }
                    } else {
                        make("agent_active", "assistant", String::new())
                    }
                }
            }
            "user" => {
                if kinds.contains("toolResponse") {
                    make("agent_active", "tool:result", String::new())
                } else if kinds.contains("actionRequired") {
                    make("agent_active", "user:action-response", String::new())
                } else if truthy(&meta["turnContext"]) || meta["userVisible"] == Value::Bool(false) {
                    // turnContext rows are the CLI's own injected context, not typed.
                    make("agent_active", "user:context", String::new())
                } else {
                    make("agent_active", "user", text_blocks(&blocks))
                }
            }
            other => make("agent_active", if other.is_empty() { "message" } else { other }, String::new()),
        };
        Ok(vec![event])
    }

    fn marker_text(mark: GooseMark<'_>) -> Option<String> {
        let GooseMark::Messages(mark) = mark;
        if truthy(&object(mark.cell(GooseMessagesColumn::MetadataJson))["turnContext"]) {
            return None;
        }
        Some(text_blocks(&blocks(&mark.payload)))
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, core, events, meta, seen, Chain};
    use crate::observe::sqlite::testing::Fixture;
    use serde_json::{json, Value};

    // The schema recorded off goose 1.55.0: tests/fixtures/cli-regression/goose.json, phases.initial[0].schema.
    const RECORDED: &str = include_str!("../../../../../tests/fixtures/cli-regression/goose.json");

    // The row shapes are backend/tests/vendors/test_goose.py:40-63 (`_session`, `_message`, `_usage`).
    fn session(store: &Fixture, id: &str, kind: &str, cwd: &str, parent: Option<&str>) {
        let parent = parent.map_or("NULL".to_owned(), |parent| format!("'{parent}'"));
        store.sql(&format!("INSERT INTO sessions (id, name, session_type, working_dir, parent_session_id) VALUES ('{id}', '', '{kind}', '{cwd}', {parent})"), &[]);
    }

    fn message(store: &Fixture, sid: &str, role: &str, content: Value, meta: Value, ts: i64) {
        store.sql(
            "INSERT INTO messages (message_id, session_id, role, content_json, created_timestamp, metadata_json) VALUES ('m', ?, ?, ?, ?, ?)",
            &[json!(sid), json!(role), json!(content.to_string()), json!(ts), json!(meta.to_string())],
        );
    }

    fn ledger(store: &Fixture, sid: &str, model: &str, input: i64, output: i64, cache: [&str; 2]) {
        store.sql(
            &format!("INSERT INTO usage_ledger (session_id, created_timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens) VALUES (?, 1792513603, {model}, ?, ?, {}, {})", cache[0], cache[1]),
            &[json!(sid), json!(input), json!(output)],
        );
    }

    fn text_block(text: &str) -> Value {
        json!([{"type": "text", "text": text}])
    }

    fn tool_block(name: &str) -> Value {
        json!([{"type": "toolRequest", "id": "c", "toolCall": {"status": "success", "value": {"name": name, "arguments": {}}}}])
    }

    fn visible() -> Value {
        json!({"userVisible": true, "agentVisible": true})
    }

    fn goose_store() -> Fixture {
        let store = parity::store("sessions.db", RECORDED);
        session(&store, "s1", "user", "/work/a", None);
        session(&store, "s2", "user", "/work/b", None);
        session(&store, "sub", "sub_agent", "/work/a", Some("s1"));
        store
    }

    fn observe(store: &Fixture, mode: &str, seen: &[String], checkpoint: &Value, state: &Value, extra: Value) -> Value {
        core("goose", &meta(&store.path, mode, seen, checkpoint, state, extra)).unwrap()
    }

    fn project(document: &Value, fields: &[&str]) -> Value {
        json!(events(document).iter().map(|event| fields.iter().map(|field| event[*field].clone()).collect::<Vec<_>>()).collect::<Vec<_>>())
    }

    const ACTIVITY: [&str; 7] = ["event_type", "detail", "session_id", "cwd", "dedup_key", "timestamp", "text"];
    const USAGE: [&str; 9] = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "model", "session_id", "cwd", "dedup_key", "timestamp"];

    // ---- the golden: the legacy observer's complete documents, compared with the core's -------------------------------------
    //
    // `golden/goose.json` holds, for every pass of `run`, the document the legacy observer (blob and command recorded in the
    // file) answered, in full, with the run-specific paths and `dev:ino` masked. The core's answer to the same pass is turned
    // into the legacy document's shape by `legacy_shape` and the two are compared WHOLE. Every step of `legacy_shape` is one
    // named difference between the observers; nothing else is removed or rewritten.

    /// The scenario every observer is run through: the messages of every kind, the ledger with its orphan, its zero rows and its
    /// NULL cells, a replay with a key already delivered, every lookup and a mode the vendor never had.
    fn run() -> (Fixture, Chain<'static>) {
        let store = goose_store();
        let path = store.path.clone();
        let mut chain = Chain::new("goose");
        message(&store, "s1", "user", text_block("history"), visible(), 1792513500);
        message(&store, "s1", "assistant", text_block("history reply"), visible(), 1792513501);
        // First sight of the store: everything in it is history.
        chain.pass("activity: first sight of a store with history", &path, "activity", json!({}));

        // One of each kind of message, across sessions: tool request (named, unnamed), action required, a reply (turn end), a
        // reply the agent hides, an empty reply, a sub-agent's reply, a tool response, an action response, injected context, an
        // invisible user row, a typed prompt with an image, a role the CLI never wrote, an empty role and a scalar metadata.
        message(&store, "s1", "assistant", tool_block("shell"), visible(), 1792513603);
        message(&store, "s1", "assistant", json!([{"type": "toolRequest", "id": "c", "toolCall": {"status": "success", "value": {"arguments": {}}}}]), visible(), 1792513604);
        message(&store, "s1", "assistant", json!([{"type": "actionRequired", "data": {}}]), json!({"userVisible": true, "agentVisible": false}), 1792513605);
        message(&store, "s1", "assistant", text_block("  the answer  "), visible(), 1792513606);
        message(&store, "s1", "assistant", text_block("hidden"), json!({"userVisible": true, "agentVisible": false}), 1792513607);
        message(&store, "s1", "assistant", json!([]), visible(), 1792513608);
        message(&store, "sub", "assistant", text_block("sub-agent done"), visible(), 1792513609);
        message(&store, "s2", "user", json!([{"type": "toolResponse", "id": "c"}]), visible(), 1792513610);
        message(&store, "s2", "user", json!([{"type": "actionRequired", "data": {}}]), json!({"userVisible": false, "agentVisible": false}), 1792513611);
        message(&store, "s2", "user", text_block("<turn-context/>"), json!({"userVisible": false, "agentVisible": true, "turnContext": true}), 1792513612);
        message(&store, "s2", "user", text_block("invisible"), json!({"userVisible": false}), 1792513613);
        message(&store, "s2", "user", json!([{"type": "text", "text": "first"}, {"type": "image"}, {"type": "text", "text": " second "}]), visible(), 1792513614);
        message(&store, "s2", "system", text_block("never written"), visible(), 1792513615);
        message(&store, "s2", "", text_block("no role"), visible(), 1792513616);
        message(&store, "s2", "user", text_block("not json meta"), json!("scalar"), 1792513617);
        chain.pass("activity: one of each kind of message", &path, "activity", json!({}));
        chain.pass("activity: nothing new", &path, "activity", json!({}));
        message(&store, "s1", "assistant", text_block("later"), visible(), 1792513700);
        chain.pass("activity: one more reply", &path, "activity", json!({}));

        // A model of text, a NULL model (the baseline COALESCE makes it ""), NULL cache cells, a zero row, and a row whose
        // session is gone (the baseline inner join drops it).
        ledger(&store, "s1", "'m-1'", 100, 20, ["5", "7"]);
        ledger(&store, "s2", "NULL", 3, 4, ["NULL", "NULL"]);
        ledger(&store, "s1", "'m-1'", 0, 0, ["1", "1"]);
        ledger(&store, "ghost", "'m-1'", 9, 9, ["0", "0"]);
        ledger(&store, "sub", "'m-2'", 0, 6, ["0", "0"]);
        chain.pass("usage: first over the ledger", &path, "usage", json!({}));
        chain.pass("usage: nothing new", &path, "usage", json!({}));
        ledger(&store, "s2", "'m-3'", 8, 9, ["2", "3"]);
        chain.pass("usage: one new row", &path, "usage", json!({}));

        // A replay with one key delivered before, then again.
        chain.seed(&path, "session_usage", &["usage:1"]);
        chain.pass("replay with one key already delivered", &path, "session_usage", json!({}));
        chain.pass("replay again", &path, "session_usage", json!({}));

        // A ledger whose last row holds no tokens.
        ledger(&store, "s1", "'m-1'", 10, 2, ["0", "0"]);
        ledger(&store, "s1", "'m-1'", 0, 0, ["0", "0"]);
        chain.pass("usage: a last row that holds no tokens", &path, "usage", json!({}));
        chain.pass("usage: nothing after the empty row", &path, "usage", json!({}));
        chain.pass("replay after the empty row", &path, "session_usage", json!({}));

        // has_session: a stored session, a sub-agent session, an absent one, an empty id and a missing id.
        for (wanted, label) in [(Some("s1"), "a stored session"), (Some("sub"), "a sub-agent session"), (Some("nope"), "an absent session"), (Some(""), "an empty id"), (None, "no id")] {
            let extra = wanted.map_or(json!({}), |wanted| json!({"wanted_session": wanted}));
            chain.pass(&format!("has_session: {label}"), &path, "has_session", extra);
        }

        // identify: markers in user rows of top-level user sessions only. Rows the marker query excludes: a sub-agent session's,
        // a session with a parent, an assistant row and a turn-context row. The first row holding the marker wins.
        session(&store, "child", "user", "/work/c", Some("s1"));
        let marked = |sid: &str, role: &str, text: &str, extra: Value| message(&store, sid, role, text_block(text), extra, 1792514000);
        marked("sub", "user", "at-pane:subagent", visible());
        marked("child", "user", "at-pane:child", visible());
        marked("s1", "assistant", "at-pane:assistant", visible());
        marked("s1", "user", "at-pane:context", json!({"turnContext": true}));
        marked("s2", "user", "prefix at-pane:first and at-pane:second", visible());
        marked("s1", "user", "at-pane:first again", visible());
        let wanted = json!(["at-pane:first", "at-pane:second", "at-pane:subagent", "at-pane:child", "at-pane:assistant", "at-pane:context", "at-pane:none", ""]);
        chain.pass("identify: markers", &path, "identify", json!({"markers": wanted}));
        chain.pass("identify: no markers", &path, "identify", json!({"markers": []}));

        // A mode the vendor never had is refused with the baseline's words.
        chain.pass("a mode the vendor never had", &path, "plan", json!({}));
        (store, chain)
    }

    /// The ledger rows the scenario writes with no tokens (input and output both 0): scenario input, the rows `run` writes with
    /// `ledger(.., 0, 0, ..)`. They are the third and the last row of the ledger.
    const NO_TOKEN_ROWS: [i64; 2] = [3, 8];

    /// The core's document in the legacy document's shape: the differences the vendors share (`parity::legacy_shape`: the file's
    /// identity, the row cursor's unfinished-row list, the typed state `goose_message::` spelled as its legacy bag key), then
    /// the ones that are Goose's own.
    fn own(answered: &parity::Answered, mode: &str, object: &mut serde_json::Map<String, Value>, _: &Value) {
        let identity = answered.document["identity"].clone();
        {
            // Difference: the core's cursor carries the anchor of the row it stands on (`<id>|<session>`, which it verifies on the
            // next pass); the legacy cursor held the file's identity instead. The same holds for the cursor each event carries.
            let spell = |checkpoint: &mut serde_json::Map<String, Value>| {
                if checkpoint.contains_key("row_id") {
                    let anchor = checkpoint.remove("anchor").unwrap();
                    assert!(anchor.as_str().is_some_and(|anchor| anchor.starts_with(&format!("{}|", checkpoint["row_id"]))), "{anchor}");
                    checkpoint.insert("identity".into(), json!(parity::identity_text(&identity)));
                }
            };
            if let Some(checkpoint) = object.get_mut("checkpoint").and_then(Value::as_object_mut) {
                spell(checkpoint);
            }
            for event in object.get_mut("events").and_then(Value::as_array_mut).into_iter().flatten() {
                if let Some(checkpoint) = event.get_mut("checkpoint").and_then(Value::as_object_mut) {
                    spell(checkpoint);
                }
                // Difference: the legacy usage event repeated its row id in a `raw` field; the core's names it once, in the key.
                if let Some(key) = event["dedup_key"].as_str().and_then(|key| key.strip_prefix("usage:")).and_then(|id| id.parse::<i64>().ok()) {
                    event["raw"] = json!({"row_id": key});
                }
            }
            // Difference: the core also records the key of a zero-token ledger row it read and skipped, so a later replay does
            // not look at it again; the baseline bag kept only delivered keys. No event differs.
            if mode == "session_usage" {
                let zero: Vec<String> = NO_TOKEN_ROWS.iter().map(|row| format!("usage:{row}")).collect();
                let kept: Vec<Value> = object["seen"].as_array().unwrap().iter().filter(|key| !zero.contains(&key.as_str().unwrap().to_owned())).cloned().collect();
                object.insert("seen".into(), json!(kept));
            }
            // Difference (t36c K7, the manager's decision): the core's cursor stands on the last row it READ, even when that row
            // held no tokens; the legacy cursor stood on the last row it DELIVERED, the one before. Nothing is lost either way:
            // the ledger is append-only, and the zero row says nothing.
            if let Some(row) = object.get_mut("checkpoint").and_then(|checkpoint| checkpoint.get_mut("row_id")) {
                let mut delivered = row.as_i64().unwrap();
                while NO_TOKEN_ROWS.contains(&delivered) {
                    delivered -= 1;
                }
                *row = json!(delivered);
            }
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let (store, chain) = run();
        parity::check_golden("goose", &chain, &[store.root.as_path()], &[store.path.as_path()], own);
    }

    /// The legacy `goose_message::17` high-water mark, migrated to typed state, continues exactly where the baseline would.
    #[test]
    fn a_legacy_message_mark_migrated_to_typed_state_continues_where_the_baseline_would() {
        let store = goose_store();
        for at in 0..17 {
            message(&store, "s1", "assistant", json!([]), visible(), 1792513500 + at);
        }
        message(&store, "s1", "assistant", text_block("later"), visible(), 1792513700);
        let migrated = observe(&store, "activity", &[], &json!({}), &json!({"marks": {"messages": {"last": 17}}}), json!({}));
        assert_eq!(project(&migrated, &ACTIVITY), json!([["turn_complete", "assistant", "s1", "/work/a", "message:18", "2026-10-20T16:28:20+00:00", "later"]]));
    }

    /// Pinned core behaviour (t36c K7, the manager's decision): a ledger whose last row holds no tokens still moves the
    /// watermark past it and puts its key in the seen bag. No event changes (the zero row says nothing either way) and the ledger
    /// is append-only, so nothing is lost. The report names both as differences from the baseline observer's bag.
    #[test]
    fn a_ledger_whose_last_row_holds_no_tokens_still_passes_the_watermark_and_the_key_enters_the_bag() {
        let store = goose_store();
        ledger(&store, "s1", "'m-1'", 10, 2, ["0", "0"]);
        ledger(&store, "s1", "'m-1'", 0, 0, ["0", "0"]);
        let first = observe(&store, "usage", &[], &json!({}), &json!(null), json!({}));
        assert_eq!(project(&first, &USAGE).as_array().unwrap().len(), 1, "only the row with tokens is an event");
        assert_eq!(first["checkpoint"]["row_id"], json!(2), "the watermark passes the zero row that came last");
        let again = observe(&store, "usage", &[], &first["checkpoint"], &json!(null), json!({}));
        assert_eq!(events(&again), Vec::<Value>::new());
        assert_eq!(again["checkpoint"]["row_id"], json!(2));
        let replay = observe(&store, "session_usage", &[], &json!({}), &json!(null), json!({}));
        assert_eq!(project(&replay, &USAGE).as_array().unwrap().len(), 1);
        assert_eq!(seen(&replay), ["usage:1", "usage:2"], "the zero row's key is in the bag with the delivered one");
    }
}
