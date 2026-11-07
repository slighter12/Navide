//! The POSIX host: spawn and ownership of PTY-backed roots on macOS and Linux.
//!
//! The control plane (control.rs, session.rs) is generic; this module is what is POSIX about it: the PTY, the
//! ownership gate (a forked root blocks before `exec` until its identity is durable), signals by recorded identity,
//! reaping, the kernel's process tables, and a control channel that is a socket carrying `SCM_RIGHTS`.
use crate::adapters::{self, ShutdownSpec};
use crate::compose;
use crate::control::{
    launch_object, post_effect_failure, precomposed, Config, Request, Runtime, KILL_ESCALATION_MS, ROOT_REAP,
};
use crate::frame::code;
use crate::host::{
    Cleanup, CreateArgs, ExitCell, ExitInfo, Host, Launch, Msg, Outcome, ResizeFailure, Spawned, WorstCase,
};
use crate::journal;
use crate::refusal::{fault, Code, Refusal, Stage};
use crate::session::{mailbox, SessionCx, SessionHandle};
use crate::session_pty::{SessionPty, SlaveEnd, FD_FORK_LOCK};
use crate::wire::{Outbound, SocketSender};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::io;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(feature = "fault-hooks")]
use crate::fault;

#[cfg(target_os = "macos")]
use crate::darwin;
#[cfg(target_os = "linux")]
use crate::linux;

#[cfg(feature = "fault-hooks")]
mod faults;
#[cfg(all(test, target_os = "macos"))]
mod initial_retention_test;
// An evaluation probe: it records the host's system SQLite and this process's own identity, so it belongs to the
// harness build only.
#[cfg(all(test, target_os = "macos", feature = "fault-hooks"))]
mod sqlite_identity_test;

/// Where a test can interleave an action with the spawn and cleanup paths (R17). The production paths call these
/// and nothing else: in a production build every one is empty, and only here does the build differ. A test arms one
/// for the thread it runs on.
mod seams {
    #[cfg(all(test, target_os = "linux"))]
    use std::cell::Cell;
    #[cfg(test)]
    use std::cell::RefCell;

    #[cfg(test)]
    thread_local! {
        /// Runs once, right after `cleanup`'s first snapshot, so a test can place the root's exit (and a late
        /// same-group child) between that snapshot and the signal.
        pub(in crate::posix) static AFTER_FIRST_SNAPSHOT: RefCell<Option<Box<dyn FnOnce()>>> =
            const { RefCell::new(None) };
        /// The next `kill_and_reap_released` wait on this thread answers `ECHILD`.
        #[cfg(target_os = "linux")]
        pub(in crate::posix) static FORCE_ECHILD: Cell<bool> = const { Cell::new(false) };
        /// Runs on the creating thread right before the failed-create branch waits for its child.
        #[cfg(target_os = "linux")]
        pub(in crate::posix) static BEFORE_CHILD_WAIT: RefCell<Option<Box<dyn FnOnce(i32)>>> =
            const { RefCell::new(None) };
        /// The next create's gate owner panics right after it released the gate.
        #[cfg(target_os = "linux")]
        pub(in crate::posix) static PANIC_AFTER_RELEASE: Cell<bool> = const { Cell::new(false) };
        /// Runs on the creating thread while it holds the reaper gate, right after the `stopped` check passed and
        /// before any pipe or fork.
        #[cfg(target_os = "linux")]
        pub(in crate::posix) static AFTER_STOPPED_CHECK: RefCell<Option<Box<dyn FnOnce()>>> =
            const { RefCell::new(None) };
    }

    pub(in crate::posix) fn after_first_snapshot() {
        #[cfg(test)]
        if let Some(hook) = AFTER_FIRST_SNAPSHOT.with(|cell| cell.borrow_mut().take()) {
            hook();
        }
    }

    #[cfg(target_os = "linux")]
    pub(in crate::posix) fn after_stopped_check() {
        #[cfg(test)]
        if let Some(hook) = AFTER_STOPPED_CHECK.with(|cell| cell.borrow_mut().take()) {
            hook();
        }
    }

    #[cfg(target_os = "linux")]
    pub(in crate::posix) fn before_child_wait(pid: i32) {
        #[cfg(test)]
        if let Some(hook) = BEFORE_CHILD_WAIT.with(|cell| cell.borrow_mut().take()) {
            hook(pid);
        }
        #[cfg(not(test))]
        let _ = pid;
    }

    /// `waitpid` on a child of this process.
    pub(in crate::posix) fn waitpid(pid: i32, status: &mut libc::c_int, flags: libc::c_int) -> libc::pid_t {
        #[cfg(all(test, target_os = "linux"))]
        if FORCE_ECHILD.with(|forced| forced.get()) {
            // SAFETY: errno is thread-local; this stands in for a third party having reaped the root.
            unsafe { *libc::__errno_location() = libc::ECHILD };
            return -1;
        }
        // SAFETY: waitpid on our own child with a valid status pointer.
        unsafe { libc::waitpid(pid, status, flags) }
    }

    /// Whether the gate owner of a create fails after it released the gate. Read on the creating thread (the
    /// owner runs on another one) and handed to the owner.
    #[derive(Clone, Copy)]
    pub(in crate::posix) struct GateRelease {
        #[cfg(all(test, target_os = "linux"))]
        fail: bool,
    }

    impl GateRelease {
        pub(in crate::posix) fn read() -> Self {
            Self {
                #[cfg(all(test, target_os = "linux"))]
                fail: PANIC_AFTER_RELEASE.with(|flag| flag.get()),
            }
        }
        pub(in crate::posix) fn after_release(self) {
            #[cfg(all(test, target_os = "linux"))]
            if self.fail {
                panic!("test: the gate owner failed after it released the gate");
            }
        }
    }
}

/// Worst-case encoded sizes used by the pre-spawn `SESSION_OWNED` bound (5.1.3).
const TTY_NAME_MAX: usize = 32;

/// A process table: pid -> (parent, process group, start identity).
pub(crate) type Table = HashMap<i32, (i32, i32, String)>;

/// R19: the first and the longest pause between two reads of a snapshot whose scan raced a change.
const RETRY_PAUSE_FIRST_MS: u64 = 1;
const RETRY_PAUSE_MAX_MS: u64 = 16;

/// A snapshot whose scan raced a change retries (R19). Each retry waits first: `RETRY_PAUSE_FIRST_MS`, doubling to
/// `RETRY_PAUSE_MAX_MS`, and never past `deadline`, so a process table that keeps changing is re-read a few dozen
/// times a second, not in a spin. This is a rate limit, not a wait for state: there is no event to wait on, the
/// table is simply read again.
pub(crate) fn retry_pause(attempt: &mut u32, deadline: Instant) {
    let pause = Duration::from_millis((RETRY_PAUSE_FIRST_MS << (*attempt).min(8)).min(RETRY_PAUSE_MAX_MS));
    *attempt += 1;
    thread::sleep(pause.min(deadline.saturating_duration_since(Instant::now())));
}
/// The POSIX platform. Its one piece of shared state is the set of slave names this runtime generation has handed
/// out and not yet retired (5.2 uniqueness).
#[derive(Default)]
pub struct PosixHost {
    ttys: Mutex<HashSet<String>>,
    /// The control socket, set at boot from `--control-fd`.
    control_fd: Option<i32>,
}

/// What a POSIX sidecar reads from its command line.
#[derive(Default)]
pub struct Options {
    control_fd: Option<i32>,
}

/// The platform half of a session handle.
pub struct PosixSession {
    pub(crate) pty: Arc<SessionPty>,
    pub(crate) spec: Option<ShutdownSpec>,
}

/// What only the session's own thread touches.
pub struct Owned {
    pub(crate) pid: i32,
    pub(crate) root: Value,
    pub(crate) descendants: Table,
    /// This session's own exact birth evidence (Linux); never shared with another session.
    pub(crate) births: Births,
}

#[cfg(target_os = "linux")]
use linux::Births;
/// macOS identifies a process by its full-resolution start time and keeps no birth ledger. It only remembers the
/// live members of the root's former group that this session never admitted: pid -> (parent, group, start). They
/// are reported, never signalled.
#[cfg(target_os = "macos")]
#[derive(Default)]
pub(crate) struct Births(Mutex<HashMap<i32, (i32, i32, String)>>);

pub(crate) fn known_owned(owned: &Owned) -> Table {
    let mut known = owned.descendants.clone();
    if let Some(start) = owned.root["start_value"].as_str() {
        known.insert(owned.pid, (0, owned.pid, start.to_owned()));
    }
    known
}
pub(crate) fn retain_owned(owned: &mut Owned, table: &Table) {
    let pid = owned.pid;
    owned.descendants.extend(
        table
            .iter()
            .filter(|(child, _)| **child != pid)
            .map(|(&child, entry)| (child, entry.clone())),
    );
    // The table is a verified snapshot: a retained identity it does not list is absent, dead or replaced. Live
    // detached descendants are seeds of the snapshot, so they stay listed and kept. Pruning here is what lets the
    // published set shrink, and so lets Python learn that the last descendant is gone (R9, K3).
    owned
        .descendants
        .retain(|child, _| table.contains_key(child));
    #[cfg(target_os = "linux")]
    owned.births.retain_live(pid, table);
}

