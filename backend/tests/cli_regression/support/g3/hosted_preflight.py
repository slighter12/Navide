"""The two checks a hosted job runs first after the overlay, before anything is installed or built (review-fix round 1, finding 5). Standard library only, so it runs on the runner's own Python:

    hosted_preflight.py ROOT       prints {"problems": [...]}; exit 0 = every pinned g1e file has its pinned bytes AND the hosted inputs are the registered ones, 2 = problems
    hosted_preflight.py sqlite [BASE_PREFIX]     (rf4e G4: with BASE_PREFIX, also refuses unless `sys.base_prefix` is that directory, the unpacked pinned build; rf4f H5: and unless the compile stamp is the pinned build's) prints {"problems": [...]}; exit 0 = this interpreter is the uv-managed CPython 3.12.11 with the registered SQLite 3.49.1 (macOS: run with the venv python, before the claim)

* the g1e pins: `pins.verify_pins` over `g1e-pins.json` (the module is loaded by path, so no package import and no third-party module is touched);
* the hosted inputs: the hashes-only documents manifest and the approval excerpt at the ledger path, held to the same rules as `hosted_f0.manifest_problems` / `excerpt_problems`.
The registered digests and paths below repeat `f0.DOC_PATHS` / `f0.DOC_DIGESTS` (this file may not import them: `f0` pulls in the psutil-using harness); `test_g3h_fix1_workflows.py` pins that they are equal.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import sqlite3
import sys
from pathlib import Path

G3 = "backend/tests/cli_regression/support/g3"
PINS_REL, PIN_MANIFEST_REL, HOSTED_PINS_REL = f"{G3}/pins.py", f"{G3}/g1e-pins.json", f"{G3}/hosted_pins.py"
MANIFEST_REL = f"{G3}/hosted_inputs/documents-manifest.json"
LEDGER_REL = ".scratch/agent-cli-runtime-evaluation/progress.md"
MANIFEST_SCHEMA = "acr-g3h-documents-manifest/1"
DOC_PATHS = {"proposal": ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/prereg/g3-prereg-proposal.md",
             "v14r2": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v14-r2.md", "v10": ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v10.md",
             "addendum": ".scratch/agent-cli-runtime-evaluation/evidence/g2/10-final/rf4c/g3r6-hosted-addendum.md"}
DOC_DIGESTS = {"proposal": "d81ccb2897a1c652ef4fc968dbb6b133bc6d4db28075f820d2f040553efb6b43", "v14r2": "67ab288132cbab8fae90d7e7ab2ffc93ee56c8224f3eaa43777aae87dca710a8",
               "v10": "aa69dbcea6d4c6ee248e99f50102dd5924cdcc970fd878461b03fdbc75d9523e", "addendum": "31f9492127c8007f4c1bc6c5ebb9d98c2d03560cd3189ae44f4578950a66af73"}
APPROVALS_REL = f"{G3}/hosted_approvals.py"      # the one list of approval labels and the excerpt rule (rf4c D15); stdlib only, loaded by path like the pins


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approvals(root: Path):
    return load("g3_hosted_approvals", Path(root) / APPROVALS_REL)


def pin_problems(root: Path, windows: bool | None = None) -> list[str]:
    """`windows` (default: this interpreter is Windows) wraps `pinned_paths` so the g1e package keys are POSIX like the manifest's (`hosted_pins.py`, loaded by path as well)."""
    try:
        module = load("g3_pins_preflight", Path(root) / PINS_REL)
        if sys.platform == "win32" if windows is None else windows:
            module.pinned_paths = load("g3_hosted_pins", Path(root) / HOSTED_PINS_REL).posix_keys(module.pinned_paths)
        manifest = json.loads((Path(root) / PIN_MANIFEST_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError, ImportError) as error:
        return [f"the g1e pin check cannot run: {error.__class__.__name__}"]
    return module.verify_pins(Path(root), manifest)


def manifest_problems(manifest: object) -> list[str]:
    if not (isinstance(manifest, dict) and set(manifest) == {"schema", "documents"} and manifest["schema"] == MANIFEST_SCHEMA and isinstance(manifest["documents"], dict)):
        return ["the manifest is not an acr-g3h-documents-manifest/1 record"]
    documents = manifest["documents"]
    if set(documents) != set(DOC_PATHS):
        return [f"the manifest names {sorted(documents)}, not {sorted(DOC_PATHS)}"]
    out = []
    for name, record in documents.items():
        if not (isinstance(record, dict) and set(record) == {"path", "sha256"}):
            out.append(f"{name}: not {{path, sha256}} (a manifest holds hashes only)")
        elif record["path"] != DOC_PATHS[name]:
            out.append(f"{name}: path {record['path']!r} is not the registered {DOC_PATHS[name]!r}")
        elif record["sha256"] != DOC_DIGESTS[name]:
            out.append(f"{name}: hashes to {str(record['sha256'])[:12]}..., not the registered {DOC_DIGESTS[name][:12]}...")
    return out


def excerpt_problems(text: str, root: Path | None = None) -> list[str]:
    """The excerpt rule of `hosted_approvals` (the same module `hosted_f0` uses), loaded from `root` (default: this file's own tree, three levels above `g3/`)."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[5]
    return approvals(base).excerpt_problems(text, DOC_DIGESTS["proposal"])


def input_problems(root: Path) -> list[str]:
    out = []
    try:
        out += manifest_problems(json.loads((Path(root) / MANIFEST_REL).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        out.append(f"the documents manifest is missing or unreadable at {MANIFEST_REL}")
    try:
        out += [f"approval excerpt: {p}" for p in excerpt_problems((Path(root) / LEDGER_REL).read_text(encoding="utf-8"), root)]
    except OSError:
        out.append(f"the approval excerpt is not in place at {LEDGER_REL}")
    except (ImportError, AttributeError, FileNotFoundError) as error:
        out.append(f"the approval rule cannot be loaded: {error.__class__.__name__}")
    return out


def problems(root: Path) -> list[str]:
    return pin_problems(root) + input_problems(root)


REGISTERED_SQLITE_VERSION = "3.49.1"      # the SQLite library behind the registered opencode reader fixture digest (`g3.fixtures.REGISTERED_SHA256["opencode"]`): its database header (bytes 96-99) records the library version
HARNESS_PYTHON = "3.12.11"      # the uv-managed CPython the local tree runs (`backend/.venv/pyvenv.cfg`); its bundled SQLite is the registered one (checked by `test_g3h_rf4d.py`, never taken from memory)
# rf4e G4: the exact python-build-standalone build behind that interpreter, which the macOS jobs install (sha256 checked before use) instead of letting `setup-uv` resolve "3.12.11" to whatever build the newest uv
# maps it to. The local interpreter reports `3.12.11 (main, Aug 18 2025, 19:02:39) [Clang 20.1.4]` with SQLite 3.49.1; release 20250818 is the one that built 3.12.11 that day. uv 0.13.7 maps 3.12.11 to
# release 20251007, a different build. The sha256 is the one in the release's published SHA256SUMS (OUT/rf4e/g4-download/SHA256SUMS-20250818).
# rf4f H5: the local interpreter IS that archive. The archive was downloaded once, its sha256 matched this pin, and the local uv install's libpython3.12.dylib equals the archive's byte for byte from the first
# section to the code signature (16,300,816 bytes, the statically linked SQLite included); only the Mach-O load commands (uv's install-name patch) and the ad-hoc signature differ (OUT/rf4f/g4/diff-dylib.json).
# `stamp` is the build's compile stamp (`platform.python_build()[1]`), which the runner checks before the claim beside the archive sha256, the base prefix and the SQLite version.
HARNESS_PYTHON_BUILD = {"release": "20250818", "compiled": "Aug 18 2025", "stamp": "Aug 18 2025 19:02:39", "sqlite": REGISTERED_SQLITE_VERSION,
                        "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20250818/cpython-3.12.11%2B20250818-aarch64-apple-darwin-install_only_stripped.tar.gz",
                        "sha256": "bbf0c85d09a8173e50d18a0198f14d1de91eab17a593ccf9445f214fb0555547"}


def sqlite_problems(version: str | None = None, python: tuple | None = None, stamp: str | None = None) -> list[str]:
    """Empty = the interpreter this runs under bundles the SQLite the registered opencode fixture digest needs AND was compiled at the pinned build's stamp. A hosted macOS job runs this with the harness
    interpreter (the uv-managed CPython of the venv, not `setup-python`) before the claim, so a different SQLite or another build stops it with nothing written; the reader-fixture build would otherwise
    fail its registered digest."""
    found = sqlite3.sqlite_version if version is None else version
    out = [] if found == REGISTERED_SQLITE_VERSION else [f"this interpreter's sqlite3 is {found}, not the {REGISTERED_SQLITE_VERSION} behind the registered opencode fixture digest"]
    have = tuple(sys.version_info[:3]) if python is None else tuple(python)
    out += [] if ".".join(map(str, have)) == HARNESS_PYTHON else [f"this interpreter is CPython {'.'.join(map(str, have))}, not the uv-managed {HARNESS_PYTHON} the harness runs on"]
    compiled = platform.python_build()[1] if stamp is None else stamp
    return out + ([] if compiled == HARNESS_PYTHON_BUILD["stamp"] else [f"this interpreter was compiled at {compiled!r}, not at the pinned build's {HARNESS_PYTHON_BUILD['stamp']!r}"])


def base_prefix_problems(expected: str, base_prefix: str | None = None) -> list[str]:
    """Empty = the interpreter's installation (`sys.base_prefix`) is the directory the pinned build was unpacked into (rf4e G4), so the venv really runs on that build and not on another one."""
    have = os.path.realpath(sys.base_prefix if base_prefix is None else base_prefix)
    return [] if have == os.path.realpath(expected) else [f"this interpreter's installation is {have}, not the pinned build unpacked at {os.path.realpath(expected)}"]


def main(argv: list[str]) -> int:
    if argv[:1] == ["sqlite"] and len(argv) in (1, 2):
        found = sqlite_problems() + (base_prefix_problems(argv[1]) if len(argv) == 2 else [])
        print(json.dumps({"problems": found, "sqliteVersion": sqlite3.sqlite_version, "python": sys.version.split()[0], "registered": REGISTERED_SQLITE_VERSION, "basePrefix": os.path.realpath(sys.base_prefix),
                          "build": sys.version.split("(", 1)[-1].split(")", 1)[0]}))
        return 0 if not found else 2
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 64
    found = problems(Path(argv[0]).resolve())
    print(json.dumps({"problems": found}))
    return 0 if not found else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
