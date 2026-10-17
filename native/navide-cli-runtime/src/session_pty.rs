//! One session's PTY, as one value (design/e1-runtime-protocol-v7.md 6.1 and the R-U1 rule).
//!
//! The master descriptor never leaves this value as a free-standing number: it is created by
//! one constructor, lives inside the session's record, and is lent to the handoff only through
//! `lend_master`, a closure that cannot keep it. The `pty` meta of `SESSION_OWNED` is produced
//! by exactly one method, `owned_record`. No function in this module takes a raw descriptor
//! together with a session id, and no map from a descriptor to a session exists.
use serde_json::{json, Value};
use std::io;
use std::os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd, RawFd};
use std::sync::Mutex;

/// The child's end of a PTY (its stdio); never handed to Python.
pub struct SlaveEnd(pub OwnedFd);

/// Serialises everything that creates descriptors a fork could inherit (this module's `open`) with
/// the fork itself (`spawn_gated` holds it too). Two measured reasons (S6): macOS `openpty` is not
/// safe from several threads at once (under 32 concurrent creates about one run in two saw it fail
/// with errno -6), and a descriptor that exists between `openpty` and its `FD_CLOEXEC` can leak
/// into another session's child (a leaked slave would keep a session's stream from ever ending).
pub static FD_FORK_LOCK: Mutex<()> = Mutex::new(());

pub struct SessionPty {
    /// `None` once the sidecar closed its own copy (hangup or cleanup); Python's copy is unaffected.
    master: Mutex<Option<OwnedFd>>,
    /// The slave device name, obtained from the master itself (not from `openpty`'s name).
    tty: String,
}

