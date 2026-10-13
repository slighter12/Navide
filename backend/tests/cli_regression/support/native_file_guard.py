"""Fixed macOS difference-fixture origins, inherited by its process chain."""
import hashlib
import json
import os
import stat
from pathlib import Path

from fixture_exec import metadata_directory_flags, stable_file
from os_startup import public_selector, public_startup, startup_read_paths


# Fixed, reviewed ticket-04 principal epoch (OpenCode and Codex cases); no environment-selected binding.
# These constants pin the ticket-04 evaluation principal epoch and are inert without its runtime binding.
_PRINCIPAL_ROOT = Path('/private/tmp/navide-t04-opencode-ws-principal-19-94dab7d8a705')
_PRINCIPAL_BINDING_SHA256 = '63109c2479201adb8488a0e888a720254b3c6454f08897c9238fcb7fe1ca0dbf'
_PRINCIPAL_RUNTIME = {'path': '/Users/slighter12/git/personal/Navide-agent-cli-runtime-evaluation/.scratch/agent-cli-runtime-evaluation/evidence/t04/t04-cand-image/navide-cli-runtime',
                      'sha256': 'b2b7248831a1654d7529b36e20a123b133e242c117e536d7b92a82d6d1805f23',
                      'dev': 16777231, 'ino': 64672005, 'mode': 33088, 'size': 846448,
                      'mtime_ns': 1791932436590455588, 'ctime_ns': 1791932436594210990}
_PRINCIPAL_MARKER = {'scope': 't04-opencode-principal-runtime', 'version': 1,
                     'binding_sha256': _PRINCIPAL_BINDING_SHA256}


def principal_fixture(root: Path) -> bool:
    """Only the original TS fixture namespace inside the fixed OWN tmpdir."""
    return root.parent == _PRINCIPAL_ROOT / 'tmp' and root.name.startswith('navide-native-difference-')


def principal_setup(root: Path) -> dict | None:
    """Pre-kernel fixed packet, used only to build the fixture's guarded setup."""
    if not principal_fixture(root):
        return None
    payload, _ = stable_file(_PRINCIPAL_ROOT / 'runtime-binding.json')
    if hashlib.sha256(payload).hexdigest() != _PRINCIPAL_BINDING_SHA256:
        raise PermissionError('changed principal runtime binding')
    document = json.loads(payload)
    expected = {'scope': _PRINCIPAL_MARKER['scope'], 'version': 1,
                'root': str(_PRINCIPAL_ROOT), 'files': {'runtime': _PRINCIPAL_RUNTIME}}
    if document != expected or stable_file(Path(_PRINCIPAL_RUNTIME['path']))[1] != _PRINCIPAL_RUNTIME:
        raise PermissionError('different principal runtime input')
    return {'principal_runtime_binding': dict(_PRINCIPAL_MARKER),
            'files': {'runtime': dict(_PRINCIPAL_RUNTIME)}}


def principal_manifest_runtime(root: Path, document: dict) -> Path | None:
    """Called only with the byte-anchored, kernel-validated setup document."""
    marker = document.get('principal_runtime_binding')
    if not principal_fixture(root):
        if marker is not None:
            raise PermissionError('principal binding outside fixed fixture namespace')
        return None
    if (marker != _PRINCIPAL_MARKER
            or document.get('files', {}).get('runtime') != _PRINCIPAL_RUNTIME):
        raise PermissionError('missing or different principal runtime role')
    return Path(document['files']['runtime']['path'])


