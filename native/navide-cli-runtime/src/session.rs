//! The session table and the session lifecycle, written once for every platform.
//!
//! A session's life is a state machine on its own thread: it waits for the root's exit, the stream's end, a
//! `KILL` or the runtime's shutdown, and for the sampling and reaping deadlines in between. What it does at each
//! step that differs by operating system (signals, reaping, ownership evidence, closing the terminal) is the
//! `Host`'s; the order of the steps, the events they publish and the registry bookkeeping are here.
use crate::control::{
    Runtime, EOF_REAP_MS, SNAPSHOT_STEADY_MS, SNAPSHOT_YOUNG_MS, SNAPSHOT_YOUNG_WINDOW_MS, TOMBSTONES,
};
use crate::frame::code;
use crate::host::{Cleanup, ExitCell, ExitInfo, Host, Msg, Outcome};
use crate::refusal::{Code, Refusal};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::AtomicBool;
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// Settled sessions kept in the table before the oldest is evicted into a tombstone (E1 v8 5.5).
const SETTLED_KEPT: usize = 256;
/// At most this many descendants travel in one `SESSION_OWNERSHIP` part.
const OWNERSHIP_PART: usize = 128;
/// The longest survivor list one frame carries; the rest is counted, not listed, so a frame never outgrows
/// `max_frame_r2p`.
pub const MAX_LISTED_SURVIVORS: usize = 128;

/// One session, as the control plane and every thread see it.
pub struct SessionHandle<H: Host> {
    pub id: String,
    pub generation: String,
    pub pane: String,
    pub workspace: String,
    pub agent_key: String,
    pub platform: H::Session,
    pub tx: mpsc::Sender<Msg>,
    pub exit: Arc<ExitCell>,
    pub done: (Mutex<Option<bool>>, Condvar),
    pub adopted: AtomicBool,
    pub started: Instant,
    /// Survivors named by this session's `SESSION_CLEANED` (what `SHUTDOWN` reports exactly).
    pub survivors: Mutex<Vec<Value>>,
}

