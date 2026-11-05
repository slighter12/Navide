"""Generate the per-vendor launch facts from shared/agent-cli/<key>.json; --check verifies the committed output.

One declaration per vendor is the only hand-written copy. This emits the Python, TypeScript and Rust data that the
backend registry, the renderer agent specs and the native runtime read. Capability status stays out of the
TypeScript output: it is internal bookkeeping, never UI text.

The `observer` block describes how a vendor's session log is read. It holds typed fields only (no expressions, no
conditionals): the layouts a vendor's logs come in (source kind, usage mode, file name, the record pointers or table
names of that source), the modes it answers, the compatibility rules that keep a baseline behaviour, and the facts the
Python reader needs. The generated Rust has an exhaustive `Parser` enum, typed facts and a static dispatch.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = Path("shared/agent-cli")
SUBJECTS = Path("shared/agent-cli-subjects.json")
COMPOSITIONS = ("rust", "python")
PYTHON_OUTPUT = Path("backend/agent_team_backend/cli_vendors/_vendor_facts.py")
SHARED_TYPESCRIPT_OUTPUT = Path("src/shared/vendorFacts.ts")  # the one TypeScript copy (renderer and main process)
RUST_OUTPUT = Path("native/navide-cli-runtime/src/vendor_facts.rs")
RUST_OBSERVERS_OUTPUT = Path("native/navide-cli-runtime/src/observe/adapters/mod.rs")
RUST_ADAPTERS = Path("native/navide-cli-runtime/src/observe/adapters")

HEADER = "Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit."
STATUSES = ("integrated", "vendor-unsupported", "not-yet-integrated", "unknown")
CAPABILITIES = ("model", "effort", "skip_permissions", "resume_by_id", "session_id_at_launch", "login")
FIELDS = (
    "key", "composition", "command", "model_flag", "effort_flag", "known_efforts", "permission_flag", "resume_args",
    "session_id_flag", "login_args", "supports_session_resume", "shutdown", "capabilities", "observer",
)

# ---- the observer block ---------------------------------------------------------------------------------------------
# observer: parser (one adapter; vendors that share a store format share it), parser_id (the id the backend checks on
# every answer), platforms, layouts (one per log layout a vendor writes) and core (what the generic observation core needs
# beyond the layouts; `observe/adapters/<parser>.rs` must exist).
OBSERVER_FIELDS = ("parser", "parser_id", "platforms", "layouts", "core")
OBSERVER_SOURCES = ("jsonl", "sqlite", "text", "sidecar")
OBSERVER_USAGE = ("per_turn", "cumulative", "none")
OBSERVER_PLATFORMS = ("unix", "windows")
LAYOUT_FIELDS = ("source", "usage", "store_file", "usage_fields", "store", "rules", "defers_to", "request", "applies_when")
# What a request carries that the log itself does not: the working directory and session the Python discovery reader
# resolved for the file, and the scan clock. A layout declares the ones it reads; the core puts them on `Ident`.
REQUEST_FIELDS = ("cwd", "session", "base_epoch", "cli_version", "now_ms", "namespace")
# What the sidecar reads of an observation request for itself: who owns the observation. `session_id` is a live-session handle
# (an id the sidecar does not hold is refused `UNKNOWN_SESSION`), so no request field of an observer may take one of these names.
OWNERSHIP_KEYS = ("session_id", "session_generation", "pane_id", "workspace_path")
# The session a `has_session` lookup asks the store about, and the pane markers an `identify` lookup searches for.
WANTED_SESSION = "wanted_session"
LOOKUP_KEYS = (WANTED_SESSION, "markers")
# Every mode a request can name, and the ones each source can answer. `identify` is answered by the core from the
# adapter's `identify` hook on a JSONL log and by the declared marker lookup on a SQLite store. On a SQLite store the
# wire modes `session_usage` and `legacy` are both the whole-store replay against the seen bag; a layout answers the ones
# its parser lists, and the Python reader sends the replay mode its layout lists.
MODES = ("usage", "session_usage", "legacy", "activity", "turns", "identify", "has_session")
MODES_BY_SOURCE = {
    "jsonl": ("usage", "session_usage", "activity", "turns", "identify"),
    # A text log (Aider's Markdown) is read a line at a time by the JSONL source; the adapter makes each line a record.
    "text": ("usage", "session_usage", "activity"),
    "sqlite": ("usage", "session_usage", "legacy", "activity", "identify", "has_session"),
}
CORE_FIELDS = ("modes", "messages", "legacy_state", "python", "compat")
MESSAGE_FIELDS = ("unsupported_mode", "non_object")
LEGACY_FIELDS = ("prefix", "field", "kind")
# `set`: a JSON list of integers the sentinel key carried (`agy_pending::[2, 5]`).
# `colon_pair`: the running totals spelled `<input>:<output>` (Droid's `droid_tok::12:3`); `pair` spells them `in=<input>,out=<output>`.
# `json`: a flat JSON object in Python's compact spelling (Kimi's `kimi_turn::{"idx":0,...}`), the keys in the order the typed state names them.
# `json_text`: a text spelled as a JSON string literal (Grok's `grok_text::"reply so far"`).
# `byte_cursor`: the byte cursor a tail reader kept (Grok's `grok_act::{"anchor":…,"identity":…,"kind":"jsonl","offset":…}`); the field is
# `legacy_cursor`, which the sidecar proves against the file on the first activity pass and turns into a position and the line water.
LEGACY_KINDS = ("int", "signed", "text", "pair", "colon_pair", "set", "json", "json_text", "byte_cursor")
PYTHON_FIELDS = ("prompt_detail", "pane_scope", "attribution", "identity_source")
PANE_SCOPES = ("home_dir", "none")
ATTRIBUTIONS = ("file_stem", "none")
IDENTITY_SOURCES = ("header", "file_stem", "store")
# Named compatibility rules: each keeps one behaviour of a baseline reader (the Python one, defect or not), and the
# baseline behaviour is the comment the generator writes on the Rust and the Python field. Every one is pinned by a
# `test_compat_*` test at the public reader seam, or by a Rust test that names it. The Python-side rules sit in the
# core's `compat`; the rules only a JSONL log reads sit in the `rules` of the JSONL layout that reads them.
COMPAT_FIELDS = (
    ("absent_file_is_empty", "the baseline reader treated a log that is not there yet as nothing to read"),
    ("no_python_turn_fallback", "the baseline refused a turn breakdown the sidecar does not answer instead of splitting turns from the generic reads"),
    ("store_error_reported", "a store query that failed was reported as the answer's error (the reader warns once and a usage pass delivers nothing) instead of refusing the observation"),
)
JSONL_RULES = (
    ("missing_identity_rotates", "a saved cursor with no file identity was taken for a different file and read from the start"),
    ("invalid_utf8_fails", "a full scan over invalid UTF-8 failed the pass instead of skipping the line"),
    ("non_object_fails", "a full scan over a record that is not an object failed the pass instead of skipping it"),
    ("activity_non_object_fails", "an activity pass over a record that is not an object failed the pass (progress kept, events dropped) instead of skipping it"),
    ("totals_never_decrease", "the checkpoint's cumulative totals are a per-component high-water mark: a reading below them emits nothing and leaves that saved total where it was"),
    ("water_survives_replacement", "the activity high-water mark outlived a replaced or rewritten file"),
)
USAGE_FIELDS = (
    "select", "object", "input", "output", "cache_read", "cache_creation", "key", "key_join", "model", "version",
    "timestamp", "number", "text", "cache", "time", "document", "cwd", "session",
)
NUMBER_RULES = ("lenient", "strict")
TEXT_RULES = ("any", "string")
# `folded`: the cache portions are part of the input the event reports (and the event names them); `separate`: the log's
# input excludes them and the event reports them beside it. `time`: a timestamp pointer holds text, or epoch seconds.
CACHE_RULES = ("folded", "separate")
TIME_RULES = ("text", "epoch_seconds", "epoch_micros")
STORE_FIELDS = ("rows", "parent", "marks", "row_prefix", "marker")
# `cursor_tag`: a cursor written for this layout carries `schema: <tag>`, and a saved cursor without the tag was taken on the
# other schema of the file, so the layout starts at the newest row instead of crediting the copies of rows it already saw.
# `raw_id`: the event carries the id of its row as `raw: {<raw_id>: <id>}` (the baseline's `TokenUsage.raw`, which Goose's reader fills
# with `row_id` and reads back to build its own cursor).
ROWS_FIELDS = ("table", "id", "key", "group", "payload", "columns", "anchor", "pending_until", "pending_max_age",
               "cursor_tag", "raw_id")
# `dir_form`: how the store spells the group's working directory. `as_stored` hands it back; `windows_native` is a store that
# spells a Windows path with forward slashes and wants the platform's own spelling (a no-op off Windows).
PARENT_FIELDS = ("table", "key", "dir", "up", "version", "columns", "join", "top", "dir_form")
DIR_FORMS = ("as_stored", "windows_native")
# `reply_from`: the reply a turn-ending mark carries is the payload of the newest earlier row of the same group whose
# `where.column` holds `where.equals`: rows are ordered by `order` and "earlier" is `order` below the mark's own `before` column.
MARKS_FIELDS = ("table", "id", "key", "group", "owner", "payload", "stamp", "columns", "prefix", "key_scope", "page",
                "text_cap", "start", "shrink", "pending_max_age", "reply_from", "after")
REPLY_FROM_FIELDS = ("order", "where", "before")
# What names a mark's dedup key beyond the walk's prefix: nothing, or the session the request names for the store file
# (a store that holds one conversation, whose marks would otherwise collide with another's).
KEY_SCOPES = ("none", "session")
DEFAULT_MARK_PAGE = 256
# `pointer`: the marker text is searched in the string at this RFC 6901 pointer of the mark's payload, not in the whole payload.
MARKER_FIELDS = ("text", "on", "role", "order_by_stamp", "within", "binding", "pointer")
# A row (or mark) the vendor never finishes is dropped from the pending set once its own timestamp is older than
# `seconds`. `pointer` reads the timestamp from the row's payload; a mark walk reads its `stamp` column instead.
PENDING_AGE_FIELDS = ("pointer", "unit", "seconds")
TIME_UNITS = ("seconds", "millis")
# Where a found marker's session comes from: the group of the row that holds it, or the session the request named for
# this store file (a store that holds one session).
BINDINGS = ("store_rows", "path_session")
JOIN_POLICIES = ("inner", "left")
# Where a first activity pass starts: past the newest mark (a missing mark leaves the walk unstarted), past the newest or
# 0, or at the oldest. What a walk does when its table shrinks below the last mark: keep it, restart past the newest, or
# restart at the oldest.
MARK_STARTS = ("newest", "newest_or_zero", "oldest")
MARK_SHRINKS = ("keep", "newest", "oldest")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PARSER_NAME = re.compile(r"^[a-z][a-z0-9]*$")
POINTER = re.compile(r"^(/([^/~]|~[01])*)+$")
# Module names the generated file itself declares or imports; a parser cannot take one.
RESERVED_PARSERS = {"observe", "find", "dispatch", "super", "self", "crate", "std", "core", "alloc"}

# The harness-only test declaration. It is not a vendor, so no shared/agent-cli file carries it; the Rust output
# emits it behind the `fault-hooks` feature so `adapters::shutdown` and `compose::admit` reach it through the one
# declaration module that the shared-code rule exempts (the product build gains no symbol).
RUST_FAULT_DECLARATION = (
    "",
    "/// Harness build only: a test declaration (not a vendor name) that declares a graceful shutdown",
    "/// with a deferred master close, so declaration-driven policy is proven without a vendor key (S3).",
    "/// `adapters::shutdown` gives it its shutdown; `compose::admit` asks `is_fault_declaration` so that",
    "/// shared code never spells the key.",
    '#[cfg(feature = "fault-hooks")]',
    'pub const FAULT_GRACEFUL: &str = "fault-graceful";',
    "",
    '#[cfg(feature = "fault-hooks")]',
    "pub fn is_fault_declaration(key: &str) -> bool {",
    "    key == FAULT_GRACEFUL",
    "}",
)
TS_FIELDS = tuple(name for name in FIELDS if name not in {"shutdown", "capabilities", "supports_session_resume", "composition", "observer"})


def output_paths() -> tuple[Path, ...]:
    return (PYTHON_OUTPUT, SHARED_TYPESCRIPT_OUTPUT, RUST_OUTPUT, RUST_OBSERVERS_OUTPUT)


def check_request_names() -> None:
    """Refuse a request field (or lookup key) that is named like an ownership key: its value would be read as who owns the
    observation, not as what the observer asked."""
    reserved = sorted((set(REQUEST_FIELDS) | set(LOOKUP_KEYS)) & set(OWNERSHIP_KEYS))
    if reserved:
        raise SystemExit(f"request names {reserved} are ownership keys the sidecar reads for itself ({OWNERSHIP_KEYS}); "
                         "a request field takes another name")


def _exact(name: str, what: str, item: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(item, dict) or set(item) != set(fields):
        got = sorted(set(item) ^ set(fields)) if isinstance(item, dict) else item
        raise SystemExit(f"{name}: {what} fields {got} do not match the shape {fields}")
    return item


def _pointer(name: str, what: str, pointer: object) -> None:
    if not isinstance(pointer, str) or not POINTER.match(pointer):
        raise SystemExit(f"{name}: {what} must be an RFC 6901 pointer (got {pointer!r})")


def _identifier(name: str, what: str, value: object) -> None:
    if not isinstance(value, str) or not IDENTIFIER.match(value):
        raise SystemExit(f"{name}: {what} must be a plain SQL identifier (got {value!r})")


def validate_usage_fields(name: str, fields: dict) -> None:
    _exact(name, "usage_fields", fields, USAGE_FIELDS)
    for rule in fields["select"]:
        _exact(name, "usage_fields.select", rule, ("pointer", "equals"))
        _pointer(name, "usage_fields.select.pointer", rule["pointer"])
    for field in ("input", "output", "cache_read", "cache_creation", "key"):
        for pointer in fields[field]:
            _pointer(name, f"usage_fields.{field}", pointer)
    for field in ("object", "model", "version", "timestamp", "cwd", "session"):
        if fields[field] is not None:
            _pointer(name, f"usage_fields.{field}", fields[field])
    if not fields["input"] and not fields["output"]:
        raise SystemExit(f"{name}: usage_fields must name an input or an output pointer")
    if fields["number"] not in NUMBER_RULES or fields["text"] not in TEXT_RULES:
        raise SystemExit(f"{name}: usage_fields.number is one of {NUMBER_RULES} and text one of {TEXT_RULES}")
    if fields["cache"] not in CACHE_RULES or fields["time"] not in TIME_RULES:
        raise SystemExit(f"{name}: usage_fields.cache is one of {CACHE_RULES} and time one of {TIME_RULES}")
    # `document`: the usage is the totals of the one JSON document of the file `<log stem><document>` beside the log (a sibling the
    # vendor rewrites as the session advances), read once under the same bounds as the log; the pointers are relative to it.
    if fields["document"] is not None and not (isinstance(fields["document"], str) and re.fullmatch(r"\.[A-Za-z0-9_.-]+", fields["document"])):
        raise SystemExit(f"{name}: usage_fields.document is a file name suffix like '.settings.json' (got {fields['document']!r})")


def _identifiers(name: str, what: str, values: object) -> None:
    if not isinstance(values, list):
        raise SystemExit(f"{name}: {what} must be a list of plain SQL identifiers (got {values!r})")
    for value in values:
        _identifier(name, what, value)


def _int(name: str, what: str, value: object, low: int = 1) -> None:
    """A plain integer at least `low` (a bool is not one, and a float never is)."""
    if not isinstance(value, int) or isinstance(value, bool) or value < low:
        raise SystemExit(f"{name}: {what} must be an integer of at least {low} (got {value!r})")


def _pending_age(name: str, what: str, age: object, *, pointer: bool) -> None:
    _exact(name, what, age, PENDING_AGE_FIELDS)
    if age["unit"] not in TIME_UNITS:
        raise SystemExit(f"{name}: {what}.unit is one of {TIME_UNITS}")
    _int(name, f"{what}.seconds", age["seconds"])
    if pointer:
        _pointer(name, f"{what}.pointer", age["pointer"])
    elif age["pointer"] is not None or age["unit"] != "seconds":
        raise SystemExit(f"{name}: {what} of a mark walk is aged by the time the adapter reads from the mark (epoch seconds): "
                         "no pointer, unit seconds")


def _column_is(name: str, what: str, rule: object) -> None:
    _exact(name, what, rule, ("column", "equals"))
    _identifier(name, f"{what}.column", rule["column"])
    if not isinstance(rule["equals"], str):
        raise SystemExit(f"{name}: {what}.equals must be text")


def validate_store(name: str, store: dict, usage: str) -> None:
    _exact(name, "store", store, STORE_FIELDS)
    rows = store["rows"]
    # The rows table is what the usage cursor walks, and what a mark walk's owner points into: a store that does neither
    # (a conversation read only through its marks) declares none.
    if rows is None:
        if usage != "none" or store["row_prefix"] is not None or any(m["owner"] is not None for m in store["marks"]):
            raise SystemExit(f"{name}: store.rows is declared when the layout has usage or a mark walk has an owner, and "
                             "row_prefix with it (and only then)")
    else:
        _exact(name, "store.rows", rows, ROWS_FIELDS)
        if not isinstance(store["row_prefix"], str):
            raise SystemExit(f"{name}: store.row_prefix is the text that starts a row's dedup key")
        for field in ("table", "id", "key"):
            _identifier(name, f"store.rows.{field}", rows[field])
        for field in ("group", "payload", "pending_until"):
            if rows[field] is not None and field != "pending_until":
                _identifier(name, f"store.rows.{field}", rows[field])
        if rows["pending_until"] is not None:
            _pointer(name, "store.rows.pending_until", rows["pending_until"])
            if rows["payload"] is None:
                raise SystemExit(f"{name}: store.rows.pending_until is a pointer into rows.payload")
        if rows["pending_max_age"] is not None:
            if rows["pending_until"] is None:
                raise SystemExit(f"{name}: store.rows.pending_max_age bounds a pending set, which needs rows.pending_until")
            _pending_age(name, "store.rows.pending_max_age", rows["pending_max_age"], pointer=True)
        if rows["cursor_tag"] is not None and (not isinstance(rows["cursor_tag"], str) or not rows["cursor_tag"]):
            raise SystemExit(f"{name}: store.rows.cursor_tag is the text a cursor of this layout is tagged with")
        if rows["raw_id"] is not None:
            _identifier(name, "store.rows.raw_id", rows["raw_id"])
        _identifiers(name, "store.rows.columns", rows["columns"])
        _identifiers(name, "store.rows.anchor", rows["anchor"])
        if rows["payload"] is None and not rows["columns"]:
            raise SystemExit(f"{name}: store.rows holds a payload or declared columns")
        known = {rows["id"], rows["key"], rows["group"], rows["payload"], *rows["columns"]}
        if not set(rows["anchor"]) <= known:
            raise SystemExit(f"{name}: store.rows.anchor names columns the rows do not declare")
    parent = store["parent"]
    if parent is not None:
        _exact(name, "store.parent", parent, PARENT_FIELDS)
        for field in ("table", "key", "dir"):
            _identifier(name, f"store.parent.{field}", parent[field])
        for field in ("up", "version"):
            if parent[field] is not None:
                _identifier(name, f"store.parent.{field}", parent[field])
        _identifiers(name, "store.parent.columns", parent["columns"])
        if parent["join"] not in JOIN_POLICIES:
            raise SystemExit(f"{name}: store.parent.join is one of {JOIN_POLICIES}")
        if parent["dir_form"] not in DIR_FORMS:
            raise SystemExit(f"{name}: store.parent.dir_form is one of {DIR_FORMS}")
        if parent["top"] is not None:
            _column_is(name, "store.parent.top", parent["top"])
            if parent["top"]["column"] not in parent["columns"]:
                raise SystemExit(f"{name}: store.parent.top.column is a declared column of the parent (the core reads it to say "
                                 "whether a mark's group is top-level)")
        if rows is not None and rows["group"] is None:
            raise SystemExit(f"{name}: a store with a parent table needs rows.group")
    tables = []
    for marks in store["marks"]:
        _exact(name, "store.marks", marks, MARKS_FIELDS)
        for field in ("table", "id"):
            _identifier(name, f"store.marks.{field}", marks[field])
        for field in ("key", "group", "owner", "payload", "stamp"):
            if marks[field] is not None:
                _identifier(name, f"store.marks.{field}", marks[field])
        _identifiers(name, "store.marks.columns", marks["columns"])
        if marks["start"] not in MARK_STARTS or marks["shrink"] not in MARK_SHRINKS or not isinstance(marks["prefix"], str):
            raise SystemExit(f"{name}: store.marks.start is one of {MARK_STARTS}, shrink one of {MARK_SHRINKS}, prefix text")
        if marks["key_scope"] not in KEY_SCOPES:
            raise SystemExit(f"{name}: store.marks.key_scope is one of {KEY_SCOPES}")
        _int(name, "store.marks.page", marks["page"])
        if marks["after"] is not None:
            _identifier(name, "store.marks.after", marks["after"])
        if marks["text_cap"] is not None:
            _int(name, "store.marks.text_cap", marks["text_cap"], 2)
        if marks["pending_max_age"] is not None:
            _pending_age(name, "store.marks.pending_max_age", marks["pending_max_age"], pointer=False)
        if marks["group"] is not None and parent is None:
            raise SystemExit(f"{name}: a mark walk with a group needs a parent table")
        if marks["owner"] is not None and marks["group"] is None:
            raise SystemExit(f"{name}: a mark walk with an owner needs a group")
        reply = marks["reply_from"]
        if reply is not None:
            _exact(name, "store.marks.reply_from", reply, REPLY_FROM_FIELDS)
            _identifier(name, "store.marks.reply_from.order", reply["order"])
            _identifier(name, "store.marks.reply_from.before", reply["before"])
            _column_is(name, "store.marks.reply_from.where", reply["where"])
            if marks["owner"] is not None or marks["group"] is None or marks["payload"] is None:
                raise SystemExit(f"{name}: store.marks.reply_from looks the reply up among the rows of the mark's own group: "
                                 "a group and a payload, and no owner (an owner already names the reply's rows)")
            if reply["before"] not in marks["columns"]:
                raise SystemExit(f"{name}: store.marks.reply_from.before is a declared column of the mark walk")
        if marks["after"] is not None and marks["after"] not in tables:
            raise SystemExit(f"{name}: store.marks.after names a walk declared before this one in the same store "
                             f"({marks['after']!r} is not among {tables})")
        tables.append(marks["table"])
    if len(set(tables)) != len(tables):
        raise SystemExit(f"{name}: each mark walk walks a table of its own")
    marker = store["marker"]
    if marker is not None:
        _exact(name, "store.marker", marker, MARKER_FIELDS)
        walk = next((m for m in store["marks"] if m["table"] == marker["on"]), None)
        if walk is None or not marker["text"] or walk["payload"] is None:
            raise SystemExit(f"{name}: store.marker needs a marker text and a mark walk with a payload to look in")
        role = marker["role"]
        if role is not None:
            if set(role) == {"pointer", "equals"}:
                _pointer(name, "store.marker.role.pointer", role["pointer"])
                if walk["owner"] is None:
                    raise SystemExit(f"{name}: a marker role read from a pointer needs the walk's owner payload")
            else:
                _column_is(name, "store.marker.role", role)
                if role["column"] not in walk["columns"]:
                    raise SystemExit(f"{name}: store.marker.role.column is a declared column of the mark walk")
        if marker["binding"] not in BINDINGS:
            raise SystemExit(f"{name}: store.marker.binding is one of {BINDINGS}")
        if marker["pointer"] is not None:
            _pointer(name, "store.marker.pointer", marker["pointer"])
            if marker["within"] is not None:
                raise SystemExit(f"{name}: store.marker.pointer reads a payload field, which a raw scan does not parse")
        if marker["within"] is not None:
            _exact(name, "store.marker.within", marker["within"], ("rows", "bytes"))
            _int(name, "store.marker.within.rows", marker["within"]["rows"])
            _int(name, "store.marker.within.bytes", marker["within"]["bytes"])
            if marker["role"] is not None:
                raise SystemExit(f"{name}: store.marker.within bounds a raw scan, which has no role")
            if marker["binding"] != "path_session":
                raise SystemExit(f"{name}: a raw marker scan finds no group, so its marker binds to the request's session")


def _modes_of(kind: str, modes: list[str]) -> list[str]:
    return [mode for mode in modes if mode in MODES_BY_SOURCE[kind]]


def validate_core(name: str, core: dict, layouts: list[dict]) -> None:
    _exact(name, "core", core, CORE_FIELDS)
    kinds = [layout["source"] for layout in layouts]
    if not set(kinds) <= set(MODES_BY_SOURCE):
        raise SystemExit(f"{name}: a parser on the core has layouts with a source kind in {tuple(MODES_BY_SOURCE)}")
    modes = core["modes"]
    answerable = {mode for kind in kinds for mode in MODES_BY_SOURCE[kind]}
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= answerable:
        raise SystemExit(f"{name}: modes must be unique and drawn from {sorted(answerable)} for the source kinds {kinds}")
    _exact(name, "core.messages", core["messages"], MESSAGE_FIELDS)
    if not core["messages"]["unsupported_mode"]:
        raise SystemExit(f"{name}: core.messages.unsupported_mode must be set")
    for entry in core["legacy_state"]:
        _exact(name, "core.legacy_state", entry, LEGACY_FIELDS)
        if entry["kind"] not in LEGACY_KINDS or not entry["prefix"] or not entry["field"]:
            raise SystemExit(f"{name}: core.legacy_state needs a prefix, a field and a kind in {LEGACY_KINDS}")
    python = _exact(name, "core.python", core["python"], PYTHON_FIELDS)
    if (python["pane_scope"] not in PANE_SCOPES or python["attribution"] not in ATTRIBUTIONS
            or python["identity_source"] not in IDENTITY_SOURCES or not python["prompt_detail"]):
        raise SystemExit(f"{name}: core.python needs a prompt_detail, a pane_scope in {PANE_SCOPES}, an attribution in "
                         f"{ATTRIBUTIONS} and an identity_source in {IDENTITY_SOURCES}")
    compat = _exact(name, "core.compat", core["compat"], tuple(field for field, _ in COMPAT_FIELDS))
    if not all(isinstance(value, bool) for value in compat.values()):
        raise SystemExit(f"{name}: core.compat holds named booleans only")
    for layout in layouts:
        usage, store, kind = layout["usage"], layout["store"], layout["source"]
        if not isinstance(layout["request"], list) or not set(layout["request"]) <= set(REQUEST_FIELDS) \
                or len(set(layout["request"])) != len(layout["request"]):
            raise SystemExit(f"{name}: layout.request is a list of distinct fields from {REQUEST_FIELDS}")
        if layout["applies_when"]:
            _identifiers(name, "layout.applies_when", layout["applies_when"])
            if len(set(layout["applies_when"])) != len(layout["applies_when"]):
                raise SystemExit(f"{name}: layout.applies_when names each table once")
            if kind != "sqlite":
                raise SystemExit(f"{name}: layout.applies_when names tables, so only a sqlite layout has one")
        wanted = set(_modes_of(kind, modes))
        usage_modes = wanted & {"usage", "session_usage", "legacy"}
        # A layout with usage lists the usage modes; one without may too, and then answers them with no events (the
        # baseline observers of such vendors did), or omits them and has them refused.
        if usage != "none" and not usage_modes:
            raise SystemExit(f"{name}: a {kind} layout with usage {usage!r} omits the usage modes")
        if kind in ("jsonl", "text"):
            if usage != "none" and layout["usage_fields"] is None:
                raise SystemExit(f"{name}: a jsonl layout with usage {usage!r} declares usage_fields")
            if usage != "none" and layout["usage_fields"]["document"] is not None and usage != "cumulative":
                raise SystemExit(f"{name}: usage_fields.document gives running totals, so the layout's usage is cumulative")
            if layout["store"] is not None or layout["rules"] is None:
                raise SystemExit(f"{name}: a jsonl layout on the core declares rules and no store")
            _exact(name, "layout.rules", layout["rules"], tuple(field for field, _ in JSONL_RULES))
            if not all(isinstance(value, bool) for value in layout["rules"].values()):
                raise SystemExit(f"{name}: layout.rules holds named booleans only")
            if "legacy" in modes and not any(l["source"] == "sqlite" for l in layouts):
                raise SystemExit(f"{name}: the legacy mode replays a row store")
        else:
            if usage == "cumulative":
                raise SystemExit(f"{name}: a sqlite layout cannot declare cumulative usage")
            if store is None or layout["rules"] is not None:
                raise SystemExit(f"{name}: a sqlite layout on the core declares a store and no jsonl rules")
            if "has_session" in modes and store["parent"] is None:
                raise SystemExit(f"{name}: the has_session mode needs store.parent")
            if "identify" in modes and store["marker"] is None:
                raise SystemExit(f"{name}: identify on a sqlite store needs store.marker")
            if "activity" in modes and not store["marks"]:
                raise SystemExit(f"{name}: the activity mode on a sqlite store needs store.marks")
            marker = store["marker"]
            if marker is not None and marker["binding"] == "path_session" and "session" not in layout["request"]:
                raise SystemExit(f"{name}: a marker that binds to the path's session needs the layout to request `session`")
            if any(m["key_scope"] == "session" for m in store["marks"]) and "session" not in layout["request"]:
                raise SystemExit(f"{name}: a mark key scoped to the session needs the layout to request `session`")
            if usage != "none":
                fields = layout["usage_fields"]
                # The row source decides the key, the record and the version (the parent's); a declaration the source
                # would ignore is refused.
                if fields is None or fields["key"] or fields["key_join"] or fields["object"] is not None or fields["version"] is not None:
                    raise SystemExit(f"{name}: a sqlite layout with usage declares usage_fields with no key (the row's key names it), "
                                     "no object and no version (the parent table's version column names it)")
                columns = {f"/{c}" for c in store["rows"]["columns"]}
                if store["rows"]["payload"] is None and not set(_all_pointers(fields)) <= columns:
                    raise SystemExit(f"{name}: with no rows.payload every usage pointer names a declared column (/column)")
            elif layout["usage_fields"] is not None:
                raise SystemExit(f"{name}: usage none declares no usage_fields")
        if layout["defers_to"] is not None:
            _exact(name, "layout.defers_to", layout["defers_to"], ("file", "up"))
            owner = next((l for l in layouts if l["store_file"] == layout["defers_to"]["file"]), None)
            if owner is None or owner["source"] != "sqlite" or owner["store"] is None or owner["store"]["parent"] is None:
                raise SystemExit(f"{name}: layout.defers_to names a sibling sqlite layout with a parent table")
            if not isinstance(layout["defers_to"]["up"], int) or layout["defers_to"]["up"] < 1:
                raise SystemExit(f"{name}: layout.defers_to.up counts the directories above the log")
    if "turns" in modes and python["identity_source"] != "file_stem":
        raise SystemExit(f"{name}: the turns mode names its session by the file stem")


def _all_pointers(fields: dict) -> list[str]:
    pointers = [*fields["input"], *fields["output"], *fields["cache_read"], *fields["cache_creation"]]
    pointers += [fields[f] for f in ("model", "version", "timestamp", "cwd", "session") if fields[f] is not None]
    pointers += [rule["pointer"] for rule in fields["select"]]
    return pointers


def validate_observer(name: str, observer: dict | None, root: Path) -> None:
    if observer is None:
        return
    _exact(name, "observer", observer, OBSERVER_FIELDS)
    parser = observer["parser"]
    if not isinstance(parser, str) or not PARSER_NAME.match(parser) or parser in RESERVED_PARSERS:
        raise SystemExit(f"{name}: observer parser must match [a-z][a-z0-9]* and not be a reserved name (got {parser!r})")
    if not observer["parser_id"]:
        raise SystemExit(f"{name}: observer parser_id must be set")
    if not observer["platforms"] or not set(observer["platforms"]) <= set(OBSERVER_PLATFORMS):
        raise SystemExit(f"{name}: observer platforms must be a non-empty subset of {OBSERVER_PLATFORMS}")
    if not observer["layouts"]:
        raise SystemExit(f"{name}: an observer declares at least one layout")
    files = []
    for layout in observer["layouts"]:
        _exact(name, "layout", layout, LAYOUT_FIELDS)
        if layout["source"] not in OBSERVER_SOURCES or layout["usage"] not in OBSERVER_USAGE:
            raise SystemExit(f"{name}: layout source must be one of {OBSERVER_SOURCES} and usage one of {OBSERVER_USAGE}")
        if layout["usage_fields"] is not None:
            validate_usage_fields(name, layout["usage_fields"])
        if layout["store"] is not None:
            validate_store(name, layout["store"], layout["usage"])
        files.append((layout["store_file"], tuple(layout["applies_when"]) if isinstance(layout["applies_when"], list) else layout["applies_when"]))
    if len(set(files)) != len(files) or (any(file is None for file, _ in files) and len(files) > 1):
        raise SystemExit(f"{name}: layouts of one observer are told apart by distinct file names, or by the set of tables a "
                         "store file must hold (and a layout with no file name is the only one)")
    if any(when and file is None for file, when in files):
        raise SystemExit(f"{name}: a layout that applies when tables exist names its store file")
    # The Python reader asks the request fields of a file before the sidecar says which schema it holds, so the layouts of one
    # file (they differ only in the schema they read) ask for the same ones.
    for file in {file for file, _ in files if file is not None}:
        asked = {tuple(layout["request"]) for layout in observer["layouts"] if layout["store_file"] == file}
        if len(asked) > 1:
            raise SystemExit(f"{name}: the layouts of {file!r} must ask for the same request fields")
    if not (root / RUST_ADAPTERS / f"{parser}.rs").exists():
        raise SystemExit(f"{name}: parser {parser!r} needs its adapter {RUST_ADAPTERS / (parser + '.rs')}")
    if observer["core"] is None:
        raise SystemExit(f"{name}: an observer declares `core`; every parser runs on the observation core")
    validate_core(name, observer["core"], observer["layouts"])


def normalize(observer: dict | None) -> None:
    """Fill the optional keys a declaration may leave out, so every later stage (and the TypeScript output) sees one
    shape: no jsonl rules and no ownership, the folded/text usage spellings, and a store with the plain defaults."""
    for layout in (observer or {}).get("layouts", []):
        layout.setdefault("rules", None)
        layout.setdefault("defers_to", None)
        layout.setdefault("request", [])
        layout.setdefault("applies_when", [])
        if isinstance(layout.get("usage_fields"), dict):
            layout["usage_fields"].setdefault("cache", "folded")
            layout["usage_fields"].setdefault("time", "text")
            layout["usage_fields"].setdefault("document", None)
            layout["usage_fields"].setdefault("cwd", None)
            layout["usage_fields"].setdefault("session", None)
        store = layout.get("store")
        if not isinstance(store, dict):
            continue
        store.setdefault("rows", None)
        store.setdefault("row_prefix", None)
        rows = store["rows"]
        if isinstance(rows, dict):
            rows.setdefault("columns", [])
            rows.setdefault("group", None)
            rows.setdefault("payload", None)
            rows.setdefault("pending_until", None)
            rows.setdefault("pending_max_age", None)
            rows.setdefault("cursor_tag", None)
            rows.setdefault("raw_id", None)
            rows.setdefault("anchor", [rows.get("key"), *([rows["group"]] if rows.get("group") else [])])
        if isinstance(store.get("parent"), dict):
            for field, value in (("up", None), ("version", None), ("columns", []), ("join", "inner"), ("top", None),
                                 ("dir_form", "as_stored")):
                store["parent"].setdefault(field, value)
        marks = store.get("marks")
        store["marks"] = [] if marks is None else [marks] if isinstance(marks, dict) else marks
        for walk in store["marks"]:
            for field, value in (("key", None), ("columns", []), ("start", "newest"), ("shrink", "keep"),
                                 ("pending_max_age", None), ("key_scope", "none"), ("page", DEFAULT_MARK_PAGE),
                                 ("text_cap", None), ("reply_from", None), ("after", None),
                                 ("prefix", store.get("mark_prefix", ""))):
                walk.setdefault(field, value)
            for field in ("group", "owner", "payload", "stamp"):
                walk.setdefault(field, None)
        store.pop("mark_prefix", None)
        marker = store.get("marker")
        if isinstance(marker, dict):
            marker.setdefault("on", store["marks"][0]["table"] if store["marks"] else None)
            for field, value in (("role", None), ("order_by_stamp", False), ("within", None), ("binding", "store_rows"),
                                 ("pointer", None)):
                marker.setdefault(field, value)


def load(root: Path) -> list[dict]:
    check_request_names()
    facts = []
    for path in sorted((root / DECLARATIONS).glob("*.json")):
        item = json.loads(path.read_text())
        if set(item) != set(FIELDS):
            raise SystemExit(f"{path.name}: fields {sorted(set(item) ^ set(FIELDS))} do not match the declaration shape")
        if item["key"] != path.stem:
            raise SystemExit(f"{path.name}: key {item['key']!r} must equal the file name")
        if set(item["capabilities"]) != set(CAPABILITIES) or not set(item["capabilities"].values()) <= set(STATUSES):
            raise SystemExit(f"{path.name}: capabilities must name {CAPABILITIES} with a status from {STATUSES}")
        if item["resume_args"] is not None and "{id}" not in item["resume_args"]:
            raise SystemExit(f"{path.name}: resume_args must contain {{id}}")
        if item["composition"] not in COMPOSITIONS:
            raise SystemExit(f"{path.name}: composition must be one of {COMPOSITIONS}")
        normalize(item["observer"])
        validate_observer(path.name, item["observer"], root)
        facts.append(item)
    if not facts:
        raise SystemExit(f"no declarations under {DECLARATIONS}")
    shapes: dict[str, tuple] = {}
    names: dict[str, str] = {}
    for item in facts:
        observer = item["observer"]
        if not observer:
            continue
        # Vendors that share a parser share one monomorphised observer: everything but the store file name and the
        # parser id must agree, so a drift between two vendors on one adapter cannot pass silently.
        shared = {"layouts": [{k: v for k, v in layout.items() if k != "store_file"} for layout in observer["layouts"]],
                  "files": [layout["store_file"] is None for layout in observer["layouts"]],
                  "core": observer["core"], "platforms": observer["platforms"]}
        if shapes.setdefault(observer["parser"], shared) != shared:
            raise SystemExit(f"{item['key']}: parser {observer['parser']!r} is shared with a vendor whose store, usage_fields, "
                             "compat or messages differ (only store_file and parser_id may)")
        if names.setdefault(pascal(observer["parser"]), observer["parser"]) != observer["parser"]:
            raise SystemExit(f"{item['key']}: parser {observer['parser']!r} collides with {names[pascal(observer['parser'])]!r}")
    return facts


def load_subjects(root: Path) -> dict:
    """The non-vendor subjects (the ordinary shell) and how their launch is composed."""
    item = json.loads((root / SUBJECTS).read_text())
    if set(item) != {"aliases", "composition"} or item["composition"] not in COMPOSITIONS:
        raise SystemExit(f"{SUBJECTS.name}: expected aliases and a composition in {COMPOSITIONS}")
    if not item["aliases"] or len(set(item["aliases"])) != len(item["aliases"]):
        raise SystemExit(f"{SUBJECTS.name}: aliases must be a non-empty list without repeats")
    return item


# ---- Python ---------------------------------------------------------------------------------------------------------


def layout_modes(layout: dict, core: dict) -> list[str]:
    """The modes one layout answers: the parser's modes that its source kind can answer."""
    return _modes_of(layout["source"], core["modes"])


