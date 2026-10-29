"""The harness pack tool (addendum F1, design section 5 option O2). Standard library only, so the job can run it from the checked-out side branch with any Python 3.

    hosted_pack.py candidates REPO [--out FILE]                   the files the rules pick, before the allowlist (what a reviewer reads to write the list)
    hosted_pack.py list    REPO [--out FILE]                      the harness file list (repo-relative, sorted) chosen by the explicit rules below and held to the allowlist
    hosted_pack.py archive REPO OUT_TAR [--list FILE] [--out FILE] writes the deterministic archive and prints/records its SHA-256
    hosted_pack.py digest  DIR [--out FILE]                       the same archive digest, recomputed from a checked-out tree (every file under DIR but `.git`; a symlink is refused)
    hosted_pack.py tree    REPO --dry-run [--list FILE] [--out FILE]   the tree SHA of the orphan commit, with no object and no ref written
    hosted_pack.py tree    REPO [--list FILE] [--out FILE]        writes the blobs, the tree and ONE parentless commit; prints the commit SHA. It updates no ref and never checks anything out

Every command that packs (list, archive, tree) refuses any file the committed allowlist (`g3/hosted_pack_allowlist.txt`, `--allowlist FILE` to name another) does not name, any allowlisted file that is absent
and any symlink: the side branch is public, so what it carries is a closed, reviewed list and not a directory rule.

The archive is a USTAR tar of the entries sorted by path, with fixed mtime, uid, gid, mode and no user or group names, so it carries no host name and the same bytes always give the same digest. The tree
is `* -text` (a virtual `.gitattributes`), so a Windows checkout with any `core.autocrlf` gives the same bytes. The approval excerpt is stored at the ledger path (`LEDGER_DEST`), where the unchanged
`approval_problems` reads the ledger. Git is used through plumbing only (`hash-object`, a temporary index named by `GIT_INDEX_FILE` in this tool's own subprocess environment, `update-index --index-info`,
`write-tree`, `commit-tree`): the repository's own index, HEAD, branches and working tree are never touched.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

GITATTRIBUTES = ".gitattributes"
GITATTRIBUTES_BYTES = b"* -text\n"
EXCERPT_SRC = "backend/tests/cli_regression/support/g3/hosted_inputs/approval-excerpt.md"
LEDGER_DEST = ".scratch/agent-cli-runtime-evaluation/progress.md"
SUPPORT = "backend/tests/cli_regression/support/"
ALLOWLIST_REL = SUPPORT + "g3/hosted_pack_allowlist.txt"
RELATIVE_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")
INCLUDE_PREFIXES = (SUPPORT + "g1e/", SUPPORT + "g3/")
INCLUDE_FILES = (SUPPORT + "g1e_peer.py", SUPPORT + "g1e_counters_gate.py", SUPPORT + "backend_entry.py", SUPPORT + "baseline_peer.py", "tests/cli/g3-launch-plan-golden.json", "tests/cli/g3Matrix.test.ts",
                 "tests/fixtures/cli-regression/g1e-burst-golden.json", "tests/fixtures/cli-regression/g1e-ledger-golden.json", "tests/fixtures/cli-regression/g1e-schedule-golden.json",
                 "tests/fixtures/cli-regression/runtime-workload-g1e.json")
TS_PREFIXES = ("tests/cli/g1e", "tests/cli/g3")
EXCLUDE_PARTS = ("__pycache__", "/target/", "/node_modules/")
EXCLUDE_SUFFIXES = (".pyc", ".log")
FIXED_MTIME = 0
MODE = 0o644
IDENTITY = {"GIT_AUTHOR_NAME": "eval-harness", "GIT_AUTHOR_EMAIL": "eval-harness@example.invalid", "GIT_AUTHOR_DATE": "2026-10-29T00:00:00+0000",
            "GIT_COMMITTER_NAME": "eval-harness", "GIT_COMMITTER_EMAIL": "eval-harness@example.invalid", "GIT_COMMITTER_DATE": "2026-10-29T00:00:00+0000"}
COMMIT_MESSAGE = "eval-only harness for G3-r2-hosted. Never merge."

Git = Callable[..., bytes]


def run_git(repo: str | Path, *args: str, stdin: bytes | None = None, env: dict | None = None) -> bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False, env={**os.environ, **(env or {})})
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.decode('utf-8', 'replace')[:200]}")
    return done.stdout


def wanted(path: str) -> bool:
    """The explicit rules: g1e/ and g3/ of the harness support, the peer and counters files, the two tracked support files the harness extends (backend_entry, baseline_peer), the TS runners (not their
    unit tests, except the runner `g3Matrix.test.ts` itself), the goldens and the workload fixture. Build output and caches are never included."""
    if any(part in "/" + path for part in EXCLUDE_PARTS) or path.endswith(EXCLUDE_SUFFIXES):
        return False
    if path in INCLUDE_FILES or path.startswith(INCLUDE_PREFIXES):
        return True
    return path.startswith(TS_PREFIXES) and path.endswith(".ts") and not path.endswith(".test.ts")


def harness_files(repo: str | Path, git: Git = run_git) -> list[str]:
    """Untracked files plus tracked files modified against HEAD, filtered by `wanted`, sorted. The two lists are NUL-separated."""
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    modified = git(repo, "diff", "--name-only", "HEAD", "-z").decode("utf-8").split("\0")
    return sorted({path for path in untracked + modified if path and wanted(path)})


def valid_relative(path: str) -> bool:
    return bool(RELATIVE_PATH.fullmatch(path)) and not path.startswith("/") and ".." not in path.split("/")


def load_allowlist(path: str | Path) -> list[str]:
    """The closed, reviewed list of repo-relative harness files: one path per line, sorted, unique, a final newline, no comments or blank lines."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"the allowlist {path} cannot be read: {error.__class__.__name__}") from error
    if not text.endswith("\n"):
        raise ValueError("the allowlist must end with a newline")
    files = text.split("\n")[:-1]
    for line in files:
        if not valid_relative(line):
            raise ValueError(f"the allowlist holds a line that is not a repo-relative path: {line[:60]!r}")
    if len(set(files)) != len(files):
        raise ValueError("the allowlist has a duplicate path")
    if files != sorted(files, key=lambda item: item.encode("utf-8")):
        raise ValueError("the allowlist is not sorted")
    return files


