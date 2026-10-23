//! Darwin owned metadata: real libproc, not the setuid ps executable.
use super::Table;
use std::collections::HashSet;
use std::io;
use std::mem::{size_of, zeroed};
use std::time::{Duration, Instant};

const MAX_BYTES: usize = 4 * 1024 * 1024;
// SDK sys/proc_info.h; libc exposes functions but not these selectors.
const PGRP_ONLY: u32 = 2;

fn legacy_start(seconds: u64, micros: u64) -> io::Result<String> {
    if seconds == 0 || micros >= 1_000_000 {
        return Err(io::Error::other("unverified process birth"));
    }
    let seconds: libc::time_t = seconds
        .try_into()
        .map_err(|_| io::Error::other("process birth overflow"))?;
    // SAFETY: valid complete tm and time_t pointers for the pinned Darwin ABI.
    let mut local: libc::tm = unsafe { zeroed() };
    if unsafe { libc::localtime_r(&seconds, &mut local) }.is_null() {
        return Err(io::Error::last_os_error());
    }
    let days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let day = days
        .get(local.tm_wday as usize)
        .ok_or_else(|| io::Error::other("invalid weekday"))?;
    let month = months
        .get(local.tm_mon as usize)
        .ok_or_else(|| io::Error::other("invalid month"))?;
    Ok(format!(
        "{day} {month} {} {:02}:{:02}:{:02} {}",
        local.tm_mday,
        local.tm_hour,
        local.tm_min,
        local.tm_sec,
        local.tm_year + 1900
    ))
}

fn decoded(
    pid: i32,
    received: i32,
    error: i32,
    info: &libc::proc_bsdinfo,
) -> io::Result<Option<(i32, i32, String)>> {
    if received == 0 {
        if error == libc::ESRCH {
            return Ok(None); // Inspected exit, not unavailable ownership.
        }
        return Err(if error == 0 {
            io::Error::other("unverified process identity")
        } else {
            io::Error::from_raw_os_error(error)
        });
    }
    if received != size_of::<libc::proc_bsdinfo>() as i32 || info.pbi_pid != pid as u32 {
        return Err(io::Error::other("short or mismatched process identity"));
    }
    let parent = info
        .pbi_ppid
        .try_into()
        .map_err(|_| io::Error::other("parent overflow"))?;
    let group = info
        .pbi_pgid
        .try_into()
        .map_err(|_| io::Error::other("group overflow"))?;
    Ok(Some((
        parent,
        group,
        legacy_start(info.pbi_start_tvsec, info.pbi_start_tvusec)?,
    )))
}

fn row(pid: i32) -> io::Result<Option<(i32, i32, String)>> {
    // SAFETY: complete initialized pinned libc struct and exact buffer size.
    // Name/command fields are never accessed. __error is this thread's errno.
    let mut info: libc::proc_bsdinfo = unsafe { zeroed() };
    let received = unsafe {
        *libc::__error() = 0;
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut info as *mut libc::proc_bsdinfo).cast(),
            size_of::<libc::proc_bsdinfo>() as i32,
        )
    };
    decoded(pid, received, unsafe { *libc::__error() }, &info)
}

// Only SELF is admissible here. No foreign PID argument or env serialization.
fn os_argv(buffer: &[u8]) -> io::Result<Vec<String>> {
    if buffer.len() < size_of::<i32>() {
        return Err(io::Error::other("short SELF argc"));
    }
    let count = i32::from_ne_bytes(buffer[..4].try_into().unwrap());
    if count <= 0 || count as usize > buffer.len() {
        return Err(io::Error::other("invalid SELF argc"));
    }
    let mut offset = 4;
    let executable_end = buffer[offset..]
        .iter()
        .position(|byte| *byte == 0)
        .ok_or_else(|| io::Error::other("unterminated SELF executable"))?;
    offset += executable_end + 1;
    while buffer.get(offset) == Some(&0) {
        offset += 1;
    }
    let mut argv = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let remaining = buffer
            .get(offset..)
            .ok_or_else(|| io::Error::other("short SELF argv"))?;
        let end = remaining
            .iter()
            .position(|byte| *byte == 0)
            .ok_or_else(|| io::Error::other("unterminated SELF argv"))?;
        argv.push(
            std::str::from_utf8(&remaining[..end])
                .map_err(|_| io::Error::other("non-UTF8 SELF argv"))?
                .to_owned(),
        );
        offset += end + 1;
    }
    Ok(argv) // Stop at OS argc. The remaining buffer is NEVER inspected/emitted.
}