def configured_timezone() -> dict:
    """Only the root-owned configured OS timezone route, never account data."""
    localtime = Path('/etc/localtime')
    before = os.lstat(localtime)
    if not stat.S_ISLNK(before.st_mode) or before.st_uid != 0:
        raise PermissionError('unverified configured OS timezone link')
    target = localtime.resolve(strict=True)
    info = os.stat(target, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('unverified configured OS timezone file')
    identity = stable_file(target)[1]
    if os.lstat(localtime) != before or os.stat(target, follow_symlinks=False) != info:
        raise PermissionError('changed configured OS timezone identity')
    aliases = {str(localtime), '/private/etc/localtime', os.readlink(localtime), str(target)}
    if any(not path.startswith('/') for path in aliases):
        raise PermissionError('unregistered configured timezone alias')
    metadata = ('/etc', '/var', '/var/db/timezone/zoneinfo', '/private/var/db/timezone/zoneinfo')
    for path in metadata:
        node = os.lstat(path)
        if not stat.S_ISLNK(node.st_mode) or node.st_uid != 0:
            raise PermissionError('unverified configured timezone route')
    # Search-only stable descriptors also require fstat on canonical ancestors;
    # grant exact metadata nodes, never directory data or /var/db subpaths.
    ancestors = ({str(parent) for parent in target.parents} - {'/'}) | {'/private/etc'}
    for path in ancestors:
        node = os.lstat(path)
        if not stat.S_ISDIR(node.st_mode) or node.st_uid != 0 or node.st_mode & 0o022:
            raise PermissionError('unverified configured timezone ancestor')
    return {'identity': identity, 'aliases': sorted(aliases), 'metadata': sorted(set(metadata) | ancestors)}


def pytest_worktree_identity(path: Path) -> dict:
    """Directory identity only; no directory data or descendant admission."""
    flags = metadata_directory_flags()
    descriptor = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            if os.fstat(child) != os.stat(part, dir_fd=descriptor, follow_symlinks=False):
                os.close(child)
                raise PermissionError('changed pytest worktree ancestor')
            os.close(descriptor)
            descriptor = child
        before = os.fstat(descriptor)
        if before != os.stat('.', dir_fd=descriptor, follow_symlinks=False):
            raise PermissionError('changed pytest worktree identity')
        return {'path': str(path), 'dev': before.st_dev, 'ino': before.st_ino,
                'mode': before.st_mode, 'uid': before.st_uid}
    finally:
        os.close(descriptor)


def pytest_catalog_paths(backend: Path) -> dict[str, Path]:
    """The fixed catalog hook's data closure, never caller-selected origins."""
    fixtures = backend.parent / 'tests/fixtures/cli-regression'
    names = ('catalog', 'aider', 'antigravity', 'claude', 'codex', 'copilot', 'cursor',
             'droid', 'grok', 'kilo', 'kimi', 'mcode', 'muse', 'opencode', 'pi', 'qwen')
    return {**{'pytest-readonly:' + name: fixtures / (name + '.json') for name in names},
            'pytest-readonly:historical': backend.parent / 'docs/testing/cli-regressions.json'}


def pytest_manifest_origins(document: dict, backend: Path) -> tuple[tuple[Path, ...], dict | None]:
    """Only the complete fixed roster in a byte-anchored setup document."""
    selected = {role for role in document['files'] if role.startswith('pytest-readonly:')}
    worktree = document.get('pytest_worktree')
    if not selected and worktree is None:
        return (), None
    expected = pytest_catalog_paths(backend)
    if selected != set(expected) or worktree != pytest_worktree_identity(backend.parent):
        raise PermissionError('different pytest origin roster')
    for role, path in expected.items():
        identity = document['files'][role]
        if identity['path'] != str(path) or stable_file(path)[1] != identity:
            raise PermissionError('changed exact pytest readonly input')
    return tuple(expected.values()), worktree


def native_file_profile(fixture: Path, backend: Path, interpreter: Path, *, difference: bool = False,
                        pytest_document: dict | None = None,
                        principal_document: dict | None = None) -> str:
    fixture, backend, interpreter = fixture.resolve(strict=True), backend.resolve(strict=True), interpreter.resolve(strict=True)
    # Keep OS runtime operations available, but deny all filesystem origins
    # except this fixture, immutable code/toolchain and OS library inputs.
    # No arbitrary config file, home override, vendor binary or network route.
    read_dirs = (fixture, backend, interpreter.parent.parent, Path('/System/Library'), Path('/usr/lib'))
    read_rules = ' '.join('(subpath ' + json.dumps(str(path)) + ')' for path in read_dirs)
    executables = (interpreter, backend / '.venv/bin/python', Path('/usr/bin/sandbox-exec'),
                   Path('/usr/bin/true'), Path('/bin/sh'), Path('/bin/bash'),
                   fixture / 'codex', fixture / 'opencode')
    startup = public_startup() if difference else None
    if difference:
        principal_runtime = (principal_manifest_runtime(fixture, principal_document)
                             if principal_document is not None else None)
        executables += (principal_runtime or backend.parent / 'native/navide-cli-runtime/target/release/navide-cli-runtime',
                        Path('/usr/libexec/path_helper'))
    source_files = (backend.parent / 'native/navide-cli-runtime/src/darwin.rs',
                    backend.parent / 'native/navide-cli-runtime/src/posix.rs') if difference else ()
    pytest_inputs = (pytest_manifest_origins(pytest_document, backend)[0]
                     if pytest_document is not None else ())
    ancestors = sorted({str(parent) for path in (*read_dirs, *executables, *source_files, *pytest_inputs) for parent in path.parents} - {'/'})
    ancestor_rules = ' '.join('(literal ' + json.dumps(path) + ')' for path in ancestors)
    tty_rules = ' (literal "/dev/ptmx") (literal "/dev/tty") (regex #"^/dev/ttys[0-9]+$")' if difference else ''
    read_rules += tty_rules
    read_rules += (' (literal "/dev/null") (literal "/dev/random") (literal "/dev/urandom")'
                   ' (subpath "/dev/fd") (literal "/dev/stdin") (literal "/dev/stdout") (literal "/dev/stderr") ') + ' '.join(
        '(literal ' + json.dumps(str(path)) + ')' for path in executables)
    read_rules += ' ' + ' '.join('(literal ' + json.dumps(str(path)) + ')' for path in source_files)
    if pytest_inputs:
        read_rules += ' ' + ' '.join('(literal ' + json.dumps(str(path)) + ')' for path in pytest_inputs)
    if startup:
        read_rules += ' ' + ' '.join('(literal ' + json.dumps(path) + ')' for path in startup_read_paths(startup))
    exec_rules = ' '.join('(literal ' + json.dumps(str(path)) + ')' for path in executables)
    # The isolated actor boot requires data rights on the OS root directory.
    # Other code/fixture ancestor nodes get metadata rights, NOT enumeration.
    timezone = configured_timezone() if difference else {'aliases': (), 'metadata': ()}
    read_rules += ' ' + ' '.join('(literal ' + json.dumps(path) + ')' for path in timezone['aliases'])
    data_rules = read_rules + ' (literal "/")'
    selector = public_selector() if difference else None
    selector_metadata = ([selector['link']['path'], *(node['path'] for node in selector['ancestors'])]
                         if selector else [])
    read_rules = data_rules + ' ' + ancestor_rules + ' ' + ' '.join(
        '(literal ' + json.dumps(path) + ')' for path in (*timezone['metadata'], *selector_metadata,
            *((node['path'] for node in startup['ancestors']) if startup else ())))
    # Scope inverse path filters to path-backed files: anonymous stdio pipes
    # and runtime operations without a filesystem path are not data origins.
    networking = ('(deny network-outbound (require-not (remote ip "localhost:*")))'
                  '(deny network-inbound (require-not (local ip "localhost:*")))'
                  '(deny network-bind (require-not (local ip "localhost:*")))') if difference else '(deny network*)'
    frozen = ('guard-identities.json', 'guard-profile.sbpl', 'exec-identities.json', 'fixture-marker',
              'home/.bash_profile', 'codex', 'opencode', 'stage-1.bin', 'stage-2.bin') if difference else ()
    frozen_rules = ''.join('(deny file-write* (literal ' + json.dumps(str(fixture / name)) + '))' for name in frozen)
    return ('(version 1)(allow default)'
            '(deny file-read* (require-all (subpath "/") (require-not (require-any ' + read_rules + '))))'
            '(deny file-read-data (require-all (subpath "/") (require-not (require-any ' + data_rules + '))))'
            '(deny file-write* (require-all (subpath "/") (require-not (require-any (subpath '
            + json.dumps(str(fixture)) + ') (subpath "/dev/fd") (literal "/dev/null")'
            ' (literal "/dev/stdout") (literal "/dev/stderr")' + tty_rules + '))))'
            + frozen_rules + networking
            + '(deny process-exec (require-all (subpath "/") (require-not (require-any ' + exec_rules + '))))')
