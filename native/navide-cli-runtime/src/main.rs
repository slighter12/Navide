//! Backend-owned JSONL controller. stdout is exclusively protocol records.
#[cfg(unix)]
mod posix;

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
