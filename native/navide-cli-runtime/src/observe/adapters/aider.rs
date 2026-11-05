//! Aider's adapter: the vocabulary of Aider's Markdown chat history and nothing else.
//!
//! The history is not JSON: each line is made a record (`{"line": …}`) and the JSONL source reads it as it reads every log. The declaration
//! carries the usage (a `> Tokens:` line's two counts, the section's name and time), the modes, the compatibility rules and the Python
//! facts; the core carries the high-water mark and the reply text of the turn left open. What stays here, and why it is not data:
//! - `read_line`: a line of Markdown is a record of its own shape; an unfinished last line is not read until it is whole.
//! - `usage_view_in` and `identify`: a token line belongs to the `# aider chat started at …` header above it, which names the session and
//!   its time. The section rides on `Ident` from record to record and in every cursor; a paged replay saves it in its position
//!   (`RESUMES_IN_CONTEXT`), so the page that resumes there reads none of the bytes before it.
//! - `activity`: prompts, the output between them and the token line that ends an assistant message are Aider's own markup.
//!   Source: the retired `observe_aider.rs` (blob `5d84bc4014fdaa1a64fc63ff3a1bd9322656c30a`) lines 144-330, `cli_vendors/aider.py:391-587`.
use crate::observe::adapters::Mode;
use crate::observe::baseline::{cap_chars, decode_ignore};
use crate::observe::{activity_event, Ctx, Decoded, Ident, JsonlFile, JsonlObserver, Line, Observer, Run};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::borrow::Cow;
use std::io;

pub struct Aider;

/// The reply text a pending turn keeps (`aider.py` `_PENDING_TEXT_MAX_CHARS`).
const PENDING_TEXT_MAX_CHARS: usize = 32_000;

/// What an activity pass carries beyond the core's: the section the walk is in, whether the line before was a prompt line (consecutive
/// prompt lines are one prompt), and whether this pass has already said the agent is producing output (one event per pass).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Walk {
    session: String,
    prompt: bool,
    output: bool,
}

impl Observer for Aider {
    type State = Walk;
}

struct Section {
    slug: String,
    iso: String,
}

/// `_SECTION_RE`: `# aider chat started at YYYY-MM-DD HH:MM:SS` as a prefix of `line`.
fn section_header(line: &str, namespace: &str) -> Option<Section> {
    let rest = line.strip_prefix("# aider chat started at ")?;
    let b = rest.as_bytes();
    let digits = |from: usize, to: usize| b.get(from..to).is_some_and(|s| s.iter().all(u8::is_ascii_digit));
    let shape = digits(0, 4)
        && b.get(4) == Some(&b'-')
        && digits(5, 7)
        && b.get(7) == Some(&b'-')
        && digits(8, 10)
        && b.get(10) == Some(&b' ')
        && digits(11, 13)
        && b.get(13) == Some(&b':')
        && digits(14, 16)
        && b.get(16) == Some(&b':')
        && digits(17, 19);
    shape.then(|| Section {
        slug: format!("aider-{namespace}-{}{}{}-{}{}{}", &rest[0..4], &rest[5..7], &rest[8..10], &rest[11..13], &rest[14..16], &rest[17..19]),
        iso: format!("{}-{}-{}T{}:{}:{}", &rest[0..4], &rest[5..7], &rest[8..10], &rest[11..13], &rest[14..16], &rest[17..19]),
    })
}

/// A count group `[\d.,]+k?` (case-insensitive `k`) at the front of `s`: `(group, rest)`.
fn count_group(s: &str) -> Option<(&str, &str)> {
    let end = s.find(|c: char| !(c.is_ascii_digit() || c == '.' || c == ',')).unwrap_or(s.len());
    if end == 0 {
        return None;
    }
    let end = if s[end..].starts_with(['k', 'K']) { end + 1 } else { end };
    Some((&s[..end], &s[end..]))
}

fn strip_ci<'a>(s: &'a str, word: &str) -> Option<&'a str> {
    (s.len() >= word.len() && s.is_char_boundary(word.len()) && s[..word.len()].eq_ignore_ascii_case(word)).then(|| &s[word.len()..])
}

/// `_TOKENS_RE` on `line`: the two raw count groups, or `None`.
fn tokens_line(line: &str) -> Option<(&str, &str)> {
    let rest = line.strip_prefix('>')?.trim_start();
    let rest = strip_ci(rest, "tokens:")?.trim_start();
    let (sent, rest) = count_group(rest)?;
    let after_gap = rest.trim_start();
    if after_gap.len() == rest.len() {
        return None;
    }
    let rest = strip_ci(after_gap, "sent,")?.trim_start();
    let (received, rest) = count_group(rest)?;
    let after_gap = rest.trim_start();
    if after_gap.len() == rest.len() || strip_ci(after_gap, "received").is_none() {
        return None;
    }
    Some((sent, received))
}

