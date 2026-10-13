"""Producer SELF API proof and default foreign/gone policy; no app recovery."""
import json
import os
from pathlib import Path
import subprocess
import sys


def run(root: Path):
    from psutil import _psosx
    from .fixture_exec import stable_bytes, stable_file
    from .process_env_guard import installed
    from agent_team_backend import osplat, pty_registry
    command = [sys.executable, '-I', '-S', '-B', '-u',
               str(Path(__file__).with_name('native_difference_peer.py')), str(root), 'codex',
               '--ownership-self-probe']
    actor = subprocess.Popen(command, cwd=root, env=dict(os.environ), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    meta = _psosx.Process(actor.pid)
    born, parent = meta.create_time(), meta.ppid()
    proof = {'captured': [{'pid': actor.pid, 'created': born, 'parent': parent}],
             'reaped': [], 'application_recovery_launched': False}
    try:
        actor.stdout.readline()
        path = root / 'ownership-command-self.json'
        assert path.exists(), 'missing genuine recovery SELF positive proof'
        exe = _psosx.Process(actor.pid).exe()
        current = {'pid': actor.pid, 'created': born, 'parent': parent,
                   'exe': exe, 'image': stable_file(Path(exe))[1]}
        from .process_argv_observation import validate_recovery_self
        assert validate_recovery_self(root, current)['argv'] == command
        before = installed().argv_native_entries
        assert osplat.process_tree.command_of(actor.pid) is None
        assert pty_registry._backend_alive(actor.pid) is True
        assert installed().argv_native_entries == before
        from copy import deepcopy
        negatives = []
        for label in ('missing', 'pid', 'birth', 'parent', 'image', 'source', 'equality'):
            negative = root / ('negative-recovery-' + label)
            negative.mkdir()
            (negative / 'guard-identities.json').write_bytes(stable_bytes(root / 'guard-identities.json'))
            header_name = f'self-admission-{actor.pid}.json'
            (negative / header_name).write_bytes(stable_bytes(root / header_name))
            if label != 'missing':
                row = json.loads(stable_bytes(path))
                row['argv'][6] = str(negative)  # Explicit crafted negative payload, never OS proof.
                row['command_of'] = ' '.join(row['argv'])
                if label == 'birth':
                    row['created'] += 1
                elif label in ('pid', 'parent'):
                    row[label] += 1
                elif label == 'image':
                    row['image']['sha256'] = '0' * 64
                elif label == 'source':
                    row['source']['sha256'] = '0' * 64
                else:
                    row['command_of'] = 'SYNTHETIC_WRONG_API_EQUALITY'
                from .process_argv_observation import write_observation
                write_observation(negative, path.name, row)
            try:
                validate_recovery_self(negative, current)
            except (PermissionError, FileNotFoundError):
                negatives.append(label)
            else:
                raise AssertionError('invalid recovery SELF proof admitted')
        proof.update(passed=True, foreign_refused_before_payload=True, negatives=negatives,
                     actual_self_api_equal=True, root_pid_retained=True)
    finally:
        actor.stdin.write(b'DONE\n')
        actor.stdin.flush()
        out, err = actor.communicate(timeout=5)
        proof['reaped'].append(actor.pid)
        proof['exit_code'] = actor.returncode
        (root / 'recovery-self-attempt.json').write_text(json.dumps(proof))
        assert actor.returncode == 0 and err == b''
    assert osplat.process_tree.command_of(actor.pid) == ''
    proof['gone_api_empty'] = True
    (root / 'recovery-self-proof.json').write_text(json.dumps(proof))
