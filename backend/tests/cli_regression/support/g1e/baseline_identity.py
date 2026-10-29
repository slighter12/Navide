"""Python-arm identity (E2 v13 V5, replacing v10 section 9 "Baseline identity (C8)"): the baseline reference manifest, the loaded-bytes comparison and the listing.

`trace_python_arm()` imports the backend application module in a fresh interpreter with the runtime selector unset (no startup handler runs: an import only) and lists every
loaded module whose file lies inside `backend/agent_team_backend` (V5 item 1). From that list:

* `e3_set()` (item 2): the traced files whose content at the E3 commit differs from its parent, files E3 added included;
* `reference_manifest()` (item 3): per traced file, the reference content is the file at the E3 parent when it is in `E3set` (`absent` for an added file), otherwise the file at
  the last upstream merge `U` (`absent` if it does not exist there). The manifest is a per-file record (path, SHA-256 or `absent`, the revision the content comes from); no git
  object is created for it;
* `identity_record()` (item 4): each file's LOADED bytes (the working-tree content: HEAD plus any uncommitted change) are compared with its reference content; every file that
  differs is listed with the SHA-256 of its diff. Consequently the list holds all of `E3set`, any later evaluation-branch edit to a Python-arm file (committed or not) and any
  file an upstream merge after `U` would change; if nothing touches a Python-arm file after `U`, the list is exactly `E3set`.

This tool never assigns a verdict: it is a measurement of what changed. `classify()` (item 5) reads recorded reviewer verdicts, each given on the file's loaded bytes at F0.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

#: V5 vocabulary, fixed by the pre-registration: the last upstream merge `U`, the E3 commit and its parent.
UPSTREAM_MERGE = "7c2b13ad59fa6dd0d59984dbe2fd128acb1b2901"
E3_COMMIT = "840b9d930c433e1a7443ebb84ef3b06d465f0476"
E3_PARENT = "09bf4d8666212c84abab5deda6780541e3312f2c"
SELECTOR_ENV = "NAVIDE_ACR_EVAL_RUNTIME"
ABSENT = "absent"

_PROBE = r"""
import json, sys
import importlib
importlib.import_module(sys.argv[1])
package = sys.argv[2]
files = sorted({getattr(m, '__file__', None) for m in list(sys.modules.values()) if getattr(m, '__file__', None)})
print(json.dumps([f for f in files if f.startswith(package)]))
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def trace_python_arm(root: Path, python: str | None = None) -> list[str]:
    """Repository-relative paths of the in-repo modules loaded by importing `agent_team_backend.app` with the selector unset."""
    root = Path(root).resolve()
    package = str(root / "backend" / "agent_team_backend") + os.sep
    env = {k: v for k, v in os.environ.items() if k != SELECTOR_ENV}
    out = subprocess.run([python or sys.executable, "-c", _PROBE, "agent_team_backend.app", package], cwd=root / "backend", env=env, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"the Python-arm import trace failed: {out.stderr[-300:]}")
    return sorted(str(Path(p).resolve().relative_to(root)) for p in json.loads(out.stdout))


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True)


def resolve_revision(root: Path, revision: str) -> str:
    done = git(Path(root), "rev-parse", "--verify", f"{revision}^{{commit}}")
    if done.returncode != 0:
        raise RuntimeError(f"revision {revision} does not resolve to a commit")
    return done.stdout.decode().strip()


def content_at(root: Path, revision: str, path: str) -> bytes | None:
    """The bytes of `path` at `revision`, or None when the file does not exist there."""
    done = git(Path(root), "show", f"{revision}:{path}")
    return done.stdout if done.returncode == 0 else None


def loaded_bytes(root: Path, path: str) -> bytes | None:
    """What an attempt actually imports: the working-tree bytes (None when the file is not there)."""
    target = Path(root) / path
    return target.read_bytes() if target.is_file() else None


