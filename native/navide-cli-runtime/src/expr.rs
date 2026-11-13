//! Evaluation of the declaration's record-access language (`Pred`, `Text`,
//! `Num`) over one JSON record. Semantics follow the Python readers exactly
//! where they matter: `str(x or "")`, `max(0, int(x))`, `str.strip()`.
use crate::decl::{Num, Path, Pred, StrTest, Text};
use crate::time;
use serde_json::Value;
use std::collections::BTreeMap;

pub type Slots = BTreeMap<String, String>;

/// Everything an expression can see.
pub struct Scope<'a> {
    pub record: &'a Value,
    pub line: usize,
    pub offset: u64,
    pub slots: &'a Slots,
    pub cancelled: bool,
    pub entry_key: &'a str,
}

impl<'a> Scope<'a> {
    pub fn with_record<'b>(&'b self, record: &'b Value) -> Scope<'b> {
        Scope { record, line: self.line, offset: self.offset, slots: self.slots, cancelled: self.cancelled, entry_key: self.entry_key }
    }
}

pub fn get<'v>(value: &'v Value, path: Path) -> Option<&'v Value> {
    let mut node = value;
    for key in path {
        node = node.as_object()?.get(*key)?;
    }
    Some(node)
}

/// Python truthiness.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python `str(value or "")`.
pub fn py_str(value: &Value) -> String {
    if !truthy(value) {
        return String::new();
    }
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(_) => "True".to_owned(),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => i.to_string(),
            (_, Some(u)) => u.to_string(),
            _ => {
                let f = n.as_f64().unwrap_or(0.0);
                if f.fract() == 0.0 && f.abs() < 1e16 { format!("{f:.1}") } else { f.to_string() }
            }
        },
        other => other.to_string(),
    }
}

pub fn py_str_opt(value: Option<&Value>) -> String {
    value.map(py_str).unwrap_or_default()
}

/// Python `int(value)`, `None` where it raises.
pub fn py_int_raw(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_u64().map(|u| u.min(i64::MAX as u64) as i64))
            .or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => {
            let t = py_strip(s).replace('_', "");
            let digits = t.strip_prefix(['+', '-']).unwrap_or(&t);
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) || s.contains("__") {
                return None;
            }
            t.parse::<i64>().ok()
        }
        _ => None,
    }
}

/// Python `max(0, int(value))`, 0 where `int` raises.
pub fn py_int(value: Option<&Value>) -> i64 {
    value.and_then(py_int_raw).unwrap_or(0).max(0)
}

/// `usage_common._num`: `float(v)` except bools and None.
pub fn py_float(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => py_strip(s).parse::<f64>().ok(),
        _ => None,
    }
}

fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// Python `str.strip()`.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

pub fn cap(text: &str, max: usize) -> String {
    let count = text.chars().count();
    if count <= max {
        return text.to_owned();
    }
    let half = max / 2;
    let head: String = text.chars().take(half).collect();
    let tail: String = text.chars().skip(count - half).collect();
    format!("{head}\n…\n{tail}")
}

pub const USER_PROMPT_MAX_CHARS: usize = 500;

/// `log_readers.base.user_prompt_text`.
pub fn user_prompt(raw: &str) -> String {
    let text = py_strip(raw);
    if text.is_empty() || text.starts_with('<') {
        return String::new();
    }
    text.chars().take(USER_PROMPT_MAX_CHARS).collect()
}

/// `log_readers.base.join_text_blocks`.
pub fn join_blocks(content: Option<&Value>, block_type: &str) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(blocks)) => blocks
            .iter()
            .filter(|b| b.get("type").and_then(Value::as_str) == Some(block_type))
            .map(|b| py_str_opt(b.get("text")))
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn tag_values(text: &str, tag: &str, first_only: bool) -> Vec<String> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(found) = text[from..].find(&open) {
        let start = from + found + open.len();
        let len = text[start..].find('<').unwrap_or(text.len() - start);
        if len > 0 && text[start + len..].starts_with(&close) {
            out.push(text[start..start + len].to_owned());
            if first_only {
                break;
            }
            from = start + len + close.len();
        } else {
            from += found + 1;
        }
    }
    out
}

