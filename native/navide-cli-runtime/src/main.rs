//! The Navide CLI runtime sidecar. stdout carries the one `ready` (or `fatal`) line; everything after
//! the handshake is binary frames on the control channel (E1 wire v2).
//!
//! One generic control plane (`control`, `session`) runs on one `Host` per platform (`posix`, `windows`).
mod adapters;
mod compose;
mod control;
#[cfg(target_os = "macos")]
mod darwin;
#[cfg(feature = "fault-hooks")]
mod fault;
mod frame;
mod host;
mod journal;
#[cfg(target_os = "linux")]
mod linux;
#[cfg(unix)]
mod observe;
#[cfg(unix)]
mod posix;
mod refusal;
mod session;
#[cfg(unix)]
mod session_pty;
#[cfg(windows)]
mod session_pty_win;
mod vendor_facts;
#[cfg(windows)]
mod windows;
mod wire;

fn main() {
    #[cfg(unix)]
    let outcome = control::run::<posix::PosixHost>();
    #[cfg(windows)]
    let outcome = control::run::<windows::WindowsHost>();
    #[cfg(not(any(unix, windows)))]
    let outcome: std::io::Result<()> = Err(std::io::Error::other(
        "UNSUPPORTED_PLATFORM (native adapter not integrated)",
    ));
    if let Err(error) = outcome {
        eprintln!("cli runtime: {error}");
        std::process::exit(1);
    }
}
