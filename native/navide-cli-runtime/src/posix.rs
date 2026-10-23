//! The sidecar: spawn and ownership of PTY-backed roots, the control channel and the lifecycle.
//!
//! After the handoff (E1 5.2, 6.1) Rust never reads or writes a PTY: Python owns every byte of
//! the stream. What stays here is the create (gated, journalled), the handle transfer, the window
//! size, signals and reaping by recorded identity, and the three independent lifecycle facts
//! (root exit, stream end, verified cleanup).
use crate::frame::{self, code, Direction, Frame};
use crate::session_pty::{SessionPty, SlaveEnd, FD_FORK_LOCK};
use crate::wire::{Outbound, SocketSender};
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::io::{self, Write};
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(target_os = "macos")]
#[path = "darwin.rs"]
mod darwin;
#[cfg(target_os = "linux")]
#[path = "linux.rs"]
mod linux;

#[path = "adapters.rs"]
mod adapters;
#[path = "claude.rs"]
mod claude;
#[path = "claude_facts.rs"]
mod claude_facts;
#[path = "codex.rs"]
mod codex;
#[cfg(all(test, target_os = "macos"))]
#[path = "initial_retention_test.rs"]
mod initial_retention_test;
#[cfg(target_os = "macos")]
#[path = "opencode.rs"]
mod opencode;

// ---- named bounds (E1 13.1: every bound has a named source) -------------------------------
/// Bound for observing the root's exit status after a SIGKILL (cleanup's root reap).
const ROOT_REAP: Duration = Duration::from_secs(1);
/// `limits.kill_escalation_ms`: the baseline's `_KILL_ESCALATION_GRACE_S`.
const KILL_ESCALATION_MS: u64 = 1000;
/// `limits.eof_reap_ms`: how long an EOF waits for the root's exit before the root-live fence.
const EOF_REAP_MS: u64 = 1000;
/// The baseline's `_REAP_CONFIRM_S` and `_REAP_WAIT_SLACK_S` (`terminals._reap_wait_timeout_s`).
const REAP_CONFIRM_MS: u64 = 1000;
const REAP_SLACK_MS: u64 = 3000;
const REQUEST_MS: u64 = 15_000;
const CREATE_ANSWER_MS: u64 = 20_000;
const PING_MS: u64 = 5000;
/// `limits.ownership_snapshot_ms`: a sampling cadence, not a wait on state (E1 13.3 site 12).
const SNAPSHOT_YOUNG_MS: u64 = 5000;
const SNAPSHOT_STEADY_MS: u64 = 30_000;
const SNAPSHOT_YOUNG_WINDOW_MS: u64 = 30_000;
/// Worst-case encoded sizes used by the pre-spawn `SESSION_OWNED` bound (5.1.3).
const TTY_NAME_MAX: usize = 32;

/// `limits.cleanup_ms`, derived exactly as the baseline's `_reap_wait_timeout_s()`: the largest
/// declared graceful grace + kill escalation + reap confirmation + slack.
fn cleanup_ms() -> u64 {
    adapters::max_grace_ms() + KILL_ESCALATION_MS + REAP_CONFIRM_MS + REAP_SLACK_MS
}

type Table = HashMap<i32, (i32, i32, String)>;
type Sessions = Arc<Mutex<Registry>>;
/// Creates being coordinated: shutdown waits for them (a condition, not a poll).
static CREATES_CHANGED: Condvar = Condvar::new();

/// One decoded request frame. It carries its inbound byte reservation, released when the request is
/// dropped (answered, refused or abandoned), so queued and in-flight requests never exceed the budget.
struct Request {
    id: u64,
    code: u16,
    meta: Value,
    blob: Vec<u8>,
    #[allow(dead_code)] // held for its Drop
    reservation: Option<Reservation>,
}

/// `limits.max_inbound_bytes` (E1 1.4): the wire bytes of every request that has been read and not yet
/// finished. The reader blocks while the next request would exceed it (backpressure, nothing dropped).
const MAX_INBOUND_BYTES: usize = 16 * 1024 * 1024;

struct InboundBudget {
    state: Mutex<(usize, usize, bool)>, // used, peak, closed
    changed: Condvar,
    cap: usize,
}
struct Reservation {
    budget: Arc<InboundBudget>,
    size: usize,
}
impl Drop for Reservation {
    fn drop(&mut self) {
        let mut state = self.budget.state.lock().unwrap();
        state.0 -= self.size;
        self.budget.changed.notify_all();
    }
}
impl InboundBudget {
    fn new(cap: usize) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new((0, 0, false)),
            changed: Condvar::new(),
            cap,
        })
    }
    /// Block until `size` more bytes fit (a request larger than the whole budget is admitted alone).
    /// `None` when the budget was closed (the channel is gone).
    fn acquire(self: &Arc<Self>, size: usize) -> Option<Reservation> {
        let mut state = self.state.lock().unwrap();
        while !state.2 && state.0 != 0 && state.0 + size > self.cap {
            state = self.changed.wait(state).unwrap();
        }
        if state.2 {
            return None;
        }
        state.0 += size;
        state.1 = state.1.max(state.0);
        Some(Reservation {
            budget: self.clone(),
            size,
        })
    }
    fn close(&self) {
        self.state.lock().unwrap().2 = true;
        self.changed.notify_all();
    }
    fn snapshot(&self) -> (usize, usize) {
        let state = self.state.lock().unwrap();
        (state.0, state.1)
    }
}

struct Wire {
    generation: String,
    journal: std::path::PathBuf,
    owner_pid: u32,
    out: Outbound,
    /// The observation worker's bounded queue (E1 14): history reads are file I/O and never run on
    /// the control reader, whose job is admission.
    observe_tx: Mutex<Option<mpsc::SyncSender<Request>>>,
    inbound: Arc<InboundBudget>,
}

/// `limits.max_observation_bytes` / `max_observation_parts` and the part size (a part's meta
/// and blob together stay under `max_frame_r2p`).
const MAX_OBSERVATION_BYTES: usize = 8_388_608;
const MAX_OBSERVATION_PARTS: usize = 128;
const OBSERVATION_QUEUE: usize = 256;
/// Settled sessions remembered by id and generation after they leave the registry (E1 v8 5.5): memory-bounded.
const TOMBSTONES: usize = 4096;
const OBSERVATION_CHUNK: usize = 64 * 1024 - 256;

// ---- the per-session handle -----------------------------------------------------------------

enum Msg {
    Kill(bool),
    TtyEof,
    Shutdown,
    Exited,
}

#[derive(Clone, Copy)]
struct ExitInfo {
    code: Option<i32>,
    signal: Option<i32>,
}
struct ExitCell {
    state: Mutex<Option<ExitInfo>>,
    changed: Condvar,
}
impl ExitCell {
    fn new() -> Self {
        Self {
            state: Mutex::new(None),
            changed: Condvar::new(),
        }
    }
    fn get(&self) -> Option<ExitInfo> {
        *self.state.lock().unwrap()
    }
    fn set(&self, info: ExitInfo) {
        *self.state.lock().unwrap() = Some(info);
        self.changed.notify_all();
    }
    /// Block until the root has exited or `limit` elapsed (an upper bound on an event wait).
    fn wait(&self, limit: Duration) -> Option<ExitInfo> {
        let deadline = Instant::now() + limit;
        let mut state = self.state.lock().unwrap();
        while state.is_none() {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            state = self.changed.wait_timeout(state, left).unwrap().0;
        }
        *state
    }
}

struct SessionHandle {
    id: String,
    generation: String,
    pane: String,
    workspace: String,
    agent_key: String,
    spec: Option<adapters::ShutdownSpec>,
    pty: Arc<SessionPty>,
    tx: mpsc::Sender<Msg>,
    exit: Arc<ExitCell>,
    done: (Mutex<Option<bool>>, Condvar),
    adopted: AtomicBool,
    started: Instant,
    /// Survivors named by this session's `SESSION_CLEANED` (what `SHUTDOWN` reports exactly).
    survivors: Mutex<Vec<Value>>,
}
impl SessionHandle {
    fn meta(&self) -> Value {
        json!({"session_id":self.id,"session_generation":self.generation,
            "pane_id":self.pane,"workspace_path":self.workspace})
    }
    fn finished(&self) -> bool {
        self.done.0.lock().unwrap().is_some()
    }
    fn send(&self, message: Msg) {
        let _ = self.tx.send(message);
    }
    /// Block until the session's cleanup is final (`Some(complete)`) or `limit` elapsed.
    fn wait_finished(&self, limit: Duration) -> Option<bool> {
        let deadline = Instant::now() + limit;
        let mut done = self.done.0.lock().unwrap();
        while done.is_none() {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            done = self.done.1.wait_timeout(done, left).unwrap().0;
        }
        *done
    }
}

/// What only the session's own thread touches.
struct Owned {
    pid: i32,
    root: Value,
    descendants: Table,
}

#[derive(Default)]
struct Registry {
    sessions: HashMap<String, Arc<SessionHandle>>,
    reserved: std::collections::HashSet<String>,
    settled: VecDeque<String>,
    /// Ids (with their generation) of sessions evicted from `sessions` after settling: a late `TTY_EOF` for one of
    /// them is still answered `accepted` (E1 5.5 race item 6; v8 bounds the memory at `TOMBSTONES`).
    tombstones: HashMap<String, String>,
    tombstone_order: VecDeque<String>,
    /// One cancellation state machine per create request still without a final answer or
    /// still published: the only source of a cancel verdict (see `CreatePhase`).
    creates: HashMap<u64, CreatePhase>,
    last_request: u64,
    /// Creates being coordinated right now (spawn in progress): shutdown waits for them.
    creating: usize,
    /// Every tty this runtime generation has handed out and not yet retired (5.2 uniqueness).
    ttys: std::collections::HashSet<String>,
}

/// The verdict of a native `cancel` for a create request.
#[derive(Debug, PartialEq)]
enum CancelVerdict {
    /// The ownership gate has not opened and will refuse: the target can never execute.
    Prevented,
    /// The gate opened (or an ungated spawn began): the target may be running.
    Released,
    /// The created session is published under this id.
    Registered(String),
    /// The create's answer was already produced and its record is gone.
    Unknown,
}

/// Where a create stands with respect to its target's first instruction. Every transition and
/// every verdict happens under the registry lock, so a verdict can never describe a state the
/// create has already left.
#[derive(Debug, PartialEq)]
enum CreatePhase {
    /// Read and admitted, ownership gate still closed.
    Queued,
    /// A cancel won before the release: admission or the gate will refuse.
    Cancelled,
    /// The gate opened (or an ungated spawn began); `true` once a cancel arrived.
    Released(bool),
    /// Published as this session id.
    Registered(String),
}

/// At most this many creates may be cancelled before their request is read.
const EARLY_CANCELS: usize = 256;

