"""Fixed macOS difference-fixture entry; kernel boundary precedes app imports."""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import sys

sys.path.insert(0, str(Path(__file__).parent))
from fixture_exec import register_fixture_chain, stable_bytes, stable_file
from native_file_guard import configured_timezone, native_file_profile, principal_setup, principal_manifest_runtime
from cli_shim import install_cli_shim
from os_startup import public_selector, public_startup, shell_profile
from process_env_guard import dependency_inputs

BACKEND = Path(__file__).resolve().parents[3]
RUNTIME = BACKEND.parent / 'native/navide-cli-runtime/target/release/navide-cli-runtime'
ANCHOR = 'NAVIDE_REGRESSION_KERNEL_MANIFEST_SHA256'
CANARY = 'NAVIDE_REGRESSION_KERNEL_CANARY'


def validate_stdio(root: Path) -> None:
    """Only anonymous transport, dev/null, or the OWN backend log."""
    for descriptor in (0, 1, 2):
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISFIFO(mode):
            continue
        if stat.S_ISSOCK(mode):
            transport = socket.socket(fileno=descriptor)
            try:
                if transport.family == socket.AF_UNIX and not transport.getsockname() and not transport.getpeername():
                    continue
            finally:
                transport.detach()
            raise PermissionError('unregistered stdio socket')
        path = fcntl.fcntl(descriptor, 50, bytes(1024)).split(b'\0', 1)[0]
        logs = {os.fsencode(root / name) for name in ('backend.log', 'stage-1-backend.log', 'stage-2-backend.log')}
        if path == b'/dev/null' or (stat.S_ISREG(mode) and path in logs and descriptor in (1, 2)):
            continue
        raise PermissionError('unregistered stdio origin')


def validate_kernel_setup(root: Path) -> dict:
    """Check the inherited kernel with a libc read of OWN synthetic data only."""
    anchor, canary = os.environ.get(ANCHOR), os.environ.get(CANARY)
    if not anchor or not canary:
        raise PermissionError('missing kernel guard setup')
    payload = stable_bytes(root / 'guard-identities.json')
    if hashlib.sha256(payload).hexdigest() != anchor:
        raise PermissionError('changed kernel guard setup manifest')
    document = json.loads(payload)
    if document['root'] != str(root) or document['canary'] != canary:
        raise PermissionError('invalid kernel guard setup origin')
    profile = stable_bytes(root / 'guard-profile.sbpl')
    if hashlib.sha256(profile).hexdigest() != document['profile_sha256']:
        raise PermissionError('changed kernel guard profile')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.open.argtypes, libc.open.restype = [ctypes.c_char_p, ctypes.c_int], ctypes.c_int
    descriptor = libc.open(os.fsencode(canary), os.O_RDONLY)
    error = ctypes.get_errno()
    if descriptor >= 0:
        libc.close(descriptor)
        raise PermissionError('kernel guard setup did not deny synthetic origin')
    if error not in (errno.EPERM, errno.EACCES):
        raise PermissionError('kernel guard setup synthetic denial unproved')
    if public_selector() != document['os_selector']:
        raise PermissionError('changed public OS shell selector')
    if public_startup() != document['os_startup']:
        raise PermissionError('changed public OS startup roster')
    for role, identity in document['files'].items():
        try:
            current = stable_file(Path(identity['path']))[1]
        except OSError as error:
            error.add_note('guard_input_role_sha256=' + hashlib.sha256(role.encode()).hexdigest())
            raise
        if current != identity:
            raise PermissionError('changed guarded source identity: ' + role)
    principal_manifest_runtime(root, document)
    return document


