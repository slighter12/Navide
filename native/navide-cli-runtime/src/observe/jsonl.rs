//! The JSONL source: one bounded open, the rotation rule, the offset tail, and the scans every JSONL observer shares.
//!
//! What an adapter never writes: opening the file, checking it is a regular file, reading lines under a cap, deciding
//! the saved cursor still belongs to this file, hashing the anchor, the fingerprinted resume and high-water mark of an
//! activity pass, the per-turn dedup window, the cumulative-to-delta rule, or the usage extraction (the declaration's
//! pointers).
use super::adapters::{JsonlRules, Mode};
use super::answer::{Piece, FIELD_CHARS};
use super::usage::{Reading, Totals, Usage};
use super::{
    blake2b, bound_key, cap_head_tail, json_len, text_of, Capability, Ctx, Cumulative, Cursor, Ident, LegacyCursor, Observer, Owed, PerTurn,
    Position, Replay, Run, Scan, Source, UsageScan, CHECKPOINT_REQUEST_BYTES,
};
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::collections::{HashSet, VecDeque};
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, Seek, SeekFrom};
use std::os::unix::fs::{FileExt, MetadataExt, OpenOptionsExt};
use std::path::Path;

/// Bytes the anchor covers (the 512 bytes that end at the saved offset).
const ANCHOR_BYTES: u64 = 512;
/// Bytes the activity position fingerprint covers.
const FINGERPRINT_BYTES: u64 = 32;
/// Entries of the per-turn dedup window a cursor carries.
const RECENT_KEYS: usize = 64;
/// What a line's events may weigh beyond twelve times the line and the open text: the identity fields each event repeats (each capped
/// at `FIELD_CHARS` characters) and the fields of the event's own cursor.
pub(super) const ACTIVITY_MARGIN: usize = 1024 * 1024;
const PASS_LIMIT: &str = "observation exceeds the pass byte limit";

/// The bounds one observation runs under.
#[derive(Clone, Copy)]
pub struct Limits {
    /// A line longer than this is not buffered: it is read through and reported as `oversize`.
    pub line: usize,
    /// Bytes one pass may read. A resumable pass (usage, activity) stops before the line that would cross it and picks
    /// up there next time; a line that alone exceeds it, and any non-resumable pass over it, is refused.
    pub pass: u64,
}

pub const LIMITS: Limits = Limits { line: 16 * 1024 * 1024, pass: 256 * 1024 * 1024 };

pub enum Flow {
    Continue,
    Stop,
}

/// One line of the file: its 1-based number, the byte offset after it, and whether a newline ended it.
pub struct Line {
    pub no: u64,
    pub end: u64,
    pub complete: bool,
    /// The line exceeded `Limits::line`; `bytes` is empty. It counts as a line that does not parse.
    pub oversize: bool,
    pub bytes: Vec<u8>,
}

impl Line {
    fn blank(&self) -> bool {
        !self.oversize && self.bytes.iter().all(u8::is_ascii_whitespace)
    }
}

/// What one line decoded to.
pub enum Decoded {
    Blank,
    Unparsable,
    Utf8Error,
    NotObject,
    Object(Value),
}

pub struct JsonlFile {
    file: File,
    identity: String,
    len: u64,
    /// The file's modification time (epoch milliseconds) as the open handle reported it; `None` where the platform gives none.
    modified_ms: Option<i64>,
    limits: Limits,
}

fn modified_ms_of(stat: &std::fs::Metadata) -> Option<i64> {
    stat.modified().ok().and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok()).map(|age| age.as_millis() as i64)
}

fn bytes_before(file: &File, end: u64, wanted: u64) -> io::Result<Vec<u8>> {
    let start = end.saturating_sub(wanted);
    let mut bytes = vec![0; (end - start) as usize];
    file.read_exact_at(&mut bytes, start)?;
    Ok(bytes)
}

fn anchor_in(file: &File, end: u64) -> io::Result<String> {
    if end == 0 {
        return Ok(String::new());
    }
    Ok(blake2b::hex_digest(&bytes_before(file, end, ANCHOR_BYTES)?, 8))
}

/// The anchors of a file's lines, read through a handle of their own (a positioned read does not move the scan's).
struct Anchors(File);

impl Anchors {
    fn at(&self, end: u64) -> io::Result<String> {
        anchor_in(&self.0, end)
    }
}

/// The facts a log names about itself (its session id, working directory, model and version) come from its own header, and every event
/// repeats them: each is capped (`FIELD_CHARS`, head and tail kept), as the answer's fixed share and the events assume.
fn cap_ident(ident: &mut Ident) {
    for field in [&mut ident.native, &mut ident.cwd, &mut ident.model, &mut ident.version] {
        if field.chars().nth(FIELD_CHARS).is_some() {
            *field = cap_head_tail(field, FIELD_CHARS);
        }
    }
}

/// The vendor's persisted checkpoint fields (`JsonlObserver::persist`), each long value capped (`FIELD_CHARS`): they come from the log's
/// own header and are copied into every cursor.
pub(super) fn persisted<O: JsonlObserver>(ident: &Ident, checkpoint: &mut Map<String, Value>) {
    O::persist(ident, checkpoint);
    for value in checkpoint.values_mut() {
        if let Some(long) = value.as_str().filter(|text| text.chars().nth(FIELD_CHARS).is_some()) {
            *value = json!(cap_head_tail(long, FIELD_CHARS));
        }
    }
}

/// A checkpoint the next request can take back: at most `CHECKPOINT_REQUEST_BYTES` serialized (`Request::parse` refuses more, and a vendor
/// whose checkpoint is refused is stopped for good). The window of recent keys is what grows with the log (a key may serialize to six
/// times its bytes), so the oldest keys leave it until the checkpoint fits; every other field is capped where it is taken. The persisted
/// window is therefore at most `JsonlObserver::RECENT` keys and fewer when they are heavy: the pass that reads on counts a key it dropped
/// again if the log repeats it (the window is a bounded memory, as it is at `RECENT`). Only the persisted copy is cut; the pass's own
/// window is whole.
pub(super) fn fit_request_bound(checkpoint: &mut Map<String, Value>) {
    let weight = serde_json::to_string(checkpoint).map_or(0, |text| text.len());
    let mut over = weight.saturating_sub(CHECKPOINT_REQUEST_BYTES);
    if over == 0 {
        return;
    }
    if let Some(Value::Array(keys)) = checkpoint.get_mut("recent_keys") {
        let mut dropped = 0;
        while over > 0 && dropped < keys.len() {
            over = over.saturating_sub(keys[dropped].as_str().map_or(0, json_len) + 1);
            dropped += 1;
        }
        keys.drain(..dropped);
    }
}

/// Put `key` at the end of the window of the latest keys; the oldest leaves it once `cap` is passed (unless it was also counted later).
fn remember(recent: &mut VecDeque<String>, window: &mut HashSet<String>, key: &str, cap: usize) {
    window.insert(key.to_owned());
    recent.push_back(key.to_owned());
    if recent.len() > cap {
        if let Some(expired) = recent.pop_front().filter(|expired| !recent.contains(expired)) {
            window.remove(&expired);
        }
    }
}