impl Registry {
    /// The stdin loop routed a create request to its worker.
    fn note_create(&mut self, request: u64) {
        self.creates.entry(request).or_insert(CreatePhase::Queued);
    }
    /// The create worker admits the request (before any spawn). An ungated create can run
    /// from here on, so it is released at admission.
    fn admit_create(&mut self, request: u64, gated: bool) -> Result<(), &'static str> {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return Err("CREATE_CANCELLED");
        }
        let phase = if gated {
            CreatePhase::Queued
        } else {
            CreatePhase::Released(false)
        };
        self.creates.insert(request, phase);
        Ok(())
    }
    /// Under the caller's lock, immediately before the gate byte is written.
    fn release_gate(&mut self, request: u64) -> bool {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return false;
        }
        self.creates.insert(request, CreatePhase::Released(false));
        true
    }
    /// The gate byte could not be written after `release_gate` allowed it: the target is still
    /// gated and its spawn is about to fail.
    fn release_failed(&mut self, request: u64) {
        self.creates.insert(request, CreatePhase::Queued);
    }
    /// `spawn` returned; the phase stays `Released` until the session is published.
    fn spawn_returned(&mut self, _request: u64) {}
    /// The create failed or was refused after admission: its answer is an error.
    fn create_failed(&mut self, request: u64) {
        self.creates.remove(&request);
    }
    /// Publish the created session; true when a cancel arrived after the release.
    fn register_create(&mut self, request: u64, session_id: &str) -> bool {
        let cancel = matches!(
            self.creates.get(&request),
            Some(CreatePhase::Released(true))
        );
        self.creates
            .insert(request, CreatePhase::Registered(session_id.to_owned()));
        cancel
    }
    /// A settled session leaves the registry, and with it its create record.
    fn evict_create(&mut self, session_id: &str) {
        self.creates
            .retain(|_, phase| *phase != CreatePhase::Registered(session_id.to_owned()));
    }
    /// The single cancellation verdict for `target`.
    fn cancel_verdict(&mut self, target: u64) -> Result<CancelVerdict, &'static str> {
        Ok(match self.creates.get_mut(&target) {
            Some(phase @ CreatePhase::Queued) => {
                *phase = CreatePhase::Cancelled;
                CancelVerdict::Prevented
            }
            Some(CreatePhase::Cancelled) => CancelVerdict::Prevented,
            Some(CreatePhase::Released(cancel)) => {
                *cancel = true;
                CancelVerdict::Released
            }
            Some(CreatePhase::Registered(sid)) => CancelVerdict::Registered(sid.clone()),
            None => {
                // A create not read yet can still be refused at admission; one whose answer
                // was already produced has no record and is never "prevented".
                let unread = target > self.last_request;
                if !unread {
                    return Ok(CancelVerdict::Unknown);
                }
                let early = self
                    .creates
                    .values()
                    .filter(|p| **p == CreatePhase::Cancelled)
                    .count();
                if early >= EARLY_CANCELS {
                    return Err("cancel admission full");
                }
                self.creates.insert(target, CreatePhase::Cancelled);
                CancelVerdict::Prevented
            }
        })
    }
}

fn known_owned(owned: &Owned) -> Table {
    let mut known = owned.descendants.clone();
    if let Some(start) = owned.root["start_value"].as_str() {
        known.insert(owned.pid, (0, owned.pid, start.to_owned()));
    }
    known
}
fn retain_owned(owned: &mut Owned, table: &Table) {
    let pid = owned.pid;
    #[cfg(target_os = "macos")]
    owned.descendants.extend(
        table
            .iter()
            .filter(|(child, _)| **child != pid)
            .map(|(&child, entry)| (child, entry.clone())),
    );
    #[cfg(target_os = "linux")]
    owned.descendants.extend(
        table
            .iter()
            .filter(|(child, _)| **child != pid)
            .map(|(&child, entry)| (child, entry.clone())),
    );
}

/// Event-driven wait for owned (possibly non-child) processes to exit; returns those still
/// running at `deadline`. macOS uses kqueue `NOTE_EXIT`; Linux uses pidfds.
fn wait_exit(pids: &[i32], deadline: Instant) -> Vec<i32> {
    #[cfg(target_os = "macos")]
    {
        darwin::wait_exit(pids, deadline)
    }
    #[cfg(target_os = "linux")]
    {
        linux::wait_exit(pids, deadline)
    }
}

/// Signal an owned process whose identity (`expected_start`) was just matched against a fresh table.
/// Linux binds the signal to the verified process instance through a pidfd; macOS signals by number
/// right after the match, as before.
fn signal_owned(pid: i32, expected_start: &str, signal: i32, group: bool) {
    #[cfg(target_os = "macos")]
    {
        let _ = expected_start;
        // SAFETY: identity (pid + birth) matched against a fresh table by the caller.
        unsafe {
            libc::kill(if group { -pid } else { pid }, signal);
        }
    }
    #[cfg(target_os = "linux")]
    {
        linux::signal(pid, expected_start, signal, group);
    }
}

#[cfg(target_os = "macos")]
fn snapshot(initial_child: Option<i32>, known: &Table) -> io::Result<Table> {
    darwin::snapshot(initial_child, known)
}
#[cfg(target_os = "linux")]
fn snapshot(initial_child: Option<i32>, known: &Table) -> io::Result<Table> {
    linux::snapshot(initial_child, known)
}
#[cfg(all(test, target_os = "macos"))]
fn descendants(table: &Table, pid: i32) -> Table {
    let mut result = Table::new();
    let mut pending = vec![pid];
    while let Some(parent) = pending.pop() {
        for (&child, entry) in table {
            if entry.0 == parent && child != pid && !result.contains_key(&child) {
                result.insert(child, entry.clone());
                pending.push(child);
            }
        }
    }
    result
}

/// `limits.snapshot_ms`: the bound on one ownership snapshot (the pre-existing 5 s literal, now named and
/// advertised; E1 13.3 rows 12-13).
const SNAPSHOT_MS: u64 = 5000;

/// Append one identity record and fsync it. The line is a single `write`, so an
/// abrupt death of this process leaves either the whole line or none of it.
///
/// Why one `fsync` per create stays (E1 decision D12): this record is the crash-durable evidence that names a root
/// before its target may run (ownership invariant I1 of ticket 05). If the sidecar or the backend dies right after
/// the gate opens, the next startup finds the root through this line and cleans it by identity; without the sync a
/// crash could leave an executed, unnamed process. It runs under `FD_FORK_LOCK`, which exists for a different reason
/// (no concurrent fork may inherit the gate's write end), so the sync cost is a correctness price, not a latency
/// target; the measured cost is in `evidence/e3/e3-handoff.md`.
fn journal_append(path: &std::path::Path, record: &Value) -> io::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut line = serde_json::to_vec(record).map_err(io::Error::other)?;
    line.push(b'\n');
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(&line)?;
    file.sync_all()
}

fn identity(pid: i32, entry: &(i32, i32, String)) -> Value {
    json!({"pid":pid,"pgid":entry.1,"start_kind":"posix-lstart","start_value":entry.2})
}

fn text<'a>(value: &'a Value, key: &str) -> io::Result<&'a str> {
    value[key]
        .as_str()
        .filter(|text| !text.contains('\0'))
        .ok_or_else(|| io::Error::other(format!("invalid {key}")))
}
fn dimension(value: &Value, key: &str) -> io::Result<u16> {
    value[key]
        .as_u64()
        .and_then(|n| u16::try_from(n).ok())
        .filter(|n| *n > 0)
        .ok_or_else(|| io::Error::other(format!("invalid {key}")))
}

fn cloexec_pipe() -> io::Result<(i32, i32)> {
    let mut fds = [0i32; 2];
    // SAFETY: valid two-element output array.
    if unsafe { libc::pipe(fds.as_mut_ptr()) } < 0 {
        return Err(io::Error::last_os_error());
    }
    for fd in fds {
        // SAFETY: descriptors just created and owned here.
        if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
            let error = io::Error::last_os_error();
            close_fds(&fds);
            return Err(error);
        }
    }
    Ok((fds[0], fds[1]))
}
fn close_fds(fds: &[i32]) {
    for &fd in fds {
        if fd >= 0 {
            // SAFETY: closing descriptors owned by this call path; errors are not actionable.
            unsafe { libc::close(fd) };
        }
    }
}

/// Owner half of the ownership gate: learn the gated child's pid, take its identity, make it
/// durable, and only then release the child. Any failure returns before the release.
struct GateContext<'a> {
    journal: &'a std::path::Path,
    wire: &'a Wire,
    argv0: &'a str,
    id: &'a str,
    generation: &'a str,
    sessions: &'a Sessions,
    request: u64,
}
fn gate_owner(report_r: i32, gate_w: i32, ctx: &GateContext) -> io::Result<(Table, Value)> {
    let GateContext {
        journal,
        wire,
        argv0,
        id,
        generation,
        sessions,
        request,
    } = *ctx;
    let mut buffer = [0u8; 4];
    let mut got = 0;
    while got < 4 {
        // SAFETY: valid owned descriptor and in-bounds buffer slice.
        let n = unsafe { libc::read(report_r, buffer[got..].as_mut_ptr().cast(), 4 - got) };
        if n < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        if n <= 0 {
            return Err(io::Error::other(
                "OWNERSHIP_JOURNAL_FAILED: child never reported",
            ));
        }
        got += n as usize;
    }
    let pid = i32::from_ne_bytes(buffer);
    let table = snapshot(Some(pid), &Table::new())
        .map_err(|_| io::Error::other("OWNERSHIP_JOURNAL_FAILED: identity unavailable"))?;
    let root = table
        .get(&pid)
        .map(|entry| identity(pid, entry))
        .filter(|root| root["start_value"].as_str().is_some_and(|v| !v.is_empty()))
        .ok_or_else(|| io::Error::other("OWNERSHIP_JOURNAL_FAILED: identity unavailable"))?;
    let record = json!({"journal":1,"pid":pid,"pgid":root["pgid"],"start_kind":root["start_kind"],
        "start_value":root["start_value"],"argv0":argv0,"session_id":id,"session_generation":generation,
        "runtime_generation":wire.generation,"owner_pid":wire.owner_pid});
    journal_append(journal, &record).map_err(|error| {
        io::Error::other(format!("OWNERSHIP_JOURNAL_FAILED: {}", error.kind() as u32))
    })?;
    // The cancellation check and the release are one step under the registry lock: a cancel
    // either lands before it (the target then never executes; the answer to the cancel is
    // "prevented") or after it (answered "released": the target may already be running).
    let mut registry = sessions.lock().unwrap_or_else(|e| e.into_inner());
    if !registry.release_gate(request) {
        return Err(io::Error::other("CREATE_CANCELLED"));
    }
    // SAFETY: valid owned descriptor, one-byte static buffer.
    if unsafe { libc::write(gate_w, b"G".as_ptr().cast(), 1) } != 1 {
        registry.release_failed(request);
        return Err(io::Error::other("OWNERSHIP_JOURNAL_FAILED: gate release"));
    }
    drop(registry);
    Ok((table, root))
}

/// Validated `create` arguments.
struct CreateArgs {
    id: String,
    generation: String,
    pane: String,
    workspace: String,
    cwd: String,
    agent: String,
    argv: Vec<String>,
    rows: u16,
    cols: u16,
}
fn create_args(args: &Value) -> io::Result<CreateArgs> {
    let agent = args["agent_key"].as_str().unwrap_or("").to_owned();
    if !matches!(agent.as_str(), "" | "terminal") && !adapters::integrated(&agent) {
        return Err(io::Error::other("agent runtime not integrated"));
    }
    let argv: Vec<String> = args["argv"]
        .as_array()
        .ok_or_else(|| io::Error::other("invalid argv"))?
        .iter()
        .map(|v| {
            v.as_str()
                .filter(|s| !s.contains('\0'))
                .map(String::from)
                .ok_or_else(|| io::Error::other("invalid argv"))
        })
        .collect::<io::Result<_>>()?;
    if argv.is_empty() {
        return Err(io::Error::other("invalid argv"));
    }
    Ok(CreateArgs {
        id: text(args, "session_id")?.to_owned(),
        generation: text(args, "session_generation")?.to_owned(),
        pane: text(args, "pane_id")?.to_owned(),
        workspace: text(args, "workspace_path")?.to_owned(),
        cwd: text(args, "cwd")?.to_owned(),
        agent,
        argv,
        rows: dimension(args, "rows")?,
        cols: dimension(args, "cols")?,
    })
}

/// The `SESSION_OWNED` meta (5.2) for a session whose root is `root`.
fn owned_meta(create: u64, handle: &SessionHandle, root: &Value) -> Value {
    json!({"create_id":create,"session_id":handle.id,"session_generation":handle.generation,
        "pane_id":handle.pane,"workspace_path":handle.workspace,"root":root,
        "pty":handle.pty.owned_record()})
}

