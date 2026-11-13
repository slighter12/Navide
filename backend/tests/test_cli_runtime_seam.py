"""The seam to the Rust CLI runtime when the process misbehaves.

``test_cli_runtime_parity.py`` shows that the runtime's answers are main's.
This file shows that a process which stalls, dies or is missing can never stop
a reader: the call times out or fails over to the Python reader, the switch
can always be turned off at once, a crash loop is given up on, and
``status()`` says who answered.

The misbehaving processes are small scripts started in place of the binary
(``NAVIDE_CLI_RUNTIME_BIN``).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from agent_team_backend import cli_runtime, osplat
from agent_team_backend.cli_vendors.registry import vendor as cli_vendor
from tests import test_cli_runtime_parity as parity

pytestmark = pytest.mark.skipif(osplat.platform_id == "win32", reason="the stand-in processes are #! scripts")

#: Answers the handshake (it observes qwen), then reads requests and never answers.
STALLS = """
seen = open(sys.argv[0] + ".log", "a")
for line in sys.stdin:
    request = json.loads(line)
    seen.write(request["op"] + "\\n")
    seen.flush()
    if request["op"] == "observes":
        print(json.dumps({"id": request["id"], "ok": True, "result": {"qwen": {"cwd": "written"}}}), flush=True)
"""

#: Exits as soon as it starts.
DIES = """
with open(sys.argv[0] + ".log", "a") as seen:
    seen.write("start\\n")
