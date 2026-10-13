"""Finite shim/script/interpreter admission for the difference fixture only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys


def metadata_directory_flags() -> int:
    """Existing search-only ancestor semantics; no directory-data grant."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    # Darwin SDK sys/fcntl.h: O_SEARCH=(0x40000000|O_DIRECTORY).
    search = getattr(os, 'O_SEARCH', 0x40000000) if sys.platform == 'darwin' else 0
    return flags | search


def stable_file(path: Path) -> tuple[bytes, dict]:
    """No-follow file read; keep ancestor descriptors until identities verify."""
    path = path.absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_flags = metadata_directory_flags()
    fd = os.open('/', directory_flags)
    opened = [(fd, os.fstat(fd), None, None)]
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=fd)
            opened.append((child, os.fstat(child), fd, component))
            fd = child
        leaf = os.open(path.name, flags, dir_fd=fd)
        before = os.fstat(leaf)
        opened.append((leaf, before, fd, path.name))
        if not stat.S_ISREG(before.st_mode):
            raise PermissionError('fixture identity is not a regular file')
        data = bytearray()
        while block := os.read(leaf, 65536):
            data.extend(block)
        for item, original, parent, name in opened:
            current = os.fstat(item)
            if (original.st_dev, original.st_ino, original.st_mode) != (current.st_dev, current.st_ino, current.st_mode):
                raise PermissionError('fixture ancestry changed during read')
            if parent is not None:
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino, named.st_mode) != (original.st_dev, original.st_ino, original.st_mode):
                    raise PermissionError('fixture ancestry name changed during read')
        after = os.fstat(leaf)
        fields = ('dev', 'ino', 'mode', 'size', 'mtime_ns', 'ctime_ns')
        if any(getattr(before, 'st_' + key) != getattr(after, 'st_' + key) for key in fields):
            raise PermissionError('fixture identity changed during read')
        identity = {key: getattr(before, 'st_' + key) for key in fields}
        payload = bytes(data)
        return payload, {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(), **identity}
    finally:
        for item, *_rest in reversed(opened):
            os.close(item)


def stable_bytes(path: Path) -> bytes:
    return stable_file(path)[0]


def register_fixture_chain(root: Path, shim: Path, script: Path, interpreter: Path, *, managed: Path | None = None) -> str:
    interpreter = interpreter.resolve(strict=True)
    paths = {'shim': shim, 'script': script, 'interpreter': interpreter,
             'shebang': Path('/bin/sh'), 'bash': Path('/bin/bash')}
    if (root / 'home/.bash_profile').exists():
        paths['profile'] = root / 'home/.bash_profile'
    if managed is not None:
        paths['managed-wrapper'] = managed
    identities = {name: stable_file(path)[1] for name, path in paths.items()}
    payload = json.dumps(identities, sort_keys=True).encode()
    (root / 'exec-identities.json').write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def admit_fixture_command(root: Path, argv: list[str], env: dict[str, str]) -> dict:
    """Finite fixture chain admission; never execute a negative control."""
    def refuse(reason):
        with (root / 'exec-refusals.jsonl').open('a') as stream:
            stream.write(json.dumps({'boundary': 'fixture-executable', 'reason': reason}) + '\n')
        raise PermissionError('fixture executable admission refused ' + reason)

    # Do not resolve or read an unknown executable as a negative control.
    shell = bool(argv and argv[0] == '/bin/bash')
    if shell:
        from .difference_entry import validate_kernel_setup
        document = validate_kernel_setup(root)
        from .isolation import executed_words
        words = executed_words(argv)
        if len(argv) != 3 or argv[1] not in {'-lc', '-ilc'} or len(words) != 2 or words[0] != '/bin/bash':
            if not document.get('principal_runtime_binding'):
                refuse('unregistered-shell-command')
            from .managed_opencode_admission import admit
            return admit(root, argv, env, document)
        program = words[1]
    else:
        program = argv[0] if argv else ''
    if Path(program).name not in {'codex', 'opencode'}:
        refuse('unregistered-command-or-shell')
    found = program if os.path.isabs(program) else shutil.which(program, path=env.get('PATH', ''))
    if found is None or Path(found).absolute() != root / Path(program).name:
        refuse('resolution-outside-registered-shim')
    expected = os.environ.get('NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256')
    if not expected:
        refuse('missing-manifest-anchor')
    payload = stable_bytes(root / 'exec-identities.json')
    if hashlib.sha256(payload).hexdigest() != expected:
        refuse('manifest-anchor-mismatch')
    identities = json.loads(payload)
    roles = {'shim', 'script', 'interpreter', 'shebang', 'bash'}
    if 'managed-wrapper' in identities:
        roles.add('managed-wrapper')
    if 'profile' in identities:
        roles.add('profile')
    if shell and 'profile' not in identities:
        refuse('missing-shell-profile')
    if set(identities) != roles:
        refuse('incomplete-chain')
    for name, identity in identities.items():
        if stable_file(Path(identity['path']))[1] != identity:
            refuse('identity-mismatch-' + name)
    if shell:
        controlled = os.pathsep.join([str(root), str(Path(sys.executable).parent), os.defpath])
        from .os_startup import shell_profile
        expected_profile = shell_profile(root, Path(found).name, controlled, document=document).encode()
        if env.get('PATH') != controlled or stable_bytes(root / 'home/.bash_profile') != expected_profile:
            refuse('changed-shell-path-or-profile')
    interpreter, script = identities['interpreter']['path'], identities['script']['path']
    lines = stable_bytes(Path(found)).decode().splitlines()
    if len(lines) != 2 or lines[0] != '#!/bin/sh' or not lines[1].endswith(' "$@"'):
        refuse('unexpected-shim-body')
    words = shlex.split(lines[1].removesuffix(' "$@"'))
    isolated = ['exec', interpreter, '-I', '-S', '-B', '-u', script, str(root), Path(found).name]
    legacy = ['exec', interpreter, '-u', script, str(root), Path(found).name]
    permitted = [isolated] if os.environ.get('NAVIDE_REGRESSION_KERNEL_MANIFEST_SHA256') else [legacy]
    if words not in permitted:
        refuse('unexpected-forward-chain')
    return {'admitted': True, 'identity_roles': sorted(identities),
            'stdout_producer': 'expected-fixture-script-not-yet-observed',
            'actual_process_provenance': 'not-launched'}
