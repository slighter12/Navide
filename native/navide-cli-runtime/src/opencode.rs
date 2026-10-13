//! OpenCode's shared SQLite log. Application stores and attribution stay in Python.
use libc::{c_char, c_int, c_void};
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::ffi::CString;
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

#[link(name = "sqlite3")]
unsafe extern "C" {
    fn sqlite3_open_v2(
        name: *const c_char,
        db: *mut *mut c_void,
        flags: c_int,
        vfs: *const c_char,
    ) -> c_int;
    fn sqlite3_close(db: *mut c_void) -> c_int;
    fn sqlite3_busy_timeout(db: *mut c_void, milliseconds: c_int) -> c_int;
    fn sqlite3_prepare_v2(
        db: *mut c_void,
        sql: *const c_char,
        bytes: c_int,
        statement: *mut *mut c_void,
        tail: *mut *const c_char,
    ) -> c_int;
    fn sqlite3_bind_int64(statement: *mut c_void, index: c_int, value: i64) -> c_int;
    fn sqlite3_bind_text(
        statement: *mut c_void,
        index: c_int,
        value: *const c_char,
        bytes: c_int,
        destroy: Option<unsafe extern "C" fn(*mut c_void)>,
    ) -> c_int;
    fn sqlite3_step(statement: *mut c_void) -> c_int;
    fn sqlite3_column_count(statement: *mut c_void) -> c_int;
    fn sqlite3_column_type(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_column_int64(statement: *mut c_void, column: c_int) -> i64;
    fn sqlite3_column_double(statement: *mut c_void, column: c_int) -> f64;
    fn sqlite3_column_text(statement: *mut c_void, column: c_int) -> *const u8;
    fn sqlite3_column_bytes(statement: *mut c_void, column: c_int) -> c_int;
    fn sqlite3_finalize(statement: *mut c_void) -> c_int;
}

// Exactly one short-lived read-only connection per query, as in the Python baseline.
struct ReadConnection(*mut c_void);
impl ReadConnection {
    fn open(path: &Path) -> Result<Self, i32> {
        let name = CString::new(format!("file:{}?mode=ro", path.display())).map_err(|_| 1)?;
        let mut raw = std::ptr::null_mut();
        let code = unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, 1 | 0x40, std::ptr::null()) };
        if code != 0 {
            if !raw.is_null() {
                unsafe {
                    sqlite3_close(raw);
                }
            }
            return Err(code);
        }
        let db = Self(raw);
        let code = unsafe { sqlite3_busy_timeout(db.0, 250) };
        if code != 0 {
            return Err(code);
        }
        Ok(db)
    }
    fn query(&self, sql: &str, parameters: &[Value]) -> Result<Vec<Vec<Value>>, i32> {
        let sql = CString::new(sql).map_err(|_| 1)?;
        let mut raw = std::ptr::null_mut();
        let code =
            unsafe { sqlite3_prepare_v2(self.0, sql.as_ptr(), -1, &mut raw, std::ptr::null_mut()) };
        if code != 0 {
            if !raw.is_null() {
                unsafe {
                    sqlite3_finalize(raw);
                }
            }
            return Err(code);
        }
        // Declare text storage first so even an error finalizes before it drops.
        let mut strings = Vec::new();
        let statement = ReadStatement(raw);
        for (index, value) in parameters.iter().enumerate() {
            let code = if let Some(integer) = value.as_i64() {
                unsafe { sqlite3_bind_int64(statement.0, index as i32 + 1, integer) }
            } else {
                strings.push(CString::new(value.as_str().ok_or(1)?).map_err(|_| 1)?);
                unsafe {
                    sqlite3_bind_text(
                        statement.0,
                        index as i32 + 1,
                        strings.last().unwrap().as_ptr(),
                        -1,
                        None,
                    )
                }
            };
            if code != 0 {
                return Err(code);
            }
        }
        let mut rows = Vec::new();
        loop {
            let code = unsafe { sqlite3_step(statement.0) };
            if code == 101 {
                break;
            }
            if code != 100 {
                return Err(code);
            }
            let mut row = Vec::new();
            for column in 0..unsafe { sqlite3_column_count(statement.0) } {
                let value = match unsafe { sqlite3_column_type(statement.0, column) } {
                    1 => json!(unsafe { sqlite3_column_int64(statement.0, column) }),
                    2 => json!(unsafe { sqlite3_column_double(statement.0, column) }),
                    3 => {
                        let pointer = unsafe { sqlite3_column_text(statement.0, column) };
                        let length = unsafe { sqlite3_column_bytes(statement.0, column) };
                        if pointer.is_null() {
                            return Err(7);
                        }
                        let bytes = unsafe { std::slice::from_raw_parts(pointer, length as usize) };
                        json!(std::str::from_utf8(bytes).map_err(|_| 1)?)
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
        Ok(rows)
    }
}
impl Drop for ReadConnection {
    fn drop(&mut self) {
        unsafe {
            sqlite3_close(self.0);
        }
    }
}
struct ReadStatement(*mut c_void);
impl Drop for ReadStatement {
    fn drop(&mut self) {
        unsafe {
            sqlite3_finalize(self.0);
        }
    }
}
fn query(path: &Path, sql: &str, parameters: &[Value]) -> Option<Vec<Vec<Value>>> {
    ReadConnection::open(path)
        .and_then(|db| db.query(sql, parameters))
        .ok()
}
fn number(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_bool().map(i64::from))
        .or_else(|| value.as_f64().filter(|v| v.is_finite()).map(|v| v as i64))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
        .max(0)
}
fn text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        _ => value.to_string(),
    }
}
fn object(value: &Value) -> Value {
    value
        .as_str()
        .and_then(|s| serde_json::from_str::<Value>(s).ok())
        .filter(Value::is_object)
        .unwrap_or(json!({}))
}
fn anchor(row: &[Value]) -> String {
    format!("{}|{}", text(&row[0]), text(&row[1]))
}
fn cursor(row: i64, pending: &BTreeSet<i64>, identity: &str, mark: &str) -> Value {
    let pending: Vec<_> = pending.iter().copied().collect();
    json!({"kind":"sqlite","row_id":row,"pending":&pending[pending.len().saturating_sub(64)..],"identity":identity,"anchor":mark})
}
fn usage_event(
    path: &Path,
    row: &[Value],
    versions: &HashMap<String, String>,
) -> (Option<Value>, bool) {
    let data = object(&row[3]);
    if data["role"] != "assistant" {
        return (None, true);
    }
    let completed = number(&data["time"]["completed"]);
    if completed == 0 {
        return (None, false);
    }
    let tokens = &data["tokens"];
    let input = number(&tokens["input"])
        + number(&tokens["cache"]["read"])
        + number(&tokens["cache"]["write"]);
    let output = number(&tokens["output"]) + number(&tokens["reasoning"]);
    if input == 0 && output == 0 {
        return (None, true);
    }
    let session = text(&row[2]);
    (
        Some(
            json!({"vendor":"opencode","input_tokens":input,"output_tokens":output,
        "cwd":text(&row[4]),"session_id":session,"file_path":path,"dedup_key":format!("msg:{}",text(&row[1])),
        "timestamp":completed.to_string(),"model":text(&data["modelID"]),
        "cli_version":versions.get(&session).cloned().unwrap_or_default()}),
        ),
        true,
    )
}

