"""The G3 shell attempt runner: `tests/cli/g1eAttempt.ts` with TEN declared substitutions (proposal H1: g3-named copies, the g1e file untouched and pinned), and its checker `tests/cli/runtimeMatched.ts` as a
pack-held, platform-aware copy. The ten, in the order they were added below: the wire number of the ready receipt identity, the Windows handoff in the same line and the checker import (three), then the
seven of `CONPTY_SUBSTITUTIONS` for the Windows ConPTY (six in rh16, the seventh in rh17).

Why (G3-r2 Phase A real-artifact check): `firstGenerationReceipts` accepts the E3 ready receipt only as `protocol: 'navide-acr', wire: 1, handoff: 'scm_rights'`. The backend speaks wire 2 at H, so the
unchanged runner rejects every Rust-arm ready receipt ("protocol/identity mismatch in ready receipt") whatever the sidecar does. Run 1 attributed that failure to the missing readyVersion; it was this.
The first substitution changes the wire number the identity check demands (1 -> the backend's current `WIRE_VERSION`, 2); the check stays exactly as strict and no measured path changes.

rh14 (R13-A1, a read-ahead finding of the rh13 re-review): the same identity line, and the 06 checker behind it, also name POSIX values the Windows sidecar does not send. Its ready line says
`handoff: "duplicate_handle"` and `pty_backend: "conpty"` (`windows.rs:367-368`, verified by `cli_runtime.py:746`), its owned receipts name `start_kind: "windows-filetime"` (`session_pty_win.rs:43`), and the
paths of an `observed` receipt are Windows paths. So every Windows Rust-arm attempt failed "protocol/identity mismatch in ready receipt" before any resource was compared. The declared changes, each one a
`process.platform === 'win32'` branch whose other arm is the g1e / 06 value byte for byte (so a Linux or macOS attempt is the g1e attempt, which a test proves with a Windows-shaped and a POSIX-shaped receipt set):
  - shell attempt, substitution 2: the identity expects `duplicate_handle` on Windows (`scm_rights` elsewhere);
  - shell attempt, substitution 3: it takes the checker from `./g3hRuntimeMatched`;
  - checker copy `tests/cli/g3hRuntimeMatched.ts` (generated from `runtimeMatched.ts`, which is not a pack file and stays untouched): `pty_backend` is `conpty` on Windows (`posix` elsewhere); the owned receipt's root
    `start_kind` is `windows-filetime` on Windows (`posix-lstart` elsewhere); a path is "inside" a directory by `/`-normalised segments on Windows (a Windows path is `\\`-separated and the receipt holds the path the
    backend passed), the same segment rule as before.
rh15 (R14-A1, the rh14 re-review): on Windows the backend runs the study's `.cmd` CLI shim as `cmd.exe /d /c <shim> --version` (`onboarding_deps.py:595` with `osplat/_windows.py:1583-1593`), so the sampled
`--version` probe's parent is that `cmd.exe`, not the backend, and the 06 rule "the probe's parent is the backend" failed every attempt that sampled it. The checker copy's fifth substitution adds, on Windows only, one
exempt shape: the probe's parent is a sampled `cmd.exe` that is a direct child of the backend and not a terminal pid. A probe under any other intermediate stays refused; off Windows the expression is the 06 one.
rh16 (windows-4b, the manager's decision): the shell attempt copy has SIX more declared substitutions (`CONPTY_SUBSTITUTIONS`), all for the Windows ConPTY and all the identity off Windows: an import of
`tests/cli/g3hConpty.ts`; a per-attempt `conptyViews()`; the `terminal.output` handler feeds the stream `conpty.view(tid, frame.data)` (the same object off Windows, so an empty chunk is still reported as one);
and the four console sizes the workload asks for (100x30 at create, 120x40 at the observation phase, the 20 resize probes) go through `conptyColumns`, which adds a fixed width on Windows so that the 2048-wide burst
line is not cut. `g3hConpty.ts` removes exactly what `tests/cli/shellRuntime.test.ts` removes on Windows (CSI sequences, a CR LF between filler characters) and turns the remaining CR LF into LF. The copy of
the checker (`MATCHED_SUBSTITUTIONS`) is unchanged by this.
rh17 (B-2, from the diagnostic run on windows-2025, `OUT/diag1/results.md`): SEVEN. The fifth now covers the whole observation-phase resize statement, and a seventh sends that same request later on
Windows: the attempt asks for no size change before the peer's FINAL line there (`conptySizeFixedUntilFinal()`), and sends each peer's 120x40 resize right after the FINAL lines, before the 20 resize probes.
The reason: the inbox console the sidecar uses paints its screen again after every size change, the product's log mirror keeps those rows as text, and the pinned log identity compares that text with the
peer's up to the FINAL line. Each peer still gets exactly the requests of the workload, in the same order, and every one of them still changes the size (the first probe goes from 120x40 to 100x30, as it
does off Windows); only the place of one request moves. Off Windows both statements are the g1e text's behaviour (`if (!false) await request(...)` with the g1e size, and `if (false) ...`). The repaints the
requests after FINAL cause are dropped by rule 4 of `g3hConpty.ts`, and the log is read up to its FINAL line (`hosted_seams.windows_log_identity`).
The copies are GENERATED from the g1e / 06 files, so every other byte is theirs; `check` proves that and is run by a test.
"""
from __future__ import annotations

