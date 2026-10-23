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

# G2 ticket 04: records the encoded size of every CREATE request meta to the named file, so the matrix can
# report the largest real CREATE against the frame cap. Inert unless NAVIDE_G2_META_LOG is set; the variable is
# removed here so no child ever sees it.
_meta_log = os.environ.pop("NAVIDE_G2_META_LOG", "")
if _meta_log:
    import importlib.abc
    import importlib.util
    import json
    import sys

    class _MetaProbe(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != "agent_team_backend.cli_runtime_frames":
                return None
            sys.meta_path.remove(self)
            spec = importlib.util.find_spec(name)
            loader, original = spec.loader, spec.loader.exec_module

            def exec_module(module):
                original(module)
                real = module.encode

                def encode(frame, *args, **kwargs):
                    out = real(frame, *args, **kwargs)
                    if frame.frame_type is module.FrameType.REQUEST and frame.code == module.code.CREATE:
                        with open(_meta_log, "a", encoding="utf-8") as handle:
                            handle.write(json.dumps({"meta_bytes": len(out) - module.HEADER_LEN - len(frame.blob),
                                                     "agent_key": frame.meta.get("agent_key"),
                                                     "launch": frame.meta.get("launch")}) + "\n")  # never the env
                    return out

                module.encode = encode

            loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _MetaProbe())
