"""Synthetic OS boundary probes only; never start the backend or sidecar."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('macOS-only synthetic boundary evaluation; ticket08 pending', allow_module_level=True)

PROBE = Path(__file__).with_name('boundary_probe.py')
BACKEND = Path(__file__).resolve().parents[2]


def probe(tmp_path, operation):
    fixture = tmp_path / 'fixture'
    physical = tmp_path / 'synthetic-physical-home'
    fixture.mkdir()
    physical.mkdir()
    sentinel = physical / 'rollout-synthetic.jsonl'
    sentinel.write_text('{"type":"session_meta","synthetic":true}\n')
    (fixture / 'escape').symlink_to(physical, target_is_directory=True)
    (physical / 'unrecognized-executable').write_text('#!/bin/sh\nexit 99\n')
    (physical / 'unrecognized-executable').chmod(0o700)
    env = {'PATH': os.defpath, 'HOME': str(fixture), 'USERPROFILE': str(fixture),
           'TMPDIR': str(fixture), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONNOUSERSITE': '1', 'LANG': 'C.UTF-8'}
    result = subprocess.run([sys.executable, '-I', '-S', str(PROBE), str(BACKEND),
                             str(fixture), str(physical), operation],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    (tmp_path / 'diagnostic-stderr').write_text(result.stderr)
    return json.loads(result.stdout), sentinel


@pytest.mark.parametrize('operation', ['account-read', 'write', 'list', 'scan', 'symlink-read', 'symlink-write', 'preopened-read', 'dirfd-read'])
def test_outside_fixture_origin_is_denied_before_observation(tmp_path, operation):
    result, sentinel = probe(tmp_path, operation)
    assert result['denied'] is True
    assert result['physical_content_observed'] is False
    assert result['denial_count'] > 0
    assert sentinel.read_text() == '{"type":"session_meta","synthetic":true}\n'


# Real import ordering/oracle now lives at the stronger inherited-kernel entry
# seam in test_difference_entry.py. The earlier overstrict red is retained.


@pytest.mark.parametrize('operation', ['exec-admission', 'exec-tampered-script', 'exec-tampered-shim', 'exec-shell', 'exec-outside-shim'])
def test_unknown_or_changed_chain_is_refused_before_execution(tmp_path, operation):
    result, _ = probe(tmp_path, operation)
    assert result['denied'] is True
    assert result['physical_content_observed'] is False


def test_registered_fixture_chain_admission_does_not_launch(tmp_path):
    result, _ = probe(tmp_path, 'exec-registered')
    assert result == {'denied': False, 'physical_content_observed': False,
                      'operation': 'exec-registered', 'completed': True, 'denial_count': 0}
