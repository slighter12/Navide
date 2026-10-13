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
    command = [doc['files']['runtime']['path'], '--protocol', '1',
               '--runtime-generation', 'c' * 32, '--owner-pid', str(os.getpid())]
    called, denied = [], []
    base = guard.creation_native_entries
    original_popen = subprocess.Popen

    class ForbiddenSpawn:
        def __init__(self, *args, **kwargs):
            called.append('popen')
            raise AssertionError('refused path reached actor creation')

    async def check(label):
        try:
            await asyncio.create_subprocess_exec(*command)
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

    async def wrong_image():
        try:
            await asyncio.create_subprocess_exec(str(root / 'unregistered-SYNTHETIC-image'))
        except PermissionError:
            denied.append('image')
        else:
            raise AssertionError('unregistered image delegated')

    async def uv_constructor():
        try:
            original_popen(command, stdout=subprocess.PIPE)
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
    finally:
        subprocess.Popen = original_popen
    assert called == [] and guard.creation_native_entries == base
    (root / 'loop-binding-proof.json').write_text(json.dumps({'passed': True,
        'denied': denied, 'actual_spawn_entries': len(called), 'payload_entries': 0,
        'actual_uvloop_preserved': True, 'created_actors': 0}))