/// Event-driven wait for owned (possibly non-child) processes to exit; returns those still running at `deadline`.
/// macOS uses kqueue `NOTE_EXIT`; Linux uses pidfds.
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

/// Signal an owned process whose identity (`expected_start`) was just matched against a fresh table. Linux binds
/// the signal to the verified process instance through a pidfd; macOS signals by number right after the match.
fn signal_owned(pid: i32, expected_start: &str, signal: i32, group: bool, births: &Births) {
    #[cfg(target_os = "macos")]
    {
        let _ = (expected_start, births);
        // SAFETY: identity (pid + birth) matched against a fresh table by the caller.
        unsafe {
            libc::kill(if group { -pid } else { pid }, signal);
        }
    }
    #[cfg(target_os = "linux")]
    {
        linux::signal(pid, expected_start, signal, group, births);
    }
}

#[cfg(target_os = "macos")]
fn snapshot(initial_child: Option<i32>, known: &Table, _births: &mut Births) -> io::Result<Table> {
    darwin::snapshot(initial_child, known)
}
#[cfg(target_os = "linux")]
fn snapshot(initial_child: Option<i32>, known: &Table, births: &mut Births) -> io::Result<Table> {
    linux::snapshot(initial_child, known, births)
}

/// What the gate admits for a root blocked before its `exec`: its own identity, read once, which is what a
/// snapshot of it would return; any other shape falls back to that snapshot.
fn gated_root_table(pid: i32, births: &mut Births) -> io::Result<Table> {
    #[cfg(target_os = "linux")]
    let row = linux::gated_root(pid, births)?;
    #[cfg(target_os = "macos")]
    let row = darwin::gated_root(pid)?;
    match row {
        Some(entry) => Ok(Table::from([(pid, entry)])),
        None => snapshot(Some(pid), &Table::new(), births),
    }
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

fn identity(pid: i32, entry: &(i32, i32, String)) -> Value {
    json!({"pid":pid,"pgid":entry.1,"start_kind":"posix-lstart","start_value":entry.2})
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

fn journal_failed(message: impl Into<std::borrow::Cow<'static, str>>) -> io::Error {
    fault(Code::OwnershipJournalFailed, message)
}

/// Owner half of the ownership gate: learn the gated child's pid, take its identity, make it durable, and only
/// then release the child. Any failure returns before the release.
struct GateContext<'a> {
    runtime: &'a Runtime<PosixHost>,
    argv0: &'a str,
    id: &'a str,
    generation: &'a str,
    request: u64,
    /// Harness build only: a release file the owner waits for before the journal append.
    #[cfg(feature = "fault-hooks")]
    hold: Option<&'a str>,
    /// Harness build only: a release file the owner waits for before it reads the child's report, so the root
    /// exists as a sidecar child with no reservation yet.
    #[cfg(feature = "fault-hooks")]
    before_read: Option<&'a str>,
    /// The Linux reaper (scoped mode) and the slot, owned by `spawn_gated`, that holds this root's reservation.
    #[cfg(target_os = "linux")]
    reaper: Option<&'a Arc<linux::Reaper>>,
    #[cfg(target_os = "linux")]
    slot: &'a Mutex<Option<linux::RootReservation>>,
    release: seams::GateRelease,
}
fn gate_owner(report_r: i32, gate_w: i32, ctx: &GateContext) -> io::Result<(Table, Value, Births)> {
    let GateContext {
        runtime,
        argv0,
        id,
        generation,
        request,
        #[cfg(feature = "fault-hooks")]
        hold,
        #[cfg(feature = "fault-hooks")]
        before_read,
        #[cfg(target_os = "linux")]
        reaper,
        #[cfg(target_os = "linux")]
        slot,
        release: gate_release,
    } = *ctx;
    #[cfg(feature = "fault-hooks")]
    if let Some(path) = before_read {
        fault::wait_for_file_bounded(path)?;
    }
    let mut buffer = [0u8; 4];
    let mut got = 0;
    while got < 4 {
        // SAFETY: valid owned descriptor and in-bounds buffer slice.
        let n = unsafe { libc::read(report_r, buffer[got..].as_mut_ptr().cast(), 4 - got) };
        if n < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        if n <= 0 {
            return Err(journal_failed("OWNERSHIP_JOURNAL_FAILED: child never reported"));
        }
        got += n as usize;
    }
    let pid = i32::from_ne_bytes(buffer);
    // From here until its own waiter has taken its status, only that waiter may reap this root. The key is
    // written before the identity read and the journal append; `spawn_gated` has held the reaper's gate since
    // before the fork, so the interval [fork, here] is closed too.
    #[cfg(target_os = "linux")]
    if let Some(reaper) = reaper {
        *slot.lock().unwrap_or_else(|e| e.into_inner()) = Some(reaper.reserve(pid));
    }
    let mut births = Births::default();
    let table = gated_root_table(pid, &mut births)
        .map_err(|_| journal_failed("OWNERSHIP_JOURNAL_FAILED: identity unavailable"))?;
    let root = table
        .get(&pid)
        .map(|entry| identity(pid, entry))
        .filter(|root| root["start_value"].as_str().is_some_and(|v| !v.is_empty()))
        .ok_or_else(|| journal_failed("OWNERSHIP_JOURNAL_FAILED: identity unavailable"))?;
    let wire = &runtime.wire;
    let record = journal::record(wire, &root, argv0, id, generation);
    #[cfg(feature = "fault-hooks")]
    if let Some(path) = hold {
        fault::mark_hold_entered(path);
        fault::wait_for_file_bounded(path)?;
    }
    journal::append(&wire.journal, &record)
        .map_err(|error| journal_failed(format!("OWNERSHIP_JOURNAL_FAILED: {}", error.kind() as u32)))?;
    runtime.release_gate(request, || {
        // SAFETY: valid owned descriptor, one-byte static buffer.
        if unsafe { libc::write(gate_w, b"G".as_ptr().cast(), 1) } != 1 {
            return Err(journal_failed("OWNERSHIP_JOURNAL_FAILED: gate release"));
        }
        Ok(())
    })?;
    gate_release.after_release();
    Ok((table, root, births))
}

/// A composition refusal as the internal error the wire reports, with its stage.
fn refused(error: compose::ComposeError) -> io::Error {
    use compose::ComposeError::*;
    let (stage, why) = match error {
        Shape(why) => (Some(Stage::LaunchShape), ("launch_shape", why)),
        Option(why) => (Some(Stage::Option), ("option", why)),
        Compose(why) => (Some(Stage::Compose), ("compose", why)),
        Validate(why) => (Some(Stage::Validate), ("validate", why)),
        Composition(why) => (Some(Stage::Composition), ("composition", why)),
        Credential(why) => (None, ("credential", why)),
    };
    let refusal = Refusal::bad(format!("invalid {}: {}", why.0, why.1));
    match stage {
        Some(stage) => refusal.staged(stage).into(),
        None => refusal.into(),
    }
}

/// The child's argv from the wire-2 `launch` object (delta section 2). The pre-composed `argv` shape is only what
/// a subject that composes in Python is admitted to use; every other subject is composed here.
fn launch_argv(agent: &str, args: &Value) -> io::Result<Launch> {
    let (launch, shape) = launch_object(args)?;
    compose::admit(agent, shape, false).map_err(refused)?;
    if shape == "argv" {
        return precomposed(launch);
    }
    let composed = compose::compose(agent, launch).map_err(refused)?;
    let mut argv = vec![composed.program];
    argv.extend(composed.args);
    Ok(Launch {
        argv,
        argv_source: composed.argv_source,
        argv0: composed.argv0,
        outer: composed.outer,
    })
}

/// How a root's status becomes the wire's exit: a code, or a signal as its negated number.
fn exit_info(code: Option<i32>, signal: Option<i32>, unverified: bool) -> ExitInfo {
    ExitInfo {
        exit_code: code.or(signal.map(|n| -n)).map(i64::from),
        signal,
        unverified,
    }
}

/// A root's reservation against the Linux reaper, shared so that a failed watcher start still holds it while the
/// sidecar kills and waits for the root itself. Empty when the reaper is off.
#[cfg(target_os = "linux")]
type RootSlot = Arc<Mutex<Option<linux::RootReservation>>>;
#[cfg(not(target_os = "linux"))]
type RootSlot = ();

fn watch_exit(pid: i32, cell: Arc<ExitCell>, tx: mpsc::Sender<Msg>, slot: RootSlot) -> io::Result<()> {
    // Scoped mode is read here, on the creating thread: in scoped mode the reservation makes this the only waiter
    // allowed to take the root's status, so `ECHILD` means a third party took it.
    #[cfg(target_os = "linux")]
    let scoped = linux::scoped();
    #[cfg(not(target_os = "linux"))]
    let scoped = false;
    thread::Builder::new()
        .name("exit-watch".into())
        .spawn(move || {
            let mut status: libc::c_int = 0;
            let info = loop {
                // SAFETY: blocking wait for our own child; `status` is a valid out pointer.
                let reaped = unsafe { libc::waitpid(pid, &mut status, 0) };
                if reaped == pid {
                    break if libc::WIFEXITED(status) {
                        exit_info(Some(libc::WEXITSTATUS(status)), None, false)
                    } else {
                        exit_info(None, Some(libc::WTERMSIG(status)), false)
                    };
                }
                if reaped < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                let echild =
                    reaped < 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ECHILD);
                break exit_info(None, None, scoped && echild); // not our child any more: status unknown
            };
            // The exact wait has returned: the reaper may take a pid this root held.
            #[cfg(target_os = "linux")]
            drop(slot.lock().unwrap_or_else(|e| e.into_inner()).take());
            #[cfg(not(target_os = "linux"))]
            let _ = slot;
            cell.set(info);
            let _ = tx.send(Msg::Exited);
        })?;
    Ok(())
}

