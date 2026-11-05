//! Tests of the JSONL source: its bounds, the resumable passes and the named compatibility rules. Each bound was written
//! before the code that satisfies it (the failing runs are in the t35f report).
use super::adapters::{self, claude::Claude, codex::Codex, grok::Grok, pi::Pi, Mode};
use super::baseline::QuietTurn;
use super::blake2b;
use super::jsonl::{Flow, JsonlFile, JsonlObserver, Limits, LIMITS};
use super::testing::facts_with;
use super::{observe_with, Capability, Ctx, Cumulative, Cursor, NoCapability, PerTurn, Request, UsageScan};
use serde_json::{json, Value};
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};

fn scratch(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!("observe-jsonl-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    root
}

fn cursor(value: serde_json::Value) -> Cursor {
    Cursor(value.as_object().unwrap().clone())
}

/// The worst identity the core lets through: every field at its cap, every character a six-byte JSON escape (and, for the section an
/// Aider replay position carries, the same).
fn hot_ident() -> super::Ident {
    let hot = "\u{1}".repeat(super::answer::FIELD_CHARS);
    super::Ident {
        vendor: "v".into(),
        path: hot.clone(),
        stem: hot.clone(),
        native: hot.clone(),
        cwd: hot.clone(),
        model: hot.clone(),
        version: hot.clone(),
        base_epoch: 0.0,
        now_ms: 0,
        namespace: String::new(),
        section: hot.clone(),
        stamp: hot,
        wanted_session: String::new(),
        modified_ms: None,
    }
}

/// t36g N1: a refused activity line is put back from a snapshot that is taken only when the line's events could cross the ledger,
/// by the bound `12 x (open text + line) + ACTIVITY_MARGIN`. Each JSONL adapter's hook is pinned to it here, with the worst identity
/// the core lets through (every field at its cap, every character a six-byte JSON escape), so a hook that emits more per line fails
/// in CI and not in the field.
fn weight_of<O: JsonlObserver>(record: Value, open_text: usize) -> (usize, usize, usize) {
    let ident = hot_ident();
    let (mut last_text, mut state, mut out) = ("o".repeat(open_text), O::State::default(), Vec::new());
    O::activity(&mut last_text, &mut state, &ident, &record, 7, &mut out);
    let line = record.to_string().len();
    (out.len(), super::answer::event_bytes(&out), 12 * (open_text + line) + super::jsonl::ACTIVITY_MARGIN)
}

#[test]
fn every_jsonl_adapter_emits_per_line_within_the_snapshot_bound() {
    let stamp = "2026-05-28T00:00:00Z";
    let long = "x".repeat(100_000);
    let claude = json!({"type": "assistant", "timestamp": stamp, "message": {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": long},
        {"type": "tool_use", "name": "AskUserQuestion"},
        {"type": "tool_use", "name": "TaskStop", "input": {"task_id": "t"}},
        {"type": "tool_use", "name": "KillShell", "input": {"shell_id": "s"}}]}});
    let codex = json!({"type": "event_msg", "timestamp": stamp, "payload": {"type": "task_complete", "last_agent_message": long}});
    let copilot = json!({"type": "assistant.turn_end", "timestamp": stamp, "data": {}});
    for (name, (events, weight, bound)) in [
        ("claude", weight_of::<Claude>(claude, 0)),
        ("codex", weight_of::<Codex>(codex, 200_000)),
        ("copilot", weight_of::<super::adapters::copilot::Copilot>(copilot, 200_000)),
    ] {
        assert!(events >= 1 && events <= 6, "{name}: {events} events from one line");
        assert!(weight <= bound, "{name}: {weight} bytes of events from one line are over the bound {bound}");
    }
}

