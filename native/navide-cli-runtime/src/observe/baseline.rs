//! The Python readers' spellings that more than one adapter shares: what `str(v or "")`, `join_text_blocks` and `_num` made of a JSON
//! value. An adapter takes these where its baseline did, so the same value reads the same in every vendor.
use serde_json::Value;

/// Python `str(v or "")`: an empty or falsy value is `""`, any other non-string is its JSON text.
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

/// `log_readers.base.join_text_blocks(content, "text")`: a string as it is, else the non-empty `text` of the `text` blocks, one per line
/// (no stripping, unlike `text_blocks`).
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

/// Python `max(0, int(v))` for the values the readers' `_int` takes: a bool, a number (truncated), an integer string; anything else is 0.
pub fn py_int(v: &Value) -> i64 {
    match v {
        Value::Bool(b) => i64::from(*b),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)).unwrap_or(0),
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    }
    .max(0)
}

/// Python `str(int(v))`, or `""` where `int(v)` fails (nothing is clamped).
pub fn int_text(v: &Value) -> String {
    match v {
        Value::Bool(b) => i64::from(*b).to_string(),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)).map(|n| n.to_string()).unwrap_or_default(),
        Value::String(s) => s.trim().parse::<i64>().map(|n| n.to_string()).unwrap_or_default(),
        _ => String::new(),
    }
}

const TEXT_MAX_CHARS: usize = 4_000;

/// The readers' shared `_cap_text`: over 4,000 characters, the first and last 2,000 around an ellipsis line.
pub fn cap_text(value: &str) -> String {
    cap_chars(value, TEXT_MAX_CHARS)
}

/// `cap_text` for a reader whose own cap is another size: over `max` characters, the first and last half around an ellipsis line.
pub fn cap_chars(value: &str, max: usize) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= max {
        return value.to_owned();
    }
    let half = max / 2;
    format!("{}\n…\n{}", chars[..half].iter().collect::<String>(), chars[chars.len() - half..].iter().collect::<String>())
}

/// Python `_num(v)` for the values a timestamp holds: a number, or a numeric string; anything else is not a number.
pub fn py_num(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

/// Python `bytes.decode("utf-8", errors="ignore")`: the bytes that are not valid UTF-8 are dropped.
pub fn decode_ignore(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(valid) => {
                out.push_str(valid);
                return out;
            }
            Err(error) => {
                let (valid, after) = rest.split_at(error.valid_up_to());
                out.push_str(std::str::from_utf8(valid).unwrap_or_default());
                match error.error_len() {
                    Some(skip) => rest = &after[skip..],
                    None => return out,
                }
            }
        }
    }
}

/// What a log's checkpoint kept of the facts its first pass resolved (Kimi's working directory; Pi's session and working directory): a
/// pass that goes on in the same log (`rotated` is false) keeps them, when the checkpoint holds one, instead of asking the request again.
/// `session` says the kept session leads: Pi keeps the working directory only along with it.
pub fn keep_resolved(ident: &mut super::Ident, cursor: &super::Cursor, rotated: bool, session: bool) {
    let lead = cursor.kept(if session { "session_id" } else { "cwd" });
    if rotated || lead.is_empty() {
        return;
    }
    if session {
        ident.stem = lead;
        ident.cwd = cursor.kept("cwd");
    } else {
        ident.cwd = lead;
    }
}

/// A turn no record ends (Pi's and Qwen's logs have no end-of-turn record): the next prompt closes it, and the latest one closes when the
/// file has been quiet for eight seconds. Its number, whether it has been closed, and the timestamp of its latest entry (the event that
/// closes it reports that one).
#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct QuietTurn {
    pub(in crate::observe) idx: i64,
    #[serde(default)]
    pub(in crate::observe) flushed: bool,
    #[serde(default)]
    pub(in crate::observe) ts: String,
}

/// The file must have been quiet this long (the file's modification time against the wall clock the request names).
const QUIET_MS: i64 = 8_000;