/// 5.1.3: before any PTY or process exists, the worst-case encoded `SESSION_OWNED` of this
/// session must fit the `HAS_FD` bound, so an oversized handoff is never found after the gate
/// opened.
fn handoff_size_ok(create: u64, args: &CreateArgs) -> bool {
    let root = json!({"pid":99_999_999,"pgid":99_999_999,"start_kind":"posix-starttime",
        "start_value":"x".repeat(40)});
    let meta = json!({"create_id":create,"session_id":args.id,"session_generation":args.generation,
        "pane_id":args.pane,"workspace_path":args.workspace,"root":root,
        "pty":{"tty":"x".repeat(TTY_NAME_MAX)}});
    let bytes = serde_json::to_vec(&meta)
        .map(|b| b.len())
        .unwrap_or(usize::MAX);
    frame::HEADER_LEN.saturating_add(bytes) <= frame::MAX_HAS_FD_FRAME
}

struct Spawned {
    handle: Arc<SessionHandle>,
    mailbox: mpsc::Receiver<Msg>,
    owned: Owned,
}

fn watch_exit(pid: i32, cell: Arc<ExitCell>, tx: mpsc::Sender<Msg>) -> io::Result<()> {
    thread::Builder::new()
        .name("exit-watch".into())
        .spawn(move || {
            let mut status: libc::c_int = 0;
            let info = loop {
                // SAFETY: blocking wait for our own child; `status` is a valid out pointer.
                let reaped = unsafe { libc::waitpid(pid, &mut status, 0) };
                if reaped == pid {
                    break if libc::WIFEXITED(status) {
                        ExitInfo {
                            code: Some(libc::WEXITSTATUS(status)),
                            signal: None,
                        }
                    } else {
                        ExitInfo {
                            code: None,
                            signal: Some(libc::WTERMSIG(status)),
                        }
                    };
                }
                if reaped < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                break ExitInfo {
                    code: None,
                    signal: None,
                }; // not our child any more: status unknown
            };
            cell.set(info);
            let _ = tx.send(Msg::Exited);
        })?;
    Ok(())
}

/// A released root whose session could not be set up: signal its group by the identity just recorded and
/// report whether it was verified reaped (E1 5.1.3). It is our own child, so the wait is a real `waitpid`.
/// Only the **root** is waited for: the group was just signalled and the only claim made on `effect:none` is that
/// the root (the one process that ran) is gone; no descendant had time to be recorded (no snapshot ran yet), and
/// anything that escaped the group is what the generation's journal-based cleanup is for.
fn kill_and_reap_released(pid: i32) -> bool {
    // SAFETY: the root is our own child, a session/group leader we just created (pgid == pid); a
    // negative pid addresses exactly that group.
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    #[cfg(target_os = "macos")]
    let _ = wait_exit(&[pid], Instant::now() + ROOT_REAP); // event-driven; the exit is reaped below
    loop {
        let mut status: libc::c_int = 0;
        #[cfg(target_os = "macos")]
        let flags = libc::WNOHANG;
        #[cfg(not(target_os = "macos"))]
        let flags = 0; // a SIGKILLed child exits promptly; E5 replaces this with pidfd
                       // SAFETY: waitpid on our own child with a valid status pointer.
        let reaped = unsafe { libc::waitpid(pid, &mut status, flags) };
        if reaped == pid {
            return true;
        }
        if reaped < 0 {
            match io::Error::last_os_error().raw_os_error() {
                Some(libc::EINTR) => continue,
                Some(libc::ECHILD) => return true, // already reaped elsewhere
                _ => return false,
            }
        }
        return false; // still running at the bound
    }
}

/// The error for a failure after the gate released a root: `effect:none` only if it was verified killed and
/// reaped first (E1 5.1.3), otherwise `CLEANUP_FAILED` (partial) and the generation is lost.
fn post_effect_failure(verified: bool, what: &str, error: &io::Error) -> io::Error {
    if verified {
        io::Error::other(format!("{what}: {error}"))
    } else {
        io::Error::other(format!(
            "CLEANUP_FAILED: {what} ({error}) and the released root could not be verified reaped"
        ))
    }
}

fn spawn(args: &Value, wire: &Wire, request: u64, sessions: &Sessions) -> io::Result<Spawned> {
    let parsed = create_args(args)?;
    let (pty, slave) = SessionPty::open(parsed.rows, parsed.cols)?;
    // Uniqueness of the slave name among this generation's live sessions (5.2, 6.4).
    if !sessions.lock().unwrap().ttys.insert(pty.tty().to_owned()) {
        return Err(io::Error::other("duplicate tty"));
    }
    let tty = pty.tty().to_owned();
    let result = spawn_gated(args, &parsed, pty, slave, wire, request, sessions);
    if result.is_err() {
        sessions.lock().unwrap().ttys.remove(&tty);
    }
    result
}

