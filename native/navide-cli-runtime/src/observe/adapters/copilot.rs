//! Copilot's adapter: what a usage event, a turn and an `events.jsonl` record mean, and nothing else.
//!
//! Two layouts, told apart by file name: the central `session-store.db` (1.0.78+; the row cursor over `assistant_usage_events`
//! with each row's working directory from `sessions`, and two mark walks, one over the usage events and one over `turns`) and
//! the per-session `events.jsonl` (1.0.75; cumulative usage, the activity high-water mark and the open turn's reply text), which
//! yields to the store once the store holds its session. The declaration carries the tables and columns, the key formats, the
//! `tokenDetails` pointers and the ownership rule; the core owns the cursor, the walks, the cumulative-to-delta arithmetic, the
//! high-water mark and the lookups. What stays here, and why it is not data:
//! - `mark_event`: a usage event is one `assistant` activity and a turn is a typed prompt plus a turn end under a second key;
//! - `reading`: `modelMetrics` is a map of per-model counters, and a sum over the values of a map cannot be a pointer; the log
//!   also names the CLI version and model in records that carry no totals;
//! - `identify` and `persist`: a later pass carries the working directory, model and version the earlier ones learned;
//! - `activity`: what a record type means.
use crate::observe::adapters::{CopilotAssistantUsageEventsColumn, CopilotMark, CopilotTurnsColumn, CopilotWalk, UsageFields};
use crate::observe::rows::text_or_empty;
use crate::observe::adapters::Mode;
use crate::observe::{activity_event, mark_activity, CellOf, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Pass, Reading, Run, SqlObserver, Totals};
use serde_json::{json, Map, Value};
use std::io;

pub struct Copilot;

impl Observer for Copilot {
    /// Nothing beyond the core's: the open turn's reply text is the core's `last_text`.
    type State = ();
}

impl SqlObserver for Copilot {
    type Walk = CopilotWalk;

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: CopilotMark<'_>, ident: &Ident, _pass: &Pass, _siblings: F) -> io::Result<Vec<Value>> {
        match mark {
            // A usage row means the assistant worked: one activity, no text.
            CopilotMark::AssistantUsageEvents(mark) => Ok(vec![mark_activity(ident, &mark, &text_or_empty(mark.cell(CopilotAssistantUsageEventsColumn::CreatedAt)), "agent_active", "assistant", String::new())]),
            // A turn row holds the prompt and the reply together: the prompt is activity, the reply ends the turn under a key of
            // its own.
            CopilotMark::Turns(mark) => {
                let stamp = text_or_empty(mark.cell(CopilotTurnsColumn::Timestamp));
                let mut turn_end = mark_activity(ident, &mark, &stamp, "turn_complete", "turn_row", text_or_empty(mark.cell(CopilotTurnsColumn::AssistantResponse)));
                turn_end["dedup_key"] = json!(format!("db_turn:{}", mark.id));
                Ok(vec![mark_activity(ident, &mark, &stamp, "agent_active", "user", text_or_empty(mark.cell(CopilotTurnsColumn::UserMessage))), turn_end])
            }
        }
    }
}

/// `modelMetrics`' running totals: the sum of every model's counters, where at least one model reports `usage`.
fn metrics_totals(fields: &UsageFields, data: &Value) -> Option<Totals> {
    let metrics = data["modelMetrics"].as_object().filter(|metrics| !metrics.is_empty())?;
    let mut totals = Totals::default();
    let mut usable = false;
    for per_model in metrics.values().filter(|per_model| per_model["usage"].is_object()) {
        usable = true;
        totals.input = crate::observe::sum(&[totals.input, fields.count(&per_model["usage"]["inputTokens"])]);
        totals.output = crate::observe::sum(&[totals.output, fields.count(&per_model["usage"]["outputTokens"])]);
    }
    usable.then_some(totals)
}

