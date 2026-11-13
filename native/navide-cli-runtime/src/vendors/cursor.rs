//! Cursor CLI. One SQLite store per session, `~/.cursor/chats/<hash>/<uuid>/store.db`,
//! whose `blobs` table mixes JSON chat messages with protobuf structure nodes
//! (never decoded). No token usage is stored locally, so there is no usage and
//! no turns. Activity is one event per new JSON message: an assistant message
//! ends the turn, a user message counts only when it carries what the person
//! typed (`<user_query>`). The blobs have no time; the sidecar `meta.json`
//! (also the only record of the cwd) stamps the whole pass.
use crate::decl::*;

const CONTENT: Text = Text::Blocks(&["data", "content"], "text");
const ROLE: Text = Text::Py(&["data", "role"]);
/// What the person typed, inside Cursor's prompt wrapper. A user row without
/// it is the context Cursor injects (rules, transcripts), not a prompt.
const TYPED: Text = Text::Enclosed(&CONTENT, "<user_query>", "</user_query>");

const fn event(kind: EventKind, detail: Str, text: Text) -> [Effect; 2] {
    [
        Effect::Emit(Emit {
            kind,
            key: Text::Concat(&[Text::Lit("blob:"), Text::Py(&["id"])]),
            detail: Text::Lit(detail),
            text,
            // Each event 1 ms past the previous one: two turns of one pass must not share a stamp.
            timestamp: Text::PassClock(&Text::Slot("updated")),
            session: None,
            require_text: false,
        }),
        Effect::Stop,
    ]
}

static DB: Sqlite = Sqlite {
    json: &["data"],
    requires: &[],
    usage: None,
    activity: Some(Rows {
        sql: "SELECT rowid AS row, id, data FROM blobs WHERE rowid > ?1 ORDER BY rowid LIMIT ?2",
        id: "row",
        newest: "SELECT COALESCE(MAX(rowid), 0) FROM blobs",
        page: 512,
        // A shrunk store (recreated, vacuumed) re-anchors at its newest row.
        restart_below: true,
        hold_last: None,
        partition: None,
    }),
};

pub static CLI: Cli = Cli {
    key: "cursor",
    label: "Cursor CLI",
    kind: Kind::Cli,
    launch: Launch {
        program: "agent",
        base_args: &[],
        yolo_args: &["--force"],
        // Effort is part of the model id (`gpt-5.3-codex-high`).
        model: Some(&["--model", "{}"]),
        effort: None,
        efforts: &[],
        env_defaults: &[],
        env_strip: &[],
    },
    resume: Resume {
        args: Some(&["--resume={id}"]),
        parse: Some(ResumeParse {
            programs: &["agent", "cursor-agent"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--resume"],
            joined: Some(ValueShape::Any),
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[], home: &[&[".cursor", "chats"]], files: &[&["*", "*", "store.db"]] },
        format: Format::Sqlite(&DB),
        accept: Some(Accept { name: "store.db", not_under: None, dir: Some(ValueShape::Uuid) }),
        slots: &[
            Slot { name: "session_id", init: SlotInit::DirName(1) },
            Slot { name: "cwd", init: SlotInit::SidecarJson { up: 1, file: "meta.json", keys: &["cwd"] } },
            Slot { name: "updated", init: SlotInit::SidecarJson { up: 1, file: "meta.json", keys: &["updatedAtMs"] } },
        ],
        usage: None,
        activity: ActivitySpec {
            slots: &[],
            cursor: Cursor::Rows,
            rules: &[
                Rule { when: Pred::Not(&Pred::IsObject(&["data"])), effects: &[Effect::Stop] },
                Rule {
                    when: Pred::Text(ROLE, StrTest::Eq("assistant")),
                    effects: &event(EventKind::TurnComplete, "assistant", Text::Cap(&CONTENT, 8000)),
                },
                Rule {
                    when: Pred::All(&[
                        Pred::Text(ROLE, StrTest::Eq("user")),
                        Pred::Encloses(CONTENT, "<user_query>", "</user_query>"),
                        // Navide's own session marker, typed in at spawn: not user activity.
                        Pred::Not(&Pred::Text(TYPED, StrTest::SessionMarker)),
                    ]),
                    effects: &event(EventKind::AgentActive, "user", Text::UserPrompt(&TYPED)),
                },
            ],
            idle: None,
            turn_stamp: TurnStamp::Text,
            turn_text: "reply",
            persist: &[],
            bag: Bag { slots: &[], turn: None, cursor: Some("cursor_blob::"), anchor: None },
        },
        turns: TurnsSpec {
            slots: &[],
            // No token usage on disk: no turns to cut.
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