#[test]
fn a_fifo_is_refused_without_blocking_the_observer() {
    let root = scratch("fifo");
    let fifo = root.join("held.jsonl");
    let name = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
    let started = std::time::Instant::now();
    let error = JsonlFile::open_path(&fifo, LIMITS).err().expect("a FIFO is not a log file");
    assert!(error.to_string().contains("not a regular file"), "{error}");
    assert!(started.elapsed() < std::time::Duration::from_secs(2), "the open must not wait for a writer");
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_device_and_a_socket_are_refused() {
    assert!(JsonlFile::open_path(Path::new("/dev/zero"), LIMITS).is_err());
    let root = scratch("socket");
    let path = root.join("s.sock");
    let _listener = std::os::unix::net::UnixListener::bind(&path).unwrap();
    assert!(JsonlFile::open_path(&path, LIMITS).is_err());
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_symlink_is_not_followed() {
    let root = scratch("symlink");
    std::fs::write(root.join("real.jsonl"), b"{}\n").unwrap();
    std::os::unix::fs::symlink(root.join("real.jsonl"), root.join("link.jsonl")).unwrap();
    assert!(JsonlFile::open_path(&root.join("link.jsonl"), LIMITS).is_err());
    assert!(JsonlFile::open_path(&root.join("real.jsonl"), LIMITS).is_ok());
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn an_oversize_line_is_read_through_not_buffered_and_the_next_line_still_arrives() {
    let root = scratch("oversize");
    let path = root.join("log.jsonl");
    let mut file = File::create(&path).unwrap();
    file.write_all(b"{\"a\":1}\n").unwrap();
    file.write_all(&vec![b'x'; 5000]).unwrap();
    file.write_all(b"\n{\"b\":2}\n").unwrap();
    let mut log = JsonlFile::open_path(&path, Limits { line: 100, pass: LIMITS.pass }).unwrap();
    let mut seen = Vec::new();
    log.lines(0, 0, true, |line| {
        seen.push((line.no, line.oversize, line.complete, line.bytes.len()));
        Flow::Continue
    })
    .unwrap();
    assert_eq!(seen, vec![(1, false, true, 8), (2, true, true, 0), (3, false, true, 8)]);
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn the_pass_cap_stops_a_resumable_scan_before_the_line_that_crosses_it_and_refuses_one_that_is_not() {
    let root = scratch("pass");
    let path = root.join("log.jsonl");
    std::fs::write(&path, b"{\"a\":1}\n{\"a\":2}\n{\"a\":3}\n").unwrap();
    let mut log = JsonlFile::open_path(&path, Limits { line: 100, pass: 17 }).unwrap();
    let mut ends = Vec::new();
    let reached = log
        .lines(0, 0, true, |line| {
            ends.push(line.end);
            Flow::Continue
        })
        .unwrap();
    assert_eq!((ends, reached), (vec![8, 16], false), "the third line would cross the cap, so the pass ends on a line boundary");
    let error = log.lines(0, 0, false, |_| Flow::Continue).unwrap_err();
    assert!(error.to_string().contains("pass byte limit"));
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_line_that_alone_exceeds_the_pass_cap_is_refused_whole_and_never_buffered() {
    let root = scratch("one-line");
    let path = root.join("log.jsonl");
    std::fs::write(&path, format!("{{\"a\":\"{}\"}}\n", "x".repeat(300))).unwrap();
    let mut log = JsonlFile::open_path(&path, Limits { line: 100, pass: 100 }).unwrap();
    let mut delivered = 0;
    let error = log
        .lines(0, 0, true, |_| {
            delivered += 1;
            Flow::Continue
        })
        .unwrap_err();
    assert!(error.to_string().contains("pass byte limit"), "{error}");
    assert_eq!(delivered, 0, "the cap holds while the line is read, not after it was read in full");
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn an_anchor_mismatch_rotates_the_cursor_and_a_matching_one_does_not() {
    let root = scratch("anchor");
    let path = root.join("log.jsonl");
    std::fs::write(&path, b"{\"a\":1}\n{\"a\":2}\n").unwrap();
    let log = JsonlFile::open_path(&path, LIMITS).unwrap();
    let at = |anchor: &str| cursor(json!({"offset": 8, "identity": log.identity(), "anchor": anchor}));
    let honest = log.anchor_at(8).unwrap();
    assert_eq!(log.resume(&at(&honest), true).unwrap(), (false, false));
    assert_eq!(log.resume(&at("0000000000000000"), true).unwrap(), (true, false), "bytes differ from the anchor");
    assert_eq!(log.resume(&at(""), true).unwrap(), (false, false), "no saved anchor, nothing to compare");
    let shrunk = cursor(json!({"offset": 999, "identity": log.identity()}));
    assert_eq!(log.resume(&shrunk, true).unwrap(), (true, false), "a file shorter than the offset");
    let other = cursor(json!({"offset": 8, "identity": "1:2"}));
    assert_eq!(log.resume(&other, true).unwrap(), (true, true), "a different inode");
    let unknown = cursor(json!({"offset": 8}));
    assert_eq!(log.resume(&unknown, true).unwrap(), (true, false));
    assert_eq!(log.resume(&unknown, false).unwrap(), (false, false));
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn the_anchor_is_the_blake2b_8_hex_of_the_last_512_bytes() {
    let root = scratch("anchor-bytes");
    let path = root.join("log.jsonl");
    let body: Vec<u8> = (0..2000u32).map(|n| b'a' + (n % 23) as u8).collect();
    std::fs::write(&path, &body).unwrap();
    let log = JsonlFile::open_path(&path, LIMITS).unwrap();
    assert_eq!(log.anchor_at(1500).unwrap(), blake2b::hex_digest(&body[988..1500], 8));
    assert_eq!(log.anchor_at(10).unwrap(), blake2b::hex_digest(&body[..10], 8));
    assert_eq!(log.anchor_at(0).unwrap(), "");
    std::fs::remove_dir_all(&root).unwrap();
}

// ---- capped passes reach the end over several passes -----------------------------------------------------------------

/// One observation of `vendor`'s declaration over a file opened under `limits`.
fn pass<O, U, X>(facts: &'static adapters::ObserverFacts, path: &Path, mode: Mode, limits: Limits, wire: &Value) -> std::io::Result<Value>
where
    O: JsonlObserver,
    U: UsageScan<O, JsonlFile>,
    X: Capability<O, JsonlFile>,
{
    let mut meta = json!({"vendor": facts.vendor, "path": path, "mode": mode.as_str()});
    meta.as_object_mut().unwrap().extend(wire.as_object().cloned().unwrap());
    let request = Request::parse(&meta)?;
    let ctx = Ctx { req: &request, facts, core: &facts.core, layout: &facts.layouts[0] };
    observe_with::<O, JsonlFile, U, X>(&ctx, mode, JsonlFile::open_path(path, limits)?)
}

fn write(name: &str, body: &str) -> PathBuf {
    let path = scratch(name).join("rollout.jsonl");
    std::fs::write(&path, body).unwrap();
    path
}

fn keys(document: &Value) -> Vec<String> {
    document["events"].as_array().unwrap().iter().map(|event| event["dedup_key"].as_str().unwrap().to_owned()).collect()
}

#[test]
fn a_capped_activity_pass_reaches_the_end_over_several_passes() {
    let body: String = (1..=6).map(|n| format!("{{\"type\":\"assistant\",\"timestamp\":\"t{n}\"}}\n")).collect();
    let path = write("multipass", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 1 };
    let facts = adapters::find("codex").unwrap();
    let (mut wire, mut delivered, mut passes) = (json!({"seen": [], "state": {}}), Vec::new(), 0);
    while delivered.len() < 6 && passes < 10 {
        let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, limits, &wire).unwrap();
        delivered.extend(keys(&document));
        wire = json!({"seen": document["seen"], "state": document["state"]});
        passes += 1;
    }
    assert_eq!(delivered, ["act:1", "act:2", "act:3", "act:4", "act:5", "act:6"]);
    assert_eq!(passes, 3, "two lines per pass, nothing re-read from the top");
    assert_eq!(wire["state"]["position"]["line"], 6);
}

/// t36i T1: an activity pass that stopped before the end of the readable log (at the answer ledger or at the byte cap of a pass) says
/// `more`; the pass that reaches the end says nothing, and neither does one that ends at a line still being written.
#[test]
fn an_activity_pass_that_stopped_before_the_end_of_the_log_says_more() {
    let prompt = "p".repeat(200_000);
    let line = json!({"type": "event_msg", "payload": {"type": "user_message", "message": prompt}}).to_string();
    let body: String = (0..30).map(|_| format!("{line}\n")).collect();
    let path = write("activity-more-ledger", &body);
    let facts = adapters::find("codex").unwrap();
    let (mut wire, mut delivered, mut passes, mut mores) = (json!({"seen": [], "state": {}}), 0, 0, Vec::new());
    loop {
        let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &wire).unwrap();
        delivered += document["events"].as_array().unwrap().len();
        mores.push(document.get("more").is_some());
        wire = json!({"seen": document["seen"], "state": document["state"]});
        passes += 1;
        if document.get("more").is_none() || passes == 20 {
            break;
        }
    }
    assert_eq!(delivered, 30, "every prompt once");
    assert!(passes >= 2 && mores[..passes - 1].iter().all(|more| *more) && !mores[passes - 1], "`more` while the ledger stops the pass, none at the end: {mores:?}");

    let body: String = (1..=6).map(|n| format!("{{\"type\":\"assistant\",\"timestamp\":\"t{n}\"}}\n")).collect();
    let path = write("activity-more-cap", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 1 };
    let (mut wire, mut mores) = (json!({"seen": [], "state": {}}), Vec::new());
    for _ in 0..3 {
        let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, limits, &wire).unwrap();
        mores.push(document.get("more").is_some());
        wire = json!({"seen": document["seen"], "state": document["state"]});
    }
    assert_eq!(mores, [true, true, false], "the byte cap of a pass is a stop before the end too");

    let unfinished = write("activity-more-partial", &format!("{}\n{}", assistant_lines(2).trim_end(), "{\"type\":\"assi"));
    let document = pass::<Codex, Cumulative, NoCapability>(facts, &unfinished, Mode::Activity, LIMITS, &json!({"seen": [], "state": {}})).unwrap();
    assert!(document.get("more").is_none(), "a line still being written is the end of what can be read");
}

#[test]
fn a_capped_per_turn_usage_pass_reaches_the_end_through_its_checkpoint() {
    let call = |n: u32| json!({"type":"assistant","requestId":format!("r{n}"),"message":{"id":format!("m{n}"),"usage":{"input_tokens":n,"output_tokens":1}}});
    let body: String = (1..=5).map(|n| format!("{}\n", call(n))).collect();
    let path = write("usage-multipass", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 1 };
    let facts = adapters::find("claude").unwrap();
    let (mut wire, mut delivered) = (json!({"checkpoint": {}}), Vec::new());
    for _ in 0..4 {
        let document = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::Usage, limits, &wire).unwrap();
        delivered.extend(keys(&document));
        wire = json!({"checkpoint": document["checkpoint"]});
    }
    assert_eq!(delivered, ["m1::r1", "m2::r2", "m3::r3", "m4::r4", "m5::r5"]);
}

/// t36i T1: an incremental usage pass (per turn or cumulative) that stopped before the end of the readable log says `more`, so the
/// caller goes on from its checkpoint now; the pass that reaches the end, or ends at a line still being written, says nothing.
#[test]
fn an_incremental_usage_pass_that_stopped_before_the_end_of_the_log_says_more() {
    let call = |n: u32| json!({"type":"assistant","requestId":format!("r{n}"),"message":{"id":format!("m{n}"),"usage":{"input_tokens":n,"output_tokens":1}}});
    let body: String = (1..=5).map(|n| format!("{}\n", call(n))).collect();
    let path = write("usage-more-per-turn", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 1 };
    let facts = adapters::find("claude").unwrap();
    let (mut wire, mut mores) = (json!({"checkpoint": {}}), Vec::new());
    for _ in 0..3 {
        let document = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::Usage, limits, &wire).unwrap();
        mores.push(document.get("more").is_some());
        wire = json!({"checkpoint": document["checkpoint"]});
    }
    assert_eq!(mores, [true, true, false], "two lines per pass over five: the third reaches the end");

    let partial = write("usage-more-partial", &format!("{}{}", body, "{\"type\":\"assi"));
    let document = pass::<Claude, PerTurn, NoCapability>(facts, &partial, Mode::Usage, LIMITS, &json!({"checkpoint": {}})).unwrap();
    assert!(document.get("more").is_none(), "a line still being written is the end of what can be read");

    let total = |input: u64, output: u64| json!({"type":"event_msg","timestamp":"t","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":input,"output_tokens":output}}}});
    let body: String = [total(10, 1), total(25, 4), total(40, 9), total(70, 10)].iter().map(|record| format!("{record}\n")).collect();
    let path = write("usage-more-cumulative", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 8 };
    let facts = adapters::find("codex").unwrap();
    let (mut wire, mut mores) = (json!({"checkpoint": {}}), Vec::new());
    for _ in 0..2 {
        let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Usage, limits, &wire).unwrap();
        mores.push(document.get("more").is_some());
        wire = json!({"checkpoint": document["checkpoint"]});
    }
    assert_eq!(mores, [true, false]);
}

#[test]
fn a_capped_cumulative_usage_pass_emits_deltas_that_add_up_to_the_final_totals() {
    let total = |input: u64, output: u64| json!({"type":"event_msg","timestamp":"t","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":input,"output_tokens":output}}}});
    let body: String = [total(10, 1), total(25, 4), total(40, 9), total(70, 10)].iter().map(|record| format!("{record}\n")).collect();
    let path = write("cumulative-multipass", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 8 };
    let facts = adapters::find("codex").unwrap();
    let (mut wire, mut sums) = (json!({"checkpoint": {}}), (0, 0));
    for _ in 0..3 {
        let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Usage, limits, &wire).unwrap();
        for event in document["events"].as_array().unwrap() {
            sums = (sums.0 + event["input_tokens"].as_u64().unwrap(), sums.1 + event["output_tokens"].as_u64().unwrap());
        }
        wire = json!({"checkpoint": document["checkpoint"]});
    }
    assert_eq!(sums, (70, 10));
}

