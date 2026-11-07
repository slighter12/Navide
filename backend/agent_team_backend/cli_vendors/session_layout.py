"""Where a CLI keeps its sessions, by the vendor's declaration (`shared/agent-cli/<vendor>.json`).

The generic core of three records: `session_root` (where the sessions start), `session_path` (one session's
transcript under it) and `logs` (the session files under it). The workspace spellings (`cwd` encodings) live here
too, dispatched through one `{CwdEncoding: encoder}` map, so the core imports no vendor module. A vendor module
supplies a hook only where the disk or the platform decides (codex's pane homes, cursor's md5 directory, grok's
shortened groups, goose's platform layout, aider's workspace history).
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..osplat import platform_id as _platform_id
from ._vendor_facts import CwdEncoding

log = logging.getLogger("agent_team_backend.cli_vendors.session_layout")


# ---- the workspace path as one directory name ------------------------------------------------------------------------


def encode_claude_cwd(cwd: str) -> str:
    """Claude Code's project-dir name for a cwd — the single source of truth.

    Claude replaces EVERY non-alphanumeric char with "-" (dots, underscores,
    spaces, unicode — not just "/"). It encodes its *normalized* cwd, which
    never carries a trailing separator, so strip one before encoding:
    otherwise the extra "-" makes the encoded dir miss the real one.

    Qwen reuses the same encoding for its per-project dirs — shared infrastructure, not
    claude-only knowledge.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", cwd.rstrip("/\\"))


def _encode_posix_droid_cwd(cwd: str) -> str:
    """The macOS/Linux spelling, read from droid's own encoder: strip trailing
    separators, strip the leading ``/`` run, collapse each remaining ``/`` run
    to a single ``-``, prepend ``-``. Only separators are touched."""
    text = re.sub(r"[\\/]+$", "", cwd)
    text = re.sub(r"^/+", "", text)
    return "-" + re.sub(r"/+", "-", text)


def _encode_windows_droid_cwd(cwd: str) -> str:
    """The Windows spelling, read from droid's own encoder: strip trailing
    separators, drop the drive letter's colon (``C:`` -> ``C``), collapse each
    run of ``\\`` or ``/`` to a single ``-``, prepend ``-``. A UNC path keeps
    its leading separator run, so ``\\\\server\\share`` names the directory
    ``--server-share``."""
    text = re.sub(r"[\\/]+$", "", cwd)
    text = re.sub(r"^([A-Za-z]):", r"\1", text)
    return "-" + re.sub(r"[\\/]+", "-", text)


def encode_droid_cwd(cwd: str, platform: str | None = None) -> str:
    """Droid's session-directory name for a cwd on ``platform``.

    Droid ships a different encoder per OS, so the spelling is not shared:
    macOS/Linux strips the leading slash and rewrites only ``/``, while Windows
    drops the drive-letter colon and rewrites both separators. ``platform``
    defaults to this process's platform (``osplat.platform_id``) and is
    overridable so both spellings can be exercised on any host, including on a
    native Windows runner.
    """
    if (platform or _platform_id) == "win32":
        return _encode_windows_droid_cwd(cwd)
    return _encode_posix_droid_cwd(cwd)


def encode_pi_cwd(cwd: str) -> str:
    """Pi's cwd → session-dir-name encoding: drop the leading "/", replace
    ONLY [/\\:] with "-", wrap in "--" (all other chars survive)."""
    if cwd.startswith("/"):
        cwd = cwd[1:]
    return "--" + re.sub(r"[/\\:]", "-", cwd) + "--"


#: One encoder per `CwdEncoding`. `absent` has no workspace in the path, so it spells nothing.
ENCODERS: dict[CwdEncoding, Callable[[str], str]] = {
    CwdEncoding.ABSENT: lambda _workspace_path: "",
    CwdEncoding.CLAUDE: encode_claude_cwd,
    CwdEncoding.DROID: encode_droid_cwd,
    CwdEncoding.URL_QUOTE: lambda workspace_path: quote(workspace_path.rstrip("/"), safe=""),
    CwdEncoding.PI: encode_pi_cwd,
    CwdEncoding.MD5: lambda workspace_path: hashlib.md5(workspace_path.encode("utf-8")).hexdigest() if workspace_path else "",
}


