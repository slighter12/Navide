"""PROPOSED revision of the frozen G1-E locality check (h1f, J6; revised in h1f2 as r3). A copy of `backend/tests/cli_regression/support/g1e/locality_check.py` with four method
changes, each marked `PROPOSED (n)` below. It is never imported by the repository and replaces nothing: the manager decides whether the re-freeze applies it.

  (1) the Rust strip also removes OUT-OF-LINE modules declared under `#[cfg(test)]`, `#[cfg(all(test, ...))]` and `#[cfg(feature = "fault-hooks")]` (derived from the `mod` declarations,
      transitively, not from a list of names), and inline modules under the same attributes;
  (2) `vendor_facts/declarations.rs` (the generated `VENDORS` declaration table, no function, no branch) and `observe/adapters/declarations.rs` (the generated `OBSERVERS` declaration data, no function, no branch) are excluded
      by exact path, as the frozen rule already excludes `claude_facts.rs` (generated data) and `adapters.rs` (the roster) whose roles they took over. The shared functions
      (`observe/adapters/mod.rs`) stay in scope;
  (3) the Rust scope gains the V6 admission rule with the same two-verdict standard (the allowlist schema becomes `/3`, an entry of the Rust scope has its own key set);
  (4) a stripped Rust block keeps its line breaks, so a finding's line is the file's own line (a frozen-rule change in its own right: every reported Rust line differs from the frozen text's).

The frozen text follows, unchanged except where marked.

The frozen G1-E locality check (E2 v10 section 9; decision fact `localityViolation`; E1 13.1 and 13.3).

Scans, for vendor-key comparisons, sleeps used as waits and bare deadline literals, exactly this scope (derived from the repository tree):
  Rust    every `*.rs` at ANY depth under `native/navide-cli-runtime/src/` (and any module added there, in a subdirectory too) EXCEPT the adapter modules `claude.rs`, `codex.rs`,
          `opencode.rs`, the roster `adapters.rs`, the generated data `claude_facts.rs` and the test file `initial_retention_test.rs`, which are excluded by their exact path DIRECTLY in
          `src/` (a file of that name in a subdirectory is in scope) (so `posix.rs`, `darwin.rs`, `main.rs` and any new shared module are in scope);
  Python  `backend/agent_team_backend/cli_runtime.py`, `cli_runtime_observation.py`, `terminals.py` and every other `cli_runtime*.py` EXCEPT the adapter modules
          `cli_runtime_claude.py`, `cli_runtime_codex.py`, `cli_runtime_opencode.py` and the roster `cli_runtime_adapters.py`.
F0 freezes this scope as a list of paths with the SHA-256 of each file's bytes (`scope_record`); the freeze and the decider recompute it on the tree and refuse any difference.
Rust test modules (`#[cfg(test)] mod ... {}`) and the harness-only `mod fault` are cut out first; comments are not code.
Allowlist (E2 v13 V6, replacing v10 section 9 item (1); a file with a hash). A finding in the PYTHON scope is admitted only if BOTH hold:
  (a) it is not evaluation code, occurrence-exact: its line is an unchanged line of a line-level diff (`difflib.SequenceMatcher(None, ..., autojunk=False)`, the one frozen
      algorithm) between the file's baseline reference content (V5 item 3, from the F0 manifest) and its loaded bytes; it maps to ONE reference line with the same text and the
      same enclosing symbol. Every inserted or modified line is rejected even when an identical text exists elsewhere, so a second copy of an admitted wait is never admitted.
      A mapping that is absent, or that differs from the entry's recorded line numbers, rejects the finding;
  (b) it is not a wait of the candidate path: two distinct reviewers (distinct reviewer id, session id and report hash) recorded, on the F0 tree, the same classification
      `python-arm-lifecycle-only` or `terminal-stream`. This module only checks that the two verdict references are present, distinct and agree with the entry; the judgment is theirs.
Each entry names file, category, symbol, the line text, the reference line and the loaded line it maps to, the classification, the reason and both verdict references; one entry
admits exactly one occurrence. PROPOSED (3): a finding in the RUST scope is admitted by an entry under the second half of that standard, (b) with its own classifications. There
is no (a) for Rust: the Rust runtime is the candidate path, it has no baseline reference to diff against, so occurrence-exactness is against the scanned bytes (the entry names the
file, category, symbol, text and the line the scan reports, and admits exactly that one occurrence). An entry that no longer admits a finding is STALE and is itself a failure of the
run, so the list cannot rot into a bypass. Anything else in scope is a finding. The check is syntactic: an indirection (a constant that holds a vendor name) can evade it; that
residual is what the reviewers' reading of the E3 change list and `maintainabilityComplete` cover (E2 v10 section 9).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

# Paths relative to RUST_DIR, matched exactly: only a file directly in src/ is excluded; a file of the same name in a subdirectory is in scope.
# PROPOSED (2): `vendor_facts/declarations.rs` and `observe/adapters/declarations.rs` (paths under src/) join the list: generated declaration data only. `vendor_facts.rs` (its types, `find`,
# `Opt::*`, `is_fault_declaration`) is NOT excluded; it stays scanned (r3, K1).
# `observe/adapters/mod.rs` (the generated shared functions: `Mode::parse`, `candidates`, `slot`, `layout_for`, `find`, `dispatch`) is NOT excluded; it stays scanned (r3, K1).
FROZEN_RUST_EXCLUDE = {"claude.rs", "codex.rs", "opencode.rs", "adapters.rs", "claude_facts.rs", "initial_retention_test.rs"}
PROPOSED_RUST_EXCLUDE = {"vendor_facts/declarations.rs", "observe/adapters/declarations.rs"}
RUST_EXCLUDE = FROZEN_RUST_EXCLUDE | PROPOSED_RUST_EXCLUDE
PY_EXCLUDE = {"cli_runtime_claude.py", "cli_runtime_codex.py", "cli_runtime_opencode.py", "cli_runtime_adapters.py"}
PY_FIXED = ["cli_runtime.py", "cli_runtime_observation.py", "terminals.py"]
RUST_DIR = Path("native/navide-cli-runtime/src")
PY_DIR = Path("backend/agent_team_backend")
CATEGORIES = ("vendor-key", "sleep-wait", "bare-deadline")
ALLOWLIST_SCHEMA = "acr-g1e-locality-allowlist/3"          # PROPOSED (3): /2 -> /3, because an entry of the Rust scope has its own key set
CLASSIFICATIONS = ("python-arm-lifecycle-only", "terminal-stream")
# PROPOSED (3): what two reviewers attest for a Rust finding: the line is not a vendor-specific wait or literal of the candidate path.
RUST_CLASSIFICATIONS = ("vendor-independent-rate-limit", "non-blocking-probe")
ENTRY_KEYS = {"file", "category", "symbol", "text", "referenceLine", "loadedLine", "classification", "reason", "verdicts"}
RUST_ENTRY_KEYS = {"file", "category", "symbol", "text", "loadedLine", "classification", "reason", "verdicts"}    # PROPOSED (3): no referenceLine
VERDICT_KEYS = {"reviewer", "session", "report", "classification"}

RUST_RULES = {
    "sleep-wait": re.compile(r"(?:thread::|tokio::time::)?\bsleep\s*\("),
    "bare-deadline": re.compile(r"Duration::from_(?:secs|millis|micros|nanos)\(\s*[0-9]|libc::poll\([^;]*,\s*[0-9]+\s*\)|(?:recv_timeout|wait_timeout)\(\s*[0-9]"),
    "vendor-key": re.compile(r"agent_key\s*[!=]=|claude_facts::KEY|\"claude\"|\"codex\"|\"opencode\""),
}
PY_RULES = {
    "sleep-wait": re.compile(r"asyncio\.sleep\(|time\.sleep\("),
    "bare-deadline": re.compile(r"wait_for\(.*,\s*[0-9.]+\)\s*$|asyncio\.timeout\(\s*[0-9.]+|timeout\s*=\s*[0-9.]+|\.result\(\s*[0-9.]+"),
    "vendor-key": re.compile(r"[\"'](?:claude|codex|opencode)[\"']|vendor\(\s*[\"']|agent_key\s*[!=]=\s*[\"']"),
}
# E1 13.3 sites 6 and 7: what the shared code said before, so the report can show before/after.
FORMER_SITES = {
    "site-6 posix.rs cleanup (Rust shared kill code)": {"file": str(RUST_DIR / "posix.rs"), "before": r"agent_key\s*==\s*claude_facts::KEY|claude_facts::(?:GRACEFUL|GRACE_MS|DEFER_MASTER_CLOSE)"},
    "site-7 cli_runtime.py observe_claude (Python shared service)": {"file": str(PY_DIR / "cli_runtime.py"), "before": r"def observe_claude|vendor\(\s*[\"']claude[\"']|agent_key\s*==\s*[\"']claude[\"']|rust-claude-jsonl-v1"},
}

# Method switches, so a script can show what each proposed change does on its own. The proposal is all of (1) to (4) on; `STRIP_GATED_MODULES` carries (1) and (4) together
# (the line-break-keeping strip is in the branch that strips gated modules), so a script's "1" stage already reports the file's own lines.
STRIP_GATED_MODULES = True          # PROPOSED (1)
EXCLUDE_DECLARATION_FILES = True    # PROPOSED (2)


def strip_balanced(text: str, opener: re.Pattern) -> str:
    out, position = [], 0
    for match in opener.finditer(text):
        if match.start() < position:
            continue
        depth, index = 1, match.end()
        while depth and index < len(text):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        out.append(text[position:match.start()])
        position = index
    out.append(text[position:])
    return "".join(out)


# ---- PROPOSED (1): a module compiled only into a test or a fault-hooks build ----------------------------------------------------------------------------------------------------

def _split_arguments(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    if text[start:].strip():
        parts.append(text[start:].strip())
    return parts


def _gated(expression: str) -> bool:
    """Is a `cfg` expression true only in a test build or a fault-hooks build? `test` and `feature = "fault-hooks"` are; `all(...)` is when one of its terms is; `any(...)` and `not(...)` are not (a
    `cfg(any(target_os = "linux", test))` module is compiled in the production Linux build)."""
    expression = expression.strip()
    if expression == "test" or re.fullmatch(r'feature\s*=\s*"fault-hooks"', expression):
        return True
    match = re.fullmatch(r"all\((.*)\)", expression, re.S)
    return bool(match) and any(_gated(term) for term in _split_arguments(match.group(1)))


def gated_attributes(attributes: list[str]) -> bool:
    return any(_gated(match.group(1)) for attribute in attributes if (match := re.fullmatch(r"#\[cfg\((.*)\)\]", attribute.strip(), re.S)))


_LEADING_ATTRIBUTES = re.compile(r"^\s*((?:#\[[^\]\n]*\]\s*)*)(.*)$")
_MODULE_DECLARATION = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;")


def declared_modules(text: str) -> list[tuple[str, list[str]]]:
    """The out-of-line modules a file declares (`mod name;`) with the attributes that precede each, on the lines before it or on its own line. An attribute that is not closed on its line cannot be
    read with certainty: ScopeError."""
    found, attributes = [], []
    for line in text.split("\n"):
        leading, rest = _LEADING_ATTRIBUTES.match(line).groups()
        if line.lstrip().startswith("#[") and not leading.strip():
            raise ScopeError(f"an attribute spread over several lines cannot be read with certainty: {line.strip()!r}")
        attributes = attributes + re.findall(r"#\[[^\]\n]*\]", leading)
        rest = rest.strip()
        if not rest or rest.startswith("//"):
            continue                                  # an attribute line, a blank line or a comment: the attributes wait for the item they belong to
        if match := _MODULE_DECLARATION.match(rest):
            found.append((match.group(1), attributes))
        attributes = []
    return found


def _module_file(declaring: Path, name: str, attributes: list[str]) -> list[Path]:
    """The files a `mod name;` declared in `declaring` (relative to the source directory) can name: the `#[path]` file, or `<dir>/name.rs` and `<dir>/name/mod.rs`, where `<dir>` is the declaring
    file's directory for a `mod.rs`, `main.rs` or `lib.rs` and its same-named directory otherwise."""
    for attribute in attributes:
        if match := re.fullmatch(r'#\[path\s*=\s*"([^"]+)"\]', attribute.strip()):
            return [declaring.parent / match.group(1)]
    base = declaring.parent if declaring.name in ("mod.rs", "main.rs", "lib.rs") else declaring.parent / declaring.stem
    return [base / f"{name}.rs", base / name / "mod.rs"]


