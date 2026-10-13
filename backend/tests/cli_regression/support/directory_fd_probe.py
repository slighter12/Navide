"""Python audit control only: created directories, no kernel/principal claim."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from account_boundary import install_account_home_guard

root = Path(sys.argv[1])
inside, outside = root / 'inside', root.parent / 'outside-SYNTHETIC'
inside.mkdir()
outside.mkdir()
(outside / 'sentinel-SYNTHETIC').write_text('NEVER_OBSERVED')
fds = [os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) for path in (inside, outside)]
try:
    install_account_home_guard(root)
    assert list(os.scandir(fds[0])) == []
    try:
        list(os.scandir(fds[1]))
    except PermissionError:
        pass
    else:
        raise AssertionError('outside directory descriptor enumeration allowed')
    print('OWN_DIRECTORY_FD_ALLOWED_OUTSIDE_REFUSED')
finally:
    for fd in fds:
        os.close(fd)