/// A released root whose session could not be set up: signal its group by the identity just recorded and report
/// whether it was verified reaped (E1 5.1.3). It is our own child, so the wait is a real `waitpid`. Only the
/// **root** is waited for: the group was just signalled and the only claim made on `effect:none` is that the root
/// (the one process that ran) is gone; no descendant had time to be recorded (no snapshot ran yet), and anything
/// that escaped the group is what the generation's journal-based cleanup is for.
fn kill_and_reap_released(pid: i32) -> bool {
    // In Linux scoped mode the root is reserved, so only this wait may take its status: `ECHILD` is then a third
    // party having taken it, which is not a verified reap.
    #[cfg(target_os = "linux")]
    let echild_is_reaped = !linux::scoped();
    #[cfg(not(target_os = "linux"))]
    let echild_is_reaped = true;
    // SAFETY: the root is our own child, a session/group leader we just created (pgid == pid); a negative pid
    // addresses exactly that group.
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
        let reaped = seams::waitpid(pid, &mut status, flags);
        if reaped == pid {
            return true;
        }
        if reaped < 0 {
            match io::Error::last_os_error().raw_os_error() {
                Some(libc::EINTR) => continue,
                Some(libc::ECHILD) => return echild_is_reaped, // already reaped elsewhere
                _ => return false,
            }
        }
        return false; // still running at the bound
    }
}

impl PosixHost {
    fn spawn_session(
        &self,
        runtime: &Runtime<Self>,
        request: u64,
        parsed: &CreateArgs,
    ) -> io::Result<Spawned<Self>> {
        // Python checked the directory before it sent CREATE; it can vanish in between (delta 2.7).
        if !std::path::Path::new(&parsed.cwd).is_dir() {
            return Err(Refusal::new(Code::SpawnFailed, "cwd is not a directory at the spawn boundary")
                .staged(Stage::Cwd)
                .into());
        }
        let (pty, slave) = SessionPty::open(parsed.rows, parsed.cols)?;
        // Uniqueness of the slave name among this generation's live sessions (5.2, 6.4).
        if !self.ttys.lock().unwrap().insert(pty.tty().to_owned()) {
            return Err(Refusal::bad("duplicate tty").into());
        }
        let tty = pty.tty().to_owned();
        let result = self.spawn_gated(parsed, pty, slave, runtime, request);
        if result.is_err() {
            self.ttys.lock().unwrap().remove(&tty);
        }
        result
    }

    fn spawn_gated(
        &self,
        parsed: &CreateArgs,
        pty: SessionPty,
        slave: SlaveEnd,
        runtime: &Runtime<Self>,
        request: u64,
    ) -> io::Result<Spawned<Self>> {
        let argv = &parsed.argv;
        let mut command = Command::new(&argv[0]);
        command
            .args(&argv[1..])
            .current_dir(&parsed.cwd)
            .env_clear();
        for (key, value) in &parsed.env {
            command.env(key, value);
        }
        command
            .stdin(Stdio::from(slave.0.try_clone()?))
            .stdout(Stdio::from(slave.0.try_clone()?))
            .stderr(Stdio::from(slave.0));
        // Ownership gate (mandatory in wire v1): the root must not execute its target before its identity is
        // durable. The forked child reports its pid and then BLOCKS on a gate pipe before exec; the owner records
        // the identity, appends+fsyncs the journal and only then releases the gate. EOF on the gate (owner dead,
        // or journal refused) makes the child fail its own spawn, so no target ever runs unnamed. Pipe creation
        // and the spawn are serialised process-wide so no concurrent fork can inherit the gate's write end (which
        // would keep the gate open after the owner's death).
        let _spawn_lock = FD_FORK_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Linux scoped mode: the reaper may not wait for any pid from before the fork until this root's
        // reservation is in place and it is out of reach of the failure paths below. Lock order is
        // `FD_FORK_LOCK` -> the reaper's gate -> its reservation set.
        #[cfg(target_os = "linux")]
        let reaper = linux::reaper();
        #[cfg(target_os = "linux")]
        let reaper_gate = reaper.as_ref().map(|reaper| reaper.hold_gate());
        // The reaper thread ended: adopted orphans would never be consumed again. Refuse before any pipe or fork,
        // so the answer is `SPAWN_FAILED` with `effect:none`; sessions already running are not touched.
        #[cfg(target_os = "linux")]
        if reaper.as_ref().is_some_and(|reaper| reaper.stopped()) {
            return Err(io::Error::other(
                "the Linux child reaper has stopped; no new session can be started safely",
            ));
        }
        #[cfg(target_os = "linux")]
        seams::after_stopped_check();
        // The reservation lives here, not in the gate-owner thread, so a panic of that thread cannot drop it.
        #[cfg(target_os = "linux")]
        let slot: Mutex<Option<linux::RootReservation>> = Mutex::new(None);
        let (report_r, report_w) = cloexec_pipe()?;
        let (gate_r, gate_w) = match cloexec_pipe() {
            Ok(pair) => pair,
            Err(error) => {
                close_fds(&[report_r, report_w]);
                return Err(error);
            }
        };
        // SAFETY: only async-signal-safe native calls between fork and exec (setsid, signal, ioctl, close,
        // getpid, write, read on plain integers); no locks, no allocation, no std I/O.
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
        let (child, table, root, births) = {
            let (id_ref, generation_ref, argv0) = (&parsed.id, &parsed.generation, &argv[0]);
            #[cfg(feature = "fault-hooks")]
            let hold = fault::journal_hold(&parsed.pane);
            #[cfg(feature = "fault-hooks")]
            let before_read = fault::before_report_read(&parsed.pane);
            #[cfg(target_os = "linux")]
            let (reaper_ref, slot_ref) = (reaper.as_ref(), &slot);
            let release = seams::GateRelease::read();
            let (spawned, verdict) = thread::scope(|scope| {
                let watcher = scope.spawn(move || {
                    // Owner side of the gate: every path below ends by closing the gate's write end (released
                    // only by the explicit `G`), so a refusal or panic means EOF.
                    let verdict = gate_owner(
                        report_r,
                        gate_w,
                        &GateContext {
                            runtime,
                            argv0,
                            id: id_ref,
                            generation: generation_ref,
                            request,
                            #[cfg(feature = "fault-hooks")]
                            hold: hold.as_deref(),
                            #[cfg(feature = "fault-hooks")]
                            before_read: before_read.as_deref(),
                            #[cfg(target_os = "linux")]
                            reaper: reaper_ref,
                            #[cfg(target_os = "linux")]
                            slot: slot_ref,
                            release,
                        },
                    );
                    close_fds(&[report_r, gate_w]);
                    verdict
                });
                let spawned = command.spawn();
                // The parent's copies of the child-side ends are no longer needed; closing the report write end
                // also lets the watcher observe a child that died before reporting.
                close_fds(&[report_w, gate_r]);
                (spawned, watcher.join())
            });
            let verdict = match verdict {
                Ok(verdict) => verdict,
                Err(_) => Err(journal_failed("OWNERSHIP_JOURNAL_FAILED: watcher panicked")),
            };
            match (spawned, verdict) {
                (Ok(child), Ok((table, root, births))) => (child, table, root, births),
                (Ok(mut child), Err(error)) => {
                    // Cannot happen (the gate is released only after success); stay safe regardless.
                    let _ = child.kill();
                    // This wait is a third waiter for the root's pid: the reservation must be in place, and stay
                    // until `wait` has returned.
                    #[cfg(target_os = "linux")]
                    if let Some(reaper) = &reaper {
                        let mut held = slot.lock().unwrap_or_else(|e| e.into_inner());
                        if held.is_none() {
                            *held = Some(reaper.reserve(child.id() as i32));
                        }
                    }
                    #[cfg(target_os = "linux")]
                    seams::before_child_wait(child.id() as i32);
                    let _ = child.wait();
                    return Err(error);
                }
                (Err(_), Err(error)) => return Err(error),
                (Err(error), Ok(_)) => return Err(error), // exec failed after a durable record: stale row only
            }
        };
        // The root exists and is reserved: the reaper may run again, and has a new child to wait on.
        #[cfg(target_os = "linux")]
        {
            drop(reaper_gate);
            if let Some(reaper) = &reaper {
                reaper.note_spawned();
            }
        }
        // Every sidecar copy of the slave closes now, so the CLI tree closing its own is observable as EOF on the
        // master.
        drop(command);
        let pid = child.id() as i32;
        drop(child); // reaped only by the exit watcher below
        let pty = Arc::new(pty);
        let (tx, mailbox, exit) = mailbox();
        // Shared with the watcher: if it cannot start, the reservation is still held while the root is waited for.
        #[cfg(target_os = "linux")]
        let root_slot: RootSlot = Arc::new(slot);
        #[cfg(not(target_os = "linux"))]
        let root_slot: RootSlot = ();
        #[cfg(feature = "fault-hooks")]
        let watched = if fault::watch_exit_fails(&parsed.pane) {
            Err(fault::watch_exit_error())
        } else {
            watch_exit(pid, exit.clone(), tx.clone(), root_slot.clone())
        };
        #[cfg(not(feature = "fault-hooks"))]
        let watched = watch_exit(pid, exit.clone(), tx.clone(), root_slot.clone());
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
        let mut owned = Owned {
            pid,
            root,
            descendants: Table::new(),
            births,
        };
        retain_owned(&mut owned, &table);
        Ok(Spawned {
            platform: PosixSession {
                pty,
                spec: adapters::shutdown(&parsed.agent),
            },
            owned,
            exit,
            tx,
            mailbox,
        })
    }
}

