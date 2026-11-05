//! Tests of the row-store components: the bounds, the quoting, the failure policy, the pending policy and the per-layout
//! modes. They run against OpenCode's real declaration where a real store is enough, and against a declaration written here
//! where a mechanic needs a shape OpenCode does not have (a mark walk that goes pending, two layouts in one vendor). What a
//! vendor's declaration holds is proven against that vendor's own observer, in the vendor's adapter tests.
use super::*;
use crate::observe::adapters::{
    self, CacheRule, KeyScope, Layout, MarkShrink, MarkStart, Marks, NumberRule, OpencodeWalk, PendingAge, Rows, SourceKind, Store, TextRule,
    TimeRule, TimeUnit, UsageFields, UsageMode, Walk,
};
use crate::observe::sqlite::testing::Fixture;
use crate::observe::testing::facts_over;
use crate::observe::{observe, observe_with, ByMarker, BySession, NoCapability, NoUsage, Request};
use std::path::Path;

// ---- a hook that reports what the core hands it ----------------------------------------------------------------------

struct Probe;

impl Observer for Probe {
    type State = ();
}

/// The probe borrows OpenCode's walks: its `steps` table is the `part` walk and its `notes` table the `session_message` walk.
const STEPS: Walk = Walk::Opencode(OpencodeWalk::Part);
const NOTES: Walk = Walk::Opencode(OpencodeWalk::SessionMessage);

/// The probe's one declared cell (the `status` column of its walks), spelled the way the generator spells a walk's columns.
#[derive(Clone, Copy)]
enum StatusColumn {
    Status,
}

impl ColumnId for StatusColumn {
    fn position(self) -> usize {
        self as usize
    }
}

/// The probe's own walks, written the way the generator writes a parser's: a walk per borrowed OpenCode walk, a mark typed by the
/// walk's columns, and how the core hands the one to the other.
#[derive(Clone, Copy, Debug)]
enum ProbeWalk {
    Part,
    SessionMessage,
}

enum ProbeMark<'a> {
    Part(WalkMark<'a, StatusColumn>),
    SessionMessage(WalkMark<'a, StatusColumn>),
}

impl std::ops::Deref for ProbeMark<'_> {
    type Target = Mark;

    fn deref(&self) -> &Mark {
        match self {
            ProbeMark::Part(mark) | ProbeMark::SessionMessage(mark) => mark,
        }
    }
}

impl WalkId for ProbeWalk {
    type Marked<'a> = ProbeMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Opencode(OpencodeWalk::Part) => Some(ProbeWalk::Part),
            Walk::Opencode(OpencodeWalk::SessionMessage) => Some(ProbeWalk::SessionMessage),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark(self, mark: &Mark) -> ProbeMark<'_> {
        match self {
            ProbeWalk::Part => ProbeMark::Part(WalkMark::new(mark)),
            ProbeWalk::SessionMessage => ProbeMark::SessionMessage(WalkMark::new(mark)),
        }
    }
}

impl SqlObserver for Probe {
    type Walk = ProbeWalk;

    fn mark_event<F: FnMut() -> io::Result<Vec<Value>>>(mark: ProbeMark<'_>, ident: &Ident, pass: &Pass, _: F) -> io::Result<Vec<Value>> {
        let (walk, status) = match &mark {
            ProbeMark::Part(mark) => ("Part", mark.cell(StatusColumn::Status).clone()),
            ProbeMark::SessionMessage(mark) => ("SessionMessage", mark.cell(StatusColumn::Status).clone()),
        };
        Ok(vec![json!({"walk": walk, "dedup_key": mark.dedup, "group": mark.group, "dir": mark.dir, "stamp": mark.stamp,
            "status": if status.is_string() { Value::Null } else { status.clone() }, "text": status.as_str(), "top_level": mark.top_level, "newest": pass.newest, "clock": pass.stamp(ident.base_epoch)})])
    }

    /// A step whose status is 5 is still being written; its time is its `stamp` column, in seconds.
    fn mark_progress(mark: ProbeMark<'_>) -> Progress {
        let (ProbeMark::Part(held) | ProbeMark::SessionMessage(held)) = &mark;
        Progress { pending: held.cell(StatusColumn::Status) == &json!(5), stamp: Some(number(&mark.stamp) as f64) }
    }
}

const ROW_MODES: &[Mode] = &[Mode::Usage, Mode::Legacy, Mode::SessionUsage, Mode::Activity, Mode::HasSession, Mode::Identify];

fn run<U: UsageScan<Probe, Sqlite>, X: Capability<Probe, Sqlite>>(facts: &'static adapters::ObserverFacts, path: &Path, mode: &str, state: &Value, checkpoint: &Value, extra: Value) -> io::Result<Value> {
    let mut meta = json!({"vendor": "opencode", "path": path, "mode": mode, "checkpoint": checkpoint, "seen": [], "state": state});
    meta.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
    let request = Request::parse(&meta)?;
    observe::<Probe, Sqlite, U, X>(&request, facts)
}

const NO_FIELDS: UsageFields = UsageFields {
    select: &[],
    object: None,
    input: &[],
    output: &[],
    cache_read: &[],
    cache_creation: &[],
    key: &[],
    key_join: "",
    model: None,
    version: None,
    timestamp: None,
    number: NumberRule::Strict,
    text: TextRule::String,
    cache: CacheRule::Folded,
    time: TimeRule::Text,
    document: None,
    cwd: None,
    session: None,
};

fn layout(file: Option<&'static str>, modes: &'static [Mode], usage_fields: Option<UsageFields>, store: Store) -> Layout {
    Layout {
        source: SourceKind::Sqlite,
        usage: if usage_fields.is_some() { UsageMode::PerTurn } else { UsageMode::None },
        store_file: file,
        applies_when: &[],
        modes,
        request: &[],
        usage_fields,
        store: Some(store),
        rules: None,
        defers_to: None,
    }
}

fn facts(layouts: Vec<Layout>) -> &'static adapters::ObserverFacts {
    facts_over("opencode", layouts)
}

fn ids(document: &Value) -> Vec<String> {
    document["events"].as_array().unwrap().iter().map(|event| event["dedup_key"].as_str().unwrap().to_owned()).collect()
}

fn context<'a>(facts: &'static adapters::ObserverFacts, request: &'a Request<'a>) -> Ctx<'a> {
    Ctx { req: request, facts, core: &facts.core, layout: facts.layout_for(request.path).unwrap() }
}

/// The mark walk of a `steps` table the tests share: a step is pending while its status is 5.
const fn step_marks(max_age: Option<PendingAge>, page: usize) -> Marks {
    Marks {
        table: "steps",
        walk: STEPS,
        id: "idx",
        key: None,
        group: None,
        owner: None,
        payload: Some("body"),
        stamp: Some("stamp"),
        columns: &["status"],
        prefix: "step:",
        key_scope: KeyScope::None,
        page,
        text_cap: None,
        start: MarkStart::Oldest,
        shrink: MarkShrink::Keep,
        pending_max_age: max_age,
        reply_from: None,
        after: None,
    }
}

const DEFAULT_STEPS: Marks = step_marks(None, 256);

// ---- modes belong to a layout ------------------------------------------------------------------------------------------------

/// A vendor with two store files: `ledger.db` answers usage (and the replay), `steps.db` answers activity only.
fn two_layouts() -> &'static adapters::ObserverFacts {
    let ledger = Store {
        rows: Some(Rows { table: "ledger", id: "id", key: "id", group: None, payload: None, columns: &["input"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }),
        parent: None,
        marks: &[],
        row_prefix: "l:",
        marker: None,
    };
    let steps = Store {
        rows: None,
        parent: None,
        marks: &[DEFAULT_STEPS],
        row_prefix: "",
        marker: None,
    };
    let fields = UsageFields { input: &["/input"], ..NO_FIELDS };
    facts(vec![
        layout(Some("ledger.db"), &[Mode::Usage, Mode::SessionUsage], Some(fields), ledger),
        layout(Some("steps.db"), &[Mode::Activity], None, steps),
    ])
}

/// One store file that may hold either of two schemas: `entries` (the newer one, applying when the store holds that table) and
/// `ledger` (the plain one). Each layout reads its own table and spells its keys with its own prefix, so a document says which
/// layout read the store.
fn two_schemas(declared_newer_first: bool) -> &'static adapters::ObserverFacts {
    let reads = |table: &'static str, prefix: &'static str| Store {
        rows: Some(Rows { table, id: "id", key: "id", group: None, payload: None, columns: &["input"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }),
        parent: None,
        marks: &[],
        row_prefix: prefix,
        marker: None,
    };
    let fields = UsageFields { input: &["/input"], ..NO_FIELDS };
    let plain = layout(Some("ledger.db"), &[Mode::Usage], Some(fields), reads("ledger", "old:"));
    let newer = Layout { applies_when: &["entries"], ..layout(Some("ledger.db"), &[Mode::Usage], Some(fields), reads("entries", "new:")) };
    facts(if declared_newer_first { vec![newer, plain] } else { vec![plain, newer] })
}

