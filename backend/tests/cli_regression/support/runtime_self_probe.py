"""Inert native ready only, using the actual Uvicorn-selected factory; no app."""
import asyncio
import json
import os
from pathlib import Path


def run(root: Path, selected: str):
    from psutil import _psosx
    import uvicorn
    from .difference_entry import validate_kernel_setup
    from .fixture_exec import stable_file
    from .process_env_guard import installed
    document = validate_kernel_setup(root)
    guard = installed()
    factory = uvicorn.Config(app=None, loop=selected).get_loop_factory()
    generation = 'a' * 32
    command = [document['files']['runtime']['path'], '--protocol', '1',
               '--runtime-generation', generation, '--owner-pid', str(os.getpid())]
    proof = {'selected': selected, 'app_started': False, 'lifespan_started': False,
             'captured': [], 'reaped': []}

    async def exercise():
        actor = await asyncio.create_subprocess_exec(*command, cwd=root,
            env={'HOME': str(root / 'home'), 'TMPDIR': str(root / 'tmp'), 'PATH': '/usr/bin:/bin',
                 'SYNTHETIC_ENV_TRAILER_NEVER_SERIALIZE': 'R12_ONLY_CREATED_SELF'},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        metadata = _psosx.Process(actor.pid)
        born, parent, exe = metadata.create_time(), metadata.ppid(), metadata.exe()
        row = {'pid': actor.pid, 'created': born, 'parent': parent,
               'exe': exe, 'image': stable_file(Path(exe))[1]}
        proof['captured'].append(row)
        assert parent == os.getpid() and row['image'] == document['files']['runtime']
        try:
            ready = json.loads(await asyncio.wait_for(actor.stdout.readline(), 5))
            proof.update(actual_loop=type(asyncio.get_running_loop()).__module__ + '.' + type(asyncio.get_running_loop()).__name__,
                constructor_entries=guard.creation_native_entries, ready_has_self='self_process' in ready,
                ready_generation=ready.get('runtime_generation'))
            (root / 'runtime-loop-attempt.json').write_text(json.dumps(proof))
            assert 'self_process' in ready, 'missing real native SELF evidence'
            from .process_argv_observation import observe_runtime_ready, consume_runtime_ready
            record = (json.dumps(ready) + '\n').encode()
            receipts = root / 'runtime-receipts.jsonl'
            receipts.write_bytes(record[:-1])
            assert consume_runtime_ready(root, row) is None  # Incomplete, not dropped.
            with receipts.open('ab') as output:
                output.write(b'\n')
            observed = consume_runtime_ready(root, row)
            assert consume_runtime_ready(root, row) == observed
            from .darwin_ownership_probe import _runtime_receipts
            fragment = b'{"event":"SYNTHETIC-reader-control"}'
            with receipts.open('ab') as output:
                output.write(fragment[:11])
            records, native_self = _runtime_receipts(root, row, generation)
            assert records == [ready] and native_self == observed
            with receipts.open('ab') as output:
                output.write(fragment[11:])
            assert _runtime_receipts(root, row, generation) == (records, native_self)
            with receipts.open('ab') as output:
                output.write(b'\n')
            assert _runtime_receipts(root, row, generation) == ([ready, json.loads(fragment)], observed)
            proof['ownership_startup_partial_record_preserved'] = True
            from copy import deepcopy
            from .fixture_exec import stable_bytes
            from .process_argv_observation import write_observation
            reader_negatives = []
            for label in ('missing', 'partial-ready', 'missing-self', 'error-self', 'wrong-pid',
                          'wrong-parent', 'stale-birth', 'wrong-image', 'wrong-generation',
                          'wrong-source', 'duplicate', 'malformed'):
                control_root = root / f'reader-control-{label}'
                control_root.mkdir()
                (control_root / 'guard-identities.json').write_bytes(stable_bytes(root / 'guard-identities.json'))
                candidate = deepcopy(ready)
                if label == 'missing-self':
                    candidate.pop('self_process')
                elif label == 'error-self':
                    candidate['self_process_error'] = {'operation': 'SYNTHETIC-control', 'errno': 1}
                elif label == 'wrong-pid':
                    candidate['pid'] += 1
                elif label == 'wrong-parent':
                    candidate['self_process']['parent'] += 1
                elif label == 'stale-birth':
                    candidate['self_process']['birth_microseconds'] = (candidate['self_process']['birth_microseconds'] + 1) % 1_000_000
                elif label == 'wrong-image':
                    candidate['self_process']['executable'] = str(control_root / 'wrong-SYNTHETIC-image')
                elif label == 'wrong-source':
                    wrong_source = deepcopy(observed)
                    wrong_source['source']['native-darwin']['sha256'] = '0' * 64
                    write_observation(control_root, f'runtime-self-process-{actor.pid}.json', wrong_source)
                controlled_record = (json.dumps(candidate) + '\n').encode()
                if label == 'missing':
                    controlled_record = fragment + b'\n'
                elif label == 'partial-ready':
                    controlled_record = controlled_record[:-1]
                elif label == 'duplicate':
                    controlled_record += controlled_record
                elif label == 'malformed':
                    controlled_record += b'{\n'
                (control_root / 'runtime-receipts.jsonl').write_bytes(controlled_record)
                try:
                    _runtime_receipts(control_root, row, 'b' * 32 if label == 'wrong-generation' else generation)
                except json.JSONDecodeError:
                    assert label == 'malformed'
                    reader_negatives.append(label)
                except PermissionError:
                    assert label != 'malformed'
                    reader_negatives.append(label)
                else:
                    raise AssertionError('invalid ownership/startup receipt accepted')
            proof['ownership_startup_reader_negatives'] = reader_negatives
            malformed = root / 'negative-malformed-SYNTHETIC'
            malformed.mkdir()
            (malformed / 'runtime-receipts.jsonl').write_bytes(b'{\n')
            try:
                consume_runtime_ready(malformed, row)
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError('malformed complete receipt filtered')
            proof['private_receipt_framing_verified'] = True
            assert observed['argv'] == command
            assert 'SYNTHETIC_ENV_TRAILER_NEVER_SERIALIZE' not in json.dumps(ready)
            proof.update(passed=True, real_self_equal=True, phase=observed['phase'])
            negatives = []
            from copy import deepcopy
            for label in ('missing', 'pid', 'parent', 'birth', 'image', 'generation', 'error'):
                candidate = deepcopy(ready)
                if label == 'missing':
                    candidate.pop('self_process')
                elif label == 'error':
                    candidate['self_process_error'] = {'operation': 'synthetic-failure', 'errno': 1}
                elif label == 'generation':
                    candidate['runtime_generation'] = 'b' * 32
                elif label == 'birth':
                    candidate['self_process']['birth_seconds'] += 1
                elif label == 'image':
                    candidate['self_process']['executable'] = str(root / 'wrong-SYNTHETIC-image')
                else:
                    candidate['self_process'][label] += 1
                try:
                    observe_runtime_ready(root, candidate, row)
                except PermissionError:
                    negatives.append(label)
                else:
                    raise AssertionError('invalid native SELF was accepted')
            # New controlled observation payload, not edits to an accepted receipt.
            from .process_argv_observation import correlate_observation, write_observation
            from .fixture_exec import stable_bytes
            negative_root = root / 'negative-source-SYNTHETIC'
            negative_root.mkdir()
            (negative_root / 'guard-identities.json').write_bytes(stable_bytes(root / 'guard-identities.json'))
            wrong_source = deepcopy(observed)
            wrong_source['source']['native-darwin']['sha256'] = '0' * 64
            write_observation(negative_root, f'runtime-self-process-{actor.pid}.json', wrong_source)
            try:
                correlate_observation(negative_root, row)
            except PermissionError:
                negatives.append('source')
            else:
                raise AssertionError('wrong native source accepted')
            proof['negative_labels'] = negatives
        finally:
            actor.stdin.close()
            try:
                await asyncio.wait_for(actor.wait(), 5)
            except BaseException:
                if actor.returncode is None:
                    assert _psosx.Process(actor.pid).create_time() == born
                    actor.kill()
                await asyncio.wait_for(actor.wait(), 5)
                proof['rescued'] = True
                raise
            proof['reaped'].append(actor.pid)
            proof['exit_code'] = actor.returncode
            assert actor.returncode == 0
            assert await actor.stderr.read() == b''
            (root / 'runtime-loop-attempt.json').write_text(json.dumps(proof))

    asyncio.run(exercise(), loop_factory=factory)
    assert proof['reaped'] == [item['pid'] for item in proof['captured']]
    (root / 'runtime-loop-proof.json').write_text(json.dumps(proof))


def protocol_controls(root):
    """Original startup/fatal/protocol/shutdown behavior; no PTY or application."""
    import subprocess
    from psutil import _psosx
    from .difference_entry import validate_kernel_setup
    document = validate_kernel_setup(root)
    generation = 'c' * 32
    base = [document['files']['runtime']['path'], '--protocol', '1',
            '--runtime-generation', generation, '--owner-pid', str(os.getpid())]
    receipts = []
    for label in ('eof', 'shutdown', 'stale', 'fatal', 'protocol'):
        flags = ['--fail-startup'] if label == 'fatal' else ['--ready-protocol', '2'] if label == 'protocol' else []
        actor = subprocess.Popen(base + flags, cwd=root,
            env={'HOME': str(root / 'home'), 'PATH': '/usr/bin:/bin', 'TMPDIR': str(root / 'tmp')},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        meta = _psosx.Process(actor.pid)
        born, parent = meta.create_time(), meta.ppid()
        assert parent == os.getpid()
        receipt = {'label': label, 'pid': actor.pid, 'born': born, 'parent': parent}
        try:
            requests = []
            if label == 'stale':
                requests.append({'id': 'r1', 'op': 'shutdown', 'runtime_generation': 'd' * 32})
            if label in ('shutdown', 'stale'):
                requests.append({'id': 'r2' if label == 'stale' else 'r1',
                    'op': 'shutdown', 'runtime_generation': generation})
            output, stderr = actor.communicate(b''.join((json.dumps(row) + '\n').encode() for row in requests), timeout=5)
            assert output.endswith(b'\n')
            records = [json.loads(line) for line in output.splitlines()]
            assert all(row['runtime_generation'] == generation for row in records)
            if label == 'fatal':
                assert actor.returncode == 1 and records[0]['event'] == 'fatal'
                assert records[0]['error']['code'] == 'STARTUP_FAILED'
                assert len(records) == 1 and stderr == b'cli runtime: STARTUP_FAILED\n'
            else:
                ready = records[0]
                assert actor.returncode == 0 and stderr == b''
                assert ready['event'] == 'ready' and ready['pid'] == actor.pid and ready['owner_pid'] == parent
                assert ready['protocol'] == (2 if label == 'protocol' else 1)
                assert ready['self_process']['argv'] == base + flags
                assert ready['self_process']['birth_seconds'] + ready['self_process']['birth_microseconds'] / 1_000_000 == born
                if label in ('shutdown', 'stale'):
                    assert records[-1]['ok'] and records[-1]['result'] == {'cleanup_complete': True, 'survivors': []}
                if label == 'stale':
                    assert records[1]['error']['code'] == 'STALE_RUNTIME'
                assert len(records) == {'eof': 1, 'protocol': 1, 'shutdown': 2, 'stale': 3}[label]
            receipt.update(reaped=True, exit_code=actor.returncode, passed=True)
        finally:
            if actor.returncode is None:
                assert _psosx.Process(actor.pid).create_time() == born
                actor.kill()
                actor.wait(timeout=5)
                receipt.update(rescued=True, reaped=True)
            receipts.append(receipt)
            (root / 'native-wire-proof.json').write_text(json.dumps(receipts))
    assert all(row.get('passed') and row['reaped'] and not row.get('rescued') for row in receipts)
