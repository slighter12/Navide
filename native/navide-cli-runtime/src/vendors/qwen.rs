//! Qwen Code. Chats: `~/.qwen/projects/<encoded cwd>/chats[/archive]/<session>.jsonl`.
//! No end-of-turn record: the next real prompt or 8 s of quiet closes a turn.
use crate::decl::*;

const TS: Text = Text::Py(&["timestamp"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const BG_KEY: Text = Text::Concat(&[Text::Lit("bg:"), Text::Line]);
/// The joined text of `message.parts[]` (user and assistant records alike).
const PARTS: Text = Text::Join { list: &["message", "parts"], filter: &Pred::IsString(&["text"]), item: &Text::Str(&["text"]), sep: "\n", keep_empty: true };
const OUTPUT: Path = &["functionResponse", "response", "output"];

const fn active(key: Text, detail: Text, text: Text, require_text: bool) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key, detail, text, timestamp: TS, session: None, require_text })
}

pub static CLI: Cli = Cli {
    key: "qwen",
    label: "Qwen Code (Alibaba Cloud)",
    kind: Kind::Cli,
    launch: Launch {
        program: "qwen",
        base_args: &[],
        yolo_args: &["--yolo"],
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["qwen"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume", "-r"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store {
            env: &[("QWEN_RUNTIME_DIR", &["projects"])],
            home: &[&[".qwen", "projects"]],
            files: &[&["*", "chats", "*.jsonl"], &["*", "chats", "archive", "*.jsonl"]],
        },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            // Every record carries the cwd: the first parseable one names it.
            Slot { name: "cwd", init: SlotInit::Header { lines: 0, when: Pred::True, field: Text::Py(&["cwd"]), first_line: false } },
            Slot { name: "session_id", init: SlotInit::FileStem },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[Pred::Eq(&["type"], "assistant"), Pred::IsObject(&["usageMetadata"])]),
                effects: &[],
                reading: Some(Reading {
                    // promptTokenCount already holds the cached part; thoughts fold into output.
                    input: Num::Of(&["usageMetadata", "promptTokenCount"]),
                    output: Num::Sum(&[Num::Of(&["usageMetadata", "candidatesTokenCount"]), Num::Of(&["usageMetadata", "thoughtsTokenCount"])]),
                    cache_read: Num::Of(&["usageMetadata", "cachedContentTokenCount"]),
                    cache_creation: Num::Zero,
                    key: Text::Py(&["uuid"]),
                    key_required: true,
                    key_invalid: "",
                    model: Text::Py(&["model"]),
                    timestamp: TS,
                    cli_version: Text::Py(&["version"]),
                    cwd: Text::Py(&["cwd"]),
                    session_id: Text::Slot("session_id"),
                    raw: &[],
                }),
            }],
            mode: UsageMode::PerRecord { window: 64, keep_on_rotation: false, credited: false },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Lines,
            rules: &[
                // Automated user records are not prompts and open no turn.
                Rule {
                    when: Pred::All(&[
                        Pred::Eq(&["type"], "user"),
                        Pred::Text(
                            Text::Py(&["subtype"]),
                            StrTest::NotIn(&["mid_turn_user_message", "cron", "notification", "agent_mention", "agent_message"]),
                        ),
                    ]),
                    effects: &[
                        Effect::Turn(TurnOp::Open { boundary: "boundary", ts: TS, ms: Num::Zero }),
                        active(LINE_KEY, Text::Lit("user"), Text::UserPrompt(&PARTS), false),
                    ],
                },
                Rule {
                    when: Pred::Eq(&["type"], "assistant"),
                    effects: &[
                        // A resumed session joined mid-turn still opens one.
                        Effect::Turn(TurnOp::EnsureOpen { ts: TS, ms: Num::Zero }),
                        Effect::Turn(TurnOp::Touch { ts: Some(TS), ms: None }),
                        Effect::SetIfNonEmpty("reply", Text::Cap(&Text::Strip(&PARTS), 4000)),
                        active(LINE_KEY, Text::Lit("assistant"), Text::Lit(""), false),
                    ],
                },
                Rule {
                    when: Pred::Eq(&["type"], "tool_result"),
                    effects: &[active(
                        BG_KEY,
                        Text::Lit("background:start"),
                        Text::Join {
                            list: &["message", "parts"],
                            filter: &Pred::IsString(OUTPUT),
                            item: &Text::FirstOf(&[
                                Text::AfterPrefix(&Text::Str(OUTPUT), "Background agent launched successfully.\ntask_id: "),
                                Text::AfterPrefix(&Text::Str(OUTPUT), "Background shell started.\nid: "),
                            ]),
                            sep: "\n",
                            keep_empty: false,
                        },
                        true,
                    )],
                },
                // A recorded <task-notification> with a terminal status ends background work.
                Rule {
                    when: Pred::All(&[
                        Pred::Eq(&["type"], "user"),
                        Pred::Text(Text::Py(&["subtype"]), StrTest::Eq("notification")),
                        Pred::Text(PARTS, StrTest::Contains("<task-notification>")),
                        Pred::Text(Text::TagFirst(&PARTS, "status"), StrTest::NotIn(&["", "running"])),
                    ]),
                    effects: &[active(BG_KEY, Text::Lit("background:end"), Text::TagValues(&PARTS, "task-id"), true)],
                },
            ],
            idle: Some(Idle { after_ms: 8000, clock: Clock::FileMtime, detail: "idle" }),
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &["reply"],
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
