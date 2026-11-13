//! Muse Code (Meta). Sessions: `$XDG_DATA_HOME|~/.local/share/muse/sessions/
//! <Y>/<M>/<D>/<id>/session.jsonl` (a subagent's log, under
//! `<id>/subagent/<child>/`, is not one). Records are envelopes: a
//! `runtime.session` envelope carries a run event in `payload.event`.
use crate::decl::*;

const TS: Text = Text::IsoFromMicros(&["recorded_at"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);
const RUN_EVENT: Pred = Pred::All(&[Pred::Eq(&["payload_type"], "runtime.session"), Pred::IsObject(&["payload", "event"])]);
const KIND: Path = &["payload", "event", "kind"];
const RECORD: Path = &["payload", "event", "record"];
const METADATA: Pred = Pred::Eq(&["payload_type"], "runtime.session.metadata");
const HAS_RECORD: Pred = Pred::IsObject(&["payload", "record"]);
const CWD: Text = Text::If(&HAS_RECORD, &Text::Py(&["payload", "record", "workspace_root"]));
const VERSION: Text = Text::If(
    &HAS_RECORD,
    &Text::If(&Pred::IsObject(&["payload", "record", "build"]), &Text::Py(&["payload", "record", "build", "semver"])),
);
/// The first metadata record within 20 lines names it ("" when its record is not an object).
const fn metadata(field: Text) -> SlotInit {
    SlotInit::Header { lines: 20, when: METADATA, field, first_line: false }
}

pub static CLI: Cli = Cli {
    key: "muse",
    label: "Muse Code (Meta)",
    kind: Kind::Cli,
    launch: Launch {
        program: "muse",
        base_args: &[],
        yolo_args: &["--disable-approval"],
        model: Some(&["--model", "{}"]),
        effort: Some(&["--reasoning-effort", "{}"]),
        efforts: &["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["resume", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["muse"],
            tokens: Tokens::Whitespace,
            subcommand: &["resume"],
            id_after_subcommand: true,
            flags: &[],
            joined: None,
            pick: Pick::First,
            value: ValueShape::Any,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store {
            env: &[("XDG_DATA_HOME", &["muse", "sessions"])],
            home: &[&[".local", "share", "muse", "sessions"]],
            files: &[&["*", "*", "*", "*", "session.jsonl"]],
        },
        format: Format::Jsonl,
        accept: Some(Accept { name: "session.jsonl", not_under: Some((2, "subagent")) }),
        slots: &[
            Slot { name: "cwd", init: metadata(CWD) },
            Slot { name: "cli_version", init: metadata(VERSION) },
            // Every log is session.jsonl: the id is its directory's name.
            Slot { name: "session_id", init: SlotInit::DirName(1) },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                // A main-requester goal_usage_attribution (cached and reasoning
                // tokens are inside input and output, so not added).
                when: Pred::All(&[
                    RUN_EVENT,
                    Pred::Eq(KIND, "goal_usage_attribution"),
                    Pred::IsObject(RECORD),
                    Pred::IsObject(&["payload", "event", "record", "owner"]),
                    Pred::Eq(&["payload", "event", "record", "owner", "requester_kind"], "main"),
                    Pred::IsObject(&["payload", "event", "record", "quantity"]),
                ]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Of(&["payload", "event", "record", "quantity", "input_tokens"]),
                    output: Num::Of(&["payload", "event", "record", "quantity", "output_tokens"]),
                    cache_read: Num::Zero,
                    cache_creation: Num::Zero,
                    key: Text::Py(&["payload", "event", "record", "usage_id"]),
                    key_required: true,
                    key_invalid: "",
                    model: Text::Lit(""),
                    timestamp: TS,
                    cli_version: Text::Slot("cli_version"),
                    cwd: Text::Slot("cwd"),
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
                // A submitted turn (slash commands, cancels and other intake kinds are not).
                Rule {
                    when: Pred::All(&[
                        Pred::Eq(&["payload_type"], "runtime.command_intake.received"),
                        Pred::IsObject(&["payload", "record"]),
                        Pred::IsObject(&["payload", "record", "command"]),
                        Pred::Eq(&["payload", "record", "command", "kind"], "turn_submit"),
                    ]),
                    effects: &[
                        Effect::Emit(Emit {
                            kind: EventKind::AgentActive,
                            key: LINE_KEY,
                            detail: Text::Lit("user"),
                            text: Text::UserPrompt(&Text::Py(&["payload", "record", "command", "prompt"])),
                            timestamp: TS,
                            session: None,
                            require_text: false,
                        }),
                        Effect::Stop,
                    ],
                },
                Rule {
                    when: Pred::All(&[RUN_EVENT, Pred::Eq(KIND, "assistant_message_committed")]),
                    effects: &[Effect::SetIfNonEmpty("reply", Text::Cap(&Text::Strip(&Text::Py(&["payload", "event", "text"])), 4000))],
                },
                // Work events only: muse logs its task graph in the same stream.
                Rule {
                    when: Pred::All(&[
                        RUN_EVENT,
                        Pred::In(KIND, &["started", "assistant_message_committed", "side_effect_intent", "model_completed", "completed", "failed"]),
                    ]),
                    effects: &[Effect::Emit(Emit {
                        kind: EventKind::AgentActive,
                        key: LINE_KEY,
                        detail: Text::Py(KIND),
                        text: Text::Lit(""),
                        timestamp: TS,
                        session: None,
                        require_text: false,
                    })],
                },
                // Any terminal value ends the turn; the value rides on detail.
                Rule {
                    when: Pred::All(&[RUN_EVENT, Pred::Eq(KIND, "terminal")]),
                    effects: &[
                        Effect::Emit(Emit {
                            kind: EventKind::TurnComplete,
                            key: Text::Concat(&[Text::Lit("turn:"), Text::Line]),
                            detail: Text::Py(&["payload", "event", "terminal"]),
                            text: Text::Slot("reply"),
                            timestamp: TS,
                            session: None,
                            require_text: false,
                        }),
                        Effect::Set("reply", Text::Lit("")),
                    ],
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &["reply"],
            bag: Bag { slots: &[BagSlot::Text { slot: "reply", prefix: "muse_text::" }], turn: None, cursor: None },
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