// ---- lifecycle ---------------------------------------------------------------------------------

/// Signal recorded identities, wait event-driven, escalate, verify (13.1 kill policy). `hangup` is true for a kill:
/// unless the adapter defers it, the sidecar's master closes right after the first signal and `SESSION_HANGUP`
/// tells Python (5.5 R-e).
fn cleanup(
    cx: &mut SessionCx<'_, PosixHost>,
    force: bool,
    hangup: bool,
) -> io::Result<Vec<Value>> {
    let handle = cx.handle;
    let pid = cx.state.owned.pid;
    if handle.exit.get().is_some() && cx.state.owned.descendants.is_empty() {
        // Nothing recorded to signal, but a member born since the last snapshot may still run in the root's group.
        record_former_group(&cx.state.owned)?;
        return Ok(with_unresolved(Vec::new(), &cx.state.owned.births));
    }
    let before = snapshot(None, &known_owned(&cx.state.owned), &mut cx.state.owned.births)?;
    retain_owned(&mut cx.state.owned, &before);
    seams::after_first_snapshot();
    let root_start = cx.state.owned.root["start_value"].as_str().map(String::from);
    let root_live = handle.exit.get().is_none()
        && before
            .get(&pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == root_start.as_deref());
    let spec = handle.platform.spec;
    let graceful = spec.is_some_and(|spec| spec.graceful);
    let signal = if force && !graceful {
        libc::SIGKILL
    } else {
        libc::SIGTERM
    };
    if root_live {
        // The root's PID+birth is matched and its own child group remains owned.
        signal_owned(
            pid,
            root_start.as_deref().unwrap_or_default(),
            signal,
            true,
            &cx.state.owned.births,
        );
    }
    // Signalled owned descendants, confirmed gone before cleanup reports them absent.
    let mut signalled: Vec<(i32, (i32, i32, String))> = Vec::new();
    for (&child, old) in &cx.state.owned.descendants {
        if before.get(&child).is_some_and(|entry| entry.2 == old.2) {
            signal_owned(child, &old.2, signal, false, &cx.state.owned.births);
            signalled.push((child, old.clone()));
        }
    }
    if hangup && !spec.is_some_and(|spec| spec.defer_master_close) {
        // The kernel hangs the PTY up when the last master closes: the baseline's "signal, then close the master"
        // effect (SIGHUP) is preserved without Rust ever reading the PTY.
        handle.platform.pty.close_master();
        cx.runtime.wire.out.event(code::SESSION_HANGUP, handle.meta());
    }
    if let Some(spec) = spec.filter(|spec| spec.graceful) {
        // Declared graceful: wait event-driven up to the declared grace for the root.
        handle.exit.wait(Duration::from_millis(spec.grace_ms));
        cx.send_exited();
    } else if !force {
        // Conditional wait (replaces the baseline's unconditional 1 s sleep): returns as soon as the root and
        // every signalled descendant exited, at the declared escalation bound.
        let deadline = Instant::now() + Duration::from_millis(KILL_ESCALATION_MS);
        handle
            .exit
            .wait(deadline.saturating_duration_since(Instant::now()));
        cx.send_exited();
        let pids: Vec<i32> = signalled.iter().map(|(child, _)| *child).collect();
        wait_exit(&pids, deadline);
    }
    let after = snapshot(None, &known_owned(&cx.state.owned), &mut cx.state.owned.births)?;
    retain_owned(&mut cx.state.owned, &after);
    if handle.exit.get().is_none()
        && after
            .get(&pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == root_start.as_deref())
    {
        // The root identity was re-verified against the fresh table.
        signal_owned(
            pid,
            root_start.as_deref().unwrap_or_default(),
            libc::SIGKILL,
            true,
            &cx.state.owned.births,
        );
    }
    for (&child, old) in &cx.state.owned.descendants {
        if after.get(&child).is_some_and(|entry| entry.2 == old.2) {
            signal_owned(child, &old.2, libc::SIGKILL, false, &cx.state.owned.births);
            signalled.push((child, old.clone()));
        }
    }
    let deadline = Instant::now() + ROOT_REAP;
    // A blocked PTY write can delay even SIGKILL until a master drains: Python keeps reading.
    if handle.exit.wait(ROOT_REAP).is_none() {
        return Err(io::Error::other("root reap timeout"));
    }
    cx.send_exited();
    signalled.sort_by_key(|(child, _)| *child);
    signalled.dedup_by_key(|(child, _)| *child);
    let pids: Vec<i32> = signalled.iter().map(|(child, _)| *child).collect();
    let still_running = wait_exit(&pids, deadline);
    let final_table = snapshot(None, &known_owned(&cx.state.owned), &mut cx.state.owned.births)?;
    retain_owned(&mut cx.state.owned, &final_table);
    record_former_group(&cx.state.owned)?;
    Ok(with_unresolved(
        survivors_after_cleanup(
            &cx.state.owned.descendants,
            &final_table,
            &signalled,
            &still_running,
        ),
        &cx.state.owned.births,
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
        // Not gone by the deadline: a survivor even if the snapshot no longer sees it. A child whose exit the
        // kqueue wait observed is not one: `owned_not_gone` is true for a zombie (it is still reachable and no
        // longer listed).
        if !confirmed_exited(child)
            && !final_table.contains_key(child)
            && darwin::owned_not_gone(*child, &old.2)
        {
            survivors.push(identity(*child, old));
        }
    }
    survivors
}

/// Final verification: the root's former process group (on Linux also its session) is scanned for live members this
/// session never admitted; `with_unresolved` then reports them. They are only recorded, never signalled. An error
/// means the scan could not establish whether any remain, and the cleanup fails closed.
///
/// A numeric PGID or SID is reusable only after its old group or session is empty, so a live process found under it
/// is either a member never admitted or a stranger that took the id afterwards; the stranger is only listed, which
/// makes the receipt conservatively incomplete.
fn record_former_group(owned: &Owned) -> io::Result<()> {
    #[cfg(target_os = "linux")]
    {
        owned.births.record_unadmitted_former_group(owned.pid)
    }
    #[cfg(target_os = "macos")]
    {
        let mut refused = owned.births.0.lock().unwrap();
        let mut members = darwin::former_group(owned.pid)?;
        members.extend(darwin::former_session(owned.pid)?);
        for (pid, entry) in members {
            if !owned
                .descendants
                .get(&pid)
                .is_some_and(|old| old.2 == entry.2)
            {
                refused.insert(pid, entry);
            }
        }
        Ok(())
    }
}

/// Why a member is listed without having been signalled. A member that exits after the final scan but before the
/// receipt is published stays listed: the receipt is then conservatively incomplete, never falsely complete.
const UNADMITTED: &str = "not admitted by this session, so reported and never signalled; the list is the state at the final scan and may include a member that exited since";

/// Add the live members this session never admitted (the group fallback's refusals and the former-group scan) to
/// `survivors`, so cleanup does not report complete while they run.
pub(crate) fn with_unresolved(mut survivors: Vec<Value>, births: &Births) -> Vec<Value> {
    #[cfg(target_os = "linux")]
    let refused = births.refused_alive();
    #[cfg(target_os = "macos")]
    let refused = darwin::still_running(&births.0.lock().unwrap());
    for (pid, entry) in refused {
        if !survivors.iter().any(|known| known["pid"] == pid) {
            let mut member = identity(pid, &entry);
            member["note"] = json!(UNADMITTED);
            survivors.push(member);
        }
    }
    survivors
}

/// A cleanup's result as the outcome `SESSION_CLEANED` is built from: a failure to establish the survivors fails
/// the cleanup closed.
fn outcome(survivors: io::Result<Vec<Value>>) -> Outcome {
    match survivors {
        Ok(survivors) => Outcome {
            survivors,
            ..Outcome::default()
        },
        Err(error) => Outcome {
            failure: Some(error.to_string()),
            ..Outcome::default()
        },
    }
}

// ---- the host ------------------------------------------------------------------------------------

fn read_control(fd: i32, buffer: &mut [u8]) -> io::Result<usize> {
    // SAFETY: valid descriptor and in-bounds buffer slice.
    let n = unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) };
    if n < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(n as usize)
    }
}

