# Redesign report: lifecycle runtime on main

Branch `refactor/cli-runtime-lifecycle-main`, based on upstream/main
`75272dd4b`. Nothing is pushed. The design is in `DESIGN.md`.

## What was built

- **Core** (`src/`, organised by lifecycle stage): launch, session (Unix PTY),
  resume, and the observe engine (`observe/`: JSONL, text and SQLite backends;
  line, byte, row and replay cursors; six usage modes; activity rules with
  the inferred-turn tracker; exact and inferred turns; the seen-keys bag
  codec). No vendor is named in it.
- **Declarations**: all 20 CLIs in main's registry have a file under
  `src/vendors/` with launch and resume. Observe is declared for 14: claude,
  codex, qwen, pi, kimi, grok, droid, muse, goose, hermes, openclaw, cursor,
  antigravity and aider. The other six are listed under "Not done".
- **Parity**: `backend/tests/test_cli_runtime_parity.py` builds scenarios
  from main's own vendor-test builders and records what main's readers return
  (`tests/parity/*.json`, 17 scenarios over the 14 observed CLIs, each step
  checking usage events, checkpoint, activity events and the seen-keys bag,
  then turns). `tests/parity.rs` replays every fixture through the Rust
  observer. The same Python test runs each scenario through the wrapped
  reader with the switch on and asserts that Rust answered every routed call
  (no silent fallback) with the same results.
- **Test-only CLI**: `tests/extensibility.rs` declares "toy" outside `src/`
  and runs its launch, resume, usage, activity and turns.
- **Declaration checks**: `tests/declarations.rs` rejects combinations the
  core does not run (a cursor without its backend or bag key, partitions
  without an anchor, settling rows not bound at `?3`, SQLite-only usage modes
  on files, kept slots with no bag key, inferred turns without usage).
- **Phase 2**: the `rustCliRuntime` setting (absent or not `true` = off).
  `cli_runtime.wrap` is applied to every production reader in `app.py`; with
  the switch off it calls the reader's own method and starts no process
  (`test_switch_off_is_mains_reader`). `ui.settings.set` applies a change
  immediately. `pytest -p tests.cli_runtime_on` runs any suite with it on.

## Size

| Part | Lines |
|------|-------|
| Core `src/` (excluding `src/vendors/`, including its unit tests) | 4,893 |
| Crate tests `tests/` (parity replay, toy CLI, declaration checks) | 463 |
| Declarations `src/vendors/` (20 files) | 2,926 |
| of which hook functions | 190 (hermes 11, antigravity 99, aider 80) |
| Seam `backend/agent_team_backend/cli_runtime.py` | 260 |

The six undeclared observers account for 170 of the declaration lines
(launch and resume only). Unit tests for hooks sit in the vendor files.

## Hooks

| Hook | CLI | Reason (also next to the function) |
|------|-----|------------------------------------|
| `session_name` | hermes | `<name> #<n>` continuation names, main's ` #\d+$` pattern |
| `workspace` | antigravity | chooses among `file://` copies in a protobuf blob by is-a-directory, count, length |
| `stamp`, `iso` | aider | main's `_SECTION_RE` fixed-width date pattern |
| `sent`, `received` | aider | main's `_TOKENS_RE` and `_parse_count` loose numbers |
| `namespace` | aider | pane token from the file name, else MD5 of the resolved path |

## Commands and results

Every command below was run in this worktree on macOS; the results are the
real ones from the final state of the branch unless noted.

| Command | Result |
|---------|--------|
| `cargo test --offline` (in `native/navide-cli-runtime`) | 25 passed, 6 suites (unit, parity replay of all 17 fixtures, toy CLI, declaration checks) |
| `cargo check --offline --all-targets` | clean, no warnings |
| `cargo build --release --offline` | built |
| `uv --project backend run pytest backend/tests/vendors backend/tests/test_cli_runtime_parity.py backend/tests/test_claude_activity_resume.py backend/tests/test_activity_high_water.py` (switch off) | 845 passed, 1 skipped (mcode package not installed) |
| the same with `-p tests.cli_runtime_on` (switch on) | 842 passed, 4 skipped: the mcode one, plus the 3 Python read meters (reason printed) |
| `node scripts/changed-backend-tests.mjs` (switch off; app.py and ws_handlers.py changed, so nearly the whole suite) | 16,553 passed, 39 skipped, 1 xfailed, **1 failed**: `test_osplat` found a `sys.platform` branch in `cli_runtime.py`. Fixed by using `osplat.paths.backend_entry_on_disk`; the full run was not repeated |
| `uv --project backend run pytest backend/tests/test_osplat.py backend/tests/test_cli_runtime_parity.py` after that fix | 119 passed, 1 skipped |
| switch-on reader set again after that fix | 842 passed, 4 skipped |
| `bun run vitest run src/renderer/src/components/__tests__/TerminalPane.agentPane.test.ts` | **not run**: no `node_modules` in this worktree (`Script not found "vitest"`), and the branch changes no frontend file |

