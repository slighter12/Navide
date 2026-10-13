"""External fixture CLI. No vendor libraries, credentials or provider calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from fixture_exec import stable_bytes, stable_file


def opencode_fixture_rows(workspace, marker, inputs, phase):
    """Finite synthetic database contents, independently compared after shutdown."""
    sid = 'ses_native_difference'
    sessions = [('ses_history', None, str(workspace), '99.0.0-regression')]
    messages = [('msg_history', 'ses_history', {'role': 'user'})]
    parts = [('part_history', 'ses_history', 'msg_history', {'type': 'text', 'text': 'SYNTHETIC T04 historical seed'}, 1)]
    if inputs:
        sessions.append((sid, None, str(workspace), '99.0.0-regression'))
    for number, value in enumerate(inputs, 1):
        settled = number < len(inputs) or phase == 'settled'
        paused = number < len(inputs) or phase in ('paused', 'settled')
        user, assistant = f'msg_{number}_user', f'msg_{number}_assistant'
        messages.extend([(user, sid, {'role': 'user'}), (assistant, sid, {
            'role': 'assistant', 'modelID': 'synthetic/owned',
            'time': {'completed': value['completed']} if settled else {},
            'tokens': value['tokens'], 'synthetic_identity': marker})])
        parts.extend([(f'part_{number}_user', sid, user, {'type': 'text', 'text': value['prompt']}, number * 100),
                      (f'part_{number}_thinking', sid, assistant, {'type': 'reasoning'}, number * 100 + 1)])
        if paused:
            child = f'ses_child_{number}'
            sessions.append((child, sid, str(workspace), '99.0.0-regression'))
            messages.append((f'msg_child_{number}', child, {'role': 'assistant', 'time': {'completed': value['completed']}, 'tokens': {}}))
            parts.extend([(f'part_{number}_pause', sid, assistant, {'type': 'step-finish', 'reason': 'tool-calls'}, number * 100 + 2),
                          (f'part_{number}_child', child, f'msg_child_{number}', {'type': 'step-finish', 'reason': 'stop'}, number * 100 + 3)])
        if settled:
            parts.extend([(f'part_{number}_reply', sid, assistant, {'type': 'text', 'text': value['reply']}, number * 100 + 4),
                          (f'part_{number}_stop', sid, assistant, {'type': 'step-finish', 'reason': 'stop'}, number * 100 + 5)])
    return {'session': [list(row) for row in sessions], 'message': [list(row) for row in messages], 'part': [list(row) for row in parts]}


def publish_opencode_phase(root, stage, phase, connection):
    """Publish every principal phase on this writer; the filesystem watcher never reports WAL-only appends."""
    if (stage, phase) not in {(1, 'pending'), (1, 'paused'), (1, 'settled'),
                              (2, 'pending'), (2, 'paused'), (2, 'settled')}:
        return None
    path = root / 'share/opencode/opencode.db'
    def stamp():
        value = path.stat()
        return [value.st_dev, value.st_ino, value.st_size, value.st_mtime]
    before = stamp()
    result = connection.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
    after = stamp()
    if (not isinstance(result, tuple) or len(result) != 3
            or any(type(value) is not int for value in result)
            or result[0] != 0 or result[1] <= 0 or result[2] != result[1]
            or before == after):
        raise PermissionError('synthetic checkpoint publication unproved')
    return {'result': list(result), 'main_before': before, 'main_after': after}


def write_opencode_phase(root, record, stage, phase, connection):
    inputs = []
    identities = []
    for number in range(1, stage + 1):
        data, identity = stable_file(root / f'stage-{number}.bin')
        expected = json.loads(stable_bytes(root / 'guard-identities.json'))['files'][f'stage-{number}']
        if identity != expected:
            raise PermissionError('changed synthetic SQLite input')
        inputs.append(json.loads(data)); identities.append(identity)
    rows = opencode_fixture_rows(Path(record['cwd']), record['fixture_marker'], inputs, phase)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.executescript('CREATE TABLE IF NOT EXISTS session(id TEXT PRIMARY KEY,parent_id TEXT,directory TEXT,version TEXT);'
        'CREATE TABLE IF NOT EXISTS message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT);'
        'CREATE TABLE IF NOT EXISTS part(id TEXT PRIMARY KEY,session_id TEXT,message_id TEXT,data TEXT,time_created INTEGER);')
    # UPDATE preserves the immutable rowid/id/session anchor of pending rows.
    for row in rows['session']:
        connection.execute('INSERT OR IGNORE INTO session VALUES(?,?,?,?)', row)
    for row in rows['message']:
        encoded = [row[0], row[1], json.dumps(row[2])]
        connection.execute('INSERT OR IGNORE INTO message VALUES(?,?,?)', encoded)
        connection.execute('UPDATE message SET data=? WHERE id=?', (encoded[2], encoded[0]))
    for row in rows['part']:
        connection.execute('INSERT OR IGNORE INTO part VALUES(?,?,?,?,?)', [*row[:3], json.dumps(row[3]), row[4]])
    connection.commit()
    publication = publish_opencode_phase(root, stage, phase, connection)
    with (root / 'metadata-origins.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind': 'synthetic-sqlite-logical-phase-producer', 'stage': stage, 'phase': phase,
            'creator_pid_claim': os.getpid(), 'script_sha256': record['script_sha256'],
            'synthetic_identity': record['fixture_marker'], 'inputs': identities, 'logical_rows': rows,
            'publication': publication}) + '\n')


def main():
    root, vendor = Path(sys.argv[1]), sys.argv[2]
    args = sys.argv[3:]
    if '--version' in args:
        print(f'{vendor} 99.0.0-regression')
        return
    # SELF source-bound kernel observation; no foreign argv permission.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    sys.path.append(str(Path(__file__).resolve().parents[3] / '.venv/lib/python3.12/site-packages'))
    from tests.cli_regression.support.difference_entry import validate_kernel_setup
    from tests.cli_regression.support.process_env_guard import install
    from tests.cli_regression.support.process_argv_observation import observe_self
    install(root, validate_kernel_setup(root))
    self_row = observe_self(root, 'peer')
    import termios
    record = {'echo_enabled': bool(termios.tcgetattr(0)[3] & termios.ECHO) if os.isatty(0) else False,
              'identity_kind': 'fixture-self-report-not-OS-proof',
              'fixture_marker': stable_bytes(root / 'fixture-marker').decode(),
              'script_sha256': hashlib.sha256(stable_bytes(Path(__file__).absolute())).hexdigest(),
              'interpreter_claim': sys.executable, 'parent_claim': os.getppid(),
              'argv': args, 'cwd': os.getcwd(), 'pid': os.getpid(), 'path': os.environ['PATH'],
              'home': os.environ['HOME'], 'codex_home': os.environ.get('CODEX_HOME'),
              'stdout_producer': 'synthetic-fixture-script',
              'isatty': os.isatty(0), 'ctty': os.isatty(0) and os.tcgetpgrp(0) == os.getpgrp()}
    orphan_probe = '--orphan-ownership-probe' in args
    if '--ownership-probe' in args or orphan_probe or '--ownership-self-probe' in args:
        # The positive API is genuine SELF before any SIGCHLD modification.
        import psutil
        from agent_team_backend import osplat
        from tests.cli_regression.support.process_argv_observation import write_observation
        actual_argv = psutil.Process(os.getpid()).cmdline()
        actual_command = osplat.process_tree.command_of(os.getpid())
        if actual_command != ' '.join(actual_argv):
            raise PermissionError('SELF recovery command API differs from OS argv')
        write_observation(root, 'ownership-command-self.json',
            {**self_row, 'argv': actual_argv, 'command_of': actual_command})
        if '--ownership-self-probe' in args:
            print('SYNTHETIC_OWNERSHIP_SELF_READY', flush=True)
            if sys.stdin.readline().strip() != 'DONE':
                raise PermissionError('missing SELF-only control release')
            return
        import signal
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        (root / 'ownership-root.json').write_text(json.dumps(record))
        print('SYNTHETIC_OWNERSHIP_ROOT_READY', flush=True)
        if sys.stdin.readline().strip() != 'GO':
            raise PermissionError('missing owned descendant probe release')
        child = os.fork()
        if child == 0:
            os.setsid()
            if orphan_probe:
                def fork_after_root_exit(_signal, _frame):
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    descendant = os.fork()
                    if descendant == 0:
                        os.setsid()
                        signal.signal(signal.SIGTERM, signal.SIG_DFL)
                        (root / 'new-orphan-child.json').write_text(json.dumps(
                            {'pid': os.getpid(), 'parent': os.getppid()}))
                        while True:
                            signal.pause()
                signal.signal(signal.SIGTERM, fork_after_root_exit)
            (root / 'escaping-child.json').write_text(json.dumps({'pid': os.getpid(), 'parent': os.getppid()}))
            while True:
                signal.pause()
        if orphan_probe:
            if sys.stdin.readline().strip() != 'RETIRE_ROOT':
                raise PermissionError('missing original root retirement control')
            os._exit(0)
        while True:
            signal.pause()
    if '--guard-probe' in args:
        print('DIFFERENCE_GUARD_READY ' + json.dumps(record), flush=True)
        if sys.stdin.readline().strip() != 'GO':
            raise PermissionError('missing owned probe release')
        print('SYNTHETIC_FIXTURE_STDOUT ' + record['fixture_marker'], flush=True)
        return
    (root / f'child-{os.getpid()}.json').write_text(json.dumps(record))
    sqlite_writer = None
    if vendor == 'opencode':
        import sqlite3
        path = root / 'share/opencode/opencode.db'
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_writer = sqlite3.connect(path)
        if not existed:
            write_opencode_phase(root, record, 0, 'history', sqlite_writer)
    print('DIFFERENCE_READY ' + json.dumps(record), flush=True)
    for line in sys.stdin:
        text = line.strip()
        if vendor == 'opencode' and text.startswith(('stage ', 'pause ', 'settle ')):
            action, stage_text = text.split()
            stage = int(stage_text)
            if stage not in (1, 2):
                raise PermissionError('unexpected SQLite fixture stage')
            phase = {'stage': 'pending', 'pause': 'paused', 'settle': 'settled'}[action]
            if stage == 2 and not (len(args) >= 2 and args[:2] == ['-s', 'ses_native_difference']):
                raise PermissionError('resume syntax variant not delivered to fixture')
            if stage == 1 and ('-s' in args or '--session' in args):
                raise PermissionError('unexpected early SQLite session flag')
            write_opencode_phase(root, record, stage, phase, sqlite_writer)
            print(f'DIFFERENCE_SQLITE {stage} {phase}', flush=True)
        elif text.startswith('stage '):
            stage = text.split()[1]
            if vendor == 'codex':
                sid = '33333333-3333-4333-8333-333333333333'
                path = Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/04' / f'rollout-fixture-{sid}.jsonl'
                path.parent.mkdir(parents=True, exist_ok=True)
                input_path = root / f'stage-{stage}.bin'
                data, input_identity = stable_file(input_path)
                expected = json.loads(stable_bytes(root / 'guard-identities.json'))['files'][f'stage-{stage}']
                if input_identity != expected:
                    raise PermissionError('changed synthetic metadata input')
                meta = {'type': 'session_meta', 'payload': {'id': sid, 'cwd': os.getcwd(),
                        'cli_version': '99.0.0-regression', 'synthetic_identity': record['fixture_marker'],
                        'producer_script_sha256': record['script_sha256']}}
                with path.open('ab') as stream:
                    if not path.stat().st_size:
                        stream.write((json.dumps(meta) + '\n').encode())
                    stream.write(data)
                with (root / 'metadata-origins.jsonl').open('a') as stream:
                    stream.write(json.dumps({'kind': 'synthetic-jsonl-session-meta-producer',
                        'creator_pid_claim': os.getpid(), 'script_sha256': record['script_sha256'],
                        'synthetic_identity': record['fixture_marker'], 'input': input_identity,
                        'output': stable_file(path)[1]}) + '\n')
            print('DIFFERENCE_STAGE ' + stage, flush=True)
        else:
            print('DIFFERENCE_INPUT ' + json.dumps(text), flush=True)
    if sqlite_writer is not None:
        sqlite_writer.close()


if __name__ == '__main__':
    main()