pub(super) fn self_process() -> io::Result<serde_json::Value> {
    // SAFETY: SELF cannot be reaped/reused while this helper executes. All
    // buffers are owned, initialized and sized for the pinned Darwin ABI.
    let pid = unsafe { libc::getpid() };
    let mut info: libc::proc_bsdinfo = unsafe { zeroed() };
    let received = unsafe {
        *libc::__error() = 0;
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut info as *mut libc::proc_bsdinfo).cast(),
            size_of::<libc::proc_bsdinfo>() as i32,
        )
    };
    if decoded(pid, received, unsafe { *libc::__error() }, &info)?.is_none() {
        return Err(io::Error::other("missing SELF identity"));
    }
    let mut path = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    let length = unsafe { libc::proc_pidpath(pid, path.as_mut_ptr().cast(), path.len() as u32) };
    if length <= 0 {
        return Err(io::Error::last_os_error());
    }
    let end = path
        .iter()
        .position(|byte| *byte == 0)
        .ok_or_else(|| io::Error::other("unterminated SELF image"))?;
    let executable =
        std::str::from_utf8(&path[..end]).map_err(|_| io::Error::other("non-UTF8 SELF image"))?;
    let mut mib = [libc::CTL_KERN, libc::KERN_PROCARGS2, pid];
    let mut buffer = vec![0u8; 256 * 1024];
    let mut size = buffer.len();
    let result = unsafe {
        libc::sysctl(
            mib.as_mut_ptr(),
            mib.len() as u32,
            buffer.as_mut_ptr().cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    if size > buffer.len() {
        return Err(io::Error::other("oversized SELF procargs"));
    }
    let argv = os_argv(&buffer[..size])?;
    Ok(serde_json::json!({"pid": pid, "parent": info.pbi_ppid,
        "birth_seconds": info.pbi_start_tvsec, "birth_microseconds": info.pbi_start_tvusec,
        "executable": executable, "argv": argv}))
}

fn list(pid_or_group: i32, children: bool, deadline: Instant) -> io::Result<Vec<i32>> {
    let mut count = 256;
    loop {
        if count * size_of::<i32>() > MAX_BYTES || Instant::now() >= deadline {
            return Err(io::Error::other("owned enumeration bound"));
        }
        let mut pids = vec![0_i32; count];
        // SAFETY: initialized contiguous buffer bounded to 4MiB/c_int. The
        // child wrapper returns PID COUNT; proc_listpids returns BYTES.
        let received = unsafe {
            *libc::__error() = 0;
            if children {
                libc::proc_listchildpids(
                    pid_or_group,
                    pids.as_mut_ptr().cast(),
                    (count * size_of::<i32>()) as i32,
                )
            } else {
                libc::proc_listpids(
                    PGRP_ONLY,
                    pid_or_group as u32,
                    pids.as_mut_ptr().cast(),
                    (count * size_of::<i32>()) as i32,
                )
            }
        };
        let error = unsafe { *libc::__error() };
        if received < 0 || (received == 0 && error != 0) {
            return Err(io::Error::from_raw_os_error(error));
        }
        if !children && !(received as usize).is_multiple_of(size_of::<i32>()) {
            return Err(io::Error::other("short owned enumeration"));
        }
        let used = if children {
            received as usize
        } else {
            received as usize / size_of::<i32>()
        };
        if used > count {
            return Err(io::Error::other("oversized owned enumeration"));
        }
        if used == count {
            count *= 2;
            continue; // Full buffer is not proof of complete discovery.
        }
        pids.truncate(used);
        if pids.iter().any(|pid| *pid <= 0) {
            return Err(io::Error::other("invalid owned enumeration PID"));
        }
        return Ok(pids);
    }
}

fn scan(seeds: &Table, deadline: Instant) -> io::Result<Table> {
    let mut table = Table::new();
    let mut pending: Vec<_> = seeds
        .iter()
        .map(|(&pid, entry)| (pid, entry.clone()))
        .collect();
    let mut seen = HashSet::new();
    let mut queued = seeds.clone();
    let mut groups = HashSet::new();
    while let Some((pid, expected)) = pending.pop() {
        if Instant::now() >= deadline || seen.len() >= MAX_BYTES / size_of::<i32>() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "owned snapshot bound",
            ));
        }
        if !seen.insert(pid) {
            continue;
        }
        let Some(entry) = row(pid)? else { continue };
        if entry.2 != expected.2 {
            continue; // Inspected replacement never authorizes traversal/signals.
        }
        if entry.1 <= 0 {
            return Err(io::Error::other("unverified owned group"));
        }
        let children = list(pid, true, deadline)?;
        let group = if groups.insert(entry.1) {
            list(entry.1, false, deadline)?
        } else {
            Vec::new()
        };
        for candidate in children.into_iter().chain(group) {
            if candidate == pid {
                continue;
            }
            let Some(value) = row(candidate)? else {
                continue;
            };
            if seeds.get(&candidate).is_some_and(|old| old.2 != value.2) {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "replaced discovery anchor",
                ));
            }
            if value.0 != pid
                && value.1 != entry.1
                && !seeds.get(&candidate).is_some_and(|old| old.2 == value.2)
            {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "unstable owned relationship",
                ));
            }
            if let Some(old) = queued.get(&candidate) {
                if old.2 != value.2 {
                    return Err(io::Error::new(
                        io::ErrorKind::WouldBlock,
                        "unstable queued birth",
                    ));
                }
            } else {
                if queued.len() >= MAX_BYTES / size_of::<i32>() {
                    return Err(io::Error::other("owned node bound"));
                }
                queued.insert(candidate, value.clone());
                pending.push((candidate, value));
            }
        }
        if row(pid)?.as_ref() != Some(&entry) {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "owned discovery raced identity",
            ));
        }
        table.insert(pid, entry);
    }
    Ok(table)
}

