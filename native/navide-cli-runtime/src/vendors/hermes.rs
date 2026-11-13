//! Hermes Agent. One SQLite store, `$HERMES_HOME|~/.hermes/state.db`.
//! Tokens are the growth of per-session counters (no per-call rows, so no
//! turns); activity is one event per new `messages` row, and an assistant row
//! with a final `finish_reason` ends the turn. A session's id is its
//! lineage's Navide name (`navide-…`), found up to eight parents up.
use crate::decl::*;
use crate::expr::py_strip;

/// Hook: Hermes names a continued session `<name> #<n>`, and main reads the
/// name with a regular expression (` #\d+$`) that the record vocabulary has
/// no form for. Only a Navide-started name (`navide-…`) counts.
fn session_name(title: &str) -> String {
    let title = py_strip(title);
    let name = match title.rfind(" #") {
        Some(at) if at + 2 < title.len() && title[at + 2..].chars().all(char::is_numeric) => &title[..at],
        _ => title,
    };
    if name.starts_with("navide-") { name.to_owned() } else { String::new() }
}

/// `l0` (the row's own session) and its parents `l1`…`l7`; a chain stops at a
/// session without a parent.
macro_rules! lineage {
    () => {
        "LEFT JOIN sessions l1 ON l1.id = l0.parent_session_id AND l0.parent_session_id != '' \
         LEFT JOIN sessions l2 ON l2.id = l1.parent_session_id AND l1.parent_session_id != '' \
         LEFT JOIN sessions l3 ON l3.id = l2.parent_session_id AND l2.parent_session_id != '' \
         LEFT JOIN sessions l4 ON l4.id = l3.parent_session_id AND l3.parent_session_id != '' \
         LEFT JOIN sessions l5 ON l5.id = l4.parent_session_id AND l4.parent_session_id != '' \
         LEFT JOIN sessions l6 ON l6.id = l5.parent_session_id AND l5.parent_session_id != '' \
         LEFT JOIN sessions l7 ON l7.id = l6.parent_session_id AND l6.parent_session_id != '' "
    };
}
macro_rules! titles {
    () => {
        "json_array(l0.title, l1.title, l2.title, l3.title, l4.title, l5.title, l6.title, l7.title) AS titles"
    };
}

const NAME: Text = Text::Hook(&Text::Str(&[]), session_name);
/// The lineage's Navide name, else the session's own id.
const SESSION: Text = Text::FirstOf(&[
    Text::FirstItem { list: &["titles"], filter: &Pred::Text(NAME, StrTest::NonEmpty), item: &NAME },
    Text::Py(&["sid"]),
]);
/// `messages.content` is plain text, or a JSON list of content blocks.
const MESSAGE: Text = Text::Concat(&[
    Text::If(&Pred::All(&[Pred::IsString(&["content"]), Pred::IsList(&["blocks"])]), &Text::Blocks(&["blocks"], "text")),
    Text::If(&Pred::Not(&Pred::IsList(&["blocks"])), &Text::Str(&["content"])),
]);
const ASSISTANT: Pred = Pred::Eq(&["role"], "assistant");
const TS: Text = Text::IsoFromFloat(&["ts"]);

const fn event(kind: EventKind, detail: Text, text: Text, require_text: bool) -> [Effect; 2] {
    [
        Effect::Emit(Emit {
            kind,
            key: Text::Concat(&[Text::Lit("msg:"), Text::Num(&Num::Of(&["id"]))]),
            detail,
            text,
            timestamp: TS,
            session: None,
            require_text,
        }),
        Effect::Stop,
    ]
}

