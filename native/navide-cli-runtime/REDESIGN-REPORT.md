# Redesign report: lifecycle runtime on main

Branch `refactor/cli-runtime-lifecycle-main`, based on upstream/main
`75272dd4b`. Nothing is pushed. The design is in `DESIGN.md`.

This is the state after fix round 1 (the first round ended at `543c573f2`).
Both reviews of that round said WITH LIMITS; the first part of this report
goes through every finding of both. The rest describes what the branch holds
now.

## Fix round 1: every review finding

"Before" is what the unfixed code did. Where a test could show the failure it
is named; where it could not, that is said.

### Design (review A D1–D7, review B design 1, 5, 6)

| Finding | Status | What changed | The test that shows it |
|---------|--------|--------------|------------------------|
| **A-D1** High: the shared vocabulary was the union of every vendor's quirks | Fixed | A vendor is a type implementing `decl::Vendor`; the observer is `Observer<V>`, monomorphised. One-vendor value extraction moved out of `Pred`/`Text`/`Num`/`SlotInit` into each vendor's `Ext` enum and its pure functions (`text`, `num`, `pred`, `init`, `probe`). Every remaining record-reading variant has two or more users, or is a combinator (DESIGN.md lists which, and which single-user items stay because they are mechanics). DESIGN.md says a new store shape is a core change by design. | `tests/extensibility.rs::observe_runs_end_to_end` declares a CLI outside `src/` with an `Ext` of its own and no core edit. The 27 parity fixtures replay unchanged in meaning through the new engine. |
| **A-D2** High: the Rust declaration was an additional source; three stages unused | Fixed | `declarations launch` is exported to `backend/agent_team_backend/cli_declarations.json` and an identical copy in the frontend's `agents/`. All 20 backend vendor modules and all 20 frontend agent specs read launch and resume through `declared(key)`; the hand-written copies and 16 Python `_resume_id_from_command` functions are deleted. Unused pieces removed: `Observe.store`, the wire's `argv` op, `launch.rs`. `session.rs` is kept and DESIGN.md says plainly it is not wired. `main.rs`'s header is corrected. | `backend/tests/test_cli_declarations.py`: `test_launch_and_resume_fields_are_mains` and `test_resume_ids_are_mains` compare against a recording of main's values and of the id each of main's parsers read from about 4,800 commands (`cli_declarations_main.json`, recorded before the old copies were deleted); `test_the_checked_in_declarations_are_current`; `test_backend_and_frontend_read_the_same_declarations`; `test_the_runtime_names_the_same_sessions`. Frontend: `agents/__tests__/declared.test.ts`. Before: 13 CLIs' launch declarations differed from main (no `env_strip`; kimi's spawn default). |
| **A-D3** Medium, **B-design-1** High: stringly slots, run-time validity, no traits | Fixed | `slots!` makes a typed enum per vendor (a misspelt slot does not compile; a lookup is an array index). `Cursor` and the usage modes are folded into each backend's own `Spec` (`JsonlRecords`, `TextRecords`, `SqliteRecords`), so an illegal combination cannot be written. `unreachable!` replaced by `Error::Unsupported`. Protobuf extractors get raw bytes (`Scope::blob`), no hex round trip. `Vendor`, `Backend<V>`, `SlotKey`, `Visit` are traits with static dispatch. | Compile-time for slots and combinations. `proto.rs::scans_like_mains_pb_scan`; `tests/declarations.rs` for what is left to check at run time. |
| **A-D4** Medium: a connection and a schema check per query, no key cache | Fixed | One connection per observer call (`db.rs`, `Conn`; `Db::open` has one caller), `requires` checked once on it, resolved partition labels kept in the observer (`Memo.labels`), unresolved ones not. | No test counts connections; it holds by construction. `openclaw_db` fixture exercises the label cache across polls. |
| **A-D5** Medium, **B-design-5** Low: generic code in a hook, duplicated, run per row | Fixed | `unquote` and the POSIX path normaliser are in `src/text.rs`, used by both. antigravity's workspace is a slot initialiser, computed once per walk. | `text.rs::unquote_matches_urllib`, `posix_paths_and_enclosures`; `antigravity.rs::workspace_prefers_a_directory_then_frequency_then_length`. |
| **A-D6** Low: boilerplate in every vendor file | Fixed | `Turns::None` / `Turns::Inferred`; constructors `Launch::of`, `Reading::of`, `Activity::rules`, `Emit::active` / `done` / `last`, `FileUsage::per_record`, `Anchored::of`; `unobserved!`. | — |
| **A-D7** Low: dead vocabulary | Fixed | `Num::MapSum`, `Num::Required`, `Effect::SetOnce` removed, with everything else nothing read (`Observe.store`, the `argv` op, `launch.rs`, `Db::scalar`). | `cargo check --all-targets`: no warnings. |
| **B-design-6** Low: Windows PTY and file identity, `session.rs` not on the wire | Not fixed (by decision) | Off Unix the runtime observes nothing (A-S6). PTY ownership is not part of this round; DESIGN.md says so. | — |

### Seam (review A S1–S8, review B design 2–4)

| Finding | Status | What changed | The test that shows it |
|---------|--------|--------------|------------------------|
| **A-S1**, **B-design-2** High: no read timeout under one lock the switch-off path also took | Fixed | A writer thread and a reader thread own the pipes; a caller waits for its answer on its own queue with a timeout (10 s), holding no lock. On timeout the process is killed and Python answers. `apply(False)` sets a flag and kills the process without any lock a call holds. | `test_cli_runtime_seam.py::test_a_stalled_process_times_out_and_python_answers`, `test_switching_off_returns_at_once_and_frees_a_stalled_call` (a stand-in process that never answers). |
| **A-S2** Medium: no backoff; a panic killed shared state | Fixed | `catch_unwind` per request in `serve`. In the seam: restart after `1 s × 2ⁿ` (capped at 60 s), and after 5 failures in a row the runtime stays on Python until the switch is flipped, saying so once. | `wire.rs::a_panicking_request_answers_an_error_and_the_next_one_is_served`; `test_a_process_that_keeps_dying_is_given_up_on_and_said_once`, `test_a_failed_process_is_not_restarted_before_its_delay`. |
| **A-S3** Medium: one lock and one single-threaded child | Fixed | `serve` answers on four worker lanes keyed by vendor and path (one file's reads stay in order, different files run side by side); the Python side holds no lock across a call. | `wire.rs::a_slow_read_does_not_hold_up_another_file`, `one_files_requests_are_answered_in_order`. |
| **A-S4** Medium: switch on with no binary was silent | Fixed | Logged once. Outcomes are counted per CLI (`rust`, `unsupported`, `unreadable`, `deferred`, `fallback`) and reported in `/health` diagnostics. | `test_switched_on_without_a_binary_says_so_once`, `test_status_counts_who_answered`, `test_health_diagnostics.py::test_health_reports_the_rust_cli_runtime_switch`. |
| **A-S5** Low: `_stop()` could stall the event loop | Fixed | The process is reaped in a thread. | `test_switching_off_returns_at_once_and_frees_a_stalled_call`. |
| **A-S6**: file identity off Unix, spawn flags, the wrong osplat helper | Fixed | Off Unix `observes` answers nothing, so nothing is routed. `osplat` has `bundled_executable(name)` and `helper_process_options()`. | `test_osplat_windows_discovery.py` (both helpers, Windows and POSIX). The off-Unix `observes` answer is a `cfg` constant and was not run: this machine is macOS. |
| **B-design-3** High: missing shutdown; binary found only in a dev checkout | Fixed, except packaging | A shutdown handler of its own (`app._stop_cli_runtime`, registered after `_stop_log_watcher`, so the watcher has stopped) calls `cli_runtime.shutdown` through `asyncio.to_thread`. `binary()` looks where a packaged app keeps its sidecars (`frozen_env.packaged_bin_dir()`), as `navide-stt` does. **Not done:** no build script compiles the runtime into the app bundle, so a packaged app finds no binary, says so once and stays on Python. | `test_shutdown_seen_flush.py::test_the_rust_cli_runtime_is_stopped_off_the_event_loop_after_the_watcher`; `test_the_binary_is_looked_up_like_the_other_sidecars`. |
| **B-design-4** Medium: `cost_ledger.py` built readers outside `wrap` | Fixed | Both sites wrap. | `test_the_cost_ledgers_own_readers_go_through_the_switch`. |
| **A-S7** Low: text describing a UI that does not exist; guide not updated | Fixed | The seam's and `app.py`'s text say there is no toggle. `docs/en-US/cli-extension-guide.md` has the declaration steps and the switch; `docs/adding-a-cli-vendor.md` starts with them. | — |
| **A-S8** Medium: the switch-on suite could not tell Rust from a silent fallback | Fixed | `tests/cli_runtime_on.py` prints who answered every routed call per CLI and fails on any fallback, or on a declined store whose CLI is not listed with its reason (`EXPECTED_UNSUPPORTED`). | The table under "Commands and results". |

### Parity (review A P1–P5, review B parity 1–5)

Before the fixes, `cargo test --test parity` on the unfixed crate with the new
fixtures reported 12 mismatches (aider turns, claude cwd and bad-bytes turns,
muse subagent turns, openclaw unkeyed, and others). All fixtures were recorded
from Python with the switch off.

| Finding | Status | What changed | The test that shows it |
|---------|--------|--------------|------------------------|
| **A-P1**, **B-parity-3** Medium: claude cwd not NFC | Fixed | The declaration says `cwd_form: Form::Nfc`; the runtime hands the cwd on as written and the seam normalises it. No new Rust dependency. | `claude_rules` fixture (a cwd written decomposed), through the seam in `test_switch_on_matches_python[claude_rules]`. |
| **A-P2** Medium: muse sub-agent turns | Fixed | `Vendor::accepts` gates usage and activity only; turns are gated by the file name, as main's are. The fixture compares turns (no `skip_turns`). | `muse_subagent` fixture. |
| **A-P3** Low, **B-parity-2** Medium: naive ISO stamps as UTC | Fixed | `time.rs` reads a naive stamp as local time, with CPython's algorithm over `libc::localtime_r`. | `tests/local_time.rs::a_naive_stamp_is_read_in_the_local_zone` (sets a non-UTC `TZ`). Not run against the old code, which read naive stamps as UTC by its own comment. |
| **B-parity-1** High: an unkeyed openclaw session aborted the whole pass | Fixed | A partition no query labels keeps its own name and leaves a note; the keyed ones are read. The seam logs the note once, through the reader's logger. | `openclaw_db` fixture (a step with an unkeyed session); `test_what_the_reader_would_log_is_logged_once_through_its_own_logger`. |
| **B-parity-4** Medium: aider `turns()` line splitting | Fixed | The whole-file usage pass reads with the same `text_lines` as the walk (`\n`, `\r\n`, a lone `\r`, an unterminated last line). | `aider_history` fixture's last step; `file.rs::text_lines_follow_universal_newlines`. |
| **A-P4** Low, **B-parity-5** Medium: fixture coverage | Fixed, and now enforced | New fixtures and steps: `claude_rules` (background tasks, `AskUserQuestion`, `TaskStop`, queue operations, compact and meta prompts, a malformed line), `claude_dash_dir`, `claude_bad_bytes` (a line that is not UTF-8), `codex_rewrites` (truncation, replacement, the `basis` upgrade, an empty `__lasttext__:`), openclaw unkeyed, aider `\r` and unterminated token lines, and more below. The parity test now traces which activity and usage rules match and **fails for a declared rule no fixture record reaches**. It found 8 (a qwen task notification, a legacy codex record, a goose action response, Kilo's OpenCode 2 rows); each has a recorded step now. | `tests/parity.rs::every_fixture_matches_mains_readers_and_every_rule_has_a_record`. Not traced: the effects inside a rule, and the parts of an exact-turns declaration. |
| **A-P5** Low: openclaw unkeyed sessions fell back every call | Fixed | By B-parity-1 and the D4 cache. | As B-parity-1. |

### Coverage (review A §6, review B 2.6)

| Item | Status | What changed | The test that shows it |
|------|--------|--------------|------------------------|
| **copilot** | Declared | The 1.0.78+ central store: an anchored row watermark for usage (`SqlUsage::Anchored`), a walk over two tables for activity (`SqlWalk::Tables`). Any other file answers `Unsupported`, so main's reader still reads the 1.0.75 `events.jsonl`; so does a store that cannot be read, which main's reader warns about. `Num::MapSum` was removed (A-D7). | `copilot_store` fixture (a rebuilt store, a row replaced under the watermark); `tests/vendors/test_copilot.py` with the switch on: 56 calls answered by Rust, 29 declined as declared. |
| **opencode, kilo** | Declared, both | One declaration (`vendors::opencode::store!`) expanded for the two vendor types. The schema is chosen per read by the vendor's probe (`SqlUsage::OneOf`, `SqlWalk::OneOf`, `Vendor::probe`). The anchored watermark gained rows still settling (`pending`), a tag naming the table it was taken on, and a lookup the store may not answer (`session.version`). The wire carries `reads`, which the seam applies to the reader's `read_problem()` registry. | Fixtures `opencode_v1`, `opencode_v2` (an upgraded database; a 1.x mark; a 1.x binary writing last, disclosed and then cleared), `opencode_odd_schema` (unknown, then half-upgraded), `kilo_store`, `kilo_v2`: each step compares the open read problem too. `test_what_the_schema_check_finds_reaches_the_readers_problem_registry`, `test_a_failing_query_is_the_readers_to_record_and_to_clear`. |

One thing here is narrower than the brief's wording, on purpose. The wire's
`reads` carry what the schema check finds (which the runtime decides). A
**query that fails** is not reported over the wire: the call answers
`Unsupported`, main's reader makes the same read and records why with its own
classification and sqlite's message, and while it has a failing query on
record it makes its own reads (`OpencodeLogReader.reads_itself()`, the one
method added to main's reader), so the query that recorded a problem is the
one that clears it. Reporting failures from Rust would have meant a second
copy of main's transient-error rules and of its registry keys, which are its
SQL strings.

## What the branch holds

- **Core** (`src/` without `src/vendors/`): the vendor and backend traits,
  the record-access language, three backends (JSONL, text log, SQLite), the
  activity walk with the inferred-turn tracker, exact and inferred turns, the
  seen-keys bag codec, the wire. No vendor is named in it.
- **Declarations**: all 20 CLIs of main's registry, one file each, with
  launch and resume. Records are declared for 17: aider, antigravity, claude,
  codex, copilot, cursor, droid, goose, grok, hermes, kilo, kimi, muse,
  openclaw, opencode, pi, qwen. devin, kiro and mcode have no log reader on
  main.
- **Single source**: the two generated `cli_declarations.json` files, read by
  `cli_vendors/_declared.py` and `agents/_declared.ts`.
- **Seam**: `backend/agent_team_backend/cli_runtime.py`, off by default.

## Size (`wc -l`)

| Part | Lines |
|------|-------|
| Core: `src/` without `src/vendors/` (22 files, 6,101, unit tests included) plus `build.rs` (35) | 6,136 |
| Declarations: `src/vendors/` (20 files) | 3,402 |
| of which extension code: each file's `Ext` enum, `impl Vendor` and helper functions, unit tests excluded (counted by item line ranges) | about 706 |
| of which the three launch-and-resume-only files (devin, kiro, mcode) | 42 |
| Crate tests: `tests/*.rs` (parity replay and rule coverage, declaration checks, the outside CLI, local time) | 627 |
| Parity fixtures: `tests/parity/*.json` | 27 files |
| Seam: `cli_runtime.py` | 616 |
| Readers of the generated JSON: `_declared.py` 129, `_declared.ts` 75 | 204 |

Extension code by file: aider 120, antigravity 90, cursor 64, opencode 60
(shared with kilo), grok 51, openclaw 49, claude 43, goose 39, muse 37,
hermes 34, qwen 30, codex 29, kimi 25, pi 21, copilot 7 and droid 7 (their
`impl Vendor` only; both declare `NoExt`).

## Commands and results

Run in this worktree on macOS, on the final code of the branch.

| Command | Result |
|---------|--------|
| `cargo test --offline` (in `native/navide-cli-runtime`) | 32 passed, 7 suites |
| `cargo check --offline --all-targets` | clean, no warnings |
| `cargo build --release --offline` | built |
| switch off: `uv --project backend run pytest backend/tests/vendors backend/tests/test_cli_runtime_parity.py backend/tests/test_cli_runtime_seam.py backend/tests/test_claude_activity_resume.py backend/tests/test_activity_high_water.py backend/tests/test_cli_declarations.py backend/tests/test_cli_vendors_registry.py backend/tests/test_health_diagnostics.py backend/tests/test_shutdown_seen_flush.py backend/tests/test_osplat.py backend/tests/test_osplat_windows_discovery.py` | 1,080 passed, 2 skipped |
| switch on: `uv --project backend run pytest -p tests.cli_runtime_on backend/tests/vendors backend/tests/test_cli_runtime_parity.py backend/tests/test_cli_runtime_seam.py backend/tests/test_claude_activity_resume.py backend/tests/test_activity_high_water.py` | 874 passed, 4 skipped (the mcode package is not installed; the 3 Python read meters, reason printed); no unexpected fallback |
| `node scripts/changed-backend-tests.mjs` (switch off; re-run in full after the last code change, exit 0) | parallel phase: 16,672 passed, 39 skipped, 109 deselected, 1 xfailed in 17:02; serial phase: 105 passed, 4 skipped |
| `bun run vitest run src/renderer/src/platform/plugin-shell tests/cli` | 18 files, 371 tests passed |
| `bun run typecheck:web` | passed |

The whole-frontend selection (`bun run vitest run --changed upstream/main`:
340 files, 6,026 tests passed) was run after the agent specs changed and not
again; no frontend file changed after it.

Who answered the routed reader calls in the switch-on run:

```
aider        rust=69   unsupported=0   unreadable=0  deferred=0  fallback=0
antigravity  rust=59   unsupported=0   unreadable=0  deferred=0  fallback=0
claude       rust=122  unsupported=0   unreadable=0  deferred=0  fallback=0
codex        rust=131  unsupported=0   unreadable=0  deferred=0  fallback=0
copilot      rust=56   unsupported=29  unreadable=0  deferred=0  fallback=0
cursor       rust=56   unsupported=0   unreadable=0  deferred=0  fallback=0
droid        rust=55   unsupported=0   unreadable=0  deferred=0  fallback=0
goose        rust=31   unsupported=0   unreadable=0  deferred=0  fallback=0
grok         rust=47   unsupported=0   unreadable=2  deferred=0  fallback=0
hermes       rust=36   unsupported=2   unreadable=0  deferred=0  fallback=0
kilo         rust=52   unsupported=0   unreadable=0  deferred=0  fallback=0
kimi         rust=69   unsupported=0   unreadable=0  deferred=0  fallback=0
muse         rust=58   unsupported=0   unreadable=0  deferred=0  fallback=0
openclaw     rust=59   unsupported=2   unreadable=0  deferred=0  fallback=0
opencode     rust=152  unsupported=2   unreadable=0  deferred=7  fallback=0
pi           rust=61   unsupported=0   unreadable=1  deferred=0  fallback=0
qwen         rust=60   unsupported=0   unreadable=0  deferred=0  fallback=0
```

`unsupported` is a declared answer, listed with its reason in
`tests/cli_runtime_on.py`; `unreadable` is a file the test removed or made
unreadable, where the Python reader decides what that means, as on main;
`deferred` is OpenCode's reader making its own reads while it has a failing
query on record. No call fell back on a fault.

Two failures on the way are worth knowing about; the final runs above caught
both, and the results in the table are from after their fixes.

- After the frontend specs were changed to spread `declared(key)`, three
  checks in `backend/tests/test_cli_vendors_registry.py` failed: they read
  `modelArgs`, `resumeArgs` and `defaultCommand` out of the spec sources with
  regular expressions. They now read the declaration the spec spreads, and
  require that a spec spreads its own key. The first pass of this round ran
  the frontend tests after that change but not this backend file.
- The first full `changed-backend-tests` run ended with 1 failed, 16,671
  passed: `test_acp_tui_unchanged.py` pins `app._stop_log_watcher` to main's
  text plus its one ACP hook, and the runtime's stop had been added inside
  it. The stop is now a shutdown handler of its own, and that function is
  main's again.

## Python defects found (main unchanged)

1. A line that is not UTF-8 raises `UnicodeDecodeError` in the JSONL activity
   walks after the mark has moved past the lines before it, so that walk's
   events are lost. The runtime reproduces it (`claude_bad_bytes`).
2. A JSON line that is valid but not an object (`[1]`, `"x"`) raises
   `AttributeError` in readers that call `.get` on it.
3. grok `_iso` raises `TypeError` on a non-numeric timestamp.
4. kimi (a quirk, possibly intended): after `turn.cancel` the tracker stays
   `cancelled` until the next `turn.prompt`, so every loop event in between is
   ignored, including reply text the CLI writes without a new prompt.
5. droid: `legacy_seen` grows without bound.
6. droid: a sidecar with invalid UTF-8 raises `UnicodeDecodeError`.
7. goose: a row whose counters are all zero is read again every cycle (the
   watermark only moves on an event).
8. goose, hermes: a `toolCall` that is not an object crashes the walk.
9. openclaw: `int(nan)` / `int(inf)` usage values raise.
10. openclaw: `remember()` matches marks by prefix, so a session whose id is a
    `::`-prefix of another's removes the other's mark.
11. openclaw: a `seq` like `--5` crashes `int()`.
12. antigravity: malformed `agy_pending::` JSON crashes `json.loads`.
13. cursor: a row without an id is keyed `blob:None`.
14. aider: activity's mark never moves back, so after the history is rewritten
    shorter, its first lines up to the old mark are never delivered
    (`set_activity_high_water` documents it as intended; the parity scenario
    shows the loss).
15. opencode (OpenCode 2): one `session_message` row whose `data` is not JSON
    makes `json_set` fail the whole usage query ("malformed JSON", recorded as
    `query-failed`), and no later usage read gets past that row while it is
    above the watermark. Found while recording the fixture; the row was taken
    out of the scenario because a declined read cannot be replayed.
16. copilot: a store read that fails is warned about once per process, so a
    second store failing for another reason is never mentioned.

(The first report listed muse's `turns_for_session` crediting a subagent
log's own usage as a defect. It is main's behaviour, and the runtime now does
the same: review A P2.)

## Rust differences from main (known)

Still open, all in cases no fixture or vendor test reaches:

- JSONL activity walks split on `\n` only; main's text mode also ends a line
  at a lone `\r`, which a JSON writer does not produce. Text logs (aider) do
  split on `\r`.
- opencode / kilo: a message id that is SQL `NULL` is keyed `msg:` (main:
  `msg:None`); a negative `seq` is not clamped to 0 when looking up a turn's
  reply; the reply of a 1.x turn is read in the same query as its `part` row
  (main: a second query, a moment later).
- opencode / kilo on Windows: main turns OpenCode 2's forward-slash paths
  back; the runtime observes nothing off Unix, so this is not written.
- Floats written with underscores (`"1_000.5"`) are not parsed where main's
  `_num` would.
- antigravity's workspace is recomputed every walk (main caches by mtime), and
  its is-a-directory test is relative to the process cwd for relative paths.
- A JSON BOM inside a SQLite BLOB is not stripped.
- goose's store env value is not checked for being absolute.
- `\d` is ASCII in aider's matchers and `char::is_numeric` in hermes's (main:
  Unicode decimal digits); aider's case-insensitive words fold ASCII only.
- claude's three read-meter tests measure Python's own `Path.open` reads; with
  the switch on they are skipped by `tests/cli_runtime_on.py` with that
  reason, and the behaviour is tested in
  `observe/file.rs::a_stale_resume_point_reads_from_the_head`.

The list above was carried over from the first report where an item was not
touched this round; the JSONL `\r` item was re-read in the code.

## Not done

- **Packaging**: no build step puts `navide-cli-runtime` into the app bundle
  (the lookup is ready for it).
- **A Settings toggle** for `rustCliRuntime`.
- **PTY ownership** (`session.rs` is kept, unwired), **Windows ConPTY** and
  **Windows file identity**.
- **devin, kiro, mcode**: main has no log reader; launch and resume only.
- **copilot's 1.0.75 `events.jsonl`** stays main's reader's (it reconciles
  against the store and needs a YAML file for its cwd).
- The first brief said 21 CLIs; main's registry has 20.
