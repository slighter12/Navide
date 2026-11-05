"""The one generic native reader: what it sends, what it keeps for Python, and the named compatibility rules.

No sidecar runs here. `observe_core` is replaced by a recorder, so these tests pin the Python side of the seam: the
request the reader builds (no file read, no anchor bytes, dedup keys and typed state apart), the mode each method asks
for, and that every choice the reader makes comes from the vendor's declaration.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team_backend import cli_runtime_native, cli_runtime_observation, cli_runtime_state
from agent_team_backend.cli_vendors._vendor_facts import FACTS, LegacyState
from agent_team_backend.cli_vendors.registry import VENDORS

CORE_VENDORS = sorted(key for key, facts in FACTS.items() if facts.observer and facts.observer.core)


class Recorder:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, vendor, path, mode, **kwargs):
        self.calls.append((vendor, str(path), mode, kwargs))
        return self.answer(mode)


def reader(vendor, monkeypatch, answer):
    recorder = Recorder(answer)
    monkeypatch.setattr(cli_runtime_native, "observe_core", recorder)
    return cli_runtime_native.native_reader(VENDORS[vendor].make_log_reader, vendor)(), recorder


def empty(mode):
    # `layout` is what the sidecar answers for a store file that may hold more than one schema (OpenCode and Kilo).
    return {"events": [], "checkpoint": {"offset": 0}, "seen": [], "state": {}, "turns": [], "found": False,
            "markers": {}, "layout": 0}


def test_the_core_vendors_are_the_ones_whose_declaration_has_a_core_block():
    assert CORE_VENDORS == ["aider", "antigravity", "claude", "codex", "copilot", "cursor", "droid", "goose", "grok", "kilo", "kimi", "muse", "opencode", "pi", "qwen"]
    assert all(FACTS[vendor].observer.core.modes for vendor in CORE_VENDORS)


@pytest.mark.parametrize("vendor", CORE_VENDORS)
def test_a_core_vendor_reader_is_its_own_discovery_reader_with_the_observed_methods_in_front(vendor, monkeypatch):
    native, _ = reader(vendor, monkeypatch, empty)
    assert isinstance(native, VENDORS[vendor].make_log_reader)
    assert type(native).__name__.startswith("Native")
    assert type(native).parse_incremental is cli_runtime_native.NativeObservation.parse_incremental


@pytest.mark.parametrize("vendor,mixin", [
    ("claude", None), ("codex", cli_runtime_native.NativeHeaderIdentity), ("antigravity", None), ("copilot", None), ("droid", None), ("muse", None), ("kimi", None), ("pi", None), ("qwen", None), ("grok", None), ("aider", None),
    ("opencode", cli_runtime_native.NativeStore), ("kilo", cli_runtime_native.NativeStore),
    ("goose", cli_runtime_native.NativeStore), ("cursor", cli_runtime_native.NativeStore)])
def test_the_identity_source_the_declaration_names_picks_the_mixin(vendor, mixin, monkeypatch):
    native, _ = reader(vendor, monkeypatch, empty)
    extras = (cli_runtime_native.NativeStore, cli_runtime_native.NativeHeaderIdentity)
    assert [cls for cls in extras if isinstance(native, cls)] == ([mixin] if mixin else [])


def test_the_request_names_the_declared_parser_id_and_carries_no_anchor_bytes(monkeypatch):
    sent = {}

    def submit(vendor, parser, request, **kwargs):
        sent.update(vendor=vendor, parser=parser, request=request, kwargs=kwargs)
        return empty("usage")

    monkeypatch.setattr(cli_runtime_observation, "_submit", submit)
    missing = "/nonexistent/never-opened.jsonl"
    cli_runtime_observation.observe_core("kilo", missing, "usage", checkpoint={"offset": 9, "anchor": "ab"})
    assert (sent["vendor"], sent["parser"]) == ("kilo", "rust-kilo-sqlite-v1")
    assert sent["request"] == {"path": missing, "mode": "usage", "checkpoint": {"offset": 9, "anchor": "ab"},
                               "seen": [], "state": {}}
    assert sent["kwargs"].get("expected", b"") == b"", "the sidecar reads and hashes the file; this side never does"


def test_each_method_asks_for_its_own_mode(monkeypatch):
    native, recorder = reader("codex", monkeypatch, empty)
    native.parse_incremental("/x", {})
    native.parse_session_file("/x", set())
    native.parse_activity("/x", set())
    assert [call[2] for call in recorder.calls] == ["usage", "session_usage", "activity"]


# ---- the named compatibility rules: each keeps one behaviour of the baseline Python reader --------------------------

#: What Python reads. The rules only a JSONL log reads are in the layout (the sidecar reads those), and a vendor that replays a
#: whole store has the `legacy` mode instead of a flag.
COMPAT = {
    "claude": dict(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=False),
    "codex": dict(absent_file_is_empty=False, no_python_turn_fallback=True, store_error_reported=False),
    "opencode": dict(absent_file_is_empty=False, no_python_turn_fallback=False, store_error_reported=False),
    # The store's unreadable-this-cycle warning and the "nothing to read" for a log that is not there yet.
    "copilot": dict(absent_file_is_empty=True, no_python_turn_fallback=False, store_error_reported=True),
}
COMPAT["kilo"] = COMPAT["goose"] = COMPAT["cursor"] = COMPAT["antigravity"] = COMPAT["muse"] = COMPAT["kimi"] = COMPAT["opencode"]
# A log that is not there yet is nothing to read (`droid.py` parse_* on a missing path), like Claude's.
COMPAT["droid"] = COMPAT["pi"] = COMPAT["qwen"] = COMPAT["grok"] = COMPAT["aider"] = COMPAT["claude"]

#: What the sidecar reads, per vendor, from the declaration's layout. A store layout has none.
JSONL_RULES = {
    "claude": [dict(missing_identity_rotates=True, invalid_utf8_fails=True, non_object_fails=True,
                    activity_non_object_fails=True, totals_never_decrease=False, water_survives_replacement=True)],
    "codex": [dict(missing_identity_rotates=False, invalid_utf8_fails=False, non_object_fails=False,
                   activity_non_object_fails=True, totals_never_decrease=False, water_survives_replacement=True)],
    "copilot": [None, dict(missing_identity_rotates=True, invalid_utf8_fails=False, non_object_fails=False,
                           activity_non_object_fails=False, totals_never_decrease=True, water_survives_replacement=True)],
    "droid": [dict(missing_identity_rotates=False, invalid_utf8_fails=True, non_object_fails=False,
                   activity_non_object_fails=False, totals_never_decrease=False, water_survives_replacement=True)],
    "muse": [dict(missing_identity_rotates=True, invalid_utf8_fails=True, non_object_fails=False,
                  activity_non_object_fails=False, totals_never_decrease=False, water_survives_replacement=True)],
    "kimi": [dict(missing_identity_rotates=True, invalid_utf8_fails=True, non_object_fails=True,
                  activity_non_object_fails=True, totals_never_decrease=False, water_survives_replacement=True)],
    "pi": [dict(missing_identity_rotates=True, invalid_utf8_fails=True, non_object_fails=False,
                activity_non_object_fails=False, totals_never_decrease=False, water_survives_replacement=True)],
    "qwen": [dict(missing_identity_rotates=True, invalid_utf8_fails=True, non_object_fails=True,
                  activity_non_object_fails=True, totals_never_decrease=False, water_survives_replacement=True)],
    "aider": [dict(missing_identity_rotates=True, invalid_utf8_fails=False, non_object_fails=False,
                   activity_non_object_fails=False, totals_never_decrease=False, water_survives_replacement=True)],
    "grok": [dict(missing_identity_rotates=True, invalid_utf8_fails=False, non_object_fails=False,
                  activity_non_object_fails=False, totals_never_decrease=False, water_survives_replacement=False)],
    "opencode": [None, None],
    "kilo": [None, None],
    "goose": [None],
    "cursor": [None],
    "antigravity": [None],
}


@pytest.mark.parametrize("vendor", CORE_VENDORS)
def test_compat_each_vendor_declares_exactly_the_baseline_behaviours_it_keeps(vendor):
    assert FACTS[vendor].observer.core.compat._asdict() == COMPAT[vendor]
    declaration = json.loads((Path(__file__).resolve().parents[2] / "shared" / "agent-cli" / f"{vendor}.json").read_text())
    assert [layout.get("rules") for layout in declaration["observer"]["layouts"]] == JSONL_RULES[vendor]
    assert "legacy_replay" not in declaration["observer"]["core"]["compat"]


def test_a_vendor_whose_mode_list_names_legacy_replays_a_whole_store_with_it(monkeypatch):
    native, recorder = reader("opencode", monkeypatch, empty)
    native.parse_session_file("/x/opencode.db", set())
    assert recorder.calls[0][2] == "legacy"


def test_compat_claude_treats_a_missing_file_as_nothing_to_read_and_the_others_do_not(monkeypatch, tmp_path):
    gone = tmp_path / "gone.jsonl"
    claude, claude_calls = reader("claude", monkeypatch, empty)
    assert claude.parse_session_file(gone, set()) == [] and claude.parse_activity(gone, set()) == []
    assert claude.turns_for_session(gone) == [] and not claude_calls.calls
    codex, codex_calls = reader("codex", monkeypatch, empty)
    codex.parse_activity(gone, set())
    assert [call[2] for call in codex_calls.calls] == ["activity"]


def _two_schema_store(root: Path) -> Path:
    """A real `opencode.db` that holds both schemas of the real declaration (an upgraded store)."""
    import sqlite3
    store = root / "opencode.db"
    connection = sqlite3.connect(store)
    connection.executescript("CREATE TABLE session(id); CREATE TABLE message(id);"
                             "CREATE TABLE session_v2(id); CREATE TABLE session_message(id);")
    connection.commit()
    connection.close()
    return store


def test_the_store_is_never_opened_on_this_side_to_choose_among_a_files_layouts(monkeypatch, tmp_path):
    """The sidecar chooses on its own connection (`opens_the_store_once` in `observe/rows_tests.rs` and the OpenCode adapter
    tests count that side); this side counts every `sqlite3.connect` it makes while it asks all five questions of a real
    two-schema store with the real OpenCode declaration, and makes none."""
    import sqlite3
    store = _two_schema_store(tmp_path)
    opened = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: opened.append(args) or real_connect(*args, **kwargs))
    native, recorder = reader("opencode", monkeypatch, lambda mode: {**empty(mode), "layout": 1})
    native._stores = lambda: [store]
    native.parse_incremental(store, {})
    native.parse_session_file(store, set())
    native.parse_activity(store, set())
    native.has_session("ses_x")
    native.find_sessions_by_marker(["at-pane:p"])
    assert [call[2] for call in recorder.calls] == ["usage", "legacy", "activity", "has_session", "identify"]
    assert opened == [], "choosing a layout opens nothing here"
    assert native._layout(store) is FACTS["opencode"].observer.layouts[0], "this side asks with the file's first layout"


def test_the_answer_names_the_layout_the_store_was_read_with_and_a_file_with_two_candidates_needs_one(monkeypatch, tmp_path):
    store = tmp_path / "opencode.db"
    native, _ = reader("opencode", monkeypatch, lambda mode: {k: v for k, v in empty(mode).items() if k != "layout"})
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR: observation answered no layout"):
        native.parse_incremental(store, {})
    native, _ = reader("opencode", monkeypatch, lambda mode: {**empty(mode), "layout": 5})
    with pytest.raises(RuntimeError, match="PROTOCOL_ERROR: observation answered no layout"):
        native.parse_incremental(store, {})
    assert native._chosen(store, {"layout": 1}) is FACTS["opencode"].observer.layouts[1]
    codex, _ = reader("codex", monkeypatch, lambda mode: {k: v for k, v in empty(mode).items() if k != "layout"})
    codex.parse_incremental(tmp_path / "rollout.jsonl", {})  # one candidate: nothing to answer
    assert native._layout(tmp_path / "other.db") is None, "a file no layout is named for is nobody's to read"


def test_compat_copilot_warns_once_about_an_unreadable_store_and_answers_no_data(monkeypatch, tmp_path, caplog):
    import logging
    from agent_team_backend.cli_vendors import copilot as copilot_vendor
    failed = {**empty("usage"), "error": "sqlite error 1 in `SELECT 1`", "checkpoint": {"row_id": 9}, "seen": ["new"]}
    copilot, _ = reader("copilot", monkeypatch, lambda mode: failed)
    store = tmp_path / "session-store.db"
    store.write_bytes(b"")
    copilot_vendor._reset_db_warning()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            result = copilot.parse_incremental(store, {"row_id": 3})
            assert (result.events, result.checkpoint) == ([], {"row_id": 3}), "the checkpoint stays where it was"
            bag = {"k"}
            assert copilot.parse_session_file(store, bag) == [] and bag == {"k"}, "a failed replay keeps the caller's bag"
            assert copilot.parse_activity(store, set()) == [], "a failed activity pass is not an exception for a store"
    assert len([row for row in caplog.records if "session-store" in row.getMessage()]) == 1


def test_a_vendor_that_does_not_declare_the_store_error_rule_still_raises_a_failed_activity_pass(monkeypatch):
    failed = {**empty("activity"), "error": "boom", "seen": [], "state": {"water": 5}}
    codex, _ = reader("codex", monkeypatch, lambda mode: failed)
    with pytest.raises(RuntimeError, match="boom"):
        codex.parse_activity("/rollout.jsonl", set())


def test_a_pass_that_dropped_pending_rows_says_so_once_at_info_level(monkeypatch, caplog):
    import logging
    dropped = [{"table": "steps", "id": 10, "why": "gone"}, {"table": "steps", "id": 11, "why": "expired"}]
    codex, _ = reader("codex", monkeypatch, lambda mode: {**empty(mode), "dropped_pending": dropped})
    with caplog.at_level(logging.INFO, logger=cli_runtime_native.__name__):
        codex.parse_incremental("/x", {})
        codex.parse_incremental("/x", {})
    said = [row for row in caplog.records if row.name == cli_runtime_native.__name__]
    assert [row.levelno for row in said] == [logging.INFO, logging.INFO], "one line per pass"
    assert "steps#10 (gone), steps#11 (expired)" in said[0].getMessage() and "2 pending" in said[0].getMessage()
    codex.parse_incremental("/x", {})
    assert len([row for row in caplog.records if row.name == cli_runtime_native.__name__]) == 3


def test_a_pass_that_dropped_nothing_says_nothing(monkeypatch, caplog):
    import logging
    codex, _ = reader("codex", monkeypatch, empty)
    with caplog.at_level(logging.INFO, logger=cli_runtime_native.__name__):
        codex.parse_incremental("/x", {})
    assert not [row for row in caplog.records if row.name == cli_runtime_native.__name__]


def test_the_receipt_names_what_a_pass_dropped_and_omits_it_when_nothing_was():
    answer = {"checkpoint": {}, "seen": [], "state": None, "events": []}
    assert "dropped_pending" not in cli_runtime_observation._receipt(answer)
    dropped = [{"table": "steps", "id": 1, "why": "gone"}]
    assert cli_runtime_observation._receipt({**answer, "dropped_pending": dropped})["dropped_pending"] == dropped


def test_compat_codex_refuses_a_native_turn_breakdown_instead_of_falling_back_to_python(monkeypatch):
    codex, recorder = reader("codex", monkeypatch, empty)
    with pytest.raises(RuntimeError, match="no Python parser fallback"):
        codex.turns_for_session("/x")
    assert not recorder.calls


def test_a_vendor_without_the_turns_mode_and_without_the_refusal_inherits_the_generic_split(monkeypatch):
    opencode, recorder = reader("opencode", monkeypatch, empty)
    called = []
    monkeypatch.setattr(VENDORS["opencode"].make_log_reader, "turns_for_session",
                        lambda self, path, session_id="": called.append(path) or ["inherited"], raising=False)
    assert opencode.turns_for_session("/x") == ["inherited"] and called == ["/x"] and not recorder.calls


def test_the_turns_mode_a_declaration_lists_is_answered_by_the_sidecar(monkeypatch):
    claude, recorder = reader("claude", monkeypatch, empty)
    monkeypatch.setattr(type(claude), "_missing", lambda self, path: False)
    assert claude.turns_for_session("/x") == []
    assert [call[2] for call in recorder.calls] == ["turns"]


def test_compat_codex_scopes_its_observation_to_the_pane_that_holds_the_file(monkeypatch):
    codex, recorder = reader("codex", monkeypatch, empty)
    monkeypatch.setattr(type(codex), "pane_home_id", lambda self, path: "pane-7")
    codex.parse_incremental("/rollout.jsonl", {})
    assert recorder.calls[0][3]["pane_id"] == "pane-7"
    claude, claude_calls = reader("claude", monkeypatch, empty)
    claude.parse_incremental("/session.jsonl", {})
    assert "pane_id" not in claude_calls.calls[0][3]


@pytest.mark.parametrize("vendor,detail", [("claude", "user"), ("codex", "user_message"), ("opencode", "user")])
def test_the_prompt_wrapper_is_stripped_from_the_vendors_own_user_events_only(vendor, detail, monkeypatch, tmp_path):
    assert FACTS[vendor].observer.core.python.prompt_detail == detail
    log = tmp_path / (FACTS[vendor].observer.layouts[0].store_file or "log")  # a store vendor reads only the file it names
    log.write_text("")

    def answer(mode):
        event = {"timestamp": "t", "vendor": vendor, "session_id": "s", "cwd": "/w", "event_type": "agent_active",
                 "file_path": str(log), "dedup_key": "k", "detail": detail,
                 "text": "<system-reminder>x</system-reminder>real prompt"}
        other = {**event, "dedup_key": "o", "detail": "assistant", "text": "<system-reminder>x</system-reminder>reply"}
        return {**empty(mode), "events": [event, other]}

    native, _ = reader(vendor, monkeypatch, answer)
    mine, theirs = native.parse_activity(log, set())
    assert "system-reminder" not in mine.text
    assert theirs.text.startswith("<system-reminder>")


# ---- the seen bag: dedup keys apart from one typed state entry -------------------------------------------------------

CODEX = FACTS["codex"].observer


def entry(parser_id, state):
    return f"native_state::{parser_id}::" + json.dumps(state, sort_keys=True, separators=(",", ":"))


def test_an_activity_error_keeps_the_progress_in_the_seen_bag_before_it_is_raised(monkeypatch):
    def answer(mode):
        # A failed activity pass delivered no key (`_checked` rejects an answer that carries one): its progress is the state alone.
        return {**empty(mode), "seen": [], "state": {"water": 3}, "error": "Claude record is not an object"}

    claude, _ = reader("claude", monkeypatch, answer)
    monkeypatch.setattr(type(claude), "_missing", lambda self, path: False)
    seen = {"stale"}
    with pytest.raises(RuntimeError, match="not an object"):
        claude.parse_activity("/exists", seen)
    # t36g R5: the bag only grows. The answer's one state entry is added; a key the caller already holds stays.
    assert seen == {"stale", entry(FACTS["claude"].observer.parser_id, {"water": 3})}


def test_the_typed_state_travels_beside_the_keys_and_comes_back_as_one_bag_entry(monkeypatch):
    state = {"water": 4, "adapter": {"last_text": "partial"}}

    def answer(mode):
        return {**empty(mode), "seen": ["act:1"], "state": state}

    codex, recorder = reader("codex", monkeypatch, answer)
    bag = {"act:1"}
    codex.parse_activity("/rollout.jsonl", bag)
    assert bag == {"act:1", entry(CODEX.parser_id, state)}
    codex.parse_activity("/rollout.jsonl", bag)
    sent = recorder.calls[1][3]
    assert (sent["seen"], sent["state"]) == (["act:1"], state), "the second pass sends the state back, apart from the keys"


def test_an_old_python_bag_migrates_once_through_the_declared_prefix_table_and_then_stays_typed(monkeypatch):
    saved = []

    def answer(mode):
        return {**empty(mode), "seen": ["k1", "k2"], "state": {"water": 4, "last_text": "",
                                                                "totals": {"input": 5, "output": 2}}}

    codex, recorder = reader("codex", monkeypatch, answer)
    old_bag = {"k1", "act_hw::3", "act_hw::2", "__lasttext__:hello", "__cum__:in=5,out=2"}
    codex.parse_activity("/rollout.jsonl", old_bag)
    first = recorder.calls[0][3]
    assert first["seen"] == ["k1"]
    assert first["state"] == {"water": 3, "last_text": "hello", "totals": {"input": 5, "output": 2}}
    assert not any(key.startswith(("act_hw::", "__lasttext__:", "__cum__:")) for key in old_bag), "no legacy key survives"
    saved.append(set(old_bag))
    codex.parse_activity("/rollout.jsonl", old_bag)
    second = recorder.calls[1][3]
    assert second["seen"] == ["k1", "k2"]
    assert second["state"] == {"water": 4, "last_text": "", "totals": {"input": 5, "output": 2}}


def test_a_legacy_integer_takes_the_largest_value_and_an_unreadable_sentinel_is_dropped():
    legacy = FACTS["claude"].observer.core.legacy_state
    keys, state = cli_runtime_state.unpack({"act_hw::7", "act_hw::12", "act_hw::not-a-number", "dedup"}, "p", legacy)
    assert (keys, state) == (["dedup"], {"water": 12})


def test_a_legacy_value_outside_the_range_the_sidecar_reads_is_dropped_not_migrated():
    """H7: the sidecar refuses a state it cannot parse, so the codec must never write one."""
    codex = FACTS["codex"].observer.core.legacy_state
    for bad in ("__cum__:in=-1,out=2", "__cum__:in=1,out=18446744073709551616", "__cum__:in=1,out=-0x1"):
        keys, state = cli_runtime_state.unpack({bad, "dedup"}, "p", codex)
        assert (keys, state) == (["dedup"], {}), bad
    for bad in ("act_hw::-3", "act_hw::9223372036854775808", "act_hw::٣"):
        keys, state = cli_runtime_state.unpack({bad, "dedup"}, "p", codex)
        assert (keys, state) == (["dedup"], {}), bad
    assert cli_runtime_state.unpack({"__cum__:in=18446744073709551615,out=0"}, "p", codex) == (
        [], {"totals": {"input": 18446744073709551615, "output": 0}})


def test_running_totals_spelled_with_a_colon_migrate_and_a_bad_spelling_is_dropped():
    """Droid's `droid_tok::<input>:<output>` (`cli_vendors/droid.py:177-196`): two plain decimals, anything else was `(0, 0)`."""
    legacy = (LegacyState(prefix="droid_tok::", field="totals", kind="colon_pair"),)
    assert cli_runtime_state.unpack({"droid_tok::12:3", "dedup"}, "p", legacy) == (["dedup"], {"totals": {"input": 12, "output": 3}})
    for bad in ("droid_tok::12", "droid_tok::1:2:3", "droid_tok::-1:2", "droid_tok::a:2", "droid_tok::1:18446744073709551616"):
        assert cli_runtime_state.unpack({bad, "dedup"}, "p", legacy) == (["dedup"], {}), bad