def gated_module_files(rust_root: Path) -> set[str]:
    """Every file (relative to the source directory, posix) that is compiled only into a test or fault-hooks build: the module a gated `mod name;` declares, and, transitively, every module such a file
    declares. Derived from the declarations in the tree, never from a list of names."""
    files = sorted(p for p in rust_root.rglob("*.rs") if p.is_file())
    declarations = {p.relative_to(rust_root): declared_modules(p.read_text()) for p in files}
    gated: set[Path] = set()
    pending = [(declaring, name, attributes) for declaring, mods in declarations.items() for name, attributes in mods if gated_attributes(attributes)]
    while pending:
        declaring, name, attributes = pending.pop()
        for candidate in _module_file(declaring, name, attributes):
            if candidate in declarations and candidate not in gated:
                gated.add(candidate)
                pending += [(candidate, child, child_attributes) for child, child_attributes in declarations[candidate]]
    return {p.as_posix() for p in gated}


_INLINE_MODULE = re.compile(r"((?:#\[[^\]\n]*\]\s*)+)(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*\{")


def rust_production(text: str) -> str:
    if STRIP_GATED_MODULES:
        # PROPOSED (1): an inline module under any gated attribute (`cfg(test)`, `cfg(all(test, ...))`, `cfg(feature = "fault-hooks")`), not only `#[cfg(test)] mod name {` and `mod fault {`.
        out, position = [], 0
        for match in _INLINE_MODULE.finditer(text):
            if match.start() < position or not gated_attributes([a for a in re.findall(r"#\[[^\]\n]*\]", match.group(1))]):
                continue
            depth, index = 1, match.end()
            while depth and index < len(text):
                depth += {"{": 1, "}": -1}.get(text[index], 0)
                index += 1
            # PROPOSED (4): a stripped block leaves its line breaks behind, so a finding's line is the line of the file itself. The frozen strip removed them, which put a finding on a line of the
            # stripped text (linux.rs:1884 for the file's line 1924), a number no reader and no later edit of a test module could keep.
            out.append(text[position:match.start()] + "\n" * text[match.start():index].count("\n"))
            position = index
        out.append(text[position:])
        text = "".join(out)
    else:
        text = strip_balanced(text, re.compile(r"#\[cfg\(test\)\]\s*mod\s+\w+\s*\{"))
        text = strip_balanced(text, re.compile(r"#\[cfg\(feature = \"fault-hooks\"\)\]\s*mod\s+fault\s*\{"))
    return re.sub(r"(?m)//.*$", "", text)


