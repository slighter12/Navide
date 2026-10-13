"""Real new-child construction and refused late/unsupported reaping controls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def run(root: Path):
    import psutil
    from .fixture_exec import stable_bytes
    from .process_env_guard import installed
    guard = installed()
    from .isolation import install_external_guards
    actual_constructor = subprocess.Popen
    install_external_guards(root)  # Existing bootstrap/backend repeated install path.
    assert subprocess.Popen is actual_constructor
    command = [sys.executable, '-I', '-S', str(Path(__file__).with_name('process_env_target.py')),
               'SYNTHETIC_R11_CREATION']
    child = subprocess.Popen(command, cwd=root, env={'HOME': str(root / 'home'), 'PATH': '/usr/bin:/bin'},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    born = psutil.Process(child.pid).create_time()
    try:
        assert child.stdout.readline() == 'SYNTHETIC_ENV_TARGET_READY\n'
        path = root / f'new-child-process-{child.pid}.json'
        assert path.exists(), 'missing actual constructor-window OS observation'
        proof = json.loads(stable_bytes(path))
        assert proof['argv'] == command and proof['created'] == born
        assert proof['parent'] == os.getpid() and proof['phase'] == 'new-child-constructor'
        before = guard.creation_native_entries
        for expected in (born, born + 1):
            try:
                guard._snapshot_new_child(child, expected)
            except PermissionError:
                pass
            else:
                raise AssertionError('late invocation admitted argv payload')
        assert guard.creation_native_entries == before
    finally:
        if child.poll() is None:
            assert psutil.Process(child.pid).create_time() == born
            child.terminate()
        child.wait(timeout=5)
    try:
        guard._snapshot_new_child(child, born)
    except PermissionError:
        pass
    else:
        raise AssertionError('already reaped child admitted payload')
    self_birth = guard.birth
    guard.birth = self_birth + 1
    try:
        try:
            subprocess.Popen(command, env={'PATH': '/usr/bin:/bin'})
        except psutil.AccessDenied:
            pass
        else:
            raise AssertionError('wrong SELF birth spawned new child')
    finally:
        guard.birth = self_birth
    assert guard.creation_native_entries == before
    prior = signal.getsignal(signal.SIGCHLD)
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        try:
            subprocess.Popen(command, env={'PATH': '/usr/bin:/bin'})
        except PermissionError:
            pass
        else:
            raise AssertionError('unsupported signal reaper spawned target')
    finally:
        signal.signal(signal.SIGCHLD, prior)
    watcher = asyncio.get_child_watcher()
    unsupported = asyncio.SafeChildWatcher()
    try:
        asyncio.set_child_watcher(unsupported)
        try:
            subprocess.Popen(command, env={'PATH': '/usr/bin:/bin'})
        except PermissionError:
            pass
        else:
            raise AssertionError('unsupported watcher spawned target')
    finally:
        asyncio.set_child_watcher(watcher)
        unsupported.close()
    assert guard.creation_native_entries == before
    async def real_async_creation():
        actor = await asyncio.create_subprocess_exec(*command, cwd=root,
            env={'HOME': str(root / 'home'), 'PATH': '/usr/bin:/bin'},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        actual = psutil.Process(actor.pid)
        birth = actual.create_time()
        try:
            assert await actor.stdout.readline() == b'SYNTHETIC_ENV_TARGET_READY\n'
            row = json.loads(stable_bytes(root / f'new-child-process-{actor.pid}.json'))
            assert row['argv'] == command and row['created'] == birth and row['parent'] == os.getpid()
        finally:
            if actor.returncode is None:
                assert psutil.Process(actor.pid).create_time() == birth
                actor.terminate()
            await asyncio.wait_for(actor.wait(), 5)
        return {'pid': actor.pid, 'created': birth}
    asynchronous = asyncio.run(real_async_creation())
    (root / 'creation-proof.json').write_text(json.dumps({'passed': True,
        'actual_constructor_argv': True, 'actual_async_constructor_argv': True,
        'late_wrong_and_reaped_refused': True, 'wrong_self_birth_refused_before_spawn': True,
        'unsupported_signal_and_watcher_refused_before_spawn': True,
        'captured': [{'pid': child.pid, 'created': born}, asynchronous], 'reaped': [child.pid, asynchronous['pid']]}))
