//! A `Host` that is only a double, for tests of the control plane that need no operating system: no PTY, no process,
//! no channel. What its few knobs make it do is what a test says it does.
use crate::compose;
use crate::control::{Config, Request, Runtime, Wire};
use crate::frame::{self, code, Direction, Frame};
use crate::host::{
    Cleanup, CreateArgs, ExitInfo, Host, Launch, Outcome, ResizeFailure, Spawned, WorstCase,
};
use crate::session::{mailbox, SessionCx, SessionHandle};
use crate::wire::Outbound;
use serde_json::{json, Value};
use std::io;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct MockHost {
    /// The error the next `send_owned` fails with (the session is then not announced).
    pub owned_failure: Mutex<Option<io::Error>>,
    /// A killed root exits, so its exit is verified; otherwise a kill leaves the root's exit unknown.
    pub kill_ends_root: bool,
    /// What every snapshot reports.
    pub members: Mutex<Vec<Value>>,
}

impl Host for MockHost {
    type Session = ();
    type Owned = ();
    type Link = ();
    type Options = ();
    type Unsettled = std::convert::Infallible;

    const PTY_BACKEND: &'static str = "mock";
    const HANDOFF: &'static str = "mock";
    const SERVICING: bool = false;
    const OPTIONS: &'static [&'static str] = &[];

    fn set_option(_: &mut (), _: &str, _: String) {}
    fn check_options(_: &()) -> Result<(), String> {
        Ok(())
    }
    fn boot(_: &Config, _: ()) -> io::Result<Self> {
        Ok(Self::default())
    }
    fn open_link(&self) -> io::Result<()> {
        Ok(())
    }
    fn link_read(&self, _: &(), _: &mut [u8]) -> io::Result<usize> {
        Ok(0)
    }
    fn link_write(&self, _: &(), _: &Outbound) -> io::Result<()> {
        Ok(())
    }
    fn link_break(&self, _: &()) {}

    fn env_key_ok(key: &str) -> bool {
        !key.contains(['\0', '='])
    }
    fn launch(&self, _: &str, _: &Value, _: &[(String, String)]) -> io::Result<Launch> {
        Ok(Launch {
            argv: vec!["/bin/true".into()],
            argv_source: "rust",
            argv0: compose::attest("/bin/true"),
            outer: None,
        })
    }
    fn worst_case(&self) -> WorstCase {
        WorstCase {
            root: root(),
            pty: json!({"tty":"mock"}),
            outer: false,
        }
    }
    fn spawn(&self, _: &Runtime<Self>, _: u64, _: &CreateArgs) -> io::Result<Spawned<Self>> {
        let (tx, mailbox, exit) = mailbox();
        Ok(Spawned {
            platform: (),
            owned: (),
            exit,
            tx,
            mailbox,
        })
    }
    fn root_record(&self, _: &()) -> Value {
        root()
    }
    fn pty_record(&self, _: &()) -> io::Result<Value> {
        Ok(json!({"tty":"mock"}))
    }
    fn send_owned(&self, runtime: &Runtime<Self>, _: &Arc<SessionHandle<Self>>, meta: Value) -> io::Result<()> {
        if let Some(error) = self.owned_failure.lock().unwrap().take() {
            return Err(error);
        }
        runtime.wire.out.event(code::SESSION_OWNED, meta);
        Ok(())
    }
    fn abort_unserved(&self, _: &SessionHandle<Self>, _: &Value) -> bool {
        true
    }

    fn resize(&self, _: &(), _: u16, _: u16) -> Result<(), ResizeFailure> {
        Ok(())
    }
    fn stream_settled(&self, cx: &SessionCx<'_, Self>) -> bool {
        cx.state.eof
    }
    fn settle_window(&self) -> Option<(Duration, Self::Unsettled)> {
        None
    }
    fn cleanup(&self, cx: &mut SessionCx<'_, Self>, _: Cleanup<Self::Unsettled>) -> Outcome {
        if self.kill_ends_root {
            cx.handle.exit.set(ExitInfo {
                exit_code: Some(0),
                signal: None,
                unverified: false,
            });
            cx.send_exited();
        }
        Outcome::default()
    }
    fn snapshot(&self, _: &mut SessionCx<'_, Self>, _: Instant) -> Option<Vec<Value>> {
        Some(self.members.lock().unwrap().clone())
    }
    fn close_stream(&self, _: &SessionHandle<Self>) {}
}