/// `_parse_count`: `1,234`, `12k`, `8.5k`, `567`; anything unparsable is 0.
fn parse_count(raw: &str) -> i64 {
    let lowered = raw.trim().to_lowercase().replace(',', "");
    let (number, multiplier) = match lowered.strip_suffix('k') {
        Some(head) => (head, 1000.0),
        None => (lowered.as_str(), 1.0),
    };
    number.parse::<f64>().map_or(0, |value| ((value * multiplier).trunc() as i64).max(0))
}

/// The text of a whole line, its line end taken off: `None` for a line still being written or too long to read.
fn whole_line(line: &Line) -> Option<String> {
    (line.complete && !line.oversize).then(|| decode_ignore(&line.bytes).trim_end_matches(['\r', '\n']).to_owned())
}

impl JsonlObserver for Aider {
    fn identify(file: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident> {
        let mut ident = Ident::requested(ctx);
        let (slug, iso) = match run.mode {
            // An incremental pass goes on in the section its cursor stands in, unless it starts the log over.
            Mode::Usage if !run.rotated => (ctx.req.cursor.text("section"), ctx.req.cursor.text("section_ts")),
            // A replay that resumes after a page is in the section its position was saved in (`RESUMES_IN_CONTEXT`); it reads none of the
            // bytes before the position.
            Mode::SessionUsage => {
                let saved = file.replay_context::<Self>(&run.saved.replay);
                let text = |field: &str| saved.and_then(|context| context.get(field)).and_then(Value::as_str).unwrap_or("").to_owned();
                (text("section"), text("section_ts"))
            }
            _ => (String::new(), String::new()),
        };
        (ident.section, ident.stamp) = (slug, iso);
        Ok(ident)
    }

    const RESUMES_IN_CONTEXT: bool = true;

    fn persist(ident: &Ident, checkpoint: &mut Map<String, Value>) {
        // The cursor keeps the kind the baseline named it by, so one written by either reader is read by the other.
        checkpoint.insert("kind".into(), json!("aider-md"));
        checkpoint.insert("section".into(), json!(ident.section));
        checkpoint.insert("section_ts".into(), json!(ident.stamp));
    }

    fn read_line(line: &Line) -> Decoded {
        match whole_line(line) {
            Some(text) => Decoded::Object(json!({ "line": text })),
            None => Decoded::Unparsable,
        }
    }

    fn usage_view_in<'a>(ident: &mut Ident, record: &'a Value, _: &[u8]) -> Cow<'a, Value> {
        let text = record["line"].as_str().unwrap_or("");
        if let Some(section) = section_header(text, &ident.namespace) {
            (ident.section, ident.stamp) = (section.slug, section.iso);
            return Cow::Owned(Value::Null);
        }
        match tokens_line(text) {
            // A token line before any header belongs to no session.
            Some((sent, received)) if !ident.section.is_empty() => {
                Cow::Owned(json!({"kind": "tokens", "input": parse_count(sent), "output": parse_count(received), "timestamp": ident.stamp, "session": ident.section}))
            }
            _ => Cow::Owned(Value::Null),
        }
    }

    /// A token line names itself by its place: the line in a replay, the offset after it in an incremental pass.
    fn position_key(ident: &Ident, replay: bool, line: u64, end: u64, _: &Value) -> Option<String> {
        Some(if replay { format!("aider::{}::L{line}", ident.section) } else { format!("aider::{}::@{end}", ident.section) })
    }

    fn activity(last_text: &mut String, walk: &mut Walk, ident: &Ident, record: &Value, line: u64, out: &mut Vec<Value>) {
        let text = record["line"].as_str().unwrap_or("");
        if let Some(section) = section_header(text, &ident.namespace) {
            walk.session = section.slug;
            walk.prompt = false;
            last_text.clear();
            return;
        }
        if text.trim().is_empty() {
            return;
        }
        let mut emit = |kind: &str, key: String, detail: &str, body: String| {
            let mut event = activity_event(ident, "", kind, key, detail, body);
            event["session_id"] = json!(walk.session);
            out.push(event);
        };
        if let Some(prompt) = text.strip_prefix("#### ") {
            last_text.clear();
            if !walk.prompt {
                // The first line of a (possibly multi-line) prompt; the frontend names the pane from it.
                emit("agent_active", format!("act:{line}"), "prompt", prompt.to_owned());
            }
            walk.prompt = true;
            return;
        }
        walk.prompt = false;
        if tokens_line(text).is_some() {
            emit("turn_complete", format!("turn:{line}"), "usage", cap_chars(last_text, PENDING_TEXT_MAX_CHARS));
            last_text.clear();
            return;
        }
        if !text.starts_with('>') {
            *last_text = cap_chars(&format!("{last_text}{text}\n"), PENDING_TEXT_MAX_CHARS);
        }
        if !walk.output {
            emit("agent_active", format!("act:{line}"), "output", String::new());
            walk.output = true;
        }
    }

    /// Plain output coalesces into one `output` event per pass: the next pass says it again.
    fn settle(_: &mut String, walk: &mut Walk, _: &Ident, _: &mut Vec<Value>) {
        walk.output = false;
    }
}