impl JsonlObserver for Copilot {
    fn identify(_: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident> {
        let mut ident = Ident::requested(ctx);
        // An incremental pass of the same file carries what the earlier ones learned; a replay starts from the request alone.
        if run.mode == Mode::Usage && !run.replaced {
            let carried = ctx.req.cursor.text("cwd");
            if !carried.is_empty() {
                ident.cwd = carried;
            }
            ident.model = ctx.req.cursor.text("model");
            ident.version = ctx.req.cursor.text("cli_version");
        }
        Ok(ident)
    }

    fn reading(fields: &UsageFields, ident: &mut Ident, record: &Value) -> Option<Reading> {
        let data = &record["data"];
        if !data.is_object() {
            return None;
        }
        let version = text_or_empty(&data["copilotVersion"]);
        if !version.is_empty() {
            ident.version = version;
        }
        // The map-sum first; a record without usable metrics falls back to the declared `tokenDetails` pointers.
        let totals = metrics_totals(fields, data).or_else(|| fields.reading(record).map(|reading| reading.totals))?;
        let model = text_or_empty(&data["currentModel"]);
        if !model.is_empty() {
            ident.model = model;
        }
        Some(Reading { totals, timestamp: text_or_empty(&record["timestamp"]) })
    }

    fn activity(last_text: &mut String, _: &mut (), ident: &Ident, record: &Value, line: u64, out: &mut Vec<Value>) {
        let kind = text_or_empty(&record["type"]);
        let data = &record["data"];
        let stamp = text_or_empty(&record["timestamp"]);
        let mut emit = |event_type: &str, key: String, detail: &str, body: String| out.push(activity_event(ident, &stamp, event_type, key, detail, body));
        match kind.as_str() {
            "user.message" => emit("agent_active", format!("act:{line}"), "user", text_or_empty(&data["content"])),
            "assistant.message" => {
                let body = text_or_empty(&data["content"]);
                if !body.is_empty() {
                    *last_text = body;
                }
                emit("agent_active", format!("act:{line}"), "assistant", String::new());
            }
            "assistant.turn_end" => {
                emit("turn_complete", format!("turn:{line}"), "turn_end", last_text.clone());
                // The turn consumed the text, so the next turn's empty boundary cannot reuse it.
                last_text.clear();
            }
            tool if tool.starts_with("tool.") => emit("agent_active", format!("act:{line}"), tool, String::new()),
            _ => {}
        }
    }

    fn persist(ident: &Ident, checkpoint: &mut Map<String, Value>) {
        for (key, value) in [("cwd", &ident.cwd), ("model", &ident.model), ("cli_version", &ident.version)] {
            checkpoint.insert(key.into(), json!(value));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe::parity::{self, Chain, Log};
    use crate::observe::sqlite::testing::Fixture;

    // The store schema is backend/tests/vendors/test_copilot.py:142-160 (`_STORE_SCHEMA`, the live column order of schema_version
    // 6); the row shapes are `_add_session`, `_add_turn` and `_add_usage` there (test_copilot.py:182-215). The events.jsonl
    // record shapes are the ones backend/agent_team_backend/cli_vendors/copilot.py:320-352 (`_metrics_totals`) and :902-1008
    // (`parse_activity`) read.
    const SCHEMA: [&str; 3] = [
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, repository TEXT, host_type TEXT, branch TEXT, summary TEXT, created_at TEXT, updated_at TEXT)",
        "CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, turn_index INTEGER NOT NULL, user_message TEXT, assistant_response TEXT, timestamp TEXT, UNIQUE(session_id, turn_index))",
        "CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, turn_index INTEGER, agent_id TEXT, parent_tool_call_id TEXT, model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, total_nano_aiu INTEGER, request_multiplier REAL, duration_ms INTEGER, time_to_first_token_ms INTEGER, inter_token_latency_ms INTEGER, initiator TEXT, api_endpoint TEXT, reasoning_effort TEXT, finish_reason TEXT, content_filter_triggered INTEGER, token_details_json TEXT, created_at TEXT)",
    ];

    fn root() -> Fixture {
        let root = Fixture::bare("session-store.db");
        for statement in SCHEMA {
            root.sql(statement, &[]);
        }
        root
    }

    fn session(store: &Fixture, id: &str, cwd: &str) {
        store.sql("INSERT INTO sessions (id, cwd, created_at) VALUES (?, ?, '2026-10-20T16:26:00.000Z')", &[json!(id), json!(cwd)]);
    }

    /// A SQL literal: the store's binder takes text and integers, and a NULL cell has to be written out.
    fn literal(value: &Value) -> String {
        match value {
            Value::Null => "NULL".to_owned(),
            Value::String(text) => format!("'{}'", text.replace('\'', "''")),
            other => other.to_string(),
        }
    }

    fn turn(store: &Fixture, sid: &str, index: i64, user: Value, assistant: Value, ts: &str) {
        store.sql(
            &format!(
                "INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp) VALUES ('{sid}', {index}, {}, {}, '{ts}')",
                literal(&user),
                literal(&assistant)
            ),
            &[],
        );
    }

    fn usage(store: &Fixture, sid: &str, model: &str, input: Value, output: Value, ts: Value) {
        store.sql(
            &format!(
                "INSERT INTO assistant_usage_events (session_id, model, input_tokens, output_tokens, created_at) VALUES ('{sid}', '{model}', {}, {}, {})",
                literal(&input),
                literal(&output),
                literal(&ts)
            ),
            &[],
        );
    }

    fn record(kind: &str, ts: &str, data: Value) -> String {
        format!("{}\n", json!({"type": kind, "timestamp": ts, "data": data}))
    }

    fn metrics(input: u64, output: u64) -> Value {
        json!({"modelMetrics": {"gpt": {"usage": {"inputTokens": input, "outputTokens": output}}, "claude": {"usage": {"inputTokens": 1, "outputTokens": 2}}}})
    }

    const WORK: &str = "/work/p";

    /// The store layout through every mode, over a store with sessions, a zero-token row, a row whose session is gone, NULL
    /// cells, a store that is replaced, re-anchored and shrunk, and a store that fails a query.
    fn store_scenario(chain: &mut Chain, root: &Fixture) {
        let db = root.path.clone();
        let extra = || json!({"cwd": WORK, "session": "s1"});
        chain.pass("usage on an empty store", &db, "usage", extra());
        session(root, "s1", "/work/a");
        session(root, "s2", "/work/b");
        usage(root, "s1", "claude-haiku-4.5", json!(100), json!(20), json!("2026-10-20T16:26:41.000Z"));
        usage(root, "s2", "gpt-5", json!(0), json!(0), json!("2026-10-20T16:26:42.000Z"));
        usage(root, "ghost", "gpt-5", json!(7), json!(3), json!("2026-10-20T16:26:43.000Z"));
        usage(root, "s1", "claude-haiku-4.5", json!(null), json!(9), json!(null));
        usage(root, "s2", "gpt-5", json!("12"), json!("4"), json!("2026-10-20T16:26:45.000Z"));
        turn(root, "s1", 0, json!("hello"), json!("hi there"), "2026-10-20T16:26:46.000Z");
        turn(root, "s2", 0, json!(null), json!(null), "2026-10-20T16:26:47.000Z");
        chain.pass("first usage over history", &db, "usage", extra());
        chain.pass("usage with nothing new", &db, "usage", extra());
        usage(root, "s2", "gpt-5", json!(8), json!(2), json!("2026-10-20T16:26:48.000Z"));
        chain.pass("one new usage row", &db, "usage", extra());
        chain.pass("whole-store replay with an empty bag", &db, "session_usage", extra());
        chain.pass("whole-store replay again", &db, "session_usage", extra());
        usage(root, "s1", "claude-haiku-4.5", json!(2), json!(2), json!("2026-10-20T16:26:49.000Z"));
        chain.pass("replay after one new row", &db, "session_usage", extra());
        chain.pass("first activity over history", &db, "activity", extra());
        chain.pass("activity with nothing new", &db, "activity", extra());
        turn(root, "s1", 1, json!("again"), json!("ok"), "2026-10-20T16:26:50.000Z");
        usage(root, "s1", "claude-haiku-4.5", json!(3), json!(1), json!("2026-10-20T16:26:51.000Z"));
        chain.pass("activity after a turn and a usage row", &db, "activity", extra());
        // The store file is another file: the cursor's identity no longer matches.
        chain.edit_checkpoint(&db, "usage", |checkpoint| checkpoint["identity"] = json!("1:1"));
        chain.pass("usage after the store was replaced", &db, "usage", extra());
        // Same file, but the row the cursor stood on is another row.
        chain.edit_checkpoint(&db, "usage", |checkpoint| checkpoint["anchor"] = json!("other|row|1|2|t"));
        chain.pass("usage whose anchor row changed", &db, "usage", extra());
        root.sql("DELETE FROM assistant_usage_events", &[]);
        root.sql("DELETE FROM turns", &[]);
        chain.pass("usage after the table was emptied", &db, "usage", extra());
        chain.pass("activity after the tables were emptied", &db, "activity", extra());
        usage(root, "s1", "claude-haiku-4.5", json!(4), json!(4), json!("2026-10-20T16:27:00.000Z"));
        turn(root, "s1", 5, json!("fresh"), json!("start"), "2026-10-20T16:27:01.000Z");
        chain.pass("usage after the table restarted", &db, "usage", extra());
        chain.pass("activity after the tables restarted", &db, "activity", extra());
        root.sql("DROP TABLE turns", &[]);
        chain.pass("activity while a table is missing", &db, "activity", extra());
        root.sql("DROP TABLE assistant_usage_events", &[]);
        chain.pass("usage while the usage table is missing", &db, "usage", extra());
        chain.pass("replay while the usage table is missing", &db, "session_usage", extra());
    }

    /// The per-session log through every mode: totals from `modelMetrics` and from `tokenDetails`, versions and models named in
    /// records that carry no totals, a total that falls, bad lines, an unterminated last line.
    fn log_scenario(log: &mut Log) {
        let rec = |kind: &str, ts: &str, data: Value| record(kind, ts, data);
        log.pass("usage on a log that is not there yet", "usage").pass("replay on a log that is not there yet", "session_usage").pass("activity on a log that is not there yet", "activity");
        log.append_text(&rec("session.start", "2026-10-20T16:00:00.000Z", json!({"copilotVersion": "1.0.75"})));
        log.append_text("not json\n[1, 2]\n");
        log.append_text(&rec("session.shutdown", "2026-10-20T16:00:01.000Z", json!({"currentModel": "gpt-5", "modelMetrics": metrics(100, 20)["modelMetrics"]})));
        log.append_text(&rec("assistant.usage", "2026-10-20T16:00:02.000Z", json!("not an object")));
        log.append_text(&rec("user.message", "2026-10-20T16:00:03.000Z", json!({"content": "<pane>hello</pane>"})));
        log.append_text(&rec("assistant.message", "2026-10-20T16:00:04.000Z", json!({"content": "first answer"})));
        log.append_text(&rec("tool.execution_start", "2026-10-20T16:00:05.000Z", json!({})));
        log.append_text(&rec("assistant.message", "2026-10-20T16:00:06.000Z", json!({"content": ""})));
        log.append_text(&rec("assistant.turn_end", "2026-10-20T16:00:07.000Z", json!({})));
        log.append_text(&rec("assistant.turn_end", "2026-10-20T16:00:08.000Z", json!({})));
        log.append_text(&rec("tool.execution_complete", "2026-10-20T16:00:09.000Z", json!({})));
        log.pass("first usage over the log", "usage").pass("usage with nothing new", "usage").pass("whole-log replay", "session_usage").pass("whole-log replay again", "session_usage");
        log.pass("first activity over the log", "activity").pass("activity with nothing new", "activity");
        log.append_text(&rec("token.details", "2026-10-20T16:01:00.000Z", json!({"copilotVersion": "1.0.76", "currentModel": "claude", "tokenDetails": {"input": {"tokenCount": 150}, "cache_read": {"tokenCount": 10}, "cache_write": {"tokenCount": "5"}, "output": {"tokenCount": 40}}})));
        log.append_text(&rec("assistant.message", "2026-10-20T16:01:01.000Z", json!({"content": "second answer"})));
        log.pass("usage after a tokenDetails record", "usage").pass("replay after a tokenDetails record", "session_usage").pass("activity after more records", "activity");
        // A total that falls: the saved total stays where it was, and no event is made.
        log.append_text(&rec("session.shutdown", "2026-10-20T16:02:00.000Z", json!({"modelMetrics": metrics(3, 3)["modelMetrics"]})));
        log.append_text(&rec("assistant.turn_end", "2026-10-20T16:02:01.000Z", json!({})));
        log.pass("usage after a total that fell", "usage").pass("usage again", "usage").pass("replay after a total that fell", "session_usage").pass("activity after a turn end", "activity");
        // An unterminated last line: still being written, so the mark stays behind it.
        log.append_text(rec("assistant.message", "2026-10-20T16:03:00.000Z", json!({"content": "half"})).trim_end());
        log.pass("activity over an unterminated last line", "activity").pass("usage over an unterminated last line", "usage");
        log.append_text("\n").pass("activity once the line is complete", "activity");
        // A file that is another file: the saved identity no longer matches.
        let path = log.path().to_owned();
        log.chain.edit_checkpoint(&path, "usage", |checkpoint| checkpoint["identity"] = json!("1:1"));
        log.pass("usage after the log was replaced", "usage");
    }

    /// The scenario's two layouts on one chain: the store through every mode, then the per-session log. The log's answers are held
    /// to what the shared shaping asks of a log: the session it names for itself (none), and the resume fields Copilot keeps.
    fn run() -> (Fixture, Log<'static>) {
        let root = root();
        let mut log = Log::beside("copilot", &root.root, "session-state/log1/events.jsonl", json!({"cwd": WORK, "session": "log1"}));
        log.expects("", &["cli_version", "cwd", "model"]);
        store_scenario(&mut log.chain, &root);
        log_scenario(&mut log);
        (root, log)
    }

    // ---- the golden: the legacy observer's complete documents, compared with the core's ----------------------------------------
    //
    // `golden/copilot.json` holds, for every pass of `scenario`, the document the legacy observer (blob and command recorded in
    // the file) answered, in full, with the run-specific paths and `dev:ino` masked. The core's answer to the same pass is
    // turned into the legacy document's shape by `legacy_shape`, and the two are compared WHOLE. Every step of `legacy_shape` is
    // one named difference between the two observers; nothing else is removed or rewritten.

    /// The core's document in the legacy document's shape: the differences the vendors share (`parity::legacy_shape`: the file's
    /// identity, the lookups Copilot has none of, the cursor's unfinished-row list, the typed state spelled as its legacy bag
    /// keys, and for the log's activity pass the line mark, the open text and the `opened`/`text_changed` flags), then the
    /// ones that are Copilot's own. A log's answer has none: `legacy_shape` shapes every log (`parity::shape_log`).
    fn own(answered: &parity::Answered, mode: &str, object: &mut serde_json::Map<String, Value>, _: &Value) {
        let store = answered.document["path"].as_str().is_some_and(|path| path.ends_with("session-store.db"));
        // Difference: a whole-store replay's events carried an empty `checkpoint`; the core's carry none.
        if store && mode == "session_usage" {
            for event in object.get_mut("events").and_then(Value::as_array_mut).into_iter().flatten() {
                event["checkpoint"] = json!({});
            }
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let (root, log) = run();
        parity::check_log_golden("copilot", &log, &[root.path.as_path()], own);
    }
}
