"""POSIX keys for the g1e pin check on Windows (rehearsal fix round 1). Standard library only: `hosted_preflight.py` loads it by path, before anything is installed.

`pins.pinned_paths` builds the g1e package keys with `str(p.relative_to(root))`, which is backslash-separated on Windows while the manifest keys are POSIX, so every package file looked unpinned.
`posix_keys(original)` wraps the unchanged function and returns its keys with forward slashes; what is pinned, the manifest and the comparison are not touched. On a POSIX runner the wrapper
returns the very same strings, so it is installed on Windows only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def posix_keys(original: Callable[[Path], list[str]]) -> Callable[[Path], list[str]]:
    """`original` (`pins.pinned_paths`) with every key as a POSIX path, same order."""
    def pinned_paths(root: Path) -> list[str]:
        return [key.replace("\\", "/") for key in original(root)]
    return pinned_paths
