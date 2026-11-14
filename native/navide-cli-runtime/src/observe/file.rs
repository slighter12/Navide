//! The file backends. `Jsonl`: a byte-offset tail with identity, shrink and
//! anchor checks (main's `read_jsonl_tail`), and the dense line walk activity
//! uses (main's `ActivityLines` plus the readers' shared walk loop).
//! `TextLog`: the same tail over plain text, and a walk that replays the
//! whole file.
use super::activity::{At, Walk};
use super::{bag, usage, ActivityState, Backend, Error, File, Memo, TurnUsage, UsageRead};
use crate::blake2b;
use crate::decl::{ExactTurns, JsonlRecords, LineCursor, TextRecords, UsageRule, Vendor};
use crate::expr::{py_int, py_str_opt, py_strip};
use crate::text::ignore_invalid;
use serde_json::{Map, Value};
use std::fs::{File as FsFile, Metadata};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

/// One transcript per session, a JSON object per line.
#[derive(Debug)]
pub struct Jsonl;

/// One plain-text transcript per session.
#[derive(Debug)]
pub struct TextLog;

impl<V: Vendor<Backend = Jsonl>> Backend<V> for Jsonl {
    type Spec = JsonlRecords<V>;

    fn usage(file: &File<'_, V>, checkpoint: &Map<String, Value>, full: bool, _memo: &mut Memo) -> Result<UsageRead, Error> {
        match &file.records().usage {
            Some(spec) => usage::from_file(file, spec, checkpoint, full, |from| read_tail(file.path, from)),
            None => Ok(UsageRead::unchanged(checkpoint)),
        }
    }

    fn has_usage(spec: &Self::Spec) -> bool {
        spec.usage.is_some()
    }

    fn usage_rules(spec: &'static Self::Spec) -> Vec<&'static UsageRule<V>> {
        spec.usage.iter().flat_map(|usage| usage.rules).collect()
    }

    fn walk(walk: &mut Walk<'_, V>, state: &mut ActivityState, memo: &mut Memo) -> Result<(), Error> {
        let path = walk.file.path;
        match walk.file.records().cursor {
            LineCursor::Lines => {
                walk.restore(state);
                // main's readers test `high_water > 0` for "the header was read on an earlier poll".
                walk.resumed = state.hw > 0;
                let resume = memo.resume.get(path).copied();
                let end = walk_lines(path, state.hw, resume, |line| walk.record(line.value, At::line(line.number, line.end)))?;
                state.hw = state.hw.max(end.last_line);
                match end.resume {
                    Some(resume) => memo.resume.insert(path.to_owned(), resume),
                    None => memo.resume.remove(path),
                };
                walk.decode_error = end.decode_error;
            }
            LineCursor::Bytes { .. } => {
                let tail = read_tail(path, &state.cursor)?;
                if !tail.rotated {
                    walk.restore(state);
                }
                for (end, record) in &tail.records {
                    if let Some(record) = record {
                        walk.record(record, At::line(0, *end));
                    }
                }
                state.cursor = tail.checkpoint;
            }
        }
        Ok(())
    }

    fn load(spec: &'static Self::Spec, bag: &[String], state: &mut ActivityState) {
        if let LineCursor::Bytes { key } = spec.cursor {
            if let Some(Value::Object(cursor)) = bag::find(bag, key).and_then(|raw| serde_json::from_str::<Value>(raw).ok()) {
                state.cursor = cursor;
            }
        }
    }

    fn store(spec: &'static Self::Spec, state: &ActivityState, bag: &mut Vec<String>) {
        if let LineCursor::Bytes { key } = spec.cursor {
            let cursor = (!state.cursor.is_empty()).then(|| bag::py_json(&Value::Object(state.cursor.clone()), false, true));
            bag::replace(bag, key, cursor);
        }
    }

    const EXACT_TURNS: bool = true;

    fn exact_turns(file: &File<'_, V>, turns: &'static ExactTurns<V>, session_id: &str) -> Result<Vec<TurnUsage>, Error> {
        super::turns::exact(file, turns, session_id)
    }
}

impl<V: Vendor<Backend = TextLog>> Backend<V> for TextLog {
    type Spec = TextRecords<V>;

