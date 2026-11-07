//! The control plane, written once: the frame loop, request admission, the session table's requests, generation and
//! loss handling, the limits and the wire contract (E1).
//!
//! After the handoff (E1 5.2, 6.1) Rust never reads or writes a PTY: Python owns every byte of the stream. What
//! stays here is the create (gated, journalled), the handle transfer, the window size, signals and reaping by
//! recorded identity, and the three independent lifecycle facts (root exit, stream end, verified cleanup). Whatever
//! differs by operating system is a `Host` (host.rs); this module is monomorphised for each.
use crate::compose;
use crate::frame::{self, code, Direction, Frame};
use crate::host::{CreateArgs, Host, Launch, Msg, Spawned};
use crate::refusal::{self, bad_request, classify, effect_of, fault, Code, Effect, Refusal, Stage};
use crate::session::{
    bounded_survivors, session_thread, CancelVerdict, Registry, SessionHandle,
};
use crate::wire::Outbound;
use serde_json::{json, Value};
use std::io::{self, Write};
use std::sync::atomic::Ordering;
use std::sync::{mpsc, Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

// ---- named bounds (E1 13.1: every bound has a named source) -------------------------------
/// Bound for observing the root's exit status after a SIGKILL or a job termination (cleanup's root reap).
pub const ROOT_REAP: Duration = Duration::from_secs(1);
/// `limits.kill_escalation_ms`: the baseline's `_KILL_ESCALATION_GRACE_S`.
pub const KILL_ESCALATION_MS: u64 = 1000;
/// `limits.eof_reap_ms`: how long an EOF waits for the root's exit before the root-live fence.
pub const EOF_REAP_MS: u64 = 1000;
/// The baseline's `_REAP_CONFIRM_S` and `_REAP_WAIT_SLACK_S` (`terminals._reap_wait_timeout_s`).
pub const REAP_CONFIRM_MS: u64 = 1000;
pub const REAP_SLACK_MS: u64 = 3000;
const REQUEST_MS: u64 = 15_000;
const CREATE_ANSWER_MS: u64 = 20_000;
const PING_MS: u64 = 5000;
/// `limits.ownership_snapshot_ms`: a sampling cadence, not a wait on state (E1 13.3 site 12).
pub const SNAPSHOT_YOUNG_MS: u64 = 5000;
pub const SNAPSHOT_STEADY_MS: u64 = 30_000;
pub const SNAPSHOT_YOUNG_WINDOW_MS: u64 = 30_000;
/// `limits.snapshot_ms`: the bound on one ownership snapshot (E1 13.3 rows 12-13).
pub const SNAPSHOT_MS: u64 = 5000;
/// `limits.max_inbound_bytes` (E1 1.4): the wire bytes of every request that has been read and not yet finished.
/// The reader blocks while the next request would exceed it (backpressure, nothing dropped).
const MAX_INBOUND_BYTES: usize = 16 * 1024 * 1024;
/// `limits.max_observation_bytes` / `max_observation_parts` and the part size (a part's meta and blob together
/// stay under `max_frame_r2p`).
pub const MAX_OBSERVATION_BYTES: usize = 8_388_608;
pub const MAX_OBSERVATION_PARTS: usize = 128;
const OBSERVATION_QUEUE: usize = 256;
pub const OBSERVATION_CHUNK: usize = 64 * 1024 - 256;
/// Settled sessions remembered by id and generation after they leave the registry (E1 v8 5.5): memory-bounded.
pub const TOMBSTONES: usize = 4096;
// The observation core's answer ledger (`observe/answer.rs`) is sized to the answer this transport can send; the two
// must agree.
const _: () = assert!(MAX_OBSERVATION_PARTS * OBSERVATION_CHUNK == crate::observe::ANSWER_BYTES);

/// `limits.cleanup_ms`, derived exactly as the baseline's `_reap_wait_timeout_s()`: the largest declared graceful
/// grace + kill escalation + reap confirmation + slack.
pub fn cleanup_ms() -> u64 {
    crate::adapters::max_grace_ms() + KILL_ESCALATION_MS + REAP_CONFIRM_MS + REAP_SLACK_MS
}

/// One decoded request frame. It carries its inbound byte reservation, released when the request is dropped
/// (answered, refused or abandoned), so queued and in-flight requests never exceed the budget.
pub struct Request {
    pub id: u64,
    pub code: u16,
    pub meta: Value,
    #[allow(dead_code)] // held for its Drop
    reservation: Reservation,
}

#[cfg(test)]
impl Request {
    /// A request as the reader would hand it over, with a reservation of its own.
    pub fn for_test(id: u64, code: u16, meta: Value) -> Self {
        Self {
            id,
            code,
            meta,
            reservation: InboundBudget::new(1 << 20)
                .acquire(0)
                .expect("an open budget admits an empty request"),
        }
    }
}

pub struct InboundBudget {
    state: Mutex<(usize, usize, bool)>, // used, peak, closed
    changed: Condvar,
    cap: usize,
}
pub struct Reservation {
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
    pub fn new(cap: usize) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new((0, 0, false)),
            changed: Condvar::new(),
            cap,
        })
    }
    /// Block until `size` more bytes fit (a request larger than the whole budget is admitted alone). `None` when
    /// the budget was closed (the channel is gone).
    pub fn acquire(self: &Arc<Self>, size: usize) -> Option<Reservation> {
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
    pub fn close(&self) {
        self.state.lock().unwrap().2 = true;
        self.changed.notify_all();
    }
    pub fn snapshot(&self) -> (usize, usize) {
        let state = self.state.lock().unwrap();
        (state.0, state.1)
    }
}

/// The channel to Python and what the sidecar knows about its own generation.
pub struct Wire {
    pub generation: String,
    pub journal: std::path::PathBuf,
    pub owner_pid: u32,
    pub out: Outbound,
    /// The observation worker's bounded queue (E1 14): history reads are file I/O and never run on the control
    /// reader, whose job is admission.
    observe_tx: Mutex<Option<mpsc::SyncSender<Request>>>,
    pub inbound: Arc<InboundBudget>,
}

impl Wire {
    pub fn new(config: &Config, tag: u64) -> Self {
        Self {
            generation: config.generation.clone(),
            journal: config.journal.clone(),
            owner_pid: config.owner_pid,
            out: Outbound::new(tag),
            observe_tx: Mutex::new(None),
            inbound: InboundBudget::new(MAX_INBOUND_BYTES),
        }
    }
    /// Answer `request_code`/`id` with the error `error` carries.
    fn refuse(&self, request_code: u16, id: u64, error: &io::Error, effect: Effect) {
        let (error_code, stage) = classify(error, request_code == code::CREATE);
        self.out.error(
            request_code,
            id,
            error_code.wire(),
            stage.wire(),
            &error.to_string(),
            effect.wire(),
        );
    }
}