def test_a_signed_mark_and_a_set_of_ids_migrate_from_the_baselines_sentinels_and_bad_ones_are_dropped():
    """Antigravity's first step is idx -1, and its unfinished steps are one JSON list of ids."""
    legacy = FACTS["antigravity"].observer.core.legacy_state
    assert cli_runtime_state.unpack({"agy_idx::-1", "agy_pending::[7, 2]", "dedup"}, "p", legacy) == (
        ["dedup"], {"marks": {"steps": {"last": -1, "pending": [2, 7]}}})
    assert cli_runtime_state.unpack({"agy_idx::-9223372036854775808"}, "p", legacy)[1]["marks"]["steps"]["last"] == -(2**63)
    for bad in ("agy_idx::-9223372036854775809", "agy_idx::9223372036854775808", "agy_idx::--1", "agy_idx::-",
                "agy_pending::[1, true]", "agy_pending::[1.5]", "agy_pending::{}", "agy_pending::not json",
                "agy_pending::[9223372036854775808]"):
        assert cli_runtime_state.unpack({bad, "dedup"}, "p", legacy) == (["dedup"], {}), bad
    # A signed mark takes the largest value a bag holds, as an unsigned one does.
    assert cli_runtime_state.unpack({"agy_idx::-5", "agy_idx::-2"}, "p", legacy)[1]["marks"]["steps"]["last"] == -2


