"""Synthetic argv payload/permission boundary, never a backend/lifespan."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('Darwin fixture payload permission', allow_module_level=True)
from test_difference_entry import environment, fixture, ENTRY
sys.path.insert(0, str(ENTRY.parent))
from fixture_exec import stable_bytes


def test_self_script_role_and_metadata_pre_admission(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe', str(root), 'capture'],
        env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    (root / 'synthetic-capture-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'capture-proof.json'))
    assert proof['passed'] and proof['synthetic_control_not_driver']
    assert proof['invalid_or_late_admissions'] == 6
    assert proof['late_getter_entries'] == proof['invalid_full_record_reads'] == 0


def test_actual_constructor_window_and_late_or_unsupported_refusal(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe', str(root), 'creation'],
        env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    (root / 'synthetic-creation-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'creation-proof.json'))
    assert proof['passed'] and proof['actual_constructor_argv']
    assert proof['late_wrong_and_reaped_refused'] and proof['unsupported_signal_and_watcher_refused_before_spawn']
    assert proof['reaped'] == [row['pid'] for row in proof['captured']]


def test_real_argv_getters_and_unavailable_owner_policy(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe', str(root), 'argv'],
        env=environment(root), cwd=root, capture_output=True, text=True, timeout=30)
    (root / 'synthetic-argv-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'process-argv-proof.json'))
    assert proof['passed'] and proof['foreign_getter_entries'] == 0
    assert proof['wrong_birth_cache_fork_revoke_denied']
    assert proof['unavailable_owner_retained'] and proof['gone_owner_gone']
    assert proof['reaped'] == [row['pid'] for row in proof['captured']]
