"""The g1e pin manifest (H1): the g3 code imports or reads the unchanged g1e files; their SHA-256 is pinned here so a change to any of them is visible.

`build_pins(root)` hashes every file of the g1e package plus the g1e files outside it that g3 relies on (`EXTRA`); `verify_pins` compares a manifest with the tree. The recorded manifest is
`g1e-pins.json` beside this module; its own canonical digest goes into F0. Nothing here edits a g1e file.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

G1E_DIR = "backend/tests/cli_regression/support/g1e"
EXTRA = [
    "backend/tests/cli_regression/support/g1e_peer.py", "backend/tests/cli_regression/support/g1e_counters_gate.py",
    "tests/cli/g1eSchedule.ts", "tests/cli/g1eBurst.ts", "tests/cli/g1eLedger.ts", "tests/cli/g1eWait.ts", "tests/cli/g1eStream.ts", "tests/cli/g1eReceipt.ts", "tests/cli/g1eRun.ts",
    "tests/cli/g1eAttempt.ts",
    "tests/fixtures/cli-regression/runtime-workload-g1e.json", "tests/fixtures/cli-regression/g1e-schedule-golden.json",
]
MANIFEST_NAME = "g1e-pins.json"
SCHEMA = "acr-g3-g1e-pins/1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_paths(root: Path) -> list[str]:
    package = sorted(str(p.relative_to(root)) for p in (Path(root) / G1E_DIR).iterdir() if p.is_file() and p.suffix in (".py", ".json"))
    return package + sorted(EXTRA)


def build_pins(root: Path) -> dict:
    return {"schema": SCHEMA, "files": [{"path": p, "sha256": sha256_file(Path(root) / p)} for p in pinned_paths(Path(root))]}


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_pins(root: Path, manifest: dict) -> list[str]:
    """Problems (empty = every pinned file has its pinned bytes, and no g1e package file is unpinned)."""
    if not (isinstance(manifest, dict) and manifest.get("schema") == SCHEMA and isinstance(manifest.get("files"), list)):
        return ["the manifest is not an acr-g3-g1e-pins/1 record"]
    out = []
    pinned = {}
    for entry in manifest["files"]:
        if not (isinstance(entry, dict) and set(entry) == {"path", "sha256"}):
            return ["a manifest entry is not {path, sha256}"]
        pinned[entry["path"]] = entry["sha256"]
        path = Path(root) / entry["path"]
        if not path.is_file():
            out.append(f"{entry['path']}: pinned but missing")
        elif sha256_file(path) != entry["sha256"]:
            out.append(f"{entry['path']}: bytes differ from the pin")
    for path in pinned_paths(Path(root)):
        if path not in pinned:
            out.append(f"{path}: a g1e file the g3 code may rely on is not pinned")
    return out


def g1e_modules_imported_by_g3(g3_dir: Path) -> set[str]:
    """The `tests.cli_regression.support.g1e.<module>` modules the g3 package imports (static scan), as repository paths."""
    found = set()
    for source in Path(g3_dir).glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "tests.cli_regression.support.g1e":
                    found |= {f"{G1E_DIR}/{alias.name}.py" for alias in node.names}
                elif node.module.startswith("tests.cli_regression.support.g1e."):
                    found.add(f"{G1E_DIR}/{node.module.rsplit('.', 1)[1]}.py")
    return found
