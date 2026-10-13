"""R10 synthetic-only payload controls; never launches backend/lifespan."""
import ctypes
import errno
import json
import os
from pathlib import Path
import subprocess
import sys


def run(root: Path) -> None:
    import psutil
    from psutil import _psosx
    try:
        from .process_env_guard import installed
        guard = installed()
    except ImportError:
        guard = None
    # No inherited account environment: fresh target receives literal OWN inputs.
    env = {'HOME': str(root / 'home'), 'PATH': '/usr/bin:/bin',
           'SYNTHETIC_R10_CANARY': 'OWN_CANARY_NEVER_ACCOUNT_DATA'}
    target = subprocess.Popen([sys.executable, '-I', '-S', str(Path(__file__).with_name('process_env_target.py'))],
        env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    captured = []
    original = _psosx.cext.proc_environ
    entries = []
    def count(pid):
        # A counter, NOT a replacement getter. Refuse accidental foreign reads.
        if pid not in (os.getpid(), target.pid):
            raise AssertionError('probe attempted unrelated payload extraction')
        entries.append(pid)
        return original(pid)
    _psosx.cext.proc_environ = count
    try:
        assert target.stdout.readline() == 'SYNTHETIC_ENV_TARGET_READY\n'
        process = psutil.Process(target.pid)
        captured.append({'pid': target.pid, 'born': process.create_time(), 'parent': process.ppid()})
        assert captured[0]['parent'] == os.getpid()
        denied = False
        try:
            process.environ()
        except psutil.AccessDenied:
            denied = True
        assert denied, 'unregistered synthetic payload was delivered'
        assert entries == [], 'denial happened after underlying getter entry'
        assert guard is not None
        # All exact pinned getters, without querying any unrelated payload/cache.
        assert process.as_dict(['environ']) == {'environ': None}  # real ad_value policy
        assert guard.native_entries == 0
        for getter in (lambda: process._proc.environ(),
                       lambda: _psosx.cext.proc_environ(target.pid)):
            before = guard.native_entries
            try:
                getter()
            except psutil.AccessDenied:
                pass
            else:
                raise AssertionError('unregistered lower/multiattribute route delivered payload')
            assert guard.native_entries == before
        me = psutil.Process(os.getpid())
        self_env = me.environ()
        assert self_env['CODEX_HOME'] == str(root / 'home/.codex-panes/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa')
        assert guard.native_entries > 0
        with me.oneshot():
            assert me.as_dict(['pid', 'environ'])['environ']['CODEX_HOME'] == self_env['CODEX_HOME']
        # Actual pinned process_iter writes .info via as_dict; exercise its SELF
        # branch without changing enumeration or reading any foreign payload.
        own = next(p for p in psutil.process_iter() if p.pid == os.getpid())
        assert next(p for p in psutil.process_iter(['pid', 'environ']) if p.pid == os.getpid()).info['environ']['CODEX_HOME'] == self_env['CODEX_HOME']
        birth = guard.birth
        before = guard.native_entries
        guard.birth = birth + 1.0
        for getter in (me.environ, lambda: own.info, me._proc.environ,
                       lambda: _psosx.cext.proc_environ(os.getpid())):
            try:
                getter()
            except psutil.AccessDenied:
                pass
            else:
                raise AssertionError('mismatched identity delivered payload/cache')
        assert guard.native_entries == before
        guard.birth = birth
        # Fork inherits wrappers/state, NOT SELF permission. No getter entry.
        read_fd, write_fd = os.pipe()
        fork = os.fork()
        if fork == 0:
            os.close(read_fd)
            try:
                me.environ()
            except psutil.AccessDenied:
                os.write(write_fd, b'DENIED')
                os._exit(0)
            os._exit(1)
        os.close(write_fd)
        assert os.read(read_fd, 16) == b'DENIED'
        os.close(read_fd)
        assert os.waitpid(fork, 0)[1] == 0
        # Preserve and run the ACTUAL production sweep; no table/home-policy stub.
        from agent_team_backend.cli_vendors.codex import CodexHomeManager
        live = root / 'home/.codex-panes/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        orphan = root / 'home/.codex-panes/bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
        live.mkdir(parents=True, exist_ok=True)
        orphan.mkdir()
        manager = CodexHomeManager()
        reclaimed = manager.sweep_orphans()
        assert live.is_dir() and not orphan.exists()
        assert reclaimed == ['bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb']
        guard.revoke()
        before = guard.native_entries
        assert me.as_dict(['environ']) == {'environ': None}
        for getter in (me.environ, lambda: own.info):
            try:
                getter()
            except psutil.AccessDenied:
                pass
            else:
                raise AssertionError('revoked identity delivered payload/cache')
        assert guard.native_entries == before
        # Real native bypass against ONLY the owned synthetic target. No dump.
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, target.pid)  # SDK CTL_KERN/KERN_PROCARGS2
        size = ctypes.c_size_t(262144)
        buffer = ctypes.create_string_buffer(size.value)
        libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        libc.sysctl.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0)
        error = ctypes.get_errno()
        obtained = result == 0 and b'SYNTHETIC_R10_CANARY=OWN_CANARY_NEVER_ACCOUNT_DATA\0' in buffer.raw[:size.value]
        proof = {'passed': True, 'unregistered_getter_entries': 0, 'self_read': True,
                 'lower_and_multiattribute_denied': True, 'cache_after_revoke_denied': True,
                 'fork_permission_denied': True, 'real_sweep_live_retained': live.is_dir(),
                 'real_sweep_orphan_reclaimed': not orphan.exists(),
                 'native_canary_return': result, 'native_canary_errno': error,
                 'native_canary_obtained': obtained, 'universal_native_isolation': False,
                 'refusals': guard.counts, 'captured': captured}
    finally:
        _psosx.cext.proc_environ = original
        # Only actual owned PID with captured fresh birth, never guessed PIDs.
        if target.poll() is None:
            assert captured and psutil.Process(target.pid).create_time() == captured[0]['born']
            target.terminate()
        target.wait(timeout=5)
    proof['reaped'] = [target.pid]
    (root / 'process-env-proof.json').write_text(json.dumps(proof, sort_keys=True))
