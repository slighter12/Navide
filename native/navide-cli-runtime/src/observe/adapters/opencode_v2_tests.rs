//! OpenCode 2 on the core: the `session_v2` / `session_message` layout of `opencode.db`, read through the same adapter.
//!
//! Every table, column and value here comes from the Python reader and its tests (`backend/tests/vendors/test_opencode_v2.py`):
//! the schema is `_V2_SCHEMA` and `_V1_LEFTOVER_SCHEMA` (lines 47-90), the rows are what its `_Db` helper writes (lines 111-194),
//! and each test names the Python test whose case it runs. The Python reader is the baseline: OpenCode 2 never had a legacy
//! Rust observer. `backend/tests/test_cli_runtime_opencode_v2_parity.py` runs the same fixtures through both readers.
use crate::observe::sqlite::testing::Fixture;
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;

const NOW_MS: i64 = 1_792_460_000_000;
const SID: &str = "ses_2a01f0c0ffee7Qx9v2";

/// `_V2_SCHEMA` (test_opencode_v2.py:47-79).
const V2_SCHEMA: [&str; 2] = [
    "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT, parent_id TEXT, slug TEXT NOT NULL, directory TEXT NOT NULL, title TEXT, version TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0, tokens_input INTEGER NOT NULL DEFAULT 0, tokens_output INTEGER NOT NULL DEFAULT 0, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL)",
    "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, seq INTEGER NOT NULL, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL)",
];

/// `_V1_LEFTOVER_SCHEMA` (test_opencode_v2.py:82-90): the 1.x tables an upgraded db still carries.
const V1_LEFTOVER: [&str; 3] = [
    "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT, directory TEXT, title TEXT, version TEXT, time_created INTEGER, time_updated INTEGER)",
    "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)",
    "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)",
];

/// A 2.x db and the writes OpenCode 2's projector makes to it (`_Db`, test_opencode_v2.py:111-194).
struct Db {
    store: Fixture,
    n: Cell<i64>,
    seq: RefCell<HashMap<String, i64>>,
}

impl Db {
    fn new(file: &str, upgraded_from_v1: bool) -> Self {
        let store = Fixture::bare(file);
        for sql in V2_SCHEMA {
            store.sql(sql, &[]);
        }
        if upgraded_from_v1 {
            for sql in V1_LEFTOVER {
                store.sql(sql, &[]);
            }
        }
        Self { store, n: Cell::new(0), seq: RefCell::new(HashMap::new()) }
    }

    fn session(&self, id: &str, directory: &str, parent: Option<&str>) {
        self.store.sql(
            "INSERT INTO session_v2 (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, 'proj1', NULLIF(?, ''), 'slug', ?, 'title', '2.0.32', ?, ?)",
            &[json!(id), json!(parent.unwrap_or("")), json!(directory), json!(NOW_MS), json!(NOW_MS)],
        );
    }

    fn message(&self, sid: &str, kind: &str, data: Value) -> String {
        self.n.set(self.n.get() + 1);
        let n = self.n.get();
        let id = format!("msg_{n:04}");
        let seq = self.seq.borrow().get(sid).map_or(0, |last| last + 1);
        self.seq.borrow_mut().insert(sid.to_owned(), seq);
        let mut merged = json!({"time": {"created": NOW_MS + n}});
        merged.as_object_mut().unwrap().extend(data.as_object().cloned().unwrap());
        self.store.sql(
            "INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)",
            &[json!(id), json!(sid), json!(kind), json!(seq), json!(NOW_MS + n), json!(NOW_MS + n), json!(merged.to_string())],
        );
        id
    }

    fn update(&self, id: &str, data: Value) {
        let stored = self.store.writer.as_ref().unwrap().query("SELECT data FROM session_message WHERE id = ?", &[json!(id)]).unwrap();
        let mut merged: Value = serde_json::from_str(stored[0][0].as_str().unwrap()).unwrap();
        merged.as_object_mut().unwrap().extend(data.as_object().cloned().unwrap());
        self.store.sql("UPDATE session_message SET data = ?, time_updated = ? WHERE id = ?", &[json!(merged.to_string()), json!(NOW_MS + 99_000), json!(id)]);
    }

    fn user(&self, sid: &str, text: &str) -> String {
        self.message(sid, "user", json!({"text": text, "files": [], "agents": [], "skills": []}))
    }

    fn assistant(&self, sid: &str, text: &str, tokens: Option<Value>, completed: bool) -> String {
        let mut data = json!({"agent": "build", "model": {"id": "claude-sonnet-5-5", "providerID": "anthropic"},
            "content": if text.is_empty() { json!([]) } else { json!([{"type": "text", "text": text}]) }});
        if let Some(tokens) = tokens {
            data["tokens"] = tokens;
        }
        let id = self.message(sid, "assistant", data);
        if completed {
            self.update(&id, json!({"time": {"created": NOW_MS, "completed": NOW_MS + 15_000}, "finish": "stop"}));
        }
        id
    }