impl Host for PosixHost {
    type Session = PosixSession;
    type Owned = Owned;
    type Observer = &'static crate::observe::adapters::ObserverFacts;
    type Link = i32;
    type Options = Options;
    /// A POSIX session has no settle window: it settles on Python's end-of-stream report alone.
    type Unsettled = std::convert::Infallible;

    const PTY_BACKEND: &'static str = "posix";
    const HANDOFF: &'static str = "scm_rights";
    const SERVICING: bool = false;
    const OBSERVES: bool = true;
    const OPTIONS: &'static [&'static str] = &["--control-fd"];

    fn set_option(options: &mut Options, _name: &str, value: String) {
        options.control_fd = value.parse::<u64>().ok().map(|fd| fd as i32);
    }
    fn check_options(options: &Options) -> Result<(), String> {
        options
            .control_fd
            .map(drop)
            .ok_or_else(|| "--control-fd required".to_owned())
    }

    fn boot(_config: &Config, options: Options) -> io::Result<Self> {
        let fd = options
            .control_fd
            .ok_or_else(|| io::Error::other("control descriptor is not open"))?;
        // SAFETY: the descriptor was passed by the owner (`pass_fds`); flags only.
        if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
            return Err(io::Error::other("control descriptor is not open"));
        }
        // A PTY child must never inherit the control channel (it could pass descriptors for the whole backend);
        // FD_CLOEXEC above, and a closed peer is EPIPE here, never a signal.
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
        // Linux: choose scoped ownership scans (subreaper + reaper) or the full-scan fallback before any session
        // can exist, so the choice is fixed for the process lifetime.
        #[cfg(target_os = "linux")]
        linux::enable_scoped_mode();
        Ok(Self {
            control_fd: Some(fd),
            ..Self::default()
        })
    }

    /// The macOS evaluation harness reads the sidecar's own process facts off the `ready` line, from the product
    /// binary itself (`runtime_self_probe.py` asserts it), so this probe is the one that stays in the product build.
    #[cfg(target_os = "macos")]
    fn ready(&self, ready: &mut Value) {
        match darwin::self_process() {
            Ok(observation) => ready["self_process"] = observation,
            Err(error) => {
                ready["self_process_error"] =
                    json!({"operation":"darwin-self-process","errno":error.raw_os_error()});
            }
        }
    }

    fn open_link(&self) -> io::Result<i32> {
        self.control_fd
            .ok_or_else(|| io::Error::other("control descriptor is not open"))
    }
    fn link_read(&self, link: &i32, buffer: &mut [u8]) -> io::Result<usize> {
        read_control(*link, buffer)
    }
    fn link_write(&self, link: &i32, out: &Outbound) -> io::Result<()> {
        out.run_writer(&mut SocketSender(*link))
    }
    fn link_break(&self, link: &i32) {
        // SAFETY: shutdown on the control socket; the reader then sees end of file.
        unsafe {
            libc::shutdown(*link, libc::SHUT_RDWR);
        }
    }

    fn env_key_ok(key: &str) -> bool {
        !key.contains(['\0', '='])
    }
    fn launch(&self, agent: &str, args: &Value, _env: &[(String, String)]) -> io::Result<Launch> {
        launch_argv(agent, args)
    }
    fn worst_case(&self) -> WorstCase {
        WorstCase {
            root: json!({"pid":99_999_999,"pgid":99_999_999,"start_kind":"posix-starttime",
                "start_value":"x".repeat(40)}),
            pty: json!({"tty":"x".repeat(TTY_NAME_MAX)}),
            outer: true,
        }
    }
    fn spawn(&self, runtime: &Runtime<Self>, request: u64, args: &CreateArgs) -> io::Result<Spawned<Self>> {
        self.spawn_session(runtime, request, args)
    }
    fn root_record(&self, owned: &Owned) -> Value {
        owned.root.clone()
    }
    fn pty_record(&self, session: &PosixSession) -> io::Result<Value> {
        Ok(session.pty.owned_record())
    }
    fn send_owned(
        &self,
        runtime: &Runtime<Self>,
        handle: &Arc<SessionHandle<Self>>,
        meta: Value,
    ) -> io::Result<()> {
        #[cfg(feature = "fault-hooks")]
        if faults::send_owned(handle, meta.clone(), runtime) {
            return Ok(());
        }
        runtime.wire.out.session_owned(meta, handle.platform.pty.clone())
    }
    fn abort_unserved(&self, handle: &SessionHandle<Self>, root: &Value) -> bool {
        let pid = root["pid"].as_i64().unwrap_or(0) as i32;
        // SAFETY: the recorded root of a session this sidecar just created (pgid == pid).
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
        let verified = handle.exit.wait(ROOT_REAP).is_some();
        handle.platform.pty.close_master();
        self.ttys.lock().unwrap().remove(handle.platform.pty.tty());
        verified
    }

    fn resize(&self, session: &PosixSession, rows: u16, cols: u16) -> Result<(), ResizeFailure> {
        session.pty.resize(rows, cols).map_err(|error| {
            if error.raw_os_error() == Some(libc::EBADF) {
                ResizeFailure::Closed(error)
            } else {
                ResizeFailure::Failed(error)
            }
        })
    }

    /// A POSIX session settles when Python reports the end of the stream.
    fn stream_settled(&self, cx: &SessionCx<'_, Self>) -> bool {
        cx.state.eof
    }
    fn settle_window(&self) -> Option<(Duration, Self::Unsettled)> {
        None
    }
    fn cleanup(&self, cx: &mut SessionCx<'_, Self>, how: Cleanup<Self::Unsettled>) -> Outcome {
        let survivors = match how {
            // R-d: the stream ended first; now the remaining descendants go, by identity.
            Cleanup::Natural => cleanup(cx, false, false),
            Cleanup::Kill { force } => cleanup(cx, force, true),
            Cleanup::Shutdown { .. } => cleanup(cx, false, true),
            Cleanup::Unsettled(never) => match never {},
        };
        outcome(survivors)
    }
    fn snapshot(&self, cx: &mut SessionCx<'_, Self>, due: Instant) -> Option<Vec<Value>> {
        let owned = &mut cx.state.owned;
        let known = known_owned(owned);
        // On Linux every session due at the same grid instant shares its /proc scans (the cadence is unchanged).
        #[cfg(target_os = "linux")]
        let result = linux::snapshot_with(None, &known, &mut owned.births, linux::Reads::Shared { due });
        #[cfg(target_os = "macos")]
        let result = {
            let _ = due;
            snapshot(None, &known, &mut owned.births)
        };
        let table = result.ok()?;
        #[cfg(target_os = "linux")]
        if !table
            .get(&owned.pid)
            .is_some_and(|entry| Some(entry.2.as_str()) == owned.root["start_value"].as_str())
        {
            return None; // Keep the last good tree when death races the snapshot.
        }
        retain_owned(owned, &table);
        Some(
            owned
                .descendants
                .iter()
                .map(|(pid, entry)| identity(*pid, entry))
                .collect(),
        )
    }
    /// On Linux the due time is moved back onto a sidecar-wide 5 s grid (never later than the session's own
    /// cadence), so sessions snapshot together and share their scans, and a full `/proc` read that was expensive
    /// holds it back (`scan_board`, B-R1).
    ///
    /// macOS has no stretch (K1): its snapshot walks the session's own tree with `proc_listchildpids` and
    /// `proc_listpgrppids`, so what it costs follows the size of that tree and not the number of processes on the
    /// host, and no scan is shared between sessions that a cost could be charged to. A policy there would have to be
    /// per session and measured around the walk, and the walk is already bounded by `SNAPSHOT_MS`.
    fn next_snapshot(&self, own: Instant) -> Instant {
        #[cfg(target_os = "linux")]
        return linux::grid_due(own);
        #[cfg(not(target_os = "linux"))]
        own
    }
    fn close_stream(&self, handle: &SessionHandle<Self>) {
        #[cfg(target_os = "linux")]
        linux::note_unattributed();
        // Rust may close its own master at cleanup, never earlier (5.5 R-a).
        handle.platform.pty.close_master();
    }
    fn retire(&self, handle: &SessionHandle<Self>) {
        self.ttys.lock().unwrap().remove(handle.platform.pty.tty());
    }
    /// Linux scoped mode: let the reaper take what it has been holding off, and publish the diagnostics.
    fn teardown(&self, deadline: Instant) {
        #[cfg(target_os = "linux")]
        {
            if let Some(reaper) = linux::reaper() {
                reaper.drain(deadline);
            }
            linux::note_unattributed();
        }
        #[cfg(not(target_os = "linux"))]
        let _ = deadline;
    }

    fn observer(&self, vendor: &str) -> Option<Self::Observer> {
        crate::observe::adapters::find(vendor)
    }
    fn observe(&self, observer: Self::Observer, request: &Request) -> io::Result<Value> {
        crate::observe::adapters::dispatch(observer, &request.meta)
    }
}