def test_a_state_entry_wins_over_nothing_and_a_legacy_key_replaces_its_field():
    legacy = FACTS["claude"].observer.core.legacy_state
    bag = {entry("p", {"water": 5, "position": {"line": 5}}), "act_hw::9"}
    keys, state = cli_runtime_state.unpack(bag, "p", legacy)
    assert (keys, state) == ([], {"water": 9, "position": {"line": 5}}), "a watcher seed that moved the mark is honoured"


def test_a_state_entry_of_another_parser_is_not_this_parsers_state():
    other = entry("q", {"water": 5})
    assert cli_runtime_state.unpack({other, "dedup"}, "p", ()) == (["dedup", other], {})


@pytest.mark.parametrize("body", ["{not json", "[1]", ""])
def test_a_state_entry_of_this_parser_that_does_not_parse_refuses_the_pass_and_keeps_the_bag(body, monkeypatch):
    """H7: dropping it would send a first pass, and the sidecar would replay every line or re-emit a whole total."""
    with pytest.raises(cli_runtime_state.StateError):
        cli_runtime_state.unpack({f"native_state::p::{body}", "dedup"}, "p", ())
    codex, recorder = reader("codex", monkeypatch, empty)
    damaged = f"native_state::{FACTS['codex'].observer.parser_id}::{body}"
    bag = {damaged, "k"}
    with pytest.raises(cli_runtime_state.StateError):
        codex.parse_activity("/rollout.jsonl", bag)
    assert (bag, recorder.calls) == ({damaged, "k"}, [])


