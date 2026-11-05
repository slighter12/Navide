//! macOS only: the SQLite the sidecar links is the system library, pinned by version and Mach-O UUID.
//! Moved unchanged from the old `opencode.rs` test module when the SQLite reader moved into `observe::sqlite`.
use libc::c_char;
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
    assert_eq!(version, "3.54.0");
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
    assert_eq!(uuid, "73d616f27b6c30468de6656f74793d5f");
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
