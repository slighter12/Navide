//! The answer ledger: the one place every byte of an observation's answer is accounted for.
//!
//! The sidecar refuses an answer over `ANSWER_BYTES` (`MAX_OBSERVATION_PARTS x OBSERVATION_CHUNK` = 128 x 65,280 = 8,355,840 bytes).
//! `Answer` owns everything a pass produces for the answer's variable parts, and charges each item to one ledger, in the form it
//! will be serialized, at the moment it is produced:
//!
//! - each event, with its final cursor (the producer builds the cursor before it takes the event);
//! - each dedup key the answer's `seen` carries (a row that makes no event still carries its key);
//! - each report (`truncated`, `dropped_pending`) and each `turns` row.
//!
//! A producer asks `take` (or `take_turn`) before it commits anything: when the item would cross the ledger and the pass already
//! holds an item, `take` answers `false`, the producer stops before the item and the answer says `more`; an item that alone is
//! over the whole ledger is cut (its non-text fields capped, its text cut) and reported, so a pass always advances. A report that
//! no longer fits is counted, and the count is the one entry that stands for them (never silent).
//!
//! The ledger's limit is `ANSWER_BYTES` minus a fixed envelope for the parts that do not grow with the work, each with the bound
//! that keeps it inside its share (checked once more when the document is assembled, `Run::into_document`):
//!
//! | share | bytes | what it holds, and what bounds it |
//! |---|---|---|
//! | open text | 3 MiB | the reply text of an open turn (`state.last_text`), cut to a serialized size of `OPEN_TEXT_BYTES` |
//! | checkpoint | 1 MiB | the pass-final checkpoint: the request's (at most `CHECKPOINT_REQUEST_BYTES`) plus the core's keys and a window of `RECENT_KEYS` keys of at most `KEY_BYTES` |
//! | state | 256 KiB | water, position, totals, the pending sets (`PENDING_MAX` ids per walk), the replay position, the adapter's state |
//! | fixed | 256 KiB | parser, path, mode, identity, error, `ownership_error`, layout, the log's own ids (capped), schema problem, skipped columns, and the sidecar's `ownership` context (`OWNERSHIP_BYTES` of it, bounded when the session is created); a const assertion in `mod.rs` sums the worst of each source kind against it |
//!
//! The marker bindings of `identify` are not in the fixed share: each is charged to the ledger (`Answer::bind`), because the request names
//! as many markers as the caller has unbound and every binding carries up to two capped cells of the store.
use serde_json::{json, Map, Value};

use super::cap_head_tail;

