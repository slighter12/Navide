"""Own isolated backends for the production-client native-difference cases."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import psutil

from .backend_process import BackendProcess
from .difference_entry import validate_kernel_setup
from .fixture_exec import stable_bytes, stable_file


def prepare_opencode_watch_root(root: Path) -> None:
    """Empty OWN data root precreated before the backend's LogWatcher starts (the peer creates the database later)."""
    directory = root / 'share/opencode'
    directory.mkdir(exist_ok=True)
    assert directory.is_dir() and not directory.is_symlink()
    assert next(directory.iterdir(), None) is None, 'initial OpenCode data watch root must be empty'


def prepare_codex_watch_root(root: Path) -> None:
    directory = root / 'home/.codex-panes'
    directory.mkdir(exist_ok=True)
    assert directory.is_dir() and not directory.is_symlink()
    assert next(directory.iterdir(), None) is None, 'initial fixture pane watch root must be empty'


def observe_owned_process(child, born: float, images: dict, process_tree) -> dict:
    """One actual captured birth; disappearance is not an unavailable image."""
    try:
        if not child.is_running():
            return {'pid': child.pid, 'created': born, 'state': 'disappeared'}
        exe = child.exe()
        if exe not in images:
            images[exe] = stable_file(Path(exe))[1]
        row = {'pid': child.pid, 'created': born, 'parent': child.ppid(),
               'pgid': os.getpgid(child.pid), 'legacy_start': process_tree.start_time(child.pid),
               'exe': exe, 'image': images[exe]}
        if psutil.Process(child.pid).create_time() != born:
            raise PermissionError('unstable owned process identity')
        return row
    except (psutil.NoSuchProcess, ProcessLookupError):
        return {'pid': child.pid, 'created': born, 'state': 'disappeared'}


def verify_synthetic_sources(root: Path, workspace: Path, sid: str, stage: int) -> list[dict]:
    """No filtering: every Codex metadata file must be the owned fixture output."""
    identities = json.loads(stable_bytes(root / 'exec-identities.json'))
    marker = stable_bytes(root / 'fixture-marker').decode()
    paths = [*list((root / 'home/.codex-panes').rglob('*.jsonl')),
             *list((root / 'home/.codex/sessions').rglob('*.jsonl'))]
    if len(paths) != 1:
        raise PermissionError('unexpected metadata source count')
    expected_body = b''.join(stable_bytes(root / f'stage-{number}.bin') for number in range(1, stage + 1))
    results = []
    for path in paths:
        data, identity = stable_file(path)
        if path.name != f'rollout-fixture-{sid}.jsonl':
            raise PermissionError('unexpected metadata source name')
        header, body = data.split(b'\n', 1)
        expected = {'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(workspace),
                    'cli_version': '99.0.0-regression', 'synthetic_identity': marker,
                    'producer_script_sha256': identities['script']['sha256']}}
        if json.loads(header) != expected or body != expected_body:
            raise PermissionError('unexpected metadata identity/body')
        results.append(identity)
    origins = [json.loads(row) for row in stable_bytes(root / 'metadata-origins.jsonl').splitlines()]
    guard = json.loads(stable_bytes(root / 'guard-identities.json'))
    if len(origins) != stage or any(row['input'] != guard['files'][f'stage-{number}']
            for number, row in enumerate(origins, 1)):
        raise PermissionError('unexpected metadata input identity')
    if origins[-1]['output'] != results[0]:
        raise PermissionError('changed observed metadata output identity')
    if len(origins) != stage or any(row['kind'] != 'synthetic-jsonl-session-meta-producer'
            or row['synthetic_identity'] != marker or row['script_sha256'] != identities['script']['sha256']
            for row in origins):
        raise PermissionError('unexpected metadata creator origin')
    for number, origin in enumerate(origins, 1):
        lineage = json.loads(stable_bytes(root / f'stage-{number}-lineage.json'))
        producer = lineage['producer']
        if (lineage['stage'] != number or lineage['fixture_marker'] != marker
                or lineage['script_sha256'] != identities['script']['sha256']
                or origin['creator_pid_claim'] != producer['pid'] or producer['created'] <= 0
                or producer['exe'] != identities['interpreter']['path']):
            raise PermissionError('metadata creator not bound to observed producer')
    return results