/// One platform's control plane: its host, its wire and its session table.
pub struct Runtime<H: Host> {
    pub host: H,
    pub wire: Wire,
    registry: Mutex<Registry<H>>,
    /// Creates being coordinated: shutdown waits for them (a condition, not a poll).
    creates_changed: Condvar,
}

impl<H: Host> Runtime<H> {
    pub fn new(host: H, wire: Wire) -> Arc<Self> {
        Arc::new(Self {
            host,
            wire,
            registry: Mutex::new(Registry::default()),
            creates_changed: Condvar::new(),
        })
    }
    pub fn registry(&self) -> MutexGuard<'_, Registry<H>> {
        self.registry.lock().unwrap()
    }
    /// The registry for the one step that must still run after another thread panicked while holding it: the
    /// ownership gate's release.
    pub fn registry_unpoisoned(&self) -> MutexGuard<'_, Registry<H>> {
        self.registry.lock().unwrap_or_else(|e| e.into_inner())
    }
    /// The cancellation check and the release of the target are one step under the registry lock: a cancel either
    /// lands before it (the target then never executes; the answer to the cancel is "prevented") or after it
    /// (answered "released": the target may already be running). `release` performs the platform's release.
    pub fn release_gate(
        &self,
        request: u64,
        release: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<()> {
        let mut registry = self.registry_unpoisoned();
        if !registry.release_gate(request) {
            return Err(Refusal::named(Code::CreateCancelled).into());
        }
        release().inspect_err(|_| registry.release_failed(request))
    }

    /// End every session before `deadline` and say whether all of them ended clean (K10). In-flight creates are
    /// waited for first, as a condition; one still running at the deadline makes the teardown unclean, because its
    /// session may register after the handles below were collected and would never be told to end. Handles are
    /// collected, signalled and awaited, and then once more: a create that finished during the wait is signalled
    /// too.
    ///
    /// This is the control plane's teardown step, kept out of `run` because `run` owns the process's standard
    /// streams and command line, which leaves a test no other way in.
    pub(crate) fn shut_down(&self, peer_alive: bool, deadline: Instant) -> (Vec<Arc<SessionHandle<H>>>, bool) {
        let mut clean = {
            let mut registry = self.registry();
            while registry.creating > 0 {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    break;
                }
                registry = self.creates_changed.wait_timeout(registry, left).unwrap().0;
            }
            registry.creating == 0
        };
        let mut ended: Vec<Arc<SessionHandle<H>>> = Vec::new();
        for _ in 0..2 {
            let fresh: Vec<_> = self
                .registry()
                .sessions
                .values()
                .filter(|handle| !ended.iter().any(|known| Arc::ptr_eq(known, handle)))
                .cloned()
                .collect();
            if fresh.is_empty() {
                break;
            }
            for handle in &fresh {
                if !handle.finished() {
                    handle.send(Msg::Shutdown { peer_alive });
                }
            }
            for handle in &fresh {
                let left = deadline.saturating_duration_since(Instant::now());
                clean &= handle.wait_finished(left) == Some(true);
            }
            ended.extend(fresh);
        }
        (ended, clean)
    }
}

/// The error for a failure after the target was released: `effect:none` only if it was verified killed and
/// reaped first (E1 5.1.3), otherwise `CLEANUP_FAILED` (partial) and the generation is lost.
pub fn post_effect_failure(verified: bool, what: &str, error: &io::Error) -> io::Error {
    if verified {
        io::Error::other(format!("{what}: {error}"))
    } else {
        fault(
            Code::CleanupFailed,
            format!("CLEANUP_FAILED: {what} ({error}) and the released root could not be verified reaped"),
        )
    }
}

// ---- request arguments ----------------------------------------------------------------------

pub fn text<'a>(value: &'a Value, key: &str) -> io::Result<&'a str> {
    value[key]
        .as_str()
        .filter(|text| !text.contains('\0'))
        .ok_or_else(|| Refusal::bad(format!("invalid {key}")).into())
}
pub fn dimension(value: &Value, key: &str) -> io::Result<u16> {
    value[key]
        .as_u64()
        .and_then(|n| u16::try_from(n).ok())
        .filter(|n| *n > 0)
        .ok_or_else(|| Refusal::bad(format!("invalid {key}")).into())
}

/// The wire-2 `launch` object and its shape (delta section 2). `argv`, `argv_source` and `argv0` are never
/// CREATE fields.
pub fn launch_object(args: &Value) -> io::Result<(&Value, &str)> {
    if args.get("argv").is_some() || args.get("argv_source").is_some() || args.get("argv0").is_some() {
        return Err(bad_request(
            Stage::LaunchShape,
            "invalid launch_shape: argv, argv_source and argv0 are not CREATE fields",
        ));
    }
    let launch = args
        .get("launch")
        .ok_or_else(|| bad_request(Stage::LaunchShape, "invalid launch_shape: launch is required"))?;
    let shape = launch.get("shape").and_then(Value::as_str).unwrap_or("");
    Ok((launch, shape))
}

/// The pre-composed `argv` shape: only what a subject that composes in Python is admitted to use.
pub fn precomposed(launch: &Value) -> io::Result<Launch> {
    let argv: Vec<String> = launch["argv"]
        .as_array()
        .ok_or_else(|| bad_request(Stage::LaunchShape, "invalid launch_shape: argv must be an array"))?
        .iter()
        .map(|v| {
            v.as_str()
                .filter(|s| !s.contains('\0'))
                .map(String::from)
                .ok_or_else(|| bad_request(Stage::Validate, "invalid validate: argv holds a non-string or NUL"))
        })
        .collect::<io::Result<_>>()?;
    let program = argv
        .first()
        .filter(|program| !program.is_empty())
        .ok_or_else(|| bad_request(Stage::LaunchShape, "invalid launch_shape: argv is empty"))?;
    let argv0 = compose::attest(program);
    Ok(Launch {
        argv,
        argv_source: "python",
        argv0,
        outer: None,
    })
}