#[cfg(test)]
mod retry_tests {
    use super::*;

    #[test]
    fn a_raced_snapshot_waits_longer_each_time_and_never_past_its_deadline() {
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut attempt = 0;
        let mut waits = Vec::new();
        for _ in 0..7 {
            let began = Instant::now();
            retry_pause(&mut attempt, deadline);
            waits.push(began.elapsed());
        }
        assert_eq!(attempt, 7);
        assert!(waits[0] >= Duration::from_millis(1), "the first retry already waits: {waits:?}");
        assert!(waits[4] >= Duration::from_millis(16), "the pause reaches 16 ms: {waits:?}");
        assert!(waits[6] < Duration::from_millis(250), "the pause stays capped: {waits:?}");

        let expired = Instant::now();
        let mut attempt = 4;
        let began = Instant::now();
        retry_pause(&mut attempt, expired);
        assert!(began.elapsed() < Duration::from_millis(10), "no wait past the deadline");
    }
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

/// E5 round 5: the root can exit, or already be reaped, before the group fallback runs. A same-group child this
/// session never admitted must still make the emitted `SESSION_CLEANED` incomplete and name it (design 5.5, 10.4).
#[cfg(test)]
mod root_exit_tests {
    use super::*;
    use crate::control::{Config, Wire};
    use crate::session::{finish, State};
    use std::os::unix::process::CommandExt;

    pub(super) fn runtime(journal: Option<std::path::PathBuf>) -> Arc<Runtime<PosixHost>> {
        let mut config = Config::for_test();
        if let Some(journal) = journal {
            config.journal = journal;
        }
        let tag = crate::frame::runtime_tag(&config.generation).unwrap();
        Runtime::new(PosixHost::default(), Wire::new(&config, tag))
    }

    pub(super) fn session(
        root: i32,
    ) -> (
        Arc<Runtime<PosixHost>>,
        Arc<SessionHandle<PosixHost>>,
        State<PosixHost>,
    ) {
        let mut births = Births::default();
        let table = snapshot(Some(root), &Table::new(), &mut births).unwrap();
        let start = table[&root].2.clone();
        let mut owned = Owned {
            pid: root,
            root: json!({ "start_value": start }),
            descendants: Table::new(),
            births,
        };
        retain_owned(&mut owned, &table);
        let (pty, slave) = SessionPty::open(24, 80).unwrap();
        drop(slave);
        let (tx, _mailbox, exit) = mailbox();
        let handle = Arc::new(SessionHandle {
            id: "s".into(),
            generation: "g".into(),
            pane: "p".into(),
            workspace: "/w".into(),
            agent_key: "terminal".into(),
            platform: PosixSession {
                pty: Arc::new(pty),
                spec: None,
            },
            tx,
            exit,
            done: (Mutex::new(None), std::sync::Condvar::new()),
            adopted: std::sync::atomic::AtomicBool::new(false),
            started: Instant::now(),
            survivors: Mutex::new(Vec::new()),
        });
        let mut state = State::new(owned, Instant::now());
        state.closing = Some(true);
        (runtime(None), handle, state)
    }

    fn sleeper_group_leader() -> std::process::Child {
        Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap()
    }

    /// A child born into the root's group after the session last looked.
    fn late_member(root: i32) -> std::process::Child {
        Command::new("/bin/sleep")
            .arg("30")
            .process_group(root)
            .spawn()
            .unwrap()
    }

    fn reap(root: &mut std::process::Child, handle: &SessionHandle<PosixHost>) {
        root.kill().unwrap();
        let status = root.wait().unwrap();
        handle.exit.set(exit_info(
            status.code(),
            std::os::unix::process::ExitStatusExt::signal(&status),
            false,
        ));
    }

    fn emit(
        runtime: &Runtime<PosixHost>,
        handle: &SessionHandle<PosixHost>,
        state: &mut State<PosixHost>,
    ) {
        let result = cleanup(
            &mut SessionCx {
                runtime,
                handle,
                state,
            },
            true,
            false,
        );
        finish(runtime, handle, outcome(result));
    }

    fn assert_incomplete_and_names(
        handle: &SessionHandle<PosixHost>,
        late: &mut std::process::Child,
    ) {
        let survivors = handle.survivors.lock().unwrap().clone();
        let complete = handle.wait_finished(Duration::from_secs(1));
        let alive = late.try_wait().unwrap().is_none();
        late.kill().unwrap();
        late.wait().unwrap();
        assert!(alive, "cleanup signalled a member it never admitted");
        assert_eq!(complete, Some(false), "survivors: {survivors:?}");
        assert!(
            survivors
                .iter()
                .any(|entry| entry["pid"] == late.id() as i32),
            "the live unadmitted member {} is not named: {survivors:?}",
            late.id()
        );
    }

    #[test]
    fn a_member_born_after_the_snapshot_survives_a_root_that_exits_before_the_group_signal() {
        let mut root = sleeper_group_leader();
        let pid = root.id() as i32;
        let (runtime, handle, mut state) = session(pid);
        let born = std::rc::Rc::new(std::cell::RefCell::new(None));
        let hook_handle = handle.clone();
        let hook_born = born.clone();
        seams::AFTER_FIRST_SNAPSHOT.with(|cell| {
            *cell.borrow_mut() = Some(Box::new(move || {
                *hook_born.borrow_mut() = Some(late_member(pid));
                reap(&mut root, &hook_handle);
            }));
        });
        emit(&runtime, &handle, &mut state);
        let mut late = born.borrow_mut().take().expect("the seam never ran");
        assert_incomplete_and_names(&handle, &mut late);
    }

    #[test]
    fn a_member_in_the_group_of_an_already_reaped_root_with_no_descendants_is_named() {
        let mut root = sleeper_group_leader();
        let pid = root.id() as i32;
        let (runtime, handle, mut state) = session(pid);
        let mut late = late_member(pid);
        reap(&mut root, &handle);
        emit(&runtime, &handle, &mut state);
        assert_incomplete_and_names(&handle, &mut late);
    }

