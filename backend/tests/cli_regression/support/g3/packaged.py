"""The packaged cold-start row (U7, review-1 finding 10): descriptive only, never a decider row, no margin, no status. Predeclared BEFORE F0.

Layout (`electron-builder --dir`, as `build.sh` produces it): `<app>/Contents/Resources/bin/agent_team_backend` (the PyInstaller-frozen backend) beside `navide-cli-runtime` (the staged runtime).
The runtime staged into `build/cli-runtime/navide-cli-runtime` BEFORE packaging is a byte copy of the F0 sidecar (`stage_runtime`), so the packaged artifact is the same runtime the cost rows measure.
Identity binding to H (the same rule as section 1.1): `sidecar-build.json` names H and the sidecar digest; `packaged-build.json` (`record`) names H, the frozen backend digest and build time, and the
packaged runtime digest, which must equal F0's sidecar digest; the backend build time must not predate the last commit touching measured code. `identity_problems` returns what breaks that rule.
The measurement itself is `tests/cli/g3Packaged.ts` (cold start of the frozen backend to the first usable shell, 1 warm-up + 10 scored); `summary` reports it descriptively.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
from pathlib import Path

from tests.cli_regression.support.g3 import rows

SCHEMA = "acr-g3-packaged-build/1"
OUTPUT_REL = ".scratch/agent-cli-runtime-evaluation/work/g3-packaged"       # electron-builder -c.directories.output (repo-relative; the plan passes it absolute)
APP_REL = f"{OUTPUT_REL}/mac-arm64/Navide.app"
BIN_REL = "Contents/Resources/bin"
BACKEND_NAME = "agent_team_backend"
RUNTIME_NAME = "navide-cli-runtime"
STAGE_REL = "build/cli-runtime"
ROW_ID = rows.PACKAGED_ID


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backend_path(repo: str | Path) -> Path:
    return Path(repo) / APP_REL / BIN_REL / BACKEND_NAME


def stage_runtime(repo: Path, sidecar: Path) -> Path:
    """Byte-copy F0's sidecar to where electron-builder's extraResources picks the runtime up. The staged bytes are the sidecar's, never a rebuild."""
    target = Path(repo) / STAGE_REL / RUNTIME_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sidecar, target)
    target.chmod(0o755)
    if _sha(target) != _sha(sidecar):
        raise ValueError("the staged runtime differs from the sidecar")
    return target


def record(repo: Path, head: str, sidecar: Path, out: Path) -> dict:
    """`packaged-build.json`: the packaged artifact set at H. Refuses a packaged runtime that is not the F0 sidecar's bytes."""
    app = Path(repo) / APP_REL
    backend, runtime = app / BIN_REL / BACKEND_NAME, app / BIN_REL / RUNTIME_NAME
    for path in (backend, runtime):
        if not path.is_file():
            raise FileNotFoundError(f"packaged artifact missing: {path}")
    if _sha(runtime) != _sha(sidecar):
        raise ValueError("the packaged runtime is not the F0 sidecar (different bytes)")
    built = backend.stat().st_mtime
    body = {"schema": SCHEMA, "head": head, "app": APP_REL, "backendSha256": _sha(backend), "runtimeSha256": _sha(runtime), "builtAtEpoch": int(built)}
    Path(out).write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return body


def identity_problems(rec: object, f0: dict, last_code_commit_ts: int | None) -> list[str]:
    """Empty = the packaged artifact is the one bound to H: same head, runtime digest == F0's sidecar digest, backend not older than the last commit touching measured code."""
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return ["no packaged-build record"]
    out = []
    if rec.get("head") != f0.get("head"): out.append("the packaged build names another head than F0")
    if rec.get("runtimeSha256") != (f0.get("sidecar") or {}).get("sha256"): out.append("the packaged runtime is not F0's sidecar")
    if not isinstance(rec.get("backendSha256"), str) or len(rec["backendSha256"]) != 64: out.append("the frozen backend digest is missing")
    if last_code_commit_ts is not None and not (isinstance(rec.get("builtAtEpoch"), (int, float)) and rec["builtAtEpoch"] >= last_code_commit_ts):
        out.append("the frozen backend predates the last commit touching measured code")
    return out