#[cfg(test)]
mod tests {
    use crate::observe::parity::{self, Log};
    use serde_json::{json, Value};

    // The line shapes are the Python reader's own: the `# aider chat started at …` header and the `> Tokens:` line are `cli_vendors/aider.py:77-83`
    // (`_SECTION_RE`, `_TOKENS_RE`), a `#### ` prompt line is `:562`; the histories are `backend/tests/vendors/test_aider.py:28-47`
    // (`_SECTION_1`, `_SECTION_2`) and the activity walk `:282-303`.
    const LOG: &str = "proj/.aider.chat.history.md";

    fn scenario() -> Log<'static> {
        let mut log = Log::new("aider", LOG, json!({"cwd": "/w", "namespace": "ns"}));
        log.expects("", &["section", "section_ts"])
            .legacy_lacks_activity_flags()
            .core_adds_to_events(&[("cli_version", json!("")), ("model", json!(""))])
            .legacy_kept_no_key_window();
        // A token line before any header belongs to no session; a header that is no header is text; two prompt lines are one prompt.
        log.append_text(concat!(
            "> Tokens: 9 sent, 9 received.\n",
            "# aider chat started at 2024-5-1 10:00:00\n",
            "# aider chat started at 2024-05-01 10:00:00\n\n",
            "> Model: gpt-4\n",
            "#### first line\n",
            "#### second line\n",
            "Hello\n",
            "world\n",
            "> Applied edit to a.py\n",
            "> Tokens: 12k sent, 1.2k received. Cost: $0.01 message, $0.01 session.\n\n"
        ))
        .passes("first");
        // Another section, a count that is nothing, counts spelled every way, a CRLF line, and a spelling of the keyword in other case.
        log.append_text(concat!(
            "# aider chat started at 2024-05-01 11:30:00\n",
            "> Tokens: 5 sent, 0 received.\n",
            "> Tokens: 0 sent, 0 received.\n",
            "#### again\r\n",
            "an answer\r\n",
            "> Tokens: 1,234 sent, 8.5K received.\n",
            ">tokens:  7 SENT,8 Received\n"
        ))
        .passes("more")
        .passes("nothing new");
        // A reply still being built rides to the next pass; a rewritten file starts over.
        log.append_text("#### question\nstill typing\nmore text\n").passes("open reply");
        log.replaced("replaced");
        // An unterminated last line is read again once it is whole.
        log.append_text("> Tokens: 3 sen").passes("half");
        log.append_text("t, 4 received.\n").passes("whole");
        log
    }

    /// The differences that are Aider's own, on top of the shared ones (`parity::legacy_shape` / `shape_log`, and what the scenario names:
    /// the model and CLI version the core's usage events carry, the window of keys its cursors keep, the activity flags): the activity events
    /// carry a time stamp (empty: a Markdown line has none) and the answer names the reply text so far as `pending`.
    fn own(mode: &str, object: &mut serde_json::Map<String, Value>) {
        // The section the walk is in is the core's own state; the baseline re-read the log from its first line instead of keeping it.
        object["seen"].as_array_mut().unwrap().retain(|key| !key.as_str().unwrap().starts_with("aider_walk::"));
        if mode == "activity" {
            for event in object["events"].as_array_mut().unwrap() {
                assert_eq!(event.as_object_mut().unwrap().remove("timestamp"), Some(json!("")));
            }
            // The baseline answered the reply text so far as `pending`; the core's flags say what the baseline had no notion of.
            let pending = object.remove("last_text").unwrap();
            object.insert("pending".into(), pending);
        }
    }

    #[test]
    fn the_core_answers_every_pass_of_the_scenario_with_the_legacy_observers_complete_document_after_the_named_differences() {
        parity::check_log_golden("aider", &scenario(), &[], |_, mode, object, _| own(mode, object));
    }
}