def test_the_state_entry_is_deterministic_so_an_unchanged_state_is_an_unchanged_bag():
    state = {"b": 1, "a": {"y": 2, "x": 1}}
    assert cli_runtime_state.pack(["k"], state, "p") == cli_runtime_state.pack(["k"], dict(reversed(state.items())), "p")
    assert cli_runtime_state.pack(["k"], {}, "p") == {"k"}


def test_opencode_marker_sentinel_migrates_into_the_part_walk():
    legacy = FACTS["opencode"].observer.core.legacy_state
    assert cli_runtime_state.unpack({"opencode_part::7"}, "p", legacy) == ([], {"marks": {"part": {"last": 7}}})


# ---- the answer checks come from the declaration ----------------------------------------------------------------------

def test_a_stem_named_session_must_be_answered_with_that_stem(tmp_path):
    from pathlib import Path
    core = FACTS["claude"].observer.core
    check = cli_runtime_native._checked(Path("/s/abc.jsonl"), core, FACTS["claude"].observer.layouts[0], [], {}, "usage")
    check({"native_session_id": "abc"}, None)
    with pytest.raises(RuntimeError, match="wrong observation session"):
        check({"native_session_id": "other"}, None)


@pytest.mark.parametrize("answer,problem", [
    # t36g R5: an answer's `seen` names only the keys the pass delivered, and a failed pass delivered none.
    ({"seen": [], "state": {"water": 4}, "events": []}, None),
    ({"seen": [], "state": {"water": 3}, "events": []}, "high-water"),
    ({"seen": [], "state": {"water": 4}, "events": [{}]}, "high-water"),
    ({"seen": ["k"], "state": {"water": 4}, "events": []}, "high-water"),
    ({"seen": ["new"], "state": {"water": 4}, "events": []}, "high-water"),
    ({"seen": [], "state": {}, "events": []}, "high-water"),
    ({"seen": [1], "state": {"water": 4}, "events": []}, "seen progress"),
])
def test_a_failed_activity_pass_must_have_moved_the_water_without_events_or_key_changes(answer, problem):
    from pathlib import Path
    core = FACTS["claude"].observer.core
    check = cli_runtime_native._checked(Path("/s/abc.jsonl"), core, FACTS["claude"].observer.layouts[0], ["k"], {"water": 3}, "activity")
    result = {"native_session_id": "abc", "error": "boom", **answer}
    if problem is None:
        check(result, None)
    else:
        with pytest.raises(RuntimeError, match=problem):
            check(result, None)


