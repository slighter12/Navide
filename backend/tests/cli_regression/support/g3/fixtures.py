"""The reader-fixture descriptor of the G3 reader runs (review-1 finding 5): built BEFORE F0 from the deterministic generators of `reader_fixtures`, verified against the digests the proposal
registers (H5: `claude` 52d4b382..., `opencode` d58be6f1...), and bound into F0 (`readerFixtureDescriptor`). The unchanged g1e reader runs open this descriptor first; a missing, changed or
unregistered fixture is `reader-fixture-missing` (exit 5, decider fact `readerFixtureMissing`), never an uncaught error in the middle of the run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REGISTERED_SHA256 = {"claude": "52d4b382b7b68627a0be6beb5bc1c8276261dc948925a00886aa4ffea8d206f6", "opencode": "d58be6f1610a1502995ec04a7f5bff49d27b435085981dd9b057ebc4a34b5b26"}
MISSING_EXIT = 5


def _sha(path) -> str | None:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def descriptor_problems(descriptor_path: Path, expected: dict[str, dict] | None = None) -> list[str]:
    """Problems of a descriptor file (empty = every registered fixture and golden exists and has the registered bytes). `expected` = F0's record {name: {sha256, golden_sha256}} when given."""
    try:
        descriptor = json.loads(Path(descriptor_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"the reader fixture descriptor {descriptor_path} is missing or not JSON"]
    out = []
    for name, registered in REGISTERED_SHA256.items():
        entry = descriptor.get(name) if isinstance(descriptor, dict) else None
        if not isinstance(entry, dict):
            out.append(f"{name}: no fixture in the descriptor")
            continue
        if entry.get("sha256") != registered or _sha(entry.get("path", "")) != registered:
            out.append(f"{name}: the fixture bytes are not the registered ones")
        if not entry.get("golden_sha256") or _sha(entry.get("golden", "")) != entry["golden_sha256"]:
            out.append(f"{name}: the golden list is missing or differs from its recorded digest")
        if expected is not None and {k: entry.get(k) for k in ("sha256", "golden_sha256")} != expected.get(name):
            out.append(f"{name}: differs from the fixture F0 recorded")
    return out


def f0_record(descriptor_path: Path) -> dict:
    """What F0 records: the descriptor path and digest and each fixture's digests. Refuses a descriptor that does not verify."""
    problems = descriptor_problems(descriptor_path)
    if problems:
        raise ValueError("; ".join(problems))
    descriptor = json.loads(Path(descriptor_path).read_text(encoding="utf-8"))
    return {"path": str(descriptor_path), "sha256": _sha(descriptor_path), "fixtures": {name: {k: descriptor[name][k] for k in ("sha256", "golden_sha256")} for name in REGISTERED_SHA256}}


def f0_problems(record: object) -> list[str]:
    """Verification of F0's record against the files now (the descriptor file's digest, then each fixture)."""
    if not isinstance(record, dict) or not isinstance(record.get("fixtures"), dict):
        return ["F0 records no reader fixture descriptor"]
    if _sha(record.get("path", "")) != record.get("sha256"):
        return ["the reader fixture descriptor differs from the one F0 recorded"]
    return descriptor_problems(Path(record["path"]), record["fixtures"])


def build(root: Path, directory: Path, descriptor_path: Path) -> dict:
    """Builds the fixtures (the deterministic generators) into the new `directory` and writes the descriptor O_EXCL; refuses bytes that are not the registered ones. Touches no existing file."""
    from tests.cli_regression.support import reader_fixtures as rf
    directory, descriptor_path = Path(directory), Path(descriptor_path)
    if directory.exists() or descriptor_path.exists():
        raise FileExistsError("reader fixtures exist: refusing to overwrite")
    directory.mkdir(parents=True)
    descriptor = rf.build(directory)
    fd = os.open(descriptor_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(descriptor, indent=1) + "\n").encode())
    finally:
        os.close(fd)
    problems = descriptor_problems(descriptor_path)
    if problems:
        raise ValueError("; ".join(problems))
    return descriptor
