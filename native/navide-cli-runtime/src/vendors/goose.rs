//! Goose. One SQLite database for every session:
//! `$GOOSE_PATH_ROOT/data|$XDG_DATA_HOME/goose|~/.local/share/goose` + `/sessions/sessions.db`.
//! `usage_ledger` has a row per model call; `messages` a row per message,
//! and a text-only assistant row of a top-level session ends the turn.
use crate::decl::*;

const TS: Text = Text::IsoFromSeconds(&Num::Of(&["created"]));
const CONTENT: Path = &["content"];
const TOOL_REQUEST: Pred = Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "toolRequest"));
const TOOL_RESPONSE: Pred = Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "toolResponse"));
const ACTION_REQUIRED: Pred = Pred::AnyItem(CONTENT, &Pred::Eq(&["type"], "actionRequired"));
/// main's `_text_of`: the stripped, non-empty text blocks, one per line.
const TEXT: Text =
    Text::Join { list: CONTENT, filter: &Pred::Eq(&["type"], "text"), item: &Text::Strip(&Text::Py(&["text"])), sep: "\n", keep_empty: false };
const ASSISTANT: Pred = Pred::Eq(&["role"], "assistant");
const USER: Pred = Pred::Eq(&["role"], "user");
const REPLY: Pred = Pred::All(&[ASSISTANT, Pred::Text(TEXT, StrTest::NonEmpty), Pred::Not(&Pred::IsFalse(&["meta", "agentVisible"]))]);

/// Every row is one event; the first rule that matches it decides which.
const fn event(kind: EventKind, detail: Text, text: Text) -> [Effect; 2] {
    [
        Effect::Emit(Emit {
            kind,
            key: Text::Concat(&[Text::Lit("message:"), Text::Num(&Num::Of(&["id"]))]),
            detail,
            text,
            timestamp: TS,
            session: None,
            require_text: false,
        }),
        Effect::Stop,
    ]
}
const fn active(detail: Str) -> [Effect; 2] {
    event(EventKind::AgentActive, Text::Lit(detail), Text::Lit(""))
}

static DB: Sqlite = Sqlite {
    json: &["content", "meta"],
    requires: &[],
    usage: Some(Rows {
        sql: "SELECT u.id AS id, u.session_id AS session_id, u.created_timestamp AS created, COALESCE(u.model, '') AS model, \
              u.input_tokens AS input, u.output_tokens AS output, u.cache_read_tokens AS cache_read, \
              u.cache_write_tokens AS cache_write, COALESCE(s.working_dir, '') AS cwd \
              FROM usage_ledger u JOIN sessions s ON s.id = u.session_id WHERE u.id > ? ORDER BY u.id",
        id: "id",
        newest: "SELECT COALESCE(MAX(id), 0) FROM usage_ledger",
        page: 0,
        restart_below: false,
        hold_last: None,
        partition: None,
    }),
    activity: Some(Rows {
        sql: "SELECT m.id AS id, m.session_id AS session_id, m.role AS role, m.content_json AS content, \
              m.metadata_json AS meta, m.created_timestamp AS created, COALESCE(s.working_dir, '') AS cwd, \
              s.parent_session_id AS parent, s.session_type AS session_type \
              FROM messages m JOIN sessions s ON s.id = m.session_id WHERE m.id > ? ORDER BY m.id LIMIT ?",
        id: "id",
        newest: "SELECT COALESCE(MAX(id), 0) FROM messages",
        page: 256,
        restart_below: false,
        hold_last: None,
        partition: None,
    }),
};

pub static CLI: Cli = Cli {
    key: "goose",
    label: "Goose (AAIF)",
    kind: Kind::Cli,
    launch: Launch {
        program: "goose",
        base_args: &[],
        // Approval is the GOOSE_MODE setting, not a flag.
        yolo_args: &[],
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["session", "-r", "--session-id", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["goose"],
            tokens: Tokens::Whitespace,
            subcommand: &["session", "s"],
            id_after_subcommand: false,
            flags: &["--session-id"],
            joined: Some(ValueShape::NoDash),
            pick: Pick::First,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store {
            env: &[("GOOSE_PATH_ROOT", &["data", "sessions"]), ("XDG_DATA_HOME", &["goose", "sessions"])],
            home: &[&[".local", "share", "goose", "sessions"]],
            files: &[&["sessions.db"]],
        },
        format: Format::Sqlite(&DB),
        accept: Some(Accept { name: "sessions.db", not_under: None, dir: None }),
        slots: &[],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::True,
                effects: &[],
                reading: Some(Reading {
                    input: Num::Of(&["input"]),
                    output: Num::Of(&["output"]),
                    cache_read: Num::Of(&["cache_read"]),
                    cache_creation: Num::Of(&["cache_write"]),
                    key: Text::Concat(&[Text::Lit("usage:"), Text::Num(&Num::Of(&["id"]))]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::Py(&["model"]),
                    timestamp: TS,
                    cli_version: Text::Lit(""),
                    cwd: Text::Py(&["cwd"]),
                    session_id: Text::Py(&["session_id"]),
                    raw: &[(&["row_id"], RawValue::Num(Num::Of(&["id"])))],
                }),
            }],
            mode: UsageMode::PerRecord { window: 0, keep_on_rotation: false, credited: false },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessReplaced,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Rows,
            rules: &[
                // Each row names its own session and cwd.
                Rule {
                    when: Pred::True,
                    effects: &[Effect::Set("cwd", Text::Py(&["cwd"])), Effect::Set("session_id", Text::Py(&["session_id"]))],
                },
                Rule {
                    when: Pred::All(&[ASSISTANT, TOOL_REQUEST]),
                    effects: &event(
                        EventKind::AgentActive,
                        Text::Concat(&[
                            Text::Lit("tool:"),
                            Text::FirstOf(&[
                                Text::FirstItem {
                                    list: CONTENT,
                                    filter: &Pred::Eq(&["type"], "toolRequest"),
                                    item: &Text::Py(&["toolCall", "value", "name"]),
                                },
                                Text::Lit("unknown"),
                            ]),
                        ]),
                        Text::Lit(""),
                    ),
                },
                Rule { when: Pred::All(&[ASSISTANT, ACTION_REQUIRED]), effects: &active("assistant:action-required") },
                // A sub-agent finished; its parent is still working.
                Rule {
                    when: Pred::All(&[REPLY, Pred::Any(&[Pred::Truthy(&["parent"]), Pred::Text(Text::Py(&["session_type"]), StrTest::Ne("user"))])]),
                    effects: &active("subagent:done"),
                },
                Rule { when: REPLY, effects: &event(EventKind::TurnComplete, Text::Lit("assistant"), TEXT) },
                Rule { when: ASSISTANT, effects: &active("assistant") },
                Rule { when: Pred::All(&[USER, TOOL_RESPONSE]), effects: &active("tool:result") },
                Rule { when: Pred::All(&[USER, ACTION_REQUIRED]), effects: &active("user:action-response") },
                // The CLI's own injected context is not typed.
                Rule {
                    when: Pred::All(&[USER, Pred::Any(&[Pred::Truthy(&["meta", "turnContext"]), Pred::IsFalse(&["meta", "userVisible"])])]),
                    effects: &active("user:context"),
                },
                Rule { when: USER, effects: &event(EventKind::AgentActive, Text::Lit("user"), Text::UserPrompt(&TEXT)) },
                Rule {
                    when: Pred::True,
                    effects: &event(EventKind::AgentActive, Text::FirstOf(&[Text::Py(&["role"]), Text::Lit("message")]), Text::Lit("")),
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: Some("goose_message::"), anchor: None },
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