/// Two schemas behind one file name: the layout that names a table applies when the store holds it, whatever the order the
/// layouts are declared in, and the layout that names none applies otherwise.
#[test]
fn a_store_file_is_read_by_the_layout_whose_table_it_holds() {
    for newer_first in [true, false] {
        let facts = two_schemas(newer_first);
        let read = |path: &Path| ids(&run::<PerTurn, NoCapability>(facts, path, "usage", &json!(null), &json!({}), json!({})).unwrap());

        let old = Fixture::bare("ledger.db");
        old.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
        old.sql("INSERT INTO ledger VALUES(1,5)", &[]);
        assert_eq!(read(&old.path), ["old:1"], "a store without the table is the plain layout's");

        // An upgraded store holds both: the layout that names its table wins, declared first or last.
        let new = Fixture::bare("ledger.db");
        new.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
        new.sql("CREATE TABLE entries(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
        new.sql("INSERT INTO ledger VALUES(1,5)", &[]);
        new.sql("INSERT INTO entries VALUES(7,5)", &[]);
        assert_eq!(read(&new.path), ["new:7"], "a store with the table is the other layout's");

        // A file that is not there holds no table, so the plain layout applies and finds nothing.
        assert_eq!(read(&old.root.join("gone").join("ledger.db")), Vec::<String>::new());
    }
}

/// t36j U3: a usage event carries the id of its row as the field the declaration names (`store.rows.raw_id`), and carries none where
/// the declaration names none.
#[test]
fn a_usage_event_carries_the_id_of_its_row_as_the_declared_raw_field_and_none_where_none_is_declared() {
    let reading = |raw_id: Option<&'static str>| {
        let rows = Rows { table: "ledger", id: "id", key: "id", group: None, payload: None, columns: &["input"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id };
        let store = Store { rows: Some(rows), parent: None, marks: &[], row_prefix: "l:", marker: None };
        facts(vec![layout(Some("ledger.db"), &[Mode::Usage], Some(UsageFields { input: &["/input"], ..NO_FIELDS }), store)])
    };
    let ledger = Fixture::bare("ledger.db");
    ledger.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
    ledger.sql("INSERT INTO ledger VALUES(4,5)", &[]);
    ledger.sql("INSERT INTO ledger VALUES(9,6)", &[]);
    let read = |facts| run::<PerTurn, NoCapability>(facts, &ledger.path, "usage", &json!(null), &json!({}), json!({})).unwrap();
    let named = read(reading(Some("row_id")));
    let raws: Vec<Value> = named["events"].as_array().unwrap().iter().map(|event| event["raw"].clone()).collect();
    assert_eq!(raws, [json!({"row_id": 4}), json!({"row_id": 9})]);
    assert!(read(reading(None))["events"].as_array().unwrap().iter().all(|event| event.get("raw").is_none()));
}

/// The layout is chosen on the observation's own connection: reading a store that holds two schemas opens it once.
#[test]
fn choosing_among_a_files_layouts_opens_the_store_once() {
    let store = Fixture::bare("ledger.db");
    store.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
    store.sql("CREATE TABLE entries(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
    store.sql("INSERT INTO entries VALUES(7,5)", &[]);
    let facts = two_schemas(true);
    let before = crate::observe::sqlite::testing::OPENED.with(|opened| opened.get());
    let read = run::<PerTurn, NoCapability>(facts, &store.path, "usage", &json!(null), &json!({}), json!({})).unwrap();
    let opened = crate::observe::sqlite::testing::OPENED.with(|opened| opened.get()) - before;
    assert_eq!(ids(&read), ["new:7"]);
    assert_eq!(opened, 1, "one connection decides the layout and reads the rows");
}

#[test]
fn a_usage_mode_sent_to_the_file_of_a_layout_that_records_no_usage_is_refused_and_the_other_file_answers_it() {
    let ledger = Fixture::bare("ledger.db");
    ledger.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
    ledger.sql("INSERT INTO ledger VALUES(1,5),(2,7)", &[]);
    let steps = Fixture::bare("steps.db");
    steps.sql("CREATE TABLE steps(idx INTEGER PRIMARY KEY, body BLOB, stamp INTEGER, status INTEGER)", &[]);
    let facts = two_layouts();
    let usage = |path: &Path, mode: &str| run::<PerTurn, NoCapability>(facts, path, mode, &json!(null), &json!({}), json!({}));
    assert_eq!(ids(&usage(&ledger.path, "session_usage").unwrap()), ["l:1", "l:2"], "on a row store session_usage is the replay");
    let refused = run::<NoUsage, NoCapability>(facts, &steps.path, "session_usage", &json!(null), &json!({}), json!({})).unwrap_err();
    assert_eq!(refused.to_string(), "OpenCode observation mode not integrated");
    let refused = run::<NoUsage, NoCapability>(facts, &steps.path, "usage", &json!(null), &json!({}), json!({})).unwrap_err();
    assert_eq!(refused.to_string(), "OpenCode observation mode not integrated");
    let refused = usage(&ledger.path, "activity").unwrap_err();
    assert_eq!(refused.to_string(), "OpenCode observation mode not integrated", "the ledger file does not list activity");
}

#[test]
fn the_replay_of_a_row_store_settles_against_the_seen_bag_page_after_page() {
    let ledger = Fixture::bare("ledger.db");
    ledger.sql("CREATE TABLE ledger(id INTEGER PRIMARY KEY, input INTEGER)", &[]);
    for id in 1..=5 {
        ledger.sql("INSERT INTO ledger VALUES(?,3)", &[json!(id)]);
    }
    let facts = two_layouts();
    let meta = json!({"vendor": "opencode", "path": ledger.path, "mode": "session_usage", "checkpoint": {}, "seen": ["l:1"], "state": null});
    let request = Request::parse(&meta).unwrap();
    let ctx = context(facts, &request);
    let small_pages = Sqlite::open_at(&ledger.path, 2).unwrap();
    let replay = observe_with::<Probe, Sqlite, PerTurn, NoCapability>(&ctx, Mode::SessionUsage, small_pages).unwrap();
    assert_eq!(ids(&replay), ["l:2", "l:3", "l:4", "l:5"]);
    assert_eq!(replay["seen"], json!(["l:2", "l:3", "l:4", "l:5"]), "the answer carries the keys it delivered, not the bag it was asked with");
    assert_eq!(replay["checkpoint"], json!({}), "a replay leaves the cursor alone");
}

// ---- pending rows that never finish ------------------------------------------------------------------------------------------

fn opencode(store: &Fixture, mode: &str, checkpoint: Value, state: Value, extra: Value) -> io::Result<Value> {
    let mut meta = json!({"vendor": "opencode", "path": store.path, "mode": mode, "checkpoint": checkpoint, "seen": [], "state": state});
    meta.as_object_mut().unwrap().extend(extra.as_object().cloned().unwrap_or_default());
    adapters::dispatch(adapters::find("opencode").unwrap(), &meta)
}

/// P6: a file that may hold one of several schemas and holds none answers the blank document in every mode (the checkpoint as
/// given, nothing found), with the tables it does hold, instead of refusing the pass on a table that is not there.
#[test]
fn a_file_with_several_candidate_layouts_none_of_which_applies_answers_blank_and_names_the_tables_it_holds() {
    for (held, tables) in [(None, json!([])), (Some("CREATE TABLE session_v2(id TEXT PRIMARY KEY)"), json!(["session_v2"]))] {
        let store = Fixture::bare("opencode.db");
        if let Some(statement) = held {
            store.sql(statement, &[]);
        }
        for mode in ["usage", "legacy", "activity", "has_session", "identify"] {
            let answer = opencode(&store, mode, json!({"row_id": 7}), json!({}), json!({"wanted_session": "ses_x", "markers": ["at-pane:x"]})).unwrap();
            assert_eq!(answer["schema_problem"], json!({"kind": "none_applies", "tables": tables}), "{mode}");
            assert_eq!(answer["found"], false, "{mode}");
            assert!(ids(&answer).is_empty(), "{mode}");
            if mode == "usage" {
                assert_eq!(answer["checkpoint"], json!({"row_id": 7}), "the checkpoint as given");
            }
        }
    }
    let fitting = Fixture::new("opencode.db");
    assert!(opencode(&fitting, "usage", json!({}), json!({}), json!({})).unwrap().get("schema_problem").is_none(), "a store that fits a layout has no problem");
}

fn unfinished(store: &Fixture, count: usize) {
    store.sql("BEGIN", &[]);
    for index in 0..count {
        store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(format!("m{index:05}")), json!(json!({"role": "assistant"}).to_string())]);
    }
    store.sql("COMMIT", &[]);
}

