//! Claude Code. Transcripts: `~/.claude/projects/<encoded cwd>/<session>.jsonl`.
use crate::decl::*;

const TS: Text = Text::Py(&["timestamp"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const USAGE_KEY: Text = Text::Concat(&[Text::Py(&["message", "id"]), Text::Lit("::"), Text::Py(&["requestId"])]);
const CONTENT: Path = &["message", "content"];

const fn active(key: Text, detail: Text, text: Text, require_text: bool) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key, detail, text, timestamp: TS, session: None, require_text })
}

/// A prompt with pasted images: text and image blocks, no tool result.
const PASTED_IMAGES: Pred = Pred::All(&[
    Pred::IsList(CONTENT),
    Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "image")),
    Pred::Not(&Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "tool_result"))),
]);

const TYPED_PROMPT: Text = Text::If(
    &Pred::All(&[Pred::Eq(&["type"], "user"), Pred::Not(&Pred::Truthy(&["isCompactSummary"])), Pred::Not(&Pred::Truthy(&["isMeta"]))]),
    &Text::FirstOf(&[
        Text::If(&Pred::IsString(CONTENT), &Text::UserPrompt(&Text::Str(CONTENT))),
        Text::If(&PASTED_IMAGES, &Text::UserPrompt(&Text::Blocks(CONTENT, "text"))),
    ]),
);

/// Ids an assistant message stops with TaskStop (or its older names).
const STOPPED_TASKS: Text = Text::Join {
    list: CONTENT,
    filter: &Pred::All(&[Pred::Eq(&["type"], "tool_use"), Pred::In(&["name"], &["TaskStop", "KillShell", "KillBash"])]),
    item: &Text::FirstOf(&[Text::Py(&["input", "task_id"]), Text::Py(&["input", "shell_id"])]),
    sep: "\n",
    keep_empty: false,
};

/// The id of a task a tool result launched in the background.
const STARTED_TASK: Text = Text::If(
    &Pred::IsObject(&["toolUseResult"]),
    &Text::FirstOf(&[
        Text::Py(&["toolUseResult", "backgroundTaskId"]),
        Text::If(
            &Pred::Any(&[
                Pred::Eq(&["toolUseResult", "status"], "async_launched"),
                Pred::All(&[Pred::Eq(&["toolUseResult", "status"], "forked"), Pred::Truthy(&["toolUseResult", "background"])]),
            ]),
            &Text::Py(&["toolUseResult", "agentId"]),
        ),
    ]),
);

const USAGE: Path = &["message", "usage"];

