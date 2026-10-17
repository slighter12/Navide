//! The Navide CLI runtime sidecar. stdout carries the one `ready` (or `fatal`) line; everything after
//! the handshake is binary frames on the control socket (E1 wire v1).
mod frame;
#[cfg(unix)]
mod posix;
#[cfg(unix)]
mod session_pty;
#[cfg(unix)]
mod wire;

fn main() {
    #[cfg(unix)]
    if let Err(error) = posix::run() {
        eprintln!("cli runtime: {error}");
        std::process::exit(1);
    }
    #[cfg(not(unix))]
    {
        eprintln!("cli runtime: UNSUPPORTED_PLATFORM (native adapter not integrated)");
        std::process::exit(1);
    }
}