Parity fixtures are regenerated with `NAVIDE_PARITY_WRITE=1` on the Python
test; the aider fixture was written that way, and the switch-on test then
asserts that Rust answered every routed call.

## Python defects found (main unchanged)

1. A line that is not UTF-8 raises `UnicodeDecodeError` in the JSONL activity
   walks after the mark has moved past the lines before it, so that walk's
   events are lost. The runtime reproduces it (and saves the bag the same way).
2. A JSON line that is valid but not an object (`[1]`, `"x"`) raises
   `AttributeError` in readers that call `.get` on it.
3. grok `_iso` raises `TypeError` on a non-numeric timestamp.
4. kimi (a quirk, possibly intended): after `turn.cancel` the tracker stays
   `cancelled` until the next `turn.prompt`, so every loop event in between is
   ignored, including reply text the CLI writes without a new prompt.
5. droid: `legacy_seen` grows without bound.
6. droid: a sidecar with invalid UTF-8 raises `UnicodeDecodeError`.
7. muse: `turns_for_session` credits a subagent log's own usage, which
   `parse_incremental` excludes as the parent's (the parity scenario skips
   turns for it).
8. goose: a row whose counters are all zero is read again every cycle (the
   watermark only moves on an event).
9. goose, hermes: a `toolCall` that is not an object crashes the walk.
10. openclaw: `int(nan)` / `int(inf)` usage values raise.
11. openclaw: `remember()` matches marks by prefix, so a session whose id is a
    `::`-prefix of another's removes the other's mark.
12. openclaw: a `seq` like `--5` crashes `int()`.
13. antigravity: malformed `agy_pending::` JSON crashes `json.loads`.
14. cursor: a row without an id is keyed `blob:None`.
15. aider: activity's mark never moves back, so after the history is rewritten
    shorter, its first lines up to the old mark are never delivered (kept:
    `set_activity_high_water` documents it as intended; listed because the
    parity scenario shows the loss).

## Rust differences from main (known, not hidden)

- claude: main NFC-normalises the cwd; Rust does not.
- Naive ISO timestamps are read as UTC (main: local time).
- JSONL activity walks split on `\n` only (main's text mode also splits on a
  lone `\r`); aider's replay walk does split on `\r`.
- codex: a `__lasttext__:` key with an empty value, when the slot was never
  written, is an untested edge.
- claude's three read-meter tests (`test_claude_activity_resume.py`) measure
  Python's own `Path.open` reads; with the switch on they are skipped by
  `tests/cli_runtime_on.py` with that reason, and the behaviour they pin is
  tested in `observe/jsonl.rs` (`a_stale_resume_point_reads_from_the_head`).
- The frontend keeps its own launch specs; the runtime's launch and resume
  declarations are not wired into the UI.
- openclaw: resolved session keys are not cached across calls, and each label
  lookup opens its own connection; an unkeyed session answers `Unsupported`
  (Python reads it).
- `_num` underscores (`"1_000"`) are not parsed.
- antigravity's cwd is recomputed every walk (main caches by mtime), and its
  is-a-directory test is relative to the process cwd for relative paths.
- A JSON BOM inside a SQLite BLOB is not stripped.
- goose's store env value is not checked for being absolute; Windows
  `APPDATA` is not modelled.
- `\d` is ASCII in aider's matchers and `char::is_numeric` in hermes's (main:
  Unicode decimal digits); aider's case-insensitive words fold ASCII only.
- aider turns: main's `parse_session_file` also counts an unterminated last
  line and splits on `\r`; the runtime's whole-file usage pass reads complete
  `\n` lines only.
- Windows: no ConPTY, and file identity is `0:0`.
- No read timeout on the `serve` pipe; no UI toggle for the setting (it is a
  settings key).

## Not done

- **opencode, kilo**: not declared. Every read updates the process-global
  `read_problem()` registry shown by `cli_get_status`; a Rust-served read can
  neither record nor clear an entry, so routing it would change main's
  diagnostics. Needs a diagnostics field in the wire answer that the seam
  applies to the reader. They also pick one of two schemas at runtime.
- **copilot**: not declared. One reader serves two stores and the JSONL branch
  defers to the database for sessions it lists (a cross-store read), and its
  cwd needs a YAML parser (a new dependency, not approved).
- **devin, kiro, mcode**: main has no log reader; only launch and resume are
  declared.
- The brief says 21 CLIs; main's registry
  (`backend/agent_team_backend/cli_vendors/registry.py`) has 20.
- Windows ConPTY and file identity.
