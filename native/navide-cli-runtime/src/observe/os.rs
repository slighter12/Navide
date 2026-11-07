//! The operating-system arm of the observation core: how a source file is opened and read, on each platform, with
//! the same guarantees.
//!
//! - a **regular file only**: a directory, a device, a pipe or a socket is refused, and so is a link;
//! - **never through a link**: POSIX opens with `O_NOFOLLOW` and refuses a symlink named by the final component;
//!   Windows opens with `FILE_FLAG_OPEN_REPARSE_POINT` (the handle is the reparse point itself, not its target) and
//!   refuses any file that carries `FILE_ATTRIBUTE_REPARSE_POINT`: a symlink, a junction, a mount point, and also every
//!   other reparse tag (a cloud placeholder, a deduplicated file), which POSIX would read as a regular file;
//! - **never blocking**: POSIX `O_NONBLOCK` keeps an open of a FIFO or device from waiting for a peer; a Windows
//!   `CreateFile` of a regular file does not wait, and the file-type check refuses anything that is not a disk file
//!   before a byte of it is read;
//! - **shared**: the observer reads a log a CLI is appending to, so a Windows open allows every other opener
//!   (`FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`); POSIX has no sharing mode;
//! - an **identity** taken from the open handle (`device:index`), so a rotation is told from an append;
//! - **positioned reads**: `read_at` never relies on the handle's own file pointer (Windows `seek_read` moves it);
//! - a **pin** (`pin_regular`): a SQLite engine opens its store by path and Windows SQLite ignores `SQLITE_OPEN_NOFOLLOW`, so
//!   the core holds the checked file, deny-delete, while the engine opens it.
//!
//! Everything above this module is platform-neutral.
use serde_json::{json, Value};
use std::fs::File;
use std::io;
use std::path::Path;
use std::time::SystemTime;

pub use arm::Pin;

/// What names a file on its volume: the device (POSIX `st_dev`; the 64-bit volume serial number on Windows) and the file's
/// index on it (the inode; on Windows the 128-bit `FILE_ID_INFO` file id, which NTFS fills with its 64-bit file reference
/// number and ReFS, including a Dev Drive, with the whole 128 bits).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FileId {
    pub device: u64,
    pub index: u128,
}

impl FileId {
    /// The spelling the saved cursor and the answers carry: `device:index`, both in decimal.
    pub fn text(self) -> String {
        format!("{}:{}", self.device, self.index)
    }

    /// The `ino` of a document's identity: a JSON integer while the index fits one (every POSIX inode and every NTFS file index),
    /// else its decimal text (a ReFS id wider than 64 bits).
    pub fn ino(self) -> Value {
        match u64::try_from(self.index) {
            Ok(small) => json!(small),
            Err(_) => json!(self.index.to_string()),
        }
    }
}

/// A regular file opened under the core's bounds, with what the handle said at the open.
pub struct Regular {
    pub file: File,
    pub id: FileId,
    pub len: u64,
    /// The modification time the handle reported; `None` where the platform gives none.
    pub modified: Option<SystemTime>,
}

/// What a path names without following a link: its identity and size.
pub struct Stat {
    pub id: FileId,
    pub len: u64,
}

/// The one message for every kind of source that is not a regular file, on every platform.
pub fn not_regular() -> io::Error {
    io::Error::other("observation source is not a regular file")
}