/// The most bytes the sidecar sends as one answer: 128 parts of 64 KiB less 256 bytes of frame.
pub const ANSWER_BYTES: usize = 128 * (64 * 1024 - 256);
/// The serialized size of an open turn's reply text the core carries between passes. The text travels in the answer's `state` and in the
/// next request's `state`, and a request carries at most `MAX_META_P2R` = 4 MiB of meta, so the text may take three quarters of it.
pub(super) const OPEN_TEXT_BYTES: usize = crate::frame::MAX_META_P2R as usize / 4 * 3;
/// The pass-final checkpoint's share.
pub(super) const CHECKPOINT_BYTES: usize = 1024 * 1024;
/// The most a request's checkpoint may weigh. The core never produces a larger one (its keys are `kind`, offsets, an identity, an
/// anchor, a window of dedup keys, a pending set), so a larger one is refused rather than copied into every cursor.
pub(super) const CHECKPOINT_REQUEST_BYTES: usize = 256 * 1024;
/// State share: see the table above.
pub(super) const STATE_BYTES: usize = 256 * 1024;
/// Fixed fields' share: see the table above.
pub(super) const FIXED_BYTES: usize = 256 * 1024;
/// The part of the fixed share the sidecar keeps for the `ownership` context it adds to an observation bound to a live session (its
/// session id, generation, pane and workspace path, `posix.rs`); the core's own fixed fields have the rest (`FIXED_BYTES -
/// OWNERSHIP_BYTES`). The context needs no check of its own: a session is created only when its `SESSION_OWNED` frame, which carries
/// the same four fields, fits `MAX_HAS_FD_FRAME` (`handoff_size_ok`), so the context cannot be larger than that frame.
pub const OWNERSHIP_BYTES: usize = crate::frame::MAX_HAS_FD_FRAME;
/// What is left for the variable parts: events, dedup keys, reports and turn rows.
pub(super) const LEDGER_BYTES: usize = ANSWER_BYTES - OPEN_TEXT_BYTES - CHECKPOINT_BYTES - STATE_BYTES - FIXED_BYTES;
/// The fields of the answer the ledger charges (the other variable parts are the events and the `seen` keys): everything else in the
/// document is the fixed share's.
pub(super) const LEDGER_LISTS: [&str; 4] = ["turns", "truncated", "dropped_pending", "markers"];
/// The longest dedup key the core carries as it is. A longer one is replaced, everywhere it travels (event, cursor, `seen`), by a
/// stable digest (`bound_key`). Real CLI ids are far below it.
pub(super) const KEY_BYTES: usize = 1024;
/// The most characters one non-text field of an event keeps when the event alone is over the whole ledger.
pub(super) const FIELD_CHARS: usize = 8 * 1024;
/// A pass this close to the end of its ledger stops reading: the next item would not fit.
const FULL_MARGIN: usize = 4096;
/// What an item that alone is over the ledger leaves free when it is cut, so the report of the cut (`truncated`) always fits.
const CUT_REPORT_BYTES: usize = 1024;

/// The serialized weight of events in the answer (their JSON text and the comma that separates them).
pub(super) fn event_bytes(events: &[Value]) -> usize {
    events.iter().map(|event| event.to_string().len() + 1).sum()
}

/// The serialized weight of one dedup key in `seen` (its JSON string and the comma).
fn key_bytes(key: &str) -> usize {
    Value::String(key.to_owned()).to_string().len() + 1
}

/// `key` as it travels: itself, or (over `KEY_BYTES`) a fixed prefix and the blake2b-32 of the key, in hex. The same key always gives
/// the same digest, so dedup holds across passes and retries.
pub(super) fn bound_key(key: String) -> String {
    if key.len() <= KEY_BYTES {
        key
    } else {
        format!("long::{}", super::blake2b::hex_digest(key.as_bytes(), 32))
    }
}

/// Cut the `text` of `events` (head and tail kept, with `cap_head_tail`) to the longest length at which they weigh at most `room`
/// bytes; events with no text to cut stay as they are, so the result can still be over `room` when their other fields alone are.
fn cut_to_fit(events: &mut [Value], room: usize) {
    let full: Vec<Option<String>> = events.iter().map(|event| event["text"].as_str().map(str::to_owned)).collect();
    let apply = |events: &mut [Value], max: usize| {
        for (event, text) in events.iter_mut().zip(&full) {
            if let Some(text) = text {
                event["text"] = json!(cap_head_tail(text, max));
            }
        }
    };
    let (mut fits, mut over) = (2usize, room.max(2));
    while fits < over {
        let middle = fits + (over - fits).div_ceil(2);
        apply(events, middle);
        if event_bytes(events) <= room {
            fits = middle;
        } else {
            over = middle - 1;
        }
    }
    apply(events, fits);
}

/// Cap the core event fields that are not `text` (`FIELD_CHARS`, head and tail kept). It runs first, on an event that alone is over
/// the ledger, and the `text` is cut into what that leaves: a mark whose timestamp or model alone is megabytes still has to be
/// delivered, and the room the text is cut to is the room after those fields.
fn cap_fields(events: &mut [Value]) {
    for event in events {
        for field in ["timestamp", "detail", "cwd", "session_id", "file_path", "model", "cli_version"] {
            if let Some(value) = event[field].as_str().filter(|value| value.chars().nth(FIELD_CHARS).is_some()) {
                event[field] = json!(cap_head_tail(value, FIELD_CHARS));
            }
        }
    }
}