    fn usage(file: &File<'_, V>, checkpoint: &Map<String, Value>, full: bool, _memo: &mut Memo) -> Result<UsageRead, Error> {
        let records = file.records();
        match &records.usage {
            // main's whole-file pass opens the file in text mode: every line
            // counts, the unterminated last one included.
            Some(spec) if full => usage::from_file(file, spec, checkpoint, true, |_| whole_text(file.path, records.kind)),
            Some(spec) => usage::from_file(file, spec, checkpoint, false, |from| read_text_tail(file.path, from, records.kind)),
            None => Ok(UsageRead::unchanged(checkpoint)),
        }
    }

    fn has_usage(spec: &Self::Spec) -> bool {
        spec.usage.is_some()
    }

    fn usage_rules(spec: &'static Self::Spec) -> Vec<&'static UsageRule<V>> {
        spec.usage.iter().flat_map(|usage| usage.rules).collect()
    }

    fn walk(walk: &mut Walk<'_, V>, state: &mut ActivityState, _memo: &mut Memo) -> Result<(), Error> {
        walk.restore(state);
        let last = walk_text(walk.file.path, state.hw, |line, record, fresh| walk.record(record, At { fresh, ..At::line(line, 0) }))?;
        state.hw = state.hw.max(last);
        Ok(())
    }

    fn load(_spec: &'static Self::Spec, _bag: &[String], _state: &mut ActivityState) {}

    fn store(_spec: &'static Self::Spec, _state: &ActivityState, _bag: &mut Vec<String>) {}
}

const ANCHOR_BYTES: u64 = 512;

/// `st_dev:st_ino`, the identity main's checkpoints carry. `None` where the
/// platform gives none through `std` (Windows): a checkpoint shared with the
/// Python reader would then never match, so nothing is observed there
/// (`wire`'s `observes` answers an empty list).
#[cfg(unix)]
pub fn identity(meta: &Metadata) -> String {
    use std::os::unix::fs::MetadataExt;
    format!("{}:{}", meta.dev(), meta.ino())
}

#[cfg(not(unix))]
pub fn identity(_meta: &Metadata) -> String {
    String::from("0:0")
}

/// Whether this build can tell one file from its replacement.
pub const HAS_FILE_IDENTITY: bool = cfg!(unix);

#[cfg(unix)]
pub fn inode(meta: &Metadata) -> u64 {
    use std::os::unix::fs::MetadataExt;
    meta.ino()
}

#[cfg(not(unix))]
pub fn inode(_meta: &Metadata) -> u64 {
    0
}

fn anchor(file: &mut FsFile, end: u64) -> io::Result<String> {
    let start = end.saturating_sub(ANCHOR_BYTES);
    file.seek(SeekFrom::Start(start))?;
    let mut buf = vec![0u8; (end - start) as usize];
    file.read_exact(&mut buf)?;
    Ok(blake2b::hex_digest(&buf, 8))
}

/// Complete records after a checkpoint's offset.
pub struct Tail {
    /// `(byte offset just past the record, the record)`; `None` for a line
    /// that is not a JSON object.
    pub records: Vec<(u64, Option<Value>)>,
    pub checkpoint: Map<String, Value>,
    /// The cursor restarted at 0 (rewrite, shrink, replacement).
    pub rotated: bool,
    /// The file is a different one than the checkpoint's.
    pub replaced: bool,
}

pub fn read_tail(path: &Path, checkpoint: &Map<String, Value>) -> io::Result<Tail> {
    tail(path, checkpoint, "jsonl", |line| {
        std::str::from_utf8(line).ok().and_then(|text| serde_json::from_str::<Value>(text).ok()).filter(Value::is_object)
    })
}

/// The same tail over a plain-text transcript: every complete line is a
/// `{"line": text}` record.
pub fn read_text_tail(path: &Path, checkpoint: &Map<String, Value>, kind: &str) -> io::Result<Tail> {
    tail(path, checkpoint, kind, |line| Some(text_record(&ignore_invalid(line))))
}

/// Every line of a plain-text transcript as main's text-mode file yields
/// them (see `text_lines`), the unterminated last one included. The offsets
/// only tell the lines apart.
fn whole_text(path: &Path, kind: &str) -> io::Result<Tail> {
    let text = ignore_invalid(&std::fs::read(path)?);
    let records = text_lines(&text).map(|line| (line.end as u64, Some(text_record(line.text)))).collect();
    let mut checkpoint = Map::new();
    checkpoint.insert("kind".into(), kind.into());
    Ok(Tail { records, checkpoint, rotated: false, replaced: false })
}

fn text_record(line: &str) -> Value {
    let mut record = Map::new();
    record.insert("line".into(), line.trim_end_matches(['\r', '\n']).into());
    Value::Object(record)
}

fn tail(path: &Path, checkpoint: &Map<String, Value>, kind: &str, decode: impl Fn(&[u8]) -> Option<Value>) -> io::Result<Tail> {
    // One path lookup: the identity and length are the opened file's own.
    let mut file = FsFile::open(path)?;
    let meta = file.metadata()?;
    let identity = identity(&meta);
    let prior_identity = py_str_opt(checkpoint.get("identity"));
    let prior_anchor = py_str_opt(checkpoint.get("anchor"));
    let mut offset = py_int(checkpoint.get("offset")) as u64;
    let mut rotated = offset != 0 && (prior_identity != identity || meta.len() < offset);
    if offset != 0 && !rotated && !prior_anchor.is_empty() && anchor(&mut file, offset)? != prior_anchor {
        rotated = true;
    }
    let replaced = rotated && !prior_identity.is_empty() && prior_identity != identity;
    if rotated {
        offset = 0;
    }
    file.seek(SeekFrom::Start(offset))?;
    let mut rest = Vec::new();
    file.read_to_end(&mut rest)?;
    let mut records = Vec::new();
    let mut committed = offset;
    let mut start = 0;
    while let Some(nl) = rest[start..].iter().position(|b| *b == b'\n') {
        let line = &rest[start..start + nl + 1];
        start += nl + 1;
        committed = offset + start as u64;
        records.push((committed, decode(line)));
    }
    let anchor = if committed != 0 { anchor(&mut file, committed)? } else { String::new() };
    let mut next = checkpoint.clone();
    next.insert("kind".into(), kind.into());
    next.insert("offset".into(), committed.into());
    next.insert("identity".into(), identity.into());
    next.insert("anchor".into(), anchor.into());
    Ok(Tail { records, checkpoint: next, rotated, replaced })
}

/// Where the last walk of a file stopped: `(mark line, byte offset just past
/// it, inode)`. Reader memory only, as in main.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LineResume {
    pub line: usize,
    pub offset: u64,
    pub inode: u64,
}

/// One line a walk hands on: past the mark, non-blank, and either valid JSON
/// or the walk would have stopped.
pub struct Line<'a> {
    pub number: usize,
    /// Byte offset just past the line.
    pub end: u64,
    pub value: &'a Value,
}

pub struct WalkEnd {
    pub last_line: usize,
    pub resume: Option<LineResume>,
    /// A line was not UTF-8. main raises there, after its mark has moved
    /// past the lines before it, and the walk's events are lost.
    pub decode_error: Option<usize>,
}

/// The activity walk every line-cursor reader in main shares: lines after the
/// mark; a blank line is skipped unmarked; an unparseable line stops the walk
/// when it is unterminated (still being written) and is stepped over for good
/// when it is terminated.
pub fn walk_lines(
    path: &Path,
    high_water: usize,
    resume: Option<LineResume>,
    mut visit: impl FnMut(Line<'_>),
) -> io::Result<WalkEnd> {
    let mut file = FsFile::open(path)?;
    let meta = file.metadata()?;
    let inode = inode(&meta);
    let (mut first_line, mut offset) = (1usize, 0u64);
    if let Some(r) = resume.filter(|r| r.line == high_water && r.inode == inode) {
        if meta.len() >= r.offset && r.offset > 0 {
            let mut byte = [0u8; 1];
            file.seek(SeekFrom::Start(r.offset - 1))?;
            if file.read_exact(&mut byte).is_ok() && byte[0] == b'\n' {
                first_line = high_water + 1;
                offset = r.offset;
            }
        }
    }
    file.seek(SeekFrom::Start(offset))?;
    let mut rest = Vec::new();
    file.read_to_end(&mut rest)?;
    let mut resume_at = (first_line > 1).then_some(offset);
    let mut last_line = high_water;
    let mut decode_error = None;
    let mut start = 0usize;
    let mut number = first_line;
    while start < rest.len() {
        let len = rest[start..].iter().position(|b| *b == b'\n').map_or(rest.len() - start, |nl| nl + 1);
        let raw = &rest[start..start + len];
        start += len;
        offset += len as u64;
        let line_no = number;
        number += 1;
        let terminated = raw.ends_with(b"\n");
        if line_no == high_water && terminated {
            resume_at = Some(offset);
        }
        let Ok(text) = std::str::from_utf8(raw) else {
            decode_error = Some(line_no);
            break;
        };
        let stripped = py_strip(text);
        if stripped.is_empty() || line_no <= high_water {
            continue;
        }
        match serde_json::from_str::<Value>(stripped) {
            Err(_) if !terminated => break,
            Err(_) => {
                last_line = line_no;
                resume_at = Some(offset);
            }
            Ok(value) => {
                last_line = line_no;
                resume_at = terminated.then_some(offset);
                visit(Line { number: line_no, end: offset, value: &value });
            }
        }
    }
    let resume = resume_at.map(|offset| LineResume { line: last_line, offset, inode });
    Ok(WalkEnd { last_line, resume, decode_error })
}

/// One line of a text-mode read.
pub struct TextLine<'t> {
    /// 1-based.
    pub number: usize,
    pub text: &'t str,
    /// False for a last line with no line ending yet.
    pub terminated: bool,
    /// Offset just past the line (and its ending) in the decoded text.
    pub end: usize,
}

/// The lines of a text as Python's text-mode file yields them (universal
/// newlines): `\n`, `\r\n` and a lone `\r` each end a line, and what follows
/// the last ending is a line too.
pub fn text_lines(text: &str) -> impl Iterator<Item = TextLine<'_>> {
    let mut at = 0;
    let mut number = 0;
    std::iter::from_fn(move || {
        let rest = &text[at..];
        if rest.is_empty() {
            return None;
        }
        number += 1;
        let (len, width) = match rest.find(['\r', '\n']) {
            Some(found) => (found, if rest[found..].starts_with("\r\n") { 2 } else { 1 }),
            None => (rest.len(), 0),
        };
        at += len + width;
        Some(TextLine { number, text: &rest[..len], terminated: width > 0, end: at })
    })
}

/// A replay walk over a plain-text transcript: every line from the top, read
/// as main's text-mode `open(errors="ignore")` reads it (invalid UTF-8
/// dropped), with whether it is past `high_water`. A new line still
/// unterminated ends the walk before it. Returns the last line walked.
pub fn walk_text(path: &Path, high_water: usize, mut visit: impl FnMut(usize, &Value, bool)) -> io::Result<usize> {
    let text = ignore_invalid(&std::fs::read(path)?);
    let mut last_line = high_water;
    for line in text_lines(&text) {
        let fresh = line.number > high_water;
        if fresh && !line.terminated {
            break;
        }
        last_line = line.number;
        visit(line.number, &text_record(line.text), fresh);
    }
    Ok(last_line)
}

/// Every JSON record of the file with its 1-based line number, for the
/// whole-file passes (turns, header slots). Blank and malformed lines are
/// skipped but still counted. `strict`: a file that is not UTF-8 is an
/// error, as main's strict text-mode open raises (else its bad bytes are
/// replaced).
pub fn all_lines(path: &Path, limit: usize, strict: bool, mut visit: impl FnMut(usize, &str, Option<&Value>) -> bool) -> Result<(), Error> {
    let mut each = |index: usize, raw: &str| {
        let stripped = py_strip(raw);
        let value = if stripped.is_empty() { None } else { serde_json::from_str::<Value>(stripped).ok() };
        visit(index + 1, stripped, value.as_ref())
    };
    if strict {
        // The whole file must be UTF-8 before any line counts.
        let data = std::fs::read(path)?;
        let text = std::str::from_utf8(&data)
            .map_err(|err| Error::Decode { line: data[..err.valid_up_to()].iter().filter(|b| **b == b'\n').count() + 1 })?;
        for (index, raw) in text.split_inclusive('\n').enumerate() {
            if (limit != 0 && index >= limit) || !each(index, raw) {
                break;
            }
        }
        return Ok(());
    }
    // Line by line, and no further than the caller looks: a header read
    // (a few lines, on every call) must not cost the whole transcript.
    let mut reader = io::BufReader::new(FsFile::open(path)?);
    let mut line = Vec::new();
    let mut index = 0;
    while limit == 0 || index < limit {
        line.clear();
        if io::BufRead::read_until(&mut reader, b'\n', &mut line)? == 0 || !each(index, &String::from_utf8_lossy(&line)) {
            break;
        }
        index += 1;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn file_with(bytes: &[u8]) -> tempfile_lite::TempFile {
        let f = tempfile_lite::TempFile::new("tail");
        std::fs::File::create(&f.path).unwrap().write_all(bytes).unwrap();
        f
    }

    #[test]
    fn tail_reads_complete_lines_and_detects_a_rewrite() {
        let f = file_with(b"{\"a\":1}\n[1]\n{\"b\":\n{\"c\":3}");
        let tail = read_tail(&f.path, &Map::new()).unwrap();
        let values: Vec<_> = tail.records.iter().map(|(end, v)| (*end, v.is_some())).collect();
        assert_eq!(values, [(8, true), (12, false), (18, false)]);
        assert_eq!(tail.checkpoint["offset"], 18);
        assert!(!tail.rotated);
        std::fs::write(&f.path, b"{\"z\":1}\n{\"y\":2}\n{\"x\":3}\n").unwrap();
        let again = read_tail(&f.path, &tail.checkpoint).unwrap();
        assert!(again.rotated, "same length or longer but different bytes: the anchor catches it");
        assert_eq!(again.records.len(), 3);
    }

    #[test]
    fn walk_marks_corrupt_lines_and_stops_at_an_unterminated_one() {
        let f = file_with(b"{\"n\":1}\n\nnot json\n{\"n\":4}\n{\"n\":");
        let mut seen = Vec::new();
        let end = walk_lines(&f.path, 0, None, |line| seen.push(line.number)).unwrap();
        assert_eq!(seen, [1, 4]);
        assert_eq!(end.last_line, 4);
        let resume = end.resume.unwrap();
        assert_eq!((resume.line, resume.offset), (4, 26));
        std::fs::OpenOptions::new().append(true).open(&f.path).unwrap().write_all(b"5}\n").unwrap();
        let mut more = Vec::new();
        let end = walk_lines(&f.path, 4, Some(resume), |line| more.push(line.number)).unwrap();
        assert_eq!(more, [5]);
        assert_eq!(end.last_line, 5);
    }

    /// main's `test_claude_activity_resume` read meters, on this side: a resume
    /// point that no longer fits (the file shrank, or the mark is not the one
    /// it was taken at) is ignored and the file is read from its first line.
    #[test]
    fn a_stale_resume_point_reads_from_the_head() {
        let f = file_with(b"{\"n\":1}\n{\"n\":2}\n{\"n\":3}\n");
        let resume = walk_lines(&f.path, 0, None, |_| {}).unwrap().resume.unwrap();
        assert_eq!(resume.offset, 24);
        let mut seen = Vec::new();
        walk_lines(&f.path, 0, Some(resume), |line| seen.push(line.number)).unwrap();
        assert_eq!(seen, [1, 2, 3], "a fresh bag (mark 0) does not match the resume point");
        // Rewritten in place with longer lines: byte 23 is no longer a line end.
        let file = std::fs::OpenOptions::new().write(true).open(&f.path).unwrap();
        file.set_len(0).unwrap();
        drop(file);
        std::fs::OpenOptions::new().append(true).open(&f.path).unwrap().write_all(b"{\"n\":11}\n{\"n\":12}\n{\"n\":13}\n{\"n\":14}\n").unwrap();
        let mut seen = Vec::new();
        walk_lines(&f.path, 3, Some(resume), |line| seen.push((line.number, line.value["n"].as_i64()))).unwrap();
        assert_eq!(seen, [(4, Some(14))], "counted from the head: line 4 is the first past the mark");
    }

    #[test]
    fn text_lines_follow_universal_newlines() {
        let lines: Vec<(usize, &str, bool)> = text_lines("a\r\nb\rc\n\nd").map(|l| (l.number, l.text, l.terminated)).collect();
        assert_eq!(lines, [(1, "a", true), (2, "b", true), (3, "c", true), (4, "", true), (5, "d", false)]);
        assert_eq!(text_lines("").count(), 0);
        assert_eq!(text_lines("x\n").map(|l| l.end).collect::<Vec<_>>(), [2]);
    }

    #[test]
    fn a_strict_pass_refuses_a_file_that_is_not_utf8() {
        let f = file_with(b"{\"n\":1}\n{\"n\":\"\xff\"}\n");
        let mut seen = 0;
        all_lines(&f.path, 0, false, |_, _, _| {
            seen += 1;
            true
        })
        .unwrap();
        assert_eq!(seen, 2);
        assert!(matches!(all_lines(&f.path, 0, true, |_, _, _| true), Err(Error::Decode { line: 2 })));
    }

    /// A slot's header read happens on every call: it must cost the lines it
    /// looks at, not the transcript. A pipe that never ends stands for a
    /// transcript of any size: reading it whole would not return.
    #[cfg(unix)]
    #[test]
    fn a_header_read_stops_at_the_lines_it_was_asked_for() {
        use std::sync::mpsc::channel;
        use std::time::Duration;
        let f = tempfile_lite::TempFile::new("fifo");
        let name = std::ffi::CString::new(f.path.to_str().unwrap()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        let (release, held) = channel::<()>();
        let path = f.path.clone();
        let writer = std::thread::spawn(move || {
            let mut pipe = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
            pipe.write_all(b"{\"cwd\":\"/w\"}\n{\"n\":2}\n").unwrap();
            // Held open: the reader never sees an end of file.
            let _ = held.recv_timeout(Duration::from_secs(20));
        });
        let (found, got) = channel();
        let path = f.path.clone();
        std::thread::spawn(move || {
            let mut seen = Vec::new();
            let read = all_lines(&path, 1, false, |number, text, _| {
                seen.push((number, text.to_owned()));
                true
            });
            let _ = found.send(read.map(|()| seen).map_err(|err| err.to_string()));
        });
        let seen = got.recv_timeout(Duration::from_secs(5)).expect("the read returns without the end of the file");
        assert_eq!(seen, Ok(vec![(1, "{\"cwd\":\"/w\"}".to_owned())]));
        release.send(()).unwrap();
        writer.join().unwrap();
    }
}

/// A scratch file under the system temp dir, removed on drop (tests only;
/// keeps `tempfile` out of the dependency list).
#[cfg(test)]
pub mod tempfile_lite {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    pub struct TempFile {
        pub path: PathBuf,
    }

    impl TempFile {
        pub fn new(tag: &str) -> Self {
            let n = NEXT.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!("navide-rt-{}-{tag}-{n}.jsonl", std::process::id()));
            TempFile { path }
        }
    }

    impl Drop for TempFile {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}
