"""The bound matrix (t36h S4, t36i T4, t36j U1-U4): every declared vendor x layout x mode, over the real sidecar. It is the coverage contract.

One table drives the whole matrix. A cell builds an adversarial input for one vendor layout from its declaration and the fixtures its
own tests already use, with every axis that applies to it: many events, rows that make no event, long text, a long open turn, a dedup
key over 1 KiB, a large call-start bag, and (for `turns`) calls with fields of megabytes. The cell then drives each mode the way its
production caller does: ONE call of the reader's own method (`parse_incremental`, `parse_session_file`, `parse_activity`,
`turns_for_session`, `has_session`, `find_sessions_by_marker`, the header's `_cwd_from_meta`), which goes on while the sidecar says
`more`; a second call must find nothing left. No test here loops over passes of the code under test: a loop is what once hid an activity
pass that did not drain. A store cell holds more rows than one declared page (usage rows over 2,000 where the source pages usage, marks
over 256, or over the walk's own page of 512 blobs / 256 steps), because a full page says `more` and the call goes on (U1). The BASELINE
side is read to its end by calling its method until it finds nothing (its own per-call bound is not the code under test).

For every request and every answer the sidecar saw it asserts

- the request's meta is at most `MAX_META_P2R` (4 MiB) and the answer at most the observation cap (8,355,840 bytes);
- the sidecar was called for the mode (and not for a mode its layout reads nothing for, `NON_READING`), and, where the cell's input is
  built to need more than one pass, at least twice (`pages`);
- the sidecar read the layout the cell's slot names: the file is the slot's `store_file`, and where the file has more than one candidate
  layout the answer names the layout it read and it is the slot (an answer that names none fails, V4);
- the result equals the vendor's own (Python) reader on a copy of the same input, field for field (`dataclasses.asdict` without the
  cursor, `raw` and the timestamps included): a dedup key over 1 KiB is compared as the digest the core sends; a string the core cut is
  the head and the tail of the baseline's, and the answers reported the cut (`truncated`; a lookup's cell is cut at `FIELD_CHARS` where it
  is read, which has no report).

`test_every_declared_triple_is_a_cell_or_named_as_not_read` derives every `(vendor, layout slot, mode)` from the declarations
(`FACTS`), lookups (`has_session`, `identify`) included, and fails if the table misses one, so a vendor that joins the core must join the
matrix. A cell that fails is fixed in the core; it is never dropped. A missing sidecar binary fails these tests, it never skips them.

Two named differences from the baseline (not exemptions of a comparison; each is fed to the baseline as an input, so the comparison
stays exact): Cursor's events are stamped from `meta.json`'s `updatedAtMs` plus 1 ms per event, and the stamps go on across the pages of
one drain AND across calls (`cursor.py:447-455` restarts the offset at every call; t36k V1 keeps a floor), so the baseline's second and
later calls are given the epoch the first ones ended at; and the baseline is called until it finds nothing. The strict increase across
calls is proven in `test_cli_runtime_drain.py`.

The run time of the matrix (macOS, release sidecar, this module alone) is stated in the t36j report.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_vendors._vendor_facts import FACTS, Mode
from agent_team_backend.cli_vendors.aider import AiderLogReader
from agent_team_backend.cli_vendors.claude import ClaudeLogReader
from agent_team_backend.cli_vendors.codex import _session_meta_resume_id
from agent_team_backend.cli_vendors.copilot import CopilotLogReader, _reset_db_warning
from agent_team_backend.cli_vendors.droid import DroidLogReader
from agent_team_backend.cli_vendors.grok import GrokLogReader
from agent_team_backend.cli_vendors.kimi import KimiLogReader
from agent_team_backend.cli_vendors.muse import MuseLogReader
from agent_team_backend.cli_vendors.pi import PiLogReader
from agent_team_backend.cli_vendors.qwen import QwenLogReader
from agent_team_backend.log_readers import AntigravityLogReader, CodexLogReader, CursorLogReader, GooseLogReader, KiloLogReader, OpencodeLogReader
from agent_team_backend.cli_runtime_observation import request_of
from tests.native_reader_peer import wire
from tests.vendors import test_antigravity_activity as agy
from tests.vendors import test_copilot as cp
from tests.vendors import test_cursor as cur
from tests.vendors import test_droid as dr
from tests.vendors import test_grok as grok_tests
from tests.vendors import test_kimi as km
from tests.vendors import test_muse as ms
from tests.vendors import test_pi as pi_tests
from tests.vendors import test_qwen as qwen_tests
from tests.vendors import test_goose as gs
from tests.vendors import test_kilo as kilo
from tests.vendors import test_opencode as oc
from tests.vendors import test_opencode_v2 as oc2

pytestmark = [pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX observation only"),
              pytest.mark.timeout(900)]

MIB = 1024 * 1024
ANSWER_CAP = 128 * 65_280
REQUEST_CAP = 4 * MIB
#: The longest dedup key the core carries as it is (`KEY_BYTES`); a longer one travels as `long::` + blake2b-32 in hex.
KEY_BYTES = 1024
#: The cut the core makes (`FIELD_CHARS`, and `cap_head_tail`'s head and tail); a cut string is compared by its head and its tail.
EDGE = 1024
#: A call-start bag a caller kept: keys of an earlier life of the reader that match nothing in the input.
BAG = 30_000


class Env:
    """What a builder may touch: the temp dir and the environment of the vendor's own tests."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.tmp, self.patch = tmp_path, monkeypatch


@dataclasses.dataclass
class Case:
    """One adversarial input: the file the sidecar reads, the vendor's readers, and (for a store whose first activity pass only
    plants its walks) the rows added after that first pass."""
    path: Path
    reader: type
    grow: Callable[[], None] = lambda: None
    seeds: bool = False
    session: str = ""
    #: The ids `has_session` is asked for, and the markers `find_sessions_by_marker` is asked for (a lookup cell only); `header` is a log
    #: whose `identify` reads the working directory from its first lines (`_cwd_from_meta`).
    sessions: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    header: bool = False


@dataclasses.dataclass(frozen=True)
class Cell:
    name: str
    vendor: str
    #: The slot of the layout among the vendor's declared layouts (`FACTS[vendor].observer.layouts`).
    slot: int
    #: Builds the input for one mode (a store's volume is sized by what the mode reads: see `HEAVY`).
    build: Callable[[Env, str], Case]
    modes: tuple[str, ...]
    #: The modes whose input is built to need more than one pass.
    pages: tuple[str, ...]