def python_arm_file_list(root: Path, files: list[str]) -> list[dict]:
    """`pythonArmFileList` entries: every traced file with the SHA-256 of its loaded bytes (`absent` for a traced file that is missing from the working tree)."""
    return [{"path": path, "sha256": (sha256_bytes(data) if (data := loaded_bytes(root, path)) is not None else ABSENT)} for path in files]


def e3_set(root: Path, files: list[str], parent: str = E3_PARENT, e3: str = E3_COMMIT) -> list[str]:
    """V5 item 2: the traced files whose content at `e3` differs from `parent`, including added files."""
    return sorted(path for path in files if content_at(root, parent, path) != content_at(root, e3, path))


def reference_manifest(root: Path, files: list[str], e3set: list[str], upstream: str = UPSTREAM_MERGE, parent: str = E3_PARENT) -> list[dict]:
    """V5 item 3: per traced file {path, sha256|absent, source}; `source` names the revision the reference content comes from (the E3 parent for `E3set`, `U` otherwise)."""
    entries = []
    for path in files:
        source = parent if path in set(e3set) else upstream
        data = content_at(root, source, path)
        entries.append({"path": path, "sha256": sha256_bytes(data) if data is not None else ABSENT, "source": source})
    return entries


def manifest_sha256(manifest: list[dict]) -> str:
    return sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def reference_content(root: Path, entry: dict) -> bytes | None:
    """The reference content of one manifest entry, taken from the revision it names and verified against the entry's digest (a mismatch means the manifest is not about this repository)."""
    data = content_at(root, entry["source"], entry["path"])
    digest = sha256_bytes(data) if data is not None else ABSENT
    if digest != entry["sha256"]:
        raise RuntimeError(f"{entry['path']}: the content at {entry['source'][:8]} does not match the manifest digest")
    return data