def test_a_failed_activity_pass_over_a_store_has_no_line_mark_to_move():
    """A store has no line mark: `compat.store_error_reported` carries its error, and the answer needs no water."""
    from pathlib import Path
    core = FACTS["copilot"].observer.core
    store = next(layout for layout in FACTS["copilot"].observer.layouts if layout.source == "sqlite")
    check = cli_runtime_native._checked(Path("/s/session-store.db"), core, store, ["k"], {}, "activity")
    check({"error": "sqlite error 1", "seen": ["k"], "state": {}, "events": []}, None)


# ---- the store vendors: the request a layout declares, and what the discovery reader keeps ---------------------------

SESSION = "0b3a7a52-1f5e-4c43-8d20-2a6f0c0b9e11"
OTHER = "1c4b8b63-2a6f-4d54-9e31-3b7a1d1cae22"


def chats(tmp_path, monkeypatch, *sessions):
    """Cursor's chat root with one `store.db` (and a `meta.json` beside the first) per session directory."""
    root = tmp_path / "chats"
    stores = []
    for session in sessions:
        store = root / "project-hash" / session / "store.db"
        store.parent.mkdir(parents=True)
        store.write_bytes(b"")
        stores.append(store)
    (stores[0].parent / "meta.json").write_text(json.dumps({"cwd": "/work/c", "updatedAtMs": 1792513600000}))
    monkeypatch.setattr(VENDORS["cursor"].make_log_reader, "_chats_root", lambda self: root)
    return stores


