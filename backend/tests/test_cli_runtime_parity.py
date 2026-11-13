"""The Rust CLI runtime (native/navide-cli-runtime) against main's Python readers.

Each scenario below is built from the record builders of main's own vendor
tests, written step by step into a fake CLI home, and read the way the log
watcher reads it: ``parse_incremental`` with the previous checkpoint,
``parse_activity`` with one persistent seen-keys bag and one reader, and
finally ``turns_for_session``.

* The Python results are the parity fixtures the Rust crate replays
  (``native/navide-cli-runtime/tests/parity/*.json``). Run with
  ``NAVIDE_PARITY_WRITE=1`` to rewrite them; otherwise this test fails when
  main's readers no longer produce what the fixtures record.
* With the ``rustCliRuntime`` switch off, a wrapped reader is main's reader.
* With it on (and a built ``navide-cli-runtime``), the wrapped reader answers
  through the Rust process and must match Python result for result.
"""

from __future__ import annotations

import base64
import dataclasses
import sqlite3
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from agent_team_backend import cli_runtime, osplat
from agent_team_backend.cli_vendors.pi import encode_pi_cwd
from agent_team_backend.cli_vendors.registry import vendor as cli_vendor
from agent_team_backend.log_readers.claude import encode_claude_cwd
from tests.vendors import test_claude as claude_t
from tests.vendors import test_codex_0_163_fixture as codex_t
from tests.vendors import test_codex_subagent as sub_t
from tests.vendors import test_droid as droid_t
from tests.vendors import test_goose as goose_t
from tests.vendors import test_grok as grok_t
from tests.vendors import test_kimi_code_2_2_fixture as kimi_t
from tests.vendors import test_muse as muse_t
from tests.vendors import test_pi as pi_t
from tests.vendors import test_qwen_0_25_fixture as qwen_t

FIXTURES = Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime" / "tests" / "parity"
#: Fixed clock base (seconds); every step's mtime and "now" are offsets from it.
B = 1_792_000_000

USAGE_FIELDS = ("vendor", "input_tokens", "output_tokens", "cwd", "session_id", "file_path", "dedup_key",
                "timestamp", "model", "checkpoint", "raw", "cache_read_tokens", "cache_creation_tokens",
                "cli_version")
ACTIVITY_FIELDS = ("vendor", "event_type", "cwd", "session_id", "file_path", "dedup_key", "timestamp",
                   "detail", "text")
TURN_FIELDS = ("turn_index", "session_id", "started_at", "ended_at", "prompt_excerpt", "input", "cache_read",
               "cache_creation", "output", "calls", "model_calls", "cli_version")


