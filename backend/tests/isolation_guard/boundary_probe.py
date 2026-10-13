"""Fresh-process synthetic probe. No backend import, server or native launch."""
import json
import os
from pathlib import Path
import pwd
import sys

backend, fixture, physical, operation = map(Path, sys.argv[1:])
operation = str(operation)
sys.path.insert(0, str(backend))
sys.path.append(str(backend / '.venv/lib/python3.12/site-packages'))
sys.dont_write_bytecode = True
# Substitute the OS account boundary with a test-owned physical home before
# any application discovery. Never call the original account resolver.
physical_record = pwd.struct_passwd(('synthetic', 'x', os.getuid(), os.getgid(), '', str(physical), '/bin/sh'))
pwd.getpwuid = lambda _uid: physical_record
from tests.cli_regression.support import isolation
physical_fd = os.open(physical / 'rollout-synthetic.jsonl', os.O_RDONLY)
physical_dirfd = os.open(physical, os.O_RDONLY | os.O_DIRECTORY)
installer = getattr(isolation, 'install_account_home_guard', None)
if installer is not None:
    installer(fixture)

observed = False
denied = False
try:
    if operation == 'account-read':
        # The captured account record deliberately bypasses the home override;
        # its file origin still has to be refused independently of HOME.
        Path(physical_record.pw_dir, 'rollout-synthetic.jsonl').read_bytes()
        observed = True
    elif operation == 'write':
        Path(physical_record.pw_dir, 'rollout-synthetic.jsonl').write_bytes(b'forbidden')
        observed = True
    elif operation == 'list':
        os.listdir(physical_record.pw_dir)
        observed = True
    elif operation == 'scan':
        with os.scandir(physical_record.pw_dir) as entries:
            list(entries)
        observed = True
    elif operation == 'symlink-read':
        (fixture / 'escape' / 'rollout-synthetic.jsonl').read_bytes()
        observed = True
    elif operation == 'symlink-write':
        (fixture / 'escape' / 'rollout-synthetic.jsonl').write_bytes(b'forbidden')
        observed = True
    elif operation == 'preopened-read':
        os.read(physical_fd, 128)
        observed = True
    elif operation == 'dirfd-read':
        fd = os.open('rollout-synthetic.jsonl', os.O_RDONLY, dir_fd=physical_dirfd)
        try:
            os.read(fd, 128)
            observed = True
        finally:
            os.close(fd)
    elif operation.startswith('exec-'):
        from tests.cli_regression.support.isolation import install_external_guards
        from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim
        from tests.cli_regression.support.fixture_exec import register_fixture_chain
        os.environ['NAVIDE_REGRESSION_ACCOUNT_GUARD'] = '1'
        script = fixture / 'peer-fixture.py'
        script.write_text('print("SYNTHETIC_FIXTURE_ONLY")\n')
        interpreter = Path(base_python_executable()).resolve(strict=True)
        shim = install_cli_shim(fixture, 'codex', [str(interpreter), '-u', str(script), str(fixture), 'codex'])
        os.environ['NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256'] = register_fixture_chain(fixture, shim, script, interpreter)
        install_external_guards(fixture)
        from agent_team_backend import cli_runtime
        environment = {'HOME': str(fixture / 'home'), 'USERPROFILE': str(fixture / 'home'), 'PATH': str(fixture)}
        argv = [str(shim), '--version']
        if operation == 'exec-admission':
            argv = [str(physical / 'unrecognized-executable')]
        elif operation == 'exec-tampered-script':
            script.write_text('print("SYNTHETIC_TAMPERED")\n')
        elif operation == 'exec-tampered-shim':
            shim.write_text('#!/bin/sh\nexit 99\n')
        elif operation == 'exec-shell':
            argv = ['/bin/bash', '-ilc', 'codex --version']
        elif operation == 'exec-outside-shim':
            argv = [str(physical / 'codex')]
        elif operation != 'exec-registered':
            raise AssertionError('unknown admission operation')
        cli_runtime.prepare_native_launch(argv, cwd=str(fixture), env=environment)
        # Admission only. Never execute any negative control or vendor CLI.
    else:
        raise AssertionError('unknown synthetic operation')
except PermissionError:
    denied = True
receipts = fixture / 'origin-refusals.jsonl'
denial_count = len(receipts.read_bytes().splitlines()) if receipts.exists() else 0
print(json.dumps({'denied': denied, 'physical_content_observed': observed,
                  'operation': operation, 'completed': True, 'denial_count': denial_count}))
