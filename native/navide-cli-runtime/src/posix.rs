use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::io::{self, BufRead, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const CAP: usize = 5 * 1024 * 1024;
const CHUNK: usize = 16384;
const MAX_REQUEST: usize = 32 * 1024 * 1024;

type Table = HashMap<i32, (i32, i32, String)>;
#[derive(Default)]
struct Registry {
    sessions: HashMap<String, Arc<Mutex<Session>>>,
    reserved: std::collections::HashSet<String>,
    workers: HashMap<String, mpsc::SyncSender<Value>>,
    settled: VecDeque<String>,
    cancelled: std::collections::HashSet<String>,
    last_request: u64,
    worker_count: Arc<AtomicUsize>,
}
type Sessions = Arc<Mutex<Registry>>;

struct Queue {
    control: VecDeque<Value>,
    output: HashMap<String, VecDeque<(Value, usize)>>,
    bytes: HashMap<String, usize>,
    order: VecDeque<String>,
    done: bool,
}
struct Wire {
    generation: String,
    queue: Mutex<Queue>,
    changed: Condvar,
}
impl Wire {
    fn control(&self, mut value: Value) {
        value["runtime_generation"] = json!(self.generation);
        let mut queue = self.queue.lock().unwrap();
        while queue.control.len() >= 256 && !queue.done {
            queue = self.changed.wait(queue).unwrap();
        }
        queue.control.push_back(value);
        self.changed.notify_all();
    }
    fn output(&self, session: &Session, data: Vec<u8>) {
        let mut value = session.event("output");
        value["runtime_generation"] = json!(self.generation);
        value["seq"] = json!(session.seq);
        value["offset"] = json!(session.offset - data.len() as u64);
        value["data_b64"] = json!(STANDARD.encode(&data));
        value["dropped_before"] = json!(0);
        let mut queue = self.queue.lock().unwrap();
        let id = session.id.clone();
        let mut size = queue.bytes.get(&id).copied().unwrap_or(0);
        let records = queue.output.entry(id.clone()).or_default();
        let mut dropped = 0;
        while size + data.len() > CAP {
            if let Some((old, bytes)) = records.pop_front() {
                size -= bytes;
                dropped += bytes + old["dropped_before"].as_u64().unwrap_or(0) as usize;
            } else {
                break;
            }
        }
        if let Some((first, _)) = records.front_mut() {
            first["dropped_before"] =
                json!(first["dropped_before"].as_u64().unwrap_or(0) + dropped as u64);
        } else {
            value["dropped_before"] = json!(dropped);
        }
        records.push_back((value, data.len()));
        queue.bytes.insert(id.clone(), size + data.len());
        if !queue.order.contains(&id) {
            queue.order.push_back(id);
        }
        self.changed.notify_all();
    }
    fn write_loop(&self) -> io::Result<()> {
        let mut stdout = io::stdout().lock();
        loop {
            let mut queue = self.queue.lock().unwrap();
            let value = loop {
                if let Some(value) = queue.control.pop_front() {
                    break Some(value);
                }
                if let Some(id) = queue.order.pop_front() {
                    if let Some((value, size)) =
                        queue.output.get_mut(&id).and_then(VecDeque::pop_front)
                    {
                        *queue.bytes.get_mut(&id).unwrap() -= size;
                        if queue
                            .output
                            .get(&id)
                            .is_some_and(|records| !records.is_empty())
                        {
                            queue.order.push_back(id);
                        } else {
                            queue.output.remove(&id);
                            queue.bytes.remove(&id);
                        }
                        break Some(value);
                    }
                }
                if queue.done {
                    break None;
                }
                queue = self.changed.wait(queue).unwrap();
            };
            self.changed.notify_all();
            drop(queue);
            let Some(value) = value else {
                return Ok(());
            };
            let bytes = serde_json::to_vec(&value)?;
            if bytes.len() + 1 > 65536 {
                return Err(io::Error::other("protocol output exceeds bound"));
            }
            stdout.write_all(&bytes)?;
            stdout.write_all(b"\n")?;
            stdout.flush()?;
        }
    }
}

struct Session {
    id: String,
    generation: String,
    create_id: String,
    pane: String,
    workspace: String,
    fd: Option<OwnedFd>,
    child: Child,
    root: Value,
    started: Instant,
    active: bool,
    barrier: Option<String>,
    paused: Option<Instant>,
    input: VecDeque<u8>,
    blocked: Option<Instant>,
    notified: bool,
    drained: usize,
    seq: u64,
    offset: u64,
    eof: bool,
    closing: Option<bool>,
    finished: bool,
    cleanup_complete: bool,
    descendants: Table,
    last_snapshot: Instant,
    exit: Option<Value>,
}
impl Session {
    fn event(&self, kind: &str) -> Value {
        json!({"event":kind,"session_id":self.id,"session_generation":self.generation,
            "pane_id":self.pane,"workspace_path":self.workspace})
    }
    fn fd(&self) -> i32 {
        self.fd.as_ref().map_or(-1, AsRawFd::as_raw_fd)
    }
    fn read(&mut self, wire: &Wire, budget: usize) {
        let mut left = budget;
        while left > 0 {
            // Never truncate a native PTY record with a tiny final-budget buffer.
            let mut data = vec![0; CHUNK];
            // SAFETY: owned nonblocking fd, valid uniquely-owned writable buffer.
            let n = unsafe { libc::read(self.fd(), data.as_mut_ptr().cast(), data.len()) };
            if n <= 0 {
                let error = io::Error::last_os_error();
                if n == 0 || error.raw_os_error() == Some(libc::EIO) {
                    self.eof = true;
                } else if error.kind() != io::ErrorKind::WouldBlock
                    && error.kind() != io::ErrorKind::Interrupted
                {
                    self.closing = Some(true);
                }
                break;
            }
            data.truncate(n as usize);
            left = left.saturating_sub(n as usize);
            self.seq += 1;
            self.offset += n as u64;
            wire.output(self, data);
        }
    }
    fn flush_input(&mut self, wire: &Wire) -> io::Result<()> {
        while !self.input.is_empty() {
            let fd = self.fd();
            let data = self.input.make_contiguous();
            // SAFETY: valid byte slice and retained writable PTY descriptor.
            let n = unsafe { libc::write(fd, data.as_ptr().cast(), data.len()) };
            if n < 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::WouldBlock {
                    break;
                }
                if error.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            if n == 0 {
                break;
            }
            self.input.drain(..n as usize);
            if self.blocked.is_some() {
                self.drained += n as usize;
            }
        }
        if self.input.is_empty() {
            if let Some(since) = self.blocked.take() {
                if self.notified {
                    let mut event = self.event("input_unblocked");
                    event["blocked_ms"] = json!(since.elapsed().as_millis() as u64);
                    event["drained"] = json!(self.drained);
                    wire.control(event);
                }
            }
            self.notified = false;
            self.drained = 0;
        } else {
            let since = self.blocked.get_or_insert_with(Instant::now);
            if !self.notified && since.elapsed() >= Duration::from_millis(500) {
                self.notified = true;
                let mut event = self.event("input_blocked");
                event["pending"] = json!(self.input.len());
                event["since_ms"] = json!(500);
                wire.control(event);
            }
        }
        Ok(())
    }
}

fn bounded_command(mut command: Command) -> io::Result<Vec<u8>> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let stdout = child.stdout.take().unwrap();
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.take(4 * 1024 * 1024 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > 4 * 1024 * 1024 {
            return Err(io::Error::other("snapshot exceeds bound"));
        }
        Ok(bytes)
    });
    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if start.elapsed() >= Duration::from_secs(5) {
            child.kill()?;
            child.wait()?;
            let _ = reader.join();
            return Err(io::Error::new(io::ErrorKind::TimedOut, "snapshot timeout"));
        }
        thread::sleep(Duration::from_millis(5));
    };
    let bytes = reader
        .join()
        .map_err(|_| io::Error::other("snapshot reader failed"))??;
    if !status.success() {
        return Err(io::Error::other("snapshot command failed"));
    }
    Ok(bytes)
}
fn snapshot() -> io::Result<Table> {
    let mut command = Command::new("/bin/ps");
    command
        .args(["-axo", "pid,ppid,pgid,lstart"])
        .env("LC_ALL", "C");
    let bytes = bounded_command(command)?;
    let mut table = Table::new();
    for line in String::from_utf8_lossy(&bytes).lines().skip(1) {
        let words: Vec<_> = line.split_whitespace().collect();
        if words.len() < 8 {
            continue;
        }
        if let (Ok(pid), Ok(ppid), Ok(pgid)) =
            (words[0].parse(), words[1].parse(), words[2].parse())
        {
            table.insert(pid, (ppid, pgid, words[3..].join(" ")));
        }
    }
    Ok(table)
}
fn descendants(table: &Table, pid: i32) -> Table {
    let mut result = Table::new();
    let mut pending = vec![pid];
    while let Some(parent) = pending.pop() {
        for (&child, entry) in table {
            if entry.0 == parent && child != pid && !result.contains_key(&child) {
                result.insert(child, entry.clone());
                pending.push(child);
            }
        }
    }
    result
}
fn identity(pid: i32, entry: &(i32, i32, String)) -> Value {
    json!({"pid":pid,"pgid":entry.1,"start_kind":"posix-lstart","start_value":entry.2})
}
fn resize(fd: i32, rows: u16, cols: u16) -> io::Result<()> {
    let size = libc::winsize {
        ws_row: rows,
        ws_col: cols,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    // SAFETY: valid retained PTY fd, correctly sized winsize pointer.
    if unsafe { libc::ioctl(fd, libc::TIOCSWINSZ, &size) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}
fn text<'a>(value: &'a Value, key: &str) -> io::Result<&'a str> {
    value[key]
        .as_str()
        .filter(|text| !text.contains('\0'))
        .ok_or_else(|| io::Error::other(format!("invalid {key}")))
}
fn dimension(value: &Value, key: &str) -> io::Result<u16> {
    value[key]
        .as_u64()
        .and_then(|n| u16::try_from(n).ok())
        .ok_or_else(|| io::Error::other(format!("invalid {key}")))
}
fn spawn(args: &Value, wire: &Wire, request: &str) -> io::Result<Session> {
    let id = text(args, "session_id")?.to_owned();
    let generation = text(args, "session_generation")?.to_owned();
    let pane = text(args, "pane_id")?.to_owned();
    let workspace = text(args, "workspace_path")?.to_owned();
    let cwd = text(args, "cwd")?.to_owned();
    let argv: Vec<String> = args["argv"]
        .as_array()
        .ok_or_else(|| io::Error::other("argv required"))?
        .iter()
        .map(|v| {
            v.as_str()
                .filter(|s| !s.contains('\0'))
                .map(String::from)
                .ok_or_else(|| io::Error::other("invalid argv"))
        })
        .collect::<io::Result<_>>()?;
    if argv.is_empty() {
        return Err(io::Error::other("argv required"));
    }
    let rows = dimension(args, "rows")?;
    let cols = dimension(args, "cols")?;
    let mut master = -1;
    let mut slave = -1;
    // SAFETY: output descriptors are initialized by openpty; no name/termios pointer.
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
    resize(master.as_raw_fd(), rows, cols)?;
    // SAFETY: retained descriptors; ensure master cannot leak into unrelated child execs.
    if unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) } < 0
        || unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) } < 0
    {
        return Err(io::Error::last_os_error());
    }
    let mut command = Command::new(&argv[0]);
    command.args(&argv[1..]).current_dir(cwd).env_clear();
    for (key, value) in args["env"]
        .as_object()
        .ok_or_else(|| io::Error::other("env required"))?
    {
        if key.contains(['\0', '=']) {
            return Err(io::Error::other("invalid env key"));
        }
        let value = value
            .as_str()
            .filter(|s| !s.contains('\0'))
            .ok_or_else(|| io::Error::other("invalid env value"))?;
        command.env(key, value);
    }
    command
        .stdin(Stdio::from(slave.try_clone()?))
        .stdout(Stdio::from(slave.try_clone()?))
        .stderr(Stdio::from(slave));
    // SAFETY: only async-signal-safe native calls between fork and exec; no locks/allocations.
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() < 0 {
                return Err(io::Error::last_os_error());
            }
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::signal(libc::SIGXFSZ, libc::SIG_DFL);
            // Preserve baseline's best-effort controlling-terminal claim.
            libc::ioctl(0, libc::TIOCSCTTY as _, 0);
            Ok(())
        });
    }
    let mut child = command.spawn()?;
    let pid = child.id() as i32;
    let table = snapshot().unwrap_or_default();
    let root = if let Some(entry) = table.get(&pid) {
        identity(pid, entry)
    } else if let Some(status) = child.try_wait()? {
        json!({"pid":pid,"pgid":pid,"start_kind":"posix-lstart","start_value":null,"early_status":status.code().or(status.signal().map(|n| -n))})
    } else {
        let _ = child.kill();
        let _ = child.wait();
        return Err(io::Error::other("OWNERSHIP_UNVERIFIED"));
    };
    let session = Session {
        id,
        generation,
        create_id: request.into(),
        pane,
        workspace,
        fd: Some(master),
        child,
        root,
        started: Instant::now(),
        active: false,
        barrier: None,
        paused: None,
        input: VecDeque::new(),
        blocked: None,
        notified: false,
        drained: 0,
        seq: 0,
        offset: 0,
        eof: false,
        closing: None,
        finished: false,
        cleanup_complete: false,
        descendants: descendants(&table, pid),
        last_snapshot: Instant::now(),
        exit: None,
    };
    let mut event = session.event("owned");
    event["create_id"] = json!(request);
    event["root"] = session.root.clone();
    wire.control(event);
    Ok(session)
}