def test_a_layout_sends_the_request_fields_it_declares_and_a_lookup_sends_only_the_session(monkeypatch, tmp_path):
    (store,) = chats(tmp_path, monkeypatch, SESSION)
    assert FACTS["cursor"].observer.layouts[0].request == ("cwd", "session", "base_epoch")
    cursor, recorder = reader("cursor", monkeypatch, lambda mode: {**empty(mode), "markers": {"at-pane:x": [SESSION, ""]}})
    cursor.parse_activity(store, set())
    sent = recorder.calls[0][3]
    assert (sent["cwd"], sent["session"], sent["base_epoch"]) == ("/work/c", SESSION, 1792513600.0)
    assert cursor.find_sessions_by_marker(["at-pane:x"]) == {"at-pane:x": (SESSION, "")}
    lookup = recorder.calls[1][3]
    assert (lookup["session"], "cwd" in lookup, "base_epoch" in lookup) == (SESSION, False, False)


def test_a_lookup_stops_at_the_first_store_when_it_has_every_marker(monkeypatch, tmp_path):
    chats(tmp_path, monkeypatch, SESSION, OTHER)
    cursor, recorder = reader("cursor", monkeypatch, lambda mode: {**empty(mode), "markers": {"at-pane:x": [SESSION, ""]}})
    assert cursor.find_sessions_by_marker(["at-pane:x", ""]) == {"at-pane:x": (SESSION, "")}
    assert len(recorder.calls) == 1


