//! One Windows session's ConPTY, as one value (design/e1-runtime-protocol-v8b.md 7.1 and the R-U1 rule).
//!
//! Every handle of a session (the caller-side pipe ends, the pseudoconsole, the kill-on-close job and
//! the root process) lives in this one value. It has one constructor; the console-side pipe ends are
//! closed inside it and never stored; no handle value crosses a function boundary together with a
//! session id; and no map from a handle value to a handle exists. The `pty` meta of `SESSION_OWNED`
//! is produced by exactly one method, `owned_record`. `ClosePseudoConsole` needs a `ClosePermit`,
//! which only `CloseGate::wait` can mint, so the pre-close gate of 7.3 cannot be skipped by a caller.
use serde_json::{json, Value};
use std::ffi::{c_void, OsStr};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr::{null, null_mut};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{
    ERROR_MORE_DATA, FILETIME, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::System::Console::{
    ClosePseudoConsole, CreatePseudoConsole, ResizePseudoConsole, COORD, HPCON,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectAssociateCompletionPortInformation,
    JobObjectBasicProcessIdList, JobObjectExtendedLimitInformation, QueryInformationJobObject,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_ASSOCIATE_COMPLETION_PORT,
    JOBOBJECT_BASIC_PROCESS_ID_LIST, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess, GetProcessTimes,
    InitializeProcThreadAttributeList, OpenProcess, ResumeThread, TerminateProcess,
    UpdateProcThreadAttribute, WaitForSingleObject, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT,
    EXTENDED_STARTUPINFO_PRESENT, PROCESS_INFORMATION, PROCESS_QUERY_LIMITED_INFORMATION,
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, STARTUPINFOEXW,
};
use windows_sys::Win32::System::IO::{CreateIoCompletionPort, GetQueuedCompletionStatus};

/// `start_kind` of a Windows identity: the process creation `FILETIME` (100 ns ticks since 1601).
pub const START_KIND: &str = "windows-filetime";

/// A process named by pid and creation time: the pid alone can be reused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    pub pid: u32,
    pub start: u64,
}

impl Identity {
    /// The identity record of the protocol (`pgid` is the pid: Windows has no process groups).
    pub fn record(&self) -> Value {
        json!({"pid":self.pid,"pgid":self.pid,"start_kind":START_KIND,"start_value":self.start.to_string()})
    }
}

struct Root {
    process: OwnedHandle,
    /// The suspended primary thread, until `resume`.
    thread: Option<OwnedHandle>,
    identity: Identity,
}

struct State {
    /// The caller-side ends, until `release_originals` (ADOPT, KILL or loss).
    caller_input: Option<OwnedHandle>,
    caller_output: Option<OwnedHandle>,
    /// `None` once the close thread took it.
    console: Option<HPCON>,
    job: OwnedHandle,
    /// The job's completion port: its messages are the event source for "the tree is gone".
    port: OwnedHandle,
    root: Option<Root>,
}

/// `console` is closed (or was never open): the single-purpose handle is gone.
fn console_closed() -> io::Error {
    io::Error::new(io::ErrorKind::NotConnected, "console already closed")
}

pub struct SessionPty {
    state: Mutex<State>,
}

fn pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
    let (mut read, mut write): (HANDLE, HANDLE) = (null_mut(), null_mut());
    // SAFETY: both output pointers are valid; CreatePipe initializes them on success.
    if unsafe { CreatePipe(&mut read, &mut write, null(), 0) } == 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: two distinct handles, owned by this call path.
    Ok(unsafe {
        (
            OwnedHandle::from_raw_handle(read),
            OwnedHandle::from_raw_handle(write),
        )
    })
}

fn value_of(handle: &OwnedHandle) -> u64 {
    handle.as_raw_handle() as usize as u64
}

