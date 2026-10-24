//! Shared tail reading for the per-session JSONL observers (G2 ticket 06, family A).
//!
//! Mirrors `log_readers/base.py::read_jsonl_tail`: whole `\n`-terminated lines from a saved byte offset, a partial
//! last line left unread, an unparsable complete line recorded as `None`, and a restart at 0 when the file identity
//! changed, shrank below the offset, or its tail anchor no longer matches. The blake2b anchor itself stays in
//! Python: this module returns the raw anchor bytes (E1 14) and checks a mismatch against the request blob.
use serde_json::{json, Map, Value};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::os::unix::fs::MetadataExt;

const ANCHOR_BYTES: u64 = 512;

pub fn text(v: &Value) -> String {
    v.as_str().map(str::to_owned).unwrap_or_default()
}

/// Python `str(v or "")`.
pub fn str_or_empty(v: &Value) -> String {
    match v {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),
        Value::Array(a) if a.is_empty() => String::new(),
        Value::Object(o) if o.is_empty() => String::new(),
        other => other.to_string(),
    }
}

const TEXT_MAX_CHARS: usize = 4_000;

/// The readers' shared 4 000-character cap: head, an ellipsis line, tail.
pub fn cap_text(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= TEXT_MAX_CHARS {
        return value.to_owned();
    }
    let half = TEXT_MAX_CHARS / 2;
    format!(
        "{}\n…\n{}",
        chars[..half].iter().collect::<String>(),
        chars[chars.len() - half..].iter().collect::<String>()
    )
}