def test_a_vendor_whose_sessions_are_files_keeps_its_own_session_lookup(monkeypatch, tmp_path):
    chats(tmp_path, monkeypatch, SESSION)
    cursor, recorder = reader("cursor", monkeypatch, empty)
    assert cursor.has_session(SESSION) is True and cursor.has_session(OTHER) is False
    assert not recorder.calls, "the declaration lists no has_session mode, so the discovery reader answers"


def test_a_vendor_that_stores_no_usage_answers_the_usage_methods_itself(monkeypatch, tmp_path):
    """The sidecar answers the usage modes of such a layout with no events, but the reader has nothing to ask it."""
    (store,) = chats(tmp_path, monkeypatch, SESSION)
    cursor, recorder = reader("cursor", monkeypatch, empty)
    assert cursor.parse_session_file(store, set()) == []
    result = cursor.parse_incremental(store, {"offset": 4})
    assert (result.events, result.checkpoint) == ([], {"offset": 4})
    assert not recorder.calls


def test_a_request_carries_no_clock_of_the_callers():
    """A pending row's age is the store's own clock (the newest stamp the pass read), so no process, product or harness, can
    set the sidecar's clock through the request or the environment."""
    from agent_team_backend import cli_runtime_observation
    assert not hasattr(cli_runtime_observation, "_evaluation_clock")


