//! The Windows sidecar: spawn and ownership of ConPTY-backed roots, the control channel and the
//! lifecycle (design/e1-runtime-protocol-v8b.md 5, 7).
//!
//! The same contract as `posix.rs`, with the Windows mechanics of section 7: the control channel is
//! the sidecar's stdin and stdout, a root is created `CREATE_SUSPENDED` in a kill-on-close job and
//! resumed only after its identity is durable, `SESSION_OWNED` names two caller-side pipe ends that
//! Python pulls with `DuplicateHandle`, and `ClosePseudoConsole` runs on its own thread only after
//! the pre-close gate. Rust never reads or writes a PTY byte.
//!
//! The request/registry bookkeeping mirrors `posix.rs` on purpose: that file stays untouched for
//! macOS and Linux, and the shared part is a follow-up extraction once both platforms are proven.
use crate::frame::{self, code, Direction, Frame};
use crate::session_pty_win::{CloseGate, GateReason, Identity, SessionPty};
use crate::wire::Outbound;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::io::{self, Read, Write};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr::null_mut;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{DuplicateHandle, DUPLICATE_SAME_ACCESS};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetCurrentThread};
use windows_sys::Win32::System::IO::CancelSynchronousIo;

#[path = "adapters.rs"]
mod adapters;
#[path = "claude_facts.rs"]
mod claude_facts;

// ---- named bounds (E1 13.1: every bound has a named source) -------------------------------
/// Bound for observing the root's exit status after the job was terminated.
const ROOT_REAP: Duration = Duration::from_secs(1);
const KILL_ESCALATION_MS: u64 = 1000;
const EOF_REAP_MS: u64 = 1000;
const REAP_CONFIRM_MS: u64 = 1000;
const REAP_SLACK_MS: u64 = 3000;
const REQUEST_MS: u64 = 15_000;
const CREATE_ANSWER_MS: u64 = 20_000;
const PING_MS: u64 = 5000;
const SNAPSHOT_YOUNG_MS: u64 = 5000;
const SNAPSHOT_STEADY_MS: u64 = 30_000;
const SNAPSHOT_YOUNG_WINDOW_MS: u64 = 30_000;
const SNAPSHOT_MS: u64 = 5000;
const MAX_INBOUND_BYTES: usize = 16 * 1024 * 1024;
/// Settled sessions remembered by id and generation after they leave the registry (E1 v8 5.5).
const TOMBSTONES: usize = 4096;
/// An event wait on the root's process handle is re-armed at this period (it ends on the exit).
const ROOT_WAIT_SLICE: Duration = Duration::from_secs(3600);

/// `limits.cleanup_ms`, derived exactly as the baseline's `_reap_wait_timeout_s()`.
fn cleanup_ms() -> u64 {
    adapters::max_grace_ms() + KILL_ESCALATION_MS + REAP_CONFIRM_MS + REAP_SLACK_MS
}

type Sessions = Arc<Mutex<Registry>>;
static CREATES_CHANGED: Condvar = Condvar::new();

struct Request {
    id: u64,
    code: u16,
    meta: Value,
    #[allow(dead_code)] // held for its Drop
    reservation: Option<Reservation>,
}

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
    inbound: Arc<InboundBudget>,
}

// ---- the per-session handle -----------------------------------------------------------------

enum Msg {
    Kill(bool),
    TtyEof,
    /// Python's pump is servicing the output: the pre-close gate is open (7.3).
    Servicing,
    Shutdown,
    Exited,
}

#[derive(Clone, Copy)]
struct ExitInfo {
    code: Option<u32>,
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
    /// Block until the root has exited or `limit` elapsed.
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
    pty: Arc<SessionPty>,
    /// The pre-close gate of 7.3: opened by `TTY_SERVICING`, a `TTY_EOF`, or Rust closing its originals.
    gate: CloseGate,
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
    root: Identity,
    descendants: HashMap<u32, Identity>,
}

#[derive(Default)]
struct Registry {
    sessions: HashMap<String, Arc<SessionHandle>>,
    reserved: std::collections::HashSet<String>,
    settled: VecDeque<String>,
    tombstones: HashMap<String, String>,
    tombstone_order: VecDeque<String>,
    creates: HashMap<u64, CreatePhase>,
    last_request: u64,
    creating: usize,
}

