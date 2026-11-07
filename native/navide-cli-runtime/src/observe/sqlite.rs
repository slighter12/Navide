//! The SQLite source: a safe path open and one read-only connection per observation, with bounded results. What walks
//! the rows (the cursor, the pending set, the activity marker, the lookups) is `rows.rs`.
//!
//! macOS links the system SQLite; Linux links the bundled one `libsqlite3-sys` builds (`use … as _` keeps it in); Windows
//! imports the operating system's own `winsqlite3.dll` (no C compiler, no new crate: the same arrangement as macOS).
use super::{os, text_of, Ctx};
use serde_json::{json, Value};
use std::ffi::{c_char, c_int, c_void, CString};
use std::io;
use std::path::Path;

#[cfg(target_os = "linux")]
use libsqlite3_sys as _;

#[cfg_attr(target_os = "macos", link(name = "sqlite3"))]
#[cfg_attr(windows, link(name = "winsqlite3", kind = "raw-dylib"))]
unsafe extern "C" {
    fn sqlite3_open_v2(name: *const c_char, db: *mut *mut c_void, flags: c_int, vfs: *const c_char) -> c_int;
    fn sqlite3_close(db: *mut c_void) -> c_int;
    fn sqlite3_busy_timeout(db: *mut c_void, milliseconds: c_int) -> c_int;
    fn sqlite3_prepare_v2(db: *mut c_void, sql: *const c_char, bytes: c_int, statement: *mut *mut c_void, tail: *mut *const c_char) -> c_int;
    fn sqlite3_bind_int64(statement: *mut c_void, index: c_int, value: i64) -> c_int;
    fn sqlite3_bind_text(statement: *mut c_void, index: c_int, value: *const c_char, bytes: c_int, destroy: Option<unsafe extern "C" fn(*mut c_void)>) -> c_int;
    fn sqlite3_step(statement: *mut c_void) -> c_int;
    fn sqlite3_column_count(statement: *mut c_void) -> c_int;
    fn sqlite3_column_name(statement: *mut c_void, column: c_int) -> *const c_char;
    fn sqlite3_column_type(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_column_int64(statement: *mut c_void, column: c_int) -> i64;
    fn sqlite3_column_double(statement: *mut c_void, column: c_int) -> f64;
    fn sqlite3_column_text(statement: *mut c_void, column: c_int) -> *const u8;
    fn sqlite3_column_blob(statement: *mut c_void, column: c_int) -> *const c_void;
    fn sqlite3_column_bytes(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_finalize(statement: *mut c_void) -> c_int;
}

const OPEN_READONLY: c_int = 1;
const OPEN_URI: c_int = 0x40;
const OPEN_NOFOLLOW: c_int = 0x0100_0000;
/// The core's own failure codes are negative, so none of them can be mistaken for a code SQLite returned.
/// A result bigger than the row or byte cap is refused, not truncated.
pub const TOO_LARGE: i32 = -1;
/// A value the request supplied that a statement cannot take: neither an integer nor text, or text with a NUL in it.
pub const BAD_PARAMETER: i32 = -2;
/// The statement text itself holds a NUL (a declared name that is not an identifier).
pub const BAD_STATEMENT: i32 = -3;
/// `SQLITE_ERROR`: what a statement that names a missing table or column fails to prepare with.
const SQLITE_ERROR: i32 = 1;
const MAX_ROWS: usize = 1_000_000;
const MAX_BYTES: usize = 256 * 1024 * 1024;

pub type Cells = Vec<Value>;

/// Is `path` a WAL-mode database (header bytes 18 and 19, the file format versions, are 2)?
fn wal_header(path: &Path) -> bool {
    use std::io::Read;
    let mut header = [0u8; 20];
    std::fs::File::open(path).and_then(|mut file| file.read_exact(&mut header)).is_ok()
        && header.starts_with(b"SQLite format 3\0")
        && header[18] == 2
        && header[19] == 2
}

/// The URI SQLite opens: the path percent-encoded byte by byte (a `?`, `#` or `%` in a directory name is a path, never
/// a URI parameter), then the read-only parameters. `None` for a path SQLite cannot be given (Windows: one that is not Unicode).
pub fn uri(path: &Path, immutable: bool) -> Option<String> {
    let mut out = String::from("file:");
    for byte in os::uri_path(path)? {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' | b'/' => out.push(byte as char),
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out.push_str(if immutable { "?mode=ro&immutable=1" } else { "?mode=ro" });
    Some(out)
}

/// One short-lived read-only connection. The core opens exactly one per observation.
pub struct Connection {
    handle: *mut c_void,
    /// Bytes one statement may return in all; a cell that would cross it is refused before it is copied.
    pub(super) max_bytes: usize,
}

impl Connection {
    fn wrap(handle: *mut c_void) -> Self {
        Self { handle, max_bytes: MAX_BYTES }
    }

    pub fn open(path: &Path) -> Result<Self, i32> {
        #[cfg(test)]
        testing::OPENED.with(|opened| opened.set(opened.get() + 1));
        // SQLITE_OPEN_NOFOLLOW refuses a symlink anywhere in the path, so the directory is resolved here (a symlinked
        // home or data directory is legitimate) and only the store file itself must not be a link.
        let resolved;
        let path = match (path.parent().and_then(|dir| dir.canonicalize().ok()), path.file_name()) {
            (Some(dir), Some(name)) => {
                resolved = dir.join(name);
                resolved.as_path()
            }
            _ => return Err(14),
        };
        // A WAL store whose last writer closed has no -wal/-shm left, and the system SQLite refuses a plain read-only
        // open of it (SQLITE_CANTOPEN: it may not create the -shm), where the Python baseline's bundled SQLite creates
        // both. With neither file present the database file alone holds every committed row, so `immutable=1` reads it
        // with no locking and without creating or writing anything beside the store. A writer that appears meanwhile
        // writes to a new -wal first: those rows are read on the next pass, which sees the -wal and takes the ordinary
        // read-only path. Only a store whose header says WAL is opened this way: a rollback-journal store keeps the
        // ordinary open, whose locking it needs.
        let sidecars = ["-wal", "-shm"].map(|suffix| {
            let mut name = path.as_os_str().to_owned();
            name.push(suffix);
            Path::new(&name).exists()
        });
        let immutable = !sidecars.contains(&true) && wal_header(path);
        let name = CString::new(uri(path, immutable).ok_or(14)?).map_err(|_| 1)?;
        let mut raw = std::ptr::null_mut();
        let code = unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, OPEN_READONLY | OPEN_URI | OPEN_NOFOLLOW, std::ptr::null()) };
        if code != 0 {
            if !raw.is_null() {
                unsafe { sqlite3_close(raw) };
            }
            return Err(code);
        }
        let db = Self::wrap(raw);
        match unsafe { sqlite3_busy_timeout(db.handle, 250) } {
            0 => Ok(db),
            code => Err(code),
        }
    }

    /// The rows of a statement. A row with a TEXT cell that is not UTF-8 is not part of the answer.
    pub fn query(&self, sql: &str, parameters: &[Value]) -> Result<Vec<Cells>, i32> {
        self.query_reporting(sql, parameters).map(|(rows, _)| rows)
    }

    /// The rows of a statement, and for each row left out because a TEXT cell was not UTF-8, the name SQLite gives that
    /// column (a bad cell skips its row and is reported by name; it never wedges the pass).
    pub fn query_reporting(&self, sql: &str, parameters: &[Value]) -> Result<(Vec<Cells>, Vec<String>), i32> {
        let sql = CString::new(sql).map_err(|_| BAD_STATEMENT)?;
        let mut raw = std::ptr::null_mut();
        let code = unsafe { sqlite3_prepare_v2(self.handle, sql.as_ptr(), -1, &mut raw, std::ptr::null_mut()) };
        if code != 0 {
            if !raw.is_null() {
                unsafe { sqlite3_finalize(raw) };
            }
            return Err(code);
        }
        // Declare text storage first so even an error finalizes before it drops.
        let mut strings = Vec::new();
        let statement = Statement(raw);
        for (index, value) in parameters.iter().enumerate() {
            let code = if let Some(integer) = value.as_i64() {
                unsafe { sqlite3_bind_int64(statement.0, index as i32 + 1, integer) }
            } else {
                strings.push(CString::new(value.as_str().ok_or(BAD_PARAMETER)?).map_err(|_| BAD_PARAMETER)?);
                unsafe { sqlite3_bind_text(statement.0, index as i32 + 1, strings.last().unwrap().as_ptr(), -1, None) }
            };
            if code != 0 {
                return Err(code);
            }
        }
        let (mut rows, mut bytes_total, mut skipped) = (Vec::new(), 0usize, Vec::new());
        'rows: loop {
            let code = unsafe { sqlite3_step(statement.0) };
            if code == 101 {
                break;
            }
            if code != 100 || rows.len() >= MAX_ROWS {
                return Err(if code == 100 { TOO_LARGE } else { code });
            }
            let mut row = Vec::new();
            for column in 0..unsafe { sqlite3_column_count(statement.0) } {
                let value = match unsafe { sqlite3_column_type(statement.0, column) } {
                    1 => json!(unsafe { sqlite3_column_int64(statement.0, column) }),
                    2 => json!(unsafe { sqlite3_column_double(statement.0, column) }),
                    3 => {
                        let pointer = unsafe { sqlite3_column_text(statement.0, column) };
                        let length = unsafe { sqlite3_column_bytes(statement.0, column) } as usize;
                        if pointer.is_null() {
                            return Err(7);
                        }
                        // The cell's own length is known before a byte of it is copied: weigh it first.
                        if bytes_total.saturating_add(length) > self.max_bytes {
                            return Err(TOO_LARGE);
                        }
                        bytes_total += length;
                        let bytes = unsafe { std::slice::from_raw_parts(pointer, length) };
                        match std::str::from_utf8(bytes) {
                            Ok(text) => json!(text),
                            Err(_) => {
                                let name = unsafe { sqlite3_column_name(statement.0, column) };
                                let name = if name.is_null() { String::new() } else { unsafe { std::ffi::CStr::from_ptr(name) }.to_string_lossy().into_owned() };
                                skipped.push(name);
                                continue 'rows;
                            }
                        }
                    }
                    4 => {
                        // A BLOB cell travels as `{"$blob": "<hex>"}`; read it with `blob_bytes`.
                        let pointer = unsafe { sqlite3_column_blob(statement.0, column) }.cast::<u8>();
                        let length = unsafe { sqlite3_column_bytes(statement.0, column) } as usize;
                        // Hex doubles it: weigh the spelling, before it is built.
                        if bytes_total.saturating_add(length.saturating_mul(2)) > self.max_bytes {
                            return Err(TOO_LARGE);
                        }
                        bytes_total += length * 2;
                        let bytes = if pointer.is_null() || length == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(pointer, length) } };
                        json!({"$blob": hex(bytes)})
                    }
                    _ => Value::Null,
                };
                row.push(value);
            }
            rows.push(row);
        }
        // Finalize while SQLITE_STATIC strings are still alive.
        let code = unsafe { sqlite3_finalize(statement.0) };
        std::mem::forget(statement);
        if code != 0 {
            return Err(code);
        }
        Ok((rows, skipped))
    }
}