fn cleanup(session: &mut Session, force: bool, wire: &Wire) -> io::Result<Vec<Value>> {
    let pid = session.child.id() as i32;
    if session.child.try_wait()?.is_some() && session.descendants.is_empty() {
        return Ok(Vec::new());
    }
    let before = snapshot()?;
    if before.contains_key(&pid) {
        session.descendants.extend(descendants(&before, pid));
    }
    let root_live = before
        .get(&pid)
        .is_some_and(|entry| Some(entry.2.as_str()) == session.root["start_value"].as_str());
    let signal = if force { libc::SIGKILL } else { libc::SIGTERM };
    if root_live {
        // SAFETY: root PID+birth is matched and its own child group remains owned.
        unsafe {
            libc::kill(-pid, signal);
        }
        // Foreground job may have its own group; signal only an observed owned group.
        let foreground = unsafe { libc::tcgetpgrp(session.fd()) };
        if foreground > 0
            && session
                .descendants
                .values()
                .any(|entry| entry.1 == foreground)
        {
            unsafe {
                libc::kill(-foreground, signal);
            }
        }
    }
    for (&child, old) in &session.descendants {
        if before.get(&child).is_some_and(|entry| entry.2 == old.2) {
            unsafe {
                libc::kill(child, signal);
            }
        }
    }
    if !force {
        thread::sleep(Duration::from_secs(1));
    }
    let after = snapshot()?;
    if after
        .get(&pid)
        .is_some_and(|entry| Some(entry.2.as_str()) == session.root["start_value"].as_str())
    {
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
    }
    for (&child, old) in &session.descendants {
        if after.get(&child).is_some_and(|entry| entry.2 == old.2) {
            unsafe {
                libc::kill(child, libc::SIGKILL);
            }
        }
    }
    let deadline = Instant::now() + Duration::from_secs(1);
    while session.child.try_wait()?.is_none() {
        // A blocked PTY write can delay even SIGKILL until its master drains.
        session.read(wire, 256 * 1024);
        if Instant::now() > deadline {
            return Err(io::Error::other("root reap timeout"));
        }
        thread::sleep(Duration::from_millis(5));
    }
    let final_table = snapshot()?;
    let survivors = session
        .descendants
        .iter()
        .filter_map(|(&child, old)| {
            final_table
                .get(&child)
                .filter(|entry| entry.2 == old.2)
                .map(|entry| identity(child, entry))
        })
        .collect();
    Ok(survivors)
}
fn session_loop(session_arc: Arc<Mutex<Session>>, wire: Arc<Wire>, sessions: Sessions) {
    loop {
        let mut session = session_arc.lock().unwrap();
        if session.finished {
            return;
        }
        if !session.active && session.closing.is_none() {
            drop(session);
            thread::sleep(Duration::from_millis(2));
            continue;
        }
        if session
            .paused
            .is_some_and(|since| since.elapsed() >= Duration::from_secs(1))
        {
            session.paused = None;
        }
        if session.barrier.is_none() && session.paused.is_none() {
            session.read(&wire, 256 * 1024);
        }
        if session.flush_input(&wire).is_err() {
            session.closing = Some(true);
        }
        let status = session.child.try_wait().ok().flatten();
        let closing = session.closing;
        if (closing.is_some() || session.eof || status.is_some()) && session.barrier.is_none() {
            session.read(&wire, 256 * 1024);
            let survivors = cleanup(&mut session, closing.unwrap_or(false), &wire);
            let status = session.child.try_wait().ok().flatten().or(status);
            let mut event = session.event("exit");
            event["reason"] = json!(if closing.is_some() { "killed" } else { "exit" });
            event["exit_code"] = json!(status.and_then(|s| s.code().or(s.signal().map(|n| -n))));
            event["signal"] = json!(status.and_then(|s| s.signal()).map(|n| format!("SIG{n}")));
            event["uptime_ms"] = json!(session.started.elapsed().as_millis() as u64);
            event["last_output_seq"] = json!(session.seq);
            event["dropped_tail_bytes"] = json!(0);
            event["dropped_tail_first_seq"] = Value::Null;
            session.exit = Some(event.clone());
            session.fd.take();
            session.finished = true;
            session.cleanup_complete = survivors.as_ref().is_ok_and(Vec::is_empty);
            wire.control(event);
            let mut event = session.event("cleaned");
            event["cleanup_complete"] = json!(session.cleanup_complete);
            event["root_reaped"] = json!(status.is_some());
            event["survivors"] =
                json!(survivors.unwrap_or_else(|error| vec![json!({"error":error.to_string()})]));
            event["unverifiable"] = json!([]);
            wire.control(event);
            let id = session.id.clone();
            drop(session);
            let mut registry = sessions.lock().unwrap();
            registry.workers.remove(&id);
            registry.settled.push_back(id);
            while registry.settled.len() > 256 {
                let id = registry.settled.pop_front().unwrap();
                registry.sessions.remove(&id);
                registry.reserved.remove(&id);
            }
            return;
        }
        if session.last_snapshot.elapsed()
            >= Duration::from_secs(if session.started.elapsed() < Duration::from_secs(30) {
                5
            } else {
                30
            })
        {
            // Probe outside the session lock so input/control remains independent.
            session.last_snapshot = Instant::now();
            let pid = session.child.id() as i32;
            drop(session);
            if let Ok(table) = snapshot() {
                let mut session = session_lock(&session_arc);
                if !table.get(&pid).is_some_and(|entry| {
                    Some(entry.2.as_str()) == session.root["start_value"].as_str()
                }) {
                    continue; // Keep the last good tree when death races the snapshot.
                }
                session.descendants = descendants(&table, pid);
                for (part, values) in session
                    .descendants
                    .iter()
                    .collect::<Vec<_>>()
                    .chunks(128)
                    .enumerate()
                {
                    let mut event = session.event("ownership");
                    event["snapshot_seq"] = json!(session.started.elapsed().as_millis() as u64);
                    event["part"] = json!(part);
                    event["final"] = json!((part + 1) * 128 >= session.descendants.len());
                    event["descendants"] = json!(values
                        .iter()
                        .map(|(pid, entry)| identity(**pid, entry))
                        .collect::<Vec<_>>());
                    wire.control(event);
                }
            }
        } else {
            drop(session);
        }
        thread::sleep(Duration::from_millis(2));
    }
}
fn session_lock(session: &Arc<Mutex<Session>>) -> std::sync::MutexGuard<'_, Session> {
    session.lock().unwrap()
}

