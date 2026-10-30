"""The two checks a hosted job runs first after the overlay, before anything is installed or built (review-fix round 1, finding 5). Standard library only, so it runs on the runner's own Python:

    hosted_preflight.py ROOT       prints {"problems": [...]}; exit 0 = every pinned g1e file has its pinned bytes AND the hosted inputs are the registered ones, 2 = problems

* the g1e pins: `pins.verify_pins` over `g1e-pins.json` (the module is loaded by path, so no package import and no third-party module is touched);
* the hosted inputs: the hashes-only documents manifest and the approval excerpt at the ledger path, held to the same rules as `hosted_f0.manifest_problems` / `excerpt_problems`.
The registered digests and paths below repeat `f0.DOC_PATHS` / `f0.DOC_DIGESTS` (this file may not import them: `f0` pulls in the psutil-using harness); `test_g3h_fix1_workflows.py` pins that they are equal.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

G3 = "backend/tests/cli_regression/support/g3"
PINS_REL, PIN_MANIFEST_REL, HOSTED_PINS_REL = f"{G3}/pins.py", f"{G3}/g1e-pins.json", f"{G3}/hosted_pins.py"
MANIFEST_REL = f"{G3}/hosted_inputs/documents-manifest.json"
LEDGER_REL = ".scratch/agent-cli-runtime-evaluation/progress.md"
MANIFEST_SCHEMA = "acr-g3h-documents-manifest/1"
DOC_PATHS = {"proposal": ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/prereg/g3-prereg-proposal.md",
             "v14r2": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v14-r2.md", "v10": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v10.md"}
DOC_DIGESTS = {"proposal": "d81ccb2897a1c652ef4fc968dbb6b133bc6d4db28075f820d2f040553efb6b43", "v14r2": "67ab288132cbab8fae90d7e7ab2ffc93ee56c8224f3eaa43777aae87dca710a8",
               "v10": "aa69dbcea6d4c6ee248e99f50102dd5924cdcc970fd878461b03fdbc75d9523e"}
APPROVAL_LABELS = ("G3 cost pre-registration", "G3-r2", "G3-r2-hosted")
SECRET_MARKERS = ("ghs_", "ghp_", "github_pat_", "BEGIN PRIVATE KEY", "Bearer ")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pin_problems(root: Path, windows: bool | None = None) -> list[str]:
    """`windows` (default: this interpreter is Windows) wraps `pinned_paths` so the g1e package keys are POSIX like the manifest's (`hosted_pins.py`, loaded by path as well)."""
    try:
        module = load("g3_pins_preflight", Path(root) / PINS_REL)
        if sys.platform == "win32" if windows is None else windows:
            module.pinned_paths = load("g3_hosted_pins", Path(root) / HOSTED_PINS_REL).posix_keys(module.pinned_paths)
        manifest = json.loads((Path(root) / PIN_MANIFEST_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError, ImportError) as error:
        return [f"the g1e pin check cannot run: {error.__class__.__name__}"]
    return module.verify_pins(Path(root), manifest)


def manifest_problems(manifest: object) -> list[str]:
    if not (isinstance(manifest, dict) and set(manifest) == {"schema", "documents"} and manifest["schema"] == MANIFEST_SCHEMA and isinstance(manifest["documents"], dict)):
        return ["the manifest is not an acr-g3h-documents-manifest/1 record"]
    documents = manifest["documents"]
    if set(documents) != set(DOC_PATHS):
        return [f"the manifest names {sorted(documents)}, not {sorted(DOC_PATHS)}"]
    out = []
    for name, record in documents.items():
        if not (isinstance(record, dict) and set(record) == {"path", "sha256"}):
            out.append(f"{name}: not {{path, sha256}} (a manifest holds hashes only)")
        elif record["path"] != DOC_PATHS[name]:
            out.append(f"{name}: path {record['path']!r} is not the registered {DOC_PATHS[name]!r}")
        elif record["sha256"] != DOC_DIGESTS[name]:
            out.append(f"{name}: hashes to {str(record['sha256'])[:12]}..., not the registered {DOC_DIGESTS[name][:12]}...")
    return out


def excerpt_problems(text: str) -> list[str]:
    lines = [line for line in text.split("\n") if line != ""]
    if not lines or text.endswith("\n\n"):
        return ["the excerpt is empty or has blank lines"]
    out, labels = [], []
    for line in lines:
        match = re.fullmatch(r"- \d{4}-\d{2}-\d{2} USER APPROVAL \(([^,)]+)[,)].*", line)
        if not match:
            out.append(f"a line is not a whole approval line: {line[:60]!r}")
            continue
        labels.append(match.group(1))
        if any(marker in line for marker in SECRET_MARKERS):
            out.append("a line carries credential-like text")
    if sorted(labels) != sorted(APPROVAL_LABELS):
        out.append(f"the excerpt approves {labels}, not exactly {list(APPROVAL_LABELS)}")
    g3 = next((line for line in lines if "(G3 cost pre-registration" in line), "")
    if DOC_DIGESTS["proposal"] not in g3 or "U1-U8" not in g3:
        out.append("the G3 line does not name the proposal digest and U1-U8")
    return out


def input_problems(root: Path) -> list[str]:
    out = []
    try:
        out += manifest_problems(json.loads((Path(root) / MANIFEST_REL).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        out.append(f"the documents manifest is missing or unreadable at {MANIFEST_REL}")
    try:
        out += [f"approval excerpt: {p}" for p in excerpt_problems((Path(root) / LEDGER_REL).read_text(encoding="utf-8"))]
    except OSError:
        out.append(f"the approval excerpt is not in place at {LEDGER_REL}")
    return out


def problems(root: Path) -> list[str]:
    return pin_problems(root) + input_problems(root)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 64
    found = problems(Path(argv[0]).resolve())
    print(json.dumps({"problems": found}))
    return 0 if not found else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
