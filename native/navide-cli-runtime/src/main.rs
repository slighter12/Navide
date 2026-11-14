//! `navide-cli-runtime declarations` prints every CLI declaration as JSON;
//! `declarations launch` prints the part the backend's registry and the
//! frontend's agent specs are built from (checked in as
//! `backend/agent_team_backend/cli_declarations.json`, with a test that it
//! is current). `navide-cli-runtime serve` answers JSON-line requests on
//! stdin (see `wire.rs`).
use navide_cli_runtime::{registry, wire};

fn print(value: &impl serde::Serialize) -> std::io::Result<()> {
    serde_json::to_writer_pretty(std::io::stdout().lock(), value).map_err(std::io::Error::other)?;
    println!();
    Ok(())
}

fn main() {
    let mut args = std::env::args().skip(1);
    let result = match (args.next().as_deref(), args.next().as_deref()) {
        (Some("declarations"), None) => print(&registry::export()),
        (Some("declarations"), Some("launch")) => std::io::Write::write_all(&mut std::io::stdout().lock(), registry::launch_json().as_bytes()),
        (Some("serve"), None) => wire::serve(std::io::stdin().lock(), std::io::stdout()),
        _ => {
            eprintln!("usage: navide-cli-runtime declarations [launch] | serve");
            std::process::exit(2);
        }
    };
    if let Err(err) = result {
        eprintln!("navide-cli-runtime: {err}");
        std::process::exit(1);
    }
}