fn execute(request: &Value, sessions: &Sessions, wire: &Arc<Wire>) -> io::Result<Value> {
    let op = text(request, "op")?;
    let args = &request["args"];
    if op == "ping" {
        let registry = sessions.lock().unwrap();
        let queue = wire.queue.lock().unwrap();
        return Ok(
            json!({"pong":true,"bookkeeping":{"last_request":registry.last_request,
            "live_sessions":registry.sessions.len()-registry.settled.len(),
            "settled_sessions":registry.settled.len(),"session_workers":registry.workers.len(),
            "queued_output_bytes":queue.bytes.values().sum::<usize>(),
            "max_session_output_bytes":queue.bytes.values().max().copied().unwrap_or(0),
            "queued_output_sessions":queue.output.len()}}),
        );
    }
    if op == "cancel" {
        let target = text(args, "target_id")?;
        let candidates: Vec<_> = sessions
            .lock()
            .unwrap()
            .sessions
            .values()
            .cloned()
            .collect();
        let session = candidates
            .into_iter()
            .find(|s| s.lock().unwrap().create_id == target);
        if let Some(session) = session {
            let mut session = session.lock().unwrap();
            if session.active {
                return Ok(json!({"state":"settled"}));
            }
            session.closing = Some(true);
            session.active = true;
            return Ok(json!({"state":"cleanup-started"}));
        }
        let mut registry = sessions.lock().unwrap();
        // At most admitted create cancellations, not a lifetime request-ID history.
        if registry.cancelled.len() >= 256 {
            return Err(io::Error::other("cancel admission full"));
        }
        registry.cancelled.insert(target.into());
        return Ok(json!({"state":"prevented"}));
    }
    if op == "create" {
        let id = text(args, "session_id")?.to_owned();
        let mut all = sessions.lock().unwrap();
        if !all.reserved.insert(id.clone()) {
            return Err(io::Error::other("duplicate create"));
        }
        let request_id = text(request, "id")?;
        if all.cancelled.remove(request_id) {
            return Err(io::Error::other("CREATE_CANCELLED"));
        }
        drop(all);
        // Reservation is retained; native spawn/probes never hold the global registry lock.
        let mut session = spawn(args, wire, request_id)?;
        if sessions.lock().unwrap().cancelled.remove(request_id) {
            session.closing = Some(true);
            session.active = true;
        }
        let result = json!({"session_id":session.id,"session_generation":session.generation,
            "state":if session.root["early_status"].is_null() {"owned"} else {"exited"},"root":session.root,"exit":null});
        let session = Arc::new(Mutex::new(session));
        sessions
            .lock()
            .unwrap()
            .sessions
            .insert(id, session.clone());
        let wire = wire.clone();
        let sessions = sessions.clone();
        thread::spawn(move || session_loop(session, wire, sessions));
        return Ok(result);
    }
    let session_arc = sessions
        .lock()
        .unwrap()
        .sessions
        .get(text(request, "session_id")?)
        .cloned()
        .ok_or_else(|| io::Error::other("UNKNOWN_SESSION"))?;
    let mut session = session_arc.lock().unwrap();
    if text(request, "session_generation")? != session.generation {
        return Err(io::Error::other("STALE_SESSION"));
    }
    match op {
        "activate" => {
            session.active = true;
            Ok(json!({"state":if session.finished {"exited"} else {"running"}}))
        }
        "write" | "interrupt" => {
            let data = STANDARD
                .decode(text(args, "data_b64")?)
                .map_err(io::Error::other)?;
            if data.len() > CHUNK {
                return Err(io::Error::other("input chunk too large"));
            }
            if session.finished || session.closing.is_some() {
                return Ok(json!({"pending":0,"accepted":true}));
            }
            let deadline = Instant::now() + Duration::from_secs(15);
            while session.input.len() + data.len() > CAP {
                if Instant::now() >= deadline {
                    return Err(io::Error::other("input admission timeout"));
                }
                drop(session);
                thread::sleep(Duration::from_millis(2));
                session = session_lock(&session_arc);
            }
            if !data.is_empty() {
                session.input.extend(data);
                session.flush_input(wire)?;
            }
            Ok(json!({"pending":session.input.len(),"accepted":true}))
        }
        "resize" => {
            if session.barrier.is_some() {
                return Err(io::Error::other("CONTROL_FENCE_HELD"));
            }
            if session.finished {
                return Ok(json!({"barrier_id":request["id"],"through_seq":session.seq}));
            }
            let cols = dimension(args, "cols")?;
            let rows = dimension(args, "rows")?;
            let mut available: i32 = 0;
            unsafe {
                libc::ioctl(session.fd(), libc::FIONREAD, &mut available);
            }
            session.read(wire, available.max(0) as usize);
            if args["redraw"].as_bool().unwrap_or(false) {
                resize(session.fd(), rows.saturating_sub(1).max(1), cols)?;
            }
            resize(session.fd(), rows, cols)?;
            session.barrier = Some(text(request, "id")?.to_owned());
            Ok(json!({"barrier_id":request["id"],"through_seq":session.seq}))
        }
        "release" => {
            if session.barrier.as_deref() == Some(text(args, "barrier_id")?) {
                session.barrier = None;
            }
            Ok(json!({"released":true}))
        }
        "flow" => {
            session.paused = args["paused"]
                .as_bool()
                .filter(|v| *v)
                .map(|_| Instant::now());
            Ok(json!({"paused":session.paused.is_some()}))
        }
        "kill" => {
            session.closing = Some(args["force"].as_bool().unwrap_or(false));
            session.active = true;
            Ok(json!({"accepted":true}))
        }
        "state" => {
            let fg = unsafe { libc::tcgetpgrp(session.fd()) };
            Ok(
                json!({"state":if session.finished {"exited"} else {"running"},"pending":session.input.len(),
                "shell_at_prompt":if fg>0 {Some(fg == session.child.id() as i32)} else {None},
                "root":session.root,"descendants":[],"cleanup_complete":session.cleanup_complete}),
            )
        }
        _ => Err(io::Error::other("UNKNOWN_OP")),
    }
}

