"""Finite ROOT-owned public OS inputs for the macOS difference fixture only."""
import os
from pathlib import Path
import stat

from fixture_exec import stable_file

SELECTOR = Path('/private/var/select/sh')
PATH_ENTRIES = ('10-cryptex', '10-pmk-global', '100-rvictl', 'homebrew')
PUBLIC_FILES = ('profile', 'bashrc', 'paths')
FIELDS = ('dev', 'ino', 'mode', 'uid', 'gid', 'size', 'mtime_ns', 'ctime_ns')


def root_node(path: Path) -> dict:
    """Stable no-follow metadata/readlink; never follow an unknown OS route."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_DIRECTORY | 0x40000000
    fd = os.open('/', flags)
    opened = [(fd, os.fstat(fd), None, None)]
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags, dir_fd=fd)
            before = os.fstat(child)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o022:
                raise PermissionError('unsafe public OS ancestor')
            opened.append((child, before, fd, component))
            fd = child
        before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if before.st_uid != 0 or (not stat.S_ISLNK(before.st_mode) and before.st_mode & 0o022):
            raise PermissionError('unsafe public OS input')
        target = os.readlink(path.name, dir_fd=fd) if stat.S_ISLNK(before.st_mode) else None
        for node, original, parent, name in opened:
            current = os.fstat(node)
            if (original.st_dev, original.st_ino, original.st_mode, original.st_uid) != (
                    current.st_dev, current.st_ino, current.st_mode, current.st_uid):
                raise PermissionError('changed public OS ancestor')
            if parent is not None:
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino, named.st_mode, named.st_uid) != (
                        original.st_dev, original.st_ino, original.st_mode, original.st_uid):
                    raise PermissionError('changed public OS ancestor name')
        after = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if any(getattr(before, 'st_' + key) != getattr(after, 'st_' + key) for key in FIELDS):
            raise PermissionError('changed public OS input')
        if target is not None and os.readlink(path.name, dir_fd=fd) != target:
            raise PermissionError('changed public OS link')
        return {'path': str(path), 'metadata': {key: getattr(before, 'st_' + key) for key in FIELDS},
                **({'target': target} if target is not None else {})}
    finally:
        for node, *_ in reversed(opened):
            os.close(node)


def root_file(path: Path) -> dict:
    before = root_node(path)
    if not stat.S_ISREG(before['metadata']['mode']):
        raise PermissionError('unregistered public OS file type')
    identity = stable_file(path)[1]
    if any(identity[key] != before['metadata'][key] for key in ('dev', 'ino', 'mode', 'size', 'mtime_ns', 'ctime_ns')):
        raise PermissionError('changed public OS hash input')
    if root_node(path) != before:
        raise PermissionError('changed public OS hash route')
    return {'node': before, 'identity': identity}


def public_selector() -> dict:
    link = root_node(SELECTOR)
    if link.get('target') != '/bin/bash':
        raise PermissionError('unregistered configured shell selector')
    target = root_file(Path('/bin/bash'))
    if root_node(SELECTOR) != link:
        raise PermissionError('changed configured shell selector')
    return {'link': link, 'target': target,
            'ancestors': [root_node(Path(path)) for path in ('/private', '/private/var', '/private/var/select')]}


def public_startup() -> dict:
    """Literal public roster: never learn grants from arbitrary disk entries."""
    alias = root_node(Path('/etc'))
    if alias.get('target') != 'private/etc':
        raise PermissionError('unregistered public etc alias')
    directory = Path('/private/etc/paths.d')
    before = root_node(directory)
    if not stat.S_ISDIR(before['metadata']['mode']):
        raise PermissionError('unregistered public paths directory')
    if tuple(sorted(os.listdir(directory))) != tuple(sorted(PATH_ENTRIES)):
        raise PermissionError('unregistered public path-helper membership')
    files = {str(Path('/private/etc') / name): root_file(Path('/private/etc') / name)
             for name in PUBLIC_FILES}
    for name in PATH_ENTRIES:
        path = directory / name
        files[str(path)] = root_file(path)
    helper = Path('/usr/libexec/path_helper')
    files[str(helper)] = root_file(helper)
    if (tuple(sorted(os.listdir(directory))) != tuple(sorted(PATH_ENTRIES))
            or root_node(directory) != before or root_node(Path('/etc')) != alias):
        raise PermissionError('changed public path-helper roster')
    return {'etc_alias': alias, 'directory': before, 'members': list(PATH_ENTRIES),
            'files': files, 'ancestors': [root_node(Path(path)) for path in
                ('/private/etc', '/usr', '/usr/libexec')]}


def managed_branch_observer(root: Path, interpreter: str, descriptor: dict | None = None) -> str:
    """OWN source-bound branch observation; no commands or credentials in output."""
    import json
    import shlex
    from managed_opencode_admission import _descriptor
    from fixture_exec import stable_file
    descriptor = descriptor or _descriptor(root, interpreter, ('opencode',))
    wrapper = descriptor['forms'][0][2]
    prefix = 'if [ "$(type -t -- opencode)" = file ]; then if '
    if not wrapper.startswith(prefix):
        raise PermissionError('changed managed branch source')
    helper = wrapper[len(prefix):wrapper.index(' --shell-check -- ', len(prefix))]
    check = helper + ' --shell-check -- "$(command -v -- opencode)"'
    sources = {'builder': descriptor['sources']['builder']['sha256'],
               'helper': descriptor['sources']['helper']['sha256'],
               'startup': stable_file(Path(__file__).absolute())[1]['sha256']}
    payload = json.dumps({'version': 1, 'root': str(root), 'sources': sources}, sort_keys=True)
    payload = payload[:-1] + ',"shell_pid":%s,"parent":%s,"branch":"%s","exit_class":%s}\\n'
    template = r'''
_navide_fixture_branch() {
  local _status="$1" _command="$2" _mode _code _options
  case "$_command" in
    __CHECK__|__CHECK_SPACE__*) _navide_fixture_phase=checking ;;
    '_navide_credential_status=$?')
      if [[ ${_navide_fixture_phase-} == checking && $_status == 76 ]]; then
        _navide_fixture_exit=76; _navide_fixture_phase=checked
      fi ;;
    'unset NAVIDE_CREDENTIAL_LAUNCH_PORT NAVIDE_CREDENTIAL_LAUNCH_TOKEN')
      if [[ ${_navide_fixture_phase-} == checking && $_status == 0 ]]; then
        _navide_fixture_exit=0; _navide_fixture_phase=checked
      fi ;;
    'exec opencode'|'exec opencode '*)
      [[ ${_navide_fixture_phase-} == checked && ${_navide_fixture_exit-} == 0 ]] || return 0
      _mode=shell-check-exec; _code=0 ;;
    'opencode'|'opencode '*)
      [[ ${_navide_fixture_phase-} == checked && ${_navide_fixture_exit-} == 76 && ${_navide_credential_status-} == 76 ]] || return 0
      _mode=shell-check-unverified; _code=76 ;;
  esac
  if [[ -n ${_mode-} ]]; then
    _options="$-"; set -C
    printf __PAYLOAD__ "$$" "$PPID" "$_mode" "$_code" > __DESTINATION__ || exit 75
    [[ $_options == *C* ]] || set +C
    trap - DEBUG
  fi
  return 0
}
trap '_navide_fixture_branch "$?" "$BASH_COMMAND"' DEBUG
'''
    return (template.replace('__CHECK__', shlex.quote(check))
        .replace('__CHECK_SPACE__', shlex.quote(check + ' '))
        .replace('__PAYLOAD__', shlex.quote(payload))
        .replace('__DESTINATION__', shlex.quote(str(root / 'managed-branch-')) + '"$$".json'))


def shell_profile(root: Path, vendor: str, paths: str, *, document: dict) -> str:
    """Real Bash builtins only, after public login startup and before CLI lookup."""
    import shlex
    if vendor not in {'codex', 'opencode'} or any(char in str(root) + paths for char in '\"\\\\\r\n'):
        raise PermissionError('unregistered synthetic shell input')
    if document.get('root') != str(root):
        raise PermissionError('different shell profile document root')
    expected = shlex.quote(str(root / vendor))
    target = shlex.quote(str(root)) + '/shell-resolution-$$.json'
    observer = ''
    if vendor == 'opencode':
        from native_file_guard import principal_manifest_runtime
        if principal_manifest_runtime(root, document) is not None:
            import sys
            observer = managed_branch_observer(root, sys.executable)
    return ('export PATH=' + shlex.quote(paths) + '\n'
            '[[ -z ${PS1-} && -z ${TERM_PROGRAM-} ]] || exit 71\n'
            'resolved=$(type -P -- ' + vendor + ') || exit 72\n'
            '[[ "$resolved" == ' + expected + ' ]] || exit 73\n'
            "printf '{\"shell_pid\":%s,\"parent\":%s,\"path\":\"%s\",\"resolved\":\"%s\","
            "\"PS1_empty\":true,\"TERM_PROGRAM_empty\":true}\\n' "
            '"$$" "$PPID" "$PATH" "$resolved" > ' + target + '\n' + observer)


def startup_read_paths(roster: dict) -> list[str]:
    # Caller validates the whole roster against public_startup before use.
    return [*roster['files'], '/etc/profile', '/etc/bashrc', '/etc/paths',
            '/private/etc/paths.d', '/etc/paths.d',
            *(str(Path('/etc/paths.d') / name) for name in PATH_ENTRIES)]
