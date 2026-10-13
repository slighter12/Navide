"""Fixed difference-fixture bootstrap: account guard before driver/app imports."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import socket
import subprocess
from pathlib import Path
import runpy
import sys

BACKEND = Path(__file__).resolve().parents[3]


def initialize(root: Path) -> None:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).parent))
    from difference_entry import ANCHOR, CANARY, validate_kernel_setup, validate_stdio
    validate_stdio(root)
    document = validate_kernel_setup(root)
    from native_file_guard import principal_manifest_runtime
    principal_runtime = principal_manifest_runtime(root, document)
    kernel = {key: os.environ[key] for key in (ANCHOR, CANARY)}
    sys.path.insert(0, str(BACKEND))
    # -I -S skips site startup/.pth/user hooks; add only the existing pinned
    # environment's package directory, without executing site.main().
    sys.path.append(str(BACKEND / '.venv/lib/python3.12/site-packages'))
    from tests.cli_regression.support.isolation import isolated_environment, install_external_guards
    manifest = os.environ.get('NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256')
    runtime = os.environ.get('NAVIDE_ACR_EVAL_RUNTIME')
    environment = isolated_environment(root)
    environment['NAVIDE_REGRESSION_ROOT'] = str(root)
    environment['NAVIDE_REGRESSION_ACCOUNT_GUARD'] = '1'
    environment.update(kernel)
    environment['PATH'] = os.pathsep.join([str(root), environment['PATH']])
    if manifest:
        if len(manifest) != 64 or any(c not in '0123456789abcdef' for c in manifest):
            raise PermissionError('invalid fixture manifest anchor')
        environment['NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256'] = manifest
    if runtime == 'rust-shell':
        environment['NAVIDE_ACR_EVAL_RUNTIME'] = runtime
    if principal_runtime is not None:
        environment['NAVIDE_CLI_RUNTIME_BIN'] = str(principal_runtime)
    os.environ.clear()
    os.environ.update(environment)
    from tests.cli_regression.support.process_env_guard import install
    install(root, validate_kernel_setup(root))
    if len(sys.argv) > 1 and sys.argv[1] in {'driver', 'backend'}:
        from tests.cli_regression.support.process_argv_observation import observe_self
        observe_self(root, sys.argv[1])
    install_external_guards(root)


def shell_probe(root: Path) -> None:
    """Actual registered Bash/shim/Python chain, never a backend or PTY."""
    import psutil
    from fixture_exec import stable_bytes
    from tests.cli_regression.support.fixture_exec import admit_fixture_command
    argv = ['/bin/bash', '-lc', 'codex --guard-probe']
    admit_fixture_command(root, argv, dict(os.environ))
    with subprocess.Popen(argv, cwd=root, env=dict(os.environ), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            line = child.stdout.readline()
            assert line.startswith('DIFFERENCE_GUARD_READY '), line
            claim = json.loads(line.removeprefix('DIFFERENCE_GUARD_READY '))
            shell = psutil.Process(child.pid)
            descendants = {item.pid for item in shell.children(recursive=True)} | {shell.pid}
            assert claim['pid'] in descendants
            process = psutil.Process(claim['pid'])
            actual = {'pid': process.pid, 'created': process.create_time(), 'parent': process.ppid(),
                      'exe': process.exe()}
            from tests.cli_regression.support.process_argv_observation import correlate_observation
            document = json.loads(stable_bytes(root / 'guard-identities.json'))
            actual['image'] = document['files']['interpreter']
            observation = correlate_observation(root, actual)
            if observation is None:
                raise PermissionError('missing actual producer SELF observation')
            actual['argv'], actual['argv_phase'] = observation['argv'], observation['phase']
            identities = json.loads(stable_bytes(root / 'exec-identities.json'))
            expected_argv = [identities['interpreter']['path'], '-I', '-S', '-B', '-u', identities['script']['path'],
                             str(root), 'codex', '--guard-probe']
            assert actual['exe'] == identities['interpreter']['path'] and actual['argv'] == expected_argv
            assert claim['script_sha256'] == identities['script']['sha256']
            assert actual['parent'] == claim['parent_claim']
            assert claim['path'] == os.environ['PATH'] and claim['home'] == str(root / 'home')
            output, stderr = child.communicate('GO\n', timeout=10)
            assert child.returncode == 0, stderr
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    (root / 'shell-proof.json').write_text(json.dumps({'os_matches_fixture': True,
        'os_observation': actual, 'shell_root_pid': child.pid, 'fixture_claim': claim,
        'output': output, 'stderr': stderr, 'root_reaped': child.poll() is not None,
        'metadata_produced': False}))


def main() -> None:
    mode, root, *arguments = sys.argv[1:]
    root = Path(root).resolve(strict=True)
    initialize(root)
    if mode == 'null-device-probe':
        import stat
        from difference_entry import CANARY
        fd = os.open('/dev/null', os.O_RDWR | os.O_NOFOLLOW)
        try:
            assert stat.S_ISCHR(os.fstat(fd).st_mode)
            assert os.write(fd, b'SYNTHETIC_NULL_ONLY') == 19
            assert os.read(fd, 1) == b''
        finally:
            os.close(fd)
        denied = 0
        alias = root / 'synthetic-null-alias'
        alias.symlink_to('/dev/null')
        for path in (alias, Path(os.environ[CANARY])):
            try:
                fd = os.open(path, os.O_WRONLY)
            except PermissionError:
                denied += 1
            else:
                os.close(fd)
                raise AssertionError('unexpected disk/alias write permission')
        assert denied == 2
        print('EXACT_NULL_RDWR_AND_NEGATIVE_CONTROLS')
        return
    if mode == 'process-env-probe':
        if arguments == ['native-startup-wire']:
            from tests.cli_regression.support.runtime_self_probe import protocol_controls
            protocol_controls(root)
            return
        if arguments == ['recovery-self']:
            from tests.cli_regression.support.recovery_self_probe import run
            run(root)
            return
        if arguments == ['loop-binding']:
            from tests.cli_regression.support.loop_binding_probe import run
            run(root)
            return
        if arguments and arguments[0] == 'runtime-self':
            from tests.cli_regression.support.runtime_self_probe import run
            run(root, arguments[1])
            return
        if arguments == ['argv']:
            from tests.cli_regression.support.process_argv_probe import run
        elif arguments == ['creation']:
            from tests.cli_regression.support.creation_window_probe import run
        elif arguments == ['capture']:
            from tests.cli_regression.support.process_argv_capture_probe import run
        else:
            from tests.cli_regression.support.process_env_probe import run
        run(root)
        return
    if mode == 'ownership-probe':
        from tests.cli_regression.support.darwin_ownership_probe import run
        run(root, arguments[0])
        return
    if mode == 'imports':
        from fixture_exec import stable_bytes, stable_file
        helper = BACKEND / 'agent_team_backend/git_askpass_helper.py'
        before = stable_file(helper)[1]
        target = arguments[0]
        if target == 'driver':
            from tests.cli_regression.support import native_difference_driver
            assert native_difference_driver.BackendProcess
        elif target == 'application':
            from agent_team_backend import app, native_skills, skills_store
            assert app.skills_store and native_skills._home() == root / 'home'
            assert skills_store._real_home() == root / 'home'
        else:
            raise PermissionError('unregistered import probe')
        after = stable_file(helper)[1]
        assert before == after and before['mode'] & 0o777 == 0o755
        refusals = root / 'origin-refusals.jsonl'
        rows = [json.loads(line) for line in stable_bytes(refusals).splitlines()] if refusals.exists() else []
        if target == 'application':
            assert len(rows) == 1, rows
            assert rows[0] == {'event': 'os.chmod', 'operation': 'write', 'origin': 'outside-fixture',
                               'target_sha256': hashlib.sha256(os.fsencode(helper)).hexdigest(),
                               'source': 'posix-paths', 'source_line': 111}, rows
        else:
            assert not rows, rows
        (root / 'import-proof.json').write_text(json.dumps({
            'target': target, 'kernel_origin_denied_before_import': True,
            'source_helper_unchanged': before == after, 'helper_identity': after,
            'refusals': len(rows), 'account_home': pwd.getpwuid(os.getuid()).pw_dir,
            'backend_main_lifespan_sidecar_pty_called': False,
        }))
        return
    if mode == 'metadata-probe':
        # Construct synthetic oracle input only; do not run a CLI or observer.
        from fixture_exec import stable_bytes, stable_file
        from tests.cli_regression.support.native_difference_driver import verify_synthetic_sources
        document = json.loads(stable_bytes(root / 'guard-identities.json'))
        identities = json.loads(stable_bytes(root / 'exec-identities.json'))
        marker = stable_bytes(root / 'fixture-marker').decode()
        workspace = root / 'workspace-SYNTHETIC'
        workspace.mkdir()
        directory = root / 'home/.codex-panes/SYNTHETIC/sessions'
        directory.mkdir(parents=True)
        sid = '33333333-3333-4333-8333-333333333333'
        output = directory / f'rollout-fixture-{sid}.jsonl'
        header = {'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(workspace),
                  'cli_version': '99.0.0-regression', 'synthetic_identity': marker,
                  'producer_script_sha256': identities['script']['sha256']}}
        if arguments[0] == 'identity':
            header['payload']['synthetic_identity'] = 'UNEXPECTED_SYNTHETIC_ONLY'
        output.write_bytes((json.dumps(header) + '\n').encode() + stable_bytes(root / 'stage-1.bin'))
        if arguments[0] == 'extra':
            (directory / 'unexpected.jsonl').write_bytes(b'SYNTHETIC_UNEXPECTED_ONLY')
        import psutil
        creator = psutil.Process(os.getpid())
        (root / 'stage-1-lineage.json').write_text(json.dumps({'stage': 1,
            'fixture_marker': marker, 'script_sha256': identities['script']['sha256'],
            'producer': {'pid': creator.pid, 'created': creator.create_time(),
                         'exe': creator.exe(), 'argv': creator.cmdline()},
            'scope': 'synthetic-oracle-only-not-a-principal-lineage'}))
        origin = {'kind': 'synthetic-jsonl-session-meta-producer', 'creator_pid_claim': os.getpid() + (1 if arguments[0] == 'creator' else 0),
                  'script_sha256': identities['script']['sha256'], 'synthetic_identity': marker,
                  'input': document['files']['stage-1'], 'output': stable_file(output)[1]}
        (root / 'metadata-origins.jsonl').write_text(json.dumps(origin) + '\n')
        denied = False
        try:
            verify_synthetic_sources(root, workspace, sid, 1)
        except PermissionError:
            denied = True
        assert denied == (arguments[0] != 'valid')
        (root / 'metadata-proof.json').write_text(json.dumps({'denied': denied,
            'scope': 'synthetic-output-oracle-only', 'actual_peer_native_observer_launched': False}))
        return
    if mode == 'fd-probe':
        import ctypes
        import errno
        descriptor = int(arguments[0])
        libc = ctypes.CDLL(None, use_errno=True)
        buffer = ctypes.create_string_buffer(1)
        count = libc.read(descriptor, buffer, 1)
        error = ctypes.get_errno()
        assert count < 0 and error == errno.EBADF, (count, error)
        (root / 'fd-proof.json').write_text(json.dumps({'closed_before_imports': True, 'observed': False}))
        return
    if mode == 'admission-probe':
        from tests.cli_regression.support.fixture_exec import admit_fixture_command
        from agent_team_backend import cli_runtime
        print('SYNTHETIC_ADMISSION_READY', flush=True)
        assert sys.stdin.readline().strip() == 'CHECK'
        operation = arguments[0]
        argv, env = ['/bin/bash', '-lc', 'codex --guard-probe'], dict(os.environ)
        if operation == 'path':
            env['PATH'] = str(root / 'unknown-SYNTHETIC-path')
        elif operation == 'unknown':
            argv = [str(root / 'unrecognized-SYNTHETIC')]
        elif operation == 'substitution':
            argv[2] = 'codex $(unrecognized-SYNTHETIC)'
        try:
            if operation == 'runtime':
                os.environ['NAVIDE_CLI_RUNTIME_BIN'] = str(root / 'unrecognized-SYNTHETIC-runtime')
                cli_runtime.runtime_path()
            else:
                admit_fixture_command(root, argv, env)
        except PermissionError:
            (root / 'admission-proof.json').write_text(json.dumps({'denied': True, 'executed': False}))
        else:
            raise AssertionError('unexpected changed-chain admission')
        return
    if mode == 'shell-probe':
        shell_probe(root)
        return
    if mode == 'loopback-probe':
        with socket.socket() as server, socket.socket() as client:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            client.connect(server.getsockname())
            with server.accept()[0] as connection:
                client.sendall(b'SYNTHETIC_LOOPBACK_ONLY')
                echo = connection.recv(128).decode()
        (root / 'loopback-proof.json').write_text(json.dumps({'echo': echo}))
        return
    if mode == 'driver':
        module = 'tests.cli_regression.support.native_difference_driver'
        sys.argv = [module, str(root), *arguments]
    elif mode == 'backend':
        module = 'tests.cli_regression.support.backend_entry'
        sys.argv = [module, *arguments]
    else:
        raise PermissionError('unregistered bootstrap mode')
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__':
    main()
