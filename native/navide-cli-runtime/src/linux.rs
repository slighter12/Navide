//! Linux owned metadata from procfs, without a `ps` subprocess.
use super::Table;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

const MAX_PIDS: usize = 4 * 1024 * 1024 / std::mem::size_of::<i32>();
/// `PIDFD_SIGNAL_PROCESS_GROUP` (Linux 6.9+): signal the whole group of the process a pidfd names.
const PIDFD_SIGNAL_PROCESS_GROUP: libc::c_uint = 1 << 2;

/// The kernel start ticks behind the `posix-lstart` values one session recorded, by pid. The wire value
/// has one-second resolution, so two processes that held the same pid within a second share it; the
/// ticks tell them apart. Each session owns its ledger (it lives and dies with the session's `Owned`),
/// so another session's snapshot can never rewrite the evidence this one admits and signals against.
/// Internal only: the table and the wire keep the legacy string.
#[derive(Default)]
pub(super) struct Births(
    HashMap<i32, (String, u64)>,
    /// Live members of a verified owned group that the pre-6.9 group fallback refused to signal because
    /// this session never admitted them: pid -> (parent, group, start string, exact birth ticks). They are
    /// never signalled, so cleanup must report them instead of calling itself complete.
    Mutex<HashMap<i32, (i32, i32, String, u64)>>,
);

impl Births {
    /// Remember a live group member the fallback left alone.
    fn refuse(&self, pid: i32, seen: &Process) {
        let start = match (boot_seconds(), clock_ticks()) {
            (Ok(boot), Ok(ticks)) => {
                legacy_start(seen.birth_ticks, boot, ticks).unwrap_or_default()
            }
            _ => String::new(),
        };
        self.1
            .lock()
            .unwrap()
            .insert(pid, (seen.parent, seen.group, start, seen.birth_ticks));
    }

    /// The refused members that are still alive now (same exact instance, not a zombie). A member whose
    /// state cannot be read stays listed: fail closed.
    pub(super) fn refused_alive(&self) -> Table {
        let mut alive = Table::new();
        for (&pid, (parent, group, start, ticks)) in self.1.lock().unwrap().iter() {
            let gone = match process(pid) {
                Ok(None) => true,
                Ok(Some(live)) => live.birth_ticks != *ticks || matches!(live.state, b'Z' | b'X'),
                Err(_) => false,
            };
            if !gone {
                alive.insert(pid, (*parent, *group, start.clone()));
            }
        }
        alive
    }

    /// Final verification: record every live member of the root's former process group, or of its session when
    /// the runtime made the root a session leader (`setsid`), that this session never admitted. The root may
    /// have exited, or been reaped, before the group fallback ran, so a child born after the last snapshot would
    /// otherwise appear nowhere. Members are only recorded, never signalled: cleanup reports them as unresolved
    /// and `cleanup_complete` stays false. A scan that cannot finish is an error, so cleanup fails closed.
    ///
    /// Reusing the numeric id cannot make this unsafe. A PGID or SID is reusable only after its old group or
    /// session is empty, so a live process under the id is either a member we never admitted or a stranger that
    /// took the id after we were done. The stranger is only listed, which makes the result conservatively
    /// incomplete; no signal ever follows from this scan.
    pub(super) fn record_unadmitted_former_group(&self, root: i32) -> io::Result<()> {
        let (all, unreadable) =
            scan_all(Instant::now() + Duration::from_millis(super::SNAPSHOT_MS))?;
        for pid in unreadable {
            // The stat file failed with something other than "gone", so its group is unknown. The group and
            // session calls do not depend on /proc: an entry that may belong to the root's group or session
            // (or whose ids cannot be read either) leaves the answer open, so cleanup fails closed.
            if let Some((group, session)) = group_ids(pid)? {
                if group == root || session == root {
                    return Err(io::Error::other(format!(
                        "unreadable proc entry {pid} may belong to the former group of {root}"
                    )));
                }
            }
        }
        for (&member, seen) in &all {
            let former = seen.group == root || seen.session == root;
            if !former || matches!(seen.state, b'Z' | b'X') || self.admitted(member, seen) {
                continue;
            }
            self.refuse(member, seen);
        }
        Ok(())
    }

    fn ticks(&self, pid: i32, start: &str) -> Option<u64> {
        self.0
            .get(&pid)
            .filter(|(noted, _)| noted == start)
            .map(|(_, ticks)| *ticks)
    }

    /// Whether this session already admitted exactly this instance of `pid` (same exact birth ticks).
    fn admitted(&self, pid: i32, seen: &Process) -> bool {
        self.0
            .get(&pid)
            .is_some_and(|(_, ticks)| *ticks == seen.birth_ticks)
    }

    /// Drop the evidence of every pid a verified snapshot no longer lists (absent, dead or replaced), so a
    /// long-lived session's ledger follows its live processes. The root's entry stays: it is one record and
    /// it keeps a same-second replacement of the root pid from being taken for the root.
    pub(super) fn retain_live(&mut self, root: i32, snapshot: &Table) {
        self.0
            .retain(|pid, _| *pid == root || snapshot.contains_key(pid));
    }

    /// Record the first instance seen at `pid` with this start string. A later process with the same
    /// string but other ticks is a replacement: it never overwrites, and the caller drops it.
    fn admit(&mut self, pid: i32, start: &str, ticks: u64) -> bool {
        match self.ticks(pid, start) {
            Some(noted) => noted == ticks,
            None => {
                self.0.insert(pid, (start.to_string(), ticks));
                true
            }
        }
    }
}

#[derive(Clone, PartialEq, Eq, Debug)]
struct Process {
    parent: i32,
    group: i32,
    session: i32,
    birth_ticks: u64,
    state: u8,
}

fn parse_stat(pid: i32, bytes: &[u8]) -> io::Result<Process> {
    // `comm` may contain spaces, parentheses, and newlines. The final ") " is
    // the only reliable separator before the fixed-position numeric fields.
    let close = bytes
        .windows(2)
        .rposition(|pair| pair == b") ")
        .ok_or_else(|| io::Error::other("invalid proc stat comm"))?;
    if bytes[..close]
        .split(|byte| *byte == b' ')
        .next()
        .and_then(|head| std::str::from_utf8(head).ok())
        .and_then(|head| head.parse::<i32>().ok())
        != Some(pid)
    {
        return Err(io::Error::other("mismatched proc stat pid"));
    }
    let tail = std::str::from_utf8(&bytes[close + 2..])
        .map_err(|_| io::Error::other("invalid proc stat fields"))?;
    let fields: Vec<_> = tail.split_ascii_whitespace().collect();
    if fields.len() <= 19 {
        return Err(io::Error::other("short proc stat"));
    }
    let parse = |index: usize| -> io::Result<i32> {
        fields[index]
            .parse()
            .map_err(|_| io::Error::other("invalid proc stat number"))
    };
    let birth_ticks = fields[19]
        .parse()
        .map_err(|_| io::Error::other("invalid proc birth"))?;
    if birth_ticks == 0 {
        return Err(io::Error::other("zero proc birth"));
    }
    Ok(Process {
        state: fields[0].as_bytes()[0],
        parent: parse(1)?,
        group: parse(2)?,
        session: parse(3)?,
        birth_ticks,
    })
}

#[cfg(test)]
thread_local! {
    /// Test seam: reading this pid's stat fails with `EACCES`, as a hidden or denied `/proc` entry would.
    pub(super) static DENIED_STAT: std::cell::Cell<Option<i32>> = const { std::cell::Cell::new(None) };
}

/// A file that is gone: the task exited between being named and being read. ENOENT, or ESRCH when it
/// exits while the read is in progress.
fn gone(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::NotFound || error.raw_os_error() == Some(libc::ESRCH)
}

/// Every procfs read the scanners make. One seam: the production reader below and a scripted reader in the
/// tests, so the walk rules can be driven through exact interleavings that a live kernel cannot repeat.
/// `Ok(None)` is "gone"; any other failure is an `Err` and makes the scan fail closed.
trait ProcFs {
    /// The numeric entries of `/proc`.
    fn list(&self) -> io::Result<Vec<i32>>;
    /// `/proc/<pid>/stat`, or `/proc/<pid>/task/<tid>/stat` when `tid` is given.
    fn stat(&self, pid: i32, tid: Option<i32>) -> io::Result<Option<Process>>;
    /// The tids in `/proc/<pid>/task`.
    fn tasks(&self, pid: i32) -> io::Result<Option<Vec<i32>>>;
    /// The pids in `/proc/<pid>/task/<tid>/children`.
    fn children(&self, pid: i32, tid: i32) -> io::Result<Option<Vec<i32>>>;
}

struct RealProc;

fn pid_list(text: &str) -> io::Result<Vec<i32>> {
    text.split_ascii_whitespace()
        .map(|word| {
            word.parse::<i32>()
                .ok()
                .filter(|pid| *pid > 0)
                .ok_or_else(|| io::Error::other("invalid proc pid list"))
        })
        .collect()
}

impl ProcFs for RealProc {
    fn list(&self) -> io::Result<Vec<i32>> {
        let mut pids = Vec::new();
        for entry in std::fs::read_dir("/proc")? {
            let entry = entry?;
            if let Some(pid) = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<i32>().ok())
                .filter(|pid| *pid > 0)
            {
                pids.push(pid);
            }
        }
        Ok(pids)
    }

    fn stat(&self, pid: i32, tid: Option<i32>) -> io::Result<Option<Process>> {
        #[cfg(test)]
        if tid.is_none() && DENIED_STAT.with(|denied| denied.get()) == Some(pid) {
            return Err(io::Error::from_raw_os_error(libc::EACCES));
        }
        let (path, id) = match tid {
            Some(tid) => (format!("/proc/{pid}/task/{tid}/stat"), tid),
            None => (format!("/proc/{pid}/stat"), pid),
        };
        match std::fs::read(path) {
            Ok(bytes) => parse_stat(id, &bytes).map(Some),
            Err(error) if gone(&error) => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn tasks(&self, pid: i32) -> io::Result<Option<Vec<i32>>> {
        let entries = match std::fs::read_dir(format!("/proc/{pid}/task")) {
            Ok(entries) => entries,
            Err(error) if gone(&error) => return Ok(None),
            Err(error) => return Err(error),
        };
        let mut tids = Vec::new();
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) if gone(&error) => return Ok(None),
                Err(error) => return Err(error),
            };
            if let Some(tid) = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<i32>().ok())
            {
                tids.push(tid);
            }
        }
        Ok(Some(tids))
    }

    fn children(&self, pid: i32, tid: i32) -> io::Result<Option<Vec<i32>>> {
        match std::fs::read_to_string(format!("/proc/{pid}/task/{tid}/children")) {
            Ok(text) => pid_list(&text).map(Some),
            Err(error) if gone(&error) => Ok(None),
            Err(error) => Err(error),
        }
    }
}

fn process(pid: i32) -> io::Result<Option<Process>> {
    RealProc.stat(pid, None)
}

fn boot_seconds() -> io::Result<u64> {
    let stat = std::fs::read_to_string("/proc/stat")?;
    stat.lines()
        .find_map(|line| line.strip_prefix("btime "))
        .and_then(|value| value.parse().ok())
        .ok_or_else(|| io::Error::other("missing proc boot time"))
}

fn clock_ticks() -> io::Result<u64> {
    // SAFETY: sysconf queries the process's immutable clock tick rate.
    let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if ticks <= 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(ticks as u64)
}

fn legacy_start(birth_ticks: u64, boot: u64, ticks: u64) -> io::Result<String> {
    let seconds: libc::time_t = boot
        .checked_add(birth_ticks / ticks)
        .ok_or_else(|| io::Error::other("proc birth overflow"))?
        .try_into()
        .map_err(|_| io::Error::other("proc birth overflow"))?;
    // SAFETY: valid input and output pointers for localtime_r.
    let mut local: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::localtime_r(&seconds, &mut local) }.is_null() {
        return Err(io::Error::last_os_error());
    }
    let days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let day = days
        .get(local.tm_wday as usize)
        .ok_or_else(|| io::Error::other("invalid proc birth weekday"))?;
    let month = months
        .get(local.tm_mon as usize)
        .ok_or_else(|| io::Error::other("invalid proc birth month"))?;
    Ok(format!(
        "{day} {month} {:>2} {:02}:{:02}:{:02} {}",
        local.tm_mday,
        local.tm_hour,
        local.tm_min,
        local.tm_sec,
        local.tm_year + 1900
    ))
}

/// The process group and session of `pid` from the kernel, independent of /proc; `None` when it is gone.
fn group_ids(pid: i32) -> io::Result<Option<(i32, i32)>> {
    // SAFETY: plain syscalls taking a pid; errno is read right after.
    let (group, session) = unsafe { (libc::getpgid(pid), libc::getsid(pid)) };
    if group >= 0 && session >= 0 {
        return Ok(Some((group, session)));
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::ESRCH) {
        return Ok(None);
    }
    Err(error)
}

fn scan(deadline: Instant) -> io::Result<HashMap<i32, Process>> {
    scan_all(deadline).map(|(all, _)| all)
}

/// Every readable process, plus the pids whose stat could not be read for a reason other than "gone".
fn scan_all(deadline: Instant) -> io::Result<(HashMap<i32, Process>, Vec<i32>)> {
    scan_all_with(&RealProc, deadline)
}

fn scan_all_with(
    fs: &dyn ProcFs,
    deadline: Instant,
) -> io::Result<(HashMap<i32, Process>, Vec<i32>)> {
    let mut all = HashMap::new();
    let mut unreadable = Vec::new();
    for pid in fs.list()? {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "owned snapshot bound",
            ));
        }
        // An unrelated process may vanish or deny inspection. Retained owned
        // processes are read separately and their errors remain fatal.
        match fs.stat(pid, None) {
            Ok(Some(value)) => {
                if all.insert(pid, value).is_none() && all.len() > MAX_PIDS {
                    return Err(io::Error::other("owned node bound"));
                }
            }
            Ok(None) => {}
            Err(_) => unreadable.push(pid),
        }
    }
    Ok((all, unreadable))
}

