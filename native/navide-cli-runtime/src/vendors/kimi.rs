//! Kimi Code CLI. Sessions: `~/.kimi-code/sessions/session_<uuid>/agents/main/wire.jsonl`,
//! the cwd in the session's `state.json`. A turn ends at `turn.ended` (0.39+),
//! `turn.cancel`, the next prompt, or 8 s past its last epoch-ms stamp.
use crate::decl::*;

const MS: Num = Num::Of(&["time"]);
const STAMP: Text = Text::Millis(&["time"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const PART_TEXT: Text = Text::Strip(&Text::Py(&["event", "part", "text"]));

const fn active(detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key: LINE_KEY, detail: Text::Lit(detail), text, timestamp: STAMP, session: None, require_text: false })
}

pub static CLI: Cli = Cli {
    key: "kimi",
    label: "Kimi Code CLI (Moonshot AI)",
    kind: Kind::Cli,
    launch: Launch {
        program: "kimi",
        base_args: &[],
        yolo_args: &["--yolo"],
        model: Some(&["--model", "{}"]),
        // Effort lives in config.toml, not on the command line.
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--session", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["kimi"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--session", "-S"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("KIMI_CODE_HOME", &["sessions"])], home: &[&[".kimi-code", "sessions"]], files: &[&["*", "agents", "*", "wire.jsonl"]] },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            Slot { name: "cwd", init: SlotInit::SidecarJson { up: 3, file: "state.json", keys: &["cwd", "workDir"] } },
            Slot { name: "session_id", init: SlotInit::DirName(3) },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[Pred::Eq(&["type"], "usage.record"), Pred::IsObject(&["usage"])]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Sum(&[Num::Of(&["usage", "inputOther"]), Num::Of(&["usage", "inputCacheRead"]), Num::Of(&["usage", "inputCacheCreation"])]),
                    output: Num::Of(&["usage", "output"]),
                    cache_read: Num::Zero,
                    cache_creation: Num::Zero,
                    // usage.record is written whole, so its byte offset is a stable key.
                    key: Text::Concat(&[Text::Lit("kimi::"), Text::Slot("session_id"), Text::Lit("::@"), Text::Offset]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::Py(&["model"]),
                    timestamp: STAMP,
                    cli_version: Text::Lit(""),
                    cwd: Text::Slot("cwd"),
                    session_id: Text::Slot("session_id"),
                    raw: &[],
                }),
            }],
            mode: UsageMode::PerRecord { window: 0, keep_on_rotation: false, credited: false },
            persist: &["cwd"],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Lines,
            rules: &[
                Rule {
                    when: Pred::Eq(&["type"], "turn.prompt"),
                    effects: &[
                        Effect::Turn(TurnOp::Open { boundary: "boundary", ts: Text::Lit(""), ms: MS }),
                        active("prompt", Text::UserPrompt(&Text::Blocks(&["input"], "text"))),
                    ],
                },
                Rule {
                    when: Pred::Eq(&["type"], "usage.record"),
                    effects: &[Effect::Turn(TurnOp::Touch { ts: None, ms: Some(MS) }), active("usage", Text::Lit(""))],
                },
                // A cancelled turn ignores the loop events still trickling in.
                Rule { when: Pred::All(&[Pred::Eq(&["type"], "context.append_loop_event"), Pred::TurnCancelled]), effects: &[Effect::Stop] },
                Rule {
                    when: Pred::Eq(&["type"], "context.append_loop_event"),
                    effects: &[
                        Effect::Turn(TurnOp::Touch { ts: None, ms: Some(MS) }),
                        Effect::When(
                            &Pred::All(&[
                                Pred::Eq(&["event", "type"], "content.part"),
                                Pred::In(&["event", "part", "type"], &["text", "think"]),
                                Pred::Text(PART_TEXT, StrTest::NonEmpty),
                            ]),
                            &[
                                // New content disproves an inferred idle end; late accounting does not.
                                Effect::Turn(TurnOp::EnsureOpen { ts: Text::Lit(""), ms: MS }),
                                Effect::When(&Pred::Eq(&["event", "part", "type"], "text"), &[Effect::Set("reply", Text::Cap(&PART_TEXT, 4000))]),
                            ],
                        ),
                    ],
                },
                Rule { when: Pred::Eq(&["type"], "turn.ended"), effects: &[Effect::Turn(TurnOp::Close { detail: "turn-ended", ms: Some(MS) })] },
                Rule { when: Pred::Eq(&["type"], "turn.cancel"), effects: &[Effect::Turn(TurnOp::Cancel { detail: "cancel", ms: Some(MS) })] },
            ],
            idle: Some(Idle { after_ms: 8000, clock: Clock::TurnMillis, detail: "idle" }),
            turn_stamp: TurnStamp::Millis,
            turn_text: "reply",
            persist: &["reply"],
            bag: Bag {
                slots: &[BagSlot::Text { slot: "reply", prefix: "kimi_text::" }],
                turn: Some(BagTurn { prefix: "kimi_turn::", compact: true, keep_null: true }),
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
