//! `serve`: one JSON request per stdin line, one JSON response per stdout
//! line. Responses carry the request's id and may come back in another
//! order: the reads of one file always go to the same worker (so they stay
//! in order, and the reader memory for that file lives in one place), and
//! different files are read side by side, so one long read does not hold up
//! the others. A request that panics answers an error and the process keeps
//! serving.
use crate::observe::{Error, HAS_FILE_IDENTITY};
use crate::registry::{AnyObserver, ALL};
use crate::resume::{self, Command};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::io::{BufRead, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};

/// Readers in flight at once. The backend's own callers are a handful of
/// threads (the watcher, the token monitor, the ledgers).
pub const WORKERS: usize = 4;

#[derive(Deserialize)]
pub struct Request {
    #[serde(default)]
    pub id: Value,
    pub op: String,
    #[serde(default)]
    pub vendor: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub checkpoint: Map<String, Value>,
    /// The watcher's seen-keys bag for the file (activity).
    #[serde(default)]
    pub bag: Vec<String>,
    #[serde(default)]
    pub session_id: String,
    /// A saved launch command, as a string or an argv (`resume_id`).
    #[serde(default)]
    pub command: Value,
    /// The caller's clock (seconds since the epoch) for idle flushes.
    #[serde(default)]
    pub now: Option<f64>,
}

/// One worker's observers, by CLI key.
pub type Observers = HashMap<&'static str, AnyObserver>;

pub fn observers() -> Observers {
    AnyObserver::all().into_iter().map(|observer| (observer.key(), observer)).collect()
}

/// A failed request: what went wrong and of which kind. `unsupported` is the
/// declared "this store is the Python reader's" answer; every other kind is
/// a fault.
pub struct Failure {
    pub kind: &'static str,
    pub error: String,
}

impl From<Error> for Failure {
    fn from(err: Error) -> Self {
        let kind = match err {
            Error::Unsupported(_) => "unsupported",
            Error::Io(_) => "io",
            Error::Decode { .. } => "decode",
        };
        Failure { kind, error: err.to_string() }
    }
}

fn bad(error: impl Into<String>) -> Failure {
    Failure { kind: "request", error: error.into() }
}

fn observer<'a>(observers: &'a mut Observers, vendor: &str) -> Result<&'a mut AnyObserver, Failure> {
    observers.get_mut(vendor).ok_or_else(|| bad(format!("no observe declaration for {vendor:?}")))
}

/// What `observes` answers: each observed CLI with the form its cwd is to
/// be put in. Empty where files have no identity (the checkpoints this
/// process shares with the Python readers could never match).
fn observed(observers: &Observers) -> Value {
    if !HAS_FILE_IDENTITY {
        return json!({});
    }
    Value::Object(observers.iter().map(|(key, observer)| ((*key).to_owned(), json!({"cwd": observer.cwd_form()}))).collect())
}

