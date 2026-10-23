//! E5 Windows-only, real-kernel probes. This binary is built only with `windows-gates`.
#[cfg(windows)]
mod windows {
    use serde_json::json;
    use std::ffi::OsStr;
    use std::io::{self, BufRead};
    use std::mem::size_of;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::ptr::{null, null_mut};
    use std::sync::mpsc;
    use std::time::Duration;
    use windows_sys::Win32::Foundation::WAIT_OBJECT_0;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::Console::{
        ClosePseudoConsole, CreatePseudoConsole, COORD, HPCON,
    };
    use windows_sys::Win32::System::Pipes::CreatePipe;
    use windows_sys::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, InitializeProcThreadAttributeList,
        UpdateProcThreadAttribute, WaitForSingleObject, EXTENDED_STARTUPINFO_PRESENT,
        PROCESS_INFORMATION, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, STARTF_USESTDHANDLES,
        STARTUPINFOEXW,
    };

    fn pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
        let (mut read, mut write): (HANDLE, HANDLE) = (null_mut(), null_mut());
        // SAFETY: both output pointers are valid and CreatePipe initializes them on success.
        if unsafe { CreatePipe(&mut read, &mut write, null(), 0) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the two handles are distinct and owned by this process.
        Ok(unsafe {
            (
                OwnedHandle::from_raw_handle(read),
                OwnedHandle::from_raw_handle(write),
            )
        })
    }

    fn value(handle: &OwnedHandle) -> u64 {
        handle.as_raw_handle() as usize as u64
    }

    fn wait_for_parent() -> io::Result<()> {
        let (send, receive) = mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            let result = io::stdin().lock().read_line(&mut line).map(|_| line);
            let _ = send.send(result);
        });
        let line = receive
            .recv_timeout(Duration::from_secs(15))
            .map_err(|_| io::Error::other("parent confirmation timed out"))??;
        if line.trim() != "done" {
            return Err(io::Error::other("parent did not confirm handle release"));
        }
        Ok(())
    }

    fn wide(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain([0]).collect()
    }

    struct Attributes {
        storage: Vec<usize>,
    }

    impl Attributes {
        fn with_console(hpc: HPCON) -> io::Result<Self> {
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
            // SAFETY: storage is pointer-aligned and holds the requested number of bytes.
            if unsafe { InitializeProcThreadAttributeList(list, 1, 0, &mut bytes) } == 0 {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: the list is initialized; hpc is valid for the following CreateProcessW.
            if unsafe {
                UpdateProcThreadAttribute(
                    list,
                    0,
                    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE as usize,
                    // The attribute value IS the HPCON (documented), not a pointer to it.
                    hpc as *const std::ffi::c_void,
                    size_of::<HPCON>(),
                    null_mut(),
                    null(),
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(this)
        }

        fn as_ptr(&mut self) -> *mut std::ffi::c_void {
            self.storage.as_mut_ptr().cast()
        }
    }

    impl Drop for Attributes {
        fn drop(&mut self) {
            // SAFETY: storage only escapes to CreateProcessW while self remains alive.
            unsafe { DeleteProcThreadAttributeList(self.as_ptr()) };
        }
    }

    fn spawn_shell(hpc: HPCON) -> io::Result<OwnedHandle> {
        let mut attrs = Attributes::with_console(hpc)?;
        let shell =
            std::env::var_os("ComSpec").ok_or_else(|| io::Error::other("ComSpec is missing"))?;
        let application = wide(&shell);
        let mut command = wide(OsStr::new("cmd.exe /d /q /k"));
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        // Null std handles: a redirected parent must not hand its stdio to the child.
        startup.StartupInfo.dwFlags |= STARTF_USESTDHANDLES;
        startup.lpAttributeList = attrs.as_ptr();
        let mut process = PROCESS_INFORMATION::default();
        // SAFETY: all pointers remain valid throughout CreateProcessW; the attribute list holds
        // the live pseudoconsole, and the returned process/thread handles are closed below.
        if unsafe {
            CreateProcessW(
                application.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                0,
                EXTENDED_STARTUPINFO_PRESENT,
                null(),
                null(),
                &startup.StartupInfo,
                &mut process,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: CreateProcessW returned two owned handles.
        unsafe { CloseHandle(process.hThread) };
        Ok(unsafe { OwnedHandle::from_raw_handle(process.hProcess) })
    }

    pub fn run() -> io::Result<()> {
        let mode = std::env::args().nth(1).unwrap_or_default();
        if mode == "pipes" {
            let (read, write) = pipe()?;
            println!("{}", json!({"read": value(&read), "write": value(&write)}));
            wait_for_parent()?;
            return Ok(());
        }
        if mode != "conpty" {
            return Err(io::Error::other("expected pipes or conpty"));
        }
        let (in_read, in_write) = pipe()?;
        let (out_read, out_write) = pipe()?;
        let mut hpc: HPCON = 0;
        // SAFETY: CreatePseudoConsole receives live pipe ends in the documented directions.
        let result = unsafe {
            CreatePseudoConsole(
                COORD { X: 80, Y: 24 },
                in_read.as_raw_handle(),
                out_write.as_raw_handle(),
                0,
                &mut hpc,
            )
        };
        if result < 0 {
            return Err(io::Error::other(format!(
                "CreatePseudoConsole HRESULT {result:#x}"
            )));
        }
        drop(in_read);
        drop(out_write);
        let child = spawn_shell(hpc)?;
        println!(
            "{}",
            json!({"input": value(&in_write), "output": value(&out_read)})
        );
        wait_for_parent()?;
        drop(in_write);
        drop(out_read);
        // SAFETY: the child process handle remains valid; this probe never waits without a bound.
        if unsafe { WaitForSingleObject(child.as_raw_handle(), 5_000) } != WAIT_OBJECT_0 {
            return Err(io::Error::other(
                "ConPTY child did not exit within 5 seconds",
            ));
        }
        let (send, receive) = mpsc::channel();
        std::thread::spawn(move || {
            // SAFETY: hpc was created above, and the Python caller closed its read duplicate.
            unsafe { ClosePseudoConsole(hpc) };
            let _ = send.send(());
        });
        receive
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| io::Error::other("ClosePseudoConsole timed out"))?;
        Ok(())
    }
}

#[cfg(windows)]
fn main() {
    if let Err(error) = windows::run() {
        eprintln!("E5 Windows probe failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {}