fn root() -> Value {
    json!({"pid":1,"pgid":1,"start_kind":"mock","start_value":"x"})
}

/// K4: the worst-case `SESSION_OWNED` records a host bounds its creates by must name every key a real record of it
/// has, and be at least as long, or a create near the bound is admitted that the bound should have refused.
pub fn assert_worst_case_covers(worst: &WorstCase, real_root: &Value, real_pty: &Value) {
    use std::collections::BTreeSet;
    let keys = |value: &Value| value.as_object().expect("a record is an object").keys().cloned().collect::<BTreeSet<_>>();
    let length = |value: &Value| serde_json::to_vec(value).unwrap().len();
    for (name, real, bound) in [("root", real_root, &worst.root), ("pty", real_pty, &worst.pty)] {
        assert_eq!(keys(real), keys(bound), "{name}: the worst case names exactly the keys a real record has");
        assert!(
            length(bound) >= length(real),
            "{name}: the worst case ({}) is shorter than a real record ({})",
            length(bound),
            length(real)
        );
    }
}

/// A runtime on `host`, with the wire of `Config::for_test`.
pub fn runtime<H: Host>(host: H) -> Arc<Runtime<H>> {
    let config = Config::for_test();
    let tag = frame::runtime_tag(&config.generation).unwrap();
    Runtime::new(host, Wire::new(&config, tag))
}

/// A session handle on `H` with the given platform half, plus the receiving end of its mailbox.
pub fn handle<H: Host>(platform: H::Session) -> (Arc<SessionHandle<H>>, std::sync::mpsc::Receiver<crate::host::Msg>) {
    let (tx, mailbox, exit) = mailbox();
    let handle = SessionHandle::<H> {
        id: "s".into(),
        generation: "g".into(),
        pane: "p".into(),
        workspace: "w".into(),
        agent_key: String::new(),
        platform,
        tx,
        exit,
        done: (Mutex::new(None), std::sync::Condvar::new()),
        adopted: std::sync::atomic::AtomicBool::new(false),
        started: Instant::now(),
        survivors: Mutex::new(Vec::new()),
    };
    (Arc::new(handle), mailbox)
}

/// A request frame as the reader hands it to the control plane.
pub fn request(id: u64, request_code: u16, meta: Value) -> Request {
    Request::for_test(id, request_code, meta)
}

/// The `CREATE` meta of a session on `workspace`.
pub fn create_meta(session: &str, workspace: &str) -> Value {
    json!({"session_id":session,"session_generation":"ffeeddccbbaa99887766554433221100",
        "pane_id":"pane-1","workspace_path":workspace,"cwd":workspace,
        "launch":{"shape":"argv","argv":["/bin/true"]},
        "env":{},"rows":24,"cols":80,"agent_key":""})
}

/// Every frame the runtime has queued for Python so far, decoded in order. The queue is then closed: call it once,
/// at the end of a test.
pub fn sent<H: Host>(runtime: &Runtime<H>) -> Vec<Frame> {
    runtime.wire.out.finish();
    let tag = runtime.wire.out.tag();
    let mut bytes = Vec::new();
    drain(&runtime.wire.out, &mut bytes);
    let (mut frames, mut at) = (Vec::new(), 0);
    while at < bytes.len() {
        let header = frame::parse_header(
            bytes[at..at + frame::HEADER_LEN].try_into().unwrap(),
            Direction::RustToPython,
            tag,
        )
        .unwrap();
        let end = at + frame::HEADER_LEN + header.body_len();
        frames.push(frame::decode_body(header, &bytes[at + frame::HEADER_LEN..end]).unwrap());
        at = end;
    }
    frames
}

#[cfg(unix)]
fn drain(out: &Outbound, bytes: &mut Vec<u8>) {
    use crate::wire::Sender;
    use std::os::fd::RawFd;
    struct Collect<'a>(&'a mut Vec<u8>);
    impl Sender for Collect<'_> {
        fn send_first(&mut self, data: &[u8], _: Option<RawFd>) -> io::Result<usize> {
            self.0.extend_from_slice(data);
            Ok(data.len())
        }
        fn send_more(&mut self, data: &[u8]) -> io::Result<usize> {
            self.0.extend_from_slice(data);
            Ok(data.len())
        }
    }
    out.run_writer(&mut Collect(bytes)).unwrap();
}

#[cfg(windows)]
fn drain(out: &Outbound, bytes: &mut Vec<u8>) {
    out.run_writer_plain(bytes).unwrap();
}
