//! The sidecar's outbound half of the control channel: one bounded queue, one writer thread.
//!
//! Memory is bounded by bytes and by frames (E1 1.4): a producer blocks (backpressure) while the
//! queue is full, nothing is ever dropped. `SESSION_OWNED` is the only frame that carries a
//! descriptor; it travels as `Payload::Owned`, which holds the session's `SessionPty` so the
//! writer lends the master to the send itself (no raw descriptor sits in the queue).
use crate::frame::{self, Frame, FrameType, MAX_FRAME_R2P};
#[cfg(unix)]
use crate::frame::{code, FLAG_HAS_FD, MAX_HAS_FD_FRAME};
#[cfg(unix)]
use crate::session_pty::SessionPty;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io;
#[cfg(unix)]
use std::os::fd::{AsRawFd, RawFd};
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(unix)]
use std::sync::Arc;
use std::sync::{Condvar, Mutex};

pub const MAX_OUTBOUND_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_OUTBOUND_FRAMES: usize = 256;

/// Harness build only: how the writer deliberately mis-sends a `SESSION_OWNED` (E1 P3).
#[cfg(feature = "fault-hooks")]
#[derive(Clone, Copy)]
pub enum SendFault {
    /// The first `sendmsg` accepts only a few bytes (the remainder follows by plain sends).
    Short,
    /// The first `sendmsg` makes zero progress: the channel is lost, the rest is never sent.
    Zero,
}

pub enum Payload {
    Plain(Vec<u8>),
    #[cfg(feature = "fault-hooks")]
    OwnedFault {
        bytes: Vec<u8>,
        pty: Arc<SessionPty>,
        fault: SendFault,
    },
    /// A `SESSION_OWNED` frame and the session PTY whose master travels with it.
    #[cfg(unix)]
    Owned {
        bytes: Vec<u8>,
        pty: Arc<SessionPty>,
    },
}
impl Payload {
    fn len(&self) -> usize {
        match self {
            Self::Plain(bytes) => bytes.len(),
            #[cfg(unix)]
            Self::Owned { bytes, .. } => bytes.len(),
            #[cfg(feature = "fault-hooks")]
            Self::OwnedFault { bytes, .. } => bytes.len(),
        }
    }
}

struct Queue {
    items: VecDeque<Payload>,
    bytes: usize,
    done: bool,
}

pub struct Outbound {
    queue: Mutex<Queue>,
    changed: Condvar,
    tag: u64,
    lost: AtomicBool,
}

impl Outbound {
    pub fn new(tag: u64) -> Self {
        Self {
            queue: Mutex::new(Queue {
                items: VecDeque::new(),
                bytes: 0,
                done: false,
            }),
            changed: Condvar::new(),
            tag,
            lost: AtomicBool::new(false),
        }
    }

    pub fn tag(&self) -> u64 {
        self.tag
    }

    /// The channel is gone (a send failed or the peer closed): later sends are discarded.
    #[cfg(any(test, feature = "fault-hooks"))]
    #[cfg_attr(windows, allow(dead_code))]
    pub fn is_lost(&self) -> bool {
        self.lost.load(Ordering::SeqCst)
    }

    fn push(&self, payload: Payload) {
        let size = payload.len();
        let mut queue = self.queue.lock().unwrap();
        while !queue.done
            && !queue.items.is_empty()
            && (queue.items.len() >= MAX_OUTBOUND_FRAMES || queue.bytes + size > MAX_OUTBOUND_BYTES)
        {
            queue = self.changed.wait(queue).unwrap();
        }
        if queue.done {
            return;
        }
        queue.bytes += size;
        queue.items.push_back(payload);
        self.changed.notify_all();
    }

    fn frame(
        &self,
        frame_type: FrameType,
        frame_code: u16,
        flags: u16,
        cid: u64,
        meta: Value,
        blob: Vec<u8>,
    ) -> Vec<u8> {
        let frame = Frame {
            frame_type,
            code: frame_code,
            flags,
            correlation_id: cid,
            runtime_tag: self.tag,
            meta,
            blob,
        };
        let bytes = frame::encode(&frame);
        debug_assert!(bytes.len() - frame::HEADER_LEN <= MAX_FRAME_R2P as usize);
        bytes
    }

    pub fn event(&self, event_code: u16, meta: Value) {
        self.event_for(event_code, 0, meta, Vec::new());
    }