/// Cap every string in `value` (its leaves, whatever the depth) to `FIELD_CHARS` characters, head and tail kept. Answers whether
/// anything was cut. One generic cut for a row of a vendor's own shape: the core does not know which of its fields a log lets grow.
pub(super) fn cap_strings(value: &mut Value) -> bool {
    match value {
        Value::String(text) if text.chars().nth(FIELD_CHARS).is_some() => {
            *text = cap_head_tail(text, FIELD_CHARS);
            true
        }
        Value::Array(items) => items.iter_mut().fold(false, |cut, item| cap_strings(item) | cut),
        Value::Object(fields) => fields.values_mut().fold(false, |cut, field| cap_strings(field) | cut),
        _ => false,
    }
}

/// The top-level array of a `turns` row that weighs the most serialized bytes (the one a row that is too large for one answer is
/// split by: splitting a short list would not make the pieces smaller).
fn heaviest_list(row: &Value) -> Option<(&str, &Vec<Value>)> {
    let fields = row.as_object()?;
    let weight = |list: &Vec<Value>| list.iter().map(|item| item.to_string().len() + 1).sum::<usize>();
    fields.iter().filter_map(|(name, value)| value.as_array().map(|list| (name.as_str(), list))).max_by_key(|(_, list)| weight(list))
}

/// One piece of a `turns` row: the whole row with `list` cut to `items` (the first piece, `from == 0`), or, for a later piece, the
/// marker `continues` naming the list and the next entries of it (the caller appends them to the row it holds).
fn piece_of(row: &Value, name: &str, items: &[Value], from: usize) -> Value {
    if from == 0 {
        let mut piece = row.clone();
        piece[name] = Value::Array(items.to_vec());
        piece
    } else {
        json!({"continues": name, "items": items})
    }
}

/// What `take_turn` did with a row (or the rest of one).
pub enum Piece {
    /// All of it is taken.
    Done,
    /// This many entries of its list are taken, and the page is full: the next page continues the row from them.
    Partial(usize),
    /// None of it is taken: the pass holds items already and it does not fit; it starts the next page.
    Wait,
}

/// What a pass produces for the answer, behind the ledger.
#[derive(Default)]
pub struct Answer {
    used: usize,
    /// Items (events with their keys, or a turn row) the pass holds: the first one is never refused, only cut.
    taken: usize,
    /// How many of those items carry events (the ones `discard_events` gives back).
    event_items: usize,
    events: Vec<Value>,
    keys: Vec<String>,
    extra: Map<String, Value>,
    /// Reports that did not fit, by list.
    suppressed: std::collections::BTreeMap<String, usize>,
    /// Found markers whose binding did not fit (`bind`).
    unbound: usize,
}

impl Answer {
    /// What is left of the ledger.
    pub fn room(&self) -> usize {
        LEDGER_BYTES.saturating_sub(self.used)
    }

    /// Does the pass hold an item already?
    pub fn holds(&self) -> bool {
        self.taken > 0
    }

    /// Is the ledger spent, for all a further item could need?
    pub fn full(&self) -> bool {
        self.room() < FULL_MARGIN
    }

    /// Take `events` (and the dedup `key` the answer carries for them, or for a row that makes none) into the pass: they fit what is
    /// left, or `false` and nothing is taken because the pass already holds an item (they wait for the next pass). Alone over the
    /// whole ledger, with nothing else in the pass, the fields that are not `text` are capped and the `text` is cut so they fit, and
    /// the cut is reported (`truncated`), so the pass always advances.
    pub fn take(&mut self, source: &str, id: i64, events: &mut Vec<Value>, key: Option<&str>) -> bool {
        let keyed = key.map_or(0, key_bytes);
        let weight = event_bytes(events) + keyed;
        let mut cut = None;
        if weight > self.room() {
            if self.taken > 0 {
                return false;
            }
            cap_fields(events);
            cut_to_fit(events, self.room().saturating_sub(keyed + CUT_REPORT_BYTES));
            cut = Some(weight);
        }
        self.used += event_bytes(events) + keyed;
        self.taken += 1;
        self.event_items += usize::from(!events.is_empty());
        self.events.append(events);
        self.keys.extend(key.map(str::to_owned));
        if let Some(bytes) = cut {
            self.report("truncated", json!({"table": source, "id": id, "bytes": bytes}));
        }
        true
    }