def diff_text(path: str, reference: bytes | None, loaded: bytes | None) -> str:
    """Deterministic unified diff of reference content against loaded bytes (an absent side is an empty file)."""
    a = (reference or b"").decode("utf-8", "replace").splitlines(keepends=True)
    b = (loaded or b"").decode("utf-8", "replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"reference/{path}", tofile=f"loaded/{path}"))


def identity_record(root: Path, files: list[str], e3set: list[str] | None = None, *, upstream: str = UPSTREAM_MERGE, parent: str = E3_PARENT, e3: str = E3_COMMIT) -> dict:
    """The V5 measurement: {upstreamMerge, e3Parent, e3Commit, e3Set, files, manifest, manifestSha256, changed}. `changed` is item 4's listing."""
    root = Path(root)
    upstream, parent, e3 = resolve_revision(root, upstream), resolve_revision(root, parent), resolve_revision(root, e3)
    e3set = e3_set(root, files, parent, e3) if e3set is None else sorted(e3set)
    manifest = reference_manifest(root, files, e3set, upstream, parent)
    listing = python_arm_file_list(root, files)
    changed = []
    for entry, loaded_entry in zip(manifest, listing):
        if entry["sha256"] == loaded_entry["sha256"]:
            continue
        reference, loaded = reference_content(root, entry), loaded_bytes(root, entry["path"])
        text = diff_text(entry["path"], reference, loaded)
        changed.append({"path": entry["path"], "status": "added" if reference is None else ("removed" if loaded is None else "modified"),
                        "inE3Set": entry["path"] in set(e3set), "loadedSha256": loaded_entry["sha256"], "referenceSha256": entry["sha256"],
                        "diffSha256": sha256_bytes(text.encode("utf-8")), "diff": text})
    return {"upstreamMerge": upstream, "e3Parent": parent, "e3Commit": e3, "e3Set": e3set, "files": listing, "manifest": manifest, "manifestSha256": manifest_sha256(manifest), "changed": changed}


def status_without_verdicts(record: dict) -> str:
    """`identical` iff nothing is listed; otherwise `changed` until the recorded verdicts satisfy `classify`."""
    return "identical" if not record["changed"] else "changed"


def classify(record: dict, verdicts: dict[str, list[dict]]) -> str:
    """V5 item 5. `verdicts[path]` = [{reviewer, session, report: {path, sha256}, verdict, loadedSha256}]. `refactor_reviewed` iff every listed file has two `behaviour-neutral` verdicts
    from distinct reviewers (distinct reviewer id, session id and report hash) given on the file's loaded bytes at F0; any other verdict or a missing second one is `changed`."""
    if not record["changed"]:
        return "identical"
    for entry in record["changed"]:
        given = verdicts.get(entry["path"], [])
        if len(given) < 2 or any(v.get("verdict") != "behaviour-neutral" or v.get("loadedSha256") != entry["loadedSha256"] for v in given):
            return "changed"
        for key in ("reviewer", "session"):
            values = [v.get(key) for v in given]
            if len(set(values)) != len(values):
                return "changed"
        reports = [(v.get("report") or {}).get("sha256") for v in given]
        if len(set(reports)) != len(reports):
            return "changed"
    return "refactor_reviewed"


VERDICTS_SCHEMA = "acr-g1e-reviewer-verdicts/1"


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode("utf-8")


def _verdict_problems(reviewers: list[dict], loaded: dict[str, str], listed: list[str]) -> list[str]:
    out = []
    for index, reviewer in enumerate(reviewers):
        doc = reviewer.get("verdicts")
        if not (isinstance(doc, dict) and doc.get("schema") == VERDICTS_SCHEMA and isinstance(doc.get("v5"), list)
                and all(isinstance(doc.get(k), str) and doc[k].strip() for k in ("reviewer", "session")) and isinstance(reviewer.get("report"), bytes) and reviewer["report"]):
            out.append(f"reviewer file {index} is not a {VERDICTS_SCHEMA} record with a reviewer, a session, a v5 list and a report")
            continue
        paths = [v.get("path") for v in doc["v5"] if isinstance(v, dict)]
        if sorted(paths) != listed or len(paths) != len(doc["v5"]):
            out.append(f"reviewer {doc['reviewer']}: the v5 verdicts are not exactly V5 item 4's listing at F0")
        for v in doc["v5"]:
            if isinstance(v, dict) and v.get("path") in loaded and v.get("loadedSha256") != loaded[v["path"]]:
                out.append(f"reviewer {doc['reviewer']}: {v['path']} was judged on other bytes than F0's loaded bytes")
            if isinstance(v, dict) and v.get("verdict") not in ("behaviour-neutral", "other"):
                out.append(f"reviewer {doc['reviewer']}: {v.get('path')} has the verdict {v.get('verdict')!r}")
    if len(reviewers) >= 2 and not out:
        for what, values in (("reviewer", [r["verdicts"]["reviewer"] for r in reviewers]), ("session", [r["verdicts"]["session"] for r in reviewers]),
                             ("report", [sha256_bytes(r["report"]) for r in reviewers])):
            if len(set(values)) != len(values):
                out.append(f"the verdict files do not come from distinct {what}s")
    return out


def compose(repo: Path, freeze: dict, reviewers: list[dict], put) -> dict:
    """The `baseline-identity.json` record (E2 v13 V5 item 5) from F0's Python-arm record and the reviewers' verdict files. `reviewers` = [{"verdicts": <acr-g1e-reviewer-verdicts/1>, "report": <bytes>}, ...];
    `put(name, data) -> {path, sha256}` stores one bundle file. The listing is item 4's (files whose F0 loaded bytes differ from their reference); every reviewer must judge exactly that listing on F0's
    loaded bytes, reviewers, sessions and report hashes must be distinct, and the diffs are taken from `repo`, which must be the F0 tree. The status is `classify`'s, never a producer's word."""
    arm = freeze["pythonArm"]
    loaded = {f["path"]: f["sha256"] for f in arm["fileList"]}
    reference = {m["path"]: m["sha256"] for m in arm["referenceManifest"]}
    listed = sorted(path for path in loaded if loaded[path] != reference[path])
    if listed and len(reviewers) < 2:
        raise ValueError("two verdict files from distinct reviewers are needed for a non-empty listing")
    problems = _verdict_problems(reviewers, loaded, listed) if listed else []
    if problems:
        raise ValueError("; ".join(problems))
    manifest = {m["path"]: m for m in arm["referenceManifest"]}
    changed, verdict_map = [], {}
    reports = [put(f"baseline/report-{''.join(ch if ch.isalnum() else '-' for ch in r['verdicts']['reviewer'])}.md", r["report"]) for r in reviewers] if listed else []
    for path in listed:
        data = loaded_bytes(repo, path)
        if (sha256_bytes(data) if data is not None else ABSENT) != loaded[path]:
            raise ValueError(f"{path}: the loaded bytes of the tree are not F0's: this is not the F0 tree")
        text = diff_text(path, reference_content(Path(repo), manifest[path]), data)
        verdicts = []
        for reviewer, report in zip(reviewers, reports):
            entry = next(v for v in reviewer["verdicts"]["v5"] if v["path"] == path)
            verdicts.append({"reviewer": reviewer["verdicts"]["reviewer"], "session": reviewer["verdicts"]["session"], "report": report, "verdict": entry["verdict"], "loadedSha256": entry["loadedSha256"]})
        verdict_map[path] = verdicts
        changed.append({"path": path, "loadedSha256": loaded[path], "referenceSha256": reference[path], "diff": put(f"baseline/diff-{path.rsplit('/', 1)[-1]}.diff", text.encode("utf-8")), "verdicts": verdicts})
    status = classify({"changed": [{"path": c["path"], "loadedSha256": c["loadedSha256"]} for c in changed]}, verdict_map)
    return {"status": status, "upstreamMerge": arm["upstreamMerge"], "e3Parent": arm["e3Parent"], "e3Commit": arm["e3Commit"], "e3Set": arm["e3Set"],
            "pythonArmFileList": put("baseline/python-arm-files.json", _json_bytes(arm["fileList"])), "referenceManifest": put("baseline/reference-manifest.json", _json_bytes(arm["referenceManifest"])),
            "changedFiles": changed}


def compose_into(repo: Path, f0_path: Path, bundle_dir: Path, pairs: list[tuple[Path, Path]]) -> dict:
    """Write `baseline-identity.json` and its `baseline/` files into `bundle_dir` (nothing is replaced: an existing record is an error); `pairs` = [(verdicts file, report file), ...]."""
    bundle_dir = Path(bundle_dir)
    target = bundle_dir / "baseline-identity.json"
    if target.exists():
        raise FileExistsError(f"{target} exists: refusing to overwrite")
    freeze = json.loads(Path(f0_path).read_text())
    reviewers = [{"verdicts": json.loads(Path(v).read_text()), "report": Path(r).read_bytes()} for v, r in pairs]

    def put(name: str, data: bytes) -> dict:
        path = bundle_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"{path} exists: refusing to overwrite")
        path.write_bytes(data)
        return {"path": name, "sha256": sha256_bytes(data)}

    record = compose(Path(repo), freeze, reviewers, put)
    target.write_bytes(_json_bytes(record))
    return record


def summary(record: dict) -> dict:
    return {"upstreamMerge": record["upstreamMerge"], "e3Parent": record["e3Parent"], "e3Commit": record["e3Commit"], "traced": len(record["files"]), "e3Set": record["e3Set"],
            "manifestSha256": record["manifestSha256"],
            "listed": [{k: v for k, v in c.items() if k != "diff"} for c in record["changed"]],
            "listedCount": len(record["changed"]), "statusWithoutVerdicts": status_without_verdicts(record)}


def main(argv: list[str]) -> int:
    """`baseline_identity.py [ROOT]`: the V5 listing of the real tree (diagnostics; assigns no verdict)."""
    root = Path(argv[0]).resolve() if argv else Path.cwd().parent
    traced = trace_python_arm(root)
    print(json.dumps(summary(identity_record(root, traced)), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