def python_production(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] if not re.search(r"[\"'][^\"']*#", line) else line for line in text.split("\n"))


class ScopeError(RuntimeError):
    """The scope cannot be derived with certainty: the check, F0, F1, `verify_freeze` and the decider all refuse (no F0, no outcome)."""


def scope(root: Path) -> dict[str, list[str]]:
    """The exact scope list, derived from the tree (sorted, repository-relative). Fails closed on a symlink anywhere under the Rust source directory (the directory itself, a file or a directory
    below it, dangling or not): `rglob` does not enter a symlinked directory while rustc follows it (`mod linked;`), so a module reachable only through one would be neither scanned nor frozen."""
    rust_root = root / RUST_DIR
    links = ([RUST_DIR.as_posix()] if rust_root.is_symlink() else []) + sorted(p.relative_to(root).as_posix() for p in rust_root.rglob("*") if p.is_symlink())
    if links:
        raise ScopeError(f"symlink under the Rust source directory (the locality scope cannot be derived with certainty): {', '.join(links)}")
    exclude = set(RUST_EXCLUDE if EXCLUDE_DECLARATION_FILES else FROZEN_RUST_EXCLUDE)       # PROPOSED (2)
    if STRIP_GATED_MODULES:
        exclude |= gated_module_files(rust_root)          # PROPOSED (1): a module only a test or fault-hooks build compiles is not production code
    rust = sorted(str(p.relative_to(root)) for p in rust_root.rglob("*.rs") if p.is_file() and p.relative_to(rust_root).as_posix() not in exclude)
    py_names = set(PY_FIXED) | {p.name for p in (root / PY_DIR).glob("cli_runtime*.py")}
    python = sorted(str((PY_DIR / name)) for name in py_names - PY_EXCLUDE if (root / PY_DIR / name).exists())
    return {"rust": rust, "python": python}