fn console_size(rows: u16, cols: u16) -> io::Result<COORD> {
    let (y, x) = (i16::try_from(rows), i16::try_from(cols));
    match (x, y) {
        (Ok(x), Ok(y)) if x > 0 && y > 0 => Ok(COORD { X: x, Y: y }),
        _ => Err(io::Error::other("invalid console size")),
    }
}

fn filetime(file: FILETIME) -> u64 {
    (u64::from(file.dwHighDateTime) << 32) | u64::from(file.dwLowDateTime)
}

fn creation_time(process: HANDLE) -> io::Result<u64> {
    let (mut created, mut exited, mut kernel, mut user): (FILETIME, FILETIME, FILETIME, FILETIME) =
        // SAFETY: FILETIME is plain data; all-zero is valid.
        unsafe { zeroed() };
    // SAFETY: a process handle with query rights and four valid output pointers.
    if unsafe { GetProcessTimes(process, &mut created, &mut exited, &mut kernel, &mut user) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(filetime(created))
}

impl SessionPty {
    /// The only constructor: two anonymous pipes, the pseudoconsole over their console-side ends
    /// (closed right here, the documented pattern) and a kill-on-close job.
    pub fn open(rows: u16, cols: u16) -> io::Result<Self> {
        let size = console_size(rows, cols)?;
        let (console_input, caller_input) = pipe()?;
        let (caller_output, console_output) = pipe()?;
        let mut console: HPCON = 0;
        // SAFETY: live pipe ends in the documented directions and a valid output pointer.
        let result = unsafe {
            CreatePseudoConsole(
                size,
                console_input.as_raw_handle(),
                console_output.as_raw_handle(),
                0,
                &mut console,
            )
        };
        if result < 0 {
            return Err(io::Error::other(format!(
                "CreatePseudoConsole HRESULT {result:#x}"
            )));
        }
        drop(console_input);
        drop(console_output);
        let (job, port) = match new_job() {
            Ok(pair) => pair,
            Err(error) => {
                // SAFETY: the console was just created and is not used elsewhere.
                unsafe { ClosePseudoConsole(console) };
                return Err(error);
            }
        };
        Ok(Self {
            state: Mutex::new(State {
                caller_input: Some(caller_input),
                caller_output: Some(caller_output),
                console: Some(console),
                job,
                port,
                root: None,
            }),
        })
    }

    /// The `pty` meta of `SESSION_OWNED` (5.2): the only producer. Fails once the originals are closed.
    pub fn owned_record(&self) -> io::Result<Value> {
        let state = self.state.lock().unwrap();
        match (&state.caller_input, &state.caller_output) {
            (Some(input), Some(output)) => {
                Ok(json!({"input":value_of(input),"output":value_of(output)}))
            }
            _ => Err(io::Error::other("caller-side ends already closed")),
        }
    }

    /// Lend the two caller-side ends to `inspect` (R-U1). The borrows cannot outlive the call, and
    /// the lock is held so a concurrent close cannot free them mid-use.
    #[cfg(test)]
    pub fn lend_caller_ends<R>(
        &self,
        inspect: impl FnOnce(
            std::os::windows::io::BorrowedHandle<'_>,
            std::os::windows::io::BorrowedHandle<'_>,
        ) -> R,
    ) -> io::Result<R> {
        use std::os::windows::io::AsHandle;
        let state = self.state.lock().unwrap();
        match (&state.caller_input, &state.caller_output) {
            (Some(input), Some(output)) => Ok(inspect(input.as_handle(), output.as_handle())),
            _ => Err(io::Error::other("caller-side ends already closed")),
        }
    }

    /// Close the sidecar's caller-side originals: on `ADOPT` (Python's duplicates exist), or on
    /// `KILL`/loss for a session that never adopted. Returns whether anything was closed.
    pub fn release_originals(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        let had = state.caller_input.is_some() || state.caller_output.is_some();
        state.caller_input = None;
        state.caller_output = None;
        had
    }

    /// Create the root `CREATE_SUSPENDED` on this console and assign it to the job before anything
    /// can run. `env` is the complete environment; `argv` is quoted into one command line.
    pub fn spawn_suspended(
        &self,
        argv: &[String],
        env: &[(String, String)],
        cwd: &str,
    ) -> io::Result<Identity> {
        let mut state = self.state.lock().unwrap();
        if state.root.is_some() {
            return Err(io::Error::other("root already spawned"));
        }
        let console = state.console.ok_or_else(console_closed)?;
        let mut attributes = Attributes::with_console(console)?;
        let mut command = wide(OsStr::new(&command_line(argv)?));
        let environment = environment_block(env);
        let directory = wide(OsStr::new(cwd));
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.as_ptr();
        let mut process = PROCESS_INFORMATION::default();
        // SAFETY: every pointer stays valid for the call; the attribute list holds the live console.
        if unsafe {
            CreateProcessW(
                null(),
                command.as_mut_ptr(),
                null(),
                null(),
                0,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                environment.as_ptr().cast(),
                directory.as_ptr(),
                &startup.StartupInfo,
                &mut process,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: CreateProcessW returned two owned handles.
        let (process, thread) = unsafe {
            (
                OwnedHandle::from_raw_handle(process.hProcess),
                OwnedHandle::from_raw_handle(process.hThread),
            )
        };
        // SAFETY: the job and the (suspended) process are live handles.
        if unsafe { AssignProcessToJobObject(state.job.as_raw_handle(), process.as_raw_handle()) }
            == 0
        {
            let error = io::Error::last_os_error();
            // SAFETY: the process never ran; nothing else can observe it.
            unsafe { TerminateProcess(process.as_raw_handle(), 1) };
            return Err(error);
        }
        let identity = Identity {
            pid: pid_of(&process)?,
            start: creation_time(process.as_raw_handle())?,
        };
        state.root = Some(Root {
            process,
            thread: Some(thread),
            identity: identity.clone(),
        });
        Ok(identity)
    }

    /// The gate release of the Windows analogue (5.2 I1): called only after the identity is durable.
    pub fn resume(&self) -> io::Result<()> {
        let mut state = self.state.lock().unwrap();
        let root = state
            .root
            .as_mut()
            .ok_or_else(|| io::Error::other("no root to resume"))?;
        let thread = root
            .thread
            .take()
            .ok_or_else(|| io::Error::other("root already resumed"))?;
        // SAFETY: the suspended primary thread handle returned by CreateProcessW.
        if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn root_identity(&self) -> Option<Identity> {
        let state = self.state.lock().unwrap();
        state.root.as_ref().map(|root| root.identity.clone())
    }

    /// Block until the root exits (`Some(exit code)`) or `limit` elapsed (`None`): an event wait on
    /// the process handle, taken as a duplicate so the session lock is not held while waiting.
    pub fn wait_root(&self, limit: Duration) -> io::Result<Option<u32>> {
        let process = {
            let state = self.state.lock().unwrap();
            let root = state
                .root
                .as_ref()
                .ok_or_else(|| io::Error::other("no root"))?;
            root.process.try_clone()?
        };
        let millis = u32::try_from(limit.as_millis())
            .unwrap_or(u32::MAX - 1)
            .min(u32::MAX - 1);
        // SAFETY: a live process handle owned by this call.
        match unsafe { WaitForSingleObject(process.as_raw_handle(), millis) } {
            WAIT_OBJECT_0 => {
                let mut code = 0u32;
                // SAFETY: as above, with a valid output pointer.
                if unsafe { GetExitCodeProcess(process.as_raw_handle(), &mut code) } == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(Some(code))
            }
            WAIT_TIMEOUT => Ok(None),
            _ => Err(io::Error::last_os_error()),
        }
    }

    /// Kill the whole tree by identity: every process of the job (and the root before it was resumed).
    pub fn terminate_tree(&self) -> io::Result<()> {
        let state = self.state.lock().unwrap();
        // SAFETY: the job handle is live while the state is.
        if unsafe { TerminateJobObject(state.job.as_raw_handle(), 1) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    /// Every process currently in the job, with its identity (descendants, event source for cleanup).
    pub fn members(&self) -> io::Result<Vec<Identity>> {
        let pids = {
            let state = self.state.lock().unwrap();
            job_pids(&state.job)?
        };
        let mut found = Vec::with_capacity(pids.len());
        for pid in pids {
            // SAFETY: plain OpenProcess; a vanished process is skipped below.
            let raw = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
            if raw.is_null() {
                continue;
            }
            // SAFETY: OpenProcess returned an owned handle.
            let process = unsafe { OwnedHandle::from_raw_handle(raw) };
            if let Ok(start) = creation_time(process.as_raw_handle()) {
                found.push(Identity { pid, start });
            }
        }
        Ok(found)
    }

    /// `JobObjectBasicProcessIdList` is empty: no member is left (the exit event of the whole tree).
    pub fn tree_is_empty(&self) -> io::Result<bool> {
        let state = self.state.lock().unwrap();
        Ok(job_pids(&state.job)?.is_empty())
    }

    /// Block until no process is left in the job, or `limit` elapsed (`false`). The job's completion
    /// port delivers `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO`; the membership is re-read after every wake,
    /// so a message for a process that was replaced by a new member never ends the wait early.
    pub fn wait_tree_empty(&self, limit: Duration) -> io::Result<bool> {
        let port = self.state.lock().unwrap().port.try_clone()?;
        let deadline = Instant::now() + limit;
        loop {
            if self.tree_is_empty()? {
                return Ok(true);
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Ok(false);
            }
            let (mut message, mut key, mut overlapped) = (0u32, 0usize, null_mut());
            let millis = u32::try_from(left.as_millis()).unwrap_or(u32::MAX - 1);
            // SAFETY: a live completion port and three valid output pointers.
            let ok = unsafe {
                GetQueuedCompletionStatus(
                    port.as_raw_handle(),
                    &mut message,
                    &mut key,
                    &mut overlapped,
                    millis,
                )
            };
            if ok == 0 && overlapped.is_null() {
                let error = io::Error::last_os_error();
                if error.raw_os_error() != Some(WAIT_TIMEOUT as i32) {
                    return Err(error);
                }
            }
        }
    }

    /// `ResizePseudoConsole` (the single-purpose use of the `HPCON`, 9.x).
    pub fn resize(&self, rows: u16, cols: u16) -> io::Result<()> {
        let size = console_size(rows, cols)?;
        let state = self.state.lock().unwrap();
        let console = state.console.ok_or_else(console_closed)?;
        // SAFETY: a live console; resizing writes nothing the caller owns.
        let result = unsafe { ResizePseudoConsole(console, size) };
        if result < 0 {
            return Err(io::Error::other(format!(
                "ResizePseudoConsole HRESULT {result:#x}"
            )));
        }
        Ok(())
    }

    /// `ClosePseudoConsole` on a dedicated thread, never on the caller's (7.2). The permit proves the
    /// pre-close gate of 7.3 was satisfied. The receiver yields once the call returned; the console is
    /// taken out of the value first, so it can be closed at most once.
    pub fn close_console(self: &Arc<Self>, _permit: ClosePermit) -> io::Result<mpsc::Receiver<()>> {
        let (done_tx, done_rx) = mpsc::channel();
        let session = Arc::clone(self);
        std::thread::Builder::new()
            .name("conpty-close".into())
            .spawn(move || {
                let console = session.state.lock().unwrap().console.take();
                if let Some(console) = console {
                    // SAFETY: taken out of the value above, so closed exactly once; the gate
                    // guarantees the output is serviced or its read end closed.
                    unsafe { ClosePseudoConsole(console) };
                }
                let _ = done_tx.send(());
            })?;
        Ok(done_rx)
    }
}

impl Drop for SessionPty {
    fn drop(&mut self) {
        // A session value dropped without the gate (sidecar teardown): the originals close first,
        // which is the documented condition for a safe `ClosePseudoConsole`; the job object closes
        // with `KILL_ON_JOB_CLOSE`, so no member outlives the value.
        let state = self.state.get_mut().unwrap_or_else(|e| e.into_inner());
        state.caller_input = None;
        state.caller_output = None;
        if let Some(console) = state.console.take() {
            // SAFETY: taken out of the value, closed once. The originals above are already closed.
            unsafe { ClosePseudoConsole(console) };
        }
    }
}

fn new_job() -> io::Result<(OwnedHandle, OwnedHandle)> {
    // SAFETY: no attributes, no name.
    let raw = unsafe { CreateJobObjectW(null(), null()) };
    if raw.is_null() {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: CreateJobObjectW returned an owned handle.
    let job = unsafe { OwnedHandle::from_raw_handle(raw) };
    // SAFETY: plain data.
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: a live job and a correctly sized limits struct.
    if unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast::<c_void>(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: a fresh port not tied to a file handle; one concurrent thread.
    let raw = unsafe { CreateIoCompletionPort(-1isize as HANDLE, null_mut(), 0, 1) };
    if raw.is_null() {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: CreateIoCompletionPort returned an owned handle.
    let port = unsafe { OwnedHandle::from_raw_handle(raw) };
    let association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
        CompletionKey: null_mut(),
        CompletionPort: port.as_raw_handle(),
    };
    // SAFETY: a live job, a live port and a correctly sized association struct.
    if unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectAssociateCompletionPortInformation,
            (&association as *const JOBOBJECT_ASSOCIATE_COMPLETION_PORT).cast::<c_void>(),
            size_of::<JOBOBJECT_ASSOCIATE_COMPLETION_PORT>() as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok((job, port))
}

fn pid_of(process: &OwnedHandle) -> io::Result<u32> {
    use windows_sys::Win32::System::Threading::GetProcessId;
    // SAFETY: a live process handle.
    match unsafe { GetProcessId(process.as_raw_handle()) } {
        0 => Err(io::Error::last_os_error()),
        pid => Ok(pid),
    }
}

fn job_pids(job: &OwnedHandle) -> io::Result<Vec<u32>> {
    let mut capacity = 64usize;
    loop {
        // Header (two u32) plus `capacity` pointer-sized ids, in pointer-aligned storage.
        let words = 1 + capacity;
        let mut storage = vec![0usize; words];
        // SAFETY: the buffer is pointer-aligned and `words * size_of::<usize>()` bytes long.
        let ok = unsafe {
            QueryInformationJobObject(
                job.as_raw_handle(),
                JobObjectBasicProcessIdList,
                storage.as_mut_ptr().cast::<c_void>(),
                (words * size_of::<usize>()) as u32,
                null_mut(),
            )
        };
        if ok == 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(ERROR_MORE_DATA as i32) && capacity < 1 << 16 {
                capacity *= 8;
                continue;
            }
            return Err(error);
        }
        let list = storage.as_ptr().cast::<JOBOBJECT_BASIC_PROCESS_ID_LIST>();
        // SAFETY: the kernel filled the header and `NumberOfProcessIdsInList` ids behind it.
        let count = unsafe { (*list).NumberOfProcessIdsInList } as usize;
        let ids = unsafe { std::ptr::addr_of!((*list).ProcessIdList).cast::<usize>() };
        return Ok((0..count.min(capacity))
            // SAFETY: bounded by the count the kernel wrote and by our capacity.
            .map(|index| unsafe { *ids.add(index) } as u32)
            .collect());
    }
}

// ---- the pre-close gate (7.3) -------------------------------------------------------------------

/// Why `ClosePseudoConsole` may be called (7.3): the output is serviced, or its read end is closed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GateReason {
    /// Python's pump is reading continuously (`TTY_SERVICING`).
    Servicing,
    /// Python closed its read end (`TTY_EOF`: eof, error or abort).
    StreamClosed,
    /// Rust closed its own originals for a session that never reached `ADOPT`.
    OriginalsClosed,
}

/// Proof that the gate was satisfied; only `CloseGate::wait` creates one.
pub struct ClosePermit(());

pub struct CloseGate {
    state: Mutex<Option<GateReason>>,
    changed: Condvar,
}

impl Default for CloseGate {
    fn default() -> Self {
        Self::new()
    }
}

impl CloseGate {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(None),
            changed: Condvar::new(),
        }
    }

    pub fn satisfy(&self, reason: GateReason) {
        let mut state = self.state.lock().unwrap();
        state.get_or_insert(reason);
        self.changed.notify_all();
    }

    pub fn reason(&self) -> Option<GateReason> {
        *self.state.lock().unwrap()
    }

    /// Wait (event-driven, bounded by `limit` = `cleanup_ms`) for the gate. `None`: it was not
    /// satisfied, so the console must not be closed and cleanup is reported incomplete.
    pub fn wait(&self, limit: Duration) -> Option<ClosePermit> {
        let deadline = Instant::now() + limit;
        let mut state = self.state.lock().unwrap();
        while state.is_none() {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return None;
            }
            state = self.changed.wait_timeout(state, left).unwrap().0;
        }
        Some(ClosePermit(()))
    }
}

// ---- process creation helpers -------------------------------------------------------------------

fn wide(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain([0]).collect()
}

/// Quote `argv` into one command line by the rules `CommandLineToArgvW` / the C runtime parse.
pub fn command_line(argv: &[String]) -> io::Result<String> {
    if argv.is_empty() || argv[0].is_empty() {
        return Err(io::Error::other("empty argv"));
    }
    let mut line = String::new();
    for (index, arg) in argv.iter().enumerate() {
        if arg.contains('\0') {
            return Err(io::Error::other("NUL in argv"));
        }
        if index > 0 {
            line.push(' ');
        }
        quote_argument(arg, &mut line);
    }
    Ok(line)
}

fn quote_argument(arg: &str, line: &mut String) {
    if !arg.is_empty() && !arg.contains([' ', '\t', '\n', '\u{b}', '"']) {
        line.push_str(arg);
        return;
    }
    line.push('"');
    let mut backslashes = 0usize;
    for ch in arg.chars() {
        match ch {
            '\\' => backslashes += 1,
            '"' => {
                line.extend(std::iter::repeat_n('\\', backslashes * 2 + 1));
                line.push('"');
                backslashes = 0;
            }
            other => {
                line.extend(std::iter::repeat_n('\\', backslashes));
                line.push(other);
                backslashes = 0;
            }
        }
    }
    line.extend(std::iter::repeat_n('\\', backslashes * 2));
    line.push('"');
}

/// The complete environment as a UTF-16 block, sorted case-insensitively by name as Windows expects.
fn environment_block(env: &[(String, String)]) -> Vec<u16> {
    let mut entries: Vec<&(String, String)> = env.iter().collect();
    entries.sort_by_key(|(name, _)| name.to_uppercase());
    let mut block = Vec::new();
    for (name, value) in entries {
        block.extend(OsStr::new(name).encode_wide());
        block.push(u16::from(b'='));
        block.extend(OsStr::new(value).encode_wide());
        block.push(0);
    }
    if block.is_empty() {
        block.push(0);
    }
    block.push(0);
    block
}

struct Attributes {
    storage: Vec<usize>,
}

impl Attributes {
    fn with_console(console: HPCON) -> io::Result<Self> {
        let mut bytes = 0usize;
        // SAFETY: the first call only asks Windows for the required buffer length.
        unsafe { InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut bytes) };
        if bytes == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut this = Self {
            storage: vec![0; bytes.div_ceil(size_of::<usize>())],
        };
        let list = this.storage.as_mut_ptr().cast();
        // SAFETY: pointer-aligned storage of the requested length.
        if unsafe { InitializeProcThreadAttributeList(list, 1, 0, &mut bytes) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the list is initialised; the console outlives the following CreateProcessW.
        if unsafe {
            UpdateProcThreadAttribute(
                list,
                0,
                PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE as usize,
                (&console as *const HPCON).cast(),
                size_of::<HPCON>(),
                null_mut(),
                null(),
            )
        } == 0
        {
            let error = io::Error::last_os_error();
            // SAFETY: initialised above.
            unsafe { DeleteProcThreadAttributeList(list) };
            return Err(error);
        }
        Ok(this)
    }

    fn as_ptr(&mut self) -> *mut c_void {
        self.storage.as_mut_ptr().cast()
    }
}

impl Drop for Attributes {
    fn drop(&mut self) {
        // SAFETY: the list only escapes to CreateProcessW while `self` is alive.
        unsafe { DeleteProcThreadAttributeList(self.as_ptr()) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn command_lines_quote_like_the_c_runtime_parses_them() {
        let line = |args: &[&str]| {
            command_line(&args.iter().map(|a| (*a).to_owned()).collect::<Vec<_>>()).unwrap()
        };
        assert_eq!(line(&["cmd.exe", "/d", "/c"]), "cmd.exe /d /c");
        assert_eq!(
            line(&["C:\\Program Files\\x.exe", "a b"]),
            "\"C:\\Program Files\\x.exe\" \"a b\""
        );
        assert_eq!(line(&["x", "say \"hi\""]), "x \"say \\\"hi\\\"\"");
        assert_eq!(line(&["x", "dir\\ "]), "x \"dir\\ \"");
        assert_eq!(line(&["x", "tail\\\\"]), "x tail\\\\");
        assert_eq!(line(&["x", ""]), "x \"\"");
        assert!(command_line(&[]).is_err());
    }

    #[test]
    fn the_environment_block_is_sorted_and_double_terminated() {
        let block = environment_block(&[("b".into(), "2".into()), ("A".into(), "1".into())]);
        let text = String::from_utf16(&block).unwrap();
        assert_eq!(text, "A=1\0b=2\0\0");
    }

    /// The gate mints a permit only after it was satisfied, and never after the bound alone.
    #[test]
    fn the_close_gate_opens_only_when_satisfied() {
        let gate = CloseGate::new();
        assert!(gate.wait(Duration::from_millis(20)).is_none());
        gate.satisfy(GateReason::Servicing);
        gate.satisfy(GateReason::StreamClosed);
        assert_eq!(gate.reason(), Some(GateReason::Servicing));
        assert!(gate.wait(Duration::ZERO).is_some());
    }

    /// R-U1 (a): across interleaved creates and closes (so handle values are reused) every session's
    /// record names exactly its own two caller-side ends, and none of any other live session's.
    #[test]
    fn owned_record_always_names_this_sessions_own_two_ends() {
        let mut live: Vec<SessionPty> = Vec::new();
        let mut seen = HashSet::new();
        let mut reused = false;
        for round in 0..24 {
            let pty = SessionPty::open(24, 80).unwrap();
            let record = pty.owned_record().unwrap();
            for key in ["input", "output"] {
                reused |= !seen.insert(record[key].as_u64().unwrap());
            }
            live.push(pty);
            if round % 3 == 2 {
                live.remove(round % live.len());
            }
            let mut values = HashSet::new();
            for pty in &live {
                let record = pty.owned_record().unwrap();
                let (input, output) = (record["input"].as_u64(), record["output"].as_u64());
                let (own_in, own_out) = pty
                    .lend_caller_ends(|i, o| {
                        (
                            i.as_raw_handle() as usize as u64,
                            o.as_raw_handle() as usize as u64,
                        )
                    })
                    .unwrap();
                assert_eq!((input, output), (Some(own_in), Some(own_out)));
                assert!(
                    values.insert(own_in) && values.insert(own_out),
                    "two sessions share a handle"
                );
            }
        }
        assert!(live.len() >= 8);
        assert!(reused, "the test never reused a handle value");
    }

    #[test]
    fn originals_are_released_once_and_the_record_then_refuses() {
        let pty = SessionPty::open(30, 100).unwrap();
        assert!(pty.owned_record().is_ok());
        pty.resize(40, 120).unwrap();
        assert!(pty.release_originals());
        assert!(!pty.release_originals());
        assert!(pty.owned_record().is_err());
        assert!(pty.lend_caller_ends(|_, _| ()).is_err());
    }

    /// The root runs only after `resume`, lives in the job, and `terminate_tree` ends it.
    #[test]
    fn a_suspended_root_is_in_the_job_runs_after_resume_and_dies_with_the_tree() {
        let pty = Arc::new(SessionPty::open(24, 80).unwrap());
        let comspec = std::env::var("ComSpec").unwrap();
        let env: Vec<(String, String)> = std::env::vars().collect();
        let identity = pty
            .spawn_suspended(&[comspec, "/d".into(), "/q".into(), "/k".into()], &env, ".")
            .unwrap();
        assert_eq!(pty.root_identity(), Some(identity.clone()));
        assert!(pty.members().unwrap().contains(&identity));
        assert_eq!(pty.wait_root(Duration::from_millis(50)).unwrap(), None);
        pty.resume().unwrap();
        assert!(pty.resume().is_err());
        pty.terminate_tree().unwrap();
        assert!(pty.wait_root(Duration::from_secs(5)).unwrap().is_some());
        assert!(pty.tree_is_empty().unwrap());
        pty.release_originals();
        let gate = CloseGate::new();
        gate.satisfy(GateReason::OriginalsClosed);
        let permit = gate.wait(Duration::from_secs(1)).unwrap();
        let done = pty.close_console(permit).unwrap();
        done.recv_timeout(Duration::from_secs(5)).unwrap();
    }

    /// R-U1 (b): static guard over the Windows PTY-handling modules. No function signature may carry a
    /// raw handle together with a session id, and no map keyed by or holding a handle may exist.
    #[test]
    fn no_function_takes_a_raw_handle_together_with_a_session_id() {
        let sources = [("session_pty_win.rs", include_str!("session_pty_win.rs"))];
        for (name, source) in sources {
            let production = source.split("#[cfg(test)]").next().unwrap();
            let mut signature = String::new();
            let mut in_signature = false;
            for line in production.lines() {
                let trimmed = line.trim_start();
                if !in_signature
                    && (trimmed.starts_with("fn ")
                        || trimmed.starts_with("pub fn ")
                        || trimmed.starts_with("pub(super) fn ")
                        || trimmed.starts_with("pub(crate) fn "))
                {
                    in_signature = true;
                    signature.clear();
                }
                if in_signature {
                    signature.push_str(trimmed);
                    signature.push(' ');
                    if trimmed.contains('{') || trimmed.ends_with(';') {
                        in_signature = false;
                        let head = signature.split(" -> ").next().unwrap_or(&signature);
                        let raw = head.contains("HANDLE")
                            || head.contains("OwnedHandle")
                            || head.contains("RawHandle")
                            || head.contains("HPCON")
                            || head.contains("handle: u64");
                        let session = head.contains("session_id")
                            || head.contains("sid:")
                            || head.contains("id: &str")
                            || head.contains("session:");
                        assert!(
                            !(raw && session),
                            "{name}: raw handle with a session id: {head}"
                        );
                    }
                }
                assert!(
                    !(trimmed.contains("HashMap<")
                        && (trimmed.contains("HANDLE")
                            || trimmed.contains("OwnedHandle")
                            || trimmed.contains("HPCON")
                            || map_keyed_by_a_number(trimmed))),
                    "{name}: a map holds or is keyed by a raw handle: {trimmed}"
                );
            }
        }
    }

    fn map_keyed_by_a_number(line: &str) -> bool {
        line.contains("HashMap<u64") || line.contains("HashMap<usize")
    }
}
