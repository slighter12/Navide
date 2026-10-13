"""Only created synthetic actors/canaries; no backend or live payload dumps."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('bounded Darwin process-payload guard', allow_module_level=True)

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / 'tests/cli_regression/support'))
from fixture_exec import stable_bytes
from test_difference_entry import environment, fixture, ENTRY


def test_directory_fd_maps_to_existing_roots_only(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY.with_name('directory_fd_probe.py')), str(root)],
        cwd=root, env=environment(root), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'OWN_DIRECTORY_FD_ALLOWED_OUTSIDE_REFUSED\n'


def test_self_only_real_payload_and_sweep_with_native_bypass_limit(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe', str(root)],
        cwd=root, env=environment(root), capture_output=True, text=True, timeout=35)
    (root / 'synthetic-probe-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'process-env-proof.json'))
    assert proof['passed'] is True
    assert proof['unregistered_getter_entries'] == 0
    assert proof['self_read'] and proof['lower_and_multiattribute_denied']
    assert proof['cache_after_revoke_denied'] and proof['fork_permission_denied']
    assert proof['real_sweep_live_retained'] and proof['real_sweep_orphan_reclaimed']
    assert proof['reaped'] == [row['pid'] for row in proof['captured']]
    assert proof['universal_native_isolation'] is False
    assert proof['native_canary_obtained'] or proof['native_canary_return'] == -1
