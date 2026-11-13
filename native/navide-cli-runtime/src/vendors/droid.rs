//! Droid CLI (Factory). Sessions: `$FACTORY_HOME_OVERRIDE|~/.factory/sessions/
//! [<encoded cwd>/]<id>.jsonl`; token totals live in the `<id>.settings.json`
//! sidecar, not in the transcript.
use crate::decl::*;

const TS: Text = Text::Py(&["timestamp"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const CONTENT: Path = &["message", "content"];
const IS_MESSAGE: Pred = Pred::All(&[Pred::Eq(&["type"], "message"), Pred::IsObject(&["message"])]);
const ASSISTANT: Pred = Pred::Eq(&["message", "role"], "assistant");
/// A real prompt: not a hook's bookkeeping entry, not a tool result filed under the user role.
const PROMPT: Pred = Pred::All(&[
    Pred::Eq(&["message", "role"], "user"),
    Pred::Not(&Pred::Truthy(&["message", "hookEventName"])),
    Pred::Not(&Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "tool_result"))),
]);
const TOKENS: Path = &["tokenUsage"];

const fn active(detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key: LINE_KEY, detail: Text::Lit(detail), text, timestamp: TS, session: None, require_text: false })
}

pub static CLI: Cli = Cli {
    key: "droid",
    label: "Droid CLI (Factory)",
    kind: Kind::Cli,
    launch: Launch {
        program: "droid",
        base_args: &[],
        yolo_args: &["--auto", "high"],
        // The interactive command accepts an unknown `--model` and ignores it.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["droid"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume", "-r"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store {
            env: &[("FACTORY_HOME_OVERRIDE", &["sessions"])],
            home: &[&[".factory", "sessions"]],
            files: &[&["*.jsonl"], &["*", "*.jsonl"]],
        },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            // The first line's session_start names the cwd; `lastCwd` (where it last ran) wins.
            Slot {
                name: "cwd",
                init: SlotInit::Header {
                    lines: 1,
                    when: Pred::Eq(&["type"], "session_start"),
                    field: Text::FirstOf(&[Text::Py(&["lastCwd"]), Text::Py(&["cwd"])]),
                    first_line: true,
                },
            },
            Slot { name: "session_id", init: SlotInit::FileStem },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::IsObject(TOKENS),
                effects: &[],
                reading: Some(Reading {
                    // Cache counters fold into input; thinkingTokens is left out (main's choice).
                    input: Num::Sum(&[
                        Num::Of(&["tokenUsage", "inputTokens"]),
                        Num::Of(&["tokenUsage", "cacheCreationTokens"]),
                        Num::Of(&["tokenUsage", "cacheReadTokens"]),
                    ]),
                    output: Num::Of(&["tokenUsage", "outputTokens"]),
                    cache_read: Num::Zero,
                    cache_creation: Num::Zero,
                    key: Text::Concat(&[
                        Text::Lit("usage::"),
                        Text::Slot("session_id"),
                        Text::Lit("::"),
                        Text::Slot("input_total"),
                        Text::Lit("::"),
                        Text::Slot("output_total"),
                    ]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::Py(&["model"]),
                    timestamp: Text::Lit(""),
                    cli_version: Text::Lit(""),
                    cwd: Text::Slot("cwd"),
                    session_id: Text::Slot("session_id"),
                    raw: &[],
                }),
            }],
            mode: UsageMode::Snapshot { suffix: ".settings.json", mark: "droid_tok::" },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Lines,
            rules: &[
                // Every outcome closes the turn, whatever its reason (it rides on detail).
                Rule {
                    when: Pred::Eq(&["type"], "agent_turn_outcome"),
                    effects: &[
                        Effect::Emit(Emit {
                            kind: EventKind::TurnComplete,
                            key: Text::Concat(&[Text::Lit("turn:"), Text::Line]),
                            detail: Text::Py(&["reason"]),
                            text: Text::Slot("reply"),
                            timestamp: TS,
                            session: None,
                            require_text: false,
                        }),
                        Effect::Set("reply", Text::Lit("")),
                    ],
                },
                Rule {
                    when: Pred::All(&[IS_MESSAGE, ASSISTANT]),
                    effects: &[Effect::Set("reply", Text::Blocks(CONTENT, "text")), active("assistant", Text::Lit(""))],
                },
                Rule {
                    when: Pred::All(&[IS_MESSAGE, Pred::Not(&ASSISTANT), PROMPT]),
                    effects: &[Effect::Set("reply", Text::Lit("")), active("user", Text::UserPrompt(&Text::Blocks(CONTENT, "text")))],
                },
                // Tool results, hook entries and other roles: activity without text.
                Rule { when: Pred::All(&[IS_MESSAGE, Pred::Not(&ASSISTANT), Pred::Not(&PROMPT)]), effects: &[active("user", Text::Lit(""))] },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &["reply"],
            bag: Bag { slots: &[BagSlot::Text { slot: "reply", prefix: "droid_text::" }], turn: None, cursor: None, anchor: None },
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
