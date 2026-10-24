//! Aider observation, derived from `cli_vendors/aider.py` (G2 ticket 06, family C).
//!
//! Aider's history is Markdown: a `# aider chat started at …` header opens a section and a `> Tokens: … sent, …
//! received` line closes each assistant message. Python keeps discovery, the per-file namespace (`namespace`, the
//! pane token or a path hash) and the user-prompt wrapper stripping; this module reads the lines.
use super::observe_jsonl::{py_int, read_tail, text};
use serde_json::{json, Map, Value};
use std::fs;
use std::io;

const PENDING_TEXT_MAX_CHARS: usize = 32_000;

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
        slug: format!(
            "aider-{namespace}-{}{}{}-{}{}{}",
            &rest[0..4],
            &rest[5..7],
            &rest[8..10],
            &rest[11..13],
            &rest[14..16],
            &rest[17..19]
        ),
        iso: format!(
            "{}-{}-{}T{}:{}:{}",
            &rest[0..4],
            &rest[5..7],
            &rest[8..10],
            &rest[11..13],
            &rest[14..16],
            &rest[17..19]
        ),
    })
}

/// A count group `[\d.,]+k?` (case-insensitive `k`) at the front of `s`: `(group, rest)`.
fn count_group(s: &str) -> Option<(&str, &str)> {
    let end = s
        .find(|c: char| !(c.is_ascii_digit() || c == '.' || c == ','))
        .unwrap_or(s.len());
    if end == 0 {
        return None;
    }
    let end = if s[end..].starts_with(['k', 'K']) { end + 1 } else { end };
    Some((&s[..end], &s[end..]))
}

fn strip_ci<'a>(s: &'a str, word: &str) -> Option<&'a str> {
    (s.len() >= word.len() && s.is_char_boundary(word.len()) && s[..word.len()].eq_ignore_ascii_case(word))
        .then(|| &s[word.len()..])
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
    number
        .parse::<f64>()
        .map_or(0, |value| ((value * multiplier).trunc() as i64).max(0))
}

