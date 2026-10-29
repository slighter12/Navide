"""The hosted correctness precondition, on every platform, on the F0 sidecar (rf4c D8, rf4d E1, rf4e G1-G3, rf4f H1-H4).

rf4f: the reader cases run on fixtures the RUNNER builds (`stage_reader_descriptor`: the descriptor of the `reader-fixtures` pre step, verified against the registered digests, written over the packed one) instead of
being deselected; only a case whose fixture the platform does not register (Linux: the two opencode cases) stays out, by a rootdir-relative `--deselect` id (`tests/<file>::<case>`) that a real
`pytest --collect-only` proves removes something (H1, H2). The gate expects every variant of a parametrized case that real collection produced, and every iteration of a literal vitest loop (H3); a
required case that did not pass, whatever its name, fails its file and its rows, and `partly-runnable` never holds one (H4).

The frozen rule (`g3-prereg-proposal.md:134`) is: the 21 G1-E correctness rows plus the G2 required cases pass at the same H in the same session. `g1e/gates.py` `REGISTRY` is the executable form of the
21 rows (row id -> the pytest files, the vitest files and the cargo run that prove it). This module runs that registry on the runner:

* a **row table** (`correctness-gate.json`, key `rows`) names, for each row on this platform, its commands, and for each command the cases it requires. The registry maps a row to FILES, not to cases, so a
  row requires every case of each of its files. A case the source says cannot run on the platform (a `skipif`, a `runIf`, a decidable `pytest.skip`, a fixture the checkout does not hold) is listed in
  the table with that reason; it is never proved by another case. A row is `passed` only when every command ran and every one of its cases was COLLECTED and PASSED by name; `partly-runnable` when the
  cases that can run all passed and at least one command or case is listed as not runnable; `failed` when a required case is absent, skipped, failed or errored (rf4e G1, reviewer B's B1); and
  `not-runnable` when nothing of it can run here;
* the Darwin plan adds the frozen macOS G2 part, the 17 files of `driver.CORRECTNESS_FILES`, as the row `G2_ROW`, through the same per-case gate (rf4e G3);
* the run writes `correctness-receipt.json` with the sidecar path and its SHA-256 (hashed here from the file), which `receipt_problems` compares with F0's sidecar before the cost run starts;
* Linux and Windows also run the G2 required cases from the workflow (`tests/cli/g2Native.mjs guard`, by case id); `bind_g2` re-checks those ids by name in the outcomes log and binds the receipts
  output to the same sidecar (`g2-receipt-binding.json`).

The static platform scan (`scan_pytest`, `scan_vitest`) reads the test sources; it never imports or runs them. A condition it cannot evaluate counts as false (the product and harness binaries are
assumed built), so a skip it did not predict is a gate problem, never a silent pass. A skip condition that reads a file (`X.is_file()`) is decided against the runner's tree (`RunnerTree`: the freeze
head's tracked files plus the pack), never against the local disk (rf4e G2).

    hosted_correctness.py run REPO PRE_OUT SYSTEM     the registry on the F0 sidecar, then the gate, the row table and the receipt; exit 0 only when every run and the gate pass
    hosted_correctness.py rows REPO SYSTEM            print the row table of the plan (no run)
    hosted_correctness.py bind REPO PRE_OUT SYSTEM RECEIPTS_OUT OUTCOMES JUNIT REQUIRED      bind the G2 receipts and required cases to the F0 sidecar

Standard library, `g1e.gates` and `g1e.reference` only (`local_runner_tree` also uses `hosted_pack`); nothing is measured.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g1e import gates, reference

SCHEMA = "acr-g3h-correctness-gate/3"
RECEIPT_SCHEMA = "acr-g3h-correctness-receipt/1"
G2_SCHEMA = "acr-g3h-g2-binding/1"
GATE_NAME = "correctness-gate.json"
JUNIT_NAME = "correctness-junit.xml"
VITEST_NAME = "correctness-vitest.json"
RECEIPT_NAME = "correctness-receipt.json"
G2_BINDING_NAME = "g2-receipt-binding.json"
PLATFORMS = {"Linux": "linux", "Windows": "win32", "Darwin": "darwin"}
POSIX_ONLY_MODULES = ("fcntl", "pty", "termios", "resource", "tty", "grp", "pwd")      # not importable on Windows: a top-level import is a collection error there
POSIX_OS_ATTRIBUTES = ("tcgetpgrp", "fork", "getpgid", "getpgrp", "setsid", "killpg")      # absent from `os` on Windows: `hasattr(os, name)` is false there
ROWS_WITHOUT_READER = ("Windows",)      # no reader rows on Windows (`hosted_reader.has_readers`), so `gates.NOT_REQUIRED_WITHOUT_READER` is not required there
G2_PLATFORMS = ("Linux", "Windows")      # the systems whose G2 required list runs in the workflow
G2_ROW = "G2-macos-frozen-17-files"      # Darwin only: the frozen macOS G2 part (rf4e G3), gated like a registry row
# The 17 files of `driver.CORRECTNESS_FILES` (the reconstruction of the mac-final-eddddb7-r3 correctness run, manager answer 3); `test_g3h_correctness.py` pins this tuple to the driver's list.
MACOS_G2_FILES = ("test_cli_runtime_composition", "test_cli_runtime_composition_vendors", "test_cli_runtime_observe_vendors", "test_cli_runtime_observe_cost", "test_cli_runtime_recovery",
                  "test_cli_runtime_lifecycle", "test_cli_runtime_handoff", "test_cli_runtime_sidecar", "test_cli_runtime_managed_login", "test_cli_runtime_vendor_transport",
                  "test_cli_runtime_control_failures", "test_cli_runtime_observe", "test_cli_runtime_claude", "test_cli_runtime_claude_parity", "test_cli_runtime_observe_codex",
                  "test_cli_create_meta_size", "test_cli_runtime_credential_env_remove")
STAGED_DESCRIPTOR = ".scratch/agent-cli-runtime-evaluation/evidence/e3/reader-fixtures.json"      # the evidence descriptor of the reader cases; the pack carries a placeholder copy (no machine path, rf4g J1), the runner stages its own over it (rf4f H1)
READER_NAMES = ("claude", "opencode")
ROOTDIR_TESTS = "tests"      # pytest's rootdir is `backend/` (`backend/pyproject.toml` holds `[tool.pytest.ini_options]`), so a node id is `tests/<file>::<case>`; `--deselect` matches `nodeid.startswith(prefix)` and normalises nothing (rf4f H2)
CLIENT = ("bunx", "--bun")
IDS = tuple(reference.CORRECTNESS_IDS)
PRINCIPAL_REASON = "requires the ticket-04 principal controller (NAVIDE_T04_WRITER_BINDING_DIR), which no hosted runner has; the test file skips without it (g1e gates.NOTES)"
ABSOLUTE_PATH = re.compile(r"(/|[A-Za-z]:[\\/])\S")


def row_ids(system: str) -> tuple[str, ...]:
    """The rows the receipt must cover on `system`: the 21 registry rows, and on Darwin the frozen G2 part."""
    return IDS + ((G2_ROW,) if system == "Darwin" else ())


def sidecar_rel(system: str) -> str:
    return "native/navide-cli-runtime/target/release/navide-cli-runtime" + (".exe" if system == "Windows" else "")


def python_rel(system: str) -> str:
    return "backend/.venv/Scripts/python.exe" if system == "Windows" else "backend/.venv/bin/python"


def harness_path(pre_out: str, system: str) -> str:
    return f"{pre_out}/harness-target/release/navide-cli-runtime" + (".exe" if system == "Windows" else "")


def basetemp(system: str) -> str:
    """Short on purpose (a long basetemp overflowed a path-derived name in an earlier run); pytest removes an existing directory itself."""
    return "/private/tmp/g3r6-ct" if system == "Darwin" else str(Path(tempfile.gettempdir()) / "g3r6-ct")


# ---------------------------------------------------------------------------------------------------------------------------- the runner's tree
class RunnerTree:
    """The files a skip condition may find on a runner. `paths=None` is the checkout on disk (what the runner itself sees, so the plan the runner computes is the truth); a set of repo-relative paths
    is the runner's tree computed away from the runner (`local_runner_tree`), so the local disk never decides a skip (rf4e G2)."""

    def __init__(self, root: str | Path, paths=None):
        self.root = Path(root)
        self.paths = None if paths is None else frozenset(paths)

    def has(self, rel: str) -> bool:
        return (self.root / rel).is_file() if self.paths is None else rel in self.paths

    def read(self, rel: str) -> bytes | None:
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return None

    def holds_absolute(self, value: str) -> bool:
        """Does an absolute path named inside a descriptor file exist in this tree? On disk: ask the disk (a path of another machine is absent). Computed: it must lie under the root and be listed."""
        if self.paths is None:
            return Path(value).is_file()
        try:
            return Path(value).relative_to(self.root).as_posix() in self.paths
        except ValueError:
            return False


