#![cfg(unix)]
//! The real sidecar binary over a real socketpair: handshake, framing errors and one session's
//! handoff (E1 H1, H5-style rejects, F3 sidecar side, P1/P11 smoke). The Python seam tests live in
//! `backend/tests`; these isolate the binary itself.
#[path = "../src/frame.rs"]
#[allow(dead_code)]
mod frame;

use frame::{code, Direction, FrameType};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read};
use std::os::fd::RawFd;
use std::path::PathBuf;
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const GENERATION: &str = "00112233445566778899aabbccddeeff";

struct Sidecar {
    /// Shared with the watchdog; every signal goes through this `Child`, which std refuses to signal once
    /// it has been reaped, so a recycled PID is never hit.
    child: Arc<Mutex<Child>>,
    sock: RawFd,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
    dir: PathBuf,
    /// Set on drop; the watchdog stops waiting once it is, and `Drop` joins it before the socket closes.
    finished: Arc<AtomicBool>,
    watchdog: Option<std::thread::JoinHandle<()>>,
    /// When the watchdog's bound started counting (`None`: not armed yet).
    armed: Arc<Mutex<Option<Instant>>>,
    /// Set if the watchdog had to fall back to killing the sidecar, i.e. the sidecar's own teardown failed.
    fallback_used: Arc<AtomicBool>,
}

/// A hard bound on every test that talks to a sidecar: `recvmsg` and `read_line` block with no
/// deadline of their own, so a sidecar that never answers would hang the test past the clock checks
/// in `recv_until`. After the bound the watchdog first shuts the control socket down (no PID is
/// involved): our blocked `recvmsg` returns end-of-file, and the sidecar, seeing its backend gone, ends
/// every session it launched by its own teardown and exits, which also ends a blocked `read_line`.
/// Only if the sidecar is still alive after `GRACE` is it killed, through the shared `Child`.
const WATCHDOG: Duration = Duration::from_secs(30);
const GRACE: Duration = Duration::from_secs(10);

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("navide-sidecar-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn start_with(name: &str, args: &[&str], journal: Option<PathBuf>) -> Sidecar {
    start_bounded(name, args, journal, WATCHDOG, true)
}

fn start_bounded(name: &str, args: &[&str], journal: Option<PathBuf>, bound: Duration, arm_now: bool) -> Sidecar {
    start_env(name, args, journal, bound, arm_now, &[])
}

