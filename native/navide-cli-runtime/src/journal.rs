//! The ownership journal (E1 5.1): one crash-durable line per root, written before the root may run, so the next
//! startup can find and clean a root by identity even if this process or its owner died right after the gate
//! opened.
use crate::control::Wire;
use serde_json::{json, Value};
use std::io::{self, Write};

/// The journal line for a root. `root` is the identity record (`pid`, `pgid`, `start_kind`, `start_value`).
pub fn record(wire: &Wire, root: &Value, argv0: &str, session_id: &str, generation: &str) -> Value {
    json!({"journal":1,"pid":root["pid"],"pgid":root["pgid"],"start_kind":root["start_kind"],
        "start_value":root["start_value"],"argv0":argv0,"session_id":session_id,
        "session_generation":generation,"runtime_generation":wire.generation,"owner_pid":wire.owner_pid})
}

/// Append one identity record and sync it. The whole line goes out through `write_all` on an append-mode file, so a
/// death of this process before the call leaves none of it. A death during it can leave a prefix (a short write is
/// finished by further `write` calls), which recovery reports as a torn journal (`pty_registry.reap_stale`) and never
/// trusts.
///
/// Why one sync per create stays (E1 decision D12): this record is the crash-durable evidence that names a root
/// before its target may run (ownership invariant I1 of ticket 05). Without the sync a crash could leave an
/// executed, unnamed process. It runs while the platform holds its spawn serialisation, so the sync cost is a
/// correctness price, not a latency target; the measured cost is in `evidence/e3/e3-handoff.md`.
pub fn append(path: &std::path::Path, record: &Value) -> io::Result<()> {
    let mut line = serde_json::to_vec(record).map_err(io::Error::other)?;
    line.push(b'\n');
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    let mut file = options.open(path)?;
    file.write_all(&line)?;
    sync(&file)
}

/// One plain `fsync(2)` on POSIX, the call `File::sync_all` makes on Linux. On Apple platforms `sync_all` issues
/// `F_FULLFSYNC` instead (~4 ms per create there): its drive-cache flush guards only against power loss, after
/// which no process the journal names can be alive.
#[cfg(unix)]
fn sync(file: &std::fs::File) -> io::Result<()> {
    use std::os::unix::io::AsRawFd;
    loop {
        // SAFETY: a valid descriptor owned by `file` for the duration of the call.
        if unsafe { libc::fsync(file.as_raw_fd()) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::EINTR) {
            return Err(error);
        }
    }
}
/// `FlushFileBuffers`.
#[cfg(windows)]
fn sync(file: &std::fs::File) -> io::Result<()> {
    file.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appended_records_are_whole_lines_that_survive_reopening() {
        let dir = std::env::temp_dir().join(format!("navide-journal-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("g.jsonl");
        let _ = std::fs::remove_file(&path);
        append(&path, &json!({"journal":1,"pid":10,"start_value":"a"})).unwrap();
        append(&path, &json!({"journal":1,"pid":11,"start_value":"b"})).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<Value> = text
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[1]["pid"], 11);
        assert!(text.ends_with('\n'));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn an_unwritable_journal_is_an_error_not_silence() {
        let missing = std::path::Path::new("/nonexistent-navide-dir/g.jsonl");
        assert!(append(missing, &json!({"journal":1})).is_err());
    }

    #[test]
    fn a_record_names_the_root_the_session_and_the_runtime() {
        let config = crate::control::Config::for_test();
        let tag = crate::frame::runtime_tag(&config.generation).unwrap();
        let wire = Wire::new(&config, tag);
        let root = json!({"pid":7,"pgid":7,"start_kind":"k","start_value":"v"});
        let line = record(&wire, &root, "/bin/x", "s", "g");
        assert_eq!(line["pid"], 7);
        assert_eq!(line["start_value"], "v");
        assert_eq!(line["session_id"], "s");
        assert_eq!(line["runtime_generation"], config.generation);
        assert_eq!(line["owner_pid"], config.owner_pid);
    }
}
