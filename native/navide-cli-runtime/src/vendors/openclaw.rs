//! OpenClaw. One SQLite store per agent,
//! `$OPENCLAW_STATE_DIR|~/.openclaw` + `agents/*/agent/openclaw-agent.sqlite`,
//! whose `transcript_events` holds every session's events under a per-session
//! `seq`. The store records no cwd. Tokens come from assistant messages that
//! carry `usage`; activity is one event per message row, and an assistant
//! message with a final `stopReason` ends the turn. Rows are read per session
//! (`Partition`), and each session's events carry its Navide session key.
use crate::decl::*;

/// The value at the path, only when it is a session key Navide handed out.
macro_rules! keyed {
    ($($p:literal),+) => {
        Text::If(&Pred::Text(Text::Str(&[$($p),+]), StrTest::StartsWith("agent:")), &Text::Str(&[$($p),+]))
    };
}

/// A readable `message` event (main's `_message`).
const MESSAGE_ROW: Pred = Pred::All(&[Pred::Eq(&["event", "type"], "message"), Pred::IsObject(&["event", "message"])]);
/// The message's role, else the row's `message_role`.
const ROLE: Text = Text::FirstOf(&[Text::Py(&["event", "message", "role"]), Text::Py(&["role"])]);
const ASSISTANT: Pred = Pred::Eq(&["event", "message", "role"], "assistant");
const CONTENT: Text = Text::Blocks(&["event", "message", "content"], "text");
const STOP: Path = &["event", "message", "stopReason"];
const TS: Text = Text::Stamp(&[&["event", "timestamp"], &["event", "message", "timestamp"]]);
const KEY: Text = Text::Py(&["key"]);

const fn event(kind: EventKind, detail: Text, text: Text, require_text: bool) -> [Effect; 2] {
    [
        Effect::Emit(Emit {
            kind,
            key: Text::Concat(&[Text::Lit("seq:"), Text::Py(&["sid"]), Text::Lit(":"), Text::Py(&["seq"])]),
            detail,
            text,
            timestamp: TS,
            session: Some(KEY),
            require_text,
        }),
        Effect::Stop,
    ]
}

/// One page of a session's rows after its watermark.
const PAGE: Str = "SELECT seq, session_id AS sid, message_role AS role, event_json AS event FROM transcript_events \
                   WHERE session_id = ?1 AND seq > ?2 ORDER BY seq LIMIT ?3";
/// A session's key: the id itself when it is one, else what the session
/// tables map it to (absent in some builds), else the session header's.
const SESSIONS: Partition = Partition {
    tops: "SELECT session_id AS part, MAX(seq) AS top FROM transcript_events GROUP BY session_id",
    label: &[
        ("SELECT ?1 AS label", keyed!("label")),
        ("SELECT session_key AS label FROM session_windows WHERE session_id = ?1 LIMIT 1", keyed!("label")),
        ("SELECT session_key AS label FROM session_nodes WHERE current_session_id = ?1 LIMIT 1", keyed!("label")),
        (
            "SELECT event_json AS header FROM transcript_events WHERE session_id = ?1 ORDER BY seq LIMIT 1",
            Text::If(&Pred::Eq(&["header", "type"], "session"), &Text::FirstOf(&[keyed!("header", "sessionKey"), keyed!("header", "key")])),
        ),
    ],
    label_as: "key",
};
/// A trailing assistant message without its stopReason yet is read again next pass.
const HOLD: Pred = Pred::All(&[MESSAGE_ROW, ASSISTANT, Pred::Not(&Pred::Truthy(STOP))]);

static DB: Sqlite = Sqlite {
    json: &["event", "header"],
    requires: &[("transcript_events", &["session_id", "seq", "event_json", "message_role"])],
    usage: Some(Rows { sql: PAGE, id: "seq", newest: "", page: 512, restart_below: false, hold_last: Some(HOLD), partition: Some(SESSIONS) }),
    // A session whose newest seq fell below its mark was rewritten: re-anchor there.
    activity: Some(Rows { sql: PAGE, id: "seq", newest: "", page: 512, restart_below: true, hold_last: Some(HOLD), partition: Some(SESSIONS) }),
};

pub static CLI: Cli = Cli {
    key: "openclaw",
    label: "OpenClaw",
    kind: Kind::Agent,
    launch: Launch {
        program: "openclaw",
        // The TUI on the local embedded runtime (a bare `openclaw` attaches to a gateway).
        base_args: &["tui", "--local"],
        yolo_args: &[],
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--session", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["openclaw"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--session"],
            joined: Some(ValueShape::Prefix("agent:")),
            pick: Pick::First,
            value: ValueShape::Prefix("agent:"),
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store {
            env: &[("OPENCLAW_STATE_DIR", &["agents"])],
            home: &[&[".openclaw", "agents"]],
            files: &[&["*", "agent", "openclaw-agent.sqlite"]],
        },
        format: Format::Sqlite(&DB),
        accept: Some(Accept { name: "openclaw-agent.sqlite", not_under: None, dir: None }),
        slots: &[],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[MESSAGE_ROW, ASSISTANT, Pred::IsObject(&["event", "message", "usage"])]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Sum(&[
                        Num::Float(&["event", "message", "usage", "input"]),
                        Num::OrZero(&Num::Float(&["event", "message", "usage", "cacheRead"])),
                        Num::OrZero(&Num::Float(&["event", "message", "usage", "cacheWrite"])),
                    ]),
                    output: Num::Float(&["event", "message", "usage", "output"]),
                    cache_read: Num::OrZero(&Num::Float(&["event", "message", "usage", "cacheRead"])),
                    cache_creation: Num::OrZero(&Num::Float(&["event", "message", "usage", "cacheWrite"])),
                    key: Text::Concat(&[Text::Lit("openclaw:"), Text::Py(&["sid"]), Text::Lit(":"), Text::Py(&["seq"])]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::Py(&["event", "message", "model"]),
                    timestamp: TS,
                    cli_version: Text::Lit(""),
                    cwd: Text::Lit(""),
                    session_id: KEY,
                    raw: &[],
                }),
            }],
            mode: UsageMode::Watermarks { kind: "openclaw_seq", map: "sessions" },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessReplaced,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Rows,
            rules: &[
                Rule { when: Pred::Not(&MESSAGE_ROW), effects: &[Effect::Stop] },
                Rule {
                    when: Pred::Text(ROLE, StrTest::Eq("user")),
                    effects: &event(EventKind::AgentActive, Text::Lit("user"), Text::UserPrompt(&CONTENT), true),
                },
                Rule {
                    when: Pred::All(&[Pred::Text(ROLE, StrTest::Eq("assistant")), Pred::Truthy(STOP), Pred::Not(&Pred::Eq(STOP, "toolUse"))]),
                    effects: &event(EventKind::TurnComplete, Text::Py(STOP), Text::Cap(&CONTENT, 8000), false),
                },
                Rule {
                    when: Pred::Text(ROLE, StrTest::Eq("assistant")),
                    effects: &event(EventKind::AgentActive, Text::Lit("assistant"), Text::Lit(""), false),
                },
                Rule {
                    when: Pred::Text(ROLE, StrTest::In(&["toolResult", "tool"])),
                    effects: &event(EventKind::AgentActive, Text::Lit("tool"), Text::Lit(""), false),
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: Some("openclaw_seq::"), anchor: Some("openclaw_anchor::") },
        },
        turns: TurnsSpec {
            slots: &[],
            // The generic cut re-reads activity from first sight, which anchors
            // every session (no replay): there would be no boundaries at all.
            method: TurnMethod::Unsupported,
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
