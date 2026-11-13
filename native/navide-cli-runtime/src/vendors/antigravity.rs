//! Antigravity CLI (`agy`). One SQLite database per conversation,
//! `~/.gemini/antigravity-cli/conversations/<id>.db`. Its payloads are
//! undocumented protobuf, so token counts are not read (no usage, no turns).
//! The `steps` table keeps `idx`, `step_type` and `status` as integers: each
//! new step is one activity event, and only the turn's text is decoded out of
//! a protobuf blob. An assistant step still streaming is read again until it
//! finishes; a finished reply ends the turn only as the pass's newest step.
use crate::decl::*;

/// Hook: the workspace is a `file://` URI that the metadata blob repeats,
/// some copies followed by protobuf bytes that read as printable junk. main
/// picks among the candidates by whether the path is a directory on disk,
/// then by how often it occurs, then the shortest — a file-system heuristic
/// the record vocabulary has no form for.
fn workspace(text: &str) -> String {
    let mut counts: Vec<(String, usize)> = Vec::new();
    let mut from = 0;
    while let Some(found) = text[from..].find("file://") {
        let start = from + found + "file://".len();
        let tail = &text[start..];
        let run = &tail[..tail.find(|c: char| c < ' ' || c == '\x7f').unwrap_or(tail.len())];
        let mut chars = run.chars();
        let rooted = run.starts_with('/') && run.chars().count() > 1;
        let drive = matches!((chars.next(), chars.next(), chars.next(), chars.next()),
            (Some(l), Some(':'), Some('\\' | '/'), Some(_)) if l.is_ascii_alphabetic());
        if !(rooted || drive) {
            from = from + found + 1;
            continue;
        }
        from = start + run.len();
        let path = unquote(strip_drive_slashes(run));
        let path = path.trim_end_matches('/');
        if !path.is_empty() {
            let key = normalise(path);
            match counts.iter_mut().find(|(p, _)| *p == key) {
                Some((_, n)) => *n += 1,
                None => counts.push((key, 1)),
            }
        }
    }
    // `sorted(key=(not is_dir, -count, len))`: stable, so first seen wins ties.
    counts.sort_by_key(|(p, n)| (!std::path::Path::new(p).is_dir(), std::cmp::Reverse(*n), p.chars().count()));
    counts.into_iter().next().map(|(p, _)| p).unwrap_or_default()
}

/// `^/+(?=[A-Za-z]:)`: a `file:///C:/…` URI's slash before the drive.
fn strip_drive_slashes(run: &str) -> &str {
    let rest = run.trim_start_matches('/');
    let mut chars = rest.chars();
    match (chars.next(), chars.next()) {
        (Some(l), Some(':')) if l.is_ascii_alphabetic() => rest,
        _ => run,
    }
}

/// `urllib.parse.unquote`: escapes are decoded within each ASCII run, as
/// UTF-8 with replacement; other characters pass through.
fn unquote(text: &str) -> String {
    if !text.contains('%') {
        return text.to_owned();
    }
    let mut out = String::new();
    let mut ascii = Vec::new();
    let flush = |ascii: &mut Vec<u8>, out: &mut String| {
        let mut bytes = Vec::new();
        let mut i = 0;
        while i < ascii.len() {
            let hex = |b: u8| (b as char).to_digit(16);
            match (ascii[i], ascii.get(i + 1).and_then(|b| hex(*b)), ascii.get(i + 2).and_then(|b| hex(*b))) {
                (b'%', Some(hi), Some(lo)) => {
                    bytes.push((hi * 16 + lo) as u8);
                    i += 3;
                }
                (b, _, _) => {
                    bytes.push(b);
                    i += 1;
                }
            }
        }
        out.push_str(&String::from_utf8_lossy(&bytes));
        ascii.clear();
    };
    for c in text.chars() {
        if c.is_ascii() {
            ascii.push(c as u8);
        } else {
            flush(&mut ascii, &mut out);
            out.push(c);
        }
    }
    flush(&mut ascii, &mut out);
    out
}

/// `str(PurePosixPath(path))`: empty and `.` parts dropped, a leading `//`
/// (exactly two) kept.
fn normalise(path: &str) -> String {
    let root = if path.starts_with("//") && !path.starts_with("///") {
        "//"
    } else if path.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = path.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let joined = format!("{root}{}", parts.join("/"));
    if joined.is_empty() { ".".to_owned() } else { joined }
}

const STEP: Text = Text::Num(&Num::Of(&["step_type"]));
const DONE: Pred = Pred::Text(Text::Num(&Num::Of(&["status"])), StrTest::Eq("3"));
const ASSISTANT: Pred = Pred::Text(STEP, StrTest::Eq("15"));
const REPLY: Text = Text::ProtoText { column: &["payload"], path: &[20], fields: &[1, 8] };
const THINKING: Text = Text::ProtoText { column: &["payload"], path: &[20], fields: &[3] };