impl JsonlFile {
    /// A regular file only, never through a symlink, and never blocking on a FIFO or device.
    pub fn open_path(path: &Path, limits: Limits) -> io::Result<Self> {
        let file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)?;
        let stat = file.metadata()?;
        if !stat.is_file() {
            return Err(io::Error::other("observation source is not a regular file"));
        }
        Ok(Self { file, identity: format!("{}:{}", stat.dev(), stat.ino()), len: stat.len(), modified_ms: modified_ms_of(&stat), limits })
    }

    /// The modification time (epoch milliseconds) the open handle reports now, for a decision made after the pass has read: a record
    /// appended while the pass ran has moved it since the open.
    fn modified_now(&self) -> Option<i64> {
        self.file.metadata().ok().and_then(|stat| modified_ms_of(&stat))
    }

    #[cfg(test)]
    pub fn identity(&self) -> &str {
        &self.identity
    }

    fn bytes_before(&self, end: u64, wanted: u64) -> io::Result<Vec<u8>> {
        bytes_before(&self.file, end, wanted)
    }

    /// The saved-anchor spelling for the bytes that end at `end` (`""` at offset 0): blake2b-8 in hex.
    pub fn anchor_at(&self, end: u64) -> io::Result<String> {
        anchor_in(&self.file, end)
    }

    /// A second handle on the file for the anchors of the cursors a scan builds while `lines` holds the file.
    fn anchors(&self) -> io::Result<Anchors> {
        Ok(Anchors(self.file.try_clone()?))
    }

    /// Where the file stands now: its identity and size, proved by a fingerprint of its last bytes.
    fn position_now(&self) -> io::Result<Position> {
        Ok(Position { line: 0, offset: self.len, identity: self.identity.clone(), fingerprint: self.fingerprint(self.len)?, context: Map::new() })
    }

    /// The hex of the last bytes before `end`: a cheap check that the file still holds what an activity position saw.
    pub fn fingerprint(&self, end: u64) -> io::Result<String> {
        Ok(self.bytes_before(end, FINGERPRINT_BYTES)?.iter().map(|byte| format!("{byte:02x}")).collect())
    }

    /// Does the saved cursor still describe this file? Identity, size and the anchor are all checked here.
    /// Returns `(rotated, replaced)`: `replaced` is a different file, `rotated` is any reason to restart at 0.
    pub(super) fn resume(&self, cursor: &Cursor, missing_identity_rotates: bool) -> io::Result<(bool, bool)> {
        let offset = cursor.offset();
        let saved = cursor.identity();
        let replaced = saved.is_some_and(|saved| saved != self.identity);
        let unknown = saved.is_none() && missing_identity_rotates;
        let anchor = cursor.anchor();
        let rewritten = offset > 0
            && !replaced
            && !unknown
            && self.len >= offset
            && !anchor.is_empty()
            && self.anchor_at(offset)? != anchor;
        let rotated = offset > 0 && (replaced || unknown || self.len < offset || rewritten);
        Ok((rotated, replaced))
    }

    /// Whole lines from byte `start` (numbered after `first`), a partial last line included with `complete == false`.
    /// A line over the line cap is read through, not buffered. The pass cap is enforced while a line is read: a
    /// `resumable` pass stops before the line that would cross it (the next pass starts there); a line that alone
    /// crosses it, or any non-resumable pass that does, is refused. Returns whether the end of the file was reached.
    pub fn lines<F: FnMut(Line) -> Flow>(&mut self, start: u64, first: u64, resumable: bool, mut each: F) -> io::Result<bool> {
        self.file.seek(SeekFrom::Start(start))?;
        let mut reader = BufReader::with_capacity(64 * 1024, &self.file);
        let (mut end, mut no, mut read) = (start, first, 0u64);
        loop {
            let (mut bytes, mut oversize, mut complete, mut seen) = (Vec::new(), false, false, 0u64);
            loop {
                let buffer = reader.fill_buf()?;
                if buffer.is_empty() {
                    break;
                }
                let (take, newline) = match buffer.iter().position(|byte| *byte == b'\n') {
                    Some(at) => (at + 1, true),
                    None => (buffer.len(), false),
                };
                if read + seen + take as u64 > self.limits.pass {
                    return if resumable && read > 0 { Ok(false) } else { Err(io::Error::other(PASS_LIMIT)) };
                }
                if !oversize {
                    if bytes.len() + take > self.limits.line {
                        oversize = true;
                        bytes = Vec::new();
                    } else {
                        bytes.extend_from_slice(&buffer[..take]);
                    }
                }
                seen += take as u64;
                reader.consume(take);
                if newline {
                    complete = true;
                    break;
                }
            }
            if seen == 0 {
                return Ok(true);
            }
            read += seen;
            end += seen;
            no += 1;
            if let Flow::Stop = each(Line { no, end, complete, oversize, bytes }) {
                return Ok(false);
            }
        }
    }

    /// The position a replay that stopped at the pass budget resumes from: its saved position while this file still holds the bytes the
    /// position fingerprinted. A vendor whose records mean what the context above them says (`RESUMES_IN_CONTEXT`) also needs the context
    /// the position was saved with: a position whose context lacks a key the adapter declares (`CONTEXT_KEYS`; written before the context was
    /// saved, or by a reader that did not know the key) cannot be resumed and is a restart. An adapter that declares none is never restarted
    /// for its context, whatever its `persist` wrote.
    fn replay_position<'a, O: JsonlObserver>(&self, replay: &'a Option<Replay>) -> Option<&'a Position> {
        match replay {
            Some(Replay::Log(at)) if self.holds(at) && !(O::RESUMES_IN_CONTEXT && O::CONTEXT_KEYS.iter().any(|key| !at.context.contains_key(*key))) => Some(at),
            _ => None,
        }
    }

    /// Where a replay that stopped at the pass budget resumes, as its byte offset and the number of the lines before it: its saved position,
    /// else the start (the keys already delivered are in the seen bag).
    pub(super) fn replay_start<O: JsonlObserver>(&self, replay: &Option<Replay>) -> (u64, u64) {
        self.replay_position::<O>(replay).map_or((0, 0), |at| (at.offset, at.line))
    }

    /// What the vendor's `persist` wrote at the position a replay resumes from (`Position::context`); `None` where the replay starts over.
    pub(super) fn replay_context<'a, O: JsonlObserver>(&self, replay: &'a Option<Replay>) -> Option<&'a Map<String, Value>> {
        self.replay_position::<O>(replay).map(|at| &at.context)
    }

    /// What a replay pass leaves of its progress: when it stopped before the end of the log it saves where (the last whole line it
    /// read) and answers `more`; when it reached the end it clears the position. `replayed` is the offset and number of the last
    /// whole line read.
    fn paged_replay<O: JsonlObserver>(&self, run: &mut Run<O>, reached: bool, replayed: (u64, u64)) -> io::Result<()> {
        if reached {
            run.saved.replay = None;
            return Ok(());
        }
        let (offset, line) = replayed;
        let mut context = Map::new();
        if O::RESUMES_IN_CONTEXT {
            persisted::<O>(&run.ident, &mut context);
        }
        run.saved.replay = Some(Replay::Log(Position { line, offset, identity: self.identity.clone(), fingerprint: self.fingerprint(offset)?, context }));
        run.answer.set("more", json!(true));
        Ok(())
    }

    /// The first `count` lines, parsed leniently (a header read; never an error for a bad line).
    pub fn head(&mut self, count: usize) -> io::Result<Vec<Value>> {
        let mut found = Vec::new();
        self.lines(0, 0, true, |line| {
            if let Ok(value) = serde_json::from_slice::<Value>(&line.bytes) {
                found.push(value);
            }
            if line.no as usize >= count {
                Flow::Stop
            } else {
                Flow::Continue
            }
        })?;
        Ok(found)
    }

    /// Every object record of the first `end` bytes of the file, one at a time (nothing buffers the file), under the declared policy
    /// for a full scan. A line that began before `end` is read as far as `end` (as a file that ended there would have shown it), one that
    /// begins at or after `end` is not read: the records are those of the file as it stood when it was `end` bytes long.
    pub fn for_each_record_until<F: FnMut(u64, &Value, &[u8])>(&mut self, ctx: &Ctx, end: u64, mut each: F) -> io::Result<()> {
        let (mut failure, mut began) = (None, 0u64);
        self.lines(0, 0, false, |mut line| {
            let start = std::mem::replace(&mut began, line.end);
            if start >= end {
                return Flow::Stop;
            }
            if line.end > end {
                line.bytes.truncate((end - start) as usize);
                (line.end, line.complete) = (end, false);
            }
            match policy(ctx, decode(&line), false) {
                Ok(Decoded::Object(record)) => {
                    each(line.no, &record, &line.bytes);
                    Flow::Continue
                }
                Ok(_) => Flow::Continue,
                Err(error) => {
                    failure = Some(error);
                    Flow::Stop
                }
            }
        })?;
        failure.map_or(Ok(()), Err)
    }

    /// The one JSON document of the file `<log stem><suffix>` beside the log (a sibling the vendor rewrites as the session advances),
    /// opened under the same bounds as the log: a regular file, never through a symlink, at most one line's bound. `None` for a file that
    /// is not there, cannot be opened or does not parse (the baseline read it as nothing); a file over the bound is refused.
    pub fn document(&self, log: &Path, suffix: &str) -> io::Result<Option<Value>> {
        let Some(mut name) = log.file_stem().map(|stem| stem.to_os_string()) else { return Ok(None) };
        name.push(suffix);
        let Ok(sibling) = Self::open_path(&log.with_file_name(name), self.limits) else { return Ok(None) };
        if sibling.len > self.limits.line as u64 {
            return Err(io::Error::other("the usage document is over the line bound"));
        }
        let mut bytes = vec![0; sibling.len as usize];
        sibling.file.read_exact_at(&mut bytes, 0)?;
        Ok(serde_json::from_slice(&bytes).ok())
    }

    /// A byte cursor a byte-offset reader left, proved against this file: the position it stands for (with the number of lines before it,
    /// which the cursor never knew), or the reason the file does not hold it. The cursor must name this file (identity), lie inside it, agree
    /// with the bytes before it (anchor, when it has one) and sit at the start of a line; the lines before it are counted under the
    /// pass cap, like any read of the file.
    fn adopt(&mut self, carried: &LegacyCursor) -> io::Result<Result<Position, &'static str>> {
        let offset = carried.offset;
        if carried.identity != self.identity {
            return Ok(Err("another file"));
        }
        if offset > self.len {
            return Ok(Err("past the end of the file"));
        }
        if !carried.anchor.is_empty() && self.anchor_at(offset)? != carried.anchor {
            return Ok(Err("the bytes before it changed"));
        }
        if self.bytes_before(offset, 1)? != b"\n" {
            return Ok(Err("inside a line"));
        }
        let (mut counted, mut reached) = (0, 0);
        match self.lines(0, 0, false, |line| {
            (counted, reached) = (line.no, line.end);
            if line.end >= offset {
                Flow::Stop
            } else {
                Flow::Continue
            }
        }) {
            Err(error) if error.to_string() == PASS_LIMIT => return Ok(Err("too long to count under the pass byte limit")),
            Err(error) => return Err(error),
            Ok(_) => {}
        }
        if reached != offset {
            return Ok(Err("past the end of the file"));
        }
        Ok(Ok(Position { line: counted, offset, identity: self.identity.clone(), fingerprint: self.fingerprint(offset)?, context: Map::new() }))
    }

    /// Spend the byte cursor a state carries: the log goes on from the position it proves, with the line water the position stands at; a
    /// cursor the file does not hold is refused by name in the answer (`legacy_cursor`), the log is read from its start and the open reply
    /// text of the old bag goes with it (it belonged to a place that is not this log's). A cursor at offset 0 had read nothing.
    fn spend<O: JsonlObserver>(&mut self, carried: &LegacyCursor, run: &mut Run<O>) -> io::Result<()> {
        let proved = if carried.offset == 0 { Ok(None) } else { self.adopt(carried)?.map(Some) };
        run.saved.position = proved.as_ref().ok().cloned().flatten();
        run.saved.water = run.saved.position.as_ref().map_or(0, |at| at.line);
        if let Err(why) = proved {
            run.answer.set("legacy_cursor", json!(why));
            run.saved.last_text.clear();
        }
        Ok(())
    }

    /// Is `at` a point this file still holds: the same file, long enough, with the bytes `at` fingerprinted before it?
    fn holds(&self, at: &Position) -> bool {
        at.identity == self.identity && at.offset <= self.len && self.fingerprint(at.offset).is_ok_and(|held| held == at.fingerprint)
    }
}