/// Answer one request. The result may carry `notes`: what main's reader
/// would have logged while reading.
pub fn handle(observers: &mut Observers, req: Request) -> Result<Value, Failure> {
    let path = Path::new(&req.path);
    match req.op.as_str() {
        "ping" => Ok(json!({"vendors": ALL.iter().map(|c| c.key).collect::<Vec<_>>()})),
        "observes" => Ok(observed(observers)),
        "usage" => {
            let observer = observer(observers, &req.vendor)?;
            let read = observer.usage(path, &req.checkpoint)?;
            Ok(json!({"events": read.events, "checkpoint": read.checkpoint, "notes": observer.take_notes()}))
        }
        "activity" => {
            let mut bag = req.bag;
            let observer = observer(observers, &req.vendor)?;
            observer.set_clock(req.now);
            match observer.activity_bag(path, &mut bag) {
                Ok(events) => Ok(json!({"events": events, "bag": bag, "notes": observer.take_notes()})),
                // The walk's state still moved (main's `finally`), so the bag is returned with the error.
                Err(err @ Error::Decode { .. }) => Ok(json!({"events": [], "bag": bag, "error": err.to_string(), "notes": observer.take_notes()})),
                // Unreadable: the caller's own reader decides (main returns no events).
                Err(err) => Err(err.into()),
            }
        }
        "turns" => {
            let observer = observer(observers, &req.vendor)?;
            observer.set_clock(req.now);
            match observer.turns(path, &req.session_id) {
                Ok(turns) => Ok(json!({"turns": turns})),
                // main's strict text-mode pass raises on a file that is not UTF-8.
                Err(err @ Error::Decode { .. }) => Ok(json!({"turns": [], "error": err.to_string()})),
                Err(err) => Err(err.into()),
            }
        }
        "resume_id" => {
            let cli = crate::cli(&req.vendor).ok_or_else(|| bad("unknown vendor"))?;
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
        other => Err(bad(format!("unknown op {other:?}"))),
    }
}

pub type Handler = fn(&mut Observers, Request) -> Result<Value, Failure>;

/// One request, answered: a panic is an error answer, and the observers it
/// may have left half-updated are replaced (what they remembered is only an
/// optimisation).
fn answer(observers: &mut Observers, req: Request, handler: Handler) -> Value {
    let id = req.id.clone();
    match catch_unwind(AssertUnwindSafe(|| handler(observers, req))) {
        Ok(Ok(result)) => json!({"id": id, "ok": true, "result": result}),
        Ok(Err(failure)) => json!({"id": id, "ok": false, "kind": failure.kind, "error": failure.error}),
        Err(panic) => {
            *observers = self::observers();
            let what = panic.downcast_ref::<&str>().map(|s| (*s).to_owned()).or_else(|| panic.downcast_ref::<String>().cloned()).unwrap_or_default();
            json!({"id": id, "ok": false, "kind": "panic", "error": format!("panic: {what}")})
        }
    }
}

fn write(output: &Mutex<impl Write>, response: &Value) -> std::io::Result<()> {
    let mut line = serde_json::to_vec(response)?;
    line.push(b'\n');
    let mut output = output.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    output.write_all(&line)?;
    output.flush()
}

/// The worker a request belongs to: the same file always the same one.
fn lane(req: &Request, workers: usize) -> usize {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    (&req.vendor, &req.path).hash(&mut hasher);
    (hasher.finish() % workers as u64) as usize
}

pub fn serve(input: impl BufRead, output: impl Write + Send + 'static) -> std::io::Result<()> {
    serve_with(input, output, WORKERS, handle)
}

pub fn serve_with(input: impl BufRead, output: impl Write + Send + 'static, workers: usize, handler: Handler) -> std::io::Result<()> {
    let output = Arc::new(Mutex::new(output));
    let mut lanes: Vec<Sender<Request>> = Vec::new();
    let mut threads = Vec::new();
    for _ in 0..workers.max(1) {
        let (sender, receiver) = channel::<Request>();
        let output = Arc::clone(&output);
        lanes.push(sender);
        threads.push(std::thread::spawn(move || {
            let mut observers = observers();
            for req in receiver {
                // A closed pipe ends the process from the reading side.
                let _ = write(&output, &answer(&mut observers, req, handler));
            }
        }));
    }
    let mut result = Ok(());
    for line in input.lines() {
        let line = match line {
            Ok(line) => line,
            Err(err) => {
                result = Err(err);
                break;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let sent = match serde_json::from_str::<Request>(&line) {
            Ok(req) => {
                let lane = lane(&req, lanes.len());
                lanes[lane].send(req).map_err(|_| std::io::Error::other("worker gone"))
            }
            Err(err) => write(&output, &json!({"id": null, "ok": false, "kind": "request", "error": format!("bad request: {err}")})),
        };
        if let Err(err) = sent {
            result = Err(err);
            break;
        }
    }
    drop(lanes);
    for thread in threads {
        let _ = thread.join();
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    /// A writer the test can read back after `serve` has finished.
    #[derive(Clone, Default)]
    struct Shared(Arc<Mutex<Vec<u8>>>);

    impl Write for Shared {
        fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(data);
            Ok(data.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn run(requests: &str, workers: usize, handler: Handler) -> Vec<Value> {
        let out = Shared::default();
        serve_with(Cursor::new(requests.to_owned()), out.clone(), workers, handler).unwrap();
        let bytes = out.0.lock().unwrap().clone();
        String::from_utf8(bytes).unwrap().lines().map(|line| serde_json::from_str(line).unwrap()).collect()
    }

    fn by_id(answers: &[Value], id: i64) -> &Value {
        answers.iter().find(|a| a["id"] == id).unwrap_or_else(|| panic!("no answer for {id}"))
    }

    #[test]
    fn a_panicking_request_answers_an_error_and_the_next_one_is_served() {
        fn handler(observers: &mut Observers, req: Request) -> Result<Value, Failure> {
            if req.op == "boom" {
                panic!("declared rule blew up");
            }
            handle(observers, req)
        }
        let previous = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let answers = run("{\"id\":1,\"op\":\"boom\"}\n{\"id\":2,\"op\":\"ping\"}\nnot json\n", 1, handler);
        std::panic::set_hook(previous);
        assert_eq!(by_id(&answers, 1)["ok"], false);
        assert_eq!(by_id(&answers, 1)["kind"], "panic");
        assert_eq!(by_id(&answers, 1)["error"], "panic: declared rule blew up");
        assert_eq!(by_id(&answers, 2)["ok"], true);
        assert!(answers.iter().any(|a| a["id"].is_null() && a["kind"] == "request"));
    }

    #[test]
    fn a_slow_read_does_not_hold_up_another_file() {
        // Two lanes: the slow request is still running when the fast one answers.
        fn handler(_: &mut Observers, req: Request) -> Result<Value, Failure> {
            if req.path == "slow" {
                std::thread::sleep(std::time::Duration::from_millis(300));
            }
            Ok(json!(req.path))
        }
        let slow = Request { id: json!(1), op: "x".into(), vendor: "v".into(), path: "slow".into(), ..empty() };
        let slow_lane = lane(&slow, 2);
        let fast_path = (0..64).map(|n| format!("fast-{n}")).find(|p| lane(&Request { path: p.clone(), vendor: "v".into(), ..empty() }, 2) != slow_lane).unwrap();
        let requests = format!("{{\"id\":1,\"op\":\"x\",\"vendor\":\"v\",\"path\":\"slow\"}}\n{{\"id\":2,\"op\":\"x\",\"vendor\":\"v\",\"path\":\"{fast_path}\"}}\n");
        let answers = run(&requests, 2, handler);
        let order: Vec<i64> = answers.iter().map(|a| a["id"].as_i64().unwrap()).collect();
        assert_eq!(order, [2, 1], "the fast file's answer is written first");
    }

    #[test]
    fn one_files_requests_are_answered_in_order() {
        fn handler(_: &mut Observers, req: Request) -> Result<Value, Failure> {
            Ok(req.id)
        }
        let requests: String = (0..50).map(|n| format!("{{\"id\":{n},\"op\":\"x\",\"vendor\":\"v\",\"path\":\"same\"}}\n")).collect();
        let order: Vec<i64> = run(&requests, 4, handler).iter().map(|a| a["id"].as_i64().unwrap()).collect();
        assert_eq!(order, (0..50).collect::<Vec<_>>());
    }

    fn empty() -> Request {
        serde_json::from_str("{\"op\":\"\"}").unwrap()
    }
}