impl Drop for Connection {
    fn drop(&mut self) {
        unsafe { sqlite3_close(self.handle) };
    }
}

struct Statement(*mut c_void);
impl Drop for Statement {
    fn drop(&mut self) {
        unsafe { sqlite3_finalize(self.0) };
    }
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 15) as usize] as char);
    }
    out
}

/// The bytes of a BLOB cell (`{"$blob": "<hex>"}`); `None` for any other cell.
pub fn blob_bytes(value: &Value) -> Option<Vec<u8>> {
    let digits = value["$blob"].as_str()?.as_bytes();
    digits
        .chunks(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).ok()?, 16).ok())
        .collect()
}

/// Python-flavoured non-negative integer from a cell (a number, a bool, a numeric string; anything else 0).
pub fn number(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_bool().map(i64::from))
        .or_else(|| value.as_f64().filter(|v| v.is_finite()).map(|v| v as i64))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
        .max(0)
}

pub fn text(value: &Value) -> String {
    text_of(value)
}


/// Rows one usage page reads. A store with more unread rows is drained one page per observation, through the watermark.
pub const PAGE_ROWS: usize = 2_000;

/// A SQLite store opened for one observation. `db` is `None` when the store cannot be read (a locked or missing file): the
/// observation then answers an empty document, as the baseline does.
pub struct Sqlite {
    pub(super) db: Option<Connection>,
    pub(super) identity: String,
    pub(super) document_identity: Value,
    pub(super) page: usize,
    /// Payload bytes a whole-store replay (`legacy`) may read in one observation.
    pub(super) replay_bytes: usize,
    /// Rows a whole-store replay may read in one observation.
    pub(super) replay_rows: usize,
    /// The columns whose bad cells left a row out of an answer.
    /// The columns a cell was skipped in (a set: the answer names a column once, however many rows had a bad cell).
    skipped: std::cell::RefCell<std::collections::BTreeSet<String>>,
    /// Why the connection could not open (a store that is there but locked or unreadable), kept for the failure report.
    open_code: Option<i32>,
    /// Under the `store_error_reported` rule a query the store fails is not an error of the observation: the first failure is
    /// kept, spelled as the baseline spelled it, and the answer carries it as its `error`.
    report_failures: bool,
    failure: std::cell::RefCell<Option<String>>,
    /// A question about the schema (`holds`, `tables`) went unanswered by a store that was open.
    unlisted: std::cell::Cell<bool>,
}