def _compact(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


# A store answers one declared page of rows per observation (2,000 usage rows, 256 marks a walk) and says `more` when the page came back
# full, so a store cell holds MORE than one page of each table it pages (U1) and needs several passes by the number and the weight of its
# rows. `HEAVY` is a field the core carries whole (under `FIELD_CHARS`); `TEXT` is a reply or a prompt (activity text is not cut).
HEAVY = 8000
TEXT = 40_000
#: Rows over the usage cursor's page of 2,000.
USAGE_ROWS = 2_100
#: Marks over a walk's declared page of 256.
MARKS = 300


def long_key(prefix: str) -> str:
    """A dedup key part over `KEY_BYTES` (2 KiB)."""
    return prefix + "k" * 2048


# ------------------------------------------------------------------------------------------------------------ builders

def claude_log(env: Env, *, records: int = 3000, model: int = 2000, text: int = 0, calls: bool = False, long_calls: bool = False) -> Case:
    """Claude's conversation JSONL (`tests.test_cli_runtime_observe.claude_record`): assistant records with real-length request and
    message ids (every 100th a request id of 2 KiB), a padded model name, a reply text for the activity cell, and the lines that make
    no usage (a user line, an assistant line without usage, a line that is not JSON) between them; `calls` puts them under one
    prompt, and `long_calls` adds the calls with fields of megabytes (the line cap is 16 MiB)."""
    env.patch.setenv("CLAUDE_CONFIG_DIR", str(env.tmp))
    log = env.tmp / "projects" / "-tmp-demo" / "abc-123.jsonl"
    log.parent.mkdir(parents=True)
    pad, lines = "m" * model, []
    if calls:
        lines.append(json.dumps({"type": "user", "promptId": "p1", "message": {"role": "user", "content": "go"}}))

    def assistant(n: int, *, request: str, timestamp: str, model_name: str, version: str = "2.1", usage: bool = True) -> str:
        message = {"id": f"msg_{n:08d}{'0' * 40}", "model": model_name, "role": "assistant", "stop_reason": "end_turn",
                   "content": [{"type": "text", "text": "r" * text}]}
        if usage:
            message["usage"] = {"input_tokens": 10, "output_tokens": 5}
        return json.dumps({"type": "assistant", "sessionId": "abc-123", "requestId": request, "timestamp": timestamp, "version": version,
                           "message": message})

    for n in range(1, records + 1):
        stamp = f"2026-05-28T00:{n // 60 % 60:02d}:{n % 60:02d}Z"
        request = long_key(f"req_{n:08d}") if n % 100 == 0 else f"req_{n:08d}{'0' * 40}"
        lines.append(assistant(n, request=request, timestamp=stamp, model_name=pad))
        if not calls and n % 10 == 0:
            lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": "next"}}))
        if n % 25 == 0:
            lines.append(assistant(n + records, request=f"nousage_{n}", timestamp=stamp, model_name=pad, usage=False))
        if n % 50 == 0:
            lines.append("this line is not JSON")
    if long_calls:
        nine = lambda marker: f"HEAD{'z' * (9 * MIB)}{marker}TAIL"  # noqa: E731
        lines.append(assistant(10_001, request="long-ts", timestamp=nine("t"), model_name="m"))
        lines.append(assistant(10_002, request="long-model", timestamp="2026-05-28T01:00:00Z", model_name=nine("m")))
        lines.append(assistant(10_003, request="long-both", timestamp=f"HEAD{'t' * (5 * MIB)}TAIL", model_name=f"HEAD{'m' * (5 * MIB)}TAIL",
                               version=f"HEAD{'v' * (4 * MIB)}TAIL"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Case(log, ClaudeLogReader, session="abc-123")


def codex_log(env: Env, *, filler: int = 20000, reply: int = 5 * MIB, cwd: str = "/tmp/demo") -> Case:
    """A Codex rollout (`tests.vendors.test_codex`): the session meta (with the working directory `cwd`), a reply that stays open (5 MiB:
    over the open-text share), then many lines of the same turn (token counts, with a line that is not JSON and a blank line among them),
    then the turn's end that delivers the reply."""
    root = env.tmp / "home" / ".codex" / "sessions" / "2026" / "05" / "27"
    root.mkdir(parents=True)
    log = root / "rollout-test.jsonl"
    count = {"timestamp": "2026-05-27T13:18:03.369Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                                                              "output_tokens": 1, "reasoning_output_tokens": 0}}}}
    lines = [{"type": "session_meta", "payload": {"cwd": cwd, "id": "rollout-test"}},
             {"timestamp": "2026-05-27T13:18:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "go", "images": []}},
             {"timestamp": "2026-05-27T13:18:02Z", "type": "event_msg", "payload": {"type": "agent_message", "message": "R" * reply}}]
    for n in range(filler):
        lines.append({**count, "payload": {**count["payload"], "info": {"total_token_usage": {"input_tokens": n + 1, "cached_input_tokens": 0,
                                                                                               "output_tokens": n + 1, "reasoning_output_tokens": 0}}}})
    lines.append({"timestamp": "2026-05-27T13:18:04Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "u", "last_agent_message": ""}})
    with log.open("w", encoding="utf-8") as stream:
        for number, record in enumerate(lines):
            stream.write(json.dumps(record) + "\n")
            if number % 1000 == 999:
                stream.write("this line is not JSON\n\n")
    return Case(log, CodexLogReader, session="rollout-test")


def droid_log(env: Env, *, turns: int = 500, reply: int = 2000, filler: int = 20000, open_reply: int = 5 * MIB) -> Case:
    """A Droid session (`tests.vendors.test_droid._session_start` / `_msg` / `_write_sidecar`): turns of a prompt, a reply, a tool result
    and the turn's outcome, with a hook's entry, a record of a kind nobody knows and a line that is not JSON among them; then a reply that
    stays open (5 MiB: over the open-text share) through many tool results of the same turn, then the turn's end that delivers it. The
    totals are in the `.settings.json` beside the log."""
    env.patch.setenv("FACTORY_HOME_OVERRIDE", str(env.tmp / ".factory"))
    log = dr._write_session(env.tmp / ".factory" / "sessions", "/w", "a", [])
    block = lambda text: [{"type": "text", "text": text}]  # noqa: E731
    records: list = [dr._session_start("a", "/w")]
    for n in range(turns):
        records += [dr._msg("user", block(f"prompt {n}")), dr._msg("assistant", block("r" * reply)),
                    dr._msg("user", [{"type": "tool_result", "content": "x"}]), {"type": "agent_turn_outcome", "reason": "completed"}]
        if n % 10 == 0:
            records.append(dr._msg("user", [], hookEventName="SessionStart"))
        if n % 25 == 0:
            records.append({"type": "something_new", "id": f"z{n}"})
        if n % 50 == 0:
            records.append("this line is not JSON")
    records += [dr._msg("user", block("go")), dr._msg("assistant", block("R" * open_reply))]
    records += [dr._msg("user", [{"type": "tool_result", "content": str(n)}]) for n in range(filler)]
    records.append({"type": "agent_turn_outcome", "reason": "completed"})
    log.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records), encoding="utf-8")
    dr._write_sidecar(log, inputTokens=10, cacheCreationTokens=5, cacheReadTokens=7, outputTokens=3)
    return Case(log, DroidLogReader)


def muse_log(env: Env, *, usages: int = 3000, reply: int = 6000) -> Case:
    """A Muse session (`tests.vendors.test_muse._envelope` / `_usage_row` / `_run` / `_intake`): the metadata head the working directory and
    build version come from, then usage rows with real-length ids (every 100th an id of 2 KiB, which is source text), the rows that make
    no event among them (a subagent's usage, a row counting nothing, a diagnostic, an intake that is no turn, a line that is not JSON),
    replies over the 4,000-character cap and turns that end."""
    env.patch.setenv("XDG_DATA_HOME", str(env.tmp / "share"))
    log = ms._session_file(env.tmp / "share" / "muse" / "sessions")
    meta = ms._metadata()
    meta["payload"]["record"]["build"] = {"semver": "1.2.3"}
    records: list = [meta, ms._intake("go", 2)]
    for n in range(usages):
        seq = 10 + n * 8
        key = long_key(f"use-{n:08d}") if n % 100 == 0 else f"use-{n:08d}-{'0' * 36}"
        records += [ms._usage_row(key, seq, input_tokens=n + 1), ms._assistant("r" * reply, seq + 1), ms._terminal(seq + 2)]
        if n % 10 == 0:
            records += [ms._usage_row(f"sub-{n}", seq + 3, requester_kind="subagent"), ms._usage_row(f"zero-{n}", seq + 4, input_tokens=0, output_tokens=0),
                        ms._diagnostic(seq + 5)]
        if n % 25 == 0:
            records.append(ms._run({"kind": "side_effect_intent"}, seq + 6))
    lines = [json.dumps(record) + ("\nthis line is not JSON" if number % 500 == 499 else "") for number, record in enumerate(records)]
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Case(log, MuseLogReader)


def kimi_log(env: Env, *, turns: int = 1500, reply: int = 6000) -> Case:
    """A Kimi session (`tests.vendors.test_kimi._session` / `_usage` / `_prompt` / `_text_part`): turns of a prompt (every 50th over the
    4,000-character cap), usage records and a reply over the cap, the turn's end, and among them the lines that make no event (a record of
    no kind, a usage record counting nothing, a line that is not JSON). The last turn is never ended: the wall clock closes it, as the
    times are far in the past. A usage record names itself by its place, so no key of the source is long."""
    env.patch.setenv("KIMI_CODE_HOME", str(env.tmp / ".kimi-code"))
    log = km._session(env.tmp / ".kimi-code", "/work/demo")
    records: list = []
    for n in range(turns):
        time = 1000 + n * 10
        records += [km._prompt("p" * (6000 if n % 50 == 0 else 20), time), km._usage(10 + n, 5, 3, time=time + 1), km._text_part("r" * reply, time=time + 2),
                    km._usage(1, 0, 1, time=time + 3)]
        if n < turns - 1:
            records.append({"type": "turn.ended", "time": time + 4})
        if n % 10 == 0:
            records += [{"type": "unknown", "time": time + 5}, km._usage(0, 0, 0, time=time + 5)]
        if n % 100 == 0:
            records.append("this line is not JSON")
    log.parent.mkdir(parents=True)
    log.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records), encoding="utf-8")
    return Case(log, KimiLogReader)


def pi_log(env: Env, *, turns: int = 1500, reply: int = 6000) -> Case:
    """A Pi session (`tests.vendors.test_pi._header` / `_user` / `_assistant` / `_compaction`): turns of a prompt (every 50th over the
    4,000-character cap) and a reply over the cap, a compaction entry (its usage is top-level) and, among them, the lines that make no event
    (an entry counting nothing, one with no id, a line that is not JSON). The last turn has no successor: the file's modification time, an
    hour in the past, closes it."""
    env.patch.setenv("PI_CODING_AGENT_SESSION_DIR", str(env.tmp / "pi-sessions"))
    env.patch.delenv("PI_CODING_AGENT_DIR", raising=False)
    log = pi_tests._session_file(env.tmp / "pi-sessions")
    records: list = [pi_tests._header()]
    for n in range(turns):
        # The entry id is the usage key, source text: every 100th is over a KiB (but the run below), and the run of turns 600-899 has an id of
        # exactly a KiB for every usage entry (the compactions included), so the 256 newest keys the cursor keeps weigh 256 x 1,027 bytes,
        # over what a request may carry (t36l Y2).
        run = 600 <= n < 900
        kib = lambda prefix: (f"{prefix}{'k' * 1024}")[:1024]  # noqa: E731
        entry_id = kib(f"a-{n}-") if run else long_key(f"a-{n}") if n % 100 == 0 else f"a-{n}"
        reply_entry = pi_tests._assistant(entry_id, input=10 + n, output=5, cache_read=3, cache_write=2)
        reply_entry["message"]["content"][0]["text"] = "r" * reply
        records += [pi_tests._user(f"u-{n}", "p" * (6000 if n % 50 == 0 else 20)), reply_entry]
        if n % 10 == 0:
            records += [pi_tests._compaction(kib(f"c-{n}-") if run else f"c-{n}"), pi_tests._assistant(f"z-{n}", input=0, output=0),
                        pi_tests._assistant("", input=4, output=4)]
        if n % 100 == 0:
            records.append("this line is not JSON")
    log.parent.mkdir(parents=True)
    log.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records), encoding="utf-8")
    quiet = log.stat().st_mtime - 3600
    os.utime(log, (quiet, quiet))
    return Case(log, PiLogReader)


def qwen_log(env: Env, *, turns: int = 1500, reply: int = 6000) -> Case:
    """A Qwen chat file (`tests.vendors.test_qwen._assistant` / `_user`, `test_qwen.py:34-53`): turns of a prompt (every 50th over the 4,000-character cap) and a
    reply over the cap, and among them the records that make no event (an assistant record counting nothing, a prompt another writer put
    there, a launched background task and its notification, a line that is not JSON). The last turn has no successor: the file's
    modification time, an hour in the past, closes it."""
    env.patch.setenv("QWEN_RUNTIME_DIR", str(env.tmp / ".qwen"))
    log = qwen_tests._chat_file(env.tmp / ".qwen")
    records: list = []
    control = "\x01" * 1000
    for n in range(turns):
        prompt = qwen_tests._user(f"u-{n}", "p" * (6000 if n % 50 == 0 else 20))
        # The record id is the usage key, source text: every 100th is over a KiB, and a run of 100 is a thousand control characters each (a
        # JSON escape of six bytes apiece), so the window of 64 keys the cursor keeps serializes to 64 x ~6,000 = ~385 KB, over the 262,144
        # bytes a request may carry. Plain characters cannot reach the bound with a window of 64 (64 x ~1 KB is ~66 KB: the t36l axis passed
        # the pre-Y2 sidecar too), and a sidecar that does not fit the window to the bound fails here (t36m Z3; t36l Y2).
        answer_id = long_key(f"a-{n}") if n % 100 == 0 else f"a-{n}-{control}" if 600 <= n < 700 else f"a-{n}"
        answer = qwen_tests._assistant(answer_id, prompt=10 + n, candidates=5, cached=3, thoughts=2)
        answer["message"] = {"role": "model", "parts": [{"text": "r" * reply}]}
        records += [prompt, answer]
        if n % 10 == 0:
            records += [qwen_tests._assistant(f"z-{n}", prompt=0, candidates=0), qwen_tests._user(f"c-{n}", "tick", subtype="cron"),
                        qwen_tests._rec("tool_result", f"t-{n}", message={"parts": [{"functionResponse": {"response": {
                            "output": f"Background agent launched successfully.\ntask_id: bg{n}\n"}}}]}),
                        qwen_tests._rec("user", f"n-{n}", subtype="notification", message={"parts": [{
                            "text": f"<task-notification><task-id>bg{n}</task-id><status>completed</status></task-notification>"}]})]
        if n % 100 == 0:
            records.append("this line is not JSON")
    log.parent.mkdir(parents=True)
    log.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records), encoding="utf-8")
    quiet = log.stat().st_mtime - 3600
    os.utime(log, (quiet, quiet))
    return Case(log, QwenLogReader)