def local_runner_tree(root: str | Path, ref: str = "HEAD") -> RunnerTree:
    """The tree of a runner checkout of `ref` plus the pack: `git ls-tree` of the freeze head and the pack's file list. Untracked and ignored files that are not in the pack are not in it."""
    from tests.cli_regression.support.g3 import hosted_pack
    listed = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", ref], capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    return RunnerTree(root, {path for path in listed if path} | set(hosted_pack.checked_files(root)))


# ---------------------------------------------------------------------------------------------------------------------------- the static platform scan
class _Ctx:
    """What the evaluation of a skip condition needs: the names bound to paths in scope, the file's own path, the runner tree, and the paths it looked at."""

    def __init__(self, tree: RunnerTree, file_parts: tuple[str, ...], env: dict, refs: list, staged: tuple[str, ...] = ()):
        self.tree, self.file_parts, self.env, self.refs, self.staged = tree, file_parts, env, refs, staged      # staged: the reader fixtures the runner builds and stages for this platform


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _constants(node: ast.AST) -> list | None:
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def _path_parts(node: ast.AST, env: dict, file_parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """The repo-relative path an expression names, as parts: `Path(__file__)` with `.resolve()`, `.parent`, `.parents[N]` and `/ "x/y"`, and the names bound to such expressions. None when it is anything else."""
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Call):
        if _dotted(node.func) == "Path" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__":
            return file_parts
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("resolve", "absolute") and not node.args:
            return _path_parts(node.func.value, env, file_parts)
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _path_parts(node.value, env, file_parts)
        return base[:-1] if base else None
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
        base = _path_parts(node.value.value, env, file_parts)
        depth = node.slice.value + 1
        return base[:len(base) - depth] if base is not None and 0 < depth <= len(base) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
        left = _path_parts(node.left, env, file_parts)
        return None if left is None else left + tuple(part for part in node.right.value.split("/") if part)
    return None


def _bind_paths(body: list[ast.stmt], env: dict, file_parts: tuple[str, ...]) -> dict:
    """`env` plus every `NAME = <path expression>` in `body`, in source order (nested statements included)."""
    bound = dict(env)
    assigns = sorted((n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)), key=lambda n: n.lineno)
    for stmt in assigns:
        parts = _path_parts(stmt.value, bound, file_parts)
        if parts is not None:
            bound[stmt.targets[0].id] = parts
    return bound


def _is_file_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "is_file" and not node.args


def _is_file(node: ast.Call, ctx: _Ctx | None) -> bool:
    """`X.is_file()`: a path the scan can resolve is looked up in the runner's tree (and recorded, so a test can require it to be there); any other path is a built binary, assumed present."""
    if ctx is None:
        return True
    parts = _path_parts(node.func.value, ctx.env, ctx.file_parts)
    if parts is None:
        return True
    rel = "/".join(parts)
    present = ctx.tree.has(rel)
    ctx.refs.append({"path": rel, "line": node.lineno, "present": present})
    return present


def _holds(node: ast.AST, platform: str, ctx: _Ctx | None = None) -> bool:
    """Does a `skipif` condition hold on `platform` ('linux', 'win32' or 'darwin')? `sys.platform` and `os.name` comparisons are evaluated; `X.is_file()` is decided against the runner's tree when `ctx`
    resolves the path (a built binary, whose path is not resolvable here, counts as present); every other term is false, so an unpredicted skip surfaces as a gate problem."""
    if isinstance(node, ast.BoolOp):
        results = [_holds(value, platform, ctx) for value in node.values]
        return any(results) if isinstance(node.op, ast.Or) else all(results)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _holds(node.operand, platform, ctx)
    if _is_file_call(node):
        return _is_file(node, ctx)
    if isinstance(node, ast.Call) and _dotted(node.func) == "hasattr" and len(node.args) == 2 and _dotted(node.args[0]) == "os" and isinstance(node.args[1], ast.Constant):
        return platform != "win32" or node.args[1].value not in POSIX_OS_ATTRIBUTES
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        subject = {"sys.platform": platform, "os.name": "nt" if platform == "win32" else "posix"}.get(_dotted(node.left))
        values = _constants(node.comparators[0])
        if subject is not None and values is not None:
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                return subject == values[0]
            if isinstance(op, ast.NotEq):
                return subject != values[0]
            if isinstance(op, ast.In):
                return subject in values
            if isinstance(op, ast.NotIn):
                return subject not in values
    return False


