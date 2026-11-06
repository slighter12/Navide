//! Grok's adapter: the record vocabulary of Grok's `updates.jsonl` and nothing else.
//!
//! The declaration carries the usage (a `turn_completed` record's `usage`: the input and output counts, read from the view below), the
//! modes, the compatibility rules and the Python facts; the core carries the high-water mark and the reply text of the turn left open.
//! What stays here, and why it is not data:
//! - `usage_view` and `position_key`: a record names its own session, its time is epoch milliseconds (or seconds) in one of two fields, its
//!   model is the FIRST key of `modelUsage` in the order the line spells it (so the line's bytes are read), and the dedup key is the CLI's
//!   event id, or the byte offset after the line for a record with none.
//! - `activity`: a message chunk is activity, `turn_completed` ends the turn and hands over the reply the chunks built.
//! - `TurnBreakdown`: a `turn_completed` record carries its whole turn's usage, its calls are the entries of `modelUsage` in the order the
//!   line spells them (so the line's bytes are read), and its prompt is the user chunks before it. The rows go through the core's ledger
//!   and splitter like every breakdown. Source: `cli_vendors/grok.py:395-456` (`turns_for_session`), the oracle of the matrix cell.
//!   Source: the retired `observe_grok.rs` (blob `bca9f13a906c1415682563775a4614e1b1d6ae7f`) lines 9-100 and 167-198, `cli_vendors/grok.py:101-210` and `454-520`.
use crate::observe::baseline::{cap_text, py_int, py_num, str_or_empty};
use crate::observe::time::stamp_millis;
use crate::observe::{activity_event, Ctx, Ident, JsonlFile, JsonlObserver, Observer, Run, TurnBreakdown, TurnCall, TurnRow, Usage};
use serde_json::{json, Value};
use std::borrow::Cow;
use std::collections::HashSet;
use std::io;

pub struct Grok;

impl Observer for Grok {
    type State = ();
}

/// The visible text of a message chunk: one text block, or the text blocks of a list, joined.
fn chunk_text(update: &Value) -> String {
    let blocks: Vec<&Value> = match &update["content"] {
        Value::Object(_) => vec![&update["content"]],
        Value::Array(items) => items.iter().collect(),
        _ => return String::new(),
    };
    blocks.into_iter().filter(|block| block.is_object() && block["type"] == "text").map(|block| str_or_empty(&block["text"])).collect()
}

/// The keys of the object at `path` in the order the line spells them (`serde_json::Value` sorts a map's keys, and Python keeps insertion
/// order, so "the first key" of an object is read from the raw line). Empty where the path is not an object of the line.
fn ordered_keys(raw: &[u8], path: &[&str]) -> Vec<String> {
    fn skip_ws(raw: &[u8], at: &mut usize) {
        while raw.get(*at).is_some_and(u8::is_ascii_whitespace) {
            *at += 1;
        }
    }
    fn string_at(raw: &[u8], at: &mut usize) -> Option<String> {
        if raw.get(*at) != Some(&b'"') {
            return None;
        }
        let start = *at;
        *at += 1;
        while let Some(&byte) = raw.get(*at) {
            match byte {
                b'\\' => *at += 2,
                b'"' => {
                    *at += 1;
                    return serde_json::from_slice::<String>(&raw[start..*at]).ok();
                }
                _ => *at += 1,
            }
        }
        None
    }
    fn skip_value(raw: &[u8], at: &mut usize) -> bool {
        match raw.get(*at) {
            Some(b'"') => string_at(raw, at).is_some(),
            Some(b'{') | Some(b'[') => {
                let mut depth = 0;
                while let Some(&byte) = raw.get(*at) {
                    match byte {
                        b'"' => {
                            if string_at(raw, at).is_none() {
                                return false;
                            }
                            continue;
                        }
                        b'{' | b'[' => depth += 1,
                        b'}' | b']' => depth -= 1,
                        _ => {}
                    }
                    *at += 1;
                    if depth == 0 {
                        return true;
                    }
                }
                false
            }
            Some(_) => {
                while raw.get(*at).is_some_and(|b| !matches!(b, b',' | b'}' | b']') && !b.is_ascii_whitespace()) {
                    *at += 1;
                }
                true
            }
            None => false,
        }
    }
    // One object level: `want` is the key to stop at the value of (`Some`), or None to list every key; the position is left at the value.
    fn level(raw: &[u8], at: &mut usize, want: Option<&str>) -> Option<Vec<String>> {
        skip_ws(raw, at);
        if raw.get(*at) != Some(&b'{') {
            return None;
        }
        *at += 1;
        let mut keys = Vec::new();
        loop {
            skip_ws(raw, at);
            if raw.get(*at) == Some(&b'}') || *at >= raw.len() {
                return want.is_none().then_some(keys);
            }
            let Some(key) = string_at(raw, at) else {
                return want.is_none().then_some(keys);
            };
            skip_ws(raw, at);
            if raw.get(*at) != Some(&b':') {
                return want.is_none().then_some(keys);
            }
            *at += 1;
            skip_ws(raw, at);
            if want == Some(key.as_str()) {
                return Some(keys);
            }
            if !skip_value(raw, at) {
                return want.is_none().then_some(keys);
            }
            keys.push(key);
            skip_ws(raw, at);
            if raw.get(*at) == Some(&b',') {
                *at += 1;
            }
        }
    }
    let mut at = 0;
    for wanted in path {
        if level(raw, &mut at, Some(wanted)).is_none() {
            return Vec::new();
        }
    }
    level(raw, &mut at, None).unwrap_or_default()
}