/// Fill `buffer` from byte `offset` (a short read at the end of the file leaves the rest untouched and is an error, as
/// `read_exact` is). The handle's own position is neither used nor trusted.
pub fn read_exact_at(file: &File, mut buffer: &mut [u8], mut offset: u64) -> io::Result<()> {
    while !buffer.is_empty() {
        match read_at(file, buffer, offset) {
            Ok(0) => return Err(io::Error::from(io::ErrorKind::UnexpectedEof)),
            Ok(read) => {
                buffer = &mut buffer[read..];
                offset += read as u64;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

#[cfg(unix)]
mod arm {
    use super::{not_regular, FileId, Regular, Stat};
    use std::fs::{File, OpenOptions};
    use std::io;
    use std::os::unix::fs::{FileExt, MetadataExt, OpenOptionsExt};
    use std::path::Path;

    pub fn open_regular(path: &Path) -> io::Result<Regular> {
        let file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)?;
        let stat = file.metadata()?;
        if !stat.is_file() {
            return Err(not_regular());
        }
        Ok(Regular { file, id: FileId { device: stat.dev(), index: u128::from(stat.ino()) }, len: stat.len(), modified: stat.modified().ok() })
    }

    pub fn stat_regular(path: &Path) -> io::Result<Option<Stat>> {
        let Ok(stat) = std::fs::symlink_metadata(path) else {
            return Ok(None);
        };
        if !stat.is_file() {
            return Err(not_regular());
        }
        Ok(Some(Stat { id: FileId { device: stat.dev(), index: u128::from(stat.ino()) }, len: stat.len() }))
    }

    /// POSIX needs no pin: `SQLITE_OPEN_NOFOLLOW` refuses a link at the engine's own open.
    pub struct Pin;

    pub fn pin_regular(_path: &Path) -> io::Result<Pin> {
        Ok(Pin)
    }

    pub fn read_at(file: &File, buffer: &mut [u8], offset: u64) -> io::Result<usize> {
        file.read_at(buffer, offset)
    }

    /// The bytes of the path, as the file system spells it.
    pub fn uri_path(path: &Path) -> Option<Vec<u8>> {
        use std::os::unix::ffi::OsStrExt;
        Some(path.as_os_str().as_bytes().to_vec())
    }
}

#[cfg(windows)]
mod arm {
    use super::{not_regular, FileId, Regular, Stat};
    use std::fs::{File, OpenOptions};
    use std::io;
    use std::mem::{size_of, MaybeUninit};
    use std::os::windows::fs::{FileExt, OpenOptionsExt};
    use std::os::windows::io::AsRawHandle;
    use std::path::Path;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, FILETIME, HANDLE};
    use windows_sys::Win32::Storage::FileSystem::{
        FileIdInfo, GetFileInformationByHandle, GetFileInformationByHandleEx, GetFileType, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_ID_INFO, FILE_SHARE_DELETE, FILE_SHARE_READ,
        FILE_SHARE_WRITE, FILE_TYPE_DISK,
    };

    /// The sharing every observation open asks for: whoever else holds the file (the CLI that appends to it) keeps
    /// every right it has.
    const SHARE_ALL: u32 = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;

    /// The sharing of a pin: everything but delete. While the pin is open, no other open can have `DELETE` access (a rename, a delete
    /// or a replacement by rename all open the file for it) and each fails with a sharing violation.
    const SHARE_NO_DELETE: u32 = FILE_SHARE_READ | FILE_SHARE_WRITE;

    /// What the handle says about the file it holds.
    struct Held {
        attributes: u32,
        disk: bool,
        id: FileId,
        len: u64,
        modified: Option<SystemTime>,
    }

    impl Held {
        /// A file on a disk that is neither a directory nor a reparse point of any kind.
        fn regular(&self) -> bool {
            self.disk && self.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) == 0
        }
    }

    /// Open `path` itself (never its reparse target), with the sharing given. `FILE_FLAG_BACKUP_SEMANTICS` lets `CreateFile` open a
    /// directory too, so that a directory is refused as "not a regular file" and not as an access error.
    fn open_itself(path: &Path, share: u32) -> io::Result<File> {
        OpenOptions::new().read(true).share_mode(share).custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS).open(path)
    }

    /// A `FILETIME` (100 ns ticks since 1601-01-01) as a time; `None` before the Unix epoch.
    fn system_time(time: FILETIME) -> Option<SystemTime> {
        const UNIX_EPOCH_TICKS: u64 = 116_444_736_000_000_000;
        let ticks = u64::from(time.dwHighDateTime) << 32 | u64::from(time.dwLowDateTime);
        let since = ticks.checked_sub(UNIX_EPOCH_TICKS)?;
        Some(UNIX_EPOCH + Duration::new(since / 10_000_000, (since % 10_000_000) as u32 * 100))
    }

    /// The identity of the open file: the 64-bit volume serial and the 128-bit file id of `FILE_ID_INFO` (Windows 8 and later; the
    /// 64-bit index of `BY_HANDLE_FILE_INFORMATION` is not unique on ReFS). On NTFS the id is the file reference number, zero-extended,
    /// and it is little-endian in the record. A file system that does not answer `FileIdInfo` (FAT, some network shares) keeps the 64-bit
    /// index it always reported.
    fn identity_of(handle: HANDLE, info: &BY_HANDLE_FILE_INFORMATION) -> FileId {
        let mut wide = MaybeUninit::<FILE_ID_INFO>::zeroed();
        // SAFETY: `handle` is open for the caller's `file`, and `wide` is a writable `FILE_ID_INFO` of the size the call is told.
        let answered = unsafe { GetFileInformationByHandleEx(handle, FileIdInfo, wide.as_mut_ptr().cast(), size_of::<FILE_ID_INFO>() as u32) } != 0;
        if answered {
            // SAFETY: the call succeeded, so it filled the record.
            let wide = unsafe { wide.assume_init() };
            return FileId { device: wide.VolumeSerialNumber, index: u128::from_le_bytes(wide.FileId.Identifier) };
        }
        FileId { device: u64::from(info.dwVolumeSerialNumber), index: u128::from(u64::from(info.nFileIndexHigh) << 32 | u64::from(info.nFileIndexLow)) }
    }

    fn hold(file: &File) -> io::Result<Held> {
        let handle = file.as_raw_handle();
        let mut info = MaybeUninit::<BY_HANDLE_FILE_INFORMATION>::zeroed();
        // SAFETY: `handle` is open for the lifetime of `file`, and `info` is a writable record of the type the call fills.
        if unsafe { GetFileInformationByHandle(handle, info.as_mut_ptr()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the call succeeded, so it filled the record.
        let info = unsafe { info.assume_init() };
        Ok(Held {
            attributes: info.dwFileAttributes,
            // SAFETY: as above.
            disk: unsafe { GetFileType(handle) } == FILE_TYPE_DISK,
            id: identity_of(handle, &info),
            len: u64::from(info.nFileSizeHigh) << 32 | u64::from(info.nFileSizeLow),
            modified: system_time(info.ftLastWriteTime),
        })
    }

    pub fn open_regular(path: &Path) -> io::Result<Regular> {
        let file = open_itself(path, SHARE_ALL)?;
        let held = hold(&file)?;
        if !held.regular() {
            return Err(not_regular());
        }
        Ok(Regular { file, id: held.id, len: held.len, modified: held.modified })
    }

    /// Only a path that is not there is `None`: a store another process holds without read sharing, or one this process may not
    /// open, is there and unreadable, and the caller must hear that (it is no empty store).
    pub fn stat_regular(path: &Path) -> io::Result<Option<Stat>> {
        let file = match open_itself(path, SHARE_ALL) {
            Ok(file) => file,
            Err(error) if is_absent(&error) => return Ok(None),
            Err(error) => return Err(error),
        };
        let held = hold(&file)?;
        if !held.regular() {
            return Err(not_regular());
        }
        Ok(Some(Stat { id: held.id, len: held.len }))
    }

    /// `ERROR_FILE_NOT_FOUND` or `ERROR_PATH_NOT_FOUND`: the one answer that says "nothing is named so".
    fn is_absent(error: &io::Error) -> bool {
        matches!(error.raw_os_error().map(|code| code as u32), Some(ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND))
    }

    /// A checked regular file, held open without `FILE_SHARE_DELETE` for as long as the value lives.
    pub struct Pin {
        _file: File,
    }

    /// Open `path` itself, refuse anything but a regular file (a reparse point of any kind included), and hold it. SQLite's Windows
    /// engine opens its store by path and ignores `SQLITE_OPEN_NOFOLLOW`, so a check made by path alone could be undone by a link put in
    /// the file's place before the engine opens it. While this value lives the file named by `path` cannot be renamed, deleted or
    /// replaced (every such change opens it for `DELETE`, which the missing `FILE_SHARE_DELETE` refuses), so the path the engine opens
    /// is the file that was checked. Reading and writing by others stay allowed.
    pub fn pin_regular(path: &Path) -> io::Result<Pin> {
        let file = open_itself(path, SHARE_NO_DELETE)?;
        if !hold(&file)?.regular() {
            return Err(not_regular());
        }
        Ok(Pin { _file: file })
    }

    pub fn read_at(file: &File, buffer: &mut [u8], offset: u64) -> io::Result<usize> {
        // `seek_read` moves the handle's file pointer: nothing here reads through the pointer.
        file.seek_read(buffer, offset)
    }

    pub fn uri_path(path: &Path) -> Option<Vec<u8>> {
        Some(super::windows_uri_path(path.to_str()?).into_bytes())
    }
}

/// The path part of a SQLite URI for a Windows path (given as text: a path that is not Unicode was refused by the caller): the verbatim prefix `\\?\` that `canonicalize` adds is dropped (`\\?\UNC\server\share` is `\\server\share`), the
/// separators are `/`, a drive path gets the slash SQLite's Windows VFS removes again (`/C:/dir/file`), and a UNC path keeps its two
/// slashes behind an empty authority (`file:////server/share/file`, never `file://server/...`, which names a host).
///
/// Compiled everywhere so that a POSIX host runs its tests; only Windows calls it.
#[cfg(any(windows, test))]
fn windows_uri_path(text: &str) -> String {
    let plain = match text.strip_prefix(r"\\?\UNC\") {
        Some(rest) => format!(r"\\{rest}"),
        None => text.strip_prefix(r"\\?\").unwrap_or(text).to_owned(),
    };
    let forward = plain.replace('\\', "/");
    let drive = matches!(forward.as_bytes(), [letter, b':', ..] if letter.is_ascii_alphabetic());
    if forward.starts_with("//") {
        format!("//{forward}")
    } else if drive {
        format!("/{forward}")
    } else {
        forward
    }
}

/// Open `path` as a regular file the observation may read.
pub fn open_regular(path: &Path) -> io::Result<Regular> {
    arm::open_regular(path)
}

/// What `path` names, without following a link: `Ok(None)` for a path that is not there, an error for one that is not a regular file.
/// A path that is there but cannot be opened for inspection is `None` on POSIX (as the baseline treats a failed `lstat`) and an error on
/// Windows (a sharing violation or a refused access is the store being unreadable, which the caller must be told, not an empty store).
pub fn stat_regular(path: &Path) -> io::Result<Option<Stat>> {
    arm::stat_regular(path)
}

/// Hold the regular file `path` names, refusing a link, until the returned value is dropped. Windows: deny-delete, so the path cannot be
/// renamed, deleted or replaced meanwhile; POSIX: nothing (the engine's `SQLITE_OPEN_NOFOLLOW` does it).
pub fn pin_regular(path: &Path) -> io::Result<Pin> {
    arm::pin_regular(path)
}

/// The bytes SQLite is given for `path` as the path part of its URI (before percent-encoding); `None` for a path it cannot be given.
pub fn uri_path(path: &Path) -> Option<Vec<u8>> {
    arm::uri_path(path)
}

/// One positioned read; the number of bytes read (0 at the end of the file).
pub fn read_at(file: &File, buffer: &mut [u8], offset: u64) -> io::Result<usize> {
    arm::read_at(file, buffer, offset)
}

/// A sequential reader over positioned reads: a buffered scan of a log reads through it, so the scan, an anchor read
/// through a clone of the handle and a fingerprint read can interleave without moving one another's position.
pub struct Positioned<'a> {
    file: &'a File,
    at: u64,
}

impl<'a> Positioned<'a> {
    pub fn new(file: &'a File, at: u64) -> Self {
        Self { file, at }
    }
}

impl io::Read for Positioned<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let read = read_at(self.file, buffer, self.at)?;
        self.at += read as u64;
        Ok(read)
    }
}