fn respond(request: Value, sessions: &Sessions, wire: &Arc<Wire>) {
    let id = request["id"].clone();
    let response = match execute(&request, sessions, wire) {
        Ok(result) => json!({"id":id,"ok":true,"result":result}),
        Err(error) => {
            if request["op"] == "create" {
                if let Some(sid) = request["args"]["session_id"].as_str() {
                    let mut registry = sessions.lock().unwrap();
                    registry.workers.remove(sid);
                    registry.reserved.remove(sid);
                }
            }
            let message = error.to_string();
            let code = match message.as_str() {
                "STALE_SESSION"
                | "UNKNOWN_SESSION"
                | "UNKNOWN_OP"
                | "OWNERSHIP_UNVERIFIED"
                | "CREATE_CANCELLED" => message.as_str(),
                _ => {
                    if message.starts_with("invalid ")
                        || message.starts_with("duplicate ")
                        || message == "input chunk too large"
                    {
                        "BAD_REQUEST"
                    } else if request["op"] == "create" {
                        "SPAWN_FAILED"
                    } else {
                        "IO_FAILED"
                    }
                }
            };
            json!({"id":id,"ok":false,"error":{"code":code,"message":message.chars().take(1024).collect::<String>(),
                "stage":if request["op"]=="create" {"spawn"} else {"io"}}})
        }
    };
    wire.control(response);
}