/// One session's owned set, derived from the full `all` read of /proc (its own or a shared one).
fn owned_scan(
    fs: &dyn ProcFs,
    seeds: &HashMap<i32, Process>,
    all: &HashMap<i32, Process>,
    deadline: Instant,
) -> io::Result<HashMap<i32, Process>> {
    let mut owned = HashMap::new();
    let mut pending: Vec<_> = seeds
        .iter()
        .map(|(&pid, value)| (pid, value.clone()))
        .collect();
    let mut seen = HashSet::new();
    let mut queued = seeds.clone();
    while let Some((pid, expected)) = pending.pop() {
        if Instant::now() >= deadline || seen.len() >= MAX_PIDS {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "owned snapshot bound",
            ));
        }
        if !seen.insert(pid) {
            continue;
        }
        let Some(value) = fs.stat(pid, None)? else {
            continue;
        };
        if expected.birth_ticks != value.birth_ticks {
            continue; // A replacement never authorizes traversal or signals.
        }
        if matches!(value.state, b'Z' | b'X') {
            continue;
        }
        if value.group <= 0 {
            return Err(io::Error::other("unverified owned group"));
        }
        for (&child, candidate) in all {
            if child != pid && (candidate.parent == pid || candidate.group == value.group) {
                if let Some(old) = queued.get(&child) {
                    if old.birth_ticks != candidate.birth_ticks {
                        return Err(io::Error::new(
                            io::ErrorKind::WouldBlock,
                            "replaced discovery anchor",
                        ));
                    }
                } else {
                    queued.insert(child, candidate.clone());
                    pending.push((child, candidate.clone()));
                }
            }
        }
        if fs.stat(pid, None)?.as_ref() != Some(&value) {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "owned discovery raced identity",
            ));
        }
        owned.insert(pid, value);
    }
    Ok(owned)
}

/// A full read of /proc, numbered so a session can tell a scan it already used from a new one.
#[derive(Clone)]
struct Shared {
    seq: u64,
    /// Taken before the first `/proc` entry was read.
    started: Instant,
    all: Arc<HashMap<i32, Process>>,
}

/// The sidecar-wide scan cache: the last two scans, oldest first. Its lock also serialises scans, so scan `k + 1`
/// always starts after scan `k` ended: two sequence numbers are two independent reads. Two are kept so a session
/// that wakes a moment late still finds the pair a faster one used.
struct Board(Mutex<Vec<Shared>>);

impl Board {
    /// The oldest cached scan with a sequence number above `after` that started at or after `not_before`, else a
    /// new read. A scan taken before `not_before` is never returned.
    fn scan_after(
        &self,
        after: u64,
        not_before: Instant,
        read: impl FnOnce() -> io::Result<HashMap<i32, Process>>,
    ) -> io::Result<Shared> {
        let mut scans = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(cached) = scans
            .iter()
            .find(|cached| cached.seq > after && cached.started >= not_before)
        {
            return Ok(cached.clone());
        }
        let started = Instant::now();
        let all = Arc::new(read()?);
        let seq = scans.last().map_or(1, |shared| shared.seq + 1);
        let shared = Shared { seq, started, all };
        if scans.len() == 2 {
            scans.remove(0);
        }
        scans.push(shared.clone());
        Ok(shared)
    }
}

static BOARD: Board = Board(Mutex::new(Vec::new()));

static EPOCH: OnceLock<Instant> = OnceLock::new();

/// The latest point of the sidecar-wide 5 s grid at or before `own_due`. Never later than the session's own due
/// time, so the "at most every 5 s / 30 s" bound holds; sessions snapshotting on the grid share their scans.
pub(super) fn grid_due(own_due: Instant) -> Instant {
    grid_due_from(*EPOCH.get_or_init(Instant::now), own_due)
}

fn grid_due_from(epoch: Instant, own_due: Instant) -> Instant {
    let step = super::SNAPSHOT_YOUNG_MS;
    let since = own_due.saturating_duration_since(epoch).as_millis() as u64;
    epoch + Duration::from_millis(since / step * step)
}

/// Where a snapshot gets its `/proc` reads.
#[derive(Clone, Copy)]
pub(super) enum Reads {
    /// Every scan is this snapshot's own (`terminal.create`).
    Own,
    /// Scans are shared with the other sessions due at the same time. `due` is when this snapshot became
    /// due; no scan taken before it is used.
    Shared { due: Instant },
}

pub(super) fn snapshot(
    initial_child: Option<i32>,
    seeds: &Table,
    births: &mut Births,
) -> io::Result<Table> {
    snapshot_with(initial_child, seeds, births, Reads::Own)
}

pub(super) fn snapshot_with(
    initial_child: Option<i32>,
    seeds: &Table,
    births: &mut Births,
    reads: Reads,
) -> io::Result<Table> {
    let _flush = counters::Flush;
    let deadline = Instant::now() + Duration::from_millis(super::SNAPSHOT_MS);
    let boot = boot_seconds()?;
    let ticks = clock_ticks()?;
    let mut known = HashMap::new();
    if let Some(pid) = initial_child {
        if !seeds.is_empty() || pid <= 0 {
            return Err(io::Error::other("invalid initial child admission"));
        }
        if let Some(value) = process(pid)? {
            known.insert(pid, value);
        }
    }
    for (&pid, expected) in seeds {
        if expected.2.is_empty() {
            return Err(io::Error::other("unknown retained birth"));
        }
        if let Some(value) = process(pid)? {
            // The string alone cannot tell a same-second replacement from the retained process.
            if legacy_start(value.birth_ticks, boot, ticks)? == expected.2
                && births
                    .ticks(pid, &expected.2)
                    .is_none_or(|noted| noted == value.birth_ticks)
            {
                known.insert(pid, value);
            }
        }
    }
    let mut previous = None;
    let mut used = 0;
    loop {
        if Instant::now() >= deadline {
            counters::skipped();
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "unstable owned snapshot",
            ));
        }
        // `scanned_at` is when the read behind `all` began: nothing admitted after it can be judged by it.
        let (all, scanned_at) = match reads {
            Reads::Own => {
                let at = Instant::now();
                (Arc::new(read_all(&known, deadline)?), at)
            }
            Reads::Shared { due } => {
                let shared = BOARD.scan_after(used, due, || read_all(&known, deadline))?;
                used = shared.seq;
                // Another session's walk may have been shared with this one: a live process this session
                // already knows must be in it, or the full scan is read instead.
                if scoped() && omitted_known(&RealProc, &known, &shared.all)? {
                    counters::fallback("omitted");
                    let at = Instant::now();
                    (Arc::new(scan(deadline)?), at)
                } else {
                    (shared.all, shared.started)
                }
            }
        };
        let current = match owned_scan(&RealProc, &known, &all, deadline) {
            Ok(current) => current,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => continue,
            Err(error) => return Err(error),
        };
        if previous.as_ref() == Some(&current) {
            let mut table = Table::new();
            let mut admitted = Vec::new();
            for (pid, value) in current {
                let start = legacy_start(value.birth_ticks, boot, ticks)?;
                // A same-second replacement of a pid this session already recorded is not owned.
                if births.admit(pid, &start, value.birth_ticks) {
                    table.insert(pid, (value.parent, value.group, start));
                    admitted.push((pid, value.birth_ticks));
                }
            }
            if scoped() {
                note_admitted(&admitted, &all, scanned_at);
            }
            return Ok(table);
        }
        known.extend(current.clone());
        previous = Some(current);
    }
}

// ---- scoped ownership enumeration (design v6) -------------------------------------------------

/// Scan counters for the isolated harness build. They exist only under `fault-hooks`: the scored product binary
/// carries none of this, and writes none of it. `NAVIDE_SCAN_STATS_FILE` names the JSON file rewritten after
/// each snapshot.
#[cfg(feature = "fault-hooks")]
mod counters {
    use super::*;
    use serde_json::json;
    use std::collections::{BTreeMap, BTreeSet, VecDeque};

    #[derive(Default)]
    struct Inner {
        scans_scoped: u64,
        scans_full: u64,
        fallbacks: BTreeMap<&'static str, u64>,
        inspected_pids: u64,
        walk_reads: u64,
        pid_set: BTreeSet<i32>,
        /// Every pid any walk read a stat of (a superset of `pid_set`, which holds the walked pids only).
        stat_pid_set: BTreeSet<i32>,
        walks: VecDeque<(usize, Vec<i32>, usize)>,
        dirty: [u64; 5],
        skipped: u64,
        subreaper: bool,
        reaper_running: bool,
        reaped: u64,
        unattributed: u64,
    }

    fn inner() -> &'static Mutex<Inner> {
        static INNER: OnceLock<Mutex<Inner>> = OnceLock::new();
        INNER.get_or_init(|| Mutex::new(Inner::default()))
    }

    fn with(act: impl FnOnce(&mut Inner)) {
        act(&mut inner().lock().unwrap_or_else(|e| e.into_inner()));
    }

    pub(super) fn scan_full_only() {
        with(|c| c.scans_full += 1);
    }

    pub(super) fn source(source: &Source) {
        with(|c| match source {
            Source::Scoped => c.scans_scoped += 1,
            Source::Full(why) => {
                c.scans_full += 1;
                let reason = match why {
                    Fallback::Dirty(_) => "dirty",
                    Fallback::Omitted => "omitted",
                    Fallback::Failed => "io",
                };
                *c.fallbacks.entry(reason).or_default() += 1;
            }
        });
    }

    pub(super) fn fallback(reason: &'static str) {
        with(|c| *c.fallbacks.entry(reason).or_default() += 1);
    }

    pub(super) fn walk(info: &WalkInfo, dirty: &Dirty) {
        with(|c| {
            c.walk_reads += 1;
            c.inspected_pids += info.stat_reads as u64;
            c.pid_set.extend(info.walked.iter().copied());
            c.stat_pid_set.extend(info.stat_pids.iter().copied());
            c.walks.push_back((info.stat_reads, info.walked.clone(), info.sidecar_tids));
            if c.walks.len() > 32 {
                c.walks.pop_front();
            }
            for (slot, hit) in [dirty.r1, dirty.r2, dirty.r2b, dirty.r3, dirty.io].into_iter().enumerate() {
                c.dirty[slot] += hit as u64;
            }
        });
    }

    pub(super) fn skipped() {
        with(|c| c.skipped += 1);
    }

    pub(super) fn subreaper(on: bool) {
        with(|c| c.subreaper = on);
    }

    /// Called by the reaper thread itself when it starts its loop.
    pub(super) fn reaper_running() {
        with(|c| c.reaper_running = true);
    }

    /// Called when the reaper thread ends for any reason (its stop guard).
    pub(super) fn reaper_stopped() {
        with(|c| c.reaper_running = false);
        flush();
    }

    #[cfg(test)]
    pub(super) fn reaper_is_running() -> bool {
        let mut running = false;
        with(|c| running = c.reaper_running);
        running
    }

    pub(super) fn reaped() {
        with(|c| c.reaped += 1);
    }

    pub(super) fn unattributed() {
        with(|c| c.unattributed += 1);
    }

    /// Adopted children of the sidecar that exited and were not reaped.
    fn unreaped() -> u64 {
        let me = std::process::id();
        let mut count = 0;
        if let Ok(tasks) = std::fs::read_dir(format!("/proc/{me}/task")) {
            for task in tasks.flatten() {
                let Ok(text) = std::fs::read_to_string(task.path().join("children")) else {
                    continue;
                };
                for child in text.split_whitespace() {
                    let stat = std::fs::read_to_string(format!("/proc/{child}/stat")).unwrap_or_default();
                    if stat.rfind(") ").is_some_and(|at| stat[at + 2..].starts_with('Z')) {
                        count += 1;
                    }
                }
            }
        }
        count
    }

    pub(super) fn flush() {
        static PATH: OnceLock<Option<std::path::PathBuf>> = OnceLock::new();
        let Some(path) = PATH.get_or_init(|| std::env::var_os("NAVIDE_SCAN_STATS_FILE").map(Into::into)) else {
            return;
        };
        let unreaped = unreaped();
        let c = inner().lock().unwrap_or_else(|e| e.into_inner());
        let walks: Vec<_> = c
            .walks
            .iter()
            .map(|(reads, walked, tids)| json!({"stat_reads":reads,"walk_pid_set":walked,"sidecar_tids":tids}))
            .collect();
        let report = json!({
            "scans_scoped": c.scans_scoped,
            "scans_full": c.scans_full,
            "fallbacks": c.fallbacks,
            "inspected_pids": c.inspected_pids,
            "inspected_pid_set": c.pid_set,
            "stat_read_pid_set": c.stat_pid_set,
            "walks": walks,
            "subreaper_enabled": c.subreaper,
            "reaper_running": c.reaper_running,
            "unreaped_children": unreaped,
            "walk_reads": c.walk_reads,
            "dirty_walks": {"r1":c.dirty[0],"r2":c.dirty[1],"r2b":c.dirty[2],"r3":c.dirty[3],"io":c.dirty[4]},
            "due_scans_skipped": c.skipped,
            "orphans_reaped": c.reaped,
            "unattributed_orphans": c.unattributed,
        });
        drop(c);
        let temporary = path.with_extension("tmp");
        if std::fs::write(&temporary, report.to_string()).is_ok() {
            let _ = std::fs::rename(&temporary, path);
        }
    }

    /// Rewrites the file when a snapshot ends, by whatever path.
    pub(super) struct Flush;

    impl Drop for Flush {
        fn drop(&mut self) {
            flush();
        }
    }
}

#[cfg(not(feature = "fault-hooks"))]
mod counters {
    use super::*;