def grok_log(env: Env, *, turns: int = 1500, reply: int = 6000) -> Case:
    """A Grok transcript (`tests.vendors.test_grok._user` / `_agent` / `_turn`, `test_grok.py:34-66`): turns of a prompt (every 50th over the 4,000-character cap),
    a reply over the cap in two chunks and a completed turn whose event id is real-length (every 100th one of 2 KiB, which is source text),
    and among them the records that make no event (an unknown update, a completed turn counting nothing, another session's turn, a line
    that is not JSON)."""
    env.patch.setenv("GROK_HOME", str(env.tmp / ".grok"))
    log = grok_tests._write(env.tmp / ".grok", [])
    records: list = []
    for n in range(turns):
        base = n * 8
        completed = grok_tests._turn(base + 3, inp=10 + n, out=5)
        if n % 100 == 0:
            completed["params"]["_meta"]["eventId"] = long_key(f"turn-{n:08d}")
        records += [grok_tests._user(base, "p" * (6000 if n % 50 == 0 else 20)), grok_tests._agent(base + 1, "r" * (reply // 2)),
                    grok_tests._agent(base + 2, "r" * (reply // 2)), completed]
        if n % 10 == 0:
            records += [grok_tests._record("tool_call", base + 4), grok_tests._turn(base + 5, inp=0, out=0),
                        grok_tests._turn(base + 6, inp=7, out=2, session_id="other-session")]
        if n % 100 == 0:
            records.append("this line is not JSON")
    log.write_text("".join((record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records), encoding="utf-8")
    return Case(log, GrokLogReader)


def grok_turns_log(env: Env, *, turns: int = 6, models: int = 40_000) -> Case:
    """A Grok transcript for the turn breakdown (`tests.vendors.test_grok._user` / `_record`; oracle: `GrokLogReader.turns_for_session`,
    `cli_vendors/grok.py:395-456`): turns of a prompt (every other one of 6,000 characters: the excerpt keeps 80) and a
    completed turn whose `modelUsage` lists 40,000 models, spelled in DESCENDING name order (a parsed map sorts them, the Python dict keeps
    the order), each with cache counts: a row of about 6 MB, over the ledger, so it comes in pieces; and among them the records that make
    no turn (an unknown update, another session's turn, a line that is not JSON). The last turn names one model with a name of 9 MiB."""
    env.patch.setenv("GROK_HOME", str(env.tmp / ".grok"))
    log = grok_tests._write(env.tmp / ".grok", [])
    lines: list[str] = []
    for n in range(turns):
        base = n * 10
        usage = {"inputTokens": 3 * models, "outputTokens": 2 * models, "modelCalls": models,
                 "modelUsage": {f"model-{models - k:06d}": {"inputTokens": 3, "outputTokens": 2, "cachedReadTokens": 1} for k in range(models)}}
        lines.append(json.dumps(grok_tests._user(base, "p" * (6000 if n % 2 == 0 else 20))))
        lines.append(json.dumps(grok_tests._record("turn_completed", base + 1, stop_reason="end_turn", usage=usage)))
        if n % 3 == 0:
            lines += [json.dumps(grok_tests._record("tool_call", base + 2)),
                      json.dumps(grok_tests._turn(base + 3, inp=7, out=2, session_id="other-session")), "this line is not JSON"]
    nine = f"HEAD{'z' * (9 * MIB)}TAIL"
    lines.append(json.dumps(grok_tests._user(900, "last")))
    lines.append(json.dumps(grok_tests._turn(901, inp=5, out=1, model=nine)))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Case(log, GrokLogReader)


def aider_log(env: Env, *, turns: int = 1500, reply: int = 6000) -> Case:
    """An Aider chat history (`# aider chat started at …` sections, `#### ` prompt lines, plain output, `> ` notes and `> Tokens:` lines; the
    shapes of `tests/vendors/test_aider.py:28-47` and `cli_vendors/aider.py:77-83`): turns of a prompt (every 50th over the 4,000-character cap), a reply over the cap and a token line (counts spelled every way), a new
    section every 100 turns, and among them the lines that make no event (a token line counting nothing, a note, a blank line)."""
    # A per-pane file: its session slugs carry the pane token, where the shared file's carry a hash of its path (which the baseline run, in
    # another directory, would not share).
    log = env.tmp / "proj" / ".aider.chat.history.abcd1234.md"
    log.parent.mkdir(parents=True)
    lines: list[str] = []
    for n in range(turns):
        if n % 100 == 0:
            lines.append(f"# aider chat started at 2024-05-{1 + n // 1000:02d} {(n // 100) % 24:02d}:{n % 60:02d}:00\n")
        lines += [f"#### {'p' * (6000 if n % 50 == 0 else 20)}", "r" * reply, "> Applied edit to a.py",
                  f"> Tokens: {10 + n}{'k' if n % 7 == 0 else ''} sent, {1 + n % 9},{n % 10}00 received.", ""]
        if n % 10 == 0:
            lines.insert(len(lines) - 1, "> Tokens: 0 sent, 0 received.")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Case(log, AiderLogReader)


def copilot_log(env: Env, *, turns: int = 12, reply: int = 800_000) -> Case:
    """Copilot's per-session `events.jsonl` (`tests.vendors.test_copilot._write_session`): turns whose replies are long, one reply that
    stays open past the turn's end line (a tool start between them) and a reply over the open-text share."""
    env.patch.setenv("COPILOT_HOME", str(env.tmp / "copilot-home"))
    _reset_db_warning()
    events = [cp._session_start()]
    for n in range(turns):
        events += [cp._user("go", eid=f"u-{n}"), cp._assistant("x" * reply, eid=f"a-{n}"), cp._event("tool.execution_start", {}, eid=f"t-{n}"),
                   cp._event("assistant.turn_end", {}, eid=f"e-{n}")]
    events += [cp._user("again", eid="u-last"), cp._assistant("y" * (4 * MIB), eid="a-last"), cp._event("assistant.turn_end", {}, eid="e-last")]
    return Case(cp._write_session(env.tmp / "copilot-home", events=events), CopilotLogReader, session=cp._SID)


def copilot_store(env: Env, mode: str) -> Case:
    """Copilot's `session-store.db` (`tests.vendors.test_copilot._store`): 2,100 usage rows (over the cursor's page of 2,000) with a long
    model name (the usage modes), and 300 usage rows beside 300 turns whose replies are long (the activity walks, 256 marks a page)."""
    env.patch.setenv("COPILOT_HOME", str(env.tmp / "copilot-home"))
    _reset_db_warning()
    db = cp._store(env.tmp / "copilot-home")
    cp._add_session(db)
    activity = mode == "activity"

    def grow() -> None:
        con = sqlite3.connect(db)
        con.executemany(
            "INSERT INTO assistant_usage_events (session_id, turn_index, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, created_at) VALUES (?, ?, ?, 10, 5, 0, 0, 0, ?)",
            [(cp._SID, n, "m" if activity else "m" * HEAVY, f"2026-07-27T18:{n // 60 % 60:02d}:{n % 60:02d}.000Z") for n in range(MARKS if activity else USAGE_ROWS)])
        if activity:
            con.executemany("INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp) VALUES (?, ?, ?, ?, ?)",
                            [(cp._SID, n, "go", "x" * TEXT, cp._TS) for n in range(MARKS)])
        con.commit()
        con.close()
    return Case(db, CopilotLogReader, grow, seeds=True, session=cp._SID)


def _opencode_family(env: Env, module, name: str, reader: type, mode: str) -> Case:
    """OpenCode 1.x's `opencode.db` / Kilo's `kilo.db` (`tests.vendors.test_opencode`): for the usage modes 1,100 complete assistant messages
    (a model and a session directory of `HEAVY` characters) with a message that makes no event after each (2,200 rows: over the cursor's
    page of 2,000), every 500th with a message id of 2 KiB; for the activity walks 300 user turns (over a walk's page of 256) with prompts
    of `TEXT` characters."""
    env.patch.setenv("XDG_DATA_HOME", str(env.tmp / "xdg"))
    directory = env.tmp / "xdg" / name
    directory.mkdir(parents=True)
    db = directory / f"{name}.db"
    con = module._create_db(db)
    activity = mode == "activity"
    module._add_session(con, module._SID, "/work" if activity else "/work/" + "d" * HEAVY)

    def grow() -> None:
        rows = []
        for n in range(0 if activity else 1100):
            ident = long_key(f"msg_{n:09d}") if n % 500 == 0 else f"msg_{n:09d}{'a' * 20}"
            rows.append((ident, module._SID, module._NOW_MS, module._NOW_MS,
                         json.dumps(module._assistant_data({"input": 10, "output": 5}, model="m" * HEAVY))))
            rows.append((f"msg_{n:09d}{'b' * 20}", module._SID, module._NOW_MS, module._NOW_MS,
                         json.dumps({"role": "user", "time": {"created": module._NOW_MS}})))
        con.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)", rows)
        for n in range(MARKS if activity else 0):
            module._add_user_turn(con, module._SID, f"msg_turn{n:06d}", "u" * TEXT)
        con.commit()
    grow.__qualname__ = "grow"
    case = Case(db, reader, grow, seeds=True, session=module._SID)
    case.close = con.close  # type: ignore[attr-defined]
    return case


def _opencode_v2_family(env: Env, name: str, reader: type, mode: str) -> Case:
    """OpenCode 2's and Kilo's `session_v2` / `session_message` schema (`tests.vendors.test_opencode_v2._Db`, which creates the store and
    the session): for the usage modes 1,100 completed assistant steps with tokens and a model of `HEAVY` characters, a user message that
    makes no usage after each (2,200 rows: over the cursor's page of 2,000), every 500th step with a message id of 2 KiB; for the activity
    walks 100 user / assistant / idle turns (300 marks: over a walk's page of 256) with texts of `TEXT` characters (the idle row is the
    turn end)."""
    activity = mode == "activity"
    env.patch.setenv("XDG_DATA_HOME", str(env.tmp / "xdg"))
    directory = env.tmp / "xdg" / name
    directory.mkdir(parents=True)
    db = oc2._Db(directory / f"{name}.db")
    db.session(oc2._SID, "/ws")

    def grow() -> None:
        rows: list[tuple] = []

        def add(kind: str, mid: str, data: dict) -> None:
            seq = len(rows)
            data = {"time": {"created": oc2._NOW_MS + seq}, **data}
            rows.append((mid, oc2._SID, kind, seq, oc2._NOW_MS + seq, oc2._NOW_MS + seq, json.dumps(data)))

        def assistant(mid: str, text: str, tokens: dict | None, model: str = "claude-sonnet-5-5") -> None:
            data: dict = {"agent": "build", "model": {"id": model, "providerID": "anthropic"}, "finish": "stop",
                          "content": [{"type": "text", "text": text}] if text else [],
                          "time": {"created": oc2._NOW_MS, "completed": oc2._NOW_MS + 15_000}}
            if tokens is not None:
                data["tokens"] = tokens
            add("assistant", mid, data)

        for n in range(0 if activity else 1100):
            assistant(long_key(f"msg_{n:09d}") if n % 500 == 0 else f"msg_{n:09d}{'a' * 20}", "r", oc2._tokens(), "m" * HEAVY)
            add("user", f"user_{n:09d}", {"text": "u", "files": [], "agents": [], "skills": []})
        for n in range(100 if activity else 0):
            add("user", f"turn_u_{n:06d}", {"text": "u" * TEXT, "files": [], "agents": [], "skills": []})
            assistant(f"turn_a_{n:06d}", "a" * TEXT, None)
            add("idle", f"turn_i_{n:06d}", {"outcome": "succeeded"})
        db.con.executemany("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        db.con.commit()
    grow.__qualname__ = "grow"
    case = Case(db.path, reader, grow, seeds=True, session=oc2._SID)
    case.close = db.con.close  # type: ignore[attr-defined]
    return case


def opencode_store(env: Env, mode: str) -> Case:
    return _opencode_family(env, oc, "opencode", OpencodeLogReader, mode)


def kilo_store(env: Env, mode: str) -> Case:
    return _opencode_family(env, kilo, "kilo", KiloLogReader, mode)


def opencode_v2_store(env: Env, mode: str) -> Case:
    return _opencode_v2_family(env, "opencode", OpencodeLogReader, mode)


def kilo_v2_store(env: Env, mode: str) -> Case:
    return _opencode_v2_family(env, "kilo", KiloLogReader, mode)


def goose_store(env: Env, mode: str) -> Case:
    """Goose's `sessions.db` (`tests.vendors.test_goose`): for the usage modes a ledger of 2,100 rows (over the cursor's page of 2,000) with
    a model of 4,000 characters; for the activity walks 100 turns of a prompt, a reply of 60,000 characters and a turn-context message that
    makes no event (300 messages: over a walk's page of 256)."""
    env.patch.setenv("GOOSE_PATH_ROOT", str(env.tmp / "root"))
    path = env.tmp / "root" / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(gs.SCHEMA)
    gs._session(con, "20261020_1")
    activity = mode == "activity"

    def grow() -> None:
        con.executemany(
            "INSERT INTO usage_ledger (session_id, created_timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)"
            " VALUES (?, ?, ?, 3, 4, NULL, NULL)", [("20261020_1", 1792513603 + n, "m" * 4000) for n in range(0 if activity else USAGE_ROWS)])
        rows = []
        for n in range(100 if activity else 0):
            rows.append(("m", "20261020_1", "user", json.dumps(gs._text("go")), 1792513700 + n * 2, json.dumps({"userVisible": True, "agentVisible": True})))
            rows.append(("m", "20261020_1", "assistant", json.dumps(gs._text("a" * 60_000)), 1792513701 + n * 2, json.dumps({"userVisible": True, "agentVisible": True})))
            rows.append(("m", "20261020_1", "user", json.dumps(gs._text("<turn-context/>")), 1792513701 + n * 2,
                         json.dumps({"userVisible": False, "agentVisible": True, "turnContext": True})))
        con.executemany("INSERT INTO messages (message_id, session_id, role, content_json, created_timestamp, metadata_json) VALUES (?, ?, ?, ?, ?, ?)", rows)
        con.commit()
    case = Case(path, GooseLogReader, grow, seeds=True, session="20261020_1")
    case.close = con.close  # type: ignore[attr-defined]
    return case


def cursor_store(env: Env, mode: str) -> Case:
    """Cursor's `store.db` (`tests.vendors.test_cursor`): 600 blobs (over the walk's page of 512), every other one an assistant reply of
    `TEXT` characters. The store has its `meta.json` (the working directory and the `updatedAtMs` the events are stamped from, which the
    two reads share: with no `meta.json` an event is stamped with the clock of the read)."""
    reader = cur._reader_rooted_at(env.tmp, env.patch)
    chats = env.tmp / ".cursor" / "chats"
    db = cur._make_store(chats, "6" * 32, cur._SID, cwd="/work")

    def grow() -> None:
        cur._append_blobs(db, "x", *[cur._user("go") if n % 2 == 0 else cur._assistant("a" * TEXT) for n in range(600)])
    return Case(db, type(reader), grow, seeds=True, session=cur._SID)


def antigravity_store(env: Env, mode: str) -> Case:
    """Antigravity's conversation `steps` (`tests.vendors.test_antigravity_activity`): 300 assistant steps (over the walk's page of 256) with
    replies of `TEXT` characters."""
    reader, db = agy._db_with(env.tmp, env.patch, [(0, 14, 3, b"", agy._user("hi"))])

    def grow() -> None:
        con = sqlite3.connect(db)
        con.executemany("INSERT INTO steps VALUES (?, ?, ?, ?, ?)",
                        [(n, 15, 3, agy._metadata(1_770_000_000 + n), agy._assistant(reply="a" * TEXT)) for n in range(1, 301)])
        con.commit()
        con.close()
    return Case(db, type(reader), grow, seeds=True, session="aa11bb22")


# ----------------------------------------------------------------------------------------------------- lookup builders
# A lookup answers a flag or a marker map for what the caller asked (`has_session(id)`, `find_sessions_by_marker(markers)`, the header's
# working directory), not a page of the source. Its inputs are the sizes of the caller's: the attribution caller sends every unbound
# marker of the vendor (`attribution.py:643-650`), the stored directories are as long as a directory can be, and a resume id is whatever
# a persisted state holds. `SESSIONS` stored sessions each bind one marker; the first `LONG_DIRS` of them have a directory over the
# field cap (the binding is cut, as the baseline's is not).
SESSIONS = 200
LONG_DIRS = 20
ASKED = 5_000
CURSOR_STORES = 100


def _stored_dir(n: int) -> str:
    return f"/work/{n:04d}/" + "d" * (20_000 if n < LONG_DIRS else HEAVY)


def _asked(session_id: Callable[[int], str]) -> dict:
    """What a lookup cell asks: `has_session` for two stored ids, one that is not stored and one of 1 MiB; `identify` for 5,000 markers,
    the first `SESSIONS` of which are in the store."""
    return {"sessions": (session_id(0), session_id(SESSIONS - 1), "ses_missing", "x" * MIB),
            "markers": tuple(f"at-pane:p{n:04d}" for n in range(ASKED))}


def goose_lookup(env: Env, mode: str) -> Case:
    """Goose's `sessions.db`: 200 sessions with a working directory of up to 20,000 characters, each with a user message holding a marker."""
    env.patch.setenv("GOOSE_PATH_ROOT", str(env.tmp / "root"))
    path = env.tmp / "root" / "data" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(gs.SCHEMA)

    def grow() -> None:
        for n in range(SESSIONS):
            gs._session(con, f"s{n:04d}", cwd=_stored_dir(n))
            gs._message(con, f"s{n:04d}", "user", gs._text(f"at-pane:p{n:04d} hello"))
    case = Case(path, GooseLogReader, grow, **_asked(lambda n: f"s{n:04d}"))
    case.close = con.close  # type: ignore[attr-defined]
    return case


def _opencode_lookup(env: Env, module, name: str, reader: type) -> Case:
    """OpenCode 1.x's / Kilo's `session` + `message` + `part`: 200 top-level sessions, each with a user turn holding a marker."""
    env.patch.setenv("XDG_DATA_HOME", str(env.tmp / "xdg"))
    directory = env.tmp / "xdg" / name
    directory.mkdir(parents=True)
    db = directory / f"{name}.db"
    con = module._create_db(db)

    def grow() -> None:
        for n in range(SESSIONS):
            module._add_session(con, f"ses_{n:04d}", _stored_dir(n))
            module._add_user_turn(con, f"ses_{n:04d}", f"msg_{n:04d}", f"at-pane:p{n:04d} hello")
    case = Case(db, reader, grow, **_asked(lambda n: f"ses_{n:04d}"))
    case.close = con.close  # type: ignore[attr-defined]
    return case


def _opencode_v2_lookup(env: Env, name: str, reader: type) -> Case:
    """OpenCode 2's / Kilo's `session_v2` + `session_message`: 200 top-level sessions, each with a user message holding a marker."""
    env.patch.setenv("XDG_DATA_HOME", str(env.tmp / "xdg"))
    directory = env.tmp / "xdg" / name
    directory.mkdir(parents=True)
    db = oc2._Db(directory / f"{name}.db")

    def grow() -> None:
        for n in range(SESSIONS):
            db.session(f"ses_{n:04d}", _stored_dir(n))
            db.user(f"ses_{n:04d}", f"at-pane:p{n:04d} hello")
    case = Case(db.path, reader, grow, **_asked(lambda n: f"ses_{n:04d}"))
    case.close = db.con.close  # type: ignore[attr-defined]
    return case


def cursor_lookup(env: Env, mode: str) -> Case:
    """Cursor's stores: 100 sessions (a `store.db` each) with a marker typed into their blobs."""
    reader = cur._reader_rooted_at(env.tmp, env.patch)
    chats = env.tmp / ".cursor" / "chats"

    def grow() -> None:
        for n in range(CURSOR_STORES):
            cur._make_store(chats, "6" * 32, str(uuid.UUID(int=n + 1)), marker_text=f"kickoff\nsession marker: at-pane:p{n:04d}\n")
    return Case(chats, type(reader), grow, markers=_asked(str)["markers"])


def codex_header(env: Env, mode: str) -> Case:
    """A Codex rollout whose header names a working directory of 20,000 characters (the baseline reads it whole)."""
    case = codex_log(env, filler=10, reply=10, cwd="/work/" + "c" * 20_000)
    case.header = True
    return case


# The table. A cell is a vendor's layout (its slot among `FACTS[vendor].observer.layouts`) and the modes that layout lists that read a log
# or a store; `pages` names the modes whose input is built to need more than one pass.
#
# Axes that do not apply, and why (every axis not named here is in the cell's input):
# - a dedup key over 1 KiB: only where the key is text from the source (Claude's request id, OpenCode / Kilo message ids). Copilot's,
#   Goose's, Cursor's and Antigravity's keys are row numbers or offsets; Codex's and Copilot-log activity keys are line numbers.
# - rows that make no event: only where the layout has them (Claude's user / no-usage / non-JSON lines, Codex's and Copilot-log's
#   non-JSON lines, OpenCode / Kilo's user messages, Goose's turn-context messages); the Copilot store, Cursor and Antigravity read
#   every row.
# - a long open turn: the reply text an activity pass carries between passes is Codex's and Copilot's; Claude, the stores' walks
#   and Goose / Cursor / Antigravity deliver a reply in the pass that reads it.
CELLS: list[Cell] = [
    Cell("aider", "aider", 0, lambda e, m: aider_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("claude", "claude", 0, lambda e, m: claude_log(e), ("usage", "session_usage"), ("usage", "session_usage")),
    Cell("claude-activity", "claude", 0, lambda e, m: claude_log(e, model=10, text=4000), ("activity",), ("activity",)),
    Cell("claude-turns", "claude", 0, lambda e, m: claude_log(e, records=15_000, model=600, calls=True, long_calls=True), ("turns",), ("turns",)),
    Cell("codex", "codex", 0, lambda e, m: codex_log(e), ("activity", "usage", "session_usage"), ("activity",)),
    Cell("droid", "droid", 0, lambda e, m: droid_log(e), ("activity", "session_usage"), ("activity",)),
    Cell("grok", "grok", 0, lambda e, m: grok_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("grok-turns", "grok", 0, lambda e, m: grok_turns_log(e), ("turns",), ("turns",)),
    Cell("kimi", "kimi", 0, lambda e, m: kimi_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("muse", "muse", 0, lambda e, m: muse_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("pi", "pi", 0, lambda e, m: pi_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("qwen", "qwen", 0, lambda e, m: qwen_log(e), ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("copilot-store", "copilot", 0, copilot_store, ("activity", "usage", "session_usage"), ("activity", "usage", "session_usage")),
    Cell("copilot-log", "copilot", 1, lambda e, m: copilot_log(e), ("activity", "usage", "session_usage"), ("activity",)),
    Cell("opencode-v2", "opencode", 0, opencode_v2_store, ("usage", "legacy", "activity"), ("usage", "legacy", "activity")),
    Cell("opencode", "opencode", 1, opencode_store, ("usage", "legacy", "activity"), ("usage", "legacy", "activity")),
    Cell("kilo-v2", "kilo", 0, kilo_v2_store, ("usage", "legacy", "activity"), ("usage", "legacy", "activity")),
    Cell("kilo", "kilo", 1, kilo_store, ("usage", "legacy", "activity"), ("usage", "legacy", "activity")),
    Cell("goose", "goose", 0, goose_store, ("usage", "session_usage", "activity"), ("usage", "session_usage", "activity")),
    Cell("cursor", "cursor", 0, cursor_store, ("activity",), ("activity",)),
    Cell("antigravity", "antigravity", 0, antigravity_store, ("activity",), ("activity",)),
    # The lookups (U4): `has_session` and `identify` of every layout that declares them, over inputs of the caller's size. A lookup is
    # one answer, so none is built to need a second pass.
    Cell("codex-lookup", "codex", 0, codex_header, ("identify",), ()),
    Cell("goose-lookup", "goose", 0, goose_lookup, ("has_session", "identify"), ()),
    Cell("cursor-lookup", "cursor", 0, cursor_lookup, ("identify",), ()),
    Cell("opencode-v2-lookup", "opencode", 0, lambda e, m: _opencode_v2_lookup(e, "opencode", OpencodeLogReader), ("has_session", "identify"), ()),
    Cell("opencode-lookup", "opencode", 1, lambda e, m: _opencode_lookup(e, oc, "opencode", OpencodeLogReader), ("has_session", "identify"), ()),
    Cell("kilo-v2-lookup", "kilo", 0, lambda e, m: _opencode_v2_lookup(e, "kilo", KiloLogReader), ("has_session", "identify"), ()),
    Cell("kilo-lookup", "kilo", 1, lambda e, m: _opencode_lookup(e, kilo, "kilo", KiloLogReader), ("has_session", "identify"), ()),
]

#: Modes whose layout records no usage (`usage: none`): the usage modes answer nothing and the reader keeps them (the sidecar is not
#: asked), so nothing is read and nothing is paged. Derived from the declarations by the coverage test.
NON_READING: frozenset[tuple[str, int, str]] = frozenset(
    (vendor, 0, mode) for vendor in ("cursor", "antigravity") for mode in ("usage", "session_usage"))

#: The lookups (`identify`, `has_session`) answer a flag or a marker map for what the caller asked, not a page of the source: they are
#: cells like the others (U4), but a string they cut is cut without a `truncated` report (a binding is cut at the field cap where it is read).
LOOKUPS = frozenset({"identify", "has_session"})


# ------------------------------------------------------------------------------------------------------------ coverage

def _declared() -> set[tuple[str, int, str]]:
    """Every `(vendor, layout slot, mode)` a declaration lists."""
    return {(vendor, slot, str(mode.value)) for vendor, facts in FACTS.items() if facts.observer
            for slot, layout in enumerate(facts.observer.layouts) for mode in layout.modes}


def test_every_declared_triple_is_a_cell_or_named_as_not_read():
    declared = _declared()
    covered = {(cell.vendor, cell.slot, mode) for cell in CELLS for mode in cell.modes}
    missing = declared - covered - NON_READING
    assert not missing, f"declared (vendor, layout slot, mode) with no cell in the bound matrix (add one, with every axis that applies): {sorted(missing)}"
    stale = (covered | NON_READING) - declared
    assert not stale, f"cells for something no declaration lists: {sorted(stale)}"
    derived = {(vendor, slot, mode) for vendor, facts in FACTS.items() if facts.observer
               for slot, layout in enumerate(facts.observer.layouts) if layout.usage == "none"
               for mode in (str(m.value) for m in layout.modes) if mode in ("usage", "session_usage", "legacy")}
    assert derived == NON_READING, "NON_READING is exactly the usage modes of the layouts that record no usage"
    assert not (covered & NON_READING), "a mode that reads nothing is not also a cell"


# ------------------------------------------------------------------------------------------------------------ drivers

def _bound(key: str) -> str:
    """The key as the core sends it: itself, or over `KEY_BYTES` the digest (`bound_key`)."""
    return key if len(key.encode()) <= KEY_BYTES else f"long::{hashlib.blake2b(key.encode(), digest_size=32).hexdigest()}"


class Cuts:
    """The strings the core delivered cut (a head and a tail of the baseline's string, around the marker)."""

    def __init__(self) -> None:
        self.count = 0


def _same(got, want, cuts: Cuts, where: str = "") -> None:
    if isinstance(want, dict):
        assert isinstance(got, dict) and sorted(got) == sorted(want), f"{where}: fields differ: {sorted(got) if isinstance(got, dict) else got!r} != {sorted(want)}"
        for name in want:
            _same(got[name], want[name], cuts, f"{where}.{name}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), f"{where}: {len(got)} entries, the baseline has {len(want)}"
        for number, (g, w) in enumerate(zip(got, want)):
            _same(g, w, cuts, f"{where}[{number}]")
    elif isinstance(want, str) and got != want:
        cut = isinstance(got, str) and len(got) < len(want) and "\n…\n" in got
        assert cut and want.startswith(got[:EDGE]) and want.endswith(got[-EDGE:]), (
            f"{where}: {str(got)[:60]!r} ({len(got)}) is not the baseline's {want[:60]!r} ({len(want)}), nor its head and tail")
        cuts.count += 1
    else:
        assert got == want, f"{where}: {got!r} != {want!r}"


def _plain(event) -> dict:
    """An event as a dict, without its cursor (a checkpoint is the core's own, not the vendor's); `raw` and the times are compared."""
    plain = dataclasses.asdict(event)
    plain.pop("checkpoint", None)
    if isinstance(plain.get("dedup_key"), str):
        # Droid's named difference (t36b): the core keys a cumulative total `<vendor>_cumulative::...` where the baseline's was `usage::...`.
        plain["dedup_key"] = _bound(plain["dedup_key"].replace("droid_cumulative::", "usage::", 1))
    return plain


def _header_session(reader, path: Path) -> str:
    """The session id a rollout's header names (t36k V6, A6): what `NativeHeaderIdentity.path_identity` takes from the sidecar's `identify`
    answer (its second field), and what the baseline's `path_identity` reads from the first 512 KiB of the file."""
    if isinstance(reader, cli_runtime_native.NativeHeaderIdentity):
        return reader._native(path, Mode.IDENTIFY)["native_session_id"]
    return _session_meta_resume_id(path.read_text(encoding="utf-8", errors="ignore")[:524_288])


def drive(reader, case: Case, mode: str, *, native: bool):
    """The result of one mode the way production calls it: ONE call of the reader's method, which goes on while the sidecar says `more`
    (`native`: a second call must find nothing left). The baseline (`native=False`) is read to its end by calling its method until it
    finds nothing: its per-call bound is not the code under test. Cursor's baseline restarts its 1 ms stamp offset at every call, so
    each call after the first is given the epoch the events before it ended at (the named difference in the module docstring)."""
    path = case.path
    if mode == "turns":
        return [dataclasses.asdict(turn) for turn in reader.turns_for_session(path, case.session)]
    if mode in LOOKUPS:
        case.grow()
        if mode == "has_session":
            return [reader.has_session(session) for session in case.sessions]
        if case.header:
            return {"cwd": reader._cwd_from_meta(path), "native_session_id": _header_session(reader, path)}
        return {marker: list(binding) for marker, binding in reader.find_sessions_by_marker(case.markers).items()}
    bag: set[str] = {f"old::{n:030d}" for n in range(BAG)}
    if mode == "activity" and case.seeds:
        reader.parse_activity(path, bag)  # the first pass of a store only plants its walks
    case.grow()
    epoch = reader._store_epoch(path) if mode == "activity" and not native and getattr(reader, "vendor", "") == "cursor" else None
    events: list = []
    checkpoint: dict = {}
    for call in range(1, 1000):
        if epoch is not None:
            reader._store_epoch = lambda _path, _epoch=epoch, _done=len(events): _epoch + _done * 0.001
        if mode == "usage":
            result = reader.parse_incremental(path, checkpoint)
            checkpoint, page = result.checkpoint, result.events
        elif mode == "activity":
            page = reader.parse_activity(path, bag)
        else:
            page = reader.parse_session_file(path, bag)
        if not page:
            break
        assert not native or call == 1, f"{mode}: the call left {len(page)} events behind after delivering {len(events)}"
        events += page
    else:
        raise AssertionError(f"{mode}: the baseline still found events after 999 calls")
    return [_plain(event) for event in events]


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    sidecar = wire(monkeypatch, tmp_path / "sidecar")
    try:
        yield sidecar
    finally:
        sidecar.close()


def _layout_read(cell: Cell, path, answer: dict, where: str) -> None:
    """The layout the sidecar read is the cell's slot: the file is the slot's store file, and where the file has more than one candidate
    layout (the reader's own rule, `NativeObservation._candidates`) the answer must name the layout it read, and name the slot. A file with
    one candidate needs no answer; the file name is its check."""
    layouts = FACTS[cell.vendor].observer.layouts
    name = Path(path).name
    assert layouts[cell.slot].store_file in (None, name), f"{where}: {name} is not the file of slot {cell.slot} ({layouts[cell.slot].store_file})"
    if sum(1 for layout in layouts if layout.store_file in (None, name)) > 1:
        assert answer.get("layout") == cell.slot, f"{where}: the file has more than one candidate layout; the answer's layout is {answer.get('layout')!r}, the cell is slot {cell.slot}"


def test_the_layout_check_fails_on_an_answer_that_names_no_layout_of_a_file_with_two():
    """V4 (t36k, B2 of the t36j checkpoint): a file that may hold two schemas (`opencode.db`, `kilo.db`) has two candidate layouts that
    share its name, so only the answer's `layout` tells the cell's slot from the other. An answer that names none must fail the cell, and
    one that names the other slot too; a file with one layout (Claude's log, Goose's store, Copilot's two files) needs no answer."""
    for name in ("opencode", "opencode-v2", "kilo", "kilo-v2"):
        cell = next(cell for cell in CELLS if cell.name == name)
        path = Path("/x") / (FACTS[cell.vendor].observer.layouts[cell.slot].store_file or "x")
        _layout_read(cell, path, {"layout": cell.slot}, name)
        with pytest.raises(AssertionError, match="layout"):
            _layout_read(cell, path, {}, name)
        with pytest.raises(AssertionError, match="layout"):
            _layout_read(cell, path, {"layout": 1 - cell.slot}, name)
    single = next(cell for cell in CELLS if cell.name == "claude")
    _layout_read(single, Path("/x/a.jsonl"), {}, "claude")
    with pytest.raises(AssertionError, match="not the file"):
        _layout_read(next(cell for cell in CELLS if cell.name == "goose"), Path("/x/other.db"), {}, "goose")


@pytest.mark.parametrize("cell", [pytest.param(cell, id=cell.name) for cell in CELLS])
def test_every_mode_of_every_layout_stays_inside_both_caps_pages_where_built_to_and_equals_the_baseline(cell, tmp_path, monkeypatch, sidecar):
    answers: list[dict] = []
    original = cli_runtime_native.observe_core

    def measured(vendor, path, mode, *, checkpoint=None, seen=None, state=None, validate=None, match=None, **extra):
        # The request as the reader sends it (`request_of` is what `admit` measures), with the vendor the frame adds.
        request = request_of(path, mode, checkpoint=checkpoint, seen=seen, state=state, **extra)
        size = _compact({"vendor": vendor, **request})
        assert size <= REQUEST_CAP, f"{cell.name} {mode}: a request of {size} bytes is over the {REQUEST_CAP}-byte meta cap"
        answer = original(vendor, path, mode, checkpoint=checkpoint, seen=seen, state=state, validate=validate, match=match, **extra)
        _layout_read(cell, path, answer, f"{cell.name} {mode}")
        answers.append({"mode": str(mode), "request": size, "answer": _compact(answer), "more": bool(answer.get("more")),
                        "truncated": len(answer.get("truncated") or [])})
        assert answers[-1]["answer"] <= ANSWER_CAP, f"{cell.name} {mode}: an answer of {answers[-1]['answer']} bytes is over the {ANSWER_CAP}-byte cap"
        return answer

    monkeypatch.setattr(cli_runtime_native, "observe_core", measured)
    for mode in cell.modes:
        env_dir = tmp_path / mode
        env_dir.mkdir()
        case = build_case(cell, Env(env_dir, monkeypatch), mode)
        native = cli_runtime_native.native_reader(case.reader, cell.vendor)()
        before = len(answers)
        got = drive(native, case, mode, native=True)
        mine = [a for a in answers[before:] if a["mode"] == mode]
        # The baseline is the vendor's own Python reader on the same input built again in its own directory (its paths are mapped back).
        base_dir = tmp_path / f"{mode}-baseline"
        base_dir.mkdir()
        base_case = build_case(cell, Env(base_dir, monkeypatch), mode)
        want = json.loads(json.dumps(drive(base_case.reader(), base_case, mode, native=False)).replace(str(base_dir), str(env_dir)))
        assert mine, f"{cell.name} {mode}: the sidecar was not asked for this mode"
        if mode in cell.pages:
            assert len(mine) >= 2, f"{cell.name} {mode}: the input is built to need more than one pass and the sidecar answered {len(mine)}"
        cuts = Cuts()
        _same(got, want, cuts, f"{cell.name} {mode}")
        if mode == "has_session":
            assert True in got and False in got, f"{cell.name} {mode}: the ids asked are stored and not stored; the answers were {got}"
        elif mode == "identify" and not case.header:
            assert got, f"{cell.name} {mode}: the markers asked are in the store and none was found"
            if case.sessions:  # a store that binds a marker to the session's directory: the long ones are cut at the field cap
                assert cuts.count == LONG_DIRS, f"{cell.name} {mode}: {cuts.count} bindings were cut, {LONG_DIRS} directories are over the cap"
        if mode == "identify" and case.header:
            assert cuts.count == 1, f"{cell.name} {mode}: the 20,000-character working directory was not cut at the field cap"
        if cuts.count and mode not in LOOKUPS:
            assert sum(a["truncated"] for a in mine) >= 1, f"{cell.name} {mode}: {cuts.count} string(s) were cut and the answers reported no cut"
        for closer in (case, base_case):
            getattr(closer, "close", lambda: None)()


def build_case(cell: Cell, env: Env, mode: str) -> Case:
    return cell.build(env, mode)


@pytest.mark.parametrize("vendor,slot,mode", [pytest.param(*triple, id="-".join(map(str, triple))) for triple in sorted(NON_READING)])
def test_a_mode_whose_layout_records_no_usage_is_not_read_through_the_sidecar(vendor, slot, mode, tmp_path, monkeypatch, sidecar):
    cell = next(cell for cell in CELLS if cell.vendor == vendor and cell.slot == slot)
    case = build_case(cell, Env(tmp_path, monkeypatch), mode)
    native = cli_runtime_native.native_reader(case.reader, vendor)()
    asked = len(sidecar.asked)
    case.grow()
    if mode == "usage":
        native.parse_incremental(case.path, {})
    else:
        native.parse_session_file(case.path, set())
    assert not [request for request in sidecar.asked[asked:] if str(getattr(request["mode"], "value", request["mode"])) == mode], (
        f"{vendor} {mode}: a layout that records no usage is not read through the sidecar")