    /// Take a `turns` row, or the rest of it from entry `from` of its heaviest list. A row that is over the whole ledger has every string
    /// in it capped first (`cap_strings`; the same cut on every page of the row, so its pieces are those of one row), and the cut is
    /// reported (`truncated`) with the row's first piece, and only when something was cut. The row then fits, or it is split. A row that
    /// fits is taken whole. One that does not fit beside the items the pass holds waits for the next page; alone in the pass it is split:
    /// as many entries of its list as fit are taken (at least one) and the page is full, and the next page takes the rest as a
    /// continuation piece. Splitting cuts nothing, so the pieces together are the (capped) row.
    pub fn take_turn(&mut self, source: &str, id: i64, row: &Value, from: usize) -> Piece {
        let before = row.to_string().len() + 1;
        let mut capped = None;
        if before > LEDGER_BYTES {
            let mut copy = row.clone();
            capped = cap_strings(&mut copy).then_some(copy);
        }
        let cut = capped.as_ref().filter(|_| from == 0).map(|_| before);
        let row = capped.as_ref().unwrap_or(row);
        let Some((name, list)) = heaviest_list(row) else {
            let weight = row.to_string().len() + 1;
            if weight > self.room() && self.taken > 0 {
                return Piece::Wait;
            }
            self.push_row(row.clone(), weight);
            self.report_cut(source, id, cut);
            return Piece::Done;
        };
        let from = from.min(list.len());
        let whole = piece_of(row, name, &list[from..], from);
        let weight = whole.to_string().len() + 1;
        if weight <= self.room() {
            self.push_row(whole, weight);
            self.report_cut(source, id, cut);
            return Piece::Done;
        }
        if self.taken > 0 {
            return Piece::Wait;
        }
        if list.len() == from {
            // Nothing in its list to split by: the row is over the ledger by its other fields, which no vendor's row is.
            self.push_row(whole, weight);
            self.report_cut(source, id, cut);
            return Piece::Done;
        }
        // Alone in the pass: the most entries that fit, found by halving (the weight grows with the count).
        let room = self.room().saturating_sub(CUT_REPORT_BYTES);
        let weigh = |count: usize| piece_of(row, name, &list[from..from + count], from).to_string().len() + 1;
        let (mut fits, mut over) = (1usize, list.len() - from);
        while fits + 1 < over {
            let middle = fits + (over - fits) / 2;
            if weigh(middle) <= room {
                fits = middle;
            } else {
                over = middle;
            }
        }
        let piece = piece_of(row, name, &list[from..from + fits], from);
        let bytes = piece.to_string().len() + 1;
        self.push_row(piece, bytes);
        self.report_cut(source, id, cut);
        Piece::Partial(fits)
    }

    fn push_row(&mut self, row: Value, weight: usize) {
        self.used += weight;
        self.taken += 1;
        if let Some(rows) = self.extra.entry("turns").or_insert_with(|| json!([])).as_array_mut() {
            rows.push(row);
        }
    }

    /// Report that a `turns` row was cut: the bytes it had before the cut. None when nothing was cut.
    fn report_cut(&mut self, source: &str, id: i64, bytes: Option<usize>) {
        if let Some(bytes) = bytes {
            self.report("truncated", json!({"table": source, "id": id, "bytes": bytes}));
        }
    }