def checked_files(repo: str | Path, git: Git = run_git, *, listing: str | Path | None = None, allowlist: str | Path | None = None) -> list[str]:
    """The harness files to pack, after the allowlist: what the rules (or an explicit `listing`) pick must equal the committed allowlist exactly. A file the list does not name, a listed file that is
    absent and a symlink all stop the pack; nothing is published that a reviewer did not approve."""
    allowed = load_allowlist(allowlist or Path(repo) / ALLOWLIST_REL)
    picked = [line for line in Path(listing).read_text(encoding="utf-8").split("\n") if line] if listing else harness_files(repo, git)
    extra = sorted(set(picked) - set(allowed))
    if extra:
        raise ValueError(f"not in the allowlist: {extra[:5]}")
    missing = sorted(set(allowed) - set(picked))
    if missing:
        raise ValueError(f"allowlisted but absent: {missing[:5]}")
    for relative in picked:
        if (Path(repo) / relative).is_symlink():
            raise ValueError(f"a symlink cannot be packed: {relative}")
    return sorted(picked, key=lambda item: item.encode("utf-8"))


def entries(repo: str | Path, files: list[str]) -> list[tuple[str, bytes]]:
    """(path in the archive, bytes) sorted by path: the listed files read byte-exactly, the excerpt stored at the ledger path, and the virtual `.gitattributes`."""
    out: dict[str, bytes] = {GITATTRIBUTES: GITATTRIBUTES_BYTES}
    for relative in files:
        if relative.startswith("/") or ".." in relative.split("/") or "\\" in relative:
            raise ValueError(f"not a repo-relative path: {relative!r}")
        if (Path(repo) / relative).is_symlink():
            raise ValueError(f"a symlink cannot be packed: {relative}")
        dest = LEDGER_DEST if relative == EXCERPT_SRC else relative
        if dest in out:
            raise ValueError(f"duplicate path {dest}")
        out[dest] = (Path(repo) / relative).read_bytes()
    return sorted(out.items(), key=lambda item: item[0].encode("utf-8"))


def read_tree(root: str | Path) -> list[tuple[str, bytes]]:
    """The same entries from a checked-out directory: every file under it except `.git`; a symlink is refused."""
    root = Path(root)
    found = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError(f"a symlink in the checkout: {relative}")
        if path.is_file():
            found[relative] = path.read_bytes()
    return sorted(found.items(), key=lambda item: item[0].encode("utf-8"))