def prepare(root: Path, vendor: str, mode: str) -> dict[str, str]:
    """No application imports, unknown executables or production data here."""
    if sys.platform != 'darwin' or vendor not in {'codex', 'opencode'}:
        raise PermissionError('unregistered difference fixture')
    marker = stable_bytes(root / 'fixture-marker')
    if not marker.startswith(b'SYNTHETIC_T04_'):
        raise PermissionError('missing synthetic fixture identity')
    for name in ('home', 'tmp', 'data', 'config', 'cache', 'share', 'state'):
        (root / name).mkdir(exist_ok=True)
    interpreter = Path(sys._base_executable).resolve(strict=True)
    script = Path(__file__).with_name('native_difference_peer.py')
    paths = os.pathsep.join([str(root), str(Path(sys.executable).parent), os.defpath])
    principal_document = principal_setup(root)
    profile_document = {'root': str(root), **(principal_document or {})}
    (root / 'home/.bash_profile').write_text(shell_profile(root, vendor, paths, document=profile_document))
    shim = install_cli_shim(root, vendor, [str(interpreter), '-I', '-S', '-B', '-u', str(script), str(root), vendor])
    managed = None
    if vendor == 'opencode' and principal_document is not None:
        from managed_opencode_admission import write_descriptor
        managed = write_descriptor(root, sys.executable)
    exec_anchor = register_fixture_chain(root, shim, script, interpreter, managed=managed)
    # This directory is created by this owner, not selected from account data.
    physical = root.parent / (root.name + '-physical-SYNTHETIC')
    physical.mkdir(mode=0o700)
    canary = physical / 'sentinel'
    canary.write_bytes(b'SYNTHETIC_T04_KERNEL_CANARY_NEVER_PROVIDER_DATA')
    principal_runtime = (principal_manifest_runtime(root, principal_document)
                         if principal_document is not None else None)
    files = {'runtime': principal_runtime or RUNTIME, 'interpreter': interpreter, 'bash': Path('/bin/bash'),
             'sh': Path('/bin/sh'), 'marker': root / 'fixture-marker',
             'askpass': BACKEND / 'agent_team_backend/git_askpass_helper.py',
             'posix-paths': BACKEND / 'agent_team_backend/osplat/_posix_paths.py'}
    for name in ('difference_entry', 'account_bootstrap', 'account_boundary', 'fixture_exec',
                 'native_file_guard', 'isolation', 'backend_process', 'native_difference_driver',
                 'native_difference_peer', 'darwin_ownership_probe', 'os_startup',
                 'process_env_probe', 'process_env_target', 'process_env_guard',
                 'process_argv_probe', 'process_argv_observation', 'creation_window_probe', 'process_argv_capture_probe',
                 'runtime_self_probe', 'loop_binding_probe', 'recovery_self_probe'):
        files[name] = Path(__file__).with_name(name + '.py')
    if mode in {'driver', 'metadata-probe'}:
        for stage in (1, 2):
            files[f'stage-{stage}'] = root / f'stage-{stage}.bin'
    if managed is not None:
        files['managed_opencode_admission'] = Path(__file__).with_name('managed_opencode_admission.py')
        files['managed-opencode-descriptor'] = managed
    files.update(dependency_inputs(BACKEND))
    timezone = configured_timezone()
    files['configured-os-timezone'] = Path(timezone['identity']['path'])
    files['darwin-process-tree'] = BACKEND / 'agent_team_backend/osplat/_darwin.py'
    files['native-darwin'] = BACKEND.parent / 'native/navide-cli-runtime/src/darwin.rs'
    files['native-posix'] = BACKEND.parent / 'native/navide-cli-runtime/src/posix.rs'
    profile = native_file_profile(root, BACKEND, interpreter, difference=True,
                                  principal_document=principal_document)
    (root / 'guard-profile.sbpl').write_text(profile)
    document = {'root': str(root), 'canary': str(canary), 'os_selector': public_selector(),
                'os_startup': public_startup(), 'shell_path': paths,
                'profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
                'files': {role: stable_file(path)[1] for role, path in files.items()}}
    if principal_document is not None:
        document['principal_runtime_binding'] = principal_document['principal_runtime_binding']
    payload = json.dumps(document, sort_keys=True).encode()
    with (root / 'guard-identities.json').open('xb') as stream:
        stream.write(payload)
    return {'HOME': str(root / 'home'), 'USERPROFILE': str(root / 'home'),
            'TMPDIR': str(root / 'tmp'), 'PATH': paths, 'LANG': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', ANCHOR: hashlib.sha256(payload).hexdigest(),
            CANARY: str(canary), 'NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256': exec_anchor,
            **({'NAVIDE_ACR_EVAL_RUNTIME': 'rust-shell'} if mode == 'driver' else {})}


def main():
    mode, supplied_root, *arguments = sys.argv[1:]
    if mode not in {'imports', 'shell-probe', 'loopback-probe', 'fd-probe', 'admission-probe', 'metadata-probe', 'ownership-probe', 'process-env-probe', 'null-device-probe', 'driver'}:
        raise PermissionError('unregistered difference entry mode')
    root = Path(supplied_root).resolve(strict=True)
    vendor = arguments[0] if mode == 'driver' else 'codex'
    validate_stdio(root)
    environment = prepare(root, vendor, mode)
    if mode == 'process-env-probe':
        environment['CODEX_HOME'] = str(root / 'home/.codex-panes/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa')
    # Metadata of this process's descriptors only. Seatbelt cannot revoke a
    # borrowed file's pre-existing read permission; close all non-stdio FDs.
    for value in os.listdir('/dev/fd'):
        if value.isdecimal() and int(value) > 2:
            try:
                os.close(int(value))
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
    # exec, not an unguarded application retry. All descendants inherit this.
    command = ['/usr/bin/sandbox-exec', '-f', str(root / 'guard-profile.sbpl'),
               sys.executable, '-I', '-S', str(Path(__file__).with_name('account_bootstrap.py')),
               mode, str(root), *arguments]
    os.chdir(root)
    os.execve(command[0], command, environment)


if __name__ == '__main__':
    main()