    pub(super) fn scan_full_only() {}
    pub(super) fn source(_: &Source) {}
    pub(super) fn fallback(_: &'static str) {}
    pub(super) fn walk(_: &WalkInfo, _: &Dirty) {}
    pub(super) fn skipped() {}
    pub(super) fn subreaper(_: bool) {}
    pub(super) fn reaper_running() {}
    pub(super) fn reaper_stopped() {}
    pub(super) fn reaped() {}
    pub(super) fn unattributed() {}
    pub(super) fn flush() {}
    pub(super) struct Flush;
}

/// Which walk rule found a walk unusable.
#[derive(Default, Clone, Copy, PartialEq, Eq, Debug)]
struct Dirty {
    r1: bool,
    r2: bool,
    r2b: bool,
    r3: bool,
    io: bool,
}

#[derive(Debug)]
enum Incomplete {
    /// A walk rule fired: the walk is retried, then the full scan supplies the table.
    Dirty(Dirty),
    /// A read failed for a reason other than "gone".
    Failed,
}

/// What one walk read, for the counters and the cost assertions.
#[derive(Default, Debug)]
struct WalkInfo {
    stat_reads: usize,
    /// Every pid whose stat (or one of its tids' stat) this walk read; kept only where something reads it back.
    #[cfg(any(test, feature = "fault-hooks"))]
    stat_pids: std::collections::BTreeSet<i32>,
    walked: Vec<i32>,
    sidecar_tids: usize,
}

impl WalkInfo {
    /// Count one stat read of `pid` (or of one of its tids).
    fn stat_read(&mut self, pid: i32) {
        self.stat_reads += 1;
        #[cfg(any(test, feature = "fault-hooks"))]
        self.stat_pids.insert(pid);
        #[cfg(not(any(test, feature = "fault-hooks")))]
        let _ = pid;
    }
}

/// A complete candidate table, or an explicit refusal: never a silently partial table. The walk's reads are
/// published through the counters; the unit tests read them back from here.
#[cfg_attr(not(test), allow(dead_code))]
enum Walk {
    Complete(HashMap<i32, Process>, WalkInfo),
    Incomplete(Incomplete),
}

#[derive(Debug, PartialEq)]
enum Fallback {
    Dirty(Dirty),
    /// A live process the session already knows is missing from the clean walk.
    Omitted,
    Failed,
}

#[derive(Debug, PartialEq)]
enum Source {
    Scoped,
    Full(Fallback),
}

/// The scoped scanner: the zombie baseline lives here, so a new instance (a restarted sidecar) starts empty.
struct Scanner {
    baseline: Mutex<HashSet<(i32, i32, u64)>>,
}

impl Scanner {
    fn new() -> Self {
        Self {
            baseline: Mutex::new(HashSet::new()),
        }
    }

    /// One walk of `root`'s tree through every `/proc/<pid>/task/<tid>/children` file. The walk rules are the
    /// design's (section 1.1) and each one closes a proven hole; do not reorder the reads:
    /// - W2: a process's tasks are listed before any of its tid reads, and again after its last children read;
    /// - W1: every tid's stat is read before that tid's children file;
    /// - R1 (the tid set changed), R2 (a live tid died or changed), R2b (a tid is a zombie at first sight and no
    ///   earlier walk saw it as one), R3 (a printed process is gone or replaced) make the walk dirty;
    /// - W3: the zombie baseline is replaced at the end of every walk that ran to its end, clean or dirty.
    fn walk(&self, fs: &dyn ProcFs, root: i32, deadline: Instant) -> Walk {
        let mut info = WalkInfo::default();
        let mut dirty = Dirty::default();
        match self.read_tree(fs, root, deadline, &mut info, &mut dirty) {
            Ok(table) => {
                counters::walk(&info, &dirty);
                if dirty == Dirty::default() {
                    Walk::Complete(table, info)
                } else {
                    Walk::Incomplete(Incomplete::Dirty(dirty))
                }
            }
            Err(_) => {
                dirty.io = true;
                counters::walk(&info, &dirty);
                Walk::Incomplete(Incomplete::Failed)
            }
        }
    }

    fn read_tree(
        &self,
        fs: &dyn ProcFs,
        root: i32,
        deadline: Instant,
        info: &mut WalkInfo,
        dirty: &mut Dirty,
    ) -> io::Result<HashMap<i32, Process>> {
        // Zombies recorded by walks that ended before this one began.
        let before = self.baseline.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let mut zombies = HashSet::new();
        let mut table = HashMap::new();
        // (pid, tid, starttime, live at first read) of every tid read.
        let mut tids: Vec<(i32, i32, u64, bool)> = Vec::new();
        let mut printed: Vec<(i32, u64)> = Vec::new();
        let mut queue = vec![root];
        let mut queued: HashSet<i32> = HashSet::from([root]);
        let dead = |state: u8| matches!(state, b'Z' | b'X');
        while let Some(pid) = queue.pop() {
            if Instant::now() >= deadline {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "owned snapshot bound"));
            }
            let Some(first) = fs.tasks(pid)? else {
                dirty.r3 = true; // the process left between being printed and being walked
                continue;
            };
            info.stat_read(pid);
            let Some(process) = fs.stat(pid, None)? else {
                dirty.r3 = true;
                continue;
            };
            printed.push((pid, process.birth_ticks));
            table.insert(pid, process);
            info.walked.push(pid);
            if pid == root {
                info.sidecar_tids = first.len();
            }
            let mut found = Vec::new();
            for &tid in &first {
                info.stat_read(pid);
                let Some(task) = fs.stat(pid, Some(tid))? else {
                    continue; // gone: the second listing shows it
                };
                let live = !dead(task.state);
                if !live {
                    let key = (pid, tid, task.birth_ticks);
                    if !before.contains(&key) {
                        dirty.r2b = true;
                    }
                    zombies.insert(key);
                }
                tids.push((pid, tid, task.birth_ticks, live));
                match fs.children(pid, tid)? {
                    Some(list) => found.extend(list),
                    None => {
                        // Either the tid exited (the second listing shows it) or the file does not exist.
                        info.stat_read(pid);
                        if fs.stat(pid, Some(tid))?.is_some() {
                            return Err(io::Error::new(
                                io::ErrorKind::Unsupported,
                                "the children file is gone from a live task",
                            ));
                        }
                    }
                }
            }
            let Some(second) = fs.tasks(pid)? else {
                dirty.r1 = true;
                continue;
            };
            let (mut a, mut b) = (first, second);
            a.sort_unstable();
            b.sort_unstable();
            if a != b {
                dirty.r1 = true;
            }
            for child in found {
                if queued.insert(child) {
                    queue.push(child);
                }
            }
        }
        // End of walk: the same tids and processes, read again.
        for &(pid, tid, start, live) in &tids {
            info.stat_read(pid);
            match fs.stat(pid, Some(tid))? {
                Some(now) => {
                    let now_live = !dead(now.state);
                    if live && (!now_live || now.birth_ticks != start) {
                        dirty.r2 = true;
                    }
                    if !now_live && now.birth_ticks == start {
                        zombies.insert((pid, tid, start));
                    }
                }
                None => {
                    if live {
                        dirty.r2 = true;
                    }
                }
            }
        }
        for &(pid, birth) in &printed {
            info.stat_read(pid);
            if fs.stat(pid, None)?.is_none_or(|now| now.birth_ticks != birth) {
                dirty.r3 = true;
            }
        }
        *self.baseline.lock().unwrap_or_else(|e| e.into_inner()) = zombies;
        Ok(table)
    }

    /// The candidate table for `root`'s tree: a clean walk that lists every live process in `known`, else the
    /// full scan. A dirty walk is retried; whatever the walk could not vouch for, `scan_all` supplies.
    fn table(
        &self,
        fs: &dyn ProcFs,
        root: i32,
        known: &HashMap<i32, Process>,
        deadline: Instant,
    ) -> io::Result<(HashMap<i32, Process>, Source)> {
        let mut why = Fallback::Failed;
        for _ in 0..WALK_ATTEMPTS {
            match self.walk(fs, root, deadline) {
                Walk::Complete(table, _) => {
                    if omitted_known(fs, known, &table)? {
                        why = Fallback::Omitted;
                        break;
                    }
                    return Ok((table, Source::Scoped));
                }
                Walk::Incomplete(Incomplete::Dirty(dirty)) => why = Fallback::Dirty(dirty),
                Walk::Incomplete(Incomplete::Failed) => {
                    why = Fallback::Failed;
                    break;
                }
            }
        }
        let (all, _) = scan_all_with(fs, deadline)?;
        Ok((all, Source::Full(why)))
    }
}

/// How many times a dirty walk is repeated before the full scan takes over.
const WALK_ATTEMPTS: usize = 3;

/// Whether a live process the caller already knows is missing from `table`. The walk rules cannot see a plain
/// omission, so a known process is the witness.
fn omitted_known(
    fs: &dyn ProcFs,
    known: &HashMap<i32, Process>,
    table: &HashMap<i32, Process>,
) -> io::Result<bool> {
    for (&pid, expected) in known {
        if table.contains_key(&pid) {
            continue;
        }
        if fs.stat(pid, None)?.is_some_and(|now| {
            now.birth_ticks == expected.birth_ticks && !matches!(now.state, b'Z' | b'X')
        }) {
            return Ok(true);
        }
    }
    Ok(false)
}

static SCANNER: OnceLock<Scanner> = OnceLock::new();
/// Set when a visited children file vanished from a live task: later scans use the full scan, the subreaper and
/// the reaper stay on.
static CHILDREN_LOST: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// One read of the processes a snapshot works from: the sidecar's tree in scoped mode, else all of `/proc`.
fn read_all(known: &HashMap<i32, Process>, deadline: Instant) -> io::Result<HashMap<i32, Process>> {
    if !scoped() || CHILDREN_LOST.load(std::sync::atomic::Ordering::SeqCst) {
        counters::scan_full_only();
        return scan(deadline);
    }
    let me = std::process::id() as i32;
    let scanner = SCANNER.get_or_init(Scanner::new);
    let (table, source) = scanner.table(&RealProc, me, known, deadline)?;
    if let Source::Full(Fallback::Failed) = source {
        // A read failed. If the sidecar's own children file is gone, the capability was lost: the full scan is
        // used from now on, for the scan only (the subreaper and the reaper stay on).
        if matches!(RealProc.children(me, me), Ok(None) | Err(_)) {
            CHILDREN_LOST.store(true, std::sync::atomic::Ordering::SeqCst);
        }
    }
    counters::source(&source);
    Ok(table)
}

/// Scoped mode is decided once at startup and never changes (design section 4).
static SCOPED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static REAPER: OnceLock<Arc<Reaper>> = OnceLock::new();

#[cfg(test)]
thread_local! {
    /// Test seam: this thread sees scoped mode on or off regardless of the process-wide decision.
    pub(super) static SCOPED_FORCE: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
    /// Test seam: this thread sees this reaper as the process-wide one.
    pub(super) static REAPER_FORCE: std::cell::RefCell<Option<Arc<Reaper>>> =
        const { std::cell::RefCell::new(None) };
}

pub(super) fn scoped() -> bool {
    #[cfg(test)]
    if let Some(forced) = SCOPED_FORCE.with(|forced| forced.get()) {
        return forced;
    }
    SCOPED.load(std::sync::atomic::Ordering::SeqCst)
}

/// The reaper, only in scoped mode.
pub(super) fn reaper() -> Option<Arc<Reaper>> {
    #[cfg(test)]
    if let Some(forced) = REAPER_FORCE.with(|forced| forced.borrow().clone()) {
        return Some(forced);
    }
    REAPER.get().cloned()
}

/// The startup facts the mode decision needs, behind one seam so the order of the checks can be asserted.
trait Startup {
    fn children_readable(&self) -> io::Result<()>;
    fn sigchld_ignored(&self) -> bool;
    fn set_subreaper(&self) -> io::Result<()>;
}

#[derive(Debug, PartialEq)]
enum Mode {
    Scoped,
    Full(&'static str),
}

/// The ordered startup checks (design section 4); the first failure selects the full scan and nothing after it
/// runs, so the subreaper is never enabled on a host where the scoped walk cannot be used.
fn choose_mode(sys: &dyn Startup) -> Mode {
    if sys.children_readable().is_err() {
        return Mode::Full("no_children");
    }
    if sys.sigchld_ignored() {
        return Mode::Full("sigchld_ignored");
    }
    if sys.set_subreaper().is_err() {
        return Mode::Full("prctl");
    }
    Mode::Scoped
}

struct RealStartup;

impl Startup for RealStartup {
    fn children_readable(&self) -> io::Result<()> {
        #[cfg(feature = "fault-hooks")]
        if std::env::var_os("NAVIDE_FAULT_NO_CHILDREN").is_some() {
            return Err(io::Error::from_raw_os_error(libc::ENOENT));
        }
        let me = std::process::id();
        let text = std::fs::read_to_string(format!("/proc/{me}/task/{me}/children"))?;
        pid_list(&text).map(|_| ())
    }

    fn sigchld_ignored(&self) -> bool {
        // SAFETY: reads the current disposition of SIGCHLD into a zeroed out-parameter.
        let mut old: libc::sigaction = unsafe { std::mem::zeroed() };
        // SAFETY: a null new action only queries.
        if unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut old) } != 0 {
            return true; // unknown: fail closed to the full scan
        }
        old.sa_sigaction == libc::SIG_IGN || old.sa_flags & libc::SA_NOCLDWAIT != 0
    }

    fn set_subreaper(&self) -> io::Result<()> {
        // SAFETY: prctl with an integer option and value.
        if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) } == 0 {
            Ok(())
        } else {
            Err(io::Error::last_os_error())
        }
    }
}

/// Decide the scan mode once, before the first create is accepted. Subreaper on, reaper running, reservation
/// enforced, `ECHILD` on a root fail-closed and the scoped walk are one decision: all on or all off.
pub(super) fn enable_scoped_mode() {
    #[cfg(feature = "fault-hooks")]
    if std::env::var_os("NAVIDE_FAULT_FORCE_FULL").is_some() {
        counters::fallback("forced");
        counters::flush();
        return;
    }
    match choose_mode(&RealStartup) {
        Mode::Full(reason) => {
            eprintln!("cli runtime: scoped ownership scan is off ({reason}); using the full /proc scan");
            counters::fallback(reason);
        }
        Mode::Scoped => {
            let reaper = Reaper::new();
            if let Err(error) = reaper.start() {
                // SAFETY: undo the subreaper so the three stay in step.
                unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0) };
                eprintln!("cli runtime: the reaper did not start ({error}); using the full /proc scan");
                counters::fallback("reaper");
            } else {
                let _ = REAPER.set(reaper);
                SCOPED.store(true, std::sync::atomic::Ordering::SeqCst);
                counters::subreaper(true);
                // Scoped mode only: the snapshot grid starts now. `grid_due` would otherwise start it inside the
                // first session's own first call, after that call computed `own_due = now + 5 s`; the grid's
                // epoch is then a few microseconds later than `now`, `own_due - epoch` truncates to 4999 ms, and
                // the first grid point at or before it is the epoch itself, so that session's first snapshot is
                // due at once, while its target is still starting. With the epoch fixed here the first point
                // falls on the 5 s grid. With scoped mode off nothing is set and the grid is HEAD's.
                EPOCH.get_or_init(Instant::now);
            }
        }
    }
    counters::flush();
}

