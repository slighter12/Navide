#!/usr/bin/env python3
"""Audit CLI-agent callers for independent vendor fact tables and launch bypasses.

Declared facts live in ``shared/agent-cli/<key>.json`` and reach callers through
the generated ``_vendor_facts.py`` / ``_vendorFacts.ts`` / ``vendor_facts.rs``.
Vendor-specific behaviour lives in the registered strategies under
``backend/agent_team_backend/cli_vendors/``. Anything else that restates a
vendor's command, permission flag, resume or login arguments, or that starts an
agent process without the terminal service seam, is a finding.

Usage: ``python3 scripts/audit-cli-callers.py [--root DIR] [--out FILE]``.
Exit status 0 when there are no findings, 1 otherwise. One line per finding:
``path:line: kind: text``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SOURCE_ROOTS = ("src", "plugins", "packages", "backend/agent_team_backend", "shared")
SOURCE_SUFFIXES = {".ts", ".vue", ".py", ".mjs", ".tsx"}
SKIP_DIR_NAMES = {"node_modules", "__tests__", "dist", "out", "test-fixtures", "__pycache__"}
SKIP_FILE_SUFFIXES = (".test.ts", ".spec.ts", ".d.ts", ".test.tsx")

# Generated fact files, the declarations' consumers by construction.
GENERATED = {
    "backend/agent_team_backend/cli_vendors/_vendor_facts.py",
    "src/renderer/src/platform/plugin-shell/agents/_vendorFacts.ts",
    "src/shared/vendorFacts.ts",
}
# Registered strategies and the terminal service seam may hold vendor behaviour
# and spawn processes.
STRATEGY_PREFIXES = ("backend/agent_team_backend/cli_vendors/",)
SEAM_FILES = {
    "backend/agent_team_backend/terminals.py",
    "backend/agent_team_backend/cli_launch.py",
}
SEAM_PREFIXES = ("backend/agent_team_backend/cli_runtime", "backend/agent_team_backend/osplat/")

# A table row is a keyed literal that restates a declared fact.
FACT_KEY = (
    r"(?:command|bin|executable|yoloFlag|yolo_flag|skipFlag|skip_flag|permissionFlag|"
    r"permission_flag|resumeArgs|resume_args|loginArgs|login_args|sessionIdFlag|session_id_flag)"
)
ROW = re.compile(r"""\b""" + FACT_KEY + r"""['"]?\s*[:=]\s*(['"])([^'"]+)\1""")

SPAWN = re.compile(
    r"\b(?:subprocess\.(?:Popen|run|check_output|check_call)|create_subprocess_exec|"
    r"create_subprocess_shell|os\.forkpty|pty\.fork|winpty\.PtyProcess|child_process\.(?:spawn|exec)|"
    r"\bspawn\()"
)


def load_facts(root: Path) -> tuple[set[str], set[str]]:
    """(restated values, vendor commands) taken from the declarations."""
    values: set[str] = set()
    commands: set[str] = set()
    for path in sorted((root / "shared" / "agent-cli").glob("*.json")):
        decl = json.loads(path.read_text(encoding="utf-8"))
        command = decl.get("command")
        if isinstance(command, str):
            values.add(command)
            commands.add(command)
        for field in ("permission_flag", "resume_args", "session_id_flag", "login_args"):
            value = decl.get(field)
            if isinstance(value, str) and value:
                values.add(value)
    return values, commands


def iter_sources(root: Path):
    for top in SOURCE_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            if SKIP_DIR_NAMES.intersection(path.relative_to(root).parts):
                continue
            if path.name.endswith(SKIP_FILE_SUFFIXES) or path.name.startswith("test_"):
                continue
            yield path


def audit(root: Path) -> list[str]:
    values, commands = load_facts(root)
    findings: list[str] = []
    for path in iter_sources(root):
        rel = path.relative_to(root).as_posix()
        if rel in GENERATED or rel.startswith(STRATEGY_PREFIXES):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        is_seam = rel in SEAM_FILES or rel.startswith(SEAM_PREFIXES)
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("//", "#", "*", "/*")):
                continue
            match = ROW.search(line)
            if match and match.group(2) in values:
                findings.append(f"{rel}:{number}: fact-table: {stripped[:140]}")
            if not is_seam and SPAWN.search(line):
                window = " ".join(lines[number - 1 : number + 3])
                quoted = set(re.findall(r"""['"]([^'"]+)['"]""", window))
                if quoted & commands:
                    findings.append(f"{rel}:{number}: launch-bypass: {stripped[:140]}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    findings = audit(Path(args.root))
    text = "\n".join(findings) + ("\n" if findings else "")
    if args.out:
        Path(args.out).write_text(text or "zero findings\n", encoding="utf-8")
    sys.stdout.write(text or "zero findings\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