    /// Give back everything the events of this pass cost (a pass whose earlier events the baseline does not deliver). The items that
    /// carried events go with them; an item that carried none (a row that made no event but whose key the answer holds) stays.
    pub fn discard_events(&mut self) {
        self.used = self.used.saturating_sub(event_bytes(&self.events));
        self.taken = self.taken.saturating_sub(self.event_items);
        self.event_items = 0;
        self.events.clear();
    }

    /// Add `entry` to the report list `list` (`truncated`, `dropped_pending`). One that no longer fits is counted instead.
    pub fn report(&mut self, list: &str, entry: Value) {
        let weight = entry.to_string().len() + 1;
        if weight > self.room() {
            *self.suppressed.entry(list.to_owned()).or_default() += 1;
            return;
        }
        self.used += weight;
        if let Some(entries) = self.extra.entry(list).or_insert_with(|| json!([])).as_array_mut() {
            entries.push(entry);
        }
    }

    /// A field of the answer's fixed share (`more`, `layout`, ...).
    pub fn set(&mut self, name: &str, value: Value) {
        self.extra.insert(name.to_owned(), value);
    }

    /// Bind a found marker (`identify`) to where it lives. The marker and its `[group, dir]` are charged to the ledger like a report: the
    /// request names as many markers as the caller has unbound, and each binding carries up to two capped cells of the store, so the
    /// bindings are a variable part, not a fixed one. One that no longer fits is counted (`markers_suppressed` in the answer), and the
    /// lookup still answers with the rest.
    pub fn bind(&mut self, marker: &str, binding: Value) {
        let weight = json_len(marker) + binding.to_string().len() + 2;
        if weight > self.room() {
            self.unbound += 1;
            return;
        }
        self.used += weight;
        if let Some(bound) = self.extra.entry("markers").or_insert_with(|| json!({})).as_object_mut() {
            bound.insert(marker.to_owned(), binding);
        }
    }

    /// The pieces of the document: events, the sorted dedup keys, and the extra fields (with the entry that stands for the reports
    /// that did not fit).
    pub fn into_parts(mut self) -> (Vec<Value>, Vec<String>, Map<String, Value>) {
        for (list, count) in std::mem::take(&mut self.suppressed) {
            let entry = json!({"table": "*", "id": 0, "why": "suppressed: the answer was full", "bytes": count});
            if let Some(entries) = self.extra.entry(list).or_insert_with(|| json!([])).as_array_mut() {
                entries.push(entry);
            }
        }
        if self.unbound > 0 {
            self.extra.insert("markers_suppressed".to_owned(), json!(self.unbound));
        }
        self.keys.sort();
        (self.events, self.keys, self.extra)
    }
}