#[test]
fn a_pass_over_a_line_that_alone_exceeds_the_cap_is_refused_in_every_resumable_mode() {
    let record = format!("{{\"type\":\"assistant\",\"pad\":\"{}\"}}\n", "x".repeat(300));
    let path = write("refused", &record);
    let limits = Limits { line: 1000, pass: 100 };
    let (claude, codex) = (adapters::find("claude").unwrap(), adapters::find("codex").unwrap());
    let wire = json!({"seen": [], "state": {}, "checkpoint": {}});
    for refused in [
        pass::<Codex, Cumulative, NoCapability>(codex, &path, Mode::Activity, limits, &wire),
        pass::<Claude, PerTurn, NoCapability>(claude, &path, Mode::Usage, limits, &wire),
        pass::<Codex, Cumulative, NoCapability>(codex, &path, Mode::Usage, limits, &wire),
    ] {
        assert!(refused.unwrap_err().to_string().contains("pass byte limit"));
    }
}

#[test]
fn the_whole_file_modes_stay_non_resumable_and_refuse_a_file_over_the_cap() {
    let body: String = (1..=4).map(|n| format!("{{\"type\":\"assistant\",\"requestId\":\"r{n}\",\"message\":{{\"id\":\"m{n}\",\"usage\":{{\"input_tokens\":1}}}}}}\n")).collect();
    let path = write("whole-file", &body);
    let facts = adapters::find("claude").unwrap();
    let limits = Limits { line: 1000, pass: 100 };
    let wire = json!({"seen": []});
    let replay = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, limits, &wire);
    assert!(replay.unwrap_err().to_string().contains("pass byte limit"), "session_usage replays the whole file or refuses");
    let turns = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, limits, &wire);
    assert!(turns.unwrap_err().to_string().contains("pass byte limit"), "turns reads the whole file or refuses");
}

