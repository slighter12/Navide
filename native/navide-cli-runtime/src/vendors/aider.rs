//! Aider. Its history is Markdown appended to `<git root>/.aider.chat.history.md`
//! (or a pane's own `.aider.chat.history.<token>.md`), one section per run,
//! headed `# aider chat started at <date> <time>`. Each `> Tokens: … sent, …
//! received` line is one model call and the only end-of-message signal; `#### `
//! lines are the prompt. The section's start, with the file's namespace, is the
//! session id, so the activity walk replays the file from the top every time
//! (main's walk does too) to know which section a new line is in.
use crate::decl::*;
use crate::expr::py_space;

/// Hook: main's `_SECTION_RE` (`^# aider chat started at (\d{4})-(\d{2})-(\d{2})
/// (\d{2}):(\d{2}):(\d{2})`), a fixed-width digit pattern the vocabulary has no
/// form for. The six fields, or `None`.
fn section(line: &str) -> Option<[&str; 6]> {
    let rest = line.strip_prefix("# aider chat started at ")?;
    let digits = |at: usize, len: usize| rest.get(at..at + len).filter(|d| d.bytes().all(|b| b.is_ascii_digit()));
    let seps = [(4, b'-'), (7, b'-'), (10, b' '), (13, b':'), (16, b':')];
    if rest.len() < 19 || seps.iter().any(|(at, sep)| rest.as_bytes()[*at] != *sep) {
        return None;
    }
    Some([digits(0, 4)?, digits(5, 2)?, digits(8, 2)?, digits(11, 2)?, digits(14, 2)?, digits(17, 2)?])
}

/// The section's slug stamp, `YYYYMMDD-HHMMSS` ("" for any other line).
fn stamp(line: &str) -> String {
    section(line).map(|[y, mo, d, h, mi, s]| format!("{y}{mo}{d}-{h}{mi}{s}")).unwrap_or_default()
}

/// The section's start as main's `_iso`: naive `YYYY-MM-DDTHH:MM:SS`.
fn iso(line: &str) -> String {
    section(line).map(|[y, mo, d, h, mi, s]| format!("{y}-{mo}-{d}T{h}:{mi}:{s}")).unwrap_or_default()
}

/// Hook: main's `_TOKENS_RE` (`^>\s*Tokens:\s*([\d.,]+k?)\s+sent,\s*([\d.,]+k?)\s+received`,
/// case-insensitive) and `_parse_count` (`1,234`, `12k`, `8.5k`): a loose
/// number grammar the vocabulary has no form for.
fn counts(line: &str) -> Option<(i64, i64)> {
    fn word<'a>(text: &'a str, word: &str) -> Option<&'a str> {
        let head = text.get(..word.len())?;
        head.eq_ignore_ascii_case(word).then(|| &text[word.len()..])
    }
    /// `([\d.,]+k?)` then `\s+`.
    fn count(text: &str) -> Option<(i64, &str)> {
        let end = text.find(|c: char| !(c.is_ascii_digit() || c == '.' || c == ',')).unwrap_or(text.len());
        if end == 0 {
            return None;
        }
        let (number, mut rest) = text.split_at(end);
        let mut mult = 1.0;
        if let Some(after) = word(rest, "k") {
            (mult, rest) = (1000.0, after);
        }
        let spaced = rest.trim_start_matches(py_space);
        if spaced.len() == rest.len() {
            return None;
        }
        // `max(0, int(float(s) * mult))`, 0 where `float` raises.
        let value = number.replace(',', "").parse::<f64>().map_or(0, |f| ((f * mult) as i64).max(0));
        Some((value, spaced))
    }
    let rest = word(line.strip_prefix('>')?.trim_start_matches(py_space), "tokens:")?;
    let (sent, rest) = count(rest.trim_start_matches(py_space))?;
    let rest = word(rest, "sent,")?;
    let (received, rest) = count(rest.trim_start_matches(py_space))?;
    word(rest, "received")?;
    Some((sent, received))
}

fn sent(line: &str) -> String {
    counts(line).map(|(n, _)| n.to_string()).unwrap_or_default()
}