/// One decoded transcript record.
struct Event<'a> {
    kind: String,
    event_id: String,
    session: String,
    update: &'a Value,
    timestamp: String,
}

fn decode(record: &Value) -> Option<Event<'_>> {
    let params = record["params"].as_object().map(|_| &record["params"])?;
    let update = params["update"].as_object().map(|_| &params["update"])?;
    let meta = &params["_meta"];
    // `_meta.agentTimestampMs` is preferred for its resolution; the top-level `timestamp` (unix seconds) is the fallback.
    let seconds = match meta.as_object().and_then(|meta| meta.get("agentTimestampMs")) {
        Some(ms) if !ms.is_null() => py_num(ms).map_or(0.0, |ms| ms / 1000.0),
        _ => py_num(&record["timestamp"]).unwrap_or(0.0),
    };
    Some(Event {
        kind: str_or_empty(&update["sessionUpdate"]),
        event_id: if meta.is_object() { str_or_empty(&meta["eventId"]) } else { String::new() },
        session: str_or_empty(&params["sessionId"]),
        update,
        timestamp: stamp_millis(seconds),
    })
}

impl Event<'_> {
    /// The event id the CLI numbers its events with, else the session and the place in the file.
    fn key(&self, prefix: &str, place: u64) -> String {
        let ident = if self.event_id.is_empty() { format!("{}@{place}", self.session) } else { self.event_id.clone() };
        format!("{prefix}:{ident}")
    }
}

impl JsonlObserver for Grok {
    /// A record with no event id is named by the session and the byte offset after its line, in every pass.
    const PLACE_IS_OFFSET: bool = true;

    fn identify(_: &mut JsonlFile, ctx: &Ctx, _: &Run<Self>) -> io::Result<Ident> {
        Ok(Ident::requested(ctx))
    }