pub fn run() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let option = |name: &str| {
        args.windows(2)
            .find(|pair| pair[0] == name)
            .map(|pair| pair[1].clone())
    };
    let generation =
        option("--runtime-generation").ok_or_else(|| io::Error::other("generation required"))?;
    let wire = Arc::new(Wire {
        generation,
        queue: Mutex::new(Queue {
            control: VecDeque::new(),
            output: HashMap::new(),
            bytes: HashMap::new(),
            order: VecDeque::new(),
            done: false,
        }),
        changed: Condvar::new(),
    });
    let writer_wire = wire.clone();
    let writer = thread::spawn(move || writer_wire.write_loop());
    if args.iter().any(|arg| arg == "--fail-startup") {
        wire.control(json!({"event":"fatal","protocol":1,"error":{"code":"STARTUP_FAILED","message":"requested isolated startup fault","stage":"initialize"}}));
        wire.queue.lock().unwrap().done = true;
        wire.changed.notify_all();
        writer.join().unwrap()?;
        return Err(io::Error::other("STARTUP_FAILED"));
    }
    wire.control(json!({"event":"ready","protocol":option("--ready-protocol").and_then(|s|s.parse::<u32>().ok()).unwrap_or(1),
        "pid":std::process::id(),"owner_pid":option("--owner-pid").and_then(|s|s.parse::<u32>().ok()).unwrap_or(0),
        "runtime":"navide-cli-runtime","version":env!("CARGO_PKG_VERSION"),"pty_backend":"posix","platform":std::env::consts::OS,"arch":std::env::consts::ARCH}));
    let sessions: Sessions = Arc::new(Mutex::new(Registry::default()));
    let mut last_request = 0u64;
    let mut input_error = None;
    let mut shutdown = None;
    let mut stdin = io::stdin().lock();
    loop {
        let mut line = Vec::new();
        let parsed = (|| -> io::Result<Option<Value>> {
            let length = (&mut stdin)
                .take((MAX_REQUEST + 1) as u64)
                .read_until(b'\n', &mut line)?;
            if length == 0 {
                return Ok(None);
            }
            if length > MAX_REQUEST || line.last() != Some(&b'\n') {
                return Err(io::Error::other("invalid bounded request line"));
            }
            let request: Value = serde_json::from_slice(&line)?;
            let id = text(&request, "id")?;
            let number = id
                .strip_prefix('r')
                .and_then(|s| s.parse::<u64>().ok())
                .filter(|n| *n > last_request)
                .ok_or_else(|| io::Error::other("invalid/duplicate request counter"))?;
            last_request = number;
            sessions.lock().unwrap().last_request = number;
            Ok(Some(request))
        })();
        let request = match parsed {
            Ok(Some(request)) => request,
            Ok(None) => break,
            Err(error) => {
                input_error = Some(error);
                break;
            }
        };
        let id = request["id"].clone();
        if request["runtime_generation"] != wire.generation {
            wire.control(json!({"id":id,"ok":false,"error":{"code":"STALE_RUNTIME","message":"runtime generation mismatch","stage":"validate"}}));
            continue;
        }
        if request["op"] == "shutdown" {
            shutdown = id.as_str().map(String::from);
            break;
        }
        let sid = if request["op"] == "create" {
            request["args"]["session_id"].as_str()
        } else {
            request["session_id"].as_str()
        };
        let sender = if let Some(sid) = sid {
            let mut registry = sessions.lock().unwrap();
            if request["op"] == "create"
                && !registry.workers.contains_key(sid)
                && !registry.sessions.contains_key(sid)
            {
                let (sender, receiver) = mpsc::sync_channel(256);
                registry.workers.insert(sid.to_owned(), sender.clone());
                let request_sessions = sessions.clone();
                let request_wire = wire.clone();
                let worker_count = registry.worker_count.clone();
                worker_count.fetch_add(1, Ordering::SeqCst);
                thread::spawn(move || {
                    while let Ok(request) = receiver.recv() {
                        respond(request, &request_sessions, &request_wire);
                    }
                    worker_count.fetch_sub(1, Ordering::SeqCst);
                });
            }
            registry.workers.get(sid).cloned()
        } else {
            None
        };
        if let Some(sender) = sender {
            if let Err(error) = sender.try_send(request) {
                let request = match error {
                    mpsc::TrySendError::Full(request)
                    | mpsc::TrySendError::Disconnected(request) => request,
                };
                wire.control(json!({"id":request["id"],"ok":false,"error":{"code":"BAD_REQUEST","message":"session queue not admitting","stage":"admit"}}));
            }
        } else {
            // Runtime operations and settled-session queries have no native I/O wait.
            respond(request, &sessions, &wire);
        }
    }
    if let Some(error) = &input_error {
        wire.control(json!({"event":"fatal","error":{"code":"PROTOCOL_ERROR","message":error.to_string(),"stage":"validate"}}));
    }
    let worker_count = {
        let mut registry = sessions.lock().unwrap();
        registry.workers.clear();
        registry.worker_count.clone()
    };
    let deadline = Instant::now() + Duration::from_secs(12);
    let clean = loop {
        let owned: Vec<_> = sessions
            .lock()
            .unwrap()
            .sessions
            .values()
            .cloned()
            .collect();
        let mut complete = worker_count.load(Ordering::SeqCst) == 0;
        let mut clean = true;
        for session in owned {
            let mut session = session.lock().unwrap();
            if !session.finished {
                session.closing = Some(false);
                session.active = true;
                session.barrier = None;
                session.paused = None;
                complete = false;
            } else {
                clean &= session.cleanup_complete;
            }
        }
        if complete {
            break clean;
        }
        if Instant::now() >= deadline {
            break false;
        }
        thread::sleep(Duration::from_millis(5));
    };
    if let Some(id) = shutdown {
        wire.control(json!({"id":id,"ok":clean,"result":{"cleanup_complete":clean,"survivors":[]},"error":{"code":"CLEANUP_FAILED","message":"incomplete runtime cleanup","stage":"shutdown"}}));
    }
    wire.queue.lock().unwrap().done = true;
    wire.changed.notify_all();
    writer
        .join()
        .map_err(|_| io::Error::other("protocol writer panic"))??;
    if !clean {
        return Err(io::Error::other("incomplete cleanup"));
    }
    if let Some(error) = input_error {
        return Err(error);
    }
    Ok(())
}