/// Python `bytes.decode("utf-8", errors="ignore")`.
fn decode_ignore(bytes: &[u8]) -> String {
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

/// A text-mode read with universal newlines: `\r\n` and a lone `\r` both become `\n`.
fn universal_lines(bytes: &[u8]) -> Vec<String> {
    let decoded = decode_ignore(bytes).replace("\r\n", "\n").replace('\r', "\n");
    decoded.split_inclusive('\n').map(str::to_owned).collect()
}

fn cap_text(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= PENDING_TEXT_MAX_CHARS {
        return value.to_owned();
    }
    let half = PENDING_TEXT_MAX_CHARS / 2;
    format!(
        "{}\n…\n{}",
        chars[..half].iter().collect::<String>(),
        chars[chars.len() - half..].iter().collect::<String>()
    )
}

pub fn observe(args: &Value, expected: &[u8]) -> io::Result<(Value, Option<Vec<u8>>)> {
    let path = args["path"]
        .as_str()
        .ok_or_else(|| io::Error::other("path required"))?;
    let mode = args["mode"].as_str().unwrap_or("");
    if !matches!(mode, "usage" | "session_usage" | "activity") {
        return Err(io::Error::other("Aider observation mode not integrated"));
    }
    let (cwd, namespace) = (text(&args["cwd"]), text(&args["namespace"]));
    let usage_event = |section: &Section, input: i64, output: i64, key: String, checkpoint: Option<Value>| {
        let mut event = json!({"vendor":"aider","input_tokens":input,"output_tokens":output,"cwd":cwd,
            "session_id":section.slug,"file_path":path,"dedup_key":key,"timestamp":section.iso});
        if let Some(checkpoint) = checkpoint {
            event["checkpoint"] = checkpoint;
        }
        event
    };
    let base = |extra: Value| -> Value {
        let mut doc = json!({"parser":"rust-aider-md-v1","path":path,"mode":mode,"identity":"","events":[],
            "checkpoint":args["checkpoint"],"seen":args["seen"],"error":null});
        doc.as_object_mut()
            .unwrap()
            .extend(extra.as_object().cloned().unwrap_or_default());
        doc
    };
    match mode {
        "usage" => {
            let tail = read_tail(path, args, &args["checkpoint"], expected)?;
            let mut current = if tail.rotated {
                Section { slug: String::new(), iso: String::new() }
            } else {
                Section {
                    slug: text(&args["checkpoint"]["section"]),
                    iso: text(&args["checkpoint"]["section_ts"]),
                }
            };
            let mut checkpoint: Map<String, Value> = tail.checkpoint.clone();
            checkpoint.insert("kind".into(), json!("aider-md"));
            let mut events = Vec::new();
            for (end, _, raw) in &tail.records {
                let decoded = decode_ignore(raw);
                let line = decoded.trim_end_matches(['\r', '\n']);
                if let Some(section) = section_header(line, &namespace) {
                    current = section;
                    continue;
                }
                let Some((sent, received)) = tokens_line(line) else { continue };
                if current.slug.is_empty() {
                    continue;
                }
                let (input, output) = (parse_count(sent), parse_count(received));
                if input == 0 && output == 0 {
                    continue;
                }
                let mut cursor = checkpoint.clone();
                cursor.insert("offset".into(), json!(end));
                cursor.insert("section".into(), json!(current.slug));
                cursor.insert("section_ts".into(), json!(current.iso));
                events.push(usage_event(
                    &current,
                    input,
                    output,
                    format!("aider::{}::@{end}", current.slug),
                    Some(Value::Object(cursor)),
                ));
            }
            checkpoint.insert("section".into(), json!(current.slug));
            checkpoint.insert("section_ts".into(), json!(current.iso));
            Ok((
                json!({"parser":"rust-aider-md-v1","path":path,"mode":mode,"identity":tail.identity,
                "events":events,"checkpoint":checkpoint,"seen":args["seen"],"error":null}),
                Some(tail.raw_anchor),
            ))
        }
        "session_usage" => {
            let mut seen: Vec<String> = args["seen"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect();
            let Ok(bytes) = fs::read(path) else {
                return Ok((base(json!({})), None));
            };
            let mut current = Section { slug: String::new(), iso: String::new() };
            let mut events = Vec::new();
            for (index, raw) in universal_lines(&bytes).iter().enumerate() {
                let line_no = index + 1;
                let line = raw.trim_end_matches(['\r', '\n']);
                if let Some(section) = section_header(line, &namespace) {
                    current = section;
                    continue;
                }
                let Some((sent, received)) = tokens_line(line) else { continue };
                if current.slug.is_empty() {
                    continue;
                }
                let (input, output) = (parse_count(sent), parse_count(received));
                if input == 0 && output == 0 {
                    continue;
                }
                let key = format!("aider::{}::L{line_no}", current.slug);
                if seen.contains(&key) {
                    continue;
                }
                seen.push(key.clone());
                events.push(usage_event(&current, input, output, key, None));
            }
            Ok((base(json!({"events":events,"seen":seen})), None))
        }
        _ => {
            let high_water = py_int(&args["high_water"]) as usize;
            let mut pending = text(&args["pending"]);
            let Ok(bytes) = fs::read(path) else {
                return Ok((base(json!({"high_water":high_water,"pending":pending})), None));
            };
            let mut events: Vec<Value> = Vec::new();
            let mut last_line = high_water;
            let mut session = String::new();
            let (mut previous_prompt, mut emitted_output) = (false, false);
            let make = |event_type: &str, key: String, session: &str, detail: &str, body: String| {
                json!({"vendor":"aider","event_type":event_type,"cwd":cwd,"session_id":session,"file_path":path,
                    "dedup_key":key,"detail":detail,"text":body})
            };
            for (index, raw) in universal_lines(&bytes).iter().enumerate() {
                let line_no = index + 1;
                let is_new = line_no > high_water;
                if is_new && !raw.ends_with('\n') {
                    // An unterminated final line is still being written; leave the mark behind it.
                    break;
                }
                let line = raw.trim_end_matches(['\r', '\n']);
                let key = format!("act:{line_no}");
                last_line = line_no;
                if let Some(section) = section_header(line, &namespace) {
                    session = section.slug;
                    if is_new {
                        pending.clear();
                    }
                    previous_prompt = false;
                    continue;
                }
                if line.trim().is_empty() {
                    continue;
                }
                if let Some(prompt) = line.strip_prefix("#### ") {
                    if is_new {
                        pending.clear();
                        if !previous_prompt {
                            events.push(make("agent_active", key, &session, "prompt", prompt.to_owned()));
                        }
                    }
                    previous_prompt = true;
                    continue;
                }
                previous_prompt = false;
                if tokens_line(line).is_some() {
                    if is_new {
                        events.push(make(
                            "turn_complete",
                            format!("turn:{line_no}"),
                            &session,
                            "usage",
                            cap_text(&pending),
                        ));
                        pending.clear();
                    }
                    continue;
                }
                if is_new {
                    if !line.starts_with('>') {
                        pending = cap_text(&format!("{pending}{line}\n"));
                    }
                    if !emitted_output {
                        events.push(make("agent_active", key, &session, "output", String::new()));
                        emitted_output = true;
                    }
                }
            }
            Ok((
                base(json!({"events":events,"high_water":last_line,"pending":pending})),
                None,
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_follow_the_python_loose_parse() {
        assert_eq!(parse_count("1,234"), 1234);
        assert_eq!(parse_count("12k"), 12_000);
        assert_eq!(parse_count("8.5K"), 8500);
        assert_eq!(parse_count("1.1k"), 1100);
        assert_eq!(parse_count("."), 0);
        assert_eq!(parse_count(","), 0);
    }

    #[test]
    fn tokens_line_matches_the_regex_shape() {
        assert_eq!(
            tokens_line("> Tokens: 12k sent, 1.2k received. Cost: $0.01"),
            Some(("12k", "1.2k"))
        );
        assert_eq!(tokens_line(">tokens:  7 SENT,8 Received"), Some(("7", "8")));
        assert_eq!(tokens_line("> Tokens: 7sent, 8 received"), None);
        assert_eq!(tokens_line("Tokens: 7 sent, 8 received"), None);
    }

    #[test]
    fn invalid_utf8_is_dropped_and_newlines_are_universal() {
        assert_eq!(decode_ignore(b"a\xffb"), "ab");
        assert_eq!(universal_lines(b"x\r\ny\rz"), vec!["x\n", "y\n", "z"]);
    }
}