impl Sqlite {
    /// Open under the core's bounds: a missing file is an empty store, anything but a regular file is refused.
    pub(super) fn open_with(ctx: &Ctx, page: usize) -> io::Result<Self> {
        Ok(Self { report_failures: ctx.core.compat.store_error_reported, ..Self::open_at(ctx.req.path, page)? })
    }

    /// A store opened for a lookup whose every failure is an answer (not owned), never an error: the first one is kept.
    pub(super) fn open_reporting(path: &Path, page: usize) -> io::Result<Self> {
        Ok(Self { report_failures: true, ..Self::open_at(path, page)? })
    }

    pub(super) fn open_at(path: &Path, page: usize) -> io::Result<Self> {
        // Not a regular file, or a link, is refused here (`os::stat_regular`); a path that cannot be inspected is an empty store.
        let Some(stat) = os::stat_regular(path)? else {
            return Ok(Self::unreadable(page));
        };
        let (db, open_code) = match Connection::open(path) {
            Ok(db) => (Some(db), None),
            Err(code) => (None, Some(code)),
        };
        Ok(Self {
            db,
            identity: stat.id.text(),
            document_identity: json!({"dev": stat.id.device, "ino": stat.id.index, "size": stat.len}),
            open_code,
            ..Self::unreadable(page)
        })
    }

