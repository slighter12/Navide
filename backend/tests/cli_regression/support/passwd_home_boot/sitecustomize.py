"""Subprocess bootstrap for the launch snapshots: the passwd home is the fixture home.

The product resolves the account home from the passwd entry, not from $HOME
(mcp_server/pane_home.real_home, skills_wiring.real_home, ...). This module sits
alone in a directory that the snapshot test prepends to the backend's
PYTHONPATH, so the interpreter imports it at startup, before any application
import, and the normal backend entry then runs unchanged. It is inert unless
NAVIDE_SNAPSHOT_PASSWD_HOME is set, and it removes its own traces (that
variable and its PYTHONPATH entry) so the launched children never see them.
"""
import os
import pwd

_home = os.environ.pop("NAVIDE_SNAPSHOT_PASSWD_HOME", "")
if _home:
    _record = pwd.struct_passwd(("navide-fixture", "x", os.getuid(), os.getgid(), "", _home, "/bin/sh"))
    pwd.getpwuid = lambda _uid: _record
    pwd.getpwnam = lambda _name: _record
    pwd.getpwall = lambda: [_record]
    _here = os.path.dirname(os.path.abspath(__file__))
    _rest = [part for part in os.environ.get("PYTHONPATH", "").split(os.pathsep) if part and os.path.abspath(part) != _here]
    if _rest:
        os.environ["PYTHONPATH"] = os.pathsep.join(_rest)
    else:
        os.environ.pop("PYTHONPATH", None)