    fn usage_view<'a>(record: &'a Value, raw: &[u8]) -> Cow<'a, Value> {
        let Some(event) = decode(record).filter(|event| event.kind == "turn_completed") else {
            return Cow::Owned(Value::Null);
        };
        let usage = &event.update["usage"];
        let model = if usage["modelUsage"].as_object().is_some_and(|models| !models.is_empty()) {
            ordered_keys(raw, &["params", "update", "usage", "modelUsage"]).into_iter().next().unwrap_or_default()
        } else {
            String::new()
        };
        Cow::Owned(json!({"kind": "turn_completed", "usage": usage, "model": model, "timestamp": event.timestamp, "session": event.session}))
    }

    fn position_key(_: &Ident, _: bool, _: u64, end: u64, record: &Value) -> Option<String> {
        decode(record).map(|event| event.key("usage", end))
    }

    fn activity(last_text: &mut String, _: &mut (), ident: &Ident, rec: &Value, line: u64, out: &mut Vec<Value>) {
        let Some(event) = decode(rec) else {
            return;
        };
        let mut emit = |kind: &str, key: String, detail: &str, text: String| {
            let mut found = activity_event(ident, &event.timestamp, kind, key, detail, text);
            found["session_id"] = json!(event.session);
            out.push(found);
        };
        if event.kind == "agent_message_chunk" {
            let piece = chunk_text(event.update);
            if !piece.is_empty() {
                *last_text = cap_text(&format!("{last_text}{piece}"));
            }
        }
        if event.kind == "turn_completed" {
            let reason = str_or_empty(&event.update["stop_reason"]);
            let detail = if reason.is_empty() { "end_turn" } else { reason.as_str() };
            return emit("turn_complete", event.key("turn", line), detail, std::mem::take(last_text));
        }
        let user = event.kind == "user_message_chunk";
        if user || event.kind == "agent_message_chunk" {
            // The injected wrappers of a prompt are stripped on the Python side (`user_prompt_text`).
            emit("agent_active", event.key("act", line), if user { "user" } else { "assistant" }, if user { chunk_text(event.update) } else { String::new() });
        }
    }
}

/// The turns of one transcript: a `turn_completed` record closes a turn, the user chunks since the last one are its prompt.
#[derive(Default)]
pub struct Turns {
    out: Vec<TurnRow>,
    prompt: String,
    started: Option<String>,
}

/// The models of `usage.modelUsage` in the order the line spells them (`serde_json` sorts a parsed map, Python's dict keeps the order), each
/// with its counts. A key the line spells twice stands once, at its first place, with the last value, as a Python dict has it.
fn models_in_order<'a>(models: &'a serde_json::Map<String, Value>, raw: &[u8]) -> Vec<(String, &'a Value)> {
    let mut named = HashSet::new();
    let spelled: Vec<String> = ordered_keys(raw, &["params", "update", "usage", "modelUsage"]).into_iter().filter(|key| named.insert(key.clone())).collect();
    if spelled.len() == models.len() && spelled.iter().all(|key| models.contains_key(key)) {
        return spelled.into_iter().map(|key| (key.clone(), &models[&key])).collect();
    }
    models.iter().map(|(key, counts)| (key.clone(), counts)).collect()
}

impl TurnBreakdown for Grok {
    type Builder = Turns;

    fn record(turns: &mut Turns, ident: &Ident, _: u64, rec: &Value, raw: &[u8], _: Option<Usage>) {
        let Some(event) = decode(rec) else { return };
        if !ident.wanted_session.is_empty() && event.session != ident.wanted_session {
            return;
        }
        if event.kind == "user_message_chunk" {
            if turns.started.is_none() {
                turns.started = Some(event.timestamp.clone()).filter(|stamp| !stamp.is_empty());
            }
            turns.prompt = TurnRow::merge_prompt_excerpt(&turns.prompt,&chunk_text(event.update));
            return;
        }
        let usage = &event.update["usage"];
        if event.kind != "turn_completed" || !usage.is_object() {
            return;
        }
        let (prompt, started) = (std::mem::take(&mut turns.prompt), turns.started.take());
        let stamp = Some(event.timestamp.clone()).filter(|stamp| !stamp.is_empty());
        let parts: Vec<(String, &Value)> = match usage["modelUsage"].as_object().filter(|models| !models.is_empty()) {
            Some(models) => models_in_order(models, raw),
            None => vec![(String::new(), usage)],
        };
        let mut row = TurnRow::new(event.session.clone(), started);
        for (model, counts) in parts.into_iter().filter(|(_, counts)| counts.is_object()) {
            let (cache_read, cache_creation) = (py_int(&counts["cachedReadTokens"]), py_int(&counts["cacheCreationTokens"]));
            let input = py_int(&counts["inputTokens"]).saturating_sub(cache_read).saturating_sub(cache_creation).max(0);
            // The counts are never negative (`py_int`): the conversion keeps them.
            let [input, cache_read, cache_creation, output] = [input, cache_read, cache_creation, py_int(&counts["outputTokens"])].map(|count| count as u64);
            row.add_call(TurnCall { ts: stamp.clone(), model, input, cache_read, cache_creation, output, cli_version: String::new() });
        }
        if row.counts_nothing() {
            return;
        }
        row.prompt_excerpt = prompt;
        row.model_calls = Some(Some(py_int(&usage["modelCalls"]) as u64).filter(|count| *count != 0));
        turns.out.push(row);
    }

