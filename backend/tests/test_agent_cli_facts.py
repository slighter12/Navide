"""One declaration per registered vendor, and the three languages that are generated from it.

``shared/agent-cli/<key>.json`` is the only place a vendor's launch facts are written. The generator in
``scripts/generate-agent-cli-facts.py`` emits the Python, TypeScript and Rust data. These tests pin the three
promises of that arrangement: every registered vendor is declared, a stale or hand-edited generated file is
caught, and the hand-written consumers read the declared values instead of a second copy.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_team_backend.cli_vendors import registry

ROOT = Path(__file__).resolve().parents[2]
DECLARATIONS = ROOT / "shared" / "agent-cli"
GENERATOR = ROOT / "scripts" / "generate-agent-cli-facts.py"

STATUSES = {"integrated", "vendor-unsupported", "not-yet-integrated", "unknown"}
CAPABILITIES = {"model", "effort", "skip_permissions", "resume_by_id", "session_id_at_launch", "login"}
REQUIRED = {
    "key", "composition", "command", "model_flag", "effort_flag", "known_efforts", "permission_flag", "resume_args",
    "session_id_flag", "login_args", "resume_aliases", "subcommand", "data_dirs", "session_root", "session_path", "logs",
    "permission_aliases", "supports_session_resume", "shutdown", "capabilities", "observer",
}
#: The typed records a declaration may carry (each is null or exactly these fields): what the generic core routines read
#: instead of per-vendor code.
RECORDS = {
    "resume_aliases": {"programs", "flags"},
    "subcommand": {"word", "aliases"},
    "data_dirs": {"env", "home", "append", "extra_env", "tilde"},
    "session_root": {"root_env", "base_env", "home", "fallbacks", "sub"},
    "permission_aliases": {"spellings", "mode_flags", "config_keys"},
    "session_path": {"segments", "cwd"},
    "logs": {"files", "cwd"},
}
CWD_ENCODINGS = {"absent", "claude", "droid", "url_quote", "pi", "md5"}
OBSERVER = {"parser", "parser_id", "platforms", "layouts", "core"}
#: A layout always names these; `rules` (a JSONL log's compatibility rules), `defers_to` (ownership), `request` (what the
#: request carries for a file) and `applies_when` (the table that tells a schema variant) are optional.
LAYOUT = {"source", "usage", "store_file", "usage_fields", "store"}
LAYOUT_OPTIONAL = {"rules", "defers_to", "request", "applies_when"}
CORE = {"modes", "messages", "legacy_state", "python", "compat"}
ADAPTERS = ROOT / "native" / "navide-cli-runtime" / "src" / "observe" / "adapters"


def declarations() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text()) for path in sorted(DECLARATIONS.glob("*.json"))}


def copy_declarations(tmp_path: Path, adapters: tuple[str, ...] = ()) -> None:
    shutil.copytree(DECLARATIONS, tmp_path / "shared" / "agent-cli")
    shutil.copy(DECLARATIONS.parent / "agent-cli-subjects.json", tmp_path / "shared" / "agent-cli-subjects.json")
    # The generator only asks which adapter modules exist (every parser needs one). A
    # parser named in `adapters` has a stub, so its declaration can be checked before its adapter is written.
    stubs = tmp_path / "native" / "navide-cli-runtime" / "src" / "observe" / "adapters"
    stubs.mkdir(parents=True)
    for adapter in ADAPTERS.glob("*.rs"):
        (stubs / adapter.name).write_text("")
    for parser in adapters:
        (stubs / f"{parser}.rs").write_text("")


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_agent_cli_facts", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_generator(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GENERATOR), *args], capture_output=True, text=True, timeout=60)


def test_every_registered_vendor_has_exactly_one_declaration() -> None:
    declared = declarations()
    assert set(declared) == set(registry.VENDORS)
    for key, facts in declared.items():
        assert facts["key"] == key


def test_declarations_have_the_declared_shape() -> None:
    for key, facts in declarations().items():
        assert set(facts) == REQUIRED, key
        assert set(facts["capabilities"]) == CAPABILITIES, key
        assert set(facts["capabilities"].values()) <= STATUSES, key
        assert facts["command"], key
        if facts["resume_args"] is not None:
            assert "{id}" in facts["resume_args"], key
        # Status stays internal: no evidence or verification text rides along in the declaration.
        assert all(isinstance(value, str) for value in facts["capabilities"].values()), key


def test_a_typed_record_is_null_or_exactly_its_fields() -> None:
    for key, facts in declarations().items():
        for name, fields in RECORDS.items():
            record = facts[name]
            assert record is None or set(record) == fields, (key, name)
            if record is not None and "cwd" in record:
                assert record["cwd"] in CWD_ENCODINGS, (key, name)
                assert ("{cwd}" in "/".join(record.get("files") or record.get("segments"))) == (record["cwd"] != "absent"), (key, name)


def test_a_logs_pattern_that_leaves_the_root_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "claude", lambda facts: facts["logs"].update(files=["../{cwd}/*.jsonl"]))
    with pytest.raises(SystemExit, match="relative"):
        generator.load(tmp_path)


def test_a_logs_cwd_encoding_must_match_its_placeholder(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "claude", lambda facts: facts["logs"].update(cwd="absent"))
    with pytest.raises(SystemExit, match="exactly when"):
        generator.load(tmp_path)


def test_a_session_path_without_the_id_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "claude", lambda facts: facts["session_path"].update(segments=["{cwd}", "x.jsonl"]))
    with pytest.raises(SystemExit, match="session_path must hold"):
        generator.load(tmp_path)


def test_a_session_root_without_a_default_base_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "droid", lambda facts: facts["session_root"].update(home=[]))
    with pytest.raises(SystemExit, match="default base"):
        generator.load(tmp_path)


def test_a_flag_whose_capability_is_not_integrated_is_refused_by_the_generator(tmp_path: Path) -> None:
    """t39f M7: a non-null flag requires capability `integrated`, so the launch composer can never be handed a flag
    the sidecar would refuse."""
    generator = declarations_with(tmp_path, "claude", lambda facts: facts["capabilities"].update(effort="unknown"))
    with pytest.raises(SystemExit, match="effort_flag is declared, so capability effort must be integrated"):
        generator.load(tmp_path)


def test_every_flag_capability_pair_is_enforced(tmp_path: Path) -> None:
    for flag, capability in load_generator().FLAG_CAPABILITIES:
        owner = next(key for key, facts in declarations().items() if facts[flag] is not None)
        generator = declarations_with(tmp_path / flag, owner, lambda facts: facts["capabilities"].update({capability: "not-yet-integrated"}))
        with pytest.raises(SystemExit, match=f"{flag} is declared"):
            generator.load(tmp_path / flag)


def test_a_subcommand_that_is_not_the_first_word_of_the_resume_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "goose", lambda facts: facts["subcommand"].update(word="chat"))
    with pytest.raises(SystemExit, match="first word of resume_args"):
        generator.load(tmp_path)


def test_an_observer_declaration_is_typed_and_a_vendor_without_one_says_so() -> None:
    for key, facts in declarations().items():
        observer = facts["observer"]
        if observer is None:
            assert key == "mcode", key  # the only registered vendor with no session log to observe
            continue
        assert set(observer) == OBSERVER, key
        assert re.fullmatch(r"[a-z][a-z0-9]*", observer["parser"]), key
        assert observer["parser_id"].startswith("rust-") and observer["platforms"] and observer["layouts"], key
        for layout in observer["layouts"]:
            assert LAYOUT <= set(layout) <= LAYOUT | LAYOUT_OPTIONAL, key
            assert layout["source"] in {"jsonl", "sqlite", "text", "sidecar"}, key
            assert layout["usage"] in {"per_turn", "cumulative", "none"}, key
            assert layout["source"] != "sqlite" or layout["store_file"] != "", key
        # Every parser is on the core: it has an adapter module there and a `core` block.
        assert (ADAPTERS / f"{observer['parser']}.rs").exists(), key
        assert set(observer["core"]) == CORE, key


def test_vendors_sharing_a_parser_share_its_layouts_and_name_their_own_store_file() -> None:
    sources: dict[str, list[str]] = {}
    for key, facts in declarations().items():
        observer = facts["observer"]
        if observer:
            kinds = [layout["source"] for layout in observer["layouts"]]
            assert sources.setdefault(observer["parser"], kinds) == kinds, key
    kilo, opencode = declarations()["kilo"]["observer"], declarations()["opencode"]["observer"]
    assert kilo["parser"] == opencode["parser"]
    assert kilo["layouts"][0]["store_file"] != opencode["layouts"][0]["store_file"]
    assert kilo["parser_id"] != opencode["parser_id"]
    assert {k: v for k, v in kilo["layouts"][0].items() if k != "store_file"} == {
        k: v for k, v in opencode["layouts"][0].items() if k != "store_file"}, "one store declaration, two file names"


def test_a_vendor_may_declare_more_than_one_layout_told_apart_by_file_name() -> None:
    copilot = declarations()["copilot"]["observer"]
    assert [(layout["source"], layout["store_file"]) for layout in copilot["layouts"]] == [
        ("sqlite", "session-store.db"), ("jsonl", "events.jsonl")]


def declarations_with(tmp_path: Path, key: str, change, adapters: tuple[str, ...] = ()) -> object:
    generator = load_generator()
    copy_declarations(tmp_path, adapters)
    path = tmp_path / "shared" / "agent-cli" / f"{key}.json"
    facts = json.loads(path.read_text())
    change(facts)
    path.write_text(json.dumps(facts, indent=2) + "\n")
    return generator


@pytest.mark.parametrize("name", ["Codex", "2fast", "open_code", "open-code", ""])
def test_a_parser_name_outside_the_lowercase_alphanumeric_form_is_refused(tmp_path: Path, name: str) -> None:
    generator = declarations_with(tmp_path, "pi", lambda facts: facts["observer"].update(parser=name))
    with pytest.raises(SystemExit, match="parser"):
        generator.load(tmp_path)


def test_a_parser_name_the_generated_module_already_uses_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "pi", lambda facts: facts["observer"].update(parser="dispatch"))
    with pytest.raises(SystemExit, match="reserved"):
        generator.load(tmp_path)


def test_a_parser_without_an_adapter_module_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "pi", lambda facts: facts["observer"].update(parser="nosuchparser"))
    with pytest.raises(SystemExit, match="needs its adapter"):
        generator.load(tmp_path)


def test_an_observer_without_a_core_block_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "pi", lambda facts: facts["observer"].update(core=None))
    with pytest.raises(SystemExit, match="declares `core`"):
        generator.load(tmp_path)


def test_a_declared_mode_the_source_kind_cannot_answer_is_refused(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "codex", lambda facts: facts["observer"]["core"]["modes"].append("has_session"))
    with pytest.raises(SystemExit):
        generator.load(tmp_path)


def test_a_flag_is_declared_only_where_the_capability_is_integrated() -> None:
    for key, facts in declarations().items():
        capabilities = facts["capabilities"]
        assert (facts["model_flag"] is not None) == (capabilities["model"] == "integrated"), key
        assert (facts["effort_flag"] is not None) == (capabilities["effort"] == "integrated"), key
        assert (facts["permission_flag"] is not None) == (capabilities["skip_permissions"] == "integrated"), key
        assert (facts["resume_args"] is not None) == (capabilities["resume_by_id"] == "integrated"), key
        assert (facts["session_id_flag"] is not None) == (capabilities["session_id_at_launch"] == "integrated"), key
        assert (facts["login_args"] is not None) == (capabilities["login"] == "integrated"), key
        assert (facts["known_efforts"] is not None) == (facts["effort_flag"] is not None), key


def test_goose_marks_missing_evidence_as_unknown() -> None:
    capabilities = declarations()["goose"]["capabilities"]
    assert {capabilities["model"], capabilities["effort"], capabilities["login"]} == {"unknown"}


def test_generated_files_are_current() -> None:
    result = run_generator("--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_hand_edited_generated_file_fails_the_check(tmp_path: Path) -> None:
    generator = load_generator()
    copy_declarations(tmp_path)
    generator.write_all(tmp_path)
    assert run_generator("--check", "--root", str(tmp_path)).returncode == 0
    for relative in generator.output_paths():
        target = tmp_path / relative
        original = target.read_text()
        target.write_text(original + "\n// hand edit\n")
        result = run_generator("--check", "--root", str(tmp_path))
        assert result.returncode != 0, relative
        assert str(relative) in result.stdout + result.stderr
        target.write_text(original)


def test_a_changed_declaration_fails_the_check_until_regenerated(tmp_path: Path) -> None:
    generator = load_generator()
    copy_declarations(tmp_path)
    generator.write_all(tmp_path)
    codex = tmp_path / "shared" / "agent-cli" / "codex.json"
    facts = json.loads(codex.read_text())
    facts["command"] = "codex-renamed"
    codex.write_text(json.dumps(facts, indent=2) + "\n")
    assert run_generator("--check", "--root", str(tmp_path)).returncode != 0
    generator.write_all(tmp_path)
    assert run_generator("--check", "--root", str(tmp_path)).returncode == 0
    assert "codex-renamed" in (tmp_path / generator.PYTHON_OUTPUT).read_text()
    assert "codex-renamed" in (tmp_path / generator.SHARED_TYPESCRIPT_OUTPUT).read_text()
    assert "codex-renamed" in (tmp_path / generator.RUST_OUTPUT).read_text()


@pytest.mark.parametrize("key", sorted(registry.VENDORS))
def test_backend_spec_follows_the_declaration(key: str) -> None:
    facts = declarations()[key]
    spec = registry.VENDORS[key]
    assert spec.key == facts["key"]
    assert spec.supports_model == (facts["model_flag"] is not None)
    assert spec.supports_effort == (facts["effort_flag"] is not None)
    assert tuple(spec.known_efforts or ()) == tuple(facts["known_efforts"] or ())
    assert spec.login_command_args == facts["login_args"]
    assert spec.supports_session_resume == facts["supports_session_resume"]
    shutdown = facts["shutdown"]
    if shutdown is None:
        assert spec.shutdown is None
    else:
        assert spec.shutdown is not None
        assert (spec.shutdown.graceful, spec.shutdown.grace_s, spec.shutdown.defer_master_close) == (
            shutdown["graceful"], shutdown["grace_s"], shutdown["defer_master_close"])


# ---- the store declarations are the real ones, and the generator refuses what the core would ignore or mishandle -------------

def fields(**changes) -> dict:
    base = {"select": [], "object": None, "input": [], "output": [], "cache_read": [], "cache_creation": [], "key": [],
            "key_join": "", "model": None, "version": None, "timestamp": None, "number": "strict", "text": "string",
            "cache": "folded", "time": "text"}
    return {**base, **changes}


#: One arm per layout, each naming its own source, usage and capability types: nothing is chosen by string at run time.
EXPECTED_ARMS = {
    "copilot": [("super::Sqlite", "super::PerTurn", "super::NoCapability"), ("super::JsonlFile", "super::Cumulative", "super::NoCapability")],
    "goose": [("super::Sqlite", "super::PerTurn", "(super::BySession, super::ByMarker)")],
    "antigravity": [("super::Sqlite", "super::NoUsage", "super::NoCapability")],
    "cursor": [("super::Sqlite", "super::NoUsage", "super::ByMarker")],
}


@pytest.mark.parametrize("key", ["goose", "antigravity", "cursor", "copilot"])
def test_the_real_store_declarations_generate_one_typed_arm_per_layout(tmp_path: Path, key: str) -> None:
    """The declarations of the vendors on the core are the real ones, written from the schema each CLI records."""
    generator = declarations_with(tmp_path, key, lambda facts: None)
    generator.write_all(tmp_path)
    dispatch = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    arms = re.findall(rf"observe::<{key}::\w+, ([\w:]+), ([\w:]+), (.+?)>\(&request", dispatch)
    assert arms == EXPECTED_ARMS[key], dispatch


def _second_schema(facts: dict, **changes) -> None:
    layouts = facts["observer"]["layouts"]
    layouts.append({**json.loads(json.dumps(layouts[0])), "applies_when": ["session_message"], **changes})


def test_two_schemas_of_one_store_file_are_one_dispatch_arm_and_one_layout_each(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "goose", _second_schema, adapters=("goose",))
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert len(re.findall(r"observe::<goose::", source)) == 1, "the arm is chosen by file name, the schema by the connection"
    assert source.count('applies_when: &["session_message"]') == 1
    assert "has_table" not in source, "choosing a layout never opens a second connection"


def test_the_layouts_of_one_store_file_must_share_their_dispatch_types(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "goose", lambda facts: _second_schema(facts, usage="none", usage_fields=None),
                                  adapters=("goose",))
    with pytest.raises(SystemExit, match="must share one source, usage and capability type"):
        generator.write_all(tmp_path)


def test_a_layout_asks_for_the_request_fields_its_adapter_reads_and_no_others(tmp_path: Path) -> None:
    layouts = {key: declarations()[key]["observer"]["layouts"][0].get("request", []) for key in ("goose", "antigravity", "cursor")}
    assert layouts == {"goose": [], "antigravity": ["cwd", "session"], "cursor": ["cwd", "session", "base_epoch"]}
    generator = declarations_with(tmp_path, "cursor", lambda facts: None)
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert "request: &[RequestField::Cwd, RequestField::Session, RequestField::BaseEpoch]" in source


def test_a_log_layout_that_defers_to_a_store_is_generated_with_its_ownership_rule(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "copilot", lambda facts: None)
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert 'defers_to: Some(Defers { file: "session-store.db", up: 3 })' in source
    assert source.count("defers_to: Some(") == 1, "only the log layout yields; the store layout owns"


def _break(path: tuple, value):
    """A change that sets the key at `path` (dict keys and list indexes) of the observer block."""
    def change(observer: dict) -> None:
        node = observer
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
    return change


STORE0 = ("layouts", 0, "store")
FIELDS0 = ("layouts", 0, "usage_fields")


@pytest.mark.parametrize("key,change,message", [
    # H1: a sqlite layout whose usage_fields the row source would ignore is refused, not accepted and dropped.
    ("goose", _break((*FIELDS0, "key"), ["/id"]), "no key"),
    ("goose", _break((*FIELDS0, "key_join"), "::"), "no key"),
    ("goose", _break((*FIELDS0, "object"), "/usage"), "no object"),
    ("goose", _break((*FIELDS0, "input"), ["/tokens/input"]), "declared column"),
    # H3 / N4: identifiers are plain, and the shape rules hold.
    ("goose", _break((*STORE0, "rows", "table"), 'ledger"; DROP TABLE x; --'), "plain SQL identifier"),
    ("goose", _break((*STORE0, "rows", "columns"), ["ok", 'bad"col']), "plain SQL identifier"),
    # U3: the name a usage event's `raw` carries its row id under is a plain identifier too (it is emitted into the generated source).
    ("goose", _break((*STORE0, "rows", "raw_id"), 'row_id"; --'), "plain SQL identifier"),
    ("goose", _break((*STORE0, "parent", "join"), "outer"), "store.parent.join"),
    ("cursor", _break((*STORE0, "marks", 0, "start"), "latest"), "store.marks.start"),
    ("cursor", _break((*STORE0, "marks", 0, "shrink"), "reset"), "store.marks.start"),
    ("goose", _break((*STORE0, "marker", "role", "column"), "missing"), "declared column of the mark walk"),
    ("cursor", lambda observer: (observer["layouts"][0]["store"]["marks"][0].update(columns=["x"]),
                                 observer["layouts"][0]["store"]["marker"].update(role={"column": "x", "equals": "y"})), "no role"),
    ("cursor", _break((*STORE0, "marks", 0, "group"), "g"), "needs a parent table"),
    ("goose", _break((*STORE0, "marker", "role"), {"pointer": "/role", "equals": "user"}), "owner payload"),
    ("goose", _break((*STORE0, "rows", "pending_until"), "/status"), "pointer into rows.payload"),
    # A raw marker scan: its bounds are typed integers, and it finds no group, so it binds to the session the request names.
    ("cursor", _break((*STORE0, "marker", "within", "rows"), True), "must be an integer"),
    ("cursor", _break((*STORE0, "marker", "within", "bytes"), "4096"), "must be an integer"),
    ("cursor", _break((*STORE0, "marker", "binding"), "store_rows"), "binds to the request's session"),
    ("cursor", _break(("layouts", 0, "request"), ["cwd", "base_epoch"]), "needs the layout to request `session`"),
    ("cursor", _break(("layouts", 0, "request"), ["cwd", "cwd"]), "distinct fields"),
    ("cursor", _break(("layouts", 0, "request"), ["pid"]), "distinct fields"),
    # A pending set expires only by a clock it can read.
    ("goose", _break((*STORE0, "rows", "pending_max_age"), {"pointer": "/time", "unit": "millis", "seconds": 60}),
     "needs rows.pending_until"),
    ("antigravity", _break((*STORE0, "marks", 0, "pending_max_age"), {"pointer": "/time", "unit": "seconds", "seconds": 60}),
     "no pointer, unit seconds"),
    # A legacy sentinel names its kind, and the codec reads only the kinds the generator knows.
    ("antigravity", lambda observer: observer["core"]["legacy_state"][0].update(kind="hex"), "kind in"),
    # A schema variant names a table of a sqlite layout; a log has none.
    ("codex", lambda observer: observer["layouts"][0].update(applies_when=["session_message"], store_file="log.jsonl"),
     "only a sqlite layout"),
    ("codex", lambda observer: observer["layouts"][0].update(applies_when=["session_message"]), "names its store file"),
    # M1(a): the table set is a list of distinct plain identifiers.
    ("goose", lambda observer: observer["layouts"][0].update(applies_when="session_message"), "list of plain SQL identifiers"),
    ("goose", lambda observer: observer["layouts"][0].update(applies_when=["a", "a"]), "each table once"),
    ("goose", lambda observer: observer["layouts"][0].update(applies_when=['x"; --']), "plain SQL identifier"),
    # M1(b)-(d), (f): the typed fields OpenCode 2 declares are checked like every other field.
    ("opencode", _break((*STORE0, "rows", "cursor_tag"), ""), "cursor_tag is the text"),
    ("opencode", _break((*STORE0, "rows", "cursor_tag"), 2), "cursor_tag is the text"),
    ("opencode", _break((*STORE0, "parent", "dir_form"), "posix"), "dir_form is one of"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marks"].update(reply_from={"order": "seq"}), "reply_from"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marks"]["reply_from"].update(before="stamp"),
     "declared column of the mark walk"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marks"]["reply_from"].update(order="seq; --"),
     "plain SQL identifier"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marks"].update(owner="data"), "no owner"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marks"].update(payload=None), "no owner|a payload"),
    ("opencode", _break((*STORE0, "marker", "pointer"), "text"), "store.marker.pointer"),
    ("opencode", lambda observer: observer["layouts"][0]["store"]["marker"].update(
        role=None, within={"rows": 1, "bytes": 10}, binding="path_session"), "reads a payload field"),
    # M1(e): layouts that share one store file ask for the same request fields, whichever schema answers.
    ("opencode", _break(("layouts", 1, "request"), ["cwd"]), "same request fields"),
    # H2: usage and modes agree per layout; the one-source-kind rule is gone but each layout is checked on its own.
    ("goose", lambda observer: observer["core"].update(modes=["activity"]), "omits the usage modes"),
    ("antigravity", lambda observer: observer["layouts"][0].update(usage_fields=fields(input=["/input"])), "declares no usage_fields"),
    ("goose", _break(("layouts", 0, "usage"), "none"), "declares no usage_fields"),
    ("copilot", _break(("layouts", 1, "defers_to"), {"file": "nowhere.db", "up": 2}), "sibling sqlite layout"),
    ("copilot", _break(("layouts", 1, "defers_to"), {"file": "session-store.db", "up": 0}), "directories above"),
    ("copilot", _break(("layouts", 1, "rules"), None), "declares rules"),
    ("copilot", _break(("layouts", 0, "rules"), {"missing_identity_rotates": True, "invalid_utf8_fails": True,
                                                  "non_object_fails": True, "water_survives_replacement": True}), "no jsonl rules"),
    # H1 / N11: a compatibility flag the mode list decides is not a flag any more.
    ("goose", lambda observer: observer["core"]["compat"].update(legacy_replay=True), "compat"),
])
def test_a_store_declaration_the_core_would_ignore_or_mishandle_is_refused(tmp_path: Path, key: str, change, message: str) -> None:
    # Every declaration is the real one, written from the schema its CLI records.
    generator = declarations_with(tmp_path, key, lambda facts: change(facts["observer"]), adapters=(key,))
    with pytest.raises(SystemExit, match=message):
        generator.load(tmp_path)


def test_vendors_sharing_a_parser_must_agree_on_everything_but_the_file_name(tmp_path: Path) -> None:
    """N7: Kilo reads through OpenCode's adapter, so a drift in its store, usage_fields, compat or messages is refused."""
    for change in (
        lambda observer: observer["layouts"][0]["usage_fields"].update(input=["/tokens/input"]),
        lambda observer: observer["layouts"][0]["store"]["rows"].update(table="messages"),
        lambda observer: observer["core"]["compat"].update(absent_file_is_empty=True),
        lambda observer: observer["core"]["messages"].update(unsupported_mode="other"),
    ):
        generator = declarations_with(tmp_path / str(id(change)), "kilo", lambda facts, change=change: change(facts["observer"]))
        with pytest.raises(SystemExit, match="is shared with a vendor"):
            generator.load(tmp_path / str(id(change)))