/// How many 10 ms retries the reaper gives a reserved root's own waiter before it stops waiting for it.
const MAX_DEFERRALS: usize = 100;

/// What the reaper decided about one waitable child.
#[derive(Debug, PartialEq)]
enum Served {
    /// Not reserved: its status was consumed (or it was already gone).
    Reaped,
    /// Reserved and still a waitable child: its own waiter reaps it within microseconds.
    Deferred,
    /// Reserved, but not our child any more: the stale key was removed.
    Stale,
}

#[derive(Debug, PartialEq)]
enum Settled {
    Done,
    GaveUp,
}

/// A root's claim that only its own waiter may consume its exit status. Removes only its own key.
pub(super) struct RootReservation {
    reaper: Arc<Reaper>,
    key: (i32, u64),
}

impl Drop for RootReservation {
    fn drop(&mut self) {
        self.reaper
            .reserved
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&self.key);
    }
}

/// Orphans the sidecar adopted as a subreaper, and the roots it must never reap out from under their waiters.
///
/// Lock order is `FD_FORK_LOCK` -> `gate` -> `reserved`; the reverse is forbidden. `reserved` is locked only for
/// a membership insert, remove or lookup, never across a wait, an `fsync` or any read (rule L1): `gate` is the
/// only lock a create may hold across another thread's work.
pub(super) struct Reaper {
    /// Held by a create from before `fork` until its reservation exists (design section 3).
    gate: Mutex<()>,
    /// `(pid, start ticks)` of every root that is alive and not yet waited for by its own waiter.
    reserved: Mutex<HashSet<(i32, u64)>>,
    /// Spawns that succeeded, so the reaper sleeps in the kernel (or on this condvar) when there is no child.
    spawns: (Mutex<u64>, std::sync::Condvar),
    /// Set, for good, when the reaper thread ends for any reason (an unexpected `waitid` error or a panic).
    /// From then on nobody consumes adopted orphans, so the subreaper's contract is broken and every new
    /// create is refused (`stopped`). Live sessions and cleanup are untouched.
    stopped: std::sync::atomic::AtomicBool,
    /// Test seam: what the loop does on its next pass instead of calling `waitid` (0 = nothing).
    #[cfg(test)]
    inject: std::sync::atomic::AtomicU8,
}

/// Test seam values for `Reaper::inject`.
#[cfg(test)]
pub(super) const INJECT_WAITID_ERROR: u8 = 1;
#[cfg(test)]
pub(super) const INJECT_PANIC: u8 = 2;
/// The loop idles (never calls `waitid`) until the value changes, so a test can pick the moment it ends.
#[cfg(test)]
pub(super) const INJECT_PARK: u8 = 3;

/// Ends the reaper thread's life on every path out of `run`, including a panic: it records the stop (so creates
/// are refused), clears the liveness counter, and says so once on stderr.
struct StopGuard<'a> {
    reaper: &'a Reaper,
    cause: String,
}

impl Drop for StopGuard<'_> {
    fn drop(&mut self) {
        // The stop is published under the create gate. A create holds the gate from its `stopped` check until its
        // root is reserved, so a create that already read `false` finishes (and reserves its root) before this
        // store, and every create that takes the gate afterwards reads `true` and is refused before any fork.
        // Without the gate the store could land between a create's check and its fork, leaving a new root
        // with no reaper.
        //
        // Self-deadlock: the reaper thread never holds `gate` when this runs. The only place the loop takes it is
        // `serve` (through `settle`), and that guard is a local of `serve`: it is released before `serve` returns
        // and, on a panic, during unwinding, before the unwind reaches `run`'s `guard` local (declared before the
        // `run_loop` call, so it drops after everything `run_loop` owns). Nothing else is held either, and the
        // lock order stays `FD_FORK_LOCK` -> `gate` -> `reserved`: this takes `gate` alone, never `reserved`.
        let gate = self.reaper.hold_gate();
        // The counter first: whoever sees the stop also sees the liveness counter already cleared.
        counters::reaper_stopped();
        self.reaper
            .stopped
            .store(true, std::sync::atomic::Ordering::SeqCst);
        drop(gate);
        eprintln!(
            "cli runtime: the reaper stopped ({}); new sessions are refused, running sessions are unaffected",
            self.cause
        );
    }
}

impl Reaper {
    pub(super) fn new() -> Arc<Self> {
        Arc::new(Self {
            gate: Mutex::new(()),
            reserved: Mutex::new(HashSet::new()),
            spawns: (Mutex::new(0), std::sync::Condvar::new()),
            stopped: std::sync::atomic::AtomicBool::new(false),
            #[cfg(test)]
            inject: std::sync::atomic::AtomicU8::new(0),
        })
    }

    /// Whether the reaper thread has ended. Checked by a create while it holds `gate`, before the fork.
    pub(super) fn stopped(&self) -> bool {
        self.stopped.load(std::sync::atomic::Ordering::SeqCst)
    }

    /// The `reaper_running` counter, which exists only with `fault-hooks`.
    #[cfg(all(test, feature = "fault-hooks"))]
    pub(super) fn counter_running() -> bool {
        counters::reaper_is_running()
    }

    #[cfg(test)]
    pub(super) fn inject(&self, what: u8) {
        self.inject.store(what, std::sync::atomic::Ordering::SeqCst);
    }

    /// Wait (bounded) until the reaper thread has ended; true if it did.
    #[cfg(test)]
    pub(super) fn wait_stopped(&self, within: Duration) -> bool {
        let end = Instant::now() + within;
        while Instant::now() < end {
            if self.stopped() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        self.stopped()
    }

    pub(super) fn hold_gate(&self) -> std::sync::MutexGuard<'_, ()> {
        self.gate.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Reserve the root `pid`: only its own waiter may consume its status from now until the guard drops.
    pub(super) fn reserve(self: &Arc<Self>, pid: i32) -> RootReservation {
        // A root that is already gone keys on 0; the key only has to differ between two holders of one pid.
        let birth = process(pid).ok().flatten().map_or(0, |seen| seen.birth_ticks);
        self.reserve_key(pid, birth)
    }

    /// A create has a new child: wake the reaper if it is idle.
    pub(super) fn note_spawned(&self) {
        *self.spawns.0.lock().unwrap_or_else(|e| e.into_inner()) += 1;
        self.spawns.1.notify_all();
    }

    pub(super) fn reserve_key(self: &Arc<Self>, pid: i32, birth: u64) -> RootReservation {
        self.reserved
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert((pid, birth));
        RootReservation {
            reaper: self.clone(),
            key: (pid, birth),
        }
    }

    /// No lock of the reaper is held by anyone.
    #[cfg(test)]
    pub(super) fn is_free(&self) -> bool {
        self.gate.try_lock().is_ok() && self.reserved.try_lock().is_ok()
    }

    /// Whether any root is reserved under `pid`.
    pub(super) fn holds(&self, pid: i32) -> bool {
        self.reserved
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .any(|(held, _)| *held == pid)
    }

    #[cfg(test)]
    pub(super) fn holds_any(&self) -> bool {
        !self.reserved.lock().unwrap().is_empty()
    }

    /// Decide what to do with the waitable child `pid` that `waitid(WNOWAIT)` named. The check and the exact
    /// wait are one critical section under `gate`, so a root forked after the check cannot be waited for: its
    /// create needs `gate` first.
    fn serve(&self, pid: i32) -> Served {
        let gate = self.hold_gate();
        let reserved = self
            .reserved
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .any(|(held, _)| *held == pid);
        if !reserved {
            let mut status: libc::c_int = 0;
            // SAFETY: the exact pid of a child that is not reserved, so no root's waiter can want it. `0` (not
            // exited any more, or a reused pid of a live child) and `ECHILD` (already reaped, or reused by a
            // non-child) are both fine to ignore.
            if unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) } == pid {
                orphan_reaped(pid);
            }
            drop(gate);
            return Served::Reaped;
        }
        drop(gate);
        // Reserved: its own waiter reaps it within microseconds, unless the key is stale.
        // SAFETY: an all-zero siginfo is a valid out buffer for waitid.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        // SAFETY: valid out pointer; WNOWAIT leaves the status for the root's waiter.
        let rc = unsafe {
            libc::waitid(
                libc::P_PID,
                pid as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOWAIT | libc::WNOHANG,
            )
        };
        if rc != 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ECHILD) {
            // Not our child any more: drop the keys whose start time no longer matches the live process.
            let live = process(pid).ok().flatten().map(|seen| seen.birth_ticks);
            self.reserved
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .retain(|(held, birth)| *held != pid || Some(*birth) == live);
            return Served::Stale;
        }
        Served::Deferred
    }

    /// `serve` with the bounded retries a deferred root gets. A leaked reservation therefore cannot become a
    /// permanent 100 Hz wake-up.
    fn settle(&self, pid: i32, sleep: &dyn Fn(Duration)) -> Settled {
        for attempt in 0..=MAX_DEFERRALS {
            match self.serve(pid) {
                Served::Reaped | Served::Stale => return Settled::Done,
                Served::Deferred if attempt == MAX_DEFERRALS => break,
                Served::Deferred => sleep(Duration::from_millis(10)),
            }
        }
        Settled::GaveUp
    }

    pub(super) fn start(self: &Arc<Self>) -> io::Result<()> {
        let reaper = self.clone();
        std::thread::Builder::new()
            .name("reaper".into())
            .spawn(move || reaper.run())
            .map(|_| ())
    }

    /// The event-driven loop: it blocks in the kernel until some child is waitable, without consuming it, and
    /// consumes only a pid that is not reserved. With no child at all it blocks on a condvar until a spawn.
    ///
    /// The loop never returns normally. If it ends, by an unexpected `waitid` error or by a panic, the guard
    /// marks the reaper stopped and creates are refused from then on (design: subreaper enabled <=> reaper
    /// running <=> reservation enforced). No restart: the error is not transient (`waitid(P_ALL, ...)` fails only
    /// for bad arguments or a kernel without it, and the same call would fail again), and a panic comes from this
    /// loop's own state, so a restarted thread would most likely hit it again. Refusing creates is the one
    /// response that cannot steal a root's status, and it keeps every live session's reservation rules.
    fn run(&self) {
        counters::reaper_running();
        let mut guard = StopGuard {
            reaper: self,
            cause: "panicked".into(),
        };
        let error = self.run_loop();
        guard.cause = format!("waitid failed: {error}");
    }

    fn run_loop(&self) -> io::Error {
        let mut seen = 0;
        let mut complained = HashSet::new();
        loop {
            #[cfg(test)]
            match self.inject.load(std::sync::atomic::Ordering::SeqCst) {
                INJECT_WAITID_ERROR => return io::Error::from_raw_os_error(libc::EIO),
                INJECT_PANIC => panic!("injected reaper panic"),
                INJECT_PARK => {
                    std::thread::sleep(Duration::from_millis(5));
                    continue;
                }
                _ => {}
            }
            // SAFETY: an all-zero siginfo is a valid out buffer for waitid.
            let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
            // SAFETY: valid out pointer; blocks until a child is waitable, and leaves its status in place.
            let rc = unsafe { libc::waitid(libc::P_ALL, 0, &mut info, libc::WEXITED | libc::WNOWAIT) };
            if rc == 0 {
                // SAFETY: waitid returned 0, so si_pid is set.
                let pid = unsafe { info.si_pid() };
                if pid > 0 && self.settle(pid, &|pause| std::thread::sleep(pause)) == Settled::GaveUp {
                    if complained.insert(pid) {
                        eprintln!("cli runtime: root {pid} was not reaped by its own waiter; waiting for it");
                    }
                    // The same zombie would be named again at once: look again slowly instead of spinning.
                    std::thread::sleep(Duration::from_secs(1));
                }
                continue;
            }
            match io::Error::last_os_error().raw_os_error() {
                Some(libc::EINTR) => continue,
                Some(libc::ECHILD) => {
                    // No child: sleep until a spawn newer than the last one this loop saw.
                    let mut spawns = self.spawns.0.lock().unwrap_or_else(|e| e.into_inner());
                    while *spawns == seen {
                        spawns = self.spawns.1.wait(spawns).unwrap_or_else(|e| e.into_inner());
                    }
                    seen = *spawns;
                }
                _ => return io::Error::last_os_error(),
            }
        }
    }

    /// Shutdown: wait, bounded, until no non-reserved child is waitable. The reaper keeps consuming; this only
    /// observes. A reserved zombie ends the wait (its own waiter is the only one allowed to take it).
    pub(super) fn drain(&self, deadline: Instant) {
        while Instant::now() < deadline {
            // SAFETY: an all-zero siginfo is a valid out buffer for waitid.
            let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
            // SAFETY: valid out pointer; WNOHANG and WNOWAIT make this a pure observation.
            let rc = unsafe {
                libc::waitid(
                    libc::P_ALL,
                    0,
                    &mut info,
                    libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
                )
            };
            // SAFETY: si_pid is read only after a successful call.
            let pid = if rc == 0 { unsafe { info.si_pid() } } else { 0 };
            let reserved = self
                .reserved
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .iter()
                .any(|(held, _)| *held == pid);
            if pid == 0 || reserved {
                return;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}

/// Every orphan the reaper consumed, and the pids already named as unattributable.
fn orphan_reaped(pid: i32) {
    counters::reaped();
    let mut orphans = orphans().lock().unwrap_or_else(|e| e.into_inner());
    orphans.named.retain(|(named, _)| *named != pid);
    orphans.admitted.retain(|(admitted, _), _| *admitted != pid);
}

#[derive(Default)]
struct Orphans {
    /// `(pid, start ticks)` of every process some session admitted, and when it was admitted.
    admitted: HashMap<(i32, u64), Instant>,
    /// Orphans already logged as unattributable, oldest first.
    named: std::collections::VecDeque<(i32, u64)>,
}

impl Orphans {
    /// Record what one published snapshot admitted, and drop every entry that the read behind that snapshot
    /// (`all`, begun at `scanned_at`) shows is gone or replaced, so the set follows the live processes of all
    /// sessions instead of growing for as long as no session finishes. An entry admitted at or after
    /// `scanned_at` is kept whatever `all` says: the read could not have seen it. No `/proc` read happens here.
    fn admit_batch(
        &mut self,
        batch: &[(i32, u64)],
        all: &HashMap<i32, Process>,
        scanned_at: Instant,
        admitted_at: Instant,
    ) {
        self.admitted.retain(|(pid, birth), since| {
            *since >= scanned_at || all.get(pid).is_some_and(|seen| seen.birth_ticks == *birth)
        });
        for &key in batch {
            self.admitted.insert(key, admitted_at);
        }
    }
}

fn orphans() -> &'static Mutex<Orphans> {
    static ORPHANS: OnceLock<Mutex<Orphans>> = OnceLock::new();
    ORPHANS.get_or_init(|| Mutex::new(Orphans::default()))
}

/// The entries kept in the named set before the oldest is dropped.
const NAMED_CAP: usize = 512;

/// A session's published snapshot admitted these exact processes.
fn note_admitted(batch: &[(i32, u64)], all: &HashMap<i32, Process>, scanned_at: Instant) {
    orphans()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .admit_batch(batch, all, scanned_at, Instant::now());
}

/// Name, once each, the adopted direct children of the sidecar that no session admitted: counted and logged,
/// never signalled, never added to a receipt (design section 2). Called when a session finishes and at shutdown.
/// A session root is the sidecar's usual child and is reserved, so roots are dropped before any stat read: with
/// no other child the call costs the task listing and the `children` reads, nothing per process.
pub(super) fn note_unattributed() {
    let Some(reaper) = reaper() else { return };
    let me = std::process::id() as i32;
    let Ok(Some(tids)) = RealProc.tasks(me) else {
        return;
    };
    let mut children = Vec::new();
    for tid in tids {
        if let Ok(Some(list)) = RealProc.children(me, tid) {
            children.extend(list.into_iter().filter(|child| !reaper.holds(*child)));
        }
    }
    if children.is_empty() {
        counters::flush();
        return;
    }
    let mut orphans = orphans().lock().unwrap_or_else(|e| e.into_inner());
    for child in children {
        let Ok(Some(seen)) = process(child) else {
            continue;
        };
        let key = (child, seen.birth_ticks);
        if matches!(seen.state, b'Z' | b'X')
            || orphans.admitted.contains_key(&key)
            || orphans.named.contains(&key)
        {
            continue;
        }
        orphans.named.push_back(key);
        if orphans.named.len() > NAMED_CAP {
            orphans.named.pop_front();
        }
        eprintln!(
            "cli runtime: process {child} was adopted by the sidecar and belongs to no session; it is left running"
        );
        counters::unattributed();
    }
    drop(orphans);
    counters::flush();
}

struct Pidfd(i32);

impl Drop for Pidfd {
    fn drop(&mut self) {
        // SAFETY: the descriptor was opened by `signal` and is owned by this guard.
        unsafe { libc::close(self.0) };
    }
}

/// Whether the process a pidfd names has exited (readable). An error reads as exited: fail closed.
/// Polls a pidfd, never a pty master.
fn pidfd_exited(fd: i32) -> bool {
    let mut pollfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    // SAFETY: one initialized pollfd and a zero timeout.
    unsafe { libc::poll(&mut pollfd, 1, 0) != 0 }
}

/// Signal the process instance `pid` named when `expected_start` was recorded, or nothing (E5 review-a 1).
/// The pid is bound with `pidfd_open` first; its identity (start string and, when this sidecar recorded
/// them, the exact kernel start ticks) is then read, and the pidfd must still be alive afterwards, which
/// proves the stat read belonged to the instance the pidfd names. The signal goes through that pidfd, so
/// a pid recycled after the check is never signalled. A process that cannot be bound is not signalled.
/// `group` signals the instance's process group (pidfd group signalling where the kernel has it, else the
/// group of the verified, still unreaped leader). Returns whether a signal was delivered.
pub(super) fn signal(
    pid: i32,
    expected_start: &str,
    signal: i32,
    group: bool,
    births: &Births,
) -> bool {
    signal_with(pid, expected_start, signal, group, births, true)
}

/// `pidfd_group` false stands in for a kernel without `PIDFD_SIGNAL_PROCESS_GROUP` (pre-6.9).
fn signal_with(
    pid: i32,
    expected_start: &str,
    signal: i32,
    group: bool,
    births: &Births,
    pidfd_group: bool,
) -> bool {
    // SAFETY: pidfd_open takes a pid and flags and returns a new descriptor or -1.
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) } as i32;
    if fd < 0 {
        return false;
    }
    let pidfd = Pidfd(fd);
    let Ok(Some(live)) = process(pid) else {
        return false;
    };
    if pidfd_exited(pidfd.0) {
        return false;
    }
    let (Ok(boot), Ok(ticks)) = (boot_seconds(), clock_ticks()) else {
        return false;
    };
    match legacy_start(live.birth_ticks, boot, ticks) {
        Ok(start) if start == expected_start => {}
        _ => return false,
    }
    if births
        .ticks(pid, expected_start)
        .is_some_and(|noted| noted != live.birth_ticks)
    {
        return false;
    }
    if !group {
        return send_pidfd(pidfd.0, signal, 0);
    }
    if pidfd_group {
        if send_pidfd(pidfd.0, signal, PIDFD_SIGNAL_PROCESS_GROUP) {
            return true;
        }
        if io::Error::last_os_error().raw_os_error() != Some(libc::EINVAL) {
            return false;
        }
    }
    // Pre-6.9 kernel: a numeric group kill is not bound to the instance verified above, so each live
    // group member is signalled through its own pidfd instead. A member that cannot be bound, or that this
    // session never admitted, stays unresolved.
    signal_group_members(live.group, signal, births)
}

