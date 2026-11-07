//! The Windows host: spawn and ownership of ConPTY-backed roots (design/e1-runtime-protocol-v8b.md 7).
//!
//! The control plane (control.rs, session.rs) is generic; this module is what is Windows about it: the control
//! channel is the sidecar's stdin and stdout, a root is created `CREATE_SUSPENDED` in a kill-on-close job and
//! resumed only after its identity is durable, `SESSION_OWNED` names two caller-side pipe ends that Python pulls
//! with `DuplicateHandle`, and `ClosePseudoConsole` runs on its own thread only after the pre-close gate. Rust
//! never reads or writes a PTY byte.
use crate::compose;
use crate::control::{
    cleanup_ms, launch_object, post_effect_failure, precomposed, Config, Runtime, KILL_ESCALATION_MS,
    REAP_CONFIRM_MS, ROOT_REAP,
};
use crate::frame::code;
use crate::host::{
    Cleanup, CreateArgs, ExitCell, ExitInfo, Host, Launch, Msg, Outcome, ResizeFailure, Spawned,
    StreamSignal, WorstCase,
};
use crate::journal;
use crate::refusal::{fault, Code, Refusal, Stage};
use crate::session::{mailbox, SessionCx, SessionHandle};
use crate::session_pty_win::{CloseGate, GateReason, Identity, SessionPty};
use crate::wire::Outbound;
use serde_json::{json, Value};
use std::io::{self, Read};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr::null_mut;
use std::sync::atomic::Ordering;
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{DuplicateHandle, DUPLICATE_SAME_ACCESS};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetCurrentThread};
use windows_sys::Win32::System::IO::CancelSynchronousIo;

/// An event wait on the root's process handle is re-armed at this period (it ends on the exit).
const ROOT_WAIT_SLICE: Duration = Duration::from_secs(3600);

/// The Windows platform. It keeps no state of its own: a session's names are job and console handles.
#[derive(Default)]
pub struct WindowsHost;

/// The platform half of a session handle.
pub struct WindowsSession {
    pub(crate) pty: Arc<SessionPty>,
    /// The pre-close gate of 7.3: opened by `TTY_SERVICING`, a `TTY_EOF`, or Rust closing its originals.
    pub(crate) gate: CloseGate,
}

/// What only the session's own thread touches.
pub struct Owned {
    root: Identity,
}

/// A real handle to the reader thread (the pseudo handle cannot be used from another thread), so the writer can
/// `CancelSynchronousIo` the reader's blocked `ReadFile` when the channel is lost.
pub struct Link(OwnedHandle);

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

/// Every composition refusal on Windows is one `invalid composition` refusal (the grammar differs, the answer
/// does not).
fn composition(error: compose::ComposeError) -> io::Error {
    Refusal::bad(format!("invalid composition: {error:?}"))
        .staged(Stage::Composition)
        .into()
}

fn journal_failed(message: impl Into<std::borrow::Cow<'static, str>>) -> io::Error {
    fault(Code::OwnershipJournalFailed, message)
}

