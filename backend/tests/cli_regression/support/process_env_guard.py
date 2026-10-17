"""Pinned Darwin fixture permission: environment payload belongs to SELF only.

This is an admitted-source Python boundary, NOT kernel containment. It cannot
revoke values already returned to a caller or cover arbitrary native syscalls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_GUARD = None

#: E1 wire v1 launch of the sidecar (ticket E3): `[runtime, --wire 1, --runtime-generation <hex32>, --owner-pid <pid>,
#: --ownership-journal <data>/runtime-ownership/<gen>.jsonl, --control-fd <n>]`, with `pass_fds == (n,)`.
WIRE_VERSION = '1'


def runtime_launch(runtime: str, generation: str, owner_pid: int, journal: Path, control_fd: int) -> list[str]:
    """The one launch form of the sidecar (used by the service's own argv builder in tests and by the probes)."""
    return [runtime, '--wire', WIRE_VERSION, '--runtime-generation', generation, '--owner-pid', str(owner_pid),
            '--ownership-journal', str(journal), '--control-fd', str(control_fd)]


def match_runtime_launch(argv, runtime: str, owner_pid: int, data_root: Path) -> int | None:
    """The control-fd number if `argv` is exactly the registered sidecar launch for this process, else None.
    Exact match on every element (no prefix, no optional tail, no blanket allowance)."""
    if not isinstance(argv, (list, tuple)):
        return None
    argv = list(argv)
    if (len(argv) != 11 or argv[:3] != [runtime, '--wire', WIRE_VERSION] or argv[3] != '--runtime-generation'
            or len(argv[4]) != 32 or any(c not in '0123456789abcdef' for c in argv[4])
            or argv[5:7] != ['--owner-pid', str(owner_pid)]
            or argv[7:9] != ['--ownership-journal', str(data_root / 'runtime-ownership' / f'{argv[4]}.jsonl')]
            or argv[9] != '--control-fd' or not argv[10].isascii() or not argv[10].isdecimal()
            or str(int(argv[10])) != argv[10] or int(argv[10]) < 3):
        return None
    return int(argv[10])


def control_fd_problem(control_fd: int, pass_fds) -> str | None:
    """Why `control_fd` is not the inherited socketpair end the service must be passing, or None if it is: it must be
    the only passed descriptor, an open AF_UNIX stream socket in this process."""
    import socket
    import stat
    if tuple(pass_fds or ()) != (control_fd,):
        return 'pass_fds is not exactly the control descriptor'
    try:
        if not stat.S_ISSOCK(os.fstat(control_fd).st_mode):
            return 'the control descriptor is not a socket'
        probe = socket.socket(fileno=os.dup(control_fd))
    except OSError:
        return 'the control descriptor is not an open descriptor'
    try:
        if probe.family != socket.AF_UNIX or probe.type != socket.SOCK_STREAM:
            return 'the control descriptor is not an AF_UNIX stream socket'
    finally:
        probe.close()
    return None


def dependency_inputs(backend: Path) -> dict[str, Path]:
    package = backend / '.venv/lib/python3.12/site-packages/psutil'
    inputs = {f'psutil-{role}': package / name for role, name in (
        ('public', '__init__.py'), ('darwin', '_psosx.py'),
        ('cache', '_common.py'), ('extension', '_psutil_osx.abi3.so'))}
    base = (backend / '.venv/bin/python').resolve(strict=True).parent.parent / 'lib/python3.12'
    inputs.update({f'cpython-{role}': base / name for role, name in (
        ('popen', 'subprocess.py'), ('watcher', 'asyncio/unix_events.py'),
        ('transport', 'asyncio/base_subprocess.py'), ('events', 'asyncio/base_events.py'),
        ('subprocess-api', 'asyncio/subprocess.py'), ('runner', 'asyncio/runners.py'))})
    site = package.parent
    inputs.update({f'loop-{role}': site / name for role, name in (
        ('uvicorn-config', 'uvicorn/config.py'), ('uvicorn-auto', 'uvicorn/loops/auto.py'),
        ('uvicorn-asyncio', 'uvicorn/loops/asyncio.py'), ('uvicorn-uvloop', 'uvicorn/loops/uvloop.py'),
        ('uvloop-public', 'uvloop/__init__.py'), ('uvloop-loop', 'uvloop/loop.pyx'),
        ('uvloop-process', 'uvloop/handles/process.pyx'),
        ('uvloop-image', 'uvloop/loop.cpython-312-darwin.so'))})
    return inputs


def installed():
    return _GUARD


def install(root: Path, document: dict):
    global _GUARD
    if _GUARD is not None:
        if _GUARD.pid != os.getpid() or _GUARD.root != root:
            raise PermissionError('process payload permission cannot be inherited')
        return _GUARD
    import psutil
    from psutil import _psosx
    from .fixture_exec import stable_file
    if psutil.__version__ != '7.2.2':
        raise PermissionError('unregistered process dependency version')
    paths = dependency_inputs(Path(__file__).resolve().parents[3])
    for role, path in paths.items():
        if stable_file(path)[1] != document['files'][role]:
            raise PermissionError('changed process dependency identity')
    if Path(_psosx.cext.__file__).resolve() != paths['psutil-extension'].resolve():
        raise PermissionError('unregistered process extension image')
    actor = psutil.Process(os.getpid())
    interpreter = document['files']['interpreter']
    if actor.exe() != interpreter['path'] or stable_file(Path(actor.exe()))[1] != interpreter:
        raise PermissionError('unregistered payload actor image')
    entries = {Path(document['files'][role]['path']) for role in ('account_bootstrap', 'native_difference_peer')}
    if Path(__import__('sys').argv[0]).resolve() not in entries:
        raise PermissionError('unregistered payload actor entry')

    class Permission:
        def __init__(self):
            self.root, self.pid = root, os.getpid()
            self.birth = psutil.Process(self.pid).create_time()  # new uncached instance
            self.native_entries = 0
            self.argv_native_entries = 0
            self.creation_native_entries = 0
            self.control_fd_violations = []
            self._constructor_code = None
            self.counts = {}
            self._ledger = os.open(root / f'process-env-refusals-{self.pid}.jsonl',
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

        def deny(self, operation, reason, pid):
            key = operation + ':' + reason
            self.counts[key] = self.counts.get(key, 0) + 1
            os.write(self._ledger, (json.dumps({'operation': operation, 'reason': reason}) + '\n').encode())
            raise psutil.AccessDenied(pid)

        def check(self, pid, operation):
            if os.getpid() != self.pid:
                self.deny(operation, 'fork-not-admitted', pid)
            if pid != self.pid:
                self.deny(operation, 'not-self', pid)
            if self.birth is None:
                self.deny(operation, 'revoked', pid)
            try:
                # No cached Process, oneshot or iterator object participates.
                actual = _psosx.Process(os.getpid()).create_time()
            except (psutil.Error, OSError):
                self.deny(operation, 'identity-unavailable', pid)
            if actual != self.birth:
                self.deny(operation, 'birth-mismatch', pid)

        def revoke(self):
            self.birth = None

        def _creation_role(self, argv, kwargs=None):
            if not isinstance(argv, (list, tuple)):
                return None
            argv = list(argv)
            runtime = document['files']['runtime']['path']
            # The one exact sidecar launch form (E1 wire v1): see `match_runtime_launch`. When the spawn keyword
            # arguments are known, the control descriptor is verified too (`control_fd_problem`).
            control_fd = match_runtime_launch(argv, runtime, os.getpid(), self.root / 'data')
            if control_fd is not None:
                if kwargs is not None:
                    problem = control_fd_problem(control_fd, kwargs.get('pass_fds'))
                    if problem is not None:
                        raise PermissionError('registered sidecar launch with a wrong control descriptor: ' + problem)
                return 'runtime'
            target = document['files']['process_env_target']['path']
            python_entries = {interpreter['path'], str(Path(__file__).resolve().parents[3] / '.venv/bin/python')}
            if (len(argv) == 5 and argv[0] in python_entries
                    and argv[1:] == ['-I', '-S', target, 'SYNTHETIC_R11_CREATION']):
                return 'synthetic-created'
            return None

        def expect_parent_closed(self, control_fd, identity):
            """Runs one loop turn after the spawn returned: the service must have closed ITS copy of the child's
            socketpair end by now (Python must hold only its own end, or it could never see the sidecar die)."""
            try:
                now = os.fstat(control_fd)
            except OSError:
                return  # closed
            if (now.st_mode, now.st_dev, now.st_ino) == identity:
                self.control_fd_violations.append('the parent still holds the child end of the control socketpair')

        def bind_loop_routes(self):
            import asyncio
            import asyncio.base_events
            import asyncio.unix_events
            import uvicorn
            import uvloop
            import uvloop.loop
            if uvicorn.__version__ != '0.52.4' or uvloop.__version__ != '0.22.1':
                raise PermissionError('unregistered loop dependency version')
            if Path(uvloop.loop.__file__).resolve() != paths['loop-uvloop-image'].resolve():
                raise PermissionError('unregistered actual loop image')
            self._cpp_loop = asyncio.SelectorEventLoop
            self._cpp_exec = asyncio.base_events.BaseEventLoop.subprocess_exec
            self._cpp_transport = asyncio.unix_events._UnixSelectorEventLoop._make_subprocess_transport
            self._uv_loop, self._uv_exec = uvloop.Loop, uvloop.Loop.subprocess_exec
            self._uv_factory = uvloop.new_event_loop
            original_factory = uvicorn.Config.get_loop_factory
            original_spawn = asyncio.create_subprocess_exec
            self._factory_code = original_factory.__code__

            def bound_factory(config):
                self.check(os.getpid(), 'loop-factory')
                self._check_loop_sources()
                if config.loop not in ('auto', 'uvloop', 'asyncio'):
                    raise PermissionError('unsupported actual loop factory')
                factory = original_factory(config)
                if ((config.loop in ('auto', 'uvloop') and factory is self._uv_factory)
                        or (config.loop == 'asyncio' and factory is self._cpp_loop)):
                    return factory  # Same actual factory. NEVER replace auto/uvloop.
                raise PermissionError('unsupported actual loop factory')

            async def bound_spawn(*argv, **kwargs):
                self.check_running_loop()
                if self._creation_role(argv, kwargs) is None:
                    raise PermissionError('unregistered loop subprocess image or argv')
                control_fd = match_runtime_launch(list(argv), document['files']['runtime']['path'], os.getpid(), self.root / 'data')
                identity = None
                if control_fd is not None:
                    info = os.fstat(control_fd)
                    identity = (info.st_mode, info.st_dev, info.st_ino)
                process = await original_spawn(*argv, **kwargs)
                if identity is not None:
                    asyncio.get_running_loop().call_soon(self.expect_parent_closed, control_fd, identity)
                return process

            self._bound_factory, self._bound_spawn = bound_factory, bound_spawn
            uvicorn.Config.get_loop_factory = bound_factory
            asyncio.create_subprocess_exec = bound_spawn

        def _check_loop_sources(self):
            for role, path in paths.items():
                if role.startswith(('loop-', 'cpython-')) and stable_file(path)[1] != document['files'][role]:
                    raise PermissionError('changed loop or spawn implementation')

        def require_loop_bindings(self):
            import asyncio
            import uvicorn
            self.check(os.getpid(), 'loop-binding')
            if self.control_fd_violations:
                raise PermissionError(self.control_fd_violations[0])
            self._check_loop_sources()
            if (uvicorn.Config.get_loop_factory is not self._bound_factory
                    or asyncio.create_subprocess_exec is not self._bound_spawn):
                raise PermissionError('changed bound loop factory or API')

        def check_running_loop(self):
            import asyncio
            self.require_loop_bindings()
            loop = asyncio.get_running_loop()
            if type(loop) is self._cpp_loop:
                if (loop.subprocess_exec != self._cpp_exec.__get__(loop, type(loop))
                        or loop._make_subprocess_transport != self._cpp_transport.__get__(loop, type(loop))):
                    raise PermissionError('unbound standard spawn method')
                return 'asyncio'
            if type(loop) is self._uv_loop and loop.subprocess_exec == self._uv_exec.__get__(loop, type(loop)):
                return 'uvloop'
            raise PermissionError('unsupported running loop or spawn method')

        def _check_constructor_reaper(self):
            import asyncio
            import signal
            import sys
            import subprocess
            import asyncio.base_subprocess
            import asyncio.unix_events
            self.check(os.getpid(), 'argv-constructor')
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass  # Synchronous NEW direct child; existing reaper checks apply.
            else:
                if self.check_running_loop() != 'asyncio':
                    raise PermissionError('no constructor observation on native loop')
            for role, module in (('cpython-popen', subprocess),
                    ('cpython-watcher', asyncio.unix_events), ('cpython-transport', asyncio.base_subprocess)):
                identity = document['files'][role]
                if Path(module.__file__).resolve() != Path(identity['path']) or stable_file(Path(identity['path']))[1] != identity:
                    raise PermissionError('changed construction/watcher implementation')
            if (sys.version_info[:3] != (3, 12, 11)
                    or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
                    or type(asyncio.get_event_loop_policy()) is not asyncio.DefaultEventLoopPolicy
                    or type(asyncio.get_child_watcher()) is not asyncio.ThreadedChildWatcher):
                raise PermissionError('unsupported constructor reaping state')

        def _snapshot_new_child(self, child, expected_birth=None):
            # This is called synchronously inside GuardedPopen, before return
            # and before asyncio registers its independent waitpid thread.
            # A later Popen lock DOES NOT exclude that thread and grants nothing.
            import inspect
            caller = inspect.currentframe().f_back
            if (caller.f_code is not self._constructor_code or caller.f_locals.get('self') is not child
                    or child.returncode is not None):
                raise PermissionError('not an unpublished new child')
            self._check_constructor_reaper()
            role = self._creation_role(child.args)
            if role is None:
                raise PermissionError('unregistered created child role')
            target = psutil.Process(child.pid)
            born, parent = target.create_time(), target.ppid()
            if parent != os.getpid() or (expected_birth is not None and born != expected_birth):
                raise PermissionError('unregistered created child identity')
            image = document['files']['runtime' if role == 'runtime' else 'interpreter']
            exe = target.exe()
            if exe != image['path'] or stable_file(Path(exe))[1] != image:
                raise PermissionError('unregistered created child image')
            self.creation_native_entries += 1
            argv = native_argv(child.pid)  # exact lifecycle-gated original C getter
            if argv != list(child.args):
                raise PermissionError('created child OS argv differs from admitted launch')
            from .process_argv_observation import write_observation
            row = {'pid': target.pid, 'created': born, 'parent': parent, 'exe': exe,
                   'image': image, 'argv': argv, 'role': role, 'phase': 'new-child-constructor'}
            write_observation(root, f'new-child-process-{target.pid}.json', row)
            return row

    guard = Permission()
    public = psutil.Process.environ
    lower = _psosx.Process.environ
    native = _psosx.cext.proc_environ
    public_argv, lower_argv, native_argv = psutil.Process.cmdline, _psosx.Process.cmdline, _psosx.cext.proc_cmdline

    def public_cmdline(process):
        guard.check(process.pid, 'argv-public')
        return public_argv(process)

    def lower_cmdline(process):
        guard.check(process.pid, 'argv-darwin')
        return lower_argv(process)

    def native_cmdline(pid):
        guard.check(pid, 'argv-extension')
        guard.argv_native_entries += 1
        return native_argv(pid)

    def public_environ(process):
        guard.check(process.pid, 'public')
        return public(process)

    def lower_environ(process):
        guard.check(process.pid, 'darwin')
        return lower(process)

    def native_environ(pid):
        guard.check(pid, 'extension')
        guard.native_entries += 1
        return native(pid)

    # process_iter's .info is a dynamic dictionary written by as_dict. Track
    # only the environment-key flag; permission precedes cached result lookup.
    # Metadata-only info is unchanged. No pre-existing cache is inspected.
    def set_info(process, value):
        process.__dict__['_navide_info_environment'] = 'environ' in value
        process.__dict__['_navide_info_argv'] = 'cmdline' in value
        process.__dict__['_navide_info'] = value

    def get_info(process):
        if process.__dict__.get('_navide_info_environment', False):
            guard.check(process.pid, 'info-cache')
        if process.__dict__.get('_navide_info_argv', False):
            guard.check(process.pid, 'argv-info-cache')
        try:
            return process.__dict__['_navide_info']
        except KeyError:
            raise AttributeError('info') from None

    psutil.Process.cmdline = public_cmdline
    _psosx.Process.cmdline = lower_cmdline
    _psosx.cext.proc_cmdline = native_cmdline
    psutil.Process.environ = public_environ
    _psosx.Process.environ = lower_environ
    _psosx.cext.proc_environ = native_environ
    psutil.Process.info = property(get_info, set_info)
    guard.bind_loop_routes()
    _GUARD = guard
    return guard