def verify_opencode_sources(root: Path, workspace: Path, stage: int) -> list[dict]:
    """Bind fixed logical SQLite phases to genuine captured fixture producers."""
    import sqlite3
    from .native_difference_peer import opencode_fixture_rows
    identities = json.loads(stable_bytes(root / 'exec-identities.json'))
    marker = stable_bytes(root / 'fixture-marker').decode()
    guard = json.loads(stable_bytes(root / 'guard-identities.json'))
    producers = {}
    for number in range(1, stage + 1):
        lineage = json.loads(stable_bytes(root / f'stage-{number}-lineage.json'))
        producer = lineage['producer']
        if (lineage['stage'] != number or lineage['fixture_marker'] != marker
                or lineage['script_sha256'] != identities['script']['sha256']
                or producer['exe'] != identities['interpreter']['path'] or producer['created'] <= 0
                or producer['argv_phase'] != 'self-producer'
                or producer['image']['sha256'] != identities['interpreter']['sha256']):
            raise PermissionError('SQLite creator lacks captured SELF/OS/image correlation')
        producers[number] = producer['pid']
    inputs = [json.loads(stable_bytes(root / f'stage-{number}.bin')) for number in range(1, stage + 1)]
    expected_phases = [(0, 'history')] + [(number, phase) for number in range(1, stage + 1)
                                                       for phase in ('pending', 'paused', 'settled')]
    origins = [json.loads(row) for row in stable_bytes(root / 'metadata-origins.jsonl').splitlines()]
    if len(origins) != len(expected_phases):
        raise PermissionError('unexpected SQLite phase count')
    for origin, (number, phase) in zip(origins, expected_phases):
        if (origin['kind'] != 'synthetic-sqlite-logical-phase-producer' or origin['stage'] != number
                or origin['phase'] != phase or origin['creator_pid_claim'] != producers[max(1, number)]
                or origin['script_sha256'] != identities['script']['sha256']
                or origin['synthetic_identity'] != marker
                or origin['inputs'] != [guard['files'][f'stage-{n}'] for n in range(1, number + 1)]
                or origin['logical_rows'] != opencode_fixture_rows(workspace, marker, inputs[:number], phase)):
            raise PermissionError('SQLite logical phase origin differs from fixed producer/input')
    path = root / 'share/opencode/opencode.db'
    before = stable_file(path)[1]
    connection = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA busy_timeout=250')
        actual = {'session': [list(row) for row in connection.execute('SELECT id,parent_id,directory,version FROM session ORDER BY rowid')],
                  'message': [[row[0], row[1], json.loads(row[2])] for row in connection.execute('SELECT id,session_id,data FROM message ORDER BY rowid')],
                  'part': [[*row[:3], json.loads(row[3]), row[4]] for row in connection.execute('SELECT id,session_id,message_id,data,time_created FROM part ORDER BY rowid')]}
        if actual != opencode_fixture_rows(workspace, marker, inputs, 'settled'):
            raise PermissionError('SQLite current logical rows differ from frozen phases')
    finally:
        connection.close()
    if stable_file(path)[1] != before:
        raise PermissionError('SQLite file changed during closed-producer logical verification')
    return [before]


