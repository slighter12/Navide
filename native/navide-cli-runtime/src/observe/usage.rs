//! Usage extraction driven by the declaration: pointers into a JSON record, no per-vendor code.
//!
//! A layout's `usage_fields` names the record to select, the pointers to sum for input and output, the pointers that
//! compose the dedup key and the pointers for model, version and timestamp. One extractor reads them for every JSONL
//! vendor. The two number spellings the baseline readers differ on are the declared `NumberRule` and `TextRule`
//! (named compatibility rules: Claude's log takes a bool, a float or a numeric string; Codex's an integer or an
//! integer string).
use super::adapters::{CacheRule, NumberRule, TextRule, TimeRule, UsageFields};
use super::time::{iso_json_micros, iso_seconds};
use super::{text_of, Ident};
use serde_json::{json, Value};

/// What a row of a row store is to the usage scan.
pub enum Verdict {
    /// Not a usage row, or one that counts no tokens.
    Skip,
    /// The vendor has not finished the row: read it again.
    Pending,
    Usage(Usage),
}

/// Saturating token sum (a vendor file can hold any value; an overflow must never panic or wrap).
pub fn sum(parts: &[u64]) -> u64 {
    parts.iter().fold(0u64, |total, part| total.saturating_add(*part))
}

/// A cumulative counter pair as a vendor reports it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Totals {
    pub input: u64,
    pub output: u64,
}

/// A cumulative reading: the vendor's running totals and the record's own timestamp.
pub struct Reading {
    pub totals: Totals,
    pub timestamp: String,
}

/// One record's own usage (the declared `per_turn` mode).
pub struct Usage {
    /// The dedup key, composed from the declared key pointers.
    pub key: String,
    /// Input tokens including the cache portions the log splits out.
    pub input: u64,
    /// Input tokens the log counts apart from the cache portions.
    pub own_input: u64,
    pub output: u64,
    /// Cache portions (already folded into `input`), where the log declares them.
    pub cache: Option<(u64, u64)>,
    pub model: String,
    pub version: String,
    pub timestamp: String,
    /// The working directory the record names, where the declaration reads one and the record has one.
    pub cwd: Option<String>,
    /// The session the record names, where the declaration reads one and the record has one.
    pub session: Option<String>,
}

impl Usage {
    pub fn event(&self, ident: &Ident) -> Value {
        let mut event = json!({"vendor": ident.vendor, "session_id": self.session.as_deref().unwrap_or_else(|| ident.session()), "cwd": self.cwd.as_deref().unwrap_or(&ident.cwd), "file_path": ident.path,
            "input_tokens": self.input, "output_tokens": self.output, "dedup_key": self.key,
            "timestamp": self.timestamp, "model": self.model,
            // A log that names no version in its usage records may have the discovery reader's (`request: cli_version`).
            "cli_version": if self.version.is_empty() { &ident.version } else { &self.version }});
        if let (Some((read, creation)), Some(object)) = (self.cache, event.as_object_mut()) {
            object.insert("cache_read_tokens".into(), json!(read));
            object.insert("cache_creation_tokens".into(), json!(creation));
        }
        event
    }
}

/// Lenient spelling: a bool is 0/1, a number is truncated, a numeric string is parsed; anything else is 0.
fn lenient(value: &Value) -> u64 {
    match value {
        Value::Bool(v) => u64::from(*v),
        Value::Number(n) => n.as_f64().unwrap_or(0.0).max(0.0) as u64,
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0).max(0) as u64,
        _ => 0,
    }
}

/// Strict spelling: an integer or an integer string; anything else is 0.
fn strict(value: &Value) -> u64 {
    value.as_i64().or_else(|| value.as_str().and_then(|s| s.parse().ok())).unwrap_or(0).max(0) as u64
}