#[derive(Debug, PartialEq)]
enum CancelVerdict {
    Prevented,
    Released,
    Registered(String),
    Unknown,
}

#[derive(Debug, PartialEq)]
enum CreatePhase {
    Queued,
    Cancelled,
    Released(bool),
    Registered(String),
}

const EARLY_CANCELS: usize = 256;

impl Registry {
    fn note_create(&mut self, request: u64) {
        self.creates.entry(request).or_insert(CreatePhase::Queued);
    }
    fn admit_create(&mut self, request: u64) -> Result<(), &'static str> {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return Err("CREATE_CANCELLED");
        }
        self.creates.insert(request, CreatePhase::Queued);
        Ok(())
    }
    /// Under the caller's lock, immediately before `ResumeThread`.
    fn release_gate(&mut self, request: u64) -> bool {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return false;
        }
        self.creates.insert(request, CreatePhase::Released(false));
        true
    }
    fn release_failed(&mut self, request: u64) {
        self.creates.insert(request, CreatePhase::Queued);
    }
    fn create_failed(&mut self, request: u64) {
        self.creates.remove(&request);
    }
    fn register_create(&mut self, request: u64, session_id: &str) -> bool {
        let cancel = matches!(
            self.creates.get(&request),
            Some(CreatePhase::Released(true))
        );
        self.creates
            .insert(request, CreatePhase::Registered(session_id.to_owned()));
        cancel
    }
    fn evict_create(&mut self, session_id: &str) {
        self.creates
            .retain(|_, phase| *phase != CreatePhase::Registered(session_id.to_owned()));
    }
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