async def main(root: Path, vendor: str):
    document = validate_kernel_setup(root)
    workspace = root / 'workspace differences café 世界'
    workspace.mkdir(exist_ok=True)
    sid = '33333333-3333-4333-8333-333333333333' if vendor == 'codex' else 'ses_native_difference'
    for stage in (1, 2):
        if vendor == 'codex' and stage == 1:
            prepare_codex_watch_root(root)
        if vendor == 'opencode' and stage == 1:
            prepare_opencode_watch_root(root)
        restart_checkpoint = None
        if vendor in ('codex', 'opencode') and stage == 2:
            from agent_team_backend.db import Database
            from agent_team_backend.tokens_store import TokensStore
            previous = json.loads(stable_bytes(root / 'stage-1-durable.json'))
            database = Database(root / 'data/navide.db')
            try:
                restart_checkpoint = TokensStore(db=database).get_ingestion_checkpoint(previous['sources'][0]['path'], None)
                assert restart_checkpoint == previous['sources'][0]['usage'], 'durable restart checkpoint changed'
            finally:
                database.close()
        backend = BackendProcess(root, artifact_prefix=f'stage-{stage}-')
        backend.env['NAVIDE_ACR_EVAL_RUNTIME'] = 'rust-shell'
        # The fixed entry already froze the shim/profile/source identities.
        # Never recreate them beneath inherited enforcement.
        (root / 'home/.codex/sessions').mkdir(parents=True, exist_ok=True)
        stopped = asyncio.Event()
        async with backend:
            process = psutil.Process(backend.process.pid)
            controller = psutil.Process()
            owned = {process.pid: process}
            images = {}
            disappeared = set()
            from agent_team_backend import osplat

            async def monitor():
                with (root / f'stage-{stage}-process-identities.jsonl').open('w') as stream:
                    while not stopped.is_set():
                        for child in process.children(recursive=True):
                            child.create_time()
                            owned.setdefault(child.pid, child)
                            backend.track_child(child.pid)
                        rows = []
                        for child in owned.values():
                            born = child.create_time()  # cached at actual capture; retain it after exit
                            row = observe_owned_process(child, born, images, osplat.process_tree)
                            if row.get('state') == 'disappeared':
                                identity = (child.pid, born)
                                if identity not in disappeared:
                                    with (root / f'stage-{stage}-process-disappeared.jsonl').open('a') as ledger:
                                        ledger.write(json.dumps(row) + '\n')
                                    disappeared.add(identity)
                            else:
                                from .process_argv_observation import correlate_observation, consume_runtime_ready
                                observation = (consume_runtime_ready(root, row)
                                    if row['exe'] == document['files']['runtime']['path']
                                    else correlate_observation(root, row))
                                row['argv'] = observation['argv'] if observation else None
                                row['argv_phase'] = observation['phase'] if observation else 'not-observed'
                                rows.append(row)
                        stream.write(json.dumps(rows) + '\n')
                        stream.flush()
                        snapshot = root / f'stage-{stage}-process-current.pending'
                        snapshot.write_text(json.dumps(rows))
                        snapshot.replace(root / f'stage-{stage}-process-current.json')
                        await asyncio.sleep(0.02)

            watcher = asyncio.create_task(monitor())
            pending = root / f'endpoint-{stage}.pending'
            pending.write_text(json.dumps({'url': backend.url, 'shell': '/bin/bash', 'workspace': str(workspace),
                'home': str(root / 'home'), 'sessionId': sid, 'stage': stage,
                'restartCheckpoint': restart_checkpoint,
                'backend': {'pid': process.pid, 'created': process.create_time()},
                'driver': {'pid': controller.pid, 'created': controller.create_time()}}))
            pending.replace(root / f'endpoint-{stage}.json')
            try:
                command = (await asyncio.to_thread(sys.stdin.readline)).strip()
            finally:
                stopped.set()
                await watcher
        from agent_team_backend.db import Database
        from agent_team_backend.tokens_store import TokensStore
        paths = list((root / 'home/.codex-panes').rglob('rollout-*.jsonl')) if vendor == 'codex' else [root / 'share/opencode/opencode.db']
        db = Database(root / 'data/navide.db')
        try:
            store = TokensStore(db=db)
            source_identities = (verify_synthetic_sources(root, workspace, sid, stage) if vendor == 'codex'
                                 else verify_opencode_sources(root, workspace, stage))
            (root / f'stage-{stage}-durable.json').write_text(json.dumps({
                'sources': [{'path': str(path), 'usage': store.get_ingestion_checkpoint(str(path), None),
                             'activity': store.get_ingestion_checkpoint(str(path), '@activity')} for path in paths],
                'totals': store.snapshot(str(workspace))['global']['all_time'],
                'verified_synthetic_sources': source_identities,
            }, indent=2))
        finally:
            db.close()
        (root / f'stage-{stage}-closed').write_text('closed')
        if command != 'restart':
            break


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
