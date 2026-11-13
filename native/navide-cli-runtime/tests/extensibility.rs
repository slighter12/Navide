//! A CLI the crate has never heard of, declared only here, runs the whole
//! lifecycle: launch argv and env, resume parsing, usage with a checkpoint,
//! activity with a resumed walk, and inferred turns. Nothing in `src/`
//! changes for it — that is the extension contract.
use navide_cli_runtime::decl::*;
use navide_cli_runtime::launch::{self, Refused, Request};
use navide_cli_runtime::observe::{ActivityState, Observer};
use navide_cli_runtime::resume::{self, Command};
use serde_json::Map;
use std::io::Write;
use std::path::PathBuf;

const TS: Text = Text::Py(&["ts"]);
const LINE_KEY: Text = Text::Concat(&[Text::Lit("act:"), Text::Line]);

const fn active(detail: Str, text: Text) -> Effect {
    Effect::Emit(Emit { kind: EventKind::AgentActive, key: LINE_KEY, detail: Text::Lit(detail), text, timestamp: TS, session: None, require_text: false })
}

static TOY: Cli = Cli {
    key: "toy",
    label: "Toy CLI",
    kind: Kind::Cli,
    launch: Launch {
        program: "toy",
        base_args: &["chat"],
        yolo_args: &["--yes"],
        model: Some(&["-m", "{}"]),
        effort: Some(&["--think={}"]),
        efforts: &["low", "high"],
        env_defaults: &[("TOY_COLOR", "never")],
        env_strip: &["TOY_HOME"],
    },
    resume: Resume {
        args: Some(&["--continue", "{id}"]),
        parse: Some(ResumeParse {
            programs: &["toy"],
            tokens: Tokens::Whitespace,
            subcommand: &[],
            id_after_subcommand: false,
            flags: &["--continue", "-c"],
            joined: None,
            pick: Pick::Last,
            value: ValueShape::NoDash,
        }),
    },
    interrupt: b"\x03",
    observe: Some(Observe {
        store: Store { env: &[("TOY_HOME", &["logs"])], home: &[&[".toy", "logs"]], files: &[&["*.jsonl"]] },
        format: Format::Jsonl,
        accept: None,
        slots: &[
            Slot { name: "cwd", init: SlotInit::Header { lines: 1, when: Pred::Eq(&["type"], "meta"), field: Text::Py(&["cwd"]), first_line: true } },
            Slot { name: "session_id", init: SlotInit::FileStem },
        ],
        usage: Some(UsageSpec {
            slots: &[],
            rules: &[UsageRule {
                when: Pred::All(&[Pred::Eq(&["type"], "reply"), Pred::IsObject(&["usage"])]),
                effects: &[],
                reading: Some(Reading {
                    input: Num::Sum(&[Num::Of(&["usage", "in"]), Num::Of(&["usage", "cached"])]),
                    output: Num::Of(&["usage", "out"]),
                    cache_read: Num::Of(&["usage", "cached"]),
                    cache_creation: Num::Zero,
                    key: Text::Py(&["id"]),
                    key_required: true,
                    key_invalid: "",
                    model: Text::Py(&["model"]),
                    timestamp: TS,
                    cli_version: Text::Lit(""),
                    cwd: Text::Slot("cwd"),
                    session_id: Text::Slot("session_id"),
                    raw: &[],
                }),
            }],
            mode: UsageMode::PerRecord { window: 8, keep_on_rotation: false, credited: false },
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
                    when: Pred::Eq(&["type"], "user"),
                    effects: &[Effect::Turn(TurnOp::Open { boundary: "boundary", ts: TS, ms: Num::Zero }), active("user", Text::UserPrompt(&Text::Str(&["text"])))],
                },
                Rule {
                    when: Pred::Eq(&["type"], "reply"),
                    effects: &[
                        Effect::Turn(TurnOp::EnsureOpen { ts: TS, ms: Num::Zero }),
                        Effect::Turn(TurnOp::Touch { ts: Some(TS), ms: None }),
                        Effect::SetIfNonEmpty("reply", Text::Strip(&Text::Str(&["text"]))),
                        active("assistant", Text::Lit("")),
                    ],
                },
                Rule { when: Pred::Eq(&["type"], "done"), effects: &[Effect::Turn(TurnOp::Close { detail: "end_turn", ms: None })] },
            ],
            idle: None,
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

struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir().join(format!("navide-cli-runtime-{tag}-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        Dir(dir)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn append(path: &std::path::Path, lines: &[&str]) {
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path).unwrap();
    for line in lines {
        writeln!(file, "{line}").unwrap();
    }
}

#[test]
fn launch_and_resume_come_from_the_declaration() {
    let request = Request { yolo: true, model: Some("m1".into()), effort: Some("high".into()), resume: Some("s-9".into()) };
    assert_eq!(launch::argv(&TOY, &request).unwrap(), ["toy", "chat", "--continue", "s-9", "-m", "m1", "--think=high", "--yes"]);
    let bad = Request { effort: Some("max".into()), ..Request::default() };
    assert_eq!(launch::argv(&TOY, &bad), Err(Refused::UnknownEffort("max".into())));

    let inherited = vec![("TOY_HOME".to_owned(), "/elsewhere".to_owned()), ("PATH".to_owned(), "/bin".to_owned())];
    let env = launch::env(&TOY, &inherited, &[]);
    assert_eq!(env, [("PATH".to_owned(), "/bin".to_owned()), ("TOY_COLOR".to_owned(), "never".to_owned())]);

    assert_eq!(resume::id_from_command(&TOY, &Command::Text("toy chat -c old --continue new")), "new");
    assert_eq!(resume::id_from_command(&TOY, &Command::Text("toy chat --continue -x")), "");
    assert_eq!(resume::id_from_command(&TOY, &Command::Text("other --continue s")), "");
}

#[test]
fn observe_runs_end_to_end() {
    let dir = Dir::new("toy");
    let path = dir.0.join("sess-1.jsonl");
    append(
        &path,
        &[
            r#"{"type":"meta","cwd":"/work/toy"}"#,
            r#"{"type":"user","text":"  fix the build  ","ts":"2026-01-01T00:00:01Z"}"#,
            r#"{"type":"reply","id":"r1","model":"toy-1","text":" working ","ts":"2026-01-01T00:00:02Z","usage":{"in":10,"cached":5,"out":3}}"#,
        ],
    );
    let mut observer = Observer::new(&TOY).expect("toy declares observe");

    let first = observer.usage(&path, &Map::new()).unwrap();
    assert_eq!(first.events.len(), 1);
    let call = &first.events[0];
    assert_eq!((call.input_tokens, call.output_tokens, call.cache_read_tokens), (15, 3, 5));
    assert_eq!((call.cwd.as_str(), call.session_id.as_str(), call.model.as_str()), ("/work/toy", "sess-1", "toy-1"));

    let mut state = ActivityState::default();
    let events = observer.activity(&path, &mut state).unwrap();
    let details: Vec<&str> = events.iter().map(|e| e.detail.as_str()).collect();
    assert_eq!(details, ["user", "assistant"]);
    assert_eq!(events[0].text, "fix the build");
    assert_eq!(state.hw, 3);

    // A second reply and the end of the turn: only the new records count.
    append(
        &path,
        &[
            r#"{"type":"reply","id":"r2","model":"toy-1","text":"done it","ts":"2026-01-01T00:00:04Z","usage":{"in":4,"cached":0,"out":2}}"#,
            r#"{"type":"done","ts":"2026-01-01T00:00:05Z"}"#,
        ],
    );
    let second = observer.usage(&path, &first.checkpoint).unwrap();
    assert_eq!(second.events.iter().map(|e| e.dedup_key.as_str()).collect::<Vec<_>>(), ["r2"]);
    let events = observer.activity(&path, &mut state).unwrap();
    let kinds: Vec<(&str, &str)> = events.iter().map(|e| (e.event_type.as_str(), e.detail.as_str())).collect();
    assert_eq!(kinds, [("agent_active", "assistant"), ("turn_complete", "end_turn")]);
    assert_eq!(events[1].text, "done it");
    assert_eq!(events[1].timestamp, "2026-01-01T00:00:04Z");

    let turns = observer.turns(&path, "sess-1").unwrap();
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].prompt_excerpt, "fix the build");
    assert_eq!((turns[0].input, turns[0].output, turns[0].calls.len()), (19, 5, 2));
}