    pub fn event_for(&self, event_code: u16, cid: u64, meta: Value, blob: Vec<u8>) {
        let bytes = self.frame(FrameType::Event, event_code, 0, cid, meta, blob);
        self.push(Payload::Plain(bytes));
    }

    pub fn respond(&self, request_code: u16, cid: u64, meta: Value) {
        let bytes = self.frame(FrameType::Response, request_code, 0, cid, meta, Vec::new());
        self.push(Payload::Plain(bytes));
    }

    #[cfg_attr(windows, allow(dead_code))] // observation answers are POSIX-only for now
    pub fn respond_with_blob(&self, request_code: u16, cid: u64, meta: Value, blob: Vec<u8>) {
        let bytes = self.frame(FrameType::Response, request_code, 0, cid, meta, blob);
        self.push(Payload::Plain(bytes));
    }

    /// `ERROR` frame (10.4): `meta` is `{code, stage, message, effect}`.
    pub fn error(
        &self,
        request_code: u16,
        cid: u64,
        error_code: &str,
        stage: &str,
        message: &str,
        effect: &str,
    ) {
        let message: String = message.chars().take(1024).collect();
        let meta = json!({"code":error_code,"stage":stage,"message":message,"effect":effect});
        let bytes = self.frame(FrameType::Error, request_code, 0, cid, meta, Vec::new());
        self.push(Payload::Plain(bytes));
    }

    /// `ERROR` frame with a prepared `meta` (extra additive keys such as `steps`, `survivors`).
    pub fn error_with(&self, request_code: u16, cid: u64, meta: Value) {
        let bytes = self.frame(FrameType::Error, request_code, 0, cid, meta, Vec::new());
        self.push(Payload::Plain(bytes));
    }

    /// Queue `SESSION_OWNED` with the session's master. The worst-case size is checked before
    /// the spawn (5.1.3); this guards the actual frame as well.
    #[cfg(unix)]
    pub fn session_owned(&self, meta: Value, pty: Arc<SessionPty>) -> io::Result<()> {
        let bytes = self.frame(
            FrameType::Event,
            code::SESSION_OWNED,
            FLAG_HAS_FD,
            0,
            meta,
            Vec::new(),
        );
        if bytes.len() > MAX_HAS_FD_FRAME {
            return Err(io::Error::other("handoff_size"));
        }
        self.push(Payload::Owned { bytes, pty });
        Ok(())
    }

    /// Harness build only: bytes that are not a frame, queued in order with real ones.
    #[cfg(feature = "fault-hooks")]
    pub fn raw(&self, bytes: Vec<u8>) {
        self.push(Payload::Plain(bytes));
    }

    /// Harness build only: `SESSION_OWNED` with a deliberately faulty send.
    #[cfg(feature = "fault-hooks")]
    pub fn session_owned_fault(&self, meta: Value, pty: Arc<SessionPty>, fault: SendFault) {
        let bytes = self.frame(
            FrameType::Event,
            code::SESSION_OWNED,
            FLAG_HAS_FD,
            0,
            meta,
            Vec::new(),
        );
        self.push(Payload::OwnedFault { bytes, pty, fault });
    }

    /// Nothing more will be queued; the writer drains what is queued, then returns.
    pub fn finish(&self) {
        self.queue.lock().unwrap().done = true;
        self.changed.notify_all();
    }

    /// The next queued payload, blocking; `None` once finished and drained.
    fn next_payload(&self) -> Option<Payload> {
        let mut queue = self.queue.lock().unwrap();
        loop {
            if let Some(payload) = queue.items.pop_front() {
                queue.bytes -= payload.len();
                self.changed.notify_all();
                return Some(payload);
            }
            if queue.done {
                return None;
            }
            queue = self.changed.wait(queue).unwrap();
        }
    }

    /// The channel is lost: nothing more is sent, later pushes are discarded.
    fn lose(&self) {
        self.lost.store(true, Ordering::SeqCst);
        let mut queue = self.queue.lock().unwrap();
        queue.done = true;
        queue.items.clear();
        queue.bytes = 0;
        self.changed.notify_all();
    }

    /// Writer thread body for a channel that carries no handle (Windows: the sidecar's stdout).
    #[cfg(windows)]
    pub fn run_writer_plain(&self, out: &mut dyn io::Write) -> io::Result<()> {
        while let Some(payload) = self.next_payload() {
            let Payload::Plain(bytes) = payload;
            if let Err(error) = out.write_all(&bytes).and_then(|()| out.flush()) {
                self.lose();
                return Err(error);
            }
        }
        Ok(())
    }