fn environment<H: Host>(args: &Value) -> io::Result<Vec<(String, String)>> {
    let mut env = Vec::new();
    for (key, value) in args["env"]
        .as_object()
        .ok_or_else(|| bad_request(Stage::Validate, "invalid env"))?
    {
        if !H::env_key_ok(key) {
            return Err(bad_request(Stage::Validate, "invalid env key"));
        }
        let value = value
            .as_str()
            .filter(|s| !s.contains('\0'))
            .ok_or_else(|| bad_request(Stage::Validate, "invalid env value"))?;
        env.push((key.clone(), value.to_owned()));
    }
    Ok(env)
}

pub(crate) fn create_args<H: Host>(host: &H, args: &Value) -> io::Result<CreateArgs> {
    let agent = args["agent_key"].as_str().unwrap_or("").to_owned();
    let env = environment::<H>(args)?;
    // Admission is the composition table's (`compose::admit`), independent of whether the sidecar also observes
    // the subject (delta 10.4).
    let launch = host.launch(&agent, args, &env)?;
    Ok(CreateArgs {
        id: text(args, "session_id")?.to_owned(),
        generation: text(args, "session_generation")?.to_owned(),
        pane: text(args, "pane_id")?.to_owned(),
        workspace: text(args, "workspace_path")?.to_owned(),
        cwd: text(args, "cwd")?.to_owned(),
        agent,
        argv: launch.argv,
        argv_source: launch.argv_source,
        argv0: launch.argv0,
        outer: launch.outer,
        env,
        rows: dimension(args, "rows")?,
        cols: dimension(args, "cols")?,
    })
}

fn argv0_json(argv0: &compose::Argv0) -> Value {
    json!({"len":argv0.len,"head":argv0.head,"tail":argv0.tail})
}

/// The `SESSION_OWNED` meta (5.2) for a session whose root is `root`.
fn owned_meta<H: Host>(
    host: &H,
    create: u64,
    handle: &SessionHandle<H>,
    root: &Value,
    parsed: &CreateArgs,
) -> io::Result<Value> {
    let mut meta = json!({"create_id":create,"session_id":handle.id,"session_generation":handle.generation,
        "pane_id":handle.pane,"workspace_path":handle.workspace,"root":root,
        "pty":host.pty_record(&handle.platform)?,"argv_source":parsed.argv_source,
        "argv0":argv0_json(&parsed.argv0)});
    if let Some(outer) = parsed.outer {
        meta["outer"] = json!(outer);
    }
    Ok(meta)
}

/// 5.1.3: before any PTY or process exists, the worst-case encoded `SESSION_OWNED` of this session must fit the
/// `HAS_FD` bound, so an oversized handoff is never found after the target was released and both platforms refuse
/// the same creates.
fn handoff_size_ok<H: Host>(host: &H, create: u64, args: &CreateArgs) -> bool {
    let worst = host.worst_case();
    let mut meta = json!({"create_id":create,"session_id":args.id,"session_generation":args.generation,
        "pane_id":args.pane,"workspace_path":args.workspace,"root":worst.root,
        "pty":worst.pty,"argv_source":args.argv_source,"argv0":argv0_json(&args.argv0)});
    if worst.outer {
        meta["outer"] = json!("credential");
    }
    let bytes = serde_json::to_vec(&meta)
        .map(|b| b.len())
        .unwrap_or(usize::MAX);
    frame::HEADER_LEN.saturating_add(bytes) <= frame::MAX_HAS_FD_FRAME
}

// ---- requests ------------------------------------------------------------------------------------

fn session_of<H: Host>(runtime: &Runtime<H>, meta: &Value) -> io::Result<Arc<SessionHandle<H>>> {
    let handle = runtime
        .registry()
        .sessions
        .get(text(meta, "session_id")?)
        .cloned()
        .ok_or_else(|| Refusal::named(Code::UnknownSession))?;
    if text(meta, "session_generation")? != handle.generation {
        return Err(Refusal::named(Code::StaleSession).into());
    }
    Ok(handle)
}

/// Is `meta` naming a settled session that was evicted from the registry (same id **and** generation)?
fn evicted_session<H: Host>(runtime: &Runtime<H>, meta: &Value) -> bool {
    let (Ok(id), Ok(generation)) = (text(meta, "session_id"), text(meta, "session_generation"))
    else {
        return false;
    };
    runtime.registry().evicted(id, generation)
}