def test_a_parser_on_a_row_store_gets_one_typed_walk_variant_per_mark_table(tmp_path: Path) -> None:
    """P1: an adapter tells its mark walks apart by a generated enum, never by a position or a table name. One variant per
    distinct mark table across the parser's layouts, in declaration order; every declared walk names its variant."""
    generator = declarations_with(tmp_path, "pi", lambda facts: None)
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert "pub enum OpencodeWalk {\n    SessionMessage,\n    Part,\n}" in source
    assert "pub enum CopilotWalk {\n    AssistantUsageEvents,\n    Turns,\n}" in source
    assert "impl super::WalkId for OpencodeWalk {\n    type Marked<'a> = OpencodeMark<'a>;\n\n    fn of(walk: Walk) -> Option<Self> {\n        match walk {\n            Walk::Opencode(own) => Some(own),\n" in source
    assert "OpencodeWalk::SessionMessage => OpencodeMark::SessionMessage(super::WalkMark::new(mark))," in source, "R8: a walk's mark is typed by its walk"
    assert "pub enum Walk {\n    Antigravity(AntigravityWalk),\n    Copilot(CopilotWalk)," in source, "one crate-wide walk id, one variant per parser"
    assert source.count("walk: Walk::Opencode(OpencodeWalk::SessionMessage)") == 2, "OpenCode's and Kilo's walk of the same table"
    assert "walk: Walk::Copilot(CopilotWalk::Turns)" in source
    assert "after: Some(Walk::Copilot(CopilotWalk::AssistantUsageEvents))" in source, "a walk waits for another by its typed id"
    assert " as usize" not in source.replace("fn position(self) -> usize {\n        self as usize", ""), "no walk or column is named by a position"
    assert "ClaudeWalk" not in source and "CodexWalk" not in source, "a parser with no row store has no walks to name"