    pub(super) fn unreadable(page: usize) -> Self {
        Self {
            db: None,
            identity: String::new(),
            document_identity: Value::Null,
            page,
            replay_bytes: super::rows::REPLAY_BYTES,
            replay_rows: super::rows::REPLAY_ROWS,
            skipped: Default::default(),
            open_code: None,
            report_failures: false,
            failure: Default::default(),
            unlisted: Default::default(),
        }
    }

    /// Does this observation's own connection hold a table of this name? A store that cannot be read holds none. It asks
    /// outside `rows`, so a store that cannot answer is not recorded as a failure of the declared reads.
    pub(super) fn holds(&self, table: &str) -> bool {
        let sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?";
        self.listed(self.db.as_ref().map(|db| db.query(sql, &[json!(table)]))).is_some_and(|rows| !rows.is_empty())
    }

    /// The names of the tables this observation's own connection holds, sorted (a store that cannot be read holds none).
    pub(super) fn tables(&self) -> Vec<String> {
        let sql = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name";
        let rows = self.listed(self.db.as_ref().map(|db| db.query(sql, &[]))).unwrap_or_default();
        rows.iter().filter_map(|row| row[0].as_str().map(str::to_owned)).collect()
    }

    /// The answer of a question about the schema; a store that cannot answer (busy, locked, unreadable) is remembered, because
    /// what it holds is then unknown, not "nothing".
    fn listed(&self, answer: Option<Result<Vec<Cells>, i32>>) -> Option<Vec<Cells>> {
        match answer {
            Some(Ok(rows)) => Some(rows),
            _ => {
                self.unlisted.set(true);
                None
            }
        }
    }

    /// Could the store not say which tables it holds (a file that is missing or cannot be opened, or a schema query that failed)?
    /// Its schema is then unknown: it fits no layout and it is no layout's problem either.
    pub(super) fn schema_unknown(&self) -> bool {
        self.db.is_none() || self.unlisted.get()
    }

    /// Keep the first failure of this observation, spelled as the baseline reader spelled it.
    fn fail(&self, code: i32, sql: &str) {
        self.failure.borrow_mut().get_or_insert_with(|| format!("sqlite error {code} in `{}`", sql.chars().take(48).collect::<String>()));
    }

