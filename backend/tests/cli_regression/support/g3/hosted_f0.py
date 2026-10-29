"""Hosted F0 inputs and the F0 wrapper (design section 1 row 3, section 7.1 rows 7 and 8; addendum F1).

A runner has neither `S/` nor the full ledger, and the three registered documents must not be published. The harness side branch therefore carries
* `hosted_inputs/documents-manifest.json` - a hashes-only manifest (path + SHA-256 of the proposal, v14-r2 and v10, no content), checked against the registered digests, and
* `hosted_inputs/approval-excerpt.md` - only the whole approval lines for the six labels of `hosted_approvals.APPROVAL_LABELS` (G3, G3-r2, G3-r2-hosted, G3-r5-hosted, the Windows approval, G3-r6-hosted). The pack tool places it at the ledger path
  (`.scratch/agent-cli-runtime-evaluation/progress.md`), so the unchanged `approval_problems` (the quote must be one whole line of the ledger file) reads it with no code change.
`documents` in F0 keeps the macOS shape (`{name: {path, sha256}}`): `doc_records` is replaced through `hosted_seams` by `manifest_doc_records`, which answers from the manifest.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import f0 as f0mod, hosted_approvals, hosted_hostclass

INPUTS_REL = "backend/tests/cli_regression/support/g3/hosted_inputs"
MANIFEST_REL = f"{INPUTS_REL}/documents-manifest.json"
EXCERPT_REL = f"{INPUTS_REL}/approval-excerpt.md"
MANIFEST_SCHEMA = "acr-g3h-documents-manifest/1"
APPROVAL_LABELS = hosted_approvals.APPROVAL_LABELS      # one shared list (rf4c D15): `hosted_preflight` loads the same module
SECRET_MARKERS = hosted_approvals.SECRET_MARKERS


def manifest_problems(manifest: object) -> list[str]:
    """Empty = a hashes-only manifest of exactly the registered documents (the proposal, v14-r2, v10 and, from rf4d, the G3-r6-hosted addendum), each at its registered path with its registered digest."""
    if not (isinstance(manifest, dict) and set(manifest) == {"schema", "documents"} and manifest["schema"] == MANIFEST_SCHEMA and isinstance(manifest["documents"], dict)):
        return ["the manifest is not an acr-g3h-documents-manifest/1 record"]
    documents = manifest["documents"]
    if set(documents) != set(f0mod.DOC_PATHS):
        return [f"the manifest names {sorted(documents)}, not {sorted(f0mod.DOC_PATHS)}"]
    out = []
    for name, record in documents.items():
        if not (isinstance(record, dict) and set(record) == {"path", "sha256"}):
            out.append(f"{name}: not {{path, sha256}} (a manifest holds hashes only)")
        elif record["path"] != f0mod.DOC_PATHS[name]:
            out.append(f"{name}: path {record['path']!r} is not the registered {f0mod.DOC_PATHS[name]!r}")
        elif record["sha256"] != f0mod.DOC_DIGESTS[name]:
            out.append(f"{name}: hashes to {str(record['sha256'])[:12]}..., not the registered {f0mod.DOC_DIGESTS[name][:12]}...")
    return out


def manifest_doc_records(root: Path) -> tuple[dict, list[str]]:
    """The hosted `f0.doc_records`: the same `({name: {path, sha256}}, problems)` answer, from the manifest instead of the documents."""
    path = Path(root) / MANIFEST_REL
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, [f"the documents manifest is missing or unreadable at {MANIFEST_REL}"]
    problems = manifest_problems(manifest)
    if problems:
        return {}, problems
    return {name: dict(record) for name, record in manifest["documents"].items()}, []


def excerpt_problems(text: str) -> list[str]:
    """Empty = the excerpt holds only whole approval lines, one for each label of `hosted_approvals.APPROVAL_LABELS` (G3, G3-r2, G3-r2-hosted, G3-r5-hosted, the Windows approval, G3-r6-hosted), the G3 one
    naming the proposal digest and U1-U8, and nothing that looks like a credential."""
    return hosted_approvals.excerpt_problems(text, f0mod.PROPOSAL_SHA256)


def inputs_problems(root: Path, ledger: Path | None = None) -> list[str]:
    """What the hosted F0 wrapper checks instead of reading `S/` and the full ledger. `ledger` = the excerpt file (default: the ledger path under `root`, where the pack tool places it)."""
    out = []
    _, doc_problems = manifest_doc_records(root)
    out += doc_problems
    excerpt = Path(ledger) if ledger else Path(root) / f0mod.LEDGER_PATH
    try:
        out += [f"approval excerpt: {p}" for p in excerpt_problems(excerpt.read_text(encoding="utf-8"))]
    except OSError:
        out.append(f"the approval excerpt is not in place at {excerpt}")
    return out


def build_hosted_f0(root: Path, *, host: dict, runner_env: dict | None = None, row_set: dict | None = None, build: Callable[..., dict] = f0mod.build_f0, **kwargs) -> dict:
    """F0 for the hosted job. The hosted inputs are checked first and fail loudly; `build_f0` then runs unchanged (its `doc_records` answers from the manifest once `hosted_seams` is installed).
    The extra runner facts go in the sibling `hostedRunner` record: the five-field `hostClass` cannot carry them. `row_set` (`hosted_rows.row_set_record`) is hashed into the freeze there."""
    problems = inputs_problems(root, kwargs.get("ledger"))
    if problems:
        raise f0mod.F0Error(problems)
    f0 = build(Path(root), host=host, **kwargs)
    return {**f0, "hostedRunner": {**hosted_hostclass.runner_record(host, runner_env), **({"rowSet": row_set} if row_set else {})}}