fn spawn_gated(
    args: &Value,
    parsed: &CreateArgs,
    pty: SessionPty,
    slave: SlaveEnd,
    wire: &Wire,
    request: u64,
    sessions: &Sessions,
) -> io::Result<Spawned> {
    let argv = &parsed.argv;
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(&parsed.cwd)
        .env_clear();
    for (key, value) in args["env"]
        .as_object()
        .ok_or_else(|| io::Error::other("invalid env"))?
    {
        if key.contains(['\0', '=']) {
            return Err(io::Error::other("invalid env key"));
        }
        let value = value
            .as_str()
            .filter(|s| !s.contains('\0'))
            .ok_or_else(|| io::Error::other("invalid env value"))?;
        command.env(key, value);
    }
    command
        .stdin(Stdio::from(slave.0.try_clone()?))
        .stdout(Stdio::from(slave.0.try_clone()?))
        .stderr(Stdio::from(slave.0));
    // Ownership gate (mandatory in wire v1): the root must not execute its target before its
    // identity is durable. The forked child reports its pid and then BLOCKS on a gate pipe before
    // exec; the owner records the identity, appends+fsyncs the journal and only then releases the
    // gate. EOF on the gate (owner dead, or journal refused) makes the child fail its own spawn,
    // so no target ever runs unnamed. Pipe creation and the spawn are serialised process-wide so
    // no concurrent fork can inherit the gate's write end (which would keep the gate open after
    // the owner's death).
    let _spawn_lock = FD_FORK_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (report_r, report_w) = cloexec_pipe()?;
    let (gate_r, gate_w) = match cloexec_pipe() {
        Ok(pair) => pair,
        Err(error) => {
            close_fds(&[report_r, report_w]);
            return Err(error);
        }
    };
    // SAFETY: only async-signal-safe native calls between fork and exec (setsid, signal, ioctl,
    // close, getpid, write, read on plain integers); no locks, no allocation, no std I/O.
    unsafe {
        command.pre_exec(move || {
            if libc::setsid() < 0 {
                return Err(io::Error::last_os_error());
            }
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::signal(libc::SIGXFSZ, libc::SIG_DFL);
            // Preserve baseline's best-effort controlling-terminal claim.
            libc::ioctl(0, libc::TIOCSCTTY as _, 0);
            // Only the owner may hold the gate's write end: drop this fork's copies.
            libc::close(gate_w);
            libc::close(report_r);
            let pid = libc::getpid();
            if libc::write(report_w, (&pid as *const i32).cast(), 4) != 4 {
                return Err(io::Error::last_os_error());
            }
            let mut byte = 0u8;
            loop {
                let n = libc::read(gate_r, (&mut byte as *mut u8).cast(), 1);
                if n == 1 && byte == b'G' {
                    break;
                }
                if n < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                // EOF or anything else: the owner is gone or refused. Never exec.
                return Err(io::Error::from_raw_os_error(libc::ECANCELED));
            }
            Ok(())
        });
    }
    let (child, table, root) = {
        let journal = wire.journal.as_path();
        let (id_ref, generation_ref, argv0) = (&parsed.id, &parsed.generation, &argv[0]);
        let (spawned, verdict) = thread::scope(|scope| {
            let watcher = scope.spawn(move || {
                // Owner side of the gate: every path below ends by closing the gate's write end
                // (released only by the explicit `G`), so a refusal or panic means EOF.
                let verdict = gate_owner(
                    report_r,
                    gate_w,
                    &GateContext {
                        journal,
                        wire,
                        argv0,
                        id: id_ref,
                        generation: generation_ref,
                        sessions,
                        request,
                    },
                );
                close_fds(&[report_r, gate_w]);
                verdict
            });
            let spawned = command.spawn();
            // The parent's copies of the child-side ends are no longer needed; closing the report
            // write end also lets the watcher observe a child that died before reporting.
            close_fds(&[report_w, gate_r]);
            (spawned, watcher.join())
        });
        let verdict = match verdict {
            Ok(verdict) => verdict,
            Err(_) => Err(io::Error::other(
                "OWNERSHIP_JOURNAL_FAILED: watcher panicked",
            )),
        };
        match (spawned, verdict) {
            (Ok(child), Ok((table, root))) => (child, table, root),
            (Ok(mut child), Err(error)) => {
                // Cannot happen (the gate is released only after success); stay safe regardless.
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
            (Err(_), Err(error)) => return Err(error),
            (Err(error), Ok(_)) => return Err(error), // exec failed after a durable record: stale row only
        }
    };
    // Every sidecar copy of the slave closes now, so the CLI tree closing its own is observable
    // as EOF on the master.
    drop(command);
    let pid = child.id() as i32;
    drop(child); // reaped only by the exit watcher below
    let pty = Arc::new(pty);
    let (tx, mailbox) = mpsc::channel();
    let exit = Arc::new(ExitCell::new());
    #[cfg(feature = "fault-hooks")]
    let watched = if fault::watch_exit_fails(&parsed.pane) {
        Err(io::Error::other("fault: exit watcher could not start"))
    } else {
        watch_exit(pid, exit.clone(), tx.clone())
    };
    #[cfg(not(feature = "fault-hooks"))]
    let watched = watch_exit(pid, exit.clone(), tx.clone());
    if let Err(error) = watched {
        // The gate was released: the root has run. `effect:none` is only true if it is dead and reaped.
        #[cfg(feature = "fault-hooks")]
        let verified = !fault::reap_is_unverifiable(&parsed.pane) && kill_and_reap_released(pid);
        #[cfg(not(feature = "fault-hooks"))]
        let verified = kill_and_reap_released(pid);
        return Err(post_effect_failure(
            verified,
            "the exit watcher could not start",
            &error,
        ));
    }
    let handle = Arc::new(SessionHandle {
        id: parsed.id.clone(),
        generation: parsed.generation.clone(),
        pane: parsed.pane.clone(),
        workspace: parsed.workspace.clone(),
        agent_key: parsed.agent.clone(),
        spec: adapters::shutdown(&parsed.agent),
        pty,
        tx,
        exit,
        done: (Mutex::new(None), Condvar::new()),
        adopted: AtomicBool::new(false),
        started: Instant::now(),
        survivors: Mutex::new(Vec::new()),
    });
    let mut owned = Owned {
        pid,
        root,
        descendants: Table::new(),
    };
    retain_owned(&mut owned, &table);
    Ok(Spawned {
        handle,
        mailbox,
        owned,
    })
}

// ---- lifecycle ---------------------------------------------------------------------------------

struct State {
    owned: Owned,
    closing: Option<bool>,
    exited_sent: bool,
    eof: bool,
    eof_deadline: Option<Instant>,
    next_snapshot: Instant,
}

fn snapshot_cadence(started: Instant) -> Duration {
    Duration::from_millis(
        if started.elapsed() < Duration::from_millis(SNAPSHOT_YOUNG_WINDOW_MS) {
            SNAPSHOT_YOUNG_MS
        } else {
            SNAPSHOT_STEADY_MS
        },
    )
}

/// `SESSION_EXITED`: the root was reaped (5.5). Nothing about the stream or the descendants.
fn send_exited(handle: &SessionHandle, state: &mut State, wire: &Wire) {
    if state.exited_sent {
        return;
    }
    let Some(info) = handle.exit.get() else {
        return;
    };
    let mut meta = handle.meta();
    meta["reason"] = json!(if state.closing.is_some() {
        "killed"
    } else {
        "exit"
    });
    meta["exit_code"] = json!(info.code.or(info.signal.map(|n| -n)));
    meta["signal"] = json!(info.signal.map(|n| format!("SIG{n}")));
    meta["uptime_ms"] = json!(handle.started.elapsed().as_millis() as u64);
    wire.out.event(code::SESSION_EXITED, meta);
    state.exited_sent = true;
}

/// Signal recorded identities, wait event-driven, escalate, verify (13.1 kill policy). `hangup`
/// is true for a kill: unless the adapter defers it, the sidecar's master closes right after the
/// first signal and `SESSION_HANGUP` tells Python (5.5 R-e).
fn cleanup(
    handle: &SessionHandle,
    state: &mut State,
    wire: &Wire,
    force: bool,
    hangup: bool,
) -> io::Result<Vec<Value>> {
    let pid = state.owned.pid;
    if handle.exit.get().is_some() && state.owned.descendants.is_empty() {
        return Ok(Vec::new());
    }
    let before = snapshot(None, &known_owned(&state.owned))?;
    retain_owned(&mut state.owned, &before);
    let root_start = state.owned.root["start_value"].as_str().map(String::from);
    let root_live = handle.exit.get().is_none()
        && before
            .get(&pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == root_start.as_deref());
    let graceful = handle.spec.is_some_and(|spec| spec.graceful);
    let signal = if force && !graceful {
        libc::SIGKILL
    } else {
        libc::SIGTERM
    };
    if root_live {
        // The root's PID+birth is matched and its own child group remains owned.
        signal_owned(pid, root_start.as_deref().unwrap_or_default(), signal, true);
    }
    // Signalled owned descendants, confirmed gone before cleanup reports them absent.
    let mut signalled: Vec<(i32, (i32, i32, String))> = Vec::new();
    for (&child, old) in &state.owned.descendants {
        if before.get(&child).is_some_and(|entry| entry.2 == old.2) {
            signal_owned(child, &old.2, signal, false);
            signalled.push((child, old.clone()));
        }
    }
    if hangup && !handle.spec.is_some_and(|spec| spec.defer_master_close) {
        // The kernel hangs the PTY up when the last master closes: the baseline's "signal, then
        // close the master" effect (SIGHUP) is preserved without Rust ever reading the PTY.
        handle.pty.close_master();
        wire.out.event(code::SESSION_HANGUP, handle.meta());
    }
    if let Some(spec) = handle.spec.filter(|spec| spec.graceful) {
        // Declared graceful: wait event-driven up to the declared grace for the root.
        handle.exit.wait(Duration::from_millis(spec.grace_ms));
        send_exited(handle, state, wire);
    } else if !force {
        // Conditional wait (replaces the baseline's unconditional 1 s sleep): returns as soon as
        // the root and every signalled descendant exited, at the declared escalation bound.
        let deadline = Instant::now() + Duration::from_millis(KILL_ESCALATION_MS);
        handle
            .exit
            .wait(deadline.saturating_duration_since(Instant::now()));
        send_exited(handle, state, wire);
        let pids: Vec<i32> = signalled.iter().map(|(child, _)| *child).collect();
        wait_exit(&pids, deadline);
    }
    let after = snapshot(None, &known_owned(&state.owned))?;
    retain_owned(&mut state.owned, &after);
    if handle.exit.get().is_none()
        && after
            .get(&pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == root_start.as_deref())
    {
        // The root identity was re-verified against the fresh table.
        signal_owned(pid, root_start.as_deref().unwrap_or_default(), libc::SIGKILL, true);
    }
    for (&child, old) in &state.owned.descendants {
        if after.get(&child).is_some_and(|entry| entry.2 == old.2) {
            signal_owned(child, &old.2, libc::SIGKILL, false);
            signalled.push((child, old.clone()));
        }
    }
    let deadline = Instant::now() + ROOT_REAP;
    // A blocked PTY write can delay even SIGKILL until a master drains: Python keeps reading.
    if handle.exit.wait(ROOT_REAP).is_none() {
        return Err(io::Error::other("root reap timeout"));
    }
    send_exited(handle, state, wire);
    signalled.sort_by_key(|(child, _)| *child);
    signalled.dedup_by_key(|(child, _)| *child);
    let pids: Vec<i32> = signalled.iter().map(|(child, _)| *child).collect();
    let still_running = wait_exit(&pids, deadline);
    let final_table = snapshot(None, &known_owned(&state.owned))?;
    retain_owned(&mut state.owned, &final_table);
    Ok(survivors_after_cleanup(
        &state.owned.descendants,
        &final_table,
        &signalled,
        &still_running,
    ))
}

/// The recorded descendants that are still there after the kill (13.1 verification). A recorded descendant that
/// was signalled and whose exit was **observed** (`still_running` is what `wait_exit` could not confirm exited by
/// the deadline) is gone even if the final snapshot still lists it: a process that has exited stays in the table
/// as a zombie until its parent (here launchd, after the root died) reaps it, and a zombie is dead, holds nothing
/// and cannot run. Counting it made `cleanup_complete` depend on how fast the system reaps (measured under load,
/// `evidence/e3/receipts/r4/survivor-zombie/`).
fn survivors_after_cleanup(
    descendants: &Table,
    final_table: &Table,
    signalled: &[(i32, (i32, i32, String))],
    still_running: &[i32],
) -> Vec<Value> {
    // Signalled and confirmed exited: dead, whatever the table still shows.
    let confirmed_exited = |child: &i32| {
        signalled.iter().any(|(pid, _)| pid == child) && !still_running.contains(child)
    };
    #[allow(unused_mut)]
    let mut survivors: Vec<Value> = descendants
        .iter()
        .filter(|(child, _)| !confirmed_exited(child))
        .filter_map(|(&child, old)| {
            final_table
                .get(&child)
                .filter(|entry| entry.2 == old.2)
                .map(|entry| (child, entry))
        })
        .map(|(child, entry)| identity(child, entry))
        .collect();
    #[cfg(target_os = "macos")]
    for (child, old) in signalled {
        // Not gone by the deadline: a survivor even if the snapshot no longer sees it. A child whose exit the kqueue
        // wait observed is not one: `owned_not_gone` is true for a zombie (it is still reachable and no longer listed).
        if !confirmed_exited(child)
            && !final_table.contains_key(child)
            && darwin::owned_not_gone(*child, &old.2)
        {
            survivors.push(identity(*child, old));
        }
    }
    survivors
}

/// `SESSION_CLEANED` and the end of the session thread's work (5.5).
fn finish(
    handle: &SessionHandle,
    wire: &Wire,
    sessions: &Sessions,
    survivors: io::Result<Vec<Value>>,
) {
    let root_reaped = handle.exit.get().is_some();
    let complete = survivors.as_ref().is_ok_and(Vec::is_empty) && root_reaped;
    let mut meta = handle.meta();
    meta["cleanup_complete"] = json!(complete);
    meta["root_reaped"] = json!(root_reaped);
    let survivors = survivors.unwrap_or_else(|error| vec![json!({"error":error.to_string()})]);
    *handle.survivors.lock().unwrap() = survivors.clone();
    meta["survivors"] = json!(survivors);
    meta["unverifiable"] = json!([]);
    #[cfg(feature = "fault-hooks")]
    let complete = if fault::survivor(&handle.pane) {
        let injected =
            json!([{"pid":1,"pgid":1,"start_kind":"posix-lstart","start_value":"fault-survivor"}]);
        *handle.survivors.lock().unwrap() = injected.as_array().cloned().unwrap_or_default();
        meta["survivors"] = injected;
        meta["cleanup_complete"] = json!(false);
        false
    } else {
        complete
    };
    // Rust may close its own master at cleanup, never earlier (5.5 R-a).
    handle.pty.close_master();
    #[cfg(feature = "fault-hooks")]
    let silent = fault::cleaned_silent(&handle.pane);
    #[cfg(not(feature = "fault-hooks"))]
    let silent = false;
    if !silent {
        wire.out.event(code::SESSION_CLEANED, meta);
    }
    *handle.done.0.lock().unwrap() = Some(complete);
    handle.done.1.notify_all();
    let mut registry = sessions.lock().unwrap();
    registry.ttys.remove(handle.pty.tty());
    registry.settled.push_back(handle.id.clone());
    while registry.settled.len() > 256 {
        let id = registry.settled.pop_front().unwrap();
        if let Some(evicted) = registry.sessions.remove(&id) {
            registry
                .tombstones
                .insert(id.clone(), evicted.generation.clone());
            registry.tombstone_order.push_back(id.clone());
            while registry.tombstone_order.len() > TOMBSTONES {
                let oldest = registry.tombstone_order.pop_front().unwrap();
                registry.tombstones.remove(&oldest);
            }
        }
        registry.reserved.remove(&id);
        registry.evict_create(&id);
    }
}

fn ownership_snapshot(handle: &SessionHandle, state: &mut State, wire: &Wire) {
    let known = known_owned(&state.owned);
    #[cfg(target_os = "linux")]
    let pid = state.owned.pid;
    let Ok(table) = snapshot(None, &known) else {
        return;
    };
    #[cfg(target_os = "linux")]
    {
        if !table
            .get(&pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == state.owned.root["start_value"].as_str())
        {
            return; // Keep the last good tree when death races the snapshot.
        }
        retain_owned(&mut state.owned, &table);
    }
    #[cfg(target_os = "macos")]
    retain_owned(&mut state.owned, &table);
    let all: Vec<_> = state.owned.descendants.iter().collect();
    for (part, values) in all.chunks(128).enumerate() {
        let mut meta = handle.meta();
        meta["snapshot_seq"] = json!(handle.started.elapsed().as_millis() as u64);
        meta["part"] = json!(part);
        meta["final"] = json!((part + 1) * 128 >= all.len());
        meta["descendants"] = json!(values
            .iter()
            .map(|(pid, entry)| identity(**pid, entry))
            .collect::<Vec<_>>());
        wire.out.event(code::SESSION_OWNERSHIP, meta);
    }
}

fn session_thread(
    handle: Arc<SessionHandle>,
    mailbox: mpsc::Receiver<Msg>,
    owned: Owned,
    wire: Arc<Wire>,
    sessions: Sessions,
) {
    let mut state = State {
        owned,
        closing: None,
        exited_sent: false,
        eof: false,
        eof_deadline: None,
        next_snapshot: Instant::now() + snapshot_cadence(handle.started),
    };
    loop {
        let mut wake = state.next_snapshot;
        if let Some(deadline) = state.eof_deadline {
            wake = wake.min(deadline);
        }
        match mailbox.recv_timeout(wake.saturating_duration_since(Instant::now())) {
            Ok(Msg::Exited) => {
                send_exited(&handle, &mut state, &wire);
                if state.eof {
                    // R-d: the stream ended first; now the remaining descendants go, by identity.
                    let survivors = cleanup(&handle, &mut state, &wire, false, false);
                    return finish(&handle, &wire, &sessions, survivors);
                }
            }
            Ok(Msg::Kill(force)) => {
                state.closing = Some(force);
                let survivors = cleanup(&handle, &mut state, &wire, force, true);
                send_exited(&handle, &mut state, &wire);
                return finish(&handle, &wire, &sessions, survivors);
            }
            Ok(Msg::Shutdown) => {
                state.closing = Some(false);
                let survivors = cleanup(&handle, &mut state, &wire, false, true);
                send_exited(&handle, &mut state, &wire);
                return finish(&handle, &wire, &sessions, survivors);
            }
            Ok(Msg::TtyEof) => {
                state.eof = true;
                if handle.exit.get().is_some() {
                    send_exited(&handle, &mut state, &wire);
                    let survivors = cleanup(&handle, &mut state, &wire, false, false);
                    return finish(&handle, &wire, &sessions, survivors);
                }
                state.eof_deadline = Some(Instant::now() + Duration::from_millis(EOF_REAP_MS));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                if state.eof_deadline.is_some_and(|deadline| now >= deadline) {
                    state.eof_deadline = None;
                    // The root may have exited just now: its `Exited` message is already queued.
                    if handle.exit.get().is_none() {
                        wire.out.event(
                            code::SESSION_ROOT_LIVE,
                            json!({"session_id":handle.id,"session_generation":handle.generation}),
                        );
                    }
                }
                if now >= state.next_snapshot {
                    ownership_snapshot(&handle, &mut state, &wire);
                    state.next_snapshot = Instant::now() + snapshot_cadence(handle.started);
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
        }
    }
}

/// Harness build only. A session whose pane id is `fault:<kind>` gets that deliberate fault, so
/// unrelated panes of the same sidecar can run beside it (E1 15.2 P3, P7, P9).
#[cfg(feature = "fault-hooks")]
mod fault {
    use super::*;
    use crate::wire::SendFault;
    use std::os::fd::{FromRawFd, OwnedFd};

    pub const SUPPRESSED: &str = "fault: response suppressed";

    /// `fault:delay-response:<path>`: the CREATE answer waits until `<path>` exists (the session is
    /// already registered and `SESSION_OWNED` already sent, so every later event can overtake it).
    pub fn delay_response(pane: &str) {
        if let Some(path) = kind(pane).and_then(|k| k.strip_prefix("delay-response:")) {
            wait_for_file(path);
        }
    }
    /// Harness build only: hold until a release file exists. A deliberate poll: the file is the test's
    /// release signal and has no event to wait on; this module is outside the product binary.
    pub fn wait_for_file(path: &str) {
        while !std::path::Path::new(path).exists() {
            thread::sleep(Duration::from_millis(5));
        }
    }
    /// `fault:watch-exit-fail` / `fault:watch-exit-fail-noreap`: the exit watcher cannot start after
    /// the gate was released. `-noreap` also makes the sidecar's kill/reap verification fail.
    pub fn watch_exit_fails(pane: &str) -> bool {
        matches!(
            kind(pane),
            Some("watch-exit-fail" | "watch-exit-fail-noreap")
        )
    }
    /// `fault:cleaned-silent`: the session is cleaned (root reaped) but `SESSION_CLEANED` is never sent (P7e).
    pub fn cleaned_silent(pane: &str) -> bool {
        kind(pane) == Some("cleaned-silent")
    }
    pub fn thread_start_fails(pane: &str) -> bool {
        kind(pane) == Some("thread-start-fail")
    }
    pub fn reap_is_unverifiable(pane: &str) -> bool {
        kind(pane) == Some("watch-exit-fail-noreap")
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

    /// Harness build only: an observation whose path ends in `.fault-endless` gets a synthetic
    /// document and then a part stream that never ends.
    pub fn observation(req: &Request) -> Option<io::Result<(Value, Option<Vec<u8>>)>> {
        let path = req.meta["path"].as_str()?;
        if path.ends_with(".fault-endless") {
            return Some(Ok((
                json!({"parser":"fault","path":path,"mode":req.meta["mode"]}),
                None,
            )));
        }
        None
    }

    /// Endless part stream: the sidecar never stops sending parts for this request.
    pub fn endless_parts(req: &Request, wire: &Arc<Wire>) -> bool {
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

    fn kind(pane: &str) -> Option<&str> {
        pane.strip_prefix("fault:")
    }
    pub fn suppress_response(pane: &str) -> bool {
        matches!(
            kind(pane),
            Some("suppress-response" | "suppress-owned-and-response")
        )
    }

    /// Returns true when the fault replaced the normal `SESSION_OWNED` send.
    pub fn send_owned(
        handle: &Arc<SessionHandle>,
        mut meta: Value,
        sessions: &Sessions,
        wire: &Arc<Wire>,
    ) -> bool {
        let Some(kind) = kind(&handle.pane) else {
            return false;
        };
        // A fresh, unrelated PTY: its master is a valid master, but not this session's.
        let decoy = || Arc::new(SessionPty::open(24, 80).expect("decoy pty").0);
        match kind {
            "suppress-owned-and-response" => {}
            "suppress-response" => return false,
            "corrupt-frame" => {
                // The normal handoff, then bytes that are not a frame (F3 principal, L8).
                wire.out
                    .session_owned(meta, handle.pty.clone())
                    .expect("owned frame fits");
                wire.out.raw(vec![0xAB; 32]);
            }
            "short-send" => {
                wire.out
                    .session_owned_fault(meta, handle.pty.clone(), SendFault::Short)
            }
            "zero-send" => wire
                .out
                .session_owned_fault(meta, handle.pty.clone(), SendFault::Zero),
            // 9a: a valid PTY master of ANOTHER pty while the frame names this session's tty.
            "wrong-master" => wire
                .out
                .session_owned_fault(meta, decoy(), SendFault::Short),
            // 9e: descriptor AND metadata swapped together (the documented undetected class).
            "consistent-swap" => {
                let pty = decoy();
                meta["pty"]["tty"] = json!(pty.tty());
                wire.out.session_owned_fault(meta, pty, SendFault::Short)
            }
            // 9d: the right master, a slave name that is not its own.
            "wrong-name" => {
                meta["pty"]["tty"] = json!("/dev/ttys999");
                wire.out
                    .session_owned_fault(meta, handle.pty.clone(), SendFault::Short)
            }
            // 9b: not a tty at all (a pipe read end).
            "non-tty" => {
                let mut ends = [0i32; 2];
                // SAFETY: two-element output array.
                assert_eq!(unsafe { libc::pipe(ends.as_mut_ptr()) }, 0);
                // SAFETY: both descriptors were just created and are owned here.
                let (read, write) =
                    unsafe { (OwnedFd::from_raw_fd(ends[0]), OwnedFd::from_raw_fd(ends[1])) };
                drop(write);
                let pty = Arc::new(SessionPty::for_fault(read, handle.pty.tty().to_owned()));
                wire.out.session_owned_fault(meta, pty, SendFault::Short)
            }
            // 9c: the tty of a session that is already claimed, with a duplicate of its master.
            "duplicate-tty" => {
                let other = sessions
                    .lock()
                    .unwrap()
                    .sessions
                    .values()
                    .find(|other| other.id != handle.id)
                    .cloned();
                let Some(other) = other else { return false };
                let duplicate = other
                    .pty
                    .lend_master(|fd| unsafe { libc::dup(std::os::fd::AsRawFd::as_raw_fd(&fd)) })
                    .expect("master open");
                // SAFETY: a fresh duplicate owned by this value.
                let pty = Arc::new(SessionPty::for_fault(
                    unsafe { OwnedFd::from_raw_fd(duplicate) },
                    other.pty.tty().to_owned(),
                ));
                meta["pty"]["tty"] = json!(other.pty.tty());
                wire.out.session_owned_fault(meta, pty, SendFault::Short)
            }
            _ => return false,
        }
        true
    }
}

// ---- requests ------------------------------------------------------------------------------------

/// Is `meta` naming a settled session that was evicted from the registry (same id **and** generation)?
fn evicted_session(sessions: &Sessions, meta: &Value) -> bool {
    let (Ok(id), Ok(generation)) = (text(meta, "session_id"), text(meta, "session_generation"))
    else {
        return false;
    };
    sessions
        .lock()
        .unwrap()
        .tombstones
        .get(id)
        .is_some_and(|known| known == generation)
}

fn session_of(sessions: &Sessions, meta: &Value) -> io::Result<Arc<SessionHandle>> {
    let handle = sessions
        .lock()
        .unwrap()
        .sessions
        .get(text(meta, "session_id")?)
        .cloned()
        .ok_or_else(|| io::Error::other("UNKNOWN_SESSION"))?;
    if text(meta, "session_generation")? != handle.generation {
        return Err(io::Error::other("STALE_SESSION"));
    }
    Ok(handle)
}

/// Map an internal failure message to the wire error code, stage and effect (10.4). Every
/// pre-effect refusal is `effect:none`: the gate guarantees the target never ran.
fn classify(request_code: u16, message: &str) -> (&'static str, &'static str) {
    let creating = request_code == code::CREATE;
    let code = match message {
        "STALE_SESSION" => "STALE_SESSION",
        "UNKNOWN_SESSION" => "UNKNOWN_SESSION",
        "OWNERSHIP_UNVERIFIED" => "OWNERSHIP_UNVERIFIED",
        "CREATE_CANCELLED" => "CREATE_CANCELLED",
        "observer not integrated" => "observer not integrated", // spelled as the E1 10.4 table does
        m if m.starts_with("OWNERSHIP_JOURNAL_FAILED") => "OWNERSHIP_JOURNAL_FAILED",
        m if m.starts_with("CLEANUP_FAILED") => "CLEANUP_FAILED",
        m if m.starts_with("invalid ") || m.starts_with("duplicate ") => "BAD_REQUEST",
        _ if creating => "SPAWN_FAILED",
        _ => "IO_FAILED",
    };
    let stage = if message == "invalid handoff_size" {
        "handoff_size"
    } else if creating {
        "spawn"
    } else {
        "io"
    };
    (code, stage)
}

fn create(req: &Request, sessions: &Sessions, wire: &Arc<Wire>) -> io::Result<Value> {
    let parsed = create_args(&req.meta)?;
    if !handoff_size_ok(req.id, &parsed) {
        sessions.lock().unwrap().create_failed(req.id);
        return Err(io::Error::other("invalid handoff_size"));
    }
    {
        let mut all = sessions.lock().unwrap();
        if !all.reserved.insert(parsed.id.clone()) {
            all.create_failed(req.id);
            return Err(io::Error::other("duplicate create"));
        }
        if let Err(refusal) = all.admit_create(req.id, true) {
            all.create_failed(req.id);
            all.reserved.remove(&parsed.id);
            return Err(io::Error::other(refusal));
        }
    }
    // Reservation is retained; native spawn/probes never hold the global registry lock.
    let spawned = spawn(&req.meta, wire, req.id, sessions);
    let Spawned {
        handle,
        mailbox,
        owned,
    } = match spawned {
        Ok(spawned) => spawned,
        Err(error) => {
            let mut all = sessions.lock().unwrap();
            all.create_failed(req.id);
            all.reserved.remove(&parsed.id);
            return Err(error);
        }
    };
    let root = owned.root.clone();
    // Publication, the cancellation check and the registry entry are one critical section, and
    // the session is findable BEFORE `SESSION_OWNED` can be read: a `KILL` that Python sends the
    // moment it sees the event must never meet UNKNOWN_SESSION.
    let cancelled = {
        let mut registry = sessions.lock().unwrap();
        registry.spawn_returned(req.id);
        let cancelled = registry.register_create(req.id, &parsed.id);
        registry.sessions.insert(parsed.id.clone(), handle.clone());
        cancelled
    };
    let owned_event = owned_meta(req.id, &handle, &root);
    let (thread_handle, thread_wire, thread_sessions) =
        (handle.clone(), wire.clone(), sessions.clone());
    #[cfg(feature = "fault-hooks")]
    let thread_started = if fault::thread_start_fails(&handle.pane) {
        Err(io::Error::other("fault: session thread could not start"))
    } else {
        thread::Builder::new()
            .name("session".into())
            .spawn(move || {
                session_thread(thread_handle, mailbox, owned, thread_wire, thread_sessions)
            })
    };
    #[cfg(not(feature = "fault-hooks"))]
    let thread_started = thread::Builder::new()
        .name("session".into())
        .spawn(move || session_thread(thread_handle, mailbox, owned, thread_wire, thread_sessions));
    if let Err(error) = thread_started {
        // The root has run and is registered but nothing will ever serve it: end it, verified, and take it
        // out of the registry before answering (E1 5.1.3: the same rule as any post-gate failure).
        let pid = root["pid"].as_i64().unwrap_or(0) as i32;
        // SAFETY: the recorded root of a session this sidecar just created (pgid == pid).
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
        let verified = handle.exit.wait(ROOT_REAP).is_some();
        handle.pty.close_master();
        let mut all = sessions.lock().unwrap();
        all.sessions.remove(&parsed.id);
        all.reserved.remove(&parsed.id);
        all.ttys.remove(handle.pty.tty());
        drop(all);
        return Err(post_effect_failure(
            verified,
            "the session thread could not start",
            &error,
        ));
    }
    #[cfg(feature = "fault-hooks")]
    let faulted = fault::send_owned(&handle, owned_event.clone(), sessions, wire);
    #[cfg(not(feature = "fault-hooks"))]
    let faulted = false;
    if !faulted {
        if let Err(error) = wire.out.session_owned(owned_event, handle.pty.clone()) {
            handle.send(Msg::Kill(true));
            return Err(error);
        }
    }
    if cancelled {
        handle.send(Msg::Kill(true));
    }
    #[cfg(feature = "fault-hooks")]
    if fault::suppress_response(&handle.pane) {
        return Err(io::Error::other(fault::SUPPRESSED));
    }
    #[cfg(feature = "fault-hooks")]
    fault::delay_response(&handle.pane);
    Ok(
        json!({"session_id":handle.id,"session_generation":handle.generation,
        "state":"owned","root":root}),
    )
}

fn resize_session(
    handle: &SessionHandle,
    meta: &Value,
) -> Result<Value, (&'static str, String, Value)> {
    let bad = |message: &str| ("BAD_REQUEST", message.to_owned(), json!({"effect":"none"}));
    let cols = dimension(meta, "cols").map_err(|_| bad("invalid cols"))?;
    let rows = dimension(meta, "rows").map_err(|_| bad("invalid rows"))?;
    if handle.finished() {
        return Ok(json!({"applied":false,"steps":0}));
    }
    let closed = |error: &io::Error| error.raw_os_error() == Some(libc::EBADF);
    let mut steps = 0;
    if meta["redraw"].as_bool().unwrap_or(false) {
        match handle.pty.resize(rows.saturating_sub(1).max(1), cols) {
            Ok(()) => steps = 1,
            Err(error) if closed(&error) => return Ok(json!({"applied":false,"steps":0})),
            Err(error) => {
                return Err((
                    "IO_FAILED",
                    error.to_string(),
                    json!({"effect":"none","steps":0}),
                ))
            }
        }
    }
    #[cfg(feature = "fault-hooks")]
    if steps == 1 && fault::fail_second_resize(&handle.pane) {
        return Err((
            "IO_FAILED",
            "fault: second size step refused".into(),
            json!({"effect":"partial","steps":1}),
        ));
    }
    match handle.pty.resize(rows, cols) {
        Ok(()) => Ok(json!({"applied":true,"steps":steps + 1})),
        Err(error) if closed(&error) && steps == 0 => Ok(json!({"applied":false,"steps":0})),
        Err(error) => Err((
            "IO_FAILED",
            error.to_string(),
            json!({"effect":if steps > 0 {"partial"} else {"none"},"steps":steps}),
        )),
    }
}

fn dispatch(req: Request, sessions: &Sessions, wire: &Arc<Wire>) {
    let reply = |meta: Value| wire.out.respond(req.code, req.id, meta);
    let refuse = |message: &str| {
        let (error_code, stage) = classify(req.code, message);
        wire.out
            .error(req.code, req.id, error_code, stage, message, "none");
    };
    match req.code {
        code::CREATE => unreachable!("creates run on their own thread"),
        code::ADOPT => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                handle.adopted.store(true, Ordering::SeqCst);
                reply(json!({"adopted":true}));
            }
            Err(error) => refuse(&error.to_string()),
        },
        #[cfg(feature = "fault-hooks")]
        code::RESIZE
            if session_of(sessions, &req.meta)
                .is_ok_and(|h| fault::resize_hold(&h.pane).is_some()) =>
        {
            // Harness build only: the answer waits until a release file exists (T6), on its own thread.
            let handle = session_of(sessions, &req.meta).expect("checked above");
            let path = fault::resize_hold(&handle.pane).expect("checked above");
            let wire = wire.clone();
            thread::spawn(move || {
                fault::wait_for_file(&path);
                match resize_session(&handle, &req.meta) {
                    Ok(meta) => wire.out.respond(req.code, req.id, meta),
                    Err((error_code, message, extra)) => {
                        let meta = json!({"code":error_code,"stage":"io","message":message,
                            "effect":extra["effect"].as_str().unwrap_or("none")});
                        wire.out.error_with(req.code, req.id, meta);
                    }
                }
            });
        }
        code::RESIZE => match session_of(sessions, &req.meta) {
            Ok(handle) => match resize_session(&handle, &req.meta) {
                Ok(meta) => reply(meta),
                Err((error_code, message, extra)) => {
                    let effect = extra["effect"].as_str().unwrap_or("none");
                    let mut meta = json!({"code":error_code,"stage":"io",
                        "message":message.chars().take(1024).collect::<String>(),"effect":effect});
                    if let Some(steps) = extra.get("steps") {
                        meta["steps"] = steps.clone();
                    }
                    wire.out.error_with(req.code, req.id, meta);
                }
            },
            Err(error) => refuse(&error.to_string()),
        },
        code::KILL => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                // Acknowledged before it acts; the outcome arrives only as EXITED / CLEANED.
                reply(json!({"accepted":true}));
                handle.send(Msg::Kill(req.meta["force"].as_bool().unwrap_or(false)));
            }
            Err(error) => refuse(&error.to_string()),
        },
        code::TTY_EOF => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                reply(json!({"accepted":true}));
                handle.send(Msg::TtyEof);
            }
            Err(error)
                if error.to_string() == "UNKNOWN_SESSION"
                    && evicted_session(sessions, &req.meta) =>
            {
                // The session settled long ago and left the registry: the late EOF report is idempotent (5.5 item 6).
                reply(json!({"accepted":true}));
            }
            Err(error) => refuse(&error.to_string()),
        },
        code::CANCEL => {
            let Some(target) = req.meta["target_id"].as_u64() else {
                return refuse("invalid target_id");
            };
            // One lock: the verdict and the state it describes cannot diverge.
            let mut registry = sessions.lock().unwrap();
            let verdict = match registry.cancel_verdict(target) {
                Ok(verdict) => verdict,
                Err(refusal) => {
                    drop(registry);
                    return refuse(refusal);
                }
            };
            let handle = match verdict {
                CancelVerdict::Prevented => return reply(json!({"state":"prevented"})),
                CancelVerdict::Released => return reply(json!({"state":"released"})),
                CancelVerdict::Unknown => return reply(json!({"state":"unknown"})),
                CancelVerdict::Registered(sid) => registry.sessions.get(&sid).cloned(),
            };
            drop(registry);
            let Some(handle) = handle else {
                return reply(json!({"state":"unknown"}));
            };
            if handle.adopted.load(Ordering::SeqCst) || handle.finished() {
                return reply(json!({"state":"settled"}));
            }
            handle.send(Msg::Kill(true));
            reply(json!({"state":"cleanup-started"}));
        }
        code::PING => {
            let registry = sessions.lock().unwrap();
            reply(
                json!({"pong":true,"bookkeeping":{"last_request":registry.last_request,
                "live_sessions":registry.sessions.len().saturating_sub(registry.settled.len()),
                "settled_sessions":registry.settled.len(),"session_workers":registry.creating,
                "inbound_bytes":wire.inbound.snapshot().0,"inbound_peak_bytes":wire.inbound.snapshot().1}}),
            );
        }
        code::OBSERVE => {
            let sender = wire.observe_tx.lock().unwrap().clone();
            let admitted = sender.is_some_and(|sender| match sender.try_send(req) {
                Ok(()) => true,
                Err(mpsc::TrySendError::Full(req) | mpsc::TrySendError::Disconnected(req)) => {
                    let (error_code, stage) = ("BAD_REQUEST", "admit");
                    wire.out.error(
                        req.code,
                        req.id,
                        error_code,
                        stage,
                        "observation queue not admitting",
                        "none",
                    );
                    true
                }
            });
            if !admitted {
                wire.out.error(
                    code::OBSERVE,
                    0,
                    "BAD_REQUEST",
                    "admit",
                    "observation worker is not running",
                    "none",
                );
            }
        }
        _ => refuse("invalid code"),
    }
}