/// Event-driven watch of the root's exit on its process handle (`WaitForSingleObject`).
fn watch_exit(pty: Arc<SessionPty>, cell: Arc<ExitCell>, tx: mpsc::Sender<Msg>) -> io::Result<()> {
    thread::Builder::new()
        .name("exit-watch".into())
        .spawn(move || {
            let info = loop {
                match pty.wait_root(ROOT_WAIT_SLICE) {
                    Ok(Some(code)) => {
                        break ExitInfo {
                            exit_code: Some(i64::from(code)),
                            signal: None,
                            unverified: false,
                        }
                    }
                    Ok(None) => continue,
                    // The status is unknown.
                    Err(_) => {
                        break ExitInfo {
                            exit_code: None,
                            signal: None,
                            unverified: false,
                        }
                    }
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

impl WindowsHost {
    fn spawn_gated(
        &self,
        runtime: &Runtime<Self>,
        request: u64,
        parsed: &CreateArgs,
    ) -> io::Result<Spawned<Self>> {
        let pty = SessionPty::open(parsed.rows, parsed.cols)?;
        let identity = pty.spawn_suspended(&parsed.argv, &parsed.env, &parsed.cwd)?;
        // From here the root exists but has executed nothing: any refusal ends it unrun.
        let refuse = |pty: &SessionPty, error: io::Error| -> io::Error {
            let _ = kill_and_reap_released(pty);
            error
        };
        let wire = &runtime.wire;
        let record = journal::record(wire, &identity.record(), &parsed.argv[0], &parsed.id, &parsed.generation);
        if let Err(error) = journal::append(&wire.journal, &record) {
            return Err(refuse(
                &pty,
                journal_failed(format!("OWNERSHIP_JOURNAL_FAILED: {}", error.kind() as u32)),
            ));
        }
        // The cancellation check and the release are one step under the registry lock: a cancel either lands
        // before it (the target never executes; "prevented") or after it ("released").
        if let Err(error) = runtime.release_gate(request, || {
            pty.resume()
                .map_err(|error| journal_failed(format!("OWNERSHIP_JOURNAL_FAILED: resume: {error}")))
        }) {
            return Err(refuse(&pty, error));
        }
        let pty = Arc::new(pty);
        let (tx, mailbox, exit) = mailbox();
        #[cfg(feature = "fault-hooks")]
        let watched = if crate::fault::watch_exit_fails(&parsed.pane) {
            Err(crate::fault::watch_exit_error())
        } else {
            watch_exit(pty.clone(), exit.clone(), tx.clone())
        };
        #[cfg(not(feature = "fault-hooks"))]
        let watched = watch_exit(pty.clone(), exit.clone(), tx.clone());
        if let Err(error) = watched {
            let verified = kill_and_reap_released(&pty);
            return Err(post_effect_failure(
                verified,
                "the exit watcher could not start",
                &error,
            ));
        }
        Ok(Spawned {
            platform: WindowsSession {
                pty,
                gate: CloseGate::new(),
            },
            owned: Owned { root: identity },
            exit,
            tx,
            mailbox,
        })
    }
}

// ---- lifecycle ---------------------------------------------------------------------------------

/// Kill the whole tree through the job and confirm the job is empty; what is left is a survivor.
fn end_tree(pty: &SessionPty, deadline: Instant) -> io::Result<Vec<Value>> {
    end_tree_by(
        deadline,
        || {
            let _ = pty.terminate_tree();
        },
        |bound| pty.wait_tree_empty(bound),
        || Ok(pty.members()?.iter().map(Identity::record).collect()),
    )
}

/// `deadline` is the one absolute bound of the whole cleanup: the wait for the tree to empty takes what
/// is left of it (never more than the escalation bound), so a late gate or an already expired no-gate
/// deadline cannot extend the cleanup. A tree still not empty then is reported as survivors.
fn end_tree_by(
    deadline: Instant,
    terminate: impl FnOnce(),
    wait_empty: impl FnOnce(Duration) -> io::Result<bool>,
    members: impl FnOnce() -> io::Result<Vec<Value>>,
) -> io::Result<Vec<Value>> {
    terminate();
    let bound = Duration::from_millis(KILL_ESCALATION_MS + REAP_CONFIRM_MS)
        .min(deadline.saturating_duration_since(Instant::now()));
    if wait_empty(bound)? {
        return Ok(Vec::new());
    }
    members()
}

/// `ClosePseudoConsole` on its own thread, only after the pre-close gate (7.3). `deadline` is the one
/// absolute bound of the whole cleanup (`cleanup_ms` from the root's exit or the kill): the gate wait and
/// the close-thread wait share what is left of it, so a late gate never earns a fresh `cleanup_ms`.
fn close_console(session: &WindowsSession, deadline: Instant) -> Result<(), String> {
    close_by(
        deadline,
        |left| session.gate.wait(left),
        |permit| {
            session
                .pty
                .close_console(permit)
                .map_err(|error| format!("close thread: {error}"))
        },
    )
}

fn close_by<P>(
    deadline: Instant,
    wait_gate: impl FnOnce(Duration) -> Option<P>,
    start_close: impl FnOnce(P) -> Result<mpsc::Receiver<()>, String>,
) -> Result<(), String> {
    let permit = wait_gate(deadline.saturating_duration_since(Instant::now()))
        .ok_or("pre-close gate not satisfied within cleanup_ms")?;
    let done = start_close(permit)?;
    done.recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| "ClosePseudoConsole did not return within cleanup_ms".to_owned())
}

fn root_record(session: &WindowsSession) -> Value {
    session
        .pty
        .root_identity()
        .map(|identity| identity.record())
        .unwrap_or(Value::Null)
}

fn finish_close(session: &WindowsSession, outcome: &mut Outcome, deadline: Instant) {
    if let Err(reason) = close_console(session, deadline) {
        outcome.unverifiable.push(root_record(session));
        outcome.failure.get_or_insert(reason);
    }
}

fn survivors_of(result: io::Result<Vec<Value>>, outcome: &mut Outcome) {
    match result {
        Ok(survivors) => outcome.survivors = survivors,
        Err(error) => outcome.failure = Some(error.to_string()),
    }
}

/// R-d on Windows: the root exited and the stream is serviced or closed; the remaining job members
/// go by identity and the console closes.
fn natural_cleanup(cx: &mut SessionCx<'_, WindowsHost>) -> Outcome {
    let mut outcome = Outcome::default();
    // The clock started when the root's exit was observed (`settle_deadline`); a gate that was already open
    // at that point starts it now.
    let deadline = *cx
        .state
        .settle_deadline
        .get_or_insert_with(|| Instant::now() + Duration::from_millis(cleanup_ms()));
    let session = &cx.handle.platform;
    survivors_of(end_tree(&session.pty, deadline), &mut outcome);
    finish_close(session, &mut outcome, deadline);
    outcome
}

/// The one absolute bound of a cleanup. A cleanup already running keeps the deadline it started with;
/// only a cleanup that has not begun starts a fresh `cleanup` budget.
fn cleanup_deadline(running: Option<Instant>, now: Instant, cleanup: Duration) -> Instant {
    running.unwrap_or(now + cleanup)
}

/// Wait for the root's exit after its termination was issued, within what is left of `deadline`. On a
/// timeout the outcome is incomplete and still names the root, which may be running.
fn confirm_root_exit(
    deadline: Instant,
    wait: impl FnOnce(Duration) -> bool,
    root: impl FnOnce() -> Value,
    outcome: &mut Outcome,
) -> bool {
    if wait(ROOT_REAP.min(deadline.saturating_duration_since(Instant::now()))) {
        return true;
    }
    outcome.unverifiable.push(root());
    outcome.failure = Some("root reap timeout".into());
    false
}

/// R-e on Windows: no hangup step. A session that never adopted closes its originals (the third way
/// of 7.3 to open the gate); the tree is terminated through the job; `SESSION_EXITED` is sent; then
/// the console closes once Python is servicing or has closed its read end.
///
/// With `peer_alive == false` (the channel is lost, 11) nobody can service or close the read end and
/// the console dies with this process, so the console is not closed here and no gate is awaited.
fn kill_cleanup(cx: &mut SessionCx<'_, WindowsHost>, peer_alive: bool) -> Outcome {
    let mut outcome = Outcome::default();
    let handle = cx.handle;
    let session = &handle.platform;
    let deadline = cleanup_deadline(
        cx.state.settle_deadline,
        Instant::now(),
        Duration::from_millis(cleanup_ms()),
    );
    if !handle.adopted.load(Ordering::SeqCst) && session.pty.release_originals() {
        session.gate.satisfy(GateReason::OriginalsClosed);
    }
    let _ = session.pty.terminate_tree();
    if !confirm_root_exit(
        deadline,
        |bound| handle.exit.wait(bound).is_some(),
        || root_record(session),
        &mut outcome,
    ) {
        return outcome;
    }
    cx.send_exited();
    survivors_of(end_tree(&session.pty, deadline), &mut outcome);
    if peer_alive {
        finish_close(session, &mut outcome, deadline);
    }
    outcome
}

/// 7.3: nobody serviced the pipe in time. The console stays open (closing it could hang) and dies with this
/// process; the tree is ended and reported.
fn unsettled_cleanup(cx: &mut SessionCx<'_, WindowsHost>) -> Outcome {
    let mut outcome = Outcome::default();
    let deadline = cx.state.settle_deadline.unwrap_or_else(Instant::now);
    survivors_of(end_tree(&cx.handle.platform.pty, deadline), &mut outcome);
    outcome
        .failure
        .get_or_insert_with(|| "pre-close gate not satisfied within cleanup_ms".into());
    outcome
}

// ---- the host ------------------------------------------------------------------------------------

impl Host for WindowsHost {
    type Session = WindowsSession;
    type Owned = Owned;
    type Link = Link;
    /// The channel is the sidecar's own stdin and stdout: no option.
    type Options = ();
    /// A host with a settle window can end a session unsettled, so the cleanup carries a token for it.
    type Unsettled = ();

    const PTY_BACKEND: &'static str = "conpty";
    const HANDOFF: &'static str = "duplicate_handle";
    const SERVICING: bool = true;
    const OPTIONS: &'static [&'static str] = &[];

    fn set_option(_: &mut (), _: &str, _: String) {}
    fn check_options(_: &()) -> Result<(), String> {
        Ok(())
    }
    fn boot(_config: &Config, _options: ()) -> io::Result<Self> {
        Ok(Self)
    }

    fn open_link(&self) -> io::Result<Link> {
        // Called on the thread that reads the channel.
        Ok(Link(current_thread()?))
    }
    fn link_read(&self, _link: &Link, buffer: &mut [u8]) -> io::Result<usize> {
        io::stdin().lock().read(buffer)
    }
    fn link_write(&self, _link: &Link, out: &Outbound) -> io::Result<()> {
        out.run_writer_plain(&mut io::stdout().lock())
    }
    fn link_break(&self, link: &Link) {
        // SAFETY: cancels the blocked synchronous read of the (live) reader thread.
        unsafe { CancelSynchronousIo(link.0.as_raw_handle()) };
    }

    /// A Windows name may begin with `=` (`=C:`), so the first character is exempt from the rule that a name
    /// holds no `=`. The first character can be wider than one byte: it is skipped as a character, never sliced.
    fn env_key_ok(key: &str) -> bool {
        !key.is_empty() && !key.contains('\0') && !key.chars().skip(1).any(|c| c == '=')
    }
    fn launch(&self, agent: &str, args: &Value, env: &[(String, String)]) -> io::Result<Launch> {
        let (launch, shape) = launch_object(args)?;
        // Admission is the composition table's, independent of observation (delta 10.4).
        compose::admit(agent, shape, true).map_err(composition)?;
        if shape != "direct" {
            // The managed-credential launch keeps the pre-composed shape on Windows (`compose::admit`).
            return precomposed(launch);
        }
        // Composed here, with the Windows grammar; the receipt says so.
        let composed = compose::compose_windows(agent, launch).map_err(composition)?;
        let argv: Vec<String> = std::iter::once(composed.program).chain(composed.args).collect();
        // What Python's own spawn does with the split command (`shutil.which`, shim unwrapping, `cmd /c`).
        let argv = compose::resolve_windows(&argv[0], &argv[1..], env, &compose::OsFiles)
            .map_err(composition)?;
        let argv0 = compose::attest(&argv[0]);
        Ok(Launch {
            argv,
            argv_source: composed.argv_source,
            argv0,
            outer: None,
        })
    }
    fn worst_case(&self) -> WorstCase {
        WorstCase {
            root: json!({"pid":u32::MAX,"pgid":u32::MAX,"start_kind":crate::session_pty_win::START_KIND,
                "start_value":u64::MAX.to_string()}),
            pty: json!({"input":u64::MAX,"output":u64::MAX}),
            outer: false,
        }
    }
    fn spawn(&self, runtime: &Runtime<Self>, request: u64, args: &CreateArgs) -> io::Result<Spawned<Self>> {
        self.spawn_gated(runtime, request, args)
    }
    fn root_record(&self, owned: &Owned) -> Value {
        owned.root.record()
    }
    fn pty_record(&self, session: &WindowsSession) -> io::Result<Value> {
        session.pty.owned_record()
    }
    fn send_owned(
        &self,
        runtime: &Runtime<Self>,
        _handle: &Arc<SessionHandle<Self>>,
        meta: Value,
    ) -> io::Result<()> {
        runtime.wire.out.event(code::SESSION_OWNED, meta);
        Ok(())
    }
    fn abort_unserved(&self, handle: &SessionHandle<Self>, _root: &Value) -> bool {
        let verified = kill_and_reap_released(&handle.platform.pty);
        handle.platform.pty.release_originals();
        verified
    }

    fn resize(&self, session: &WindowsSession, rows: u16, cols: u16) -> Result<(), ResizeFailure> {
        session.pty.resize(rows, cols).map_err(|error| {
            if error.kind() == io::ErrorKind::NotConnected {
                ResizeFailure::Closed(error)
            } else {
                ResizeFailure::Failed(error)
            }
        })
    }
    fn adopted(&self, session: &WindowsSession) {
        // Python's duplicates exist by now: the originals close (7.2).
        session.pty.release_originals();
    }
    fn stream_signal(&self, session: &WindowsSession, signal: StreamSignal) {
        session.gate.satisfy(match signal {
            StreamSignal::Servicing => GateReason::Servicing,
            StreamSignal::Eof => GateReason::StreamClosed,
        });
    }

    fn stream_settled(&self, cx: &SessionCx<'_, Self>) -> bool {
        cx.handle.platform.gate.reason().is_some()
    }
    fn settle_window(&self) -> Option<(Duration, Self::Unsettled)> {
        Some((Duration::from_millis(cleanup_ms()), ()))
    }
    fn cleanup(&self, cx: &mut SessionCx<'_, Self>, how: Cleanup<Self::Unsettled>) -> Outcome {
        match how {
            Cleanup::Natural => natural_cleanup(cx),
            Cleanup::Kill { .. } => kill_cleanup(cx, true),
            Cleanup::Shutdown { peer_alive } => kill_cleanup(cx, peer_alive),
            Cleanup::Unsettled(()) => unsettled_cleanup(cx),
        }
    }
    /// The job's members now, less the root. A member that exited is gone from the job, so the published set never
    /// names a dead identity (K3); a failed read publishes nothing, leaving the last set in force.
    fn snapshot(&self, cx: &mut SessionCx<'_, Self>, _due: Instant) -> Option<Vec<Value>> {
        let members = cx.handle.platform.pty.members().ok()?;
        Some(
            members
                .iter()
                .filter(|member| **member != cx.state.owned.root)
                .map(Identity::record)
                .collect(),
        )
    }
    fn close_stream(&self, handle: &SessionHandle<Self>) {
        // The originals are Rust's until here at the latest (a session that adopted already closed them).
        handle.platform.pty.release_originals();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::control::parse_args;
    use crate::session::settle_outcome;

    /// E5 final review 3: a gate that opens near the deadline leaves the close thread only what is left of
    /// the one bound, not a fresh `cleanup_ms`.
    #[test]
    fn a_gate_delivered_near_the_deadline_leaves_the_close_only_the_remaining_bound() {
        let bound = Duration::from_millis(1000);
        let late = Duration::from_millis(900);
        let started = Instant::now();
        let (_never_answers, done) = mpsc::channel::<()>();
        let result = close_by(
            started + bound,
            |left| {
                std::thread::sleep(left.min(late));
                Some(())
            },
            |()| Ok(done),
        );
        assert!(result.is_err(), "a hung close must be reported incomplete");
        let took = started.elapsed();
        assert!(
            took < bound + Duration::from_millis(400),
            "cleanup took {took:?} for a {bound:?} bound"
        );
    }

    #[test]
    fn a_gate_delivered_near_the_deadline_still_finishes_when_the_close_returns() {
        let started = Instant::now();
        let (answer, done) = mpsc::channel();
        let result = close_by(
            started + Duration::from_millis(1000),
            |_| {
                std::thread::sleep(Duration::from_millis(900));
                Some(())
            },
            |()| {
                answer.send(()).unwrap();
                Ok(done)
            },
        );
        assert_eq!(result, Ok(()));
    }

    /// A tree that never empties: the wait takes whatever bound it is given (capped), like the real job wait.
    fn stubborn_tree(deadline: Instant) -> (io::Result<Vec<Value>>, Duration) {
        let started = Instant::now();
        let result = end_tree_by(
            deadline,
            || {},
            |bound| {
                std::thread::sleep(bound.min(Duration::from_secs(5)));
                Ok(false)
            },
            || Ok(vec![json!({"pid": 7})]),
        );
        (result, started.elapsed())
    }

    /// E5 final review 4 finding 2: tree termination is bounded by the one cleanup deadline.
    #[test]
    fn tree_termination_never_outlives_the_cleanup_deadline() {
        let bound = Duration::from_millis(300);
        let (result, took) = stubborn_tree(Instant::now() + bound);
        assert_eq!(
            result.unwrap().len(),
            1,
            "an unemptied tree must be reported"
        );
        assert!(
            took < bound + Duration::from_millis(400),
            "tree cleanup took {took:?} for a {bound:?} deadline"
        );
    }

    /// The no-gate branch ends the tree after its deadline already passed: nothing more may be waited.
    #[test]
    fn tree_termination_after_the_deadline_waits_for_nothing() {
        let (result, took) = stubborn_tree(Instant::now());
        assert_eq!(result.unwrap().len(), 1);
        assert!(
            took < Duration::from_millis(300),
            "waited {took:?} past the deadline"
        );
    }

    #[test]
    fn a_tree_that_empties_inside_the_deadline_reports_no_survivors() {
        let result = end_tree_by(
            Instant::now() + Duration::from_secs(5),
            || {},
            |_| Ok(true),
            || panic!("an emptied tree lists no members"),
        );
        assert!(result.unwrap().is_empty());
    }

    /// K4: the worst-case records the create bound uses name every key a real record has, and are at least as long.
    #[test]
    fn the_worst_case_records_cover_a_real_root_and_a_real_pty_record() {
        let pty = SessionPty::open(24, 80).unwrap();
        let real_root = Identity {
            pid: 4,
            start: 133_000_000_000_000_000,
        }
        .record();
        crate::testing::assert_worst_case_covers(
            &WindowsHost.worst_case(),
            &real_root,
            &pty.owned_record().unwrap(),
        );
    }

    /// E5 final review 4 finding 2: a kill or shutdown arriving while the exited root's gate deadline runs
    /// finishes within that deadline; only a cleanup that has not begun gets a fresh budget.
    #[test]
    fn a_late_kill_keeps_the_gate_deadline_that_is_already_running() {
        let now = Instant::now();
        let budget = Duration::from_secs(10);
        let running = now + Duration::from_millis(50);
        assert_eq!(cleanup_deadline(Some(running), now, budget), running);
        assert_eq!(cleanup_deadline(None, now, budget), now + budget);
    }

    /// The whole late-kill cleanup, driven through its waits with the running deadline: it ends at the
    /// original deadline, not a fresh `cleanup_ms` later.
    #[test]
    fn a_late_kill_cleanup_ends_within_the_original_deadline() {
        let started = Instant::now();
        let running = started + Duration::from_millis(200);
        let deadline = cleanup_deadline(Some(running), started, Duration::from_secs(5));
        let mut outcome = Outcome::default();
        let reaped = confirm_root_exit(
            deadline,
            |bound| {
                std::thread::sleep(bound.min(Duration::from_secs(5)));
                false
            },
            || json!({"pid": 9}),
            &mut outcome,
        );
        assert!(!reaped);
        assert!(
            started.elapsed() < Duration::from_millis(600),
            "the late kill waited {:?} past a 200ms running deadline",
            started.elapsed()
        );
    }

    /// E5 final review 4 finding 3: a root whose termination was issued but whose exit was not confirmed in
    /// time is named by the emitted receipt, which is incomplete.
    #[test]
    fn a_root_that_did_not_confirm_its_exit_is_named_in_the_incomplete_receipt() {
        let mut outcome = Outcome::default();
        let reaped = confirm_root_exit(
            Instant::now(),
            |bound| {
                assert!(
                    bound.is_zero(),
                    "an expired deadline leaves nothing to wait"
                );
                false
            },
            || json!({"pid": 4242, "start_kind": "windows-filetime", "start_value": "1"}),
            &mut outcome,
        );
        assert!(!reaped);
        let (complete, survivors, unverifiable) = settle_outcome(outcome, false);
        assert!(!complete);
        assert!(
            unverifiable.iter().any(|entry| entry["pid"] == 4242),
            "root missing from the receipt: survivors {survivors:?}, unverifiable {unverifiable:?}"
        );
        assert!(
            survivors
                .iter()
                .any(|entry| entry["error"] == "root reap timeout"),
            "the failure must still be reported: {survivors:?}"
        );
    }

    #[test]
    fn unknown_and_missing_arguments_fail_startup_and_a_wrong_wire_is_incompatible() {
        let args = |list: &[&str]| list.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        let generation = "0123456789abcdef0123456789abcdef";
        let good = [
            "--wire",
            "2",
            "--runtime-generation",
            generation,
            "--owner-pid",
            "7",
            "--ownership-journal",
            "j",
        ];
        assert!(parse_args::<WindowsHost>(&args(&good)).is_ok());
        let mut with_fd = good.to_vec();
        with_fd.extend(["--control-fd", "3"]);
        assert_eq!(
            parse_args::<WindowsHost>(&args(&with_fd)).err().unwrap().0,
            "STARTUP_FAILED"
        );
        let mut wrong = good.to_vec();
        wrong[1] = "3";
        assert_eq!(
            parse_args::<WindowsHost>(&args(&wrong)).err().unwrap().0,
            "INCOMPATIBLE_WIRE"
        );
    }

    /// R5: the first character of a name may be wider than one byte; slicing after one byte panicked.
    #[test]
    fn an_environment_name_is_judged_by_characters_and_never_sliced_inside_one() {
        assert!(WindowsHost::env_key_ok("PATH"));
        assert!(WindowsHost::env_key_ok("=C:"), "a leading = names a drive directory");
        assert!(WindowsHost::env_key_ok("é"));
        assert!(!WindowsHost::env_key_ok("é=x"));
        assert!(!WindowsHost::env_key_ok("A=B"));
        assert!(!WindowsHost::env_key_ok(""));
        assert!(!WindowsHost::env_key_ok("A\0"));
    }
}