def artifact_problems(repo: str | Path, rec: object, sidecar_sha256: str, head: str, last_code_commit_ts: int | None) -> list[str]:
    """The packaged artifact AS IT IS NOW (review-3 finding 4; section 1.1): rehash the backend and the runtime in the app, compare both with the record, the runtime with the sidecar and the
    record's head with `head`, and read the backend's file time (not the recorded one) against the last commit touching measured code. Empty = the bytes about to be measured are the bound ones."""
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return ["no packaged-build record"]
    out = []
    bin_dir = Path(repo) / APP_REL / BIN_REL
    backend, runtime = bin_dir / BACKEND_NAME, bin_dir / RUNTIME_NAME
    for label, path, recorded in (("backend", backend, rec.get("backendSha256")), ("runtime", runtime, rec.get("runtimeSha256"))):
        if not path.is_file():
            out.append(f"the packaged {label} is missing")
        elif _sha(path) != recorded:
            out.append(f"the packaged {label} bytes differ from the packaged-build record")
    if runtime.is_file() and _sha(runtime) != sidecar_sha256: out.append("the packaged runtime is not the F0 sidecar (different bytes now)")
    if rec.get("head") != head: out.append("the packaged build names another head")
    if backend.is_file() and last_code_commit_ts is not None and backend.stat().st_mtime < last_code_commit_ts:
        out.append("the packaged backend predates the last commit touching measured code")
    return out


def _iso(epoch: object) -> str:
    from datetime import datetime, timezone
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def predates(epoch: object, last_code_commit_ts: int | None) -> bool:
    """Section 1.1 age check for ONE artifact: its own recorded build time is before the last commit touching measured code (same rule as `helper.artifact_identity`: no usable time counts as old)."""
    return last_code_commit_ts is not None and not (isinstance(epoch, (int, float)) and not isinstance(epoch, bool) and epoch >= last_code_commit_ts)


def link_probe(directory: Path) -> list[str]:
    """Pre-F0 probe (review-6): can this file system hard-link in `directory`? Uses two unique disposable names and removes both; touches nothing else."""
    import tempfile
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".g3-link-probe.", dir=directory)
    os.close(fd)
    source, target = Path(name), Path(name + ".link")
    try:
        os.link(source, target)
        return [] if source.stat().st_nlink == 2 else ["hard-link creation did not raise but the link count is not 2"]
    except OSError as error:
        return [f"hard-link creation failed in {directory}: {error}"]
    finally:
        source.unlink()
        if target.exists():
            target.unlink()


def artifact_entries(rec: object, build_record: dict | None = None, last_code_commit_ts: int | None = None) -> list[dict]:
    """The packaged artifacts for the outcome's `source.artifacts` (section 1.1: each artifact's digest and build time; review-5 finding 2). The frozen backend's time is the packaged record's; the
    packaged runtime is the F0 sidecar's bytes, so its build time is the sidecar build record's. Nothing here is measured; an absent record gives no entries."""
    if not isinstance(rec, dict):
        return []
    epoch = rec.get("builtAtEpoch")
    return [{"name": "agent_team_backend (frozen, packaged)", "sha256": rec.get("backendSha256"), "builtAt": _iso(epoch), "predatesCode": predates(epoch, last_code_commit_ts)},
            {"name": "navide-cli-runtime (packaged)", "sha256": rec.get("runtimeSha256"), "builtAt": (build_record or {}).get("builtAt", ""),
             "predatesCode": predates((build_record or {}).get("builtAtEpoch"), last_code_commit_ts)}]


def identity_status(problems: list[str]) -> str:
    """The registered `artifactIdentity` value of the packaged artifact (section 1.1, same vocabulary as `helper.artifact_identity`): `ok`, `predates-code` when the only problem is the backend's age,
    else `digest-mismatch`."""
    if not problems:
        return "ok"
    return "predates-code" if all("predates" in p for p in problems) else "digest-mismatch"


def merge_identity(*states: str) -> str:
    """The first failing identity wins (`digest-mismatch` before `predates-code`); `ok` only when every artifact is ok."""
    for wanted in ("digest-mismatch", "predates-code"):
        if wanted in states:
            return wanted
    return "ok"


def summary(directory: Path | None, identity: list[str]) -> dict:
    """The descriptive report field: the ten scored cold-start values and their order statistics. No margin, no status, no pair, no decider input. A missing run is `not measured`."""
    base = {"row": ROW_ID, "decisionBearing": False, "identityProblems": identity}
    run = Path(directory) / "run.json" if directory else None
    if run is None or not run.is_file():
        return {**base, "status": "not measured", "values": []}
    values = []
    for index in range(10):
        path = Path(directory) / f"trial-{index}.json"
        receipt = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        value = receipt.get("coldToFirstUsableMs") if receipt.get("passed") is True and not receipt.get("warmup") and not receipt.get("smoke") else None
        values.append(value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None)
    valid = [v for v in values if v is not None]
    return {**base, "status": "measured" if valid else "no valid attempt", "values": values, "n": len(valid),
            "median": statistics.median(valid) if valid else None, "min": min(valid) if valid else None, "max": max(valid) if valid else None}