/// The ownership context an observation answer carries: the session's identity when the
/// observation is bound to a live session, nulls for a historical one.
fn observation_context(req: &Request, sessions: &Sessions) -> io::Result<Value> {
    let vendor = req.meta["vendor"].as_str().unwrap_or("");
    let Some(sid) = req.meta["session_id"].as_str() else {
        return Ok(
            json!({"event":"observed","session_id":null,"session_generation":null,
            "pane_id":null,"workspace_path":null}),
        );
    };
    let handle = sessions
        .lock()
        .unwrap()
        .sessions
        .get(sid)
        .cloned()
        .ok_or_else(|| io::Error::other("UNKNOWN_SESSION"))?;
    if req.meta["session_generation"] != handle.generation || vendor != handle.agent_key {
        return Err(io::Error::other("STALE_SESSION"));
    }
    let mut context = handle.meta();
    context["event"] = json!("observed");
    Ok(context)
}

/// One observation, computed completely before the first part is sent, so a refusal for size
/// sends no part at all (E1 14).
fn observe(req: &Request, sessions: &Sessions, wire: &Arc<Wire>) {
    let refuse = |message: &str| {
        let (error_code, stage) = classify(req.code, message);
        wire.out
            .error(req.code, req.id, error_code, stage, message, "none");
    };
    if !req.meta["vendor"]
        .as_str()
        .is_some_and(adapters::integrated)
    {
        return refuse("observer not integrated");
    }
    let context = match observation_context(req, sessions) {
        Ok(context) => context,
        Err(error) => return refuse(&error.to_string()),
    };
    #[cfg(feature = "fault-hooks")]
    let computed =
        fault::observation(req).unwrap_or_else(|| adapters::observe(&req.meta, &req.blob));
    #[cfg(not(feature = "fault-hooks"))]
    let computed = adapters::observe(&req.meta, &req.blob);
    let (mut document, anchor) = match computed {
        Ok(computed) => computed,
        Err(error) => return refuse(&error.to_string()),
    };
    document["ownership"] = context;
    let bytes = serde_json::to_vec(&document).expect("a JSON value is serialisable");
    let parts = bytes.len().div_ceil(OBSERVATION_CHUNK);
    if bytes.len() > MAX_OBSERVATION_BYTES || parts > MAX_OBSERVATION_PARTS {
        return wire.out.error(
            req.code,
            req.id,
            "OBSERVATION_TOO_LARGE",
            "io",
            &format!(
                "result of {} bytes exceeds the observation limits",
                bytes.len()
            ),
            "none",
        );
    }
    #[cfg(feature = "fault-hooks")]
    if fault::endless_parts(req, wire) {
        return;
    }
    for (part, chunk) in bytes.chunks(OBSERVATION_CHUNK).enumerate() {
        wire.out.event_for(
            code::OBSERVATION_PART,
            req.id,
            json!({ "part": part }),
            chunk.to_vec(),
        );
    }
    if let Some(message) = document["error"].as_str() {
        // The inherited activity "error with progress" shape: the document carries the progress.
        let meta = json!({"code":"IO_FAILED","stage":"io","effect":"partial",
            "message":message.chars().take(1024).collect::<String>(),"observation_parts":parts});
        return wire.out.error_with(req.code, req.id, meta);
    }
    wire.out.respond_with_blob(
        req.code,
        req.id,
        json!({"observation_parts":parts,"raw_anchor":anchor.is_some()}),
        anchor.unwrap_or_default(),
    );
}