    fn idle(&self, sid: &str) -> String {
        self.message(sid, "idle", json!({"outcome": "succeeded"}))
    }
}

fn tokens() -> Value {
    json!({"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 1000, "write": 50}})
}

fn observe(store: &Fixture, mode: &str, checkpoint: Value, state: Value, extra: Value) -> Value {
    let mut meta = json!({"vendor": "opencode", "path": store.path, "mode": mode, "checkpoint": checkpoint, "seen": [], "state": state});
    meta.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
    let facts = crate::observe::adapters::find("opencode").unwrap();
    crate::observe::adapters::dispatch(facts, &meta).unwrap()
}

fn keys(document: &Value) -> Vec<String> {
    document["events"].as_array().unwrap().iter().map(|event| event["dedup_key"].as_str().unwrap().to_owned()).collect()
}

/// `test_completed_assistant_steps_are_credited` and `test_incremental_parse_rechecks_a_streaming_step` (lines 254-284).
#[test]
fn a_completed_step_is_credited_and_a_streaming_one_waits_for_its_completion() {
    let db = Db::new("opencode.db", false);
    db.session(SID, "/ws", None);
    let streaming = db.assistant(SID, "", Some(tokens()), false);
    db.user(SID, "next");

    let first = observe(&db.store, "usage", json!({}), json!({}), json!({}));
    assert_eq!(first["events"], json!([]), "a streaming step and a user message are not usage");
    assert_eq!(first["checkpoint"]["pending"], json!([1]));
    assert_eq!(first["checkpoint"]["schema"], "v2", "a cursor of this layout is tagged");
    assert_eq!(first["layout"], 0, "the answer names the layout the store was read with");

    db.update(&streaming, json!({"time": {"created": NOW_MS, "completed": NOW_MS + 1}}));
    let second = observe(&db.store, "usage", first["checkpoint"].clone(), json!({}), json!({}));
    assert_eq!(keys(&second), [format!("msg:{streaming}")]);
    let event = &second["events"][0];
    assert_eq!((event["input_tokens"].as_u64(), event["output_tokens"].as_u64()), (Some(100 + 1000 + 50), Some(20 + 5)));
    assert_eq!((event["session_id"].as_str(), event["cwd"].as_str(), event["model"].as_str(), event["cli_version"].as_str()), (Some(SID), Some("/ws"), Some("claude-sonnet-5-5"), Some("2.0.32")));
    assert_eq!(keys(&observe(&db.store, "usage", second["checkpoint"].clone(), json!({}), json!({}))), Vec::<String>::new());
}

/// `test_a_1x_watermark_on_an_upgraded_db_does_not_recredit_history` (lines 287-304).
#[test]
fn a_1x_cursor_on_a_v2_store_starts_at_the_newest_row_and_credits_nothing_twice() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    for _ in 0..3 {
        db.assistant(SID, "", Some(tokens()), true);
    }
    let stat = crate::observe::os::stat_regular(&db.store.path).unwrap().expect("the store is a regular file");
    let old = json!({"kind": "sqlite", "row_id": 1, "pending": [], "anchor": "", "identity": stat.id.text()});

    let first = observe(&db.store, "usage", old, json!({}), json!({}));
    assert_eq!(first["events"], json!([]));
    assert_eq!((first["checkpoint"]["row_id"].as_i64(), first["checkpoint"]["schema"].as_str()), (Some(3), Some("v2")));
    let fresh = db.assistant(SID, "", Some(tokens()), true);
    let second = observe(&db.store, "usage", first["checkpoint"].clone(), json!({}), json!({}));
    assert_eq!(keys(&second), [format!("msg:{fresh}")]);
}

/// `test_an_idle_message_ends_the_turn_with_the_reply` (lines 199-218) and the first-sight rule of `parse_activity` (lines 703-719).
#[test]
fn an_idle_message_ends_the_turn_with_the_last_assistant_step_before_it() {
    let db = Db::new("opencode.db", false);
    db.session(SID, "/ws", None);
    let first = observe(&db.store, "activity", json!({}), json!({}), json!({}));
    assert_eq!(first["events"], json!([]), "first sight: history");
    assert_eq!(first["state"]["marks"]["session_message"]["last"], 0, "an empty table is a watermark too");

    db.user(SID, "run the tests");
    db.assistant(SID, "Running them.", None, true);
    db.assistant(SID, "All 12 tests pass.", Some(tokens()), true);
    db.idle(SID);
    let live = observe(&db.store, "activity", json!({}), first["state"].clone(), json!({}));
    let events = live["events"].as_array().unwrap();
    assert_eq!(keys(&live), ["message:1", "message:2", "message:3", "message:4"]);
    assert_eq!((events[0]["detail"].as_str(), events[0]["text"].as_str()), (Some("user"), Some("run the tests")));
    assert!(events[..3].iter().all(|event| event["event_type"] == "agent_active"));
    assert_eq!((events[3]["event_type"].as_str(), events[3]["text"].as_str()), (Some("turn_complete"), Some("All 12 tests pass.")));
    assert_eq!((events[3]["session_id"].as_str(), events[3]["cwd"].as_str()), (Some(SID), Some("/ws")));
    assert_eq!(observe(&db.store, "activity", json!({}), live["state"].clone(), json!({}))["events"], json!([]));
}

/// `test_a_subagent_going_idle_does_not_end_the_pane_turn` (lines 221-234).
#[test]
fn a_subagent_going_idle_does_not_end_the_pane_turn() {
    let db = Db::new("opencode.db", false);
    db.session(SID, "/ws", None);
    db.session("ses_child", "/ws", Some(SID));
    let first = observe(&db.store, "activity", json!({}), json!({}), json!({}));
    db.assistant("ses_child", "child done", None, true);
    db.idle("ses_child");
    let live = observe(&db.store, "activity", json!({}), first["state"].clone(), json!({}));
    let events = live["events"].as_array().unwrap();
    assert!(events.iter().all(|event| event["event_type"] == "agent_active"));
    assert_eq!(events.last().unwrap()["detail"], "subagent:done");
}

/// `test_activity_first_sight_of_a_v2_db_skips_history_even_with_a_1x_watermark` (lines 237-249): a 1.x `part` mark means
/// nothing in `session_message`.
#[test]
fn a_1x_activity_mark_is_not_a_mark_of_the_v2_table() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    for _ in 0..3 {
        db.user(SID, "old prompt");
        db.idle(SID);
    }
    let answer = observe(&db.store, "activity", json!({}), json!({"marks": {"part": {"last": 1, "pending": []}}}), json!({}));
    assert_eq!(answer["events"], json!([]));
    assert_eq!(answer["state"]["marks"]["session_message"]["last"], 6);
}

