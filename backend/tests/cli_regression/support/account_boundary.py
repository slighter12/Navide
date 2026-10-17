"""Disposable Python-only account/filesystem boundary, NOT an OS sandbox."""
from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import pwd
import stat
import sys
import threading

_INSTALLED_ROOT: Path | None = None


def install_account_home_guard(root: Path) -> None:
    """Install before application imports. Never query the physical account.

    This denies Python audited file operations outside the fixture or exact
    read-only source/interpreter roots. Native extensions, Rust and shell
    children are NOT thereby sandboxed. Refusals are persisted even if caught.
    """
    global _INSTALLED_ROOT
    root = root.resolve(strict=True)
    if _INSTALLED_ROOT is not None:
        if root != _INSTALLED_ROOT:
            raise RuntimeError('account boundary already installed for another fixture')
        return
    home = root / 'home'
    home.mkdir(exist_ok=True)
    # Standard-library MIME discovery is an external OS configuration input,
    # not application policy: provide only the empty synthetic OS table.
    mime_file = root / 'os-mime.types'
    mime_file.write_text('')
    mimetypes.knownfiles = [str(mime_file)]
    readonly = (Path(__file__).resolve().parents[3], Path(sys.base_prefix).resolve())
    timezone = {'aliases': ()}
    pytest_inputs, pytest_worktree = (), None
    readonly_runtime = Path(__file__).resolve().parents[4] / 'native/navide-cli-runtime/target/release/navide-cli-runtime'
    if os.environ.get('NAVIDE_REGRESSION_KERNEL_MANIFEST_SHA256'):
        from native_file_guard import configured_timezone, pytest_manifest_origins, pytest_worktree_identity, principal_manifest_runtime
        from difference_entry import validate_kernel_setup
        timezone = configured_timezone()
        document = validate_kernel_setup(root)
        pytest_inputs, pytest_worktree = pytest_manifest_origins(document, Path(__file__).resolve().parents[3])
        readonly_runtime = principal_manifest_runtime(root, document) or readonly_runtime
    startup_paths = ()
    if os.environ.get('NAVIDE_REGRESSION_KERNEL_MANIFEST_SHA256'):
        from os_startup import public_startup, startup_read_paths
        startup_paths = tuple(Path(path) for path in startup_read_paths(public_startup()))
    null = os.stat('/dev/null', follow_symlinks=False)
    if not stat.S_ISCHR(null.st_mode):
        raise PermissionError('unverified OS null character device')
    # platform.mac_ver()/send2trash imports require this exact OS version file.
    readonly_files = (Path('/System/Library/CoreServices/SystemVersion.plist'),
                      Path('/bin/sh'), Path('/bin/bash'), Path('/bin/ps'),
                      readonly_runtime,
                      *(Path(path) for path in timezone['aliases']), *startup_paths,
                      Path(__file__).resolve().parents[4] / 'native/navide-cli-runtime/src/darwin.rs',
                      Path(__file__).resolve().parents[4] / 'native/navide-cli-runtime/src/posix.rs',
                      *pytest_inputs)
    receipt_fd = os.open(root / 'origin-refusals.jsonl',
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    original_read, original_write = os.read, os.write
    # Synthetic OS account inputs; these functions never call their originals.
    record = pwd.struct_passwd(('navide-fixture', 'x', os.getuid(), os.getgid(), '', str(home), '/bin/sh'))
    pwd.getpwuid = lambda _uid: record
    pwd.getpwnam = lambda _name: record
    pwd.getpwall = lambda: [record]
    os.environ['HOME'] = str(home)
    os.environ['USERPROFILE'] = str(home)
    os.environ['ZDOTDIR'] = str(home)

    def refusal(event, operation, path):
        # Only operation labels/hash are emitted, never target paths/contents.
        sources = {'skills_store.py': 'skills-store', 'native_skills.py': 'native-skills',
                   'native_mcp.py': 'native-mcp', 'native_memory.py': 'native-memory',
                   '_posix_paths.py': 'posix-paths', 'applog.py': 'application-log',
                   'app.py': 'application-import', 'codex.py': 'codex-adapter'}
        frame = sys._getframe(2)
        source, line = 'synthetic-probe', 0
        while frame:
            name = Path(frame.f_code.co_filename).name
            if name in sources:
                source, line = sources[name], frame.f_lineno
                break
            frame = frame.f_back
        row = {'event': event, 'operation': operation, 'origin': 'outside-fixture',
               'source': source, 'source_line': line,
               'target_sha256': hashlib.sha256(os.fsencode(str(path))).hexdigest()}
        original_write(receipt_fd, (json.dumps(row) + '\n').encode())
        raise PermissionError('fixture origin guard refused ' + operation)

    def check(path, event, write=False):
        if isinstance(path, int):
            # Anonymous pipes are needed by subprocess/bootstrap. A preopened
            # regular file is not an acceptable origin bypass.
            before = os.fstat(path)
            if stat.S_ISFIFO(before.st_mode) or stat.S_ISSOCK(before.st_mode):
                return
            if stat.S_ISDIR(before.st_mode):
                directory = fcntl.fcntl(path, 50, bytes(1024)).split(b'\0', 1)[0]
                after = os.fstat(path)
                if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
                    refusal(event, 'changed-directory-descriptor', 'descriptor')
                return check(directory, event, write)  # SAME existing roots, never a new grant.
            refusal(event, 'descriptor', 'descriptor')
        lexical = Path(os.path.abspath(os.fsdecode(path)))
        operation = 'write' if write else 'read'
        if lexical == Path('/dev/null'):
            current = os.stat(lexical, follow_symlinks=False)
            if (stat.S_ISCHR(current.st_mode) and
                    (current.st_dev, current.st_ino, current.st_rdev) == (null.st_dev, null.st_ino, null.st_rdev)):
                return  # Exact genuine character device; no disk-write grant.
            refusal(event, 'changed-null-device', lexical)
        roots = (root,) if write else (root, *readonly)
        # Do not even resolve an obviously outside path into a production tree.
        if not (any(lexical.is_relative_to(base) for base in roots)
                or (not write and lexical in readonly_files)):
            refusal(event, operation, lexical)
        resolved = Path(os.path.realpath(lexical))
        if not (any(resolved.is_relative_to(base) for base in roots)
                or (not write and resolved in readonly_files)):
            refusal(event, operation, lexical)

    original_open = os.open
    state = threading.local()

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            return original_open(path, flags, mode)
        # macOS F_GETPATH is metadata-only. Resolve the actual stable directory
        # descriptor, not the unrelated cwd used by the Python audit event.
        parent = fcntl.fcntl(dir_fd, 50, bytes(1024)).split(b'\0', 1)[0]
        absolute = Path(os.fsdecode(parent)) / os.fsdecode(path)
        directory_only = bool(flags & os.O_DIRECTORY) and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
        if not directory_only:
            check(absolute, 'open', bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        state.verified_relative_open = True
        try:
            return original_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            state.verified_relative_open = False

    os.open = guarded_open

    def is_pty_master(fd, mode):
        """A pseudo-terminal MASTER descriptor (ticket E3: Python owns the data plane over the descriptor the sidecar
        hands over). Admitted exactly: a character device whose own path is `/dev/ptmx` and that names its slave
        (`TIOCPTYGNAME`) as a `/dev/ttys*` device. It names no account file, so it is not an origin bypass; a
        regular file, directory, other device or slave descriptor is still refused."""
        if not stat.S_ISCHR(mode):
            return False
        try:
            if fcntl.fcntl(fd, 50, bytes(1024)).split(b'\0', 1)[0] != b'/dev/ptmx':
                return False
            slave = fcntl.ioctl(fd, 0x40807453, bytes(128)).split(b'\0', 1)[0]  # TIOCPTYGNAME (macOS)
        except OSError:
            return False
        return slave.startswith(b'/dev/ttys') and slave[len(b'/dev/ttys'):].isdigit()

    def check_descriptor(fd, write):
        mode = os.fstat(fd).st_mode
        if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or (fd in {0, 1, 2} and stat.S_ISCHR(mode)) or is_pty_master(fd, mode):
            return
        path = fcntl.fcntl(fd, 50, bytes(1024)).split(b'\0', 1)[0]
        check(path, 'descriptor-write' if write else 'descriptor-read', write)

    def guarded_read(fd, count):
        check_descriptor(fd, False)
        return original_read(fd, count)

    def guarded_write(fd, data):
        check_descriptor(fd, True)
        return original_write(fd, data)

    os.read, os.write = guarded_read, guarded_write

    def audit(event, args):
        if event == 'open':
            path, mode, flags = args
            directory_only = bool(flags & os.O_DIRECTORY) and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
            if getattr(state, 'verified_relative_open', False) or directory_only:
                return  # Metadata traversal only; enumeration is still checked.
            write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            check(path, event, write)
        elif event in {'os.listdir', 'os.scandir'}:
            check(args[0] if args[0] is not None else '.', event)
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.truncate', 'os.utime'}:
            check(args[0], event, True)
        elif event in {'os.rename', 'os.link'}:
            check(args[0], event, True)
            check(args[1], event, True)
        elif event == 'os.symlink':
            check(args[1], event, True)
            target = Path(os.fsdecode(args[0]))
            if not target.is_absolute():
                target = Path(os.fsdecode(args[1])).parent / target
            check(target, event)
        elif event == 'os.chdir':
            if pytest_worktree is not None and isinstance(args[0], (str, bytes, os.PathLike)):
                lexical = Path(os.path.abspath(os.fsdecode(args[0])))
                if (str(lexical) == pytest_worktree['path']
                        and Path(os.path.realpath(os.fsdecode(args[0]))) == lexical):
                    if pytest_worktree_identity(lexical) != pytest_worktree:
                        refusal(event, 'read', lexical)
                    return
            check(args[0], event)

    sys.addaudithook(audit)
    _INSTALLED_ROOT = root