/// Decode one line the way every observer reads it: blank, parsable, an object.
pub fn decode(line: &Line) -> Decoded {
    if line.oversize {
        return Decoded::Unparsable;
    }
    let Ok(text) = std::str::from_utf8(&line.bytes) else {
        return Decoded::Utf8Error;
    };
    let text = text.trim();
    if text.is_empty() {
        return Decoded::Blank;
    }
    match serde_json::from_str::<Value>(text) {
        Ok(value) if value.is_object() => Decoded::Object(value),
        Ok(_) => Decoded::NotObject,
        Err(_) => Decoded::Unparsable,
    }
}

/// The keys of the object at `path` in the order the line spells them. `serde_json::Value` sorts a map's keys and Python keeps
/// insertion order, so a vendor whose behaviour names "the first key" of an object reads it from the raw line. Empty where
/// `path` is not an object of the line, or the line ends before the object does (the keys read so far stay). A key the line spells
/// twice is listed twice: the reader that wants a Python dict's single place keeps the first.
pub fn ordered_keys(raw: &[u8], path: &[&str]) -> Vec<String> {
    fn skip_ws(raw: &[u8], at: &mut usize) {
        while raw.get(*at).is_some_and(u8::is_ascii_whitespace) {
            *at += 1;
        }
    }
    fn string_at(raw: &[u8], at: &mut usize) -> Option<String> {
        if raw.get(*at) != Some(&b'"') {
            return None;
        }
        let start = *at;
        *at += 1;
        while let Some(&byte) = raw.get(*at) {
            match byte {
                b'\\' => *at += 2,
                b'"' => {
                    *at += 1;
                    return serde_json::from_slice::<String>(&raw[start..*at]).ok();
                }
                _ => *at += 1,
            }
        }
        None
    }
    fn skip_value(raw: &[u8], at: &mut usize) -> bool {
        match raw.get(*at) {
            Some(b'"') => string_at(raw, at).is_some(),
            Some(b'{') | Some(b'[') => {
                let mut depth = 0;
                while let Some(&byte) = raw.get(*at) {
                    match byte {
                        b'"' => {
                            if string_at(raw, at).is_none() {
                                return false;
                            }
                            continue;
                        }
                        b'{' | b'[' => depth += 1,
                        b'}' | b']' => depth -= 1,
                        _ => {}
                    }
                    *at += 1;
                    if depth == 0 {
                        return true;
                    }
                }
                false
            }
            Some(_) => {
                while raw.get(*at).is_some_and(|b| !matches!(b, b',' | b'}' | b']') && !b.is_ascii_whitespace()) {
                    *at += 1;
                }
                true
            }
            None => false,
        }
    }
    // One object level: `want` is the key to stop at the value of (`Some`), or None to list every key; the position is left at the value.
    fn level(raw: &[u8], at: &mut usize, want: Option<&str>) -> Option<Vec<String>> {
        skip_ws(raw, at);
        if raw.get(*at) != Some(&b'{') {
            return None;
        }
        *at += 1;
        let mut keys = Vec::new();
        loop {
            skip_ws(raw, at);
            if raw.get(*at) == Some(&b'}') || *at >= raw.len() {
                return want.is_none().then_some(keys);
            }
            let Some(key) = string_at(raw, at) else {
                return want.is_none().then_some(keys);
            };
            skip_ws(raw, at);
            if raw.get(*at) != Some(&b':') {
                return want.is_none().then_some(keys);
            }
            *at += 1;
            skip_ws(raw, at);
            if want == Some(key.as_str()) {
                return Some(keys);
            }
            if !skip_value(raw, at) {
                return want.is_none().then_some(keys);
            }
            keys.push(key);
            skip_ws(raw, at);
            if raw.get(*at) == Some(&b',') {
                *at += 1;
            }
        }
    }
    let mut at = 0;
    for wanted in path {
        if level(raw, &mut at, Some(wanted)).is_none() {
            return Vec::new();
        }
    }
    level(raw, &mut at, None).unwrap_or_default()
}