sys.exit(3)
"""


def _stand_in(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "stand-in-runtime"
    script.write_text(f"#!{sys.executable}\nimport json, sys\n{body}", encoding="utf-8")
    script.chmod(0o755)
    return script


def _seen(script: Path) -> list[str]:
    log_file = Path(str(script) + ".log")
    return log_file.read_text(encoding="utf-8").split() if log_file.exists() else []


def _wait_for(done: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch):
    """The switch off and the counters empty; both as they were afterwards."""
    before = cli_runtime.enabled()
    monkeypatch.setattr(cli_runtime, "_counts", {})
    cli_runtime.apply(False)
    yield cli_runtime
    cli_runtime.apply(False)
    cli_runtime.apply(before)


def _qwen(tmp_path: Path) -> tuple[Any, Path]:
    """A wrapped reader and an empty chat it can read."""
    chat = tmp_path / "chat.jsonl"
    chat.write_text("", encoding="utf-8")
    return cli_runtime.wrap(cli_vendor("qwen").make_log_reader()), chat


def test_a_stalled_process_times_out_and_python_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam) -> None:
    exe = _stand_in(tmp_path, STALLS)
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(exe))
    monkeypatch.setattr(cli_runtime, "CALL_TIMEOUT_S", 0.4)
    monkeypatch.setattr(cli_runtime, "BACKOFF_BASE_S", 1000.0)
    scenario = parity._scenarios()["qwen_0_25"]
    expected = parity._run(scenario, tmp_path / "py", parity._python_reader("qwen"), monkeypatch)
    seam.apply(True)
    started = time.monotonic()
    wrapped = parity._run(scenario, tmp_path / "rt",
                          lambda: cli_runtime.wrap(cli_vendor("qwen").make_log_reader()), monkeypatch)
    assert time.monotonic() - started < 5.0, "one timeout, then no more waiting"
    assert parity._compared(wrapped).replace("/rt", "/py") == parity._compared(expected)
    # The stalled process got exactly one reader call, was killed, and is not
    # started again before its delay.
    assert _seen(exe) == ["observes", "usage"]
    counts = seam.status()["calls"]["qwen"]
    assert counts["rust"] == 0
    assert counts["fallback"] >= 2 * len(scenario["steps"]) + 1
    assert seam.status()["state"] == "waiting-to-restart"


def test_switching_off_returns_at_once_and_frees_a_stalled_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                seam) -> None:
    exe = _stand_in(tmp_path, STALLS)
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(exe))
    monkeypatch.setattr(cli_runtime, "CALL_TIMEOUT_S", 120.0)
    reader, chat = _qwen(tmp_path)
    seam.apply(True)
    results: list[Any] = []
    caller = threading.Thread(target=lambda: results.append(reader.parse_incremental(chat, {})), daemon=True)
    caller.start()
    _wait_for(lambda: "usage" in _seen(exe))
    assert caller.is_alive(), "the call is waiting on the stalled process"

    started = time.monotonic()
    seam.apply(False)
    assert time.monotonic() - started < 1.0, "the escape hatch does not wait for the call"

    caller.join(5.0)
    assert not caller.is_alive(), "killing the process woke the waiting call"
    assert results and results[0].events == []
    assert seam.status()["state"] == "off"


def test_a_process_that_keeps_dying_is_given_up_on_and_said_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 seam, caplog: pytest.LogCaptureFixture) -> None:
    exe = _stand_in(tmp_path, DIES)
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(exe))
    monkeypatch.setattr(cli_runtime, "BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(cli_runtime, "BREAKER_FAILURES", 3)
    reader, chat = _qwen(tmp_path)
    seam.apply(True)
    with caplog.at_level(logging.WARNING, logger=cli_runtime.log.name):
        for _ in range(8):
            assert reader.parse_incremental(chat, {}).events == []
    assert _seen(exe) == ["start"] * 3, "three starts, then none"
    assert seam.status()["state"] == "stopped-after-failures"
    assert len([r for r in caplog.records if "in a row" in r.getMessage()]) == 1

    # Flipping the switch is the way back: the process is started again.
    seam.apply(False)
    seam.apply(True)
    reader.parse_incremental(chat, {})
    assert len(_seen(exe)) > 3


def test_a_failed_process_is_not_restarted_before_its_delay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            seam) -> None:
    exe = _stand_in(tmp_path, DIES)
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(exe))
    monkeypatch.setattr(cli_runtime, "BACKOFF_BASE_S", 1000.0)
    reader, chat = _qwen(tmp_path)
    seam.apply(True)
    for _ in range(6):
        reader.parse_incremental(chat, {})
    assert _seen(exe) == ["start"]
    assert seam.status()["state"] == "waiting-to-restart"
    assert seam.status()["failures"] == 1


def test_switched_on_without_a_binary_says_so_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(tmp_path / "missing"))
    reader, chat = _qwen(tmp_path)
    with caplog.at_level(logging.WARNING, logger=cli_runtime.log.name):
        seam.apply(True)
        seam.apply(True)
        for _ in range(3):
            assert reader.parse_incremental(chat, {}).events == []
    assert len([r for r in caplog.records if "was not found" in r.getMessage()]) == 1
    status = seam.status()
    assert status["enabled"] is True and status["binary"] is False and status["calls"] == {}


def test_the_binary_is_looked_up_like_the_other_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = osplat.paths.bundled_executable("navide-cli-runtime")
    packaged = tmp_path / "resources" / "bin"
    packaged.mkdir(parents=True)
    (packaged / name).write_text("", encoding="utf-8")
    monkeypatch.delenv(cli_runtime.BINARY_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli_runtime.frozen_env, "packaged_bin_dir", lambda: str(packaged))
    assert cli_runtime.binary() == packaged / name

    override = tmp_path / "my-build"
    override.write_text("", encoding="utf-8")
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(override))
    assert cli_runtime.binary() == override
    # A named binary that is not there is not quietly replaced by another.
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(tmp_path / "gone"))
    assert cli_runtime.binary() is None


def test_status_counts_who_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam) -> None:
    if cli_runtime.binary() is None:
        pytest.skip("navide-cli-runtime is not built (cargo build --release in native/navide-cli-runtime)")
    scenario = parity._scenarios()["qwen_0_25"]
    seam.apply(True)
    parity._run(scenario, tmp_path, lambda: cli_runtime.wrap(cli_vendor("qwen").make_log_reader()), monkeypatch)
    status = seam.status()
    assert status["state"] == "running" and status["failures"] == 0
    assert status["calls"] == {"qwen": {"rust": 2 * len(scenario["steps"]) + 1, "unsupported": 0, "unreadable": 0,
                                        "fallback": 0}}
    seam.shutdown()
    assert seam.status()["state"] == "idle"


def test_what_the_reader_would_log_is_logged_once_through_its_own_logger(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam, caplog: pytest.LogCaptureFixture) -> None:
    if cli_runtime.binary() is None:
        pytest.skip("navide-cli-runtime is not built (cargo build --release in native/navide-cli-runtime)")
    # The scenario has a session no table gives a key (two polls read it).
    scenario = parity._scenarios()["openclaw_db"]
    seam.apply(True)
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.openclaw"):
        parity._run(scenario, tmp_path, lambda: cli_runtime.wrap(cli_vendor("openclaw").make_log_reader()),
                    monkeypatch)
    said = [r for r in caplog.records if "names no session key" in r.getMessage()]
    assert len(said) == 1 and said[0].name == "agent_team_backend.log_readers.openclaw"
    assert seam.status()["calls"]["openclaw"]["fallback"] == 0
