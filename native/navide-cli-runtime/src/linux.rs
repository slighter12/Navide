//! Linux owned metadata from procfs, without a `ps` subprocess.
use super::Table;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::Mutex;
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
        let all = scan(Instant::now() + Duration::from_millis(super::SNAPSHOT_MS))?;
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

#[derive(Clone, PartialEq, Eq)]
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

fn process(pid: i32) -> io::Result<Option<Process>> {
    match std::fs::read(format!("/proc/{pid}/stat")) {
        Ok(bytes) => parse_stat(pid, &bytes).map(Some),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
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

fn scan(deadline: Instant) -> io::Result<HashMap<i32, Process>> {
    let mut all = HashMap::new();
    for entry in std::fs::read_dir("/proc")? {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "owned snapshot bound",
            ));
        }
        let entry = entry?;
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<i32>().ok())
        else {
            continue;
        };
        if pid <= 0 {
            continue;
        }
        // An unrelated process may vanish or deny inspection. Retained owned
        // processes are read separately and their errors remain fatal.
        if let Ok(Some(value)) = process(pid) {
            if all.insert(pid, value).is_none() && all.len() > MAX_PIDS {
                return Err(io::Error::other("owned node bound"));
            }
        }
    }
    Ok(all)
}

fn owned_scan(
    seeds: &HashMap<i32, Process>,
    deadline: Instant,
) -> io::Result<HashMap<i32, Process>> {
    let all = scan(deadline)?;
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
        let Some(value) = process(pid)? else { continue };
        if expected.birth_ticks != value.birth_ticks {
            continue; // A replacement never authorizes traversal or signals.
        }
        if matches!(value.state, b'Z' | b'X') {
            continue;
        }
        if value.group <= 0 {
            return Err(io::Error::other("unverified owned group"));
        }
        for (&child, candidate) in &all {
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
        if process(pid)?.as_ref() != Some(&value) {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "owned discovery raced identity",
            ));
        }
        owned.insert(pid, value);
    }
    Ok(owned)
}

pub(super) fn snapshot(
    initial_child: Option<i32>,
    seeds: &Table,
    births: &mut Births,
) -> io::Result<Table> {
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
    loop {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "unstable owned snapshot",
            ));
        }
        let current = match owned_scan(&known, deadline) {
            Ok(current) => current,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => continue,
            Err(error) => return Err(error),
        };
        if previous.as_ref() == Some(&current) {
            let mut table = Table::new();
            for (pid, value) in current {
                let start = legacy_start(value.birth_ticks, boot, ticks)?;
                // A same-second replacement of a pid this session already recorded is not owned.
                if births.admit(pid, &start, value.birth_ticks) {
                    table.insert(pid, (value.parent, value.group, start));
                }
            }
            return Ok(table);
        }
        known.extend(current.clone());
        previous = Some(current);
    }
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
}
