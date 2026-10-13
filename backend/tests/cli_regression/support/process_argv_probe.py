"""Created synthetic argv actors only; no backend or unrelated payload reads."""
import json
import os
from pathlib import Path
import subprocess
import sys


def run(root: Path):
    import psutil
    from psutil import _psosx
    from .process_env_guard import installed
    guard = installed()
    command = [sys.executable, '-I', '-S', str(Path(__file__).with_name('process_env_target.py')),
               'SYNTHETIC_R11_ARG', 'agent_team_backend']
    target = subprocess.Popen(command, cwd=root, env={'HOME': str(root / 'home'), 'PATH': '/usr/bin:/bin'},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    original = _psosx.cext.proc_cmdline
    entries = []
    def counted(pid):
        assert pid in (os.getpid(), target.pid), 'unrelated synthetic probe target'
        entries.append(pid)
        return original(pid)
    _psosx.cext.proc_cmdline = counted
    born = psutil.Process(target.pid).create_time()
    try:
        assert target.stdout.readline() == 'SYNTHETIC_ENV_TARGET_READY\n'
        actor = psutil.Process(target.pid)
        try:
            actor.cmdline()
        except psutil.AccessDenied:
            pass
        else:
            raise AssertionError('foreign synthetic argv delivered before admission')
        assert entries == []
        before = guard.argv_native_entries
        for getter in (actor.cmdline, actor._proc.cmdline, lambda: _psosx.cext.proc_cmdline(target.pid)):
            try:
                getter()
            except psutil.AccessDenied:
                pass
            else:
                raise AssertionError('foreign pinned route delivered argv')
        assert guard.argv_native_entries == before
        actor.info = actor.as_dict(['pid', 'cmdline'])  # actual ad_value, no foreign payload
        try:
            actor.info
        except psutil.AccessDenied:
            pass
        else:
            raise AssertionError('foreign cached .info was delivered')
        assert guard.argv_native_entries == before
        me = psutil.Process(os.getpid())
        actual = me.cmdline()
        assert 'account_bootstrap.py' == Path(actual[3]).name
        me.info = me.as_dict(['cmdline'])
        assert me.info['cmdline'] == actual
        birth = guard.birth
        before = guard.argv_native_entries
        for invalid_birth in (birth + 1, birth - 1):
            guard.birth = invalid_birth
            for getter in (me.cmdline, me._proc.cmdline, lambda: _psosx.cext.proc_cmdline(os.getpid()), lambda: me.info):
                try:
                    getter()
                except psutil.AccessDenied:
                    pass
                else:
                    raise AssertionError('wrong/stale birth delivered argv/cache')
            assert guard.argv_native_entries == before
        guard.birth = birth
        read_fd, write_fd = os.pipe()
        fork = os.fork()
        if fork == 0:
            os.close(read_fd)
            try:
                me.cmdline()
            except psutil.AccessDenied:
                os.write(write_fd, b'DENIED')
                os._exit(0)
            os._exit(1)
        os.close(write_fd)
        assert os.read(read_fd, 16) == b'DENIED'
        os.close(read_fd)
        assert os.waitpid(fork, 0)[1] == 0
        from agent_team_backend import pty_registry, osplat
        before = guard.argv_native_entries
        assert osplat.process_tree.command_of(target.pid) is None
        assert pty_registry._backend_alive(target.pid) is True
        assert guard.argv_native_entries == before
        target.terminate()
        target.wait(timeout=5)
        assert osplat.process_tree.command_of(target.pid) == ''
        assert pty_registry._backend_alive(target.pid) is False
        guard.revoke()
        for getter in (me.cmdline, lambda: me.info):
            try:
                getter()
            except psutil.AccessDenied:
                pass
            else:
                raise AssertionError('revoked argv permission delivered payload/cache')
        proof = {'passed': True, 'foreign_getter_entries': 0, 'actual_self': True,
                 'wrong_birth_cache_fork_revoke_denied': True, 'unavailable_owner_retained': True,
                 'gone_owner_gone': True, 'captured': [{'pid': target.pid, 'created': born}],
                 'reaped': [target.pid]}
    finally:
        _psosx.cext.proc_cmdline = original
        if target.poll() is None:
            assert psutil.Process(target.pid).create_time() == born
            target.terminate()
        target.wait(timeout=5)
    (root / 'process-argv-proof.json').write_text(json.dumps(proof))