/// Q2: a whole-log replay that is over the pass event budget is answered in pages. A page says `more`, keeps a replay watermark
/// in the typed state, and answers only the keys it delivered; the caller adds them to the earlier pages' and asks again. The
/// pages together are the one read the log would have been.
#[test]
fn a_session_usage_replay_over_the_event_budget_is_paged_and_the_pages_are_the_whole_log_in_order() {
    let pad = "m".repeat(1000);
    let count = 9000;
    let body: String = (1..=count)
        .map(|n| format!("{{\"type\":\"assistant\",\"requestId\":\"r{n}\",\"timestamp\":\"2025-01-01T00:00:00Z\",\"message\":{{\"id\":\"m{n}\",\"model\":\"{pad}\",\"usage\":{{\"input_tokens\":1,\"output_tokens\":1}}}}}}\n"))
        .collect();
    let path = write("paged-replay", &body);
    let facts = adapters::find("claude").unwrap();
    let (mut seen, mut state, mut delivered, mut pages) = (Vec::<String>::new(), json!({}), Vec::<String>::new(), 0);
    loop {
        let document = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, LIMITS, &json!({"seen": seen, "state": state})).unwrap();
        pages += 1;
        let weight = super::answer::event_bytes(document["events"].as_array().unwrap()) + document["seen"].to_string().len();
        assert!(weight <= super::answer::LEDGER_BYTES, "no page is over the ledger: {weight}");
        delivered.extend(keys(&document));
        seen.extend(document["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()));
        state = document["state"].clone();
        assert!(document["seen"].as_array().unwrap().len() < count as usize, "a page answers only the keys it delivered");
        if document.get("more").is_none() {
            assert!(state.get("replay").is_none(), "the end of the replay clears the watermark: {state}");
            break;
        }
        assert!(state["replay"].is_object() || state["replay"].is_string() || !state["replay"].is_null(), "a page leaves its watermark: {state}");
        assert!(pages < 20, "the replay makes progress");
    }
    assert!(pages >= 2, "9,000 events of over 1 KB are not one page: {pages}");
    let expected: Vec<String> = (1..=count).map(|n| format!("m{n}::r{n}")).collect();
    assert_eq!(delivered, expected, "the pages are the whole log, once, in order");
    let mut sorted = seen.clone();
    sorted.sort();
    sorted.dedup();
    assert_eq!(sorted.len(), count as usize, "and the bag the caller built holds each key once");
}

/// t36l Y4: a replay page that resumes in the middle of an Aider history carries the section it stood in (the `# aider chat started at …`
/// header names the session and the time of the token lines below it) in its own saved position, so it reads nothing of the bytes before the
/// position. The log: the header, one three-megabyte line of output, then token lines, enough of them to fill the first page. The second page
/// is read under a pass limit that the bytes before its position (the header and the output) are over and the bytes after it are not, so a
/// resume that scans the prefix for the header fails the pass and one that carries the section does not.
#[test]
fn an_aider_replay_page_resumes_in_its_section_without_reading_the_bytes_before_its_position() {
    let namespace = "n".repeat(8000);
    let slug = format!("aider-{namespace}-20240501-100000");
    let count = 1000;
    let mut body = String::from("# aider chat started at 2024-05-01 10:00:00\n");
    body += &format!("{}\n", "x".repeat(3_000_000));
    for _ in 0..count {
        body += "> Tokens: 1 sent, 1 received.\n";
    }
    let path = write("aider-paged", &body);
    let facts = adapters::find("aider").unwrap();
    let wire = |seen: &[String], state: &Value| json!({"seen": seen, "state": state, "cwd": "/w", "namespace": namespace});
    let first = pass::<adapters::aider::Aider, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, LIMITS, &wire(&[], &json!({}))).unwrap();
    assert!(first.get("more").is_some(), "{count} token lines of a long section are over the first page");
    let (mut seen, mut state, mut delivered, mut pages) = (Vec::<String>::new(), first["state"].clone(), first["events"].as_array().unwrap().len(), 1);
    seen.extend(first["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()));
    // Every later page is read under a limit the bytes before its position are over and the bytes after it are not.
    let reduced = Limits { line: LIMITS.line, pass: 1_000_000 };
    loop {
        assert_eq!(state["replay"]["log"]["context"]["section"], slug, "the position carries the section it stands in: {}", state["replay"]);
        let page = pass::<adapters::aider::Aider, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, reduced, &wire(&seen, &state))
            .expect("a resumed page reads only the bytes after its position");
        pages += 1;
        let events = page["events"].as_array().unwrap();
        assert!(!events.is_empty(), "every page makes progress");
        assert!(
            events.iter().all(|event| event["session_id"] == slug && event["timestamp"] == "2024-05-01T10:00:00"),
            "every token line after a boundary belongs to the section above the boundary: {}",
            events[0]
        );
        delivered += events.len();
        seen.extend(page["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()));
        if page.get("more").is_none() {
            break;
        }
        state = page["state"].clone();
        assert!(pages < 10, "the replay makes progress");
    }
    assert!(pages >= 3, "{count} events of this weight are over two pages: {pages}");
    assert_eq!(delivered, count, "the pages are every token line once");
}

/// t36m Z2 (t36l review A5): a replay position with no context, for a vendor whose lines mean what the context above them says (Aider), is a
/// position a build before the context wrote (or one that lost it): resumed as it stands it would read the token lines after it in no
/// section and drop them. It is read as a restart instead (the seen bag keeps what the earlier page delivered), so the pages together are
/// every token line once, each in its section.
#[test]
fn an_aider_replay_position_without_its_context_starts_over_and_loses_no_token_line() {
    let namespace = "n".repeat(8000);
    let slug = format!("aider-{namespace}-20240501-100000");
    let count = 1000;
    let mut body = String::from("# aider chat started at 2024-05-01 10:00:00\n");
    for _ in 0..count {
        body += "> Tokens: 1 sent, 1 received.\n";
    }
    let path = write("aider-no-context", &body);
    let facts = adapters::find("aider").unwrap();
    let wire = |seen: &[String], state: &Value| json!({"seen": seen, "state": state, "cwd": "/w", "namespace": namespace});
    let first = pass::<adapters::aider::Aider, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, LIMITS, &wire(&[], &json!({}))).unwrap();
    assert!(first.get("more").is_some(), "{count} token lines are over the first page");
    let mut state = first["state"].clone();
    assert!(state["replay"]["log"]["context"].is_object(), "the position of this build carries its context: {}", state["replay"]);
    state["replay"]["log"].as_object_mut().unwrap().remove("context");
    let mut seen: Vec<String> = first["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()).collect();
    let (mut delivered, mut pages) = (first["events"].as_array().unwrap().len(), 1);
    loop {
        let page = pass::<adapters::aider::Aider, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, LIMITS, &wire(&seen, &state)).unwrap();
        pages += 1;
        let events = page["events"].as_array().unwrap();
        assert!(events.iter().all(|event| event["session_id"] == slug), "every token line is in its section: {:?}", events.first());
        delivered += events.len();
        seen.extend(page["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()));
        if page.get("more").is_none() {
            break;
        }
        state = page["state"].clone();
        assert!(pages < 20, "the replay makes progress");
    }
    assert_eq!(delivered, count, "the pages are every token line once");
}

/// The context a replay position carries is bounded like every persisted field: a section name of any weight (here twenty thousand
/// characters, each a six-byte JSON escape) leaves a state inside its share.
#[test]
fn the_section_an_aider_replay_position_carries_is_capped_inside_the_state_share() {
    let namespace = "\u{1}".repeat(20_000);
    let body = format!("# aider chat started at 2024-05-01 10:00:00\n{}", "> Tokens: 1 sent, 1 received.\n".repeat(200));
    let path = write("aider-paged-heavy", &body);
    let facts = adapters::find("aider").unwrap();
    let page = pass::<adapters::aider::Aider, PerTurn, NoCapability>(
        facts,
        &path,
        Mode::SessionUsage,
        LIMITS,
        &json!({"seen": [], "state": {}, "cwd": "/w", "namespace": namespace}),
    )
    .unwrap();
    assert!(page.get("more").is_some(), "200 events of this weight are over a page");
    let context = page["state"]["replay"]["log"]["context"]["section"].as_str().unwrap();
    assert!(context.chars().count() <= super::answer::FIELD_CHARS + 8, "the saved section is capped: {} characters", context.chars().count());
    let weight = page["state"].to_string().len();
    assert!(weight <= super::answer::STATE_BYTES, "the state of the page weighs {weight}, over its share");
}

/// t36l Y6: the quiet of a log is decided by the modification time of the file the pass holds open, not by what the path names when the pass
/// ends. The path is replaced by a newer file after the open; the open file is old enough, the replacement is not (its time is after the
/// wall clock the request names).
#[test]
fn the_quiet_of_a_log_is_read_from_the_file_the_pass_holds_open_and_not_from_what_its_path_names_now() {
    let user = json!({"type": "message", "id": "u1", "timestamp": "t1", "message": {"role": "user", "content": "hello"}});
    let path = write("quiet-handle", &format!("{user}\n"));
    let long_ago = std::time::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000);
    std::fs::File::options().write(true).open(&path).unwrap().set_modified(long_ago).unwrap();
    let held = JsonlFile::open_path(&path, LIMITS).unwrap();
    std::fs::remove_file(&path).unwrap();
    std::fs::write(&path, "{}\n").unwrap();
    let facts = adapters::find("pi").unwrap();
    let meta = json!({"vendor": "pi", "path": path, "mode": "activity", "seen": [], "state": {}, "cwd": "/w", "session": "s", "now_ms": 1_000_000_000i64 + 9_000});
    let request = Request::parse(&meta).unwrap();
    let ctx = Ctx { req: &request, facts, core: &facts.core, layout: &facts.layouts[0] };
    let document = observe_with::<Pi, JsonlFile, PerTurn, NoCapability>(&ctx, Mode::Activity, held).unwrap();
    let events = document["events"].to_string();
    assert!(events.contains("turn_complete") && events.contains("idle"), "the open file was quiet for nine seconds and its turn closes: {events}");
}

/// t36m Z1 (t36l review A1): the quiet of a log is judged after the pass has read, as the Python baseline stats the file after its read
/// loop. A reply appended while the pass runs (here: after the open, before the pass) makes the file not quiet, so the turn it belongs to
/// is not closed `idle` on the modification time the open saw. The time is read again from the same open handle just before `settle`.
#[test]
fn a_record_appended_during_the_pass_keeps_the_turn_from_closing_idle_on_the_modification_time_the_open_saw() {
    let user = json!({"type": "message", "id": "u1", "timestamp": "t1", "message": {"role": "user", "content": "hello"}});
    let reply = json!({"type": "message", "id": "a1", "timestamp": "t2", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}});
    let path = write("quiet-append", &format!("{user}\n"));
    let long_ago = std::time::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000);
    std::fs::File::options().write(true).open(&path).unwrap().set_modified(long_ago).unwrap();
    let held = JsonlFile::open_path(&path, LIMITS).unwrap();
    let mut other = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
    writeln!(other, "{reply}").unwrap();
    let facts = adapters::find("pi").unwrap();
    let meta = json!({"vendor": "pi", "path": path, "mode": "activity", "seen": [], "state": {}, "cwd": "/w", "session": "s", "now_ms": 1_000_000_000i64 + 9_000});
    let request = Request::parse(&meta).unwrap();
    let ctx = Ctx { req: &request, facts, core: &facts.core, layout: &facts.layouts[0] };
    let document = observe_with::<Pi, JsonlFile, PerTurn, NoCapability>(&ctx, Mode::Activity, held).unwrap();
    let events = document["events"].to_string();
    assert!(events.contains("a1") || events.contains("act:2"), "the appended reply was read in this pass: {events}");
    assert!(!events.contains("turn_complete"), "the file was written to during the pass, so it was not quiet: {events}");
}

/// t36l Y8 (B4 and R9 of the t36b review): a replay page that resumes from its position goes on numbering the lines where the position
/// stood. Kimi names a usage record by its place (`kimi::<session>::L<line>`), so a page that numbered from 1 again would repeat the keys of
/// the page before and the caller's bag would drop the records. Every seventh line is no usage (after every sixth record), so a line's number is not its place among the
/// events; the pages together are the usage records of the log under their absolute line numbers, once, in order. This pins the resume
/// (`replay_start` returns the position's line); it was written after the fix and is not red without reverting it.
#[test]
fn a_position_keyed_replay_goes_on_numbering_its_lines_across_the_pages() {
    let pad = "m".repeat(1000);
    let (count, mut body, mut expected, mut line) = (9000, String::new(), Vec::new(), 0);
    for n in 1..=count {
        line += 1;
        body += &format!("{{\"type\":\"usage.record\",\"model\":\"{pad}\",\"usage\":{{\"inputOther\":1,\"output\":1,\"inputCacheRead\":0,\"inputCacheCreation\":0}},\"time\":1700000000000}}\n");
        expected.push(format!("kimi::s::L{line}"));
        if n % 6 == 0 {
            line += 1;
            body += "{\"type\":\"turn.ended\",\"time\":1}\n";
        }
    }
    let path = write("kimi-paged", &body);
    let facts = adapters::find("kimi").unwrap();
    let (mut seen, mut state, mut delivered, mut pages) = (Vec::<String>::new(), json!({}), Vec::<String>::new(), 0);
    loop {
        let wire = json!({"seen": seen, "state": state, "cwd": "/w", "session": "s", "now_ms": 0});
        let page = pass::<adapters::kimi::Kimi, PerTurn, NoCapability>(facts, &path, Mode::SessionUsage, LIMITS, &wire).unwrap();
        pages += 1;
        delivered.extend(keys(&page));
        seen.extend(page["seen"].as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()));
        if page.get("more").is_none() {
            break;
        }
        state = page["state"].clone();
        assert!(pages < 20, "the replay makes progress");
    }
    assert!(pages >= 2, "9,000 events of over 1 KB are not one page: {pages}");
    assert_eq!(delivered, expected, "the usage records of the log under their absolute line numbers, once, in order");
}

// ---- the turns breakdown in pages -----------------------------------------------------------------------------------------

