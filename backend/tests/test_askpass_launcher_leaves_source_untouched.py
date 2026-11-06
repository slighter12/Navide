"""Importing `git_service` must not touch the checked-in askpass helper when it is already executable.

`git_service` resolves its GIT_ASKPASS path at import time, and from a source checkout that path is the helper file in the
package itself. `chmod` updates a file's ctime even when the mode does not change, so every worker that first imported
`git_service` bumped the ctime of a tracked source file. The isolation-guard fixtures record the identity (mode, mtime, ctime)
of that file and refuse to run if it changed ("changed guarded source identity: askpass"), which made them fail in a full
parallel run and pass alone.
"""
from __future__ import annotations

import os
import stat
import sys

import pytest

from agent_team_backend import osplat

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")


def test_an_already_executable_helper_keeps_its_ctime(tmp_path):
    helper = tmp_path / "git_askpass_helper.py"
    helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    helper.chmod(0o755)
    before = os.stat(helper)

    assert osplat.paths.askpass_launcher(helper, [sys.executable]) == helper

    after = os.stat(helper)
    assert (after.st_mode, after.st_ctime_ns, after.st_mtime_ns) == (before.st_mode, before.st_ctime_ns, before.st_mtime_ns)


def test_a_helper_missing_its_execute_bits_still_gets_them(tmp_path):
    helper = tmp_path / "git_askpass_helper.py"
    helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    helper.chmod(0o644)

    osplat.paths.askpass_launcher(helper, [sys.executable])

    assert stat.S_IMODE(os.stat(helper).st_mode) == 0o755