fn unfinished_since(store: &Fixture, id: &str, created_ms: i64) {
    let data = json!({"role": "assistant", "time": {"created": created_ms}});
    store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(id), json!(data.to_string())]);
}

#[test]
fn more_unfinished_rows_than_a_small_cache_are_all_carried_and_the_oldest_may_complete_last() {
    let store = Fixture::new("opencode.db");
    unfinished(&store, 70);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"].as_array().unwrap().len(), 70, "no unfinished row is dropped from a successful checkpoint");
    let completed = json!({"role": "assistant", "time": {"completed": 5}, "tokens": {"input": 4, "output": 1}});
    store.sql("UPDATE message SET data=? WHERE id='m00000'", &[json!(completed.to_string())]);
    let second = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(ids(&second), ["msg:m00000"], "the oldest row completes after the sixty-fourth that came after it");
    assert_eq!(second["checkpoint"]["pending"].as_array().unwrap().len(), 69);
}

#[test]
fn a_pass_that_would_carry_more_unfinished_rows_than_the_bound_is_refused_whole() {
    let store = Fixture::new("opencode.db");
    unfinished(&store, PENDING_MAX + 1);
    let refused = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap_err();
    assert!(refused.to_string().contains("pending"), "{refused}");
}

#[test]
fn a_pending_row_the_store_no_longer_holds_leaves_the_checkpoint_and_the_drop_is_reported() {
    let store = Fixture::new("opencode.db");
    unfinished(&store, 3);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"], json!([1, 2, 3]));
    assert!(first.get("dropped_pending").is_none(), "a pass that drops nothing reports nothing");
    store.sql("DELETE FROM message WHERE id='m00001'", &[]);
    let second = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(second["checkpoint"]["pending"], json!([1, 3]), "a deleted row cannot complete");
    assert_eq!(second["dropped_pending"], json!([{"table": "message", "id": 2, "why": "gone"}]));
    let third = opencode(&store, "usage", second["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(third["checkpoint"]["pending"], json!([1, 3]));
    assert!(third.get("dropped_pending").is_none());
}

/// A completed message whose `time.created` is `created_ms`: what the store's own clock reads.
fn completed_at(store: &Fixture, id: &str, created_ms: i64) {
    let data = json!({"role": "assistant", "time": {"created": created_ms, "completed": created_ms}, "tokens": {"input": 1, "output": 1}});
    store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(id), json!(data.to_string())]);
}

/// Every way a pending set is given up wholesale is reported, one test per path: the store file was replaced, the row the cursor
/// stood on is a different row, and the table shrank under the cursor. (A single pending row that is deleted is `gone`, above.)
#[test]
fn pending_rows_given_up_because_the_store_was_replaced_are_reported() {
    let store = Fixture::new("opencode.db");
    unfinished(&store, 2);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"], json!([1, 2]));
    let mut moved = first["checkpoint"].clone();
    moved["identity"] = json!("1:1");
    let second = opencode(&store, "usage", moved, json!({}), json!({})).unwrap();
    assert_eq!(second["dropped_pending"], json!([{"table": "message", "id": 1, "why": "replaced"}, {"table": "message", "id": 2, "why": "replaced"}]));
    assert_eq!(second["checkpoint"]["pending"], json!([1, 2]), "the rows the new store holds are read again from the start");
}

#[test]
fn pending_rows_given_up_because_the_anchor_row_is_a_different_row_are_reported() {
    let store = Fixture::new("opencode.db");
    let done = json!({"role": "assistant", "time": {"completed": 1}, "tokens": {"input": 2, "output": 1}});
    store.message("first", done);
    unfinished_since(&store, "open", 5);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"], json!([2]));
    // The same store file, but the row the cursor stood on is another row: the anchor no longer matches.
    let mut other = first["checkpoint"].clone();
    other["anchor"] = json!("ses_other");
    let second = opencode(&store, "usage", other, json!({}), json!({})).unwrap();
    assert_eq!(second["dropped_pending"], json!([{"table": "message", "id": 2, "why": "reanchored"}]));
}

#[test]
fn pending_rows_given_up_because_the_table_shrank_are_reported() {
    let store = Fixture::new("opencode.db");
    unfinished(&store, 3);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"], json!([1, 2, 3]));
    store.sql("DELETE FROM message", &[]);
    let second = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(second["checkpoint"]["pending"], json!([]));
    assert_eq!(
        second["dropped_pending"],
        json!([{"table": "message", "id": 1, "why": "shrunk"}, {"table": "message", "id": 2, "why": "shrunk"}, {"table": "message", "id": 3, "why": "shrunk"}])
    );
}

#[test]
fn pending_marks_given_up_because_the_table_shrank_are_reported() {
    let store = steps_store();
    step(&store, 1, 100, 5);
    step(&store, 2, 100, 5);
    let facts = step_walk_shrinking();
    let first = walk(&store, facts, &json!(null));
    assert_eq!(first["state"]["marks"]["steps"]["pending"], json!([1, 2]));
    store.sql("DELETE FROM steps", &[]);
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(second["state"]["marks"]["steps"]["pending"], json!([]));
    assert_eq!(second["dropped_pending"], json!([{"table": "steps", "id": 1, "why": "shrunk"}, {"table": "steps", "id": 2, "why": "shrunk"}]));
}

/// A pending row is old against the newest time the store showed in the pass, never against the machine's clock: the same
/// store answers the same way whenever it is read.
#[test]
fn a_pending_row_older_than_the_declared_age_against_the_stores_newest_time_is_dropped_and_reported() {
    let store = Fixture::new("opencode.db");
    let newest_ms = 1_800_000_000_000_i64;
    unfinished_since(&store, "old", newest_ms - 86_400_001);
    unfinished_since(&store, "young", newest_ms - 80_000_000);
    completed_at(&store, "newest", newest_ms);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["pending"], json!([2]), "declared age: one day against the newest time.created the pass read");
    assert_eq!(first["dropped_pending"], json!([{"table": "message", "id": 1, "why": "expired"}]));
    completed_at(&store, "five-seconds-on", newest_ms + 5_000);
    let later = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(later["checkpoint"]["pending"], json!([2]), "five seconds on it is still young");
    completed_at(&store, "a-day-on", newest_ms + 86_400_000);
    let much_later = opencode(&store, "usage", later["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(much_later["checkpoint"]["pending"], json!([]));
    assert_eq!(much_later["dropped_pending"], json!([{"table": "message", "id": 2, "why": "expired"}]));
}

/// The trade-off of a store clock: a store that stops receiving rows never ages its last pending rows. They stay bounded by
/// `PENDING_MAX` and stall nothing new.
#[test]
fn a_store_that_stops_receiving_rows_never_ages_its_last_pending_rows() {
    let store = Fixture::new("opencode.db");
    unfinished_since(&store, "stuck", 1_000);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    let again = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(again["checkpoint"]["pending"], json!([1]), "its own time is the newest the pass saw, so it is never old against it");
    assert!(again.get("dropped_pending").is_none());
}

/// A mark walk with a pending set, for the mechanics only: a step is pending while its status is 5.
fn step_walk(max_age: Option<PendingAge>) -> &'static adapters::ObserverFacts {
    let store = Store {
        rows: None,
        parent: None,
        marks: Box::leak(Box::new([step_marks(max_age, 256)])),
        row_prefix: "",
        marker: None,
    };
    facts(vec![layout(Some("steps.db"), ROW_MODES, None, store)])
}

/// The same walk, but one that follows a table down when it shrinks (the default keeps the walk where it was).
fn step_walk_shrinking() -> &'static adapters::ObserverFacts {
    let walk = Marks { shrink: MarkShrink::Newest, ..step_marks(None, 256) };
    let store = Store { rows: None, parent: None, marks: Box::leak(Box::new([walk])), row_prefix: "", marker: None };
    facts(vec![layout(Some("steps.db"), ROW_MODES, None, store)])
}

fn steps_store() -> Fixture {
    let store = Fixture::bare("steps.db");
    store.sql("CREATE TABLE steps(idx INTEGER PRIMARY KEY, body BLOB, stamp INTEGER, status INTEGER)", &[]);
    store
}

fn step(store: &Fixture, idx: i64, stamp: i64, status: i64) {
    store.sql("INSERT INTO steps VALUES(?,x'00',?,?)", &[json!(idx), json!(stamp), json!(status)]);
}

fn walk(store: &Fixture, facts: &'static adapters::ObserverFacts, state: &Value) -> Value {
    run::<NoUsage, NoCapability>(facts, &store.path, "activity", state, &json!({}), json!({})).unwrap()
}