fn send_pidfd(fd: i32, signal: i32, flags: libc::c_uint) -> bool {
    // SAFETY: a valid pidfd, a signal number, no siginfo.
    unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            fd,
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            flags,
        ) == 0
    }
}

/// Signal every live member of process group `group` through that member's own pidfd. The member's group
/// is read after the pidfd is bound and the pidfd must still be alive, so a recycled pid is never signalled.
/// A member is also signalled only when this session's ledger already holds its exact birth ticks: if the
/// verified leader's group ended and the numeric group id was reused, the new members were never admitted
/// and are left alone.
fn signal_group_members(group: i32, signal: i32, births: &Births) -> bool {
    let Ok(all) = scan(Instant::now() + Duration::from_millis(super::SNAPSHOT_MS)) else {
        return false;
    };
    let mut delivered = false;
    for (&member, seen) in &all {
        if seen.group != group || matches!(seen.state, b'Z' | b'X') {
            continue;
        }
        if !births.admitted(member, seen) {
            // Never signalled, but never forgotten: cleanup reports it while it is alive.
            births.refuse(member, seen);
            continue;
        }
        // SAFETY: pidfd_open takes a pid and flags and returns a new descriptor or -1.
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, member, 0) } as i32;
        if fd < 0 {
            continue;
        }
        let pidfd = Pidfd(fd);
        let bound = matches!(process(member), Ok(Some(live)) if live.group == group
            && live.birth_ticks == seen.birth_ticks);
        if bound && !pidfd_exited(pidfd.0) && send_pidfd(pidfd.0, signal, 0) {
            delivered = true;
        }
    }
    delivered
}

