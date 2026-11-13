//! `serve`: one JSON request per stdin line, one JSON response per stdout
//! line, in order. The backend keeps one long-lived process; per-file
//! reader memory (where each activity walk stopped) lives here, as it lives
//! in main's reader objects.
use crate::observe::{observers, ActivityState, Observer};
use crate::resume::{self, Command};
use crate::{launch, vendors};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::path::Path;

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    id: Value,
    op: String,
    #[serde(default)]
    vendor: String,
    #[serde(default)]
    path: String,
    #[serde(default)]
    checkpoint: Map<String, Value>,
    #[serde(default)]
    state: Option<ActivityState>,
    #[serde(default)]
    session_id: String,
    #[serde(default)]
    command: Value,
    #[serde(default)]
    yolo: bool,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    effort: Option<String>,
    #[serde(default)]
    resume: Option<String>,
    /// The caller's clock (seconds since the epoch) for idle flushes.
    #[serde(default)]
    now: Option<f64>,
}

fn observer<'a>(observers: &'a mut HashMap<&'static str, Observer>, vendor: &str) -> Result<&'a mut Observer, String> {
    observers.get_mut(vendor).ok_or_else(|| format!("no observe declaration for {vendor:?}"))
}

fn handle(observers: &mut HashMap<&'static str, Observer>, req: Request) -> Result<Value, String> {
    let path = Path::new(&req.path);
    let to_value = |v: Result<Value, serde_json::Error>| v.map_err(|e| e.to_string());
    match req.op.as_str() {
        "ping" => Ok(json!({"vendors": vendors::ALL.iter().map(|c| c.key).collect::<Vec<_>>()})),
        "observes" => Ok(json!(observers.keys().collect::<Vec<_>>())),
        "usage" => {
            let read = observer(observers, &req.vendor)?.usage(path, &req.checkpoint).map_err(|e| e.to_string())?;
            to_value(serde_json::to_value(read))
        }
        "activity" => {
            let mut state = req.state.unwrap_or_default();
            let observer = observer(observers, &req.vendor)?;
            observer.set_clock(req.now);
            let result = observer.activity(path, &mut state);
            let state = serde_json::to_value(&state).map_err(|e| e.to_string())?;
            match result {
                Ok(events) => Ok(json!({"events": events, "state": state})),
                // The walk's state still moved (main's `finally`), so it is returned with the error.
                Err(err) => Ok(json!({"events": [], "state": state, "error": err.to_string()})),
            }
        }
        "turns" => {
            let observer = observer(observers, &req.vendor)?;
            observer.set_clock(req.now);
            let turns = observer.turns(path, &req.session_id).map_err(|e| e.to_string())?;
            to_value(serde_json::to_value(turns))
        }
        "resume_id" => {
            let cli = crate::cli(&req.vendor).ok_or("unknown vendor")?;
            let id = match &req.command {
                Value::Array(items) => {
                    let argv: Vec<String> = items.iter().map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_owned)).collect();
                    resume::id_from_command(cli, &Command::Argv(&argv))
                }
                Value::String(text) => resume::id_from_command(cli, &Command::Text(text)),
                Value::Null => String::new(),
                other => resume::id_from_command(cli, &Command::Text(&other.to_string())),
            };
            Ok(json!(id))
        }
        "argv" => {
            let cli = crate::cli(&req.vendor).ok_or("unknown vendor")?;
            let request = launch::Request { yolo: req.yolo, model: req.model, effort: req.effort, resume: req.resume };
            launch::argv(cli, &request).map(|argv| json!(argv)).map_err(|refused| format!("{refused:?}"))
        }
        other => Err(format!("unknown op {other:?}")),
    }
}

pub fn serve(input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
    let mut observers = observers();
    for line in input.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Request>(&line) {
            Ok(req) => {
                let id = req.id.clone();
                match handle(&mut observers, req) {
                    Ok(result) => json!({"id": id, "ok": true, "result": result}),
                    Err(error) => json!({"id": id, "ok": false, "error": error}),
                }
            }
            Err(err) => json!({"id": null, "ok": false, "error": format!("bad request: {err}")}),
        };
        serde_json::to_writer(&mut output, &response)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}