impl<H: Host> SessionHandle<H> {
    pub fn meta(&self) -> Value {
        json!({"session_id":self.id,"session_generation":self.generation,
            "pane_id":self.pane,"workspace_path":self.workspace})
    }
    pub fn finished(&self) -> bool {
        self.done.0.lock().unwrap().is_some()
    }
    pub fn send(&self, message: Msg) {
        let _ = self.tx.send(message);
    }
    /// Block until the session's cleanup is final (`Some(complete)`) or `limit` elapsed.
    pub fn wait_finished(&self, limit: Duration) -> Option<bool> {
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

/// The verdict of a native `cancel` for a create request.
#[derive(Debug, PartialEq)]
pub enum CancelVerdict {
    /// The ownership gate has not opened and will refuse: the target can never execute.
    Prevented,
    /// The gate opened: the target may be running.
    Released,
    /// The created session is published under this id.
    Registered(String),
    /// The create's answer was already produced and its record is gone.
    Unknown,
}

/// Where a create stands with respect to its target's first instruction. Every transition and every verdict
/// happens under the registry lock, so a verdict can never describe a state the create has already left.
#[derive(Debug, PartialEq)]
pub enum CreatePhase {
    /// Read and admitted, ownership gate still closed.
    Queued,
    /// A cancel won before the release: admission or the gate will refuse.
    Cancelled,
    /// The gate opened; `true` once a cancel arrived.
    Released(bool),
    /// Published as this session id.
    Registered(String),
}

/// At most this many creates may be cancelled before their request is read.
const EARLY_CANCELS: usize = 256;

pub struct Registry<H: Host> {
    pub sessions: HashMap<String, Arc<SessionHandle<H>>>,
    pub reserved: HashSet<String>,
    settled: VecDeque<String>,
    /// Ids (with their generation) of sessions evicted from `sessions` after settling: a late `TTY_EOF` for one of
    /// them is still answered `accepted` (E1 5.5 race item 6; v8 bounds the memory at `TOMBSTONES`).
    tombstones: HashMap<String, String>,
    tombstone_order: VecDeque<String>,
    /// One cancellation state machine per create request still without a final answer or still published: the
    /// only source of a cancel verdict (see `CreatePhase`).
    creates: HashMap<u64, CreatePhase>,
    pub last_request: u64,
    /// Creates being coordinated right now (spawn in progress): shutdown waits for them.
    pub creating: usize,
}

impl<H: Host> Default for Registry<H> {
    fn default() -> Self {
        Self {
            sessions: HashMap::new(),
            reserved: HashSet::new(),
            settled: VecDeque::new(),
            tombstones: HashMap::new(),
            tombstone_order: VecDeque::new(),
            creates: HashMap::new(),
            last_request: 0,
            creating: 0,
        }
    }
}

impl<H: Host> Registry<H> {
    /// The stdin loop routed a create request to its worker.
    pub fn note_create(&mut self, request: u64) {
        self.creates.entry(request).or_insert(CreatePhase::Queued);
    }
    /// The create worker admits the request (before any spawn).
    pub fn admit_create(&mut self, request: u64) -> Result<(), Refusal> {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return Err(Refusal::named(Code::CreateCancelled));
        }
        self.creates.insert(request, CreatePhase::Queued);
        Ok(())
    }
    /// Under the caller's lock, immediately before the target is released.
    pub fn release_gate(&mut self, request: u64) -> bool {
        if self.creates.get(&request) == Some(&CreatePhase::Cancelled) {
            self.creates.remove(&request);
            return false;
        }
        self.creates.insert(request, CreatePhase::Released(false));
        true
    }
    /// The release could not be performed after `release_gate` allowed it: the target is still gated and its
    /// spawn is about to fail.
    pub fn release_failed(&mut self, request: u64) {
        self.creates.insert(request, CreatePhase::Queued);
    }
    /// The create failed or was refused after admission: its answer is an error.
    pub fn create_failed(&mut self, request: u64) {
        self.creates.remove(&request);
    }
    /// Publish the created session; true when a cancel arrived after the release.
    pub fn register_create(&mut self, request: u64, session_id: &str) -> bool {
        let cancel = matches!(
            self.creates.get(&request),
            Some(CreatePhase::Released(true))
        );
        self.creates
            .insert(request, CreatePhase::Registered(session_id.to_owned()));
        cancel
    }
    /// A settled session leaves the registry, and with it its create record.
    pub fn evict_create(&mut self, session_id: &str) {
        self.creates
            .retain(|_, phase| *phase != CreatePhase::Registered(session_id.to_owned()));
    }
    /// The single cancellation verdict for `target`.
    pub fn cancel_verdict(&mut self, target: u64) -> Result<CancelVerdict, Refusal> {
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
                // A create not read yet can still be refused at admission; one whose answer was already
                // produced has no record and is never "prevented".
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
                    return Err(Refusal::new(Code::IoFailed, "cancel admission full"));
                }
                self.creates.insert(target, CreatePhase::Cancelled);
                CancelVerdict::Prevented
            }
        })
    }
    /// How many creates the registry still tracks for cancellation.
    #[cfg(test)]
    pub fn tracked_creates(&self) -> usize {
        self.creates.len()
    }
    /// Is `id` with this `generation` a settled session that was evicted from the table?
    pub fn evicted(&self, id: &str, generation: &str) -> bool {
        self.tombstones
            .get(id)
            .is_some_and(|known| known == generation)
    }
    pub fn settled_count(&self) -> usize {
        self.settled.len()
    }
    /// A session's cleanup is final. The oldest settled sessions leave the table into tombstones.
    fn settle(&mut self, id: &str) {
        self.settled.push_back(id.to_owned());
        while self.settled.len() > SETTLED_KEPT {
            let id = self.settled.pop_front().unwrap();
            if let Some(evicted) = self.sessions.remove(&id) {
                self.tombstones
                    .insert(id.clone(), evicted.generation.clone());
                self.tombstone_order.push_back(id.clone());
                while self.tombstone_order.len() > TOMBSTONES {
                    let oldest = self.tombstone_order.pop_front().unwrap();
                    self.tombstones.remove(&oldest);
                }
            }
            self.reserved.remove(&id);
            self.evict_create(&id);
        }
    }
}

/// A fresh session channel: the sender the platform's waiter and the control plane use, and the mailbox the
/// session thread reads.
pub fn mailbox() -> (mpsc::Sender<Msg>, mpsc::Receiver<Msg>, Arc<ExitCell>) {
    let (tx, rx) = mpsc::channel();
    (tx, rx, Arc::new(ExitCell::new()))
}