/// Whether `pid` may still be the owned process born at `start`: kill(pid, 0) still reaches a
/// process and proc_pidinfo either reports that birth or no longer answers (ESRCH while exiting).
/// A different birth is PID reuse, i.e. the owned process is gone.
pub(super) fn owned_not_gone(pid: i32, start: &str) -> bool {
    // SAFETY: signal 0 performs only the existence/permission check.
    if unsafe { libc::kill(pid, 0) } != 0
        && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
    {
        return false;
    }
    match row(pid) {
        Ok(Some(entry)) => entry.2 == start,
        Ok(None) | Err(_) => true,
    }
}

/// The live members of process group `root` (a former root's group stays listable after the root is gone). A
/// member whose identity cannot be read for a reason other than "gone" is an error: fail closed.
pub(super) fn former_group(root: i32) -> io::Result<Table> {
    let deadline = Instant::now() + Duration::from_millis(super::SNAPSHOT_MS);
    let mut members = Table::new();
    for pid in list(root, false, deadline)? {
        if let Some(entry) = row(pid)? {
            if !exited(pid) {
                members.insert(pid, entry);
            }
        }
    }
    Ok(members)
}

/// The recorded members that still run as the same instance. One whose state cannot be read stays listed.
pub(super) fn still_running(members: &std::collections::HashMap<i32, (i32, i32, String)>) -> Table {
    members
        .iter()
        .filter(|(&pid, entry)| match row(pid) {
            Ok(None) => false,
            Ok(Some(live)) => live.2 == entry.2 && !exited(pid),
            Err(_) => true,
        })
        .map(|(&pid, entry)| (pid, entry.clone()))
        .collect()
}

