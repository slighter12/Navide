//! macOS only: the SQLite the sidecar links is the system library (`/usr/lib/libsqlite3.dylib`), not a copy it carries.
//! Moved from the old `opencode.rs` test module when the SQLite reader moved into `observe::sqlite`.
//!
//! What is checked is a property that survives an OS update: the SQLite symbols resolve into that system image, whose Mach-O header holds
//! a well-formed LC_UUID, and the library reports a 3.x version. The exact version and UUID are not pinned: the system library is rebuilt by
//! every macOS update (3.54.0 with UUID 73d616f2... on the earlier macOS, 3.54.0 with UUID 36af1d09... on macOS 27.2, same version, other
//! build), so a pin would only say which OS build ran the test. They are printed in the evidence line, which is where a registered run
//! records them.
use libc::{c_char, c_void};
use serde_json::json;
use std::ffi::CStr;

#[test]
fn genuine_self_system_sqlite_identity() {
    unsafe extern "C" {
        fn sqlite3_libversion() -> *const c_char;
        fn _dyld_image_count() -> u32;
        fn _dyld_get_image_name(index: u32) -> *const c_char;
        fn _dyld_get_image_header(index: u32) -> *const u32;
    }
    let version = unsafe { CStr::from_ptr(sqlite3_libversion()) }
        .to_str()
        .unwrap();
    assert!(version.starts_with("3.") && version.split('.').all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())), "{version}");
    // The symbol the sidecar calls lives in the system image, not in a library the binary carries.
    let mut home: libc::Dl_info = unsafe { std::mem::zeroed() };
    assert_ne!(unsafe { libc::dladdr(sqlite3_libversion as *const c_void, &mut home) }, 0);
    assert_eq!(unsafe { CStr::from_ptr(home.dli_fname) }.to_str().unwrap(), "/usr/lib/libsqlite3.dylib");
    let mut matched = None;
    for index in 0..unsafe { _dyld_image_count() } {
        let name = unsafe { CStr::from_ptr(_dyld_get_image_name(index)) }
            .to_str()
            .unwrap();
        if name != "/usr/lib/libsqlite3.dylib" {
            continue;
        }
        let header = unsafe { _dyld_get_image_header(index) };
        assert_eq!(unsafe { *header }, 0xfeedfacf);
        let count = unsafe { *header.add(4) };
        let total = unsafe { *header.add(5) } as usize;
        let mut offset = 32usize;
        for _ in 0..count {
            assert!(offset + 8 <= 32 + total);
            let command = unsafe { header.cast::<u8>().add(offset).cast::<u32>() };
            let kind = unsafe { *command };
            let length = unsafe { *command.add(1) } as usize;
            assert!(length >= 8 && offset + length <= 32 + total);
            if kind == 0x1b {
                assert!(length >= 24);
                let uuid = unsafe { std::slice::from_raw_parts(command.cast::<u8>().add(8), 16) };
                matched = Some(uuid.iter().map(|b| format!("{b:02x}")).collect::<String>());
                break;
            }
            offset += length;
        }
    }
    let uuid = matched.unwrap();
    assert!(uuid.len() == 32 && uuid.bytes().all(|b| b.is_ascii_hexdigit()) && uuid.bytes().any(|b| b != b'0'), "not an LC_UUID: {uuid}");
    let own = super::darwin::self_process().unwrap();
    assert_eq!(own["pid"], std::process::id());
    assert_eq!(own["parent"], unsafe { libc::getppid() });
    println!(
        "{}",
        json!({"kind":"sqlite-system-SELF","pid":own["pid"],"parent":own["parent"],
        "birth_seconds":own["birth_seconds"],"birth_microseconds":own["birth_microseconds"],"executable":own["executable"],
        "self_api":"darwin::self_process",
        "library":"/usr/lib/libsqlite3.dylib","version":version,"uuid":uuid,"runtime_library_bytes_frozen":false,"dyld_cache_byte_limit":true})
    );
}