// ---- startup and the main loop -------------------------------------------------------------------

struct Config {
    wire: u64,
    generation: String,
    owner_pid: u32,
    journal: std::path::PathBuf,
    control_fd: i32,
    #[cfg(feature = "fault-hooks")]
    fail_startup: bool,
    #[cfg(feature = "fault-hooks")]
    ready_wire: Option<u64>,
    #[cfg(feature = "fault-hooks")]
    ready_protocol: Option<String>,
}

fn parse_args(args: &[String]) -> Result<Config, (&'static str, String)> {
    let startup = |message: String| ("STARTUP_FAILED", message);
    let mut wire = None;
    let mut generation = None;
    let mut owner_pid = None;
    let mut journal = None;
    let mut control_fd = None;
    #[cfg(feature = "fault-hooks")]
    let (mut fail_startup, mut ready_wire, mut ready_protocol) = (false, None, None);
    let mut iter = args.iter();
    while let Some(option) = iter.next() {
        let mut value = |name: &str| {
            iter.next()
                .cloned()
                .ok_or_else(|| startup(format!("{name} needs a value")))
        };
        match option.as_str() {
            "--wire" => wire = Some(value("--wire")?),
            "--runtime-generation" => generation = Some(value("--runtime-generation")?),
            "--owner-pid" => owner_pid = Some(value("--owner-pid")?),
            "--ownership-journal" => journal = Some(value("--ownership-journal")?),
            "--control-fd" => control_fd = Some(value("--control-fd")?),
            #[cfg(feature = "fault-hooks")]
            "--fail-startup" => fail_startup = true,
            #[cfg(feature = "fault-hooks")]
            "--ready-wire" => ready_wire = value("--ready-wire")?.parse().ok(),
            #[cfg(feature = "fault-hooks")]
            "--ready-protocol" => ready_protocol = Some(value("--ready-protocol")?),
            other => return Err(startup(format!("unknown argument {other}"))),
        }
    }
    let number = |text: Option<String>, name: &str| {
        text.and_then(|text| text.parse::<u64>().ok())
            .ok_or_else(|| startup(format!("{name} required")))
    };
    let wire = number(wire, "--wire")?;
    if wire != u64::from(frame::WIRE_VERSION) {
        return Err(("INCOMPATIBLE_WIRE", format!("wire {wire} is not supported")));
    }
    let generation = generation.ok_or_else(|| startup("--runtime-generation required".into()))?;
    if frame::runtime_tag(&generation).is_none() {
        return Err(startup(
            "--runtime-generation must be 32 hex characters".into(),
        ));
    }
    Ok(Config {
        wire,
        generation,
        owner_pid: number(owner_pid, "--owner-pid")? as u32,
        journal: journal
            .map(std::path::PathBuf::from)
            .ok_or_else(|| startup("--ownership-journal required".into()))?,
        control_fd: number(control_fd, "--control-fd")? as i32,
        #[cfg(feature = "fault-hooks")]
        fail_startup,
        #[cfg(feature = "fault-hooks")]
        ready_wire,
        #[cfg(feature = "fault-hooks")]
        ready_protocol,
    })
}