#[test]
fn a_pending_mark_the_store_no_longer_holds_leaves_the_walk_and_the_drop_is_reported() {
    let store = steps_store();
    step(&store, 1, 100, 5);
    step(&store, 2, 100, 3);
    step(&store, 3, 100, 5);
    let facts = step_walk(None);
    let first = walk(&store, facts, &json!(null));
    assert_eq!(first["state"]["marks"]["steps"]["pending"], json!([1, 3]));
    store.sql("DELETE FROM steps WHERE idx=1", &[]);
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(second["state"]["marks"]["steps"]["pending"], json!([3]));
    assert_eq!(second["dropped_pending"], json!([{"table": "steps", "id": 1, "why": "gone"}]));
}

#[test]
fn a_pending_mark_older_than_the_declared_age_against_the_newest_mark_time_is_dropped_and_reported() {
    let store = steps_store();
    step(&store, 1, 100, 5);
    step(&store, 2, 300, 5);
    let facts = step_walk(Some(PendingAge { pointer: None, unit: TimeUnit::Seconds, seconds: 500 }));
    let first = walk(&store, facts, &json!(null));
    assert_eq!(first["state"]["marks"]["steps"]["pending"], json!([1, 2]), "the newest time is 300, so both are young, and each is reported once");
    assert_eq!(ids(&first), ["step:1", "step:2"]);
    step(&store, 3, 700, 3);
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(second["state"]["marks"]["steps"]["pending"], json!([2]), "against the new mark's 700 step 1 is 600 seconds old, step 2 is 400");
    assert_eq!(second["dropped_pending"], json!([{"table": "steps", "id": 1, "why": "expired"}]));
    assert_eq!(ids(&second), ["step:3"], "a pending mark already reported is not repeated");
}

/// Pins the page rule on 600 synthetic `steps` rows with a page the test passes in (256 and 512). It does not read any vendor's
/// declaration (Cursor's declares 512, Copilot's two walks 1000000): each vendor's page is tested where its store is read.
#[test]
fn a_walk_reads_the_declared_page_of_new_marks_per_pass_and_the_rest_on_the_next() {
    let store = steps_store();
    store.sql("BEGIN", &[]);
    for idx in 1..=600 {
        step(&store, idx, 100, 3);
    }
    store.sql("COMMIT", &[]);
    let at = |page: usize| {
        let store_decl = Store { rows: None, parent: None, marks: Box::leak(Box::new([step_marks(None, page)])), row_prefix: "", marker: None };
        facts(vec![layout(Some("steps.db"), ROW_MODES, None, store_decl)])
    };
    let default_page = walk(&store, at(256), &json!(null));
    assert_eq!(ids(&default_page).len(), 256);
    let wide = walk(&store, at(512), &json!(null));
    assert_eq!(ids(&wide).len(), 512, "a walk that declares a page of 512 reads 512 new marks, not the default 256");
    assert_eq!(wide["state"]["marks"]["steps"]["last"], 512);
    let rest = walk(&store, at(512), &wide["state"]);
    assert_eq!(ids(&rest).len(), 88);
}

/// A store of two walks, `steps` then `notes`; `notes` waits for `steps`.
fn ordered_walks(page: usize) -> &'static adapters::ObserverFacts {
    let notes = Marks { table: "notes", walk: NOTES, prefix: "note:", after: Some(STEPS), ..step_marks(None, page) };
    let store = Store { rows: None, parent: None, marks: Box::leak(Box::new([step_marks(None, page), notes])), row_prefix: "", marker: None };
    facts(vec![layout(Some("steps.db"), ROW_MODES, None, store)])
}

fn notes(store: &Fixture, ids: std::ops::RangeInclusive<i64>) {
    store.sql("CREATE TABLE IF NOT EXISTS notes(idx INTEGER PRIMARY KEY, body BLOB, stamp INTEGER, status INTEGER)", &[]);
    for idx in ids {
        store.sql("INSERT INTO notes VALUES(?,x'00',100,3)", &[json!(idx)]);
    }
}

/// P2: a walk that declares `after` reads nothing while the earlier walk's page came back full, so what it delivers never lands
/// before the earlier walk's last mark, however many passes the earlier walk takes.
#[test]
fn a_walk_that_waits_for_an_earlier_one_reads_nothing_until_that_walks_page_comes_back_short() {
    let store = steps_store();
    for idx in 1..=5 {
        step(&store, idx, 100, 3);
    }
    notes(&store, 1..=3);
    let facts = ordered_walks(2);
    let first = walk(&store, facts, &json!(null));
    assert_eq!(ids(&first), ["step:1", "step:2"], "the earlier page is full: the later walk waits");
    assert!(first["state"]["marks"].get("notes").is_none(), "a waiting walk plants nothing");
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(ids(&second), ["step:3", "step:4"]);
    let third = walk(&store, facts, &second["state"]);
    assert_eq!(ids(&third), ["step:5", "note:1", "note:2"], "the earlier page came back short: the later walk reads in the same pass");
    let fourth = walk(&store, facts, &third["state"]);
    assert_eq!(ids(&fourth), ["note:3"]);
    assert!(ids(&walk(&store, facts, &fourth["state"])).is_empty());
}

const MIB: usize = 1024 * 1024;

fn big_step(store: &Fixture, table: &str, idx: i64, bytes: usize) {
    store.sql(&format!("INSERT INTO {table} VALUES(?,x'00',100,?)"), &[json!(idx), json!("x".repeat(bytes))]);
}

/// Q1: a pass is bounded by the answer ledger (about 3.5 MiB: the cap less the fixed envelope) as well as by marks. A mark that would
/// take the pass past it waits for the next pass, and the walk commits its mark at the last one delivered.
#[test]
fn a_walk_leaves_the_mark_that_would_cross_the_pass_budget_for_the_next_pass() {
    let store = steps_store();
    for idx in 1..=10 {
        big_step(&store, "steps", idx, MIB * 8 / 5);
    }
    let facts = step_walk(None);
    let first = walk(&store, facts, &json!(null));
    assert_eq!(ids(&first), ["step:1", "step:2"], "two 1.6 MiB marks fit the ledger, the third would cross it: well short of the 256-mark page");
    assert_eq!(first["state"]["marks"]["steps"]["last"], 2, "the mark is committed at the last row delivered");
    assert!(first.get("truncated").is_none(), "a mark that fits a pass is never cut");
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(ids(&second), ["step:3", "step:4"]);
    let third = walk(&store, facts, &second["state"]);
    assert_eq!(ids(&third), ["step:5", "step:6"]);
    let fourth = walk(&store, facts, &third["state"]);
    assert_eq!(ids(&fourth), ["step:7", "step:8"]);
    assert_eq!(ids(&walk(&store, facts, &fourth["state"])), ["step:9", "step:10"]);
}

