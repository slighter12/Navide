"""Generate the per-vendor launch facts from shared/agent-cli/<key>.json; --check verifies the committed output.

One declaration per vendor is the only hand-written copy. This emits the Python, TypeScript and Rust data that the
backend registry, the renderer agent specs and the native runtime read. Capability status stays out of the
TypeScript output: it is internal bookkeeping, never UI text.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = Path("shared/agent-cli")
SUBJECTS = Path("shared/agent-cli-subjects.json")
COMPOSITIONS = ("rust", "python")
PYTHON_OUTPUT = Path("backend/agent_team_backend/cli_vendors/_vendor_facts.py")
TYPESCRIPT_OUTPUT = Path("src/renderer/src/platform/plugin-shell/agents/_vendorFacts.ts")
RUST_OUTPUT = Path("native/navide-cli-runtime/src/vendor_facts.rs")

HEADER = "Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit."
STATUSES = ("integrated", "vendor-unsupported", "not-yet-integrated", "unknown")
CAPABILITIES = ("model", "effort", "skip_permissions", "resume_by_id", "session_id_at_launch", "login")
FIELDS = (
    "key", "composition", "command", "model_flag", "effort_flag", "known_efforts", "permission_flag", "resume_args",
    "session_id_flag", "login_args", "supports_session_resume", "shutdown", "capabilities",
)
TS_FIELDS = tuple(name for name in FIELDS if name not in {"shutdown", "capabilities", "supports_session_resume", "composition"})


def output_paths() -> tuple[Path, ...]:
    return (PYTHON_OUTPUT, TYPESCRIPT_OUTPUT, RUST_OUTPUT)


def load(root: Path) -> list[dict]:
    facts = []
    for path in sorted((root / DECLARATIONS).glob("*.json")):
        item = json.loads(path.read_text())
        if set(item) != set(FIELDS):
            raise SystemExit(f"{path.name}: fields {sorted(set(item) ^ set(FIELDS))} do not match the declaration shape")
        if item["key"] != path.stem:
            raise SystemExit(f"{path.name}: key {item['key']!r} must equal the file name")
        if set(item["capabilities"]) != set(CAPABILITIES) or not set(item["capabilities"].values()) <= set(STATUSES):
            raise SystemExit(f"{path.name}: capabilities must name {CAPABILITIES} with a status from {STATUSES}")
        if item["resume_args"] is not None and "{id}" not in item["resume_args"]:
            raise SystemExit(f"{path.name}: resume_args must contain {{id}}")
        if item["composition"] not in COMPOSITIONS:
            raise SystemExit(f"{path.name}: composition must be one of {COMPOSITIONS}")
        facts.append(item)
    if not facts:
        raise SystemExit(f"no declarations under {DECLARATIONS}")
    return facts


def load_subjects(root: Path) -> dict:
    """The non-vendor subjects (the ordinary shell) and how their launch is composed."""
    item = json.loads((root / SUBJECTS).read_text())
    if set(item) != {"aliases", "composition"} or item["composition"] not in COMPOSITIONS:
        raise SystemExit(f"{SUBJECTS.name}: expected aliases and a composition in {COMPOSITIONS}")
    if not item["aliases"] or len(set(item["aliases"])) != len(item["aliases"]):
        raise SystemExit(f"{SUBJECTS.name}: aliases must be a non-empty list without repeats")
    return item


def python_source(facts: list[dict], subjects: dict) -> str:
    lines = [
        f'"""{HEADER}"""',
        "from typing import NamedTuple",
        "",
        "",
        "class ShutdownFacts(NamedTuple):",
        "    graceful: bool",
        "    grace_s: float",
        "    defer_master_close: bool",
        "",
        "",
        "class CapabilityStatus(NamedTuple):",
        *(f"    {name}: str" for name in CAPABILITIES),
        "",
        "",
        "class VendorFacts(NamedTuple):",
        "    key: str",
        "    composition: str",
        "    command: str",
        "    model_flag: str | None",
        "    effort_flag: str | None",
        "    known_efforts: tuple[str, ...] | None",
        "    permission_flag: str | None",
        "    resume_args: str | None",
        "    session_id_flag: str | None",
        "    login_args: str | None",
        "    supports_session_resume: bool",
        "    shutdown: ShutdownFacts | None",
        "    capabilities: CapabilityStatus",
        "",
        "",
        "FACTS: dict[str, VendorFacts] = {",
    ]
    for item in facts:
        lines.append(f"    {item['key']!r}: VendorFacts(")
        for name in FIELDS:
            value = item[name]
            if name == "known_efforts" and value is not None:
                value = tuple(value)
            if name == "shutdown" and value is not None:
                value = "ShutdownFacts(" + ", ".join(f"{k}={value[k]!r}" for k in ("graceful", "grace_s", "defer_master_close")) + ")"
                lines.append(f"        {name}={value},")
            elif name == "capabilities":
                inner = ", ".join(f"{k}={value[k]!r}" for k in CAPABILITIES)
                lines.append(f"        {name}=CapabilityStatus({inner}),")
            else:
                lines.append(f"        {name}={value!r},")
        lines.append("    ),")
    lines.append("}")
    lines += ["", "#: Non-vendor subjects (the ordinary shell) and how their launch is composed.",
              f"SUBJECT_COMPOSITION: dict[str, str] = {{{', '.join(f'{a!r}: {subjects['composition']!r}' for a in subjects['aliases'])}}}"]
    return "\n".join(lines) + "\n"


def typescript_source(facts: list[dict]) -> str:
    table = {item["key"]: {name: item[name] for name in TS_FIELDS} for item in facts}
    helper = ("\n/** Put a session id into a declared `resume_args` template. */\n"
              "export const fillId = (template: string, id: string): string => template.split('{id}').join(id)\n")
    return f"// {HEADER}\nexport const VENDOR_FACTS = " + json.dumps(table, indent=2) + " as const\n" + helper


def rust_string(value: str) -> str:
    return json.dumps(value)


def rust_option(value: str | None) -> str:
    return "None" if value is None else f"Some({rust_string(value)})"


def rust_source(facts: list[dict], subjects: dict) -> str:
    variants = {"integrated": "Integrated", "vendor-unsupported": "VendorUnsupported",
                "not-yet-integrated": "NotYetIntegrated", "unknown": "Unknown"}
    lines = [
        f"// {HEADER}",
        "#![allow(dead_code)]",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Status {",
        *(f"    {variant}," for variant in variants.values()),
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct Shutdown {",
        "    pub graceful: bool,",
        "    pub grace_ms: u64,",
        "    pub defer_master_close: bool,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct Capabilities {",
        *(f"    pub {name}: Status," for name in CAPABILITIES),
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub enum Composition {",
        "    Rust,",
        "    Python,",
        "}",
        "",
        "#[derive(Clone, Copy, Debug, PartialEq, Eq)]",
        "pub struct VendorFacts {",
        "    pub key: &'static str,",
        "    pub composition: Composition,",
        "    pub command: &'static str,",
        "    pub model_flag: Option<&'static str>,",
        "    pub effort_flag: Option<&'static str>,",
        "    pub known_efforts: Option<&'static [&'static str]>,",
        "    pub permission_flag: Option<&'static str>,",
        "    pub resume_args: Option<&'static str>,",
        "    pub session_id_flag: Option<&'static str>,",
        "    pub login_args: Option<&'static str>,",
        "    pub shutdown: Option<Shutdown>,",
        "    pub capabilities: Capabilities,",
        "}",
        "",
        "pub const VENDORS: &[VendorFacts] = &[",
    ]
    for item in facts:
        shutdown = item["shutdown"]
        if shutdown is None:
            shutdown_text = "None"
        else:
            shutdown_text = ("Some(Shutdown { graceful: %s, grace_ms: %d, defer_master_close: %s })" % (
                str(shutdown["graceful"]).lower(), round(shutdown["grace_s"] * 1000),
                str(shutdown["defer_master_close"]).lower()))
        capabilities = ", ".join(f"{name}: Status::{variants[item['capabilities'][name]]}" for name in CAPABILITIES)
        lines += [
            "    VendorFacts {",
            f"        key: {rust_string(item['key'])},",
            f"        composition: Composition::{item['composition'].capitalize()},",
            f"        command: {rust_string(item['command'])},",
            f"        model_flag: {rust_option(item['model_flag'])},",
            f"        effort_flag: {rust_option(item['effort_flag'])},",
            "        known_efforts: " + ("None" if item["known_efforts"] is None else
                                        "Some(&[" + ", ".join(rust_string(v) for v in item["known_efforts"]) + "])") + ",",
            f"        permission_flag: {rust_option(item['permission_flag'])},",
            f"        resume_args: {rust_option(item['resume_args'])},",
            f"        session_id_flag: {rust_option(item['session_id_flag'])},",
            f"        login_args: {rust_option(item['login_args'])},",
            f"        shutdown: {shutdown_text},",
            f"        capabilities: Capabilities {{ {capabilities} }},",
            "    },",
        ]
    aliases = ", ".join(rust_string(alias) for alias in subjects["aliases"])
    lines += [
        "];",
        "",
        "pub fn find(key: &str) -> Option<&'static VendorFacts> {",
        "    VENDORS.iter().find(|facts| facts.key == key)",
        "}",
        "",
        "/// Non-vendor subjects (the ordinary shell): how their launch is composed.",
        f"pub const SUBJECT_ALIASES: &[&str] = &[{aliases}];",
        f"pub const SUBJECT_COMPOSITION: Composition = Composition::{subjects['composition'].capitalize()};",
    ]
    return "\n".join(lines) + "\n"


def outputs(root: Path = ROOT) -> dict[Path, str]:
    facts = load(root)
    subjects = load_subjects(root)
    return {
        PYTHON_OUTPUT: python_source(facts, subjects),
        TYPESCRIPT_OUTPUT: typescript_source(facts),
        RUST_OUTPUT: rust_source(facts, subjects),
    }


def write_all(root: Path = ROOT) -> None:
    for relative, content in outputs(root).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def stale(root: Path = ROOT) -> list[Path]:
    return [relative for relative, content in outputs(root).items()
            if not (root / relative).exists() or (root / relative).read_text() != content]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.check:
        names = [str(path) for path in stale(args.root)]
        if names:
            sys.exit("Stale generated agent CLI facts (run scripts/generate-agent-cli-facts.py): " + ", ".join(names))
    else:
        write_all(args.root)


if __name__ == "__main__":
    main()
