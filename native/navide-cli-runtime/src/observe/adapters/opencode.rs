//! OpenCode's adapter (and its fork Kilo, whose store file the declaration names): the meaning of a part, and nothing else.
//!
//! The declaration carries the table and column names, the key formats, the usage pointers (a message is usage once the
//! assistant has completed it; until then its `time.completed` is 0 and the row stays pending) and the marker (the `store`
//! block); the core builds the queries and owns the row cursor, the pending set, the activity walk and the lookups. What
//! stays here, and why it is not data:
//! - `mark_event`: a part means something by its type; the reply text of a turn is the text parts of its message, fetched
//!   when the turn's closing step arrives. OpenCode 2's `session_message` row means something by its type the same way
//!   (`message_event`), with the reply the declaration's `reply_from` finds.
//! - `choose_layout`: which schema an upgraded store is really in (the newest write decides), a rule no table name can say.
use crate::observe::adapters::{Layout, OpencodeMark, OpencodeSessionMessageColumn, OpencodeWalk};
use crate::observe::rows::{object, q, text_or_empty};
use crate::observe::sqlite::{number, text};
use crate::observe::time::iso_millis;
use crate::observe::{mark_activity, text_blocks, CellOf, Ident, Observer, Pass, Sqlite, SqlObserver, WalkMark};
use serde_json::{json, Value};
use std::io;

pub struct Opencode;

impl Observer for Opencode {
    /// Nothing beyond the core's activity marks.
    type State = ();
}

impl SqlObserver for Opencode {
    type Walk = OpencodeWalk;

    /// A store that holds both schemas is an upgraded one, whose 1.x tables stay behind and never change again, so OpenCode 2's
    /// are read, unless the 1.x tables took the newest write (a downgrade, or a 1.x binary sharing the file): reading only the
    /// 2.x side would then drop that session without a word. OpenCode 2's migration copies `time_updated` across, so the two
    /// are equal once it has run, and only a 1.x binary still writing makes the 1.x side newer. Hook: the tables a store holds
    /// cannot say which side was written last. The two tables are the ones the layouts declare (`rows.table`), never spelled here.
    fn choose_layout(applicable: &[&'static Layout], store: &Sqlite) -> usize {
        let Some(old) = applicable.iter().position(|layout| layout.applies_when.is_empty()) else { return 0 };
        let table = |layout: &Layout| layout.store.as_ref().and_then(|held| held.rows.as_ref()).map(|rows| q(rows.table));
        let (Some(old_table), Some(new_table)) = (table(applicable[old]), applicable.iter().find(|layout| !layout.applies_when.is_empty()).and_then(|layout| table(layout))) else { return 0 };
        let newest = format!("SELECT (SELECT COALESCE(MAX(time_updated), 0) FROM {old_table}), (SELECT COALESCE(MAX(time_updated), 0) FROM {new_table})");
        match store.rows(&newest, &[]) {
            Ok(Some(rows)) if rows.first().is_some_and(|row| number(&row[0]) > number(&row[1])) => old,
            _ => 0,
        }
    }

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: OpencodeMark<'_>, ident: &Ident, _pass: &Pass, mut siblings: F) -> io::Result<Vec<Value>> {
        let mark = match mark {
            OpencodeMark::SessionMessage(mark) => return message_event(ident, &mark, &mut siblings),
            OpencodeMark::Part(mark) => mark,
        };
        let (part, message) = (object(&mark.payload), object(&mark.owner_payload));
        let kind = text(&part["type"]);
        let (mut event_type, mut body) = ("agent_active", String::new());
        let detail = match kind.as_str() {
            "step-finish" => {
                let reason = text(&part["reason"]);
                if reason != "stop" {
                    format!("step-finish:{}", if reason.is_empty() { "unknown" } else { &reason })
                } else if !mark.top_level {
                    "subagent:done".to_owned()
                } else {
                    event_type = "turn_complete";
                    body = text_blocks(&siblings()?.iter().map(object).collect::<Vec<_>>());
                    "assistant".to_owned()
                }
            }
            "tool" => {
                let tool = text(&part["tool"]);
                format!("tool:{}", if tool.is_empty() { "unknown" } else { &tool })
            }
            "reasoning" => "assistant:thinking".to_owned(),
            "text" if message["role"] == "user" => {
                body = text(&part["text"]);
                "user".to_owned()
            }
            "text" => "assistant".to_owned(),
            _ => if kind.is_empty() { "part".to_owned() } else { kind },
        };
        Ok(vec![mark_activity(ident, &mark, &iso_millis(number(&mark.stamp)), event_type, &detail, body)])
    }
}