pub(super) fn wait_exit(pids: &[i32], deadline: Instant) -> Vec<i32> {
    let mut remaining = Vec::new();
    let mut watched = Vec::new();
    for &pid in pids {
        if matches!(
            process(pid),
            Ok(None)
                | Ok(Some(Process {
                    state: b'Z' | b'X',
                    ..
                }))
        ) {
            continue;
        }
        // SAFETY: pidfd_open binds this wait to the current process instance.
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) } as i32;
        if fd < 0 {
            if io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) {
                remaining.push(pid);
            }
            continue;
        }
        watched.push((pid, fd));
    }
    while !watched.is_empty() {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            break;
        }
        let timeout = left.as_millis().min(i32::MAX as u128) as i32;
        let mut fds: Vec<libc::pollfd> = watched
            .iter()
            .map(|(_, fd)| libc::pollfd {
                fd: *fd,
                events: libc::POLLIN,
                revents: 0,
            })
            .collect();
        // SAFETY: initialized pollfd array, valid count and bounded timeout.
        let count = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout) };
        if count < 0 {
            if io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            break;
        }
        if count == 0 {
            break;
        }
        for index in (0..fds.len()).rev() {
            if fds[index].revents & (libc::POLLIN | libc::POLLHUP) != 0 {
                let (_, fd) = watched.swap_remove(index);
                // SAFETY: pidfd opened and owned by this function.
                unsafe { libc::close(fd) };
            }
        }
    }
    for (pid, fd) in watched {
        remaining.push(pid);
        // SAFETY: pidfd opened and owned by this function.
        unsafe { libc::close(fd) };
    }
    remaining
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::process::CommandExt;
    use std::process::Command;

    #[test]
    fn proc_stat_comm_with_parentheses_does_not_shift_identity_fields() {
        let input = b"123 (a ) odd name) S 1 23 23 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 456 0";
        let value = parse_stat(123, input).unwrap();
        assert_eq!((value.parent, value.group, value.birth_ticks), (1, 23, 456));
        assert!(parse_stat(124, input).is_err());
    }

    #[test]
    fn live_descendant_is_discovered_and_exited_group_is_observed() {
        let mut child = Command::new("/bin/sh")
            .args(["-c", "sleep 30 & wait"])
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = child.id() as i32;
        let mut births = Births::default();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let deadline = Instant::now() + Duration::from_secs(2);
            let first = loop {
                let current = snapshot(Some(pid), &Table::new(), &mut births).unwrap();
                if current.len() > 1 || Instant::now() >= deadline {
                    break current;
                }
                std::thread::sleep(Duration::from_millis(10));
            };
            assert_eq!(first.get(&pid).unwrap().1, pid);
            assert!(first
                .iter()
                .any(|(&candidate, entry)| candidate != pid && entry.0 == pid));
            let ps = Command::new("/bin/ps")
                .args(["-p", &pid.to_string(), "-o", "lstart="])
                .env("LC_ALL", "C")
                .output()
                .unwrap();
            assert!(ps.status.success());
            assert_eq!(
                first.get(&pid).unwrap().2,
                String::from_utf8(ps.stdout).unwrap().trim()
            );
            // SAFETY: this test created and still owns the unreaped group leader.
            assert_eq!(unsafe { libc::kill(-pid, libc::SIGKILL) }, 0);
            let pids: Vec<_> = first.keys().copied().collect();
            assert!(wait_exit(&pids, Instant::now() + Duration::from_secs(2)).is_empty());
            assert!(snapshot(None, &first, &mut births).unwrap().is_empty());
        }));
        // SAFETY: the unreaped child prevents pid/group reuse until wait below.
        unsafe { libc::kill(-pid, libc::SIGKILL) };
        let _ = child.wait();
        result.unwrap();
    }

    /// A same-second replacement at a retained pid: the stored ticks differ from the live process while the
    /// `posix-lstart` string is identical, which is exactly what the string alone cannot see.
    fn sleeper() -> (std::process::Child, i32, String, u64, Births) {
        let child = Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = child.id() as i32;
        let mut births = Births::default();
        let table = snapshot(Some(pid), &Table::new(), &mut births).unwrap();
        let start = table.get(&pid).unwrap().2.clone();
        let ticks = process(pid).unwrap().unwrap().birth_ticks;
        (child, pid, start, ticks, births)
    }

    /// A session ledger that recorded `ticks` for `pid`, as a session whose process died and was replaced.
    fn ledger(pid: i32, start: &str, ticks: u64) -> Births {
        let mut births = Births::default();
        assert!(births.admit(pid, start, ticks));
        births
    }

    #[test]
    fn a_signal_reaches_only_the_instance_whose_ticks_were_recorded() {
        let (mut child, pid, start, ticks, _) = sleeper();
        let stale = ledger(pid, &start, ticks + 1); // the recorded instance is not the one at this pid
        assert!(!signal(pid, &start, libc::SIGKILL, false, &stale));
        assert!(
            child.try_wait().unwrap().is_none(),
            "the replacement was signalled"
        );
        let own = ledger(pid, &start, ticks);
        assert!(signal(pid, &start, libc::SIGKILL, false, &own));
        let status = child.wait().unwrap();
        assert_eq!(
            std::os::unix::process::ExitStatusExt::signal(&status),
            Some(libc::SIGKILL)
        );
    }

    /// Old session A recorded ticks for a retained descendant; a new session B then admits a root that now
    /// holds the same pid in the same `posix-lstart` second. B's snapshot must not touch what A recorded.
    #[test]
    fn a_new_session_cannot_overwrite_another_sessions_birth_evidence() {
        let (mut child, pid, start, ticks, mut session_b) = sleeper();
        let session_a = ledger(pid, &start, ticks + 1);
        let admitted = snapshot(Some(pid), &Table::new(), &mut session_b).unwrap();
        assert!(admitted.contains_key(&pid));
        assert!(
            !signal(pid, &start, libc::SIGKILL, false, &session_a),
            "A's cleanup signalled B's process"
        );
        assert!(child.try_wait().unwrap().is_none());
        let seeds: Table = [(pid, (1, pid, start.clone()))].into_iter().collect();
        let mut session_a = session_a;
        assert!(
            snapshot(None, &seeds, &mut session_a).unwrap().is_empty(),
            "A re-admitted B's process"
        );
        assert!(signal(pid, &start, libc::SIGKILL, false, &session_b));
        child.wait().unwrap();
    }

    #[test]
    fn a_wrong_start_string_or_an_exited_process_is_never_signalled() {
        let (mut child, pid, start, _, births) = sleeper();
        assert!(!signal(
            pid,
            "Thu Jan  1 00:00:00 1970",
            libc::SIGKILL,
            false,
            &births
        ));
        assert!(child.try_wait().unwrap().is_none());
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(
            !signal(pid, &start, libc::SIGKILL, false, &births),
            "a gone pid has nothing to signal"
        );
    }

    #[test]
    fn a_retained_seed_with_other_ticks_is_not_readmitted() {
        let (mut child, pid, start, ticks, _) = sleeper();
        let seeds: Table = [(pid, (1, pid, start.clone()))].into_iter().collect();
        let mut stale = ledger(pid, &start, ticks + 1);
        assert!(snapshot(None, &seeds, &mut stale).unwrap().is_empty());
        let mut own = ledger(pid, &start, ticks);
        assert!(snapshot(None, &seeds, &mut own).unwrap().contains_key(&pid));
        child.kill().unwrap();
        child.wait().unwrap();
    }

    /// A leader with two group members, as `pre-6.9` sees them: `PIDFD_SIGNAL_PROCESS_GROUP` unsupported.
    fn group_of_three() -> (std::process::Child, i32, String, Births, i32) {
        let child = Command::new("/bin/sh")
            .args(["-c", "sleep 30 & sleep 30 & wait"])
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = child.id() as i32;
        let mut births = Births::default();
        let deadline = Instant::now() + Duration::from_secs(5);
        let table = loop {
            let table = snapshot(Some(pid), &Table::new(), &mut births).unwrap();
            if table.len() >= 3 || Instant::now() >= deadline {
                break table;
            }
            std::thread::sleep(Duration::from_millis(10));
            births = Births::default();
        };
        assert!(table.len() >= 3, "the group never formed");
        let start = table.get(&pid).unwrap().2.clone();
        let other_group = Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let bystander = other_group.id() as i32;
        std::mem::forget(other_group); // reaped by init when the test process exits
        (child, pid, start, births, bystander)
    }

    #[test]
    fn the_pre_6_9_group_fallback_signals_each_member_by_pidfd_and_nothing_else() {
        let (mut leader, pid, start, births, bystander) = group_of_three();
        assert!(signal_with(
            pid,
            &start,
            libc::SIGKILL,
            true,
            &births,
            false
        ));
        let status = leader.wait().unwrap();
        assert_eq!(
            std::os::unix::process::ExitStatusExt::signal(&status),
            Some(libc::SIGKILL)
        );
        assert!(
            wait_exit(&[bystander], Instant::now() + Duration::from_millis(200)) == vec![bystander],
            "a process outside the verified group was signalled"
        );
        // SAFETY: the bystander is this test's own process.
        unsafe { libc::kill(bystander, libc::SIGKILL) };
    }

    /// A session whose shell keeps spawning and reaping children must not accumulate one ledger entry per
    /// pid it ever saw, while a live detached descendant it retained stays owned and signallable.
    #[test]
    fn a_long_lived_session_keeps_a_bounded_ledger_and_still_signals_a_retained_descendant() {
        use super::super::{known_owned, retain_owned, Owned};
        let mut root = Command::new("/bin/sh")
            .args([
                "-c",
                "setsid sleep 60 & i=0; while [ $i -lt 30 ]; do sleep 0.05; i=$((i+1)); done",
            ])
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = root.id() as i32;
        let mut births = Births::default();
        let first = snapshot(Some(pid), &Table::new(), &mut births).unwrap();
        let start = first.get(&pid).unwrap().2.clone();
        let mut owned = Owned {
            pid,
            root: serde_json::json!({ "start_value": start }),
            descendants: Table::new(),
            births,
        };
        retain_owned(&mut owned, &first);
        let mut seen: HashSet<i32> = first.keys().copied().collect();
        let mut peak = 0;
        let mut reaped = false;
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut after_exit = 0;
        while Instant::now() < deadline && after_exit < 5 {
            if !reaped && root.try_wait().unwrap().is_some() {
                reaped = true;
            }
            if reaped {
                after_exit += 1;
            }
            let table = snapshot(None, &known_owned(&owned), &mut owned.births).unwrap();
            seen.extend(table.keys().copied());
            retain_owned(&mut owned, &table);
            peak = peak.max(owned.births.0.len() + owned.descendants.len());
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(
            seen.len() >= 10,
            "the churn never happened: {} pids seen",
            seen.len()
        );
        assert!(
            peak <= 8,
            "the ledger grew with the churn: peak {peak} for {} pids",
            seen.len()
        );
        assert!(
            owned.births.0.len() <= 3 && owned.descendants.len() <= 2,
            "dead identities were kept: {} births, {} descendants",
            owned.births.0.len(),
            owned.descendants.len()
        );
        let (&kept, entry) = owned
            .descendants
            .iter()
            .next()
            .expect("the live detached descendant was dropped");
        assert!(signal(kept, &entry.2, libc::SIGKILL, false, &owned.births));
        assert!(wait_exit(&[kept], Instant::now() + Duration::from_secs(2)).is_empty());
        // SAFETY: the group is this test's own shell group.
        unsafe { libc::kill(-pid, libc::SIGKILL) };
        let _ = root.wait();
    }

    /// The fallback scan sees every live member of the numeric group, including ones this session never
    /// admitted (what a reused PGID looks like: members that exist but that no snapshot of this session
    /// recorded). Only the admitted leader may be signalled; the unadmitted members stay untouched.
    #[test]
    fn the_pre_6_9_group_fallback_never_signals_a_member_this_session_did_not_admit() {
        let (mut leader, pid, start, _, bystander) = group_of_three();
        let admitted_only_leader = ledger(pid, &start, process(pid).unwrap().unwrap().birth_ticks);
        let others: Vec<i32> = scan(Instant::now() + Duration::from_secs(2))
            .unwrap()
            .into_iter()
            .filter(|(member, seen)| *member != pid && seen.group == pid)
            .map(|(member, _)| member)
            .collect();
        assert!(others.len() >= 2, "the group never formed");
        assert!(signal_with(
            pid,
            &start,
            libc::SIGKILL,
            true,
            &admitted_only_leader,
            false
        ));
        leader.wait().unwrap();
        for member in &others {
            assert!(
                wait_exit(&[*member], Instant::now() + Duration::from_millis(300)) == vec![*member],
                "member {member} was signalled although this session never admitted it"
            );
        }
        // SAFETY: the unadmitted members and the bystander are this test's own sleepers.
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
            libc::kill(bystander, libc::SIGKILL);
        }
    }

    /// A same-group child born after the cleanup's first snapshot is never signalled by the fallback, and the
    /// survivors cleanup emits (`with_unresolved` is the last step of `cleanup`) must name it: once the root is
    /// gone nothing else would, and `cleanup_complete` would read true while it runs.
    #[test]
    fn an_unadmitted_group_member_the_fallback_refuses_is_named_in_the_cleanup_survivors() {
        use super::super::with_unresolved;
        let (mut leader, pid, start, _, bystander) = group_of_three();
        let admitted_only_leader = ledger(pid, &start, process(pid).unwrap().unwrap().birth_ticks);
        let late: Vec<i32> = scan(Instant::now() + Duration::from_secs(2))
            .unwrap()
            .into_iter()
            .filter(|(member, seen)| *member != pid && seen.group == pid)
            .map(|(member, _)| member)
            .collect();
        assert!(late.len() >= 2, "the group never formed");
        assert!(signal_with(
            pid,
            &start,
            libc::SIGKILL,
            true,
            &admitted_only_leader,
            false
        ));
        leader.wait().unwrap();
        let survivors = with_unresolved(Vec::new(), &admitted_only_leader);
        for member in &late {
            assert!(
                survivors.iter().any(|entry| entry["pid"] == *member),
                "unadmitted live member {member} is missing from the survivors: {survivors:?}"
            );
        }
        assert!(
            survivors.iter().all(|entry| entry["pid"] != pid),
            "the signalled, reaped leader was reported: {survivors:?}"
        );
        // SAFETY: the unadmitted members and the bystander are this test's own sleepers.
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
            libc::kill(bystander, libc::SIGKILL);
        }
        for member in &late {
            assert!(wait_exit(&[*member], Instant::now() + Duration::from_secs(2)).is_empty());
        }
        // Reap-independent: a member that is gone (or a zombie) is no longer listed.
        let gone = Instant::now() + Duration::from_secs(2);
        while !with_unresolved(Vec::new(), &admitted_only_leader).is_empty()
            && Instant::now() < gone
        {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(with_unresolved(Vec::new(), &admitted_only_leader).is_empty());
    }

    #[test]
    fn the_pre_6_9_group_fallback_never_signals_by_number_once_the_leader_is_gone() {
        let (mut leader, pid, start, births, bystander) = group_of_three();
        leader.kill().unwrap();
        leader.wait().unwrap(); // the leader is reaped before the signal: nothing left to bind
        assert!(!signal_with(
            pid,
            &start,
            libc::SIGKILL,
            true,
            &births,
            false
        ));
        // SAFETY: the bystander is this test's own process.
        unsafe { libc::kill(bystander, libc::SIGKILL) };
        // SAFETY: the orphaned members are this test's own sleepers.
        unsafe { libc::kill(-pid, libc::SIGKILL) };
    }

    fn reader(counter: &std::sync::atomic::AtomicUsize) -> io::Result<HashMap<i32, Process>> {
        counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(60));
        Ok(HashMap::new())
    }

    #[test]
    fn sessions_due_together_share_one_scan_sequence() {
        let board = Board(Mutex::new(Vec::new()));
        let reads = std::sync::atomic::AtomicUsize::new(0);
        let due = Instant::now();
        std::thread::scope(|scope| {
            for _ in 0..4 {
                scope.spawn(|| {
                    // Each session needs two independent scans for its equal-twice rule.
                    let first = board.scan_after(0, due, || reader(&reads)).unwrap();
                    let second = board
                        .scan_after(first.seq, due, || reader(&reads))
                        .unwrap();
                    assert!(second.seq > first.seq);
                });
            }
        });
        assert_eq!(reads.load(std::sync::atomic::Ordering::SeqCst), 2);
    }

    #[test]
    fn a_session_never_uses_a_scan_taken_before_its_request() {
        let board = Board(Mutex::new(Vec::new()));
        let reads = std::sync::atomic::AtomicUsize::new(0);
        let old = board
            .scan_after(0, Instant::now(), || reader(&reads))
            .unwrap();
        std::thread::sleep(Duration::from_millis(5));
        let due = Instant::now();
        let fresh = board.scan_after(0, due, || reader(&reads)).unwrap();
        assert!(fresh.seq > old.seq && fresh.started >= due);
        assert_eq!(reads.load(std::sync::atomic::Ordering::SeqCst), 2);
        // A scan that began after the request, and was not used yet, is reused.
        let again = board.scan_after(old.seq, due, || reader(&reads)).unwrap();
        assert_eq!(again.seq, fresh.seq);
        assert_eq!(reads.load(std::sync::atomic::Ordering::SeqCst), 2);
    }

    #[test]
    fn a_failed_scan_is_not_cached() {
        let board = Board(Mutex::new(Vec::new()));
        let due = Instant::now();
        assert!(board
            .scan_after(0, due, || Err(io::Error::other("boom")))
            .is_err());
        let reads = std::sync::atomic::AtomicUsize::new(0);
        board.scan_after(0, due, || reader(&reads)).unwrap();
        assert_eq!(reads.load(std::sync::atomic::Ordering::SeqCst), 1);
    }

    #[test]
    fn the_grid_never_moves_a_snapshot_later_and_aligns_sessions() {
        let epoch = Instant::now();
        let ms = |n: u64| epoch + Duration::from_millis(n);
        // Two sessions with different own due times land on the same grid point.
        assert_eq!(grid_due_from(epoch, ms(15_200)), ms(15_000));
        assert_eq!(grid_due_from(epoch, ms(15_900)), ms(15_000));
        assert_eq!(grid_due_from(epoch, ms(15_000)), ms(15_000));
        // The steady 30 s cadence stays on the same 5 s grid and is never later than its own due time.
        assert_eq!(grid_due_from(epoch, ms(47_300)), ms(45_000));
        // One cadence step after a snapshot is still strictly in the future.
        let now = ms(12_040);
        assert!(grid_due_from(epoch, now + Duration::from_millis(5000)) > now);
    }
}