fn str_test(value: &str, test: StrTest) -> bool {
    match test {
        StrTest::Eq(s) => value == s,
        StrTest::Ne(s) => value != s,
        StrTest::In(set) => set.contains(&value),
        StrTest::NotIn(set) => !set.contains(&value),
        StrTest::StartsWith(s) => value.starts_with(s),
        StrTest::Contains(s) => value.contains(s),
        StrTest::NonEmpty => !value.is_empty(),
        StrTest::Empty => value.is_empty(),
    }
}

fn content_tree(value: Option<&Value>, test: StrTest) -> bool {
    match value {
        Some(Value::String(s)) => str_test(s, test),
        Some(Value::Array(blocks)) => blocks.iter().filter(|b| b.is_object()).any(|b| {
            let child = if b.get("type").and_then(Value::as_str) == Some("text") { b.get("text") } else { b.get("content") };
            content_tree(child, test)
        }),
        _ => false,
    }
}

pub fn pred(p: &Pred, s: &Scope) -> bool {
    match p {
        Pred::True => true,
        Pred::Eq(path, lit) => get(s.record, path).and_then(Value::as_str) == Some(*lit),
        Pred::In(path, set) => get(s.record, path).and_then(Value::as_str).is_some_and(|v| set.contains(&v)),
        Pred::Has(path) => get(s.record, path).is_some(),
        Pred::Truthy(path) => get(s.record, path).is_some_and(truthy),
        Pred::IsObject(path) => get(s.record, path).is_some_and(Value::is_object),
        Pred::IsList(path) => get(s.record, path).is_some_and(Value::is_array),
        Pred::IsString(path) => get(s.record, path).is_some_and(Value::is_string),
        Pred::Text(t, test) => str_test(&text(t, s), *test),
        Pred::AnyItem(path, inner) => get(s.record, path)
            .and_then(Value::as_array)
            .is_some_and(|items| items.iter().any(|item| pred(inner, &s.with_record(item)))),
        Pred::ContentTree(path, test) => content_tree(get(s.record, path), *test),
        Pred::TurnCancelled => s.cancelled,
        Pred::SlotSet(name) => s.slots.get(*name).is_some_and(|v| !v.is_empty()),
        Pred::All(ps) => ps.iter().all(|p| pred(p, s)),
        Pred::Any(ps) => ps.iter().any(|p| pred(p, s)),
        Pred::Not(p) => !pred(p, s),
    }
}