fn create<H: Host>(runtime: &Arc<Runtime<H>>, req: &Request) -> io::Result<Value> {
    let host = &runtime.host;
    let parsed = match create_args(host, &req.meta) {
        Ok(parsed) => parsed,
        Err(error) => {
            // The reader noted this create before it was parsed: a refusal here ends it, so no later `CANCEL` can
            // find a record that will never be released, and the table does not grow with every malformed create.
            runtime.registry().create_failed(req.id);
            return Err(error);
        }
    };
    if !handoff_size_ok(host, req.id, &parsed) {
        runtime.registry().create_failed(req.id);
        return Err(bad_request(Stage::HandoffSize, "invalid handoff_size"));
    }
    {
        let mut all = runtime.registry();
        if !all.reserved.insert(parsed.id.clone()) {
            all.create_failed(req.id);
            return Err(Refusal::bad("duplicate create").into());
        }
        if let Err(refusal) = all.admit_create(req.id) {
            all.create_failed(req.id);
            all.reserved.remove(&parsed.id);
            return Err(refusal.into());
        }
    }
    // Reservation is retained; native spawn/probes never hold the global registry lock.
    let Spawned {
        platform,
        owned,
        exit,
        tx,
        mailbox,
    } = match host.spawn(runtime, req.id, &parsed) {
        Ok(spawned) => spawned,
        Err(error) => {
            let mut all = runtime.registry();
            all.create_failed(req.id);
            all.reserved.remove(&parsed.id);
            return Err(error);
        }
    };
    let handle = Arc::new(SessionHandle {
        id: parsed.id.clone(),
        generation: parsed.generation.clone(),
        pane: parsed.pane.clone(),
        workspace: parsed.workspace.clone(),
        agent_key: parsed.agent.clone(),
        platform,
        tx,
        exit,
        done: (Mutex::new(None), Condvar::new()),
        adopted: std::sync::atomic::AtomicBool::new(false),
        started: Instant::now(),
        survivors: Mutex::new(Vec::new()),
    });
    let root = host.root_record(&owned);
    // Publication, the cancellation check and the registry entry are one critical section, and the session is
    // findable BEFORE `SESSION_OWNED` can be read: a `KILL` that Python sends the moment it sees the event must
    // never meet UNKNOWN_SESSION.
    let cancelled = {
        let mut registry = runtime.registry();
        let cancelled = registry.register_create(req.id, &parsed.id);
        registry.sessions.insert(parsed.id.clone(), handle.clone());
        cancelled
    };
    let owned_event = owned_meta(host, req.id, &handle, &root, &parsed);
    let (thread_runtime, thread_handle) = (runtime.clone(), handle.clone());
    #[cfg(feature = "fault-hooks")]
    let thread_started = if crate::fault::thread_start_fails(&handle.pane) {
        Err(io::Error::other("fault: session thread could not start"))
    } else {
        thread::Builder::new()
            .name("session".into())
            .spawn(move || session_thread(thread_runtime, thread_handle, mailbox, owned))
    };
    #[cfg(not(feature = "fault-hooks"))]
    let thread_started = thread::Builder::new()
        .name("session".into())
        .spawn(move || session_thread(thread_runtime, thread_handle, mailbox, owned));
    if let Err(error) = thread_started {
        // The root has run and is registered but nothing will ever serve it: end it, verified, and take it out of
        // the registry before answering (E1 5.1.3: the same rule as any post-gate failure).
        let verified = host.abort_unserved(&handle, &root);
        let mut all = runtime.registry();
        all.sessions.remove(&parsed.id);
        all.reserved.remove(&parsed.id);
        drop(all);
        return Err(post_effect_failure(
            verified,
            "the session thread could not start",
            &error,
        ));
    }
    match owned_event.and_then(|meta| host.send_owned(runtime, &handle, meta)) {
        Ok(()) => {}
        Err(error) => {
            // The root has run. Like any failure after the release, the answer says `effect:none` only if the root
            // was verified ended and reaped first (E1 5.1.3); a size error here is no pre-spawn refusal.
            handle.send(Msg::Kill(true));
            let verified = handle.exit.wait(ROOT_REAP).is_some();
            return Err(post_effect_failure(verified, "SESSION_OWNED could not be sent", &error));
        }
    }
    if cancelled {
        handle.send(Msg::Kill(true));
    }
    #[cfg(feature = "fault-hooks")]
    if crate::fault::suppress_response(&handle.pane) {
        return Err(crate::fault::suppressed());
    }
    #[cfg(feature = "fault-hooks")]
    crate::fault::delay_response(&handle.pane);
    Ok(
        json!({"session_id":handle.id,"session_generation":handle.generation,
        "state":"owned","root":root}),
    )
}

/// A resize that failed: what to tell Python and how much of it was applied.
struct ResizeError {
    code: Code,
    message: String,
    effect: Effect,
    /// Size steps already applied; `None` for a request refused before any step.
    steps: Option<u32>,
}

impl ResizeError {
    fn bad(message: &str) -> Self {
        Self {
            code: Code::BadRequest,
            message: message.to_owned(),
            effect: Effect::None,
            steps: None,
        }
    }
}

fn resize_session<H: Host>(host: &H, handle: &SessionHandle<H>, meta: &Value) -> Result<Value, ResizeError> {
    use crate::host::ResizeFailure::{Closed, Failed};
    let cols = dimension(meta, "cols").map_err(|_| ResizeError::bad("invalid cols"))?;
    let rows = dimension(meta, "rows").map_err(|_| ResizeError::bad("invalid rows"))?;
    if handle.finished() {
        return Ok(json!({"applied":false,"steps":0}));
    }
    let mut steps = 0;
    if meta["redraw"].as_bool().unwrap_or(false) {
        match host.resize(&handle.platform, rows.saturating_sub(1).max(1), cols) {
            Ok(()) => steps = 1,
            Err(Closed(_)) => return Ok(json!({"applied":false,"steps":0})),
            Err(Failed(error)) => {
                return Err(ResizeError {
                    code: Code::IoFailed,
                    message: error.to_string(),
                    effect: Effect::None,
                    steps: Some(0),
                })
            }
        }
    }
    #[cfg(feature = "fault-hooks")]
    if steps == 1 && crate::fault::fail_second_resize(&handle.pane) {
        return Err(ResizeError {
            code: Code::IoFailed,
            message: "fault: second size step refused".into(),
            effect: Effect::Partial,
            steps: Some(1),
        });
    }
    match host.resize(&handle.platform, rows, cols) {
        Ok(()) => Ok(json!({"applied":true,"steps":steps + 1})),
        Err(Closed(_)) if steps == 0 => Ok(json!({"applied":false,"steps":0})),
        Err(Closed(error) | Failed(error)) => Err(ResizeError {
            code: Code::IoFailed,
            message: error.to_string(),
            effect: if steps > 0 { Effect::Partial } else { Effect::None },
            steps: Some(steps),
        }),
    }
}

/// Answer a failed resize on the wire.
fn resize_error<H: Host>(runtime: &Runtime<H>, req: &Request, error: ResizeError) {
    let mut meta = json!({"code":error.code.wire(),"stage":"io",
        "message":error.message.chars().take(1024).collect::<String>(),"effect":error.effect.wire()});
    if let Some(steps) = error.steps {
        meta["steps"] = json!(steps);
    }
    runtime.wire.out.error_with(req.code, req.id, meta);
}

