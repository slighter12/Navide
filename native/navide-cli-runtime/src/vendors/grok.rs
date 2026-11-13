//! Grok Build. Transcripts: `~/.grok/sessions/<url-encoded cwd>/<session>/updates.jsonl`,
//! ACP `session/update` records; `turn_completed` ends a turn and carries its usage.
use crate::decl::*;

const KIND: Path = &["params", "update", "sessionUpdate"];
const SESSION: Text = Text::Py(&["params", "sessionId"]);
/// The CLI's own per-session event counter, else the byte offset.
const IDENT: Text = Text::FirstOf(&[Text::Py(&["params", "_meta", "eventId"]), Text::Concat(&[SESSION, Text::Lit("@"), Text::Offset])]);
/// Millisecond `_meta.agentTimestampMs`, else the seconds `timestamp`.
const ISO: Text = Text::IsoFromEpoch(&["params", "_meta", "agentTimestampMs"], &["timestamp"]);
/// A chunk's visible text: one text block, or a list of them.
const CHUNK: Text = Text::Concat(&[
    Text::If(
        &Pred::All(&[Pred::IsObject(&["params", "update", "content"]), Pred::Eq(&["params", "update", "content", "type"], "text")]),
        &Text::Py(&["params", "update", "content", "text"]),
    ),
    Text::Join { list: &["params", "update", "content"], filter: &Pred::Eq(&["type"], "text"), item: &Text::Py(&["text"]), sep: "", keep_empty: true },
]);
const TURN_USAGE: Pred = Pred::All(&[Pred::Eq(KIND, "turn_completed"), Pred::IsObject(&["params", "update", "usage"])]);

const fn active(detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit {
        kind: EventKind::AgentActive,
        key: Text::Concat(&[Text::Lit("act:"), IDENT]),
        detail: Text::Lit(detail),
        text,
        timestamp: ISO,
        session: Some(SESSION),
        require_text: false,
    })
}

pub static CLI: Cli = Cli {
    key: "grok",
    label: "Grok Build (SpaceXAI)",
    kind: Kind::Cli,
    launch: Launch {
        program: "grok",
        base_args: &[],
        // `--always-approve` exists but is deliberately not wired (see agents/grok.ts).
        yolo_args: &[],
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["-r", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["grok"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["-r", "--resume"],
            joined: Some(ValueShape::Uuid),
            pick: Pick::First,
            // grok also accepts titles; only a UUID names the transcript.
            value: ValueShape::Uuid,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("GROK_HOME", &["sessions"])], home: &[&[".grok", "sessions"]], files: &[&["*", "*", "updates.jsonl"]] },
        format: Format::Jsonl,
        accept: Some(Accept { name: "updates.jsonl", not_under: None }),
        slots: &[
            // A shortened group name cannot be decoded; its `.cwd` file holds the path.
            Slot { name: "cwd", init: SlotInit::GroupDir { up: 2, override_file: ".cwd" } },
            Slot { name: "session_id", init: SlotInit::DirName(1) },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: TURN_USAGE,
                effects: &[],
                reading: Some(Reading {
                    // inputTokens is the whole input (cache folded in), outputTokens includes reasoning.
                    input: Num::Of(&["params", "update", "usage", "inputTokens"]),
                    output: Num::Of(&["params", "update", "usage", "outputTokens"]),
                    cache_read: Num::Zero,
                    cache_creation: Num::Zero,
                    key: Text::Concat(&[Text::Lit("usage:"), IDENT]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::FirstKey(&["params", "update", "usage", "modelUsage"]),
                    timestamp: ISO,
                    cli_version: Text::Lit(""),
                    cwd: Text::Slot("cwd"),
                    session_id: SESSION,
                    raw: &[],
                }),
            }],
            mode: UsageMode::PerRecord { window: 0, keep_on_rotation: false, credited: false },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessRotated,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            // eventId is not in file order (hook records take theirs when queued), so the
            // cursor is a byte offset.
            cursor: Cursor::Bytes,
            rules: &[
                Rule { when: Pred::Eq(KIND, "agent_message_chunk"), effects: &[Effect::Append("pending", CHUNK, 4000)] },
                Rule {
                    when: Pred::Eq(KIND, "turn_completed"),
                    effects: &[
                        Effect::Emit(Emit {
                            kind: EventKind::TurnComplete,
                            key: Text::Concat(&[Text::Lit("turn:"), IDENT]),
                            detail: Text::FirstOf(&[Text::Py(&["params", "update", "stop_reason"]), Text::Lit("end_turn")]),
                            text: Text::Slot("pending"),
                            timestamp: ISO,
                            session: Some(SESSION),
                            require_text: false,
                        }),
                        Effect::Set("pending", Text::Lit("")),
                        Effect::Stop,
                    ],
                },
                Rule { when: Pred::Eq(KIND, "user_message_chunk"), effects: &[active("user", Text::UserPrompt(&CHUNK))] },
                Rule { when: Pred::Eq(KIND, "agent_message_chunk"), effects: &[active("assistant", Text::Lit(""))] },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "pending",
            persist: &["pending"],
            bag: Bag { slots: &[BagSlot::JsonText { slot: "pending", prefix: "grok_text::" }], turn: None, cursor: Some("grok_act::"), anchor: None },
        },
        turns: TurnsSpec {
            slots: &[],
            method: TurnMethod::Exact,
            filter: SessionFilter::Record(SESSION),
            effects: &[],
            skip: Pred::Not(&Pred::True),
            open: Some(TurnOpen { when: Pred::Eq(KIND, "user_message_chunk"), mode: OpenMode::Join, prompt: CHUNK, merge: PromptMerge::Merge, started: ISO }),
            // One turn per turn_completed; its modelUsage entries are the calls.
            call: Some(TurnCall {
                when: TURN_USAGE,
                each: Some(Each { map: &["params", "update", "usage", "modelUsage"], fallback: &["params", "update", "usage"] }),
                input: Num::Of(&["inputTokens"]),
                cache_read: Num::Of(&["cachedReadTokens"]),
                cache_creation: Num::Of(&["cacheCreationTokens"]),
                output: Num::Of(&["outputTokens"]),
                input_includes_cache: true,
                cumulative: false,
                skip_zero: false,
                key: None,
                key_invalid: "",
                model: Text::EntryKey,
                ts: ISO,
                cli_version: Text::Lit(""),
                orphan: Orphan::ToClose,
            }),
            close: Some(TurnClose {
                when: TURN_USAGE,
                ended: ISO,
                session: Some(SESSION),
                model_calls: Some(Num::Of(&["params", "update", "usage", "modelCalls"])),
                drop_empty: true,
            }),
            session: "session_id",
        },
    }),
};