def py_observer(observer: dict) -> str:
    core = observer["core"]

    def layout_text(layout: dict) -> str:
        modes = ", ".join(f"Mode.{m.upper()}" for m in layout_modes(layout, core))
        return (f"LayoutFacts(source={layout['source']!r}, usage={layout['usage']!r}, store_file={layout['store_file']!r}, "
                f"applies_when={tuple(layout['applies_when'])!r}, request={tuple(layout['request'])!r}, modes=({modes}{',' if len(layout_modes(layout, core)) == 1 else ''}))")

    layouts = ", ".join(layout_text(layout) for layout in observer["layouts"])
    legacy = ", ".join(f"LegacyState(prefix={e['prefix']!r}, field={e['field']!r}, kind={e['kind']!r})"
                       for e in core["legacy_state"])
    python = ", ".join(f"{k}={core['python'][k]!r}" for k in PYTHON_FIELDS)
    compat = ", ".join(f"{k}={core['compat'][k]!r}" for k, _ in COMPAT_FIELDS)
    modes = ", ".join(f"Mode.{m.upper()}" for m in core["modes"])
    core_text = (f"CoreFacts(modes=({modes}{',' if len(core['modes']) == 1 else ''}), legacy_state=({legacy}{',' if legacy else ''}), "
                 f"python=PythonFacts({python}), compat=CompatFacts({compat}))")
    return (f"ObserverFacts(parser={observer['parser']!r}, parser_id={observer['parser_id']!r}, "
            f"platforms={tuple(observer['platforms'])!r}, layouts=({layouts},), core={core_text})")