    /// Writer thread body. Returns when finished, or with the error that made the channel lost.
    #[cfg(unix)]
    pub fn run_writer(&self, sender: &mut dyn Sender) -> io::Result<()> {
        loop {
            let Some(payload) = self.next_payload() else {
                return Ok(());
            };
            let outcome = match &payload {
                #[cfg(feature = "fault-hooks")]
                Payload::OwnedFault { bytes, pty, fault } => pty
                    .lend_master(|fd| {
                        let mut faulty = Faulty {
                            inner: &mut *sender,
                            fault: *fault,
                            first: true,
                        };
                        write_frame(&mut faulty, bytes, Some(fd.as_raw_fd()))
                    })
                    .and_then(|sent| sent),
                Payload::Plain(bytes) => write_frame(sender, bytes, None),
                Payload::Owned { bytes, pty } => pty
                    .lend_master(|fd| write_frame(sender, bytes, Some(fd.as_raw_fd())))
                    .and_then(|sent| sent),
            };
            if let Err(error) = outcome {
                self.lose();
                return Err(error);
            }
        }
    }
}

/// The transport the writer uses; the production one is a socket, the tests inject faults.
/// (POSIX only: Windows frames carry no handle, E1 7, and use `run_writer_plain`.)
#[cfg(unix)]
pub trait Sender {
    /// First send of a frame, with one descriptor attached (`SCM_RIGHTS`) when given.
    fn send_first(&mut self, data: &[u8], descriptor: Option<RawFd>) -> io::Result<usize>;
    /// Continuation of a frame whose first part was accepted: plain bytes, never a descriptor.
    fn send_more(&mut self, data: &[u8]) -> io::Result<usize>;
}

/// One complete frame (6.1 step 4). A first send that accepts at least one byte has transferred
/// the descriptor; the remainder follows by plain sends and the descriptor is never attached
/// twice. Zero progress, or a failure other than `EINTR` before the first byte, means nothing was
/// transferred: it is an error and the rest of the frame is NOT sent (no byte would carry the
/// descriptor).
#[cfg(unix)]
pub fn write_frame(
    sender: &mut dyn Sender,
    bytes: &[u8],
    descriptor: Option<RawFd>,
) -> io::Result<()> {
    let mut offset = loop {
        match sender.send_first(bytes, descriptor) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::WriteZero,
                    "zero progress on first send",
                ))
            }
            Ok(n) => break n,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    };
    while offset < bytes.len() {
        match sender.send_more(&bytes[offset..]) {
            Ok(0) => return Err(io::Error::new(io::ErrorKind::WriteZero, "zero progress")),
            Ok(n) => offset += n,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

#[cfg(feature = "fault-hooks")]
struct Faulty<'a> {
    inner: &'a mut dyn Sender,
    fault: SendFault,
    first: bool,
}
#[cfg(feature = "fault-hooks")]
impl Sender for Faulty<'_> {
    fn send_first(&mut self, data: &[u8], descriptor: Option<RawFd>) -> io::Result<usize> {
        match (self.fault, std::mem::replace(&mut self.first, false)) {
            (SendFault::Zero, true) => Ok(0),
            (SendFault::Short, true) => self
                .inner
                .send_first(&data[..3.min(data.len())], descriptor),
            _ => self.inner.send_first(data, descriptor),
        }
    }
    fn send_more(&mut self, data: &[u8]) -> io::Result<usize> {
        self.inner.send_more(data)
    }
}

/// The production sender: the blocking control socket.
#[cfg(unix)]
pub struct SocketSender(pub RawFd);

#[cfg(target_os = "linux")]
const SEND_FLAGS: libc::c_int = libc::MSG_NOSIGNAL;
#[cfg(all(unix, not(target_os = "linux")))]
const SEND_FLAGS: libc::c_int = 0; // SO_NOSIGPIPE is set on the socket at startup (macOS)