/// t36i T3: the tables a foreign store names are bounded by their serialized size (64 KiB in all, 256 characters a name), whatever the
/// characters: a name of control characters weighs six bytes a character once serialized.
#[test]
fn the_tables_a_foreign_store_names_are_bounded_by_their_serialized_size() {
    let store = Fixture::bare("opencode.db");
    for n in 0..300 {
        store.sql(&format!("CREATE TABLE \"t{n:03}{}\"(x)", "\u{1}".repeat(2000)), &[]);
    }
    let answer = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    let tables = answer["schema_problem"]["tables"].as_array().unwrap();
    assert!(!tables.is_empty() && tables.iter().all(|name| name.as_str().unwrap().chars().count() <= 256 + 3), "no name is over 256 characters");
    assert!(answer["schema_problem"]["tables"].to_string().len() <= 64 * 1024, "{} bytes", answer["schema_problem"]["tables"].to_string().len());
    let plain = Fixture::bare("opencode.db");
    plain.sql("CREATE TABLE a(x)", &[]);
    plain.sql("CREATE TABLE b(x)", &[]);
    let small = opencode(&plain, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(small["schema_problem"]["tables"], json!(["a", "b"]), "an ordinary store names every table, in order");
}

/// t36i T1 and t36j U1: an activity pass the ledger stopped before the end of its walk says `more`, so the caller goes on without
/// waiting for the store to change; the pass that reaches the end says nothing. A walk whose declared page came back full says `more`
/// too: the baseline of a store has no such page (Copilot's reads every row in one call), so a full page is not a natural end. The
/// last page is short and says nothing; an exactly full last page costs one empty pass.
#[test]
fn an_activity_walk_the_ledger_or_its_declared_page_stopped_says_more_and_one_that_reached_its_end_does_not() {
    let store = steps_store();
    for idx in 1..=5 {
        big_step(&store, "steps", idx, MIB * 8 / 5);
    }
    let by_ledger = step_walk(None);
    let first = walk(&store, by_ledger, &json!(null));
    assert_eq!(ids(&first), ["step:1", "step:2"]);
    assert_eq!(first["more"], true, "the ledger stopped the walk with marks left");
    let second = walk(&store, by_ledger, &first["state"]);
    assert_eq!(ids(&second), ["step:3", "step:4"]);
    assert_eq!(second["more"], true);
    let third = walk(&store, by_ledger, &second["state"]);
    assert_eq!(ids(&third), ["step:5"]);
    assert!(third.get("more").is_none(), "the walk reached the end of the table");

    let paged = steps_store();
    for idx in 1..=5 {
        step(&paged, idx, 100, 3);
    }
    let store = Store { rows: None, parent: None, marks: Box::leak(Box::new([step_marks(None, 2)])), row_prefix: "", marker: None };
    let small_page = facts(vec![layout(Some("steps.db"), ROW_MODES, None, store)]);
    let page = walk(&paged, small_page, &json!(null));
    assert_eq!(ids(&page), ["step:1", "step:2"]);
    assert_eq!(page["more"], true, "the declared page came back full: marks may remain, so the caller goes on");
    let page_two = walk(&paged, small_page, &page["state"]);
    assert_eq!(ids(&page_two), ["step:3", "step:4"]);
    assert_eq!(page_two["more"], true);
    let last = walk(&paged, small_page, &page_two["state"]);
    assert_eq!(ids(&last), ["step:5"]);
    assert!(last.get("more").is_none(), "a short page is the end of the walk");

    // An exactly full last page costs one empty pass: nothing says the table ends there.
    step(&paged, 6, 100, 3);
    step(&paged, 7, 100, 3);
    let filled = walk(&paged, small_page, &last["state"]);
    assert_eq!((ids(&filled), filled["more"].clone()), (vec!["step:6".to_owned(), "step:7".to_owned()], json!(true)));
    let empty = walk(&paged, small_page, &filled["state"]);
    assert!(ids(&empty).is_empty() && empty.get("more").is_none(), "the empty pass that ends it");
}

/// t36j U1: a walk that waits for an earlier one starts in the pass after the one that drained the earlier walk, and every pass up to
/// there says `more` (the earlier walk's full pages), so one drain delivers the earlier walk's marks and then the later walk's.
#[test]
fn a_waiting_walk_runs_on_a_later_page_of_the_same_drain() {
    let store = steps_store();
    for idx in 1..=5 {
        step(&store, idx, 100, 3);
    }
    notes(&store, 1..=3);
    let facts = ordered_walks(2);
    let (mut state, mut delivered, mut passes) = (json!(null), Vec::new(), 0);
    loop {
        let pass = walk(&store, facts, &state);
        delivered.extend(ids(&pass));
        state = pass["state"].clone();
        passes += 1;
        if pass.get("more").is_none() {
            break;
        }
    }
    assert_eq!(delivered, ["step:1", "step:2", "step:3", "step:4", "step:5", "note:1", "note:2", "note:3"]);
    assert_eq!(passes, 4, "steps 2, steps 2, steps 1 with the notes' first 2 beside it, then the last note");
}

/// The walk of `steps` over pages of `page` marks, whose layout asks for a scan clock (`base_epoch`), as Cursor's does.
fn clocked_walk(page: usize, shrink: MarkShrink) -> &'static adapters::ObserverFacts {
    let walk = Marks { shrink, ..step_marks(None, page) };
    let store = Store { rows: None, parent: None, marks: Box::leak(Box::new([walk])), row_prefix: "", marker: None };
    facts(vec![Layout { request: &[adapters::RequestField::BaseEpoch], ..layout(Some("steps.db"), ROW_MODES, None, store) }])
}

/// One call of `walk` with the scan clock the reader would have named (epoch seconds).
fn walk_at(store: &Fixture, facts: &'static adapters::ObserverFacts, state: &Value, clock: f64) -> Value {
    run::<NoUsage, NoCapability>(facts, &store.path, "activity", state, &json!({}), json!({"base_epoch": clock})).unwrap()
}

/// The stamps (`Pass::stamp`) the probe gave the events of a pass, in microseconds since the epoch.
fn clocks(pass: &Value) -> Vec<i64> {
    pass["events"].as_array().unwrap().iter().map(|event| event["clock"].as_i64().unwrap()).collect()
}

/// t36j U2 and t36k V1: the stamps an adapter gives its events (`Pass::stamp`) go on across the pages of one drain AND across calls
/// (a drained walk keeps its last stamp), and a clock that moved past them is followed, not pushed beyond.
#[test]
fn an_adapters_stamps_go_on_across_the_pages_of_a_drain_and_across_calls_and_follow_a_clock_that_moves_past_them() {
    let store = steps_store();
    for idx in 1..=5 {
        step(&store, idx, 100, 3);
    }
    let facts = clocked_walk(2, MarkShrink::Keep);
    let at = |milliseconds: i64| 1_000_000_000 + milliseconds * 1000; // the clock 1,000 s, plus milliseconds
    let first = walk_at(&store, facts, &json!(null), 1000.0);
    assert_eq!((clocks(&first), first["more"].clone()), (vec![at(0), at(1)], json!(true)));
    let second = walk_at(&store, facts, &first["state"], 1000.0);
    assert_eq!((clocks(&second), second["more"].clone()), (vec![at(2), at(3)], json!(true)));
    let third = walk_at(&store, facts, &second["state"], 1000.0);
    assert_eq!(clocks(&third), [at(4)]);
    assert!(third.get("more").is_none());
    assert_eq!(third["state"]["marks"]["steps"]["stamped"], at(4), "a drained walk keeps its last stamp");
    // The next call: the clock did not move, then it moved by less than the drain was long, then past it.
    step(&store, 6, 100, 3);
    let unchanged = walk_at(&store, facts, &third["state"], 1000.0);
    assert_eq!(clocks(&unchanged), [at(5)], "a later call goes on from the last stamp, it does not start again at the clock");
    step(&store, 7, 100, 3);
    let nudged = walk_at(&store, facts, &unchanged["state"], 1000.002);
    assert_eq!(clocks(&nudged), [at(6)]);
    step(&store, 8, 100, 3);
    let moved = walk_at(&store, facts, &nudged["state"], 1060.0);
    assert_eq!(clocks(&moved), [at(60_000)], "a clock past the last stamp is followed");
}

/// t36k V1: a store that shrank under the walk re-anchors its position (`last`, `pending`), not its stamps: the pane's gate does not reset.
#[test]
fn a_store_that_shrank_re_anchors_the_walk_but_not_its_stamp_floor() {
    let store = steps_store();
    for idx in 1..=3 {
        step(&store, idx, 100, 3);
    }
    let facts = clocked_walk(256, MarkShrink::Newest);
    let at = |milliseconds: i64| 1_000_000_000 + milliseconds * 1000;
    let first = walk_at(&store, facts, &json!(null), 1000.0);
    assert_eq!(clocks(&first), [at(0), at(1), at(2)]);
    store.sql("DELETE FROM steps", &[]);
    step(&store, 1, 100, 3);
    let shrunk = walk_at(&store, facts, &first["state"], 1000.0);
    assert!(clocks(&shrunk).is_empty() && shrunk["state"]["marks"]["steps"]["last"] == 1, "the walk was planted again at the new newest mark");
    assert_eq!(shrunk["state"]["marks"]["steps"]["stamped"], at(2), "and its stamp floor is what it was");
    step(&store, 2, 100, 3);
    assert_eq!(clocks(&walk_at(&store, facts, &shrunk["state"], 1000.0)), [at(3)]);
}

/// t36b W2 (A5 of the t36k checkpoint): a mark the answer ledger refuses is built again by the next pass, so the stamp it took is dropped
/// with it: the floor the walk keeps is the last stamp of a mark that was DELIVERED, and the refused mark gets the stamp it would have got.
#[test]
fn a_mark_the_ledger_refuses_gives_back_its_stamp_to_the_next_pass() {
    let store = steps_store();
    for idx in 1..=3 {
        big_step(&store, "steps", idx, MIB * 8 / 5);
    }
    let facts = clocked_walk(256, MarkShrink::Keep);
    let at = |milliseconds: i64| 1_000_000_000 + milliseconds * 1000;
    let first = walk_at(&store, facts, &json!(null), 1000.0);
    assert_eq!((ids(&first), clocks(&first), first["more"].clone()), (vec!["step:1".to_owned(), "step:2".to_owned()], vec![at(0), at(1)], json!(true)), "the third 1.6 MiB mark would cross the ledger");
    assert_eq!(first["state"]["marks"]["steps"]["stamped"], at(1), "the floor is the last delivered mark's stamp, not the one the refused mark took");
    let second = walk_at(&store, facts, &first["state"], 1000.0);
    assert_eq!((ids(&second), clocks(&second)), (vec!["step:3".to_owned()], vec![at(2)]), "the refused mark is built again and stamped as the next one");
}