/// The declared policy for the lines a scan cannot use. `incremental` scans (usage) always skip; a full scan follows the
/// vendor's baseline: with the compatibility rules set, invalid UTF-8 and a non-object record fail the pass.
fn policy(ctx: &Ctx, decoded: Decoded, incremental: bool) -> io::Result<Decoded> {
    let rules = rules(ctx)?;
    Ok(match decoded {
        Decoded::Utf8Error if !incremental && rules.invalid_utf8_fails => {
            return Err(io::Error::other("stream did not contain valid UTF-8"))
        }
        Decoded::Utf8Error => Decoded::Unparsable,
        Decoded::NotObject if !incremental && rules.non_object_fails => return Err(io::Error::other(not_an_object(ctx))),
        other => other,
    })
}

fn not_an_object(ctx: &Ctx) -> &'static str {
    ctx.core.messages.non_object.unwrap_or("record is not an object")
}

/// The named compatibility rules of a JSONL layout (the generator refuses a core JSONL layout without them).
fn rules(ctx: &Ctx) -> io::Result<&'static JsonlRules> {
    ctx.layout.rules.as_ref().ok_or_else(|| io::Error::other("the layout declares no jsonl rules"))
}

/// The hooks a JSONL adapter supplies. Each is here because its behaviour is not data; the adapter says why.
pub trait JsonlObserver: Observer {
    /// Who the file belongs to. Reads the header or the path; the core stores the answer as `Run::ident`.
    fn identify(file: &mut JsonlFile, ctx: &Ctx, run: &Run<Self>) -> io::Result<Ident>;
    /// A record's activity events. The high-water mark, the resume position and the pass cap are the core's, and so is
    /// `last_text`, the reply text of the turn left open (a turn's end delivers it and clears it).
    fn activity(last_text: &mut String, state: &mut Self::State, ident: &Ident, record: &Value, line: u64, out: &mut Vec<Value>);
    /// Extra checkpoint fields this vendor persists beside the core's.
    fn persist(_ident: &Ident, _checkpoint: &mut Map<String, Value>) {}
    /// A log whose records mean what the context above them says (hook: Aider's token line belongs to the section header above it). A
    /// replay that stops at the pass budget then saves what `persist` writes for the ident it stopped with, in its position
    /// (`Position::context`), and the page that resumes there takes it from the position (`JsonlFile::replay_context`) and reads none of the
    /// bytes before it. The context is bounded like a checkpoint's persisted fields (each value `FIELD_CHARS`).
    const RESUMES_IN_CONTEXT: bool = false;
    /// The keys of `persist` that the vendor's resumed page reads from the context. A saved position whose context lacks one is a restart
    /// (`RESUMES_IN_CONTEXT` vendors only); none declared means a context is never a reason to restart.
    const CONTEXT_KEYS: &'static [&'static str] = &[];
    /// The dedup key of a usage record that names itself by its place in the log (hook: no field of the record is an id): the line
    /// number in a replay, the byte offset after the line in an incremental pass. `None` leaves the key to the declared pointers.
    fn position_key(_ident: &Ident, _replay: bool, _line: u64, _end: u64, _record: &Value) -> Option<String> {
        None
    }
    /// What an activity pass concludes from the silence after the last record it read (hook: a turn that ends because the log went
    /// quiet is decided by the wall clock the request names, not by a record). Called once per pass that reached the end of the log
    /// without an error.
    fn settle(_last_text: &mut String, _state: &mut Self::State, _ident: &Ident, _out: &mut Vec<Value>) {}
    /// The most of the latest usage keys an incremental cursor remembers (a log that is rewritten in place wants a wider window). It is an
    /// upper bound, not a count: a cursor must fit what a request may carry (`CHECKPOINT_REQUEST_BYTES`), so `fit_request_bound` drops the
    /// oldest keys from the persisted window until it does. Keys are short in practice (`KEY_BYTES`) and the whole window is kept; a window
    /// of keys that serialize heavily (a key's JSON escapes may weigh six times its characters) keeps fewer. A key that left the persisted
    /// window and comes again in a later pass is counted again, as one older than a window of `RECENT` is: the window was always a bounded
    /// memory (t36m Z3, t36l review A6).
    const RECENT: usize = RECENT_KEYS;
    /// A log the vendor rewrites in place (hook: a rewrite makes the cursor start over, and the keys alone cannot stop the entries the
    /// window has long forgotten from being counted twice). The cursor then also carries `credited_count`, how many usage records it
    /// counted; a pass that starts over counts the first that many again without delivering them, and keeps the window it had (the window
    /// the checkpoint kept after `fit_request_bound`, at most `RECENT` keys).
    const CREDITED: bool = false;
    /// What `activity` is told as the record's place: its line number, or (for a vendor whose baseline named a record without an id by its
    /// place) the byte offset after its line.
    const PLACE_IS_OFFSET: bool = false;
    /// The record the declared `usage_fields` read (hook: a vendor spells its usage on the record in more than one place, or names the
    /// record kinds it counts by more than one field). The default is the record itself.
    fn usage_view<'a>(record: &'a Value, _raw: &[u8]) -> std::borrow::Cow<'a, Value> {
        std::borrow::Cow::Borrowed(record)
    }
    /// What one line of the file is (hook: a log that is not JSONL, Aider's Markdown, is read a line at a time and each line made a record
    /// of its own shape). The default is the JSON decode every other vendor reads under.
    fn read_line(line: &Line) -> Decoded {
        decode(line)
    }
    /// `usage_view`, for a log whose records are read in the context the ones before them set (hook: Aider's token line belongs to the
    /// section header above it). The context is `ident`, which the scan carries from record to record and writes into every cursor
    /// (`persist`). The default reads no context.
    fn usage_view_in<'a>(_ident: &mut Ident, record: &'a Value, raw: &[u8]) -> std::borrow::Cow<'a, Value> {
        Self::usage_view(record, raw)
    }
    /// The running totals one record of a cumulative log reports, and the facts it names about the session (hook: where a
    /// vendor spells its totals in a shape the declared pointers cannot read, or names its model or version in records that
    /// carry no totals). The default reads the declared pointers and names nothing.
    fn reading(fields: &super::adapters::UsageFields, _ident: &mut Ident, record: &Value) -> Option<Reading> {
        fields.reading(record)
    }
}

