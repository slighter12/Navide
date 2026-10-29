"""The G1-E evidence bundle: a directory of files read ONLY through a SHA-256 manifest (E2 v10 section 12, "obligations of the future E4 decider").

The caller pins the manifest's own SHA-256 (the receipts manifest the manager writes immediately after the matrix). Every file the decider
consumes must be listed in the manifest with its hash and must hash to it when read; a file that is listed but absent, that hashes differently, that
is not listed, or whose path leaves the bundle is a problem (`BundleError` for a hard failure on read; `problems()` lists the rest). Nothing is read
from outside the bundle and no file is read twice with a different result: contents are cached by their verified hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

MANIFEST_NAME = "bundle-manifest.json"
MANIFEST_SCHEMA = "acr-g1e-bundle/1"
SAFE_PATH = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BundleError(Exception):
    """The bundle cannot be trusted for a read: a missing, tampered or unlisted file, or a manifest that is not the pinned one."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Bundle:
    def __init__(self, root: Path, manifest_sha256: str) -> None:
        self.root = Path(root)
        if not isinstance(manifest_sha256, str) or not HEX64.match(manifest_sha256):
            raise BundleError("the pinned manifest SHA-256 is missing or not a 64-hex digest")
        self.manifest_path = self.root / MANIFEST_NAME
        try:
            raw = self.manifest_path.read_bytes()
        except OSError as error:
            raise BundleError(f"manifest unreadable: {type(error).__name__}") from None
        if sha256_bytes(raw) != manifest_sha256:
            raise BundleError("the manifest does not hash to the pinned SHA-256 (a replaced or edited manifest)")
        self.manifest_sha256 = manifest_sha256
        try:
            manifest = json.loads(raw)
        except ValueError:
            raise BundleError("manifest is not valid JSON") from None
        if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA or set(manifest) != {"schema", "files"}:
            raise BundleError("manifest has the wrong schema or keys")
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise BundleError("manifest lists no files")
        for name, digest in files.items():
            if not isinstance(name, str) or not SAFE_PATH.match(name) or ".." in name.split("/") or name == MANIFEST_NAME:
                raise BundleError(f"manifest path {name!r} is not a plain relative bundle path")
            if not isinstance(digest, str) or not HEX64.match(digest):
                raise BundleError(f"manifest digest of {name!r} is not a 64-hex digest")
        self.files: dict[str, str] = dict(files)
        self._cache: dict[str, bytes] = {}

    # -- reads ---------------------------------------------------------------------------------------------------------------
    def names(self) -> list[str]:
        return sorted(self.files)

    def exists(self, name: str) -> bool:
        return name in self.files

    def digest(self, name: str) -> str:
        if name not in self.files:
            raise BundleError(f"{name!r} is not listed in the manifest")
        return self.files[name]

    def read(self, name: str) -> bytes:
        if name not in self.files:
            raise BundleError(f"{name!r} is not listed in the manifest")
        if name in self._cache:
            return self._cache[name]
        path = self.root / name
        try:
            if path.is_symlink() or not path.is_file():
                raise BundleError(f"{name!r} is not a regular file")
            data = path.read_bytes()
        except OSError as error:
            raise BundleError(f"{name!r} unreadable: {type(error).__name__}") from None
        if sha256_bytes(data) != self.files[name]:
            raise BundleError(f"{name!r} does not hash to its manifest digest")
        self._cache[name] = data
        return data

    def read_json(self, name: str):
        try:
            return json.loads(self.read(name).decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise BundleError(f"{name!r} is not valid JSON") from None

    def read_jsonl(self, name: str) -> list:
        rows = []
        for number, line in enumerate(self.read(name).decode("utf-8", "strict").split("\n"), 1):
            if not line:
                continue
            try:
                rows.append(json.loads(line, parse_constant=_reject_constant))
            except ValueError:
                raise BundleError(f"{name!r} line {number} is not valid JSON") from None
        return rows

    # -- completeness ----------------------------------------------------------------------------------------------------------
    def problems(self) -> list[str]:
        """Files on disk that the manifest does not list, and listed files that are missing or tampered (read-only; each file is hashed)."""
        found: list[str] = []
        on_disk = set()
        for directory, _dirs, filenames in os.walk(self.root):
            for filename in filenames:
                relative = (Path(directory) / filename).relative_to(self.root).as_posix()
                if relative != MANIFEST_NAME:
                    on_disk.add(relative)
        for name in sorted(on_disk - set(self.files)):
            found.append(f"unlisted file {name}")
        for name in sorted(set(self.files) - on_disk):
            found.append(f"listed file {name} is missing")
        for name in sorted(set(self.files) & on_disk):
            try:
                self.read(name)
            except BundleError as error:
                found.append(str(error))
        return found

    def resolves(self, ref: object) -> bool:
        """True iff `ref` is a {path, sha256} reference to a manifested file with exactly that digest."""
        return (isinstance(ref, dict) and set(ref) == {"path", "sha256"} and isinstance(ref["path"], str)
                and self.files.get(ref["path"]) == ref["sha256"] and isinstance(ref["sha256"], str) and bool(HEX64.match(ref["sha256"])))


def _reject_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name}")


def write_manifest(root: Path) -> str:
    """Test/producer helper: list every file under `root` (except the manifest) with its SHA-256 and write the manifest; returns its SHA-256."""
    files = {}
    for directory, _dirs, filenames in os.walk(root):
        for filename in sorted(filenames):
            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST_NAME:
                files[relative] = sha256_bytes(path.read_bytes())
    raw = json.dumps({"schema": MANIFEST_SCHEMA, "files": dict(sorted(files.items()))}, indent=1, sort_keys=True).encode("utf-8") + b"\n"
    (Path(root) / MANIFEST_NAME).write_bytes(raw)
    return sha256_bytes(raw)