def scope_record(root: Path) -> dict:
    """What F0 freezes of the scope (E2 v13 section 4: "the locality check script, scope and allowlist"): every file the check scans, as a sorted list of repository-relative paths with the SHA-256
    of the bytes on disk (tracked or untracked alike), and the digest of that list. The scanned files are the Rust and Python scope and the files of `FORMER_SITES`. A file that is renamed out of
    scope, or added to it, changes the path set; an edited one changes its digest; so neither can change what the check sees without changing this record."""
    root = Path(root)
    sc = scope(root)
    paths = set(sc["rust"]) | set(sc["python"]) | {site["file"] for site in FORMER_SITES.values() if (root / site["file"]).is_file()}
    files = [{"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()} for path in sorted(paths)]
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}


def enclosing(lines: list[str], index: int, rust: bool) -> str:
    pattern = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|const|static)\s+(\w+)" if rust else r"^\s*(?:async\s+)?def\s+(\w+)|^([A-Z_][A-Z0-9_]*)\s*=")
    for i in range(index, -1, -1):
        match = pattern.match(lines[i])
        if match:
            return next(g for g in match.groups() if g)
    return "<module>"


def scan_text(path: str, text: str, rust: bool) -> list[dict]:
    rules = RUST_RULES if rust else PY_RULES
    code = rust_production(text) if rust else python_production(text)
    lines = code.split("\n")
    findings = []
    for index, line in enumerate(lines):
        if rust and re.match(r"\s*(pub\s+)?const\s", line):
            continue   # a named bound's own definition is the named source, not a use
        if not rust and re.match(r"^[A-Z_][A-Z0-9_]*\s*=\s*[0-9.]+\s*$", line):
            continue
        for category, pattern in rules.items():
            if pattern.search(line):
                findings.append({"file": path, "line": index + 1, "category": category, "text": line.strip(), "symbol": enclosing(lines, index, rust)})
    return findings


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _verdicts_problem(entry: dict, where: str) -> None:
    verdicts = entry["verdicts"]
    if not (isinstance(verdicts, list) and len(verdicts) == 2 and all(isinstance(v, dict) and set(v) == VERDICT_KEYS and _nonempty(v["reviewer"]) and _nonempty(v["session"])
                                                                     and isinstance(v["report"], dict) and set(v["report"]) == {"path", "sha256"} and _nonempty(v["report"]["path"])
                                                                     and isinstance(v["report"]["sha256"], str) and HEX64.match(v["report"]["sha256"]) for v in verdicts)):
        raise ValueError(f"allowlist entry at {where} needs exactly two well-formed verdict references")
    for key, values in (("reviewer", [v["reviewer"] for v in verdicts]), ("session", [v["session"] for v in verdicts]), ("report hash", [v["report"]["sha256"] for v in verdicts])):
        if len(set(values)) != 2:
            raise ValueError(f"allowlist entry at {where}: the two verdicts share a {key}")
    if any(v["classification"] != entry["classification"] for v in verdicts):
        raise ValueError(f"allowlist entry at {where}: a verdict does not record the entry's classification")