/// A link to a file, for the tests of every platform (a POSIX symlink; a Windows file symlink, which needs the symlink privilege that a
/// hosted runner's administrator has).
#[cfg(test)]
pub fn link_file(target: &Path, link: &Path) -> io::Result<()> {
    #[cfg(unix)]
    return std::os::unix::fs::symlink(target, link);
    #[cfg(windows)]
    return std::os::windows::fs::symlink_file(target, link);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    /// A directory of its own, removed when the test ends.
    struct Dir(PathBuf);

    impl Dir {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("observe-os-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            std::fs::create_dir_all(&root).unwrap();
            Self(root)
        }
        fn file(&self, name: &str, body: &[u8]) -> PathBuf {
            let path = self.0.join(name);
            std::fs::write(&path, body).unwrap();
            path
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// A link the platform lets this process make; a refusal to make one is the test's failure, said in words (a skipped link test would
    /// leave "a link is not followed" unproven on the platform that cannot make one).
    fn linked(dir: &Dir, target: &Path, name: &str) -> PathBuf {
        let link = dir.0.join(name);
        if let Err(error) = link_file(target, &link) {
            panic!("this runner cannot create a file symlink ({error}; Windows needs the symlink privilege, which a hosted runner's administrator has): the no-follow rule cannot be exercised here");
        }
        link
    }

    #[test]
    fn a_regular_file_opens_with_a_stable_identity_that_a_different_file_does_not_share() {
        let dir = Dir::new();
        let (first, second) = (dir.file("a.jsonl", b"one\n"), dir.file("b.jsonl", b"two\n"));
        let (a, again, b) = (open_regular(&first).unwrap(), open_regular(&first).unwrap(), open_regular(&second).unwrap());
        assert_eq!(a.id, again.id, "one file, one identity, however often it is opened");
        assert_ne!(a.id, b.id, "two files never share an identity");
        assert_eq!(a.id.text(), format!("{}:{}", a.id.device, a.id.index));
        let mut writer = std::fs::OpenOptions::new().append(true).open(&first).unwrap();
        writer.write_all(b"more\n").unwrap();
        assert_eq!(open_regular(&first).unwrap().id, a.id, "an append keeps the file's identity: a rotation is told from an append by it");
    }

    /// A 128-bit file id (ReFS) must not be cut to 64 bits in the text the cursor keeps, nor become a lossy JSON number.
    #[test]
    fn an_identity_spells_all_of_its_bits_in_the_text_and_in_the_document() {
        let small = FileId { device: 7, index: 42 };
        assert_eq!((small.text(), small.ino()), ("7:42".to_owned(), json!(42)));
        let wide = FileId { device: u64::MAX, index: u128::MAX };
        assert_eq!(wide.text(), format!("{}:{}", u64::MAX, u128::MAX));
        assert_eq!(wide.ino(), json!(u128::MAX.to_string()), "an id over 64 bits is its decimal text, never a rounded number");
        let edge = FileId { device: 1, index: u128::from(u64::MAX) };
        assert_eq!(edge.ino(), json!(u64::MAX), "the largest id a JSON integer holds stays a number");
        assert_ne!(FileId { device: 1, index: 1 << 64 }.text(), FileId { device: 1, index: 0 }.text(), "ids that differ only above bit 63 differ");
    }

    #[test]
    fn an_open_file_reports_its_size_and_modification_time_from_the_one_handle() {
        let dir = Dir::new();
        let path = dir.file("sized.jsonl", b"12345\n");
        let long_ago = std::time::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000);
        std::fs::File::options().write(true).open(&path).unwrap().set_modified(long_ago).unwrap();
        let held = open_regular(&path).unwrap();
        assert_eq!(held.len, 6);
        assert_eq!(held.modified, Some(long_ago), "the handle's own modification time, not a second query");
    }

    #[test]
    fn a_directory_is_refused_as_not_a_regular_file_on_every_platform() {
        let dir = Dir::new();
        let refused = open_regular(&dir.0).err().expect("a directory is not a source");
        assert_eq!(refused.to_string(), not_regular().to_string(), "{refused:?}");
        let refused = stat_regular(&dir.0).err().expect("nor is it a store");
        assert_eq!(refused.to_string(), not_regular().to_string(), "{refused:?}");
    }

    #[test]
    fn a_link_to_a_file_is_not_followed_by_either_open() {
        let dir = Dir::new();
        let real = dir.file("real.jsonl", b"{}\n");
        let link = linked(&dir, &real, "link.jsonl");
        assert!(open_regular(&link).is_err(), "the log's open followed a link");
        assert!(stat_regular(&link).is_err(), "the store's inspection followed a link");
        assert!(open_regular(&real).is_ok(), "the target itself is still a source");
    }

    #[test]
    fn a_missing_path_is_no_store_and_no_source() {
        let dir = Dir::new();
        assert!(stat_regular(&dir.0.join("absent.db")).unwrap().is_none());
        assert!(open_regular(&dir.0.join("absent.jsonl")).is_err());
    }

    #[test]
    fn a_store_inspection_reports_the_size_and_the_identity_of_the_file() {
        let dir = Dir::new();
        let path = dir.file("store.db", b"12345");
        let stat = stat_regular(&path).unwrap().expect("a regular file");
        assert_eq!((stat.len, stat.id), (5, open_regular(&path).unwrap().id));
    }

    /// The observer reads a log a CLI is still writing: its open must not stop the writer, and a writer must not stop its open.
    #[test]
    fn a_log_a_writer_has_open_is_read_and_sees_what_the_writer_appends_after() {
        let dir = Dir::new();
        let path = dir.file("live.jsonl", b"first\n");
        let mut writer = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
        let held = open_regular(&path).expect("a log that is being written is readable");
        writer.write_all(b"second\n").unwrap();
        writer.flush().unwrap();
        let mut text = String::new();
        Positioned::new(&held.file, 0).read_to_string(&mut text).unwrap();
        assert_eq!(text, "first\nsecond\n");
        // The writer is not held up by the observer's open either.
        writer.write_all(b"third\n").unwrap();
    }

    #[test]
    fn a_positioned_scan_is_not_disturbed_by_reads_through_the_same_handle() {
        let dir = Dir::new();
        let body: Vec<u8> = (0..200_000u32).map(|n| (n % 251) as u8).collect();
        let path = dir.file("scan.bin", &body);
        let held = open_regular(&path).unwrap();
        // The scan reads on from where it stood, whatever other reads did to the handle in between (Windows `seek_read` moves its pointer;
        // an anchor, a fingerprint or a clone's read must never move the scan).
        let clone = held.file.try_clone().unwrap();
        let mut scan = Positioned::new(&held.file, 1_000);
        let mut seen = Vec::new();
        let mut chunk = [0u8; 4096];
        for round in 0..20u64 {
            let read = scan.read(&mut chunk).unwrap();
            seen.extend_from_slice(&chunk[..read]);
            let mut probe = [0u8; 32];
            read_exact_at(&clone, &mut probe, 150_000 + round * 7).unwrap();
            assert_eq!(&probe[..], &body[(150_000 + round * 7) as usize..][..32]);
            read_exact_at(&held.file, &mut probe, 5 + round).unwrap();
        }
        assert_eq!(seen, body[1_000..1_000 + seen.len()], "the scan skipped or repeated bytes after a read through another handle");
        assert!(seen.len() >= 20 * 4096 - 20 * 64, "the scan made progress: {}", seen.len());
    }

    #[test]
    fn a_read_past_the_end_is_an_unexpected_eof_and_a_read_at_the_end_is_zero_bytes() {
        let dir = Dir::new();
        let path = dir.file("short.bin", b"0123456789");
        let held = open_regular(&path).unwrap();
        let mut buffer = [0u8; 4];
        assert_eq!(read_at(&held.file, &mut buffer, 10).unwrap(), 0);
        assert_eq!(read_at(&held.file, &mut buffer, 4_000).unwrap(), 0);
        read_exact_at(&held.file, &mut buffer, 6).unwrap();
        assert_eq!(&buffer, b"6789");
        assert_eq!(read_exact_at(&held.file, &mut buffer, 8).unwrap_err().kind(), io::ErrorKind::UnexpectedEof);
    }

    /// The SQLite URI path of a Windows path, as the Windows VFS wants it (the function is compiled on every host; its use is Windows).
    #[test]
    fn a_windows_path_becomes_the_uri_path_sqlite_reads_back_as_that_path() {
        assert_eq!(windows_uri_path(r"C:\Users\a b\opencode.db"), "/C:/Users/a b/opencode.db");
        assert_eq!(windows_uri_path(r"\\?\C:\Users\a\x.db"), "/C:/Users/a/x.db", "the verbatim prefix `canonicalize` adds is dropped");
        assert_eq!(windows_uri_path(r"\\?\UNC\server\share\x.db"), "////server/share/x.db", "a UNC path keeps both slashes behind an empty authority");
        assert_eq!(windows_uri_path(r"\\server\share\x.db"), "////server/share/x.db");
        assert_eq!(windows_uri_path("C:/already/forward.db"), "/C:/already/forward.db");
        assert_eq!(windows_uri_path(r"relative\x.db"), "relative/x.db");
    }

    /// Windows only: what a POSIX host cannot say. Each of these opens a thing that is not a plain disk file, or opens a file another process
    /// holds in a way the observer must survive.
    #[cfg(windows)]
    mod windows {
        use super::*;
        use std::os::windows::fs::OpenOptionsExt;

        #[test]
        fn a_device_name_is_refused_and_never_read() {
            for name in ["NUL", r"\\.\NUL"] {
                let refused = open_regular(Path::new(name)).err();
                assert!(refused.is_some(), "{name} was accepted as a regular file: a read of a device could block the observer");
            }
        }

        #[test]
        fn a_directory_junction_is_refused() {
            let dir = Dir::new();
            let target = dir.0.join("target");
            std::fs::create_dir(&target).unwrap();
            let junction = dir.0.join("junction");
            let made = std::process::Command::new("cmd").args(["/C", "mklink", "/J"]).arg(&junction).arg(&target).output().unwrap();
            assert!(made.status.success(), "mklink /J failed: {}", String::from_utf8_lossy(&made.stderr));
            assert!(open_regular(&junction).is_err(), "a junction was opened as a source");
            assert!(stat_regular(&junction).is_err(), "a junction was inspected as a store");
        }

        /// A symlink is a reparse point; the open names the link itself, finds `FILE_ATTRIBUTE_REPARSE_POINT` and refuses, even where the
        /// target is a perfectly good file.
        #[test]
        fn a_file_symlink_is_refused_by_its_reparse_attribute_not_by_what_it_points_at() {
            let dir = Dir::new();
            let real = dir.file("real.jsonl", b"{}\n");
            let link = linked(&dir, &real, "link.jsonl");
            let refused = open_regular(&link).err().expect("a symlink is a reparse point");
            assert_eq!(refused.to_string(), not_regular().to_string(), "{refused:?}");
        }

        const SHARE_READ: u32 = 1;
        const SHARE_WRITE: u32 = 2;
        const ERROR_SHARING_VIOLATION: i32 = 32;

        /// The identity must be the volume's 128-bit answer, not the 64-bit fallback: ReFS (a Dev Drive) does not keep the 64-bit index
        /// unique. A volume that does not answer `FileIdInfo` fails here, loudly, where the fallback would have hidden it.
        #[test]
        fn the_identity_is_the_128_bit_file_id_the_handle_reports() {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::Storage::FileSystem::{FileIdInfo, GetFileInformationByHandleEx, FILE_ID_INFO};
            let dir = Dir::new();
            let path = dir.file("id.jsonl", b"x\n");
            let held = open_regular(&path).unwrap();
            let mut wide = FILE_ID_INFO::default();
            // SAFETY: the handle is open for `held`'s lifetime and `wide` is a writable `FILE_ID_INFO` of the size passed.
            let answered = unsafe { GetFileInformationByHandleEx(held.file.as_raw_handle(), FileIdInfo, (&mut wide as *mut FILE_ID_INFO).cast(), std::mem::size_of::<FILE_ID_INFO>() as u32) };
            assert_ne!(answered, 0, "the volume of {} does not answer FileIdInfo ({}): the identity fell back to the 64-bit index", path.display(), io::Error::last_os_error());
            assert_eq!(held.id, FileId { device: wide.VolumeSerialNumber, index: u128::from_le_bytes(wide.FileId.Identifier) });
        }

        /// A store held without read sharing is there and unreadable: the caller hears the sharing violation (it is no empty store),
        /// and a path that is not there stays `None`.
        #[test]
        fn a_store_another_process_holds_without_read_sharing_is_an_error_and_only_a_missing_path_is_none() {
            let dir = Dir::new();
            let path = dir.file("held.db", b"x");
            for share in [0, SHARE_WRITE] {
                let closed = std::fs::OpenOptions::new().write(true).share_mode(share).open(&path).unwrap();
                let outcome = stat_regular(&path);
                drop(closed);
                let error = match outcome {
                    Err(error) => error,
                    Ok(found) => panic!("a store held with sharing {share} was reported as {}", if found.is_some() { "readable" } else { "absent" }),
                };
                assert_eq!(error.raw_os_error(), Some(ERROR_SHARING_VIOLATION), "sharing {share}: {error:?}");
            }
            assert!(stat_regular(&path).unwrap().is_some(), "released, the store is readable again");
            assert!(stat_regular(&dir.0.join("absent.db")).unwrap().is_none(), "a missing file is no store");
            assert!(stat_regular(&dir.0.join("no-such-dir").join("absent.db")).unwrap().is_none(), "nor is a file under a missing directory");
        }

        /// The pin is what keeps a store's path from being swapped for a link while the engine opens it: it must refuse every change of the
        /// name and must not hold anyone's reading or writing back.
        #[test]
        fn a_pinned_file_cannot_be_renamed_deleted_or_replaced_and_readers_and_writers_are_unaffected() {
            let dir = Dir::new();
            let path = dir.file("pinned.db", b"x\n");
            let replacement = dir.file("replacement.db", b"y\n");
            let moved = dir.0.join("moved.db");
            let pin = pin_regular(&path).expect("a regular file is pinned");
            let renamed = std::fs::rename(&path, &moved);
            assert!(renamed.is_err(), "a pinned file was renamed away");
            let deleted = std::fs::remove_file(&path);
            assert!(deleted.is_err(), "a pinned file was deleted");
            let replaced = std::fs::rename(&replacement, &path);
            assert!(replaced.is_err(), "a pinned file was replaced by a rename over it");
            assert_eq!(std::fs::read(&path).unwrap(), b"x\n", "the pinned name still names the pinned file");
            let mut writer = std::fs::OpenOptions::new().append(true).open(&path).expect("a writer is not held back by a pin");
            writer.write_all(b"more\n").unwrap();
            assert!(open_regular(&path).is_ok(), "a reader is not held back by a pin");
            assert!(pin_regular(&path).is_ok(), "pins do not exclude each other");
            drop(writer);
            drop(pin);
            std::fs::rename(&path, &moved).expect("released, the name is free again");
        }

        #[test]
        fn a_pin_refuses_a_link_a_directory_and_a_device() {
            let dir = Dir::new();
            let real = dir.file("real.db", b"x");
            let link = linked(&dir, &real, "link.db");
            let refused = pin_regular(&link).err().expect("a symlink is a reparse point");
            assert_eq!(refused.to_string(), not_regular().to_string(), "{refused:?}");
            let refused = pin_regular(&dir.0).err().expect("a directory is not a store");
            assert_eq!(refused.to_string(), not_regular().to_string(), "{refused:?}");
            assert!(pin_regular(Path::new("NUL")).is_err(), "a device was pinned as a store");
        }

        #[test]
        fn a_file_a_writer_shares_is_readable_and_one_held_without_read_sharing_fails_cleanly() {
            let dir = Dir::new();
            let path = dir.file("held.jsonl", b"x\n");
            // The common writer: read, write and delete sharing, as Rust's own default gives it.
            let writer = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
            assert!(open_regular(&path).is_ok(), "a writer with default sharing blocked the observer");
            drop(writer);
            // A writer that shares reading only: the observer asks for read access and grants write and delete sharing, so both sides of
            // the sharing check agree and the open succeeds.
            let readers_only = std::fs::OpenOptions::new().write(true).share_mode(SHARE_READ).open(&path).unwrap();
            let outcome = open_regular(&path);
            drop(readers_only);
            assert!(outcome.is_ok(), "a writer that shares reading blocked the observer: {:?}", outcome.err());
            // A writer that shares no reading (exclusive, or write-only sharing): no reader can open the file, and this one must say so
            // with the sharing violation, not panic or hang. The log is unobservable then; nothing here could read it.
            for share in [0, SHARE_WRITE] {
                let closed = std::fs::OpenOptions::new().write(true).share_mode(share).open(&path).unwrap();
                let outcome = open_regular(&path);
                drop(closed);
                let error = outcome.err().unwrap_or_else(|| panic!("a file held with sharing {share} was opened for reading"));
                assert_eq!(error.raw_os_error(), Some(ERROR_SHARING_VIOLATION), "sharing {share}: {error:?}");
            }
        }
    }
}