/// t36j U1: a usage pass whose page of rows came back full says `more` (the baseline of a store reads every row in one call); a short
/// one does not.
#[test]
fn a_usage_pass_whose_page_came_back_full_says_more_and_a_short_one_does_not() {
    let store = Fixture::new("opencode.db");
    let done = json!({"role": "assistant", "time": {"completed": 5}, "tokens": {"input": 4, "output": 1}}).to_string();
    store.sql("BEGIN", &[]);
    for index in 0..2001 {
        store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(format!("m{index:05}")), json!(done)]);
    }
    store.sql("COMMIT", &[]);
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["events"].as_array().unwrap().len(), 2000);
    assert_eq!(first["more"], true, "the page of 2,000 rows came back full");
    let second = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(ids(&second), ["msg:m02000"]);
    assert!(second.get("more").is_none(), "a short page is the end of the table");
}

/// Q1: one ledger for the whole pass, shared by the walks: the second walk takes what the first left.
#[test]
fn the_walks_of_a_pass_share_one_budget() {
    let store = steps_store();
    store.sql("CREATE TABLE notes(idx INTEGER PRIMARY KEY, body BLOB, stamp INTEGER, status INTEGER)", &[]);
    for idx in 1..=2 {
        big_step(&store, "steps", idx, MIB * 3 / 2);
        big_step(&store, "notes", idx, MIB * 3 / 2);
    }
    let facts = ordered_walks(256);
    let first = walk(&store, facts, &json!(null));
    assert_eq!(ids(&first), ["step:1", "step:2"], "3 MiB of steps leave under 1 MiB: the first note would cross the ledger");
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(ids(&second), ["note:1", "note:2"]);
    assert_eq!(first["more"], true, "the notes wait because the ledger is spent (t36i T1: the caller asks again)");
    assert!(second.get("more").is_none() && second.get("truncated").is_none());
}

/// Q1: a mark whose events are alone over the whole budget is delivered with the text of its events cut (head and tail kept), so
/// the walk advances; the cut is reported once, with the bytes the mark had.
#[test]
fn a_mark_over_the_whole_budget_is_cut_reported_and_the_walk_moves_past_it() {
    let store = steps_store();
    store.sql("INSERT INTO steps VALUES(1,x'00',100,?)", &[json!(format!("HEAD{}TAIL", "y".repeat(9 * MIB)))]);
    big_step(&store, "steps", 2, 10);
    let facts = step_walk(None);
    let first = walk(&store, facts, &json!(null));
    assert_eq!(ids(&first), ["step:1"], "the oversized mark is the whole pass");
    let text = first["events"][0]["text"].as_str().unwrap();
    assert!(text.starts_with("HEAD") && text.ends_with("TAIL"), "head and tail survive");
    assert!(first["events"][0].to_string().len() <= super::super::answer::LEDGER_BYTES, "the pass fits its ledger: {}", first["events"][0].to_string().len());
    let cut = first["truncated"].as_array().unwrap();
    assert_eq!((cut.len(), &cut[0]["table"], &cut[0]["id"]), (1, &json!("steps"), &json!(1)));
    assert!(cut[0]["bytes"].as_u64().unwrap() > 9 * MIB as u64);
    assert_eq!(first["state"]["marks"]["steps"]["last"], 1);
    let second = walk(&store, facts, &first["state"]);
    assert_eq!(ids(&second), ["step:2"], "the next pass reads on");
    assert!(second.get("truncated").is_none());
}

#[test]
fn a_mark_is_handed_the_newest_mark_of_the_pass_and_stamps_a_millisecond_apart() {
    let store = steps_store();
    for idx in 1..=3 {
        step(&store, idx, 100, 3);
    }
    let result = walk(&store, step_walk(None), &json!(null));
    let seen: Vec<(i64, i64)> = result["events"].as_array().unwrap().iter().map(|e| (e["newest"].as_i64().unwrap(), e["clock"].as_i64().unwrap())).collect();
    assert_eq!(seen, [(3, 0), (3, 1000), (3, 2000)], "a walk whose layout names no scan clock stamps from zero");
}

// ---- the failure policy ------------------------------------------------------------------------------------------------------------

#[test]
fn a_text_cell_that_is_not_utf8_skips_its_row_and_is_reported_by_column_name() {
    let store = Fixture::new("opencode.db");
    let done = json!({"role": "assistant", "time": {"completed": 1}, "tokens": {"input": 2, "output": 1}});
    store.message("good-a", done.clone());
    store.sql("INSERT INTO message VALUES('bad','ses_own',CAST(x'ff7b7d' AS TEXT))", &[]);
    store.message("good-b", done);
    let read = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(ids(&read), ["msg:good-a", "msg:good-b"], "the bad row is left out, the others are delivered");
    assert_eq!(read["skipped_cells"], json!(["data"]));
}

#[test]
fn a_request_value_the_store_cannot_take_is_its_own_error_and_not_a_schema_error() {
    let store = Fixture::new("opencode.db");
    let refused = opencode(&store, "has_session", json!({}), json!({}), json!({"wanted_session": "a\u{0}b"})).unwrap_err();
    assert!(refused.to_string().contains("request value"), "{refused}");
    assert!(!refused.to_string().contains("declared tables"), "{refused}");
}

#[test]
fn a_session_lookup_with_no_session_id_finds_none_and_is_not_an_error() {
    let store = Fixture::new("opencode.db");
    for extra in [json!({}), json!({"wanted_session": null}), json!({"wanted_session": ""}), json!({"wanted_session": 7})] {
        let answer = opencode(&store, "has_session", json!({}), json!({}), extra).unwrap();
        assert_eq!(answer["found"], false);
    }
}

/// The facts of a log (`events.jsonl`) that yields to the store `session-store.db` two directories above it, as Copilot's do.
fn deferring_facts() -> &'static adapters::ObserverFacts {
    let log = Layout {
        source: SourceKind::Jsonl,
        usage: UsageMode::None,
        store_file: Some("events.jsonl"),
        applies_when: &[],
        modes: &[Mode::Activity],
        request: &[],
        usage_fields: None,
        store: None,
        rules: None,
        defers_to: Some(adapters::Defers { file: "session-store.db", up: 2 }),
    };
    let parent = adapters::Parent { table: "sessions", key: "id", dir: "cwd", up: None, version: None, columns: &[], join: adapters::JoinPolicy::Left, top: None, dir_form: adapters::DirForm::AsStored };
    let held = Store {
        rows: Some(Rows { table: "turns", id: "id", key: "id", group: Some("session_id"), payload: None, columns: &["x"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }),
        parent: Some(parent),
        marks: &[],
        row_prefix: "",
        marker: None,
    };
    facts(vec![log, layout(Some("session-store.db"), &[Mode::Activity], None, held)])
}

#[test]
fn a_half_built_store_without_the_parent_table_does_not_own_the_session_so_the_log_is_read() {
    // Compat: the Python `_db_owns_session` answers False for a store it cannot read, so a poll between two CREATE TABLEs reads the log.
    let store = Fixture::bare("session-store.db");
    store.sql("CREATE TABLE turns(id INTEGER)", &[]);
    let meta = json!({"path": store.root.join("session-a").join("events.jsonl"), "mode": "activity"});
    let request = Request::parse(&meta).unwrap();
    let half_built = defers(&context(deferring_facts(), &request));
    assert!(!half_built.owned);
    let problem = half_built.problem.expect("a store that could not answer is reported with the answer");
    assert!(problem["error"].as_str().unwrap().contains("sqlite error 1"), "{problem}");
    assert_eq!(problem["store"], json!(store.path));
    store.sql("CREATE TABLE sessions(id TEXT, cwd TEXT)", &[]);
    let unlisted = defers(&context(deferring_facts(), &request));
    assert!(!unlisted.owned && unlisted.problem.is_none(), "a store that does not list the session does not own it, and that is no problem");
    store.sql("INSERT INTO sessions VALUES ('session-a', '/w')", &[]);
    let listed = defers(&context(deferring_facts(), &request));
    assert!(listed.owned && listed.problem.is_none(), "a store that lists it does");
}