def test_a_file_no_layout_owns_is_nobodys_to_read(monkeypatch, tmp_path):
    (store,) = chats(tmp_path, monkeypatch, SESSION)
    sibling = store.with_name("store.db-wal")
    sibling.write_bytes(b"")
    cursor, recorder = reader("cursor", monkeypatch, empty)
    assert cursor.parse_activity(sibling, set()) == [] and cursor.parse_session_file(sibling, set()) == []
    assert cursor.parse_incremental(sibling, {"offset": 1}).checkpoint == {"offset": 1}
    assert not recorder.calls


def test_a_session_directory_that_is_not_a_conversation_has_nothing_to_attribute(monkeypatch, tmp_path):
    (store,) = chats(tmp_path, monkeypatch, SESSION)
    stray = store.parents[1] / "not-a-uuid" / "store.db"
    stray.parent.mkdir()
    stray.write_bytes(b"")
    cursor, recorder = reader("cursor", monkeypatch, empty)
    assert cursor.parse_activity(stray, set()) == []
    assert not recorder.calls


def test_goose_asks_each_store_the_discovery_reader_lists_for_a_session(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    db.write_bytes(b"")
    monkeypatch.setattr(VENDORS["goose"].make_log_reader, "session_files", lambda self: [db])
    goose, recorder = reader("goose", monkeypatch, lambda mode: {**empty(mode), "found": True})
    assert goose.has_session(" s1 ") is True and goose.has_session("  ") is False
    assert [(call[2], call[3]["wanted_session"]) for call in recorder.calls] == [("has_session", "s1")]
    assert "session_id" not in recorder.calls[0][3], "session_id is an ownership key, not the lookup key"
    assert "cwd" not in recorder.calls[0][3], "Goose's layout declares no request field"