/// A `turns` mode: the session's usage split by prompt. The core streams the records and extracts each one's usage from
/// the declaration; the adapter groups them (a Claude turn is a graph, not a field).
pub trait TurnBreakdown: JsonlObserver {
    type Builder: Default;
    /// One record of the log, with the bytes of its line (for a vendor whose order of keys is part of the answer: a parsed map sorts them)
    /// and the usage the declaration reads from the record itself. `ident.wanted_session` is the session the caller asked the breakdown
    /// of, empty for any.
    fn record(builder: &mut Self::Builder, ident: &Ident, line: u64, record: &Value, raw: &[u8], usage: Option<Usage>);
    fn finish(builder: Self::Builder) -> Vec<Value>;
}

/// One model call of a turn, as a `turns` row carries it (`log_readers.base.TurnCall`).
#[derive(Clone, Debug, Serialize)]
pub struct TurnCall {
    pub ts: Option<String>,
    pub model: String,
    pub input: u64,
    pub cache_read: u64,
    pub cache_creation: u64,
    pub output: u64,
    pub cli_version: String,
}

/// One row of a `turns` answer (`log_readers.base.TurnUsage`): the row's own fields, the four totals of its calls and the calls. An adapter
/// groups its records into turns and adds each call; the field names, the totals and the numbering are the core's.
#[derive(Clone, Debug, Serialize)]
pub struct TurnRow {
    pub turn_index: usize,
    pub session_id: String,
    pub started_at: Option<String>,
    pub ended_at: Option<String>,
    pub prompt_excerpt: String,
    pub input: u64,
    pub cache_read: u64,
    pub cache_creation: u64,
    pub output: u64,
    pub calls: Vec<TurnCall>,
    /// The calls the CLI says the turn made: absent where the vendor never reports it (Claude), `null` where it reported none (Grok).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model_calls: Option<Option<u64>>,
    pub cli_version: String,
}

/// Python `str.isspace` for one character: Unicode white space, and the four separators `\x1c`-`\x1f` Python counts as well.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `log_readers.base.turn_excerpt`: the words of `text` joined by single spaces, the first 80 characters of them.
fn turn_excerpt(text: &str) -> String {
    text.split(py_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ").chars().take(80).collect()
}

impl TurnRow {
    /// `log_readers.base.merge_prompt_excerpt`: one more user record folded into a turn's excerpt. The first typed prompt wins, and a
    /// `<`-prefixed injected wrapper (session marker, command stub) only stands in until a typed one arrives (`user_prompt_text` drops the
    /// wrappers: stripped, empty or `<`-prefixed text is no prompt, any other is cut to 500 characters). Claude and Grok both fold through it.
    pub fn merge_prompt_excerpt(current: &str, text: &str) -> String {
        let stripped = text.trim_matches(py_space);
        if !stripped.is_empty() && !stripped.starts_with('<') {
            let typed: String = stripped.chars().take(500).collect();
            return if !current.is_empty() && !current.starts_with('<') { current.to_owned() } else { turn_excerpt(&typed) };
        }
        if current.is_empty() { turn_excerpt(text) } else { current.to_owned() }
    }

    /// A turn of `session_id` that began at `started_at`, with no call yet.
    pub fn new(session_id: String, started_at: Option<String>) -> Self {
        Self {
            turn_index: 0,
            session_id,
            started_at,
            ended_at: None,
            prompt_excerpt: String::new(),
            input: 0,
            cache_read: 0,
            cache_creation: 0,
            output: 0,
            calls: Vec::new(),
            model_calls: None,
            cli_version: String::new(),
        }
    }

    /// One more call: the totals take its counts (saturating), the turn ends at the call's time when it has one, and the row's CLI version
    /// is the latest the calls name.
    pub fn add_call(&mut self, call: TurnCall) {
        for (total, part) in [(&mut self.input, call.input), (&mut self.cache_read, call.cache_read), (&mut self.cache_creation, call.cache_creation), (&mut self.output, call.output)] {
            *total = total.saturating_add(part);
        }
        if call.ts.is_some() {
            self.ended_at.clone_from(&call.ts);
        }
        if !call.cli_version.is_empty() {
            self.cli_version.clone_from(&call.cli_version);
        }
        self.calls.push(call);
    }

    /// Whether the calls counted nothing at all.
    pub fn counts_nothing(&self) -> bool {
        self.input.saturating_add(self.cache_read).saturating_add(self.cache_creation).saturating_add(self.output) == 0
    }

    /// The rows an adapter kept, numbered from 1 in order, as the answer carries them.
    pub fn numbered(rows: Vec<Self>) -> Vec<Value> {
        rows.into_iter().enumerate().map(|(index, row)| json!(Self { turn_index: index + 1, ..row })).collect()
    }
}

impl<O: TurnBreakdown> Capability<O, JsonlFile> for super::ByTurns {
    /// The breakdown is built from the log as it stood when its first page was asked (the prefix of that length), and its rows are
    /// delivered under the answer ledger. A page that stops at the ledger saves how many rows it delivered and that prefix
    /// (`Replay::Turns`: the length, proved by a fingerprint of the bytes before it) and answers `more`; the next page builds the
    /// breakdown again over the same prefix and continues, so a log that is being appended to (the case that matters: the session is
    /// live) goes on without a restart. Only a rewrite of the prefix (another file, a shorter one, other bytes where it ended) starts
    /// the breakdown over, and the answer says `restart` (the caller drops the rows it holds).
    fn answer(file: &mut JsonlFile, ctx: &Ctx, run: &mut Run<O>) -> io::Result<bool> {
        if run.mode != Mode::Turns {
            return Ok(false);
        }
        let fields = usage_fields(ctx)?;
        let (mut delivered, mut inner, prefix) = match run.saved.replay.take() {
            Some(Replay::Turns { delivered, inner, log }) if file.holds(&log) => (delivered, inner, log),
            Some(Replay::Turns { .. }) => {
                run.answer.set("restart", json!(true));
                (0, 0, file.position_now()?)
            }
            _ => (0, 0, file.position_now()?),
        };
        let (mut ident, mut builder) = (run.ident.clone(), O::Builder::default());
        ident.wanted_session = ctx.req.meta[super::adapters::WANTED_SESSION].as_str().unwrap_or("").to_owned();
        file.for_each_record_until(ctx, prefix.offset, |line, record, raw| O::record(&mut builder, &ident, line, record, raw, fields.per_turn(record)))?;
        let rows = O::finish(builder);
        run.answer.set("turns", json!([]));
        // A row too large for one answer comes in pieces: the page that splits it ends there, so a continuation piece always
        // belongs to the last row the caller holds.
        while let Some(row) = rows.get(delivered) {
            match run.answer.take_turn("turns", delivered as i64, row, inner) {
                Piece::Done => (delivered, inner) = (delivered + 1, 0),
                Piece::Partial(taken) => {
                    inner += taken;
                    break;
                }
                Piece::Wait => break,
            }
        }
        if delivered < rows.len() {
            run.saved.replay = Some(Replay::Turns { delivered, inner, log: prefix });
            run.answer.set("more", json!(true));
        }
        Ok(true)
    }
}

fn usage_fields<'a>(ctx: &Ctx<'a>) -> io::Result<&'static super::adapters::UsageFields> {
    ctx.layout.usage_fields.as_ref().ok_or_else(|| io::Error::other("the layout declares no usage fields"))
}

