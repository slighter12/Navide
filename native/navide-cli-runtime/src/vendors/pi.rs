//! Pi. Sessions: `~/.pi/agent/sessions/--<encoded cwd>--/<ts>_<id>.jsonl`,
//! line 1 a `session` header. Files are rewritten in place (migrations,
//! /tree), so usage keeps its dedup window and a credited count across
//! rewrites. No end-of-turn record: turns are inferred like qwen's.
use crate::decl::*;

const TS: Text = Text::Py(&["timestamp"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const CONTENT: Path = &["message", "content"];

const fn header(field: Text) -> SlotInit {
    SlotInit::Header { lines: 0, when: Pred::Eq(&["type"], "session"), field, first_line: true }
}

/// main's `_usage_of`: on the message object for assistant messages, at the
/// top level for compaction / branch-summary entries.
const MESSAGE_USAGE: Pred = Pred::All(&[Pred::IsObject(&["message"]), Pred::IsObject(&["message", "usage"])]);
const TOP_USAGE: Pred = Pred::All(&[Pred::Not(&Pred::IsObject(&["message"])), Pred::IsObject(&["usage"])]);

const fn active(detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key: LINE_KEY, detail: Text::Lit(detail), text, timestamp: TS, session: None, require_text: false })
}

pub static CLI: Cli = Cli {
    key: "pi",
    label: "Pi",
    kind: Kind::Cli,
    launch: Launch {
        program: "pi",
        base_args: &[],
        // pi has no permission system to bypass.
        yolo_args: &[],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--thinking", "{}"]),
        efforts: &["off", "minimal", "low", "medium", "high", "xhigh", "max"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--session-id", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["pi"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--session-id"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("PI_CODING_AGENT_SESSION_DIR", &[]), ("PI_CODING_AGENT_DIR", &["sessions"])], home: &[&[".pi", "agent", "sessions"]], files: &[&["*", "*.jsonl"]] },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            Slot { name: "session_id", init: SlotInit::FirstOf(&[header(Text::Py(&["id"])), SlotInit::StemAfterUnderscore]) },
            Slot { name: "cwd", init: header(Text::Py(&["cwd"])) },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[Pred::Not(&Pred::Eq(&["type"], "session")), Pred::Any(&[MESSAGE_USAGE, TOP_USAGE])]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Case(&[
                        (
                            Pred::IsObject(&["message"]),
                            Num::Sum(&[
                                Num::Of(&["message", "usage", "input"]),
                                Num::Of(&["message", "usage", "cacheRead"]),
                                Num::Of(&["message", "usage", "cacheWrite"]),
                            ]),
                        ),
                        (Pred::True, Num::Sum(&[Num::Of(&["usage", "input"]), Num::Of(&["usage", "cacheRead"]), Num::Of(&["usage", "cacheWrite"])])),
                    ]),
                    output: Num::Case(&[(Pred::IsObject(&["message"]), Num::Of(&["message", "usage", "output"])), (Pred::True, Num::Of(&["usage", "output"]))]),
                    cache_read: Num::Zero,
                    cache_creation: Num::Zero,
                    key: Text::Py(&["id"]),
                    key_required: true,
                    key_invalid: "",
                    model: Text::FirstOf(&[Text::Py(&["message", "model"]), Text::Py(&["model"])]),
                    timestamp: TS,
                    cli_version: Text::Lit(""),
                    cwd: Text::Slot("cwd"),
                    session_id: Text::Slot("session_id"),
                    raw: &[],
                }),
            }],
            mode: UsageMode::PerRecord { window: 256, keep_on_rotation: true, credited: true },
            persist: &["session_id", "cwd"],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Lines,
            rules: &[
                Rule {
                    when: Pred::All(&[Pred::Eq(&["type"], "message"), Pred::Eq(&["message", "role"], "user")]),
                    effects: &[
                        Effect::Turn(TurnOp::Open { boundary: "boundary", ts: TS, ms: Num::Zero }),
                        active("user", Text::UserPrompt(&Text::Blocks(CONTENT, "text"))),
                    ],
                },
                Rule {
                    when: Pred::All(&[Pred::Eq(&["type"], "message"), Pred::Eq(&["message", "role"], "assistant")]),
                    effects: &[
                        Effect::Turn(TurnOp::EnsureOpen { ts: TS, ms: Num::Zero }),
                        Effect::Turn(TurnOp::Touch { ts: Some(TS), ms: None }),
                        Effect::SetIfNonEmpty("reply", Text::Cap(&Text::Strip(&Text::Blocks(CONTENT, "text")), 4000)),
                        active("assistant", Text::Lit("")),
                    ],
                },
            ],
            idle: Some(Idle { after_ms: 8000, clock: Clock::FileMtime, detail: "idle" }),
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &["reply"],
            bag: Bag {
                slots: &[BagSlot::Text { slot: "reply", prefix: "pi_text::" }],
                turn: Some(BagTurn { prefix: "pi_turn::", compact: false, keep_null: false }),
                cursor: None,
                anchor: None,
            },
        },
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