/// What only the session's own thread keeps.
pub struct State<H: Host> {
    pub owned: H::Owned,
    pub closing: Option<bool>,
    exited_sent: bool,
    /// Python reported the end of the stream.
    pub eof: bool,
    eof_deadline: Option<Instant>,
    /// Set when the root exited while the stream was still unsettled: when the platform stops waiting for it.
    pub settle_deadline: Option<Instant>,
    /// What the host handed out with its settle window. Only a host that has a window can ever hold one, so a host
    /// without one (`Unsettled = Infallible`) can never be asked for an unsettled cleanup.
    unsettled: Option<H::Unsettled>,
    next_snapshot: Instant,
    /// The member set last published as `SESSION_OWNERSHIP`, in `publish_ownership`'s canonical order. A session
    /// starts with none, so one that never has a descendant never publishes (R9, K2).
    published: Vec<Value>,
}

impl<H: Host> State<H> {
    pub fn new(owned: H::Owned, next_snapshot: Instant) -> Self {
        Self {
            owned,
            closing: None,
            exited_sent: false,
            eof: false,
            eof_deadline: None,
            settle_deadline: None,
            unsettled: None,
            next_snapshot,
            published: Vec::new(),
        }
    }
}

/// One session thread's view, handed to the `Host` for each step it implements.
pub struct SessionCx<'a, H: Host> {
    pub runtime: &'a Runtime<H>,
    pub handle: &'a SessionHandle<H>,
    pub state: &'a mut State<H>,
}

impl<H: Host> SessionCx<'_, H> {
    /// `SESSION_EXITED`: the root was reaped (5.5). Nothing about the stream or the descendants.
    pub fn send_exited(&mut self) {
        if self.state.exited_sent {
            return;
        }
        let Some(info) = self.handle.exit.get() else {
            return;
        };
        self.runtime
            .wire
            .out
            .event(code::SESSION_EXITED, exited_meta(self.handle, &info, self.state.closing.is_some()));
        self.state.exited_sent = true;
    }
}

fn exited_meta<H: Host>(handle: &SessionHandle<H>, info: &ExitInfo, killed: bool) -> Value {
    let mut meta = handle.meta();
    meta["reason"] = json!(if killed { "killed" } else { "exit" });
    meta["exit_code"] = json!(info.exit_code);
    meta["signal"] = json!(info.signal.map(|n| format!("SIG{n}")));
    meta["uptime_ms"] = json!(handle.started.elapsed().as_millis() as u64);
    meta
}

pub fn snapshot_cadence(started: Instant) -> Duration {
    Duration::from_millis(
        if started.elapsed() < Duration::from_millis(SNAPSHOT_YOUNG_WINDOW_MS) {
            SNAPSHOT_YOUNG_MS
        } else {
            SNAPSHOT_STEADY_MS
        },
    )
}

/// Bound a survivor list to what one frame carries; the rest is counted.
pub fn bounded_survivors(mut survivors: Vec<Value>) -> Vec<Value> {
    if survivors.len() > MAX_LISTED_SURVIVORS {
        let unlisted = survivors.len() - MAX_LISTED_SURVIVORS;
        survivors.truncate(MAX_LISTED_SURVIVORS);
        survivors.push(json!({"error":format!("{unlisted} further survivors not listed")}));
    }
    survivors
}

/// What `SESSION_CLEANED` reports for an outcome: whether the cleanup is complete, the survivors (a failure is
/// listed as an error entry) and the unverifiable identities.
pub(crate) fn settle_outcome(outcome: Outcome, root_reaped: bool) -> (bool, Vec<Value>, Vec<Value>) {
    let complete = outcome.survivors.is_empty()
        && outcome.unverifiable.is_empty()
        && outcome.failure.is_none()
        && root_reaped;
    let mut survivors = outcome.survivors;
    if let Some(failure) = &outcome.failure {
        survivors.push(json!({"error":failure}));
    }
    (
        complete,
        bounded_survivors(survivors),
        bounded_survivors(outcome.unverifiable),
    )
}