impl UsageFields {
    fn at<'a>(&self, record: &'a Value, pointer: &str) -> &'a Value {
        record.pointer(pointer).unwrap_or(&Value::Null)
    }

    /// A counter cell as the declaration's number rule reads it.
    pub fn count(&self, value: &Value) -> u64 {
        match self.number {
            NumberRule::Lenient => lenient(value),
            NumberRule::Strict => strict(value),
        }
    }

    fn total(&self, record: &Value, pointers: &[&str]) -> u64 {
        sum(&pointers.iter().map(|pointer| self.count(self.at(record, pointer))).collect::<Vec<_>>())
    }

    fn text(&self, value: &Value) -> String {
        match self.text {
            TextRule::Any => text_of(value),
            TextRule::String => value.as_str().map(str::to_owned).unwrap_or_default(),
        }
    }

    fn field(&self, record: &Value, pointer: Option<&str>) -> String {
        pointer.map(|pointer| self.text(self.at(record, pointer))).unwrap_or_default()
    }

    /// Is this record one the declaration selects?
    fn selects(&self, record: &Value) -> bool {
        self.select.iter().all(|rule| record.pointer(rule.pointer).and_then(Value::as_str) == Some(rule.equals))
            && self.object.is_none_or(|pointer| record.pointer(pointer).is_some_and(Value::is_object))
    }

    /// A record's own usage, or `None` for a record that is not a usage record, has no key, or counts no tokens.
    pub fn per_turn(&self, record: &Value) -> Option<Usage> {
        if !self.selects(record) {
            return None;
        }
        let parts: Vec<String> = self.key.iter().map(|pointer| self.text(self.at(record, pointer))).collect();
        if parts.iter().all(String::is_empty) {
            return None;
        }
        self.counted(record, parts.join(self.key_join))
    }

    /// A record's usage under a key the adapter composed (a log whose usage records name themselves by their place in it).
    pub fn keyed(&self, record: &Value, key: String) -> Option<Usage> {
        self.selects(record).then(|| self.counted(record, key)).flatten()
    }

    /// A row's verdict. A row store's row names itself (its dedup key), so no key pointer is read; a row the vendor has not
    /// finished is `Pending` (a selected row whose `pending_until` number is 0), never counted early.
    pub fn of_row(&self, record: &Value, key: String, pending_until: Option<&str>) -> Verdict {
        if !self.selects(record) {
            return Verdict::Skip;
        }
        if pending_until.is_some_and(|pointer| self.count(self.at(record, pointer)) == 0) {
            return Verdict::Pending;
        }
        self.counted(record, key).map_or(Verdict::Skip, Verdict::Usage)
    }

    fn counted(&self, record: &Value, key: String) -> Option<Usage> {
        let (read, creation) = (self.total(record, self.cache_read), self.total(record, self.cache_creation));
        let own_input = self.total(record, self.input);
        let input = match self.cache {
            CacheRule::Folded => sum(&[own_input, read, creation]),
            CacheRule::Separate => own_input,
        };
        let output = self.total(record, self.output);
        if input == 0 && output == 0 {
            return None;
        }
        Some(Usage {
            // A key over the bound travels as a stable digest everywhere (event, cursor, `seen`): real ids are far below it.
            key: super::bound_key(key),
            input,
            own_input,
            output,
            cache: (!self.cache_read.is_empty() || !self.cache_creation.is_empty()).then_some((read, creation)),
            model: self.field(record, self.model),
            version: self.field(record, self.version),
            timestamp: self.stamp(record),
            cwd: Some(self.field(record, self.cwd)).filter(|cwd| !cwd.is_empty()),
            session: Some(self.field(record, self.session)).filter(|session| !session.is_empty()),
        })
    }

    /// The record's timestamp as the event reports it.
    fn stamp(&self, record: &Value) -> String {
        match self.time {
            TimeRule::Text => self.field(record, self.timestamp),
            TimeRule::EpochSeconds => self.timestamp.map(|pointer| iso_seconds(self.count(self.at(record, pointer)) as f64)).unwrap_or_default(),
            TimeRule::EpochMicros => self.timestamp.map(|pointer| iso_json_micros(self.at(record, pointer))).unwrap_or_default(),
        }
    }

    /// The model a record names, as the declaration reads it (empty where it declares none).
    pub fn model_of(&self, record: &Value) -> String {
        self.field(record, self.model)
    }

    /// A record's running totals (the declared `cumulative` mode).
    pub fn reading(&self, record: &Value) -> Option<Reading> {
        self.selects(record).then(|| Reading {
            totals: Totals { input: self.total(record, self.input), output: self.total(record, self.output) },
            timestamp: self.field(record, self.timestamp),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe::adapters::find;

    fn fields(vendor: &str) -> &'static UsageFields {
        find(vendor).unwrap().layouts[0].usage_fields.as_ref().expect("a declared usage record")
    }

    #[test]
    fn token_sums_saturate() {
        assert_eq!(sum(&[u64::MAX, 5, 1]), u64::MAX);
        assert_eq!(sum(&[1, 2, 3]), 6);
    }

    #[test]
    fn claude_usage_saturates_instead_of_overflowing_and_folds_the_cache_into_the_input() {
        let record = json!({"type":"assistant","requestId":"r","message":{"id":"m","usage":{
            "input_tokens":18446744073709551615u64,"cache_read_input_tokens":5,"output_tokens":1}}});
        let usage = fields("claude").per_turn(&record).unwrap();
        assert_eq!((usage.input, usage.cache), (u64::MAX, Some((5, 0))));
        let small = json!({"type":"assistant","requestId":"r","message":{"id":"m","model":"x","usage":{
            "input_tokens":"3","cache_read_input_tokens":true,"cache_creation_input_tokens":2.9,"output_tokens":4}}});
        let usage = fields("claude").per_turn(&small).unwrap();
        assert_eq!((usage.input, usage.own_input, usage.output, usage.key.as_str(), usage.model.as_str()), (6, 3, 4, "m::r", "x"));
    }

    #[test]
    fn a_record_without_ids_tokens_or_the_selected_type_is_no_usage() {
        let claude = fields("claude");
        assert!(claude.per_turn(&json!({"type":"assistant","message":{"usage":{"input_tokens":3}}})).is_none(), "no ids");
        assert!(claude.per_turn(&json!({"type":"assistant","requestId":"r","message":{"id":"m","usage":{}}})).is_none(), "no tokens");
        assert!(claude.per_turn(&json!({"type":"user","requestId":"r","message":{"id":"m","usage":{"input_tokens":3}}})).is_none());
        assert!(claude.per_turn(&json!({"type":"assistant","requestId":"r","message":{"id":"m"}})).is_none(), "no usage object");
    }

    #[test]
    fn codex_totals_saturate_and_only_a_token_count_record_with_totals_is_a_reading() {
        let codex = fields("codex");
        let record = json!({"type":"event_msg","timestamp":"t","payload":{"type":"token_count","info":{"total_token_usage":{
            "input_tokens":9223372036854775807i64,"cached_input_tokens":9223372036854775807i64,
            "output_tokens":9223372036854775807i64,"reasoning_output_tokens":9223372036854775807i64}}}});
        assert_eq!(codex.reading(&record).unwrap().totals, Totals { input: 18446744073709551614, output: 18446744073709551614 });
        let beyond = json!({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{
            "input_tokens":"9223372036854775807","cached_input_tokens":"9223372036854775807","output_tokens":1}}}});
        assert_eq!(codex.reading(&beyond).unwrap().totals.output, 1, "an integer string past i64 is not a number");
        assert!(codex.reading(&json!({"type":"event_msg","payload":{"type":"agent_message"}})).is_none());
        assert!(codex.reading(&json!({"type":"event_msg","payload":{"type":"token_count","info":{}}})).is_none());
    }

    #[test]
    fn the_two_number_rules_differ_exactly_where_the_baselines_do() {
        assert_eq!((lenient(&json!(true)), strict(&json!(true))), (1, 0));
        assert_eq!((lenient(&json!(2.9)), strict(&json!(2.9))), (2, 0));
        assert_eq!((lenient(&json!(" 7 ")), strict(&json!(" 7 "))), (7, 0));
        assert_eq!((lenient(&json!("7")), strict(&json!("7"))), (7, 7));
        assert_eq!((lenient(&json!(-3)), strict(&json!(-3))), (0, 0));
    }
}