from pathlib import Path

from agent_team_backend import cli_runtime_frames

SOURCE = "tests/cli/g1eAttempt.ts"
TARGET = "tests/cli/g3ShellAttempt.ts"
MATCHED_SOURCE = "tests/cli/runtimeMatched.ts"
MATCHED_TARGET = "tests/cli/g3hRuntimeMatched.ts"
HEADER = ("// GENERATED by backend/tests/cli_regression/support/g3/attempt_copy.py from tests/cli/g1eAttempt.ts. Do not edit: `attempt_copy.check` compares this file with the g1e file and the declared substitutions.\n"
          "// The changes: the E3 ready receipt identity expects the backend's current wire version, and on Windows the duplicate-handle handoff; the 06 checker is `./g3hRuntimeMatched`; on Windows the ConPTY view of the stream, the console width and the observation-phase resize sent after the FINAL line (`./g3hConpty`). See attempt_copy.py.\n")
MATCHED_HEADER = ("// GENERATED by backend/tests/cli_regression/support/g3/attempt_copy.py from tests/cli/runtimeMatched.ts. Do not edit: `attempt_copy.check` compares this file with the 06 file and the declared substitutions.\n"
                  "// The only changes are `process.platform === 'win32'` branches whose other arm is the 06 value (see attempt_copy.py): the sidecar's `pty_backend`, the owned receipt's `start_kind`, the separator of a path and of the `.jsonl` suffix,\n"
                  "// and the `--version` probe run through the `.cmd` shim as `cmd.exe /d /c <shim> --version` (the backend's call sites: `onboarding_deps.py:595`, `app.py:4125`, `claude_hooks.py:322`, with `osplat/_windows.py:1583-1593`).\n")
OLD_IDENTITY = "const identityOk = ready.protocol === 'navide-acr' && ready.wire === 1 && ready.handoff === 'scm_rights'"
OLD_HANDOFF = "ready.handoff === 'scm_rights'"
NEW_HANDOFF = "ready.handoff === (process.platform === 'win32' ? 'duplicate_handle' : 'scm_rights')"
OLD_CHECKER_IMPORT = "type RuntimeMode } from './runtimeMatched'"
NEW_CHECKER_IMPORT = "type RuntimeMode } from './g3hRuntimeMatched'"

#: (old, new) pairs applied to `runtimeMatched.ts`, each of which must occur exactly once.
MATCHED_SUBSTITUTIONS = (
    ("root.start_kind !== 'posix-lstart'", "root.start_kind !== (process.platform === 'win32' ? 'windows-filetime' : 'posix-lstart')"),
    ("handshake.pty_backend !== 'posix'", "handshake.pty_backend !== (process.platform === 'win32' ? 'conpty' : 'posix')"),
    ("  if (typeof path !== 'string' || !path.startsWith(`${dir}/`)) return false\n  return path.slice(dir.length + 1).split('/').every(",
     "  if (typeof path !== 'string') return false\n  const [full, base] = process.platform === 'win32' ? [path.replace(/\\\\/g, '/'), dir.replace(/\\\\/g, '/')] : [path, dir]\n"
     "  if (!full.startsWith(`${base}/`)) return false\n  return full.slice(base.length + 1).split('/').every("),
    ("&& entry.path.endsWith(`/${peer.pane}.jsonl`)", "&& (process.platform === 'win32' ? entry.path.replace(/\\\\/g, '/') : entry.path).endsWith(`/${peer.pane}.jsonl`)"),
    # rh15 (R14-A1). `globalThis.process`: the arrow parameter of the `probes` map is named `process`. The Windows arm adds one more exempt shape (the .cmd shim run as `cmd.exe /d /c <shim> --version`);
    # off Windows `(A || false) && B` is the 06 expression `A && B`.
    ("exempt: process.ppid === input.backend.pid && !terminalPids.has(process.pid) }))",
     r"exempt: (process.ppid === input.backend.pid || (globalThis.process.platform === 'win32' && !terminalPids.has(process.ppid) && seen.get(process.ppid)?.ppid === input.backend.pid"
     r" && /(^|[\\/])cmd\.exe$/i.test(seen.get(process.ppid)?.executable ?? ''))) && !terminalPids.has(process.pid) }))"),
)


