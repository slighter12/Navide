//! `navide-cli-runtime declarations` prints every CLI declaration as JSON
//! (the backend reads it); `navide-cli-runtime serve` answers JSON-line
//! requests on stdin (see `wire.rs`).
use navide_cli_runtime::{vendors, wire};

fn main() {
    let arg = std::env::args().nth(1).unwrap_or_default();
    let result = match arg.as_str() {
        "declarations" => serde_json::to_writer_pretty(std::io::stdout().lock(), vendors::ALL)
            .map_err(std::io::Error::other)
            .map(|()| println!()),
        "serve" => wire::serve(std::io::stdin().lock(), std::io::stdout().lock()),
        _ => {
            eprintln!("usage: navide-cli-runtime declarations | serve");
            std::process::exit(2);
        }
    };
    if let Err(err) = result {
        eprintln!("navide-cli-runtime: {err}");
        std::process::exit(1);
    }
}