impl<O: JsonlObserver> Source<O> for JsonlFile {
    fn open(ctx: &Ctx) -> io::Result<Self> {
        Self::open_path(ctx.req.path, LIMITS)
    }

    fn declined(ctx: &Ctx, mode: Mode) -> io::Result<Run<O>> {
        Run::new(ctx.req, mode, false)
    }

    fn drive<U: UsageScan<O, Self>, X: Capability<O, Self>>(&mut self, ctx: &Ctx, mode: Mode) -> io::Result<Run<O>> {
        let (rotated, replaced) = self.resume(&ctx.req.cursor, rules(ctx)?.missing_identity_rotates)?;
        let mut run = Run::<O>::new(ctx.req, mode, replaced)?;
        run.rotated = rotated;
        run.identity = json!(self.identity);
        run.ident = O::identify(self, ctx, &run)?;
        run.ident.modified_ms = self.modified_ms;
        cap_ident(&mut run.ident);
        let mut scan = Scan { committed: 0, owed: Owed::Nothing };
        match mode {
            Mode::Usage | Mode::SessionUsage => {
                let start = if mode == Mode::Usage && !rotated { ctx.req.cursor.offset() } else { 0 };
                scan = U::scan(self, ctx, &mut run, start)?;
            }
            Mode::Activity => scan.committed = self.activity(ctx, &mut run)?,
            // The log names its own session: the identity is already the answer.
            Mode::Identify => {}
            Mode::Turns | Mode::Legacy | Mode::HasSession => {
                if !X::answer(self, ctx, &mut run)? {
                    return Err(io::Error::other(ctx.core.messages.unsupported_mode));
                }
            }
        }
        self.finish(&mut run, scan.committed, scan.owed)?;
        run.answer.set("native_session_id", json!(run.ident.native));
        run.answer.set("cwd", json!(run.ident.cwd));
        Ok(run)
    }
}