    /// The member is told apart from the root's group (own group) but shares its session.
    #[test]
    fn a_member_of_the_session_of_a_real_session_leader_root_is_named_and_unsignalled() {
        use std::io::{BufRead, BufReader, Write};
        // bash: monitor mode gives the background job its own process group (dash has no job control).
        let mut command = Command::new("/bin/bash");
        command
            .args(["-c", "set -m; read go; sleep 30 & echo $!; wait"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped());
        // SAFETY: setsid is async-signal-safe and runs in the forked child before exec.
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        let mut root = command.spawn().unwrap();
        let pid = root.id() as i32;
        let (runtime, handle, mut state) = session(pid);
        writeln!(root.stdin.as_mut().unwrap()).unwrap();
        let mut line = String::new();
        BufReader::new(root.stdout.as_mut().unwrap())
            .read_line(&mut line)
            .unwrap();
        let late: i32 = line.trim().parse().unwrap();
        reap(&mut root, &handle);
        emit(&runtime, &handle, &mut state);
        let survivors = handle.survivors.lock().unwrap().clone();
        let complete = handle.wait_finished(Duration::from_secs(1));
        #[cfg(target_os = "linux")]
        let stat = std::fs::read_to_string(format!("/proc/{late}/stat")).unwrap_or_default();
        // SAFETY: signal 0 only checks existence of a sleeper this test started.
        #[cfg(target_os = "macos")]
        let unsignalled = unsafe { libc::kill(late, 0) } == 0;
        // SAFETY: identifier queries only, on a sleeper this test started.
        #[cfg(target_os = "macos")]
        let layout = unsafe { (libc::getpgid(late), libc::getsid(late)) };
        // SAFETY: the pid is a sleeper this test started.
        unsafe { libc::kill(late, libc::SIGKILL) };
        #[cfg(target_os = "linux")]
        assert!(
            !stat.is_empty() && !stat.contains(") Z "),
            "cleanup signalled a member it never admitted: {stat}"
        );
        #[cfg(target_os = "macos")]
        assert!(unsignalled, "cleanup signalled a member it never admitted");
        assert_eq!(complete, Some(false), "survivors: {survivors:?}");
        assert!(survivors.iter().any(|entry| entry["pid"] == late));
        // The child was in the root's session but not in the root's group, so only the session arm can name it.
        #[cfg(target_os = "linux")]
        {
            let fields: Vec<&str> = stat
                .split(") ")
                .nth(1)
                .unwrap_or_default()
                .split(' ')
                .collect();
            assert_eq!(fields.get(3).copied(), Some(pid.to_string().as_str()));
            assert_ne!(fields.get(2).copied(), Some(pid.to_string().as_str()));
        }
        #[cfg(target_os = "macos")]
        {
            assert_eq!(layout.1, pid, "the child must share the root's session");
            assert_ne!(layout.0, pid, "the child must not share the root's group");
        }
    }

    /// Linux: an entry that cannot be read for a pid that could belong to the group is not skipped.
    #[cfg(target_os = "linux")]
    #[test]
    fn an_unreadable_proc_entry_of_a_group_member_makes_the_receipt_incomplete() {
        let mut root = sleeper_group_leader();
        let pid = root.id() as i32;
        let (runtime, handle, mut state) = session(pid);
        let mut late = late_member(pid);
        reap(&mut root, &handle);
        linux::DENIED_STAT.with(|denied| denied.set(Some(late.id() as i32)));
        emit(&runtime, &handle, &mut state);
        linux::DENIED_STAT.with(|denied| denied.set(None));
        let survivors = handle.survivors.lock().unwrap().clone();
        let complete = handle.wait_finished(Duration::from_secs(1));
        let alive = late.try_wait().unwrap().is_none();
        late.kill().unwrap();
        late.wait().unwrap();
        assert!(alive, "cleanup signalled a member it could not read");
        assert_eq!(complete, Some(false), "survivors: {survivors:?}");
        assert!(survivors.iter().any(|entry| entry["error"].is_string()));
    }
}

#[cfg(test)]
mod startup_tests {
    use crate::control::parse_args;
    use crate::host::Host;
    use crate::posix::{identity, retain_owned, Owned, PosixHost, Table};
    use crate::session_pty::SessionPty;
    use serde_json::json;

    /// K3: a retained descendant that the verified table no longer lists is dropped, so the published set can shrink
    /// to nothing and Python learns the last descendant is gone (R9); one that is still listed stays.
    #[test]
    fn a_descendant_the_verified_table_no_longer_lists_is_dropped_from_the_published_set() {
        let entry = |parent, group, start: &str| (parent, group, start.to_owned());
        let mut owned = Owned {
            pid: 100,
            root: json!({"pid":100,"start_value":"root-start"}),
            descendants: [(101, entry(100, 100, "a")), (102, entry(100, 100, "b"))].into(),
            births: Default::default(),
        };
        // 101 is gone, 102 is listed again, 103 is new, the root itself is never kept as a descendant.
        let table: Table = [
            (100, entry(1, 100, "root-start")),
            (102, entry(100, 100, "b")),
            (103, entry(102, 100, "c")),
        ]
        .into();
        retain_owned(&mut owned, &table);
        let mut kept: Vec<_> = owned.descendants.keys().copied().collect();
        kept.sort();
        assert_eq!(kept, vec![102, 103]);
        retain_owned(&mut owned, &[(100, entry(1, 100, "root-start"))].into());
        assert!(owned.descendants.is_empty(), "the last descendant is gone: the set must empty");
    }

    /// K4: the worst-case records the create bound uses name every key a real record has, and are at least as long.
    #[test]
    fn the_worst_case_records_cover_a_real_root_and_a_real_pty_record() {
        let (pty, _slave) = SessionPty::open(24, 80).unwrap();
        // The longest pid a Linux kernel hands out, and a start string at its usual length.
        let real_root = identity(4_194_304, &(1, 4_194_304, "Thu Nov  5 12:34:56 2026".into()));
        crate::testing::assert_worst_case_covers(&PosixHost::default().worst_case(), &real_root, &pty.owned_record());
    }

    #[test]
    fn unknown_and_missing_arguments_fail_startup_and_a_wrong_wire_is_incompatible() {
        let ok = |extra: &[&str]| {
            let mut args: Vec<String> = [
                "--wire",
                "2",
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
            parse_args::<PosixHost>(&args)
        };
        assert!(ok(&[]).is_ok());
        assert_eq!(ok(&["--bogus"]).err().map(|e| e.0), Some("STARTUP_FAILED"));
        let mut wrong = vec!["--wire", "1"]; // a wire-1 request of a wire-2 sidecar is refused (delta 1)
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
            parse_args::<PosixHost>(&args).err().map(|e| e.0),
            Some("INCOMPATIBLE_WIRE")
        );
        assert_eq!(
            parse_args::<PosixHost>(&["--wire".to_string(), "2".to_string()])
                .err()
                .map(|e| e.0),
            Some("STARTUP_FAILED")
        );
    }

    #[test]
    fn a_posix_runtime_requires_the_control_descriptor() {
        let args: Vec<String> = [
            "--wire",
            "2",
            "--runtime-generation",
            "00112233445566778899aabbccddeeff",
            "--owner-pid",
            "1",
            "--ownership-journal",
            "/tmp/j",
        ]
        .map(String::from)
        .to_vec();
        assert_eq!(
            parse_args::<PosixHost>(&args).err().map(|e| e.0),
            Some("STARTUP_FAILED")
        );
    }
}

/// Linux scoped mode: `ECHILD` on a root fails closed, no lock is held across a wait, and a failed create
/// keeps its reservation until its own wait has returned (design section 3, rows U-16, U-18, U-19).
#[cfg(target_os = "linux")]
#[cfg(test)]
mod scoped_tests {
    use super::root_exit_tests::{runtime, session};
    use super::*;
    use crate::refusal::{classify, Code, Stage};
    use crate::session::finish;
    use std::sync::atomic::{AtomicBool, Ordering};

    /// The tests that start a real reaper thread share the process-wide `reaper_running` counter, so they run
    /// one at a time.
    static REAPER_THREAD_TESTS: Mutex<()> = Mutex::new(());

    fn scoped(on: bool) {
        linux::SCOPED_FORCE.with(|forced| forced.set(Some(on)));
    }

    fn create_for_test(
        runtime: &Arc<Runtime<PosixHost>>,
        args: &Value,
        request: u64,
    ) -> io::Result<Spawned<PosixHost>> {
        let parsed = crate::control::create_args(&runtime.host, args)?;
        runtime.host.spawn(runtime, request, &parsed)
    }

    fn direct_sleep(name: &str, dir: &std::path::Path) -> Value {
        json!({"session_id":name,"session_generation":"ffeeddccbbaa99887766554433221100",
            "pane_id":"pane-1","workspace_path":dir,"cwd":dir,
            "launch":{"shape":"direct","form":"list","elements":["/bin/sleep","30"],"words":[]},
            "env":{"PATH":"/usr/bin:/bin"},"rows":24,"cols":80,"agent_key":""})
    }

    #[test]
    fn echild_on_root_is_unverified() {
        // A pid that cannot name a group: the group kill finds nothing.
        let nowhere = i32::MAX - 1;
        seams::FORCE_ECHILD.with(|forced| forced.set(true));
        scoped(true);
        assert!(
            !kill_and_reap_released(nowhere),
            "ECHILD on a root was reported as reaped"
        );
        scoped(false);
        assert!(
            kill_and_reap_released(nowhere),
            "with scoped mode off, ECHILD keeps its HEAD meaning"
        );
        seams::FORCE_ECHILD.with(|forced| forced.set(false));
        // watch_exit: waiting for a pid that is not our child answers ECHILD.
        // SAFETY: getppid takes no arguments.
        let stranger = unsafe { libc::getppid() };
        for on in [true, false] {
            scoped(on);
            let cell = Arc::new(ExitCell::new());
            let (tx, _rx) = mpsc::channel();
            watch_exit(stranger, cell.clone(), tx, Arc::new(Mutex::new(None))).unwrap();
            let info = cell
                .wait(Duration::from_secs(2))
                .expect("the exit watcher never answered");
            assert_eq!(info.unverified, on, "scoped mode {on}");
        }
        // finish: an unverified root is not reaped, so the cleanup is not complete.
        for unverified in [true, false] {
            let mut root = Command::new("/bin/sleep")
                .arg("30")
                .process_group(0)
                .spawn()
                .unwrap();
            let (runtime, handle, _state) = session(root.id() as i32);
            handle.exit.set(exit_info(None, None, unverified));
            finish(&runtime, &handle, Outcome::default());
            let complete = handle.wait_finished(Duration::from_secs(1));
            root.kill().unwrap();
            root.wait().unwrap();
            assert_eq!(complete, Some(!unverified), "unverified = {unverified}");
        }
        scoped(false);
    }