/// Append one identity record and flush it (`FlushFileBuffers` through `sync_all`). The line is a
/// single `write`, so an abrupt death leaves either the whole line or none of it.
fn journal_append(path: &std::path::Path, record: &Value) -> io::Result<()> {
    let mut line = serde_json::to_vec(record).map_err(io::Error::other)?;
    line.push(b'\n');
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    file.write_all(&line)?;
    file.sync_all()
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

struct CreateArgs {
    id: String,
    generation: String,
    pane: String,
    workspace: String,
    cwd: String,
    argv: Vec<String>,
    env: Vec<(String, String)>,
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
    let mut env = Vec::new();
    for (key, value) in args["env"]
        .as_object()
        .ok_or_else(|| io::Error::other("invalid env"))?
    {
        if key.is_empty() || key.contains('\0') || key[1..].contains('=') {
            return Err(io::Error::other("invalid env key"));
        }
        let value = value
            .as_str()
            .filter(|s| !s.contains('\0'))
            .ok_or_else(|| io::Error::other("invalid env value"))?;
        env.push((key.clone(), value.to_owned()));
    }
    Ok(CreateArgs {
        id: text(args, "session_id")?.to_owned(),
        generation: text(args, "session_generation")?.to_owned(),
        pane: text(args, "pane_id")?.to_owned(),
        workspace: text(args, "workspace_path")?.to_owned(),
        cwd: text(args, "cwd")?.to_owned(),
        argv,
        env,
        rows: dimension(args, "rows")?,
        cols: dimension(args, "cols")?,
    })
}

/// The `SESSION_OWNED` meta (5.2): the `pty` part comes from the session value's one producer.
fn owned_meta(create: u64, handle: &SessionHandle, root: &Value) -> io::Result<Value> {
    Ok(
        json!({"create_id":create,"session_id":handle.id,"session_generation":handle.generation,
        "pane_id":handle.pane,"workspace_path":handle.workspace,"root":root,
        "pty":handle.pty.owned_record()?}),
    )
}

/// 5.1.3: before any PTY or process exists, the worst-case encoded `SESSION_OWNED` must fit the
/// 4 096-byte bound that the POSIX `HAS_FD` frame has, so both platforms refuse the same creates.
fn handoff_size_ok(create: u64, args: &CreateArgs) -> bool {
    let digits = u64::MAX.to_string();
    let root = json!({"pid":u32::MAX,"pgid":u32::MAX,"start_kind":crate::session_pty_win::START_KIND,
        "start_value":digits});
    let meta = json!({"create_id":create,"session_id":args.id,"session_generation":args.generation,
        "pane_id":args.pane,"workspace_path":args.workspace,"root":root,
        "pty":{"input":u64::MAX,"output":u64::MAX}});
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

/// Event-driven watch of the root's exit on its process handle (`WaitForSingleObject`).
fn watch_exit(pty: Arc<SessionPty>, cell: Arc<ExitCell>, tx: mpsc::Sender<Msg>) -> io::Result<()> {
    thread::Builder::new()
        .name("exit-watch".into())
        .spawn(move || {
            let info = loop {
                match pty.wait_root(ROOT_WAIT_SLICE) {
                    Ok(Some(code)) => break ExitInfo { code: Some(code) },
                    Ok(None) => continue,
                    Err(_) => break ExitInfo { code: None }, // status unknown
                }
            };
            cell.set(info);
            let _ = tx.send(Msg::Exited);
        })?;
    Ok(())
}

/// End a released (or never resumed) root and report whether its exit was observed.
fn kill_and_reap_released(pty: &SessionPty) -> bool {
    let _ = pty.terminate_tree();
    matches!(pty.wait_root(ROOT_REAP), Ok(Some(_)))
}

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
    let pty = SessionPty::open(parsed.rows, parsed.cols)?;
    let identity = pty.spawn_suspended(&parsed.argv, &parsed.env, &parsed.cwd)?;
    // From here the root exists but has executed nothing: any refusal ends it unrun.
    let refuse = |pty: &SessionPty, error: io::Error| -> io::Error {
        let _ = kill_and_reap_released(pty);
        error
    };
    let record = json!({"journal":1,"pid":identity.pid,"pgid":identity.pid,
        "start_kind":crate::session_pty_win::START_KIND,"start_value":identity.start.to_string(),
        "argv0":parsed.argv[0],"session_id":parsed.id,"session_generation":parsed.generation,
        "runtime_generation":wire.generation,"owner_pid":wire.owner_pid});
    if let Err(error) = journal_append(&wire.journal, &record) {
        return Err(refuse(
            &pty,
            io::Error::other(format!("OWNERSHIP_JOURNAL_FAILED: {}", error.kind() as u32)),
        ));
    }
    // The cancellation check and the release are one step under the registry lock: a cancel either
    // lands before it (the target never executes; "prevented") or after it ("released").
    {
        let mut registry = sessions.lock().unwrap_or_else(|e| e.into_inner());
        if !registry.release_gate(request) {
            drop(registry);
            return Err(refuse(&pty, io::Error::other("CREATE_CANCELLED")));
        }
        if let Err(error) = pty.resume() {
            registry.release_failed(request);
            drop(registry);
            return Err(refuse(
                &pty,
                io::Error::other(format!("OWNERSHIP_JOURNAL_FAILED: resume: {error}")),
            ));
        }
    }
    let pty = Arc::new(pty);
    let (tx, mailbox) = mpsc::channel();
    let exit = Arc::new(ExitCell::new());
    if let Err(error) = watch_exit(pty.clone(), exit.clone(), tx.clone()) {
        let verified = kill_and_reap_released(&pty);
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
        pty,
        gate: CloseGate::new(),
        tx,
        exit,
        done: (Mutex::new(None), Condvar::new()),
        adopted: AtomicBool::new(false),
        started: Instant::now(),
        survivors: Mutex::new(Vec::new()),
    });
    let owned = Owned {
        root: identity,
        descendants: HashMap::new(),
    };
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

/// What a cleanup established; `SESSION_CLEANED` is built from it.
#[derive(Default)]
struct Outcome {
    survivors: Vec<Value>,
    unverifiable: Vec<Value>,
    failure: Option<String>,
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
    meta["exit_code"] = json!(info.code);
    meta["signal"] = json!(null);
    meta["uptime_ms"] = json!(handle.started.elapsed().as_millis() as u64);
    wire.out.event(code::SESSION_EXITED, meta);
    state.exited_sent = true;
}

/// Remember every non-root job member (descendants are enumerated through the job, 7.3).
fn retain_members(handle: &SessionHandle, owned: &mut Owned) -> Vec<Identity> {
    let members = handle.pty.members().unwrap_or_default();
    for member in &members {
        if *member != owned.root {
            owned.descendants.insert(member.pid, member.clone());
        }
    }
    members
}

/// Kill the whole tree by identity and confirm the job is empty; what is left is a survivor.
fn end_tree(handle: &SessionHandle, owned: &mut Owned) -> io::Result<Vec<Value>> {
    retain_members(handle, owned);
    let _ = handle.pty.terminate_tree();
    let bound = Duration::from_millis(KILL_ESCALATION_MS + REAP_CONFIRM_MS);
    if handle.pty.wait_tree_empty(bound)? {
        return Ok(Vec::new());
    }
    Ok(handle.pty.members()?.iter().map(Identity::record).collect())
}

/// `ClosePseudoConsole` on its own thread, only after the pre-close gate (7.3), under `limit`.
fn close_console(handle: &SessionHandle, limit: Duration) -> Result<(), String> {
    let permit = handle
        .gate
        .wait(limit)
        .ok_or("pre-close gate not satisfied within cleanup_ms")?;
    let done = handle
        .pty
        .close_console(permit)
        .map_err(|error| format!("close thread: {error}"))?;
    done.recv_timeout(limit)
        .map_err(|_| "ClosePseudoConsole did not return within cleanup_ms".to_owned())
}

fn finish_close(handle: &SessionHandle, outcome: &mut Outcome) {
    if let Err(reason) = close_console(handle, Duration::from_millis(cleanup_ms())) {
        outcome.unverifiable.push(handle_root_record(handle));
        outcome.failure.get_or_insert(reason);
    }
}

fn handle_root_record(handle: &SessionHandle) -> Value {
    handle
        .pty
        .root_identity()
        .map(|identity| identity.record())
        .unwrap_or(Value::Null)
}

/// R-d on Windows: the root exited and the stream is serviced or closed; the remaining job members
/// go by identity and the console closes.
fn natural_cleanup(handle: &SessionHandle, state: &mut State) -> Outcome {
    let mut outcome = Outcome::default();
    match end_tree(handle, &mut state.owned) {
        Ok(survivors) => outcome.survivors = survivors,
        Err(error) => outcome.failure = Some(error.to_string()),
    }
    finish_close(handle, &mut outcome);
    outcome
}

/// R-e on Windows: no hangup step. A session that never adopted closes its originals (the third way
/// of 7.3 to open the gate); the tree is terminated through the job; `SESSION_EXITED` is sent; then
/// the console closes once Python is servicing or has closed its read end.
fn kill_cleanup(handle: &SessionHandle, state: &mut State, wire: &Wire) -> Outcome {
    let mut outcome = Outcome::default();
    if !handle.adopted.load(Ordering::SeqCst) && handle.pty.release_originals() {
        handle.gate.satisfy(GateReason::OriginalsClosed);
    }
    retain_members(handle, &mut state.owned);
    let _ = handle.pty.terminate_tree();
    if handle.exit.wait(ROOT_REAP).is_none() {
        outcome.failure = Some("root reap timeout".into());
        return outcome;
    }
    send_exited(handle, state, wire);
    match end_tree(handle, &mut state.owned) {
        Ok(survivors) => outcome.survivors = survivors,
        Err(error) => outcome.failure = Some(error.to_string()),
    }
    finish_close(handle, &mut outcome);
    outcome
}

/// `SESSION_CLEANED` and the end of the session thread's work (5.5).
fn finish(handle: &SessionHandle, wire: &Wire, sessions: &Sessions, outcome: Outcome) {
    let root_reaped = handle.exit.get().is_some();
    let complete = outcome.survivors.is_empty()
        && outcome.unverifiable.is_empty()
        && outcome.failure.is_none()
        && root_reaped;
    let mut survivors = outcome.survivors;
    if let Some(failure) = &outcome.failure {
        survivors.push(json!({"error":failure}));
    }
    let mut meta = handle.meta();
    meta["cleanup_complete"] = json!(complete);
    meta["root_reaped"] = json!(root_reaped);
    *handle.survivors.lock().unwrap() = survivors.clone();
    meta["survivors"] = json!(survivors);
    meta["unverifiable"] = json!(outcome.unverifiable);
    // The originals are Rust's until here at the latest (a session that adopted already closed them).
    handle.pty.release_originals();
    wire.out.event(code::SESSION_CLEANED, meta);
    *handle.done.0.lock().unwrap() = Some(complete);
    handle.done.1.notify_all();
    let mut registry = sessions.lock().unwrap();
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
    retain_members(handle, &mut state.owned);
    let all: Vec<_> = state.owned.descendants.values().collect();
    for (part, values) in all.chunks(128).enumerate() {
        let mut meta = handle.meta();
        meta["snapshot_seq"] = json!(handle.started.elapsed().as_millis() as u64);
        meta["part"] = json!(part);
        meta["final"] = json!((part + 1) * 128 >= all.len());
        meta["descendants"] = json!(values.iter().map(|d| d.record()).collect::<Vec<_>>());
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
            Ok(Msg::Exited | Msg::Servicing) => {
                send_exited(&handle, &mut state, &wire);
                // The console only closes once the root exited and Python services or closed the pipe.
                if handle.exit.get().is_some() && handle.gate.reason().is_some() {
                    let outcome = natural_cleanup(&handle, &mut state);
                    return finish(&handle, &wire, &sessions, outcome);
                }
            }
            Ok(Msg::Kill(force)) => {
                state.closing = Some(force);
                let outcome = kill_cleanup(&handle, &mut state, &wire);
                send_exited(&handle, &mut state, &wire);
                return finish(&handle, &wire, &sessions, outcome);
            }
            Ok(Msg::Shutdown) => {
                state.closing = Some(false);
                let outcome = kill_cleanup(&handle, &mut state, &wire);
                send_exited(&handle, &mut state, &wire);
                return finish(&handle, &wire, &sessions, outcome);
            }
            Ok(Msg::TtyEof) => {
                state.eof = true;
                if handle.exit.get().is_some() {
                    send_exited(&handle, &mut state, &wire);
                    let outcome = natural_cleanup(&handle, &mut state);
                    return finish(&handle, &wire, &sessions, outcome);
                }
                state.eof_deadline = Some(Instant::now() + Duration::from_millis(EOF_REAP_MS));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                if state.eof_deadline.is_some_and(|deadline| now >= deadline) {
                    state.eof_deadline = None;
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

// ---- requests ----------------------------------------------------------------------------------

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

/// Map an internal failure message to the wire error code and stage (10.4).
fn classify(request_code: u16, message: &str) -> (&'static str, &'static str) {
    let creating = request_code == code::CREATE;
    let code = match message {
        "STALE_SESSION" => "STALE_SESSION",
        "UNKNOWN_SESSION" => "UNKNOWN_SESSION",
        "OWNERSHIP_UNVERIFIED" => "OWNERSHIP_UNVERIFIED",
        "CREATE_CANCELLED" => "CREATE_CANCELLED",
        "observer not integrated" => "observer not integrated",
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
        if let Err(refusal) = all.admit_create(req.id) {
            all.create_failed(req.id);
            all.reserved.remove(&parsed.id);
            return Err(io::Error::other(refusal));
        }
    }
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
    let root = owned.root.record();
    // Publication, the cancellation check and the registry entry are one critical section, and the
    // session is findable BEFORE `SESSION_OWNED` can be read.
    let cancelled = {
        let mut registry = sessions.lock().unwrap();
        let cancelled = registry.register_create(req.id, &parsed.id);
        registry.sessions.insert(parsed.id.clone(), handle.clone());
        cancelled
    };
    let owned_event = owned_meta(req.id, &handle, &root);
    let (thread_handle, thread_wire, thread_sessions) =
        (handle.clone(), wire.clone(), sessions.clone());
    let thread_started = thread::Builder::new()
        .name("session".into())
        .spawn(move || session_thread(thread_handle, mailbox, owned, thread_wire, thread_sessions));
    if let Err(error) = thread_started {
        let verified = kill_and_reap_released(&handle.pty);
        handle.pty.release_originals();
        let mut all = sessions.lock().unwrap();
        all.sessions.remove(&parsed.id);
        all.reserved.remove(&parsed.id);
        drop(all);
        return Err(post_effect_failure(
            verified,
            "the session thread could not start",
            &error,
        ));
    }
    match owned_event {
        Ok(meta) => wire.out.event(code::SESSION_OWNED, meta),
        Err(error) => {
            handle.send(Msg::Kill(true));
            return Err(error);
        }
    }
    if cancelled {
        handle.send(Msg::Kill(true));
    }
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
    let closed = |error: &io::Error| error.kind() == io::ErrorKind::NotConnected;
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
    // A late report for a session that settled long ago is idempotent (5.5 race item 6).
    let late = |error: &io::Error| {
        error.to_string() == "UNKNOWN_SESSION" && evicted_session(sessions, &req.meta)
    };
    match req.code {
        code::CREATE => unreachable!("creates run on their own thread"),
        code::ADOPT => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                handle.adopted.store(true, Ordering::SeqCst);
                // Python's duplicates exist by now: the originals close (7.2).
                handle.pty.release_originals();
                reply(json!({"adopted":true}));
            }
            Err(error) => refuse(&error.to_string()),
        },
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
                reply(json!({"accepted":true}));
                handle.send(Msg::Kill(req.meta["force"].as_bool().unwrap_or(false)));
            }
            Err(error) => refuse(&error.to_string()),
        },
        code::TTY_SERVICING => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                handle.gate.satisfy(GateReason::Servicing);
                reply(json!({"accepted":true}));
                handle.send(Msg::Servicing);
            }
            Err(error) if late(&error) => reply(json!({"accepted":true})),
            Err(error) => refuse(&error.to_string()),
        },
        code::TTY_EOF => match session_of(sessions, &req.meta) {
            Ok(handle) => {
                handle.gate.satisfy(GateReason::StreamClosed);
                reply(json!({"accepted":true}));
                handle.send(Msg::TtyEof);
            }
            Err(error) if late(&error) => reply(json!({"accepted":true})),
            Err(error) => refuse(&error.to_string()),
        },
        code::CANCEL => {
            let Some(target) = req.meta["target_id"].as_u64() else {
                return refuse("invalid target_id");
            };
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
        // Vendor observers read POSIX session files; none is integrated on Windows (E5 scope: shells).
        code::OBSERVE => refuse("observer not integrated"),
        _ => refuse("invalid code"),
    }
}

