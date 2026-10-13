"""OS-enforced synthetic origins; real backend/native runtime never launched."""
from pathlib import Path
import hashlib
import json
import os
import select
import subprocess
import sys

import psutil
import pytest

if sys.platform != 'darwin':
    pytest.skip('macOS-only synthetic boundary evaluation; ticket08 pending', allow_module_level=True)

BACKEND = Path(__file__).resolve().parents[2]
GUARD = BACKEND / 'tests/cli_regression/support/native_file_guard.py'
PROBE = Path(__file__).with_name('native_origin_probe.py')


sys.path.insert(0, str(GUARD.parent))
sys.path.insert(0, str(BACKEND))
from tests.cli_regression.support.fixture_exec import stable_bytes


def profile_for(fixture):
    namespace = {}
    exec(compile(stable_bytes(GUARD), str(GUARD), 'exec'), namespace)
    return namespace['native_file_profile'](fixture, BACKEND, Path(sys._base_executable).resolve(), difference=True)


def run_owned_probe(receipts, profile, fixture, physical, operation, *, shell=False):
    receipts.mkdir(exist_ok=True)
    (receipts / 'profile.sbpl').write_text(profile)
    command = ['/usr/bin/sandbox-exec', '-p', profile, sys.executable, '-I', '-S', '-B',
               str(PROBE), str(fixture), str(physical), operation]
    if shell:
        command[3:3] = ['/bin/sh', '-c', 'exec "$@"', 'owned-native-guard']
    borrowed = None
    if operation in {'preopened-read', 'dirfd-read'}:
        path = physical if operation == 'dirfd-read' else physical / 'sentinel'
        borrowed = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if operation == 'dirfd-read' else 0))
        command.append(str(borrowed))
    expected_argv = command[7:] if shell else command[3:]
    with subprocess.Popen(command, cwd=fixture, pass_fds=() if borrowed is None else (borrowed,),
                          env={'HOME': str(fixture), 'PATH': os.defpath, 'TMPDIR': str(fixture), 'LANG': 'C', 'LC_ALL': 'C'},
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        ready = ''
        identity = {'status': 'UNOBSERVED_BEFORE_READY', 'spawn_pid': child.pid}
        try:
            if select.select([child.stdout], [], [], 10)[0]:
                ready = child.stdout.readline().strip()
            if ready == 'SYNTHETIC_NATIVE_GUARD_READY':
                process = psutil.Process(child.pid)
                identity = {'pid': process.pid, 'created': process.create_time(), 'parent': process.ppid(),
                            'exe': process.exe(), 'argv': process.cmdline()}
            output, stderr = child.communicate('GO\n' if ready else '', timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    if borrowed is not None:
        os.close(borrowed)
    receipt = {'identity': identity, 'exit_code': child.returncode, 'stderr': stderr,
               'ready': ready, 'output': output, 'data': 'synthetic-only',
               'profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
               'probe_sha256': hashlib.sha256(stable_bytes(PROBE)).hexdigest(),
               'expected_argv': expected_argv, 'root_reaped': child.poll() is not None}
    (receipts / 'native-identity.json').write_text(json.dumps(receipt, indent=2))
    return receipt


def assert_denied(receipt):
    assert receipt['exit_code'] == 0, receipt['stderr']
    assert receipt['ready'] == 'SYNTHETIC_NATIVE_GUARD_READY'
    identity = receipt['identity']
    assert identity['exe'] == str(Path(sys._base_executable).resolve())
    assert identity['argv'] == receipt['expected_argv']
    result = json.loads(receipt['output'])
    assert result['pid_claim'] == identity['pid']
    assert result['denied'] is True
    assert result['observed'] is False


@pytest.mark.parametrize('shell', [False, True], ids=['direct', 'inherited-sh'])
@pytest.mark.parametrize('operation', ['read', 'write', 'list', 'scan', 'symlink-read', 'symlink-write',
                                        'dirfd-read', 'readonly-source-write'])
def test_native_syscall_cannot_observe_outside_fixture(tmp_path, operation, shell):
    fixture, physical = tmp_path / 'fixture', tmp_path / 'synthetic-physical-home'
    fixture.mkdir()
    physical.mkdir()
    sentinel = physical / 'sentinel'
    sentinel.write_bytes(b'SYNTHETIC_NEVER_PROVIDER_DATA')
    (fixture / 'fixture-marker').write_bytes(b'SYNTHETIC_T04_READONLY_SOURCE')
    marker = stable_bytes(fixture / 'fixture-marker')
    (fixture / 'escape').symlink_to(physical, target_is_directory=True)
    receipt = run_owned_probe(tmp_path, profile_for(fixture), fixture, physical, operation, shell=shell)
    assert_denied(receipt)
    assert stable_bytes(sentinel) == b'SYNTHETIC_NEVER_PROVIDER_DATA'
    assert stable_bytes(fixture / 'fixture-marker') == marker


@pytest.mark.parametrize('shell', [False, True], ids=['direct', 'inherited-sh'])
def test_raw_kernel_limit_borrowed_fd_requires_entry_closure(tmp_path, shell):
    # The earlier 2 failed denial assertions are preserved, not relabelled.
    # This documents the OS limit; fixed-entry closure has a separate test.
    fixture, physical = tmp_path / 'fixture', tmp_path / 'synthetic-physical-home'
    fixture.mkdir()
    physical.mkdir()
    (physical / 'sentinel').write_bytes(b'SYNTHETIC_NEVER_PROVIDER_DATA')
    receipt = run_owned_probe(tmp_path, profile_for(fixture), fixture, physical, 'preopened-read', shell=shell)
    assert receipt['exit_code'] == 0 and receipt['root_reaped'] is True
    assert receipt['identity']['exe'] == str(Path(sys._base_executable).resolve())
    assert receipt['identity']['argv'] == receipt['expected_argv']
    result = json.loads(receipt['output'])
    assert result['pid_claim'] == receipt['identity']['pid']
    assert result['fresh_origin_denied'] is True
    assert result['denied'] is False and result['observed'] is True


# One-variable policy experiments remain in the preserved first-phase snapshot
# and receipts; they are not release assertions in this fixed-boundary suite.