    #[test]
    fn no_lock_is_held_across_a_blocking_wait() {
        // A child that is not a group leader: the group kill misses it, so the wait blocks.
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let pid = child.id() as i32;
        let reaper = linux::Reaper::new();
        let _reservation = reaper.reserve_key(pid, 1);
        let waiter = thread::spawn(move || kill_and_reap_released(pid));
        thread::sleep(Duration::from_millis(100));
        assert!(
            reaper.is_free(),
            "a lock was held while a root's wait was blocked"
        );
        child.kill().unwrap();
        assert!(waiter.join().unwrap());
    }

    #[test]
    fn failed_create_keeps_reservation_until_child_wait() {
        let reaper = linux::Reaper::new();
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = Some(reaper.clone()));
        scoped(true);
        seams::PANIC_AFTER_RELEASE.with(|flag| flag.set(true));
        let seen = Arc::new(AtomicBool::new(false));
        {
            let (reaper, seen) = (reaper.clone(), seen.clone());
            seams::BEFORE_CHILD_WAIT.with(|cell| {
                *cell.borrow_mut() = Some(Box::new(move |pid| {
                    assert!(
                        reaper.holds(pid),
                        "the reservation was gone before child.wait() started"
                    );
                    seen.store(true, Ordering::SeqCst);
                }));
            });
        }
        let dir = std::env::temp_dir().join(format!("navide-u19-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let runtime = runtime(Some(dir.join("journal.jsonl")));
        let result = create_for_test(&runtime, &direct_sleep("u19", &dir), 1);
        seams::PANIC_AFTER_RELEASE.with(|flag| flag.set(false));
        scoped(false);
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = None);
        assert!(result.is_err(), "the owner failed after the release");
        assert!(seen.load(Ordering::SeqCst), "the failed-create branch never ran");
        assert!(
            !reaper.holds_any(),
            "the reservation outlived the failed create's wait"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// B1: a reaper thread that ended (`what` selects how) leaves the runtime refusing the next create, with the
    /// `SPAWN_FAILED` / `spawn` answer of any pre-fork refusal, and the liveness counter cleared.
    fn create_is_refused_once_the_reaper_has_stopped(what: u8, name: &str) {
        let _serial = REAPER_THREAD_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        let reaper = linux::Reaper::new();
        reaper.inject(what);
        reaper.start().unwrap();
        assert!(
            reaper.wait_stopped(Duration::from_secs(5)),
            "the reaper thread never ended"
        );
        #[cfg(feature = "fault-hooks")]
        assert!(
            !linux::Reaper::counter_running(),
            "reaper_running still reports true"
        );
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = Some(reaper.clone()));
        scoped(true);
        let dir = std::env::temp_dir().join(format!("navide-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join("journal.jsonl");
        let runtime = runtime(Some(journal.clone()));
        let result = create_for_test(&runtime, &direct_sleep(name, &dir), 1);
        scoped(false);
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = None);
        let error = result.err().expect("a create was accepted after the reaper stopped");
        assert_eq!(classify(&error, true), (Code::SpawnFailed, Stage::Spawn), "{error}");
        assert!(error.to_string().contains("reaper has stopped"), "{error}");
        assert!(
            std::fs::metadata(&journal).map_or(true, |meta| meta.len() == 0),
            "a root was journalled although the create was refused"
        );
        assert!(runtime.host.ttys.lock().unwrap().is_empty(), "the refused create kept its tty");
        assert!(reaper.is_free(), "the refused create left a reaper lock held");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn create_is_refused_after_the_reaper_hits_a_waitid_error() {
        create_is_refused_once_the_reaper_has_stopped(linux::INJECT_WAITID_ERROR, "b1-error");
    }

    #[test]
    fn create_is_refused_after_the_reaper_panics() {
        create_is_refused_once_the_reaper_has_stopped(linux::INJECT_PANIC, "b1-panic");
    }

    /// B1 (review): the stop is published under the create gate. A create that already passed the `stopped` check
    /// finishes and reserves its root first; the stop lands only after it released the gate; a create that starts
    /// after that is refused before any fork.
    #[test]
    fn the_reaper_stop_waits_for_a_create_that_holds_the_gate() {
        let _serial = REAPER_THREAD_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        let reaper = linux::Reaper::new();
        reaper.inject(linux::INJECT_PARK);
        reaper.start().unwrap();
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = Some(reaper.clone()));
        scoped(true);
        let held = Arc::new(AtomicBool::new(false));
        {
            let (reaper, held) = (reaper.clone(), held.clone());
            seams::AFTER_STOPPED_CHECK.with(|cell| {
                *cell.borrow_mut() = Some(Box::new(move || {
                    // The create holds the gate and has read `stopped == false`. End the reaper now.
                    reaper.inject(linux::INJECT_WAITID_ERROR);
                    // Wait until the reaper is *at* publication, blocked on the gate this create holds, so the
                    // stop is really waiting for this create. The timeout only guards a failure. The verdict is
                    // not read here: it is what the publication site recorded (`stop_store_seen`, asserted
                    // below), so it does not depend on when this hook runs.
                    assert!(
                        reaper.wait_stop_blocked_on_gate(Duration::from_secs(10)),
                        "the reaper never reached the stop publication while the create held the gate"
                    );
                    held.store(true, Ordering::SeqCst);
                }));
            });
        }
        let dir = std::env::temp_dir().join(format!("navide-b1-gate-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let runtime = runtime(Some(dir.join("journal.jsonl")));
        // The held create's own outcome: it succeeds and its root is reserved by the time it returns.
        let first = create_for_test(&runtime, &direct_sleep("b1-held", &dir), 1);
        assert!(held.load(Ordering::SeqCst), "the hook never ran");
        let spawned = first.ok().expect("the create that held the gate was refused");
        let pid = spawned.owned.pid;
        assert!(reaper.holds(pid), "the held create's root was not reserved");
        // The stop is published once the create released the gate, and then every create is refused.
        assert!(
            reaper.wait_stopped(Duration::from_secs(5)),
            "the stop was never published after the gate was released"
        );
        assert_eq!(
            reaper.stop_store_seen(),
            linux::STOP_UNDER_GATE,
            "the stopped flag was published before the stopper owned the gate"
        );
        let second = create_for_test(&runtime, &direct_sleep("b1-later", &dir), 2);
        scoped(false);
        linux::REAPER_FORCE.with(|forced| *forced.borrow_mut() = None);
        // Clean up the held create's child before judging the later one.
        // SAFETY: the root is a session leader of its own group; this signals only that group.
        unsafe { libc::kill(-pid, libc::SIGKILL) };
        let end = Instant::now() + Duration::from_secs(10);
        while reaper.holds_any() && Instant::now() < end {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(!reaper.holds_any(), "the held create's root was never reaped by its own waiter");
        let error = second.err().expect("a create was accepted after the stop");
        assert_eq!(classify(&error, true), (Code::SpawnFailed, Stage::Spawn), "{error}");
        assert!(error.to_string().contains("reaper has stopped"), "{error}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

/// g2-t11 F1: the gate's one identity read returns what a snapshot of a root held at the gate returns.
#[cfg(test)]
mod gated_root_tests {
    use super::*;
    use std::os::unix::process::CommandExt;

    /// Shaped like a root held at the gate: a `setsid` leader with no child, blocked on its stdin.
    fn held_root(leader: bool) -> std::process::Child {
        let mut command = Command::new("/bin/cat");
        command.stdin(Stdio::piped()).stdout(Stdio::null());
        if leader {
            // SAFETY: setsid is async-signal-safe and runs in the forked child before exec.
            unsafe {
                command.pre_exec(|| {
                    if libc::setsid() < 0 {
                        return Err(io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
        }
        command.spawn().unwrap()
    }

    #[test]
    fn the_gate_identity_read_equals_the_snapshot_of_a_held_root() {
        let mut root = held_root(true);
        let pid = root.id() as i32;
        let from_read = gated_root_table(pid, &mut Births::default());
        let from_snapshot = snapshot(Some(pid), &Table::new(), &mut Births::default());
        root.kill().unwrap();
        root.wait().unwrap();
        let from_read = from_read.unwrap();
        assert_eq!(from_read, from_snapshot.unwrap());
        assert_eq!(from_read.len(), 1, "{from_read:?}");
        assert_eq!(from_read[&pid].1, pid, "the root leads its own group");
    }

    /// A root that is not its own group leader is not the gate's shape: no identity read, the snapshot decides.
    #[test]
    fn a_root_outside_the_gate_shape_gets_no_identity_read() {
        let mut root = held_root(false);
        let pid = root.id() as i32;
        #[cfg(target_os = "linux")]
        let row = linux::gated_root(pid, &mut Births::default());
        #[cfg(target_os = "macos")]
        let row = darwin::gated_root(pid);
        root.kill().unwrap();
        root.wait().unwrap();
        assert_eq!(row.unwrap(), None);
    }
}