def python_source(facts: list[dict], subjects: dict) -> str:
    lines = [
        f'"""{HEADER}"""',
        "from enum import StrEnum",
        "from typing import NamedTuple",
        "",
        "",
        "class Mode(StrEnum):",
        '    """What a request asks of an observer; the wire spelling is the value."""',
        *(f"    {mode.upper()} = {mode!r}" for mode in MODES),
        "",
        "",
        "#: The request key a `has_session` lookup names its session in. Never an ownership key: the sidecar reads `session_id`,",
        "#: `session_generation`, `pane_id` and `workspace_path` as who owns the observation.",
        f"WANTED_SESSION = {WANTED_SESSION!r}",
        "",
        "",
        "class ShutdownFacts(NamedTuple):",
        "    graceful: bool",
        "    grace_s: float",
        "    defer_master_close: bool",
        "",
        "",
        "class CapabilityStatus(NamedTuple):",
        *(f"    {name}: str" for name in CAPABILITIES),
        "",
        "",
        "class LayoutFacts(NamedTuple):",
        "    source: str",
        "    usage: str",
        "    store_file: str | None",
        "    #: The tables the store file must all hold for this layout to apply (two schemas in one file). The sidecar says which",
        "    #: layout it read a file with; the layouts of one file share their request fields and modes, so this side never opens",
        "    #: the store to look.",
        "    applies_when: tuple[str, ...]",
        "    #: What the request carries for this file that the log does not: `cwd`, `session`, `base_epoch`.",
        "    request: tuple[str, ...]",
        "    #: The modes this layout answers.",
        "    modes: tuple[Mode, ...]",
        "",
        "",
        "class LegacyState(NamedTuple):",
        '    """A seen-bag key a Python reader once wrote, and the typed state field the one native codec reads it into."""',
        "    prefix: str",
        "    field: str",
        "    kind: str",
        "",
        "",
        "class PythonFacts(NamedTuple):",
        "    prompt_detail: str",
        "    pane_scope: str",
        "    attribution: str",
        "    identity_source: str",
        "",
        "",
        "class CompatFacts(NamedTuple):",
        '    """Named compatibility rules: each keeps one behaviour of the baseline Python reader, defect or not."""',
        *(line for name, baseline in COMPAT_FIELDS for line in (f"    #: Baseline: {baseline}.", f"    {name}: bool")),
        "",
        "",
        "class CoreFacts(NamedTuple):",
        "    modes: tuple[Mode, ...]",
        "    legacy_state: tuple[LegacyState, ...]",
        "    python: PythonFacts",
        "    compat: CompatFacts",
        "",
        "",
        "class ObserverFacts(NamedTuple):",
        "    parser: str",
        "    parser_id: str",
        "    platforms: tuple[str, ...]",
        "    layouts: tuple[LayoutFacts, ...]",
        "    core: CoreFacts",
        "",
        "",
        "class VendorFacts(NamedTuple):",
        "    key: str",
        "    composition: str",
        "    command: str",
        "    model_flag: str | None",
        "    effort_flag: str | None",
        "    known_efforts: tuple[str, ...] | None",
        "    permission_flag: str | None",
        "    resume_args: str | None",
        "    session_id_flag: str | None",
        "    login_args: str | None",
        "    supports_session_resume: bool",
        "    shutdown: ShutdownFacts | None",
        "    capabilities: CapabilityStatus",
        "    observer: ObserverFacts | None",
        "",
        "",
        "FACTS: dict[str, VendorFacts] = {",
    ]
    for item in facts:
        lines.append(f"    {item['key']!r}: VendorFacts(")
        for name in FIELDS:
            value = item[name]
            if name == "known_efforts" and value is not None:
                value = tuple(value)
            if name == "shutdown" and value is not None:
                value = "ShutdownFacts(" + ", ".join(f"{k}={value[k]!r}" for k in ("graceful", "grace_s", "defer_master_close")) + ")"
                lines.append(f"        {name}={value},")
            elif name == "capabilities":
                inner = ", ".join(f"{k}={value[k]!r}" for k in CAPABILITIES)
                lines.append(f"        {name}=CapabilityStatus({inner}),")
            elif name == "observer" and value is not None:
                lines.append(f"        {name}={py_observer(value)},")
            else:
                lines.append(f"        {name}={value!r},")
        lines.append("    ),")
    lines.append("}")
    lines += ["", "#: Non-vendor subjects (the ordinary shell) and how their launch is composed.",
              f"SUBJECT_COMPOSITION: dict[str, str] = {{{', '.join(f'{a!r}: {subjects['composition']!r}' for a in subjects['aliases'])}}}"]
    return "\n".join(lines) + "\n"