const fn event(kind: EventKind, detail: Text, text: Text) -> [Effect; 2] {
    [
        Effect::Emit(Emit {
            kind,
            key: Text::Concat(&[Text::Lit("step:"), Text::Slot("session_id"), Text::Lit(":"), Text::Num(&Num::Of(&["idx"]))]),
            detail,
            text,
            // The step's end time, else its start.
            timestamp: Text::ProtoStamp { column: &["metadata"], at: &[8, 7, 6] },
            session: None,
            require_text: false,
        }),
        Effect::Stop,
    ]
}

static DB: Sqlite = Sqlite {
    json: &[],
    binary: &["metadata", "payload"],
    requires: &[],
    usage: None,
    activity: Some(Rows {
        sql: "SELECT idx, step_type, status, metadata, step_payload AS payload FROM steps \
              WHERE idx > ?1 OR idx IN (SELECT value FROM json_each(?3)) ORDER BY idx LIMIT ?2",
        id: "idx",
        newest: "SELECT MAX(idx) FROM steps",
        page: 256,
        restart_below: false,
        hold_last: None,
        partition: None,
        revisit: Some(Revisit { when: Pred::All(&[ASSISTANT, Pred::Not(&DONE)]), bag: "agy_pending::" }),
        last_as: Some("last"),
    }),
};

pub static CLI: Cli = Cli {
    key: "antigravity",
    label: "Antigravity CLI (Google)",
    kind: Kind::Cli,
    launch: Launch {
        program: "agy",
        base_args: &[],
        yolo_args: &["--dangerously-skip-permissions"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--effort", "{}"]),
        efforts: &["low", "medium", "high"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--conversation", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["agy"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--conversation"],
            joined: Some(ValueShape::NoDash),
            pick: Pick::First,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[], home: &[&[".gemini", "antigravity-cli", "conversations"]], files: &[&["*.db"]] },
        format: Format::Sqlite(&DB),
        accept: None,
        slots: &[
            Slot { name: "session_id", init: SlotInit::FileStem },
            Slot { name: "meta", init: SlotInit::Joined("SELECT data FROM trajectory_metadata_blob") },
        ],
        // main's base reader: no token records, its legacy checkpoint kept.
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[],
            mode: UsageMode::Legacy,
            persist: &[],
            flags: &[],
            restore: Restore::UnlessReplaced,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Rows,
            rules: &[
                Rule { when: Pred::True, effects: &[Effect::Set("cwd", Text::Hook(&Text::Slot("meta"), workspace))] },
                Rule {
                    when: Pred::Text(STEP, StrTest::Eq("14")),
                    effects: &event(
                        EventKind::AgentActive,
                        Text::Lit("user"),
                        Text::UserPrompt(&Text::ProtoText { column: &["payload"], path: &[19], fields: &[2] }),
                    ),
                },
                // ask_question: the agent waits on the user, so the turn is over.
                Rule { when: Pred::Text(STEP, StrTest::Eq("138")), effects: &event(EventKind::TurnComplete, Text::Lit("assistant:question"), Text::Lit("")) },
                // A finished reply with no later step in this pass: the closest thing to an end of turn.
                Rule {
                    when: Pred::All(&[ASSISTANT, Pred::Text(REPLY, StrTest::NonEmpty), DONE, Pred::Truthy(&["last"])]),
                    effects: &event(EventKind::TurnComplete, Text::Lit("assistant"), REPLY),
                },
                Rule {
                    when: ASSISTANT,
                    effects: &event(
                        EventKind::AgentActive,
                        Text::FirstOf(&[Text::If(&Pred::Text(THINKING, StrTest::NonEmpty), &Text::Lit("assistant:thinking")), Text::Lit("assistant")]),
                        Text::Lit(""),
                    ),
                },
                Rule { when: Pred::True, effects: &event(EventKind::AgentActive, Text::Concat(&[Text::Lit("step-"), STEP]), Text::Lit("")) },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: Some("agy_idx::"), anchor: None },
        },
        turns: TurnsSpec {
            slots: &[],
            // Token counts live in undocumented protobuf: none read, no turns to cut.
            method: TurnMethod::Unsupported,
            filter: SessionFilter::Ignore,
            effects: &[],
            skip: Pred::Not(&Pred::True),
            open: None,
            call: None,
            close: None,
            session: "session_id",
        },
    }),
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workspace_prefers_a_directory_then_frequency_then_length() {
        // A clean copy and two junk-suffixed ones; only the clean one is a directory.
        let text = "\u{12}0file:///usr\u{0}\"A$9bc-uuid:/file:///usrz\u{88}\u{1}file:///usrz\u{88}";
        assert_eq!(workspace(text), "/usr");
        assert_eq!(workspace("file:///no/such%20dir/\u{0}"), "/no/such dir");
        assert_eq!(workspace("file:///C:/work\u{0}"), "C:/work");
        assert_eq!(workspace("file:// nothing"), "");
        assert_eq!(normalise("//a/./b//c"), "//a/b/c");
    }
}