/// t36m Z5 (t36l A3): the row of a `turns` answer is the core's typed `TurnRow`, whichever adapter fills it. Its members, the totals its calls
/// add up to (saturating), the end of the turn (the latest call that has a time), the CLI version (the latest a call names), the calls the CLI
/// reported (absent, `null` or a number) and the numbering from 1 are one contract.
#[test]
fn a_turn_row_is_the_typed_row_of_the_python_readers_turn_usage() {
    use super::{TurnCall, TurnRow};
    let call = |ts: Option<&str>, parts: [u64; 4], version: &str| TurnCall {
        ts: ts.map(str::to_owned),
        model: "m".into(),
        input: parts[0],
        cache_read: parts[1],
        cache_creation: parts[2],
        output: parts[3],
        cli_version: version.into(),
    };
    let mut row = TurnRow::new("s".into(), Some("t0".into()));
    row.prompt_excerpt = "p".into();
    assert!(row.counts_nothing());
    row.add_call(call(Some("t1"), [1, 2, 3, 4], "1.0"));
    row.add_call(call(None, [10, 20, 30, 40], ""));
    row.add_call(call(Some("t3"), [u64::MAX, 0, 0, 0], "2.0"));
    assert!(!row.counts_nothing());
    let mut other = TurnRow::new("s".into(), None);
    other.model_calls = Some(None);
    let mut counted = TurnRow::new("s".into(), None);
    counted.model_calls = Some(Some(2));
    let rows = TurnRow::numbered(vec![row, other, counted]);
    assert_eq!(rows[0]["turn_index"], 1);
    assert_eq!((rows[0]["input"].clone(), rows[0]["cache_read"].clone(), rows[0]["cache_creation"].clone(), rows[0]["output"].clone()), (json!(u64::MAX), json!(22), json!(33), json!(44)));
    assert_eq!((rows[0]["started_at"].clone(), rows[0]["ended_at"].clone(), rows[0]["cli_version"].clone()), (json!("t0"), json!("t3"), json!("2.0")));
    assert_eq!(rows[0]["calls"][1], json!({"ts": null, "model": "m", "input": 10, "cache_read": 20, "cache_creation": 30, "output": 40, "cli_version": ""}));
    assert!(rows[0].get("model_calls").is_none(), "a vendor that never reports the calls has no member for them: {}", rows[0]);
    assert_eq!((rows[1]["turn_index"].clone(), rows[1]["model_calls"].clone(), rows[1]["ended_at"].clone()), (json!(2), Value::Null, Value::Null));
    assert!(rows[1].as_object().unwrap().contains_key("model_calls"), "reported as none is a null member");
    assert_eq!((rows[2]["turn_index"].clone(), rows[2]["model_calls"].clone()), (json!(3), json!(2)));
    let names: Vec<&str> = rows[0].as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(names, ["cache_creation", "cache_read", "calls", "cli_version", "ended_at", "input", "output", "prompt_excerpt", "session_id", "started_at", "turn_index"]);
}

fn claude_turns_log(prompts: usize, calls: usize, model: usize) -> String {
    let pad = "m".repeat(model);
    let mut body = String::new();
    for prompt in 1..=prompts {
        body += &format!("{{\"type\":\"user\",\"promptId\":\"p{prompt}\",\"message\":{{\"role\":\"user\",\"content\":\"go {prompt}\"}}}}\n");
        for call in 1..=calls {
            body += &format!(
                "{{\"type\":\"assistant\",\"sessionId\":\"abc-123\",\"requestId\":\"r{prompt}-{call}\",\"timestamp\":\"2026-05-28T00:00:00Z\",\"message\":{{\"id\":\"m{prompt}-{call}\",\"model\":\"{pad}\",\"role\":\"assistant\",\"usage\":{{\"input_tokens\":10,\"output_tokens\":5}}}}}}\n"
            );
        }
    }
    body
}

/// The breakdown as the caller reassembles it: rows, a continuation piece appended to the row before it, `restart` dropping what was held.
fn all_turns(path: &Path, mut each_page: impl FnMut(usize)) -> (Vec<Value>, usize) {
    let facts = adapters::find("claude").unwrap();
    let (mut rows, mut state, mut pages) = (Vec::<Value>::new(), json!({}), 0);
    loop {
        let page = pass::<Claude, PerTurn, super::ByTurns>(facts, path, Mode::Turns, LIMITS, &json!({"seen": [], "state": state})).unwrap();
        pages += 1;
        assert!(page.to_string().len() <= super::ANSWER_BYTES, "every page is inside the transport cap");
        if page.get("restart").is_some() {
            rows.clear();
        }
        for piece in page["turns"].as_array().unwrap() {
            match piece["continues"].as_str() {
                Some(list) => {
                    let more = piece["items"].as_array().unwrap().clone();
                    rows.last_mut().unwrap()[list].as_array_mut().unwrap().extend(more);
                }
                None => rows.push(piece.clone()),
            }
        }
        each_page(pages);
        if page.get("more").is_none() {
            assert!(page["state"].get("replay").is_none(), "the end of the breakdown clears its position");
            return (rows, pages);
        }
        state = page["state"].clone();
        assert!(pages < 50, "the breakdown makes progress");
    }
}

#[test]
fn a_turns_breakdown_over_the_ledger_is_paged_and_the_pages_are_the_whole_breakdown() {
    let path = write("turns-paged", &claude_turns_log(40, 1, 200_000));
    let (rows, pages) = all_turns(&path, |_| {});
    assert!(pages >= 3, "40 rows of 200 KB are not one page: {pages}");
    let indexes: Vec<i64> = rows.iter().map(|row| row["turn_index"].as_i64().unwrap()).collect();
    assert_eq!(indexes, (1..=40).collect::<Vec<i64>>(), "every row once, in order");
    assert!(rows.iter().all(|row| row["calls"].as_array().unwrap().len() == 1));
}

#[test]
fn one_turn_too_large_for_an_answer_comes_in_pieces_that_join_into_the_turn() {
    // Many calls with ordinary fields: nothing to cut, so the pieces are the row (a long field would be cut, see the next test).
    let path = write("turns-split", &claude_turns_log(2, 6000, 600));
    let (rows, pages) = all_turns(&path, |_| {});
    assert!(pages >= 3, "two turns of 4 MB each: {pages}");
    assert_eq!(rows.len(), 2);
    for row in &rows {
        assert_eq!(row["calls"].as_array().unwrap().len(), 6000, "no call is lost");
        assert_eq!((row["input"].as_u64(), row["output"].as_u64()), (Some(60_000), Some(30_000)));
        assert!(row["calls"].as_array().unwrap().iter().all(|call| call["model"].as_str().unwrap().len() == 600), "and no field is cut");
    }
}

const MIB: usize = 1024 * 1024;

/// t36i T2: a record with a 9 MiB `timestamp`, one with a 9 MiB `model`, and one with several megabytes in each of its three fields
/// (the line cap is 16 MiB): the answer is under the cap, the call keeps head and tail of every field, and the cut is reported.
#[test]
fn a_call_with_fields_of_megabytes_is_answered_cut_and_reported() {
    let long = |marker: &str, size: usize| format!("HEAD{}{marker}TAIL", "z".repeat(size));
    let record = |n: u32, timestamp: String, model: String, version: String| {
        json!({"type": "assistant", "sessionId": "abc-123", "requestId": format!("r{n}"), "timestamp": timestamp, "version": version,
            "message": {"id": format!("m{n}"), "model": model, "role": "assistant", "usage": {"input_tokens": 10, "output_tokens": 5}}})
    };
    let mut body = format!("{}\n", json!({"type": "user", "promptId": "p1", "message": {"role": "user", "content": "go"}}));
    for call in [
        record(1, long("t", 9 * MIB), "m".into(), "2.1".into()),
        record(2, "2026-05-28T00:00:00Z".into(), long("m", 9 * MIB), "2.1".into()),
        record(3, long("t", 5 * MIB), long("m", 5 * MIB), long("v", 5 * MIB)),
    ] {
        body += &format!("{call}\n");
    }
    let path = write("turns-long-fields", &body);
    let facts = adapters::find("claude").unwrap();
    let page = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": {}})).unwrap();
    assert!(page.to_string().len() < super::answer::LEDGER_BYTES, "the answer is inside the ledger: {}", page.to_string().len());
    assert!(page.get("more").is_none(), "after the cut the turn is one small row");
    let row = &page["turns"][0];
    assert_eq!(row["calls"].as_array().unwrap().len(), 3, "no call is lost");
    for text in [&row["calls"][0]["ts"], &row["calls"][1]["model"], &row["calls"][2]["ts"], &row["calls"][2]["model"], &row["calls"][2]["cli_version"], &row["cli_version"]] {
        let text = text.as_str().unwrap();
        assert!(text.chars().count() <= super::answer::FIELD_CHARS + 3, "{} chars", text.chars().count());
        if text.len() > 100 {
            assert!(text.starts_with("HEAD") && text.ends_with("TAIL"), "head and tail kept");
        }
    }
    let cuts = page["truncated"].as_array().unwrap();
    assert_eq!((cuts.len(), &cuts[0]["table"]), (1, &json!("turns")), "the cut is reported");
    let plain = write("turns-no-cut", &claude_turns_log(3, 2, 300));
    assert!(pass::<Claude, PerTurn, super::ByTurns>(facts, &plain, Mode::Turns, LIMITS, &json!({"seen": [], "state": {}})).unwrap().get("truncated").is_none());
}

fn append_turn_41(path: &Path) {
    let mut file = std::fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(claude_turns_log(1, 1, 10).replace("p1", "p41").replace("r1-", "r41-").replace("m1-", "m41-").as_bytes()).unwrap();
}