// ---- startup, the control channel and teardown --------------------------------------------------

struct Config {
    wire: u64,
    generation: String,
    owner_pid: u32,
    journal: std::path::PathBuf,
}

fn parse_args(args: &[String]) -> Result<Config, (&'static str, String)> {
    let startup = |message: String| ("STARTUP_FAILED", message);
    let (mut wire, mut generation, mut owner_pid, mut journal) = (None, None, None, None);
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
    json!({"event":"ready","protocol":"navide-acr","wire":config.wire,
        "version":env!("CARGO_PKG_VERSION"),"runtime":"navide-cli-runtime",
        "runtime_generation":config.generation,"pid":std::process::id(),"owner_pid":config.owner_pid,
        "platform":std::env::consts::OS,"arch":std::env::consts::ARCH,"pty_backend":"conpty",
        "handoff":"duplicate_handle","ownership_gate":true,"limits":limits()})
}

/// Blocking read of exactly `buffer.len()` bytes; fewer only at end of file.
fn read_full(input: &mut impl Read, buffer: &mut [u8]) -> io::Result<usize> {
    let mut got = 0;
    while got < buffer.len() {
        match input.read(&mut buffer[got..]) {
            Ok(0) => break,
            Ok(n) => got += n,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(got)
}

/// A real handle to the calling thread (the pseudo handle cannot be used from another thread), so
/// the writer can `CancelSynchronousIo` the reader's blocked `ReadFile` when the channel is lost.
fn current_thread() -> io::Result<OwnedHandle> {
    let mut real = null_mut();
    // SAFETY: duplicating the calling thread's pseudo handle within this process.
    let ok = unsafe {
        DuplicateHandle(
            GetCurrentProcess(),
            GetCurrentThread(),
            GetCurrentProcess(),
            &mut real,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: DuplicateHandle returned an owned handle.
    Ok(unsafe { OwnedHandle::from_raw_handle(real) })
}

enum Ended {
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
    let tag = frame::runtime_tag(&config.generation).expect("validated while parsing");
    stdout_line(&ready_line(&config))?;
    let wire = Arc::new(Wire {
        generation: config.generation.clone(),
        journal: config.journal.clone(),
        owner_pid: config.owner_pid,
        out: Outbound::new(tag),
        inbound: InboundBudget::new(MAX_INBOUND_BYTES),
    });
    let reader_thread = Arc::new(current_thread()?);
    let (writer_wire, writer_reader) = (wire.clone(), reader_thread.clone());
    let writer = thread::Builder::new()
        .name("control-writer".into())
        .spawn(move || {
            let outcome = writer_wire.out.run_writer_plain(&mut io::stdout().lock());
            if outcome.is_err() {
                writer_wire.inbound.close();
                // Nothing can be said to Python any more: wake the reader as if the peer had closed.
                // SAFETY: cancels the blocked synchronous read of the (live) reader thread.
                unsafe { CancelSynchronousIo(writer_reader.as_raw_handle()) };
            }
            outcome
        })?;
    let sessions: Sessions = Arc::new(Mutex::new(Registry::default()));
    let mut input = io::stdin().lock();
    let ended = loop {
        let mut header = [0u8; frame::HEADER_LEN];
        match read_full(&mut input, &mut header) {
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
        match read_full(&mut input, &mut body) {
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
            // The spawn blocks on the journal: its own thread, never the reader.
            {
                let mut registry = sessions.lock().unwrap();
                registry.note_create(request.id);
                registry.creating += 1;
            }
            let (create_sessions, create_wire) = (sessions.clone(), wire.clone());
            let spawned = thread::Builder::new().name("create".into()).spawn(move || {
                match create(&request, &create_sessions, &create_wire) {
                    Ok(meta) => create_wire.out.respond(request.code, request.id, meta),
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
    // Teardown: in-flight creates settle, then every session ends by the (non-forced) kill policy,
    // each wait a condition with the one overall `cleanup_ms` bound.
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
mod tests {
    use super::*;

    #[test]
    fn a_cancel_before_the_release_is_prevented_and_the_release_then_refuses() {
        let mut registry = Registry::default();
        registry.note_create(1);
        registry.last_request = 1;
        assert_eq!(registry.cancel_verdict(1), Ok(CancelVerdict::Prevented));
        assert!(!registry.release_gate(1));
    }

    #[test]
    fn a_cancel_after_the_release_is_released_and_a_finished_create_is_unknown() {
        let mut registry = Registry::default();
        registry.note_create(1);
        registry.last_request = 1;
        assert!(registry.release_gate(1));
        assert_eq!(registry.cancel_verdict(1), Ok(CancelVerdict::Released));
        assert!(registry.register_create(1, "s"));
        assert_eq!(
            registry.cancel_verdict(1),
            Ok(CancelVerdict::Registered("s".into()))
        );
        assert_eq!(registry.cancel_verdict(0), Ok(CancelVerdict::Unknown));
    }

    #[test]
    fn the_worst_case_owned_frame_is_refused_before_any_process_exists() {
        let long = "p".repeat(4000);
        let mk = |pane: &str| CreateArgs {
            id: "s".into(),
            generation: "g".into(),
            pane: pane.into(),
            workspace: "w".into(),
            cwd: ".".into(),
            argv: vec!["x".into()],
            env: vec![],
            rows: 24,
            cols: 80,
        };
        assert!(handoff_size_ok(1, &mk("pane")));
        assert!(!handoff_size_ok(1, &mk(&long)));
    }

    #[test]
    fn unknown_and_missing_arguments_fail_startup_and_a_wrong_wire_is_incompatible() {
        let args = |list: &[&str]| list.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        let generation = "0123456789abcdef0123456789abcdef";
        let good = [
            "--wire",
            "1",
            "--runtime-generation",
            generation,
            "--owner-pid",
            "7",
            "--ownership-journal",
            "j",
        ];
        assert!(parse_args(&args(&good)).is_ok());
        let mut with_fd = good.to_vec();
        with_fd.extend(["--control-fd", "3"]);
        assert_eq!(
            parse_args(&args(&with_fd)).err().unwrap().0,
            "STARTUP_FAILED"
        );
        let mut wrong = good.to_vec();
        wrong[1] = "2";
        assert_eq!(
            parse_args(&args(&wrong)).err().unwrap().0,
            "INCOMPATIBLE_WIRE"
        );
    }

    #[test]
    fn appended_journal_records_are_whole_lines_that_survive_reopening() {
        let path =
            std::env::temp_dir().join(format!("navide-journal-{}.jsonl", std::process::id()));
        let _ = std::fs::remove_file(&path);
        journal_append(&path, &json!({"journal":1,"pid":1})).unwrap();
        journal_append(&path, &json!({"journal":1,"pid":2})).unwrap();
        let lines: Vec<_> = std::fs::read_to_string(&path)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap()["pid"].as_u64())
            .collect();
        assert_eq!(lines, vec![Some(1), Some(2)]);
        let _ = std::fs::remove_file(&path);
    }
}