impl<O: JsonlObserver> UsageScan<O, JsonlFile> for PerTurn {
    const WINDOWED: bool = true;
    /// Per-turn usage: each record carries its own usage and is deduplicated by key. `usage` keeps a bounded window of
    /// keys in the cursor; `session_usage` dedups against the whole seen bag. An incremental event is built with its final cursor
    /// (the offset after its line, that line's anchor, the window as it stands after the event) before the ledger weighs it.
    fn scan(file: &mut JsonlFile, ctx: &Ctx, run: &mut Run<O>, start: u64) -> io::Result<Scan> {
        let fields = usage_fields(ctx)?;
        let incremental = run.mode == Mode::Usage;
        let mut recent: VecDeque<String> = if incremental && (!run.rotated || O::CREDITED) {
            let keys = ctx.req.cursor.0.get("recent_keys").and_then(Value::as_array).into_iter().flatten();
            keys.map(text_of).collect()
        } else {
            VecDeque::new()
        };
        while recent.len() > O::RECENT {
            recent.pop_front();
        }
        let mut window: HashSet<String> = recent.iter().cloned().collect();
        // A rewritten log is counted again from its start: the first `skip` usage records are the ones the cursor already counted (all of
        // them, for a cursor that never recorded a count), and `credited` is how many records the cursor stands behind.
        let prior = ctx.req.cursor.0.get("credited_count").filter(|count| !count.is_null()).map(|count| count.as_i64().unwrap_or(0).max(0));
        let (mut skip, mut credited, everything) = match (run.rotated, prior) {
            (true, Some(count)) => (count, 0i64, false),
            (true, None) => (0, 0, true),
            (false, count) => (0, count.unwrap_or(0), false),
        };
        // A replay that stopped at the pass budget resumes where it stopped (the keys it delivered are in the seen bag, so a
        // position that no longer holds only costs a re-read from the start).
        let (from, first) = if incremental { (start, 0) } else { file.replay_start::<O>(&run.saved.replay) };
        let (mut committed, mut failure) = (start, None);
        let (mut ident, mut replayed) = (run.ident.clone(), (from, first));
        // The scan ended on a line still being written, not for want of room.
        let mut unfinished = false;
        // What every event's cursor repeats of the pass's final checkpoint (`finish` writes the same fields into it).
        let mut base = run.checkpoint.clone();
        base.insert("kind".into(), json!("jsonl"));
        base.insert("identity".into(), json!(file.identity));
        persisted::<O>(&ident, &mut base);
        let anchors = file.anchors()?;
        let reached = file.lines(from, first, incremental, |line| {
            if incremental && !line.complete {
                unfinished = true;
                return Flow::Stop;
            }
            let (before, before_committed) = (replayed, committed);
            if line.complete {
                replayed = (line.end, line.no);
            }
            if incremental {
                committed = line.end;
            }
            let record = match policy(ctx, O::read_line(&line), incremental) {
                Ok(Decoded::Object(record)) => record,
                Ok(_) => return Flow::Continue,
                Err(error) => {
                    failure = Some(error);
                    return Flow::Stop;
                }
            };
            let view = O::usage_view_in(&mut ident, &record, &line.bytes);
            let usage = match O::position_key(&ident, !incremental, line.no, line.end, &record) {
                Some(key) => fields.keyed(&view, key),
                None => fields.per_turn(&view),
            };
            let Some(usage) = usage else {
                return Flow::Continue;
            };
            if O::CREDITED && incremental && (everything || skip > 0) {
                // A record the cursor already counted: it is not delivered again, and it stays in the window.
                skip = skip.saturating_sub(1);
                credited += 1;
                remember(&mut recent, &mut window, &usage.key, O::RECENT);
                return Flow::Continue;
            }
            if if incremental { window.contains(&usage.key) } else { run.dedup.contains(&usage.key) } {
                return Flow::Continue;
            }
            let mut event = usage.event(&ident);
            if incremental {
                let mut after = recent.clone();
                after.push_back(usage.key.clone());
                if after.len() > O::RECENT {
                    after.pop_front();
                }
                let anchor = match anchors.at(line.end) {
                    Ok(anchor) => anchor,
                    Err(error) => {
                        failure = Some(error);
                        return Flow::Stop;
                    }
                };
                let mut cursor = base.clone();
                // What the log's context has become by this line (a section header above it) is part of where the cursor stands.
                persisted::<O>(&ident, &mut cursor);
                cursor.insert("offset".into(), json!(line.end));
                // The anchor digests the bytes behind the offset it sits with: this event's, not the pass's final one.
                cursor.insert("anchor".into(), json!(anchor));
                cursor.insert("recent_keys".into(), json!(after));
                if O::CREDITED {
                    cursor.insert("credited_count".into(), json!(credited + 1));
                }
                fit_request_bound(&mut cursor);
                event["checkpoint"] = Value::Object(cursor);
            }
            let mut taken = vec![event];
            // A replay's `seen` carries the key (the next page and the next call skip it); an incremental pass has its window.
            if !run.take("line", line.no as i64, &mut taken, (!incremental).then_some(usage.key.as_str())) {
                // This event would take the pass past the ledger: it waits for the next pass, which resumes at the line before.
                (replayed, committed) = (before, before_committed);
                return Flow::Stop;
            }
            if incremental {
                credited += 1;
                remember(&mut recent, &mut window, &usage.key, O::RECENT);
            }
            // The ledger is spent: stop after this line. An incremental pass picks up at its cursor; a replay at its position.
            if run.answer.full() {
                return Flow::Stop;
            }
            Flow::Continue
        })?;
        if let Some(error) = failure {
            return Err(error);
        }
        cap_ident(&mut ident);
        run.ident = ident;
        if !incremental {
            file.paged_replay(run, reached, replayed)?;
            return Ok(Scan { committed, owed: Owed::Nothing });
        }
        run.checkpoint.insert("recent_keys".into(), json!(recent));
        if O::CREDITED {
            run.checkpoint.insert("credited_count".into(), json!(credited));
        }
        // Room ran out (the ledger, or the byte cap of a pass) before the end of the readable log: the caller goes on from the
        // checkpoint now, it does not wait for the next write to the log.
        if !reached && !unfinished {
            run.answer.set("more", json!(true));
        }
        Ok(Scan { committed, owed: Owed::Nothing })
    }
}

impl<O: JsonlObserver> UsageScan<O, JsonlFile> for Cumulative {
    const WINDOWED: bool = false;
    /// Cumulative usage: the vendor reports running totals; the observation emits the delta since the saved totals.
    fn scan(file: &mut JsonlFile, ctx: &Ctx, run: &mut Run<O>, start: u64) -> io::Result<Scan> {
        let fields = usage_fields(ctx)?;
        let incremental = run.mode == Mode::Usage;
        let never_decrease = rules(ctx)?.totals_never_decrease;
        let previous = if !incremental {
            run.saved.totals.unwrap_or_default()
        } else if run.replaced {
            Totals::default()
        } else {
            Totals { input: ctx.req.cursor.count("input_total"), output: ctx.req.cursor.count("output_total") }
        };
        let (mut latest, mut last, mut committed) = (previous, None, start);
        let mut ident = run.ident.clone();
        let mut unfinished = false;
        let reached = if let Some(suffix) = fields.document {
            // The totals are the whole document beside the log, not records of it: nothing of the log is read, so the cursor stays.
            if let Some(Reading { totals, timestamp }) = file.document(ctx.req.path, suffix)?.and_then(|doc| {
                ident.model = fields.model_of(&doc);
                fields.reading(&doc)
            }) {
                (latest, last) = (totals, Some((0, timestamp)));
            }
            true
        } else {
            file.lines(start, 0, incremental, |line| {
            if incremental && !line.complete {
                unfinished = true;
                return Flow::Stop;
            }
            if incremental {
                committed = line.end;
            }
            if let Decoded::Object(record) = O::read_line(&line) {
                if let Some(Reading { totals, timestamp }) = O::reading(fields, &mut ident, &record) {
                    latest = totals;
                    last = Some((line.end, timestamp));
                }
            }
            Flow::Continue
            })?
        };
        if incremental && !reached && !unfinished {
            run.answer.set("more", json!(true));
        }
        cap_ident(&mut ident);
        run.ident = ident;
        if !incremental {
            run.saved.totals = Some(latest);
        }
        // Under `totals_never_decrease` a reading below what was saved leaves that total where it was (each component alone).
        let kept = if never_decrease { Totals { input: previous.input.max(latest.input), output: previous.output.max(latest.output) } } else { latest };
        run.checkpoint.insert("input_total".into(), json!(kept.input));
        run.checkpoint.insert("output_total".into(), json!(kept.output));
        Ok(Scan { committed, owed: Owed::Delta { previous, latest, last } })
    }
}