/// Macro `SZOMB` of `<sys/proc.h>`: the process has exited and only awaits its parent's reap.
const SZOMB: u32 = 5;

/// Has this process already stopped running (gone, or a zombie that only awaits a reap)?
fn exited(pid: i32) -> bool {
    // SAFETY: signal 0 performs only the existence/permission check.
    if unsafe { libc::kill(pid, 0) } != 0
        && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
    {
        return true;
    }
    // SAFETY: complete initialized pinned libc struct and exact buffer size.
    let mut info: libc::proc_bsdinfo = unsafe { zeroed() };
    let received = unsafe {
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut info as *mut libc::proc_bsdinfo).cast(),
            size_of::<libc::proc_bsdinfo>() as i32,
        )
    };
    received as usize == size_of::<libc::proc_bsdinfo>() && info.pbi_status == SZOMB
}

/// Event-driven wait (E1 13.2): block until every pid in `pids` has exited or `deadline`
/// passes, woken by kqueue `EVFILT_PROC`/`NOTE_EXIT` (no polling interval). Returns the pids that
/// had not exited at the deadline. Works for non-children of the same user (S0 spike).
pub(super) fn wait_exit(pids: &[i32], deadline: Instant) -> Vec<i32> {
    let mut remaining: Vec<i32> = pids.iter().copied().filter(|pid| !exited(*pid)).collect();
    if remaining.is_empty() {
        return remaining;
    }
    // SAFETY: plain kqueue(2); the descriptor is closed below on every path.
    let queue = unsafe { libc::kqueue() };
    if queue < 0 {
        return remaining;
    }
    let zero = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    let mut watched = Vec::new();
    for &pid in &remaining {
        // SAFETY: zeroed kevent is a valid value; fields are set before the call.
        let mut change: libc::kevent = unsafe { zeroed() };
        change.ident = pid as usize;
        change.filter = libc::EVFILT_PROC;
        change.flags = libc::EV_ADD | libc::EV_ONESHOT;
        change.fflags = libc::NOTE_EXIT;
        let mut event: libc::kevent = unsafe { zeroed() };
        // SAFETY: one change and one event slot, valid queue.
        let count = unsafe { libc::kevent(queue, &change, 1, &mut event, 1, &zero) };
        if count > 0 && event.flags & libc::EV_ERROR != 0 && event.data as i32 == libc::ESRCH {
            continue; // already gone: not remaining
        }
        if count > 0 && event.filter == libc::EVFILT_PROC && event.fflags & libc::NOTE_EXIT != 0 {
            continue; // exited between the pre-check and the registration
        }
        watched.push(pid);
    }
    remaining = watched.clone();
    while !remaining.is_empty() {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            break;
        }
        let timeout = libc::timespec {
            tv_sec: left.as_secs() as _,
            tv_nsec: left.subsec_nanos() as _,
        };
        // SAFETY: zeroed event buffer of a fixed size; valid queue.
        let mut events: [libc::kevent; 16] = unsafe { zeroed() };
        let count = unsafe {
            libc::kevent(
                queue,
                std::ptr::null(),
                0,
                events.as_mut_ptr(),
                events.len() as i32,
                &timeout,
            )
        };
        if count <= 0 {
            break;
        }
        for event in &events[..count as usize] {
            if event.filter == libc::EVFILT_PROC && event.fflags & libc::NOTE_EXIT != 0 {
                remaining.retain(|pid| *pid as usize != event.ident);
            }
        }
    }
    // SAFETY: the queue descriptor was created above and is not used again.
    unsafe { libc::close(queue) };
    remaining
}

