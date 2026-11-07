//! What a platform supplies to the one control plane.
//!
//! `Runtime<H: Host>` (control.rs, session.rs) owns the frame loop, request admission, the session table, generation
//! and loss handling, the limits and the wire contract. A `Host` is only the part that differs by operating system:
//! spawning a root, the PTY or ConPTY, signals, reaping, ownership evidence and the control channel. The control
//! plane is monomorphised per platform, so no call on the request path is dynamic.
use crate::compose;
use crate::control::{Config, Request, Runtime};
use crate::session::{SessionCx, SessionHandle};
use crate::wire::Outbound;
use serde_json::Value;
use std::io;
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// A message to a session thread.
pub enum Msg {
    Kill(bool),
    TtyEof,
    /// Python's pump is servicing the output (Windows: the pre-close gate is open, 7.3).
    Servicing,
    /// End the session; `peer_alive` says whether the control channel still is (a `SHUTDOWN` request).
    Shutdown { peer_alive: bool },
    Exited,
}

/// How the root ended, in the terms the wire reports (`SESSION_EXITED`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ExitInfo {
    /// The exit code, or the negated signal number for a signalled POSIX root. `None`: the status is unknown.
    pub exit_code: Option<i64>,
    pub signal: Option<i32>,
    /// The root's status was not read by its own waiter (Linux scoped mode only): `ECHILD` there means a
    /// third party reaped it, so the exit is not verified and cleanup cannot be called complete.
    pub unverified: bool,
}

/// The root's exit, set once by the platform's waiter and awaited by the session thread.
pub struct ExitCell {
    state: Mutex<Option<ExitInfo>>,
    changed: Condvar,
}
impl ExitCell {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(None),
            changed: Condvar::new(),
        }
    }
    pub fn get(&self) -> Option<ExitInfo> {
        *self.state.lock().unwrap()
    }
    pub fn set(&self, info: ExitInfo) {
        *self.state.lock().unwrap() = Some(info);
        self.changed.notify_all();
    }
    /// Block until the root has exited or `limit` elapsed (an upper bound on an event wait).
    pub fn wait(&self, limit: Duration) -> Option<ExitInfo> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

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
            exit_code: Some(3),
            signal: None,
            unverified: false,
        });
        assert_eq!(waiter.join().unwrap().and_then(|info| info.exit_code), Some(3));
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "woken by the event, not by the bound"
        );
    }
}

/// What a cleanup established; `SESSION_CLEANED` is built from it.
#[derive(Default)]
pub struct Outcome {
    pub survivors: Vec<Value>,
    pub unverifiable: Vec<Value>,
    pub failure: Option<String>,
}

/// Why a session is being cleaned up.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cleanup {
    /// The root exited and the stream is settled: the remaining members go by identity.
    Natural,
    /// A `KILL` (or a cancel that arrived after the release).
    Kill { force: bool },
    /// The runtime is ending; `peer_alive` is false when the control channel is lost.
    Shutdown { peer_alive: bool },
    /// The root exited and nobody settled the stream within the settle window.
    Unsettled,
}

/// What the platform learned from a resize.
pub enum ResizeFailure {
    /// The terminal is already closed: not an error when nothing was applied yet.
    Closed(io::Error),
    Failed(io::Error),
}

/// What the stream's reader tells the sidecar.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamSignal {
    Servicing,
    Eof,
}

/// Validated `create` arguments, common to every platform.
pub struct CreateArgs {
    pub id: String,
    pub generation: String,
    pub pane: String,
    pub workspace: String,
    pub cwd: String,
    pub agent: String,
    pub argv: Vec<String>,
    /// Who composed `argv` (`rust` or `python`) and the bounded attestation of its program, both reported on
    /// `SESSION_OWNED` (G2 delta section 4).
    pub argv_source: &'static str,
    pub argv0: compose::Argv0,
    /// `Some("credential")` when the managed-credential outer wrapper composed the argv (delta 10.2).
    pub outer: Option<&'static str>,
    pub env: Vec<(String, String)>,
    pub rows: u16,
    pub cols: u16,
}

/// The longest values the platform's parts of `SESSION_OWNED` can take.
pub struct WorstCase {
    pub root: Value,
    pub pty: Value,
    /// The platform can report an `outer` wrapper.
    pub outer: bool,
}

/// The child's argv as the platform composed it.
pub struct Launch {
    pub argv: Vec<String>,
    pub argv_source: &'static str,
    pub argv0: compose::Argv0,
    pub outer: Option<&'static str>,
}

/// A created session: the platform's parts, before the control plane publishes it.
pub struct Spawned<H: Host> {
    pub platform: H::Session,
    pub owned: H::Owned,
    pub exit: Arc<ExitCell>,
    pub tx: mpsc::Sender<Msg>,
    pub mailbox: mpsc::Receiver<Msg>,
}

