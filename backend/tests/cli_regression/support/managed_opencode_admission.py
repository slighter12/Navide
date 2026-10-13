"""Finite, byte-anchored production wrapper forms for the principal fixture."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

from fixture_exec import stable_file
from usage_evidence_snapshot import _read

BACKEND = Path(__file__).absolute().parents[3]
SOURCE_PATHS = {
    'builder': BACKEND / 'agent_team_backend/credential_launch.py',
    'helper': BACKEND / 'agent_team_backend/credential_path_helper.py',
    'base': BACKEND / 'agent_team_backend/cli_vendors/base.py',
    'vendor': BACKEND / 'agent_team_backend/cli_vendors/opencode.py',
    'paths': BACKEND / 'agent_team_backend/osplat/_posix_paths.py',
    'terminal': BACKEND / 'agent_team_backend/osplat/_posix.py',
    'push': BACKEND / 'agent_team_backend/push_delivery.py',
}


def _descriptor(root, interpreter, commands):
    """Extract only original pure builder bodies; never start credentials."""
    trees, identities = {}, {}
    for name, path in SOURCE_PATHS.items():
        tree, identity = _read(path, 65536, ast.parse)
        trees[name], identities[name] = tree, identity

    def definitions(name, names, scope):
        selected = [n for n in ast.walk(trees[name])
                    if isinstance(n, ast.FunctionDef) and n.name in names]
        if len(selected) != len(names):
            raise PermissionError('changed managed builder definitions')
        module = ast.Module(body=[ast.ImportFrom(module='__future__',
            names=[ast.alias(name='annotations')], level=0), *selected], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(SOURCE_PATHS[name]), 'exec'), scope)

    base = {'Path': Path, 'shlex': shlex}
    definitions('base', ('command_text', 'simple_command_args'), base)
    paths = {'shlex': shlex}
    definitions('paths', ('home_env_var',), paths)
    terminal = next(n for n in trees['terminal'].body
                    if isinstance(n, ast.ClassDef) and n.name == 'PosixTerminalBackend')
    waits = next(n.value for n in terminal.body if isinstance(n, ast.AnnAssign)
                 and isinstance(n.target, ast.Name) and n.target.id == 'helper_waits_for_child')
    if ast.literal_eval(waits) is not False:
        raise PermissionError('changed POSIX managed helper declaration')
    spec = next(n.value for n in trees['vendor'].body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'SPEC' for t in n.targets))
    fields = {k.arg: k.value for k in spec.keywords}
    switch = {k.arg: k.value for k in fields['account_switch'].keywords}
    if 'portable_credential' in fields or 'credential_env_vars' in switch:
        raise PermissionError('changed OpenCode credential declaration')
    names = tuple(dict.fromkeys(name for _, values in
        ast.literal_eval(switch['uncertain_env_by_scope']) for name in values))
    key = ast.literal_eval(fields['key'])
    if key != 'opencode':
        raise PermissionError('changed managed vendor')
    scope = {'Path': Path, 'sys': SimpleNamespace(executable=str(interpreter), frozen=False),
             'shlex': shlex, '__file__': str(SOURCE_PATHS['builder']),
             'simple_command_args': base['simple_command_args'],
             'osplat': SimpleNamespace(paths=SimpleNamespace(home_env_var=paths['home_env_var']),
                                      terminal_backend=SimpleNamespace(helper_waits_for_child=False))}
    for n in trees['helper'].body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in {
                'HELPER_FLAG', 'PORT_ENV', 'TOKEN_ENV'}:
            scope[n.targets[0].id] = ast.literal_eval(n.value)
    definitions('builder', ('helper_argv', 'wrap_command'), scope)
    start = next(n for n in ast.walk(trees['builder'])
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == 'start')
    actor = SimpleNamespace(_timeout=ast.literal_eval(start.args.kw_defaults[0]),
                           _path_inputs=ast.literal_eval(fields['credential_path_env_vars']))
    helper = scope['helper_argv'](actor, login=False, vendor=key, credential_env=names)
    # Actual caller: nativeDifferences.test.ts:449-477, 525; fixed SID: driver:138.
    # The restored default --session spelling is never sent by this principal.
    forms = [scope['wrap_command'](['/bin/bash', '-lc', word], SimpleNamespace(key=key), helper)
             for word in commands]
    return {'version': 1, 'scope': 't04-managed-opencode-admission', 'root': str(root),
            'interpreter': str(interpreter), 'helper': identities['helper'],
            'sources': identities, 'forms': forms, 'commands': list(commands)}


def descriptor(root, interpreter):
    return _descriptor(root, interpreter, ('opencode', 'opencode -s ses_native_difference'))


def _registered_push_descriptor(root, form):
    """Exact forms for the one channel in this already-validated OWN backend."""
    from agent_team_backend import push_delivery

    if stable_file(Path(push_delivery.__file__))[1] != form['sources']['push']:
        raise PermissionError('changed registered push source')
    channels = [(pane, state) for pane, state in push_delivery._panes.items()
                if state.agent_key == 'opencode']
    if not channels:
        return {'forms': [], 'commands': []}
    if len(channels) != 1:
        raise PermissionError('ambiguous registered OpenCode channel')
    pane, state = channels[0]
    declared = push_delivery.registry.vendor('opencode')
    if (declared is None or state.channel != declared.push_channel
            or not pane or state.pane_id != pane or state.kind != push_delivery.KIND_HTTP
            or type(state.port) is not int or not 1 <= state.port <= 65535
            or state.channel.port_flag != '--port'
            or state.channel.host_flag != '--hostname'
            or state.channel.host != '127.0.0.1'):
        raise PermissionError('unregistered OpenCode channel metadata')
    commands = tuple(command + f' --port {state.port} --hostname 127.0.0.1'
                     for command in ('opencode', 'opencode -s ses_native_difference'))
    return _descriptor(root, sys.executable, commands)


def write_descriptor(root, interpreter):
    payload = json.dumps(descriptor(root, interpreter), sort_keys=True).encode()
    path = root / 'managed-opencode.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        for start in range(0, len(payload), 4096):
            part = memoryview(payload)[start:start + 4096]
            while part:
                n = os.write(fd, part)
                if n <= 0:
                    raise OSError('managed descriptor write stalled')
                part = part[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def validate_self(root, document, metadata, observation):
    """Compare a fresh SELF observation to the existing pre-app birth receipt."""
    source = document['files']['account_bootstrap']
    image = document['files']['interpreter']
    if (observation is None or observation['role'] != 'backend'
            or observation['phase'] != 'self-before-app'
            or observation['source'] != source or metadata['image'] != image
            or metadata['exe'] != image['path']
            or any(observation[k] != metadata[k] for k in ('pid', 'created', 'parent', 'exe', 'image'))):
        raise PermissionError('managed wrapper SELF mismatch')
    entries = {image['path'], str(BACKEND / '.venv/bin/python')}
    argv = observation['argv']
    if (not argv or argv[0] not in entries
            or argv[:6] != [argv[0], '-I', '-S', source['path'], 'backend', str(root)]):
        raise PermissionError('managed wrapper SELF root or entry mismatch')


def admit(root, argv, env, document):
    """Additional form only; kernel validation and bare admission stay real."""
    from .native_file_guard import principal_manifest_runtime
    from .process_env_guard import installed
    from .process_argv_observation import correlate_observation
    import psutil
    if principal_manifest_runtime(root, document) is None:
        raise PermissionError('managed wrapper requires principal binding')
    guard = installed()
    if guard is None or guard.root != root:
        raise PermissionError('managed wrapper requires installed SELF guard')
    guard.check(os.getpid(), 'managed-wrapper-admission')
    actor = psutil.Process(os.getpid())
    metadata = {'pid': actor.pid, 'created': actor.create_time(), 'parent': actor.ppid(),
                'exe': actor.exe(), 'image': stable_file(Path(actor.exe()))[1]}
    validate_self(root, document, metadata, correlate_observation(root, metadata))
    payload, _ = _read(root / 'exec-identities.json', 65536, lambda b: b)
    if hashlib.sha256(payload).hexdigest() != os.environ.get('NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256'):
        raise PermissionError('managed wrapper exec anchor mismatch')
    identities = json.loads(payload)
    identity = identities.get('managed-wrapper')
    if identity is None or identity['path'] != str(root / 'managed-opencode.json'):
        raise PermissionError('managed wrapper missing descriptor role')
    form, actual = _read(Path(identity['path']), 65536, json.loads)
    if actual != identity or form != descriptor(root, sys.executable):
        raise PermissionError('changed managed wrapper descriptor')
    selected = form
    if argv not in selected['forms']:
        selected = _registered_push_descriptor(root, form)
    if argv not in selected['forms']:
        raise PermissionError('unregistered managed wrapper form')
    # Reuse every original shim/interpreter/profile/forward-chain check.
    from .fixture_exec import admit_fixture_command
    result = admit_fixture_command(root, ['/bin/bash', '-lc', 'opencode'], env)
    command = selected['commands'][selected['forms'].index(argv)]
    peer_argv = [identities['interpreter']['path'], '-I', '-S', '-B', '-u',
                 identities['script']['path'], str(root), 'opencode', *shlex.split(command)[1:]]
    intent = {'version': 1, 'root': str(root), 'backend_pid': metadata['pid'],
              'backend_created': metadata['created'], 'descriptor_sha256': identity['sha256'],
              'guard_sha256': hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
              'peer_argv': peer_argv,
              'validated_chain': {role: identities[role] for role in ('interpreter', 'profile', 'script', 'shim')},
              'sources': {'builder': form['sources']['builder']['sha256'],
                  'helper': form['sources']['helper']['sha256'],
                  'startup': document['files']['os_startup']['sha256']}}
    payload = json.dumps(intent, sort_keys=True).encode()
    fd = os.open(root / f'managed-intent-{metadata["pid"]}.json',
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        for start in range(0, len(payload), 4096):
            part = memoryview(payload)[start:start + 4096]
            while part:
                n = os.write(fd, part)
                if n <= 0:
                    raise OSError('managed intent write stalled')
                part = part[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return dict(result, admission_form='manifest-bound-managed-opencode')