/// `test_marker_binding_announces_the_v2_session`, `test_a_marker_in_a_subagent_or_assistant_message_does_not_bind` and
/// `test_an_upgraded_db_is_read_through_its_v2_tables` (lines 309-361): the marker is in a `user` row's `data.text`.
#[test]
fn a_pane_marker_binds_the_top_level_session_whose_user_message_holds_it() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    db.session("ses_child", "/ws", Some(SID));
    db.user(SID, "kickoff\nsession marker: at-pane:p9\n");
    db.user("ses_child", "forwarded at-pane:p1");
    db.assistant(SID, "I saw at-pane:p2", None, true);
    let answer = observe(&db.store, "identify", json!({}), json!({}), json!({"markers": ["at-pane:p9", "at-pane:p1", "at-pane:p2"]}));
    assert_eq!(answer["markers"], json!({"at-pane:p9": [SID, "/ws"]}));
}

/// `test_has_session_reads_the_v2_table` and the either-side rule of `has_session` (lines 341-348, 388-395, 335-359).
#[test]
fn a_session_of_either_schema_is_found_in_an_upgraded_store() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    db.store.sql("INSERT INTO session VALUES ('ses_v1', 'proj1', NULL, 'slug', '/ws', 't', '1.19.3', 1, 1)", &[]);
    let found = |id: &str| observe(&db.store, "has_session", json!({}), json!({}), json!({"wanted_session": id}))["found"].clone();
    assert_eq!((found(SID), found("ses_v1"), found("ses_missing")), (json!(true), json!(true), json!(false)));
    let plain = Fixture::new("opencode.db");
    assert_eq!(observe(&plain, "has_session", json!({}), json!({}), json!({"wanted_session": "ses_own"}))["found"], true);
}

fn write_1x_turn(db: &Db, sid: &str, when_ms: i64) {
    // `_write_1x_turn` (test_opencode_v2.py:364-375): what a 1.x binary writes into the leftover tables of a shared db.
    db.store.sql("INSERT INTO session VALUES (?, 'proj1', NULL, 'slug', '/ws', 't', '1.19.3', ?, ?)", &[json!(sid), json!(when_ms), json!(when_ms)]);
    let data = json!({"role": "assistant", "modelID": "m", "time": {"created": when_ms, "completed": when_ms}, "tokens": tokens()});
    db.store.sql("INSERT INTO message VALUES ('msg_v1', ?, ?, ?, ?)", &[json!(sid), json!(when_ms), json!(when_ms), json!(data.to_string())]);
}