/// t36i T5: the pages of a breakdown are read over the prefix the first page saw. A log that only grows between two pages (the case
/// that matters: a live session being written while the monitor asks for its turns) neither restarts the breakdown nor adds the
/// appended turn to the pages that continue it; the next call sees it.
#[test]
fn a_log_that_grows_between_the_pages_of_a_breakdown_is_read_up_to_the_prefix_the_first_page_saw() {
    let path = write("turns-append", &claude_turns_log(40, 1, 200_000));
    let facts = adapters::find("claude").unwrap();
    let first = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": {}})).unwrap();
    assert!(first.get("more").is_some() && first.get("restart").is_none());
    append_turn_41(&path);
    let second = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": first["state"]})).unwrap();
    assert!(second.get("restart").is_none(), "an append is not a rewrite: the breakdown goes on");
    let delivered = first["turns"].as_array().unwrap().len();
    assert_eq!(second["turns"][0]["turn_index"].as_u64(), Some(delivered as u64 + 1), "from the row after the last one delivered");
    let path2 = write("turns-append-whole", &claude_turns_log(40, 1, 200_000));
    let (all, pages) = all_turns(&path2, |page| {
        if page == 1 {
            append_turn_41(&path2);
        }
    });
    assert!(pages >= 3);
    assert_eq!(all.iter().map(|row| row["turn_index"].as_i64().unwrap()).collect::<Vec<_>>(), (1..=40).collect::<Vec<i64>>(), "the breakdown the first page began, whole, once");
    assert_eq!(all_turns(&path2, |_| {}).0.len(), 41, "and a new breakdown sees the appended turn");
}

/// t36i T5: what restarts a breakdown is a rewrite: the log is another file, shorter than the prefix the first page saw, or holds other
/// bytes where that prefix ended.
#[test]
fn a_log_rewritten_between_the_pages_of_a_breakdown_starts_it_over_and_says_restart() {
    let path = write("turns-restart", &claude_turns_log(40, 1, 200_000));
    let facts = adapters::find("claude").unwrap();
    let first = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": {}})).unwrap();
    assert!(first.get("more").is_some() && first.get("restart").is_none());
    // Other bytes where the prefix ended (the file is longer, so only the fingerprint can tell).
    std::fs::write(&path, claude_turns_log(40, 1, 200_001)).unwrap();
    let second = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": first["state"]})).unwrap();
    assert!(second.get("restart").is_some(), "the prefix no longer holds: the breakdown starts over");
    assert_eq!(second["turns"][0]["turn_index"], 1, "from the first row");
    // A file shorter than the prefix.
    std::fs::write(&path, claude_turns_log(3, 1, 10)).unwrap();
    let third = pass::<Claude, PerTurn, super::ByTurns>(facts, &path, Mode::Turns, LIMITS, &json!({"seen": [], "state": first["state"]})).unwrap();
    assert!(third.get("restart").is_some() && third.get("more").is_none() && third["turns"].as_array().unwrap().len() == 3);
}

// ---- the activity resume position and the named water rule -----------------------------------------------------------

fn assistant_lines(count: u32) -> String {
    (1..=count).map(|n| format!("{{\"type\":\"assistant\",\"timestamp\":\"t{n}\"}}\n")).collect()
}

#[test]
fn a_replaced_file_keeps_the_old_water_under_the_named_rule_and_starts_over_without_it() {
    let facts_keep = adapters::find("codex").unwrap();
    let facts_reset = facts_with("codex", None, Some(false));
    let state = json!({"water": 3, "position": {"line": 3, "offset": 63, "identity": "1:1", "fingerprint": "00"}});
    let wire = json!({"seen": [], "state": state});
    let path = write("replaced", &assistant_lines(5));
    let kept = pass::<Codex, Cumulative, NoCapability>(facts_keep, &path, Mode::Activity, LIMITS, &wire).unwrap();
    assert_eq!(keys(&kept), ["act:4", "act:5"], "water_survives_replacement: the first lines of the new file stay skipped");
    let reset = pass::<Codex, Cumulative, NoCapability>(facts_reset, &path, Mode::Activity, LIMITS, &wire).unwrap();
    assert_eq!(keys(&reset), ["act:1", "act:2", "act:3", "act:4", "act:5"], "without the rule a replaced file is read from its start");
}

#[test]
fn a_replaced_file_that_ends_below_the_water_marks_what_it_holds_as_read() {
    let facts = adapters::find("codex").unwrap();
    let state = json!({"water": 50, "position": {"line": 50, "offset": 9999, "identity": "1:1", "fingerprint": "00"}});
    let path = write("shorter", &format!("{}\n{}", assistant_lines(2).trim_end(), "{\"type\":\"assi"));
    let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": [], "state": state})).unwrap();
    assert_eq!(keys(&document), Vec::<String>::new(), "nothing of the old transcript is replayed");
    assert_eq!(document["state"]["water"], 2, "the water drops to the last complete line, the unterminated one stays unread");
}

#[test]
fn a_position_the_file_no_longer_matches_is_not_trusted() {
    let path = write("rewritten", &assistant_lines(4));
    let facts = adapters::find("claude").unwrap();
    let first = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": [], "state": {}})).unwrap();
    assert_eq!(first["state"]["position"]["line"], 4);
    // Same inode, same length, different bytes before the saved offset.
    std::fs::write(&path, assistant_lines(4).replace("t1", "XX")).unwrap();
    let again = pass::<Claude, PerTurn, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": first["seen"], "state": first["state"]})).unwrap();
    assert_eq!(keys(&again), Vec::<String>::new(), "the water still holds under the named rule, and nothing new arrived");
}

// ---- every checkpoint the core returns is one the next request accepts (t36l Y2) -----------------------------------------

/// Drain `path`'s usage through its checkpoints, one pass at a time, each pass sent the checkpoint the one before returned (a request
/// over `CHECKPOINT_REQUEST_BYTES` is refused, so a checkpoint the core could not take back stops the vendor for good). Answers how many
/// events arrived and the widest checkpoint (an event's cursor or a pass's final one) in serialized bytes.
fn drain_usage<O: JsonlObserver>(facts: &'static adapters::ObserverFacts, path: &Path, request: &Value) -> (usize, usize) {
    let (delivered, widest, _) = drain_checkpoint::<O>(facts, path, request);
    (delivered, widest)
}

/// `drain_usage`, and the checkpoint the last pass left (what the next request carries).
fn drain_checkpoint<O: JsonlObserver>(facts: &'static adapters::ObserverFacts, path: &Path, request: &Value) -> (usize, usize, Value) {
    let (mut wire, mut delivered, mut widest) = (request.clone(), 0, 0);
    for _ in 0..120 {
        let document = pass::<O, PerTurn, NoCapability>(facts, path, Mode::Usage, LIMITS, &wire).expect("the checkpoint the core returned is accepted");
        for event in document["events"].as_array().unwrap() {
            widest = widest.max(event["checkpoint"].to_string().len());
        }
        widest = widest.max(document["checkpoint"].to_string().len());
        delivered += document["events"].as_array().unwrap().len();
        wire = json!({"checkpoint": document["checkpoint"], "cwd": request["cwd"], "session": request["session"]});
        if document.get("more").is_none() {
            break;
        }
    }
    (delivered, widest, wire["checkpoint"].clone())
}

/// A Pi session log of `count` assistant entries, each carrying usage and an entry id of `id(n)` (the usage key).
fn pi_entries(count: usize, id: impl Fn(usize) -> String) -> String {
    (0..count)
        .map(|n| format!("{}\n", json!({"type": "message", "id": id(n), "timestamp": "t", "message": {"role": "assistant", "model": "m", "usage": {"input": 1, "output": 1}}})))
        .collect()
}

/// The identity fields a log names for itself come into every checkpoint, each capped at `FIELD_CHARS` characters; the worst the cap lets
/// through is every character a six-byte JSON escape.
fn worst_identity() -> Value {
    let hot = "\u{1}".repeat(super::answer::FIELD_CHARS);
    json!({"cwd": hot, "session": hot})
}

#[test]
fn pi_with_256_entry_ids_of_a_kilobyte_leaves_a_checkpoint_the_next_request_accepts() {
    let facts = adapters::find("pi").unwrap();
    // 256 ids of 1,024 bytes: the window alone is 256 x 1,027 = 262,912 bytes, over the request bound before any other field.
    let path = write("window-plain", &pi_entries(256, |n| format!("{n:04}{}", "k".repeat(1020))));
    let (delivered, widest) = drain_usage::<Pi>(facts, &path, &json!({"checkpoint": {}, "cwd": "/w", "session": "s"}));
    assert_eq!(delivered, 256);
    assert!(widest <= super::answer::CHECKPOINT_REQUEST_BYTES, "a checkpoint of {widest} bytes is over what a request may carry");
    // The same, with the worst identity the core lets into a checkpoint beside the window.
    let (delivered, widest) = drain_usage::<Pi>(facts, &path, &json!({"checkpoint": {}, "cwd": worst_identity()["cwd"], "session": worst_identity()["session"]}));
    assert_eq!(delivered, 256);
    assert!(widest <= super::answer::CHECKPOINT_REQUEST_BYTES, "{widest}");
}