def _fixtures_needed(node: ast.AST) -> set[str]:
    """The reader fixtures a test function opens through the descriptor: the names `claude` / `opencode` it mentions, or all of them when it loops over `rb.FIXTURES`, builds both with `rf.build` or is
    parametrized over `SINGLE_PASS` (every fixture), or when it names none (the conservative answer)."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value in READER_NAMES:
            found.add(sub.value)
        elif (isinstance(sub, ast.Attribute) and _dotted(sub) in ("rb.FIXTURES", "rf.build")) or (isinstance(sub, ast.Name) and sub.id == "SINGLE_PASS"):
            return set(READER_NAMES)
    return found or set(READER_NAMES)


def _descriptor_problem(condition: ast.AST, ctx: _Ctx | None, needs: set[str] | None = None) -> str | None:
    """A case whose gate reads a descriptor file (`X.is_file()` on a `.json` the tree holds) can only run when every absolute path the descriptor names is in the tree too. The evidence descriptor
    `reader-fixtures.json` names the fixture files by absolute path of the machine that built them; a runner holds neither those paths nor the 4.7 MB of fixtures, so the case would fail, not skip.
    For `STAGED_DESCRIPTOR` the runner builds the fixtures from the frozen generators, verifies them against the registered digests and stages its own descriptor over the packed one (rf4f H1), so a case
    that needs only fixtures the platform registers (`ctx.staged`) is runnable; one that needs another fixture is not, with that reason."""
    if ctx is None:
        return None
    for sub in ast.walk(condition):
        if not _is_file_call(sub):
            continue
        parts = _path_parts(sub.func.value, ctx.env, ctx.file_parts)
        rel = "/".join(parts) if parts else ""
        if not rel.endswith(".json") or not ctx.tree.has(rel):
            continue
        if rel == STAGED_DESCRIPTOR:
            missing = sorted((needs or set(READER_NAMES)) - set(ctx.staged))
            if not missing:
                continue
            return (f"needs the {', '.join(missing)} reader fixture(s), which this platform does not register (hosted_reader.READERS: {', '.join(ctx.staged) or 'none'}); the runner cannot build and "
                    f"digest-verify {'it' if len(missing) == 1 else 'them'} here (the opencode database header, bytes 96-99, records the SQLite version of the host that wrote it)")
        raw = ctx.tree.read(rel)
        try:
            document = json.loads(raw) if raw is not None else None
        except ValueError:
            continue
        stack = [document]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and ABSOLUTE_PATH.match(item) and not ctx.tree.holds_absolute(item):
                return f"reads fixture files that {rel} names by absolute path of another machine (.../{'/'.join(Path(item.replace(chr(92), '/')).parts[-2:])}); the checkout does not hold them"
    return None


def _skipifs(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "skipif" and sub.args:
            reason = next((kw.value.value for kw in sub.keywords if kw.arg == "reason" and isinstance(kw.value, ast.Constant)), "")
            yield sub.args[0], str(reason), sub.lineno


def _top_level_posix_import(path: Path, tests_root: Path, seen: set[Path]) -> str | None:
    """The first POSIX-only module imported at the top level of `path` or, through `tests.*` imports, of a test-support module it pulls in; a guarded import (try/if) does not count."""
    if path in seen or not path.is_file():
        return None
    seen.add(path)
    for stmt in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name.split(".")[0] in POSIX_ONLY_MODULES:
                    return f"imports {alias.name} (POSIX only) at {path.name}:{stmt.lineno}"
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            if stmt.module.split(".")[0] in POSIX_ONLY_MODULES:
                return f"imports {stmt.module} (POSIX only) at {path.name}:{stmt.lineno}"
            if stmt.module.split(".")[0] == "tests":
                for candidate in [stmt.module] + [f"{stmt.module}.{alias.name}" for alias in stmt.names]:
                    found = _top_level_posix_import(tests_root.parent / (candidate.replace(".", "/") + ".py"), tests_root, seen)
                    if found:
                        return found
    return None


def scan_pytest(path: Path, platform: str, tests_root: Path, tree: RunnerTree | None = None, staged: tuple[str, ...] = ()) -> dict:
    """{runnable, reason, skips, cases, refs}. The whole module is not runnable on `platform` when a module-level `skipif` holds, `pytest.importorskip` names a POSIX-only module, or a top-level import
    (of the file or of a test-support module it imports) is POSIX-only on Windows. `cases` lists every test function as {class, case, line, run, reason, deselect}: `run` is false when a case-level `skipif`,
    a class-level `skipif` or a decidable `if <condition>: pytest.skip(...)` holds, or (`deselect`) when the case reads a descriptor naming files the tree does not hold. `skips` is the not-runnable
    subset as {class, case, reason, line} (a class-level one has case None): those skips are expected, any other is a problem. `refs` lists the paths the skip conditions looked up in `tree`."""
    text = Path(path).read_text(encoding="utf-8")
    module = ast.parse(text)
    file_parts = tuple(Path(path).relative_to(Path(tests_root).parent.parent).parts) if tree is not None else ()      # the checkout is two levels above `backend/tests`
    refs: list[dict] = []
    module_env = _bind_paths(module.body, {}, file_parts) if tree is not None else {}
    ctx = _Ctx(tree, file_parts, module_env, refs, staged) if tree is not None else None
    marks: dict[str, tuple[ast.AST, str, int]] = {}
    reason = None
    for stmt in module.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            for cond, why, line in _skipifs(stmt.value):
                if reason is None and _holds(cond, platform, ctx):
                    reason = f"module skipif at {Path(path).name}:{line}: {why or ast.unparse(cond)}"
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            for cond, why, line in _skipifs(stmt.value):
                marks[stmt.targets[0].id] = (cond, why, line)
        if isinstance(stmt, (ast.Assign, ast.Expr)):
            for sub in ast.walk(stmt.value):
                if isinstance(sub, ast.Call) and _dotted(sub.func) == "pytest.importorskip" and sub.args and isinstance(sub.args[0], ast.Constant):
                    if platform == "win32" and str(sub.args[0].value) in POSIX_ONLY_MODULES and reason is None:
                        reason = f"pytest.importorskip({sub.args[0].value!r}) at {Path(path).name}:{sub.lineno}: POSIX only"
    if reason is None and platform == "win32":
        found = _top_level_posix_import(Path(path), tests_root, set())
        reason = f"collection error on Windows: {found}" if found else None
    skips: list[dict] = []
    cases: list[dict] = []

    def decorators(node, cls):
        for decorator in node.decorator_list:
            found = list(_skipifs(decorator))
            if isinstance(decorator, ast.Name) and decorator.id in marks:
                found.append(marks[decorator.id])
            for cond, why, line in found:
                case = None if isinstance(node, ast.ClassDef) else node.name
                if _holds(cond, platform, ctx):
                    skips.append({"class": cls, "case": case, "reason": why or ast.unparse(cond), "line": line})
                elif case is not None and (problem := _descriptor_problem(cond, ctx, _fixtures_needed(node))):
                    skips.append({"class": cls, "case": case, "reason": problem, "line": line, "deselect": True})

    def visit(body, cls):
        for node in body:
            if isinstance(node, ast.ClassDef):
                decorators(node, node.name)
                visit(node.body, node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                decorators(node, cls)
                cases.append({"class": cls, "case": node.name, "line": node.lineno})
                local = _Ctx(tree, file_parts, _bind_paths(node.body, module_env, file_parts), refs, staged) if tree is not None else None
                for sub in ast.walk(node):      # `if <condition>: pytest.skip(...)` in the body, when the condition is decidable on this platform
                    if not isinstance(sub, ast.If):
                        continue
                    for inner in sub.body:
                        if isinstance(inner, ast.Expr) and isinstance(inner.value, ast.Call) and _dotted(inner.value.func) == "pytest.skip":
                            why = f"pytest.skip at {Path(path).name}:{inner.lineno}: {ast.unparse(sub.test)}"
                            if _holds(sub.test, platform, local):
                                skips.append({"class": cls, "case": node.name, "reason": why, "line": inner.lineno})
                            elif problem := _descriptor_problem(sub.test, local, _fixtures_needed(node)):
                                skips.append({"class": cls, "case": node.name, "reason": problem, "line": inner.lineno, "deselect": True})
    visit(module.body, None)
    if reason:
        return {"runnable": False, "reason": reason, "skips": [], "cases": [], "refs": refs}
    for case in cases:
        mine = [s for s in skips if s["class"] == case["class"] and s["case"] in (None, case["case"])]
        hit = next((s for s in mine if not s.get("deselect")), mine[0] if mine else None)      # a platform skip explains the case without a deselect; the deselect is for a case that would otherwise run
        case["run"], case["reason"], case["deselect"] = hit is None, hit["reason"] if hit else None, bool(hit and hit.get("deselect"))
    return {"runnable": True, "reason": None, "skips": skips, "cases": cases, "refs": refs}


TS_DECLARATION = re.compile(r"^(?P<indent>\s*)(?P<kind>it|test|describe)(?:\.runIf\((?P<cond>.*?)\))?\(\s*(?P<q>['\"`])(?P<title>.*?)(?P=q)")
TS_TERM = re.compile(r"process\.platform\s*(?P<op>===|!==)\s*'(?P<value>\w+)'")


def _ts_holds(cond: str, platform: str) -> bool:
    """A vitest `runIf` condition on `platform`: `||` of `process.platform ===/!== 'x'` terms. The `controllerBound` constant (ticket-04 controller) is false on a hosted runner. Anything else is an error: the
    scan refuses to guess."""
    if cond.strip() == "controllerBound":
        return False
    value = False
    for term in cond.split("||"):
        match = TS_TERM.fullmatch(term.strip())
        if not match:
            raise ValueError(f"vitest condition not scannable: {cond}")
        value = value or ((match["value"] == platform) == (match["op"] == "==="))
    return value


TS_LOOP = re.compile(r"^(?P<indent>\s*)for \(const (?P<names>\w+|\[[\w, ]+\]) of (?P<values>\[.*\])(?: as const)?\) \{\s*$")
PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def _loop_bindings(names: str, values: str) -> list[dict] | None:
    """The variable bindings of one iteration each for a single-line `for (const x of [literals])` or `for (const [a, b] of [[..], ..])`; None when the iterable is not a literal the scan can read."""
    try:
        items = ast.literal_eval(values)
    except (ValueError, SyntaxError):
        return None
    variables = [n.strip() for n in names.strip("[]").split(",")] if names.startswith("[") else [names]
    out = []
    for item in items:
        row = list(item) if names.startswith("[") and isinstance(item, (list, tuple)) else [item]
        if len(row) < len(variables):
            return None
        out.append(dict(zip(variables, row)))
    return out


def _variants(title: str, loops: list) -> dict:
    """{"variants": [full name per iteration]} for a case whose title holds `${x}` placeholders that the innermost enclosing literal loop binds: the exact names the run must produce (rf4f H3). Empty otherwise."""
    wanted = set(PLACEHOLDER.findall(title))
    if not wanted:
        return {}
    for _indent, bindings in reversed(loops):
        if bindings and all(wanted <= set(row) for row in bindings):
            return {"variants": [PLACEHOLDER.sub(lambda m, row=row: str(row[m.group(1)]), title) for row in bindings]}
    return {}


def _title_pattern(title: str) -> str:
    return ".+".join(re.escape(part) for part in re.split(r"\$\{.*?\}", title))


def scan_vitest(path: Path, platform: str) -> dict:
    """{runnable, reason, skips, cases}. `cases` lists every `it`/`test` as {case, pattern, line, run, reason}: `pattern` is the regular expression its full name (the enclosing `describe` titles and its own,
    joined by a space) must match completely, with a `${...}` placeholder as `.+`. A `runIf` guard of the case or of an enclosing `describe` that is false on `platform` makes it not runnable. A file with no
    runnable case is not runnable."""
    cases, stack, loops = [], [], []      # stack: (indent, title pattern, guard condition or None, line); loops: (indent, [(variable names, value)]) of the open `for (const x of [...])` lines
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        while stack and indent <= stack[-1][0] and stripped.startswith("}"):
            stack.pop()
        while loops and indent <= loops[-1][0] and stripped.startswith("}"):
            loops.pop()
        loop = TS_LOOP.match(line)
        if loop:
            loops.append((len(loop["indent"]), _loop_bindings(loop["names"], loop["values"])))
            continue
        match = TS_DECLARATION.match(line)
        if not match:
            continue
        pattern, guards = _title_pattern(match["title"]), [(match["kind"], match["cond"], number)] if match["cond"] else []
        if match["kind"] == "describe":
            stack.append((len(match["indent"]), pattern, guards[0] if guards else None, number))
            continue
        inherited = [(g[2][0], g[2][1], g[2][2]) for g in stack if g[2]]
        full = " ".join([g[1] for g in stack] + [pattern])
        failing = next(((kind, cond, at) for kind, cond, at in inherited + guards if not _ts_holds(cond, platform)), None)
        title = " ".join([re.sub(r"\\(.)", r"\1", g[1]) for g in stack] + [match["title"]])
        cases.append({"case": title, "pattern": full, "line": number, "run": failing is None,
                      "reason": None if failing is None else f"{failing[0]}.runIf({failing[1]}) at line {failing[2]} is false on {platform}", **_variants(title, loops)})
    skips = [{"pattern": c["pattern"], "title": c["case"], "reason": c["reason"], "line": c["line"]} for c in cases if not c["run"]]
    if not any(c["run"] for c in cases):
        first = skips[0] if skips else None
        return {"runnable": False, "reason": (f"every case of {Path(path).name} is runIf-guarded and none holds on {platform} (first: line {first['line']}, {first['reason']})" if first else "no case"),
                "skips": [], "cases": [], "refs": []}
    return {"runnable": True, "reason": None, "skips": skips, "cases": cases, "refs": []}


# ---------------------------------------------------------------------------------------------------------------------------- the row table
def commands(row: str) -> list[tuple[str, str | None]]:
    """The commands of a row as (kind, target), in the registry's order: ('cargo', None), ('pytest', 'test_x.py'), ('vitest', 'x.test.ts'). The Darwin G2 part is its 17 pytest files."""
    if row == G2_ROW:
        return [("pytest", f"{name}.py") for name in MACOS_G2_FILES]
    out: list[tuple[str, str | None]] = []
    for argv in gates.REGISTRY[row]:
        if argv[:2] == ["cargo", "test"]:
            out.append(("cargo", None))
        for arg in argv:
            if arg.startswith("backend/tests/"):
                out.append(("pytest", arg[len("backend/tests/"):]))
            elif arg.startswith("tests/cli/"):
                out.append(("vitest", arg[len("tests/cli/"):]))
    return out


