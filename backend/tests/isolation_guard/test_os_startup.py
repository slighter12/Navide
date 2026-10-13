"""Real guarded native Bash startup; zero extra bytes, no backend/provider."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('bounded macOS OS-startup evaluation', allow_module_level=True)

from test_difference_entry import ENTRY, environment, fixture
sys.path.insert(0, str(ENTRY.parent))
from fixture_exec import stable_bytes, stable_file
from difference_entry import ANCHOR, prepare
from os_startup import shell_profile


def test_no_backend_native_login_startup_has_no_extra_bytes(tmp_path):
    root = fixture(tmp_path)
    workspace = root / 'workspace differences café 世界'
    workspace.mkdir()
    codex_home = root / 'home/.codex-panes/SYNTHETIC_STARTUP'
    codex_home.mkdir(parents=True)
    launch = {'command': ['/bin/bash', '-lc', 'codex'], 'cwd': str(workspace),
              'env': {'CODEX_HOME': str(codex_home)}, 'fixture_args': []}
    payload = json.dumps(launch).encode()
    (root / 'startup-launch.json').write_bytes(payload)
    mode = 'native-shell-startup:' + hashlib.sha256(payload).hexdigest()
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'ownership-probe', str(root), mode],
        cwd=root, env=environment(root), capture_output=True, timeout=45)
    (root / 'startup-entry.stderr').write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'ownership-proof.json'))
    assert proof['passed'] and not proof['backend_launched'] and not proof['metadata_produced']
    assert proof['ctty'] and proof['root_reaped'] and not proof['cleaned']['survivors']
    assert proof['shell_resolution']['shell_pid'] == proof['ready_os']['pid']
    assert proof['shell_resolution']['parent'] == proof['ready_os']['parent']
    assert proof['shell_birth'] == proof['ready_os']['created']
    assert proof['shell_resolution']['resolved'] == str(root / 'codex')
    assert proof['shell_resolution']['PS1_empty'] and proof['shell_resolution']['TERM_PROGRAM_empty']
    assert stable_bytes(root / 'startup-prefix.bin') == b''


def test_actual_builtin_resolution_refuses_owned_wrong_shim_before_exec(tmp_path):
    root = fixture(tmp_path)
    env = prepare(root, 'codex', 'imports')
    wrong = root / 'unregistered-directory'
    wrong.mkdir()
    sentinel = root / 'wrong-shim-executed'
    script = wrong / 'codex'
    script.write_text('#!/bin/bash\nprintf wrong > ' + str(sentinel) + '\n')
    script.chmod(0o700)
    profile = root / 'home/.bash_profile'
    profile.write_text(shell_profile(root, 'codex', str(wrong) + ':' + os.defpath, document={'root': str(root)}))
    frozen = stable_file(profile)[1]
    result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(root / 'guard-profile.sbpl'),
        '/bin/bash', '-lc', 'codex'], env=env, cwd=root, capture_output=True, timeout=10)
    assert result.returncode == 73  # Real builtin gate, not an executed CLI.
    assert result.stdout == result.stderr == b''
    assert not sentinel.exists() and not list(root.glob('shell-resolution-*.json'))
    assert stable_file(profile)[1] == frozen


def test_inverse_exec_deny_rejects_inert_unregistered_owned_image(tmp_path):
    root = fixture(tmp_path)
    env = prepare(root, 'codex', 'imports')
    sentinel = root / 'unknown-executed'
    script = root / 'unregistered-SYNTHETIC'
    script.write_text('#!/bin/bash\nprintf wrong > ' + str(sentinel) + '\n')
    script.chmod(0o700)
    result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(root / 'guard-profile.sbpl'),
        str(script)], env=env, cwd=root, capture_output=True, timeout=10)
    assert result.returncode != 0 and result.stdout == b''
    assert not sentinel.exists()


@pytest.mark.parametrize('fault', ['membership', 'hash', 'selector'])
def test_changed_bound_public_input_refused_under_actual_kernel(tmp_path, fault):
    root = fixture(tmp_path)
    env = prepare(root, 'codex', 'imports')
    document = json.loads(stable_bytes(root / 'guard-identities.json'))
    if fault == 'membership':
        document['os_startup']['members'].append('UNREGISTERED_SYNTHETIC')
    elif fault == 'hash':
        document['os_startup']['files']['/private/etc/profile']['identity']['sha256'] = '0' * 64
    else:
        document['os_selector']['link']['target'] = '/bin/UNREGISTERED_SYNTHETIC'
    payload = json.dumps(document, sort_keys=True).encode()
    (root / 'guard-identities.json').write_bytes(payload)
    env[ANCHOR] = hashlib.sha256(payload).hexdigest()
    code = ('import sys;from pathlib import Path;sys.path.insert(0,' + repr(str(ENTRY.parent)) + ');'
            'from difference_entry import validate_kernel_setup;'
            'validate_kernel_setup(Path(' + repr(str(root)) + '))')
    result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(root / 'guard-profile.sbpl'),
        sys.executable, '-I', '-S', '-c', code], env=env, cwd=root, capture_output=True, timeout=10)
    assert result.returncode != 0 and result.stdout == b''
    assert b'changed public OS ' in result.stderr
    assert b'setup synthetic denial unproved' not in result.stderr