def encoded_cwd(encoding: CwdEncoding | str, workspace_path: str) -> str:
    """The workspace path spelled as one directory name, the way a CLI declared by `encoding` spells it."""
    return ENCODERS[CwdEncoding(encoding)](workspace_path)


# ---- the session root --------------------------------------------------------------------------------------------------


def session_root(record: Any, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """The directory a CLI's sessions start in, by the vendor's `session_root` declaration.

    A variable of `root_env` names the root itself. Otherwise the root is `sub` under a base: the first non-empty
    variable of `base_env`; else the default (the home directory plus `home`). With `fallbacks` declared the root is
    instead the first existing of: the `base_env` base, each fallback, the default, in that order, and the default
    stands in when none exists. The root may not exist: a reader lists the roots that do."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    def first(names: Iterable[str]) -> str | None:
        return next((value for name in names if (value := (env.get(name) or "").strip())), None)

    named = first(record.root_env)
    if named:
        return Path(named)
    default = home.joinpath(*record.home, *record.sub)
    base = first(record.base_env)
    chosen = [Path(base).joinpath(*record.sub)] if base else []
    if not record.fallbacks:
        return chosen[0] if chosen else default
    candidates = [*chosen, *(home.joinpath(*fallback.split("/"), *record.sub) for fallback in record.fallbacks), default]
    return next((path for path in candidates if path.is_dir()), default)


# ---- session paths and files under the root ---------------------------------------------------------------------------


def declared_session_path(record: Any, root: Path, workspace_path: str, session_id: str) -> Path:
    """Where one session's transcript lives under `root`, by the vendor's `session_path` declaration.

    Each segment is joined onto `root` with `{id}` the session id and `{cwd}` the workspace path as the vendor spells
    it. A vendor that cannot name the path (no workspace) answers None before it calls this."""
    cwd = encoded_cwd(record.cwd, workspace_path) if "{cwd}" in "/".join(record.segments) else ""
    return root.joinpath(*(segment.replace("{cwd}", cwd).replace("{id}", session_id) for segment in record.segments))


def declared_vendor_session_path(vendor: Any, workspace_path: str, session_id: str) -> Path:
    """Where `session_id`'s transcript lives for a vendor that declares `session_root` and `session_path`."""
    return declared_session_path(vendor.session_path, session_root(vendor.session_root), workspace_path, session_id)


def declared_log_files(
    record: Any,
    roots: Iterable[Path],
    workspace_path: str | None = None,
    *,
    accept: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """The session log files under `roots`, by the vendor's `logs` declaration.

    Every pattern of `record.files` is globbed under each root, in the declared order. All sessions
    (`workspace_path` None): `{cwd}` matches any directory name. One workspace: only the patterns that hold `{cwd}`
    apply, with the workspace's directory name in its place; a layout with no workspace in its paths (`absent`) has
    none. Only files count; `accept` is a vendor's extra test of a path the pattern cannot say (an id's shape). A
    root that cannot be listed gives nothing, as a missing one does."""
    patterns = record.files
    if workspace_path is None:
        patterns = tuple(pattern.replace("{cwd}", "*") for pattern in patterns)
    elif record.cwd == CwdEncoding.ABSENT:
        return []
    else:
        name = glob.escape(encoded_cwd(record.cwd, workspace_path))
        patterns = tuple(pattern.replace("{cwd}", name) for pattern in patterns if "{cwd}" in pattern)
    out: list[Path] = []
    for root in roots:
        for pattern in patterns:
            try:
                out.extend(
                    sorted(path for path in root.glob(pattern) if path.is_file() and (accept is None or accept(path)))
                )
            except OSError as err:
                log.debug("glob %s under %s failed: %s", pattern, root, err)
    return out