pub static CLI: Cli = Cli {
    key: "claude",
    label: "Claude Code (Anthropic)",
    kind: Kind::Cli,
    launch: Launch {
        program: "claude",
        base_args: &[],
        yolo_args: &["--dangerously-skip-permissions"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--effort", "{}"]),
        efforts: &["low", "medium", "high", "xhigh", "max"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["claude"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("CLAUDE_CONFIG_DIR", &["projects"])], home: &[&[".claude", "projects"]], files: &[&["*", "*.jsonl"]] },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            Slot {
                name: "cwd",
                init: SlotInit::FirstOf(&[
                    SlotInit::Header {
                        lines: 50,
                        when: Pred::All(&[Pred::IsString(&["cwd"]), Pred::Truthy(&["cwd"])]),
                        field: Text::Str(&["cwd"]),
                        first_line: false,
                    },
                    SlotInit::DashDir,
                ]),
            },
            Slot { name: "session_id", init: SlotInit::FileStem },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[Pred::Eq(&["type"], "assistant"), Pred::IsObject(&["message"]), Pred::IsObject(USAGE)]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Sum(&[
                        Num::Of(&["message", "usage", "input_tokens"]),
                        Num::Of(&["message", "usage", "cache_read_input_tokens"]),
                        Num::Of(&["message", "usage", "cache_creation_input_tokens"]),
                    ]),
                    output: Num::Of(&["message", "usage", "output_tokens"]),
                    cache_read: Num::Of(&["message", "usage", "cache_read_input_tokens"]),
                    cache_creation: Num::Of(&["message", "usage", "cache_creation_input_tokens"]),
                    key: USAGE_KEY,
                    key_required: false,
                    key_invalid: "::",
                    model: Text::Py(&["message", "model"]),
                    timestamp: TS,
                    cli_version: Text::Py(&["version"]),
                    cwd: Text::Slot("cwd"),
                    session_id: Text::Slot("session_id"),
                    // The 1-hour TTL share of cache creation, priced differently (cost ledger).
                    raw: &[(&["cache_write_1h"], RawValue::Num(Num::Of(&["message", "usage", "cache_creation", "ephemeral_1h_input_tokens"])))],
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
                Rule {
                    when: Pred::Eq(&["type"], "assistant"),
                    effects: &[
                        active(LINE_KEY, Text::Lit("assistant"), Text::Lit(""), false),
                        // AskUserQuestion parks the turn on the user without ending it.
                        Effect::When(
                            &Pred::AnyItem(CONTENT, &Pred::All(&[Pred::Eq(&["type"], "tool_use"), Pred::Eq(&["name"], "AskUserQuestion")])),
                            &[active(Text::Concat(&[Text::Lit("q:"), Text::Line]), Text::Lit("assistant:question"), Text::Lit(""), false)],
                        ),
                        active(Text::Concat(&[Text::Lit("bg:"), Text::Line]), Text::Lit("background:end"), STOPPED_TASKS, true),
                        Effect::When(
                            &Pred::Text(Text::Py(&["message", "stop_reason"]), StrTest::Eq("end_turn")),
                            &[Effect::Emit(Emit {
                                kind: EventKind::TurnComplete,
                                key: Text::Concat(&[Text::Lit("turn:"), Text::Line]),
                                detail: Text::Lit("end_turn"),
                                text: Text::Blocks(CONTENT, "text"),
                                timestamp: TS,
                                session: None,
                                require_text: false,
                            })],
                        ),
                    ],
                },
                Rule {
                    when: Pred::In(&["type"], &["tool_use", "user"]),
                    effects: &[
                        active(LINE_KEY, Text::Py(&["type"]), TYPED_PROMPT, false),
                        Effect::When(
                            &Pred::All(&[
                                Pred::Eq(&["type"], "user"),
                                Pred::IsObject(&["message"]),
                                Pred::ContentTree(CONTENT, StrTest::StartsWith("[Request interrupted by user")),
                            ]),
                            &[active(Text::Concat(&[Text::Lit("int:"), Text::Line]), Text::Lit("user:interrupted"), Text::Lit(""), false)],
                        ),
                        Effect::When(
                            &Pred::Eq(&["type"], "user"),
                            &[active(Text::Concat(&[Text::Lit("bg:"), Text::Line]), Text::Lit("background:start"), STARTED_TASK, true)],
                        ),
                    ],
                },
                Rule {
                    when: Pred::All(&[
                        Pred::Eq(&["type"], "queue-operation"),
                        Pred::Eq(&["operation"], "enqueue"),
                        Pred::IsString(&["content"]),
                        Pred::Text(Text::Str(&["content"]), StrTest::Contains("<task-notification>")),
                        Pred::Text(Text::Str(&["content"]), StrTest::Contains("<status>")),
                    ]),
                    effects: &[active(
                        Text::Concat(&[Text::Lit("bg:"), Text::Line]),
                        Text::Lit("background:end"),
                        Text::TagValues(&Text::Str(&["content"]), "task-id"),
                        true,
                    )],
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: None, anchor: None },
        },
        turns: TurnsSpec {
            slots: &[],
            method: TurnMethod::Exact,
            filter: SessionFilter::Ignore,
            effects: &[],
            skip: Pred::Not(&Pred::IsObject(&["message"])),
            // One turn per promptId: a prompt's attachments and images are
            // extra user records under the same id.
            open: Some(TurnOpen {
                when: Pred::All(&[Pred::Eq(&["type"], "user"), Pred::Not(&Pred::Has(&["toolUseResult"]))]),
                mode: OpenMode::ByKey(Text::FirstOf(&[Text::Py(&["promptId"]), Text::Concat(&[Text::Lit("line:"), Text::Line])])),
                prompt: Text::Blocks(CONTENT, "text"),
                merge: PromptMerge::Merge,
                started: TS,
            }),
            call: Some(TurnCall {
                when: Pred::All(&[Pred::Eq(&["type"], "assistant"), Pred::IsObject(USAGE)]),
                each: None,
                input: Num::Of(&["message", "usage", "input_tokens"]),
                cache_read: Num::Of(&["message", "usage", "cache_read_input_tokens"]),
                cache_creation: Num::Of(&["message", "usage", "cache_creation_input_tokens"]),
                output: Num::Of(&["message", "usage", "output_tokens"]),
                input_includes_cache: false,
                cumulative: false,
                skip_zero: true,
                key: Some(USAGE_KEY),
                key_invalid: "::",
                model: Text::Py(&["message", "model"]),
                ts: TS,
                cli_version: Text::Py(&["version"]),
                orphan: Orphan::Drop,
            }),
            close: None,
            session: "session_id",
        },
    }),
};
