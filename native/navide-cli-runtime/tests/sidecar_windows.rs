#![cfg(windows)]
//! The real sidecar binary on Windows, driven frame by frame over its stdin and stdout: handshake,
//! the `DuplicateHandle` pull with the 7.4 checks, a ConPTY round trip, kill and the pre-close gate
//! (G-W4), per-session isolation (W10), the cancellation timing of G-W5 and uniform loss. The Python
//! seam cases live in `tests/cli` and `backend/tests`; these isolate the Rust binary itself.
//!
//! Receipts: when `E5_RECEIPT_DIR` is set each measured case appends one status-only JSON line to
//! `<dir>/rust-receipts.jsonl` (no paths, no tokens, no environment).
#[path = "../src/frame.rs"]
#[allow(dead_code)]
mod frame;

use frame::{code, Direction, FrameType};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::ptr::null_mut;
use std::sync::mpsc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{
    DuplicateHandle, DUPLICATE_SAME_ACCESS, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0,
};
use windows_sys::Win32::Storage::FileSystem::{GetFileType, ReadFile, WriteFile, FILE_TYPE_PIPE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::Pipes::PeekNamedPipe;
use windows_sys::Win32::System::ProcessStatus::{GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
};
use windows_sys::Win32::System::IO::CancelSynchronousIo;

const GENERATION: &str = "00112233445566778899aabbccddeeff";
/// The wait for any one expected frame or byte pattern; a hang fails the case, it is never rescued.
const LONG: Duration = Duration::from_secs(30);
/// `THREAD_CLOSE_S` as proposed by the design (7.3); G-W5 fixes or refutes it.
const THREAD_CLOSE: Duration = Duration::from_secs(2);

fn receipt(case: &str, fields: Value) {
    let Some(dir) = std::env::var_os("E5_RECEIPT_DIR") else {
        return;
    };
    let mut line =
        json!({"runtime":"rust-sidecar","platform":"windows","build":windows_build(),"case":case});
    line.as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(PathBuf::from(dir).join("rust-receipts.jsonl"))
        .unwrap();
    writeln!(file, "{line}").unwrap();
}

/// The third number of `ver` ("10.0.26100.x"), asked of the shell so no registry access is needed.
fn windows_build() -> u32 {
    let output = Command::new(std::env::var("ComSpec").unwrap())
        .args(["/d", "/c", "ver"])
        .output()
        .unwrap();
    String::from_utf8_lossy(&output.stdout)
        .split(|c: char| !c.is_ascii_digit() && c != '.')
        .find_map(|word| {
            let mut parts = word.split('.');
            (parts.next() == Some("10") && parts.next() == Some("0"))
                .then(|| parts.next()?.parse().ok())
                .flatten()
        })
        .unwrap_or(0)
}

struct Sidecar {
    child: Child,
    stdin: Option<ChildStdin>,
    frames: mpsc::Receiver<frame::Frame>,
    trace: std::cell::RefCell<Vec<String>>,
    next_id: u64,
    dir: PathBuf,
    ready: Value,
}

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("navide-sidecar-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn tag() -> u64 {
    frame::runtime_tag(GENERATION).unwrap()
}

fn start(name: &str) -> Sidecar {
    let dir = temp_dir(name);
    let mut child = Command::new(env!("CARGO_BIN_EXE_navide-cli-runtime"))
        .args([
            "--wire",
            "1",
            "--runtime-generation",
            GENERATION,
            "--owner-pid",
        ])
        .arg(std::process::id().to_string())
        .arg("--ownership-journal")
        .arg(dir.join("journal.jsonl"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    stdout.read_line(&mut line).unwrap();
    let ready: Value =
        serde_json::from_str(&line).unwrap_or_else(|e| panic!("not JSON ({e}): {line:?}"));
    let (send, frames) = mpsc::channel();
    std::thread::spawn(move || loop {
        let mut header = [0u8; 32];
        if stdout.read_exact(&mut header).is_err() {
            return;
        }
        let parsed = frame::parse_header(&header, Direction::RustToPython, tag())
            .unwrap_or_else(|e| panic!("bad frame header {e:?}: {header:02x?}"));
        let mut body = vec![0u8; parsed.body_len()];
        if stdout.read_exact(&mut body).is_err() {
            return;
        }
        if send
            .send(frame::decode_body(parsed, &body).unwrap())
            .is_err()
        {
            return;
        }
    });
    Sidecar {
        stdin: child.stdin.take(),
        child,
        frames,
        trace: std::cell::RefCell::new(Vec::new()),
        next_id: 0,
        dir,
        ready,
    }
}

impl Sidecar {
    fn send(&mut self, request_code: u16, meta: Value) -> u64 {
        self.next_id += 1;
        let bytes = frame::encode_raw(
            FrameType::Request,
            request_code,
            0,
            self.next_id,
            tag(),
            &serde_json::to_vec(&meta).unwrap(),
            b"",
        );
        let stdin = self.stdin.as_mut().expect("stdin already closed");
        stdin.write_all(&bytes).unwrap();
        stdin.flush().unwrap();
        self.next_id
    }

    fn recv_until(&self, what: &str, wanted: impl Fn(&frame::Frame) -> bool) -> frame::Frame {
        let deadline = Instant::now() + LONG;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            match self.frames.recv_timeout(left) {
                Ok(frame) if wanted(&frame) => return frame,
                Ok(other) => self.trace.borrow_mut().push(format!(
                    "{:?} code={} id={} {}",
                    other.frame_type, other.code, other.correlation_id, other.meta
                )),
                Err(error) => panic!(
                    "waiting for {what}: {error}; unmatched frames: {:#?}",
                    self.trace.borrow()
                ),
            }
        }
    }

    fn answer(&self, id: u64) -> frame::Frame {
        self.recv_until("an answer", |f| {
            f.correlation_id == id && !matches!(f.frame_type, FrameType::Event)
        })
    }

    fn session_event(&self, event: u16, sid: &str) -> frame::Frame {
        self.recv_until("a session event", |f| {
            f.code == event && f.meta["session_id"] == sid
        })
    }

    fn exit_status(&mut self) -> std::process::ExitStatus {
        let deadline = Instant::now() + LONG;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status;
            }
            assert!(Instant::now() < deadline, "the sidecar did not exit");
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

// ---- the pulled ends -----------------------------------------------------------------------------

fn pull(sidecar: &Child, value: u64) -> OwnedHandle {
    let mut mine: HANDLE = null_mut();
    // SAFETY: `Child` holds a process handle with PROCESS_DUP_HANDLE; the source value was announced.
    let ok = unsafe {
        DuplicateHandle(
            sidecar.as_raw_handle(),
            value as usize as HANDLE,
            GetCurrentProcess(),
            &mut mine,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    };
    assert!(
        ok != 0,
        "DuplicateHandle: {}",
        std::io::Error::last_os_error()
    );
    // SAFETY: DuplicateHandle returned an owned handle.
    unsafe { OwnedHandle::from_raw_handle(mine) }
}

fn peek(handle: &OwnedHandle) -> Option<u32> {
    let mut available = 0u32;
    // SAFETY: a live handle and a valid output pointer; nothing is consumed (no buffer).
    let ok = unsafe {
        PeekNamedPipe(
            handle.as_raw_handle(),
            null_mut(),
            0,
            null_mut(),
            &mut available,
            null_mut(),
        )
    };
    (ok != 0).then_some(available)
}

fn write_all(handle: &OwnedHandle, data: &[u8]) {
    let mut done = 0;
    while done < data.len() {
        let mut written = 0u32;
        // SAFETY: a live pipe write end and an in-bounds slice.
        let ok = unsafe {
            WriteFile(
                handle.as_raw_handle(),
                data[done..].as_ptr(),
                (data.len() - done) as u32,
                &mut written,
                null_mut(),
            )
        };
        assert!(ok != 0, "WriteFile: {}", std::io::Error::last_os_error());
        done += written as usize;
    }
}

/// One session as Python would hold it: the two pulled ends and a pump thread blocked in `ReadFile`.
struct Session {
    id: String,
    root_pid: u64,
    input: OwnedHandle,
    /// The output end, held open like Python's `py_out` until the session ends (the pump has its own).
    #[allow(dead_code)]
    output: OwnedHandle,
    pump: Option<JoinHandle<()>>,
    bytes: mpsc::Receiver<Vec<u8>>,
    seen: Vec<u8>,
}

fn start_pump(output: &OwnedHandle) -> (JoinHandle<()>, mpsc::Receiver<Vec<u8>>) {
    let (send, receive) = mpsc::channel();
    let mine = output.try_clone().unwrap();
    let pump = std::thread::spawn(move || loop {
        let mut buffer = vec![0u8; 65536];
        let mut got = 0u32;
        // SAFETY: a live pipe read end and a buffer of the stated length.
        let ok = unsafe {
            ReadFile(
                mine.as_raw_handle(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                &mut got,
                null_mut(),
            )
        };
        if ok == 0 || got == 0 {
            return; // broken pipe (the console closed) or cancelled
        }
        buffer.truncate(got as usize);
        if send.send(buffer).is_err() {
            return;
        }
    });
    (pump, receive)
}

impl Session {
    fn read_until(&mut self, needle: &str) {
        let deadline = Instant::now() + LONG;
        loop {
            if String::from_utf8_lossy(&self.seen).contains(needle) {
                return;
            }
            let left = deadline.saturating_duration_since(Instant::now());
            match self.bytes.recv_timeout(left) {
                Ok(chunk) => self.seen.extend(chunk),
                Err(error) => panic!(
                    "waiting for {needle:?}: {error}; saw {:?}",
                    String::from_utf8_lossy(&self.seen)
                ),
            }
        }
    }
}

fn comspec() -> String {
    std::env::var("ComSpec").unwrap()
}

fn create_args(sid: &str, argv: Vec<String>) -> Value {
    let env: serde_json::Map<String, Value> =
        std::env::vars().map(|(k, v)| (k, json!(v))).collect();
    json!({"session_id":sid,"session_generation":"g1","pane_id":format!("pane-{sid}"),
        "workspace_path":"w","cwd":std::env::temp_dir(),"argv":argv,"env":env,
        "rows":24,"cols":80,"agent_key":"terminal"})
}

fn shell() -> Vec<String> {
    vec![comspec(), "/d".into(), "/q".into(), "/k".into()]
}

impl Sidecar {
    /// CREATE, the `SESSION_OWNED` event and the answer; then the 7.4 checks, the pull, and `ADOPT`.
    fn open(&mut self, sid: &str, argv: Vec<String>) -> Session {
        let id = self.send(code::CREATE, create_args(sid, argv));
        let owned = self.recv_until("SESSION_OWNED", |f| {
            f.code == code::SESSION_OWNED && f.meta["session_id"] == sid
        });
        assert_eq!(
            owned.flags & frame::FLAG_HAS_FD,
            0,
            "Windows frames carry no handle"
        );
        assert_eq!(owned.meta["root"]["start_kind"], "windows-filetime");
        let created = self.answer(id);
        assert_eq!(
            created.frame_type as u8,
            FrameType::Response as u8,
            "{:?}",
            created.meta
        );
        let pty = &owned.meta["pty"];
        let (input, output) = (
            pty["input"].as_u64().unwrap(),
            pty["output"].as_u64().unwrap(),
        );
        assert_ne!(input, output);
        let (input, output) = (pull(&self.child, input), pull(&self.child, output));
        // 7.4: type, direction (Peek succeeds on the read end only), distinct objects.
        for end in [&input, &output] {
            // SAFETY: a live handle.
            assert_eq!(unsafe { GetFileType(end.as_raw_handle()) }, FILE_TYPE_PIPE);
        }
        assert!(
            peek(&output).is_some(),
            "PeekNamedPipe must succeed on the read end"
        );
        assert!(
            peek(&input).is_none(),
            "PeekNamedPipe must fail on the write end"
        );
        let adopt = self.send(
            code::ADOPT,
            json!({"session_id":sid,"session_generation":"g1"}),
        );
        assert_eq!(self.answer(adopt).meta["adopted"], true);
        let (pump, bytes) = start_pump(&output);
        Session {
            id: sid.to_owned(),
            root_pid: owned.meta["root"]["pid"].as_u64().unwrap(),
            input,
            output,
            pump: Some(pump),
            bytes,
            seen: Vec::new(),
        }
    }

    fn kill_and_clean(&mut self, session: &mut Session) -> Value {
        let kill = self.send(
            code::KILL,
            json!({"session_id":session.id,"session_generation":"g1","force":true}),
        );
        assert_eq!(self.answer(kill).meta["accepted"], true);
        self.session_event(code::SESSION_EXITED, &session.id);
        // 7.3: Python services the output, then tells Rust.
        let serving = self.send(
            code::TTY_SERVICING,
            json!({"session_id":session.id,"session_generation":"g1"}),
        );
        assert_eq!(self.answer(serving).meta["accepted"], true);
        self.session_event(code::SESSION_CLEANED, &session.id).meta
    }
}

// ---- cases -------------------------------------------------------------------------------------

#[test]
fn the_handshake_names_conpty_and_the_duplicate_handle_transfer() {
    let sidecar = start("ready");
    assert_eq!(sidecar.ready["event"], "ready");
    assert_eq!(sidecar.ready["pty_backend"], "conpty");
    assert_eq!(sidecar.ready["handoff"], "duplicate_handle");
    assert_eq!(sidecar.ready["platform"], "windows");
    assert_eq!(sidecar.ready["ownership_gate"], true);
}

#[test]
fn a_shell_round_trips_bytes_through_the_pulled_handles_and_a_kill_cleans_the_console() {
    let mut sidecar = start("roundtrip");
    let mut session = sidecar.open("s1", shell());
    write_all(
        &session.input,
        b"set /a NAVIDE_X=6*7\r\necho NAVIDE-%NAVIDE_X%-DONE\r\n",
    );
    session.read_until("NAVIDE-42-DONE");
    let resize = sidecar.send(
        code::RESIZE,
        json!({"session_id":"s1","session_generation":"g1","rows":30,"cols":100,"redraw":false}),
    );
    assert_eq!(sidecar.answer(resize).meta["applied"], true);
    let cleaned = sidecar.kill_and_clean(&mut session);
    assert_eq!(cleaned["cleanup_complete"], true, "{cleaned}");
    assert_eq!(cleaned["root_reaped"], true);
    // The pump reaches the end of the stream once the console closed: broken pipe.
    session.pump.take().unwrap().join().unwrap();
    receipt(
        "shell-roundtrip-kill",
        json!({"status":"pass","stage":"cleaned"}),
    );
}

/// G-W4 (24H2 or later only, by decision): `ClosePseudoConsole` on the close thread returns, after
/// `TTY_SERVICING` or after the output handle was closed, while the CLI saturates the output.
#[test]
fn closing_the_console_completes_under_a_saturating_cli_both_ways() {
    let build = windows_build();
    assert!(
        build >= 26100,
        "G-W4 is verified on Windows 11 24H2 (26100) or later; this is {build}"
    );
    let saturating = vec![
        comspec(),
        "/d".into(),
        "/q".into(),
        "/c".into(),
        "for /l %i in (1,1,2147483647) do @echo SATURATE-0123456789-0123456789-0123456789-%i"
            .into(),
    ];
    let mut timings = Vec::new();
    for (way, servicing) in [("servicing", true), ("closed-read-end", false)] {
        let mut sidecar = start("saturate");
        let sid = format!("sat-{way}");
        let id = sidecar.send(code::CREATE, create_args(&sid, saturating.clone()));
        let owned = sidecar.recv_until("SESSION_OWNED", |f| f.code == code::SESSION_OWNED);
        sidecar.answer(id);
        let input = pull(&sidecar.child, owned.meta["pty"]["input"].as_u64().unwrap());
        let output = pull(
            &sidecar.child,
            owned.meta["pty"]["output"].as_u64().unwrap(),
        );
        let adopt = sidecar.send(
            code::ADOPT,
            json!({"session_id":sid,"session_generation":"g1"}),
        );
        sidecar.answer(adopt);
        // Nobody reads yet: wait (bounded) until the pipe holds output, i.e. the CLI is saturating it.
        let deadline = Instant::now() + LONG;
        while peek(&output).unwrap_or(0) < 16 * 1024 {
            assert!(Instant::now() < deadline, "the CLI never produced output");
            std::thread::sleep(Duration::from_millis(5));
        }
        let kill = sidecar.send(
            code::KILL,
            json!({"session_id":sid,"session_generation":"g1","force":true}),
        );
        assert_eq!(sidecar.answer(kill).meta["accepted"], true);
        sidecar.session_event(code::SESSION_EXITED, &sid);
        let pump = if servicing {
            let (pump, _bytes) = start_pump(&output);
            Some(pump)
        } else {
            None
        };
        let started = Instant::now();
        let (name, message) = if servicing {
            (code::TTY_SERVICING, "servicing")
        } else {
            drop(output); // the closed read end is the gate
            (code::TTY_EOF, "closed read end")
        };
        let gate = sidecar.send(name, json!({"session_id":sid,"session_generation":"g1"}));
        assert_eq!(sidecar.answer(gate).meta["accepted"], true, "{message}");
        let cleaned = sidecar.session_event(code::SESSION_CLEANED, &sid).meta;
        let elapsed = started.elapsed();
        assert_eq!(cleaned["cleanup_complete"], true, "{way}: {cleaned}");
        if let Some(pump) = pump {
            pump.join().unwrap();
        }
        drop(input);
        timings.push(json!({"way":way,"close_to_cleaned_ms":elapsed.as_millis() as u64}));
    }
    receipt(
        "g-w4-close-under-saturation",
        json!({"status":"pass","stage":"cleaned","ways":timings}),
    );
}

/// W10 at the sidecar: bytes written to session A reach A's console and never B's, and the reverse.
#[test]
fn two_sessions_never_see_each_others_bytes() {
    let mut sidecar = start("isolation");
    let mut a = sidecar.open("iso-a", shell());
    let mut b = sidecar.open("iso-b", shell());
    write_all(
        &a.input,
        b"echo ISOLATION-A-%RANDOM%-MARK\r\nset /a A_N=1+1\r\necho A-RESULT-%A_N%\r\n",
    );
    write_all(
        &b.input,
        b"echo ISOLATION-B-%RANDOM%-MARK\r\nset /a B_N=2+2\r\necho B-RESULT-%B_N%\r\n",
    );
    a.read_until("A-RESULT-2");
    b.read_until("B-RESULT-4");
    let (a_text, b_text) = (
        String::from_utf8_lossy(&a.seen).into_owned(),
        String::from_utf8_lossy(&b.seen).into_owned(),
    );
    assert!(
        !a_text.contains("B-RESULT") && !a_text.contains("ISOLATION-B"),
        "A saw B's bytes"
    );
    assert!(
        !b_text.contains("A-RESULT") && !b_text.contains("ISOLATION-A"),
        "B saw A's bytes"
    );
    for session in [&mut a, &mut b] {
        let cleaned = sidecar.kill_and_clean(session);
        assert_eq!(cleaned["cleanup_complete"], true, "{cleaned}");
    }
    receipt(
        "w10-two-sessions-isolated",
        json!({"status":"pass","stage":"cleaned"}),
    );
}

fn thread_count(pid: u32) -> u32 {
    // SAFETY: a plain snapshot of system threads, closed below by its owner handle.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    assert!(snapshot != INVALID_HANDLE_VALUE);
    // SAFETY: the snapshot is owned by this function from here on.
    let snapshot = unsafe { OwnedHandle::from_raw_handle(snapshot) };
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..unsafe { std::mem::zeroed() }
    };
    let mut count = 0;
    // SAFETY: a live snapshot and a correctly sized entry.
    let mut more = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } != 0;
    while more {
        if entry.th32OwnerProcessID == pid {
            count += 1;
        }
        // SAFETY: as above.
        more = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } != 0;
    }
    count
}

fn working_set(process: &Child) -> u64 {
    let mut counters: PROCESS_MEMORY_COUNTERS = unsafe { std::mem::zeroed() };
    counters.cb = std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
    // SAFETY: a live process handle with query rights and a sized struct.
    let ok = unsafe { GetProcessMemoryInfo(process.as_raw_handle(), &mut counters, counters.cb) };
    assert!(ok != 0);
    counters.WorkingSetSize as u64
}

/// G-W5 (API level): N sessions each with a pump blocked in `ReadFile`; the sidecar's thread and
/// memory cost per session; then `CancelSynchronousIo` + join of every pump concurrently, racing the
/// sessions' own end, completes within `THREAD_CLOSE_S`.
#[test]
fn many_sessions_cost_bounded_threads_and_cancellation_joins_within_the_bound() {
    let count: usize = std::env::var("E5_SESSIONS")
        .ok()
        .and_then(|n| n.parse().ok())
        .unwrap_or(16);
    let mut sidecar = start("scale");
    let sidecar_pid = sidecar.child.id();
    let (threads_before, memory_before) = (thread_count(sidecar_pid), working_set(&sidecar.child));
    let mut sessions: Vec<Session> = (0..count)
        .map(|n| sidecar.open(&format!("scale-{n}"), shell()))
        .collect();
    let (threads_after, memory_after) = (thread_count(sidecar_pid), working_set(&sidecar.child));
    // Cancel every pump while the sessions are being killed: cancellation racing normal completion.
    let started = Instant::now();
    for session in &sessions {
        let pump = session.pump.as_ref().unwrap();
        // SAFETY: the pump's own thread handle, valid until joined.
        unsafe { CancelSynchronousIo(pump.as_raw_handle()) };
    }
    let kills: Vec<u64> = sessions
        .iter()
        .map(|s| {
            sidecar.send(
                code::KILL,
                json!({"session_id":s.id,"session_generation":"g1","force":true}),
            )
        })
        .collect();
    let mut worst_join = Duration::ZERO;
    for session in &mut sessions {
        let join_started = Instant::now();
        session.pump.take().unwrap().join().unwrap();
        worst_join = worst_join.max(join_started.elapsed());
    }
    let total_join = started.elapsed();
    assert!(
        worst_join < THREAD_CLOSE,
        "a pump joined after {worst_join:?}"
    );
    for id in kills {
        sidecar.answer(id);
    }
    for session in &sessions {
        sidecar.session_event(code::SESSION_EXITED, &session.id);
        let serving = sidecar.send(
            code::TTY_SERVICING,
            json!({"session_id":session.id,"session_generation":"g1"}),
        );
        sidecar.answer(serving);
    }
    for session in &sessions {
        let cleaned = sidecar
            .session_event(code::SESSION_CLEANED, &session.id)
            .meta;
        assert_eq!(cleaned["cleanup_complete"], true, "{cleaned}");
    }
    let per_session = |after: u32| f64::from(after.saturating_sub(threads_before)) / count as f64;
    receipt(
        "g-w5-scale-and-cancel",
        json!({"status":"pass","stage":"cleaned","sessions":count,
            "sidecar_threads_before":threads_before,"sidecar_threads_after":threads_after,
            "sidecar_threads_per_session":per_session(threads_after),
            "sidecar_working_set_delta_bytes":memory_after.saturating_sub(memory_before),
            "worst_pump_join_ms":worst_join.as_millis() as u64,
            "total_cancel_join_ms":total_join.as_millis() as u64,
            "thread_close_s_proposed":THREAD_CLOSE.as_secs()}),
    );
}

/// Uniform loss: the backend side of the channel closes; the sidecar ends every session and exits,
/// and the root is gone.
#[test]
fn closing_stdin_ends_every_session_and_the_sidecar() {
    let mut sidecar = start("loss");
    let session = sidecar.open("loss-1", shell());
    let root = session.root_pid as u32;
    drop(sidecar.stdin.take());
    let status = sidecar.exit_status();
    assert!(status.success(), "{status:?}");
    // SAFETY: opening by pid only to observe that this root no longer runs.
    let process = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, root) };
    if !process.is_null() {
        // SAFETY: a live handle opened above; zero timeout is a state query, not a wait.
        let state = unsafe { WaitForSingleObject(process, 0) };
        // SAFETY: closing the handle opened above.
        unsafe { windows_sys::Win32::Foundation::CloseHandle(process) };
        assert_eq!(state, WAIT_OBJECT_0, "the root outlived the sidecar");
    }
    receipt(
        "uniform-loss-stdin-eof",
        json!({"status":"pass","stage":"exited"}),
    );
}
