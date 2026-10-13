"""Actual guarded Darwin ownership/recovery; external CLI is synthetic only."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import psutil

from .fixture_exec import stable_bytes, stable_file


def wait_file(path: Path) -> dict:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('owned synthetic process readiness')
        time.sleep(0.02)
    return json.loads(stable_bytes(path))


def observation(pid: int) -> dict:
    process = psutil.Process(pid)
    return {'pid': pid, 'created': process.create_time(), 'parent': process.ppid(),
            'pgid': os.getpgid(pid), 'exe': process.exe()}


def exact_cleanup(rows: list[dict]) -> None:
    for row in reversed(rows):
        try:
            process = psutil.Process(row['pid'])
            if process.create_time() == row['created'] and process.status() != psutil.STATUS_ZOMBIE:
                process.kill()
                process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass


def _runtime_receipts(root: Path, metadata: dict, generation: str) -> tuple[list[dict], dict]:
    """Ownership/startup success requires the actual runtime's correlated SELF ready."""
    from .process_argv_observation import consume_runtime_ready, read_runtime_receipts
    receipts = read_runtime_receipts(root)
    ready = consume_runtime_ready(root, metadata)
    if ready is None or ready['runtime_generation'] != generation:
        raise PermissionError('missing or wrong correlated ownership/startup ready')
    return receipts, ready


async def native(root: Path, orphan: bool = False) -> dict:
    from agent_team_backend import osplat
    from agent_team_backend.cli_runtime import RustTerminalService
    from agent_team_backend.db import Database
    events, captured = [], []

    async def emit(event):
        events.append(event)

    service = RustTerminalService(emit)
    try:
        command = 'codex --orphan-ownership-probe' if orphan else 'codex --ownership-probe'
        session = await service.create_async(command=['/bin/bash', '-lc', command],
            cwd=str(root), pane_id='SYNTHETIC_OWNERSHIP', agent_key=None)
        runtime = observation(service._runtime.pid)
        runtime['image'] = stable_file(Path(runtime['exe']))[1]
        _, runtime_self = _runtime_receipts(root, runtime, service._runtime_generation)
        captured = [observation(session.proc.pid)]
        (root / 'ownership-os.json').write_text(json.dumps(captured))
        await service.activate_async(session.id)
        await asyncio.to_thread(wait_file, root / 'ownership-root.json')
        await service.write_async(session.id, 'GO\n')
        child = await asyncio.to_thread(wait_file, root / 'escaping-child.json')
        captured.append(observation(child['pid']))
        (root / 'ownership-os.json').write_text(json.dumps(captured))
        table = osplat.process_tree.snapshot()
        assert table[session.proc.pid].start == session.root['start_value']
        assert session.root['start_kind'] == 'posix-lstart'
        assert child['pid'] in osplat.process_tree.descendants(session.proc.pid)
        assert captured[1]['pgid'] == captured[1]['pid'] != captured[0]['pgid']
        # Actual supplied native identity round-trips through durable registry.
        db = Database(root / 'data/navide.db')
        try:
            entry = db.kv_get('pty_registry')[str(session.proc.pid)]
            assert entry['lstart'] == session.root['start_value']
        finally:
            db.close()
        if orphan:
            deadline = time.monotonic() + 12
            while True:
                if session.descendants.get(child['pid']) == table[child['pid']].start:
                    db = Database(root / 'data/navide.db')
                    try:
                        persisted = db.kv_get('pty_registry')[str(session.proc.pid)]
                        if persisted.get('descendants', {}).get(str(child['pid'])) == table[child['pid']].start:
                            break
                    finally:
                        db.close()
                assert time.monotonic() < deadline, 'native retained seed was not observed'
                await asyncio.sleep(0.02)
            await service.write_async(session.id, 'RETIRE_ROOT\n')
            newborn = await asyncio.to_thread(wait_file, root / 'new-orphan-child.json')
            captured.append(observation(newborn['pid']))
            (root / 'ownership-os.json').write_text(json.dumps(captured))
            root_present = psutil.pid_exists(session.proc.pid)
            root_state = {'pid': captured[0]['pid'], 'captured_birth': captured[0]['created'],
                          'present_at_newborn_receipt': root_present}
            try:
                actual_root = psutil.Process(session.proc.pid)
                root_state.update({'observed_birth': actual_root.create_time(), 'status': actual_root.status()})
            except psutil.NoSuchProcess:
                root_state['status'] = 'gone-during-owned-sample'
            escaped = psutil.Process(child['pid'])
            ordering = {'root': root_state, 'escaped': {'pid': child['pid'],
                'birth': escaped.create_time(), 'parent': escaped.ppid()},
                'newborn': captured[-1], 'retained_births': dict(session.descendants)}
            (root / 'orphan-ordering-observation.json').write_text(json.dumps(ordering))
            assert not root_present, 'new descendant did not follow root death'
            assert ordering['escaped']['parent'] == 1
            deadline = time.monotonic() + 10
            while True:
                receipts, runtime_self = _runtime_receipts(root, runtime, service._runtime_generation)
                if any(row.get('event') == 'cleaned' for row in receipts):
                    break
                assert time.monotonic() < deadline, 'retained orphan cleanup timed out'
                await asyncio.sleep(0.02)
        else:
            await service.kill(session.id, force=True)
        receipts, runtime_self = _runtime_receipts(root, runtime, service._runtime_generation)
        cleaned = [row for row in receipts if row.get('event') == 'cleaned']
        assert cleaned and cleaned[-1]['survivors'] == []
        return {'native_start': session.root, 'runtime_self': runtime_self,
                'os': captured, 'registry_roundtrip': True,
                'escaping_descendant': True, 'retained_orphan_new_fork': orphan, 'cleaned': cleaned[-1]}
    finally:
        await service.kill_all()
        exact_cleanup(captured)


