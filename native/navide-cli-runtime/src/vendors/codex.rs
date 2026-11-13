//! Codex CLI. Rollouts: `$CODEX_HOME/sessions/Y/M/D/rollout-*.jsonl`, one per
//! session, holding running token totals.
use crate::decl::*;

const TS: Text = Text::Py(&["timestamp"]);
const PTYPE: Path = &["payload", "type"];
const TOTALS: Path = &["payload", "info", "total_token_usage"];

/// main reads `rec.get("payload") or {}`: a missing or empty payload counts
/// as an empty object, any other non-object as no payload.
const PAYLOAD: Pred = Pred::Any(&[Pred::IsObject(&["payload"]), Pred::Not(&Pred::Truthy(&["payload"]))]);
const IS_META: Pred = Pred::All(&[Pred::Eq(&["type"], "session_meta"), PAYLOAD]);
const IS_TOKEN_COUNT: Pred = Pred::All(&[Pred::Eq(&["type"], "event_msg"), Pred::Eq(PTYPE, "token_count"), Pred::IsObject(TOTALS)]);

/// A rollout written by a sub-agent: its turns are not the pane's.
const SUBAGENT_META: Pred = Pred::Any(&[
    Pred::Eq(&["payload", "thread_source"], "subagent"),
    Pred::Truthy(&["payload", "parent_thread_id"]),
    Pred::All(&[Pred::IsObject(&["payload", "source"]), Pred::Has(&["payload", "source", "subagent"])]),
]);

/// The user's typed prompt in either rollout shape: a `user_message` event,
/// or an `item_completed` UserMessage whose text blocks carry it.
const IS_PROMPT: Pred = Pred::Any(&[
    Pred::Eq(PTYPE, "user_message"),
    Pred::All(&[Pred::Eq(PTYPE, "item_completed"), Pred::Eq(&["payload", "item", "type"], "UserMessage")]),
]);
const PROMPT: Text = Text::Concat(&[
    Text::If(&Pred::Eq(PTYPE, "user_message"), &Text::Py(&["payload", "message"])),
    Text::If(
        &Pred::Eq(PTYPE, "item_completed"),
        &Text::Join { list: &["payload", "item", "content"], filter: &Pred::Eq(&["type"], "text"), item: &Text::Py(&["text"]), sep: "\n", keep_empty: true },
    ),
]);

/// The first session_meta is the rollout's own (a sub-agent replays its
/// ancestors' after it).
const FIRST_META: Effect = Effect::When(
    &Pred::Not(&Pred::SlotSet("meta_seen")),
    &[
        Effect::Set("meta_seen", Text::Lit("1")),
        Effect::Set("cwd", Text::FirstOf(&[Text::Py(&["payload", "cwd"]), Text::Slot("cwd")])),
        Effect::Set("session_id", Text::FirstOf(&[Text::Py(&["payload", "id"]), Text::Slot("session_id")])),
        Effect::Set("model", Text::FirstOf(&[Text::Py(&["payload", "model_provider"]), Text::Py(&["payload", "model"]), Text::Slot("model")])),
        Effect::Set("cli_version", Text::FirstOf(&[Text::Py(&["payload", "cli_version"]), Text::Slot("cli_version")])),
    ],
);

const fn event(kind: EventKind, key: Text, detail: Text, text: Text) -> Effect {
    Effect::Emit(Emit { kind, key, detail, text, timestamp: TS, session: None, require_text: false })
}
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const TURN_KEY: Text = Text::Concat(&[Text::Lit("turn:"), Text::Line]);
const TURN_END: Pred = Pred::In(PTYPE, &["task_complete", "turn_aborted"]);

const fn empty(name: Str) -> Slot {
    Slot { name, init: SlotInit::Empty }
}