/// Answer one non-create request. `Some` ends the control channel: the request found the runtime in a state the
/// protocol cannot continue from.
fn dispatch<H: Host>(req: Request, runtime: &Arc<Runtime<H>>) -> Option<Ended> {
    let wire = &runtime.wire;
    let host = &runtime.host;
    let reply = |meta: Value| wire.out.respond(req.code, req.id, meta);
    let refuse = |error: &io::Error| wire.refuse(req.code, req.id, error, Effect::None);
    // A late report for a session that settled long ago is idempotent (5.5 race item 6).
    let late = |error: &io::Error| {
        refusal::is(error, Code::UnknownSession) && evicted_session(runtime, &req.meta)
    };
    match req.code {
        code::CREATE => unreachable!("creates run on their own thread"),
        code::ADOPT => match session_of(runtime, &req.meta) {
            Ok(handle) => {
                handle.adopted.store(true, Ordering::SeqCst);
                host.adopted(&handle.platform);
                reply(json!({"adopted":true}));
            }
            Err(error) => refuse(&error),
        },
        #[cfg(feature = "fault-hooks")]
        code::RESIZE
            if session_of(runtime, &req.meta)
                .is_ok_and(|h| crate::fault::resize_hold(&h.pane).is_some()) =>
        {
            // Harness build only: the answer waits until a release file exists (T6), on its own thread.
            let handle = session_of(runtime, &req.meta).expect("checked above");
            let path = crate::fault::resize_hold(&handle.pane).expect("checked above");
            let runtime = runtime.clone();
            thread::spawn(move || {
                crate::fault::wait_for_file(&path);
                match resize_session(&runtime.host, &handle, &req.meta) {
                    Ok(meta) => runtime.wire.out.respond(req.code, req.id, meta),
                    Err(failure) => resize_error(&runtime, &req, failure),
                }
            });
        }
        code::RESIZE => match session_of(runtime, &req.meta) {
            Ok(handle) => match resize_session(host, &handle, &req.meta) {
                Ok(meta) => reply(meta),
                Err(failure) => resize_error(runtime, &req, failure),
            },
            Err(error) => refuse(&error),
        },
        code::KILL => match session_of(runtime, &req.meta) {
            Ok(handle) => {
                // Acknowledged before it acts; the outcome arrives only as EXITED / CLEANED.
                reply(json!({"accepted":true}));
                handle.send(Msg::Kill(req.meta["force"].as_bool().unwrap_or(false)));
            }
            Err(error) => refuse(&error),
        },
        code::TTY_SERVICING if H::SERVICING => match session_of(runtime, &req.meta) {
            Ok(handle) => {
                host.stream_signal(&handle.platform, crate::host::StreamSignal::Servicing);
                reply(json!({"accepted":true}));
                handle.send(Msg::Servicing);
            }
            Err(error) if late(&error) => reply(json!({"accepted":true})),
            Err(error) => refuse(&error),
        },
        code::TTY_EOF => match session_of(runtime, &req.meta) {
            Ok(handle) => {
                host.stream_signal(&handle.platform, crate::host::StreamSignal::Eof);
                reply(json!({"accepted":true}));
                handle.send(Msg::TtyEof);
            }
            Err(error) if late(&error) => {
                // The session settled long ago and left the registry: the late EOF report is idempotent.
                reply(json!({"accepted":true}));
            }
            Err(error) => refuse(&error),
        },
        code::CANCEL => cancel(&req, runtime),
        code::PING => {
            let registry = runtime.registry();
            reply(
                json!({"pong":true,"bookkeeping":{"last_request":registry.last_request,
                "live_sessions":registry.sessions.len().saturating_sub(registry.settled_count()),
                "settled_sessions":registry.settled_count(),"session_workers":registry.creating,
                "inbound_bytes":wire.inbound.snapshot().0,"inbound_peak_bytes":wire.inbound.snapshot().1}}),
            );
        }
        code::OBSERVE => {
            let sender = wire.observe_tx.lock().unwrap().clone();
            // The queue's sender is installed before the reader starts and taken only after it ended, so a missing
            // one means the runtime's own bookkeeping is broken. No request can be answered then, and an answer
            // with no request to name would be a protocol error for Python: the reader ends the channel instead.
            let Some(sender) = sender else {
                return Some(Ended::Protocol("observation worker is not running".into()));
            };
            if let Err(mpsc::TrySendError::Full(req) | mpsc::TrySendError::Disconnected(req)) = sender.try_send(req)
            {
                wire.out.error(
                    req.code,
                    req.id,
                    Code::BadRequest.wire(),
                    Stage::Admit.wire(),
                    "observation queue not admitting",
                    Effect::None.wire(),
                );
            }
        }
        _ => refuse(&Refusal::bad("invalid code").into()),
    }
    None
}

