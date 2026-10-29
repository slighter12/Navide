//! `ps` for the frozen `tests/cli/g1eAttempt.ts` on Windows (design section 1 row 9, option a).
//!
//! The runner calls exactly two shapes and nothing else is supported:
//!   ps -A -o pid=            one pid per line (its line count is the receipt's `processCount`, which must be >= 1)
//!   ps -o lstart= -p PID     the process start time in procps `lstart` shape, e.g. `Thu Oct 29 10:00:00 2026` (an identity string)
//! A pid that does not exist prints nothing and exits 1, as `ps -p` does. Any other argument list exits 2 with a message on stderr.
//! No process name, command line or environment is read. Windows only; elsewhere the binary refuses (it is built in the Windows job).

#[derive(Debug, PartialEq)]
enum Request {
    ListPids,
    StartTime(u32),
}

fn parse_args(args: &[String]) -> Result<Request, String> {
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.as_slice() {
        ["-A", "-o", "pid="] => Ok(Request::ListPids),
        ["-o", "lstart=", "-p", pid] => pid.parse::<u32>().map(Request::StartTime).map_err(|_| format!("not a pid: {pid}")),
        _ => Err(format!("unsupported arguments: {}", words.join(" "))),
    }
}

fn format_pids(pids: &[u32]) -> String {
    pids.iter().map(|pid| format!("{pid:>5}\n")).collect()
}

/// Days since 1970-01-01 to (year, month, day): the civil-from-days algorithm (proleptic Gregorian).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe as i64 + era * 400 + if month <= 2 { 1 } else { 0 };
    (year, month, day)
}

/// A Windows FILETIME (100 ns ticks since 1601-01-01 UTC) as `Www Mmm dd HH:MM:SS YYYY` (UTC).
fn format_lstart(filetime: u64) -> String {
    const WEEKDAYS: [&str; 7] = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"];
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let unix_seconds = (filetime / 10_000_000) as i64 - 11_644_473_600;
    let days = unix_seconds.div_euclid(86_400);
    let seconds = unix_seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!("{} {} {:>2} {:02}:{:02}:{:02} {}", WEEKDAYS[days.rem_euclid(7) as usize], MONTHS[(month - 1) as usize], day, seconds / 3600, seconds % 3600 / 60, seconds % 60, year)
}

#[cfg(windows)]
mod sys {
    use std::ffi::c_void;

    type Handle = *mut c_void;
    const TH32CS_SNAPPROCESS: u32 = 0x2;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;

    #[repr(C)]
    struct ProcessEntry32W {
        size: u32,
        usage: u32,
        process_id: u32,
        default_heap_id: usize,
        module_id: u32,
        threads: u32,
        parent_process_id: u32,
        pri_class_base: i32,
        flags: u32,
        exe_file: [u16; 260],
    }

    #[repr(C)]
    #[derive(Default, Clone, Copy)]
    struct FileTime {
        low: u32,
        high: u32,
    }

    extern "system" {
        fn CreateToolhelp32Snapshot(flags: u32, process_id: u32) -> Handle;
        fn Process32FirstW(snapshot: Handle, entry: *mut ProcessEntry32W) -> i32;
        fn Process32NextW(snapshot: Handle, entry: *mut ProcessEntry32W) -> i32;
        fn OpenProcess(access: u32, inherit: i32, process_id: u32) -> Handle;
        fn GetProcessTimes(process: Handle, creation: *mut FileTime, exit: *mut FileTime, kernel: *mut FileTime, user: *mut FileTime) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }

    pub fn pids() -> Result<Vec<u32>, String> {
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snapshot as isize == -1 {
                return Err("CreateToolhelp32Snapshot failed".into());
            }
            let mut entry: ProcessEntry32W = std::mem::zeroed();
            entry.size = std::mem::size_of::<ProcessEntry32W>() as u32;
            let mut out = Vec::new();
            let mut more = Process32FirstW(snapshot, &mut entry);
            while more != 0 {
                out.push(entry.process_id);
                more = Process32NextW(snapshot, &mut entry);
            }
            CloseHandle(snapshot);
            Ok(out)
        }
    }

    pub fn creation_filetime(pid: u32) -> Option<u64> {
        unsafe {
            let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if process.is_null() {
                return None;
            }
            let (mut created, mut a, mut b, mut c) = (FileTime::default(), FileTime::default(), FileTime::default(), FileTime::default());
            let ok = GetProcessTimes(process, &mut created, &mut a, &mut b, &mut c);
            CloseHandle(process);
            (ok != 0).then(|| (created.high as u64) << 32 | created.low as u64)
        }
    }
}

#[cfg(not(windows))]
mod sys {
    pub fn pids() -> Result<Vec<u32>, String> {
        Err("the ps shim runs on Windows only".into())
    }
    pub fn creation_filetime(_pid: u32) -> Option<u64> {
        None
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match parse_args(&args) {
        Err(message) => {
            eprintln!("ps: {message}");
            2
        }
        Ok(Request::ListPids) => match sys::pids() {
            Ok(pids) => {
                print!("{}", format_pids(&pids));
                0
            }
            Err(message) => {
                eprintln!("ps: {message}");
                1
            }
        },
        Ok(Request::StartTime(pid)) => match sys::creation_filetime(pid) {
            Some(filetime) => {
                println!("{}", format_lstart(filetime));
                0
            }
            None => 1,
        },
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    #[test]
    fn the_two_shapes_of_the_runner_parse_and_nothing_else_does() {
        assert_eq!(parse_args(&args(&["-A", "-o", "pid="])), Ok(Request::ListPids));
        assert_eq!(parse_args(&args(&["-o", "lstart=", "-p", "4242"])), Ok(Request::StartTime(4242)));
        for bad in [vec![], vec!["-ef"], vec!["-o", "lstart=", "-p", "x"], vec!["-A", "-o", "comm="], vec!["-o", "lstart=", "-p", "1", "-A"]] {
            assert!(parse_args(&args(&bad)).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn pids_print_one_per_line_so_the_line_count_is_the_process_count() {
        assert_eq!(format_pids(&[4, 1234, 70000]), "    4\n 1234\n70000\n");
        assert_eq!(format_pids(&[]).lines().count(), 0);
    }

    #[test]
    fn a_filetime_becomes_a_procps_lstart_string() {
        // 2026-10-29 10:00:00 UTC (a Thursday): unix 1_793_268_000 -> filetime = (unix + 11_644_473_600) * 10_000_000
        assert_eq!(format_lstart((1_793_268_000 + 11_644_473_600) * 10_000_000), "Thu Oct 29 10:00:00 2026");
        assert_eq!(format_lstart(11_644_473_600 * 10_000_000), "Thu Jan  1 00:00:00 1970");
        assert_eq!(format_lstart((951_782_400 + 11_644_473_600) * 10_000_000 + 9_999_999), "Tue Feb 29 00:00:00 2000", "a leap day; sub-second ticks are dropped");
    }

    #[test]
    fn the_same_filetime_is_the_same_identity_and_another_one_is_not() {
        let a = format_lstart(132_000_000_000_000_000);
        assert_eq!(a, format_lstart(132_000_000_000_000_000));
        assert_ne!(a, format_lstart(132_000_000_100_000_000));
    }
}