static DB: Sqlite = Sqlite {
    json: &["blocks", "titles"],
    binary: &[],
    requires: &[
        (
            "sessions",
            &["id", "title", "parent_session_id", "cwd", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "last_activity_at"],
        ),
        ("messages", &["id", "session_id", "role", "content", "tool_calls", "timestamp", "finish_reason"]),
    ],
    usage: Some(Rows {
        sql: concat!(
            "SELECT l0.id AS sid, l0.cwd AS cwd, l0.input_tokens AS input_tokens, l0.output_tokens AS output_tokens, ",
            "l0.cache_read_tokens AS cache_read_tokens, l0.cache_write_tokens AS cache_write_tokens, l0.last_activity_at AS ts, ",
            titles!(),
            " FROM sessions l0 ",
            lineage!()
        ),
        id: "sid",
        newest: "",
        page: 0,
        restart_below: false,
        hold_last: None,
        partition: None,
        revisit: None,
        last_as: None,
    }),
    activity: Some(Rows {
        sql: concat!(
            "SELECT m.id AS id, m.session_id AS sid, m.role AS role, m.content AS content, m.content AS blocks, ",
            "m.tool_calls AS tool_calls, m.timestamp AS ts, m.finish_reason AS finish, l0.cwd AS cwd, ",
            titles!(),
            " FROM messages m LEFT JOIN sessions l0 ON l0.id = m.session_id ",
            lineage!(),
            "WHERE m.id > ? ORDER BY m.id LIMIT ?"
        ),
        id: "id",
        newest: "SELECT COALESCE(MAX(id), 0) FROM messages",
        page: 512,
        // A replaced store starts over at its newest row.
        restart_below: true,
        // The newest row is an assistant reply with no finish_reason yet: read
        // it again next pass rather than lose its turn end.
        hold_last: Some(Pred::All(&[ASSISTANT, Pred::Not(&Pred::Truthy(&["finish"])), Pred::Not(&Pred::Truthy(&["tool_calls"]))])),
        partition: None,
        revisit: None,
        last_as: None,
    }),
};

pub static CLI: Cli = Cli {
    key: "hermes",
    label: "Hermes Agent",
    kind: Kind::Agent,
    launch: Launch {
        program: "hermes",
        base_args: &["chat"],
        yolo_args: &["--yolo"],
        // Every pane launches through `--continue`, which refuses `-m` / `--reasoning`.
        model: None,
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--continue", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["hermes"],
            tokens: Tokens::Literal,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["-c", "--continue"],
            // Only a Navide-started session name.
            joined: Some(ValueShape::Prefix("navide-")),
            pick: Pick::First,
            value: ValueShape::Prefix("navide-"),
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("HERMES_HOME", &[])], home: &[&[".hermes"]], files: &[&["state.db"]] },
        format: Format::Sqlite(&DB),
        accept: Some(Accept { name: "state.db", not_under: None, dir: None }),
        slots: &[],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::True,
                effects: &[],
                reading: Some(Reading {
                    input: Num::Sum(&[
                        Num::Of(&["delta", "input_tokens"]),
                        Num::Of(&["delta", "cache_read_tokens"]),
                        Num::Of(&["delta", "cache_write_tokens"]),
                    ]),
                    output: Num::Of(&["delta", "output_tokens"]),
                    cache_read: Num::Of(&["delta", "cache_read_tokens"]),
                    cache_creation: Num::Of(&["delta", "cache_write_tokens"]),
                    key: Text::Concat(&[Text::Lit("hermes:"), Text::Py(&["sid"]), Text::Lit(":"), Text::Py(&["counters"])]),
                    key_required: false,
                    key_invalid: "",
                    model: Text::Lit(""),
                    timestamp: TS,
                    cli_version: Text::Lit(""),
                    cwd: Text::Py(&["cwd"]),
                    session_id: SESSION,
                    raw: &[],
                }),
            }],
            mode: UsageMode::Counters {
                kind: "hermes_counters",
                map: "sessions",
                columns: &["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"],
            },
            persist: &[],
            flags: &[],
            restore: Restore::UnlessReplaced,
            stamp: &[],
        }),
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Rows,
            rules: &[
                Rule { when: Pred::True, effects: &[Effect::Set("cwd", Text::Py(&["cwd"])), Effect::Set("session_id", SESSION)] },
                Rule { when: Pred::Eq(&["role"], "user"), effects: &event(EventKind::AgentActive, Text::Lit("user"), Text::UserPrompt(&MESSAGE), true) },
                Rule {
                    when: Pred::All(&[
                        ASSISTANT,
                        Pred::Truthy(&["finish"]),
                        Pred::Not(&Pred::Eq(&["finish"], "tool_calls")),
                        Pred::Not(&Pred::Truthy(&["tool_calls"])),
                    ]),
                    effects: &event(EventKind::TurnComplete, Text::Py(&["finish"]), Text::Cap(&MESSAGE, 8000), false),
                },
                Rule { when: Pred::In(&["role"], &["assistant", "tool"]), effects: &event(EventKind::AgentActive, Text::Py(&["role"]), Text::Lit(""), false) },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: Some("hermes_msg::"), anchor: None },
        },
        turns: TurnsSpec {
            slots: &[],
            // Token events are counter growth, not per-call usage: no turns to cut.
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