/// `log_readers.base.join_text_blocks(content, "text")`.
pub fn join_text_blocks(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter(|b| b.is_object() && b["type"] == "text")
            .map(|b| str_or_empty(&b["text"]))
            .filter(|p| !p.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

/// Python `max(0, int(v))` with a `0` for anything `int()` rejects.
pub fn py_int(v: &Value) -> i64 {
    match v {
        Value::Bool(b) => i64::from(*b),
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f.trunc() as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    }
    .max(0)
}

/// Python `_num`: a float from an int, float or numeric string; `None` for a bool or anything else.
pub fn py_num(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

/// `time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".mmmZ"`; `""` for a zero stamp.
pub fn iso_utc(seconds: f64) -> String {
    if seconds == 0.0 {
        return String::new();
    }
    let whole = seconds.floor();
    let millis = ((seconds - whole) * 1000.0) as i64;
    format!("{}.{millis:03}Z", utc_fields(whole as i64))
}

/// `YYYY-MM-DDTHH:MM:SS` of a whole-second Unix time (UTC).
pub fn utc_fields(whole: i64) -> String {
    let (days, rest) = (whole.div_euclid(86_400), whole.rem_euclid(86_400));
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// Keys of the object found by walking `path` from the top-level object of `raw`, in FILE order.
/// `serde_json::Value` sorts object keys, and Python keeps insertion order, so anything that takes "the first key"
/// (grok's model name) reads the order from the raw line.
pub fn ordered_keys(raw: &[u8], path: &[&str]) -> Vec<String> {
    let mut at = 0;
    for wanted in path {
        skip_ws(raw, &mut at);
        if raw.get(at) != Some(&b'{') {
            return Vec::new();
        }
        at += 1;
        loop {
            skip_ws(raw, &mut at);
            if raw.get(at) == Some(&b'}') || at >= raw.len() {
                return Vec::new();
            }
            let Some(key) = string_at(raw, &mut at) else {
                return Vec::new();
            };
            skip_ws(raw, &mut at);
            if raw.get(at) != Some(&b':') {
                return Vec::new();
            }
            at += 1;
            skip_ws(raw, &mut at);
            if key == *wanted {
                break;
            }
            if !skip_value(raw, &mut at) {
                return Vec::new();
            }
            skip_ws(raw, &mut at);
            if raw.get(at) == Some(&b',') {
                at += 1;
            }
        }
    }
    list_keys(raw, at)
}

fn list_keys(raw: &[u8], mut at: usize) -> Vec<String> {
    skip_ws(raw, &mut at);
    if raw.get(at) != Some(&b'{') {
        return Vec::new();
    }
    at += 1;
    let mut keys = Vec::new();
    loop {
        skip_ws(raw, &mut at);
        if raw.get(at) == Some(&b'}') || at >= raw.len() {
            return keys;
        }
        let Some(key) = string_at(raw, &mut at) else {
            return keys;
        };
        skip_ws(raw, &mut at);
        if raw.get(at) != Some(&b':') {
            return keys;
        }
        at += 1;
        skip_ws(raw, &mut at);
        if !skip_value(raw, &mut at) {
            return keys;
        }
        keys.push(key);
        skip_ws(raw, &mut at);
        if raw.get(at) == Some(&b',') {
            at += 1;
        }
    }
}

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
            while raw
                .get(*at)
                .is_some_and(|b| !matches!(b, b',' | b'}' | b']') && !b.is_ascii_whitespace())
            {
                *at += 1;
            }
            true
        }
        None => false,
    }
}

pub struct Tail {
    /// `(end offset, value)`; `None` for a complete line that is not a JSON object.
    pub records: Vec<(u64, Option<Value>, Vec<u8>)>,
    pub checkpoint: Map<String, Value>,
    pub rotated: bool,
    pub identity: String,
    pub raw_anchor: Vec<u8>,
}

fn anchor_bytes(file: &mut File, end: u64) -> io::Result<Vec<u8>> {
    let start = end.saturating_sub(ANCHOR_BYTES);
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = vec![0; (end - start) as usize];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}

/// `read_jsonl_tail`. `checkpoint` is the saved cursor (an object or null); `expected` is the anchor request blob.
pub fn read_tail(
    path: &str,
    args: &Value,
    checkpoint: &Value,
    expected: &[u8],
) -> io::Result<Tail> {
    let mut file = File::open(path)?;
    let stat = file.metadata()?;
    let identity = format!("{}:{}", stat.dev(), stat.ino());
    let prior_identity = text(&checkpoint["identity"]);
    let offset = py_int(&checkpoint["offset"]) as u64;
    let mut rotated = offset > 0 && (prior_identity != identity || stat.len() < offset);
    if offset > 0
        && !rotated
        && (args["anchor_invalid"] == true
            || (!expected.is_empty() && anchor_bytes(&mut file, offset)? != expected))
    {
        rotated = true;
    }
    let start = if rotated { 0 } else { offset };
    let mut committed = start;
    file.seek(SeekFrom::Start(start))?;
    let mut reader = BufReader::new(file.try_clone()?);
    let mut records = Vec::new();
    loop {
        let mut raw = Vec::new();
        if reader.read_until(b'\n', &mut raw)? == 0 {
            break;
        }
        if !raw.ends_with(b"\n") {
            break;
        }
        committed += raw.len() as u64;
        let value = serde_json::from_slice::<Value>(&raw)
            .ok()
            .filter(Value::is_object);
        records.push((committed, value, raw));
    }
    let raw_anchor = if committed > 0 {
        anchor_bytes(&mut file, committed)?
    } else {
        Vec::new()
    };
    let mut next = checkpoint.as_object().cloned().unwrap_or_default();
    next.insert("kind".into(), json!("jsonl"));
    next.insert("offset".into(), json!(committed));
    next.insert("identity".into(), json!(identity));
    next.insert("anchor".into(), json!(""));
    Ok(Tail {
        records,
        checkpoint: next,
        rotated,
        identity,
        raw_anchor,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_matches_python_gmtime_with_millis() {
        assert_eq!(iso_utc(1_789_379_453.5), "2026-09-14T09:50:53.500Z");
        // The truncation of a float like x.001 to .000 is Python's too (`int((s % 1) * 1000)`).
        assert_eq!(iso_utc(1_789_379_453.001), "2026-09-14T09:50:53.000Z");
        assert_eq!(iso_utc(0.0), "");
        assert_eq!(iso_utc(951_782_400.0), "2000-02-29T00:00:00.000Z");
    }

    #[test]
    fn ordered_keys_keep_file_order_not_sorted_order() {
        let raw = br#"{"params":{"x":1,"update":{"usage":{"modelUsage":{"zeta":{"a":1},"alpha":{"b":[1,{"c":"}"}]}}}}}"#;
        assert_eq!(
            ordered_keys(raw, &["params", "update", "usage", "modelUsage"]),
            vec!["zeta".to_string(), "alpha".to_string()]
        );
        assert!(ordered_keys(raw, &["params", "missing"]).is_empty());
    }

    #[test]
    fn py_int_follows_python_int_semantics() {
        assert_eq!(py_int(&json!("12")), 12);
        assert_eq!(py_int(&json!(1.9)), 1);
        assert_eq!(py_int(&json!(-5)), 0);
        assert_eq!(py_int(&json!(null)), 0);
        assert_eq!(py_int(&json!(true)), 1);
    }
}
