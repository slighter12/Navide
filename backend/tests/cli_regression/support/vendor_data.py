"""Materialize synthetic vendor stores; no production reader is replaced.

Fixtures contain literal wire records, expected results, and provenance.
SQLite writers stay open in WAL mode for the entire scenario, matching a
live CLI whose committed rows have not checkpointed into the main database.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from agent_team_backend.cli_vendors.droid import encode_droid_cwd
from agent_team_backend.cli_vendors.pi import encode_pi_cwd
from agent_team_backend.cli_vendors.cursor import cursor_project_hash
from agent_team_backend.log_readers.base import encode_claude_cwd
from ..catalog import FIXTURES

PANE = "abcd1234-1111-4111-8111-111111111111"
MARKER = f"at-pane:{PANE}"


class VendorData:
    def __init__(self, key, root, monkeypatch, set_home):
        self.fixture = json.loads((FIXTURES / f"{key}.json").read_text(encoding="utf-8"))
        self.home = root / "home"
        self.workspace = root / "workspace with spaces"
        self.home.mkdir(exist_ok=True)  # a backend's isolated environment creates it first
        self.workspace.mkdir()
        set_home(self.home)
        for name in ("CLAUDE_CONFIG_DIR", "GROK_HOME", "KIMI_CODE_HOME", "QWEN_RUNTIME_DIR",
                     "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR", "COPILOT_HOME",
                     "FACTORY_HOME_OVERRIDE", "CODEX_HOME", "QWEN_HOME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(self.home / ".local" / "share"))
        # Goose's data dir follows %APPDATA% on Windows; its own root variable
        # keeps that store inside the scratch home on every runner.
        monkeypatch.setenv("GOOSE_PATH_ROOT", str(self.home / "goose-root"))
        self.values = {
            "HOME": str(self.home), "WORKSPACE": str(self.workspace),
            "PANE": PANE, "MARKER": MARKER,
            "CLAUDE_CWD": encode_claude_cwd(str(self.workspace)),
            "PI_CWD": encode_pi_cwd(str(self.workspace)),
            "DROID_CWD": encode_droid_cwd(str(self.workspace)),
            "GROK_CWD": quote(str(self.workspace), safe=""),
            "CURSOR_CWD": cursor_project_hash(str(self.workspace)),
            "WORKSPACE_URI": self.workspace.as_uri(),
        }
        self.fixture = self.expand(self.fixture)
        self.path = Path(self.fixture.get("path", str(self.workspace / "unused")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connections = {}
        #: A store shared by every session (one file the CLI keeps open) can be seen empty before its first rows. A
        #: per-session file, named by the session id, is created together with its first rows and never is.
        self.shared_store = self.fixture.get("sessionId", "") not in str(self.path)

    def expand(self, value, extra=None):
        if isinstance(value, str):
            for key, replacement in {**self.values, **(extra or {})}.items():
                value = value.replace("${" + key + "}", replacement)
            return value
        if isinstance(value, list):
            return [self.expand(item, extra) for item in value]
        if isinstance(value, dict):
            return {key: self.expand(item, extra) for key, item in value.items()}
        return value

    def has_schema(self, phase) -> bool:
        return any(operation.get("schema") for operation in self.fixture.get("phases", {}).get(phase, []))

    def apply(self, phase, only=None):
        """Write a phase. `only="schema"` creates a SQLite store's tables and nothing else; `only="rows"` then adds
        the phase's rows, so a scenario can let a watcher see the empty store first."""
        self._write(self.fixture.get("phases", {}).get(phase, []), only)

    def append(self, number):
        """Add one new record to a store whose fixture declares an `append` template. Every `${N}` in the template
        becomes `number`, so each call writes a fresh id of the reader's own shape instead of rewriting a fixed row."""
        self._write(self.expand(self.fixture["append"]["operations"], {"N": str(number)}), None)

    def count(self):
        """The records the fixture's `append.count` query reports, on a connection of its own."""
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(self.fixture["append"]["count"]).fetchone()[0]
        finally:
            connection.close()

    def _write(self, operations, only):
        for operation in operations:
            path = Path(operation.get("path", str(self.path)))
            path.parent.mkdir(parents=True, exist_ok=True)
            kind = operation["kind"]
            if kind != "sqlite" and only == "schema":
                continue
            if kind == "json":
                path.write_text(json.dumps(operation["value"]), encoding="utf-8")
            elif kind in {"jsonl", "text"}:
                text = ("".join(json.dumps(record) + "\n" for record in operation["records"])
                        if kind == "jsonl" else operation["text"])
                with path.open("a" if operation.get("append") else "w", encoding="utf-8") as stream:
                    stream.write(text)
            elif kind == "sqlite":
                connection = self.connections.get(path)
                built = only == "rows"
                written = only == "schema"
                if connection is None:
                    if operation.get("schema") and not path.exists() and only != "rows":
                        # Build the store in a sibling file and rename it into place, so the watched path never
                        # exists half-built. A shared store appears with its tables only (a poll can find it empty).
                        # A per-session store appears already holding its creating phase's rows, as a CLI's file
                        # does: a poll between "schema" and "rows" would otherwise read an empty workspace cwd.
                        staged = path.with_name(path.name + ".staged")
                        with sqlite3.connect(staged) as staging:
                            staging.execute("PRAGMA journal_mode=WAL")
                            staging.executescript(f"BEGIN;\n{operation['schema']}\nCOMMIT;")
                            if not self.shared_store and not written:
                                self._execute(staging, operation.get("statements", []))
                                written = True
                        staging.close()
                        staged.replace(path)
                        built = True
                    connection = sqlite3.connect(path)
                    connection.execute("PRAGMA journal_mode=WAL")
                    self.connections[path] = connection
                if operation.get("schema") and not built:
                    # One transaction: a poll between two CREATEs would find a half-built schema.
                    connection.executescript(f"BEGIN;\n{operation['schema']}\nCOMMIT;")
                if not written:
                    self._execute(connection, operation.get("statements", []))
                connection.commit()
            else:
                raise AssertionError(f"unknown fixture operation {kind}")

    @staticmethod
    def _execute(connection, statements):
        for statement in statements:
            params = []
            for value in statement.get("params", []):
                if isinstance(value, dict) and "$hex" in value:
                    value = bytes.fromhex(value["$hex"])
                elif isinstance(value, dict) and "$utf8" in value:
                    value = value["$utf8"].encode()
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value)
                params.append(value)
            connection.execute(statement["sql"], params)

    def close(self):
        for connection in self.connections.values():
            connection.close()
