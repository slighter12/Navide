"""Fixed difference-fixture boundary; synthetic controls and imports only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('macOS-only synthetic boundary evaluation; ticket08 pending', allow_module_level=True)

BACKEND = Path(__file__).resolve().parents[2]
ENTRY = BACKEND / 'tests/cli_regression/support/difference_entry.py'
BOOTSTRAP = ENTRY.with_name('account_bootstrap.py')
sys.path.insert(0, str(BACKEND))
from tests.cli_regression.support.fixture_exec import stable_bytes


def environment(root):
    return {'HOME': str(root / 'home'), 'USERPROFILE': str(root / 'home'),
            'TMPDIR': str(root), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1'}


def fixture(tmp_path):
    root = tmp_path / 'fixture'
    root.mkdir()
    (root / 'fixture-marker').write_text('SYNTHETIC_T04_R2_FIXED_ENTRY')
    return root


def test_genuine_null_device_rdwr_does_not_admit_alias_or_disk_writes(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'null-device-probe', str(root)],
        cwd=root, env=environment(root), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'EXACT_NULL_RDWR_AND_NEGATIVE_CONTROLS' in result.stdout


def test_missing_kernel_setup_refused_before_import_gate(tmp_path):
    root = fixture(tmp_path)
    code = (f'import sys; from pathlib import Path; sys.path.insert(0, {str(BOOTSTRAP.parent)!r}); '
            f'from account_bootstrap import initialize; initialize(Path({str(root)!r})); '
            'print("UNGUARDED_IMPORT_GATE")')
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code],
                            env=environment(root), cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'UNGUARDED_IMPORT_GATE' not in result.stdout
    assert 'kernel guard setup' in result.stderr


@pytest.mark.parametrize('target', ['driver', 'application'])
def test_inherited_guard_precedes_successful_import(tmp_path, target):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'imports', str(root), target],
                            env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'import-proof.json'))
    assert proof['target'] == target
    assert proof['kernel_origin_denied_before_import'] is True
    assert proof['source_helper_unchanged'] is True
    assert proof['refusals'] == (1 if target == 'application' else 0)
    assert proof['account_home'] == str(root / 'home')


def test_registered_login_shell_has_os_collected_stdout_producer(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'shell-probe', str(root)],
                            env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'shell-proof.json'))
    assert proof['os_matches_fixture'] is True
    assert proof['output'].startswith('SYNTHETIC_FIXTURE_STDOUT ')
    assert proof['root_reaped'] is True
    assert proof['metadata_produced'] is False


def test_borrowed_synthetic_descriptor_closed_by_fixed_entry(tmp_path):
    root = fixture(tmp_path)
    physical = tmp_path / 'borrowed-SYNTHETIC'
    physical.write_bytes(b'SYNTHETIC_NOT_PROVIDER_DATA')
    descriptor = os.open(physical, os.O_RDONLY)
    try:
        result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'fd-probe', str(root), str(descriptor)],
                                env=environment(root), cwd=root, capture_output=True, text=True,
                                pass_fds=(descriptor,), timeout=30)
    finally:
        os.close(descriptor)
    assert result.returncode == 0, result.stderr
    assert json.loads(stable_bytes(root / 'fd-proof.json')) == {'closed_before_imports': True, 'observed': False}


def test_borrowed_regular_stdin_rejected_without_observation(tmp_path):
    root = fixture(tmp_path)
    physical = tmp_path / 'stdio-SYNTHETIC'
    physical.write_bytes(b'SYNTHETIC_NOT_PROVIDER_DATA')
    with physical.open('rb') as source:
        result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'imports', str(root), 'application'],
                                stdin=source, env=environment(root), cwd=root, capture_output=True, text=True)
        assert source.tell() == 0
    assert result.returncode != 0 and 'unregistered stdio origin' in result.stderr
    assert not (root / 'import-proof.json').exists()


@pytest.mark.parametrize('operation', ['manifest', 'profile', 'path', 'unknown', 'substitution', 'runtime'])
def test_changed_or_unknown_chain_refused_without_execution(tmp_path, operation):
    root = fixture(tmp_path)
    (root / 'unrecognized-SYNTHETIC-runtime').write_bytes(b'SYNTHETIC_NON_EXECUTABLE')
    with subprocess.Popen([sys.executable, '-I', '-S', str(ENTRY), 'admission-probe', str(root), operation],
                          env=environment(root), cwd=root, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == 'SYNTHETIC_ADMISSION_READY'
            if operation == 'manifest':
                (root / 'exec-identities.json').write_bytes(b'{"changed":"SYNTHETIC"}')
            elif operation == 'profile':
                (root / 'home/.bash_profile').write_text('export PATH=/SYNTHETIC_UNKNOWN\n')
            output, stderr = child.communicate('CHECK\n', timeout=10)
            assert child.returncode == 0, stderr
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    assert json.loads(stable_bytes(root / 'admission-proof.json')) == {'denied': True, 'executed': False}


@pytest.mark.parametrize('case', ['valid', 'identity', 'extra', 'creator'])
def test_synthetic_metadata_oracle_never_filters_unexpected_sources(tmp_path, case):
    root = fixture(tmp_path)
    for stage in (1, 2):
        (root / f'stage-{stage}.bin').write_bytes(b'{"synthetic":"T04_oracle_only"}\n')
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'metadata-probe', str(root), case],
                            env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(stable_bytes(root / 'metadata-proof.json')) == {
        'denied': case != 'valid', 'scope': 'synthetic-output-oracle-only',
        'actual_peer_native_observer_launched': False}


def test_loopback_socket_helper_under_inherited_kernel_guard(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'loopback-probe', str(root)],
                            env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(stable_bytes(root / 'loopback-proof.json')) == {'echo': 'SYNTHETIC_LOOPBACK_ONLY'}
