//! Harness build only (`--features fault-hooks`): a `SESSION_OWNED` that is deliberately mis-sent, selected by the
//! pane id `fault:<kind>` (E1 15.2 P3, P9). The product binary does not contain this module.
use super::PosixHost;
use crate::control::Runtime;
use crate::fault::kind;
use crate::session::SessionHandle;
use crate::session_pty::SessionPty;
use crate::wire::SendFault;
use serde_json::{json, Value};
use std::os::fd::{FromRawFd, OwnedFd};
use std::sync::Arc;

/// Returns true when the fault replaced the normal `SESSION_OWNED` send.
pub fn send_owned(
    handle: &Arc<SessionHandle<PosixHost>>,
    mut meta: Value,
    runtime: &Runtime<PosixHost>,
) -> bool {
    let Some(kind) = kind(&handle.pane) else {
        return false;
    };
    let wire = &runtime.wire;
    let pty = &handle.platform.pty;
    // A fresh, unrelated PTY: its master is a valid master, but not this session's.
    let decoy = || Arc::new(SessionPty::open(24, 80).expect("decoy pty").0);
    match kind {
        "suppress-owned-and-response" => {}
        "suppress-response" => return false,
        "corrupt-frame" => {
            // The normal handoff, then bytes that are not a frame (F3 principal, L8).
            wire.out
                .session_owned(meta, pty.clone())
                .expect("owned frame fits");
            wire.out.raw(vec![0xAB; 32]);
        }
        "short-send" => wire
            .out
            .session_owned_fault(meta, pty.clone(), SendFault::Short),
        "zero-send" => wire
            .out
            .session_owned_fault(meta, pty.clone(), SendFault::Zero),
        // 9a: a valid PTY master of ANOTHER pty while the frame names this session's tty.
        "wrong-master" => wire
            .out
            .session_owned_fault(meta, decoy(), SendFault::Short),
        // 9e: descriptor AND metadata swapped together (the documented undetected class).
        "consistent-swap" => {
            let pty = decoy();
            meta["pty"]["tty"] = json!(pty.tty());
            wire.out.session_owned_fault(meta, pty, SendFault::Short)
        }
        // 9d: the right master, a slave name that is not its own.
        "wrong-name" => {
            meta["pty"]["tty"] = json!("/dev/ttys999");
            wire.out
                .session_owned_fault(meta, pty.clone(), SendFault::Short)
        }
        // 9b: not a tty at all (a pipe read end).
        "non-tty" => {
            let mut ends = [0i32; 2];
            // SAFETY: two-element output array.
            assert_eq!(unsafe { libc::pipe(ends.as_mut_ptr()) }, 0);
            // SAFETY: both descriptors were just created and are owned here.
            let (read, write) =
                unsafe { (OwnedFd::from_raw_fd(ends[0]), OwnedFd::from_raw_fd(ends[1])) };
            drop(write);
            let fake = Arc::new(SessionPty::for_fault(read, pty.tty().to_owned()));
            wire.out.session_owned_fault(meta, fake, SendFault::Short)
        }
        // 9c: the tty of a session that is already claimed, with a duplicate of its master.
        "duplicate-tty" => {
            let other = runtime
                .registry()
                .sessions
                .values()
                .find(|other| other.id != handle.id)
                .cloned();
            let Some(other) = other else { return false };
            let duplicate = other
                .platform
                .pty
                .lend_master(|fd| unsafe { libc::dup(std::os::fd::AsRawFd::as_raw_fd(&fd)) })
                .expect("master open");
            // SAFETY: a fresh duplicate owned by this value.
            let fake = Arc::new(SessionPty::for_fault(
                unsafe { OwnedFd::from_raw_fd(duplicate) },
                other.platform.pty.tty().to_owned(),
            ));
            meta["pty"]["tty"] = json!(other.platform.pty.tty());
            wire.out.session_owned_fault(meta, fake, SendFault::Short)
        }
        _ => return false,
    }
    true
}
