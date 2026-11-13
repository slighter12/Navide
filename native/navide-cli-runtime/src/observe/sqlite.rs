//! The SQLite backend: one short-lived read-only connection per query, as
//! main's readers open one (`sqlite3.connect("file:…?mode=ro")`, a 250 ms
//! busy wait). A row is read into a JSON object keyed by column name, so the
//! declaration's predicates and texts read rows exactly as they read JSONL
//! records. Any failure (busy, locked, missing, a schema the query does not
//! fit) is "no rows this cycle", as main treats it.
//!
//! The FFI connection is the evaluation crate's: macOS links the system
//! SQLite, Linux the bundled one `libsqlite3-sys` builds, Windows the OS's
//! `winsqlite3.dll`.
use super::Error;
use crate::decl::{Partition, Rows, Sqlite};
use crate::expr::{self, Scope, Slots};
use serde_json::{Map, Value};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
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
    fn sqlite3_bind_text(statement: *mut c_void, index: c_int, text: *const c_char, bytes: c_int, destructor: *const c_void) -> c_int;
    fn sqlite3_step(statement: *mut c_void) -> c_int;
    fn sqlite3_column_count(statement: *mut c_void) -> c_int;
    fn sqlite3_column_name(statement: *mut c_void, column: c_int) -> *const c_char;
    fn sqlite3_column_type(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_column_int64(statement: *mut c_void, column: c_int) -> i64;
    fn sqlite3_column_double(statement: *mut c_void, column: c_int) -> f64;
    fn sqlite3_column_text(statement: *mut c_void, column: c_int) -> *const u8;
    fn sqlite3_column_bytes(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_finalize(statement: *mut c_void) -> c_int;
}

const OPEN_READONLY: c_int = 1;
const OPEN_URI: c_int = 0x40;
const ROW: c_int = 100;
const DONE: c_int = 101;

/// main's `_BUSY_TIMEOUT_MS`.
const BUSY_TIMEOUT_MS: c_int = 250;

/// Is `path` a WAL-mode database (header bytes 18 and 19 are 2)?
fn wal_header(path: &Path) -> bool {
    use std::io::Read;
    let mut header = [0u8; 20];
    std::fs::File::open(path).and_then(|mut file| file.read_exact(&mut header)).is_ok()
        && header.starts_with(b"SQLite format 3\0")
        && header[18] == 2
        && header[19] == 2
}

/// The URI SQLite opens: the path percent-encoded byte by byte (a `?`, `#`
/// or `%` in a directory name is part of the path), then the read-only mode.
fn uri(path: &Path, immutable: bool) -> Option<String> {
    let mut out = String::from("file:");
    for byte in path.to_str()?.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' | b'/' => out.push(byte as char),
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out.push_str(if immutable { "?mode=ro&immutable=1" } else { "?mode=ro" });
    Some(out)
}

struct Connection(*mut c_void);

impl Drop for Connection {
    fn drop(&mut self) {
        unsafe { sqlite3_close(self.0) };
    }
}

struct Statement(*mut c_void);

impl Drop for Statement {
    fn drop(&mut self) {
        unsafe { sqlite3_finalize(self.0) };
    }
}

impl Connection {
    fn open(path: &Path) -> Option<Self> {
        if !path.is_file() {
            return None;
        }
        // A WAL database whose last writer closed has no -wal/-shm, and the
        // system SQLite refuses a read-only open of it (it may not create the
        // -shm) where Python's bundled one reads it. With neither file there
        // the database file holds every committed row: `immutable=1` reads it
        // without creating anything beside it.
        let sidecars = ["-wal", "-shm"].iter().any(|suffix| {
            let mut name = path.as_os_str().to_owned();
            name.push(suffix);
            Path::new(&name).exists()
        });
        let name = CString::new(uri(path, !sidecars && wal_header(path))?).ok()?;
        let mut raw = std::ptr::null_mut();
        let code = unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, OPEN_READONLY | OPEN_URI, std::ptr::null()) };
        let db = (!raw.is_null()).then_some(Connection(raw));
        if code != 0 {
            return None;
        }
        let db = db?;
        (unsafe { sqlite3_busy_timeout(db.0, BUSY_TIMEOUT_MS) } == 0).then_some(db)
    }

    fn query(&self, sql: &str, params: &[Param], json: &[&str]) -> Option<Vec<Value>> {
        let sql = CString::new(sql).ok()?;
        let mut raw = std::ptr::null_mut();
        let code = unsafe { sqlite3_prepare_v2(self.0, sql.as_ptr(), -1, &mut raw, std::ptr::null_mut()) };
        let statement = (!raw.is_null()).then_some(Statement(raw));
        if code != 0 {
            return None;
        }
        let statement = statement?;
        for (index, value) in params.iter().enumerate() {
            let index = index as c_int + 1;
            let code = match value {
                Param::Int(n) => unsafe { sqlite3_bind_int64(statement.0, index, *n) },
                // SQLITE_STATIC (null): the text outlives the statement, which is finalised below.
                Param::Text(t) => unsafe { sqlite3_bind_text(statement.0, index, t.as_ptr().cast(), c_int::try_from(t.len()).ok()?, std::ptr::null()) },
            };
            if code != 0 {
                return None;
            }
        }
        let columns: Vec<String> = (0..unsafe { sqlite3_column_count(statement.0) })
            .map(|c| {
                let name = unsafe { sqlite3_column_name(statement.0, c) };
                if name.is_null() { String::new() } else { unsafe { CStr::from_ptr(name) }.to_string_lossy().into_owned() }
            })
            .collect();
        let mut rows = Vec::new();
        loop {
            match unsafe { sqlite3_step(statement.0) } {
                DONE => break,
                ROW => {}
                _ => return None,
            }
            let mut row = Map::new();
            for (c, name) in columns.iter().enumerate() {
                let c = c as c_int;
                let value = match unsafe { sqlite3_column_type(statement.0, c) } {
                    1 => Value::from(unsafe { sqlite3_column_int64(statement.0, c) }),
                    2 => serde_json::Number::from_f64(unsafe { sqlite3_column_double(statement.0, c) }).map_or(Value::Null, Value::Number),
                    3 | 4 => {
                        let pointer = unsafe { sqlite3_column_text(statement.0, c) };
                        let length = unsafe { sqlite3_column_bytes(statement.0, c) } as usize;
                        let bytes = if pointer.is_null() || length == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(pointer, length) } };
                        // Python decodes TEXT as UTF-8 and fails the whole query on a bad cell.
                        let text = std::str::from_utf8(bytes).ok()?.to_owned();
                        if json.contains(&name.as_str()) {
                            serde_json::from_str(&text).unwrap_or(Value::Null)
                        } else {
                            Value::String(text)
                        }
                    }
                    _ => Value::Null,
                };
                row.insert(name.clone(), value);
            }
            rows.push(Value::Object(row));
        }
        Some(rows)
    }
}