def plan(system: str, root: Path | str, tree: RunnerTree | None = None) -> dict:
    """The correctness plan of `system` on the checkout at `root`: the files to run and the row table's static part. {pytest, vitest, rows, scans, deselect, system}. `tree` is the runner's tree for the
    skip conditions that read a file; the default is `root` on disk, which is right on the runner and wrong anywhere else (use `local_runner_tree`)."""
    from tests.cli_regression.support.g3 import hosted_reader
    root, platform = Path(root), PLATFORMS[system]
    tree = tree or RunnerTree(root)
    staged = tuple(hosted_reader.READERS[system])      # the reader fixtures the runner builds, digest-verifies and stages for the reader cases (rf4f H1)
    scans: dict[str, dict] = {}
    rows: list[dict] = []
    for row in row_ids(system):
        entries = []
        for kind, target in commands(row):
            if kind == "cargo":
                entries.append({"kind": kind, "target": "cargo test (workflow step 'Rust tests', fail-fast ahead of this step)", "run": "workflow-step", "reason": None, "cases": []})
                continue
            key = f"{kind}:{target}"
            if row in gates.REQUIRES_ENV:
                scan = {"runnable": False, "reason": PRINCIPAL_REASON, "skips": [], "cases": [], "refs": []}
            elif row == gates.NOT_REQUIRED_WITHOUT_READER and system in ROWS_WITHOUT_READER:
                scan = {"runnable": False, "reason": f"not required on {system}: no reader rows (gates.NOT_REQUIRED_WITHOUT_READER)", "skips": [], "cases": [], "refs": []}
            else:
                if key not in scans:
                    scans[key] = scan_pytest(root / "backend/tests" / target, platform, root / "backend/tests", tree, staged) if kind == "pytest" else scan_vitest(root / "tests/cli" / target, platform)
                scan = scans[key]
            entries.append({"kind": kind, "target": target, "run": scan["runnable"], "reason": scan["reason"], "key": key, "cases": scan["cases"]})
        rows.append({"row": row, "platform": system, "commands": entries})
    pytest_files = list(dict.fromkeys(t for r in rows for c in r["commands"] for t in [c["target"]] if c["kind"] == "pytest" and c["run"] is True))
    vitest_files = list(dict.fromkeys(t for r in rows for c in r["commands"] for t in [c["target"]] if c["kind"] == "vitest" and c["run"] is True))
    deselect = [f"{ROOTDIR_TESTS}/{name}::" + (f"{c['class']}::" if c["class"] else "") + c["case"] for name in pytest_files for c in scans[f"pytest:{name}"]["cases"] if c.get("deselect")]
    return {"pytest": pytest_files, "vitest": vitest_files, "rows": rows, "scans": scans, "deselect": deselect, "system": system, "staged": list(staged)}