fn stdout_line(value: &Value) -> io::Result<()> {
    let mut stdout = io::stdout().lock();
    stdout.write_all(&serde_json::to_vec(value)?)?;
    stdout.write_all(b"\n")?;
    stdout.flush()
}

fn fatal_line(error_code: &str, message: &str) {
    let _ = stdout_line(&json!({"event":"fatal","protocol":"navide-acr","wire":1,
        "error":{"code":error_code,"message":message,"stage":"initialize"}}));
}

fn limits() -> Value {
    json!({"max_meta_p2r":frame::MAX_META_P2R,"max_blob_p2r":frame::MAX_BLOB_P2R,
        "max_frame_r2p":frame::MAX_FRAME_R2P,"max_observation_bytes":8_388_608,
        "max_observation_parts":128,"observe_inflight":4,
        "max_outbound_bytes":crate::wire::MAX_OUTBOUND_BYTES,
        "max_outbound_frames":crate::wire::MAX_OUTBOUND_FRAMES,"max_inbound_bytes":16_777_216,
        "request_ms":REQUEST_MS,"create_answer_ms":CREATE_ANSWER_MS,"cleanup_ms":cleanup_ms(),
        "ping_ms":PING_MS,"kill_escalation_ms":KILL_ESCALATION_MS,"eof_reap_ms":EOF_REAP_MS,
        "ownership_snapshot_ms":{"young":SNAPSHOT_YOUNG_MS,"steady":SNAPSHOT_STEADY_MS,
            "young_window_ms":SNAPSHOT_YOUNG_WINDOW_MS},
        "snapshot_ms":SNAPSHOT_MS})
}

fn ready_line(config: &Config) -> Value {
    #[allow(unused_mut)]
    let mut ready = json!({"event":"ready","protocol":"navide-acr","wire":config.wire,
        "version":env!("CARGO_PKG_VERSION"),"runtime":"navide-cli-runtime",
        "runtime_generation":config.generation,"pid":std::process::id(),"owner_pid":config.owner_pid,
        "platform":std::env::consts::OS,"arch":std::env::consts::ARCH,"pty_backend":"posix",
        "handoff":"scm_rights","ownership_gate":true,"limits":limits()});
    #[cfg(feature = "fault-hooks")]
    {
        if let Some(wire) = config.ready_wire {
            ready["wire"] = json!(wire);
        }
        if let Some(protocol) = &config.ready_protocol {
            ready["protocol"] = serde_json::from_str(protocol).unwrap_or(json!(protocol));
        }
    }
    #[cfg(target_os = "macos")]
    match darwin::self_process() {
        Ok(observation) => ready["self_process"] = observation,
        Err(error) => {
            // Observation failure is private evidence, not a new startup policy.
            ready["self_process_error"] =
                json!({"operation":"darwin-self-process","errno":error.raw_os_error()});
        }
    }
    ready
}

/// Blocking read of exactly `buffer.len()` bytes from the control socket. Returns the number
/// of bytes read, fewer only at end of file.
fn read_full(fd: i32, buffer: &mut [u8]) -> io::Result<usize> {
    let mut got = 0;
    while got < buffer.len() {
        // SAFETY: valid descriptor and in-bounds buffer slice.
        let n = unsafe { libc::read(fd, buffer[got..].as_mut_ptr().cast(), buffer.len() - got) };
        if n < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error);
        }
        if n == 0 {
            break;
        }
        got += n as usize;
    }
    Ok(got)
}

enum Ended {
    /// The backend closed the channel (it died, or this side failed a send).
    PeerEof,
    Shutdown(u64),
    Protocol(String),
}

pub fn run() -> io::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let config = match parse_args(&args) {
        Ok(config) => config,
        Err((error_code, message)) => {
            fatal_line(error_code, &message);
            return Err(io::Error::other(error_code));
        }
    };
    #[cfg(feature = "fault-hooks")]
    if config.fail_startup {
        fatal_line("STARTUP_FAILED", "requested isolated startup fault");
        return Err(io::Error::other("STARTUP_FAILED"));
    }
    let fd = config.control_fd;
    // SAFETY: the descriptor was passed by the owner (`pass_fds`); flags only.
    if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
        fatal_line("STARTUP_FAILED", "control descriptor is not open");
        return Err(io::Error::other("STARTUP_FAILED"));
    }
    // A PTY child must never inherit the control channel (it could pass descriptors for the
    // whole backend); FD_CLOEXEC above, and a closed peer is EPIPE here, never a signal.
    #[cfg(target_os = "macos")]
    {
        let on: libc::c_int = 1;
        // SAFETY: valid socket and a 4-byte option value.
        unsafe {
            libc::setsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_NOSIGPIPE,
                (&on as *const i32).cast(),
                4,
            );
        }
    }
    let tag = frame::runtime_tag(&config.generation).expect("validated while parsing");
    stdout_line(&ready_line(&config))?;
    let wire = Arc::new(Wire {
        generation: config.generation.clone(),
        journal: config.journal.clone(),
        owner_pid: config.owner_pid,
        out: Outbound::new(tag),
        observe_tx: Mutex::new(None),
        inbound: InboundBudget::new(MAX_INBOUND_BYTES),
    });
    let (observe_tx, observe_rx) = mpsc::sync_channel::<Request>(OBSERVATION_QUEUE);
    *wire.observe_tx.lock().unwrap() = Some(observe_tx);
    let writer_wire = wire.clone();
    let writer = thread::Builder::new()
        .name("control-writer".into())
        .spawn(move || {
            let outcome = writer_wire.out.run_writer(&mut SocketSender(fd));
            if outcome.is_err() {
                writer_wire.inbound.close();
                // Nothing can be said to Python any more: wake the reader as if the peer had closed.
                // SAFETY: shutdown on the control socket; the reader then sees end of file.
                unsafe {
                    libc::shutdown(fd, libc::SHUT_RDWR);
                }
            }
            outcome
        })?;
    let sessions: Sessions = Arc::new(Mutex::new(Registry::default()));
    let observer = {
        let (observe_sessions, observe_wire) = (sessions.clone(), wire.clone());
        thread::Builder::new()
            .name("observe".into())
            .spawn(move || {
                while let Ok(request) = observe_rx.recv() {
                    observe(&request, &observe_sessions, &observe_wire);
                }
            })?
    };
    let ended = loop {
        let mut header = [0u8; frame::HEADER_LEN];
        match read_full(fd, &mut header) {
            Ok(0) | Err(_) => break Ended::PeerEof,
            Ok(n) if n < header.len() => break Ended::PeerEof,
            Ok(_) => {}
        }
        let header = match frame::parse_header(&header, Direction::PythonToRust, wire.out.tag()) {
            Ok(header) => header,
            Err(violation) => {
                break Ended::Protocol(format!("{}: invalid header", violation.name()))
            }
        };
        let Some(reservation) = wire.inbound.acquire(frame::HEADER_LEN + header.body_len()) else {
            break Ended::PeerEof;
        };
        let mut body = vec![0u8; header.body_len()];
        match read_full(fd, &mut body) {
            Ok(n) if n == body.len() => {}
            _ => break Ended::PeerEof,
        }
        let frame: Frame = match frame::decode_body(header, &body) {
            Ok(frame) => frame,
            Err(violation) => break Ended::Protocol(format!("{}: invalid body", violation.name())),
        };
        let request = Request {
            id: frame.correlation_id,
            code: frame.code,
            meta: frame.meta,
            blob: frame.blob,
            reservation: Some(reservation),
        };
        {
            let mut registry = sessions.lock().unwrap();
            if request.id <= registry.last_request {
                drop(registry);
                break Ended::Protocol("correlation: non-increasing request id".into());
            }
            registry.last_request = request.id;
        }
        if request.code == code::SHUTDOWN {
            break Ended::Shutdown(request.id);
        }
        if request.code == code::CREATE {
            // The spawn blocks on the ownership gate: its own thread, never the reader.
            {
                let mut registry = sessions.lock().unwrap();
                registry.note_create(request.id);
                registry.creating += 1;
            }
            let (create_sessions, create_wire) = (sessions.clone(), wire.clone());
            let spawned = thread::Builder::new().name("create".into()).spawn(move || {
                match create(&request, &create_sessions, &create_wire) {
                    Ok(meta) => create_wire.out.respond(request.code, request.id, meta),
                    #[cfg(feature = "fault-hooks")]
                    Err(error) if error.to_string() == fault::SUPPRESSED => {}
                    Err(error) => {
                        let message = error.to_string();
                        let (error_code, stage) = classify(request.code, &message);
                        let effect = if error_code == "CLEANUP_FAILED" {
                            "partial"
                        } else {
                            "none"
                        };
                        create_wire.out.error(
                            request.code,
                            request.id,
                            error_code,
                            stage,
                            &message,
                            effect,
                        );
                    }
                }
                create_sessions.lock().unwrap().creating -= 1;
                CREATES_CHANGED.notify_all();
            });
            if spawned.is_err() {
                sessions.lock().unwrap().creating -= 1;
                break Ended::Protocol("create worker could not start".into());
            }
        } else {
            dispatch(request, &sessions, &wire);
        }
    };
    if let Ended::Protocol(message) = &ended {
        wire.out.event(
            code::FATAL,
            json!({"code":"PROTOCOL_ERROR","stage":"validate","message":message}),
        );
    }
    // Teardown: in-flight creates settle, then every session ends by the (non-forced) kill
    // policy, each wait a condition with the one overall `cleanup_ms` bound.
    let deadline = Instant::now() + Duration::from_millis(cleanup_ms());
    {
        let mut registry = sessions.lock().unwrap();
        while registry.creating > 0 {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            registry = CREATES_CHANGED.wait_timeout(registry, left).unwrap().0;
        }
    }
    let handles: Vec<Arc<SessionHandle>> = sessions
        .lock()
        .unwrap()
        .sessions
        .values()
        .cloned()
        .collect();
    for handle in &handles {
        if !handle.finished() {
            handle.send(Msg::Shutdown);
        }
    }
    let mut clean = true;
    for handle in &handles {
        let left = deadline.saturating_duration_since(Instant::now());
        clean &= handle.wait_finished(left) == Some(true);
    }
    if let Ended::Shutdown(id) = &ended {
        let survivors: Vec<Value> = handles
            .iter()
            .flat_map(|h| h.survivors.lock().unwrap().clone())
            .collect();
        if clean {
            wire.out.respond(
                code::SHUTDOWN,
                *id,
                json!({"cleanup_complete":true,"survivors":[]}),
            );
        } else {
            wire.out.error_with(
                code::SHUTDOWN,
                *id,
                json!({"code":"CLEANUP_FAILED","stage":"shutdown",
                "message":"incomplete runtime cleanup","effect":"partial","survivors":survivors}),
            );
        }
    }
    // Admitted observations finish (they are bounded file reads); then the queue closes.
    wire.observe_tx.lock().unwrap().take();
    let _ = observer.join();
    wire.out.finish();
    let written = writer
        .join()
        .map_err(|_| io::Error::other("control writer panic"))?;
    if !clean {
        return Err(io::Error::other("incomplete cleanup"));
    }
    if let Ended::Protocol(message) = ended {
        return Err(io::Error::other(message));
    }
    if matches!(ended, Ended::Shutdown(_)) {
        written?;
    }
    Ok(())
}

