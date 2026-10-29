"""The approval excerpt rules, in ONE place (rf4c D15). Standard library only: `hosted_f0` imports it, and `hosted_preflight` loads it by path (it may not import the harness).

The hosted pack carries `hosted_inputs/approval-excerpt.md`: whole approval lines, one per label in `APPROVAL_LABELS`, and nothing else. Before rf4c the list lived twice (`hosted_f0.py`,
`hosted_preflight.py`) and held three labels, so G3-r5-hosted, the Windows approval and G3-r6-hosted could not be bound into a hosted F0. A line is
`- <date>[ <time> CST] USER APPROVAL (<label>[,)] ...`; the time part exists because the ledger's own Windows line (`progress.md`, 2026-11-03 21:1x CST) has one.
"""
from __future__ import annotations

import re

APPROVAL_LABELS = ("G3 cost pre-registration", "G3-r2", "G3-r2-hosted", "G3-r5-hosted", "Windows G3 hosted", "G3-r6-hosted")
SECRET_MARKERS = ("ghs_", "ghp_", "github_pat_", "BEGIN PRIVATE KEY", "Bearer ")
LINE_PATTERN = re.compile(r"- \d{4}-\d{2}-\d{2}(?: ~?[\dx]{1,2}:[\dx]{2} CST)? USER APPROVAL \(([^,)]+)[,)].*")
G3_LABEL = "(G3 cost pre-registration"


def excerpt_problems(text: str, proposal_sha256: str) -> list[str]:
    """Empty = the excerpt holds only whole approval lines, exactly one for each of `APPROVAL_LABELS`, the G3 one naming the proposal digest and U1-U8, and nothing that looks like a credential."""
    lines = [line for line in text.split("\n") if line != ""]
    if not lines or text.endswith("\n\n"):
        return ["the excerpt is empty or has blank lines"]
    out, labels = [], []
    for line in lines:
        match = LINE_PATTERN.fullmatch(line)
        if not match:
            out.append(f"a line is not a whole approval line: {line[:60]!r}")
            continue
        labels.append(match.group(1))
        if any(marker in line for marker in SECRET_MARKERS):
            out.append("a line carries credential-like text")
    if sorted(labels) != sorted(APPROVAL_LABELS):
        out.append(f"the excerpt approves {labels}, not exactly {list(APPROVAL_LABELS)}")
    g3 = next((line for line in lines if G3_LABEL in line), "")
    if proposal_sha256 not in g3 or "U1-U8" not in g3:
        out.append("the G3 line does not name the proposal digest and U1-U8")
    return out
