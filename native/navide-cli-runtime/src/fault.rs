//! Harness build only (`--features fault-hooks`): a session whose pane id is `fault:<kind>` gets that deliberate
//! fault, so unrelated panes of the same sidecar can run beside it (E1 15.2 P3, P7, P9). The product binary does not
//! contain this module; `tests/product_binary.rs` and `tests/cli/g2Native.mjs` prove it.
use crate::control::{Request, Wire, MAX_OBSERVATION_PARTS, OBSERVATION_CHUNK};
use crate::frame::code;
use serde_json::{json, Value};
use std::io;
use std::thread;
use std::time::Duration;

/// The error a suppressed create answer returns, so the create thread knows to send nothing.
#[derive(Debug)]
pub struct Suppressed;
impl std::fmt::Display for Suppressed {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.write_str("fault: response suppressed")
    }
}
impl std::error::Error for Suppressed {}

pub fn suppressed() -> io::Error {
    io::Error::other(Suppressed)
}
pub fn is_suppressed(error: &io::Error) -> bool {
    error
        .get_ref()
        .is_some_and(|inner| inner.downcast_ref::<Suppressed>().is_some())
}

/// The failure of an exit watcher that "could not start". This literal exists only in the harness build, which is
/// how `g2Native.mjs proof` tells the two binaries apart.
pub fn watch_exit_error() -> io::Error {
    io::Error::other("fault: exit watcher could not start")
}

pub fn kind(pane: &str) -> Option<&str> {
    pane.strip_prefix("fault:")
}

/// `fault:delay-response:<path>`: the CREATE answer waits until `<path>` exists (the session is already registered
/// and `SESSION_OWNED` already sent, so every later event can overtake it).
pub fn delay_response(pane: &str) {
    if let Some(path) = kind(pane).and_then(|k| k.strip_prefix("delay-response:")) {
        wait_for_file(path);
    }
}
/// Hold until a release file exists. A deliberate poll: the file is the test's release signal and has no event to
/// wait on; this module is outside the product binary.
pub fn wait_for_file(path: &str) {
    while !std::path::Path::new(path).exists() {
        thread::sleep(Duration::from_millis(5));
    }
}
/// The hold is bounded: a release that never comes fails the create loudly instead of blocking.
#[cfg(unix)]
pub fn wait_for_file_bounded(path: &str) -> io::Result<()> {
    let deadline = std::time::Instant::now() + Duration::from_secs(60);
    while !std::path::Path::new(path).exists() {
        if std::time::Instant::now() >= deadline {
            return Err(io::Error::other(
                "OWNERSHIP_JOURNAL_FAILED: fault journal hold was not released within 60 s",
            ));
        }
        thread::sleep(Duration::from_millis(5));
    }
    Ok(())
}
/// `fault:journal-hold:<path>`: the owner waits, before the ownership journal append, until `<path>` exists. A
/// regular-file journal keeps its `fsync`; Linux rejects `fsync` on the FIFO that holds the append on macOS.
#[cfg(unix)]
pub fn journal_hold(pane: &str) -> Option<String> {
    kind(pane)?.strip_prefix("journal-hold:").map(str::to_owned)
}
/// The journal hold creates `<path>.entered` once the owner has taken the root's identity and is about to wait, so a
/// test can tell "the gate is holding" from "the gate has not got there yet".
#[cfg(unix)]
pub fn mark_hold_entered(path: &str) {
    let _ = std::fs::write(format!("{path}.entered"), b"");
}
/// `fault:before-report-read:<path>`: the owner waits, before it reads the root's pid report, until `<path>` exists
/// (the fork-to-reservation window of the Linux reaper).
#[cfg(unix)]
pub fn before_report_read(pane: &str) -> Option<String> {
    kind(pane)?
        .strip_prefix("before-report-read:")
        .map(str::to_owned)
}
/// `fault:watch-exit-fail` / `fault:watch-exit-fail-noreap`: the exit watcher cannot start after the gate was
/// released. `-noreap` also makes the sidecar's kill/reap verification fail.
pub fn watch_exit_fails(pane: &str) -> bool {
    matches!(
        kind(pane),
        Some("watch-exit-fail" | "watch-exit-fail-noreap")
    )
}
#[cfg(unix)]
pub fn reap_is_unverifiable(pane: &str) -> bool {
    kind(pane) == Some("watch-exit-fail-noreap")
}
/// `fault:cleaned-silent`: the session is cleaned (root reaped) but `SESSION_CLEANED` is never sent (P7e).
pub fn cleaned_silent(pane: &str) -> bool {
    kind(pane) == Some("cleaned-silent")
}
pub fn thread_start_fails(pane: &str) -> bool {
    kind(pane) == Some("thread-start-fail")
}
pub fn fail_second_resize(pane: &str) -> bool {
    kind(pane) == Some("resize-partial")
}
/// `fault:resize-hold:<path>`: the RESIZE answer waits until `<path>` exists.
pub fn resize_hold(pane: &str) -> Option<String> {
    kind(pane)?.strip_prefix("resize-hold:").map(str::to_owned)
}
/// A cleanup that reports a survivor (S6: `KILL` accepted, then an incomplete `SESSION_CLEANED`).
pub fn survivor(pane: &str) -> bool {
    kind(pane) == Some("survivor")
}
pub fn suppress_response(pane: &str) -> bool {
    matches!(
        kind(pane),
        Some("suppress-response" | "suppress-owned-and-response")
    )
}

/// An observation whose path ends in `.fault-endless` gets a synthetic document and then a part stream that never
/// ends.
pub fn observation(req: &Request) -> Option<io::Result<Value>> {
    let path = req.meta["path"].as_str()?;
    // A held observation: it occupies its worker until `<path>.release` exists, then fails. (A FIFO cannot hold one
    // any more: a source that is not a regular file is refused without being opened.)
    #[cfg(unix)]
    if path.ends_with(".fault-hold") {
        let released = wait_for_file_bounded(&format!("{path}.release")).map_err(|_| {
            io::Error::other("fault: held observation was not released within 60 s")
        });
        return Some(released.and(Err(io::Error::other("fault: held observation released"))));
    }
    if path.ends_with(".fault-endless") {
        return Some(Ok(json!({"parser":"fault","path":path,"mode":req.meta["mode"]})));
    }
    // An answer one byte over the transport's limit: the observation core can no longer produce one (its answer
    // ledger keeps every answer inside the limit), so the transport's refusal is reached by this synthetic
    // document alone.
    if path.ends_with(".fault-oversize") {
        return Some(Ok(
            json!({"parser":"fault","path":path,"mode":req.meta["mode"],"pad":"x".repeat(MAX_OBSERVATION_PARTS * OBSERVATION_CHUNK)}),
        ));
    }
    None
}

/// Endless part stream: the sidecar never stops sending parts for this request.
pub fn endless_parts(req: &Request, wire: &Wire) -> bool {
    if !req.meta["path"]
        .as_str()
        .is_some_and(|path| path.ends_with(".fault-endless"))
    {
        return false;
    }
    let mut part = 0u64;
    while !wire.out.is_lost() {
        wire.out.event_for(
            code::OBSERVATION_PART,
            req.id,
            json!({ "part": part }),
            vec![b'x'; 60_000],
        );
        part += 1;
    }
    true
}