impl JsonlFile {
    /// The high-water activity scan: lines above the saved water, each parsed and handed to the adapter once.
    ///
    /// The pass resumes at the saved byte position when the file still holds the bytes that position fingerprinted, so a
    /// pass that stops at the byte cap picks up where it stopped. A position the file no longer matches (a replaced or
    /// rewritten file) starts over; the water mark then survives only under the declared `water_survives_replacement`
    /// rule, and a restart that reaches the end of a file shorter than the water marks what it holds as read.
    /// Returns the offset the checkpoint stays at (the pass's start: activity never moves the usage cursor).
    fn activity<O: JsonlObserver>(&mut self, ctx: &Ctx, run: &mut Run<O>) -> io::Result<u64> {
        if let Some(carried) = run.saved.legacy_cursor.take() {
            self.spend(&carried, run)?;
        }
        let position = match &run.saved.position {
            Some(at) if at.identity == self.identity && at.offset <= self.len => {
                (self.fingerprint(at.offset)? == at.fingerprint).then(|| at.clone())
            }
            _ => None,
        };
        if run.saved.position.is_some() && position.is_none() && !rules(ctx)?.water_survives_replacement {
            run.saved.water = 0;
        }
        let (start, first) = position.map_or((0, 0), |at| (at.offset, at.line));
        let initial = run.saved.water;
        let mut high = initial;
        let (mut committed, mut committed_line) = (start, first);
        let (mut last_complete, mut total) = (0u64, 0u64);
        let (mut error, mut failure) = (None, None);
        // The scan ended on a line it could not use (still being written, or an error), not for want of room.
        let mut halted = false;
        let non_object_fails = rules(ctx)?.activity_non_object_fails;
        let mut ident = run.ident.clone();
        let reached = self.lines(start, first, true, |line| {
            let at = (total, committed, committed_line, last_complete, high);
            total = line.no;
            if line.complete {
                committed = line.end;
                committed_line = line.no;
                if !line.blank() {
                    last_complete = line.no;
                }
            }
            if line.no <= initial {
                return Flow::Continue;
            }
            // A non-object record is this pass's own error (progress kept) under `activity_non_object_fails`, whatever the
            // other scans do with it; without the rule it is skipped like a line that does not parse.
            let decoded = match O::read_line(&line) {
                Decoded::NotObject if non_object_fails => Ok(Decoded::NotObject),
                Decoded::NotObject => Ok(Decoded::Unparsable),
                other => policy(ctx, other, false),
            };
            match decoded {
                Err(failed) => {
                    failure = Some(failed);
                    halted = true;
                    return Flow::Stop;
                }
                Ok(Decoded::Blank) => {}
                Ok(Decoded::Unparsable | Decoded::Utf8Error) => {
                    if !line.complete {
                        halted = true;
                        return Flow::Stop;
                    }
                    high = line.no;
                }
                Ok(Decoded::NotObject) => {
                    // The baseline raises here after advancing; the progress is kept, the prior events are not.
                    high = line.no;
                    run.answer.discard_events();
                    error = Some(not_an_object(ctx).to_owned());
                    halted = true;
                    return Flow::Stop;
                }
                Ok(Decoded::Object(record)) => {
                    high = line.no;
                    // What the hook may change is kept to put back if the line has to wait. A line's events and the text it leaves
                    // open weigh at most a few times the line (a JSON escape is at most six bytes), plus the open text it may
                    // release into an event and the identity fields each event repeats (all capped); a pass that is far from the end
                    // of the ledger cannot be refused, so it keeps nothing.
                    let bound = 12 * (run.saved.last_text.len() + line.bytes.len()) + ACTIVITY_MARGIN;
                    let kept = (run.answer.holds() && run.room() < bound).then(|| (run.saved.last_text.clone(), run.saved.adapter.clone()));
                    let mut added = Vec::new();
                    O::activity(&mut run.saved.last_text, &mut run.saved.adapter, &ident, &record, if O::PLACE_IS_OFFSET { line.end } else { line.no }, &mut added);
                    let cut = run.bound_open_text();
                    // The line's events and the text it leaves open are taken into the pass: they fit, or the line waits (the hook's
                    // changes are put back) for a pass it fits; a line that alone is over the whole ledger is cut (reported).
                    if !run.take("line", line.no as i64, &mut added, None) {
                        let Some((text, adapter)) = kept else {
                            failure = Some(io::Error::other("an activity record grew past its line"));
                            return Flow::Stop;
                        };
                        (run.saved.last_text, run.saved.adapter) = (text, adapter);
                        (total, committed, committed_line, last_complete, high) = at;
                        return Flow::Stop;
                    }
                    if let Some(bytes) = cut {
                        run.report_cut("last_text", line.no as i64, bytes);
                    }
                    if run.answer.full() {
                        return Flow::Stop;
                    }
                }
            }
            Flow::Continue
        })?;
        if let Some(failed) = failure {
            return Err(failed);
        }
        let errored = error.is_some();
        run.error = error;
        if !reached && !halted {
            // Room ran out (the answer ledger, or the byte cap of a pass) before the end of the readable log: the caller asks again, it
            // does not wait for the next write to the file to read what is already there.
            run.answer.set("more", json!(true));
        } else if !errored {
            // The end of the log was read: what the silence after it means is the vendor's. The events wait for the next pass like a line's.
            // The quiet is judged on the modification time as the open handle reports it now, after the read (a record appended while the
            // pass ran is not silence), as the Python baseline stats the file after its read loop.
            ident.modified_ms = self.modified_now();
            let kept = (run.saved.last_text.clone(), run.saved.adapter.clone());
            let mut added = Vec::new();
            O::settle(&mut run.saved.last_text, &mut run.saved.adapter, &ident, &mut added);
            if !added.is_empty() && !run.take("settle", total as i64, &mut added, None) {
                (run.saved.last_text, run.saved.adapter) = kept;
                run.answer.set("more", json!(true));
            }
        }
        let rewound = reached && start == 0 && initial > 0 && total < initial;
        if rewound || high > initial || committed > start {
            run.saved.water = if rewound { last_complete } else { high };
            run.saved.position = Some(Position {
                line: committed_line,
                offset: committed,
                identity: self.identity.clone(),
                fingerprint: self.fingerprint(committed)?,
                context: Map::new(),
            });
        }
        Ok(start)
    }

    /// The checkpoint the pass leaves, and the delta event a cumulative pass owes.
    fn finish<O: JsonlObserver>(&mut self, run: &mut Run<O>, committed: u64, owed: Owed) -> io::Result<()> {
        let checkpoint = &mut run.checkpoint;
        checkpoint.insert("kind".into(), json!("jsonl"));
        checkpoint.insert("offset".into(), json!(committed));
        checkpoint.insert("identity".into(), json!(self.identity));
        checkpoint.insert("anchor".into(), json!(self.anchor_at(committed)?));
        persisted::<O>(&run.ident, checkpoint);
        fit_request_bound(checkpoint);
        let base = Value::Object(checkpoint.clone());
        match owed {
            Owed::Nothing => {}
            Owed::Delta { previous, latest, last } => {
                let Some((end, timestamp)) = last else {
                    return Ok(());
                };
                if latest.input >= previous.input && latest.output >= previous.output && latest != previous {
                    let mut cursor = base;
                    cursor["offset"] = json!(end);
                    // The anchor digests the bytes behind the offset it sits with: this cursor's, not the pass's.
                    cursor["anchor"] = json!(self.anchor_at(end)?);
                    let (ident, session) = (&run.ident, run.ident.session());
                    let mut delta = vec![json!({"vendor": ident.vendor, "session_id": session,
                        "input_tokens": latest.input - previous.input, "output_tokens": latest.output - previous.output,
                        "cwd": ident.cwd, "file_path": ident.path, "timestamp": timestamp,
                        "dedup_key": bound_key(format!("{}_cumulative::{session}::{}::{}", ident.vendor, latest.input, latest.output)),
                        "model": ident.model, "cli_version": ident.version, "checkpoint": cursor})];
                    // The pass holds no other event here, so the delta is always taken (its long fields capped, and reported, when
                    // it alone is over the ledger).
                    run.take("line", end as i64, &mut delta, None);
                }
            }
        }
        Ok(())
    }
}
