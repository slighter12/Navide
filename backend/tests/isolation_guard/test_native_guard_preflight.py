"""SAFE macOS enforcement preflight, no backend/sidecar/provider execution."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

if sys.platform != 'darwin':
    pytest.skip('macOS-only synthetic boundary evaluation; ticket08 pending', allow_module_level=True)


def test_os_guard_available_before_any_native_release(tmp_path):
    assert sys.platform == 'darwin'
    physical = tmp_path / 'synthetic-physical-home'
    physical.mkdir()
    (physical / 'sentinel').write_bytes(b'SYNTHETIC_NEVER_PROVIDER_DATA')
    # Availability only. true reads no account/provider input and does not
    # start a child, contact a provider, run a backend or use a credential.
    # The restrictive first-phase SIGABRT is preserved, not an availability
    # result. Enforcement is proved separately by the native origin matrix.
    profile = '(version 1)(allow default)'
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/usr/bin/true'],
                            env={'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin', 'TMPDIR': str(tmp_path)},
                            capture_output=True, text=True, timeout=10)
    (tmp_path / 'native-preflight.json').write_text(json.dumps({
        'operation': 'sandbox-exec-true-availability-only', 'exit_code': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr,
        'native_filesystem_enforcement': 'NOT_PROVED',
        'provider_or_backend_launched': False,
    }, indent=2))
    assert result.returncode == 0, result.stderr
    # Success here is only availability. A later SAFE inherited-denial probe
    # is still required before any native/principal/backend launch release.
