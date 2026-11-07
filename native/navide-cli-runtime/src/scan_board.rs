//! The sidecar-wide cache of whole-host process reads and the one cost policy that bounds how often they are made
//! (B-R1). It is generic over what a read returns, so its rules are testable on any host; only Linux has a use for it
//! (a snapshot there may read every `/proc` entry), so only Linux builds it outside tests.
//!
//! Two rules live here and nowhere else:
//!
//! * **Sharing.** The last two reads are kept, numbered. A session takes the oldest cached read that began at or after
//!   the moment its own snapshot became due and that it has not used yet; only if there is none does it read. The lock
//!   also serialises reads, so two sequence numbers are two independent reads.
//! * **Cost.** A *full* read (every process of the host, as against the sidecar's own tree) is charged to the sidecar,
//!   not to a session: what the read itself took, measured inside the lock, so a wait on the lock and the retry
//!   pauses around it are never charged. The next full read is allowed `min(spent * factor, max_gap)` after the
//!   charged one ended, and that time is stored here. Sessions stay on the shared grid: the due time a session asks
//!   for is moved onto the first grid point at or after the allowed time, so every session of the sidecar lands on the
//!   same point and shares the one read.
//!
//! The bound that follows, for a host where a full read takes `spent`: at most one full read per
//! `spent + min(spent * factor, max_gap)`, so the duty is `1 / (1 + factor)` while `spent * factor <= max_gap` and
//! `spent / (spent + max_gap)` above it. With `POLICY` that is 0.1 % of a core up to a 300 ms read, and at most
//! about 1.6 % at the 5 s a read may take at all (`SNAPSHOT_MS`), whatever the number of sessions.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]
use std::io;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

/// How the cost of full reads is bounded.
#[derive(Clone, Copy)]
pub struct Policy {
    /// A full read that took `spent` pushes the next one out by `spent * factor`.
    pub factor: u32,
    /// Never by more than this, so the ownership data Python keeps cannot go stale without bound.
    pub max_gap: Duration,
}

/// B-R1: sampling by full reads may use at most `1 / (1 + COST_FACTOR)` of a core (above the cap, see the module doc).
const COST_FACTOR: u32 = 1000;
/// The longest a full read may push the next one out: five minutes of stale ownership data at the worst.
const STRETCH_MAX_GAP: Duration = Duration::from_secs(300);

/// The policy of the product.
pub const POLICY: Policy = Policy {
    factor: COST_FACTOR,
    max_gap: STRETCH_MAX_GAP,
};

/// One read, numbered, with the moment before its first entry was read.
pub struct Shared<T> {
    pub seq: u64,
    pub started: Instant,
    pub all: Arc<T>,
}

impl<T> Clone for Shared<T> {
    fn clone(&self) -> Self {
        Self {
            seq: self.seq,
            started: self.started,
            all: Arc::clone(&self.all),
        }
    }
}

/// What a read returns and whether it was a full one, which is what the cost policy charges.
pub struct Read<T> {
    pub value: T,
    pub full: bool,
}

struct Inner<T> {
    /// The last two reads, oldest first. Two are kept so a session that wakes a moment late still finds the pair a
    /// faster one used.
    scans: Vec<Shared<T>>,
    /// The earliest moment the next full read may begin.
    next_allowed: Option<Instant>,
}

pub struct Board<T> {
    inner: Mutex<Inner<T>>,
    /// The grid's origin: fixed by the first use, or by `start_grid`.
    epoch: OnceLock<Instant>,
    step: Duration,
    policy: Policy,
}