#[test]
fn an_ownership_lookup_that_the_store_cannot_prepare_is_not_owned_and_reported_and_a_missing_store_is_neither() {
    let store = Fixture::bare("session-store.db");
    store.sql("CREATE TABLE sessions(other TEXT)", &[]);
    let log = Layout {
        source: SourceKind::Jsonl,
        usage: UsageMode::None,
        store_file: Some("events.jsonl"),
        applies_when: &[],
        modes: &[Mode::Activity],
        request: &[],
        usage_fields: None,
        store: None,
        rules: None,
        defers_to: Some(adapters::Defers { file: "session-store.db", up: 2 }),
    };
    let mut parent = adapters::Parent { table: "sessions", key: "id", dir: "cwd", up: None, version: None, columns: &[], join: adapters::JoinPolicy::Left, top: None, dir_form: adapters::DirForm::AsStored };
    parent.columns = &[];
    let held = Store {
        rows: Some(Rows { table: "turns", id: "id", key: "id", group: Some("session_id"), payload: None, columns: &["x"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }),
        parent: Some(parent),
        marks: &[],
        row_prefix: "",
        marker: None,
    };
    let facts = facts(vec![log, layout(Some("session-store.db"), &[Mode::Activity], None, held)]);
    let meta = json!({"path": store.root.join("session-a").join("events.jsonl"), "mode": "activity"});
    let request = Request::parse(&meta).unwrap();
    let unreadable = defers(&context(facts, &request));
    assert!(!unreadable.owned, "the lookup the store cannot prepare means not owned, so the log is read");
    assert!(unreadable.problem.is_some_and(|problem| problem["error"].as_str().unwrap().contains("sqlite error 1")), "and it is reported");
    let elsewhere = Fixture::bare("other.db");
    let meta = json!({"path": elsewhere.root.join("session-a").join("events.jsonl"), "mode": "activity"});
    let request = Request::parse(&meta).unwrap();
    let missing = defers(&context(facts, &request));
    assert!(!missing.owned && missing.problem.is_none(), "no store beside the log means the log is read, and nothing went wrong");
}

// ---- the bounds and the quoting --------------------------------------------------------------------------------------------------

#[test]
fn a_reserved_word_is_a_valid_table_name_and_a_missing_table_is_an_error_not_silence() {
    let fields = UsageFields { input: &["/select"], output: &["/select"], ..NO_FIELDS };
    let store = Store {
        rows: Some(Rows { table: "order", id: "id", key: "id", group: Some("group"), payload: None, columns: &["select"], anchor: &["id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }),
        parent: None,
        marks: &[],
        row_prefix: "w:",
        marker: None,
    };
    let facts = facts(vec![layout(Some("reserved.db"), ROW_MODES, Some(fields), store)]);
    let fixture = Fixture::bare("reserved.db");
    let missing = run::<PerTurn, NoCapability>(facts, &fixture.path, "usage", &json!(null), &json!({}), json!({})).unwrap_err();
    assert!(missing.to_string().contains("sqlite error 1"), "{missing}");
    fixture.sql("CREATE TABLE \"order\"(id TEXT, \"group\" TEXT, \"select\" INTEGER)", &[]);
    fixture.sql("INSERT INTO \"order\" VALUES('k1','g',4)", &[]);
    let read = run::<PerTurn, NoCapability>(facts, &fixture.path, "usage", &json!(null), &json!({}), json!({})).unwrap();
    assert_eq!(ids(&read), ["w:k1"]);
}

/// The anchor is how a baseline reader fingerprints a row: `"|".join(str(cell))`, so a NULL cell is `None` and a flag is
/// `True` or `False`; a cursor saved by the baseline must still match the row it stood on.
#[test]
fn an_anchor_spells_each_cell_the_way_pythons_str_does() {
    assert_eq!(anchor_of_cells(&[json!(null), json!(true), json!(false), json!("a"), json!(3)]), "None|True|False|a|3");
}

#[test]
fn a_like_pattern_takes_percent_underscore_and_backslash_literally() {
    assert_eq!(contains("a_b%c\\d"), "%a\\_b\\%c\\\\d%");
    assert_eq!(contains("plain"), "%plain%");
    assert_eq!(q("order"), "`order`");
}

fn replay_store(rows: usize) -> Fixture {
    let store = Fixture::new("opencode.db");
    let done = json!({"role": "assistant", "time": {"completed": 1}, "tokens": {"input": 2, "output": 1}});
    store.sql("BEGIN", &[]);
    for index in 1..=rows {
        store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(format!("m{index}")), json!(done.to_string())]);
    }
    store.sql("COMMIT", &[]);
    store
}

fn replay_with(store: &Fixture, seen: &[&str], limit: impl FnOnce(&mut Sqlite)) -> io::Result<Value> {
    let facts = adapters::find("opencode").unwrap();
    let meta = json!({"vendor": "opencode", "path": store.path, "mode": "legacy", "checkpoint": {}, "seen": seen, "state": null});
    let request = Request::parse(&meta).unwrap();
    let ctx = context(facts, &request);
    let mut source = Sqlite::open_at(&store.path, 2).unwrap();
    limit(&mut source);
    observe_with::<crate::observe::adapters::opencode::Opencode, Sqlite, PerTurn, (BySession, ByMarker)>(&ctx, Mode::Legacy, source)
}

#[test]
fn a_legacy_replay_settles_page_by_page_across_page_boundaries() {
    let store = replay_store(5);
    let replay = replay_with(&store, &["msg:m1"], |_| {}).unwrap();
    assert_eq!(ids(&replay), ["msg:m2", "msg:m3", "msg:m4", "msg:m5"], "a row the seen bag holds is not delivered again, across page boundaries");
    assert_eq!(replay["seen"], json!(["msg:m2", "msg:m3", "msg:m4", "msg:m5"]), "only the keys it delivered");
}

#[test]
fn the_replay_row_ceiling_admits_a_store_of_exactly_that_many_rows_and_refuses_one_more() {
    let store = replay_store(5);
    assert_eq!(ids(&replay_with(&store, &[], |source| source.replay_rows = 5).unwrap()).len(), 5, "equal to the ceiling is read");
    let refused = replay_with(&store, &["msg:m1"], |source| source.replay_rows = 4).unwrap_err();
    assert!(refused.to_string().contains("limit"), "{refused}");
    store.sql("INSERT INTO message VALUES('m6','ses_own','{}')", &[]);
    let refused = replay_with(&store, &[], |source| source.replay_rows = 5).unwrap_err();
    assert!(refused.to_string().contains("limit"), "one row over the ceiling is refused whole");
}

#[test]
fn the_replay_byte_ceiling_admits_exactly_that_many_payload_bytes_and_refuses_one_more() {
    let store = replay_store(5);
    // The bytes the replay weighs: each row's parsed record, as text.
    let weight = |source: &Sqlite| {
        let facts = adapters::find("opencode").unwrap();
        let store_decl = facts.layouts[1].store.as_ref().unwrap();
        source.page(store_decl, store_decl.rows.as_ref().unwrap(), "").unwrap().unwrap().iter().map(|row| row.record.to_string().len()).sum::<usize>()
    };
    let probe = Sqlite::open_at(&store.path, 2_000).unwrap();
    let total = weight(&probe);
    assert!(replay_with(&store, &[], |source| source.replay_bytes = total).is_ok(), "equal to the ceiling is read");
    let refused = replay_with(&store, &[], |source| source.replay_bytes = total - 1).unwrap_err();
    assert!(refused.to_string().contains("limit"), "{refused}");
}

#[test]
fn a_row_cursor_whose_table_shrank_restarts_past_the_newest_row_and_reads_the_rows_after_it() {
    let store = Fixture::new("opencode.db");
    let done = json!({"role": "assistant", "time": {"completed": 1}, "tokens": {"input": 2, "output": 1}});
    for id in ["a", "b"] {
        store.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(id), json!(done.to_string())]);
    }
    let first = opencode(&store, "usage", json!({}), json!({}), json!({})).unwrap();
    assert_eq!(first["checkpoint"]["row_id"], 2);
    store.sql("DELETE FROM message", &[]);
    store.sql("INSERT INTO message VALUES('c','ses_own',?)", &[json!(done.to_string())]);
    let after = opencode(&store, "usage", first["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(after["checkpoint"]["row_id"], 1, "the cursor follows the table down");
    store.sql("INSERT INTO message VALUES('d','ses_own',?)", &[json!(done.to_string())]);
    let next = opencode(&store, "usage", after["checkpoint"].clone(), json!({}), json!({})).unwrap();
    assert_eq!(ids(&next), ["msg:d"]);
}

// ---- the walk enum -------------------------------------------------------------------------------------------------------------

/// `Table_name` as the generator spells a walk's variant: each `_`-separated word capitalised.
fn variant_of(table: &str) -> String {
    table.split('_').map(|word| word[..1].to_uppercase() + &word[1..]).collect()
}

/// P1 and Q4: a walk's identity is a typed value, never a position. Every declared walk names its own parser's variant for its own
/// table, and an `after` names a walk the same store declares before it, so an adapter matches a variant and the core looks no
/// position up.
#[test]
fn every_declared_walk_is_the_typed_variant_of_its_own_table_and_waits_only_for_an_earlier_one() {
    fn check<W: WalkId + std::fmt::Debug>(vendor: &str) {
        for layout in adapters::find(vendor).unwrap().layouts {
            let walks: Vec<&Marks> = layout.store.iter().flat_map(|store| store.marks).collect();
            for (at, walk) in walks.iter().enumerate() {
                let own = W::of(walk.walk).unwrap_or_else(|| panic!("{vendor}: the walk of `{}` is another parser's", walk.table));
                assert_eq!(format!("{own:?}"), variant_of(walk.table), "{vendor}: the walk of `{}`", walk.table);
                if let Some(earlier) = walk.after {
                    assert!(walks[..at].iter().any(|declared| declared.walk == earlier), "{vendor}: `{}` waits for a walk declared before it", walk.table);
                }
            }
        }
    }
    check::<adapters::OpencodeWalk>("opencode");
    check::<adapters::OpencodeWalk>("kilo");
    check::<adapters::CopilotWalk>("copilot");
    check::<adapters::GooseWalk>("goose");
    assert!(adapters::CopilotWalk::of(adapters::Walk::Goose(adapters::GooseWalk::Messages)).is_none(), "another parser's walk is no walk of this one");
}

/// Q5: a walk's cells are reached by a generated column enum, indexed by position, never by name. Each variant is the position of
/// the declared column it spells in the cells the core hands the adapter (the walk's columns, then the parent's).
#[test]
fn a_column_of_a_walk_is_the_position_of_the_declared_cell_it_spells() {
    use adapters::{AntigravityStepsColumn, CopilotAssistantUsageEventsColumn, CopilotTurnsColumn, GooseMessagesColumn, OpencodeSessionMessageColumn};
    fn declared(vendor: &str, table: &str) -> Vec<&'static str> {
        let layout = adapters::find(vendor).unwrap().layouts.iter().find(|layout| layout.store.iter().flat_map(|store| store.marks).any(|walk| walk.table == table)).unwrap();
        let (store, walk) = (layout.store.as_ref().unwrap(), layout.store.iter().flat_map(|store| store.marks).find(|walk| walk.table == table).unwrap());
        walk.columns.iter().chain(store.parent.iter().filter(|_| walk.group.is_some()).flat_map(|parent| parent.columns)).copied().collect()
    }
    let named = |cells: Vec<&'static str>, column: &dyn Fn() -> usize| cells[column()];
    assert_eq!(named(declared("copilot", "turns"), &|| CopilotTurnsColumn::AssistantResponse.position()), "assistant_response");
    assert_eq!(named(declared("copilot", "turns"), &|| CopilotTurnsColumn::UserMessage.position()), "user_message");
    assert_eq!(named(declared("copilot", "turns"), &|| CopilotTurnsColumn::Timestamp.position()), "timestamp");
    assert_eq!(named(declared("copilot", "assistant_usage_events"), &|| CopilotAssistantUsageEventsColumn::CreatedAt.position()), "created_at");
    assert_eq!(named(declared("goose", "messages"), &|| GooseMessagesColumn::MetadataJson.position()), "metadata_json");
    assert_eq!(named(declared("goose", "messages"), &|| GooseMessagesColumn::SessionType.position()), "session_type", "the parent's columns follow the walk's");
    assert_eq!(named(declared("opencode", "session_message"), &|| OpencodeSessionMessageColumn::Seq.position()), "seq");
    assert_eq!(named(declared("antigravity", "steps"), &|| AntigravityStepsColumn::Status.position()), "status");
}

/// Q5: whether a mark belongs to a top-level group is the core's, computed from the declared parent: its `up` cell is empty and its
/// declared `top` column holds the declared value. A walk with no group has no parent to be under.
#[test]
fn a_mark_knows_whether_its_group_is_top_level_from_the_declared_parent() {
    let parent = adapters::Parent {
        table: "sessions",
        key: "id",
        dir: "dir",
        up: Some("parent_id"),
        version: None,
        columns: &["kind"],
        join: adapters::JoinPolicy::Inner,
        top: Some(adapters::TopIs { column: "kind", at: 0, equals: "user" }),
        dir_form: adapters::DirForm::AsStored,
    };
    let grouped = Marks { group: Some("session_id"), ..step_marks(None, 256) };
    let store = Store { rows: None, parent: Some(parent), marks: Box::leak(Box::new([grouped])), row_prefix: "", marker: None };
    let facts = facts(vec![layout(Some("steps.db"), ROW_MODES, None, store)]);
    let fixture = Fixture::bare("steps.db");
    fixture.sql("CREATE TABLE sessions(id TEXT, dir TEXT, parent_id TEXT, kind TEXT)", &[]);
    fixture.sql("CREATE TABLE steps(idx INTEGER PRIMARY KEY, body BLOB, stamp INTEGER, status INTEGER, session_id TEXT)", &[]);
    for statement in [
        "INSERT INTO sessions VALUES('top','/w',NULL,'user')",
        "INSERT INTO sessions VALUES('child','/w','top','user')",
        "INSERT INTO sessions VALUES('scheduled','/w',NULL,'scheduled')",
        "INSERT INTO sessions VALUES('blank','/w','','user')",
    ] {
        fixture.sql(statement, &[]);
    }
    for (idx, session) in ["top", "child", "scheduled", "blank"].iter().enumerate() {
        fixture.sql("INSERT INTO steps VALUES(?,x'00',100,3,?)", &[json!(idx as i64 + 1), json!(session)]);
    }
    let answer = walk(&fixture, facts, &json!(null));
    let top: Vec<bool> = answer["events"].as_array().unwrap().iter().map(|event| event["top_level"].as_bool().unwrap()).collect();
    assert_eq!(top, [true, false, false, true], "a child session and a session of another kind are not top-level; an empty parent id is none");
    let bare = steps_store();
    step(&bare, 1, 100, 3);
    assert_eq!(walk(&bare, step_walk(None), &json!(null))["events"][0]["top_level"], true, "a walk with no group has no parent above it");
}

// ---- t36g R8: a column belongs to its own walk, and the `top` column is a generated position -------------------------------

/// Does a mark of one walk take the column `D`? The compiler answers: the inherent `accepted` exists only where `M: CellOf<D>`, so a
/// column of another walk (or another parser) falls through to the trait method, which says no. A column that does not compile as
/// the argument of `cell` is exactly one for which this says `false`.
struct Asks<M, D>(std::marker::PhantomData<(M, D)>);

trait Refused {
    fn accepted(&self) -> bool {
        false
    }
}

impl<T> Refused for T {}

impl<M: CellOf<D>, D> Asks<M, D> {
    fn accepted(&self) -> bool {
        true
    }
}

fn asks<M, D>() -> Asks<M, D> {
    Asks(std::marker::PhantomData)
}

#[test]
fn a_mark_takes_only_the_columns_of_its_own_walk() {
    use crate::observe::adapters::{CopilotAssistantUsageEventsColumn, CopilotTurnsColumn, GooseMessagesColumn};
    assert!(asks::<WalkMark<CopilotTurnsColumn>, CopilotTurnsColumn>().accepted(), "its own column");
    assert!(!asks::<WalkMark<CopilotTurnsColumn>, CopilotAssistantUsageEventsColumn>().accepted(), "another walk of the same parser");
    assert!(!asks::<WalkMark<CopilotTurnsColumn>, GooseMessagesColumn>().accepted(), "another parser's walk");
    assert!(!asks::<WalkMark<CopilotTurnsColumn>, StatusColumn>().accepted(), "a column enum written outside the generator");
}

#[test]
fn the_declared_top_column_is_a_generated_position_in_the_parents_columns() {
    let goose = adapters::find("goose").unwrap();
    let parent = goose.layouts[0].store.as_ref().unwrap().parent.as_ref().unwrap();
    let top = parent.top.unwrap();
    assert_eq!((top.column, top.at, parent.columns[top.at], top.equals), ("session_type", 0, "session_type", "user"));
}

/// t36g N5: a `WalkMark` is made only by its walk's generated `mark()`. The hand-written adapters never name its constructor, so a
/// mark typed by one walk's columns cannot be rebuilt for another's.
#[test]
fn no_hand_written_adapter_builds_a_walk_mark() {
    let adapters = [
        ("antigravity", include_str!("adapters/antigravity.rs")),
        ("claude", include_str!("adapters/claude.rs")),
        ("codex", include_str!("adapters/codex.rs")),
        ("copilot", include_str!("adapters/copilot.rs")),
        ("cursor", include_str!("adapters/cursor.rs")),
        ("goose", include_str!("adapters/goose.rs")),
        ("opencode", include_str!("adapters/opencode.rs")),
    ];
    for (name, source) in adapters {
        assert!(!source.contains("WalkMark::new"), "{name} builds a WalkMark by hand");
    }
    assert!(include_str!("adapters/mod.rs").contains("WalkMark::new(mark)"), "the generated dispatch is where marks are made");
}