# ---------------------------------------------------------------------------------------------------------------------------- results
def _blank() -> dict:
    return {"collected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "unexpectedSkipped": 0, "unexpected": [], "cases": {}}


def _case_key(case: dict) -> str:
    return (f"{case['class']}::" if case.get("class") else "") + case["case"]


def _junit_outcome(case: ElementTree.Element) -> str:
    for child in case:
        if child.tag in ("failure", "error", "skipped"):
            return "failed" if child.tag == "failure" else child.tag
    return "passed"


def _tally(counts: dict, key: str | None, bucket: str, name: str | None = None) -> None:
    """Count one result into the file's totals and, when it belongs to an enumerated case, into that case's own tally (which also keeps the names it saw, a parametrization suffix included)."""
    counts["collected"] += 1
    counts[bucket] += 1
    if key is not None:
        mine = counts["cases"].setdefault(key, {"collected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "names": set()})
        mine["collected"] += 1
        mine[bucket] += 1
        mine["names"].add(name)


def node_variant(node_id: str) -> tuple[str, str, str]:
    """(file, case key, name with its parametrization) of a pytest node id like `tests/test_x.py::TestK::test_c[a-b]`; the key is `TestK::test_c`, the name `test_c[a-b]` (what JUnit calls the case)."""
    head, bracket, tail = node_id.partition("[")
    parts = head.split("::")
    return parts[0].rsplit("/", 1)[-1], "::".join(parts[1:]), parts[-1] + bracket + tail


def expected_variants(plan_: dict, name: str, key: str) -> set[str] | None:
    """The names of every variant of a required case that REAL collection produced (`plan_["collected"]`, the node ids of `pytest --collect-only` with the run's arguments), or None when the plan holds no
    collection (a static plan). A parametrized case has one name per variant; an unparametrized one has its own name."""
    collected = plan_.get("collected")
    if collected is None:
        return None
    return {variant for node in collected for file, case, variant in [node_variant(node)] if file == name and case == key}


def collect_pytest(junit_text: str, plan_: dict) -> dict[str, dict]:
    """{file: counts}. A case belongs to a file when the file's module name is one dotted component of its `classname`, and to an enumerated case of the file by its class (the last component) and its name
    without the `[...]` of a parametrization. A skipped case is expected only when the scan predicted it."""
    root = ElementTree.fromstring(junit_text)
    counts = {name: _blank() for name in plan_["pytest"]}
    for case in root.iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        for name in plan_["pytest"]:
            module = name[:-3]
            if module not in parts:
                continue
            outcome = _junit_outcome(case)
            base = (case.get("name") or "").split("[")[0]
            scan = plan_["scans"][f"pytest:{name}"]
            listed = next((c for c in scan["cases"] if c["case"] == base and parts[-1] == (c["class"] or module)), None)
            _tally(counts[name], None if listed is None else _case_key(listed), outcome, case.get("name") or "")
            if outcome == "skipped":
                expected = any((s["case"] == base or s["case"] is None) and (s["class"] is None or s["class"] in parts) for s in scan["skips"])
                if not expected:
                    counts[name]["unexpectedSkipped"] += 1
                    counts[name]["unexpected"].append(f"{(listed and _case_key(listed)) or base}")
    return counts


def collect_vitest(text: str, plan_: dict) -> dict[str, dict]:
    data = json.loads(text)
    counts = {name: _blank() for name in plan_["vitest"]}
    for result in data.get("testResults", []):
        path = str(result.get("name", "")).replace("\\", "/")
        for name in plan_["vitest"]:
            if not path.endswith("/tests/cli/" + name):
                continue
            scan = plan_["scans"][f"vitest:{name}"]
            for case in result.get("assertionResults", []):
                status = {"pending": "skipped", "todo": "skipped", "failed": "failed"}.get(case.get("status"), case.get("status"))
                bucket = status if status in ("passed", "failed", "skipped") else "error"
                full = str(case.get("fullName", ""))
                listed = next((c for c in scan["cases"] if re.fullmatch(c["pattern"], full)), None)
                _tally(counts[name], None if listed is None else _case_key(listed), bucket, full)
                if bucket == "skipped" and (listed is None or listed["run"]):
                    counts[name]["unexpectedSkipped"] += 1
                    counts[name]["unexpected"].append(full)
    return counts


def file_status(c: dict) -> tuple[str, str | None]:
    if c["collected"] == 0:
        return "missing", "not collected"
    if c["failed"] or c["error"]:
        return "failed", f"{c['failed']} failed, {c['error']} errored"
    if c["unexpectedSkipped"]:
        return "skipped", f"{c['unexpectedSkipped']} case(s) skipped that no platform condition in the source explains: {', '.join(c['unexpected'][:5])}"
    if c["passed"] == 0:
        return "skipped", f"collected {c['collected']} case(s) and none passed"
    return "passed", None


def case_status(tally: dict | None, expected: set[str] | None = None) -> str:
    """One required case: `absent` (never collected), `failed`, `skipped`, `incomplete` or `passed`. A parametrized case passes only when EVERY variant did: with `expected`, the variant names real
    collection produced, a variant that never ran makes the case `incomplete` even when the others passed (rf4f H3)."""
    if not tally or not tally["collected"]:
        return "absent"
    if tally["failed"] or tally["error"]:
        return "failed"
    if tally["skipped"] or not tally["passed"]:
        return "skipped"
    if expected is not None and expected - set(tally.get("names", ())):
        return "incomplete"
    return "passed"


def evaluate(plan_: dict, pytest_counts: dict, vitest_counts: dict) -> tuple[list[str], list[dict]]:
    """(problems, rows). A runnable command passes only when its file is collected, has no failed, errored or unexplained skipped case, AND every case the scan says can run was collected and passed by
    name; a case the source says cannot run is listed with its reason and proves nothing. A row is `failed` when any command did not pass, `partly-runnable` when everything that can run passed but a
    command or a case is listed as not runnable, `passed` when every command ran in full, and `not-runnable` when nothing of it can run here. The cargo run is the workflow step."""
    problems: list[str] = []
    status_of = {}
    for kind, counts in (("pytest", pytest_counts), ("vitest", vitest_counts)):
        for name, c in counts.items():
            status, why = file_status(c)
            scan = plan_["scans"][f"{kind}:{name}"]
            required = []
            for case in scan["cases"]:
                if case["run"]:
                    expected = expected_variants(plan_, name, _case_key(case)) if kind == "pytest" else (set(case["variants"]) if case.get("variants") else None)      # vitest: the iterations of a literal loop
                    required.append({"case": case["case"], "id": _case_key(case) if kind == "pytest" else case["case"], "status": case_status(c["cases"].get(_case_key(case)), expected),
                                     "variants": None if expected is None else len(expected)})
            absent = [r["id"] for r in required if r["status"] == "absent"]
            short = [f"{r['id']} ({r['status']})" for r in required if r["status"] not in ("passed", "absent")]      # a required case that did not pass fails the file whatever its name (rf4f H4)
            if status == "passed" and (absent or short):
                status = "failed"
                why = "; ".join(([f"{len(absent)} required case(s) not collected: {', '.join(absent[:5])}"] if absent else []) + ([f"{len(short)} required case(s) not passed: {', '.join(short[:5])}"] if short else []))
            status_of[f"{kind}:{name}"] = {"status": status, "reason": why, "counts": {k: v for k, v in c.items() if k not in ("cases", "unexpected")}, "requiredCases": required}
            if status != "passed":
                problems.append(f"{name}: {why}")
    rows = []
    for row in plan_["rows"]:
        outcome = []
        for entry in row["commands"]:
            if entry["run"] is True:
                scan = plan_["scans"][entry["key"]]
                outcome.append({**{k: entry[k] for k in ("kind", "target")}, **status_of[entry["key"]], "expectedSkips": [s["reason"] for s in scan["skips"]],
                                "notRunnableCases": [{"case": s["case"] if "pattern" not in s else s["title"], "class": s.get("class"), "reason": s["reason"]} for s in scan["skips"]]})
            elif entry["run"] == "workflow-step":
                outcome.append({"kind": entry["kind"], "target": entry["target"], "status": "workflow-step", "reason": None})
            else:
                outcome.append({"kind": entry["kind"], "target": entry["target"], "status": "not-runnable", "reason": entry["reason"]})
        ran = [o for o in outcome if o["status"] != "not-runnable"]
        if not ran:
            state = "not-runnable"
        elif any(o["status"] in ("missing", "failed", "skipped") or any(r["status"] != "passed" for r in o.get("requiredCases", [])) for o in ran):
            state = "failed"      # `partly-runnable` never holds a required case that was skipped, absent, incomplete or failed (rf4f H4)
        elif any(o["status"] == "not-runnable" or o.get("notRunnableCases") for o in outcome):
            state = "partly-runnable"
        else:
            state = "passed"
        rows.append({"row": row["row"], "platform": row["platform"], "status": state, "commands": outcome})
    return problems, rows


def sha256_file(path: str | Path) -> str | None:
    """The SHA-256 of the file's bytes, hashed here (never by `sha256sum`, which escapes a backslash path); None when the file cannot be read, so a receipt then fails the F0 comparison."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def receipt(system: str, repo: str, pre_out: str, rows: list[dict], problems: list[str]) -> dict:
    """What the cost run's precondition compares with F0: the sidecar this correctness run used, by path and by the SHA-256 of the file on disk now, and the harness beside it."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    harness = harness_path(pre_out, system)
    return {"schema": RECEIPT_SCHEMA, "platform": system, "sidecar": {"path": sidecar, "sha256": sha256_file(sidecar)}, "harnessSha256": sha256_file(harness),
            "rowIds": [r["row"] for r in rows], "rowStatuses": {r["row"]: r["status"] for r in rows}, "problems": problems, "passed": not problems and all(r["status"] != "failed" for r in rows)}


def write_outputs(system: str, repo: str, pre_out: str, plan_: dict, junit_text: str | None, vitest_text: str | None, extra_problems: list[str] = ()) -> list[str]:
    """Gate the two result files against the plan, then write `correctness-gate.json` (counts, problems, the row table, the deselected ids, the reader descriptor staging and the number of collected node ids)
    and `correctness-receipt.json`. `extra_problems` are what the steps before the gate found (staging, real collection). Returns the problems."""
    problems: list[str] = list(extra_problems)
    pytest_counts: dict = {}
    vitest_counts: dict = {}
    try:
        pytest_counts = collect_pytest(junit_text, plan_) if junit_text is not None else {}
        if junit_text is None and plan_["pytest"]:
            problems.append("pytest wrote no JUnit file")
    except ElementTree.ParseError as error:
        problems.append(f"the JUnit file is not parseable ({error.__class__.__name__})")
    try:
        vitest_counts = collect_vitest(vitest_text, plan_) if vitest_text is not None else {}
        if vitest_text is None and plan_["vitest"]:
            problems.append("vitest wrote no JSON result file")
    except ValueError as error:
        problems.append(f"the vitest JSON file is not parseable ({error.__class__.__name__})")
    gate_problems, rows = evaluate(plan_, pytest_counts or {n: _blank() for n in plan_["pytest"]}, vitest_counts or {n: _blank() for n in plan_["vitest"]})      # no readable result: every file counts as not collected
    problems += gate_problems
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    record = {"schema": SCHEMA, "platform": system, "rows": rows, "deselected": plan_.get("deselect", []), "readerDescriptor": plan_.get("readerDescriptor"),
              "collectedNodeIds": None if plan_.get("collected") is None else len(plan_["collected"]), "problems": problems, "passed": not problems}
    (Path(pre_out) / GATE_NAME).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (Path(pre_out) / RECEIPT_NAME).write_text(json.dumps(receipt(system, repo, pre_out, rows, problems), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return problems


# ---------------------------------------------------------------------------------------------------------------------------- the run
def pytest_argv(python: str, files: list[str], junit: str, temp: str, deselect: list[str] = ()) -> list[str]:
    return [python, "-m", "pytest", *[f"backend/tests/{name}" for name in files], *[arg for node in deselect for arg in ("--deselect", node)], "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit2",
            "--basetemp", temp, "--junitxml", junit]


def collect_argv(python: str, files: list[str], deselect: list[str] = ()) -> list[str]:
    """`pytest --collect-only` with the run's own file and `--deselect` arguments, so real collection enumerates exactly the node ids the run will execute (rf4f H2, H3)."""
    return [python, "-m", "pytest", *[f"backend/tests/{name}" for name in files], *[arg for node in deselect for arg in ("--deselect", node)], "--collect-only", "-q", "-p", "no:cacheprovider"]


COLLECTED_LINE = re.compile(r"(?:(\d+)/)?(\d+) tests? collected(?: \((\d+) deselected\))?")


def parse_collection(text: str) -> tuple[list[str], int | None, int]:
    """(node ids, deselected count, collected count) of `pytest --collect-only -q` output: every line that starts with `tests/` and holds `::` is a node id (a parametrization id may hold spaces); the summary
    line says how many items `--deselect` removed. `None` when the output has no summary line."""
    ids = [line.strip() for line in text.splitlines() if line.startswith(f"{ROOTDIR_TESTS}/") and "::" in line]
    summary = COLLECTED_LINE.search(text)
    return ids, (int(summary.group(3) or 0) if summary else None), (int(summary.group(1) or summary.group(2)) if summary else 0)


def real_collect(argv: list[str], cwd: str, env: dict) -> tuple[int, str]:
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return done.returncode, done.stdout


def collection_problems(ids: list[str], deselected: int | None, collected: int, deselect: list[str], unfiltered: list[str] | None = None) -> list[str]:
    """What real collection says about the plan: it collected something, and each `--deselect` id removed exactly the cases the plan names for it (rf4g J3). `--deselect` is a prefix match, so an id is checked on
    its own against `unfiltered`, the node ids of the same collection without any `--deselect`: it must match at least one item (an id that matches nothing, like the pre-rf4f `backend/tests/...` form, removes
    none), and every item it matches must be the named case itself or one of its parametrized variants (`<id>[...]`), not a different case that merely starts with the same text. The total alone cannot tell, since an
    id that matches two items hides another that matches none. Without `unfiltered` only the total is checked."""
    out = []
    if not ids or collected != len(ids):
        out.append(f"pytest --collect-only listed {len(ids)} node id(s) but reported {collected} collected")
    if deselect and (deselected is None or deselected < len(deselect)):
        out.append(f"{len(deselect)} --deselect id(s) removed only {deselected or 0} item(s) in real collection: an id matches nothing (node ids are relative to the rootdir, `{ROOTDIR_TESTS}/<file>::<case>`)")
    for node in deselect if unfiltered is not None else ():
        hit = [item for item in unfiltered if item.startswith(node)]
        stray = [item for item in hit if item != node and not item.startswith(node + "[")]
        if not hit:
            out.append(f"--deselect id {node!r} matches no collected case")
        elif stray:
            out.append(f"--deselect id {node!r} also matches {len(stray)} case(s) the plan does not name: {stray[:3]}")
    return out


def vitest_argv(files: list[str], out: str) -> list[str]:
    return [*CLIENT, "vitest", "run", "--project", "cli", *[f"tests/cli/{name}" for name in files], "--reporter=json", f"--outputFile.json={out}"]


def stage_reader_descriptor(repo: str, pre_out: str, system: str) -> dict:
    """The runner-local fixture descriptor of the reader cases (rf4f H1). The pack carries a placeholder `reader-fixtures.json` (relative placeholder paths, rf4g J1); here the descriptor the `reader-fixtures`
    pre step wrote (`pre_out/FIXTURES.json`: fixtures generated on this runner by the frozen synthetic generators of `reader_fixtures`, SEED 20260725) is verified against the registered digests for the readers
    this platform registers, and written over the packed one. Returns {fixtures, staged, packedSha256, stagedSha256, entries, problems}; nothing is staged (and a problem recorded) when the descriptor is missing or
    does not verify, so the reader cases then fail loudly on the packed paths instead of passing on something else. A platform with no readers stages nothing."""
    from tests.cli_regression.support.g3 import fixtures as g3_fixtures, hosted_reader
    names = tuple(hosted_reader.READERS[system])
    target = Path(repo) / STAGED_DESCRIPTOR
    record: dict = {"fixtures": list(names), "staged": False, "target": STAGED_DESCRIPTOR, "problems": []}
    if not names:
        return record
    source = Path(pre_out) / "FIXTURES.json"
    problems = g3_fixtures.descriptor_problems(source, names=names)
    if problems:
        record["problems"] = problems
        return record
    descriptor = json.loads(source.read_text(encoding="utf-8"))
    record["packedSha256"] = sha256_file(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)      # never write through a link
    target.write_text(json.dumps({name: descriptor[name] for name in names}, indent=1) + "\n", encoding="utf-8")
    record.update(staged=True, stagedSha256=sha256_file(target), source=str(source), entries={name: {k: descriptor[name][k] for k in ("bytes", "entries", "sha256", "golden_sha256")} for name in names})
    return record


def run(repo: str, pre_out: str, system: str, *, python: str | None = None, call: Callable[..., subprocess.CompletedProcess] = subprocess.run, tree: RunnerTree | None = None,
        collect: Callable[[list[str], str, dict], tuple[int, str]] = real_collect) -> int:
    """The registry's pytest and vitest files that can run on `system`, on the F0 sidecar (the file the cost run measures) and the harness built beside it, then the gate. Exit 0 only when both runs exit 0
    and the gate has no problem. The runner first stages its own reader descriptor (`stage_reader_descriptor`), so the reader cases run on fixtures it built and verified; then REAL collection
    (`pytest --collect-only` with the run's arguments) fixes the node ids the gate expects, every variant of a parametrized case included, and proves each `--deselect` id removes something. What the scan lists as
    not runnable on the platform is deselected (it would fail, not skip) and stays listed in the row table."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    env = {"NAVIDE_CLI_RUNTIME_BIN": sidecar, "NAVIDE_T05_SIDECAR": sidecar, "NAVIDE_CLI_RUNTIME_HARNESS_BIN": harness_path(pre_out, system)}
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ACR_G3_HOSTED") and k not in ("BUN_OPTIONS", "NAVIDE_T04_WRITER_BINDING_DIR")}      # the tests run on the product, not under the measurement seams, and without a ticket-04 controller
    chosen = plan(system, repo, tree)
    junit, vitest = f"{pre_out}/{JUNIT_NAME}", f"{pre_out}/{VITEST_NAME}"
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    chosen["readerDescriptor"] = stage_reader_descriptor(repo, pre_out, system)
    extra = [f"reader descriptor: {p}" for p in chosen["readerDescriptor"]["problems"]]
    codes = []
    if chosen["pytest"]:
        interpreter = python or f"{repo}/{python_rel(system)}"
        code, text = collect(collect_argv(interpreter, chosen["pytest"], chosen["deselect"]), repo, {**clean, **env})
        ids, deselected, collected = parse_collection(text)
        chosen["collected"] = ids
        unfiltered = None
        if chosen["deselect"]:      # rf4g J3: the same collection with no --deselect, so each id is checked on its own
            full_code, full_text = collect(collect_argv(interpreter, chosen["pytest"], []), repo, {**clean, **env})
            unfiltered = parse_collection(full_text)[0]
            extra += [f"pytest --collect-only (no --deselect) exited {full_code}"] if full_code != 0 else []
        extra += ([f"pytest --collect-only exited {code}"] if code != 0 else []) + collection_problems(ids, deselected, collected, chosen["deselect"], unfiltered)
        codes.append(call(pytest_argv(interpreter, chosen["pytest"], junit, basetemp(system), chosen["deselect"]), cwd=repo, env={**clean, **env}).returncode)
    if chosen["vitest"]:
        codes.append(call(vitest_argv(chosen["vitest"], vitest), cwd=repo, env={**clean, **env}).returncode)
    problems = write_outputs(system, repo, pre_out, chosen, Path(junit).read_text(encoding="utf-8") if Path(junit).is_file() else None, Path(vitest).read_text(encoding="utf-8") if Path(vitest).is_file() else None, extra)
    for problem in problems:
        print(f"correctness gate: {problem}", file=sys.stderr)
    return 0 if all(code == 0 for code in codes) and not problems else 1


# ---------------------------------------------------------------------------------------------------------------------------- binding to F0
def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def receipt_problems(f0: dict, pre_out: str | Path, system: str) -> list[str]:
    """The precondition: the correctness receipt (every system) and the G2 binding (Linux, Windows) exist, passed, and name F0's sidecar by path and by SHA-256. A receipt of another sidecar fails."""
    problems = []
    f0_sidecar = f0.get("sidecar") or {}
    for name, schema in [(RECEIPT_NAME, RECEIPT_SCHEMA)] + ([(G2_BINDING_NAME, G2_SCHEMA)] if system in G2_PLATFORMS else []):
        record = _load(Path(pre_out) / name)
        if not (isinstance(record, dict) and record.get("schema") == schema):
            problems.append(f"{name} is missing or not a {schema} record")
            continue
        if record.get("passed") is not True:
            problems.append(f"{name} did not pass: {record.get('problems')}")
        sidecar = record.get("sidecar") or {}
        if sidecar.get("sha256") != f0_sidecar.get("sha256"):
            problems.append(f"{name} names another sidecar than F0 (sha256 differs)")
        if os.path.realpath(str(sidecar.get("path", ""))) != os.path.realpath(str(f0_sidecar.get("path", ""))):
            problems.append(f"{name} names another sidecar path than F0")
        if name == RECEIPT_NAME and sorted(record.get("rowIds", [])) != sorted(row_ids(system)):
            problems.append(f"{name} does not cover the 21 correctness rows" + (" and the macOS G2 part" if system == "Darwin" else ""))
    return problems


RECEIPTS_LINE = re.compile(r"ready receipts=(\d+) expected executableSha256=([0-9a-fA-F]+) problems=(\d+)")
OUTCOME_LINE = re.compile(r"\bcase=(.+) status=(\S+) stage=\S+\s*$", re.M)      # `g2Native.mjs guard` lines: `... case=<id> status=<status> stage=<stage>`; a parametrized id may hold spaces


def required_ids(text: str) -> list[str]:
    """The node ids of a `tests/cli/g2-required-<platform>.txt`: blank lines and `#` comments skipped (as `g2Native.requiredIds`)."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def bind_g2(repo: str, pre_out: str, system: str, receipts_out: str, outcomes: str, junit: str, required: str) -> list[str]:
    """Re-check, by name, the G2 required cases the workflow's guard ran, and bind the receipts step to the F0 sidecar: the receipts output must say `problems=0` for the SHA-256 of the sidecar file as it is now,
    every required id must be `passed` in the outcomes log, and the record names the sidecar by path and SHA-256 (`g2-receipt-binding.json`)."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    sha = sha256_file(sidecar)
    problems = []
    text = Path(receipts_out).read_text(encoding="utf-8")
    matches = RECEIPTS_LINE.findall(text)
    if len(matches) != 1:
        problems.append("the receipts output has no single `ready receipts=... problems=...` line")
    else:
        ready, named, bad = matches[0]
        if named.lower() != sha:
            problems.append("the receipts were checked against another sidecar hash than the F0 sidecar's")
        if int(bad) != 0 or int(ready) == 0:
            problems.append(f"the receipts step reported {ready} ready receipt(s) and {bad} problem(s)")
    ids = required_ids(Path(required).read_text(encoding="utf-8"))
    states = dict(OUTCOME_LINE.findall(Path(outcomes).read_text(encoding="utf-8")))
    problems += [f"{case}: {states.get(case, 'missing')}" for case in ids if states.get(case) != "passed"]
    if not ids:
        problems.append("the required list names no case")
    record = {"schema": G2_SCHEMA, "platform": system, "sidecar": {"path": sidecar, "sha256": sha}, "harnessSha256": sha256_file(harness_path(pre_out, system)), "requiredCases": len(ids),
              "passedCases": sum(states.get(case) == "passed" for case in ids), "receiptsOutputSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "junitSha256": sha256_file(junit),
              "requiredListSha256": sha256_file(required), "problems": problems, "passed": not problems}
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    (Path(pre_out) / G2_BINDING_NAME).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return problems


def table_markdown(rows: list[dict]) -> str:
    """The row table as Markdown: row id, platform, status, then each command with its outcome or reason and its case counts. Each command's required and not-runnable cases follow the table, by name."""
    lines = ["| row | platform | status | commands |", "|---|---|---|---|"]
    for row in rows:
        cells = "; ".join(f"{c['kind']} {c['target']}: {c['status']}" + (f" ({c['reason']})" if c.get("reason") else "")
                          + (f" [{len(c['requiredCases'])} required case(s), {len(c['notRunnableCases'])} not runnable]" if c.get("requiredCases") is not None and (c["requiredCases"] or c["notRunnableCases"]) else "")
                          for c in row["commands"])
        lines.append(f"| {row['row']} | {row['platform']} | {row['status']} | {cells} |")
    detail = ["", "## Cases by row", ""]
    for row in rows:
        for c in row["commands"]:
            if c.get("requiredCases") is None or not (c["requiredCases"] or c["notRunnableCases"]):
                continue
            detail.append(f"- `{row['row']}` / {c['kind']} `{c['target']}`")
            detail += [f"  - required: `{r['id']}`" + (f" ({r['status']})" if r["status"] not in ("passed", "absent") else "") for r in c["requiredCases"]]
            detail += [f"  - not runnable: `{(n['class'] + '::' if n.get('class') else '') + n['case']}`: {n['reason']}" for n in c["notRunnableCases"]]
    return "\n".join(lines) + "\n" + ("\n".join(detail) + "\n" if len(detail) > 3 else "")


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "run":
        return run(argv[1], argv[2], argv[3])
    if len(argv) == 3 and argv[0] == "rows":
        chosen = plan(argv[2], argv[1])
        print(table_markdown(evaluate(chosen, {n: _blank() for n in chosen["pytest"]}, {n: _blank() for n in chosen["vitest"]})[1]))
        return 0
    if len(argv) == 8 and argv[0] == "bind":
        problems = bind_g2(*argv[1:])
        for problem in problems:
            print(problem)
        return 0 if not problems else 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