/// The slave device name of a PTY master, asked of the master descriptor itself.
#[cfg(target_os = "macos")]
pub fn slave_name_of(master: RawFd) -> io::Result<String> {
    let mut name = [0 as libc::c_char; 128];
    // SAFETY: a 128-byte writable buffer is what TIOCPTYGNAME documents.
    if unsafe { libc::ioctl(master, libc::TIOCPTYGNAME as _, name.as_mut_ptr()) } < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: the kernel NUL-terminated the name inside the buffer.
    Ok(unsafe { std::ffi::CStr::from_ptr(name.as_ptr()) }
        .to_string_lossy()
        .into_owned())
}
#[cfg(target_os = "linux")]
pub fn slave_name_of(master: RawFd) -> io::Result<String> {
    let mut number: libc::c_uint = 0;
    // SAFETY: TIOCGPTN writes one unsigned int.
    if unsafe { libc::ioctl(master, libc::TIOCGPTN as _, &mut number) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(format!("/dev/pts/{number}"))
}
#[cfg(not(any(target_os = "macos", target_os = "linux")))]
pub fn slave_name_of(_master: RawFd) -> io::Result<String> {
    Err(io::Error::other("UNSUPPORTED_PLATFORM"))
}

impl SessionPty {
    /// Open a PTY: the session value and the slave end for the child's stdio.
    pub fn open(rows: u16, cols: u16) -> io::Result<(Self, SlaveEnd)> {
        let (mut master, mut slave) = (-1, -1);
        let opener = FD_FORK_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: output descriptors are initialised by openpty; no name/termios pointer.
        if unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } < 0
        {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: openpty succeeded and transfers each distinct descriptor exactly once.
        let master = unsafe { OwnedFd::from_raw_fd(master) };
        let slave = unsafe { OwnedFd::from_raw_fd(slave) };
        // Neither end may leak into a child by accident: the child gets its slave as stdio
        // (dup2 clears the flag on 0/1/2); the original numbers close at exec.
        // SAFETY: retained descriptors.
        if unsafe { libc::fcntl(slave.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) } < 0
            || unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) } < 0
        {
            return Err(io::Error::last_os_error());
        }
        drop(opener);
        resize_fd(master.as_raw_fd(), rows, cols)?;
        // O_NONBLOCK is an open-file-description flag, so the descriptor Python receives is
        // non-blocking too.
        // SAFETY: retained descriptor.
        if unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) } < 0 {
            return Err(io::Error::last_os_error());
        }
        let tty = slave_name_of(master.as_raw_fd())?;
        Ok((
            Self {
                master: Mutex::new(Some(master)),
                tty,
            },
            SlaveEnd(slave),
        ))
    }

    pub fn tty(&self) -> &str {
        &self.tty
    }

    /// Harness build only: a handoff payload whose descriptor and tty name are chosen freely, so
    /// the receiver's binding checks can be shown to fail closed. Never part of the product binary.
    #[cfg(feature = "fault-hooks")]
    pub fn for_fault(descriptor: OwnedFd, tty: String) -> Self {
        Self {
            master: Mutex::new(Some(descriptor)),
            tty,
        }
    }

    /// The `pty` meta of `SESSION_OWNED` (5.2): the only producer.
    pub fn owned_record(&self) -> Value {
        json!({ "tty": self.tty })
    }

    /// The single-purpose use after the handoff: `TIOCSWINSZ` (never read, write, poll or query).
    pub fn resize(&self, rows: u16, cols: u16) -> io::Result<()> {
        self.lend_master(|fd| resize_fd(fd.as_raw_fd(), rows, cols))?
    }

    /// Close the sidecar's own copy (never Python's): the PTY hangs up when the last master
    /// closes, so this is the hangup of a kill (5.5 R-e) and the release at cleanup.
    pub fn close_master(&self) {
        self.master.lock().unwrap().take();
    }

    /// Lend the master to the handoff send for the duration of `send`. The closure receives a
    /// `BorrowedFd`: a borrow that cannot outlive the call (the closure's return type cannot name its
    /// lifetime), while the master lock is held so a concurrent close cannot free the number mid-send.
    /// The closure *can* read the number out of the borrow; keeping that from ever being paired with a
    /// session id is what the R-U1 static guard below checks.
    pub fn lend_master<R>(&self, send: impl FnOnce(BorrowedFd<'_>) -> R) -> io::Result<R> {
        let guard = self.master.lock().unwrap();
        let master = guard
            .as_ref()
            .ok_or_else(|| io::Error::from_raw_os_error(libc::EBADF))?;
        Ok(send(master.as_fd()))
    }
}