def validate_allowlist(data: object) -> None:
    """Structure of an allowlist under V6; raises ValueError. Checks (b) as far as this module can: two verdict references, distinct reviewer / session / report hash, agreeing with the entry.
    PROPOSED (3): an entry names a file of the Python scope (the frozen keys, with `referenceLine`) or of the Rust scope (`RUST_ENTRY_KEYS`, with the Rust classifications)."""
    if not isinstance(data, dict) or data.get("schema") != ALLOWLIST_SCHEMA or set(data) != {"schema", "entries"} or not isinstance(data["entries"], list):
        raise ValueError("allowlist has the wrong schema")
    seen_loaded, seen_reference = set(), set()
    for entry in data["entries"]:
        rust = isinstance(entry, dict) and isinstance(entry.get("file"), str) and entry["file"].startswith(RUST_DIR.as_posix() + "/") and entry["file"].endswith(".rs")
        keys, classifications, numbers = (RUST_ENTRY_KEYS, RUST_CLASSIFICATIONS, ("loadedLine",)) if rust else (ENTRY_KEYS, CLASSIFICATIONS, ("referenceLine", "loadedLine"))
        if not (isinstance(entry, dict) and set(entry) == keys and all(_nonempty(entry[k]) for k in ("file", "category", "symbol", "text", "classification", "reason"))
                and entry["category"] in CATEGORIES and entry["classification"] in classifications
                and all(type(entry[k]) is int and entry[k] >= 1 for k in numbers)):
            raise ValueError(f"malformed allowlist entry {entry!r}")
        if not rust and not (entry["file"].startswith(PY_DIR.as_posix() + "/") and entry["file"].endswith(".py")):
            raise ValueError(f"allowlist entry {entry['file']!r}: only a file of the Python scope or of the Rust scope admits findings")
        _verdicts_problem(entry, f"{entry['file']}:{entry['loadedLine']}")
        if (entry["file"], entry["loadedLine"]) in seen_loaded or (not rust and (entry["file"], entry["referenceLine"]) in seen_reference):
            raise ValueError(f"allowlist entry at {entry['file']}:{entry['loadedLine']}: one entry admits exactly one occurrence")
        seen_loaded.add((entry["file"], entry["loadedLine"]))
        if not rust:
            seen_reference.add((entry["file"], entry["referenceLine"]))