#[test]
fn a_window_of_escape_heavy_keys_is_bounded_by_its_serialized_weight_for_the_default_window_too() {
    let hot = "\u{1}".repeat(1020);
    // Pi: 256 keys of 1,024 bytes that serialize to six bytes each (1.5 MB of window).
    let pi = adapters::find("pi").unwrap();
    let path = write("window-escapes-pi", &pi_entries(256, |n| format!("{n:04}{hot}")));
    let identity = worst_identity();
    let (delivered, widest) = drain_usage::<Pi>(pi, &path, &json!({"checkpoint": {}, "cwd": identity["cwd"], "session": identity["session"]}));
    assert_eq!(delivered, 256);
    assert!(widest <= super::answer::CHECKPOINT_REQUEST_BYTES, "pi: {widest}");
    // Grok keeps the default window of 64 (64 x 6,150 = 393,600 bytes): its key is `usage:<event id>`.
    let grok = adapters::find("grok").unwrap();
    let body: String = (0..64)
        .map(|n| {
            let id = format!("{n:04}{}", "\u{1}".repeat(1010));
            format!("{}\n", json!({"params": {"sessionId": "s1", "_meta": {"eventId": id, "agentTimestampMs": 1_700_000_000_000i64 + n},
                "update": {"sessionUpdate": "turn_completed", "usage": {"inputTokens": 1, "outputTokens": 1}}}}))
        })
        .collect();
    let path = write("window-escapes-grok", &body);
    let (delivered, widest) = drain_usage::<Grok>(grok, &path, &json!({"checkpoint": {}, "cwd": identity["cwd"], "session": "s1"}));
    assert_eq!(delivered, 64);
    assert!(widest <= super::answer::CHECKPOINT_REQUEST_BYTES, "grok: {widest}");
}

/// t36m Z3 (t36l reviews A6 and N2): the key window a checkpoint persists is an upper bound, trimmed by serialized weight to fit what a request
/// may carry. This documents what the trimming means: with the worst identity beside 256 keys of a kilobyte, the checkpoint keeps fewer than
/// 256 of the newest keys; an entry whose id is among them is not counted again when the log repeats it, one whose id was trimmed away is
/// (the window is a bounded memory, here a shorter one). The pass's own window is whole, so nothing is counted twice inside one pass.
#[test]
fn an_id_repeated_after_the_window_was_trimmed_to_the_request_bound_is_counted_again_and_one_in_the_kept_window_is_not() {
    let facts = adapters::find("pi").unwrap();
    let id = |n: usize| format!("{n:04}{}", "k".repeat(1020));
    let path = write("window-trimmed-repeat", &pi_entries(256, id));
    let identity = worst_identity();
    let request = json!({"checkpoint": {}, "cwd": identity["cwd"], "session": identity["session"]});
    let (delivered, widest, checkpoint) = drain_checkpoint::<Pi>(facts, &path, &request);
    assert_eq!(delivered, 256);
    assert!(widest <= super::answer::CHECKPOINT_REQUEST_BYTES, "{widest}");
    let kept = checkpoint["recent_keys"].as_array().unwrap().len();
    assert!(kept < 256, "the worst identity leaves no room for 256 keys of a kilobyte: {kept} kept");
    assert!(kept > 100, "the window keeps most of what fits, not a token few: {kept} kept");
    // The oldest id (long gone from the persisted window) and the newest (in it) come again, appended to the log.
    let mut log = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
    log.write_all(pi_entries(1, |_| id(0)).as_bytes()).unwrap();
    log.write_all(pi_entries(1, |_| id(255)).as_bytes()).unwrap();
    let next = json!({"checkpoint": checkpoint, "cwd": identity["cwd"], "session": identity["session"]});
    let document = pass::<Pi, PerTurn, NoCapability>(facts, &path, Mode::Usage, LIMITS, &next).unwrap();
    let again = keys(&document);
    assert_eq!(again.len(), 1, "one of the two repeats is counted again: {again:?}");
    assert!(again[0].contains(&id(0)), "the repeat of the id the trimmed window forgot is the one counted again, not the newest: {}", &again[0][..again[0].len().min(80)]);
}

// ---- the adapter state is bounded where it is taken (t36l Y3) --------------------------------------------------------------

#[test]
fn a_pi_record_with_a_timestamp_of_four_mebibytes_does_not_stop_the_activity_of_its_file() {
    let facts = adapters::find("pi").unwrap();
    let user = |id: &str, ts: &str| format!("{}\n", json!({"type": "message", "id": id, "timestamp": ts, "message": {"role": "user", "content": "go"}}));
    let long = "9".repeat(4 * 1024 * 1024);
    let path = write("long-timestamp", &user("u1", &long));
    let request = json!({"seen": [], "state": {}, "cwd": "/w", "session": "s", "now_ms": 0});
    let first = pass::<Pi, PerTurn, NoCapability>(facts, &path, Mode::Activity, LIMITS, &request).expect("the pass answers whatever a timestamp weighs");
    assert!(first["state"]["adapter"]["ts"].as_str().unwrap().chars().count() <= super::answer::FIELD_CHARS + 3, "the timestamp the state keeps is capped");
    // The next pass is sent the state the first returned and answers too; a later user message closes the turn that held the long stamp.
    let mut more = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
    more.write_all(user("u2", "2026-07-27T10:00:00.000Z").as_bytes()).unwrap();
    let next = json!({"seen": first["seen"], "state": first["state"], "cwd": "/w", "session": "s", "now_ms": 0});
    let second = pass::<Pi, PerTurn, NoCapability>(facts, &path, Mode::Activity, LIMITS, &next).expect("the state the core returned is one it takes back");
    let closed: Vec<&Value> = second["events"].as_array().unwrap().iter().filter(|event| event["event_type"] == "turn_complete").collect();
    assert_eq!(closed.len(), 1, "the user message closes the turn: {second}");
    assert!(closed[0]["timestamp"].as_str().unwrap().chars().count() <= super::answer::FIELD_CHARS + 3);
}

/// The serialized weight of the whole state `O` saves (`Saved`): `adapter` as its state, and the core's own fields at their worst: a position
/// and a replay position (a vendor that resumes in its context, Aider, carries the context its `persist` writes for the worst identity), the
/// byte cursor of an older reader, and the largest line numbers and offsets. The open reply text (`last_text`) has a share of its own
/// (`OPEN_TEXT_BYTES`) and is left empty.
fn saved_weight<O: JsonlObserver>(adapter: O::State) -> usize {
    let mut context = serde_json::Map::new();
    if O::RESUMES_IN_CONTEXT {
        super::jsonl::persisted::<O>(&hot_ident(), &mut context);
    }
    let at = super::Position {
        line: u64::MAX,
        offset: u64::MAX,
        identity: format!("{}:{}", u64::MAX, u64::MAX),
        fingerprint: "f".repeat(64),
        context: serde_json::Map::new(),
    };
    let legacy = super::LegacyCursor { offset: u64::MAX, identity: "x".repeat(64), anchor: "f".repeat(64) };
    let saved = super::Saved {
        water: u64::MAX,
        position: Some(at.clone()),
        legacy_cursor: Some(legacy),
        replay: Some(super::Replay::Log(super::Position { context, ..at })),
        adapter,
        ..super::Saved::default()
    };
    serde_json::to_string(&saved).unwrap().len()
}

/// The serialized weight of the whole state an `O` saves with the adapter state built from `worst` (its fields at their extremes).
fn state_weight<O: JsonlObserver>(worst: Value) -> usize {
    saved_weight::<O>(serde_json::from_value::<O::State>(worst).expect("a worst state of the adapter's own shape"))
}

/// t36l Y3 (A R2 of the t36b checkpoint), summed with the core's fields by t36m Z3 (t36l N4): the state share (`STATE_BYTES`) holds the
/// core's own fields and the adapter's state, so each adapter is pinned as the whole `Saved` it writes, its state at its worst beside the
/// core's worst position, replay position (with the context Aider saves in it) and byte cursor. Every adapter whose `State` is not `()` is
/// pinned here; a new one with no entry fails the roster check, so a state is never added without its bound.
#[test]
fn every_adapter_state_at_its_worst_fits_the_state_share_beside_the_core_fields() {
    let budget = super::answer::STATE_BYTES;
    let hot = "\u{1}".repeat(super::answer::FIELD_CHARS);
    let quiet = json!({"idx": i64::MIN, "flushed": true, "ts": hot});
    // The state the adapters build from a record they read: a timestamp of any size goes through the core's cap.
    let built = QuietTurn::after(&None, &"\u{1}".repeat(4 * 1024 * 1024));
    let pinned = [
        ("aider", state_weight::<adapters::aider::Aider>(json!({"session": format!("aider-{}-20240101-000000", "n".repeat(4096)), "prompt": true, "output": true}))),
        ("kimi", state_weight::<adapters::kimi::Kimi>(json!({"idx": i64::MIN, "last_ms": i64::MIN, "flushed": true, "cancelled": true}))),
        ("pi", state_weight::<Pi>(quiet.clone())),
        ("qwen", state_weight::<adapters::qwen::Qwen>(quiet)),
        ("pi built from a 4 MiB timestamp", saved_weight::<Pi>(Some(built))),
    ];
    for (name, weight) in pinned {
        assert!(weight <= budget, "{name}: a state of {weight} bytes does not fit the {budget} the share leaves the adapter");
    }
    // The roster: every adapter file that declares a state of its own is one of the four pinned above.
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/observe/adapters");
    let mut stateful: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .filter_map(|entry| {
            let path = entry.unwrap().path();
            let text = std::fs::read_to_string(&path).ok()?;
            let declared = text.lines().any(|line| line.trim_start().starts_with("type State =") && !line.contains("type State = ()"));
            declared.then(|| path.file_stem().unwrap().to_string_lossy().into_owned())
        })
        .collect();
    stateful.sort();
    assert_eq!(stateful, ["aider", "kimi", "pi", "qwen"], "an adapter with a state of its own must be pinned at its worst above");
}