fn activity_timestamp(milliseconds: i64) -> String {
    let seconds = milliseconds / 1000;
    let mut time: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::gmtime_r(&seconds, &mut time) }.is_null()
        || !(1..=9999).contains(&(time.tm_year + 1900))
    {
        return String::new();
    }
    let fraction = if milliseconds % 1000 == 0 {
        String::new()
    } else {
        format!(".{:06}", (milliseconds % 1000) * 1000)
    };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}{}+00:00",
        time.tm_year + 1900,
        time.tm_mon + 1,
        time.tm_mday,
        time.tm_hour,
        time.tm_min,
        time.tm_sec,
        fraction
    )
}
fn activity(path: &Path, seen: &mut BTreeSet<String>) -> Vec<Value> {
    let previous = seen.iter().find_map(|key| {
        key.strip_prefix("opencode_part::")
            .and_then(|v| v.parse::<i64>().ok())
    });
    let remember = |seen: &mut BTreeSet<String>, id: i64| {
        seen.retain(|key| !key.starts_with("opencode_part::"));
        seen.insert(format!("opencode_part::{id}"));
    };
    let Some(previous) = previous else {
        if let Some(last) = query(path, "SELECT MAX(rowid) FROM part", &[])
            .and_then(|v| v.first().and_then(|r| r[0].as_i64()))
        {
            remember(seen, last);
        }
        return Vec::new();
    };
    let sql="SELECT p.rowid,p.session_id,p.message_id,p.data,m.data,COALESCE(s.directory,''),s.parent_id,p.time_created FROM part p JOIN message m ON m.id=p.message_id JOIN session s ON s.id=p.session_id WHERE p.rowid>? ORDER BY p.rowid LIMIT 256";
    let rows = query(path, sql, &[json!(previous)]).unwrap_or_default();
    if let Some(row) = rows.last() {
        remember(seen, number(&row[0]));
    }
    rows.into_iter().map(|row| {
        let part=object(&row[3]);let message=object(&row[4]);
        let kind=text(&part["type"]);
        let mut event_type="agent_active";let mut body=String::new();
        let detail=match kind.as_str() {
            "step-finish" => {
                let reason=text(&part["reason"]);
                if reason!="stop" {format!("step-finish:{}",if reason.is_empty(){"unknown"}else{&reason})}
                else if !row[6].is_null() && !text(&row[6]).is_empty() {"subagent:done".to_owned()}
                else {
                    event_type="turn_complete";
                    body=query(path,"SELECT data FROM part WHERE message_id=? ORDER BY rowid",&[row[2].clone()]).unwrap_or_default().into_iter()
                        .map(|r|object(&r[0])).filter(|p|p["type"]=="text")
                        .map(|p|text(&p["text"]).trim().to_owned()).filter(|s|!s.is_empty()).collect::<Vec<_>>().join("\n");
                    "assistant".to_owned()
                }
            }
            "tool" => {let tool=text(&part["tool"]);format!("tool:{}",if tool.is_empty(){"unknown"}else{&tool})}
            "reasoning" => "assistant:thinking".to_owned(),
            "text" if message["role"]=="user" => {body=text(&part["text"]);"user".to_owned()}
            "text" => "assistant".to_owned(),
            _ => if kind.is_empty(){"part".to_owned()}else{kind},
        };
        json!({"vendor":"opencode","event_type":event_type,"cwd":text(&row[5]),"session_id":text(&row[1]),
            "file_path":path,"dedup_key":format!("part:{}",number(&row[0])),"timestamp":activity_timestamp(number(&row[7])),"detail":detail,"text":body})
    }).collect()
}