def recovery(root: Path) -> dict:
    from agent_team_backend import osplat, pty_registry
    tree, rows = osplat.process_tree, []
    child = subprocess.Popen(['/bin/bash', '-lc', 'codex --ownership-probe'], cwd=root,
                             env=dict(os.environ), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rows = [observation(child.pid)]
        (root / 'ownership-os.json').write_text(json.dumps(rows))
        wait_file(root / 'ownership-root.json')
        from .process_argv_observation import validate_recovery_self
        from .fixture_exec import stable_file
        current_root = observation(child.pid)
        if current_root['created'] != rows[0]['created'] or current_root['parent'] != rows[0]['parent']:
            raise PermissionError('changed captured recovery root')
        current_root['image'] = stable_file(Path(current_root['exe']))[1]
        validate_recovery_self(root, current_root)
        child.stdin.write(b'GO\n')
        child.stdin.flush()
        escaped = wait_file(root / 'escaping-child.json')
        rows.append(observation(escaped['pid']))
        (root / 'ownership-os.json').write_text(json.dumps(rows))
        # No foreign live argv extraction: genuine producer SELF proof above.
        assert tree.command_of(child.pid) is None
        assert pty_registry._backend_alive(child.pid) is True
        # Leaf permission-fault control only; never used in OS/recovery inspection.
        from unittest.mock import patch
        with patch.object(psutil, 'Process', side_effect=psutil.AccessDenied(child.pid)):
            assert tree.command_of(child.pid) is None
            assert pty_registry._backend_alive(os.getpid()) is True
        table = tree.snapshot()
        assert child.pid in table and escaped['pid'] in table, 'guarded recovery identity unavailable'
        assert tree.start_time(child.pid) == table[child.pid].start
        assert escaped['pid'] in tree.descendants(child.pid)
        assert table[escaped['pid']].gid == escaped['pid']
        start, descendant_start = table[child.pid].start, table[escaped['pid']].start

        def persist(mismatch):
            owner = os.fork()
            if owner == 0:
                try:
                    pty_registry.register_owned(child.pid, ['codex'], start + (' INVALID' if mismatch else ''),
                        'SYNTHETIC_RUNTIME', 'SYNTHETIC_SESSION', 'SYNTHETIC_GENERATION')
                    pty_registry.update_descendants({child.pid: {escaped['pid']: descendant_start}})
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            assert os.waitpid(owner, 0)[1] == 0
            return owner

        mismatch_owner = persist(True)
        assert pty_registry.scan_orphans() == []
        assert all(psutil.Process(row['pid']).is_running() for row in rows)
        owner = persist(False)
        assert set(pty_registry.scan_orphans()) == {child.pid, escaped['pid']}
        reaped = pty_registry.reap_stale(grace=0.2)
        assert set(reaped) == {child.pid, escaped['pid']}
        child.wait(timeout=5)
        assert tree.command_of(child.pid) == ''
        assert tree.start_time(child.pid) == ''
        assert child.pid not in tree.snapshot()
        return {'os': rows, 'births': [start, descendant_start], 'stale_owner': owner,
                'mismatch_owner': mismatch_owner, 'mismatch_not_signalled': True,
                'actual_reaped': reaped, 'exited_pid_absent': True,
                'actual_live_gone_command': True, 'leaf_unknown_command_fault': True}
    finally:
        exact_cleanup(rows)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def monitor_disappearance(root: Path) -> dict:
    from agent_team_backend import osplat
    from .native_difference_driver import observe_owned_process
    child = subprocess.Popen(['codex', '--guard-probe'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rows = []
    original = os.getpgid
    actual_errors = []
    try:
        assert child.stdout.readline().startswith('DIFFERENCE_GUARD_READY ')
        process = psutil.Process(child.pid)
        born = process.create_time()
        images = {}
        live = observe_owned_process(process, born, images, osplat.process_tree)
        rows.append(live)
        assert live['created'] == born and live['pgid'] == original(child.pid)

        def exit_at_group_query(pid):
            assert pid == child.pid
            child.stdin.write('GO\n')
            child.stdin.flush()
            assert child.wait(timeout=5) == 0
            # Real owned death at the race boundary; real syscall, not a
            # replacement process inspector or fabricated return/error.
            try:
                return original(pid)
            except ProcessLookupError as error:
                actual_errors.append(error.errno)
                raise

        os.getpgid = exit_at_group_query
        gone = observe_owned_process(process, born, images, osplat.process_tree)
        assert gone == {'pid': child.pid, 'created': born, 'state': 'disappeared'}
        assert actual_errors == [3]
        return {'real_disappearance_errno': 3, 'recorded_birth_retained': gone == {
            'pid': live['pid'], 'created': live['created'], 'state': 'disappeared'},
            'actual_reaped': [child.pid], 'os': rows, 'permission_swallowing': False}
    finally:
        os.getpgid = original
        exact_cleanup(rows)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


async def shell_startup(root: Path, expected_hash: str) -> dict:
    """Same guarded native PTY/Bash startup, empty metadata, no backend."""
    import ctypes
    import errno
    import hashlib
    from .fixture_exec import stable_file
    from agent_team_backend.cli_runtime import RustTerminalService
    payload = stable_bytes(root / 'startup-launch.json')
    assert hashlib.sha256(payload).hexdigest() == expected_hash
    launch = json.loads(payload)
    assert launch['command'][:2] == ['/bin/bash', '-lc']
    assert launch['cwd'] == str(root / 'workspace differences café 世界')
    assert Path(launch['env']['CODEX_HOME']).is_relative_to(root / 'home')
    assert not list((root / 'home').rglob('*.jsonl'))
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.open.restype = ctypes.c_int
    ctypes.set_errno(0)
    fd = libc.open(b'/etc/profile', os.O_RDONLY | os.O_NOFOLLOW)
    profile_errno = ctypes.get_errno()
    if fd >= 0:
        os.close(fd)  # R9 admitted exact public profile; do not read its payload here.
    else:
        assert profile_errno in (errno.EPERM, errno.EACCES)
    raw, captured = bytearray(), []
    session_id = None

    async def emit(frame):
        if isinstance(frame, bytes):
            sid_end = 6 + frame[5]
            assert frame[6:sid_end].decode() == session_id
            pane_end = sid_end + 1 + frame[sid_end]
            raw.extend(frame[pane_end:])
            assert len(raw) < 16384

    service = RustTerminalService(emit)
    try:
        session = await service.create_async(command=launch['command'], cwd=launch['cwd'],
            env=launch['env'], pane_id='difference-codex', agent_key='codex')
        session_id = session.id
        runtime = observation(service._runtime.pid)
        runtime['image'] = stable_file(Path(runtime['exe']))[1]
        _, runtime_self = _runtime_receipts(root, runtime, service._runtime_generation)
        captured.append(observation(session.proc.pid))
        (root / 'ownership-os.json').write_text(json.dumps(captured))
        await service.activate_async(session.id)
        claim = await asyncio.to_thread(wait_file, root / f'child-{session.proc.pid}.json')
        record = stable_bytes(root / f'child-{session.proc.pid}.json')
        ready = b'DIFFERENCE_READY ' + record + b'\r\n'
        deadline = time.monotonic() + 10
        while not bytes(raw).endswith(ready):
            assert time.monotonic() < deadline, 'complete owned startup READY absent'
            await asyncio.sleep(.01)
        before = bytes(raw)
        prefix = before[:-len(ready)]
        assert claim['argv'] == launch['fixture_args']
        assert claim['cwd'] == launch['cwd'] and claim['isatty'] and claim['ctty']
        assert claim['pid'] == captured[0]['pid'] and claim['parent_claim'] == captured[0]['parent']
        ready_os = observation(claim['pid'])
        assert ready_os['created'] == captured[0]['created']
        assert ready_os['parent'] == claim['parent_claim']
        guard = json.loads(stable_bytes(root / 'guard-identities.json'))
        exec_manifest = json.loads(stable_bytes(root / 'exec-identities.json'))
        assert ready_os['exe'] == exec_manifest['interpreter']['path']
        assert stable_file(Path(ready_os['exe']))[1]['sha256'] == guard['files']['interpreter']['sha256']
        assert claim['script_sha256'] == guard['files']['native_difference_peer']['sha256']
        resolution = json.loads(stable_bytes(root / f'shell-resolution-{claim["pid"]}.json'))
        assert resolution == {'shell_pid': ready_os['pid'], 'parent': ready_os['parent'],
            'path': guard['shell_path'], 'resolved': exec_manifest['shim']['path'],
            'PS1_empty': True, 'TERM_PROGRAM_empty': True}
        await service.kill(session.id, force=True)
        await service.drain_output(session.id)
        assert bytes(raw) == before, 'post-startup extra bytes'
        assert not list((root / 'home').rglob('*.jsonl'))
        (root / 'startup-output.bin').write_bytes(before)
        (root / 'startup-prefix.bin').write_bytes(prefix)
        receipts, runtime_self = _runtime_receipts(root, runtime, service._runtime_generation)
        cleaned = next(row for row in receipts if row.get('event') == 'cleaned')
        assert cleaned['cleanup_complete'] and cleaned['root_reaped'] and not cleaned['survivors'] and not cleaned['unverifiable']
        return {'backend_launched': False, 'metadata_produced': False,
                'prefix_length': len(prefix), 'prefix_sha256': hashlib.sha256(prefix).hexdigest(),
                'system_profile_open': {'operation': 'open-readonly-nofollow', 'return': fd,
                                        'errno': profile_errno, 'payload_read': False},
                'runtime': next(row for row in receipts if row.get('event') == 'ready'
                                and row.get('pid') == runtime['pid']),
                'runtime_self': runtime_self,
                'os': captured, 'ready_os': ready_os, 'ctty': True, 'command_input_hash': expected_hash,
                'shell_resolution': resolution, 'shell_birth': ready_os['created'],
                'shell_profile_sha256': exec_manifest['profile']['sha256'],
                'os_startup': guard['os_startup'], 'os_selector': guard['os_selector'],
                'root_reaped': True, 'cleaned': cleaned}
    finally:
        await service.kill_all()
        exact_cleanup(captured)


def run(root: Path, mode: str) -> None:
    try:
        if mode.startswith('native-shell-startup:'):
            proof = asyncio.run(shell_startup(root, mode.split(':', 1)[1]))
        elif mode == 'watch-root':
            from .native_difference_driver import prepare_codex_watch_root
            directory = root / 'home/.codex-panes'
            assert not directory.exists()
            prepare_codex_watch_root(root)
            assert directory.is_dir() and not directory.is_symlink()
            assert list(directory.iterdir()) == []
            proof = {'watch_root_existed_before_backend': True, 'empty_initial_watch_root': True,
                     'backend_launched': False}
        elif mode == 'monitor-disappearance':
            proof = monitor_disappearance(root)
        else:
            proof = asyncio.run(native(root, orphan=mode == 'native-orphan')) if mode.startswith('native') else recovery(root)
        proof['passed'] = True
    except BaseException as error:
        (root / 'ownership-proof.json').write_text(json.dumps({'passed': False,
            'error_type': type(error).__name__, 'error': str(error)}))
        raise
    (root / 'ownership-proof.json').write_text(json.dumps(proof))
