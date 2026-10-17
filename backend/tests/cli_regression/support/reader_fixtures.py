"""Hash-pinned reader-benchmark fixtures and their golden event lists (E3 S14; E2 v10 section 10).

Two fixtures, each >= 1 MiB and >= 1000 entries: a Claude conversation JSONL and an OpenCode SQLite database,
generated deterministically from a seed. The generator emits the golden event list (the ground truth the readers
are compared against); the fixture and golden hashes are recorded in `evidence/e3/reader-fixtures.json` and
checked before every run. Normalised event: `[input_tokens, output_tokens]` in source order.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3

SEED = 20260725
CLAUDE_ENTRIES = 2400          # assistant records with usage
OPENCODE_MESSAGES = 2000       # completed assistant messages
FILLER_BYTES = 600             # per-entry text so each fixture is comfortably over 1 MiB
MIN_BYTES, MIN_ENTRIES = 1 << 20, 1000


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tokens(rng: random.Random) -> tuple[int, int]:
    return rng.randint(1, 9000), rng.randint(1, 4000)


def generate_claude(directory: Path) -> tuple[Path, list[list[int]]]:
    """`directory/-bench-claude/<uuid>.jsonl`; the golden list holds one [input, output] per assistant record."""
    rng = random.Random(SEED)
    session = "7d3c1c1e-5b1f-4a55-9a52-0b6d9c7e4f10"
    folder = directory / "-bench-claude"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.jsonl"
    golden: list[list[int]] = []
    lines = []
    for index in range(CLAUDE_ENTRIES):
        text = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(FILLER_BYTES))
        if index % 3 == 0:  # interleaved user records the readers must skip
            lines.append(json.dumps({"type": "user", "uuid": f"u-{index}", "message": {"role": "user", "content": text}}))
        i, o = _tokens(rng)
        lines.append(json.dumps({"type": "assistant", "requestId": f"request-{index}", "version": "bench", "sessionId": session,
                                 "cwd": "/bench/claude", "timestamp": f"2026-01-01T00:{index // 60 % 60:02d}:{index % 60:02d}Z",
                                 "message": {"id": f"message-{index}", "model": "bench-model", "role": "assistant",
                                             "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                             "usage": {"input_tokens": i, "output_tokens": o}}}))
        golden.append([i, o])
    path.write_text("\n".join(lines) + "\n")
    return path, golden


def generate_opencode(directory: Path) -> tuple[Path, list[list[int]]]:
    """`directory/opencode.db` (rollback-journal mode, no WAL file); golden: one [input, output] per completed message."""
    rng = random.Random(SEED + 1)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "opencode.db"
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript("PRAGMA journal_mode=DELETE;"
                     "CREATE TABLE session(id TEXT PRIMARY KEY,parent_id TEXT,directory TEXT,version TEXT);"
                     "CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT);"
                     "CREATE TABLE part(id TEXT PRIMARY KEY,session_id TEXT,message_id TEXT,data TEXT,time_created INTEGER);")
    db.execute("INSERT INTO session VALUES(?,?,?,?)", ("ses_bench", None, "/bench/opencode", "bench"))
    golden: list[list[int]] = []
    for index in range(OPENCODE_MESSAGES):
        i, o = _tokens(rng)
        text = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(FILLER_BYTES))
        db.execute("INSERT INTO message VALUES(?,?,?)", (f"msg_{index:06d}", "ses_bench", json.dumps(
            {"role": "assistant", "time": {"created": 1700000000000 + index, "completed": 1700000000500 + index},
             "tokens": {"input": i, "output": o}, "modelID": "bench-model"})))
        db.execute("INSERT INTO part VALUES(?,?,?,?,?)", (f"prt_{index:06d}", "ses_bench", f"msg_{index:06d}",
                                                         json.dumps({"type": "text", "text": text}), 1700000000000 + index))
        golden.append([i, o])
    db.commit()
    db.close()
    return path, golden


def build(directory: Path) -> dict:
    """Generate both fixtures and golden lists under `directory` and return their descriptor (paths and hashes)."""
    claude, claude_golden = generate_claude(directory / "claude")
    opencode, opencode_golden = generate_opencode(directory / "opencode")
    out = {}
    for name, path, golden in (("claude", claude, claude_golden), ("opencode", opencode, opencode_golden)):
        golden_path = directory / f"{name}-golden.json"
        golden_path.write_text(json.dumps(golden, separators=(",", ":")))
        out[name] = {"path": str(path), "golden": str(golden_path), "bytes": path.stat().st_size, "entries": len(golden),
                     "sha256": sha256_file(path), "golden_sha256": sha256_file(golden_path)}
        assert out[name]["bytes"] >= MIN_BYTES and out[name]["entries"] >= MIN_ENTRIES, out[name]
    return out


def verify(descriptor: dict, pinned: dict) -> list[str]:
    """Names every pinned hash the descriptor no longer matches (an empty list means the fixtures are the pinned ones)."""
    problems = []
    for name, pin in pinned.items():
        got = descriptor.get(name, {})
        for key in ("sha256", "golden_sha256", "bytes", "entries"):
            if got.get(key) != pin.get(key):
                problems.append(f"{name}.{key}")
    return problems
