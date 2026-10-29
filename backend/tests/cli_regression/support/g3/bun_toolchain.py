"""The G3 Bun toolchain identity (rf4c D2, D3): `{bun, python, sidecar}` instead of the G1-E `{node, pnpm, python, sidecar}`.

The G1-E toolchain module (`g1e/toolchain.py`, pinned bytes) records and compares node and pnpm under `mise exec node@22.23.2 pnpm@10.0.0`. The user's rule is plain default Bun and nothing else
(no version pin, no Node, no pnpm), so G3 records Bun by real path, version and SHA-256, compares it before every attempt exactly as G1-E compared node, and takes drift as an invalid attempt
(`toolchain-mismatch`, a stop under the frozen stop rule). The G1-E bytes stay frozen: this module is the G3 side, and `hosted_seams` installs it over the G1-E functions that G3 reaches
(`tc.observe`, `tc.compare`, `tc.check`, `tc.runner_in`, `summarise.toolchain_record_problems`, `summarise.check_record_problems`) so that a G1-E code path run inside a G3 process (a reader run,
`summarise.trial_problems`) sees the same key set.

`runner` is a callable `argv -> stdout text`, as in G1-E; the tests inject a fake. Bun is resolved with `which` (injectable), never through a shell, so the same code runs on Windows. Nothing here prints an
environment value; only resolved paths, versions and digests are kept.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from tests.cli_regression.support.g1e import summarise, toolchain as tc

TOOLS = ("bun", "python")
IDENTITY_FIELDS = tc.IDENTITY_FIELDS
SIDECAR_FIELDS = tc.SIDECAR_FIELDS
STOP_REASON = tc.STOP_REASON
KEYS = frozenset({*TOOLS, "sidecar"})
Runner = tc.Runner


def translating_runner(*, which: Callable[[str], str | None] = shutil.which, call: Callable[[list[str]], str] | None = None, cwd: Path | None = None) -> Runner:
    """A runner that needs no shell: the one G1-E shape that is not an executable path (`sh -c "command -v uv"`, uv's informational record) is answered with `which`; every other argv is run as it is."""
    call = call or (lambda argv: tc.default_runner(argv, cwd))

    def run(argv: list[str]) -> str:
        if len(argv) == 3 and argv[:2] == ["sh", "-c"] and argv[2].startswith("command -v "):
            name = argv[2][len("command -v "):].strip()
            found = which(name)
            if not found:
                raise RuntimeError(f"{name} does not resolve on PATH")
            return found + "\n"
        return call(argv)
    return run


def runner_in(root: Path) -> Runner:
    """The replacement of `tc.runner_in`: commands run from the repository root (bun reads `bunfig.toml` there)."""
    return translating_runner(cwd=Path(root))


def observe_bun(runner: Runner, which: Callable[[str], str | None] = shutil.which) -> dict:
    """The file `bun` resolves to on PATH (real path after every symlink), its `--version` and its SHA-256. `bunx` is the same file under another name, so it needs no record of its own."""
    found = which("bun")
    if not found:
        raise RuntimeError("bun does not resolve on PATH")
    return {**tc.file_identity(found), "version": tc.last_line(runner([found, "--version"]))}


def observe(root: Path, sidecar: str | Path | None, runner: Runner | None = None, *, which: Callable[[str], str | None] = shutil.which) -> dict:
    """The identities that are checked before an attempt: bun and the backend venv python (triples) and, when a sidecar is given, its (real path, SHA-256)."""
    runner = runner or runner_in(root)
    out = {"bun": observe_bun(runner, which), "python": tc.observe_python(Path(root), runner)}      # `tc.observe_python` by attribute: the Windows seam swaps in the `Scripts\python.exe` layout
    if sidecar is not None:
        out["sidecar"] = tc.file_identity(sidecar)
    return out


def compare(recorded: dict, observed: dict) -> list[dict]:
    """Every difference between the recorded (F0) identities and the observed ones, as [{tool, field, recorded, observed}]. An identity that is absent on either side is a difference."""
    mismatches = []
    for tool, fields in (("bun", IDENTITY_FIELDS), ("python", IDENTITY_FIELDS), ("sidecar", SIDECAR_FIELDS)):
        if tool == "sidecar" and tool not in observed and tool not in recorded:
            continue
        want, got = recorded.get(tool), observed.get(tool)
        for field in fields:
            a = want.get(field) if isinstance(want, dict) else None
            b = got.get(field) if isinstance(got, dict) else None
            if a is None or b is None or a != b:
                mismatches.append({"tool": tool, "field": field, "recorded": a, "observed": b})
    return mismatches


def check(root: Path, recorded: dict, sidecar: str | Path | None, runner: Runner | None = None, *, which: Callable[[str], str | None] = shutil.which) -> dict:
    """The per-attempt check: observe, compare with F0's record, return the record that goes into the attempt's receipt.
    {recorded, observed, match, mismatches, uv}; `match` is True only when `mismatches` is empty. uv is informational and never part of the match."""
    runner = runner or runner_in(root)
    observed = observe(root, sidecar, runner, which=which)
    scope = {k: v for k, v in recorded.items() if k in TOOLS}
    if sidecar is not None and isinstance(recorded.get("sidecar"), dict):
        scope["sidecar"] = {k: recorded["sidecar"].get(k) for k in SIDECAR_FIELDS}      # the ready-line version is checked on the Rust-arm attempt itself
    mismatches = compare(scope, observed)
    try:
        uv = tc.observe_uv(runner)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        uv = {"realPath": None, "version": None, "sha256": None}
    return {"recorded": scope, "observed": observed, "match": not mismatches, "mismatches": mismatches, "uv": uv}


def record_for_freeze(root: Path, sidecar: str | Path, ready_version: str, runner: Runner | None = None) -> tuple[dict, dict]:
    """(toolchain, uv) for F0: the identities (the sidecar with the `ready` version of the one probe) and uv's informational identity."""
    runner = runner or runner_in(root)
    observed = observe(root, sidecar, runner)
    observed["sidecar"] = {**observed["sidecar"], "readyVersion": ready_version}
    return observed, tc.observe_uv(runner)


# ------------------------------------------------------------------------------------------------------------------------ the summarise side
def toolchain_record_problems(toolchain: Any, uv: Any, sidecar_sha256: str) -> list[str]:
    """`summarise.toolchain_record_problems` for F0's Bun record: exactly `{bun, python, sidecar}`, the first two triples (real path, version, SHA-256)."""
    triple = lambda x: isinstance(x, dict) and set(x) == {"realPath", "version", "sha256"} and summarise._text(x["realPath"]) and summarise._text(x["version"]) and summarise._hex(x["sha256"])
    if not (isinstance(toolchain, dict) and set(toolchain) == KEYS and all(triple(toolchain[k]) for k in TOOLS)):
        return ["freeze.toolchain does not record bun and python as (real path, version, SHA-256) and the sidecar"]
    out = []
    sidecar = toolchain["sidecar"]
    if not (isinstance(sidecar, dict) and set(sidecar) == {"realPath", "sha256", "readyVersion"} and summarise._text(sidecar["realPath"]) and summarise._hex(sidecar["sha256"]) and summarise._text(sidecar["readyVersion"])):
        out.append("freeze.toolchain.sidecar is not (real path, SHA-256, ready-line version)")
    elif sidecar["sha256"] != sidecar_sha256:
        out.append("freeze.toolchain.sidecar.sha256 differs from freeze.sidecarSha256")
    if not (isinstance(uv, dict) and set(uv) == {"realPath", "version", "sha256"} and all(v is None or isinstance(v, str) for v in uv.values())):
        out.append("freeze.uv is not the informational (real path, version, SHA-256) record")
    return out


def check_record_problems(record: Any, freeze: dict, extra_keys: frozenset | set = frozenset()) -> list[str]:
    """`summarise.check_record_problems` for the Bun record: the recorded identities are F0's, the observed ones equal them (RECOMPUTED here, the declared `match` is never trusted), and the informational
    uv record is present. Used for every attempt receipt and every reader run."""
    if not isinstance(record, dict) or set(record) != summarise.CHECK_KEYS | set(extra_keys):
        return ["the toolchain identity record is missing or malformed (V7 item 2)"]
    frozen = freeze["toolchain"]
    out = []
    scope = {k: frozen[k] for k in TOOLS} | {"sidecar": {"realPath": frozen["sidecar"]["realPath"], "sha256": frozen["sidecar"]["sha256"]}}
    if record["recorded"] != scope:
        out.append("the toolchain record names other recorded identities than F0's")
    observed = record["observed"]
    if not isinstance(observed, dict):
        return out + ["the toolchain record has no observed identities"]
    if compare(scope, observed) or record["match"] is not True or record["mismatches"] != []:
        out.append("toolchain-mismatch: an observed identity differs from F0's, or the match was not recorded")
    uv = record["uv"]
    if not (isinstance(uv, dict) and set(uv) == {"realPath", "version", "sha256"} and all(v is None or isinstance(v, str) for v in uv.values())):
        out.append("the uv record is malformed (informational, but it must be present)")
    return out
