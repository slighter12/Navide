"""Bounded, retained-descriptor snapshots of OWN synthetic native receipts."""
import hashlib
import json
import os
from pathlib import Path
import stat

from fixture_exec import metadata_directory_flags


class EvidenceMoved(RuntimeError):
    """The live writer changed the snapshot; this is not an observation fence."""


def _state(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _read(path, limit, consume):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise PermissionError('non-canonical usage evidence path')
    held = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_flags = metadata_directory_flags()
    try:
        fd = os.open('/', directory_flags)
        held.append((fd, os.fstat(fd), None))
        for index, part in enumerate(path.parts[1:]):
            leaf = index == len(path.parts) - 2
            fd = os.open(part, flags if leaf else directory_flags, dir_fd=held[-1][0])
            before = os.fstat(fd)
            held.append((fd, before, part))
        fd, before, _ = held[-1]
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise PermissionError('usage evidence type/size bound')
        chunks, count = [], 0
        while True:
            piece = os.read(fd, min(65536, limit + 1 - count))
            if not piece:
                break
            count += len(piece)
            if count > limit:
                raise PermissionError('usage evidence grew beyond bound')
            chunks.append(piece)
        data = b''.join(chunks)
        def verify():
            for index, (descriptor, snapshot, name) in enumerate(held):
                if _state(os.fstat(descriptor)) != _state(snapshot):
                    raise EvidenceMoved('usage evidence descriptor changed')
                if index and _state(os.stat(name, dir_fd=held[index - 1][0], follow_symlinks=False)) != _state(snapshot):
                    raise EvidenceMoved('usage evidence ancestry name changed')
        verify()
        identity = {'path': str(path), 'dev': before.st_dev, 'ino': before.st_ino,
                    'mode': before.st_mode, 'size': before.st_size,
                    'mtime_ns': before.st_mtime_ns, 'ctime_ns': before.st_ctime_ns,
                    'sha256': hashlib.sha256(data).hexdigest()}
        value = consume(data)
        verify()  # Hold every ancestor and the leaf through JSON parsing.
        return value, identity
    finally:
        for fd, _, _ in reversed(held):
            os.close(fd)


def usage_evidence(root):
    def parse(data):
        complete = data[:data.rfind(b'\n') + 1]
        return [{'index': index, 'record': record}
                for index, record in enumerate(json.loads(line) for line in complete.splitlines())
                if record.get('parser') == 'rust-opencode-sqlite-v1' and record.get('mode') == 'usage']
    try:
        usage, identity = _read(root / 'runtime-receipts.jsonl', 1048576, parse)
        _, database = _read(root / 'share/opencode/opencode.db', 8388608, lambda data: None)
    except (FileNotFoundError, EvidenceMoved):
        return None
    return {'source': identity, 'database': database, 'usage': usage}