pub fn observe(args: &Value) -> io::Result<Value> {
    let path = Path::new(
        args["path"]
            .as_str()
            .ok_or_else(|| io::Error::other("path required"))?,
    );
    let mode = args["mode"].as_str().unwrap_or("usage");
    let checkpoint = args.get("checkpoint").cloned().unwrap_or(json!({}));
    let mut seen: BTreeSet<String> = args["seen"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect();
    let mut result = json!({"parser":"rust-opencode-sqlite-v1","path":path,"mode":mode,
        "identity":null,"events":[],"checkpoint":checkpoint,"seen":seen,"markers":{},"found":false});
    if path.file_name().and_then(|s| s.to_str()) != Some("opencode.db") {
        return Ok(result);
    }
    let Ok(stat) = path.metadata() else {
        return Ok(result);
    };
    let identity = format!("{}:{}", stat.dev(), stat.ino());
    result["identity"] = json!({"dev":stat.dev(),"ino":stat.ino(),"size":stat.len()});
    if mode == "activity" {
        result["events"] = json!(activity(path, &mut seen));
        result["seen"] = json!(seen);
        return Ok(result);
    }
    if mode == "has_session" {
        result["found"] = json!(query(
            path,
            "SELECT 1 FROM session WHERE id=?",
            &[args["session_id"].clone()]
        )
        .is_some_and(|r| !r.is_empty()));
        return Ok(result);
    }
    if mode == "identify" {
        let sql="SELECT p.session_id,p.data,m.data,COALESCE(s.directory,'') FROM part p JOIN message m ON m.id=p.message_id JOIN session s ON s.id=p.session_id WHERE s.parent_id IS NULL AND p.data LIKE '%at-pane:%' ORDER BY p.time_created";
        let wanted: Vec<_> = args["markers"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter(|s| !s.is_empty())
            .collect();
        let mut found = serde_json::Map::new();
        if !wanted.is_empty() {
            for row in query(path, sql, &[]).unwrap_or_default() {
                if object(&row[2])["role"] != "user" {
                    continue;
                }
                let body = text(&row[1]);
                for marker in &wanted {
                    if !found.contains_key(*marker) && body.contains(*marker) {
                        found.insert((*marker).to_owned(), json!([text(&row[0]), text(&row[3])]));
                    }
                }
            }
        }
        result["markers"] = Value::Object(found);
        return Ok(result);
    }
    if mode != "usage" && mode != "legacy" {
        return Err(io::Error::other("OpenCode observation mode not integrated"));
    }
    let replaced = checkpoint["identity"]
        .as_str()
        .is_some_and(|id| !id.is_empty() && id != identity);
    let mut watermark = if replaced {
        0
    } else {
        number(&checkpoint["row_id"])
    };
    let mut mark = if replaced {
        String::new()
    } else {
        text(&checkpoint["anchor"])
    };
    let mut pending: BTreeSet<i64> = if replaced {
        BTreeSet::new()
    } else {
        checkpoint["pending"]
            .as_array()
            .into_iter()
            .flatten()
            .map(number)
            .filter(|v| *v > 0)
            .collect()
    };
    if watermark > 0 && !mark.is_empty() {
        if let Some(rows) = query(
            path,
            "SELECT id, session_id FROM message WHERE rowid = ?",
            &[json!(watermark)],
        ) {
            if rows.first().is_some_and(|row| anchor(row) != mark) {
                watermark -= 1;
                mark.clear();
                pending.clear();
            }
        }
    }
    let predicate = if mode == "legacy" {
        String::new()
    } else {
        let mut clause = format!("WHERE m.rowid > {watermark}");
        if !pending.is_empty() {
            clause.push_str(&format!(
                " OR m.rowid IN ({})",
                pending
                    .iter()
                    .map(i64::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            ));
        }
        clause
    };
    let sql = format!("SELECT m.rowid,m.id,m.session_id,m.data,COALESCE(s.directory,'') FROM message m JOIN session s ON s.id=m.session_id {predicate} ORDER BY m.rowid");
    let Some(rows) = query(path, &sql, &[]) else {
        return Ok(result);
    };
    if rows.is_empty() && watermark > 0 && mode == "usage" {
        if let Some(max) = query(path, "SELECT COALESCE(MAX(rowid),0) FROM message", &[])
            .and_then(|v| v.first().map(|r| number(&r[0])))
        {
            if max < watermark {
                watermark = max;
                pending.clear();
                mark = query(
                    path,
                    "SELECT id,session_id FROM message WHERE rowid=?",
                    &[json!(watermark)],
                )
                .and_then(|v| v.first().map(|r| anchor(r)))
                .unwrap_or_default();
            }
        }
    }
    let versions: HashMap<_, _> = if rows.is_empty() {
        HashMap::new()
    } else {
        query(path, "SELECT id,version FROM session", &[])
            .unwrap_or_default()
            .into_iter()
            .filter(|r| !text(&r[0]).is_empty())
            .map(|r| (text(&r[0]), text(&r[1])))
            .collect()
    };
    let mut events = Vec::new();
    for row in rows {
        let id = number(&row[0]);
        let key = format!("msg:{}", text(&row[1]));
        if mode == "legacy" && seen.contains(&key) {
            continue;
        }
        if id > watermark {
            watermark = id;
            mark = anchor(&row[1..3]);
        }
        let (event, done) = usage_event(path, &row, &versions);
        if !done {
            pending.insert(id);
            continue;
        }
        pending.remove(&id);
        if mode == "legacy" {
            seen.insert(key);
        }
        if let Some(mut event) = event {
            if mode == "usage" {
                event["checkpoint"] = cursor(watermark, &pending, &identity, &mark);
            }
            events.push(event);
        }
    }
    result["events"] = json!(events);
    result["seen"] = json!(seen);
    if mode == "usage" {
        result["checkpoint"] = cursor(watermark, &pending, &identity, &mark);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CStr;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        path: PathBuf,
        writer: Option<ReadConnection>,
    }
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "opencode-sqlite-own-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let path = root.join("opencode.db");
            let name = CString::new(path.to_str().unwrap()).unwrap();
            let mut raw = std::ptr::null_mut();
            assert_eq!(
                unsafe { sqlite3_open_v2(name.as_ptr(), &mut raw, 6, std::ptr::null()) },
                0
            );
            let writer = ReadConnection(raw);
            for sql in ["CREATE TABLE session(id TEXT PRIMARY KEY,directory TEXT,parent_id TEXT,version TEXT)",
                "CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT)",
                "CREATE TABLE part(id TEXT PRIMARY KEY,message_id TEXT,session_id TEXT,data TEXT,time_created INTEGER)"] {
                writer.query(sql,&[]).unwrap();
            }
            writer
                .query(
                    "INSERT INTO session VALUES('ses_own','/own/work',NULL,'fixture-v1')",
                    &[],
                )
                .unwrap();
            Self {
                root,
                path,
                writer: Some(writer),
            }
        }
        fn sql(&self, sql: &str, values: &[Value]) {
            self.writer.as_ref().unwrap().query(sql, values).unwrap();
        }
        fn message(&self, id: &str, data: Value) {
            self.sql(
                "INSERT INTO message VALUES(?,'ses_own',?)",
                &[json!(id), json!(data.to_string())],
            );
        }
        fn part(&self, id: &str, message: &str, data: Value) {
            self.sql(
                "INSERT INTO part VALUES(?,?,'ses_own',?,1700000000123)",
                &[json!(id), json!(message), json!(data.to_string())],
            );
        }
        fn observe(&self, mode: &str, checkpoint: Value, seen: Value) -> Value {
            observe(&json!({"path":self.path,"mode":mode,"checkpoint":checkpoint,"seen":seen}))
                .unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            drop(self.writer.take());
            std::fs::remove_dir_all(&self.root).unwrap();
        }
    }

    #[test]
    fn marker_uses_creation_time_not_rowid_and_excludes_children() {
        let own = Fixture::new();
        own.message("late", json!({"role":"user"}));
        own.message("early", json!({"role":"user"}));
        own.sql(
            "INSERT INTO session VALUES('ses_earliest','/own/earliest',NULL,'v1')",
            &[],
        );
        own.sql(
            "UPDATE message SET session_id='ses_earliest' WHERE id='early'",
            &[],
        );
        own.sql(
            "INSERT INTO part VALUES('late','late','ses_own',?,200)",
            &[json!(
                json!({"type":"text","text":"[at-pane:OWN]"}).to_string()
            )],
        );
        own.sql(
            "INSERT INTO part VALUES('early','early','ses_earliest',?,100)",
            &[json!(
                json!({"type":"text","text":"[at-pane:OWN]"}).to_string()
            )],
        );
        let result =
            observe(&json!({"path":own.path,"mode":"identify","markers":["at-pane:OWN"]})).unwrap();
        assert_eq!(
            result["markers"]["at-pane:OWN"],
            json!(["ses_earliest", "/own/earliest"])
        );
    }

    #[test]
    fn genuine_self_system_sqlite_identity() {
        unsafe extern "C" {
            fn sqlite3_libversion() -> *const c_char;
            fn _dyld_image_count() -> u32;
            fn _dyld_get_image_name(index: u32) -> *const c_char;
            fn _dyld_get_image_header(index: u32) -> *const u32;
        }
        let version = unsafe { CStr::from_ptr(sqlite3_libversion()) }
            .to_str()
            .unwrap();
        assert_eq!(version, "3.54.0");
        let mut matched = None;
        for index in 0..unsafe { _dyld_image_count() } {
            let name = unsafe { CStr::from_ptr(_dyld_get_image_name(index)) }
                .to_str()
                .unwrap();
            if name != "/usr/lib/libsqlite3.dylib" {
                continue;
            }
            let header = unsafe { _dyld_get_image_header(index) };
            assert_eq!(unsafe { *header }, 0xfeedfacf);
            let count = unsafe { *header.add(4) };
            let total = unsafe { *header.add(5) } as usize;
            let mut offset = 32usize;
            for _ in 0..count {
                assert!(offset + 8 <= 32 + total);
                let command = unsafe { header.cast::<u8>().add(offset).cast::<u32>() };
                let kind = unsafe { *command };
                let length = unsafe { *command.add(1) } as usize;
                assert!(length >= 8 && offset + length <= 32 + total);
                if kind == 0x1b {
                    assert!(length >= 24);
                    let uuid =
                        unsafe { std::slice::from_raw_parts(command.cast::<u8>().add(8), 16) };
                    matched = Some(uuid.iter().map(|b| format!("{b:02x}")).collect::<String>());
                    break;
                }
                offset += length;
            }
        }
        let uuid = matched.unwrap();
        assert_eq!(uuid, "73d616f27b6c30468de6656f74793d5f");
        let own = super::super::darwin::self_process().unwrap();
        assert_eq!(own["pid"], std::process::id());
        assert_eq!(own["parent"], unsafe { libc::getppid() });
        println!(
            "{}",
            json!({"kind":"sqlite-system-SELF","pid":own["pid"],"parent":own["parent"],
            "birth_seconds":own["birth_seconds"],"birth_microseconds":own["birth_microseconds"],"executable":own["executable"],
            "self_api":"darwin::self_process",
            "library":"/usr/lib/libsqlite3.dylib","version":version,"uuid":uuid,"runtime_library_bytes_frozen":false,"dyld_cache_byte_limit":true})
        );
    }

    #[test]
    fn readonly_each_query_250ms_and_real_busy_code() {
        let own = Fixture::new();
        for _ in 0..2 {
            let db = ReadConnection::open(&own.path).unwrap();
            assert_eq!(
                db.query("PRAGMA busy_timeout", &[]).unwrap(),
                vec![vec![json!(250)]]
            );
            assert_eq!(
                db.query("INSERT INTO session VALUES('bad','',NULL,'')", &[])
                    .unwrap_err(),
                8
            );
        }
        own.sql("BEGIN EXCLUSIVE", &[]);
        let start = std::time::Instant::now();
        assert_eq!(
            ReadConnection::open(&own.path)
                .unwrap()
                .query("SELECT id FROM session", &[])
                .unwrap_err(),
            5
        );
        assert!(start.elapsed() >= std::time::Duration::from_millis(200));
        assert_eq!(
            own.observe("usage", json!({"row_id":7}), json!([]))["checkpoint"],
            json!({"row_id":7})
        );
        own.sql("ROLLBACK", &[]);
    }

    #[test]
    fn real_locked_shared_connection_code_and_no_data() {
        let own = Fixture::new();
        let name = CString::new(own.path.to_str().unwrap()).unwrap();
        let open = |flags| {
            let mut raw = std::ptr::null_mut();
            assert_eq!(
                unsafe {
                    sqlite3_open_v2(name.as_ptr(), &mut raw, flags | 0x20000, std::ptr::null())
                },
                0
            );
            ReadConnection(raw)
        };
        let writer = open(2);
        let reader = open(1);
        writer.query("BEGIN IMMEDIATE", &[]).unwrap();
        writer
            .query("UPDATE session SET version='uncommitted'", &[])
            .unwrap();
        assert_eq!(
            reader
                .query("SELECT version FROM session", &[])
                .unwrap_err(),
            6
        );
        writer.query("ROLLBACK", &[]).unwrap();
        assert_eq!(
            reader.query("SELECT version FROM session", &[]).unwrap()[0][0],
            "fixture-v1"
        );
    }

    #[test]
    fn wal_uncommitted_pending_anchor_and_restart_cursor() {
        let own = Fixture::new();
        own.sql("PRAGMA journal_mode=WAL", &[]);
        own.sql("PRAGMA wal_autocheckpoint=0", &[]);
        let completed = json!({"role":"assistant","time":{"completed":1700000000000_i64},"tokens":{"input":"10","cache":{"read":2,"write":3},"output":5,"reasoning":1}});
        own.message("pending", json!({"role":"assistant","tokens":{"input":99}}));
        own.message("done", completed.clone());
        let first = own.observe("usage", json!({}), json!([]));
        assert_eq!(first["events"].as_array().unwrap().len(), 1);
        assert_eq!(first["events"][0]["input_tokens"], 15);
        assert_eq!(first["events"][0]["output_tokens"], 6);
        assert_eq!(first["checkpoint"]["pending"], json!([1]));
        own.sql("BEGIN IMMEDIATE", &[]);
        own.sql(
            "UPDATE message SET data=? WHERE id='pending'",
            &[json!(completed.to_string())],
        );
        assert_eq!(
            own.observe("usage", first["checkpoint"].clone(), json!([]))["events"],
            json!([])
        );
        own.sql("COMMIT", &[]);
        let second = own.observe("usage", first["checkpoint"].clone(), json!([]));
        assert_eq!(second["events"][0]["dedup_key"], "msg:pending");
        assert_eq!(second["checkpoint"]["pending"], json!([]));
        assert_eq!(
            own.observe("usage", second["checkpoint"].clone(), json!([]))["events"],
            json!([])
        );
        own.sql("DELETE FROM message WHERE id='done'", &[]);
        own.message("reused", completed);
        let reused = own.observe("usage", second["checkpoint"].clone(), json!([]));
        assert_eq!(reused["events"][0]["dedup_key"], "msg:reused");
        assert!(own.path.with_extension("db-wal").is_file());
    }

    #[test]
    fn activity_history_then_live_top_level_settlement() {
        let own = Fixture::new();
        own.message("user", json!({"role":"user"}));
        own.part(
            "history",
            "user",
            json!({"type":"text","text":"OWN history"}),
        );
        let first = own.observe("activity", json!({}), json!([]));
        assert_eq!(first["events"], json!([]));
        assert_eq!(first["seen"], json!(["opencode_part::1"]));
        own.message("assistant", json!({"role":"assistant"}));
        own.part(
            "text",
            "assistant",
            json!({"type":"text","text":"OWN reply"}),
        );
        own.part(
            "toolpause",
            "assistant",
            json!({"type":"step-finish","reason":"tool-calls"}),
        );
        own.part(
            "stop",
            "assistant",
            json!({"type":"step-finish","reason":"stop"}),
        );
        let live = own.observe("activity", json!({}), first["seen"].clone());
        assert_eq!(live["events"][1]["event_type"], "agent_active");
        assert_eq!(live["events"][2]["event_type"], "turn_complete");
        assert_eq!(live["events"][2]["text"], "OWN reply");
        assert_eq!(
            live["events"][2]["timestamp"],
            "2023-11-14T22:13:20.123000+00:00"
        );
        assert_eq!(
            own.observe("activity", json!({}), live["seen"].clone())["events"],
            json!([])
        );
    }
}
