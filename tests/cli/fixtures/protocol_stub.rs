// Test-only stand-in for navide-cli-runtime that presents an incompatible protocol.
// Compiled by tests/cli/packaged_runtime_smoke.py with plain `rustc` and staged in place of the runtime; it is not
// part of the product crate. It prints one `ready` line with a wire version the backend does not speak, then waits
// to be ended.
use std::io::Write;

fn main() {
    let mut out = std::io::stdout();
    writeln!(
        out,
        "{{\"event\":\"ready\",\"protocol\":\"navide-acr\",\"wire\":999,\"runtime\":\"navide-cli-runtime\",\"version\":\"stub\"}}"
    )
    .unwrap();
    out.flush().unwrap();
    std::thread::sleep(std::time::Duration::from_secs(30));
}
