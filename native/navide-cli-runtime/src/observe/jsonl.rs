//! The JSONL backend: a byte-offset tail with identity, shrink and anchor
//! checks (main's `read_jsonl_tail`), and the dense line walk activity uses
//! (main's `ActivityLines` plus the readers' shared walk loop).
use crate::blake2b;
use crate::expr::{py_int, py_str_opt, py_strip};
use serde_json::{Map, Value};
use std::fs::{File, Metadata};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

const ANCHOR_BYTES: u64 = 512;

#[cfg(unix)]
fn identity(meta: &Metadata) -> String {
    use std::os::unix::fs::MetadataExt;
    format!("{}:{}", meta.dev(), meta.ino())
}

#[cfg(not(unix))]
fn identity(_meta: &Metadata) -> String {
    // Not built: Windows has no stable st_ino through std (see REDESIGN-REPORT).
    String::from("0:0")
}

#[cfg(unix)]
pub fn inode(meta: &Metadata) -> u64 {
    use std::os::unix::fs::MetadataExt;
    meta.ino()
}

#[cfg(not(unix))]
pub fn inode(_meta: &Metadata) -> u64 {
    0
}

fn anchor(file: &mut File, end: u64) -> io::Result<String> {
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
    let meta = std::fs::metadata(path)?;
    let identity = identity(&meta);
    let prior_identity = py_str_opt(checkpoint.get("identity"));
    let prior_anchor = py_str_opt(checkpoint.get("anchor"));
    let mut offset = py_int(checkpoint.get("offset")) as u64;
    let mut rotated = offset != 0 && (prior_identity != identity || meta.len() < offset);
    let mut file = File::open(path)?;
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
        let value = std::str::from_utf8(line)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(text).ok())
            .filter(Value::is_object);
        records.push((committed, value));
    }
    let anchor = if committed != 0 { anchor(&mut file, committed)? } else { String::new() };
    let mut next = checkpoint.clone();
    next.insert("kind".into(), "jsonl".into());
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
    let mut file = File::open(path)?;
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

/// Every JSON record of the file with its 1-based line number, for the
/// whole-file passes (turns, header slots). Blank and malformed lines are
/// skipped but still counted.
pub fn all_lines(path: &Path, limit: usize, mut visit: impl FnMut(usize, &str, Option<&Value>) -> bool) -> io::Result<()> {
    let mut data = Vec::new();
    File::open(path)?.read_to_end(&mut data)?;
    let text = String::from_utf8_lossy(&data);
    for (index, raw) in text.split_inclusive('\n').enumerate() {
        if limit != 0 && index >= limit {
            break;
        }
        let stripped = py_strip(raw);
        let value = if stripped.is_empty() { None } else { serde_json::from_str::<Value>(stripped).ok() };
        if !visit(index + 1, stripped, value.as_ref()) {
            break;
        }
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