/// The checkpoint `O` writes with the worst identity (`hot_ident`) beside a window of 64 keys of the longest the core carries as they are
/// (`KEY_BYTES`), after `fit_request_bound`: its serialized weight, and how many keys it kept.
fn checkpoint_at_its_worst<O: JsonlObserver>() -> (usize, usize) {
    let mut checkpoint = serde_json::Map::new();
    checkpoint.insert("kind".into(), json!("jsonl"));
    checkpoint.insert("offset".into(), json!(u64::MAX));
    checkpoint.insert("identity".into(), json!(format!("{}:{}", u64::MAX, u64::MAX)));
    checkpoint.insert("anchor".into(), json!("f".repeat(16)));
    super::jsonl::persisted::<O>(&hot_ident(), &mut checkpoint);
    let keys: Vec<String> = (0..64).map(|n| format!("{n:04}{}", "k".repeat(super::answer::KEY_BYTES - 4))).collect();
    checkpoint.insert("recent_keys".into(), json!(keys));
    if O::CREDITED {
        checkpoint.insert("credited_count".into(), json!(i64::MAX));
    }
    super::jsonl::fit_request_bound(&mut checkpoint);
    (serde_json::to_string(&checkpoint).unwrap().len(), checkpoint["recent_keys"].as_array().unwrap().len())
}

/// t36m Z3 (t36l N3): what a vendor persists beside the window (`JsonlObserver::persist`: each value capped at `FIELD_CHARS`, and up to six
/// bytes a character) is not trimmed by `fit_request_bound`: only the window is. So the worst of them, for every adapter, leaves a checkpoint
/// under what a request may carry with at least half of the default window (32 of the 64 longest plain keys) kept, and a further capped field
/// in any adapter, which would leave a vendor with nothing left to trim, fails here. The roster is every adapter that reads a JSONL log.
#[test]
fn what_every_adapter_persists_at_its_worst_leaves_room_for_half_of_a_window_of_the_longest_keys() {
    let worst = [
        ("aider", checkpoint_at_its_worst::<adapters::aider::Aider>()),
        ("claude", checkpoint_at_its_worst::<Claude>()),
        ("codex", checkpoint_at_its_worst::<Codex>()),
        ("copilot", checkpoint_at_its_worst::<adapters::copilot::Copilot>()),
        ("droid", checkpoint_at_its_worst::<adapters::droid::Droid>()),
        ("grok", checkpoint_at_its_worst::<Grok>()),
        ("kimi", checkpoint_at_its_worst::<adapters::kimi::Kimi>()),
        ("muse", checkpoint_at_its_worst::<adapters::muse::Muse>()),
        ("pi", checkpoint_at_its_worst::<Pi>()),
        ("qwen", checkpoint_at_its_worst::<adapters::qwen::Qwen>()),
    ];
    for (name, (weight, kept)) in worst {
        assert!(weight <= super::answer::CHECKPOINT_REQUEST_BYTES, "{name}: a checkpoint of {weight} bytes is over what a request may carry");
        assert!(kept >= 32, "{name}: only {kept} of 64 keys fit beside what the adapter persists ({weight} bytes)");
    }
    // The roster: every adapter file that reads a JSONL log is one of the ten above.
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/observe/adapters");
    let mut readers: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .filter_map(|entry| {
            let path = entry.unwrap().path();
            let text = std::fs::read_to_string(&path).ok()?;
            text.lines().any(|line| line.starts_with("impl JsonlObserver for ")).then(|| path.file_stem().unwrap().to_string_lossy().into_owned())
        })
        .collect();
    readers.sort();
    assert_eq!(readers, ["aider", "claude", "codex", "copilot", "droid", "grok", "kimi", "muse", "pi", "qwen"], "an adapter that reads a JSONL log must be pinned at its worst above");
}

// ---- the byte cursor a byte-offset reader left (t36l Y1) -------------------------------------------------------------

/// The state a bag carries when the Python tail reader (a byte cursor: offset, file identity, anchor) was the last to read the log
/// (`cli_runtime_state.py`, kind `byte_cursor`), for the log at `path` and an `offset` in it.
fn byte_cursor(path: &Path, offset: u64) -> Value {
    let log = JsonlFile::open_path(path, LIMITS).unwrap();
    json!({"offset": offset, "identity": log.identity(), "anchor": log.anchor_at(offset).unwrap()})
}

#[test]
fn a_byte_cursor_becomes_a_verified_position_and_the_line_water_and_only_what_follows_is_read() {
    let body = assistant_lines(5);
    let path = write("byte-cursor", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let facts = adapters::find("codex").unwrap();
    let state = json!({"legacy_cursor": byte_cursor(&path, 2 * line), "last_text": "Hel"});
    let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": [], "state": state})).unwrap();
    assert_eq!(keys(&document), ["act:3", "act:4", "act:5"], "the two lines before the cursor are not read again");
    assert_eq!((document["state"]["water"].as_u64(), document["state"]["position"]["line"].as_u64()), (Some(5), Some(5)));
    assert!(document["state"].get("legacy_cursor").is_none() && document.get("legacy_cursor").is_none(), "spent, and nothing refused: {document}");
    assert_eq!(document["state"]["last_text"], "Hel", "the open reply text rides across the migration");

    // A cursor at the start of the file migrates nothing and refuses nothing.
    let start = json!({"legacy_cursor": byte_cursor(&path, 0)});
    let all = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": [], "state": start})).unwrap();
    assert_eq!(keys(&all).len(), 5);
    assert!(all.get("legacy_cursor").is_none(), "{all}");
}

#[test]
fn a_byte_cursor_the_file_does_not_hold_is_refused_with_its_reason_and_the_log_is_read_from_its_start() {
    let body = assistant_lines(5);
    let path = write("byte-cursor-refused", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let facts = adapters::find("codex").unwrap();
    let honest = byte_cursor(&path, 2 * line);
    let refusals = [
        ("past the end of the file", json!({"offset": 5 * line + 100, "identity": honest["identity"], "anchor": ""})),
        ("another file", json!({"offset": 2 * line, "identity": "1:1", "anchor": honest["anchor"]})),
        ("the bytes before it changed", json!({"offset": 2 * line, "identity": honest["identity"], "anchor": "0000000000000000"})),
        ("inside a line", json!({"offset": 2 * line + 3, "identity": honest["identity"], "anchor": ""})),
    ];
    for (why, cursor) in refusals {
        let state = json!({"legacy_cursor": cursor, "last_text": "stale"});
        let first = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": [], "state": state})).unwrap();
        assert_eq!(keys(&first), ["act:1", "act:2", "act:3", "act:4", "act:5"], "{why}: the log is read from its start");
        assert_eq!(first["legacy_cursor"], why, "{why}: the answer names the refusal");
        assert_eq!(first["state"]["last_text"], "", "{why}: the reply text of the old bag is dropped with it");
        assert!(first["state"].get("legacy_cursor").is_none());
        let again = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, LIMITS, &json!({"seen": first["seen"], "state": first["state"]})).unwrap();
        assert!(keys(&again).is_empty() && again.get("legacy_cursor").is_none(), "{why}: then the position holds: {again}");
    }
}

#[test]
fn a_byte_cursor_too_far_into_the_log_to_count_under_the_pass_cap_is_refused_and_the_pass_still_makes_progress() {
    let body = assistant_lines(5);
    let path = write("byte-cursor-far", &body);
    let line = body.lines().next().unwrap().len() as u64 + 1;
    let limits = Limits { line: 1000, pass: line * 2 + 1 };
    let facts = adapters::find("codex").unwrap();
    let state = json!({"legacy_cursor": byte_cursor(&path, 4 * line)});
    let document = pass::<Codex, Cumulative, NoCapability>(facts, &path, Mode::Activity, limits, &json!({"seen": [], "state": state})).unwrap();
    assert_eq!(document["legacy_cursor"], "too long to count under the pass byte limit");
    assert_eq!(keys(&document), ["act:1", "act:2"], "the log is read again from its start, a pass at a time");
    assert!(document.get("more").is_some());
}
