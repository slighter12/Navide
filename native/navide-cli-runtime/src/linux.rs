//! Linux owned metadata from procfs, without a `ps` subprocess.
use super::Table;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const MAX_PIDS: usize = 4 * 1024 * 1024 / std::mem::size_of::<i32>();
/// `PIDFD_SIGNAL_PROCESS_GROUP` (Linux 6.9+): signal the whole group of the process a pidfd names.
const PIDFD_SIGNAL_PROCESS_GROUP: libc::c_uint = 1 << 2;

type Births = std::collections::HashMap<i32, (String, u64)>;

/// The kernel start ticks behind every `posix-lstart` value this sidecar handed out, by pid. The wire
/// value has one-second resolution, so two processes that held the same pid within a second share it;
/// the ticks tell them apart. Internal only: the table and the wire keep the legacy string.
fn births() -> &'static Mutex<Births> {
    static BIRTHS: OnceLock<Mutex<Births>> = OnceLock::new();
    BIRTHS.get_or_init(Default::default)
}

fn note_birth(pid: i32, start: &str, ticks: u64) {
    if let Ok(mut births) = births().lock() {
        births.insert(pid, (start.to_string(), ticks));
    }
}

fn noted_ticks(pid: i32, start: &str) -> Option<u64> {
    let births = births().lock().ok()?;
    births
        .get(&pid)
        .filter(|(noted, _)| noted == start)
        .map(|(_, ticks)| *ticks)
}

#[derive(Clone, PartialEq, Eq)]
struct Process {
    parent: i32,
    group: i32,
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

pub(super) fn snapshot(initial_child: Option<i32>, seeds: &Table) -> io::Result<Table> {
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
                && noted_ticks(pid, &expected.2).is_none_or(|noted| noted == value.birth_ticks)
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
            return current
                .into_iter()
                .map(|(pid, value)| {
                    let start = legacy_start(value.birth_ticks, boot, ticks)?;
                    note_birth(pid, &start, value.birth_ticks);
                    Ok((pid, (value.parent, value.group, start)))
                })
                .collect();
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
pub(super) fn signal(pid: i32, expected_start: &str, signal: i32, group: bool) -> bool {
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
    if noted_ticks(pid, expected_start).is_some_and(|noted| noted != live.birth_ticks) {
        return false;
    }
    let flags = if group { PIDFD_SIGNAL_PROCESS_GROUP } else { 0 };
    // SAFETY: a valid pidfd, a signal number, no siginfo.
    let sent = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            pidfd.0,
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            flags,
        )
    };
    if sent == 0 {
        return true;
    }
    if group && io::Error::last_os_error().raw_os_error() == Some(libc::EINVAL) {
        // SAFETY: the leader was verified alive and unreaped just above, so the group id is still its own.
        return unsafe { libc::kill(-pid, signal) } == 0;
    }
    false
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
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let deadline = Instant::now() + Duration::from_secs(2);
            let first = loop {
                let current = snapshot(Some(pid), &Table::new()).unwrap();
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
            assert!(snapshot(None, &first).unwrap().is_empty());
        }));
        // SAFETY: the unreaped child prevents pid/group reuse until wait below.
        unsafe { libc::kill(-pid, libc::SIGKILL) };
        let _ = child.wait();
        result.unwrap();
    }

    /// A same-second replacement at a retained pid: the stored ticks differ from the live process while the
    /// `posix-lstart` string is identical, which is exactly what the string alone cannot see.
    fn sleeper() -> (std::process::Child, i32, String, u64) {
        let child = Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = child.id() as i32;
        let table = snapshot(Some(pid), &Table::new()).unwrap();
        let start = table.get(&pid).unwrap().2.clone();
        let ticks = process(pid).unwrap().unwrap().birth_ticks;
        (child, pid, start, ticks)
    }

    #[test]
    fn a_signal_reaches_only_the_instance_whose_ticks_were_recorded() {
        let (mut child, pid, start, ticks) = sleeper();
        note_birth(pid, &start, ticks + 1); // the recorded instance is not the one at this pid
        assert!(!signal(pid, &start, libc::SIGKILL, false));
        assert!(child.try_wait().unwrap().is_none(), "the replacement was signalled");
        note_birth(pid, &start, ticks);
        assert!(signal(pid, &start, libc::SIGKILL, false));
        let status = child.wait().unwrap();
        assert_eq!(std::os::unix::process::ExitStatusExt::signal(&status), Some(libc::SIGKILL));
    }

    #[test]
    fn a_wrong_start_string_or_an_exited_process_is_never_signalled() {
        let (mut child, pid, start, ticks) = sleeper();
        note_birth(pid, &start, ticks);
        assert!(!signal(pid, "Thu Jan  1 00:00:00 1970", libc::SIGKILL, false));
        assert!(child.try_wait().unwrap().is_none());
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(!signal(pid, &start, libc::SIGKILL, false), "a gone pid has nothing to signal");
    }

    #[test]
    fn a_retained_seed_with_other_ticks_is_not_readmitted() {
        let (mut child, pid, start, ticks) = sleeper();
        let seeds: Table = [(pid, (1, pid, start.clone()))].into_iter().collect();
        note_birth(pid, &start, ticks + 1);
        assert!(snapshot(None, &seeds).unwrap().is_empty());
        note_birth(pid, &start, ticks);
        assert!(snapshot(None, &seeds).unwrap().contains_key(&pid));
        child.kill().unwrap();
        child.wait().unwrap();
    }
}