#[cfg(test)]
mod survivor_tests {
    #[allow(unused_imports)]
    use super::*;

    #[cfg(target_os = "macos")]
    /// A real zombie: a signalled child that has exited while its parent (this test) has not reaped it yet. Its identity
    /// is taken while it still runs; afterwards `darwin::row` no longer lists it, but `kill(pid, 0)` still reaches it.
    fn zombie() -> (std::process::Child, i32, (i32, i32, String)) {
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let pid = child.id() as i32;
        let entry = darwin::snapshot(Some(pid), &Table::new())
            .unwrap()
            .remove(&pid)
            .unwrap();
        child.kill().unwrap(); // SIGKILL; not reaped
        let still = darwin::wait_exit(&[pid], Instant::now() + Duration::from_secs(5));
        assert!(still.is_empty(), "the child must have exited");
        // SAFETY: signal 0 only checks existence.
        assert_eq!(
            unsafe { libc::kill(pid, 0) },
            0,
            "an unreaped zombie is still reachable"
        );
        (child, pid, entry)
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn a_killed_descendant_that_only_awaits_its_reap_is_not_a_survivor() {
        let (mut child, pid, entry) = zombie();
        let mut descendants = Table::new();
        descendants.insert(pid, entry.clone());
        // The snapshot no longer lists the zombie; its exit was observed (`still_running` is empty).
        let survivors = survivors_after_cleanup(&descendants, &Table::new(), &[(pid, entry)], &[]);
        assert!(
            survivors.is_empty(),
            "a zombie was reported as a survivor: {survivors:?}"
        );
        child.wait().unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn a_descendant_whose_exit_was_not_observed_is_a_survivor_and_so_is_an_unsignalled_one() {
        let mut keep = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let pid = keep.id() as i32;
        let entry = darwin::snapshot(Some(pid), &Table::new())
            .unwrap()
            .remove(&pid)
            .unwrap();
        let mut descendants = Table::new();
        descendants.insert(pid, entry.clone());
        let final_table: Table = [(pid, entry.clone())].into_iter().collect();
        let signalled = [(pid, entry)];
        assert_eq!(
            survivors_after_cleanup(&descendants, &final_table, &signalled, &[pid]).len(),
            1
        );
        assert_eq!(
            survivors_after_cleanup(&descendants, &final_table, &[], &[]).len(),
            1
        );
        keep.kill().unwrap();
        keep.wait().unwrap();
    }
}

#[cfg(test)]
mod inbound_budget_tests {
    use super::*;

    /// E1 1.4: the sum of live reservations never exceeds the cap, a request that does not fit waits (is
    /// not dropped), and it proceeds as soon as earlier ones are dropped.
    #[test]
    fn reservations_never_exceed_the_cap_and_waiters_proceed_in_order_of_release() {
        let budget = InboundBudget::new(1000);
        let first = budget.acquire(600).unwrap();
        let second = budget.acquire(300).unwrap();
        let waiting = {
            let budget = budget.clone();
            thread::spawn(move || budget.acquire(400).map(|r| r.size))
        };
        thread::sleep(Duration::from_millis(100));
        assert!(
            !waiting.is_finished(),
            "400 more bytes do not fit beside 900"
        );
        assert_eq!(budget.snapshot().0, 900);
        drop(first);
        assert_eq!(waiting.join().unwrap(), Some(400));
        drop(second);
        let (used, peak) = budget.snapshot();
        assert_eq!(used, 0);
        assert!(peak <= 1000, "peak {peak}");
    }

    #[test]
    fn an_oversized_request_is_admitted_alone_and_closing_wakes_waiters() {
        let budget = InboundBudget::new(100);
        let big = budget.acquire(500).unwrap();
        let blocked = {
            let budget = budget.clone();
            thread::spawn(move || budget.acquire(10).is_none())
        };
        thread::sleep(Duration::from_millis(50));
        budget.close();
        assert!(
            blocked.join().unwrap(),
            "a closed budget refuses instead of blocking forever"
        );
        drop(big);
    }
}

#[cfg(test)]
mod ownership_journal_tests {
    use super::*;

    #[test]
    fn appended_records_are_whole_lines_that_survive_reopening() {
        let dir = std::env::temp_dir().join(format!("navide-journal-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("g.jsonl");
        let _ = std::fs::remove_file(&path);
        journal_append(&path, &json!({"journal":1,"pid":10,"start_value":"a"})).unwrap();
        journal_append(&path, &json!({"journal":1,"pid":11,"start_value":"b"})).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<Value> = text
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[1]["pid"], 11);
        assert!(text.ends_with('\n'));
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn an_unwritable_journal_is_an_error_not_silence() {
        let missing = std::path::Path::new("/nonexistent-navide-dir/g.jsonl");
        assert!(journal_append(missing, &json!({"journal":1})).is_err());
    }
}

#[cfg(test)]
mod cancel_state_tests {
    use super::*;

    fn queued(gated: bool) -> Registry {
        // As the stdin loop does: the request counter is recorded before the create is routed.
        let mut registry = Registry {
            last_request: 7,
            ..Registry::default()
        };
        registry.note_create(7);
        registry.admit_create(7, gated).unwrap();
        registry
    }

    #[test]
    fn a_cancel_before_release_is_prevented_and_the_gate_then_refuses() {
        let mut registry = queued(true);
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        assert!(
            !registry.release_gate(7),
            "the gate must refuse a prevented create"
        );
    }

    #[test]
    fn a_cancel_between_release_and_spawn_return_is_released() {
        let mut registry = queued(true);
        assert!(registry.release_gate(7));
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Released));
    }

    #[test]
    fn a_cancel_after_spawn_returned_but_before_registration_is_never_prevented() {
        // N1 window 1: the gate opened, `spawn` returned, the session is not yet published.
        let mut registry = queued(true);
        assert!(registry.release_gate(7));
        registry.spawn_returned(7);
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Released));
        assert!(
            registry.register_create(7, "s7"),
            "the late cancel must close the session"
        );
    }

    #[test]
    fn a_cancel_after_registration_names_the_session_and_is_never_prevented() {
        // N1 window 2: the session was published between the cancel's lookup and its verdict;
        // with one critical section the verdict itself must see the publication.
        let mut registry = queued(true);
        assert!(registry.release_gate(7));
        registry.spawn_returned(7);
        assert!(!registry.register_create(7, "s7"));
        assert_eq!(
            registry.cancel_verdict(7),
            Ok(CancelVerdict::Registered("s7".into()))
        );
    }

    #[test]
    fn an_ungated_create_is_released_from_admission_on() {
        let mut registry = queued(false);
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Released));
    }

    #[test]
    fn a_cancel_for_a_finished_create_is_unknown_not_prevented() {
        let mut registry = queued(true);
        assert!(registry.release_gate(7));
        registry.spawn_returned(7);
        registry.register_create(7, "s7");
        registry.evict_create("s7");
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Unknown));
        let mut failed = queued(true);
        failed.create_failed(7);
        assert_eq!(failed.cancel_verdict(7), Ok(CancelVerdict::Unknown));
    }

    #[test]
    fn a_cancel_that_wins_before_admission_refuses_the_create() {
        let mut registry = Registry {
            last_request: 7,
            ..Registry::default()
        };
        registry.note_create(7);
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        assert_eq!(registry.admit_create(7, true), Err("CREATE_CANCELLED"));
    }

    #[test]
    fn a_cancel_for_a_create_not_read_yet_is_refused_at_admission() {
        let mut registry = Registry {
            last_request: 6,
            ..Registry::default()
        };
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        registry.last_request = 7;
        registry.note_create(7);
        assert_eq!(registry.admit_create(7, true), Err("CREATE_CANCELLED"));
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;

    #[test]
    fn cleanup_ms_is_derived_exactly_like_the_baseline() {
        // largest declared graceful grace (Claude, 3000) + escalation (1000) + confirm (1000) + slack (3000)
        assert_eq!(cleanup_ms(), 8000);
    }

    #[test]
    fn the_shutdown_declaration_is_read_through_the_adapter_roster() {
        assert!(adapters::shutdown("claude")
            .is_some_and(|spec| spec.graceful && spec.defer_master_close));
        assert_eq!(adapters::shutdown(""), None);
        assert_eq!(adapters::shutdown("terminal"), None);
        assert_eq!(adapters::shutdown("codex"), None);
    }

    #[test]
    fn exit_cell_wakes_a_waiter_on_the_event_and_times_out_otherwise() {
        let cell = Arc::new(ExitCell::new());
        assert!(cell.wait(Duration::from_millis(20)).is_none());
        let waiter = {
            let cell = cell.clone();
            thread::spawn(move || cell.wait(Duration::from_secs(10)))
        };
        thread::sleep(Duration::from_millis(30));
        let started = Instant::now();
        cell.set(ExitInfo {
            code: Some(3),
            signal: None,
        });
        assert_eq!(waiter.join().unwrap().and_then(|info| info.code), Some(3));
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "woken by the event, not by the bound"
        );
    }

    #[test]
    fn the_worst_case_owned_frame_is_refused_before_any_process_exists() {
        let args = |workspace: String| CreateArgs {
            id: "00000000-0000-0000-0000-000000000000".into(),
            generation: "0".repeat(32),
            pane: "pane".into(),
            workspace,
            cwd: "/".into(),
            agent: String::new(),
            argv: vec!["x".into()],
            rows: 24,
            cols: 80,
        };
        assert!(handoff_size_ok(1, &args("/tmp/ordinary".into())));
        assert!(!handoff_size_ok(1, &args("/x".repeat(2100))));
    }

    #[test]
    fn unknown_and_missing_arguments_fail_startup_and_a_wrong_wire_is_incompatible() {
        let ok = |extra: &[&str]| {
            let mut args: Vec<String> = [
                "--wire",
                "1",
                "--runtime-generation",
                "00112233445566778899aabbccddeeff",
                "--owner-pid",
                "1",
                "--ownership-journal",
                "/tmp/j",
                "--control-fd",
                "3",
            ]
            .map(String::from)
            .to_vec();
            args.extend(extra.iter().map(|s| s.to_string()));
            parse_args(&args)
        };
        assert!(ok(&[]).is_ok());
        assert_eq!(ok(&["--bogus"]).err().map(|e| e.0), Some("STARTUP_FAILED"));
        let mut wrong = vec!["--wire", "2"];
        wrong.extend([
            "--runtime-generation",
            "00112233445566778899aabbccddeeff",
            "--owner-pid",
            "1",
            "--ownership-journal",
            "/tmp/j",
            "--control-fd",
            "3",
        ]);
        let args: Vec<String> = wrong.iter().map(|s| s.to_string()).collect();
        assert_eq!(
            parse_args(&args).err().map(|e| e.0),
            Some("INCOMPATIBLE_WIRE")
        );
        assert_eq!(
            parse_args(&["--wire".to_string(), "1".to_string()])
                .err()
                .map(|e| e.0),
            Some("STARTUP_FAILED")
        );
    }
}