fn resize_fd(master: RawFd, rows: u16, cols: u16) -> io::Result<()> {
    let size = libc::winsize {
        ws_row: rows,
        ws_col: cols,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    // SAFETY: a retained PTY master and a correctly sized winsize pointer.
    if unsafe { libc::ioctl(master, libc::TIOCSWINSZ, &size) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    /// R-U1 (a): across interleaved creates and closes (so descriptor numbers are reused) every
    /// session's record names exactly its own tty, and the descriptor lent for the handoff is the
    /// master of exactly that tty, never another live session's.
    #[test]
    fn owned_record_and_lent_master_always_belong_to_the_same_session() {
        let mut live: Vec<(SessionPty, SlaveEnd)> = Vec::new();
        let mut seen_numbers = HashSet::new();
        let mut reused = false;
        for round in 0..24 {
            let pair = SessionPty::open(24, 80).unwrap();
            let number = pair.0.lend_master(|fd| fd.as_raw_fd()).unwrap();
            reused |= !seen_numbers.insert(number);
            live.push(pair);
            if round % 3 == 2 {
                live.remove(round % live.len()); // close one, freeing its numbers for reuse
            }
            let mut ttys = HashSet::new();
            for (pty, _slave) in &live {
                assert_eq!(pty.owned_record(), json!({"tty": pty.tty()}));
                let lent = pty
                    .lend_master(|fd| slave_name_of(fd.as_raw_fd()).unwrap())
                    .unwrap();
                assert_eq!(lent, pty.tty(), "the lent master is not this session's");
                assert!(
                    ttys.insert(pty.tty().to_owned()),
                    "two sessions share a tty"
                );
            }
        }
        assert!(live.len() >= 8);
        assert!(reused, "the test never reused a descriptor number");
    }

    #[test]
    fn the_master_is_nonblocking_and_close_on_exec_and_resizable() {
        let (pty, _slave) = SessionPty::open(30, 100).unwrap();
        let (flags, fdflags) = pty
            .lend_master(|fd| unsafe {
                (
                    libc::fcntl(fd.as_raw_fd(), libc::F_GETFL),
                    libc::fcntl(fd.as_raw_fd(), libc::F_GETFD),
                )
            })
            .unwrap();
        assert!(flags & libc::O_NONBLOCK != 0 && fdflags & libc::FD_CLOEXEC != 0);
        pty.resize(40, 120).unwrap();
        let mut size: libc::winsize = unsafe { std::mem::zeroed() };
        pty.lend_master(|fd| unsafe { libc::ioctl(fd.as_raw_fd(), libc::TIOCGWINSZ, &mut size) })
            .unwrap();
        assert_eq!((size.ws_row, size.ws_col), (40, 120));
    }

    /// The lending interface hands the closure a `BorrowedFd`, whose lifetime cannot outlive the call
    /// (the closure's return type cannot name it). Extracting the number remains possible, which is
    /// why R-U1 (b) below guards the signatures that could pair a number with a session id.
    #[test]
    fn lend_master_lends_a_borrowed_descriptor_not_a_raw_number() {
        let production = include_str!("session_pty.rs")
            .split("#[cfg(test)]")
            .next()
            .unwrap();
        let signature = production
            .lines()
            .find(|line| line.contains("pub fn lend_master"))
            .expect("lend_master exists");
        assert!(
            signature.contains("BorrowedFd") && !signature.contains("RawFd"),
            "{signature}"
        );
    }

    /// R-U1 (b): static guard over the PTY-handling modules. No function signature may carry a
    /// raw descriptor together with a session id, and no map keyed by or holding a raw
    /// descriptor may exist.
    #[test]
    fn no_function_takes_a_raw_descriptor_together_with_a_session_id() {
        let sources = [
            ("session_pty.rs", include_str!("session_pty.rs")),
            ("wire.rs", include_str!("wire.rs")),
            ("posix.rs", include_str!("posix.rs")),
        ];
        for (name, source) in sources {
            let production = source.split("#[cfg(test)]").next().unwrap();
            let mut signature = String::new();
            let mut in_signature = false;
            for line in production.lines() {
                let trimmed = line.trim_start();
                if !in_signature
                    && (trimmed.starts_with("fn ")
                        || trimmed.starts_with("pub fn ")
                        || trimmed.starts_with("pub(super) fn ")
                        || trimmed.starts_with("pub(crate) fn "))
                {
                    in_signature = true;
                    signature.clear();
                }
                if in_signature {
                    signature.push_str(trimmed);
                    signature.push(' ');
                    if trimmed.contains('{') || trimmed.ends_with(';') {
                        in_signature = false;
                        let head = signature.split(" -> ").next().unwrap_or(&signature);
                        let raw = head.contains("RawFd")
                            || head.contains("OwnedFd")
                            || head.contains("fd: i32")
                            || head.contains("fd: &");
                        let session = head.contains("session_id")
                            || head.contains("sid:")
                            || head.contains("id: &str")
                            || head.contains("session:");
                        assert!(
                            !(raw && session),
                            "{name}: raw descriptor with a session id: {head}"
                        );
                    }
                }
                assert!(
                    !(trimmed.contains("HashMap<")
                        && (trimmed.contains("RawFd")
                            || trimmed.contains("OwnedFd")
                            || trimmed.contains("<i32, OwnedFd")
                            || trimmed.contains("HashMap<i32, Arc<SessionPty"))),
                    "{name}: a map holds or is keyed by a raw descriptor: {trimmed}"
                );
            }
        }
    }
}
