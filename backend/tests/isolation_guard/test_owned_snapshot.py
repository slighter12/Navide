"""Real guarded Darwin native ownership and durable recovery contract."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('real Darwin metadata/recovery contract', allow_module_level=True)

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / 'tests/cli_regression/support'))
from fixture_exec import stable_bytes
from test_difference_entry import environment, fixture, ENTRY


@pytest.mark.parametrize('mode', ['recovery', 'native', 'native-orphan', 'monitor-disappearance', 'watch-root'])
def test_actual_owned_identity_and_escaping_descendant_recovery(tmp_path, mode):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'ownership-probe', str(root), mode],
        cwd=root, env=environment(root), capture_output=True, text=True, timeout=45)
    (root / 'ownership-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'ownership-proof.json'))
    assert proof['passed'] is True
    if mode.startswith('native'):
        assert proof['registry_roundtrip'] is True
        assert proof['escaping_descendant'] is True
        assert proof['cleaned']['root_reaped'] is True
        assert proof['cleaned']['survivors'] == []
        assert proof['retained_orphan_new_fork'] == (mode == 'native-orphan')
        if mode == 'native-orphan':
            assert len(proof['os']) == 3
    elif mode == 'watch-root':
        assert proof['watch_root_existed_before_backend'] is True
        assert proof['empty_initial_watch_root'] is True
        assert proof['backend_launched'] is False
    elif mode == 'monitor-disappearance':
        assert proof['real_disappearance_errno'] == 3
        assert proof['recorded_birth_retained'] is True
        assert proof['actual_reaped'] == [proof['os'][0]['pid']]
    else:
        assert proof['actual_live_gone_command'] is True
        assert proof['leaf_unknown_command_fault'] is True
        assert proof['mismatch_not_signalled'] is True
        assert proof['exited_pid_absent'] is True
        assert set(proof['actual_reaped']) == {row['pid'] for row in proof['os']}