    fn finish(turns: Turns) -> Vec<Value> {
        TurnRow::numbered(turns.out)
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::adapters::Mode;
    use crate::observe::parity::{self, Answered, Log};
    use serde_json::{json, Value};
    use std::cell::Cell;

    // The record shapes are the Python reader's own tests: ACP `session/update` notifications, `backend/tests/vendors/test_grok.py:34-47`
    // (`_record`), `:50-57` (`_user`, `_agent`), `:60-66` (`_turn`); decoded by `cli_vendors/grok.py:174-198` (`_Event`) and `:101-118` (`_chunk_text`).
    const LOG: &str = "sessions/g/s1/updates.jsonl";

    fn update(session: &str, event_id: Option<&str>, millis: i64, body: Value) -> Value {
        let mut meta = json!({"agentTimestampMs": millis});
        if let Some(id) = event_id {
            meta["eventId"] = json!(id);
        }
        json!({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "_meta": meta, "update": body}})
    }

    fn chunk(kind: &str, text: &str, event_id: &str, millis: i64) -> Value {
        update("s1", Some(event_id), millis, json!({"sessionUpdate": kind, "content": {"type": "text", "text": text}}))
    }

    fn completed(session: &str, event_id: Option<&str>, millis: i64, input: i64, output: i64, reason: Value) -> Value {
        let mut body = json!({"sessionUpdate": "turn_completed", "usage": {"inputTokens": input, "outputTokens": output, "modelCalls": 2,
            "modelUsage": {"zeta-model": {"inputTokens": input, "outputTokens": output}, "alpha-model": {"inputTokens": 1, "outputTokens": 1}}}});
        if !reason.is_null() {
            body["stop_reason"] = reason;
        }
        update(session, event_id, millis, body)
    }

