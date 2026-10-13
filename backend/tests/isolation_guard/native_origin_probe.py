"""Safe standalone native syscalls against a synthetic sentinel only."""
import ctypes
import errno
import json
import os
from pathlib import Path
import sys

fixture, physical, operation, *descriptors = sys.argv[1:]
print('SYNTHETIC_NATIVE_GUARD_READY', flush=True)
assert sys.stdin.readline().strip() == 'GO'
libc = ctypes.CDLL(None, use_errno=True)
libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.open.restype = ctypes.c_int
path = Path(physical) / 'sentinel'
if operation.startswith('symlink-'):
    path = Path(fixture) / 'escape' / 'sentinel'
if operation == 'readonly-source-write':
    path = Path(fixture) / 'fixture-marker'
denied = False
observed = False
error = 0
fresh_origin_denied = None
if operation == 'preopened-read':
    fresh = libc.open(os.fsencode(path), os.O_RDONLY)
    fresh_origin_denied = fresh < 0 and ctypes.get_errno() in {errno.EPERM, errno.EACCES}
    assert fresh_origin_denied
    buffer = ctypes.create_string_buffer(1)
    count = libc.read(int(descriptors[0]), buffer, 1)
    error = ctypes.get_errno()
    denied = count < 0 and error in {errno.EPERM, errno.EACCES}
    observed = count >= 0
elif operation in {'read', 'write', 'symlink-read', 'symlink-write', 'dirfd-read', 'readonly-source-write'}:
    writing = operation.endswith('write')
    if operation == 'dirfd-read':
        fd = libc.openat(int(descriptors[0]), b'sentinel', os.O_RDONLY)
    else:
        fd = libc.open(os.fsencode(path), os.O_WRONLY | os.O_TRUNC if writing else os.O_RDONLY)
    error = ctypes.get_errno()
    if fd >= 0:
        if not writing:
            os.read(fd, 1)
        observed = True
        os.close(fd)
    else:
        denied = error in {errno.EPERM, errno.EACCES}
elif operation in {'list', 'scan'}:
    try:
        if operation == 'list':
            os.listdir(physical)
        else:
            with os.scandir(physical) as entries:
                list(entries)
        observed = True
    except PermissionError as exc:
        denied, error = True, exc.errno
else:
    raise AssertionError('unknown synthetic operation')
print(json.dumps({'denied': denied, 'observed': observed, 'errno': error,
                  'operation': operation, 'pid_claim': os.getpid(),
                  'fresh_origin_denied': fresh_origin_denied}), flush=True)
