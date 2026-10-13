"""Real SELF admission and metadata-only negative/late-capture controls."""
import json
import os
from pathlib import Path


def run(root: Path):
    import psutil
    from .fixture_exec import stable_bytes
    from .process_env_guard import installed
    from . import process_argv_observation as observation
    guard = installed()
    self_row = observation.observe_self(root, 'process-env-probe')
    parent = psutil.Process(os.getpid()).ppid()
    header_path = root / f'self-admission-{os.getpid()}.json'
    full_path = root / f'self-process-{os.getpid()}.json'
    header_bytes, full_bytes = stable_bytes(header_path), stable_bytes(full_path)
    original = observation.stable_bytes
    reads = []
    def count(path):
        reads.append(path)
        return original(path)  # real reader, not a fabricated inspector/cache
    observation.stable_bytes = count
    before = guard.argv_native_entries
    try:
        admitted = observation.admit_bootstrap_owner(root, os.getpid(), parent, 'process-env-probe')
        assert admitted['created'] == self_row['created'] and full_path in reads
        negatives = 0
        for case in ('wrong-parent', 'wrong-birth', 'stale-birth', 'wrong-role', 'missing-header', 'late-payload'):
            reads.clear()
            header = json.loads(header_bytes)
            expected_parent = parent
            if case == 'wrong-parent':
                expected_parent += 1
            elif case in ('wrong-birth', 'stale-birth'):
                header['created'] += 1 if case == 'wrong-birth' else -1
            elif case == 'wrong-role':
                header['role'] = 'driver'
            header_path.write_text(json.dumps(header))
            if case == 'missing-header':
                header_path.unlink()
            if case == 'late-payload':
                full_path.unlink()
            try:
                observation.admit_bootstrap_owner(root, os.getpid(), expected_parent, 'process-env-probe')
            except (PermissionError, FileNotFoundError):
                pass
            else:
                raise AssertionError('missing/stale/late bootstrap capture was admitted')
            assert full_path not in reads, 'invalid admission attempted stored argv read'
            header_path.write_bytes(header_bytes)
            full_path.write_bytes(full_bytes)
            negatives += 1
        assert guard.argv_native_entries == before, 'metadata capture performed late argv extraction'
    finally:
        observation.stable_bytes = original
        header_path.write_bytes(header_bytes)
        full_path.write_bytes(full_bytes)
    (root / 'capture-proof.json').write_text(json.dumps({'passed': True, 'actual_self_role': 'process-env-probe',
        'synthetic_control_not_driver': True, 'invalid_or_late_admissions': negatives,
        'late_getter_entries': 0, 'invalid_full_record_reads': 0}))
