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
use std::time::{Duration, Instant};

const GENERATION: &str = "00112233445566778899aabbccddeeff";

struct Sidecar {
    child: Child,
    sock: RawFd,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
    dir: PathBuf,
}

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("navide-sidecar-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn start_with(name: &str, args: &[&str], journal: Option<PathBuf>) -> Sidecar {
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
            "1",
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
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    let mut child = command.spawn().unwrap();
    unsafe { libc::close(pair[1]) };
    let stdout = BufReader::new(child.stdout.take().unwrap());
    Sidecar {
        child,
        sock: pair[0],
        stdout,
        next_id: 0,
        dir,
    }
}

fn start(name: &str) -> Sidecar {
    start_with(name, &[], None)
}

impl Sidecar {
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
        "pane_id":"pane-1","workspace_path":dir,"cwd":dir,"argv":argv,
        "env":{"PATH":"/usr/bin:/bin","TERM":"xterm"},"rows":24,"cols":80,"agent_key":""})
}

// ---------------------------------------------------------------------------- handshake

#[test]
fn ready_is_the_only_stdout_line_and_advertises_the_frozen_limits() {
    let mut sidecar = start("ready");
    let ready = sidecar.line();
    assert_eq!(ready["event"], "ready");
    assert_eq!(ready["protocol"], "navide-acr");
    assert_eq!(ready["wire"], 1);
    assert_eq!(ready["runtime"], "navide-cli-runtime");
    assert_eq!(ready["runtime_generation"], GENERATION);
    assert_eq!(ready["pid"].as_u64().unwrap(), sidecar.child.id() as u64);
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
        Some("2"),
    );
    assert_eq!(fatal["event"], "fatal");
    assert_eq!(fatal["error"]["code"], "INCOMPATIBLE_WIRE");
    assert!(!status.success());
    let (fatal, status) = failed_start(
        &[&common[..], &["--ownership-journal", "/tmp/j", "--bogus"]].concat(),
        Some("1"),
    );
    assert_eq!(fatal["error"]["code"], "STARTUP_FAILED");
    assert!(!status.success());
    let (fatal, _) = failed_start(&common, Some("1"));
    assert_eq!(
        fatal["error"]["code"], "STARTUP_FAILED",
        "the journal is mandatory in wire v1"
    );
    let (fatal, _) = failed_start(
        &[
            "--wire",
            "1",
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