def test_a_walks_declared_cells_are_a_typed_column_enum_and_a_reply_names_its_column_by_its_place_in_them(tmp_path: Path) -> None:
    """Q5: an adapter reads a cell of a walk by a generated column variant, in the order the declaration lists the cells."""
    generator = declarations_with(tmp_path, "pi", lambda facts: None)
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert "pub enum OpencodeSessionMessageColumn {\n    Type,\n    Seq,\n}" in source
    assert "impl super::ColumnId for OpencodeSessionMessageColumn {" in source
    assert "pub enum CopilotTurnsColumn {\n    UserMessage,\n    AssistantResponse,\n    Timestamp,\n}" in source
    assert "reply_from: Some(ReplyFrom { order: \"seq\", matching: ColumnIs { column: \"type\", equals: \"assistant\" }, before: 1 })" in source


def _copilot_marks(facts: dict) -> list[dict]:
    return next(layout["store"]["marks"] for layout in facts["observer"]["layouts"] if layout.get("store", {}).get("marks"))


def test_a_walk_that_waits_for_an_earlier_walk_is_generated_as_its_typed_id_and_the_wait_is_declared_not_coded(tmp_path: Path) -> None:
    """P2: `after` names an earlier walk of the same store; the generated declaration carries its typed walk id."""
    generator = declarations_with(tmp_path, "copilot", lambda facts: None)
    generator.write_all(tmp_path)
    source = (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert source.count("after: Some(Walk::Copilot(CopilotWalk::AssistantUsageEvents))") == 1 and "after: None" in source
    assert "page: 1000000" not in source, "Copilot's walks are bounded by the default page again"


@pytest.mark.parametrize("after", ["turns", "no_such_table"])
def test_a_walk_waiting_for_itself_or_for_a_walk_not_declared_before_it_is_refused(tmp_path: Path, after: str) -> None:
    generator = declarations_with(tmp_path, "copilot", lambda facts: _copilot_marks(facts)[1].update(after=after))
    with pytest.raises(SystemExit, match="after"):
        generator.load(tmp_path)


OWNERSHIP_KEYS = ("session_id", "session_generation", "pane_id", "workspace_path")


@pytest.mark.parametrize("vocabulary", ["REQUEST_FIELDS", "LOOKUP_KEYS"])
@pytest.mark.parametrize("name", OWNERSHIP_KEYS)
def test_a_request_name_the_sidecar_reads_as_an_ownership_key_is_refused(tmp_path: Path, monkeypatch, vocabulary: str, name: str) -> None:
    """P3: the sidecar reads these four keys of a request as who owns the observation (`session_id` is a live-session handle
    and an id it does not hold is refused `UNKNOWN_SESSION`), so no request field or lookup key may spell one."""
    generator = declarations_with(tmp_path, "pi", lambda facts: None)
    monkeypatch.setattr(generator, vocabulary, (*getattr(generator, vocabulary), name))
    with pytest.raises(SystemExit, match="ownership keys"):
        generator.load(tmp_path)


def test_the_lookup_key_is_one_generated_name_on_both_sides_and_is_not_an_ownership_key(tmp_path: Path) -> None:
    generator = declarations_with(tmp_path, "pi", lambda facts: None)
    assert generator.WANTED_SESSION == "wanted_session" and generator.WANTED_SESSION not in OWNERSHIP_KEYS
    assert tuple(generator.OWNERSHIP_KEYS) == OWNERSHIP_KEYS
    generator.write_all(tmp_path)
    assert 'pub const WANTED_SESSION: &str = "wanted_session";' in (tmp_path / generator.RUST_OBSERVERS_OUTPUT).read_text()
    assert "WANTED_SESSION = 'wanted_session'" in (tmp_path / generator.PYTHON_OUTPUT).read_text()