#[cfg(unix)]
impl Sender for SocketSender {
    fn send_first(&mut self, data: &[u8], descriptor: Option<RawFd>) -> io::Result<usize> {
        let mut iov = libc::iovec {
            iov_base: data.as_ptr() as *mut libc::c_void,
            iov_len: data.len(),
        };
        // SAFETY: zeroed msghdr is a valid empty message; the fields set below point at live
        // locals for the duration of the call.
        let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
        message.msg_iov = &mut iov;
        message.msg_iovlen = 1;
        let space = unsafe { libc::CMSG_SPACE(std::mem::size_of::<RawFd>() as u32) } as usize;
        let mut control = vec![0u8; space];
        if let Some(fd) = descriptor {
            message.msg_control = control.as_mut_ptr().cast();
            message.msg_controllen = space as _;
            // SAFETY: msg_control points at `space` zeroed bytes, enough for one RawFd cmsg.
            unsafe {
                let header = libc::CMSG_FIRSTHDR(&message);
                (*header).cmsg_level = libc::SOL_SOCKET;
                (*header).cmsg_type = libc::SCM_RIGHTS;
                (*header).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<RawFd>() as u32) as _;
                std::ptr::copy_nonoverlapping(
                    (&fd as *const RawFd).cast::<u8>(),
                    libc::CMSG_DATA(header),
                    std::mem::size_of::<RawFd>(),
                );
            }
        }
        // SAFETY: valid descriptor and message.
        let sent = unsafe { libc::sendmsg(self.0, &message, SEND_FLAGS) };
        if sent < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(sent as usize)
        }
    }

    fn send_more(&mut self, data: &[u8]) -> io::Result<usize> {
        // SAFETY: valid descriptor and byte slice.
        let sent = unsafe { libc::send(self.0, data.as_ptr().cast(), data.len(), SEND_FLAGS) };
        if sent < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(sent as usize)
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::fd::AsRawFd;

    /// Records what the writer sent; `plan` decides each send's outcome.
    struct Scripted {
        first_calls: Vec<(usize, Option<RawFd>)>,
        more_calls: Vec<usize>,
        first_plan: VecDeque<io::Result<usize>>,
        more_plan: VecDeque<io::Result<usize>>,
    }
    impl Scripted {
        fn new(first: Vec<io::Result<usize>>, more: Vec<io::Result<usize>>) -> Self {
            Self {
                first_calls: vec![],
                more_calls: vec![],
                first_plan: first.into(),
                more_plan: more.into(),
            }
        }
    }
    impl Sender for Scripted {
        fn send_first(&mut self, data: &[u8], descriptor: Option<RawFd>) -> io::Result<usize> {
            self.first_calls.push((data.len(), descriptor));
            self.first_plan.pop_front().unwrap_or(Ok(data.len()))
        }
        fn send_more(&mut self, data: &[u8]) -> io::Result<usize> {
            self.more_calls.push(data.len());
            self.more_plan.pop_front().unwrap_or(Ok(data.len()))
        }
    }

    #[test]
    fn a_short_first_write_attaches_the_descriptor_once_and_completes_with_plain_sends() {
        let mut sender = Scripted::new(vec![Ok(3)], vec![Ok(10), Ok(27)]);
        write_frame(&mut sender, &[0u8; 40], Some(9)).unwrap();
        assert_eq!(
            sender.first_calls,
            vec![(40, Some(9))],
            "the descriptor is attached once"
        );
        assert_eq!(
            sender.more_calls,
            vec![37, 27],
            "the remainder follows without a descriptor"
        );
    }

    #[test]
    fn zero_progress_on_the_first_send_never_sends_the_rest_of_the_frame() {
        let mut sender = Scripted::new(vec![Ok(0)], vec![]);
        let error = write_frame(&mut sender, &[0u8; 40], Some(9)).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::WriteZero);
        assert!(
            sender.more_calls.is_empty(),
            "no byte could carry the descriptor"
        );
    }

    #[test]
    fn a_non_eintr_failure_before_the_first_byte_is_an_error_and_sends_nothing_more() {
        let mut sender =
            Scripted::new(vec![Err(io::Error::from_raw_os_error(libc::EPIPE))], vec![]);
        assert!(write_frame(&mut sender, &[0u8; 8], Some(9)).is_err());
        assert!(sender.more_calls.is_empty());
    }

    #[test]
    fn eintr_before_the_first_byte_is_retried_with_the_descriptor() {
        let mut sender = Scripted::new(
            vec![Err(io::Error::from_raw_os_error(libc::EINTR)), Ok(8)],
            vec![],
        );
        write_frame(&mut sender, &[0u8; 8], Some(9)).unwrap();
        assert_eq!(sender.first_calls, vec![(8, Some(9)), (8, Some(9))]);
    }

    #[test]
    fn a_failure_after_the_first_byte_is_an_error() {
        let mut sender = Scripted::new(
            vec![Ok(2)],
            vec![Err(io::Error::from_raw_os_error(libc::ECONNRESET))],
        );
        assert!(write_frame(&mut sender, &[0u8; 8], Some(9)).is_err());
    }

    /// Real kernel: a master written by `SocketSender` arrives, attached to the frame, as a
    /// descriptor of the same PTY.
    #[test]
    fn socket_sender_transfers_a_pty_master_with_scm_rights() {
        let mut pair = [0i32; 2];
        assert_eq!(
            unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_STREAM, 0, pair.as_mut_ptr()) },
            0
        );
        let (pty, _slave) = SessionPty::open(24, 80).unwrap();
        let payload = [7u8; 64];
        pty.lend_master(|fd| {
            write_frame(&mut SocketSender(pair[0]), &payload, Some(fd.as_raw_fd()))
        })
        .unwrap()
        .unwrap();
        let mut data = [0u8; 64];
        let mut iov = libc::iovec {
            iov_base: data.as_mut_ptr().cast(),
            iov_len: 64,
        };
        let space = unsafe { libc::CMSG_SPACE(4) } as usize;
        let mut control = vec![0u8; space];
        let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
        message.msg_iov = &mut iov;
        message.msg_iovlen = 1;
        message.msg_control = control.as_mut_ptr().cast();
        message.msg_controllen = space as _;
        assert_eq!(unsafe { libc::recvmsg(pair[1], &mut message, 0) }, 64);
        let mut received = -1i32;
        unsafe {
            let header = libc::CMSG_FIRSTHDR(&message);
            assert!(!header.is_null() && (*header).cmsg_type == libc::SCM_RIGHTS);
            std::ptr::copy_nonoverlapping(
                libc::CMSG_DATA(header),
                (&mut received as *mut i32).cast::<u8>(),
                4,
            );
        }
        assert_eq!(
            crate::session_pty::slave_name_of(received).unwrap(),
            pty.tty()
        );
        assert_ne!(
            received,
            pty.lend_master(|fd| fd.as_raw_fd()).unwrap(),
            "a duplicate, not the sender's number"
        );
        unsafe {
            libc::close(received);
            libc::close(pair[0]);
            libc::close(pair[1]);
        }
        let _ = std::io::stdout().as_raw_fd();
    }

    #[test]
    fn the_outbound_queue_blocks_producers_when_full_and_never_drops() {
        let out = Arc::new(Outbound::new(1));
        let count = MAX_OUTBOUND_FRAMES + 40;
        let producer = {
            let out = out.clone();
            std::thread::spawn(move || {
                for n in 0..count {
                    out.event(code::SESSION_EXITED, json!({"n": n}));
                }
                out.finish();
            })
        };
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(
            !producer.is_finished(),
            "the producer must wait for the writer, not overrun"
        );
        struct Collect(Vec<u8>);
        impl Sender for Collect {
            fn send_first(&mut self, data: &[u8], _: Option<RawFd>) -> io::Result<usize> {
                self.0.extend_from_slice(data);
                Ok(data.len())
            }
            fn send_more(&mut self, data: &[u8]) -> io::Result<usize> {
                self.0.extend_from_slice(data);
                Ok(data.len())
            }
        }
        let mut sink = Collect(Vec::new());
        out.run_writer(&mut sink).unwrap();
        producer.join().unwrap();
        let mut cursor = 0;
        let mut seen = 0;
        while cursor < sink.0.len() {
            let header = frame::parse_header(
                sink.0[cursor..cursor + 32].try_into().unwrap(),
                frame::Direction::RustToPython,
                1,
            )
            .unwrap();
            let body = &sink.0[cursor + 32..cursor + 32 + header.body_len()];
            assert_eq!(frame::decode_body(header, body).unwrap().meta["n"], seen);
            cursor += 32 + header.body_len();
            seen += 1;
        }
        assert_eq!(seen, count);
    }

    #[test]
    fn a_failed_send_marks_the_channel_lost_and_discards_the_rest() {
        let out = Outbound::new(1);
        out.event(code::SESSION_EXITED, json!({}));
        out.event(code::SESSION_EXITED, json!({}));
        let mut sender =
            Scripted::new(vec![Err(io::Error::from_raw_os_error(libc::EPIPE))], vec![]);
        assert!(out.run_writer(&mut sender).is_err());
        assert!(out.is_lost());
        out.event(code::SESSION_EXITED, json!({})); // discarded, never blocks
    }
}
