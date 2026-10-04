"""Generate reference-vendor facts; --check verifies committed consumers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def outputs() -> dict[Path, str]:
    facts = json.loads((ROOT / "shared/agent-cli/claude.json").read_text())
    header = "Generated from shared/agent-cli/claude.json; do not edit."
    py = f'"""{header}"""\nfrom typing import NamedTuple\n\n\nclass ClaudeFacts(NamedTuple):\n'
    for key, value in facts.items():
        if key != "shutdown":
            py += f"    {key}: {'tuple[str, ...]' if isinstance(value, list) else 'str'}\n"
    py += "    graceful: bool\n    grace_s: float\n    defer_master_close: bool\n\n\nCLAUDE = ClaudeFacts(\n"
    for key, value in facts.items():
        if key != "shutdown":
            py += f"    {key}={tuple(value) if isinstance(value, list) else value!r},\n"
    for key, value in facts["shutdown"].items():
        py += f"    {key}={value!r},\n"
    py += ")\n"
    ts = f"// {header}\nexport const CLAUDE = " + json.dumps(facts, indent=2) + " as const\n"
    rust = f"// {header}\n"
    rust += f'pub const KEY: &str = {json.dumps(facts["key"])};\n'
    rust += f'pub const GRACEFUL: bool = {str(facts["shutdown"]["graceful"]).lower()};\n'
    rust += f'pub const GRACE_MS: u64 = {int(facts["shutdown"]["grace_s"] * 1000)};\n'
    rust += f'pub const DEFER_MASTER_CLOSE: bool = {str(facts["shutdown"]["defer_master_close"]).lower()};\n'
    return {
        ROOT / "backend/agent_team_backend/cli_vendors/_claude_facts.py": py,
        ROOT / "src/renderer/src/platform/plugin-shell/agents/_claudeFacts.ts": ts,
        ROOT / "native/navide-cli-runtime/src/claude_facts.rs": rust,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Stale generated Claude facts: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