    fn scenario() -> Log<'static> {
        let mut log = Log::new("grok", LOG, json!({"cwd": "/w"}));
        log.expects("", &[]).baseline_cursor_replaced().beyond_golden(Mode::Turns).legacy_lacks_activity_flags();
        log.core_adds_to_events(&[("cli_version", json!(""))]).legacy_kept_no_key_window();
        log.append(&[chunk("user_message_chunk", "rename this pane", "1", 1_700_000_000_000), chunk("agent_message_chunk", "Hel", "2", 1_700_000_000_100),
            chunk("agent_message_chunk", "lo", "3", 1_700_000_000_200), completed("s1", Some("4"), 1_700_000_000_300, 10, 5, json!("end_turn"))])
            .passes("first");
        // Records that count nothing, one with no event id and no millisecond stamp (the place in the file names it, the seconds time stamps it), another
        // session in the same file, a stop reason that is empty, an unknown kind, and a line that is no JSON.
        let mut seconds = completed("s1", None, 0, 7, 2, json!(""));
        seconds["params"].as_object_mut().unwrap().remove("_meta");
        seconds["timestamp"] = json!(1_700_000_100.25);
        log.append(&[completed("s1", Some("5"), 1_700_000_000_400, 0, 0, Value::Null), chunk("user_message_chunk", "again", "6", 1_700_000_000_500),
            update("s1", Some("7"), 1_700_000_000_600, json!({"sessionUpdate": "tool_call"})), seconds,
            completed("s2", Some("8"), 1_700_000_000_700, 3, 1, json!("cancelled")), json!([1, 2])])
            .append_text("this line is not JSON\n\n")
            .passes("more")
            .passes("nothing new");
        // `json!` sorts the keys of a map, so the model order of this line is spelled by hand: `zeta-model` first, as the CLI wrote it.
        log.append_text(concat!(r#"{"params":{"sessionId":"s1","_meta":{"eventId":"11","agentTimestampMs":1700000001000},"update":{"sessionUpdate":"turn_completed","#,
            r#""usage":{"inputTokens":4,"outputTokens":2,"modelUsage":{"zeta-model":{"inputTokens":4},"alpha-model":{"inputTokens":1}}}}}}"#, "\n"))
            .passes("raw model order");
        // A reply still being built rides to the next pass; a rewritten file starts over.
        log.append(&[chunk("agent_message_chunk", "partial", "9", 1_700_000_000_800)]).passes("open reply");
        log.replaced("replaced");
        // An unterminated last line is read again once it is whole.
        log.append_text(r#"{"params":{"sessionId":"s1","_meta":{"eventId":"10","agentTimestampMs":1700000000900},"update":{"sessionUpdate":"user_message_chunk","content":[{"type":"text","text":"pla"#)
            .passes("half");
        log.append_text("in\"}]}}}\n").passes("whole");
        log
    }

    /// The differences that are Grok's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`, and what the scenario names: the
    /// `cli_version` the core's usage events carry, the window of keys its cursors keep, the activity flags): the cursor of an activity pass
    /// is the core's position and water, where the baseline kept a byte cursor in a `grok_act::` key of the bag.
    fn own(replaced: &Cell<bool>, answered: &Answered, mode: &str, object: &mut serde_json::Map<String, Value>, _: &Value) {
        // The baseline's document always named the reply text so far (the core's `last_text`) and the turn rows, whatever the pass.
        let pending = object.remove("last_text").unwrap_or(json!(""));
        object.insert("pending".into(), pending);
        object.insert("turns".into(), json!([]));
        if mode == "activity" && answered.asked["state"]["position"]["identity"] == "1:1" {
            // The log is another file than the one the cursor saw. The baseline's cursor was a bare byte offset with no file identity, so it
            // found nothing past the offset; the core knows the file is not the same one and reads it from the start.
            assert!(!object["events"].as_array().unwrap().is_empty());
            object.insert("events".into(), json!([]));
            replaced.set(true);
        }
        if mode == "activity" && replaced.get() {
            // The core keeps the reply text it held, and what it re-read ends with, across the replacement; the baseline read nothing.
            assert_eq!(object["pending"], "partial");
            object.insert("pending".into(), json!(""));
        }
        if mode == "activity" {
            assert!(object.remove("high_water").is_some());
            object["seen"].as_array_mut().unwrap().retain(|key| !key.as_str().unwrap().starts_with("grok_act::"));
        }
    }

    /// The `turns` answer over `lines` (the log as written), for the session asked (empty: any).
    fn turns_of(lines: &[String], wanted: &str) -> Value {
        use crate::observe::adapters::{dispatch, find};
        let dir = std::env::temp_dir().join(format!("grok-turns-{}-{}", std::process::id(), wanted.len()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("updates.jsonl");
        std::fs::write(&path, lines.iter().map(|line| format!("{line}\n")).collect::<String>()).unwrap();
        let meta = json!({"vendor": "grok", "path": path, "mode": "turns", "checkpoint": {}, "seen": [], "state": {}, "cwd": "/w", "wanted_session": wanted});
        let document = dispatch(find("grok").unwrap(), &meta).unwrap();
        std::fs::remove_dir_all(&dir).unwrap();
        document["turns"].clone()
    }

    /// `cli_vendors/grok.py:395-456` read by hand: the models keep the order the line spells them, cache counts are parts of the input, a
    /// prompt wrapper only stands in until a typed prompt arrives, a usage that is no object leaves the prompt for the next turn, a turn that
    /// counts nothing is dropped and spends it, and the session asked for is the only one whose records are read. The matrix cell and
    /// `backend/tests/test_cli_runtime_grok_turns.py` hold the same breakdown to the Python reader itself.
    #[test]
    fn the_turn_breakdown_is_the_python_readers_turns_over_the_session_asked() {
        let line = |session: &str, kind: &str, millis: i64, body: Value| {
            let mut update = json!({"sessionUpdate": kind});
            update.as_object_mut().unwrap().extend(body.as_object().unwrap().clone());
            json!({"params": {"sessionId": session, "_meta": {"agentTimestampMs": millis}, "update": update}}).to_string()
        };
        let chunk = |session: &str, text: &str, millis: i64| line(session, "user_message_chunk", millis, json!({"content": {"type": "text", "text": text}}));
        let zeta_first = concat!(r#"{"params":{"sessionId":"s1","_meta":{"agentTimestampMs":1700000005000},"update":{"sessionUpdate":"turn_completed","#,
            r#""usage":{"inputTokens":9,"outputTokens":3,"modelCalls":2,"modelUsage":{"zeta":{"inputTokens":6,"outputTokens":2,"cachedReadTokens":4,"cacheCreationTokens":1},"alpha":{"inputTokens":3,"outputTokens":1}}}}}}"#);
        let lines = vec![
            chunk("s1", "<injected wrapper>", 1_700_000_000_000),
            chunk("s1", " typed\u{1c}prompt\u{3000}here ", 1_700_000_001_000),
            chunk("s2", "the other session", 1_700_000_002_000),
            zeta_first.to_owned(),
            chunk("s1", "waits", 1_700_000_006_000),
            line("s1", "turn_completed", 1_700_000_007_000, json!({"usage": "not an object"})),
            line("s1", "turn_completed", 1_700_000_008_000, json!({"usage": {"inputTokens": 5, "outputTokens": 0, "modelCalls": 0}})),
        ];
        let mine = turns_of(&lines, "s1");
        let rows = mine.as_array().unwrap();
        assert_eq!(rows.len(), 2, "{mine}");
        let (first, second) = (&rows[0], &rows[1]);
        assert_eq!((first["turn_index"].clone(), first["session_id"].clone(), first["prompt_excerpt"].clone()), (json!(1), json!("s1"), json!("typed prompt here")));
        assert_eq!(first["started_at"], "2023-11-14T22:13:20.000Z", "the first chunk that has a time starts the turn: the wrapper's, before the typed prompt");
        assert_eq!(first["model_calls"], 2);
        let models: Vec<Value> = first["calls"].as_array().unwrap().iter().map(|call| call["model"].clone()).collect();
        assert_eq!(models, [json!("zeta"), json!("alpha")], "the order the line spells");
        assert_eq!((first["input"].clone(), first["cache_read"].clone(), first["cache_creation"].clone(), first["output"].clone()), (json!(4), json!(4), json!(1), json!(3)));
        assert_eq!(second["prompt_excerpt"], "waits");
        assert_eq!((second["model_calls"].clone(), second["calls"][0]["model"].clone(), second["input"].clone()), (Value::Null, json!(""), json!(5)));
        assert_eq!(turns_of(&lines, "s2").as_array().unwrap().len(), 0, "a session that never completes a turn has none");
        assert_eq!(turns_of(&lines, "").as_array().unwrap().len(), 2, "no session asked: every record is read");
    }

    #[test]
    fn keys_come_in_the_order_the_line_spells_them_and_a_missing_path_has_none() {
        let raw = br#"{"params":{"x":1,"update":{"usage":{"modelUsage":{"zeta":{"a":1},"alpha":{"b":[1,{"c":"}"}]}}}}}"#;
        assert_eq!(super::ordered_keys(raw, &["params", "update", "usage", "modelUsage"]), ["zeta", "alpha"]);
        assert!(super::ordered_keys(raw, &["params", "missing"]).is_empty());
        assert!(super::ordered_keys(b"not json", &["a"]).is_empty());
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        let replaced = Cell::new(false);
        parity::check_log_golden("grok", &scenario(), &[], |answered, mode, object, core| own(&replaced, answered, mode, object, core));
    }
}