fn received(line: &str) -> String {
    counts(line).map(|(_, n)| n.to_string()).unwrap_or_default()
}

/// Hook: main's `history_namespace`: a per-pane file's token, else the first 8
/// hex digits of the MD5 of the file's resolved path, so two workspaces'
/// shared files starting in the same second keep apart. A file-system lookup
/// and a hash, which no record can carry.
fn namespace(path: &str) -> String {
    let name = path.rsplit(['/', '\\']).next().unwrap_or_default();
    if let Some(token) = name.strip_prefix(".aider.chat.history.").and_then(|r| r.strip_suffix(".md")) {
        if token.len() == 8 && token.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
            return token.to_owned();
        }
    }
    let resolved = std::fs::canonicalize(path).map_or_else(|_| path.to_owned(), |p| p.to_string_lossy().into_owned());
    crate::md5::hex_digest(resolved.as_bytes())[..8].to_owned()
}

const LINE: Text = Text::Py(&["line"]);
const HEADER: Pred = Pred::Text(Text::Hook(&LINE, stamp), StrTest::NonEmpty);
const TOKENS: Pred = Pred::Text(Text::Hook(&LINE, sent), StrTest::NonEmpty);
const SECTION: Text = Text::Concat(&[Text::Lit("aider-"), Text::Hook(&Text::Slot("path"), namespace), Text::Lit("-"), Text::Hook(&LINE, stamp)]);
/// main's `_PENDING_TEXT_MAX_CHARS`: the reply text kept between walks.
const PENDING_MAX: usize = 32_000;

const ACT: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const TURN: Text = Text::Concat(&[Text::Lit("turn:"), Text::Line]);

const fn emit(kind: EventKind, key: Text, detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit {
        kind,
        key,
        detail: Text::Lit(detail),
        text,
        timestamp: Text::Lit(""),
        session: None,
        require_text: false,
    })
}