    /// The first query failure this observation kept, if the declaration reports them.
    pub(super) fn failure(&self) -> Option<String> {
        self.failure.borrow().clone()
    }

    /// A query on the observation's connection. `Ok(None)` is a store that is busy or locked right now (as the baseline
    /// treats it: nothing observed this cycle). A query the store cannot prepare is an error of the observation: the core
    /// builds every query from validated, quoted names, so this is a store whose schema is not the declared one. So is an
    /// oversize result.
    pub fn rows(&self, sql: &str, parameters: &[Value]) -> io::Result<Option<Vec<Cells>>> {
        let Some(db) = &self.db else {
            if let (true, Some(code)) = (self.report_failures, self.open_code) {
                self.fail(code, sql);
            }
            return Ok(None);
        };
        match db.query_reporting(sql, parameters) {
            Ok((rows, skipped)) => {
                self.skipped.borrow_mut().extend(skipped);
                Ok(Some(rows))
            }
            Err(code) if self.report_failures => {
                self.fail(code, sql);
                Ok(None)
            }
            Err(TOO_LARGE) => Err(io::Error::other("observation result exceeds the row or byte limit")),
            Err(SQLITE_ERROR) => Err(io::Error::other("sqlite error 1: the store does not hold the declared tables and columns")),
            Err(BAD_PARAMETER) => Err(io::Error::other("a request value is not text or a number the store can take (for example a NUL byte)")),
            Err(BAD_STATEMENT) => Err(io::Error::other("a declared name is not a plain identifier")),
            Err(_) => Ok(None),
        }
    }

    /// The columns whose non-UTF-8 text left a row out of an answer, once each per row (empty when none did).
    pub fn skipped(&self) -> Vec<String> {
        self.skipped.borrow().iter().cloned().collect()
    }
}

/// A writable store on disk for the adapter tests: the shared `session`/`message`/`part` schema, one live writer.
#[cfg(test)]
pub mod testing {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    thread_local! {
        /// How many connections this thread has opened: an observation opens exactly one.
        pub static OPENED: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
    }

    pub struct Fixture {
        pub root: PathBuf,
        pub path: PathBuf,
        pub writer: Option<Connection>,
    }

    impl Fixture {
        /// An empty store: the caller creates whatever shape it is testing.
        pub fn bare(file: &str) -> Self {
            let root = std::env::temp_dir().join(format!("observe-store-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            std::fs::create_dir(&root).unwrap();
            let path = root.join(file);
            let name = CString::new(path.to_str().unwrap()).unwrap();
            let mut raw = std::ptr::null_mut();
            assert_eq!(unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, 6, std::ptr::null()) }, 0);
            let writer = Connection::wrap(raw);
            // The bundled SQLite (Linux) enforces foreign keys by default, the system one (macOS) does not; the Python baseline's
            // writer leaves them off, and the scenarios insert ledger rows for sessions that were never created.
            writer.query("PRAGMA foreign_keys=OFF", &[]).unwrap();
            Self { root, path, writer: Some(writer) }
        }

        pub fn new(file: &str) -> Self {
            let store = Self::bare(file);
            let writer = store.writer.as_ref().unwrap();
            for sql in [
                "CREATE TABLE session(id TEXT PRIMARY KEY,directory TEXT,parent_id TEXT,version TEXT)",
                "CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT)",
                "CREATE TABLE part(id TEXT PRIMARY KEY,message_id TEXT,session_id TEXT,data TEXT,time_created INTEGER)",
                "INSERT INTO session VALUES('ses_own','/own/work',NULL,'fixture-v1')",
            ] {
                writer.query(sql, &[]).unwrap();
            }
            store
        }

        pub fn sql(&self, sql: &str, values: &[Value]) {
            self.writer.as_ref().unwrap().query(sql, values).unwrap();
        }

        pub fn message(&self, id: &str, data: Value) {
            self.sql("INSERT INTO message VALUES(?,'ses_own',?)", &[json!(id), json!(data.to_string())]);
        }

        pub fn part(&self, id: &str, message: &str, data: Value) {
            self.sql("INSERT INTO part VALUES(?,?,'ses_own',?,1700000000123)", &[json!(id), json!(message), json!(data.to_string())]);
        }

        pub fn files(&self) -> Vec<String> {
            let mut names: Vec<String> = std::fs::read_dir(&self.root).unwrap().map(|entry| entry.unwrap().file_name().into_string().unwrap()).collect();
            names.sort();
            names
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            drop(self.writer.take());
            std::fs::remove_dir_all(&self.root).unwrap();
        }
    }

