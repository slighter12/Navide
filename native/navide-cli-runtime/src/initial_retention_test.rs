//! Caller-unit control: a genuinely inspected, now-reparented owned row.
use super::*;
use std::io::{BufRead, Write};
use std::os::unix::process::CommandExt;

#[test]
fn initial_retention_keeps_detached_real_owned_rows() {
    let script = "import os,signal,sys\nsignal.signal(signal.SIGCHLD,signal.SIG_IGN)\nr,w=os.pipe()\nc=os.fork()\nif c==0:\n os.setsid()\n os.write(w,b'1')\n while True: signal.pause()\nos.read(r,1)\nprint(c,flush=True)\nsys.stdin.readline()\nos._exit(0)";
    let mut root = Command::new("/usr/bin/python3")
        .args(["-I", "-S", "-c", script])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .process_group(0)
        .spawn()
        .unwrap();
    let pid = root.id() as i32;
    let mut line = String::new();
    io::BufReader::new(root.stdout.as_mut().unwrap())
        .read_line(&mut line)
        .unwrap();
    let detached_pid: i32 = line.trim().parse().unwrap();
    // Independently capture only this actual Child's just-created child, before
    // testing the table walker. Do not signal a self-reported PID without birth.
    let mut captured: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
    let bytes = unsafe {
        libc::proc_pidinfo(
            detached_pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut captured as *mut libc::proc_bsdinfo).cast(),
            std::mem::size_of::<libc::proc_bsdinfo>() as i32,
        )
    };
    assert_eq!(bytes as usize, std::mem::size_of::<libc::proc_bsdinfo>());
    assert_eq!(captured.pbi_pid, detached_pid as u32);
    assert_eq!(captured.pbi_ppid, pid as u32);
    let mut root = Some(root);
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let known = snapshot(Some(pid), &Table::new()).unwrap();
        assert!(known.contains_key(&detached_pid));
        root.as_mut()
            .unwrap()
            .stdin
            .as_mut()
            .unwrap()
            .write_all(b"RETIRE\n")
            .unwrap();
        root.as_mut().unwrap().wait().unwrap();
        let table = snapshot(None, &known).unwrap();
        assert!(!table.contains_key(&pid));
        assert_eq!(table[&detached_pid].0, 1);
        assert!(descendants(&table, pid).is_empty()); // The old narrowing loses it.
        let mut owned = Owned {
            pid,
            root: identity(pid, &known[&pid]),
            descendants: Table::new(),
        };
        root.take();
        // Identical helper used by the real spawn initializer before owned ACK.
        retain_owned(&mut owned, &table);
        assert_eq!(owned.descendants[&detached_pid], table[&detached_pid]);
        assert!(!owned.descendants.contains_key(&pid));
    }));
    if let Some(mut root) = root.take() {
        let _ = root.kill();
        root.wait().unwrap();
    }
    let mut actual: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
    let bytes = unsafe {
        libc::proc_pidinfo(
            detached_pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut actual as *mut libc::proc_bsdinfo).cast(),
            std::mem::size_of::<libc::proc_bsdinfo>() as i32,
        )
    };
    if bytes as usize == std::mem::size_of::<libc::proc_bsdinfo>()
        && actual.pbi_pid == captured.pbi_pid
        && actual.pbi_start_tvsec == captured.pbi_start_tvsec
        && actual.pbi_start_tvusec == captured.pbi_start_tvusec
    {
        unsafe {
            libc::kill(detached_pid, libc::SIGKILL);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let bytes = unsafe {
            libc::proc_pidinfo(
                detached_pid,
                libc::PROC_PIDTBSDINFO,
                0,
                (&mut actual as *mut libc::proc_bsdinfo).cast(),
                std::mem::size_of::<libc::proc_bsdinfo>() as i32,
            )
        };
        if bytes == 0 && unsafe { *libc::__error() } == libc::ESRCH {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "captured child not inspected gone"
        );
        thread::sleep(Duration::from_millis(5));
    }
    if let Err(error) = result {
        std::panic::resume_unwind(error);
    }
}