/// `arm_now: false` leaves the watchdog idle until `Sidecar::arm`, so a test can count the bound from
/// the moment it deliberately stalls rather than from process start. `envs` is added to the sidecar's environment.
fn start_env(
    name: &str,
    args: &[&str],
    journal: Option<PathBuf>,
    bound: Duration,
    arm_now: bool,
    envs: &[(&str, &str)],
) -> Sidecar {
    let dir = temp_dir(name);
    let mut pair = [0i32; 2];
    assert_eq!(
        unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_STREAM, 0, pair.as_mut_ptr()) },
        0
    );
    // Our end must not leak into the child, or EOF could never be seen.
    unsafe { libc::fcntl(pair[0], libc::F_SETFD, libc::FD_CLOEXEC) };
    let journal = journal.unwrap_or_else(|| dir.join("journal.jsonl"));
    let mut command = Command::new(env!("CARGO_BIN_EXE_navide-cli-runtime"));
    command
        .args([
            "--wire",
            "2",
            "--runtime-generation",
            GENERATION,
            "--owner-pid",
        ])
        .arg(std::process::id().to_string())
        .arg("--ownership-journal")
        .arg(&journal)
        .arg("--control-fd")
        .arg(pair[1].to_string())
        .args(args)
        .envs(envs.iter().copied())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    let mut child = command.spawn().unwrap();
    unsafe { libc::close(pair[1]) };
    let stdout = BufReader::new(child.stdout.take().unwrap());
    let finished = Arc::new(AtomicBool::new(false));
    let child = Arc::new(Mutex::new(child));
    let armed = Arc::new(Mutex::new(if arm_now { Some(Instant::now()) } else { None }));
    let fallback_used = Arc::new(AtomicBool::new(false));
    let (flag, shared, sock) = (finished.clone(), child.clone(), pair[0]);
    let (armed_at, fallback) = (armed.clone(), fallback_used.clone());
    let watchdog = std::thread::spawn(move || {
        // Wait for the arm instant plus the bound; an unarmed watchdog waits for the arm.
        loop {
            if flag.load(Ordering::SeqCst) {
                return;
            }
            if let Some(at) = *armed_at.lock().unwrap() {
                if at.elapsed() >= bound {
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        // `Drop` joins this thread before it closes `sock`, so the descriptor is still ours here.
        unsafe { libc::shutdown(sock, libc::SHUT_RDWR) };
        let deadline = Instant::now() + GRACE;
        while Instant::now() < deadline {
            if flag.load(Ordering::SeqCst) {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        fallback.store(true, Ordering::SeqCst);
        let _ = shared.lock().unwrap().kill();
    });
    Sidecar {
        child,
        sock: pair[0],
        stdout,
        next_id: 0,
        dir,
        finished,
        watchdog: Some(watchdog),
        armed,
        fallback_used,
    }
}

fn start(name: &str) -> Sidecar {
    start_with(name, &[], None)
}

impl Sidecar {
    /// Start counting the watchdog's bound now and return that instant.
    fn arm(&self) -> Instant {
        let now = Instant::now();
        *self.armed.lock().unwrap() = Some(now);
        now
    }

    fn line(&mut self) -> Value {
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap_or_else(|e| panic!("not JSON ({e}): {line:?}"))
    }

    fn tag(&self) -> u64 {
        frame::runtime_tag(GENERATION).unwrap()
    }

    fn send_raw(&self, bytes: &[u8]) {
        let sent = unsafe { libc::send(self.sock, bytes.as_ptr().cast(), bytes.len(), 0) };
        assert_eq!(sent as usize, bytes.len());
    }

    fn send(&mut self, request_code: u16, meta: Value) -> u64 {
        self.next_id += 1;
        let bytes = frame::encode_raw(
            FrameType::Request,
            request_code,
            0,
            self.next_id,
            self.tag(),
            &serde_json::to_vec(&meta).unwrap(),
            b"",
        );
        self.send_raw(&bytes);
        self.next_id
    }

    /// One frame and the descriptor attached to it, if any.
    fn recv(&self) -> Option<(frame::Frame, Option<RawFd>)> {
        let mut header = [0u8; 32];
        let mut descriptor = None;
        let mut got = 0;
        while got < 32 {
            let (n, fd) = recvmsg(self.sock, &mut header[got..]);
            if n == 0 {
                return None;
            }
            if fd.is_some() {
                assert_eq!(
                    got, 0,
                    "a descriptor must arrive with the first byte of its frame"
                );
                descriptor = fd;
            }
            got += n;
        }
        let parsed = frame::parse_header(&header, Direction::RustToPython, self.tag()).unwrap();
        let mut body = vec![0u8; parsed.body_len()];
        let mut got = 0;
        while got < body.len() {
            let (n, fd) = recvmsg(self.sock, &mut body[got..]);
            assert!(n > 0 && fd.is_none());
            got += n;
        }
        Some((frame::decode_body(parsed, &body).unwrap(), descriptor))
    }

    fn recv_until(&self, wanted: impl Fn(&frame::Frame) -> bool) -> (frame::Frame, Option<RawFd>) {
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            assert!(Instant::now() < deadline, "timed out waiting for a frame");
            let (frame, fd) = self.recv().expect("the sidecar closed the channel");
            if wanted(&frame) {
                return (frame, fd);
            }
        }
    }

    fn exit_status(&mut self) -> std::process::ExitStatus {
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Some(status) = self.child.lock().unwrap().try_wait().unwrap() {
                return status;
            }
            assert!(Instant::now() < deadline, "the sidecar did not exit");
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        self.finished.store(true, Ordering::SeqCst);
        if let Some(watchdog) = self.watchdog.take() {
            let _ = watchdog.join();
        }
        let mut child = self.child.lock().unwrap();
        let _ = child.kill();
        let _ = child.wait();
        drop(child);
        unsafe { libc::close(self.sock) };
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn recvmsg(sock: RawFd, buffer: &mut [u8]) -> (usize, Option<RawFd>) {
    let mut iov = libc::iovec {
        iov_base: buffer.as_mut_ptr().cast(),
        iov_len: buffer.len(),
    };
    let space = unsafe { libc::CMSG_SPACE(4) } as usize;
    let mut control = vec![0u8; space];
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = space as _;
    let n = unsafe { libc::recvmsg(sock, &mut message, 0) };
    assert!(n >= 0, "recvmsg: {}", std::io::Error::last_os_error());
    assert_eq!(message.msg_flags & libc::MSG_CTRUNC, 0);
    let mut fd = None;
    unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        if !header.is_null() && (*header).cmsg_type == libc::SCM_RIGHTS {
            let mut value = -1i32;
            std::ptr::copy_nonoverlapping(
                libc::CMSG_DATA(header),
                (&mut value as *mut i32).cast::<u8>(),
                4,
            );
            fd = Some(value);
        }
    }
    (n as usize, fd)
}

#[cfg(target_os = "macos")]
fn slave_name(master: RawFd) -> String {
    let mut name = [0 as libc::c_char; 128];
    assert_eq!(
        unsafe { libc::ioctl(master, libc::TIOCPTYGNAME as _, name.as_mut_ptr()) },
        0
    );
    unsafe { std::ffi::CStr::from_ptr(name.as_ptr()) }
        .to_string_lossy()
        .into_owned()
}

#[cfg(target_os = "linux")]
fn slave_name(master: RawFd) -> String {
    let mut number: libc::c_uint = 0;
    assert_eq!(
        unsafe { libc::ioctl(master, libc::TIOCGPTN as _, &mut number) },
        0
    );
    format!("/dev/pts/{number}")
}

fn create_meta(dir: &std::path::Path, argv: &[&str], session: &str) -> Value {
    json!({"session_id":session,"session_generation":"ffeeddccbbaa99887766554433221100",
        "pane_id":"pane-1","workspace_path":dir,"cwd":dir,
        "launch":{"shape":"direct","form":"list","elements":argv,"words":[]},
        "env":{"PATH":"/usr/bin:/bin","TERM":"xterm"},"rows":24,"cols":80,"agent_key":""})
}

// ---------------------------------------------------------------------------- handshake

#[test]
fn ready_is_the_only_stdout_line_and_advertises_the_frozen_limits() {
    let mut sidecar = start("ready");
    let ready = sidecar.line();
    assert_eq!(ready["event"], "ready");
    assert_eq!(ready["protocol"], "navide-acr");
    assert_eq!(ready["wire"], 2);
    assert_eq!(ready["runtime"], "navide-cli-runtime");
    assert_eq!(ready["runtime_generation"], GENERATION);
    assert_eq!(ready["pid"].as_u64().unwrap(), sidecar.child.lock().unwrap().id() as u64);
    assert_eq!(
        ready["owner_pid"].as_u64().unwrap(),
        std::process::id() as u64
    );
    assert_eq!(ready["handoff"], "scm_rights");
    assert_eq!(ready["pty_backend"], "posix");
    assert_eq!(ready["ownership_gate"], true);
    assert!(!ready["version"].as_str().unwrap().is_empty());
    let limits = &ready["limits"];
    for (key, value) in [
        ("max_meta_p2r", 4_194_304u64),
        ("max_blob_p2r", 1_048_576),
        ("max_frame_r2p", 65_536),
        ("max_observation_bytes", 8_388_608),
        ("max_observation_parts", 128),
        ("observe_inflight", 4),
        ("max_outbound_bytes", 4_194_304),
        ("max_outbound_frames", 256),
        ("max_inbound_bytes", 16_777_216),
        ("request_ms", 15_000),
        ("create_answer_ms", 20_000),
        ("cleanup_ms", 8_000),
        ("ping_ms", 5_000),
        ("kill_escalation_ms", 1_000),
        ("eof_reap_ms", 1_000),
    ] {
        assert_eq!(limits[key], value, "{key}");
    }
    assert_eq!(
        limits["ownership_snapshot_ms"],
        json!({"young":5000,"steady":30000,"young_window_ms":30000})
    );
    // Nothing else is ever written to stdout: the pipe stays quiet until the sidecar exits.
    let id = sidecar.send(code::PING, json!({}));
    let (pong, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Response);
    assert_eq!(
        (pong.code, pong.correlation_id, pong.meta["pong"].clone()),
        (code::PING, id, json!(true))
    );
    assert!(pong.meta["bookkeeping"].is_object());
    // Closing the channel is the backend-death signal: the sidecar cleans up and exits 0.
    unsafe { libc::shutdown(sidecar.sock, libc::SHUT_RDWR) };
    assert!(sidecar.exit_status().success());
    let mut rest = Vec::new();
    sidecar.stdout.read_to_end(&mut rest).unwrap();
    assert!(rest.is_empty(), "stdout carried bytes after the ready line");
}

fn failed_start(args: &[&str], wire_arg: Option<&str>) -> (Value, std::process::ExitStatus) {
    let dir = temp_dir("failed");
    let mut command = Command::new(env!("CARGO_BIN_EXE_navide-cli-runtime"));
    command
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .stdin(Stdio::null());
    if let Some(wire) = wire_arg {
        command.args(["--wire", wire]);
    }
    let output = command.output().unwrap();
    let _ = std::fs::remove_dir_all(dir);
    let line: Value =
        serde_json::from_slice(output.stdout.split(|b| *b == b'\n').next().unwrap()).unwrap();
    (line, output.status)
}

#[test]
fn an_incompatible_wire_unknown_arguments_and_a_missing_journal_fail_startup_with_a_fatal_line() {
    let common = [
        "--runtime-generation",
        GENERATION,
        "--owner-pid",
        "1",
        "--control-fd",
        "3",
    ];
    let (fatal, status) = failed_start(
        &[&common[..], &["--ownership-journal", "/tmp/j"]].concat(),
        Some("1"), // a wire-1 request of a wire-2 sidecar is refused, with no downgrade
    );
    assert_eq!(fatal["event"], "fatal");
    assert_eq!(fatal["error"]["code"], "INCOMPATIBLE_WIRE");
    assert!(!status.success());
    let (fatal, status) = failed_start(
        &[&common[..], &["--ownership-journal", "/tmp/j", "--bogus"]].concat(),
        Some("2"),
    );
    assert_eq!(fatal["error"]["code"], "STARTUP_FAILED");
    assert!(!status.success());
    let (fatal, _) = failed_start(&common, Some("2"));
    assert_eq!(
        fatal["error"]["code"], "STARTUP_FAILED",
        "the journal is mandatory in wire v2"
    );
    let (fatal, _) = failed_start(
        &[
            "--wire",
            "2",
            "--runtime-generation",
            "short",
            "--owner-pid",
            "1",
            "--ownership-journal",
            "/tmp/j",
            "--control-fd",
            "3",
        ],
        None,
    );
    assert_eq!(fatal["error"]["code"], "STARTUP_FAILED");
}

// -------------------------------------------------------------------------- F3 (sidecar)

#[test]
fn a_malformed_frame_from_python_is_answered_with_fatal_and_ends_the_sidecar() {
    for (name, mutate) in [
        (
            "bad magic",
            (|b: &mut Vec<u8>| b[0] = 0) as fn(&mut Vec<u8>),
        ),
        ("bad version", |b| b[2] = 9),
        ("unknown code", |b| {
            b[4] = 0x7f;
        }),
        ("reserved flag", |b| b[6] = 0x80),
        ("wrong runtime tag", |b| b[16] ^= 0xff),
    ] {
        let mut sidecar = start("fatal");
        sidecar.line();
        let mut bytes = frame::encode_raw(
            FrameType::Request,
            code::PING,
            0,
            1,
            sidecar.tag(),
            b"{}",
            b"",
        );
        mutate(&mut bytes);
        sidecar.send_raw(&bytes);
        let (fatal, _) = sidecar.recv_until(|f| f.code == code::FATAL);
        assert_eq!(fatal.meta["code"], "PROTOCOL_ERROR", "{name}");
        assert!(!sidecar.exit_status().success(), "{name}");
    }
}

#[test]
fn a_zero_duplicate_or_regressing_request_id_is_a_protocol_error() {
    let mut sidecar = start("ids");
    sidecar.line();
    sidecar.send(code::PING, json!({})); // id 1
    sidecar.recv_until(|f| f.frame_type == FrameType::Response);
    let again = frame::encode_raw(
        FrameType::Request,
        code::PING,
        0,
        1,
        sidecar.tag(),
        b"{}",
        b"",
    );
    sidecar.send_raw(&again);
    let (fatal, _) = sidecar.recv_until(|f| f.code == code::FATAL);
    assert!(fatal.meta["message"]
        .as_str()
        .unwrap()
        .starts_with("correlation"));
    assert!(!sidecar.exit_status().success());
}

// ----------------------------------------------------------------------- one session's handoff

#[test]
fn a_created_session_hands_over_a_master_bound_to_its_own_frame_then_hangs_up_on_kill() {
    let mut sidecar = start("handoff");
    sidecar.line();
    let dir = sidecar.dir.clone();
    let id = sidecar.send(
        code::CREATE,
        create_meta(&dir, &["/bin/cat"], "session-one"),
    );
    let (owned, fd) = sidecar.recv_until(|f| f.code == code::SESSION_OWNED);
    let master = fd.expect("SESSION_OWNED must carry the master");
    assert_eq!(owned.flags & frame::FLAG_HAS_FD, frame::FLAG_HAS_FD);
    assert_eq!(owned.meta["create_id"], id);
    assert_eq!(owned.meta["session_id"], "session-one");
    assert_eq!(owned.meta["pane_id"], "pane-1");
    assert_eq!(owned.meta["root"]["start_kind"], "posix-lstart");
    // The descriptor is the master of exactly the tty the frame names (E1 6.4 check 2).
    assert_eq!(
        slave_name(master),
        owned.meta["pty"]["tty"].as_str().unwrap()
    );
    let flags = unsafe { libc::fcntl(master, libc::F_GETFL) };
    assert!(
        flags & libc::O_NONBLOCK != 0,
        "the master arrives non-blocking"
    );
    let (response, none) =
        sidecar.recv_until(|f| f.frame_type == FrameType::Response && f.code == code::CREATE);
    assert!(none.is_none());
    assert_eq!(response.correlation_id, id);
    assert_eq!(response.meta["state"], "owned");
    assert_eq!(response.meta["root"], owned.meta["root"]);
    // Bytes written to the handed-off master reach the CLI and come back (cat on the tty).
    let deadline = Instant::now() + Duration::from_secs(10);
    unsafe { libc::write(master, b"hello\n".as_ptr().cast(), 6) };
    let mut seen = Vec::new();
    while !String::from_utf8_lossy(&seen).contains("hello") {
        assert!(
            Instant::now() < deadline,
            "no echo: {:?}",
            String::from_utf8_lossy(&seen)
        );
        let mut chunk = [0u8; 256];
        let n = unsafe { libc::read(master, chunk.as_mut_ptr().cast(), chunk.len()) };
        if n > 0 {
            seen.extend_from_slice(&chunk[..n as usize]);
        } else {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    // KILL: accepted first, then HANGUP (no declared graceful shutdown), EXITED, CLEANED.
    let kill = sidecar.send(
        code::KILL,
        json!({"session_id":"session-one",
        "session_generation":"ffeeddccbbaa99887766554433221100","force":true}),
    );
    let mut order = Vec::new();
    let mut cleaned = None;
    while cleaned.is_none() {
        let (frame, _) = sidecar.recv().expect("channel closed");
        order.push(frame.code);
        if frame.code == code::SESSION_CLEANED {
            cleaned = Some(frame);
        } else if frame.frame_type == FrameType::Response {
            assert_eq!((frame.code, frame.correlation_id), (code::KILL, kill));
            assert_eq!(frame.meta["accepted"], true);
        }
    }
    assert_eq!(
        order[0],
        code::KILL,
        "the acknowledgement precedes every outcome event"
    );
    assert!(order.contains(&code::SESSION_HANGUP) && order.contains(&code::SESSION_EXITED));
    let cleaned = cleaned.unwrap();
    assert_eq!(cleaned.meta["cleanup_complete"], true);
    assert_eq!(cleaned.meta["root_reaped"], true);
    assert_eq!(cleaned.meta["survivors"], json!([]));
    unsafe { libc::close(master) };
}

// ---------------------------------------------------------------------------- the time bound itself

const SHORT_BOUND: Duration = Duration::from_millis(600);

/// Create a `/bin/cat` session and return its root pid and the master (kept open, so cat stays alive).
fn live_session(sidecar: &mut Sidecar) -> (libc::pid_t, RawFd) {
    let dir = sidecar.dir.clone();
    sidecar.send(code::CREATE, create_meta(&dir, &["/bin/cat"], "session-bound"));
    let (owned, fd) = sidecar.recv_until(|f| f.code == code::SESSION_OWNED);
    sidecar.recv_until(|f| f.frame_type == FrameType::Response && f.code == code::CREATE);
    let pid = owned.meta["root"]["pid"].as_i64().expect("the owned root names its pid") as libc::pid_t;
    (pid, fd.expect("SESSION_OWNED carries the master"))
}

fn process_exists(pid: libc::pid_t) -> bool {
    if unsafe { libc::kill(pid, 0) } == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

/// After a bounded failure the sidecar has exited and the session it launched (its own session, so a
/// kill of the sidecar alone would not reach it) is gone.
/// Scheduling slack on top of the bound; the 10 s grace (a fallback kill) is never an acceptable release.
const ALLOWANCE: Duration = Duration::from_millis(500);

/// The root of a launched session leads a session of its own (`setsid` in the sidecar's spawn), which is
/// why killing the sidecar alone would not reach it.
fn assert_own_session(pid: libc::pid_t) {
    assert!(process_exists(pid), "the session must be alive before the stall");
    assert_eq!(unsafe { libc::getsid(pid) }, pid, "the live root must lead its own session");
}

/// The blocked call was released by the watchdog's socket shutdown (not earlier, not by the fallback),
/// the sidecar then ended by its own teardown with a success status, and the session it launched is gone.
fn assert_released_cleanly(sidecar: &mut Sidecar, pid: libc::pid_t, armed: Instant, released: Duration) {
    assert!(released >= SHORT_BOUND, "released after {released:?}, before the bound: not the watchdog");
    assert!(released < SHORT_BOUND + ALLOWANCE, "released after {released:?}, outside the bound");
    let status = sidecar.exit_status();
    assert!(
        !sidecar.fallback_used.load(Ordering::SeqCst),
        "the sidecar had to be killed ({:?} after arming)",
        armed.elapsed()
    );
    assert!(status.success(), "the sidecar did not end cleanly by its own teardown: {status:?}");
    let deadline = Instant::now() + Duration::from_secs(5);
    while process_exists(pid) {
        assert!(Instant::now() < deadline, "the launched session {pid} survived the sidecar");
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn a_stalled_recvmsg_is_released_within_the_bound_and_leaves_no_owned_process() {
    let mut sidecar = start_bounded("stall-recv", &[], None, SHORT_BOUND, false);
    sidecar.line();
    let (pid, master) = live_session(&mut sidecar);
    assert_own_session(pid);
    let armed = sidecar.arm();
    // Nothing more is coming: the blocking receive can only return because the watchdog released it.
    while sidecar.recv().is_some() {}
    let released = armed.elapsed();
    assert_released_cleanly(&mut sidecar, pid, armed, released);
    unsafe { libc::close(master) };
}

#[test]
fn a_stalled_read_line_is_released_within_the_bound_and_leaves_no_owned_process() {
    let mut sidecar = start_bounded("stall-line", &[], None, SHORT_BOUND, false);
    sidecar.line();
    let (pid, master) = live_session(&mut sidecar);
    assert_own_session(pid);
    let armed = sidecar.arm();
    let mut line = String::new();
    // The sidecar writes nothing further to stdout; the read ends only when the sidecar has gone.
    let read = sidecar.stdout.read_line(&mut line).unwrap();
    let released = armed.elapsed();
    assert_eq!(read, 0, "unexpected stdout line {line:?}");
    assert_released_cleanly(&mut sidecar, pid, armed, released);
    unsafe { libc::close(master) };
}

/// A structured CREATE for a vendor subject (`goose` has no Rust observer).
fn vendor_create_meta(dir: &std::path::Path, agent: &str, session: &str) -> Value {
    json!({"session_id":session,"session_generation":"ffeeddccbbaa99887766554433221100",
        "pane_id":"pane-1","workspace_path":dir,"cwd":dir,
        "launch":{"shape":"wrapped","shell":"/bin/sh","flags":["-c"],"words":[{"lit":"exec /bin/cat"}]},
        "env":{"PATH":"/usr/bin:/bin","TERM":"xterm"},"rows":24,"cols":80,"agent_key":agent})
}

#[test]
fn a_vendor_without_a_rust_observer_still_launches_and_stays_unobservable() {
    let mut sidecar = start("unobserved-vendor");
    sidecar.line();
    let dir = sidecar.dir.clone();
    // MCode is the one registered vendor with no Python-baseline observer, so it stays unobservable.
    sidecar.send(code::CREATE, vendor_create_meta(&dir, "mcode", "session-mcode"));
    // Either outcome ends the wait, so a refusal fails the test at once instead of hanging it.
    let (owned, fd) =
        sidecar.recv_until(|f| f.code == code::SESSION_OWNED || f.frame_type == FrameType::Error);
    assert_eq!(
        owned.code,
        code::SESSION_OWNED,
        "the create was refused: {}",
        owned.meta
    );
    let master = fd.expect("SESSION_OWNED must carry the master");
    assert_eq!(owned.meta["argv_source"], "rust");
    assert_eq!(owned.meta["session_id"], "session-mcode");
    unsafe { libc::close(master) };
    // Launching does not make it observable: the observer roster is separate from admission.
    sidecar.send(
        code::OBSERVE,
        json!({"vendor":"mcode","session_id":"session-mcode",
            "session_generation":"ffeeddccbbaa99887766554433221100"}),
    );
    let (refused, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
    assert_eq!(refused.meta["code"], "observer not integrated");
    assert_eq!(refused.meta["effect"], "none");
}

#[test]
fn an_agent_that_is_neither_a_vendor_nor_a_shell_is_still_refused() {
    let mut sidecar = start("unknown-agent");
    sidecar.line();
    let dir = sidecar.dir.clone();
    sidecar.send(code::CREATE, vendor_create_meta(&dir, "no-such-vendor", "session-unknown"));
    let (refused, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
    // The composition table refuses it before any spawn (compose::admit -> Composition).
    assert_eq!(refused.meta["code"], "BAD_REQUEST", "{}", refused.meta);
    assert_eq!(refused.meta["stage"], "composition", "{}", refused.meta);
    assert_eq!(refused.meta["effect"], "none");
}

#[test]
fn a_shape_this_build_does_not_know_is_refused_by_the_composition_table() {
    // What an older wire-2 sidecar answers to the `credential` shape (delta 10.1): a bounded refusal
    // before any spawn, never a silent reinterpretation. Here the unknown shape stands for it.
    let mut sidecar = start("unknown-shape");
    sidecar.line();
    let dir = sidecar.dir.clone();
    let mut meta = vendor_create_meta(&dir, "", "session-shape");
    meta["launch"] = json!({"shape": "no-such-shape", "words": []});
    sidecar.send(code::CREATE, meta);
    let (refused, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
    assert_eq!(refused.meta["code"], "BAD_REQUEST", "{}", refused.meta);
    assert_eq!(refused.meta["stage"], "composition", "{}", refused.meta);
    assert_eq!(refused.meta["effect"], "none");
}

#[test]
fn an_unwritable_journal_refuses_the_create_and_the_target_never_runs() {
    let ran = std::env::temp_dir().join(format!("navide-i-ran-{}", std::process::id()));
    let _ = std::fs::remove_file(&ran);
    let mut sidecar = start_with(
        "journal",
        &[],
        Some(PathBuf::from("/nonexistent-navide-dir/j.jsonl")),
    );
    sidecar.line();
    let dir = sidecar.dir.clone();
    let script = format!("touch {}", ran.display());
    let id = sidecar.send(
        code::CREATE,
        create_meta(&dir, &["/bin/sh", "-c", &script], "session-journal"),
    );
    let (error, fd) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
    assert!(fd.is_none());
    assert_eq!(error.correlation_id, id);
    assert_eq!(error.meta["code"], "OWNERSHIP_JOURNAL_FAILED");
    assert_eq!(error.meta["effect"], "none");
    std::thread::sleep(Duration::from_millis(300));
    assert!(
        !ran.exists(),
        "the gated target ran although its identity was never durable"
    );
    // No SESSION_OWNED was queued before the refusal.
    let id = sidecar.send(code::PING, json!({}));
    loop {
        let (frame, _) = sidecar.recv().unwrap();
        assert_ne!(frame.code, code::SESSION_OWNED);
        if frame.correlation_id == id {
            break;
        }
    }
}

#[test]
fn a_create_whose_worst_case_owned_frame_cannot_fit_is_refused_before_any_process_exists() {
    let ran = std::env::temp_dir().join(format!("navide-i-ran-big-{}", std::process::id()));
    let _ = std::fs::remove_file(&ran);
    let mut sidecar = start("big");
    sidecar.line();
    let dir = sidecar.dir.clone();
    let mut meta = create_meta(
        &dir,
        &["/bin/sh", "-c", &format!("touch {}", ran.display())],
        "session-big",
    );
    meta["workspace_path"] = json!("/w".repeat(2100));
    sidecar.send(code::CREATE, meta);
    let (error, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
    assert_eq!(
        (error.meta["code"].as_str(), error.meta["stage"].as_str()),
        (Some("BAD_REQUEST"), Some("handoff_size"))
    );
    assert_eq!(error.meta["effect"], "none");
    std::thread::sleep(Duration::from_millis(200));
    assert!(!ran.exists());
}

/// Descriptive cost probe, not a gate (`cargo test -- --ignored --nocapture idle_cost`): the sidecar's CPU ticks
/// over an idle window with `NAVIDE_PROBE_SHELLS` idle sessions, and the `terminal.create` round trips.
#[cfg(target_os = "linux")]
#[test]
#[ignore]
fn idle_cost_probe() {
    let env = |name: &str, default: u64| {
        std::env::var(name)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(default)
    };
    let (shells, window) = (env("NAVIDE_PROBE_SHELLS", 4), env("NAVIDE_PROBE_SECONDS", 10));
    // Counters exist only in a `fault-hooks` build, which is never the scored one.
    let stats_path = std::env::temp_dir().join(format!("navide-probe-stats-{}.json", std::process::id()));
    let stats_arg = stats_path.to_string_lossy().into_owned();
    let envs: Vec<(&str, &str)> = if cfg!(feature = "fault-hooks") {
        vec![("NAVIDE_SCAN_STATS_FILE", stats_arg.as_str())]
    } else {
        Vec::new()
    };
    let mut sidecar = start_env("idle-cost", &[], None, WATCHDOG, true, &envs);
    sidecar.line();
    let pid = sidecar.child.lock().unwrap().id();
    let ticks = || {
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
        let fields: Vec<_> = stat[stat.rfind(") ").unwrap() + 2..].split(' ').collect();
        fields[11].parse::<u64>().unwrap() + fields[12].parse::<u64>().unwrap()
    };
    let dir = sidecar.dir.clone();
    // Unrelated processes, so /proc is as crowded as on a busy host (`NAVIDE_PROBE_NOISE`).
    let mut noise: Vec<_> = (0..env("NAVIDE_PROBE_NOISE", 0))
        .map(|_| {
            std::process::Command::new("/bin/sleep")
                .arg("600")
                .spawn()
                .unwrap()
        })
        .collect();
    let mut masters = Vec::new();
    let mut creates = Vec::new();
    for index in 0..shells {
        let started = Instant::now();
        sidecar.send(
            code::CREATE,
            create_meta(&dir, &["/bin/cat"], &format!("probe-{index}")),
        );
        let (_, fd) = sidecar.recv_until(|f| f.code == code::SESSION_OWNED);
        sidecar.recv_until(|f| f.frame_type == FrameType::Response && f.code == code::CREATE);
        creates.push(started.elapsed().as_micros());
        masters.push(fd.unwrap());
    }
    // The decider divides CPU seconds by the sampled phase duration, so the tick rate is read at run time and the
    // window is measured on the monotonic clock around the sleep, never assumed to be `window`.
    // SAFETY: sysconf only reads an immutable system constant.
    let clk_tck = unsafe { libc::sysconf(libc::_SC_CLK_TCK) } as u64;
    let before = ticks();
    let started = Instant::now();
    std::thread::sleep(Duration::from_secs(window));
    let used = ticks() - before;
    let elapsed = started.elapsed();
    let cpu_pct = used as f64 / clk_tck as f64 / elapsed.as_secs_f64() * 100.0;
    println!(
        "probe shells={shells} noise={} window_s={window} elapsed_ns={} clk_tck={clk_tck} sidecar_ticks={used} cpu_pct={cpu_pct:.4} exceed={} create_us={creates:?}",
        noise.len(),
        elapsed.as_nanos(),
        cpu_pct > 0.10
    );
    #[cfg(feature = "fault-hooks")]
    if let Ok(text) = std::fs::read_to_string(&stats_path) {
        let stats: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
        println!(
            "counters scans_scoped={} scans_full={} fallbacks={} inspected_pids={} due_scans_skipped={}",
            stats["scans_scoped"], stats["scans_full"], stats["fallbacks"], stats["inspected_pids"], stats["due_scans_skipped"]
        );
    }
    for child in &mut noise {
        let _ = child.kill();
        let _ = child.wait();
    }
    for master in masters {
        unsafe { libc::close(master) };
    }
}

// ---------------------------------------------------------------------------- Linux scoped enumeration
//
// Rows R-1 to R-11 of the design (`linux-scoped-enum/design.md` section 8). The counters file exists only in a
// `fault-hooks` build (`NAVIDE_SCAN_STATS_FILE`); a row that needs it is compiled only there.

#[cfg(target_os = "linux")]
#[cfg_attr(not(feature = "fault-hooks"), allow(dead_code))]
mod scoped {
    use super::*;
    use std::collections::HashSet;

    /// Process shapes a shell cannot make: a leader that `pthread_exit`s over live children, a non-leader thread
    /// that `exec`s over and over (`de_thread`), and workers whose leaders exit at random moments.
    const HELPER_C: &str = r#"
#define _GNU_SOURCE
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static void record(const char *path, int pid) {
  FILE *f = fopen(path, "a");
  if (f) { fprintf(f, "%d\n", pid); fclose(f); }
}
static pid_t sleeper(void) {
  pid_t p = fork();
  if (p == 0) { for (;;) pause(); }
  return p;
}
static void *idle(void *arg) { (void)arg; for (;;) pause(); return 0; }
static void *exec_thread(void *arg) { char **argv = arg; execv(argv[0], argv); _exit(127); return 0; }
int main(int argc, char **argv) {
  const char *mode = argc > 1 ? argv[1] : "";
  int n = argc > 2 ? atoi(argv[2]) : 0;
  const char *out = argc > 3 ? argv[3] : "/dev/null";
  pthread_t t;
  if (!strcmp(mode, "leader-exit")) {
    for (int i = 0; i < n; i++) record(out, sleeper());
    pthread_create(&t, 0, idle, 0);
    pthread_exit(0);
  }
  if (!strcmp(mode, "exec-loop")) {
    if (argc <= 4) { for (int i = 0; i < n; i++) record(out, sleeper()); }
    static char *next[6];
    next[0] = argv[0]; next[1] = "exec-loop"; next[2] = argv[2]; next[3] = argv[3]; next[4] = "again"; next[5] = 0;
    pthread_create(&t, 0, exec_thread, next);
    for (;;) pause();
  }
  if (!strcmp(mode, "rounds")) {
    for (int i = 0; i < n; i++) {
      if (fork() == 0) {
        record(out, sleeper());
        pthread_create(&t, 0, idle, 0);
        pthread_exit(0);
      }
      usleep(3000);
    }
    for (;;) pause();
  }
  if (!strcmp(mode, "workers")) {
    for (int i = 0; i < n; i++) {
      if (fork() == 0) {
        srand(getpid());
        record(out, sleeper());
        pthread_create(&t, 0, idle, 0);
        usleep((rand() % 8000) * 1000);
        pthread_exit(0);
      }
    }
    for (;;) pause();
  }
  return 2;
}
"#;

    const SESSION_GENERATION: &str = "ffeeddccbbaa99887766554433221100";
    const LONG: Duration = Duration::from_secs(240);

    struct Stats(PathBuf);

    impl Stats {
        fn read(&self) -> Value {
            std::fs::read_to_string(&self.0)
                .ok()
                .and_then(|text| serde_json::from_str(&text).ok())
                .unwrap_or(Value::Null)
        }
        fn num(&self, key: &str) -> u64 {
            self.read()[key].as_u64().unwrap_or(0)
        }
        fn nested(&self, key: &str, field: &str) -> u64 {
            self.read()[key][field].as_u64().unwrap_or(0)
        }
    }

    fn start_scoped(name: &str, envs: &[(&str, &str)]) -> (Sidecar, Stats) {
        let path = std::env::temp_dir().join(format!("navide-stats-{name}-{}.json", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let text = path.to_string_lossy().into_owned();
        let mut all = vec![("NAVIDE_SCAN_STATS_FILE", text.as_str())];
        all.extend_from_slice(envs);
        let mut sidecar = start_env(name, &[], None, LONG, true, &all);
        sidecar.line();
        (sidecar, Stats(path))
    }

    fn pid_of(sidecar: &Sidecar) -> i32 {
        sidecar.child.lock().unwrap().id() as i32
    }

    fn stat_tail(pid: i32) -> Option<Vec<String>> {
        let text = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
        let at = text.rfind(") ")?;
        Some(text[at + 2..].split(' ').map(str::to_owned).collect())
    }

    fn ppid_of(pid: i32) -> Option<i32> {
        stat_tail(pid)?.get(1)?.parse().ok()
    }

    fn state_of(pid: i32) -> Option<String> {
        stat_tail(pid)?.first().cloned()
    }

    fn children_of(pid: i32) -> Vec<i32> {
        let mut found = Vec::new();
        if let Ok(tasks) = std::fs::read_dir(format!("/proc/{pid}/task")) {
            for task in tasks.flatten() {
                if let Ok(text) = std::fs::read_to_string(task.path().join("children")) {
                    found.extend(text.split_whitespace().filter_map(|word| word.parse::<i32>().ok()));
                }
            }
        }
        found
    }

    fn wait_for(what: &str, timeout: Duration, mut done: impl FnMut() -> bool) {
        let deadline = Instant::now() + timeout;
        while !done() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn read_pids(path: &std::path::Path) -> Vec<i32> {
        std::fs::read_to_string(path)
            .unwrap_or_default()
            .split_whitespace()
            .filter_map(|word| word.parse().ok())
            .collect()
    }

    fn write_script(dir: &std::path::Path, name: &str, body: &str) -> String {
        let path = dir.join(name);
        std::fs::write(&path, body).unwrap();
        path.to_string_lossy().into_owned()
    }

    fn build_helper(dir: &std::path::Path) -> String {
        let source = dir.join("helper.c");
        let binary = dir.join("helper");
        std::fs::write(&source, HELPER_C).unwrap();
        let status = Command::new("cc")
            .args(["-O1", "-pthread", "-o"])
            .arg(&binary)
            .arg(&source)
            .status()
            .expect("a C compiler is needed for the process-shape helper");
        assert!(status.success(), "the helper did not compile");
        binary.to_string_lossy().into_owned()
    }

    fn create_in(
        sidecar: &mut Sidecar,
        argv: &[&str],
        session: &str,
        pane: &str,
    ) -> (libc::pid_t, RawFd) {
        let dir = sidecar.dir.clone();
        let mut meta = create_meta(&dir, argv, session);
        meta["session_generation"] = json!(SESSION_GENERATION);
        meta["pane_id"] = json!(pane);
        sidecar.send(code::CREATE, meta);
        let (owned, fd) = sidecar.recv_until(|f| f.code == code::SESSION_OWNED);
        sidecar.recv_until(|f| f.frame_type == FrameType::Response && f.code == code::CREATE);
        let pid = owned.meta["root"]["pid"].as_i64().unwrap() as libc::pid_t;
        (pid, fd.expect("SESSION_OWNED carries the master"))
    }

    fn kill_and_clean(sidecar: &mut Sidecar, session: &str) -> Value {
        sidecar.send(
            code::KILL,
            json!({"session_id":session,"session_generation":SESSION_GENERATION,"force":true}),
        );
        sidecar.recv_until(|f| f.code == code::SESSION_CLEANED).0.meta
    }

    /// Read ownership events until one whole snapshot satisfies `want`; returns that snapshot's pids.
    fn ownership_until(sidecar: &Sidecar, mut want: impl FnMut(&HashSet<i32>) -> bool) -> HashSet<i32> {
        let mut seen = HashSet::new();
        let deadline = Instant::now() + Duration::from_secs(120);
        loop {
            assert!(Instant::now() < deadline, "no ownership snapshot satisfied the test: {} pids", seen.len());
            let (frame, _) = sidecar.recv().expect("the sidecar closed the channel");
            if frame.code != code::SESSION_OWNERSHIP {
                continue;
            }
            if frame.meta["part"] == 0 {
                seen.clear();
            }
            for entry in frame.meta["descendants"].as_array().unwrap() {
                seen.insert(entry["pid"].as_i64().unwrap() as i32);
            }
            if frame.meta["final"] == true && want(&seen) {
                return seen;
            }
        }
    }

    fn kill_all(pids: &[i32]) {
        for &pid in pids {
            // SAFETY: test-owned helper processes.
            unsafe { libc::kill(pid, libc::SIGKILL) };
        }
    }

    #[test]
    fn adopted_orphan_is_a_sidecar_child() {
        let (mut sidecar, _stats) = start_scoped("r1", &[]);
        let dir = sidecar.dir.clone();
        let pidfile = dir.join("orphan.pid");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("(sleep 600 & echo $! > {}) ; exec sleep 600\n", pidfile.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r1", "pane-1");
        wait_for("the orphan's pid", Duration::from_secs(10), || !read_pids(&pidfile).is_empty());
        let orphan = read_pids(&pidfile)[0];
        let sidecar_pid = pid_of(&sidecar);
        let reparented = Instant::now() + Duration::from_secs(2);
        while ppid_of(orphan) != Some(sidecar_pid) && Instant::now() < reparented {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(ppid_of(orphan), Some(sidecar_pid), "the orphan's parent is not the sidecar");
        // Guard: the same-PGID edge already attributes it at HEAD.
        ownership_until(&sidecar, |pids| pids.contains(&orphan));
        let cleaned = kill_and_clean(&mut sidecar, "r1");
        assert_eq!(cleaned["cleanup_complete"], true, "{cleaned}");
        unsafe { libc::close(master) };
    }

    #[test]
    fn unattributable_orphan_changes_no_receipt() {
        let (mut sidecar, stats) = start_scoped("r2", &[]);
        let dir = sidecar.dir.clone();
        let (marker, pidfile) = (dir.join("signalled"), dir.join("orphan.pid"));
        let handler = write_script(
            &dir,
            "handler.sh",
            &format!(
                "trap 'touch {}' HUP TERM INT QUIT USR1\necho $$ > {}\nwhile :; do sleep 1; done\n",
                marker.display(),
                pidfile.display()
            ),
        );
        let root_script = write_script(
            &dir,
            "root.sh",
            &format!("( setsid /bin/sh {handler} & ) ; exec sleep 600\n"),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &root_script], "r2", "pane-1");
        wait_for("the orphan's pid", Duration::from_secs(10), || !read_pids(&pidfile).is_empty());
        let orphan = read_pids(&pidfile)[0];
        // Two 5 s grid points pass before the cleanup, so the orphan is looked at and still not attributed. No
        // ownership event is emitted while the session owns no descendant, so this waits on the clock.
        std::thread::sleep(Duration::from_secs(11));
        let alive_before_cleanup = process_exists(orphan);
        sidecar.send(
            code::KILL,
            json!({"session_id":"r2","session_generation":SESSION_GENERATION,"force":true}),
        );
        let named = std::cell::Cell::new(false);
        let cleaned = sidecar
            .recv_until(|f| {
                if f.code == code::SESSION_OWNERSHIP
                    && f.meta["descendants"]
                        .as_array()
                        .is_some_and(|d| d.iter().any(|e| e["pid"].as_i64() == Some(orphan as i64)))
                {
                    named.set(true);
                }
                f.code == code::SESSION_CLEANED
            })
            .0
            .meta;
        let named_by_events = named.get();
        assert_eq!(cleaned["cleanup_complete"], true, "{cleaned}");
        assert_eq!(cleaned["root_reaped"], true);
        assert_eq!(cleaned["survivors"], json!([]));
        assert_eq!(cleaned["unverifiable"], json!([]));
        std::thread::sleep(Duration::from_millis(500));
        #[cfg(feature = "fault-hooks")]
        let seen_by_scans = stats.read()["inspected_pid_set"]
            .as_array()
            .is_some_and(|set| set.iter().any(|pid| pid.as_i64() == Some(orphan as i64)));
        #[cfg(not(feature = "fault-hooks"))]
        let seen_by_scans = false;
        assert!(
            process_exists(orphan),
            "the unattributable orphan was killed (alive before cleanup: {alive_before_cleanup}, named by an ownership event: {named_by_events}, scans inspected it: {seen_by_scans}): {cleaned}"
        );
        assert!(!marker.exists(), "the unattributable orphan was signalled");
        #[cfg(feature = "fault-hooks")]
        assert_eq!(stats.num("unattributed_orphans"), 1, "{}", stats.read());
        let _ = &stats;
        // SAFETY: the orphan is this test's own process.
        unsafe { libc::kill(orphan, libc::SIGKILL) };
        wait_for("the sidecar to reap the orphan", Duration::from_secs(2), || {
            !std::path::Path::new(&format!("/proc/{orphan}")).exists()
        });
        unsafe { libc::close(master) };
    }

    /// No child of the sidecar is a zombie.
    fn no_zombie_children(sidecar_pid: i32) -> bool {
        children_of(sidecar_pid)
            .into_iter()
            .all(|child| state_of(child).is_none_or(|state| state != "Z"))
    }

    #[test]
    fn root_status_survives_orphan_reap() {
        let (mut sidecar, _stats) = start_scoped("r3", &[]);
        let dir = sidecar.dir.clone();
        let (pidfile, go) = (dir.join("orphan.pid"), dir.join("go"));
        let script = write_script(
            &dir,
            "root.sh",
            &format!(
                "(sleep 600 & echo $! > {}) ; while [ ! -e {} ]; do sleep 0.05; done ; exit 7\n",
                pidfile.display(),
                go.display()
            ),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r3", "pane-1");
        wait_for("the orphan's pid", Duration::from_secs(10), || !read_pids(&pidfile).is_empty());
        let orphan = read_pids(&pidfile)[0];
        let sidecar_pid = pid_of(&sidecar);
        let reparented = Instant::now() + Duration::from_secs(2);
        while ppid_of(orphan) != Some(sidecar_pid) && Instant::now() < reparented {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(ppid_of(orphan), Some(sidecar_pid), "the orphan was not a sidecar child");
        kill_all(&[orphan]);
        std::fs::write(&go, b"").unwrap();
        let (exited, _) = sidecar.recv_until(|f| f.code == code::SESSION_EXITED);
        assert_eq!(exited.meta["exit_code"], 7, "{}", exited.meta);
        assert_eq!(exited.meta["signal"], Value::Null);
        wait_for("no zombie sidecar child", Duration::from_secs(2), || no_zombie_children(sidecar_pid));
        unsafe { libc::close(master) };
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn root_status_survives_orphan_reap_on_the_released_root_failure_path() {
        let (mut sidecar, _stats) = start_scoped("r3b", &[]);
        let sidecar_pid = pid_of(&sidecar);
        let dir = sidecar.dir.clone();
        let mut meta = create_meta(&dir, &["/bin/sleep", "600"], "r3b");
        meta["pane_id"] = json!("fault:watch-exit-fail");
        sidecar.send(code::CREATE, meta);
        let (error, _) = sidecar.recv_until(|f| f.frame_type == FrameType::Error);
        assert_eq!(error.meta["effect"], "none", "{}", error.meta);
        wait_for("no zombie sidecar child", Duration::from_secs(2), || no_zombie_children(sidecar_pid));
        assert!(children_of(sidecar_pid).is_empty(), "the released root is still a child");
    }

    /// The only child of the sidecar, once the gated root exists.
    #[cfg(feature = "fault-hooks")]
    fn only_child(sidecar_pid: i32) -> i32 {
        let mut found = None;
        wait_for("the gated root", Duration::from_secs(10), || {
            found = children_of(sidecar_pid).first().copied();
            found.is_some()
        });
        found.unwrap()
    }

    #[cfg(feature = "fault-hooks")]
    fn killed_root_keeps_its_status(hook: &str, name: &str) {
        let (mut sidecar, _stats) = start_scoped(name, &[]);
        let sidecar_pid = pid_of(&sidecar);
        let release = sidecar.dir.join("release");
        let dir = sidecar.dir.clone();
        let mut meta = create_meta(&dir, &["/bin/sleep", "600"], name);
        meta["session_generation"] = json!(SESSION_GENERATION);
        meta["pane_id"] = json!(format!("fault:{hook}:{}", release.display()));
        sidecar.send(code::CREATE, meta);
        let root = only_child(sidecar_pid);
        // SAFETY: the sidecar's only child, a gated root of this test's session.
        unsafe { libc::kill(root, libc::SIGKILL) };
        std::thread::sleep(Duration::from_millis(200));
        std::fs::write(&release, b"").unwrap();
        loop {
            let (frame, _) = sidecar.recv().expect("the sidecar closed the channel");
            if frame.frame_type == FrameType::Error {
                return; // a refusal before publication is allowed
            }
            if frame.code == code::SESSION_EXITED {
                assert_eq!(frame.meta["exit_code"], -9, "{}", frame.meta);
                assert_eq!(frame.meta["signal"], "SIGKILL", "{}", frame.meta);
                return;
            }
        }
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn root_killed_after_reservation_keeps_its_status() {
        killed_root_keeps_its_status("journal-hold", "r4");
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn root_killed_in_fork_to_reservation_window_keeps_its_status() {
        killed_root_keeps_its_status("before-report-read", "r4b");
    }

    fn rchar_of(pid: i32) -> u64 {
        std::fs::read_to_string(format!("/proc/{pid}/io"))
            .expect("task I/O accounting is required")
            .lines()
            .find_map(|line| line.strip_prefix("rchar: "))
            .expect("no rchar field")
            .trim()
            .parse()
            .unwrap()
    }

    /// The sidecar's `rchar` over ten idle seconds with four sessions and `noise` unrelated processes, and the
    /// pids of those processes.
    fn idle_rchar(name: &str, noise: usize, envs: &[(&str, &str)]) -> (u64, Stats, Vec<i64>) {
        let (mut sidecar, stats) = start_scoped(name, envs);
        let mut sleepers: Vec<_> = (0..noise)
            .map(|_| Command::new("/bin/sleep").arg("600").spawn().unwrap())
            .collect();
        let noise_pids: Vec<i64> = sleepers.iter().map(|sleeper| i64::from(sleeper.id())).collect();
        let masters: Vec<_> = (0..4)
            .map(|index| create_in(&mut sidecar, &["/bin/cat"], &format!("{name}-{index}"), "pane-1").1)
            .collect();
        // Past the first snapshot window, so every session is on its grid.
        std::thread::sleep(Duration::from_secs(6));
        let sidecar_pid = pid_of(&sidecar);
        let before = rchar_of(sidecar_pid);
        std::thread::sleep(Duration::from_secs(10));
        let used = rchar_of(sidecar_pid) - before;
        for sleeper in &mut sleepers {
            let _ = sleeper.kill();
            let _ = sleeper.wait();
        }
        for master in masters {
            unsafe { libc::close(master) };
        }
        (used, stats, noise_pids)
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn idle_scan_reads_only_owned_pids() {
        let (head, _, _) = idle_rchar("r5-head", 165, &[("NAVIDE_FAULT_FORCE_FULL", "1")]);
        let (used_165, stats, noise) = idle_rchar("r5-165", 165, &[]);
        let (used_495, _, _) = idle_rchar("r5-495", 495, &[]);
        assert!(
            used_165 * 4 < head,
            "rchar over 10 s: scoped {used_165} is not below a quarter of the full-scan {head}"
        );
        let spread = (used_495 as f64 - used_165 as f64).abs() / used_165 as f64;
        assert!(spread <= 0.10, "rchar depends on the noise: {used_165} vs {used_495}");
        let read = stats.read();
        assert_eq!(read["scans_full"], 0, "{read}");
        assert!(read["scans_scoped"].as_u64().unwrap_or(0) > 0, "{read}");
        assert_eq!(read["due_scans_skipped"], 0, "{read}");
        assert_eq!(read["reaper_running"], true, "{read}");
        // No walk ever stat-read a noise pid: the stat-read set holds the sidecar, the session roots and their
        // descendants only. The set is checked non-empty so the intersection cannot pass on a missing counter.
        let stat_read: Vec<i64> = read["stat_read_pid_set"]
            .as_array()
            .expect("no stat-read pid set")
            .iter()
            .filter_map(Value::as_i64)
            .collect();
        assert!(!stat_read.is_empty(), "{read}");
        let touched: Vec<i64> = stat_read.iter().copied().filter(|pid| noise.contains(pid)).collect();
        assert!(touched.is_empty(), "a walk stat-read noise pids {touched:?}: {read}");
        let walked_max = read["walks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|walk| walk["stat_reads"].as_u64().unwrap())
            .max()
            .unwrap();
        assert!(walked_max < 120, "a walk read {walked_max} stats: it depends on the noise");
    }

    /// 250 children over a leader that `pthread_exit`s: a stable already-zombie leader (rows R-6, R-7).
    #[cfg(feature = "fault-hooks")]
    fn leader_exit_world(name: &str) -> (Sidecar, Stats, RawFd, Vec<i32>) {
        let (mut sidecar, stats) = start_scoped(name, &[]);
        let dir = sidecar.dir.clone();
        let helper = build_helper(&dir);
        let out = dir.join("children.pids");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("{helper} leader-exit 250 {} & exec sleep 600\n", out.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], name, "pane-1");
        wait_for("the children", Duration::from_secs(20), || read_pids(&out).len() == 250);
        let kids = read_pids(&out);
        (sidecar, stats, master, kids)
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn zombie_stable_probe_shape() {
        let (mut sidecar, stats, master, kids) = leader_exit_world("r7");
        let all = ownership_until(&sidecar, |pids| kids.iter().all(|kid| pids.contains(kid)));
        assert!(kids.iter().all(|kid| all.contains(kid)));
        assert_eq!(stats.nested("dirty_walks", "r2b"), 1, "{}", stats.read());
        // Two more due scans add none: the baseline is held.
        let mut snapshots = 0;
        ownership_until(&sidecar, |_| {
            snapshots += 1;
            snapshots >= 2
        });
        assert_eq!(stats.nested("dirty_walks", "r2b"), 1, "{}", stats.read());
        assert_eq!(stats.num("scans_full"), 0, "{}", stats.read());
        kill_all(&kids);
        kill_and_clean(&mut sidecar, "r7");
        unsafe { libc::close(master) };
    }

    /// Workers whose leaders `pthread_exit` at random moments over eight seconds (row R-6). A walk is dirty only
    /// when a leader dies while it runs, so the row needs enough deaths per second, and a long enough walk, that
    /// some walk meets one. With 300 workers a release build missed every walk in about 5 runs of 12.
    #[cfg(feature = "fault-hooks")]
    const WORKERS: usize = 900;

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn thread_exit_reparent_probe_shape() {
        let (mut sidecar, stats) = start_scoped("r6", &[]);
        let dir = sidecar.dir.clone();
        let helper = build_helper(&dir);
        let out = dir.join("children.pids");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("{helper} workers {WORKERS} {} & exec sleep 600\n", out.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r6", "pane-1");
        wait_for("the workers", Duration::from_secs(60), || read_pids(&out).len() == WORKERS);
        let kids = read_pids(&out);
        let all = ownership_until(&sidecar, |pids| kids.iter().all(|kid| pids.contains(kid)));
        assert!(kids.iter().all(|kid| all.contains(kid)), "an event lost a live child");
        // The exits are spread over eight seconds, so some walk meets a leader that dies while it is read.
        wait_for("a walk that met a dying leader", Duration::from_secs(30), || {
            stats.nested("dirty_walks", "r2") + stats.nested("dirty_walks", "r1") >= 1
        });
        // While leaders keep dying a walk can be dirty three times in a row, and the full scan then takes over
        // (every one of those is a dirty fallback). Once the exits are over the baseline holds every zombie, the
        // retry succeeds and the scoped scan is back: no further full scan.
        std::thread::sleep(Duration::from_secs(9));
        let settled = stats.num("scans_full");
        let scoped_before = stats.num("scans_scoped");
        wait_for("two scoped scans after the exits", Duration::from_secs(30), || {
            stats.num("scans_scoped") >= scoped_before + 2
        });
        assert_eq!(stats.num("scans_full"), settled, "{}", stats.read());
        let read = stats.read();
        // Every full scan must be a dirty fallback; name any other reason that caused one.
        let other: Vec<String> = read["fallbacks"]
            .as_object()
            .map(|reasons| {
                reasons
                    .iter()
                    .filter(|(reason, count)| reason.as_str() != "dirty" && count.as_u64().unwrap_or(0) > 0)
                    .map(|(reason, count)| format!("{reason}={count}"))
                    .collect()
            })
            .unwrap_or_default();
        assert_eq!(
            stats.num("scans_full"),
            stats.nested("fallbacks", "dirty"),
            "full scans exceed the dirty fallbacks; other fallback reasons: {other:?}; {read}"
        );
        kill_all(&kids);
        kill_and_clean(&mut sidecar, "r6");
        unsafe { libc::close(master) };
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn de_thread_probe_shape() {
        let (mut sidecar, stats) = start_scoped("r8", &[]);
        let dir = sidecar.dir.clone();
        let helper = build_helper(&dir);
        let out = dir.join("children.pids");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("{helper} exec-loop 100 {} & exec sleep 600\n", out.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r8", "pane-1");
        wait_for("the children", Duration::from_secs(20), || read_pids(&out).len() == 100);
        let kids = read_pids(&out);
        for _ in 0..2 {
            let all = ownership_until(&sidecar, |pids| kids.iter().all(|kid| pids.contains(kid)));
            assert!(kids.iter().all(|kid| all.contains(kid)), "an event lost a live child");
        }
        // The tid set changes under a walk at a random moment, and the walk's end re-read (R2) catches the same
        // exec more often than the second listing (R1) does: scans keep coming until one lands in the R1 window.
        // Every event on the way must still hold all the live children.
        let mut r1_seen = stats.nested("dirty_walks", "r1") >= 1;
        let deadline = Instant::now() + Duration::from_secs(120);
        while !r1_seen {
            assert!(Instant::now() < deadline, "no walk met the tid set changing: {}", stats.read());
            let all = ownership_until(&sidecar, |pids| kids.iter().all(|kid| pids.contains(kid)));
            assert!(kids.iter().all(|kid| all.contains(kid)), "an event lost a live child");
            r1_seen = stats.nested("dirty_walks", "r1") >= 1;
        }
        kill_all(&kids);
        kill_and_clean(&mut sidecar, "r8");
        unsafe { libc::close(master) };
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    #[ignore]
    fn zombie_first_seen_probe_shape() {
        let (mut sidecar, stats) = start_scoped("r9", &[]);
        let dir = sidecar.dir.clone();
        let helper = build_helper(&dir);
        let out = dir.join("children.pids");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("{helper} rounds 500 {} & exec sleep 600\n", out.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r9", "pane-1");
        wait_for("the first-seen zombie shape", Duration::from_secs(120), || {
            stats.nested("dirty_walks", "r2b") >= 1
        });
        wait_for("all rounds", Duration::from_secs(30), || read_pids(&out).len() == 500);
        let kids = read_pids(&out);
        let all = ownership_until(&sidecar, |pids| kids.iter().all(|kid| pids.contains(kid)));
        assert!(kids.iter().all(|kid| all.contains(kid)), "Y is missing from a published event");
        kill_all(&kids);
        kill_and_clean(&mut sidecar, "r9");
        unsafe { libc::close(master) };
    }

    /// The roles the persisted table names in one mode: a double-fork orphan and a same-group member.
    #[cfg(feature = "fault-hooks")]
    fn fixture_roles(name: &str, envs: &[(&str, &str)]) -> (Vec<&'static str>, Stats) {
        let (mut sidecar, stats) = start_scoped(name, envs);
        let dir = sidecar.dir.clone();
        let (orphan_file, member_file, setsid_file) =
            (dir.join("orphan.pid"), dir.join("member.pid"), dir.join("setsid.pid"));
        let handler = write_script(
            &dir,
            "setsid.sh",
            &format!("echo $$ > {}\nexec sleep 600\n", setsid_file.display()),
        );
        let script = write_script(
            &dir,
            "root.sh",
            &format!(
                "(sleep 600 & echo $! > {}) ; ( setsid /bin/sh {handler} & ) ; sleep 600 & echo $! > {} ; wait\n",
                orphan_file.display(),
                member_file.display()
            ),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], name, "pane-1");
        wait_for("the fixture", Duration::from_secs(10), || {
            [&orphan_file, &member_file, &setsid_file].iter().all(|file| !read_pids(file).is_empty())
        });
        let (orphan, member, setsid) =
            (read_pids(&orphan_file)[0], read_pids(&member_file)[0], read_pids(&setsid_file)[0]);
        let table = ownership_until(&sidecar, |pids| pids.contains(&orphan) && pids.contains(&member));
        let mut roles = Vec::new();
        if table.contains(&orphan) {
            roles.push("orphan");
        }
        if table.contains(&member) {
            roles.push("member");
        }
        if table.contains(&setsid) {
            roles.push("setsid");
        }
        kill_and_clean(&mut sidecar, name);
        kill_all(&[setsid]);
        unsafe { libc::close(master) };
        (roles, stats)
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn scoped_table_equals_full_scan() {
        let (full, _) = fixture_roles("r10-full", &[("NAVIDE_FAULT_FORCE_FULL", "1")]);
        let (scoped_roles, stats) = fixture_roles("r10-scoped", &[]);
        assert_eq!(full, scoped_roles, "the persisted tables differ");
        assert_eq!(full, ["orphan", "member"], "the setsid orphan must not be attributed");
        assert!(stats.num("scans_scoped") > 0, "the scoped run never walked: {}", stats.read());
    }

    #[cfg(feature = "fault-hooks")]
    #[test]
    fn missing_children_falls_back_to_full_scan() {
        let (mut sidecar, stats) = start_scoped("r11", &[("NAVIDE_FAULT_NO_CHILDREN", "1")]);
        let sidecar_pid = pid_of(&sidecar);
        let dir = sidecar.dir.clone();
        let pidfile = dir.join("orphan.pid");
        let script = write_script(
            &dir,
            "root.sh",
            &format!("(sleep 600 & echo $! > {}) ; exec sleep 600\n", pidfile.display()),
        );
        let (_root, master) = create_in(&mut sidecar, &["/bin/sh", &script], "r11", "pane-1");
        wait_for("the helper", Duration::from_secs(10), || !read_pids(&pidfile).is_empty());
        let orphan = read_pids(&pidfile)[0];
        ownership_until(&sidecar, |pids| pids.contains(&orphan));
        let read = stats.read();
        assert_eq!(read["scans_scoped"], 0, "{read}");
        assert!(read["scans_full"].as_u64().unwrap_or(0) >= 1, "{read}");
        assert_eq!(read["fallbacks"]["no_children"], 1, "{read}");
        assert_eq!(read["subreaper_enabled"], false, "{read}");
        assert_eq!(read["reaper_running"], false, "{read}");
        assert_ne!(ppid_of(orphan), Some(sidecar_pid), "the orphan was adopted without a subreaper");
        kill_all(&[orphan]);
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(stats.num("unreaped_children"), 0, "{}", stats.read());
        assert!(no_zombie_children(sidecar_pid));
        kill_and_clean(&mut sidecar, "r11");
        unsafe { libc::close(master) };
    }
}
