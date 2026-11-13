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
                                        "deferred": 0, "fallback": 0}}
    seam.shutdown()
    assert seam.status()["state"] == "idle"


def test_the_cost_ledgers_own_readers_go_through_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ledger builds a Claude and a Codex reader of its own; they are routed like the backend's."""
    from agent_team_backend import cost_ledger

    wrapped: list[str] = []
    real_wrap = cli_runtime.wrap

    def recording(reader: Any) -> Any:
        wrapped.append(str(reader.vendor))
        return real_wrap(reader)

    monkeypatch.setattr(cli_runtime, "wrap", recording)
    monkeypatch.setattr(cost_ledger, "_claude_reader", None)
    usage = cost_ledger.TokenUsage(vendor="claude", input_tokens=1, output_tokens=1, cwd="/w", session_id="s",
                                   file_path="/nowhere/s.jsonl", dedup_key="k")
    cost_ledger.transcript_cwd(usage)
    cost_ledger.transcript_cwd(usage)  # built once, kept
    assert wrapped == ["claude"]

    # The backfill command: both readers, wrapped, before any file is read.
    handed_over: list[Any] = []
    monkeypatch.setattr(cost_ledger, "CostLedger", lambda _db: None)
    monkeypatch.setattr(cost_ledger, "Database", lambda _path: None)
    monkeypatch.setattr(cost_ledger, "backfill", lambda _ledger, readers: handed_over.extend(readers) or (0, 0))
    assert cost_ledger.main(["backfill", "unused.db"]) == 0
    assert wrapped == ["claude", "claude", "codex"]
    assert [reader.parse_incremental.__name__ for reader in handed_over] == ["routed", "routed"]


@pytest.fixture
def no_read_problems():
    from agent_team_backend.cli_vendors import opencode

    opencode._READ_PROBLEMS.clear()
    yield opencode
    opencode._READ_PROBLEMS.clear()


def test_what_the_schema_check_finds_reaches_the_readers_problem_registry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam, no_read_problems) -> None:
    """The runtime read the store; the CLI's status must say what main's reader would."""
    if cli_runtime.binary() is None:
        pytest.skip("navide-cli-runtime is not built (cargo build --release in native/navide-cli-runtime)")
    import sqlite3

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = tmp_path / "opencode" / "opencode.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversation_v3 (id TEXT)")
    con.commit()
    reader = cli_runtime.wrap(cli_vendor("opencode").make_log_reader())
    seam.apply(True)

    assert reader.parse_incremental(db, {}).events == []
    problem = no_read_problems.read_problem("opencode")
    assert (problem["status"], problem["reason"], problem["detail"]) == ("unreadable", "unrecognized-schema",
                                                                         "tables: conversation_v3")
    assert problem["path"] == str(db)
    since = problem["since"]
    assert reader.parse_activity(db, set()) == []
    assert no_read_problems.read_problem("opencode")["since"] == since  # the same problem, not a new one

    # A schema it knows: the same entry is cleared by the read that now works.
    from tests.vendors import test_opencode as builders

    con.executescript("DROP TABLE conversation_v3;" + builders._SCHEMA)
    con.commit()
    con.close()
    reader.parse_incremental(db, {})
    assert no_read_problems.read_problem("opencode") is None
    calls = seam.status()["calls"]["opencode"]
    assert (calls["rust"], calls["unsupported"], calls["deferred"], calls["fallback"]) == (3, 0, 0, 0)


def test_a_failing_query_is_the_readers_to_record_and_to_clear(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam, no_read_problems) -> None:
    """The runtime declines a read whose query fails; main's reader makes it,
    records why, and keeps making it until it works and clears its entry."""
    if cli_runtime.binary() is None:
        pytest.skip("navide-cli-runtime is not built (cargo build --release in native/navide-cli-runtime)")
    import sqlite3

    from tests.vendors import test_opencode as builders

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = tmp_path / "opencode" / "opencode.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    # No `part` table: usage reads, activity cannot.
    con.executescript(builders._SCHEMA + "DROP TABLE part;")
    builders._add_session(con, "ses_a", "/ws")
    builders._add_message(con, "m1", "ses_a", builders._assistant_data({"input": 5, "output": 2}))
    reader = cli_runtime.wrap(cli_vendor("opencode").make_log_reader())
    seam.apply(True)

    def calls() -> tuple[int, int, int]:
        counts = seam.status()["calls"]["opencode"]
        return counts["rust"], counts["unsupported"], counts["deferred"]

    # (Counts are compared loosely where `-p tests.cli_runtime_on` routes the class as well as the instance.)
    assert len(reader.parse_incremental(db, {}).events) == 1
    assert calls() == (1, 0, 0)
    seen: set[str] = set()
    assert reader.parse_activity(db, seen) == []
    rust, declined, deferred = calls()
    assert (rust, deferred) == (1, 0) and declined > 0  # declined: the reader read it, and recorded why
    problem = no_read_problems.read_problem("opencode")
    assert problem["reason"] == "query-failed" and "no such table: part" in problem["detail"]

    # While the query is on record as failing, the reader makes every read.
    reader.parse_incremental(db, {})
    reader.parse_activity(db, seen)
    assert calls()[:2] == (1, declined) and calls()[2] > 1
    assert no_read_problems.read_problem("opencode") == problem  # held still: same entry, same `since`

    # The table is there again: the reader's own read clears its entry, and the runtime answers the next one.
    con.executescript("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,"
                      " time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);")
    con.commit()
    con.close()
    reader.parse_activity(db, seen)
    assert no_read_problems.read_problem("opencode") is None
    assert calls()[:2] == (1, declined)
    deferred = calls()[2]
    reader.parse_activity(db, seen)
    assert calls() == (2, declined, deferred)


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