impl<T> Board<T> {
    pub const fn new(step: Duration, policy: Policy) -> Self {
        Self {
            inner: Mutex::new(Inner {
                scans: Vec::new(),
                next_allowed: None,
            }),
            epoch: OnceLock::new(),
            step,
            policy,
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner<T>> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Fix the grid's origin now. Left to the first caller of `due`, the origin falls a few microseconds after the
    /// moment that caller computed its own due time from, and the first grid point at or before that due time would
    /// be the origin itself: a first snapshot due at once.
    pub fn start_grid(&self) {
        self.epoch.get_or_init(Instant::now);
    }

    /// The oldest cached read with a sequence number above `after` that began at or after `not_before`, else a new
    /// one. A read taken before `not_before` is never returned. A full read is charged what it took.
    pub fn scan_after(
        &self,
        after: u64,
        not_before: Instant,
        read: impl FnOnce() -> io::Result<Read<T>>,
    ) -> io::Result<Shared<T>> {
        let mut inner = self.lock();
        if let Some(cached) = inner
            .scans
            .iter()
            .find(|cached| cached.seq > after && cached.started >= not_before)
        {
            return Ok(cached.clone());
        }
        // Taken after the lock was won: a wait on it is not part of what the read cost.
        let started = Instant::now();
        let Read { value, full } = read()?;
        let ended = Instant::now();
        if full {
            self.charge_in(&mut inner, ended.saturating_duration_since(started), ended);
        }
        let seq = inner.scans.last().map_or(1, |shared| shared.seq + 1);
        let shared = Shared {
            seq,
            started,
            all: Arc::new(value),
        };
        if inner.scans.len() == 2 {
            inner.scans.remove(0);
        }
        inner.scans.push(shared.clone());
        Ok(shared)
    }

    /// Charge a full read that was made outside `scan_after` and took `spent`, ending now.
    pub fn charge(&self, spent: Duration) {
        let mut inner = self.lock();
        self.charge_in(&mut inner, spent, Instant::now());
    }

    fn charge_in(&self, inner: &mut Inner<T>, spent: Duration, ended: Instant) {
        let gap = spent
            .saturating_mul(self.policy.factor)
            .min(self.policy.max_gap);
        let allowed = ended + gap;
        inner.next_allowed = Some(inner.next_allowed.map_or(allowed, |known| known.max(allowed)));
    }

    /// When a session whose own cadence makes its next snapshot due at `own` should take it: the latest grid point at
    /// or before `own` (never later than the cadence, so the bound on how often it runs holds, and sessions snapshot
    /// together), and not before the next full read is allowed: then the first grid point at or after that time.
    pub fn due(&self, own: Instant) -> Instant {
        let allowed = self.lock().next_allowed;
        let epoch = *self.epoch.get_or_init(Instant::now);
        grid_point(epoch, self.step, own, allowed)
    }

    #[cfg(test)]
    fn next_allowed(&self) -> Option<Instant> {
        self.lock().next_allowed
    }
}

/// The grid point for `own`, held back to `allowed` when that is later.
fn grid_point(epoch: Instant, step: Duration, own: Instant, allowed: Option<Instant>) -> Instant {
    let target = allowed.map_or(own, |allowed| allowed.max(own));
    let since = target.saturating_duration_since(epoch).as_nanos();
    let floor = epoch + Duration::from_nanos((since / step.as_nanos() * step.as_nanos()) as u64);
    match allowed {
        Some(allowed) if floor < allowed => floor + step,
        _ => floor,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }

    fn board(step: u64, factor: u32, max_gap: Duration) -> Board<()> {
        Board::new(ms(step), Policy { factor, max_gap })
    }

    fn read(cost: u64, full: bool) -> io::Result<Read<()>> {
        std::thread::sleep(ms(cost));
        Ok(Read { value: (), full })
    }

    #[test]
    fn sessions_due_together_share_one_scan_sequence() {
        let board = board(5000, 1000, POLICY.max_gap);
        let reads = AtomicUsize::new(0);
        let due = Instant::now();
        std::thread::scope(|scope| {
            for _ in 0..4 {
                scope.spawn(|| {
                    // Each session needs two independent scans for its equal-twice rule.
                    let counted = || {
                        reads.fetch_add(1, Ordering::SeqCst);
                        read(60, false)
                    };
                    let first = board.scan_after(0, due, counted).unwrap();
                    let second = board.scan_after(first.seq, due, counted).unwrap();
                    assert!(second.seq > first.seq);
                });
            }
        });
        assert_eq!(reads.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_session_never_uses_a_scan_taken_before_its_request() {
        let board = board(5000, 1000, POLICY.max_gap);
        let old = board.scan_after(0, Instant::now(), || read(5, false)).unwrap();
        std::thread::sleep(ms(5));
        let due = Instant::now();
        let fresh = board.scan_after(0, due, || read(5, false)).unwrap();
        assert!(fresh.seq > old.seq && fresh.started >= due);
        // A scan that began after the request, and was not used yet, is reused.
        let again = board
            .scan_after(old.seq, due, || panic!("a usable scan is cached"))
            .unwrap();
        assert_eq!(again.seq, fresh.seq);
    }

    #[test]
    fn a_failed_scan_is_not_cached() {
        let board = board(5000, 1000, POLICY.max_gap);
        let due = Instant::now();
        assert!(board
            .scan_after(0, due, || Err(io::Error::other("boom")))
            .is_err());
        assert_eq!(board.scan_after(0, due, || read(1, false)).unwrap().seq, 1);
    }

    #[test]
    fn the_grid_never_moves_a_snapshot_later_and_aligns_sessions() {
        let epoch = Instant::now();
        let at = |n: u64| epoch + ms(n);
        let grid = |own: u64| grid_point(epoch, ms(5000), at(own), None);
        // Two sessions with different own due times land on the same grid point.
        assert_eq!(grid(15_200), at(15_000));
        assert_eq!(grid(15_900), at(15_000));
        assert_eq!(grid(15_000), at(15_000));
        // The steady 30 s cadence stays on the same 5 s grid and is never later than its own due time.
        assert_eq!(grid(47_300), at(45_000));
        // One cadence step after a snapshot is still strictly in the future.
        assert!(grid(12_040 + 5000) > at(12_040));
    }

    #[test]
    fn a_full_read_not_yet_allowed_holds_the_snapshot_back_to_the_next_grid_point() {
        let epoch = Instant::now();
        let at = |n: u64| epoch + ms(n);
        let held = |own: u64, allowed: u64| grid_point(epoch, ms(5000), at(own), Some(at(allowed)));
        // Allowed later than the cadence: the first grid point at or after it, the same for every session.
        assert_eq!(held(15_200, 31_000), at(35_000));
        assert_eq!(held(20_100, 31_000), at(35_000));
        // Allowed exactly on a grid point: that point.
        assert_eq!(held(15_200, 30_000), at(30_000));
        // Allowed between the cadence's grid point and the cadence itself: not before it.
        assert_eq!(held(19_000, 17_000), at(20_000));
        // Already allowed: the plain grid.
        assert_eq!(held(19_000, 2_000), at(15_000));
        // Whatever the inputs, never before the allowed time.
        for own in (0..40_000).step_by(700) {
            for allowed in (0..60_000).step_by(1300) {
                assert!(held(own, allowed) >= at(allowed), "own {own} allowed {allowed}");
            }
        }
    }

    /// B-R1 as a duty: sessions that each snapshot on their own cadence while a full read is expensive do at most one
    /// read per stretched period, every read is on a grid point, and the time spent reading stays under the policy.
    #[test]
    fn n_sessions_with_an_expensive_read_do_at_most_one_read_per_stretched_period_on_one_grid_point() {
        const COST: u64 = 20;
        const FACTOR: u32 = 5;
        let board = board(50, FACTOR, Duration::from_secs(10));
        board.start_grid();
        let epoch = *board.epoch.get().unwrap();
        let reads = Mutex::new(Vec::<(Instant, Instant, Instant)>::new()); // due, began, ended
        let end = Instant::now() + ms(1300);
        std::thread::scope(|scope| {
            for _ in 0..4 {
                scope.spawn(|| {
                    let mut used = 0;
                    loop {
                        let due = board.due(Instant::now() + ms(50));
                        std::thread::sleep(due.saturating_duration_since(Instant::now()));
                        if Instant::now() >= end {
                            break;
                        }
                        let shared = board
                            .scan_after(used, due, || {
                                let began = Instant::now();
                                let done = read(COST, true);
                                reads.lock().unwrap().push((due, began, Instant::now()));
                                done
                            })
                            .unwrap();
                        used = shared.seq;
                    }
                });
            }
        });
        let mut reads = reads.into_inner().unwrap();
        reads.sort_by_key(|read| read.1);
        assert!(reads.len() >= 3, "the sessions made progress: {} reads", reads.len());
        for (due, ..) in &reads {
            let offset = due.duration_since(epoch).as_nanos() % ms(50).as_nanos();
            assert_eq!(offset, 0, "a read was taken for a due time off the grid");
        }
        for pair in reads.windows(2) {
            let (_, _, ended) = pair[0];
            let (_, began, _) = pair[1];
            assert!(
                began.duration_since(ended) >= ms(COST) * FACTOR - ms(1),
                "two full reads are closer than the policy allows"
            );
        }
        let busy = ms(COST) * reads.len() as u32;
        let span = reads.last().unwrap().2.duration_since(reads[0].1);
        assert!(
            busy <= span / (FACTOR + 1) + ms(COST) + ms(2),
            "{} reads took {busy:?} of a {span:?} span: more than 1/{} of the time",
            reads.len(),
            FACTOR + 1
        );
    }

    /// A session that only waited on the lock behind a slow read is not stretched: what it is charged is the read it
    /// made itself.
    #[test]
    fn a_session_that_only_waited_on_the_lock_is_not_charged_for_the_wait() {
        let board = board(50, 5, Duration::from_secs(10));
        std::thread::scope(|scope| {
            scope.spawn(|| {
                board.scan_after(0, Instant::now(), || read(200, false)).unwrap();
            });
            std::thread::sleep(ms(20)); // the slow read holds the lock
            let waiter = Instant::now();
            board.scan_after(0, waiter, || read(2, true)).unwrap();
            let allowed = board.next_allowed().expect("a full read was charged");
            // 2 ms of read times a factor of 5 is 10 ms; the 180 ms of waiting would have made it 900 ms.
            assert!(
                allowed <= Instant::now() + ms(80),
                "the wait on the lock was charged: allowed {:?} from now",
                allowed.saturating_duration_since(Instant::now())
            );
        });
    }

    #[test]
    fn a_read_that_is_not_full_is_never_charged_however_long_it_takes() {
        let board = board(50, 5, Duration::from_secs(10));
        board.scan_after(0, Instant::now(), || read(30, false)).unwrap();
        assert!(board.next_allowed().is_none());
    }

    #[test]
    fn a_charged_gap_is_capped_so_the_duty_bound_is_the_stated_one() {
        let board: Board<()> = Board::new(ms(5000), POLICY);
        // 5 s is the longest a read may take (`SNAPSHOT_MS`): 5 s * 1000 would be 83 minutes, the cap makes it 300 s.
        let before = Instant::now();
        board.charge(Duration::from_secs(5));
        let gap = board.next_allowed().unwrap().duration_since(before);
        assert!(gap >= Duration::from_secs(299) && gap <= Duration::from_secs(301), "{gap:?}");
        // The duty at that read: 5 s per (5 s + 300 s), about 1.6 %, as stated in the module's doc.
        let duty = 5.0 / (5.0 + 300.0);
        assert!(duty < 0.0164 && duty > 0.0163, "{duty}");
        // A read of 100 ms is on the factor: 100 ms * 1000 = 100 s, a duty of 1 / 1001.
        let fast: Board<()> = Board::new(ms(5000), POLICY);
        let before = Instant::now();
        fast.charge(ms(100));
        let gap = fast.next_allowed().unwrap().duration_since(before);
        assert!(gap >= Duration::from_secs(99) && gap <= Duration::from_secs(101), "{gap:?}");
    }

    #[test]
    fn a_later_charge_never_shortens_a_gap_already_owed() {
        let board = board(50, 1000, Duration::from_secs(300));
        board.charge(ms(100));
        let owed = board.next_allowed().unwrap();
        board.charge(ms(1));
        assert_eq!(board.next_allowed(), Some(owed));
    }
}