def archive_bytes(items: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path, data in items:
            info = tarfile.TarInfo(path)
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname, info.type = len(data), FIXED_MTIME, MODE, 0, 0, "", "", tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def archive_digest(items: list[tuple[str, bytes]]) -> str:
    return hashlib.sha256(archive_bytes(items)).hexdigest()


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()      # noqa: S324 - git's object id, not a security hash


def tree_sha(items: list[tuple[str, bytes]]) -> str:
    """The git tree id of `items` (all mode 100644), computed without git: a dry run needs no repository and writes no object. Directories sort as `name/`, as git orders them."""
    root: dict = {}
    for path, data in items:
        node = root
        *folders, name = path.split("/")
        for folder in folders:
            node = node.setdefault(folder, {})
        node[name] = data

    def build(node: dict) -> str:
        rows = []
        for name, value in node.items():
            if isinstance(value, bytes):
                rows.append((name.encode("utf-8"), b"100644", blob_sha(value)))
            else:
                rows.append((name.encode("utf-8") + b"/", b"40000", build(value)))
        body = b"".join(mode + b" " + key.rstrip(b"/") + b"\0" + bytes.fromhex(sha) for key, mode, sha in sorted(rows, key=lambda row: row[0]))
        return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()      # noqa: S324
    return build(root)


def build_tree(repo: str | Path, items: list[tuple[str, bytes]], *, write: bool, git: Git = run_git) -> tuple[str, str | None]:
    """(tree SHA, commit SHA or None). `write=False` computes the tree id in Python: no git process, no object, no ref. `write=True` stores the blobs, the tree and ONE parentless commit through
    plumbing (a temporary index named by `GIT_INDEX_FILE` in this tool's own subprocess environment) and refuses to go on if git's tree differs from the computed one. No ref is updated either way."""
    expected = tree_sha(items)
    if not write:
        return expected, None
    with tempfile.TemporaryDirectory(prefix="g3h-pack-") as scratch:
        index = {"GIT_INDEX_FILE": str(Path(scratch).resolve() / "index")}
        lines = []
        for path, data in items:
            blob = git(repo, "hash-object", "--no-filters", "-w", "--stdin", stdin=data).decode().strip()
            lines.append(f"100644 {blob}\t{path}\n")
        git(repo, "update-index", "--add", "--index-info", stdin="".join(lines).encode("utf-8"), env=index)
        tree = git(repo, "write-tree", env=index).decode().strip()
    if tree != expected:
        raise RuntimeError(f"git wrote tree {tree}, the computed tree is {expected}")
    return tree, git(repo, "commit-tree", tree, "-m", COMMIT_MESSAGE, env=IDENTITY).decode().strip()


def _option(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def _emit(result: dict, args: list[str]) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    out = _option(args, "--out")
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text, end="")


def _files(repo: str, args: list[str], git: Git) -> list[str]:
    """The files to pack: the rules' pick (or `--list`) held to the allowlist (`--allowlist`, default the committed one)."""
    return checked_files(repo, git, listing=_option(args, "--list"), allowlist=_option(args, "--allowlist"))


def _allowlist_record(repo: str, args: list[str]) -> dict:
    path = _option(args, "--allowlist") or Path(repo) / ALLOWLIST_REL
    return {"allowlistFiles": len(load_allowlist(path)), "allowlistSha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "matchesAllowlist": True}      # `_files` already refused a mismatch


def main(argv: list[str], git: Git = run_git) -> int:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "candidates" and args:      # the rules' pick before the allowlist: what a reviewer reads to write or update the list
            _emit({"files": harness_files(os.path.abspath(args[0]), git)}, args)
            return 0
        if cmd == "list" and args:
            repo = os.path.abspath(args[0])
            _emit({"files": _files(repo, args, git), **_allowlist_record(repo, args)}, args)
            return 0
        if cmd == "archive" and len(args) >= 2:
            repo = os.path.abspath(args[0])
            files = _files(repo, args, git)
            items = entries(repo, files)
            data = archive_bytes(items)
            Path(args[1]).write_bytes(data)
            _emit({"archiveSha256": hashlib.sha256(data).hexdigest(), "entries": len(items), "bytes": len(data)}, args)
            return 0
        if cmd == "digest" and args:
            items = read_tree(os.path.abspath(args[0]))
            _emit({"archiveSha256": archive_digest(items), "entries": len(items)}, args)
            return 0
        if cmd == "tree" and args:
            repo = os.path.abspath(args[0])
            files = _files(repo, args, git)
            items = entries(repo, files)
            dry = "--dry-run" in args
            tree, commit = build_tree(repo, items, write=not dry, git=git)
            _emit({"dryRun": dry, "treeSha": tree, "commitSha": commit, "archiveSha256": archive_digest(items), "entries": len(items), "files": [path for path, _ in items], **_allowlist_record(repo, args)}, args)
            return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"{type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
