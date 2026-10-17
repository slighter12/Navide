"""Fixed OWN observations; SELF payload or an unpublished NEW direct child only."""
import hashlib
import json
import os
from pathlib import Path

from .fixture_exec import stable_bytes, stable_file
from .process_env_guard import match_runtime_launch


def write_observation(root, name, row):
    pending = root / ('.' + name + '.pending')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        data = (json.dumps(row) + '\n').encode()
        assert os.write(fd, data) == len(data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.link(pending, root / name, follow_symlinks=False)
    pending.unlink()


def _peer_vendor(root, document):
    """Use only the registered, admitted fixture shim and its exact role pins."""
    from .fixture_exec import admit_fixture_command

    payload = stable_bytes(root / 'exec-identities.json')
    if hashlib.sha256(payload).hexdigest() != os.environ.get('NAVIDE_REGRESSION_EXEC_MANIFEST_SHA256'):
        raise PermissionError('unbound peer fixture manifest')
    identities = json.loads(payload)
    vendor = Path(identities['shim']['path']).name
    if (vendor not in {'codex', 'opencode'}
            or document['root'] != str(root)
            or identities['shim']['path'] != str(root / vendor)
            or identities['script'] != document['files']['native_difference_peer']
            or identities['interpreter'] != document['files']['interpreter']):
        raise PermissionError('changed peer fixture role binding')
    admit_fixture_command(root, [identities['shim']['path']], {})
    return vendor


def observe_self(root, role):
    import psutil
    from .difference_entry import validate_kernel_setup
    from .process_env_guard import installed
    guard = installed()
    guard.check(os.getpid(), 'argv-self-observation')
    document = validate_kernel_setup(root)
    actor = psutil.Process(os.getpid())
    argv = actor.cmdline()  # real pinned C getter, never sys.argv/launch intent
    image = document['files']['interpreter']
    if actor.exe() != image['path'] or stable_file(Path(actor.exe()))[1] != image:
        raise PermissionError('unregistered SELF observation image')
    source_role = 'native_difference_peer' if role == 'peer' else 'account_bootstrap'
    source = document['files'][source_role]
    python_entries = {image['path'], str(Path(__file__).resolve().parents[3] / '.venv/bin/python')}
    peer_vendor = _peer_vendor(root, document) if role == 'peer' else None
    prefix = ([argv[0], '-I', '-S', '-B', '-u', source['path'], str(root), peer_vendor]
              if role == 'peer' else [argv[0], '-I', '-S', source['path'], role, str(root)])
    if role not in {'driver', 'backend', 'peer', 'process-env-probe'} or argv[0] not in python_entries or argv[:len(prefix)] != prefix:
        raise PermissionError('unregistered SELF script role')
    row = {'pid': actor.pid, 'created': actor.create_time(), 'parent': actor.ppid(),
           'exe': actor.exe(), 'image': image, 'argv': argv, 'role': role,
           'source': source, 'phase': ('control-self' if role == 'process-env-probe' else
               'self-before-app' if role != 'peer' else 'self-producer')}
    header = {key: row[key] for key in ('pid', 'created', 'parent', 'exe', 'image', 'role', 'source', 'phase')}
    write_observation(root, f'self-admission-{actor.pid}.json', header)
    write_observation(root, f'self-process-{actor.pid}.json', row)
    return row


def admit_bootstrap_owner(root, pid, parent, role='driver'):
    """Metadata-only pre-admission, then the previously emitted OWN SELF payload."""
    import psutil
    from psutil import _psosx
    actor = psutil.Process(pid)
    if actor.ppid() != parent:
        raise PermissionError('unregistered bootstrap parent')
    header = json.loads(stable_bytes(root / f'self-admission-{pid}.json'))
    document = json.loads(stable_bytes(root / 'guard-identities.json'))
    if (header['pid'] != pid or header['parent'] != parent or header['created'] != actor.create_time()
            or header['role'] != role or role not in {'driver', 'process-env-probe'}
            or header['source'] != document['files']['account_bootstrap']
            or header['image'] != document['files']['interpreter']
            or header['exe'] != _psosx.Process(pid).exe()):
        raise PermissionError('missing stale or invalid bootstrap admission')
    row = correlate_observation(root, header)
    if row is None or row['role'] != role:
        raise PermissionError('missing bootstrap SELF observation')
    return header


def validate_recovery_self(root, metadata):
    """Real producer SELF API proof, admitted through metadata before payload."""
    document = json.loads(stable_bytes(root / 'guard-identities.json'))
    header = json.loads(stable_bytes(root / f'self-admission-{metadata["pid"]}.json'))
    required = {'pid': metadata['pid'], 'created': metadata['created'], 'parent': metadata['parent'],
        'exe': metadata['exe'], 'image': document['files']['interpreter'], 'role': 'peer',
        'source': document['files']['native_difference_peer'], 'phase': 'self-producer'}
    if header != required or metadata['image'] != required['image']:
        raise PermissionError('wrong or stale recovery SELF admission')
    row = json.loads(stable_bytes(root / 'ownership-command-self.json'))
    if any(row.get(key) != value for key, value in required.items()):
        raise PermissionError('wrong or stale recovery SELF proof')
    argv = row.get('argv')
    entries = {required['image']['path'], str(Path(__file__).resolve().parents[3] / '.venv/bin/python')}
    prefix = [argv[0], '-I', '-S', '-B', '-u', required['source']['path'], str(root), 'codex'] if isinstance(argv, list) and argv else []
    if (not prefix or argv[0] not in entries or argv[:8] != prefix
            or len(argv) != 9 or argv[7] != 'codex'
            or argv[8] not in ('--ownership-probe', '--orphan-ownership-probe', '--ownership-self-probe')
            or row.get('command_of') != ' '.join(argv)):
        raise PermissionError('invalid positive recovery SELF API')
    return row


def _runtime_self_row(root, ready, metadata):
    """Consume actual native SELF payload; metadata is correlation, never a grant."""
    document = json.loads(stable_bytes(root / 'guard-identities.json'))
    self_row = ready.get('self_process')
    if ready.get('self_process_error') or not isinstance(self_row, dict):
        raise PermissionError('missing native SELF evidence')
    if (ready.get('event') != 'ready' or ready.get('protocol') != 'navide-acr' or ready.get('wire') != 1
            or ready.get('pid') != metadata['pid'] or ready.get('owner_pid') != metadata['parent']
            or self_row.get('pid') != metadata['pid'] or self_row.get('parent') != metadata['parent']
            or self_row.get('executable') != metadata['exe']
            or metadata['image'] != document['files']['runtime']):
        raise PermissionError('invalid native SELF origin')
    seconds, micros = self_row.get('birth_seconds'), self_row.get('birth_microseconds')
    if (type(seconds) is not int or type(micros) is not int or seconds <= 0
            or not 0 <= micros < 1_000_000 or seconds + micros / 1_000_000 != metadata['created']):
        raise PermissionError('stale native SELF birth')
    generation = ready.get('runtime_generation')
    argv = self_row.get('argv')
    # The one wire-v1 launch form (process_env_guard.match_runtime_launch); the control descriptor's number is the only
    # free element and is only checked for shape here (its socket nature is checked at spawn, before the child exists).
    if (not isinstance(generation, str) or match_runtime_launch(
            argv, metadata['exe'], metadata['parent'], root / 'data') is None
            or argv[4] != generation):
        raise PermissionError('invalid native SELF argv generation')
    for role in ('native-darwin', 'native-posix', 'runtime'):
        identity = document['files'][role]
        if stable_file(Path(identity['path']))[1] != identity:
            raise PermissionError('changed native SELF source or image')
    row = {**{key: metadata[key] for key in ('pid', 'created', 'parent', 'exe', 'image')},
           'argv': self_row['argv'], 'role': 'runtime',
           'phase': 'runtime-self-ready', 'runtime_generation': generation,
           'source': {role: document['files'][role] for role in ('native-darwin', 'native-posix')}}
    return row


def observe_runtime_ready(root, ready, metadata):
    row = _runtime_self_row(root, ready, metadata)
    write_observation(root, f'runtime-self-process-{metadata["pid"]}.json', row)
    return row


def read_runtime_receipts(root):
    """Complete private NDJSON only; retain the growing tail and all complete errors."""
    path = root / 'runtime-receipts.jsonl'
    if not path.exists():
        return []
    data = stable_bytes(path)
    complete = data[:data.rfind(b'\n') + 1]
    return [json.loads(line) for line in complete.splitlines()]


def consume_runtime_ready(root, metadata):
    """Existing private NDJSON receipt; accept only complete newline records."""
    candidates = [event for event in read_runtime_receipts(root)
                  if event.get('event') == 'ready' and event.get('pid') == metadata['pid']]
    if len(candidates) > 1:
        raise PermissionError('ambiguous native ready origin')
    if not candidates:
        return None
    path = root / f'runtime-self-process-{metadata["pid"]}.json'
    expected = _runtime_self_row(root, candidates[0], metadata)
    if not path.exists():
        write_observation(root, path.name, expected)
    observed = correlate_observation(root, metadata)
    if observed != expected:
        raise PermissionError('native ready and delivered SELF proof disagree')
    return observed


def correlate_observation(root, metadata):
    """No late argv read. Metadata correlation is not permission for payload."""
    paths = [root / f'{kind}-process-{metadata["pid"]}.json' for kind in ('self', 'new-child', 'runtime-self')]
    found = [path for path in paths if path.exists()]
    if not found:
        return None  # Explicit not-yet-observed, never invented argv.
    if len(found) > 1:
        if len(found) != 2 or set(found) != {paths[1], paths[2]}:
            raise PermissionError('ambiguous OWN observation')
        constructor, native = (json.loads(stable_bytes(path)) for path in (paths[1], paths[2]))
        if (constructor['phase'] != 'new-child-constructor'
                or any(constructor[key] != native[key] for key in ('pid', 'created', 'parent', 'exe', 'image', 'argv', 'role'))):
            raise PermissionError('constructor and native SELF disagree')
    row = json.loads(stable_bytes(paths[2] if paths[2] in found else found[0]))
    for key in ('pid', 'created', 'parent', 'exe', 'image'):
        if row[key] != metadata[key]:
            raise PermissionError('stale OWN observation metadata')
    document = json.loads(stable_bytes(root / 'guard-identities.json'))
    if row['phase'] in ('self-before-app', 'self-producer', 'control-self'):
        if row['image'] != document['files']['interpreter'] or row['exe'] != row['image']['path']:
            raise PermissionError('changed interpreter image observation')
        role = row['role']
        source_role = 'native_difference_peer' if role == 'peer' else 'account_bootstrap'
        if role not in {'driver', 'backend', 'peer', 'process-env-probe'} or row['source'] != document['files'][source_role]:
            raise PermissionError('invalid OWN script source')
        entries = {row['exe'], str(Path(__file__).resolve().parents[3] / '.venv/bin/python')}
        peer_vendor = _peer_vendor(root, document) if role == 'peer' else None
        prefix = ([row['argv'][0], '-I', '-S', '-B', '-u', row['source']['path'], str(root), peer_vendor]
                  if role == 'peer' else [row['argv'][0], '-I', '-S', row['source']['path'], role, str(root)])
        if row['argv'][0] not in entries or row['argv'][:len(prefix)] != prefix:
            raise PermissionError('invalid OWN script argv')
    elif row['phase'] in ('new-child-constructor', 'runtime-self-ready'):
        if row['phase'] == 'runtime-self-ready' and row['source'] != {
                role: document['files'][role] for role in ('native-darwin', 'native-posix')}:
            raise PermissionError('invalid native SELF source')
        if row['role'] != 'runtime' or row['image'] != document['files']['runtime']:
            raise PermissionError('unregistered created runtime')
        if match_runtime_launch(row['argv'], row['exe'], row['parent'], root / 'data') is None:
            raise PermissionError('invalid created runtime argv')
    else:
        raise PermissionError('unknown OWN observation phase')
    return row
