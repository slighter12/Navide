"""t14 item 5: the Linux G2 build step states whether `/proc/<pid>/task/<tid>/children` exists, and fails when it does not.

The sidecar's Linux scoped ownership mode needs that file (CONFIG_PROC_CHILDREN; `native/navide-cli-runtime/src/linux.rs:1188`). Without it the sidecar silently falls back to the full /proc scan,
so a hosted run could be measured in the wrong mode with nothing in the log naming the cause. `tests/cli/g2-linux-proc-children.sh` is called first by `tests/cli/g2-linux-build.sh`, which both
G2 Linux workflows run.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tests" / "cli" / "g2-linux-proc-children.sh"
BUILD = REPO / "tests" / "cli" / "g2-linux-build.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run(override: Path | None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin"}
    if override is not None:
        env["G2_PROC_CHILDREN_FILE_FOR_TEST"] = str(override)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_a_readable_children_file_is_named_in_the_log(tmp_path: Path) -> None:
    present = tmp_path / "children"
    present.write_text("")
    done = run(present)
    assert done.returncode == 0, done.stderr
    assert f"proc children: present: {present}" in done.stdout


def test_a_missing_children_file_fails_the_run_and_says_why(tmp_path: Path) -> None:
    done = run(tmp_path / "no-such-children")
    assert done.returncode != 0
    assert "proc children: MISSING" in done.stderr
    assert "CONFIG_PROC_CHILDREN" in done.stderr


@pytest.mark.skipif(not Path("/proc/self/task").exists(), reason="Linux only")
def test_on_linux_the_real_children_file_passes() -> None:
    done = run(None)
    assert done.returncode == 0, done.stderr
    assert "proc children: present: /proc/" in done.stdout


def test_the_build_step_runs_the_check_before_it_builds() -> None:
    text = BUILD.read_text()
    assert "g2-linux-proc-children.sh" in text
    assert text.index("g2-linux-proc-children.sh") < text.index("cargo build")