pub(super) fn snapshot(initial_child: Option<i32>, seeds: &Table) -> io::Result<Table> {
    let deadline = Instant::now() + Duration::from_millis(super::SNAPSHOT_MS);
    let mut known = seeds.clone();
    if let Some(pid) = initial_child {
        if !known.is_empty() || pid <= 0 {
            return Err(io::Error::other("invalid initial child admission"));
        }
        // ONLY the actual retained Child caller can acquire an initial birth.
        if let Some(entry) = row(pid)? {
            known.insert(pid, entry);
        }
    }
    if known.values().any(|entry| entry.2.is_empty()) {
        return Err(io::Error::other("unknown retained birth"));
    }
    let mut previous = None;
    loop {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "unstable owned snapshot",
            ));
        }
        let table = match scan(&known, deadline) {
            Ok(table) => table,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => continue,
            Err(error) => return Err(error), // Never ignore owned permission errors.
        };
        if previous.as_ref() == Some(&table) {
            return Ok(table);
        }
        known.extend(table.clone()); // Retain new escaped anchors between passes.
        previous = Some(table);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::process::CommandExt;

    #[test]
    fn self_argc_excludes_environment_and_preserves_empty_arguments() {
        let mut data = 3i32.to_ne_bytes().to_vec();
        data.extend_from_slice(
            b"/OWN/runtime\0\0/OWN/runtime\0\0literal\0SYNTHETIC_ENV=NEVER_EMIT\0",
        );
        assert_eq!(os_argv(&data).unwrap(), ["/OWN/runtime", "", "literal"]);
        assert!(!serde_json::to_string(&os_argv(&data).unwrap())
            .unwrap()
            .contains("SYNTHETIC_ENV"));
        for malformed in [
            vec![],
            0i32.to_ne_bytes().to_vec(),
            (-1i32).to_ne_bytes().to_vec(),
            [2i32.to_ne_bytes().as_slice(), b"image\0arg\0"].concat(),
        ] {
            assert!(os_argv(&malformed).is_err());
        }
    }

    #[test]
    fn invalid_birth_never_authorizes_identity() {
        assert!(legacy_start(0, 0).is_err());
        assert!(legacy_start(1, 1_000_000).is_err());
        assert!(legacy_start(u64::MAX, 0).is_err());
    }

    #[test]
    fn invalid_or_denied_return_is_not_exit() {
        let info = unsafe { zeroed() };
        assert!(decoded(123, 0, libc::EPERM, &info).is_err());
        assert!(decoded(123, 0, 0, &info).is_err());
        assert!(decoded(123, 1, 0, &info).is_err());
        assert!(decoded(123, size_of::<libc::proc_bsdinfo>() as i32, 0, &info).is_err());
        assert!(decoded(123, 0, libc::ESRCH, &info).unwrap().is_none());
    }

    #[test]
    fn real_owned_membership_and_mismatch_never_signal() {
        let mut child = std::process::Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = child.id() as i32;
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let table = snapshot(Some(pid), &Table::new()).unwrap();
            assert!(table.contains_key(&pid));
            let mut wrong = table.clone();
            wrong.get_mut(&pid).unwrap().2.push_str(" INVALID");
            assert!(snapshot(None, &wrong).unwrap().is_empty());
            assert!(child.try_wait().unwrap().is_none());
            wrong.get_mut(&pid).unwrap().2.clear();
            assert!(snapshot(None, &wrong).is_err());
        }));
        child.kill().unwrap();
        child.wait().unwrap();
        if let Err(error) = result {
            std::panic::resume_unwind(error)
        }
    }

    #[test]
    fn real_owned_exit_is_not_an_access_failure() {
        let mut child = std::process::Command::new("/usr/bin/true").spawn().unwrap();
        let pid = child.id() as i32;
        child.wait().unwrap();
        assert!(row(pid).unwrap().is_none());
    }
}