/// The serialized size of `text` as a JSON string (quotes and escapes included), without building it.
pub(super) fn json_len(text: &str) -> usize {
    2 + text
        .bytes()
        .map(|byte| match byte {
            b'"' | b'\\' | b'\n' | b'\r' | b'\t' | 0x08 | 0x0c => 2,
            0..=0x1f => 6,
            _ => 1,
        })
        .sum::<usize>()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(text: &str) -> Value {
        json!({"dedup_key": "k", "text": text})
    }

    #[test]
    fn the_ledger_is_the_cap_less_the_stated_envelope() {
        assert_eq!(ANSWER_BYTES, 8_355_840);
        assert_eq!(LEDGER_BYTES + OPEN_TEXT_BYTES + CHECKPOINT_BYTES + STATE_BYTES + FIXED_BYTES, ANSWER_BYTES);
    }

    #[test]
    fn an_item_that_would_cross_the_ledger_waits_unless_the_pass_holds_nothing() {
        let mut answer = Answer::default();
        let mut first = vec![event(&"a".repeat(LEDGER_BYTES / 2))];
        assert!(answer.take("t", 1, &mut first, Some("k1")));
        let mut second = vec![event(&"b".repeat(LEDGER_BYTES / 2))];
        assert!(!answer.take("t", 2, &mut second, Some("k2")), "the second would cross the ledger: it waits");
        assert_eq!(second.len(), 1, "and nothing of it is taken");
        let mut huge = Answer::default();
        let mut alone = vec![event(&"c".repeat(2 * LEDGER_BYTES))];
        assert!(huge.take("t", 3, &mut alone, Some("k3")), "alone over the whole ledger it is cut and taken");
        let (events, keys, extra) = huge.into_parts();
        assert!(event_bytes(&events) <= LEDGER_BYTES && keys == ["k3"] && extra["truncated"][0]["id"] == 3);
    }

    #[test]
    fn giving_back_the_events_of_a_pass_keeps_the_items_that_made_none() {
        let mut answer = Answer::default();
        assert!(answer.take("t", 1, &mut vec![event("a")], Some("k1")));
        assert!(answer.take("t", 2, &mut Vec::new(), Some("k2")), "a row that makes no event is an item by its key");
        assert!(answer.take("t", 3, &mut vec![event("b")], Some("k3")));
        assert_eq!(answer.taken, 3);
        answer.discard_events();
        assert_eq!(answer.taken, 1, "the two items that carried events go, the one that made none stays");
        assert!(answer.holds() && event_bytes(&answer.events) == 0);
        answer.discard_events();
        assert_eq!(answer.taken, 1, "a second call gives back nothing more");
    }

    #[test]
    fn a_key_alone_is_an_item_and_reports_that_do_not_fit_are_counted() {
        let mut answer = Answer::default();
        answer.used = LEDGER_BYTES - 10;
        answer.taken = 1;
        assert!(!answer.take("t", 1, &mut Vec::new(), Some(&"k".repeat(100))), "a row without an event is weighed by its key");
        answer.report("dropped_pending", json!({"table": "t", "id": 1, "why": "x".repeat(100)}));
        answer.report("dropped_pending", json!({"table": "t", "id": 2, "why": "x".repeat(100)}));
        let (_, _, extra) = answer.into_parts();
        assert_eq!(extra["dropped_pending"], json!([{"table": "*", "id": 0, "why": "suppressed: the answer was full", "bytes": 2}]));
    }

    /// t36j U5: the bindings of the markers a lookup found are a variable part of the answer, charged to the ledger like a report; one
    /// that does not fit is counted (`markers_suppressed`) and the others are answered.
    #[test]
    fn a_found_marker_is_charged_to_the_ledger_and_one_that_does_not_fit_is_counted_not_refused() {
        let mut answer = Answer::default();
        answer.set("markers", json!({}));
        answer.bind("at-pane:a", json!(["group-a", "/dir-a"]));
        let used = answer.used;
        assert!(used >= "at-pane:a".len() + "group-a".len() + "/dir-a".len(), "the binding has a weight: {used}");
        answer.used = LEDGER_BYTES - 20;
        answer.bind("at-pane:b", json!(["group-b", "/dir-b"]));
        answer.bind("at-pane:c", json!(["group-c", "x".repeat(100)]));
        let (_, _, extra) = answer.into_parts();
        assert_eq!(extra["markers"], json!({"at-pane:a": ["group-a", "/dir-a"]}), "only the binding that fit");
        assert_eq!(extra["markers_suppressed"], 2, "the others are counted");
    }

    #[test]
    fn a_turn_row_over_the_ledger_is_split_by_its_longest_list_and_the_pieces_are_the_row() {
        let calls: Vec<Value> = (0..LEDGER_BYTES / 50).map(|n| json!({"n": n, "pad": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"})).collect();
        let row = json!({"turn_index": 1, "input": 5, "calls": calls});
        let (mut from, mut pieces, mut rebuilt) = (0, 0, Vec::<Value>::new());
        loop {
            let mut answer = Answer::default();
            let done = match answer.take_turn("turns", 1, &row, from) {
                Piece::Done => true,
                Piece::Partial(taken) => {
                    from += taken;
                    false
                }
                Piece::Wait => panic!("a pass that holds nothing never waits"),
            };
            let (_, _, extra) = answer.into_parts();
            let piece = &extra["turns"][0];
            assert!(piece.to_string().len() < LEDGER_BYTES, "every piece fits the ledger");
            assert!(extra.get("truncated").is_none(), "splitting a row cuts nothing, so it reports nothing");
            if pieces == 0 {
                assert_eq!(piece["input"], 5, "the first piece carries the row's own fields");
                rebuilt.extend(piece["calls"].as_array().unwrap().iter().cloned());
            } else {
                assert_eq!(piece["continues"], "calls");
                rebuilt.extend(piece["items"].as_array().unwrap().iter().cloned());
            }
            pieces += 1;
            if done {
                break;
            }
        }
        assert!(pieces >= 2, "a row of more than one ledger is not one piece");
        assert_eq!(&rebuilt, row["calls"].as_array().unwrap(), "nothing is cut: the pieces are the row");
    }

    /// t36i T2: a row over the ledger because of a long string leaf is cut, not shipped over the transport cap. Every string leaf of the
    /// row (its own fields and those of every entry of its lists) keeps its head and tail at `FIELD_CHARS`, and the cut is reported once.
    #[test]
    fn a_turn_row_with_a_leaf_over_the_ledger_has_every_leaf_cut_and_the_cut_reported_once() {
        let long = |marker: &str| format!("HEAD{}{marker}TAIL", "z".repeat(9 * 1024 * 1024));
        let row = json!({"turn_index": 1, "started_at": long("a"), "ended_at": long("b"), "cli_version": "2.1", "calls": [
            {"ts": long("c"), "model": "m", "cli_version": "2.1"},
            {"ts": "2026-05-28T00:00:00Z", "model": long("d"), "cli_version": "2.1"},
            {"ts": long("e"), "model": long("f"), "cli_version": long("g")}]});
        let mut answer = Answer::default();
        assert!(matches!(answer.take_turn("turns", 7, &row, 0), Piece::Done), "after the cut the row fits one piece");
        let (_, _, extra) = answer.into_parts();
        let piece = &extra["turns"][0];
        assert!(piece.to_string().len() < LEDGER_BYTES, "the piece fits the ledger: {}", piece.to_string().len());
        for text in [&piece["started_at"], &piece["calls"][0]["ts"], &piece["calls"][1]["model"], &piece["calls"][2]["cli_version"]] {
            let text = text.as_str().unwrap();
            assert!(text.starts_with("HEAD") && text.ends_with("TAIL") && text.chars().count() <= FIELD_CHARS + 3, "head and tail kept, {} chars", text.chars().count());
        }
        assert_eq!(piece["calls"][0]["model"], "m", "a leaf under the cap is untouched");
        let cuts = extra["truncated"].as_array().unwrap();
        assert_eq!((cuts.len(), &cuts[0]["table"], &cuts[0]["id"]), (1, &json!("turns"), &json!(7)));
        assert!(cuts[0]["bytes"].as_u64().unwrap() > 9 * 1024 * 1024, "the report names the bytes the row had");
    }

    /// t36i T2: the cut is the same on every page of a row, so the pieces of a row that is both too long and has leaves over the cap are
    /// still one row; and a row that needed no cut reports none (`truncated` is a statement that something was cut).
    #[test]
    fn the_pieces_of_a_row_are_cut_the_same_way_on_every_page_and_a_row_that_needed_no_cut_reports_none() {
        let calls: Vec<Value> = (0..LEDGER_BYTES / 200).map(|n| json!({"n": n, "model": "m".repeat(300)})).collect();
        let row = json!({"turn_index": 1, "started_at": "z".repeat(5 * 1024 * 1024), "calls": calls});
        let (mut from, mut pieces, mut rebuilt) = (0, 0, Vec::<Value>::new());
        loop {
            let mut answer = Answer::default();
            let done = match answer.take_turn("turns", 1, &row, from) {
                Piece::Done => true,
                Piece::Partial(taken) => {
                    from += taken;
                    false
                }
                Piece::Wait => panic!("a pass that holds nothing never waits"),
            };
            let (_, _, extra) = answer.into_parts();
            let piece = &extra["turns"][0];
            assert!(piece.to_string().len() < LEDGER_BYTES);
            assert_eq!(extra.get("truncated").map_or(0, |cuts| cuts.as_array().unwrap().len()), usize::from(pieces == 0), "the cut is reported with the row's first piece, once");
            if pieces == 0 {
                assert!(piece["started_at"].as_str().unwrap().chars().count() <= FIELD_CHARS + 3);
                rebuilt.extend(piece["calls"].as_array().unwrap().iter().cloned());
            } else {
                rebuilt.extend(piece["items"].as_array().unwrap().iter().cloned());
            }
            pieces += 1;
            if done {
                break;
            }
        }
        assert!(pieces >= 2, "{pieces}");
        assert_eq!(&rebuilt, row["calls"].as_array().unwrap(), "no entry is lost");
        let mut plain = Answer::default();
        let fits = json!({"turn_index": 2, "calls": [{"model": "m".repeat(100_000)}]});
        assert!(matches!(plain.take_turn("turns", 2, &fits, 0), Piece::Done));
        assert!(plain.into_parts().2.get("truncated").is_none(), "a row that fits the ledger is not cut and reports nothing");
    }

    /// t36i T2: a row is split by the list that weighs the most serialized bytes, not by the one with the most entries.
    #[test]
    fn a_turn_row_is_split_by_the_list_that_weighs_the_most_bytes() {
        let tiny: Vec<Value> = (0..2000).map(|n| json!(n)).collect();
        let heavy: Vec<Value> = (0..40).map(|n| json!({"n": n, "pad": "h".repeat(7000)})).collect();
        let row = json!({"turn_index": 1, "tiny": tiny, "heavy": heavy.iter().cycle().take(600).cloned().collect::<Vec<_>>()});
        let mut answer = Answer::default();
        let Piece::Partial(taken) = answer.take_turn("turns", 1, &row, 0) else { panic!("the row is over the ledger") };
        let (_, _, extra) = answer.into_parts();
        let piece = &extra["turns"][0];
        assert_eq!(piece["tiny"].as_array().unwrap().len(), 2000, "the short list travels whole");
        assert_eq!(piece["heavy"].as_array().unwrap().len(), taken, "and the heavy one is the one that is split");
        assert!(piece.to_string().len() < LEDGER_BYTES && taken < 600);
    }

    #[test]
    fn a_turn_row_that_does_not_fit_beside_an_item_waits_for_the_next_page() {
        let mut answer = Answer::default();
        let small = json!({"turn_index": 0, "calls": []});
        assert!(matches!(answer.take_turn("turns", 0, &small, 0), Piece::Done));
        let big = json!({"turn_index": 1, "calls": vec![json!({"pad": "x".repeat(1000)}); LEDGER_BYTES / 1000]});
        assert!(matches!(answer.take_turn("turns", 1, &big, 0), Piece::Wait));
    }

    #[test]
    fn a_key_over_the_bound_is_a_stable_digest_and_a_short_key_is_itself() {
        let long = "r".repeat(KEY_BYTES + 1);
        let bounded = bound_key(long.clone());
        assert!(bounded.starts_with("long::") && bounded.len() == "long::".len() + 64);
        assert_eq!(bounded, bound_key(long));
        assert_eq!(bound_key("m1::short".into()), "m1::short");
    }

    #[test]
    fn the_json_length_counts_the_escapes_serde_writes() {
        for text in ["plain", "quo\"te", "new\nline", "\u{1}ctl", "héllo", "back\\slash\t"] {
            assert_eq!(json_len(text), serde_json::to_string(text).unwrap().len(), "{text:?}");
        }
    }
}