/// A bound query parameter.
#[derive(Clone, Copy)]
pub enum Param<'a> {
    Int(i64),
    Text(&'a str),
}

/// The rows of `sql`, each an object by column name (the declaration's JSON
/// columns parsed, `null` when malformed). `None`: the database could not be
/// read this cycle.
///
/// A store without the declared columns is an `Error::Unsupported`: the
/// caller's own reader takes the call (main's readers report such a store).
pub fn rows(path: &Path, db: &Sqlite, sql: &str, params: &[Param]) -> Result<Option<Vec<Value>>, Error> {
    let Some(con) = Connection::open(path) else { return Ok(None) };
    for (table, columns) in db.requires {
        let Some(have) = con.query(&format!("PRAGMA table_info({table})"), &[], &[]) else { return Ok(None) };
        let has = |column: &str| have.iter().any(|row| row.get("name").and_then(Value::as_str) == Some(column));
        if let Some(missing) = columns.iter().find(|c| !has(c)) {
            return Err(Error::Unsupported(format!("table {table} lacks {missing}")));
        }
    }
    Ok(con.query(sql, params, db.json))
}

/// The first column of the first row, as main's `_int` reads it.
pub fn scalar(path: &Path, db: &Sqlite, sql: &str) -> Result<Option<i64>, Error> {
    let Some(rows) = rows(path, db, sql, &[])? else { return Ok(None) };
    let first = rows.first().and_then(Value::as_object).and_then(|row| row.values().next().cloned());
    Ok(Some(crate::expr::py_int(first.as_ref())))
}

/// Does a last row match the query's `hold_last` (not consumed yet)?
pub fn held(query: &Rows, row: &Value) -> bool {
    let slots = Slots::new();
    let scope = Scope { record: row, line: 0, offset: 0, slots: &slots, cancelled: false, entry_key: "" };
    query.hold_last.as_ref().is_some_and(|p| expr::pred(p, &scope))
}

/// Every partition with its newest id, in the query's order (main iterates
/// its `GROUP BY` result); rows without either are skipped.
pub fn tops(path: &Path, db: &Sqlite, part: &Partition) -> Result<Option<Vec<(String, i64)>>, Error> {
    let Some(rows) = rows(path, db, part.tops, &[])? else { return Ok(None) };
    Ok(Some(
        rows.iter()
            .filter_map(|row| {
                let name = row.get("part").filter(|v| !v.is_null())?;
                let top = row.get("top").and_then(expr::py_int_raw)?;
                Some((expr::py_str(name), top))
            })
            .collect(),
    ))
}

/// One page of a partition's rows after `after`, a held last row dropped and
/// every row labelled. `None`: not readable this cycle.
pub fn page(path: &Path, db: &Sqlite, query: &Rows, part: &Partition, name: &str, after: i64) -> Result<Option<Vec<Value>>, Error> {
    let label = label(path, db, part, name)?;
    let Some(mut rows) = rows(path, db, query.sql, &[Param::Text(name), Param::Int(after), Param::Int(query.page)])? else { return Ok(None) };
    if rows.last().is_some_and(|row| held(query, row)) {
        rows.pop();
    }
    for row in &mut rows {
        if let Value::Object(object) = row {
            object.insert(part.label_as.to_owned(), label.clone().into());
        }
    }
    Ok(Some(rows))
}

fn label(path: &Path, db: &Sqlite, part: &Partition, name: &str) -> Result<String, Error> {
    let slots = Slots::new();
    for (sql, text) in part.label {
        let Some(rows) = rows(path, db, sql, &[Param::Text(name)])? else { continue };
        let Some(first) = rows.first() else { continue };
        let scope = Scope { record: first, line: 0, offset: 0, slots: &slots, cancelled: false, entry_key: "" };
        let value = expr::text(text, &scope);
        if !value.is_empty() {
            return Ok(value);
        }
    }
    Err(Error::Unsupported(format!("partition {name} has no label")))
}