/// The scoped walk, the startup order and the reaper, driven through exact interleavings (design section 8,
/// rows U-1 to U-15 and U-17).
#[cfg(test)]
mod scoped_tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::BTreeMap;
    use std::process::Command;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    #[derive(Clone, Debug, PartialEq)]
    enum Call {
        List,
        Stat(i32, Option<i32>),
        Tasks(i32),
        Children(i32, i32),
    }

    #[derive(Clone)]
    struct FTask {
        state: u8,
        start: u64,
        kids: Vec<i32>,
    }

    #[derive(Clone)]
    struct FProc {
        parent: i32,
        group: i32,
        session: i32,
        order: Vec<i32>,
        tasks: HashMap<i32, FTask>,
    }

    impl FProc {
        fn new(parent: i32, group: i32, session: i32, tids: &[i32]) -> Self {
            FProc {
                parent,
                group,
                session,
                order: tids.to_vec(),
                tasks: tids
                    .iter()
                    .map(|&tid| {
                        let task = FTask {
                            state: b'S',
                            start: 1000 + tid as u64,
                            kids: Vec::new(),
                        };
                        (tid, task)
                    })
                    .collect(),
            }
        }

        fn kids(mut self, tid: i32, kids: &[i32]) -> Self {
            self.tasks.get_mut(&tid).unwrap().kids = kids.to_vec();
            self
        }
    }

    type Procs = BTreeMap<i32, FProc>;
    type Hook = Box<dyn FnMut(&Call, &mut Procs)>;

    /// A scripted procfs. A hook sees every read before it is answered and may change the world first.
    struct Fake {
        procs: RefCell<Procs>,
        log: RefCell<Vec<Call>>,
        hooks: RefCell<Vec<Hook>>,
    }

    impl Fake {
        fn new(procs: Vec<(i32, FProc)>) -> Self {
            Fake {
                procs: RefCell::new(procs.into_iter().collect()),
                log: RefCell::new(Vec::new()),
                hooks: RefCell::new(Vec::new()),
            }
        }

        fn hook(&self, hook: impl FnMut(&Call, &mut Procs) + 'static) {
            self.hooks.borrow_mut().push(Box::new(hook));
        }

        /// A hook that runs `act` on the `nth` (1-based) occurrence of `call`.
        fn on_nth(&self, call: Call, nth: usize, mut act: impl FnMut(&mut Procs) + 'static) {
            let mut seen = 0;
            self.hook(move |current, procs| {
                if *current == call {
                    seen += 1;
                    if seen == nth {
                        act(procs);
                    }
                }
            });
        }

        fn serve(&self, call: Call) {
            self.log.borrow_mut().push(call.clone());
            let mut procs = self.procs.borrow_mut();
            for hook in self.hooks.borrow_mut().iter_mut() {
                hook(&call, &mut procs);
            }
        }

        fn calls(&self) -> Vec<Call> {
            self.log.borrow().clone()
        }

        fn stats(&self) -> usize {
            self.calls()
                .iter()
                .filter(|call| matches!(call, Call::Stat(..)))
                .count()
        }
    }

    impl ProcFs for Fake {
        fn list(&self) -> io::Result<Vec<i32>> {
            self.serve(Call::List);
            Ok(self.procs.borrow().keys().copied().collect())
        }

        fn stat(&self, pid: i32, tid: Option<i32>) -> io::Result<Option<Process>> {
            self.serve(Call::Stat(pid, tid));
            let procs = self.procs.borrow();
            let Some(process) = procs.get(&pid) else {
                return Ok(None);
            };
            let Some(task) = process.tasks.get(&tid.unwrap_or(pid)) else {
                return Ok(None);
            };
            Ok(Some(Process {
                parent: process.parent,
                group: process.group,
                session: process.session,
                birth_ticks: task.start,
                state: task.state,
            }))
        }

        fn tasks(&self, pid: i32) -> io::Result<Option<Vec<i32>>> {
            self.serve(Call::Tasks(pid));
            Ok(self.procs.borrow().get(&pid).map(|process| process.order.clone()))
        }

        fn children(&self, pid: i32, tid: i32) -> io::Result<Option<Vec<i32>>> {
            self.serve(Call::Children(pid, tid));
            Ok(self
                .procs
                .borrow()
                .get(&pid)
                .and_then(|process| process.tasks.get(&tid))
                .map(|task| task.kids.clone()))
        }
    }

    fn soon() -> Instant {
        Instant::now() + Duration::from_secs(5)
    }

    fn dirty_of(walk: Walk) -> Dirty {
        match walk {
            Walk::Incomplete(Incomplete::Dirty(dirty)) => dirty,
            Walk::Incomplete(other) => panic!("expected a dirty walk, got {other:?}"),
            Walk::Complete(table, _) => panic!("expected a dirty walk, got a table of {}", table.len()),
        }
    }

    fn table_of(walk: Walk) -> (HashMap<i32, Process>, WalkInfo) {
        match walk {
            Walk::Complete(table, info) => (table, info),
            Walk::Incomplete(why) => panic!("expected a clean walk, got {why:?}"),
        }
    }

    /// root 100 -> 101, 102.
    fn small_tree() -> Vec<(i32, FProc)> {
        vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (100, FProc::new(1, 100, 100, &[100]).kids(100, &[101, 102])),
            (101, FProc::new(100, 100, 100, &[101])),
            (102, FProc::new(100, 100, 100, &[102])),
        ]
    }

    #[test]
    fn omitted_known_child_forces_full_rescan() {
        // 102 is alive and its stat is readable, but root's children read does not list it.
        let mut tree = small_tree();
        tree[1].1 = FProc::new(1, 100, 100, &[100]).kids(100, &[101]);
        let fake = Fake::new(tree);
        let known: HashMap<_, _> = [(102, fake.stat(102, None).unwrap().unwrap())].into();
        let (table, source) = Scanner::new().table(&fake, 100, &HashMap::new(), soon()).unwrap();
        assert_eq!(source, Source::Scoped, "no witness: the walk alone cannot see the omission");
        assert!(!table.contains_key(&102));
        let (table, source) = Scanner::new().table(&fake, 100, &known, soon()).unwrap();
        assert_eq!(source, Source::Full(Fallback::Omitted));
        assert!(table.contains_key(&102), "the full scan did not supply the omitted child");
    }

    #[test]
    fn postwalk_vanished_printed_child_forces_full_rescan() {
        // Walk 1 prints A (101) and A is reaped; live B (102) is omitted. Walk 2 prints C (103) and C is
        // reaped; B is omitted again. Both walks are dirty by R3, so neither table is published.
        for (printed, reaped) in [(101, 101), (103, 103)] {
            let mut tree = small_tree();
            tree[1].1 = FProc::new(1, 100, 100, &[100]).kids(100, &[printed]);
            tree.push((103, FProc::new(100, 100, 100, &[103])));
            let fake = Fake::new(tree);
            fake.on_nth(Call::Stat(reaped, None), 2, move |procs| {
                procs.remove(&reaped);
            });
            let dirty = dirty_of(Scanner::new().walk(&fake, 100, soon()));
            assert!(dirty.r3, "{dirty:?}");
        }
        let mut tree = small_tree();
        tree[1].1 = FProc::new(1, 100, 100, &[100]).kids(100, &[101]);
        let fake = Fake::new(tree);
        fake.hook(|call, procs| {
            if *call == Call::Tasks(100) {
                procs.remove(&101);
            }
        });
        let known: HashMap<_, _> = [(102, fake.stat(102, None).unwrap().unwrap())].into();
        let (table, source) = Scanner::new().table(&fake, 100, &known, soon()).unwrap();
        assert!(matches!(source, Source::Full(_)));
        assert!(table.contains_key(&102), "scan_all did not supply B");
    }

    /// Process 200 under root 100: tids [201, 200] (leader second), live child 210 on the leader's list.
    fn thread_exit_world() -> Fake {
        let fake = Fake::new(vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (100, FProc::new(1, 100, 100, &[100]).kids(100, &[200])),
            (
                200,
                FProc::new(100, 100, 100, &[201, 200]).kids(200, &[210]),
            ),
            (210, FProc::new(200, 100, 100, &[210])),
        ]);
        // The leader exits between its stat read and its children read: its list moves to the other tid,
        // whose list was already read, and the read ends with nothing.
        fake.on_nth(Call::Children(200, 200), 1, |procs| {
            let process = procs.get_mut(&200).unwrap();
            let moved = std::mem::take(&mut process.tasks.get_mut(&200).unwrap().kids);
            process.tasks.get_mut(&200).unwrap().state = b'Z';
            process.tasks.get_mut(&201).unwrap().kids = moved;
        });
        fake
    }

    #[test]
    fn thread_exit_reparent_forces_full_rescan() {
        let fake = thread_exit_world();
        let dirty = dirty_of(Scanner::new().walk(&fake, 100, soon()));
        assert_eq!(
            dirty,
            Dirty { r2: true, ..Dirty::default() },
            "R2 must be the only rule that fires; the v2 identity-only predicate (R3) would have passed it"
        );
    }

    #[test]
    fn leader_exit_between_walks_is_dirty_then_clean() {
        let fake = thread_exit_world();
        let scanner = Scanner::new();
        assert!(dirty_of(scanner.walk(&fake, 100, soon())).r2);
        // The same scanner: the zombie leader is a baseline now, so walk 2 accepts it and prints 210.
        let (table, _) = table_of(scanner.walk(&fake, 100, soon()));
        assert!(table.contains_key(&210));
        // A fresh scanner has no baseline: dirty once by R2b, then clean.
        let fresh = Scanner::new();
        assert!(dirty_of(fresh.walk(&fake, 100, soon())).r2b);
        assert!(table_of(fresh.walk(&fake, 100, soon())).0.contains_key(&210));
    }

    /// Process 301 under root 300: tids [302, 301]; live child 303 on the leader's list.
    fn first_seen_zombie_world() -> Fake {
        let fake = Fake::new(vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (300, FProc::new(1, 300, 300, &[300]).kids(300, &[301])),
            (
                301,
                FProc::new(300, 300, 300, &[302, 301]).kids(301, &[303]),
            ),
            (303, FProc::new(301, 300, 300, &[303])),
        ]);
        // The leader is already a zombie at its first stat read, and its list went to the tid read before.
        fake.on_nth(Call::Stat(301, Some(301)), 1, |procs| {
            let process = procs.get_mut(&301).unwrap();
            let moved = std::mem::take(&mut process.tasks.get_mut(&301).unwrap().kids);
            process.tasks.get_mut(&301).unwrap().state = b'Z';
            process.tasks.get_mut(&302).unwrap().kids = moved;
        });
        fake
    }

    #[test]
    fn first_seen_zombie_intermediate_forces_retry() {
        let fake = first_seen_zombie_world();
        let scanner = Scanner::new();
        let dirty = dirty_of(scanner.walk(&fake, 300, soon()));
        assert_eq!(
            dirty,
            Dirty { r2b: true, ..Dirty::default() },
            "the v3 predicate (R1, R2, R3 only) would have called this walk clean"
        );
        let (table, _) = table_of(scanner.walk(&fake, 300, soon()));
        assert!(table.contains_key(&303), "the retry must print the live child");
    }

    #[test]
    fn zombie_baseline_is_keyed_and_not_shared() {
        let fake = first_seen_zombie_world();
        let scanner = Scanner::new();
        assert!(dirty_of(scanner.walk(&fake, 300, soon())).r2b);
        assert!(scanner.baseline.lock().unwrap().contains(&(301, 301, 1301)));
        table_of(scanner.walk(&fake, 300, soon()));
        // Same pid and tid, other starttime: a different instance, dirty again.
        {
            let mut procs = fake.procs.borrow_mut();
            procs.get_mut(&301).unwrap().tasks.get_mut(&301).unwrap().start = 9999;
        }
        assert!(dirty_of(scanner.walk(&fake, 300, soon())).r2b);
        // A new scanner has no baseline at all.
        assert!(Scanner::new().baseline.lock().unwrap().is_empty());
        assert!(dirty_of(Scanner::new().walk(&fake, 300, soon())).r2b);
    }

    #[test]
    fn de_thread_tid_set_change_is_dirty() {
        let fake = Fake::new(vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (100, FProc::new(1, 100, 100, &[100, 110, 111])),
        ]);
        // A thread exec swaps the tid set between the two listings; the leader keeps its tid.
        fake.on_nth(Call::Tasks(100), 2, |procs| {
            *procs.get_mut(&100).unwrap() = FProc::new(1, 100, 100, &[100, 112]);
        });
        assert!(dirty_of(Scanner::new().walk(&fake, 100, soon())).r1);
    }

    #[test]
    fn walk_reads_stat_before_children() {
        let fake = Fake::new(vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (100, FProc::new(1, 100, 100, &[100, 101]).kids(100, &[102])),
            (102, FProc::new(100, 100, 100, &[102])),
        ]);
        table_of(Scanner::new().walk(&fake, 100, soon()));
        let calls = fake.calls();
        assert!(!calls.is_empty(), "the walk read nothing");
        for pid in [100, 102] {
            let first = |wanted: &dyn Fn(&Call) -> bool| calls.iter().position(|call| wanted(call));
            let last = |wanted: &dyn Fn(&Call) -> bool| calls.iter().rposition(|call| wanted(call));
            let listing = first(&|call| *call == Call::Tasks(pid)).expect("no listing");
            let tid_stat = first(&|call| matches!(call, Call::Stat(p, Some(_)) if *p == pid)).unwrap();
            let last_children =
                last(&|call| matches!(call, Call::Children(p, _) if *p == pid)).unwrap();
            let last_listing = last(&|call| *call == Call::Tasks(pid)).unwrap();
            assert!(listing < tid_stat, "W2: listing before the first stat of {pid}");
            assert!(last_listing > last_children, "W2: listing repeated after the last children read of {pid}");
            for &tid in &fake.procs.borrow()[&pid].order {
                let stat = first(&|call| *call == Call::Stat(pid, Some(tid))).unwrap();
                let kids = first(&|call| *call == Call::Children(pid, tid)).unwrap();
                assert!(stat < kids, "W1: stat of tid {tid} before its children read");
            }
        }
    }

    #[test]
    fn transient_tid_between_listings_is_clean_and_complete() {
        let fake = Fake::new(small_tree());
        fake.on_nth(Call::Children(100, 100), 1, |procs| {
            let process = procs.get_mut(&100).unwrap();
            process.order.push(150);
            process.tasks.insert(150, FTask { state: b'S', start: 1150, kids: Vec::new() });
        });
        fake.on_nth(Call::Tasks(100), 2, |procs| {
            let process = procs.get_mut(&100).unwrap();
            process.order.retain(|tid| *tid != 150);
            process.tasks.remove(&150);
        });
        let (table, _) = table_of(Scanner::new().walk(&fake, 100, soon()));
        assert!(table.contains_key(&101) && table.contains_key(&102));
    }

    #[test]
    fn already_zombie_leader_clean_from_walk_2() {
        let kids: Vec<i32> = (1000..1250).collect();
        let mut tree = vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (100, FProc::new(1, 100, 100, &[100]).kids(100, &[200])),
            (200, FProc::new(100, 100, 100, &[200, 201]).kids(201, &kids)),
        ];
        for &kid in &kids {
            tree.push((kid, FProc::new(200, 100, 100, &[kid])));
        }
        let fake = Fake::new(tree);
        fake.procs.borrow_mut().get_mut(&200).unwrap().tasks.get_mut(&200).unwrap().state = b'Z';
        let scanner = Scanner::new();
        assert!(dirty_of(scanner.walk(&fake, 100, soon())).r2b);
        let (table, _) = table_of(scanner.walk(&fake, 100, soon()));
        assert!(kids.iter().all(|kid| table.contains_key(kid)));
    }

    struct Sys {
        calls: RefCell<Vec<&'static str>>,
        readable: bool,
        ignored: bool,
        subreaper: bool,
    }

    impl Startup for Sys {
        fn children_readable(&self) -> io::Result<()> {
            self.calls.borrow_mut().push("children");
            if self.readable {
                Ok(())
            } else {
                Err(io::Error::from_raw_os_error(libc::ENOENT))
            }
        }
        fn sigchld_ignored(&self) -> bool {
            self.calls.borrow_mut().push("sigchld");
            self.ignored
        }
        fn set_subreaper(&self) -> io::Result<()> {
            self.calls.borrow_mut().push("prctl");
            if self.subreaper {
                Ok(())
            } else {
                Err(io::Error::from_raw_os_error(libc::EPERM))
            }
        }
    }

    #[test]
    fn children_read_enoent_selects_full_scan() {
        let sys = |readable, ignored, subreaper| Sys {
            calls: RefCell::new(Vec::new()),
            readable,
            ignored,
            subreaper,
        };
        let missing = sys(false, false, true);
        assert_eq!(choose_mode(&missing), Mode::Full("no_children"));
        assert_eq!(*missing.calls.borrow(), ["children"], "nothing may run after the first failure");
        let ignoring = sys(true, true, true);
        assert_eq!(choose_mode(&ignoring), Mode::Full("sigchld_ignored"));
        assert_eq!(*ignoring.calls.borrow(), ["children", "sigchld"]);
        let denied = sys(true, false, false);
        assert_eq!(choose_mode(&denied), Mode::Full("prctl"));
        assert_eq!(*denied.calls.borrow(), ["children", "sigchld", "prctl"]);
        let good = sys(true, false, true);
        assert_eq!(choose_mode(&good), Mode::Scoped);
        assert_eq!(*good.calls.borrow(), ["children", "sigchld", "prctl"]);
    }

    /// Sidecar 1000 with root 1001, a descendant chain, a double-fork orphan adopted by the sidecar, a
    /// setsid orphan, and processes outside its tree.
    fn orphan_world() -> Fake {
        let mut tree = vec![
            (1, FProc::new(0, 1, 1, &[1])),
            (
                1000,
                FProc::new(1, 1000, 1000, &[1000]).kids(1000, &[1001, 1005, 1006]),
            ),
            (1001, FProc::new(1000, 1001, 1001, &[1001]).kids(1001, &[1002])),
            (1002, FProc::new(1001, 1001, 1001, &[1002]).kids(1002, &[1003, 1004])),
            (1003, FProc::new(1002, 1001, 1001, &[1003])),
            (1004, FProc::new(1002, 1001, 1001, &[1004])),
            (1005, FProc::new(1000, 1001, 1001, &[1005])),
            (1006, FProc::new(1000, 1006, 1006, &[1006])),
        ];
        for noise in 1100..1130 {
            tree.push((noise, FProc::new(1, noise, noise, &[noise])));
        }
        Fake::new(tree)
    }

    #[test]
    fn scoped_equals_full_scan_on_fixture() {
        let fake = orphan_world();
        let root = fake.stat(1001, None).unwrap().unwrap();
        let seeds: HashMap<_, _> = [(1001, root)].into();
        let (full, _) = scan_all_with(&fake, soon()).unwrap();
        let (scoped, source) = Scanner::new().table(&fake, 1000, &HashMap::new(), soon()).unwrap();
        assert_eq!(source, Source::Scoped);
        let from_full = owned_scan(&fake, &seeds, &full, soon()).unwrap();
        let from_scoped = owned_scan(&fake, &seeds, &scoped, soon()).unwrap();
        assert_eq!(from_full, from_scoped);
        let mut pids: Vec<_> = from_scoped.keys().copied().collect();
        pids.sort();
        assert_eq!(pids, [1001, 1002, 1003, 1004, 1005], "1006 is a setsid orphan: not owned");
    }

    #[test]
    fn scoped_scan_inspects_only_the_sidecar_tree() {
        let measure = |noise: i32| {
            let mut tree = vec![
                (1, FProc::new(0, 1, 1, &[1])),
                (
                    100,
                    FProc::new(1, 100, 100, &[100, 101, 102, 103, 104, 105, 106, 107, 108])
                        .kids(100, &[200, 201, 202, 203, 204]),
                ),
            ];
            for owned in 200..205 {
                tree.push((owned, FProc::new(100, owned, owned, &[owned])));
            }
            for index in 0..noise {
                tree.push((5000 + index, FProc::new(1, 5000 + index, 5000 + index, &[5000 + index])));
            }
            let fake = Fake::new(tree);
            let (_, info) = table_of(Scanner::new().walk(&fake, 100, soon()));
            let stats: Vec<i32> = fake
                .calls()
                .iter()
                .filter_map(|call| match call {
                    Call::Stat(pid, _) => Some(*pid),
                    _ => None,
                })
                .collect();
            (fake.stats(), info, stats)
        };
        let (few, info_few, _) = measure(5);
        let (many, info_many, stats_many) = measure(500);
        assert!(few > 0 && many > 0, "the walk read nothing");
        assert!(
            (many as f64 - few as f64).abs() <= few as f64 * 0.10,
            "stat reads depend on the noise: {few} vs {many}"
        );
        assert!(stats_many.iter().all(|pid| *pid < 5000), "a noise pid was inspected");
        // The walk's own record of the pids it read (what the harness counters report) matches the seam's log.
        let logged: std::collections::BTreeSet<i32> = stats_many.iter().copied().collect();
        assert_eq!(info_many.stat_pids, logged);
        let tree: HashSet<i32> = [100, 200, 201, 202, 203, 204].into();
        assert!(info_many.walked.iter().all(|pid| tree.contains(pid)));
        assert_eq!(info_few.stat_reads, few);
        assert!(many <= 3 * (info_many.walked.len() + info_many.sidecar_tids));
    }

    fn live(birth: u64) -> Process {
        Process {
            parent: 1,
            group: 1,
            session: 1,
            birth_ticks: birth,
            state: b'S',
        }
    }

    /// F7: with no session finishing, the admitted set follows the live processes instead of growing.
    #[test]
    fn admitted_set_stays_bounded_while_children_exit() {
        let base = Instant::now();
        let mut orphans = Orphans::default();
        let mut largest = 0;
        for round in 0..500u64 {
            // Each round's snapshot admits four new processes; every earlier round's processes have exited, so
            // the read behind this snapshot lists only these four.
            let batch: Vec<(i32, u64)> = (0..4)
                .map(|slot| (10_000 + (round as i32) * 4 + slot, 100 + round))
                .collect();
            let all: HashMap<i32, Process> = batch.iter().map(|&(pid, birth)| (pid, live(birth))).collect();
            let scanned_at = base + Duration::from_millis(round * 10);
            let admitted_at = scanned_at + Duration::from_millis(5);
            orphans.admit_batch(&batch, &all, scanned_at, admitted_at);
            largest = largest.max(orphans.admitted.len());
        }
        // Every earlier round's processes were judged gone by the next read: the set never holds more than the
        // live four, where unpruned it would hold 2000.
        assert_eq!(largest, 4, "the admitted set reached {largest}");
        assert_eq!(orphans.admitted.len(), 4, "only the live processes remain");
    }

    /// F7: a replaced pid is not kept alive by the old entry, and an entry admitted after a read began is kept
    /// when that read is judged.
    #[test]
    fn admitted_set_prunes_replaced_pids_but_keeps_entries_newer_than_the_read() {
        let base = Instant::now();
        let at = |millis: u64| base + Duration::from_millis(millis);
        let mut orphans = Orphans::default();
        orphans.admit_batch(&[(500, 1), (501, 2), (502, 3)], &HashMap::new(), at(0), at(0));
        // Another session admits 503 at 20 ms, while a read that began at 10 ms is still being used.
        orphans.admit_batch(&[(503, 4)], &HashMap::new(), at(0), at(20));
        // That older read: 500 live with the same birth, 501 replaced (new birth), 502 absent, 503 unseen.
        let all = HashMap::from([(500, live(1)), (501, live(99))]);
        orphans.admit_batch(&[], &all, at(10), at(25));
        let mut kept: Vec<_> = orphans.admitted.keys().copied().collect();
        kept.sort();
        assert_eq!(kept, [(500, 1), (503, 4)]);
        // A read that began after 503 was admitted and does not list it drops it.
        orphans.admit_batch(&[], &all, at(30), at(40));
        assert!(!orphans.admitted.contains_key(&(503, 4)));
    }

    fn zombie_pid(pid: i32) -> bool {
        // SAFETY: an all-zero siginfo is a valid out buffer for waitid.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        // SAFETY: valid out pointer; WNOWAIT leaves the status in place.
        let rc = unsafe {
            libc::waitid(libc::P_PID, pid as u32, &mut info, libc::WEXITED | libc::WNOWAIT | libc::WNOHANG)
        };
        // SAFETY: the union field is only read after waitid returned 0.
        rc == 0 && unsafe { info.si_pid() } == pid
    }

    fn exited_child() -> (std::process::Child, i32) {
        let child = Command::new("/bin/true").spawn().unwrap();
        let pid = child.id() as i32;
        let deadline = Instant::now() + Duration::from_secs(5);
        while !zombie_pid(pid) {
            assert!(Instant::now() < deadline, "the child never exited");
            std::thread::sleep(Duration::from_millis(5));
        }
        (child, pid)
    }

    #[test]
    fn reaper_defers_to_held_gate_and_reserved_roots() {
        let reaper = Reaper::new();
        let (_orphan, orphan) = exited_child();
        let guard = reaper.hold_gate();
        let done = Arc::new(AtomicBool::new(false));
        let served = {
            let (reaper, done) = (reaper.clone(), done.clone());
            std::thread::spawn(move || {
                let served = reaper.serve(orphan);
                done.store(true, Ordering::SeqCst);
                served
            })
        };
        std::thread::sleep(Duration::from_millis(150));
        assert!(!done.load(Ordering::SeqCst), "served an exited child while a create held the gate");
        assert!(zombie_pid(orphan), "the status was consumed while the gate was held");
        drop(guard);
        assert_eq!(served.join().unwrap(), Served::Reaped);
        assert!(!zombie_pid(orphan), "the orphan was not reaped after the gate opened");
        let (mut root, root_pid) = exited_child();
        let _reservation = reaper.reserve_key(root_pid, 7);
        assert_eq!(reaper.serve(root_pid), Served::Deferred);
        assert!(zombie_pid(root_pid), "a reserved root was waited for");
        root.wait().unwrap();
    }

    /// The churn-probe shape on the real kernel (850 children, constant replacement): a regression guard for
    /// R1-R3, not evidence for the omission argument. It would pass vacuously if the first three asserts
    /// were dropped.
    #[test]
    #[ignore]
    fn strong_check_regression_guard() {
        fn sleeper() -> i32 {
            // SAFETY: the child only calls async-signal-safe functions before it exits.
            match unsafe { libc::fork() } {
                0 => loop {
                    // SAFETY: pause and _exit take no pointers.
                    unsafe { libc::pause() };
                },
                pid => pid,
            }
        }
        let pool = Arc::new(Mutex::new((0..850).map(|_| sleeper()).collect::<Vec<_>>()));
        let stop = Arc::new(AtomicBool::new(false));
        let churn = {
            let (pool, stop) = (pool.clone(), stop.clone());
            std::thread::spawn(move || {
                let mut index = 0;
                while !stop.load(Ordering::SeqCst) {
                    let mut slots = pool.lock().unwrap();
                    let old = slots[index];
                    // SAFETY: `old` is this process's own unreaped child.
                    unsafe {
                        libc::kill(old, libc::SIGKILL);
                        libc::waitpid(old, std::ptr::null_mut(), 0);
                    }
                    slots[index] = sleeper();
                    index = (index + 1) % slots.len();
                }
            })
        };
        let me = std::process::id() as i32;
        let scanner = Scanner::new();
        let (mut reads, mut omissions, mut dirty, mut unflagged) = (0, 0, 0, 0);
        let deadline = Instant::now() + Duration::from_secs(120);
        while reads < 500 && Instant::now() < deadline {
            let before: HashSet<i32> = pool.lock().unwrap().iter().copied().collect();
            let walk = scanner.walk(&RealProc, me, Instant::now() + Duration::from_secs(30));
            let raw: HashSet<i32> = RealProc
                .tasks(me)
                .unwrap()
                .unwrap_or_default()
                .into_iter()
                .flat_map(|tid| RealProc.children(me, tid).unwrap().unwrap_or_default())
                .collect();
            let after: HashSet<i32> = pool.lock().unwrap().iter().copied().collect();
            let stable: Vec<i32> = before.intersection(&after).copied().collect();
            reads += 1;
            if stable.iter().any(|pid| !raw.contains(pid)) {
                omissions += 1;
            }
            match walk {
                Walk::Complete(table, _) => {
                    if stable.iter().any(|pid| !table.contains_key(pid)) {
                        unflagged += 1;
                    }
                }
                Walk::Incomplete(Incomplete::Dirty(_)) => dirty += 1,
                Walk::Incomplete(_) => {}
            }
        }
        stop.store(true, Ordering::SeqCst);
        churn.join().unwrap();
        for pid in pool.lock().unwrap().iter() {
            // SAFETY: the pool holds this process's own children.
            unsafe {
                libc::kill(*pid, libc::SIGKILL);
                libc::waitpid(*pid, std::ptr::null_mut(), 0);
            }
        }
        assert!(omissions > 0, "the churn never omitted a child: {reads} reads");
        assert!(dirty > 0, "no read was flagged: {reads} reads, {omissions} omissions");
        assert!(reads >= 500, "only {reads} reads");
        assert_eq!(unflagged, 0, "{unflagged} unflagged omissions in {reads} reads");
    }

    #[test]
    fn stale_reservation_does_not_spin_the_reaper() {
        let reaper = Reaper::new();
        let (mut gone, pid) = exited_child();
        gone.wait().unwrap();
        let stale = reaper.reserve_key(pid, 42);
        let sleeps = AtomicUsize::new(0);
        let count = |_: Duration| {
            sleeps.fetch_add(1, Ordering::SeqCst);
        };
        assert_eq!(reaper.settle(pid, &count), Settled::Done);
        assert!(sleeps.load(Ordering::SeqCst) <= 2, "a stale key made the reaper sleep and retry");
        assert!(reaper.reserved.lock().unwrap().is_empty(), "the stale key was kept");
        drop(stale);
        // A reserved root whose waiter never comes: bounded retries, then it gives up.
        let (mut stuck, stuck_pid) = exited_child();
        let _key = reaper.reserve_key(stuck_pid, 1);
        sleeps.store(0, Ordering::SeqCst);
        assert_eq!(reaper.settle(stuck_pid, &count), Settled::GaveUp);
        assert_eq!(sleeps.load(Ordering::SeqCst), MAX_DEFERRALS);
        stuck.wait().unwrap();
        // Two roots that held the same pid in turn keep distinct keys.
        let first = reaper.reserve_key(77, 1);
        let _second = reaper.reserve_key(77, 2);
        drop(first);
        let held = reaper.reserved.lock().unwrap();
        assert!(held.contains(&(77, 2)) && !held.contains(&(77, 1)));
    }
}