#: (old, new) pairs applied to `g1eAttempt.ts` for the Windows ConPTY (rh16, windows-4b), each of which must occur exactly once. Off Windows every one of them is the g1e text's behaviour:
#: `conptyColumns(n)` is `n`, `conptySizeFixedUntilFinal()` is false, and `conpty.view` returns the chunk it was given.
CONPTY_SUBSTITUTIONS = (
    ("import { ArrivalWaiter } from './g1eWait'", "import { ArrivalWaiter } from './g1eWait'\nimport { conptyColumns, conptySizeFixedUntilFinal, conptyViews } from './g3hConpty'"),
    ("  const lastArrival = new Map<string, number>()", "  const lastArrival = new Map<string, number>()\n  const conpty = conptyViews()"),
    ("    stream.onChunk(monotonic, frame.data)",
     "    const view = conpty.view(tid, frame.data)\n    if (view === frame.data || view.length > 0) stream.onChunk(monotonic, view)"),
    ("        cols: 100, rows: 30, create_generation:", "        cols: conptyColumns(100), rows: 30, create_generation:"),
    ("      await request('terminal.resize', { terminal_session_id: peer.tid, cols: 120, rows: 40 })",      # rh17 B-2: the observation-phase resize, the only size change before the FINAL line
     "      if (!conptySizeFixedUntilFinal()) await request('terminal.resize', { terminal_session_id: peer.tid, cols: conptyColumns(120), rows: 40 })"),
    ("cols: 100 + (n % 2 ? 20 : 0), rows: 30", "cols: conptyColumns(100 + (n % 2 ? 20 : 0)), rows: 30"),
    ("    const probe = peers[0]\n",      # rh17 B-2: on Windows the observation-phase resize of every peer is sent here, after the FINAL lines and before the resize probes
     "    if (conptySizeFixedUntilFinal()) await Promise.all(peers.map((peer) => request('terminal.resize', { terminal_session_id: peer.tid, cols: conptyColumns(120), rows: 40 })))\n    const probe = peers[0]\n"),
)


def new_identity(wire: int | None = None, windows_handoff: bool = True) -> str:
    identity = OLD_IDENTITY.replace("ready.wire === 1", f"ready.wire === {cli_runtime_frames.WIRE_VERSION if wire is None else wire}")
    return identity.replace(OLD_HANDOFF, NEW_HANDOFF) if windows_handoff else identity


def _once(text: str, old: str, what: str) -> None:
    if text.count(old) != 1:
        raise ValueError(f"{what} no longer has {old[:60]!r} exactly once: review the copy")


def generate(root: Path, wire: int | None = None) -> str:
    source = (Path(root) / SOURCE).read_text(encoding="utf-8")
    _once(source, OLD_IDENTITY, SOURCE)
    _once(source, OLD_CHECKER_IMPORT, SOURCE)
    text = source.replace(OLD_IDENTITY, new_identity(wire)).replace(OLD_CHECKER_IMPORT, NEW_CHECKER_IMPORT)
    for old, new in CONPTY_SUBSTITUTIONS:
        _once(source, old, SOURCE)
        text = text.replace(old, new)
    return HEADER + text


def generate_matched(root: Path) -> str:
    source = (Path(root) / MATCHED_SOURCE).read_text(encoding="utf-8")
    text = source
    for old, new in MATCHED_SUBSTITUTIONS:
        _once(source, old, MATCHED_SOURCE)
        text = text.replace(old, new)
    return MATCHED_HEADER + text


def write(root: Path) -> str:
    text = generate(root)
    (Path(root) / TARGET).write_text(text, encoding="utf-8")
    (Path(root) / MATCHED_TARGET).write_text(generate_matched(root), encoding="utf-8")
    return text


def check(root: Path) -> list[str]:
    problems = []
    for target, produce in ((TARGET, generate), (MATCHED_TARGET, generate_matched)):
        path = Path(root) / target
        if not path.is_file():
            problems.append(f"{target} is missing")
        elif path.read_text(encoding="utf-8") != produce(root):
            problems.append(f"{target} differs from its source file plus the declared substitutions")
    return problems