/// `test_a_db_a_1x_binary_still_writes_is_read_as_1x_and_disclosed` (lines 378-402): the newer write decides which side is read.
#[test]
fn a_store_whose_1x_tables_took_the_newest_write_is_read_as_1x() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    db.user(SID, "a 2.x turn");
    write_1x_turn(&db, "ses_v1", NOW_MS + 500_000);
    let replay = observe(&db.store, "legacy", json!({}), json!({}), json!({}));
    assert_eq!(replay["layout"], 1, "the 1.x tables were written last: read as 1.x");
    assert_eq!(replay["schema_problem"], json!({"kind": "mixed", "chosen": 1}), "and the 2.x side going unread is said, with the slot read");
    assert_eq!(keys(&replay), ["msg:msg_v1"]);
    assert_eq!(replay["events"][0]["session_id"], "ses_v1");

    db.assistant(SID, "", Some(tokens()), true);
    db.store.sql("UPDATE session_message SET time_updated = ?", &[json!(NOW_MS + 900_000)]);
    let later = observe(&db.store, "legacy", json!({}), json!({}), json!({}));
    assert_eq!(later["layout"], 0, "OpenCode 2 wrote again: read as 2.x");
    assert!(later.get("schema_problem").is_none(), "the first layout that applies, read: no problem");
}

/// `test_a_half_v2_db_is_read_as_1x_and_disclosed` (lines 593-605): `session_v2` without `session_message` is not OpenCode 2.
#[test]
fn a_store_with_half_of_the_v2_tables_is_read_as_1x() {
    let store = Fixture::bare("opencode.db");
    for sql in V1_LEFTOVER {
        store.sql(sql, &[]);
    }
    store.sql("CREATE TABLE session_v2 (id TEXT)", &[]);
    let half = observe(&store, "legacy", json!({}), json!({}), json!({}));
    assert_eq!(half["layout"], 1);
    assert_eq!(half["schema_problem"], json!({"kind": "partial", "held": ["session_v2"], "missing": ["session_message"]}));
    let pure = Fixture::new("opencode.db");
    let plain = observe(&pure, "legacy", json!({}), json!({}), json!({}));
    assert_eq!(plain["layout"], 1);
    assert!(plain.get("schema_problem").is_none(), "a pure 1.x store has no problem");
}

/// A store file with two candidate layouts is opened once, and one with a single layout has no `layout` answer.
#[test]
fn the_real_two_schema_declaration_opens_the_store_once_and_the_answer_names_the_layout() {
    let db = Db::new("opencode.db", true);
    db.session(SID, "/ws", None);
    db.assistant(SID, "", Some(tokens()), true);
    write_1x_turn(&db, "ses_v1", NOW_MS - 1);
    let before = crate::observe::sqlite::testing::OPENED.with(|opened| opened.get());
    let answer = observe(&db.store, "usage", json!({}), json!({}), json!({}));
    let opened = crate::observe::sqlite::testing::OPENED.with(|opened| opened.get()) - before;
    assert_eq!((opened, answer["layout"].as_i64()), (1, Some(0)), "one connection chooses among the layouts (the newest-write check included) and reads the rows");
    let kilo = Fixture::new("kilo.db");
    let facts = crate::observe::adapters::find("kilo").unwrap();
    let meta = json!({"vendor": "kilo", "path": kilo.path, "mode": "usage", "checkpoint": {}, "seen": [], "state": null});
    assert_eq!(crate::observe::adapters::dispatch(facts, &meta).unwrap()["layout"], 1, "Kilo declares the same two layouts");
}

/// The Windows spelling of a directory is a typed rule of the parent table (`_v2_directory`, opencode.py:254-260).
#[test]
fn a_windows_native_directory_form_spells_forward_slashes_as_backslashes_on_windows_only() {
    use crate::observe::adapters::DirForm;
    use crate::observe::dir_form;
    assert_eq!(dir_form(DirForm::WindowsNative, "C:/Users/me/work", true), "C:\\Users\\me\\work");
    assert_eq!(dir_form(DirForm::WindowsNative, "//server/share/work", true), "\\\\server\\share\\work");
    assert_eq!(dir_form(DirForm::WindowsNative, "/home/me/work", true), "/home/me/work", "a Unix path is left alone");
    assert_eq!(dir_form(DirForm::WindowsNative, "C:/Users/me/work", false), "C:/Users/me/work", "off Windows it is a no-op");
    assert_eq!(dir_form(DirForm::AsStored, "C:/Users/me/work", true), "C:/Users/me/work");
}