def load_allowlist(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    data = json.loads(raw)
    validate_allowlist(data)
    return data, hashlib.sha256(raw).hexdigest()


def line_mapping(reference_text: str, loaded_text: str) -> dict[int, int]:
    """The frozen algorithm of V6 (a): loaded line number -> reference line number for the lines that are `equal` in a line-level `SequenceMatcher(None, ..., autojunk=False)` diff.
    A loaded line that is not in an equal block (inserted or modified) has no entry."""
    reference_lines, loaded_lines = reference_text.split("\n"), loaded_text.split("\n")
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, _j2 in difflib.SequenceMatcher(None, reference_lines, loaded_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                mapping[j1 + offset + 1] = i1 + offset + 1
    return mapping


def admitting_entry(finding: dict, entries: list[dict], used: set[int], reference_text: str | None, loaded_text: str) -> int | None:
    """Index of the entry that admits `finding` occurrence-exactly, or None. Requires: the entry names this file, category, symbol, text and loaded line; the loaded line is an
    unchanged line of the reference diff and maps to the entry's reference line; the reference has the same finding (same category, text and enclosing symbol) on that line."""
    if reference_text is None:
        return None                                   # the file is absent from the baseline reference (added by E3 or after U): all of it is evaluation code
    mapping = line_mapping(reference_text, loaded_text)
    reference_line = mapping.get(finding["line"])
    if reference_line is None:
        return None
    reference_findings = {(f["line"], f["category"]): f for f in scan_text(finding["file"], reference_text, False)}
    in_reference = reference_findings.get((reference_line, finding["category"]))
    if in_reference is None or (in_reference["text"], in_reference["symbol"]) != (finding["text"], finding["symbol"]):
        return None
    for index, entry in enumerate(entries):
        if index in used:
            continue
        if ((entry["file"], entry["category"], entry["symbol"], entry["text"], entry["loadedLine"], entry["referenceLine"])
                == (finding["file"], finding["category"], finding["symbol"], finding["text"], finding["line"], reference_line)):
            return index
    return None


def admitting_rust_entry(finding: dict, entries: list[dict], used: set[int]) -> int | None:
    """PROPOSED (3): the index of the entry that admits a Rust `finding`, or None. The Rust runtime is the candidate path and has no baseline reference, so there is no diff to map through: the entry
    names this file, category, symbol, text and the line the scan reports, and admits exactly that occurrence (a second copy of the same text on another line is a finding)."""
    for index, entry in enumerate(entries):
        if index not in used and ((entry["file"], entry["category"], entry["symbol"], entry["text"], entry.get("loadedLine"))
                                  == (finding["file"], finding["category"], finding["symbol"], finding["text"], finding["line"])) and "referenceLine" not in entry:
            return index
    return None


def check(root: Path, allowlist: dict, reference: dict[str, str | None] | None = None) -> dict:
    """`reference` maps a Python-scope path to its baseline reference content (V5 item 3; None = absent). Without it nothing can be admitted in the Python scope."""
    validate_allowlist(allowlist)
    root = Path(root)
    reference = reference or {}
    sc = scope(root)
    findings: list[dict] = []
    texts: dict[str, str] = {}
    for relative in sc["rust"]:
        findings += scan_text(relative, (root / relative).read_text(), True)
    for relative in sc["python"]:
        texts[relative] = (root / relative).read_text()
        findings += scan_text(relative, texts[relative], False)
    used: set[int] = set()
    admitted = []
    remaining = []
    for finding in findings:
        if finding["file"] in texts:
            match = admitting_entry(finding, allowlist["entries"], used, reference.get(finding["file"]), texts[finding["file"]])
        else:
            match = admitting_rust_entry(finding, allowlist["entries"], used)
        if match is None:
            remaining.append(finding)
        else:
            used.add(match)
            admitted.append({**finding, **({"referenceLine": allowlist["entries"][match]["referenceLine"]} if "referenceLine" in allowlist["entries"][match] else {}),
                             "classification": allowlist["entries"][match]["classification"]})
    stale = [allowlist["entries"][i] for i in range(len(allowlist["entries"])) if i not in used]
    former = {}
    for name, site in FORMER_SITES.items():
        file = root / site["file"]
        hits = len(re.findall(site["before"], rust_production(file.read_text()) if file.suffix == ".rs" else python_production(file.read_text()))) if file.exists() else None
        former[name] = {"file": site["file"], "beforePattern": site["before"], "afterOccurrences": hits, "inScope": site["file"] in sc["rust"] + sc["python"]}
    violation = bool(remaining or stale or any(v["afterOccurrences"] for v in former.values()) or any(v["afterOccurrences"] is None for v in former.values()))
    return {"violation": violation, "scope": sc, "findings": remaining, "admitted": admitted, "staleAllowlistEntries": stale, "formerSites": former, "allowlistEntriesUsed": len(used)}


def findings_lines(result: dict) -> list[str]:
    """The findings of a `check` result as the sorted lines `locality.json` records (one function for the producer and for the decider's re-run)."""
    return sorted({f"{f['file']}:{f['line']} {f['category']} {f['text']}" for f in result["findings"]}
                  | {f"stale allowlist entry {e['file']} {e['category']} {e['text']}" for e in result["staleAllowlistEntries"]}
                  | {f"former site reappeared: {name}" for name, site in result["formerSites"].items() if site["afterOccurrences"] != 0})


def scope_text(record: dict) -> str:
    """`locality/scope.txt`: a `scope_record` rendered one file per line (`<sha256>  <path>`) and closed by the digest of the list. The record names its scope (paths, bytes and digest) through the
    hash reference to this file, so the outcome schema needs no new key; the decider compares the file with the rendering of the scope F0 froze."""
    return "".join(f"{f['sha256']}  {f['path']}\n" for f in record["files"]) + f"scope-sha256 {record['sha256']}\n"


def reference_texts(root: Path, manifest: list[dict]) -> dict[str, str | None]:
    """Reference content (V5 item 3) of every manifest file, read from the revision each entry names and verified against its digest."""
    from tests.cli_regression.support.g1e import baseline_identity as bi
    out: dict[str, str | None] = {}
    for entry in manifest:
        data = bi.reference_content(Path(root), entry)
        out[entry["path"]] = None if data is None else data.decode("utf-8")
    return out


def reference_for_tree(root: Path, python: str | None = None) -> tuple[dict[str, str | None], list[dict]]:
    """Diagnostics: trace the Python arm of `root`, derive E3set and the manifest (V5), return (reference texts, manifest). The freeze pins the manifest at F0 instead."""
    from tests.cli_regression.support.g1e import baseline_identity as bi
    traced = bi.trace_python_arm(Path(root), python)
    manifest = bi.reference_manifest(Path(root), traced, bi.e3_set(Path(root), traced))
    return reference_texts(root, manifest), manifest


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path.cwd()
    allowlist_path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("locality-allowlist.json")
    allowlist, digest = load_allowlist(allowlist_path)
    texts, _manifest = reference_for_tree(root.resolve())
    result = check(root, allowlist, texts)
    result["allowlistSha256"] = digest
    print(json.dumps(result, indent=1, sort_keys=True))
    return 1 if result["violation"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