/// Answer a `CANCEL`: the one verdict the registry gives for the target create.
fn cancel<H: Host>(req: &Request, runtime: &Arc<Runtime<H>>) {
    let wire = &runtime.wire;
    let reply = |meta: Value| wire.out.respond(req.code, req.id, meta);
    let refuse = |error: &io::Error| wire.refuse(req.code, req.id, error, Effect::None);
    let Some(target) = req.meta["target_id"].as_u64() else {
        return refuse(&Refusal::bad("invalid target_id").into());
    };
    // One lock: the verdict and the state it describes cannot diverge.
    let mut registry = runtime.registry();
    let verdict = match registry.cancel_verdict(target) {
        Ok(verdict) => verdict,
        Err(refusal) => {
            drop(registry);
            return refuse(&refusal.into());
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

/// The ownership context an observation answer carries: the session's identity when the observation is bound to a
/// live session, nulls for a historical one.
fn observation_context<H: Host>(req: &Request, runtime: &Runtime<H>) -> io::Result<Value> {
    let vendor = req.meta["vendor"].as_str().unwrap_or("");
    let Some(sid) = req.meta["session_id"].as_str() else {
        return Ok(
            json!({"event":"observed","session_id":null,"session_generation":null,
            "pane_id":null,"workspace_path":null}),
        );
    };
    let handle = runtime
        .registry()
        .sessions
        .get(sid)
        .cloned()
        .ok_or_else(|| Refusal::named(Code::UnknownSession))?;
    if req.meta["session_generation"] != handle.generation || vendor != handle.agent_key {
        return Err(Refusal::named(Code::StaleSession).into());
    }
    let mut context = handle.meta();
    context["event"] = json!("observed");
    Ok(context)
}

/// One observation, computed completely before the first part is sent, so a refusal for size sends no part at all
/// (E1 14).
fn observe<H: Host>(req: &Request, runtime: &Arc<Runtime<H>>) {
    let wire = &runtime.wire;
    let refuse = |error: &io::Error| wire.refuse(req.code, req.id, error, Effect::None);
    // The vendor's declaration is resolved once here and handed down; the wire's vendor string is not read again. Observation is the
    // core's, not the host's: every platform reads the same declarations through `observe::adapters`, whose files open through
    // `observe::os`, the one place that differs by operating system.
    let observer = req.meta["vendor"]
        .as_str()
        .and_then(crate::observe::adapters::find);
    #[cfg(feature = "fault-hooks")]
    let fault_vendor = req.meta["vendor"].as_str() == Some(crate::adapters::FAULT_GRACEFUL);
    #[cfg(not(feature = "fault-hooks"))]
    let fault_vendor = false;
    let not_integrated = || fault(Code::ObserverNotIntegrated, "observer not integrated");
    if observer.is_none() && !fault_vendor {
        return refuse(&not_integrated());
    }
    let context = match observation_context(req, runtime) {
        Ok(context) => context,
        Err(error) => return refuse(&error),
    };
    let run = || match observer {
        Some(observer) => crate::observe::adapters::dispatch(observer, &req.meta),
        None => Err(not_integrated()),
    };
    #[cfg(feature = "fault-hooks")]
    let computed = crate::fault::observation(req).unwrap_or_else(run);
    #[cfg(not(feature = "fault-hooks"))]
    let computed = run();
    let mut document = match computed {
        Ok(computed) => computed,
        Err(error) => return refuse(&error),
    };
    document["ownership"] = context;
    let bytes = serde_json::to_vec(&document).expect("a JSON value is serialisable");
    let parts = bytes.len().div_ceil(OBSERVATION_CHUNK);
    if bytes.len() > MAX_OBSERVATION_BYTES || parts > MAX_OBSERVATION_PARTS {
        return wire.out.error(
            req.code,
            req.id,
            "OBSERVATION_TOO_LARGE",
            Stage::Io.wire(),
            &format!(
                "result of {} bytes exceeds the observation limits",
                bytes.len()
            ),
            Effect::None.wire(),
        );
    }
    #[cfg(feature = "fault-hooks")]
    if crate::fault::endless_parts(req, wire) {
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
        let meta = json!({"code":Code::IoFailed.wire(),"stage":"io","effect":Effect::Partial.wire(),
            "message":message.chars().take(1024).collect::<String>(),"observation_parts":parts});
        return wire.out.error_with(req.code, req.id, meta);
    }
    wire.out
        .respond(req.code, req.id, json!({ "observation_parts": parts }));
}

// ---- startup and the main loop -------------------------------------------------------------------

pub struct Config {
    pub wire: u64,
    pub generation: String,
    pub owner_pid: u32,
    pub journal: std::path::PathBuf,
    /// The control socket a POSIX host reads and writes (Windows uses stdin and stdout).
    #[cfg(feature = "fault-hooks")]
    pub fail_startup: bool,
    #[cfg(feature = "fault-hooks")]
    pub ready_wire: Option<u64>,
    #[cfg(feature = "fault-hooks")]
    pub ready_protocol: Option<String>,
}

#[cfg(test)]
impl Config {
    /// A configuration for tests that build a `Runtime` without starting a process.
    pub fn for_test() -> Self {
        Self {
            wire: u64::from(frame::WIRE_VERSION),
            generation: "0123456789abcdef0123456789abcdef".into(),
            owner_pid: std::process::id(),
            journal: std::env::temp_dir().join("navide-runtime-test-journal"),
            #[cfg(feature = "fault-hooks")]
            fail_startup: false,
            #[cfg(feature = "fault-hooks")]
            ready_wire: None,
            #[cfg(feature = "fault-hooks")]
            ready_protocol: None,
        }
    }
}

/// The command line: the options every platform reads (`Config`) and the ones only `H` reads (`H::Options`).
pub(crate) fn parse_args<H: Host>(args: &[String]) -> Result<(Config, H::Options), (&'static str, String)> {
    let startup = |message: String| ("STARTUP_FAILED", message);
    let mut wire = None;
    let mut generation = None;
    let mut owner_pid = None;
    let mut journal = None;
    let mut host_options = H::Options::default();
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
            #[cfg(feature = "fault-hooks")]
            "--fail-startup" => fail_startup = true,
            #[cfg(feature = "fault-hooks")]
            "--ready-wire" => ready_wire = value("--ready-wire")?.parse().ok(),
            #[cfg(feature = "fault-hooks")]
            "--ready-protocol" => ready_protocol = Some(value("--ready-protocol")?),
            host_option if H::OPTIONS.contains(&host_option) => {
                H::set_option(&mut host_options, host_option, value(host_option)?)
            }
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
    let config = Config {
        wire,
        generation,
        owner_pid: number(owner_pid, "--owner-pid")? as u32,
        journal: journal
            .map(std::path::PathBuf::from)
            .ok_or_else(|| startup("--ownership-journal required".into()))?,
        #[cfg(feature = "fault-hooks")]
        fail_startup,
        #[cfg(feature = "fault-hooks")]
        ready_wire,
        #[cfg(feature = "fault-hooks")]
        ready_protocol,
    };
    // Last, so a wrong wire or a missing core option is still reported as before.
    H::check_options(&host_options).map_err(startup)?;
    Ok((config, host_options))
}

fn stdout_line(value: &Value) -> io::Result<()> {
    let mut stdout = io::stdout().lock();
    stdout.write_all(&serde_json::to_vec(value)?)?;
    stdout.write_all(b"\n")?;
    stdout.flush()
}

fn fatal_line(error_code: &str, message: &str) {
    let _ = stdout_line(&json!({"event":"fatal","protocol":"navide-acr","wire":frame::WIRE_VERSION,
        "error":{"code":error_code,"message":message,"stage":"initialize"}}));
}

fn limits() -> Value {
    json!({"max_meta_p2r":frame::MAX_META_P2R,"max_blob_p2r":frame::MAX_BLOB_P2R,
        "max_frame_r2p":frame::MAX_FRAME_R2P,"max_observation_bytes":MAX_OBSERVATION_BYTES,
        "max_observation_parts":MAX_OBSERVATION_PARTS,"observe_inflight":4,
        "max_outbound_bytes":crate::wire::MAX_OUTBOUND_BYTES,
        "max_outbound_frames":crate::wire::MAX_OUTBOUND_FRAMES,"max_inbound_bytes":MAX_INBOUND_BYTES,
        "request_ms":REQUEST_MS,"create_answer_ms":CREATE_ANSWER_MS,"cleanup_ms":cleanup_ms(),
        "ping_ms":PING_MS,"kill_escalation_ms":KILL_ESCALATION_MS,"eof_reap_ms":EOF_REAP_MS,
        "ownership_snapshot_ms":{"young":SNAPSHOT_YOUNG_MS,"steady":SNAPSHOT_STEADY_MS,
            "young_window_ms":SNAPSHOT_YOUNG_WINDOW_MS},
        "snapshot_ms":SNAPSHOT_MS})
}

fn ready_line<H: Host>(host: &H, config: &Config) -> Value {
    #[allow(unused_mut)]
    let mut ready = json!({"event":"ready","protocol":"navide-acr","wire":config.wire,
        "version":env!("CARGO_PKG_VERSION"),"runtime":"navide-cli-runtime",
        "runtime_generation":config.generation,"pid":std::process::id(),"owner_pid":config.owner_pid,
        "platform":std::env::consts::OS,"arch":std::env::consts::ARCH,"pty_backend":H::PTY_BACKEND,
        "handoff":H::HANDOFF,"ownership_gate":true,"limits":limits()});
    #[cfg(feature = "fault-hooks")]
    {
        if let Some(wire) = config.ready_wire {
            ready["wire"] = json!(wire);
        }
        if let Some(protocol) = &config.ready_protocol {
            ready["protocol"] = serde_json::from_str(protocol).unwrap_or(json!(protocol));
        }
    }
    host.ready(&mut ready);
    ready
}

/// Blocking read of exactly `buffer.len()` bytes from the control channel. Returns the number of bytes read, fewer
/// only at end of file.
fn read_full(mut read: impl FnMut(&mut [u8]) -> io::Result<usize>, buffer: &mut [u8]) -> io::Result<usize> {
    let mut got = 0;
    while got < buffer.len() {
        match read(&mut buffer[got..]) {
            Ok(0) => break,
            Ok(n) => got += n,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(got)
}

enum Ended {
    /// The backend closed the channel (it died, or this side failed a send).
    PeerEof,
    Shutdown(u64),
    Protocol(String),
}

/// Run the sidecar on host `H` until the channel ends or `SHUTDOWN` arrives.
pub fn run<H: Host>() -> io::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (config, options) = match parse_args::<H>(&args) {
        Ok(parsed) => parsed,
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
    let host = match H::boot(&config, options) {
        Ok(host) => host,
        Err(error) => {
            fatal_line("STARTUP_FAILED", &error.to_string());
            return Err(io::Error::other("STARTUP_FAILED"));
        }
    };
    let tag = frame::runtime_tag(&config.generation).expect("validated while parsing");
    stdout_line(&ready_line(&host, &config))?;
    let link = Arc::new(host.open_link()?);
    let runtime = Runtime::new(host, Wire::new(&config, tag));
    let (observe_tx, observe_rx) = mpsc::sync_channel::<Request>(OBSERVATION_QUEUE);
    *runtime.wire.observe_tx.lock().unwrap() = Some(observe_tx);
    let (writer_runtime, writer_link) = (runtime.clone(), link.clone());
    let writer = thread::Builder::new()
        .name("control-writer".into())
        .spawn(move || {
            let outcome = writer_runtime
                .host
                .link_write(&writer_link, &writer_runtime.wire.out);
            if outcome.is_err() {
                writer_runtime.wire.inbound.close();
                // Nothing can be said to Python any more: wake the reader as if the peer had closed.
                writer_runtime.host.link_break(&writer_link);
            }
            outcome
        })?;
    let observe_runtime = runtime.clone();
    let observer = thread::Builder::new()
        .name("observe".into())
        .spawn(move || {
            while let Ok(request) = observe_rx.recv() {
                observe(&request, &observe_runtime);
            }
        })?;
    let wire = &runtime.wire;
    let read = |buffer: &mut [u8]| runtime.host.link_read(&link, buffer);
    let ended = loop {
        let mut header = [0u8; frame::HEADER_LEN];
        match read_full(read, &mut header) {
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
        match read_full(read, &mut body) {
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
            reservation,
        };
        {
            let mut registry = runtime.registry();
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
                let mut registry = runtime.registry();
                registry.note_create(request.id);
                registry.creating += 1;
            }
            let create_runtime = runtime.clone();
            let spawned = thread::Builder::new().name("create".into()).spawn(move || {
                match create(&create_runtime, &request) {
                    Ok(meta) => create_runtime.wire.out.respond(request.code, request.id, meta),
                    #[cfg(feature = "fault-hooks")]
                    Err(error) if crate::fault::is_suppressed(&error) => {}
                    Err(error) => {
                        create_runtime
                            .wire
                            .refuse(request.code, request.id, &error, effect_of(&error));
                    }
                }
                create_runtime.registry().creating -= 1;
                create_runtime.creates_changed.notify_all();
            });
            if spawned.is_err() {
                runtime.registry().creating -= 1;
                break Ended::Protocol("create worker could not start".into());
            }
        } else if let Some(end) = dispatch(request, &runtime) {
            break end;
        }
    };
    if let Ended::Protocol(message) = &ended {
        wire.out.event(
            code::FATAL,
            json!({"code":"PROTOCOL_ERROR","stage":"validate","message":message}),
        );
    }
    // Teardown: in-flight creates settle, then every session ends by the (non-forced) kill policy, each wait a
    // condition with the one overall `cleanup_ms` bound.
    let deadline = Instant::now() + Duration::from_millis(cleanup_ms());
    let (handles, clean) = runtime.shut_down(matches!(ended, Ended::Shutdown(_)), deadline);
    runtime.host.teardown(deadline);
    if let Ended::Shutdown(id) = &ended {
        let survivors = bounded_survivors(
            handles
                .iter()
                .flat_map(|h| h.survivors.lock().unwrap().clone())
                .collect(),
        );
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
                json!({"code":Code::CleanupFailed.wire(),"stage":"shutdown",
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
mod tests {
    use super::*;
    #[cfg(unix)]
    type Plat = crate::posix::PosixHost;
    #[cfg(windows)]
    type Plat = crate::windows::WindowsHost;

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

    #[test]
    fn cleanup_ms_is_derived_exactly_like_the_baseline() {
        // largest declared graceful grace (Claude, 3000) + escalation (1000) + confirm (1000) + slack (3000)
        assert_eq!(cleanup_ms(), 8000);
    }

    #[test]
    fn the_shutdown_declaration_is_read_through_the_adapter_roster() {
        use crate::adapters;
        assert!(adapters::shutdown("claude")
            .is_some_and(|spec| spec.graceful && spec.defer_master_close));
        assert_eq!(adapters::shutdown(""), None);
        assert_eq!(adapters::shutdown("terminal"), None);
        assert_eq!(adapters::shutdown("codex"), None);
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
            argv_source: "rust",
            argv0: compose::attest(&"/x".repeat(300)),
            outer: None,
            env: Vec::new(),
            rows: 24,
            cols: 80,
        };
        let host = Plat::default();
        assert!(handoff_size_ok(&host, 1, &args("/tmp/ordinary".into())));
        assert!(!handoff_size_ok(&host, 1, &args("/x".repeat(2100))));
    }

    #[test]
    fn a_bad_environment_key_is_refused_by_the_platform_rule_with_its_stage() {
        let args = json!({"env":{"BAD=KEY":"v"}});
        let refused = environment::<Plat>(&args).unwrap_err();
        assert_eq!(classify(&refused, true), (Code::BadRequest, Stage::Validate));
        assert_eq!(refused.to_string(), "invalid env key");
        let fine = environment::<Plat>(&json!({"env":{"PATH":"/usr/bin"}})).unwrap();
        assert_eq!(fine, vec![("PATH".to_owned(), "/usr/bin".to_owned())]);
    }
}

/// The create, teardown and dispatch paths on a host that is only a double (`testing::MockHost`).
#[cfg(test)]
mod plane_tests {
    use super::*;
    use crate::refusal::Effect;
    use crate::testing::{self, MockHost};

    /// What the reader does before it routes a create: it records the request id and notes the create.
    fn read_create(runtime: &Runtime<MockHost>, id: u64) {
        let mut registry = runtime.registry();
        registry.last_request = id;
        registry.note_create(id);
    }

    /// K9: a create refused while its arguments are parsed was noted by the reader and never released. Its record
    /// must go, or a later `CANCEL` is told `prevented` for a create that was answered long ago.
    #[test]
    fn a_create_refused_while_parsing_leaves_no_record_for_a_later_cancel() {
        let runtime = testing::runtime(MockHost::default());
        read_create(&runtime, 7);
        let mut meta = testing::create_meta("s1", "/tmp");
        meta["rows"] = json!(0);
        let error = create(&runtime, &testing::request(7, code::CREATE, meta)).unwrap_err();
        assert_eq!(classify(&error, true), (Code::BadRequest, Stage::Spawn), "{error}");
        let mut registry = runtime.registry();
        assert_eq!(registry.tracked_creates(), 0, "the refused create is still tracked");
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Unknown));
    }

    #[test]
    fn malformed_creates_do_not_grow_the_table() {
        let runtime = testing::runtime(MockHost::default());
        for id in 1..=20 {
            read_create(&runtime, id);
            let mut meta = testing::create_meta("s", "/tmp");
            meta["cwd"] = json!(7);
            assert!(create(&runtime, &testing::request(id, code::CREATE, meta)).is_err());
        }
        assert_eq!(runtime.registry().tracked_creates(), 0);
    }

    /// K5(a): the root has run by the time `SESSION_OWNED` is sent, so a failure to send it is not a pre-spawn
    /// refusal. The root is ended first; `effect:none` is only said when that was verified (E1 5.1.3).
    #[test]
    fn a_session_owned_that_cannot_be_sent_after_the_spawn_is_a_post_release_failure() {
        let host = MockHost {
            kill_ends_root: true,
            ..MockHost::default()
        };
        *host.owned_failure.lock().unwrap() = Some(bad_request(Stage::HandoffSize, "handoff_size"));
        let runtime = testing::runtime(host);
        read_create(&runtime, 1);
        let error = create(&runtime, &testing::request(1, code::CREATE, testing::create_meta("s1", "/tmp"))).unwrap_err();
        assert_eq!(
            classify(&error, true),
            (Code::SpawnFailed, Stage::Spawn),
            "a size error after the release is no pre-spawn refusal: {error}"
        );
        assert_eq!(effect_of(&error), Effect::None, "the root was verified ended");
        assert!(error.to_string().contains("SESSION_OWNED could not be sent"), "{error}");
    }

    #[test]
    fn a_session_owned_that_cannot_be_sent_and_a_root_not_verified_ended_is_a_partial_cleanup_failure() {
        let host = MockHost::default(); // a kill leaves the root's exit unknown
        *host.owned_failure.lock().unwrap() = Some(bad_request(Stage::HandoffSize, "handoff_size"));
        let runtime = testing::runtime(host);
        read_create(&runtime, 1);
        let error = create(&runtime, &testing::request(1, code::CREATE, testing::create_meta("s1", "/tmp"))).unwrap_err();
        assert_eq!(classify(&error, true).0, Code::CleanupFailed, "{error}");
        assert_eq!(effect_of(&error), Effect::Partial);
    }

    /// K10: a create still running at the deadline may register its session after the handles were collected; that
    /// session is never told to end, so the teardown cannot call itself clean.
    #[test]
    fn a_create_still_running_at_the_teardown_deadline_makes_the_teardown_unclean() {
        let runtime = testing::runtime(MockHost::default());
        runtime.registry().creating = 1;
        let began = Instant::now();
        let (handles, clean) = runtime.shut_down(true, began + Duration::from_millis(60));
        assert!(!clean, "a create was still running");
        assert!(handles.is_empty());
        assert!(began.elapsed() >= Duration::from_millis(60), "it waited for the create until the deadline");
    }

    #[test]
    fn a_session_registered_while_the_teardown_waits_is_told_to_end() {
        let runtime = testing::runtime(MockHost::default());
        runtime.registry().creating = 1;
        let (handle, mailbox) = testing::handle::<MockHost>(());
        let finishing = {
            let (runtime, handle) = (runtime.clone(), handle.clone());
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(30));
                let mut registry = runtime.registry();
                registry.sessions.insert("s".into(), handle);
                registry.creating -= 1;
                drop(registry);
                runtime.creates_changed.notify_all();
            })
        };
        // The session thread's part: answer the shutdown with a final cleanup.
        let session = {
            let handle = handle.clone();
            thread::spawn(move || {
                let told = mailbox.recv_timeout(Duration::from_secs(5));
                *handle.done.0.lock().unwrap() = Some(true);
                handle.done.1.notify_all();
                matches!(told, Ok(Msg::Shutdown { peer_alive: true }))
            })
        };
        let (handles, clean) = runtime.shut_down(true, Instant::now() + Duration::from_secs(5));
        finishing.join().unwrap();
        assert!(session.join().unwrap(), "the late session was not told to shut down");
        assert!(clean);
        assert_eq!(handles.len(), 1);
    }

    /// K11: the observation queue's sender is installed before the reader runs, so a missing one is the runtime's
    /// own fault. An answer for request id 0 would be a protocol error Python reports as a runtime loss for a request
    /// it never made; the reader ends the channel instead.
    #[test]
    fn an_observe_with_no_worker_ends_the_channel_and_answers_nothing() {
        let runtime = testing::runtime(MockHost::default()); // `run` has not installed the queue
        let ended = dispatch(testing::request(5, code::OBSERVE, json!({"vendor":"x"})), &runtime);
        assert!(matches!(ended, Some(Ended::Protocol(_))), "the channel did not end");
        assert!(testing::sent(&runtime).is_empty(), "something was answered");
    }

    #[test]
    fn a_request_that_is_answered_does_not_end_the_channel() {
        let runtime = testing::runtime(MockHost::default());
        assert!(dispatch(testing::request(1, code::PING, json!({})), &runtime).is_none());
        assert_eq!(testing::sent(&runtime).len(), 1);
    }
}