pub fn text(t: &Text, s: &Scope) -> String {
    match t {
        Text::Lit(l) => (*l).to_owned(),
        Text::Py(path) => py_str_opt(get(s.record, path)),
        Text::Str(path) => get(s.record, path).and_then(Value::as_str).unwrap_or_default().to_owned(),
        Text::Slot(name) => s.slots.get(*name).cloned().unwrap_or_default(),
        Text::FirstOf(options) => options.iter().map(|o| text(o, s)).find(|v| !v.is_empty()).unwrap_or_default(),
        Text::Concat(parts) => parts.iter().map(|p| text(p, s)).collect(),
        Text::Blocks(path, kind) => join_blocks(get(s.record, path), kind),
        Text::Join { list, filter, item, sep, keep_empty } => get(s.record, list)
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|element| {
                        let scope = s.with_record(element);
                        pred(filter, &scope).then(|| text(item, &scope))
                    })
                    .filter(|v| *keep_empty || !v.is_empty())
                    .collect::<Vec<_>>()
                    .join(sep)
            })
            .unwrap_or_default(),
        Text::TagValues(inner, tag) => tag_values(&text(inner, s), tag, false).join("\n"),
        Text::TagFirst(inner, tag) => tag_values(&text(inner, s), tag, true).pop().unwrap_or_default(),
        Text::AfterPrefix(inner, prefix) => {
            let value = text(inner, s);
            value
                .strip_prefix(prefix)
                .map(|rest| rest.split(py_space).next().unwrap_or_default().to_owned())
                .unwrap_or_default()
        }
        Text::UserPrompt(inner) => user_prompt(&text(inner, s)),
        Text::Strip(inner) => py_strip(&text(inner, s)).to_owned(),
        Text::Cap(inner, max) => cap(&text(inner, s), *max),
        Text::If(p, inner) => if pred(p, s) { text(inner, s) } else { String::new() },
        Text::Num(n) => num(n, s).map(|v| v.to_string()).unwrap_or_default(),
        Text::Millis(path) => get(s.record, path).and_then(py_int_raw).map(|v| v.to_string()).unwrap_or_default(),
        Text::IsoFromEpoch(ms_path, s_path) => {
            let seconds = match get(s.record, ms_path).filter(|v| !v.is_null()) {
                Some(ms) => py_float(Some(ms)).unwrap_or(0.0) / 1000.0,
                None => py_float(get(s.record, s_path)).unwrap_or(0.0),
            };
            if seconds == 0.0 { String::new() } else { time::iso_truncated_ms(seconds) }
        }
        Text::FirstKey(path) => get(s.record, path)
            .and_then(Value::as_object)
            .and_then(|o| o.keys().next().cloned())
            .unwrap_or_default(),
        Text::Line => s.line.to_string(),
        Text::Offset => s.offset.to_string(),
        Text::EntryKey => s.entry_key.to_owned(),
    }
}

/// `None` is "this record carries no reading".
pub fn num(n: &Num, s: &Scope) -> Option<i64> {
    match n {
        Num::Zero => Some(0),
        Num::Of(path) => Some(py_int(get(s.record, path))),
        Num::Sum(parts) => parts.iter().try_fold(0i64, |acc, p| Some(acc.saturating_add(num(p, s)?))),
        Num::Sub(a, rest) => {
            let base = num(a, s)?;
            let minus = rest.iter().try_fold(0i64, |acc, p| Some(acc.saturating_add(num(p, s)?)))?;
            Some((base - minus).max(0))
        }
        Num::MapSum { map, object, field } => {
            let entries = get(s.record, map)?.as_object()?;
            let mut total = 0i64;
            let mut usable = false;
            for value in entries.values() {
                if let Some(inner) = get(value, object).filter(|v| v.is_object()) {
                    usable = true;
                    total = total.saturating_add(py_int(get(inner, field)));
                }
            }
            usable.then_some(total)
        }
        Num::Case(cases) => cases.iter().find(|(p, _)| pred(p, s)).and_then(|(_, n)| num(n, s)),
        Num::Required(path, inner) => {
            get(s.record, path).filter(|v| v.is_object())?;
            num(inner, s)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_scalar_semantics() {
        assert_eq!(py_str(&json!(0)), "");
        assert_eq!(py_str(&json!(5)), "5");
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&json!(2.0)), "2.0");
        assert_eq!(py_int(Some(&json!(" 12 "))), 12);
        assert_eq!(py_int(Some(&json!(-3))), 0);
        assert_eq!(py_int(Some(&json!(3.9))), 3);
        assert_eq!(py_int(Some(&json!("1.5"))), 0);
        assert_eq!(py_int(Some(&json!(null))), 0);
        assert_eq!(py_strip("\u{1c} a \n"), "a");
    }

    #[test]
    fn tags_and_prefixes() {
        let s = "<task-notification><task-id>a1</task-id><task-id></task-id><task-id>b2</task-id><status>done</status>";
        assert_eq!(tag_values(s, "task-id", false), ["a1", "b2"]);
        assert_eq!(tag_values(s, "status", true), ["done"]);
        assert_eq!(cap(&"x".repeat(10), 4), "xx\n…\nxx");
        assert_eq!(user_prompt("  <cmd>  "), "");
        assert_eq!(user_prompt(" hi "), "hi");
    }
}