/// One OpenCode 2 `session_message` row as activity (OpenCode 2 has no `part` table): the row's type says what it is, and an
/// `idle` row ends the turn. The reply is the text blocks of the assistant row the declaration's `reply_from` finds.
fn message_event<F: FnMut() -> io::Result<Vec<Value>>>(ident: &Ident, mark: &WalkMark<'_, OpencodeSessionMessageColumn>, siblings: &mut F) -> io::Result<Vec<Value>> {
    let kind = text(mark.cell(OpencodeSessionMessageColumn::Type));
    let (mut event_type, mut body) = ("agent_active", String::new());
    let detail = match kind.as_str() {
        "idle" if !mark.top_level => "subagent:done".to_owned(),
        "idle" => {
            event_type = "turn_complete";
            let reply = siblings()?.first().map_or(json!({}), object);
            body = text_blocks(reply["content"].as_array().into_iter().flatten());
            "assistant".to_owned()
        }
        "user" => {
            body = text_or_empty(&object(&mark.payload)["text"]);
            "user".to_owned()
        }
        "" => "message".to_owned(),
        other => other.to_owned(),
    };
    Ok(vec![mark_activity(ident, mark, &iso_millis(number(&mark.stamp)), event_type, &detail, body)])
}

#[cfg(test)]
#[path = "opencode_v2_tests.rs"]
mod v2;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe::sqlite::testing::Fixture;
    use serde_json::json;

    // ---- the observation, end to end through the generated dispatch, against a real store on disk ----

    fn observe_vendor(vendor: &str, store: &Fixture, mode: &str, checkpoint: Value, state: Value, extra: Value) -> Value {
        let mut meta = json!({"vendor":vendor,"path":store.path,"mode":mode,"checkpoint":checkpoint,"seen":[],"state":state});
        meta.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
        let facts = crate::observe::adapters::find(vendor).unwrap();
        crate::observe::adapters::dispatch(facts, &meta).unwrap()
    }

    fn observe(store: &Fixture, mode: &str, checkpoint: Value, state: Value, extra: Value) -> Value {
        observe_vendor("opencode", store, mode, checkpoint, state, extra)
    }

    #[test]
    fn token_sums_saturate_instead_of_overflowing() {
        let store = Fixture::new("opencode.db");
        store.message("m", json!({"role":"assistant","time":{"completed":1},"tokens":{"input":9223372036854775807i64,
            "cache":{"read":9223372036854775807i64,"write":9223372036854775807i64},"output":1}}));
        let result = observe(&store, "usage", json!({}), json!({}), json!({}));
        assert_eq!(result["events"][0]["input_tokens"], json!(u64::MAX));
    }

    #[test]
    fn an_unfinished_assistant_message_stays_pending_and_a_user_message_is_not_usage() {
        let store = Fixture::new("opencode.db");
        store.message("unfinished", json!({"role":"assistant"}));
        store.message("user", json!({"role":"user","tokens":{"input":5}}));
        let result = observe(&store, "usage", json!({}), json!({}), json!({}));
        assert_eq!(result["events"], json!([]));
        assert_eq!(result["checkpoint"]["pending"], json!([1]));
    }

    #[test]
    fn a_wal_store_is_read_while_held_open_and_after_its_last_writer_closed() {
        let mut store = Fixture::new("opencode.db");
        store.sql("PRAGMA journal_mode=WAL", &[]);
        store.message("m1", json!({"role":"assistant","time":{"completed":1},"tokens":{"input":3,"output":2}}));
        store.part("p1", "m1", json!({"type":"text","text":"one"}));
        // Held open: the live -wal/-shm are read through the ordinary read-only open.
        assert_eq!(observe(&store, "usage", json!({}), json!({}), json!({}))["events"].as_array().unwrap().len(), 1);
        let first = observe(&store, "activity", json!({}), json!({}), json!({}));
        assert_eq!(first["state"]["marks"]["part"]["last"], 1);
        assert_eq!(first["seen"], json!([]), "the marker is typed state, not a dedup key");
        store.part("p2", "m1", json!({"type":"text","text":"two"}));
        // The last writer quits: the system SQLite this test writes with keeps -wal/-shm, so they go here, after the
        // checkpoint, as a quitting OpenCode's SQLite would remove them.
        drop(store.writer.take());
        for suffix in ["-wal", "-shm"] {
            let _ = std::fs::remove_file(store.root.join(format!("opencode.db{suffix}")));
        }
        assert_eq!(store.files(), ["opencode.db"]);
        let usage = observe(&store, "usage", json!({}), json!({}), json!({}));
        assert_eq!(usage["events"][0]["dedup_key"], "msg:m1");
        let activity = observe(&store, "activity", json!({}), first["state"].clone(), json!({}));
        assert_eq!(activity["events"][0]["dedup_key"], "part:2");
        assert_eq!(activity["state"]["marks"]["part"]["last"], 2);
        assert_eq!(store.files(), ["opencode.db"], "reading created or left nothing beside the store");
    }

    #[test]
    fn the_marker_uses_creation_time_not_rowid_and_excludes_children() {
        let store = Fixture::new("opencode.db");
        store.message("late", json!({"role":"user"}));
        store.message("early", json!({"role":"user"}));
        store.sql("INSERT INTO session VALUES('ses_earliest','/own/earliest',NULL,'v1')", &[]);
        store.sql("UPDATE message SET session_id='ses_earliest' WHERE id='early'", &[]);
        let marker = json!({"type":"text","text":"[at-pane:OWN]"}).to_string();
        store.sql("INSERT INTO part VALUES('late','late','ses_own',?,200)", &[json!(marker)]);
        store.sql("INSERT INTO part VALUES('early','early','ses_earliest',?,100)", &[json!(marker)]);
        let result = observe(&store, "identify", json!({}), json!({}), json!({"markers":["at-pane:OWN"]}));
        assert_eq!(result["markers"]["at-pane:OWN"], json!(["ses_earliest", "/own/earliest"]));
        store.sql("INSERT INTO session VALUES('ses_child','/child',?,'v1')", &[json!("ses_earliest")]);
        store.sql("UPDATE part SET session_id='ses_child' WHERE id='early'", &[]);
        let child = observe(&store, "identify", json!({}), json!({}), json!({"markers":["at-pane:OWN"]}));
        assert_eq!(child["markers"]["at-pane:OWN"][0], "ses_own", "a child session never holds the marker");
    }

    #[test]
    fn a_session_lookup_is_answered_from_the_declared_parent_table() {
        let store = Fixture::new("opencode.db");
        assert_eq!(observe(&store, "has_session", json!({}), json!({}), json!({"wanted_session":"ses_own"}))["found"], true);
        assert_eq!(observe(&store, "has_session", json!({}), json!({}), json!({"wanted_session":"nope"}))["found"], false);
    }

    #[test]
    fn a_pending_message_stays_pending_through_an_uncommitted_write_and_the_cursor_survives_a_restart() {
        let store = Fixture::new("opencode.db");
        store.sql("PRAGMA journal_mode=WAL", &[]);
        store.sql("PRAGMA wal_autocheckpoint=0", &[]);
        let completed = json!({"role":"assistant","time":{"completed":1700000000000_i64},"tokens":{"input":"10","cache":{"read":2,"write":3},"output":5,"reasoning":1}});
        store.message("pending", json!({"role":"assistant","tokens":{"input":99}}));
        store.message("done", completed.clone());
        let first = observe(&store, "usage", json!({}), json!({}), json!({}));
        assert_eq!(first["events"].as_array().unwrap().len(), 1);
        assert_eq!(first["events"][0]["input_tokens"], 15);
        assert_eq!(first["events"][0]["output_tokens"], 6);
        assert_eq!(first["events"][0]["timestamp"], "1700000000000");
        assert_eq!(first["checkpoint"]["pending"], json!([1]));
        store.sql("BEGIN IMMEDIATE", &[]);
        store.sql("UPDATE message SET data=? WHERE id='pending'", &[json!(completed.to_string())]);
        assert_eq!(observe(&store, "usage", first["checkpoint"].clone(), json!({}), json!({}))["events"], json!([]));
        store.sql("COMMIT", &[]);
        let second = observe(&store, "usage", first["checkpoint"].clone(), json!({}), json!({}));
        assert_eq!(second["events"][0]["dedup_key"], "msg:pending");
        assert_eq!(second["checkpoint"]["pending"], json!([]));
        assert_eq!(observe(&store, "usage", second["checkpoint"].clone(), json!({}), json!({}))["events"], json!([]));
        store.sql("DELETE FROM message WHERE id='done'", &[]);
        store.message("reused", completed);
        let reused = observe(&store, "usage", second["checkpoint"].clone(), json!({}), json!({}));
        assert_eq!(reused["events"][0]["dedup_key"], "msg:reused");
        assert!(store.path.with_extension("db-wal").is_file());
    }

    #[test]
    fn activity_replays_no_history_and_then_settles_live_top_level_turns() {
        let store = Fixture::new("opencode.db");
        store.message("user", json!({"role":"user"}));
        store.part("history", "user", json!({"type":"text","text":"OWN history"}));
        let first = observe(&store, "activity", json!({}), json!({}), json!({}));
        assert_eq!(first["events"], json!([]));
        assert_eq!(first["state"]["marks"]["part"]["last"], 1);
        store.message("assistant", json!({"role":"assistant"}));
        store.part("text", "assistant", json!({"type":"text","text":"OWN reply"}));
        store.part("toolpause", "assistant", json!({"type":"step-finish","reason":"tool-calls"}));
        store.part("stop", "assistant", json!({"type":"step-finish","reason":"stop"}));
        let live = observe(&store, "activity", json!({}), first["state"].clone(), json!({}));
        assert_eq!(live["events"][1]["event_type"], "agent_active");
        assert_eq!(live["events"][2]["event_type"], "turn_complete");
        assert_eq!(live["events"][2]["text"], "OWN reply");
        assert_eq!(live["events"][2]["timestamp"], "2023-11-14T22:13:20.123000+00:00");
        assert_eq!(observe(&store, "activity", json!({}), live["state"].clone(), json!({}))["events"], json!([]));
    }

    /// H7: a state that does not parse refuses the pass; it is never a silent reset to a first pass.
    #[test]
    fn a_state_that_does_not_parse_refuses_the_pass_instead_of_resetting() {
        let store = Fixture::new("opencode.db");
        let meta = json!({"vendor":"opencode","path":store.path,"mode":"activity","checkpoint":{},"seen":[],"state":{"water":"not a number"}});
        let facts = crate::observe::adapters::find("opencode").unwrap();
        let refused = crate::observe::adapters::dispatch(facts, &meta).unwrap_err();
        assert!(refused.to_string().contains("state"), "{refused}");
    }

    #[test]
    fn a_locked_store_answers_with_no_data_and_keeps_the_checkpoint() {
        let store = Fixture::new("opencode.db");
        store.sql("BEGIN EXCLUSIVE", &[]);
        let result = observe(&store, "usage", json!({"row_id":7}), json!({}), json!({}));
        store.sql("ROLLBACK", &[]);
        assert_eq!(result["checkpoint"], json!({"row_id":7}));
    }

    #[test]
    fn kilo_reads_its_own_store_file_and_declines_another() {
        let store = Fixture::new("kilo.db");
        store.message("m1", json!({"role":"assistant","time":{"completed":1},"tokens":{"input":3,"output":2}}));
        let result = observe_vendor("kilo", &store, "usage", json!({}), json!({}), json!({}));
        assert_eq!(result["parser"], "rust-kilo-sqlite-v1");
        assert_eq!(result["events"][0]["vendor"], "kilo");
        let other = json!({"vendor":"kilo","path":store.root.join("opencode.db"),"mode":"usage","checkpoint":{},"seen":[]});
        let facts = crate::observe::adapters::find("kilo").unwrap();
        assert_eq!(crate::observe::adapters::dispatch(facts, &other).unwrap()["events"], json!([]));
    }

    #[test]
    fn a_mode_the_declaration_does_not_list_is_refused_even_when_the_store_is_absent() {
        let store = Fixture::new("opencode.db");
        let meta = json!({"vendor":"opencode","path":store.path,"mode":"turns","checkpoint":{},"seen":[]});
        let facts = crate::observe::adapters::find("opencode").unwrap();
        let refused = crate::observe::adapters::dispatch(facts, &meta).unwrap_err();
        assert_eq!(refused.to_string(), "OpenCode observation mode not integrated");
    }
}
