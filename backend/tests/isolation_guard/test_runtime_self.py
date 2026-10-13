"""Native-only private ready controls; no server, lifespan, CLI or principal."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('real Darwin SELF evidence', allow_module_level=True)
from test_difference_entry import environment, fixture, ENTRY
sys.path.insert(0, str(ENTRY.parent))
from fixture_exec import stable_bytes


@pytest.mark.parametrize('selected', ['auto', 'asyncio'])
def test_actual_uvicorn_factory_native_self_and_reap(tmp_path, selected):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe',
        str(root), 'runtime-self', selected], env=environment(root), cwd=root,
        capture_output=True, text=True, timeout=30)
    (root / 'controlled-runtime-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'runtime-loop-proof.json'))
    assert proof['passed'] and proof['real_self_equal']
    assert not proof['app_started'] and not proof['lifespan_started']
    assert proof['constructor_entries'] == (0 if selected == 'auto' else 1)
    assert proof['actual_loop'] == ('uvloop.Loop' if selected == 'auto' else 'asyncio.unix_events._UnixSelectorEventLoop')
    assert proof['reaped'] == [item['pid'] for item in proof['captured']]
    assert proof['exit_code'] == 0 and not proof.get('rescued', False)
    assert proof['negative_labels'] == ['missing', 'pid', 'parent', 'birth', 'image', 'generation', 'error', 'source']
    assert proof['ownership_startup_partial_record_preserved']
    assert proof['ownership_startup_reader_negatives'] == ['missing', 'partial-ready', 'missing-self',
        'error-self', 'wrong-pid', 'wrong-parent', 'stale-birth', 'wrong-image', 'wrong-generation',
        'wrong-source', 'duplicate', 'malformed']


def test_original_native_startup_protocol_and_cleanup_are_unchanged(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe', str(root),
                             'native-startup-wire'], cwd=root, env=environment(root),
                            capture_output=True, timeout=40)
    (root / 'native-wire.stderr').write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'native-wire-proof.json'))
    assert [row['label'] for row in proof] == ['eof', 'shutdown', 'stale', 'fatal', 'protocol']
    assert all(row['reaped'] and row['passed'] and not row.get('rescued') for row in proof)


def test_recovery_producer_self_api_and_missing_wrong_stale_proof(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe',
        str(root), 'recovery-self'], env=environment(root), cwd=root,
        capture_output=True, text=True, timeout=30)
    (root / 'recovery-self-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'recovery-self-proof.json'))
    assert proof['passed'] and proof['actual_self_api_equal'] and proof['root_pid_retained']
    assert proof['foreign_refused_before_payload'] and proof['gone_api_empty']
    assert not proof['application_recovery_launched']
    assert proof['negatives'] == ['missing', 'pid', 'birth', 'parent', 'image', 'source', 'equality']
    assert proof['reaped'] == [row['pid'] for row in proof['captured']]


def test_unsupported_actual_factory_loop_method_and_uv_constructor_before_spawn(tmp_path):
    root = fixture(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', str(ENTRY), 'process-env-probe',
        str(root), 'loop-binding'], env=environment(root), cwd=root,
        capture_output=True, text=True, timeout=30)
    (root / 'loop-negative-stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    proof = json.loads(stable_bytes(root / 'loop-binding-proof.json'))
    assert proof['passed'] and proof['created_actors'] == proof['actual_spawn_entries'] == proof['payload_entries'] == 0
    assert proof['denied'] == ['factory', 'factory', 'loop', 'method', 'method', 'uvloop-constructor', 'image']