/// `SESSION_CLEANED` and the end of the session thread's work (5.5).
pub fn finish<H: Host>(runtime: &Runtime<H>, handle: &SessionHandle<H>, outcome: Outcome) {
    // An exit whose status the root's own waiter did not read is not a reap (Linux scoped mode only).
    let root_reaped = handle.exit.get().is_some_and(|info| !info.unverified);
    let (complete, survivors, unverifiable) = settle_outcome(outcome, root_reaped);
    let mut meta = handle.meta();
    meta["cleanup_complete"] = json!(complete);
    meta["root_reaped"] = json!(root_reaped);
    *handle.survivors.lock().unwrap() = survivors.clone();
    meta["survivors"] = json!(survivors);
    meta["unverifiable"] = json!(unverifiable);
    #[cfg(feature = "fault-hooks")]
    let complete = if crate::fault::survivor(&handle.pane) {
        let injected =
            json!([{"pid":1,"pgid":1,"start_kind":"posix-lstart","start_value":"fault-survivor"}]);
        *handle.survivors.lock().unwrap() = injected.as_array().cloned().unwrap_or_default();
        meta["survivors"] = injected;
        meta["cleanup_complete"] = json!(false);
        false
    } else {
        complete
    };
    // The platform may close its end of the stream at cleanup, never earlier (5.5 R-a).
    runtime.host.close_stream(handle);
    #[cfg(feature = "fault-hooks")]
    let silent = crate::fault::cleaned_silent(&handle.pane);
    #[cfg(not(feature = "fault-hooks"))]
    let silent = false;
    if !silent {
        runtime.wire.out.event(code::SESSION_CLEANED, meta);
    }
    *handle.done.0.lock().unwrap() = Some(complete);
    handle.done.1.notify_all();
    runtime.host.retire(handle);
    runtime.registry().settle(&handle.id);
}

/// Publish the session's members as `SESSION_OWNERSHIP` parts, once per change of the set (K2). A change to the empty
/// set is published as a single final part, so Python learns that the last descendant is gone (R9); an unchanged
/// set, an empty one included, sends nothing, so an idle session costs the peer no event and no registry reload.
///
/// The hosts list their members out of hash maps, so the set is put in pid order first: the same members in another
/// order are not a change.
fn publish_ownership<H: Host>(
    runtime: &Runtime<H>,
    handle: &SessionHandle<H>,
    published: &mut Vec<Value>,
    mut members: Vec<Value>,
) {
    members.sort_by_key(|member| member["pid"].as_u64());
    if *published == members {
        return;
    }
    let seq = handle.started.elapsed().as_millis() as u64;
    let parts: Vec<&[Value]> = if members.is_empty() {
        vec![&members[..]]
    } else {
        members.chunks(OWNERSHIP_PART).collect()
    };
    let last = parts.len() - 1;
    for (part, values) in parts.into_iter().enumerate() {
        let mut meta = handle.meta();
        meta["snapshot_seq"] = json!(seq);
        meta["part"] = json!(part);
        meta["final"] = json!(part == last);
        meta["descendants"] = json!(values);
        runtime.wire.out.event(code::SESSION_OWNERSHIP, meta);
    }
    *published = members;
}