pub static CLI: Cli = Cli {
    key: "codex",
    label: "Codex CLI (OpenAI)",
    kind: Kind::Cli,
    launch: Launch {
        program: "codex",
        base_args: &[],
        yolo_args: &["--dangerously-bypass-approvals-and-sandbox"],
        model: Some(&["--model", "{}"]),
        // Effort is a config override (`-c model_reasoning_effort=…`), not a flag.
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["codex"],
            tokens: Tokens::Whitespace,
            subcommand: &["resume"],
            id_after_subcommand: true,
            flags: &[],
            joined: None,
            pick: Pick::First,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x1b",
    observe: Some(Observe {
        store: Store { env: &[("CODEX_HOME", &["sessions"])], home: &[&[".codex", "sessions"]], files: &[&["*", "*", "*", "rollout-*.jsonl"]] },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            Slot { name: "session_id", init: SlotInit::FileStem },
            Slot {
                name: "cwd",
                init: SlotInit::Header { lines: 5, when: Pred::Eq(&["type"], "session_meta"), field: Text::Py(&["payload", "cwd"]), first_line: false },
            },
            Slot {
                name: "subagent",
                init: SlotInit::Header {
                    lines: 5,
                    when: Pred::Eq(&["type"], "session_meta"),
                    field: Text::If(&Pred::All(&[Pred::IsObject(&["payload"]), SUBAGENT_META]), &Text::Lit("1")),
                    first_line: false,
                },
            },
        ],
        usage: Some(UsageSpec {
            // Usage takes cwd from the rollout's own first session_meta.
            slots: &[empty("cwd"), empty("model"), empty("cli_version"), empty("meta_seen"), empty("ledger_model")],
            rules: &[
                UsageRule {
                    when: Pred::Eq(&["type"], "turn_context"),
                    effects: &[Effect::SetIfNonEmpty("ledger_model", Text::If(&Pred::IsObject(&["payload"]), &Text::Py(&["payload", "model"])))],
                    reading: None,
                },
                UsageRule { when: Pred::Eq(&["type"], "session_meta"), effects: &[Effect::When(&PAYLOAD, &[FIRST_META])], reading: None },
                UsageRule {
                    when: IS_TOKEN_COUNT,
                    effects: &[],
                    reading: Some(Reading {
                        // input_tokens already holds the cached part, output_tokens the reasoning.
                        input: Num::Of(&["payload", "info", "total_token_usage", "input_tokens"]),
                        output: Num::Of(&["payload", "info", "total_token_usage", "output_tokens"]),
                        cache_read: Num::Zero,
                        cache_creation: Num::Zero,
                        key: Text::Concat(&[
                            Text::Lit("codex_cumulative::"),
                            Text::Slot("session_id"),
                            Text::Lit("::"),
                            Text::Slot("input_total"),
                            Text::Lit("::"),
                            Text::Slot("output_total"),
                        ]),
                        key_required: false,
                        key_invalid: "",
                        model: Text::Slot("model"),
                        timestamp: TS,
                        cli_version: Text::Slot("cli_version"),
                        cwd: Text::Slot("cwd"),
                        session_id: Text::Slot("session_id"),
                        // The session's running totals, for the cost ledger.
                        raw: &[
                            (
                                &["ledger_session_totals", "input"],
                                RawValue::Num(Num::Sub(
                                    &Num::Of(&["payload", "info", "total_token_usage", "input_tokens"]),
                                    &[
                                        Num::Of(&["payload", "info", "total_token_usage", "cached_input_tokens"]),
                                        Num::Of(&["payload", "info", "total_token_usage", "cache_write_input_tokens"]),
                                    ],
                                )),
                            ),
                            (&["ledger_session_totals", "cache_read"], RawValue::Num(Num::Of(&["payload", "info", "total_token_usage", "cached_input_tokens"]))),
                            (
                                &["ledger_session_totals", "cache_write"],
                                RawValue::Num(Num::Of(&["payload", "info", "total_token_usage", "cache_write_input_tokens"])),
                            ),
                            (&["ledger_session_totals", "output"], RawValue::Num(Num::Of(&["payload", "info", "total_token_usage", "output_tokens"]))),
                            (&["ledger_session_totals", "model"], RawValue::Text(Text::Slot("ledger_model"))),
                        ],
                    }),
                },
            ],
            mode: UsageMode::Cumulative,
            persist: &["cwd", "model", "session_id", "cli_version", "meta_seen", "ledger_model"],
            flags: &["meta_seen"],
            restore: Restore::UnlessReplaced,
            // Checkpoints without basis 2 counted cached and reasoning tokens twice.
            stamp: &[("basis", 2)],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Lines,
            rules: &[
                Rule {
                    when: Pred::Eq(&["type"], "session_meta"),
                    effects: &[
                        Effect::When(
                            &PAYLOAD,
                            &[
                                Effect::Set("cwd", Text::FirstOf(&[Text::Py(&["payload", "cwd"]), Text::Slot("cwd")])),
                                // When this walk holds the file's own header, it decides.
                                Effect::When(
                                    &Pred::All(&[Pred::Not(&Pred::SlotSet("_walk_resumed")), Pred::Not(&Pred::SlotSet("_header_seen"))]),
                                    &[
                                        Effect::Set("_header_seen", Text::Lit("1")),
                                        Effect::SetIfNonEmpty("subagent", Text::If(&SUBAGENT_META, &Text::Lit("1"))),
                                    ],
                                ),
                            ],
                        ),
                        Effect::Stop,
                    ],
                },
                Rule {
                    when: Pred::SlotSet("subagent"),
                    effects: &[
                        Effect::When(
                            &Pred::All(&[Pred::Eq(&["type"], "event_msg"), TURN_END]),
                            &[event(EventKind::SubagentTurnEnd, TURN_KEY, Text::Py(PTYPE), Text::Lit(""))],
                        ),
                        Effect::Stop,
                    ],
                },
                Rule { when: Pred::Eq(&["type"], "assistant"), effects: &[event(EventKind::AgentActive, LINE_KEY, Text::Lit("assistant"), Text::Lit(""))] },
                Rule {
                    when: Pred::All(&[
                        Pred::Eq(&["type"], "response_item"),
                        Pred::Eq(&["payload", "role"], "assistant"),
                        Pred::Eq(PTYPE, "message"),
                    ]),
                    effects: &[Effect::SetIfNonEmpty("reply", Text::Blocks(&["payload", "content"], "output_text"))],
                },
                Rule {
                    when: Pred::Eq(&["type"], "event_msg"),
                    effects: &[
                        Effect::When(&Pred::Eq(PTYPE, "agent_message"), &[Effect::SetIfNonEmpty("reply", Text::Py(&["payload", "message"]))]),
                        event(
                            EventKind::AgentActive,
                            LINE_KEY,
                            Text::FirstOf(&[Text::If(&IS_PROMPT, &Text::Lit("user_message")), Text::Py(PTYPE)]),
                            Text::If(&IS_PROMPT, &Text::UserPrompt(&PROMPT)),
                        ),
                        // task_complete is the turn's end (token_count fires per model call).
                        Effect::When(
                            &TURN_END,
                            &[
                                event(
                                    EventKind::TurnComplete,
                                    TURN_KEY,
                                    Text::Py(PTYPE),
                                    Text::If(
                                        &Pred::Eq(PTYPE, "task_complete"),
                                        &Text::FirstOf(&[Text::Slot("reply"), Text::Py(&["payload", "last_agent_message"])]),
                                    ),
                                ),
                                Effect::Set("reply", Text::Lit("")),
                            ],
                        ),
                    ],
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &["reply"],
        },
        turns: TurnsSpec {
            slots: &[empty("model"), empty("cli_version"), empty("meta_seen")],
            method: TurnMethod::Exact,
            filter: SessionFilter::File,
            effects: &[Rule { when: IS_META, effects: &[FIRST_META] }],
            skip: Pred::Any(&[Pred::Not(&PAYLOAD), Pred::Not(&Pred::Eq(&["type"], "event_msg"))]),
            open: Some(TurnOpen { when: IS_PROMPT, mode: OpenMode::Each, prompt: PROMPT, merge: PromptMerge::Excerpt, started: TS }),
            call: Some(TurnCall {
                when: IS_TOKEN_COUNT,
                each: None,
                input: Num::Of(&["payload", "info", "total_token_usage", "input_tokens"]),
                cache_read: Num::Of(&["payload", "info", "total_token_usage", "cached_input_tokens"]),
                cache_creation: Num::Zero,
                output: Num::Of(&["payload", "info", "total_token_usage", "output_tokens"]),
                input_includes_cache: true,
                cumulative: true,
                skip_zero: true,
                key: None,
                key_invalid: "",
                model: Text::Slot("model"),
                ts: TS,
                cli_version: Text::Slot("cli_version"),
                orphan: Orphan::OwnTurnUntilFirstOpen,
            }),
            close: None,
            session: "session_id",
        },
    }),
};