    /// A connection with both reader and writer rights and the shared-cache flag, as two cooperating handles.
    pub fn shared(path: &Path, flags: c_int) -> Connection {
        let name = CString::new(path.to_str().unwrap()).unwrap();
        let mut raw = std::ptr::null_mut();
        assert_eq!(unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, flags | 0x20000, std::ptr::null()) }, 0);
        Connection::wrap(raw)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_fixture_writer_leaves_foreign_keys_off_on_every_platform() {
        let store = testing::Fixture::bare("fk.db");
        store.sql("CREATE TABLE parent(id TEXT PRIMARY KEY)", &[]);
        store.sql("CREATE TABLE child(id TEXT, parent_id TEXT REFERENCES parent(id))", &[]);
        store.sql("INSERT INTO child VALUES('c','ghost')", &[]);
        let pragma = store.writer.as_ref().unwrap().query("PRAGMA foreign_keys", &[]).unwrap();
        assert_eq!(pragma, vec![vec![json!(0)]]);
    }

    /// A POSIX path is its own URI path; the same encoding applies on Windows to the path `os::uri_path` gives (its own test), which this
    /// platform-neutral check does not need to know.
    #[cfg(unix)]
    #[test]
    fn a_path_with_uri_metacharacters_is_encoded_as_a_path() {
        let encoded = uri(Path::new("/tmp/a?b#c%41 d/opencode.db"), false).unwrap();
        assert_eq!(encoded, "file:/tmp/a%3Fb%23c%2541%20d/opencode.db?mode=ro");
        assert!(uri(Path::new("/x"), true).unwrap().ends_with("?mode=ro&immutable=1"));
    }

    #[cfg(windows)]
    #[test]
    fn a_windows_path_is_encoded_as_a_drive_path_and_its_metacharacters_as_data() {
        let encoded = uri(Path::new(r"C:\Users\a?b#c%41 d\opencode.db"), false).unwrap();
        assert_eq!(encoded, "file:/C%3A/Users/a%3Fb%23c%2541%20d/opencode.db?mode=ro");
        assert!(uri(Path::new(r"C:\x"), true).unwrap().ends_with("?mode=ro&immutable=1"));
        assert_eq!(uri(Path::new(r"\\server\share\x.db"), false).unwrap(), "file:////server/share/x.db?mode=ro");
        assert_eq!(uri(Path::new(r"\\?\C:\x\y.db"), false).unwrap(), "file:/C%3A/x/y.db?mode=ro", "the verbatim prefix canonicalize adds is not part of the name");
    }

    #[test]
    fn a_store_under_a_directory_named_with_uri_metacharacters_opens_and_reads() {
        // A `?` is not a character Windows lets a file name hold; `#`, `%` and `=` are the metacharacters left to it.
        let query = if cfg!(windows) { "" } else { "?" };
        let root = std::env::temp_dir().join(format!("sqlite-uri-{}{query}x=1#frag%41", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        let path = root.join("opencode.db");
        let name = CString::new(path.to_str().unwrap()).unwrap();
        let mut raw = std::ptr::null_mut();
        assert_eq!(unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, 6, std::ptr::null()) }, 0);
        let writer = Connection::wrap(raw);
        writer.query("CREATE TABLE session(id TEXT)", &[]).unwrap();
        writer.query("INSERT INTO session VALUES('s')", &[]).unwrap();
        let rows = Connection::open(&path).and_then(|db| db.query("SELECT id FROM session", &[]));
        drop(writer);
        std::fs::remove_dir_all(&root).unwrap();
        assert_eq!(rows.unwrap(), vec![vec![json!("s")]]);
    }

    #[test]
    fn a_symlinked_store_is_not_followed() {
        let root = std::env::temp_dir().join(format!("sqlite-link-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("real.db"), b"").unwrap();
        os::link_file(&root.join("real.db"), &root.join("opencode.db")).expect("this runner cannot create a file symlink");
        #[cfg(unix)]
        assert!(Connection::open(&root.join("opencode.db")).is_err(), "SQLITE_OPEN_NOFOLLOW refuses the link");
        // The path the observation takes is the one that refuses a link before SQLite is asked (SQLite's own NOFOLLOW is POSIX only).
        assert!(Sqlite::open_at(&root.join("opencode.db"), 10).is_err(), "a symlinked store was opened by the observation");
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn each_read_is_read_only_waits_250ms_and_reports_the_real_busy_code() {
        use super::testing::Fixture;
        let store = Fixture::new("opencode.db");
        for _ in 0..2 {
            let db = Connection::open(&store.path).unwrap();
            assert_eq!(db.query("PRAGMA busy_timeout", &[]).unwrap(), vec![vec![json!(250)]]);
            assert_eq!(db.query("INSERT INTO session VALUES('bad','',NULL,'')", &[]).unwrap_err(), 8);
        }
        store.sql("BEGIN EXCLUSIVE", &[]);
        let start = std::time::Instant::now();
        assert_eq!(Connection::open(&store.path).unwrap().query("SELECT id FROM session", &[]).unwrap_err(), 5);
        assert!(start.elapsed() >= std::time::Duration::from_millis(200));
        store.sql("ROLLBACK", &[]);
    }

    #[test]
    fn a_locked_shared_connection_reports_its_code_and_then_reads_the_committed_row() {
        use super::testing::{shared, Fixture};
        let store = Fixture::new("opencode.db");
        let (writer, reader) = (shared(&store.path, 2), shared(&store.path, 1));
        writer.query("BEGIN IMMEDIATE", &[]).unwrap();
        writer.query("UPDATE session SET version='uncommitted'", &[]).unwrap();
        assert_eq!(reader.query("SELECT version FROM session", &[]).unwrap_err(), 6);
        writer.query("ROLLBACK", &[]).unwrap();
        assert_eq!(reader.query("SELECT version FROM session", &[]).unwrap()[0][0], "fixture-v1");
    }

    /// A cell is weighed against the bytes still allowed before it is copied: a cell that would cross the cap is refused whole,
    /// whatever it holds. A text cell that is not UTF-8 is the case that shows the order: it is skipped, never copied, so a late
    /// check (after the row is built) would never see it.
    #[test]
    fn a_cell_larger_than_the_remaining_byte_budget_is_refused_before_it_is_copied() {
        let store = super::testing::Fixture::bare("cap.db");
        store.sql("CREATE TABLE cells(a, b)", &[]);
        store.sql("INSERT INTO cells VALUES(zeroblob(40), 'abcdefghij')", &[]);
        store.sql("INSERT INTO cells VALUES(1, CAST(zeroblob(40) AS TEXT))", &[]);
        store.sql("INSERT INTO cells VALUES(1, CAST(x'ff' || zeroblob(40) AS TEXT))", &[]);
        let mut db = Connection::open(&store.path).unwrap();

        // The blob cell weighs 2 x 40 bytes in hex and the text 10: 90. A cap of 90 admits them, one byte less refuses.
        db.max_bytes = 90;
        assert_eq!(db.query("SELECT a, b FROM cells WHERE rowid = 1", &[]).unwrap().len(), 1, "equal to the cap is read");
        db.max_bytes = 89;
        assert_eq!(db.query("SELECT a, b FROM cells WHERE rowid = 1", &[]).unwrap_err(), TOO_LARGE);

        // One oversized cell among small rows refuses the statement, and a cell that is not UTF-8 is no exception.
        db.max_bytes = 30;
        assert_eq!(db.query("SELECT b FROM cells WHERE rowid = 2", &[]).unwrap_err(), TOO_LARGE, "a valid text cell over the budget");
        assert_eq!(db.query("SELECT b FROM cells WHERE rowid = 3", &[]).unwrap_err(), TOO_LARGE, "a text cell over the budget that is not UTF-8");
        assert_eq!(db.query("SELECT a FROM cells WHERE rowid = 1", &[]).unwrap_err(), TOO_LARGE, "a blob over the budget");
        db.max_bytes = 1_000;
        let (rows, skipped) = db.query_reporting("SELECT b FROM cells WHERE rowid = 3", &[]).unwrap();
        assert_eq!((rows.len(), skipped), (0, vec!["b".to_owned()]), "within the budget the same cell is skipped and reported");
    }

    #[test]
    fn blob_cells_round_trip_through_hex() {
        let bytes: Vec<u8> = (0..=255u8).collect();
        assert_eq!(blob_bytes(&json!({"$blob": hex(&bytes)})), Some(bytes));
        assert_eq!(blob_bytes(&json!("text")), None);
    }
}