pub fn session_thread<H: Host>(
    runtime: Arc<Runtime<H>>,
    handle: Arc<SessionHandle<H>>,
    mailbox: mpsc::Receiver<Msg>,
    owned: H::Owned,
) {
    let host = &runtime.host;
    let mut state = State::new(
        owned,
        host.next_snapshot(Instant::now() + snapshot_cadence(handle.started)),
    );
    macro_rules! cx {
        () => {
            SessionCx {
                runtime: &runtime,
                handle: &handle,
                state: &mut state,
            }
        };
    }
    loop {
        let mut wake = state.next_snapshot;
        if let Some(deadline) = state.eof_deadline {
            wake = wake.min(deadline);
        }
        if let Some(deadline) = state.settle_deadline {
            if !host.stream_settled(&cx!()) {
                wake = wake.min(deadline);
            }
        }
        match mailbox.recv_timeout(wake.saturating_duration_since(Instant::now())) {
            Ok(Msg::Exited | Msg::Servicing) => {
                cx!().send_exited();
                if handle.exit.get().is_some() {
                    // The stream is closed only once the root exited and Python services or closed it.
                    if host.stream_settled(&cx!()) {
                        let outcome = host.cleanup(&mut cx!(), Cleanup::Natural);
                        return finish(&runtime, &handle, outcome);
                    }
                    if state.settle_deadline.is_none() {
                        if let Some((window, unsettled)) = host.settle_window() {
                            state.settle_deadline = Some(Instant::now() + window);
                            state.unsettled = Some(unsettled);
                        }
                    }
                }
            }
            Ok(Msg::Kill(force)) => {
                state.closing = Some(force);
                let outcome = host.cleanup(&mut cx!(), Cleanup::Kill { force });
                cx!().send_exited();
                return finish(&runtime, &handle, outcome);
            }
            Ok(Msg::Shutdown { peer_alive }) => {
                state.closing = Some(false);
                let outcome = host.cleanup(&mut cx!(), Cleanup::Shutdown { peer_alive });
                cx!().send_exited();
                return finish(&runtime, &handle, outcome);
            }
            Ok(Msg::TtyEof) => {
                state.eof = true;
                if handle.exit.get().is_some() {
                    cx!().send_exited();
                    let outcome = host.cleanup(&mut cx!(), Cleanup::Natural);
                    return finish(&runtime, &handle, outcome);
                }
                state.eof_deadline = Some(Instant::now() + Duration::from_millis(EOF_REAP_MS));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                if let (Some(deadline), Some(unsettled)) = (state.settle_deadline, state.unsettled) {
                    if now >= deadline && !host.stream_settled(&cx!()) {
                        let outcome = host.cleanup(&mut cx!(), Cleanup::Unsettled(unsettled));
                        return finish(&runtime, &handle, outcome);
                    }
                }
                if state.eof_deadline.is_some_and(|deadline| now >= deadline) {
                    state.eof_deadline = None;
                    // The root may have exited just now: its `Exited` message is already queued.
                    if handle.exit.get().is_none() {
                        runtime.wire.out.event(
                            code::SESSION_ROOT_LIVE,
                            json!({"session_id":handle.id,"session_generation":handle.generation}),
                        );
                    }
                }
                if now >= state.next_snapshot {
                    let due = state.next_snapshot;
                    if let Some(members) = host.snapshot(&mut cx!(), due) {
                        publish_ownership(&runtime, &handle, &mut state.published, members);
                    }
                    // The host decides how soon it may be asked again: a host whose snapshots can be costly
                    // spaces them by what they actually cost (see `scan_board`), not by how long this session
                    // waited for them.
                    state.next_snapshot = host.next_snapshot(Instant::now() + snapshot_cadence(handle.started));
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
        }
    }
}

#[cfg(test)]
mod ownership_tests {
    use super::*;
    use crate::testing::{self, MockHost};

    /// Every `SESSION_OWNERSHIP` event the snapshots in `rounds` make a session publish, in order, as metas. Each
    /// round is one snapshot's member set.
    fn published(rounds: &[Vec<Value>]) -> Vec<Value> {
        let runtime = testing::runtime(MockHost::default());
        let (handle, _mailbox) = testing::handle::<MockHost>(());
        let mut state = State::<MockHost>::new((), Instant::now());
        for members in rounds {
            publish_ownership(&runtime, &handle, &mut state.published, members.clone());
        }
        testing::sent(&runtime)
            .into_iter()
            .map(|frame| {
                assert_eq!(frame.code, code::SESSION_OWNERSHIP);
                frame.meta
            })
            .collect()
    }

    fn member(pid: u64) -> Value {
        json!({"pid":pid,"pgid":pid,"start_kind":"mock","start_value":"x"})
    }

    /// R9: with no members `chunks` yields nothing, so the last descendant exiting was never announced.
    #[test]
    fn a_change_to_the_empty_set_is_published_as_one_final_part() {
        let metas = published(&[vec![member(7)], vec![]]);
        assert_eq!(metas.len(), 2, "the member, then its going away");
        assert_eq!(metas[1]["descendants"], json!([]));
        assert_eq!((metas[1]["part"].clone(), metas[1]["final"].clone()), (json!(0), json!(true)));
    }

    /// K2: an idle session costs the peer nothing: no event, so no registry reload for it.
    #[test]
    fn an_unchanged_set_is_not_published_again_and_a_session_with_no_member_never_publishes() {
        let metas = published(&[vec![], vec![], vec![member(7)], vec![member(7)], vec![member(7)], vec![]]);
        let sets: Vec<Value> = metas.iter().map(|meta| meta["descendants"].clone()).collect();
        assert_eq!(sets, vec![json!([member(7)]), json!([])], "one event per change: {metas:?}");
    }

    /// The hosts list members out of hash maps: the same set in another order is not a change.
    #[test]
    fn the_same_members_in_another_order_are_not_a_change() {
        let metas = published(&[vec![member(9), member(3)], vec![member(3), member(9)]]);
        assert_eq!(metas.len(), 1);
        assert_eq!(metas[0]["descendants"], json!([member(3), member(9)]), "published in pid order");
    }

    #[test]
    fn a_member_that_changes_is_published_again() {
        let mut moved = member(7);
        moved["pgid"] = json!(8);
        assert_eq!(published(&[vec![member(7)], vec![moved]]).len(), 2);
    }

    #[test]
    fn a_large_member_set_travels_in_parts_and_only_the_last_is_final() {
        let members: Vec<Value> = (0..OWNERSHIP_PART as u64 + 2).map(member).collect();
        let metas = published(&[members]);
        assert_eq!(metas.len(), 2);
        assert_eq!(metas[0]["descendants"].as_array().unwrap().len(), OWNERSHIP_PART);
        assert_eq!(metas[0]["final"], json!(false));
        assert_eq!(metas[1]["descendants"].as_array().unwrap().len(), 2);
        assert_eq!(metas[1]["final"], json!(true));
    }
}

#[cfg(test)]
mod cancel_state_tests {
    use super::*;
    #[cfg(unix)]
    type Plat = crate::posix::PosixHost;
    #[cfg(windows)]
    type Plat = crate::windows::WindowsHost;

    fn queued() -> Registry<Plat> {
        // As the request loop does: the request counter is recorded before the create is routed.
        let mut registry = Registry::<Plat> {
            last_request: 7,
            ..Registry::default()
        };
        registry.note_create(7);
        registry.admit_create(7).unwrap();
        registry
    }

    #[test]
    fn a_cancel_before_release_is_prevented_and_the_gate_then_refuses() {
        let mut registry = queued();
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        assert!(
            !registry.release_gate(7),
            "the gate must refuse a prevented create"
        );
    }

    #[test]
    fn a_cancel_after_the_release_is_released() {
        let mut registry = queued();
        assert!(registry.release_gate(7));
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Released));
    }

    #[test]
    fn a_cancel_after_the_release_but_before_registration_closes_the_session() {
        // N1 window 1: the gate opened, the session is not yet published.
        let mut registry = queued();
        assert!(registry.release_gate(7));
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
        let mut registry = queued();
        assert!(registry.release_gate(7));
        assert!(!registry.register_create(7, "s7"));
        assert_eq!(
            registry.cancel_verdict(7),
            Ok(CancelVerdict::Registered("s7".into()))
        );
    }

    #[test]
    fn a_cancel_for_a_finished_create_is_unknown_not_prevented() {
        let mut registry = queued();
        assert!(registry.release_gate(7));
        registry.register_create(7, "s7");
        registry.evict_create("s7");
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Unknown));
        let mut failed = queued();
        failed.create_failed(7);
        assert_eq!(failed.cancel_verdict(7), Ok(CancelVerdict::Unknown));
    }

    #[test]
    fn a_cancel_that_wins_before_admission_refuses_the_create() {
        let mut registry = Registry::<Plat> {
            last_request: 7,
            ..Registry::default()
        };
        registry.note_create(7);
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        assert_eq!(
            registry.admit_create(7),
            Err(Refusal::named(Code::CreateCancelled))
        );
    }

    #[test]
    fn a_cancel_for_a_create_not_read_yet_is_refused_at_admission() {
        let mut registry = Registry::<Plat> {
            last_request: 6,
            ..Registry::default()
        };
        assert_eq!(registry.cancel_verdict(7), Ok(CancelVerdict::Prevented));
        registry.last_request = 7;
        registry.note_create(7);
        assert_eq!(
            registry.admit_create(7),
            Err(Refusal::named(Code::CreateCancelled))
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_long_survivor_list_is_bounded_and_counted() {
        let many: Vec<Value> = (0..MAX_LISTED_SURVIVORS + 5).map(|n| json!({"pid":n})).collect();
        let bounded = bounded_survivors(many);
        assert_eq!(bounded.len(), MAX_LISTED_SURVIVORS + 1);
        assert_eq!(bounded[MAX_LISTED_SURVIVORS]["error"], "5 further survivors not listed");
        assert_eq!(bounded_survivors(vec![json!({"pid":1})]).len(), 1);
    }

    /// K7: a host with no settle window has `Infallible` for its token, so an unsettled cleanup of it cannot be built.
    /// This only compiles because that arm is uninhabited.
    #[test]
    fn an_unsettled_cleanup_cannot_be_built_for_a_host_without_a_settle_window() {
        use crate::testing::MockHost;
        fn handled(how: Cleanup<<MockHost as Host>::Unsettled>) -> &'static str {
            match how {
                Cleanup::Natural => "natural",
                Cleanup::Kill { .. } => "kill",
                Cleanup::Shutdown { .. } => "shutdown",
                Cleanup::Unsettled(never) => match never {},
            }
        }
        assert_eq!(handled(Cleanup::Natural), "natural");
        assert!(MockHost::default().settle_window().is_none());
    }
}