def _lines(records: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def _step(mode: str, records: list[dict] | None, mtime: float, now: float, sidecars: dict[str, str] | None = None) -> dict:
    """One watcher poll; `sidecars` are files (re)written beside the transcript first."""
    step = {"mode": mode, "text": _lines(records or []), "mtime": B + mtime, "now": B + now}
    if sidecars:
        step["sidecars"] = sidecars
    return step


def _db_step(statements: list[tuple[str, tuple] | str], mtime: float, now: float) -> dict:
    """One poll of a SQLite CLI: the statements run (a bare string is a script), then the readers read.
    The fixture records the database file's bytes after the step, which the Rust test writes back."""
    sql = [[s, None] if isinstance(s, str) else [s[0], list(s[1])] for s in statements]
    return {"mode": "db", "sql": sql, "mtime": B + mtime, "now": B + now}


def _scenarios() -> dict[str, dict]:
    qwen = qwen_t._chat_0_25()
    kimi = kimi_t._records(B * 1000)
    codex = codex_t._rollout_0163()
    pi_head = [pi_t._header(), pi_t._user("u1", "fix the parser"), pi_t._assistant("a1", "u1", input=120, cache_read=30)]
    pi_more = [pi_t._compaction("c1", "a1"), pi_t._user("u2", [{"type": "text", "text": "and the tests"}], "c1"),
               pi_t._assistant_saying("a2", "all green", "u2")]
    sub_day = "home/.codex-panes/pane-1/sessions/2026/09/05"
    sub_parent = [
        sub_t._parent_meta(),
        sub_t._ev("2026-09-05T01:10:40Z", "task_started", turn_id="p1"),
        sub_t._prompt("2026-09-05T01:10:40Z", "Refactor the parser"),
        sub_t._tokens("2026-09-05T01:10:50Z", 1000, 100),
        sub_t._ev("2026-09-05T01:12:00Z", "agent_message", message="Parent's answer"),
        sub_t._tokens("2026-09-05T01:12:00Z", 3000, 300),
        sub_t._ev("2026-09-05T01:12:01Z", "task_complete", turn_id="p1", last_agent_message="Parent's answer"),
    ]
    sub_child = [
        sub_t._child_meta(),
        sub_t._parent_meta(),
        sub_t._ev("2026-09-05T01:10:58Z", "task_started", turn_id="c1"),
        sub_t._prompt("2026-09-05T01:10:58Z", "Explore the cloud folder"),
        sub_t._tokens("2026-09-05T01:11:10Z", 24796, 50),
        sub_t._ev("2026-09-05T01:11:20Z", "agent_message", message="Child's findings"),
        sub_t._ev("2026-09-05T01:11:21Z", "task_complete", turn_id="c1", last_agent_message="Child's findings"),
        sub_t._ev("2026-09-05T01:13:00Z", "task_started", turn_id="c2"),
        sub_t._ev("2026-09-05T01:13:05Z", "turn_aborted", turn_id="c2", reason="interrupted"),
    ]
    claude = [
        claude_t._prompt_line("p1", "幫我修 login bug", "2026-09-16T00:00:00Z"),
        claude_t._assistant_line("m1", "r1", "2026-09-16T00:00:01Z", inp=2, cr=100, cc=40, out=10),
        claude_t._assistant_line("m1", "r1", "2026-09-16T00:00:02Z", inp=2, cr=100, cc=40, out=10),
        claude_t._tool_result_line("p1", "2026-09-16T00:00:03Z"),
        claude_t._assistant_line("m2", "r2", "2026-09-16T00:00:05Z", inp=3, cr=200, cc=0, out=20, stop="end_turn"),
        claude_t._prompt_line("p2", [{"type": "text", "text": "再來一次"}, {"type": "image"}], "2026-09-16T00:01:00Z"),
        {"type": "user", "promptId": "p2", "timestamp": "2026-09-16T00:01:02Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "summary", "summary": "resumed"},
        claude_t._prompt_line("p3", "after resume", "2026-09-16T01:00:00Z"),
        claude_t._assistant_line("m3", "r3", "2026-09-16T01:00:04Z", inp=1, cr=300, cc=5, out=7, stop="end_turn"),
    ]
    droid_dir = "droid/sessions/-tmp-demo"
    droid_sid = "d0c1e2f3-0000-4000-8000-000000000001"
    droid_settings = f"{droid_dir}/{droid_sid}.settings.json"

    def droid_totals(inp: int, out: int, cache_read: int = 0) -> dict[str, str]:
        usage = {"inputTokens": inp, "outputTokens": out, "cacheReadTokens": cache_read,
                 "cacheCreationTokens": 0, "thinkingTokens": 9}
        return {droid_settings: json.dumps({"model": "glm-4.6", "tokenUsage": usage})}

    droid = [
        droid_t._session_start(droid_sid, "/tmp/filed", lastCwd="/tmp/demo"),
        droid_t._msg("user", [{"type": "text", "text": "fix the build"}]),
        droid_t._msg("assistant", [{"type": "text", "text": "looking"}, {"type": "tool_use", "id": "t1"}]),
        droid_t._msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        droid_t._msg("user", "echo hook", hookEventName="Stop"),
        {"type": "agent_turn_outcome", "reason": "completed", "timestamp": "2026-08-26T17:59:00.000Z"},
        droid_t._msg("user", "and the docs"),
        droid_t._msg("assistant", [{"type": "text", "text": "done"}]),
        {"type": "agent_turn_outcome", "reason": "cancelled", "timestamp": "2026-08-26T18:00:00.000Z"},
    ]
    muse_meta = muse_t._metadata()
    muse_meta["payload"]["record"]["build"] = {"semver": "0.9.1"}
    muse = [
        muse_meta, muse_t._diagnostic(2), muse_t._intake("refactor the cache", 3),
        muse_t._run({"kind": "started"}, 4), muse_t._assistant("  cache is now LRU  ", 5),
        muse_t._usage_row("u-1", 6, input_tokens=900, output_tokens=40),
        muse_t._usage_row("u-sub", 7, requester_kind="subagent"),
        muse_t._usage_row("u-0", 8, input_tokens=0, output_tokens=0),
        muse_t._terminal(9),
        muse_t._intake("/compact", 10), muse_t._usage_row("u-1", 11),
        muse_t._usage_row("u-2", 12, input_tokens=50, output_tokens=5),
        muse_t._assistant("second", 13), muse_t._terminal(14, "failed"),
    ]
    muse_dir = "share/muse/sessions/2026/08/12/" + muse_t._SID
    def goose_msg(sid: str, role: str, content: Any, meta: dict | None = None, ts: int = 1792513596) -> tuple:
        return ("INSERT INTO messages (message_id, session_id, role, content_json, created_timestamp, metadata_json)"
                " VALUES ('m', ?, ?, ?, ?, ?)",
                (sid, role, json.dumps(content), ts, json.dumps(meta or {"userVisible": True, "agentVisible": True})))

    def goose_session(sid: str, parent: str | None = None, kind: str = "user") -> tuple:
        return ("INSERT INTO sessions (id, name, session_type, working_dir, parent_session_id) VALUES (?, '', ?, ?, ?)",
                (sid, kind, goose_t.CWD, parent))

    def goose_usage(sid: str, inp: int | None, out: int | None, cache_read: int | None = None) -> tuple:
        return ("INSERT INTO usage_ledger (session_id, created_timestamp, model, input_tokens, output_tokens,"
                " cache_read_tokens, cache_write_tokens) VALUES (?, 1792513603, 'm1', ?, ?, ?, 7)",
                (sid, inp, out, cache_read))

    goose_steps = [
        _db_step([goose_t.SCHEMA, goose_session("s1"), goose_msg("s1", "user", goose_t._text("old history"))], 0, 1),
        _db_step([
            goose_msg("s1", "user", goose_t._text("fix the build"), ts=1792513600),
            goose_msg("s1", "assistant", goose_t._tool("developer__shell")),
            goose_msg("s1", "user", [{"type": "toolResponse", "id": "c"}]),
            goose_msg("s1", "assistant", [{"type": "actionRequired"}]),
            goose_msg("s1", "user", goose_t._text("injected"), meta={"turnContext": True}),
            goose_msg("s1", "assistant", goose_t._text("  built  ") + goose_t._text("and tested"), ts=1792513610),
            goose_usage("s1", 120, 30, cache_read=40), goose_usage("s1", 0, 0), goose_usage("s1", None, 5),
            goose_session("s2", parent="s1", kind="sub_agent"),
            goose_msg("s2", "assistant", goose_t._text("child done")),
            goose_msg("s1", "assistant", goose_t._text("hidden"), meta={"agentVisible": False}),
            goose_msg("s1", "system", goose_t._text("note")),
        ], 1, 2),
        _db_step([goose_msg("s1", "user", goose_t._text("again"), ts=1792513700), goose_usage("s1", 50, 5)], 2, 3),
    ]
    grok = [grok_t._user(1, "hello grok"), grok_t._agent(2, "hi "), grok_t._agent(3, "there"), grok_t._turn(4),
            grok_t._user(5, "again"), grok_t._agent(6, "ok"), grok_t._turn(7, inp=50, out=5, stop="cancelled")]
    return {
        "qwen_0_25": {
            "vendor": "qwen", "env": {"QWEN_RUNTIME_DIR": "qwen"},
            "file": f"qwen/projects/{encode_claude_cwd(qwen_t._CWD)}/chats/{qwen_t._SID}.jsonl",
            "steps": [
                _step("write", qwen[:4], 0, 1),
                _step("append", qwen[4:], 2, 3),
                _step("append", [qwen_t._prompt("u2", "a2", 8, "second prompt"),
                                 qwen_t._reply("a3", "u2", 9, "second reply", 1500, 30, 1200, 5)], 4, 5),
                _step("append", [], 4, 14),
            ],
        },
        "kimi_2_2": {
            "vendor": "kimi", "env": {"KIMI_CODE_HOME": "kimi"},
            "file": f"kimi/sessions/wd_proj_c41def6fd06b/{kimi_t._SID}/agents/main/wire.jsonl",
            "sidecars": {f"kimi/sessions/wd_proj_c41def6fd06b/{kimi_t._SID}/state.json": json.dumps(kimi_t._state())},
            "steps": [_step("write", kimi[:12], 1, 1), _step("append", kimi[12:], 2, 2), _step("append", [], 2, 100)],
        },
        "codex_0_163": {
            "vendor": "codex", "env": {},
            "file": f"home/.codex/sessions/2026/10/20/rollout-2026-10-20T10-00-00-{codex_t._THREAD}.jsonl",
            "steps": [_step("write", codex[:12], 0, 1), _step("append", codex[12:], 1, 2)],
        },
        "codex_subagent_parent": {
            "vendor": "codex", "env": {},
            "file": f"{sub_day}/rollout-2026-09-05T09-10-06-{sub_t.PARENT_ID}.jsonl",
            "steps": [_step("write", sub_parent[:4], 0, 1), _step("append", sub_parent[4:], 1, 2)],
        },
        "codex_subagent_child": {
            "vendor": "codex", "env": {},
            "file": f"{sub_day}/rollout-2026-09-05T09-10-57-{sub_t.CHILD_ID}.jsonl",
            "steps": [_step("write", sub_child[:5], 0, 1), _step("append", sub_child[5:], 1, 2)],
        },
        "claude_turns": {
            "vendor": "claude", "env": {"CLAUDE_CONFIG_DIR": "claude"},
            "file": "claude/projects/-tmp-demo/turns-1.jsonl",
            "steps": [_step("write", claude[:4], 0, 1), _step("append", claude[4:7], 1, 2),
                      _step("append", claude[7:], 2, 3)],
        },
        "pi_rewrite": {
            "vendor": "pi", "env": {"PI_CODING_AGENT_SESSION_DIR": "pi"},
            "file": f"pi/{encode_pi_cwd(pi_t._CWD)}/{pi_t._TS_PREFIX}_{pi_t._SID}.jsonl",
            "steps": [
                _step("write", pi_head, 0, 1),
                _step("append", pi_more, 1, 2),
                _step("append", [], 1, 20),
                # pi rewrites its file in place (a branch switch): same records, new layout.
                _step("write", [pi_head[0], pi_head[1], pi_t._assistant("a1", "u1", input=120, cache_read=30), *pi_more,
                                pi_t._user("u3", "one more", "a2")], 30, 31),
            ],
        },
        "droid_sidecar": {
            "vendor": "droid", "env": {"FACTORY_HOME_OVERRIDE": "droid"},
            "file": f"{droid_dir}/{droid_sid}.jsonl",
            "steps": [
                _step("write", droid[:3], 0, 1),
                _step("append", droid[3:6], 1, 2, droid_totals(100, 20, cache_read=50)),
                _step("append", droid[6:], 2, 3, droid_totals(400, 60, cache_read=50)),
                # The sidecar was replaced (a fork): re-baseline, no event; then growth again.
                _step("append", [], 3, 4, droid_totals(30, 5)),
                _step("append", [], 4, 5, droid_totals(70, 9)),
            ],
        },
        "muse_session": {
            "vendor": "muse", "env": {"XDG_DATA_HOME": "share"},
            "file": f"{muse_dir}/session.jsonl",
            "steps": [_step("write", muse[:6], 0, 1), _step("append", muse[6:10], 1, 2),
                      _step("append", muse[10:], 2, 3)],
        },
        # A subagent's log: not a transcript (its tokens are the parent's).
        # main's turns_for_session does not apply that rule (parse_session_file
        # credits the child's own usage); the runtime applies it everywhere, so
        # turns are not compared here (REDESIGN-REPORT.md, Python defects).
        "muse_subagent": {
            "vendor": "muse", "env": {"XDG_DATA_HOME": "share"}, "skip_turns": True,
            "file": f"{muse_dir}/subagent/child-1/session.jsonl",
            "steps": [_step("write", muse[:9], 0, 1)],
        },
        "goose_db": {
            "vendor": "goose", "env": {"GOOSE_PATH_ROOT": "goose"},
            "file": "goose/data/sessions/sessions.db",
            "steps": goose_steps,
        },
        "grok_turns": {
            "vendor": "grok", "env": {"GROK_HOME": "grok"},
            "file": f"grok/sessions/{grok_t.GROUP}/{grok_t.SID}/updates.jsonl",
            "steps": [_step("write", grok[:2], 0, 1), _step("append", grok[2:4], 1, 2), _step("append", grok[4:], 2, 3)],
        },
    }


def _norm(value: Any, root: str) -> Any:
    """Paths relative to the scenario root; checkpoint `identity` (dev:inode) dropped."""
    if isinstance(value, dict):
        return {k: _norm(v, root) for k, v in value.items() if k != "identity"}
    if isinstance(value, list):
        return [_norm(v, root) for v in value]
    if isinstance(value, str):
        # A byte cursor serialised into a seen-keys bag key carries its identity too.
        return re.sub(r'"identity": "[^"]*", ', "", value.replace(root, "<root>"), count=1)
    return value


def _pick(obj: Any, fields: tuple[str, ...]) -> dict:
    data = dataclasses.asdict(obj)
    return {k: data[k] for k in fields}


def _activity_dict(event: Any) -> dict:
    data = _pick(event, ACTIVITY_FIELDS)
    full = getattr(event.text, "full", "")
    if full:
        data["full"] = full
    return data


def _turn_dict(turn: Any) -> dict:
    data = _pick(turn, TURN_FIELDS)
    data["calls"] = [dict(c) for c in data["calls"]]
    return data


def _run(scenario: dict, root: Path, make_reader: Callable[[], Any], monkeypatch: pytest.MonkeyPatch) -> dict:
    """Read the scenario the watcher's way; return every result, normalised."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(osplat.paths.home_env_var(), str(home))
    for key, rel in scenario["env"].items():
        monkeypatch.setenv(key, str(root / rel))
    for rel, text in (scenario.get("sidecars") or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    path = root / scenario["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    clock = [0.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    reader = make_reader()
    checkpoint: dict = {}
    seen: set[str] = set()
    steps = []
    files: list[str] = []
    for step in scenario["steps"]:
        for rel, text in (step.get("sidecars") or {}).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        if step["mode"] == "db":
            con = sqlite3.connect(path)
            for sql, params in step["sql"]:
                if params is None:
                    con.executescript(sql)
                else:
                    con.execute(sql, params)
            con.commit()
            con.close()
            files.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        else:
            with path.open("w" if step["mode"] == "write" else "a", encoding="utf-8", newline="") as fh:
                fh.write(step["text"])
        os.utime(path, (step["mtime"], step["mtime"]))
        clock[0] = step["now"]
        parsed = reader.parse_incremental(path, checkpoint)
        checkpoint = parsed.checkpoint
        activity = reader.parse_activity(path, seen)
        steps.append({
            "usage": {"events": [_pick(e, USAGE_FIELDS) for e in parsed.events], "checkpoint": parsed.checkpoint},
            "activity": [_activity_dict(e) for e in activity],
            "bag": sorted(seen),
        })
    turns = None if scenario.get("skip_turns") else [_turn_dict(t) for t in reader.turns_for_session(path, "")]
    result = _norm({"steps": steps, "turns": turns}, str(root))
    if files:
        result["files"] = files
    return result


def _compared(result: dict) -> str:
    """A run's results without the database bytes (two runs' files differ in nothing a reader sees)."""
    return json.dumps({k: v for k, v in result.items() if k != "files"})


def _python_reader(vendor: str) -> Callable[[], Any]:
    return lambda: cli_vendor(vendor).make_log_reader()


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_python_readers_match_the_parity_fixtures(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = _scenarios()[name]
    result = _run(scenario, tmp_path, _python_reader(scenario["vendor"]), monkeypatch)
    fixture = {
        "vendor": scenario["vendor"],
        "file": scenario["file"],
        "sidecars": scenario.get("sidecars") or {},
        "steps": [
            {**step, **({"db": result["files"][i]} if step["mode"] == "db" else {}), "expect": got}
            for i, (step, got) in enumerate(zip(scenario["steps"], result["steps"]))
        ],
        "turns": result["turns"],
    }
    target = FIXTURES / f"{name}.json"
    if os.environ.get("NAVIDE_PARITY_WRITE") == "1":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    def without_bytes(doc: dict) -> dict:
        # The recorded database differs run to run (CURRENT_TIMESTAMP defaults); what is read from it does not.
        return {**doc, "steps": [{k: v for k, v in s.items() if k != "db"} for s in doc["steps"]]}

    recorded = json.loads(target.read_text(encoding="utf-8"))
    assert without_bytes(recorded) == without_bytes(json.loads(json.dumps(fixture, ensure_ascii=False)))


@pytest.fixture
def runtime_switch():
    before = cli_runtime.enabled()
    yield cli_runtime
    cli_runtime.apply(before)


@pytest.mark.parametrize("name", ["qwen_0_25", "claude_turns"])
def test_switch_off_is_mains_reader(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_switch) -> None:
    scenario = _scenarios()[name]
    runtime_switch.apply(False)
    started: list[Any] = []
    monkeypatch.setattr(cli_runtime, "_Client", lambda exe: started.append(exe))
    expected = _run(scenario, tmp_path / "py", _python_reader(scenario["vendor"]), monkeypatch)
    wrapped = _run(scenario, tmp_path / "rt", lambda: cli_runtime.wrap(cli_vendor(scenario["vendor"]).make_log_reader()),
                   monkeypatch)
    assert _compared(wrapped).replace("/rt", "/py") == _compared(expected)
    assert started == []


def test_settings_turn_the_switch_on_and_off(runtime_switch) -> None:
    runtime_switch.apply_settings({})
    assert not runtime_switch.enabled()
    runtime_switch.apply_settings({cli_runtime.SETTING_KEY: True})
    assert runtime_switch.enabled()
    runtime_switch.apply_settings({cli_runtime.SETTING_KEY: "yes"})
    assert not runtime_switch.enabled()


def test_switch_on_without_a_binary_falls_back_to_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         runtime_switch) -> None:
    scenario = _scenarios()["qwen_0_25"]
    monkeypatch.setenv(cli_runtime.BINARY_ENV, str(tmp_path / "missing"))
    runtime_switch.apply(True)
    expected = _run(scenario, tmp_path / "py", _python_reader("qwen"), monkeypatch)
    wrapped = _run(scenario, tmp_path / "rt", lambda: cli_runtime.wrap(cli_vendor("qwen").make_log_reader()), monkeypatch)
    assert json.dumps(wrapped).replace("/rt", "/py") == json.dumps(expected)


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_switch_on_matches_python(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_switch) -> None:
    if cli_runtime.binary() is None:
        pytest.skip("navide-cli-runtime is not built (cargo build --release in native/navide-cli-runtime)")
    scenario = _scenarios()[name]
    runtime_switch.apply(True)
    expected = _run(scenario, tmp_path / "py", _python_reader(scenario["vendor"]), monkeypatch)
    calls: list[str] = []
    real_call = cli_runtime._call

    def counting(vendor: str, op: str, **fields: Any):  # noqa: ANN202
        answer = real_call(vendor, op, **fields)
        if answer is not None and answer[0]:
            calls.append(op)
        return answer

    monkeypatch.setattr(cli_runtime, "_call", counting)
    wrapped = _run(scenario, tmp_path / "rt",
                   lambda: cli_runtime.wrap(cli_vendor(scenario["vendor"]).make_log_reader()), monkeypatch)
    # The Rust process answered every routed call (no silent fallback).
    turns = 0 if scenario.get("skip_turns") else 1
    assert set(calls) == {"usage", "activity", "turns"} - ({"turns"} if not turns else set())
    assert len(calls) == 2 * len(scenario["steps"]) + turns
    assert json.loads(_compared(wrapped).replace("/rt", "/py")) == json.loads(_compared(expected))
