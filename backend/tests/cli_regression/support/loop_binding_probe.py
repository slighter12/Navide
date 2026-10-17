"""Actual-loop pre-spawn negatives; no actor is created on refused routes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess


def run(root: Path):
    import uvicorn
    from .process_env_guard import installed
    from .difference_entry import validate_kernel_setup
    guard = installed()
    doc = validate_kernel_setup(root)
    from .process_env_guard import runtime_launch
    # The REGISTERED launch form (journal under the fixture's data dir, a real socketpair end): every refusal below must
    # come from the unbound ROUTE, not from an unregistered argv. The descriptor is passed only where the guard admits.
    (root / 'data' / 'runtime-ownership').mkdir(parents=True, exist_ok=True)
    parent_end, child_end = __import__('socket').socketpair()
    command = runtime_launch(doc['files']['runtime']['path'], 'c' * 32, os.getpid(),
                             root / 'data' / 'runtime-ownership' / ('c' * 32 + '.jsonl'), child_end.fileno())
    spawn_kwargs = {'pass_fds': (child_end.fileno(),)}
    called, denied = [], []
    base = guard.creation_native_entries
    original_popen = subprocess.Popen

    class ForbiddenSpawn:
        def __init__(self, *args, **kwargs):
            called.append('popen')
            raise AssertionError('refused path reached actor creation')

    async def check(label):
        try:
            await asyncio.create_subprocess_exec(*command, **spawn_kwargs)
        except PermissionError:
            denied.append(label)
        else:
            raise AssertionError('unbound route admitted')

    class UnboundSelector(asyncio.SelectorEventLoop):
        pass

    async def wrong_method():
        loop = asyncio.get_running_loop()
        original = loop.subprocess_exec
        async def never_spawn(*args, **kwargs):
            called.append('method')
            raise AssertionError('unbound method delegated')
        loop.subprocess_exec = never_spawn
        try:
            await check('method')
        finally:
            loop.subprocess_exec = original

    async def wrong_control_descriptor():
        # The registered argv, but the descriptor handed to the child is not exactly the control socketpair end.
        for label, kwargs in (('no-pass-fds', {}), ('extra-pass-fd', {'pass_fds': (child_end.fileno(), parent_end.fileno())})):
            try:
                await asyncio.create_subprocess_exec(*command, **kwargs)
            except PermissionError:
                denied.append('control-fd-' + label)
            else:
                raise AssertionError('registered launch with a wrong control descriptor admitted')

    async def wrong_image():
        try:
            await asyncio.create_subprocess_exec(str(root / 'unregistered-SYNTHETIC-image'))
        except PermissionError:
            denied.append('image')
        else:
            raise AssertionError('unregistered image delegated')

    async def uv_constructor():
        try:
            original_popen(command, stdout=subprocess.PIPE, **spawn_kwargs)
        except PermissionError:
            denied.append('uvloop-constructor')
        else:
            raise AssertionError('native loop granted constructor permission')

    subprocess.Popen = ForbiddenSpawn
    try:
        for selected in ('none', lambda: UnboundSelector()):
            try:
                uvicorn.Config(app=None, loop=selected).get_loop_factory()
            except PermissionError:
                denied.append('factory')
            else:
                raise AssertionError('unbound factory admitted')
        asyncio.run(check('loop'), loop_factory=UnboundSelector)
        asyncio.run(wrong_method(), loop_factory=uvicorn.Config(app=None, loop='asyncio').get_loop_factory())
        asyncio.run(wrong_method(), loop_factory=uvicorn.Config(app=None, loop='auto').get_loop_factory())
        asyncio.run(uv_constructor(), loop_factory=uvicorn.Config(app=None, loop='auto').get_loop_factory())
        asyncio.run(wrong_image(), loop_factory=uvicorn.Config(app=None, loop='auto').get_loop_factory())
        asyncio.run(wrong_control_descriptor(), loop_factory=uvicorn.Config(app=None, loop='auto').get_loop_factory())
    finally:
        subprocess.Popen = original_popen
    assert called == [] and guard.creation_native_entries == base
    parent_end.close()
    child_end.close()
    (root / 'loop-binding-proof.json').write_text(json.dumps({'passed': True,
        'denied': denied, 'actual_spawn_entries': len(called), 'payload_entries': 0,
        'actual_uvloop_preserved': True, 'created_actors': 0}))