pub trait Host: Sized + Send + Sync + 'static {
    /// The platform half of a session handle (PTY, close gate, declared shutdown), shared across threads.
    type Session: Send + Sync + 'static;
    /// Ownership evidence only the session's own thread touches.
    type Owned: Send + 'static;
    /// An observer resolved from the wire's vendor field.
    type Observer: Copy + Send;
    /// The open control channel.
    type Link: Send + Sync + 'static;

    const PTY_BACKEND: &'static str;
    const HANDOFF: &'static str;
    /// `TTY_SERVICING` is part of this platform's stream contract.
    const SERVICING: bool;
    /// This platform runs vendor observers.
    const OBSERVES: bool;

    /// Platform start-up that must precede the `ready` line and any session.
    fn boot(config: &Config) -> io::Result<Self>;
    /// Platform-private additions to the `ready` line.
    fn ready(&self, _ready: &mut Value) {}

    // ---- the control channel ------------------------------------------------------------------
    fn open_link(&self, config: &Config) -> io::Result<Self::Link>;
    fn link_read(&self, link: &Self::Link, buffer: &mut [u8]) -> io::Result<usize>;
    /// The writer thread's body: send what `out` queues until it is finished or the channel is lost.
    fn link_write(&self, link: &Self::Link, out: &Outbound) -> io::Result<()>;
    /// The channel is lost: wake the reader as if the peer had closed.
    fn link_break(&self, link: &Self::Link);

    // ---- create -------------------------------------------------------------------------------
    /// Whether `key` may name an environment variable on this platform.
    fn env_key_ok(key: &str) -> bool;
    /// The child's argv from the wire-2 `launch` object (delta section 2), composed with this platform's grammar.
    fn launch(&self, agent: &str, args: &Value, env: &[(String, String)]) -> io::Result<Launch>;
    /// The longest `root` and `pty` records `SESSION_OWNED` can carry, for the pre-spawn size bound (5.1.3).
    fn worst_case(&self) -> WorstCase;
    /// Open the PTY and start the root gated: its identity is made durable before the target executes (5.1).
    fn spawn(&self, runtime: &Runtime<Self>, request: u64, args: &CreateArgs) -> io::Result<Spawned<Self>>;
    fn root_record(&self, owned: &Self::Owned) -> Value;
    /// The `pty` part of `SESSION_OWNED`.
    fn pty_record(&self, session: &Self::Session) -> io::Result<Value>;
    /// Queue `SESSION_OWNED` with the handle the platform transfers.
    fn send_owned(&self, runtime: &Runtime<Self>, handle: &Arc<SessionHandle<Self>>, meta: Value) -> io::Result<()>;
    /// A created session whose thread could not start: end its root and report whether that was verified.
    fn abort_unserved(&self, handle: &SessionHandle<Self>, root: &Value) -> bool;

    // ---- requests -----------------------------------------------------------------------------
    fn resize(&self, session: &Self::Session, rows: u16, cols: u16) -> Result<(), ResizeFailure>;
    /// Python took over the stream: platform originals that were kept for the handoff can go.
    fn adopted(&self, _session: &Self::Session) {}
    fn stream_signal(&self, _session: &Self::Session, _signal: StreamSignal) {}

    // ---- the session thread -------------------------------------------------------------------
    /// The stream is serviced or closed, so the session may be cleaned up once the root exited.
    fn stream_settled(&self, cx: &SessionCx<'_, Self>) -> bool;
    /// How long the stream may stay unsettled after the root exited. `None`: no bound (POSIX).
    fn settle_window(&self) -> Option<Duration> {
        None
    }
    fn cleanup(&self, cx: &mut SessionCx<'_, Self>, how: Cleanup) -> Outcome;
    /// The identity of every current member of the session's tree, or `None` to keep the last good view.
    fn snapshot(&self, cx: &mut SessionCx<'_, Self>, due: Instant) -> Option<Vec<Value>>;
    /// When the session's next snapshot is due, given its own cadence.
    fn next_snapshot(&self, own: Instant) -> Instant {
        own
    }
    /// The cleanup is final: release what the platform kept for the stream.
    fn close_stream(&self, handle: &SessionHandle<Self>);
    /// The session left the table: forget the platform's claim on its names.
    fn retire(&self, _handle: &SessionHandle<Self>) {}
    /// The runtime is ending and every session settled.
    fn teardown(&self, _deadline: Instant) {}

    // ---- observation --------------------------------------------------------------------------
    fn observer(&self, _vendor: &str) -> Option<Self::Observer> {
        None
    }
    fn observe(&self, _observer: Self::Observer, _request: &Request) -> io::Result<Value> {
        Err(crate::refusal::fault(
            crate::refusal::Code::ObserverNotIntegrated,
            "observer not integrated",
        ))
    }
}