def typescript_source(facts: list[dict]) -> str:
    table = {item["key"]: {name: item[name] for name in TS_FIELDS} for item in facts}
    helper = ("\n/** Put a session id into a declared `resume_args` template. */\n"
              "export const fillId = (template: string, id: string): string => template.split('{id}').join(id)\n")
    observers = {item["key"]: item["observer"] for item in facts}
    return (f"// {HEADER}\nexport const VENDOR_FACTS = " + json.dumps(table, indent=2) + " as const\n"
            + "\n/** How each vendor's session log is observed (null where none is). */\n"
            + "export const OBSERVER_FACTS = " + json.dumps(observers, indent=2) + " as const\n" + helper)


# ---- Rust -----------------------------------------------------------------------------------------------------------


def rust_string(value: str) -> str:
    return json.dumps(value)


def rust_option(value: str | None) -> str:
    return "None" if value is None else f"Some({rust_string(value)})"


def rust_strs(values: list[str]) -> str:
    return "&[" + ", ".join(rust_string(v) for v in values) + "]"


def rust_bool(value: bool) -> str:
    return "true" if value else "false"


def rust_source(facts: list[dict], subjects: dict) -> str:
    variants = {"integrated": "Integrated", "vendor-unsupported": "VendorUnsupported",
                "not-yet-integrated": "NotYetIntegrated", "unknown": "Unknown"}
    lines = [
        f"// {HEADER}",
        "#![allow(dead_code)]",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Status {",
        *(f"    {variant}," for variant in variants.values()),
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct Shutdown {",
        "    pub graceful: bool,",
        "    pub grace_ms: u64,",
        "    pub defer_master_close: bool,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct Capabilities {",
        *(f"    pub {name}: Status," for name in CAPABILITIES),
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Composition {",
        "    Rust,",
        "    Python,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct VendorFacts {",
        "    pub key: &'static str,",
        "    pub composition: Composition,",
        "    pub command: &'static str,",
        "    pub model_flag: Option<&'static str>,",
        "    pub effort_flag: Option<&'static str>,",
        "    pub known_efforts: Option<&'static [&'static str]>,",
        "    pub permission_flag: Option<&'static str>,",
        "    pub resume_args: Option<&'static str>,",
        "    pub session_id_flag: Option<&'static str>,",
        "    pub login_args: Option<&'static str>,",
        "    pub shutdown: Option<Shutdown>,",
        "    pub capabilities: Capabilities,",
        "}",
        "",
        "pub const VENDORS: &[VendorFacts] = &[",
    ]
    for item in facts:
        shutdown = item["shutdown"]
        if shutdown is None:
            shutdown_text = "None"
        else:
            shutdown_text = ("Some(Shutdown { graceful: %s, grace_ms: %d, defer_master_close: %s })" % (
                str(shutdown["graceful"]).lower(), round(shutdown["grace_s"] * 1000),
                str(shutdown["defer_master_close"]).lower()))
        capabilities = ", ".join(f"{name}: Status::{variants[item['capabilities'][name]]}" for name in CAPABILITIES)
        lines += [
            "    VendorFacts {",
            f"        key: {rust_string(item['key'])},",
            f"        composition: Composition::{item['composition'].capitalize()},",
            f"        command: {rust_string(item['command'])},",
            f"        model_flag: {rust_option(item['model_flag'])},",
            f"        effort_flag: {rust_option(item['effort_flag'])},",
            "        known_efforts: " + ("None" if item["known_efforts"] is None else
                                        "Some(&[" + ", ".join(rust_string(v) for v in item["known_efforts"]) + "])") + ",",
            f"        permission_flag: {rust_option(item['permission_flag'])},",
            f"        resume_args: {rust_option(item['resume_args'])},",
            f"        session_id_flag: {rust_option(item['session_id_flag'])},",
            f"        login_args: {rust_option(item['login_args'])},",
            f"        shutdown: {shutdown_text},",
            f"        capabilities: Capabilities {{ {capabilities} }},",
            "    },",
        ]
    aliases = ", ".join(rust_string(alias) for alias in subjects["aliases"])
    lines += [
        "];",
        "",
        "pub fn find(key: &str) -> Option<&'static VendorFacts> {",
        "    VENDORS.iter().find(|facts| facts.key == key)",
        "}",
        "",
        "/// Non-vendor subjects (the ordinary shell): how their launch is composed.",
        f"pub const SUBJECT_ALIASES: &[&str] = &[{aliases}];",
        f"pub const SUBJECT_COMPOSITION: Composition = Composition::{subjects['composition'].capitalize()};",
        *RUST_FAULT_DECLARATION,
    ]
    return "\n".join(lines) + "\n"


def pascal(name: str) -> str:
    return name.capitalize()


def mode_variant(mode: str) -> str:
    return "".join(part.capitalize() for part in mode.split("_"))


def observer_parsers(facts: list[dict]) -> tuple[dict[str, tuple[str, dict]], list[str]]:
    """Every declared parser with its source kind and one declaration of it, and the parser names in declaration order."""
    parsers: dict[str, tuple[str, dict]] = {}
    for item in facts:
        observer = item["observer"]
        if observer:
            parsers.setdefault(observer["parser"], (observer["layouts"][0]["source"], observer["core"]))
    return parsers, list(parsers)


def rust_usage_fields(fields: dict | None) -> str:
    if fields is None:
        return "None"
    select = ", ".join(f"Select {{ pointer: {rust_string(r['pointer'])}, equals: {rust_string(r['equals'])} }}"
                       for r in fields["select"])
    return ("Some(UsageFields { "
            f"select: &[{select}], object: {rust_option(fields['object'])}, input: {rust_strs(fields['input'])}, "
            f"output: {rust_strs(fields['output'])}, cache_read: {rust_strs(fields['cache_read'])}, "
            f"cache_creation: {rust_strs(fields['cache_creation'])}, key: {rust_strs(fields['key'])}, "
            f"key_join: {rust_string(fields['key_join'])}, model: {rust_option(fields['model'])}, "
            f"version: {rust_option(fields['version'])}, timestamp: {rust_option(fields['timestamp'])}, "
            f"number: NumberRule::{pascal(fields['number'])}, text: TextRule::{pascal(fields['text'])}, "
            f"cache: CacheRule::{pascal(fields['cache'])}, time: TimeRule::{mode_variant(fields['time'])}, "
            f"document: {rust_option(fields['document'])}, cwd: {rust_option(fields['cwd'])}, session: {rust_option(fields['session'])} }})")


def rust_top(parent: dict) -> str:
    """The condition that makes a parent group top-level, with the position of its column among the parent's own: the core reads
    the cell by that position and never looks a column up by name. A column the parent does not select cannot be read, so it is
    refused here."""
    top = parent["top"]
    if top is None:
        return "None"
    if top["column"] not in parent["columns"]:
        raise SystemExit(f"parent {parent['table']!r}: its `top` column {top['column']!r} is not one of its columns ({parent['columns']})")
    return (f"Some(TopIs {{ column: {rust_string(top['column'])}, at: {parent['columns'].index(top['column'])}, "
            f"equals: {rust_string(top['equals'])} }})")


def rust_age(age: dict | None) -> str:
    if age is None:
        return "None"
    return (f"Some(PendingAge {{ pointer: {rust_option(age['pointer'])}, unit: TimeUnit::{pascal(age['unit'])}, "
            f"seconds: {age['seconds']} }})")


def rust_reply(reply: dict | None, walk: dict) -> str:
    if reply is None:
        return "None"
    return (f"Some(ReplyFrom {{ order: {rust_string(reply['order'])}, matching: ColumnIs {{ column: {rust_string(reply['where']['column'])}, "
            f"equals: {rust_string(reply['where']['equals'])} }}, before: {walk['columns'].index(reply['before'])} }})")


def rust_walk(parser: str, table: str) -> str:
    """The typed identity of a parser's walk of `table`: the crate-wide `Walk` holding the parser's own variant."""
    return f"Walk::{pascal(parser)}({pascal(parser)}Walk::{mode_variant(table)})"


def rust_after(parser: str, walk: dict) -> str:
    """The typed identity of the earlier walk this one waits for (the generator has checked it is declared before this one)."""
    return "None" if walk["after"] is None else f"Some({rust_walk(parser, walk['after'])})"


def walk_cells(store: dict, walk: dict) -> list[str]:
    """The cells the core hands an adapter for a mark of `walk`, in order: the walk's columns, then the parent's (a walk with a
    group reads its parent's)."""
    parent = store["parent"] if walk["group"] is not None and store["parent"] is not None else None
    return [*walk["columns"], *(parent["columns"] if parent else [])]


def walk_tables(observer: dict) -> list[str]:
    """The mark tables a parser walks across all its layouts, once each, in declaration order: one typed walk per table. Two
    tables that spell one variant would be one walk to an adapter, so they are refused."""
    tables: list[str] = []
    for layout in observer["layouts"]:
        for walk in (layout["store"] or {}).get("marks", []):
            if walk["table"] not in tables:
                tables.append(walk["table"])
    variants = [mode_variant(table) for table in tables]
    if len(set(variants)) != len(variants):
        raise SystemExit(f"parser {observer['parser']!r}: two mark tables spell one walk variant ({tables})")
    return tables


def walk_columns(observer: dict) -> dict[str, list[str]]:
    """The cells of each mark table a parser walks, once each. A table several layouts walk must spell the same cells in the same
    order (a column is a position), and no two cells may spell one variant."""
    columns: dict[str, list[str]] = {}
    for layout in observer["layouts"]:
        store = layout["store"] or {}
        for walk in store.get("marks", []):
            cells = walk_cells(store, walk)
            if columns.setdefault(walk["table"], cells) != cells:
                raise SystemExit(f"parser {observer['parser']!r}: the layouts that walk {walk['table']!r} declare different cells "
                                 f"({columns[walk['table']]} and {cells})")
            variants = [mode_variant(cell) for cell in cells]
            if len(set(variants)) != len(variants):
                raise SystemExit(f"parser {observer['parser']!r}: two cells of {walk['table']!r} spell one column variant ({cells})")
    return columns


def walk_enum(observer: dict) -> list[str]:
    """The typed walks of a parser on a row store, as Rust: the enum an adapter matches, how the crate-wide `Walk` yields it, and
    one column enum per walk (a cell is reached by its variant's position). A parser with no row store has none."""
    if not any(layout["source"] == "sqlite" for layout in observer["layouts"]):
        return []
    parser = pascal(observer["parser"])
    name, tables = f"{parser}Walk", walk_tables(observer)
    variants = [mode_variant(table) for table in tables]
    columns = walk_columns(observer)
    # The column enum of each walk: a walk that declares no cell has none to name, and its mark takes no column.
    column_of = {table: (f"{parser}{mode_variant(table)}Column" if columns[table] else "super::NoColumn") for table in tables}
    lines = [
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        f"pub enum {name} {{",
        *(f"    {variant}," for variant in variants),
        "}",
        "",
        f"/// A mark of one of {observer['parser']}'s walks, typed by the walk's own columns: its cells are reached by a column of that",
        "/// walk and no other (a column of another walk or parser does not compile).",
        "#[allow(dead_code)]",
        f"pub enum {parser}Mark<'a> {{",
        *(f"    {mode_variant(table)}(super::WalkMark<'a, {column_of[table]}>)," for table in tables),
        "}",
        "",
        f"impl std::ops::Deref for {parser}Mark<'_> {{",
        "    type Target = super::Mark;",
        "",
        "    fn deref(&self) -> &super::Mark {",
        "        match self {",
        *(f"            {parser}Mark::{mode_variant(table)}(mark) => mark," for table in tables),
        "        }",
        "    }",
        "}",
        "",
        f"impl super::WalkId for {name} {{",
        f"    type Marked<'a> = {parser}Mark<'a>;",
        "",
        "    fn of(walk: Walk) -> Option<Self> {",
        "        match walk {",
        f"            Walk::{parser}(own) => Some(own),",
        "            #[allow(unreachable_patterns)]",
        "            _ => None,",
        "        }",
        "    }",
        "",
        f"    fn mark<'a>(self, mark: &'a super::Mark) -> {parser}Mark<'a> {{",
        "        match self {",
        *(f"            {name}::{mode_variant(table)} => {parser}Mark::{mode_variant(table)}(super::WalkMark::new(mark))," for table in tables),
        "        }",
        "    }",
        "}",
        "",
    ]
    for table, cells in columns.items():
        if not cells:
            continue  # a walk that declares no cell has no column to name
        column =f"{parser}{mode_variant(table)}Column"
        lines += [
            f"/// The cells of {observer['parser']}'s `{table}` walk, in the order the core hands them to an adapter. A declared cell",
            "/// the adapter does not read is still a cell of the walk (and a position of the ones after it).",
            "#[allow(dead_code)]",
            "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
            f"pub enum {column} {{",
            *(f"    {mode_variant(cell)}," for cell in cells),
            "}",
            "",
            f"impl super::ColumnId for {column} {{",
            "    fn position(self) -> usize {",
            "        self as usize",
            "    }",
            "}",
            "",
        ]
    return lines


def rust_store(store: dict | None, parser: str) -> str:
    if store is None:
        return "None"
    rows = store["rows"]
    rows_text = "None" if rows is None else (
        f"Some(Rows {{ table: {rust_string(rows['table'])}, id: {rust_string(rows['id'])}, "
        f"key: {rust_string(rows['key'])}, group: {rust_option(rows['group'])}, "
        f"payload: {rust_option(rows['payload'])}, columns: {rust_strs(rows['columns'])}, "
        f"anchor: {rust_strs(rows['anchor'])}, pending_until: {rust_option(rows['pending_until'])}, "
        f"pending_max_age: {rust_age(rows['pending_max_age'])}, cursor_tag: {rust_option(rows['cursor_tag'])}, "
        f"raw_id: {rust_option(rows['raw_id'])} }})")
    parent = store["parent"]
    parent_text = "None" if parent is None else (
        f"Some(Parent {{ table: {rust_string(parent['table'])}, key: {rust_string(parent['key'])}, "
        f"dir: {rust_string(parent['dir'])}, up: {rust_option(parent['up'])}, version: {rust_option(parent['version'])}, "
        f"columns: {rust_strs(parent['columns'])}, join: JoinPolicy::{pascal(parent['join'])}, top: {rust_top(parent)}, "
        f"dir_form: DirForm::{mode_variant(parent['dir_form'])} }})")
    walks = ", ".join(
        f"Marks {{ table: {rust_string(m['table'])}, id: {rust_string(m['id'])}, key: {rust_option(m['key'])}, "
        f"group: {rust_option(m['group'])}, owner: {rust_option(m['owner'])}, payload: {rust_option(m['payload'])}, "
        f"stamp: {rust_option(m['stamp'])}, columns: {rust_strs(m['columns'])}, prefix: {rust_string(m['prefix'])}, "
        f"key_scope: KeyScope::{mode_variant(m['key_scope'])}, page: {m['page']}, "
        f"text_cap: {'None' if m['text_cap'] is None else f'Some({m['text_cap']})'}, "
        f"start: MarkStart::{mode_variant(m['start'])}, shrink: MarkShrink::{mode_variant(m['shrink'])}, "
        f"pending_max_age: {rust_age(m['pending_max_age'])}, reply_from: {rust_reply(m['reply_from'], m)}, "
        f"walk: {rust_walk(parser, m['table'])}, "
        f"after: {rust_after(parser, m)} }}"
        for m in store["marks"])
    marker = store["marker"]
    if marker is None:
        marker_text = "None"
    else:
        role = marker["role"]
        if role is None:
            role_text = "None"
        elif "pointer" in role:
            role_text = f"Some(Role::Pointer(Select {{ pointer: {rust_string(role['pointer'])}, equals: {rust_string(role['equals'])} }}))"
        else:
            role_text = f"Some(Role::Column(ColumnIs {{ column: {rust_string(role['column'])}, equals: {rust_string(role['equals'])} }}))"
        within = "None" if marker["within"] is None else (
            f"Some(Within {{ rows: {marker['within']['rows']}, bytes: {marker['within']['bytes']} }})")
        marker_text = (f"Some(Marker {{ text: {rust_string(marker['text'])}, on: {rust_string(marker['on'])}, role: {role_text}, "
                       f"order_by_stamp: {rust_bool(marker['order_by_stamp'])}, within: {within}, "
                       f"binding: Binding::{mode_variant(marker['binding'])}, pointer: {rust_option(marker['pointer'])} }})")
    return (f"Some(Store {{ rows: {rows_text}, parent: {parent_text}, marks: &[{walks}], "
            f"row_prefix: {rust_string(store['row_prefix'] or '')}, marker: {marker_text} }})")


def rust_rules(rules: dict | None) -> str:
    if rules is None:
        return "None"
    return "Some(JsonlRules { " + ", ".join(f"{name}: {rust_bool(rules[name])}" for name, _ in JSONL_RULES) + " })"


def rust_defers(defers: dict | None) -> str:
    return "None" if defers is None else f"Some(Defers {{ file: {rust_string(defers['file'])}, up: {defers['up']} }})"


def rust_core(core: dict) -> str:
    compat = ", ".join(f"{name}: {rust_bool(core['compat'][name])}" for name, _ in COMPAT_FIELDS)
    return (f"Core {{ "
            f"messages: Messages {{ unsupported_mode: {rust_string(core['messages']['unsupported_mode'])}, "
            f"non_object: {rust_option(core['messages']['non_object'])} }}, compat: Compat {{ {compat} }} }}")


def capabilities(parser: str, kind: str, core: dict) -> str:
    """The capability marker types a layout's source kind and the parser's declared modes select, as one nested tuple type."""
    chosen = []
    if kind == "jsonl" and "turns" in core["modes"]:
        chosen.append("super::ByTurns")
    if kind == "sqlite":
        if "has_session" in core["modes"]:
            chosen.append("super::BySession")
        if "identify" in core["modes"]:
            chosen.append("super::ByMarker")
    if not chosen:
        return "super::NoCapability"
    text = chosen[-1]
    for marker in reversed(chosen[:-1]):
        text = f"({marker}, {text})"
    return text


def rust_observers_source(facts: list[dict]) -> str:
    """The typed observer roster and the static dispatch. A parser runs with the source, usage and capability types its
    declaration names."""
    observers = [(item["key"], item["observer"]) for item in facts if item["observer"]]
    parsers, core = observer_parsers(facts)
    first_observer: dict[str, dict] = {}
    for _, observer in observers:
        first_observer.setdefault(observer["parser"], observer)
    source_types = {"jsonl": "super::JsonlFile", "text": "super::JsonlFile", "sqlite": "super::Sqlite"}
    usage_types = {"per_turn": "super::PerTurn", "cumulative": "super::Cumulative", "none": "super::NoUsage"}
    lines = [
        f"// {HEADER}",
        "//! The observer roster: which parser reads which vendor's logs, in which layout, answering which modes.",
        "use super::{observe, Request};",
        "use serde_json::Value;",
        "use std::io;",
        "use std::path::Path;",
        "",
        *(f"pub mod {name};" for name in core),
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Parser {",
        *(f"    {pascal(name)}," for name in parsers),
        "}",
        "",
        # One typed walk per mark table a parser's layouts walk: an adapter matches the variant it is handed, never a position or
        # a table name, and each declared walk names its identity (`Marks::walk`, a `Walk` holding the parser's own variant).
        "/// The identity of a declared mark walk: its parser and the parser's own walk.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Walk {",
        *(f"    {pascal(name)}({pascal(name)}Walk)," for name in core if walk_enum(first_observer[name])),
        "}",
        "",
        *(line for name in core for line in walk_enum(first_observer[name])),
        "// Declaration data: a source kind no vendor on the core declares yet (a sidecar) is still part of the vocabulary the",
        "// declarations are checked against.",
        "#[allow(dead_code)]",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum SourceKind {",
        *(f"    {pascal(kind)}," for kind in OBSERVER_SOURCES),
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum UsageMode {",
        "    PerTurn,",
        "    Cumulative,",
        "    None,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Platform {",
        "    Unix,",
        "    Windows,",
        "}",
        "",
        "pub const CURRENT: Platform = if cfg!(unix) { Platform::Unix } else { Platform::Windows };",
        "",
        "/// The request key a `has_session` lookup names its session in. Never an ownership key: the sidecar reads `session_id`,",
        "/// `session_generation`, `pane_id` and `workspace_path` as who owns the observation.",
        f"pub const WANTED_SESSION: &str = {rust_string(WANTED_SESSION)};",
        "",
        "/// What a request asks of an observer. A parser answers the ones its declaration lists and refuses the rest.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Mode {",
        *(f"    {mode_variant(mode)}," for mode in MODES),
        "}",
        "",
        "impl Mode {",
        "    /// The one decode of the wire's mode text.",
        "    pub fn parse(text: &str) -> Option<Self> {",
        "        Some(match text {",
        *(f"            {rust_string(mode)} => Self::{mode_variant(mode)}," for mode in MODES),
        "            _ => return None,",
        "        })",
        "    }",
        "",
        "    pub fn as_str(self) -> &'static str {",
        "        match self {",
        *(f"            Self::{mode_variant(mode)} => {rust_string(mode)}," for mode in MODES),
        "        }",
        "    }",
        "}",
        "",
        "/// How a log spells a number: `Lenient` takes a bool, a float or a numeric string; `Strict` an integer or an",
        "/// integer string. A named compatibility difference between the baseline readers.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum NumberRule {",
        "    Lenient,",
        "    Strict,",
        "}",
        "",
        "/// How a log spells a text field: `Any` renders any value as text; `String` takes strings only.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum TextRule {",
        "    Any,",
        "    String,",
        "}",
        "",
        "/// Whether the cache portions a log splits out are part of the input an event reports (`Folded`, with the event naming",
        "/// them beside it) or the log's input already excludes them and the event reports them next to it (`Separate`).",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum CacheRule {",
        "    Folded,",
        "    Separate,",
        "}",
        "",
        "/// What a timestamp pointer holds: the text to report, or epoch seconds (or microseconds) to report as an ISO 8601 UTC time.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum TimeRule {",
        "    Text,",
        "    EpochSeconds,",
        "    EpochMicros,",
        "}",
        "",
        "/// How rows are joined to the parent (session) table: `Inner` drops a row with no parent, `Left` keeps it.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum JoinPolicy {",
        "    Inner,",
        "    Left,",
        "}",
        "",
        "/// Where a first activity pass of a mark walk starts.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum MarkStart {",
        "    /// Past the newest mark; an empty table leaves the walk unstarted.",
        "    Newest,",
        "    /// Past the newest mark, or 0 for an empty table.",
        "    NewestOrZero,",
        "    /// At the oldest mark: the table's history is delivered.",
        "    Oldest,",
        "}",
        "",
        "/// What a mark walk does when its table has fewer marks than the last mark it delivered.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum MarkShrink {",
        "    Keep,",
        "    /// Restart past the newest mark and deliver nothing this pass.",
        "    Newest,",
        "    /// Restart at the oldest mark.",
        "    Oldest,",
        "}",
        "",
        "/// A record is selected when the value at `pointer` (RFC 6901) is the string `equals`.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Select {",
        "    pub pointer: &'static str,",
        "    pub equals: &'static str,",
        "}",
        "",
        "/// A row is selected when the column holds the text `equals`.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct ColumnIs {",
        "    pub column: &'static str,",
        "    pub equals: &'static str,",
        "}",
        "",
        "/// A parent group is top-level when its `column` holds the text `equals`. `at` is the column's position in the parent's `columns`,",
        "/// worked out when the facts are generated (a column the parent does not select is refused there), so the core never looks a",
        "/// column up by its name.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct TopIs {",
        "    pub column: &'static str,",
        "    pub at: usize,",
        "    pub equals: &'static str,",
        "}",
        "",
        "/// The unit of an epoch timestamp (a declaration picks one; the others are the vocabulary).",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum TimeUnit {",
        "    Seconds,",
        "    Millis,",
        "}",
        "",
        "/// A pending row (or mark) the vendor never finishes is dropped once its own timestamp is `seconds` older than the newest",
        "/// timestamp the pass read in the store (a store's own clock, never the machine's). A row reads the timestamp at `pointer`",
        "/// in its payload; a mark is aged by the time its adapter reads from it (`Progress::stamp`, epoch seconds).",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct PendingAge {",
        "    pub pointer: Option<&'static str>,",
        "    pub unit: TimeUnit,",
        "    pub seconds: u64,",
        "}",
        "",
        "/// What a request carries for a store file that the store does not: the working directory and session the discovery",
        "/// reader resolved for it, and the scan clock.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum RequestField {",
        "    Cwd,",
        "    Session,",
        "    BaseEpoch,",
        "    CliVersion,",
        "    NowMs,",
        "    Namespace,",
        "}",
        "",
        "/// Where a found marker's session comes from.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Binding {",
        "    /// The group (and working directory) of the row that holds the marker.",
        "    StoreRows,",
        "    /// The session the request named for this store file: the store holds one session, and a raw scan finds no group.",
        "    PathSession,",
        "}",
        "",
        "/// Where a JSON record keeps its usage: pointers only, no expressions. For a row store with no payload column the record",
        "/// is the row's declared columns, each a key of the record.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct UsageFields {",
        "    pub select: &'static [Select],",
        "    pub object: Option<&'static str>,",
        "    pub input: &'static [&'static str],",
        "    pub output: &'static [&'static str],",
        "    pub cache_read: &'static [&'static str],",
        "    pub cache_creation: &'static [&'static str],",
        "    pub key: &'static [&'static str],",
        "    pub key_join: &'static str,",
        "    pub model: Option<&'static str>,",
        "    pub version: Option<&'static str>,",
        "    pub timestamp: Option<&'static str>,",
        "    pub number: NumberRule,",
        "    pub text: TextRule,",
        "    pub cache: CacheRule,",
        "    pub time: TimeRule,",
        "    /// A file name suffix: the usage is the totals of the JSON document `<log stem><suffix>` beside the log, not of the log's records.",
        "    pub document: Option<&'static str>,",
        "    /// The working directory a usage record names for itself (the request's, where the record names none).",
        "    pub cwd: Option<&'static str>,",
        "    /// The session a usage record names for itself (the request's, where the record names none).",
        "    pub session: Option<&'static str>,",
        "}",
        "",
        "/// The table of rows a usage scan walks (a message or ledger table): id, key, owning group, and what a row holds: a JSON",
        "/// payload, declared scalar `columns`, or both. `anchor` names the columns that tell a reused row id from the row the cursor",
        "/// saw; `pending_until` is a payload pointer whose number is 0 while the vendor has not finished the row.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Rows {",
        "    pub table: &'static str,",
        "    pub id: &'static str,",
        "    pub key: &'static str,",
        "    pub group: Option<&'static str>,",
        "    pub payload: Option<&'static str>,",
        "    pub columns: &'static [&'static str],",
        "    pub anchor: &'static [&'static str],",
        "    pub pending_until: Option<&'static str>,",
        "    pub pending_max_age: Option<PendingAge>,",
        "    /// A cursor of this layout is tagged `schema: <tag>`; a saved cursor without the tag was taken on the file's other",
        "    /// schema, so the layout starts at the newest row instead of crediting the copies of rows it already saw.",
        "    pub cursor_tag: Option<&'static str>,",
        "    /// A usage event carries the id of its row as `raw: {<raw_id>: <id>}`, the field the baseline's reader fills (and reads back).",
        "    pub raw_id: Option<&'static str>,",
        "}",
        "",
        "/// How a store spells a group's working directory.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum DirForm {",
        "    /// As stored.",
        "    AsStored,",
        "    /// A Windows path the store spells with forward slashes, in the platform's own spelling (a no-op off Windows).",
        "    WindowsNative,",
        "}",
        "",
        "/// The table a row's group belongs to (a session table). `top` is an extra condition for a top-level group.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Parent {",
        "    pub table: &'static str,",
        "    pub key: &'static str,",
        "    pub dir: &'static str,",
        "    pub up: Option<&'static str>,",
        "    pub version: Option<&'static str>,",
        "    pub columns: &'static [&'static str],",
        "    pub join: JoinPolicy,",
        "    pub top: Option<TopIs>,",
        "    pub dir_form: DirForm,",
        "}",
        "",
        "/// What names a mark's dedup key beyond the walk's prefix.",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum KeyScope {",
        "    /// Nothing: the prefix and the mark's key.",
        "    None,",
        "    /// The session the request names for the store file: the prefix, the session, `:` and the mark's key.",
        "    Session,",
        "}",
        "",
        "/// Where a turn-ending mark finds its reply: the payload of the newest row of the mark's own group whose `matching.column`",
        "/// holds `matching.equals`, among those with `order` below the mark's own cell at position `before`.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct ReplyFrom {",
        "    pub order: &'static str,",
        "    pub matching: ColumnIs,",
        "    /// The position of the walk's column whose value bounds the reply (the walk's own cells come first).",
        "    pub before: usize,",
        "}",
        "",
        "/// A table an activity scan walks: parts that belong to a row (`owner`) and a group, or the rows of one table",
        "/// themselves (no owner, and no group when the table has no parent). `columns` are extra cells for the adapter. `page`",
        "/// is how many new marks one pass reads, and `text_cap` the length a reply's text is cut to (head and tail). A pass answers",
        "/// one observation document of at most 8 MiB, so a vendor declares the `page` its reach needs and tests its largest first",
        "/// pass; a walk also stops at the answer ledger (`observe/answer.rs`, shared by every walk), whatever its page says.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Marks {",
        "    pub table: &'static str,",
        "    pub id: &'static str,",
        "    pub key: Option<&'static str>,",
        "    pub group: Option<&'static str>,",
        "    pub owner: Option<&'static str>,",
        "    pub payload: Option<&'static str>,",
        "    pub stamp: Option<&'static str>,",
        "    pub columns: &'static [&'static str],",
        "    pub prefix: &'static str,",
        "    pub key_scope: KeyScope,",
        "    pub page: usize,",
        "    pub text_cap: Option<usize>,",
        "    pub start: MarkStart,",
        "    pub shrink: MarkShrink,",
        "    pub pending_max_age: Option<PendingAge>,",
        "    pub reply_from: Option<ReplyFrom>,",
        "    /// This walk's typed identity (generated from the declaration, so the adapter is handed its parser's own walk variant and",
        "    /// never matches a position or a table name).",
        "    pub walk: Walk,",
        "    /// An earlier walk of the store this one waits for: while that walk's page came back full (more of its marks remain)",
        "    /// this walk reads nothing, so what it delivers never lands before the earlier walk's last.",
        "    pub after: Option<Walk>,",
        "}",
        "",
        "/// Who says a mark is a user's: a pointer in the owner's payload, or a column of the mark.",
        "#[derive(Clone, Copy, Debug)]",
        "pub enum Role {",
        "    Pointer(Select),",
        "    Column(ColumnIs),",
        "}",
        "",
        "/// A raw scan bound: the first `rows` rows, the first `bytes` bytes of each payload.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Within {",
        "    pub rows: usize,",
        "    pub bytes: usize,",
        "}",
        "",
        "/// A pane marker a user message carries; the lookup finds the group that holds it, in the payloads of the walk `on`.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Marker {",
        "    pub text: &'static str,",
        "    pub on: &'static str,",
        "    pub role: Option<Role>,",
        "    pub order_by_stamp: bool,",
        "    pub within: Option<Within>,",
        "    pub binding: Binding,",
        "    /// The marker text is searched in the string at this pointer of the mark's payload, not in the whole payload.",
        "    pub pointer: Option<&'static str>,",
        "}",
        "",
        "/// A row store: table and column names (validated, quoted identifiers; the core builds fixed query shapes from them).",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Store {",
        "    /// The table a usage cursor walks (and a mark's owner points into); none for a store read through its marks only.",
        "    pub rows: Option<Rows>,",
        "    pub parent: Option<Parent>,",
        "    pub marks: &'static [Marks],",
        "    pub row_prefix: &'static str,",
        "    pub marker: Option<Marker>,",
        "}",
        "",
        "/// The compatibility rules only a JSONL log reads (each keeps one behaviour of a baseline reader).",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct JsonlRules {",
        *(line for name, baseline in JSONL_RULES for line in (f"    /// Baseline: {baseline}.", f"    pub {name}: bool,")),
        "}",
        "",
        "/// This layout yields to the store `file`, `up` directories above the log, when that store's parent table already",
        "/// holds the session the log's directory is named for.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Defers {",
        "    pub file: &'static str,",
        "    pub up: usize,",
        "}",
        "",
        "/// One log layout of a vendor: its source kind, usage mode, file name and the syntax facts of that source.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Layout {",
        "    // Declaration data the generator and the tests check; the dispatch is already chosen by them.",
        "    #[allow(dead_code)]",
        "    pub source: SourceKind,",
        "    #[allow(dead_code)]",
        "    pub usage: UsageMode,",
        "    pub store_file: Option<&'static str>,",
        "    /// The tables the store file must all hold for this layout to apply (two schemas in one file); empty for the plain one.",
        "    pub applies_when: &'static [&'static str],",
        "    /// The modes this layout answers: the parser's modes that the layout's source kind can answer.",
        "    pub modes: &'static [Mode],",
        "    pub request: &'static [RequestField],",
        "    pub usage_fields: Option<UsageFields>,",
        "    pub store: Option<Store>,",
        "    pub rules: Option<JsonlRules>,",
        "    pub defers_to: Option<Defers>,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Messages {",
        "    pub unsupported_mode: &'static str,",
        "    pub non_object: Option<&'static str>,",
        "}",
        "",
        "/// Named compatibility rules: each keeps one behaviour of a baseline reader, defect or not. Python reads them.",
        "// Some rules are read only by the Python reader (through the generated Python facts); they are carried here so the two",
        "// stay one declaration.",
        "#[allow(dead_code)]",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Compat {",
        *(line for name, baseline in COMPAT_FIELDS for line in (f"    /// Baseline: {baseline}.", f"    pub {name}: bool,")),
        "}",
        "",
        "/// What the generic core needs beyond the layouts.",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct Core {",
        "    pub messages: Messages,",
        "    pub compat: Compat,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug)]",
        "pub struct ObserverFacts {",
        "    pub vendor: &'static str,",
        "    pub parser: Parser,",
        "    pub parser_id: &'static str,",
        "    pub platforms: &'static [Platform],",
        "    pub layouts: &'static [Layout],",
        "    pub core: Core,",
        "}",
        "",
        "impl ObserverFacts {",
        "    /// The layouts named for `path`'s file, in declaration order (a layout with no file name takes any path). A store file",
        "    /// that may hold more than one schema has one layout per schema; which one applies is for the observation's own",
        "    /// connection to say (`Source::layout`), so choosing among them never opens the store.",
        "    pub fn candidates<'p>(&'static self, path: &'p Path) -> impl Iterator<Item = &'static Layout> + 'p {",
        "        let name = path.file_name().and_then(|name| name.to_str());",
        "        self.layouts.iter().filter(move |layout| layout.store_file.is_none_or(|file| Some(file) == name))",
        "    }",
        "",
        "    /// The slot of the first layout named for `path`'s file; the first layout when none is. A file's layouts share one",
        "    /// source, usage and capability type (the generator refuses any that do not), so this picks the dispatch arm.",
        "    pub fn slot(&self, path: &Path) -> usize {",
        "        let name = path.file_name().and_then(|name| name.to_str());",
        "        self.layouts.iter().position(|layout| layout.store_file.is_none_or(|file| Some(file) == name)).unwrap_or(0)",
        "    }",
        "",
        "    pub fn layout_for(&'static self, path: &Path) -> Option<&'static Layout> {",
        "        self.layouts.get(self.slot(path))",
        "    }",
        "}",
        "",
        "pub const OBSERVERS: &[ObserverFacts] = &[",
    ]
    for key, observer in observers:
        platforms = ", ".join(f"Platform::{pascal(p)}" for p in observer["platforms"])
        lines += [
            "    ObserverFacts {",
            f"        vendor: {rust_string(key)},",
            f"        parser: Parser::{pascal(observer['parser'])},",
            f"        parser_id: {rust_string(observer['parser_id'])},",
            f"        platforms: &[{platforms}],",
            "        layouts: &[",
        ]
        for layout in observer["layouts"]:
            lines += [
                "            Layout {",
                f"                source: SourceKind::{pascal(layout['source'])},",
                f"                usage: UsageMode::{mode_variant(layout['usage'])},",
                f"                store_file: {rust_option(layout['store_file'])},",
                f"                applies_when: {rust_strs(layout['applies_when'])},",
                f"                modes: &[{', '.join(f'Mode::{mode_variant(m)}' for m in layout_modes(layout, observer['core']))}],",
                f"                request: &[{', '.join(f'RequestField::{mode_variant(f)}' for f in layout['request'])}],",
                f"                usage_fields: {rust_usage_fields(layout['usage_fields'])},",
                f"                store: {rust_store(layout['store'], observer['parser'])},",
                f"                rules: {rust_rules(layout['rules'])},",
                f"                defers_to: {rust_defers(layout['defers_to'])},",
                "            },",
            ]
        lines += [
            "        ],",
            f"        core: {rust_core(observer['core'])},",
            "    },",
        ]
    lines += [
        "];",
        "",
        "/// The observer declared for `vendor` on this platform.",
        "pub fn find(vendor: &str) -> Option<&'static ObserverFacts> {",
        "    OBSERVERS.iter().find(|facts| facts.vendor == vendor && facts.platforms.contains(&CURRENT))",
        "}",
        "",
        "/// One observation: the result document.",
        "/// Every parser is named here, so a declared parser without a branch does not compile. The usage and capability",
        "/// types of a parser come from its declaration, so an adapter that does not implement what its declaration",
        "/// names does not compile either.",
        "pub fn dispatch(facts: &'static ObserverFacts, args: &Value) -> io::Result<Value> {",
        "    match facts.parser {",
    ]
    for name in core:
        _, declared = parsers[name]
        layouts = next(o["layouts"] for _, o in observers if o["parser"] == name)
        # One arm per store file: the layouts of one file (one per schema it may hold) share their types, so the arm is chosen
        # by file name and the schema by the observation's own connection.
        arms: list[tuple[int, str]] = []
        arm_of_file: dict[str | None, str] = {}
        for slot, layout in enumerate(layouts):
            kind = layout["source"]
            if kind not in source_types:
                raise SystemExit(f"parser {name!r}: no core source for kind {kind!r}")
            if layout["usage"] == "cumulative" and kind == "sqlite":
                raise SystemExit(f"parser {name!r}: no core source reads cumulative usage from a row store")
            arm = (f"observe::<{name}::{pascal(name)}, {source_types[kind]}, {usage_types[layout['usage']]}, "
                   f"{capabilities(name, kind, declared)}>(&request, facts)?")
            if layout["store_file"] not in arm_of_file:
                arms.append((slot, arm))
            if arm_of_file.setdefault(layout["store_file"], arm) != arm:
                raise SystemExit(f"parser {name!r}: the layouts of {layout['store_file']!r} must share one source, usage and "
                                 "capability type (they differ only in the schema they read)")
        if len(arms) == 1:
            body = [f"            let document = {arms[0][1]};"]
        else:
            body = ["            // One arm per store file: each names the source, usage and capability types of its own declaration.",
                    "            let document = match facts.slot(request.path) {",
                    *(f"                {slot} => {arm}," for slot, arm in arms[:-1]),
                    f"                _ => {arms[-1][1]},", "            };"]
        lines += [f"        Parser::{pascal(name)} => {{", "            let request = Request::parse(args)?;", *body,
                  "            Ok(document)", "        }"]
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def outputs(root: Path = ROOT) -> dict[Path, str]:
    facts = load(root)
    subjects = load_subjects(root)
    return {
        RUST_OBSERVERS_OUTPUT: rust_observers_source(facts),
        PYTHON_OUTPUT: python_source(facts, subjects),
        SHARED_TYPESCRIPT_OUTPUT: typescript_source(facts),
        RUST_OUTPUT: rust_source(facts, subjects),
    }


def write_all(root: Path = ROOT) -> None:
    for relative, content in outputs(root).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def stale(root: Path = ROOT) -> list[Path]:
    return [relative for relative, content in outputs(root).items()
            if not (root / relative).exists() or (root / relative).read_text() != content]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.check:
        names = [str(path) for path in stale(args.root)]
        if names:
            sys.exit("Stale generated agent CLI facts (run scripts/generate-agent-cli-facts.py): " + ", ".join(names))
    else:
        write_all(args.root)


if __name__ == "__main__":
    main()