/// A stamp as the state keeps it: a log's own text (a record's `timestamp` may be any size), capped like every identity field the log
/// names (`FIELD_CHARS`, head and tail kept), so the state the pass saves stays inside the share the answer reserves for it.
fn bounded_stamp(ts: &str) -> String {
    super::cap_head_tail(ts, super::answer::FIELD_CHARS)
}

impl QuietTurn {
    /// The turn after `state`'s (or the first), opened by an entry stamped `ts`.
    pub fn after(state: &Option<QuietTurn>, ts: &str) -> Self {
        Self { idx: state.as_ref().map_or(0, |turn| turn.idx + 1), flushed: false, ts: bounded_stamp(ts) }
    }

    /// A prompt entry stamped `ts`: it closes the turn before it, if that is still open (`boundary`, with the reply text so far), and
    /// begins the next.
    pub fn prompt(state: &mut Option<QuietTurn>, ident: &super::Ident, ts: &str, last_text: &mut String, out: &mut Vec<Value>) {
        if let Some(open) = state.as_ref().filter(|_| Self::is_open(state)) {
            out.push(open.closing(ident, "boundary", std::mem::take(last_text)));
        }
        *state = Some(Self::after(state, ts));
    }

    /// An assistant entry stamped `ts` holding `text`: it opens a turn when none is open (a resumed session joined mid-turn), is the
    /// turn's latest entry, and its text, trimmed and capped, when it has any, is the reply so far.
    pub fn reply(state: &mut Option<QuietTurn>, ts: &str, text: &str, last_text: &mut String) {
        if !Self::is_open(state) {
            *state = Some(Self::after(state, ts));
        }
        if let Some(open) = state.as_mut() {
            open.touch(ts);
        }
        let reply = text.trim();
        if !reply.is_empty() {
            *last_text = cap_text(reply);
        }
    }

    pub fn is_open(state: &Option<QuietTurn>) -> bool {
        state.as_ref().is_some_and(|turn| !turn.flushed)
    }

    /// An entry of the turn: it is the turn's latest.
    pub fn touch(&mut self, ts: &str) {
        self.ts = bounded_stamp(ts);
    }

    /// The `turn_complete` event that closes the turn, carrying the reply text.
    pub fn closing(&self, ident: &super::Ident, detail: &str, text: String) -> Value {
        super::activity_event(ident, &self.ts, "turn_complete", format!("turn:{}", self.idx), detail, text)
    }

    /// An open turn closes when the file has been quiet long enough (a file whose modification time cannot be read has not been quiet).
    pub fn settle(last_text: &mut String, state: &mut Option<QuietTurn>, ident: &super::Ident, out: &mut Vec<Value>) {
        let Some(open) = state.as_mut().filter(|turn| !turn.flushed) else {
            return;
        };
        if ident.modified_ms.is_some_and(|modified| ident.now_ms - modified >= QUIET_MS) {
            out.push(open.closing(ident, "idle", std::mem::take(last_text)));
            open.flushed = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn invalid_utf8_is_dropped_as_pythons_ignore_does() {
        assert_eq!(decode_ignore(b"a\xffb\xe2\x82c\xe2\x82\xac"), "abc€");
        assert_eq!(decode_ignore(b"tail\xe2\x82"), "tail", "a sequence the bytes end in the middle of is dropped");
    }

    #[test]
    fn a_falsy_value_is_empty_and_text_blocks_join_without_stripping() {
        assert_eq!([json!(null), json!(false), json!(0), json!([]), json!({})].iter().map(str_or_empty).collect::<Vec<_>>(), [""; 5]);
        assert_eq!(str_or_empty(&json!(7)), "7");
        let content = json!([{"type": "text", "text": " a "}, {"type": "image"}, {"type": "text", "text": ""}, {"type": "text", "text": "b"}]);
        assert_eq!(join_text_blocks(&content), " a \nb");
        assert_eq!(join_text_blocks(&json!("plain")), "plain");
    }
}