pub static CLI: Cli = Cli {
    key: "aider",
    label: "Aider",
    kind: Kind::Cli,
    launch: Launch {
        program: "aider",
        base_args: &[],
        yolo_args: &["--yes-always"],
        // Any model string goes straight to litellm, so a wrong one cannot be caught.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    // Only a lossy `--restore-chat-history`, which names no session.
    resume: Resume { args: None, parse: None },
    interrupt: b"\x03",
    observe: Some(Observe {
        // No global root: history files live in each workspace, found by
        // main's workspace scan, which stays in Python.
        store: Store { env: &[], home: &[], files: &[] },
        format: Format::Text { kind: "aider-md" },
        accept: None,
        slots: &[
            Slot { name: "path", init: SlotInit::FilePath },
            // The history file sits at the session's git root.
            Slot { name: "cwd", init: SlotInit::Parent },
            Slot { name: "session_id", init: SlotInit::Empty },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[
                UsageRule {
                    when: HEADER,
                    effects: &[Effect::Set("section", SECTION), Effect::Set("section_ts", Text::Hook(&LINE, iso))],
                    reading: None,
                },
                // A usage line before any header (its section precedes the checkpoint) is skipped.
                UsageRule {
                    when: Pred::All(&[TOKENS, Pred::SlotSet("section")]),
                    effects: &[],
                    reading: Some(Reading {
                        input: Num::Parse(&Text::Hook(&LINE, sent)),
                        output: Num::Parse(&Text::Hook(&LINE, received)),
                        cache_read: Num::Zero,
                        cache_creation: Num::Zero,
                        key: Text::Concat(&[Text::Lit("aider::"), Text::Slot("section"), Text::Lit("::@"), Text::Offset]),
                        key_required: false,
                        key_invalid: "",
                        model: Text::Lit(""),
                        timestamp: Text::Slot("section_ts"),
                        cli_version: Text::Lit(""),
                        cwd: Text::Slot("cwd"),
                        session_id: Text::Slot("section"),
                        raw: &[],
                    }),
                },
            ],
            mode: UsageMode::PerRecord { window: 0, keep_on_rotation: false, credited: false },
            persist: &["section", "section_ts"],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Replay,
            rules: &[
                // A header names the section even when already walked.
                Rule {
                    when: HEADER,
                    effects: &[
                        Effect::Set("session_id", SECTION),
                        Effect::When(&Pred::Fresh, &[Effect::Set("pending", Text::Lit(""))]),
                        Effect::Set("prev_prompt", Text::Lit("")),
                        Effect::Stop,
                    ],
                },
                Rule { when: Pred::Text(Text::Strip(&LINE), StrTest::Empty), effects: &[Effect::Stop] },
                // A multi-line prompt is one event, named from its first line.
                Rule {
                    when: Pred::Text(LINE, StrTest::StartsWith("#### ")),
                    effects: &[
                        Effect::When(
                            &Pred::Fresh,
                            &[
                                Effect::Set("pending", Text::Lit("")),
                                Effect::When(
                                    &Pred::Not(&Pred::SlotSet("prev_prompt")),
                                    &[emit(EventKind::AgentActive, ACT, "prompt", Text::UserPrompt(&Text::After(&LINE, "#### ")))],
                                ),
                            ],
                        ),
                        Effect::Set("prev_prompt", Text::Lit("1")),
                        Effect::Stop,
                    ],
                },
                Rule { when: Pred::True, effects: &[Effect::Set("prev_prompt", Text::Lit(""))] },
                // One usage line per finished assistant message: it carries the message's text.
                Rule {
                    when: TOKENS,
                    effects: &[
                        Effect::When(
                            &Pred::Fresh,
                            &[
                                emit(EventKind::TurnComplete, TURN, "usage", Text::Cap(&Text::Slot("pending"), PENDING_MAX)),
                                Effect::Set("pending", Text::Lit("")),
                            ],
                        ),
                        Effect::Stop,
                    ],
                },
                // Other output: its text is kept (not `>` tool lines); one event per walk.
                Rule {
                    when: Pred::Fresh,
                    effects: &[
                        Effect::When(
                            &Pred::Not(&Pred::Text(LINE, StrTest::StartsWith(">"))),
                            &[Effect::Append("pending", Text::Concat(&[LINE, Text::Lit("\n")]), PENDING_MAX)],
                        ),
                        Effect::When(
                            &Pred::Not(&Pred::SlotSet("emitted")),
                            &[emit(EventKind::AgentActive, ACT, "output", Text::Lit("")), Effect::Set("emitted", Text::Lit("1"))],
                        ),
                    ],
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "pending",
            persist: &["pending"],
            bag: Bag { slots: &[BagSlot::Text { slot: "pending", prefix: "aider_text::" }], turn: None, cursor: None, anchor: None },
        },
        // main's base reader: usage cut on turn_complete timestamps (aider's carry none).
        turns: TurnsSpec {
            slots: &[],
            method: TurnMethod::Inferred,
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
    fn section_and_token_lines_follow_main() {
        assert_eq!(stamp("# aider chat started at 2025-01-02 03:04:05 extra"), "20250102-030405");
        assert_eq!(iso("# aider chat started at 2025-01-02 03:04:05"), "2025-01-02T03:04:05");
        assert_eq!(stamp("# aider chat started at 2025-1-02 03:04:05"), "");
        assert_eq!(counts("> Tokens: 1,234 sent, 8.5k received. Cost: $0.01"), Some((1234, 8500)));
        assert_eq!(counts(">tokens:12K SENT, 567 Received"), Some((12000, 567)));
        assert_eq!(counts("> Tokens: 12k sent, 567received"), None, "`\\s+` before `received`");
        assert_eq!(counts(">  TOKENS: 12K  SENT,567 received"), Some((12000, 567)));
        assert_eq!(counts("> Tokens: 1.2.3 sent, ,, received"), Some((0, 0)));
        assert_eq!(counts("> Tokens: k sent, 1 received"), None);
        assert_eq!(namespace("/w/.aider.chat.history.0a1b2c3d.md"), "0a1b2c3d");
        assert_eq!(namespace("/no/such/.aider.chat.history.md"), crate::md5::hex_digest(b"/no/such/.aider.chat.history.md")[..8]);
    }
}
