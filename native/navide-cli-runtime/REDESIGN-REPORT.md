# Redesign report: lifecycle runtime on main

Branch `refactor/cli-runtime-lifecycle-main`, based on upstream/main
`75272dd4b`, pushed to the fork (draft PR 18). The design is in `DESIGN.md`.

This is the state after fix round 2. Round 1 ended at `aec1a324b`; its two
reviews (2-A: WITH LIMITS, 2-B: NOT READY as a base) are the first part of
this report, finding by finding. Round 1's own table follows, and the rest
describes what the branch holds now.

## Fix round 2: every review 2 finding

"Before" is what the code at `aec1a324b` did. Each fix names the test that
shows it; where that test was run against the unfixed code, the result is
given.

### Acted on

| Finding | Status | What changed | The test that shows it |
|---------|--------|--------------|------------------------|
| **B-1** High, **A goal 1**: adding a CLI edited two shared registries | Fixed | A declaration names the CLI's place in the two lists (`Cli::order.menu`, `order.registry`). `cli_vendors/registry.py` imports each declared CLI's module by its key; `agents/index.ts` finds spec files with `import.meta.glob`. Neither keeps a list, and a declared CLI with no adapter file is registered from its declaration alone. `AgentKey` is the declared keys, so the frontend copy of the declarations became a typed constant (`agents/_cli_declarations.ts`). DESIGN.md and `docs/en-US/cli-extension-guide.md` state exactly which files a new CLI needs. | Backend: `test_cli_vendors_registry.py::test_registry_is_discovered_in_mains_order` (main's order, recorded; each spec is its module's `SPEC`), `test_a_declared_cli_with_no_module_is_registered_from_its_declaration`, `test_a_vendor_module_that_cannot_be_imported_is_not_mistaken_for_a_missing_one`, `test_pyinstaller_spec.py::test_every_vendor_module_is_a_hidden_import`. Before the hand tuple was deleted, a one-off test compared the discovered tuple with it, spec by spec, by identity: 20 of 20, same order. Frontend: `agents/__tests__/declared.test.ts` "lists the specs in the order main listed them", `specs.test.ts` "lists every spec file, found without being registered". |
| **B-2** High, **A-N1** Medium: the currency test compared the JSON with a prebuilt binary and skipped without one; no CI ran the crate | Fixed | `tests/checked_in.rs` (plain `cargo test`, no binary) compares both checked-in copies with what the source prints and rewrites them on request. The backend currency test runs `cargo run -- declarations launch`. Every backend test that runs the runtime has cargo bring the release build up to date first (`tests/cli_runtime_build.py`): with `NAVIDE_CLI_RUNTIME_REQUIRED=1` a runtime that cannot be built is a failure, never a skip. CI has a `cli-runtime` job on Linux and macOS, on the backend scope of `scripts/ci-changed-scopes.mjs` and in the required gate: `cargo check` with warnings denied, `cargo test`, the sidecar build, the backend's runtime tests with the switch off, then the vendor suites with `-p tests.cli_runtime_on`. | `tests/checked_in.rs::the_checked_in_declarations_are_what_the_source_prints`: run after `order` was added to the declarations and before regenerating, it failed on the stale JSON with no release rebuild. `tests/infrastructure/cliRuntimePackaging.test.ts` ("Rust CLI runtime in CI"), `ciPathFilter.test.ts`. |
| **B-3** Medium, **A-N2** Medium: one dead process counted once per waiting caller | Fixed | Every process that becomes the current one has a generation; `_process_failed` counts a generation once, under `_state`. | `test_cli_runtime_seam.py::test_one_dead_process_is_one_failure_however_many_callers_waited`: 8 callers behind a barrier, a stand-in that exits once all 8 are waiting. Before: `failures == 8`, breaker open. After: 1, `waiting-to-restart`. |
| **B-4** Medium, **A-N10**: `Vendor::probe` took `&mut dyn FnMut` | Fixed | `probe` is generic over the query function. `src/` holds no `dyn`. `registry::observer(key)` builds the one observer asked for. | Compile-time; `grep -rn dyn src/` is empty. |
| **Packaging** (A, B): no build step put the binary in a package | Wired, not yet exercised | `scripts/build-cli-runtime.mjs` (modelled on `build-stt.mjs`; `build:cli-runtime`) stages the release binary in `build/cli-runtime/`; the macOS and Linux `extraResources` copy it to `<resources>/bin`; `release.yml` builds it where it builds the speech sidecar (required on macOS arm64 and Linux x64, best-effort on the Linux arm64 dry run), verifies its Developer ID signature and hardened runtime on macOS and its presence in the Linux AppImage. Windows carries nothing, and the script, `package.json` and the tests say so. **A release dry run is still needed**: none was run, so signing, notarization and the package contents are unverified. | `tests/infrastructure/cliRuntimePackaging.test.ts` (16 tests: script, staged name against the seam's, both resource lists, no Windows entry, the three release jobs, the macOS and Linux verify steps). The script itself was run: it built and staged the binary. |
| **Routed-call overhead** (manager; the harness's 0.19 ms against 0.10 ms) | Reduced; the boundary's own cost remains | Found with `backend/tests/bench_cli_runtime.py`: every call read the **whole transcript** to look at the header lines a slot is seeded from. Fixed in `observe/file.rs::all_lines` (line by line, no further than asked) and one path lookup saved in the tail read. Numbers under "Micro-benchmark". | `observe/file.rs::a_header_read_stops_at_the_lines_it_was_asked_for` (a pipe that never ends: the old read would not return). |
| **A-N3** Low: a call registered as the reader thread ended waited out its timeout | Fixed | `dead` is set and checked under the lock a call registers under. | `test_a_call_that_arrives_as_the_process_dies_does_not_wait_out_its_timeout`. Before: `TimeoutError` after 30 s. |
| **A-N4** Medium if confirmed: `joined` read a cell's type after converting it to text | Confirmed, fixed | The type is read first. | `observe/sqlite.rs::a_blob_that_is_not_utf8_is_joined_as_a_blob`. Written first, it failed against the old order: `joined` returned `None` for a BLOB holding `0xFF`. |
| **A-N5** Low: an error answer logged on every poll; a panic dropped a whole lane's reader memory | Fixed | The seam logs an error answer once per CLI and kind (`status()` still counts every one). A panic replaces only the observer of the request's CLI. | `test_an_error_answered_on_every_poll_is_logged_once_and_counted_every_time` (before: 5 warnings); `wire.rs::a_panic_costs_only_its_own_clis_reader_memory` (before: the other CLI's observer was rebuilt too). |
| **A-N6** Low, **A-D2** frontend: 17 hand-written resume patterns | Partly generated; the rest documented | `declared()` derives `resumeCommandPattern` from the declared program, base arguments and resume arguments: the command Navide itself builds. Six hand-written patterns that only repeated it are deleted (antigravity, codex, goose, grok, muse, pi), and claude, droid and qwen now carry the pattern that was App.vue's generic fallback for them. Seven stay hand-written because they recognize more than the declared command, which the declaration's resume arguments cannot express: a short-flag alias (kilo, kimi, opencode), the `=` and spaced forms both (copilot, cursor, openclaw), a former program name (cursor), arguments in between (hermes, openclaw). aider's is for its id-less restore. Deriving them from `resume.parse` instead was tried on paper and rejected: it would change what six CLIs recognize (it would add `=` forms and aliases main's patterns refuse). | `declared.test.ts` "%s recognizes a resume command as on main": all 20, the regex source compared with a recording of main's (or the generic shape where main had none). |
| **A-N7** Low: a reader call after `shutdown()` started the process again | Fixed | `shutdown()` sets a flag `_client_for_call` honours; applying the switch clears it. | `test_a_call_after_shutdown_does_not_start_the_process_again`. Before: a new process was asked `observes` and `usage`. |
| **A-N8** Low, **A-D4** residual: antigravity opened two connections per walk | Fixed | A slot seeded from the database is declared (`SqliteRecords::seeds`, `StoreSeed`) and read on the walk's own connection; `sqlite::joined(path, …)`, the second opener, is gone. | The antigravity parity fixtures replay unchanged. No test counts connections: `Db::open` has one caller and nothing else opens one. |

### Not acted on

| Finding | Why |
|---------|-----|
| **A-N9** Low: the rule-coverage trace does not see effects, `Settling.when`, `revisit.when`, label queries, initialiser alternatives, `Ext` branches or the parts of `ExactTurns` | Not small. Each of those is a separate trace point in the engine and needs a fixture record that reaches it, recorded from main's Python; several (orphan handling in exact turns) have no recorded scenario yet. Left as stated in round 1: the check covers rule matches. |
| **A**, small leftover: `LINE_KEY` repeated in 8 vendor files | Each is a `const` of that vendor's own `Text<V>`. Sharing it needs a generic associated constant holding a promoted slice, which was not tried; eight one-line constants were judged cheaper than the risk. |
| **A**: the opencode / kilo `reads` and `deferred` costs (the Rust path depends on the Python reader; deferral is per CLI, not per database; a busy database is read twice) | Port debt by the reviewer's own verdict, not a defect of this stage. Carried under "Not done". |
| Header slot read on every call (found by the benchmark) | See "Micro-benchmark": about 15 of the remaining 35 µs. Matching main's per-file cache needs the observer's memory in slot initialisation at seven call sites and a per-CLI declared choice; planned with the next wire. |

Two things review 2-A pointed at in this report are corrected below: the
backoff is `1 s × 2ⁿ`, so 1, 2, 4 and 8 s before the breaker opens at the
fifth failure, and the 60 s cap in the code is not reached with these
constants; and "one connection per observer call" is now true of antigravity
too.

### Found while fixing

- **The crate's unit tests were flaky on macOS once a SQLite unit test joined
  them**: the PTY test's child was killed with signal 9 before it wrote
  anything, in 12 of about 58 runs. In none of 16 runs with the SQLite test
  left out, and none of 16 with its connection opened under
  `session::FD_FORK_LOCK`, which is what the test now does. The cause is not
  established (a hypothesis only: the system SQLite's descriptors across
  `fork`). It matters for the next stage, where one process will both read
  SQLite stores and fork panes; `session.rs` and DESIGN.md record it.
- The same PTY test read its child's output after the child had exited, and
  its drain helper threw away a read that returned data right after the
  exit. The child now waits for a line and the output is read while it is
  alive.
- Two `wire.rs` tests swapped the process's panic hook at the same time, so
  the silent hook could stay in place and hide another test's failure
  message. They take turns now.

## Micro-benchmark

`uv --project backend run python backend/tests/bench_cli_runtime.py <rounds>
<earlier>`: a Claude transcript in the fake agent's format grows by one
record, then `parse_incremental` and `parse_activity` are called, `rounds`
times, on a transcript that already holds `earlier` records. macOS, release
build, microseconds per call, median; "mean" is the mean of the two methods.

| Transcript | main's Python reader | Routed, before | Routed, after |
|------------|---------------------:|---------------:|--------------:|
| starts empty, 2,000 rounds | 22.9 | 82.1 | 61.8 |
| 20,000 earlier records, 500 rounds | 23.5 | 392.5 | 62.7 |

"Before" in the first row is the runtime as built at `aec1a324b` plus this
round's earlier commits (the read path was untouched until then). The second
row's "before" was measured with the old whole-file read put back beside the
new code for one run, since the old binary was gone by then; that slightly
overstates it (the same construction gave 88.0 where the true old binary
gave 82.1 in the first row).

One routed usage call, taken apart (after; 20,000 earlier records):

| Part | µs |
|------|---:|
| request encoded to a JSON line (Python) | 1.7 |
| pipe round trip and the runtime's own request handling (a `ping`) | 12.4 |
| the seam's writer and reader threads and the caller's queue | 7.0 |
| the read itself in Rust, with its larger answer | 35.1 |
| answer parsed (Python) | 1.6 |
| result objects built (Python) | 0.4 |

Inside the 35 µs, measured in the runtime with a throwaway timing test: the
slot header read about 15, the tail read about 20, request parsing 2,
building and serialising the answer 3. JSON is not where the time goes; file
opens are (two per call on macOS).

What this means for the harness's result: its transcripts were short (180
records at most), so the whole-file read was a small part of its 0.19 ms.
The fix removes a cost that grew with the transcript (20 µs per call by
2,000 records, 330 by 20,000) and will move the harness's number only a
little; that was not re-measured with the harness, which was not run this
round. The rest of the gap to Python is the process boundary
(about 19 µs) and the per-call header read, neither of which this round
removes; DESIGN.md has the plan (a framed duplex channel before PTY and
WebSocket work, the header value kept per file with it). No
unchanged-file shortcut was added in Python: main's reader has none.

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
| **A-S2** Medium: no backoff; a panic killed shared state | Fixed | `catch_unwind` per request in `serve`. In the seam: restart after `1 s × 2ⁿ` (1, 2, 4, 8 s; the code's 60 s cap is not reached before the breaker opens), and after 5 failures in a row the runtime stays on Python until the switch is flipped, saying so once. Round 2 made a failure a process, not a caller (B-3). | `wire.rs::a_panicking_request_answers_an_error_and_the_next_one_is_served`; `test_a_process_that_keeps_dying_is_given_up_on_and_said_once`, `test_a_failed_process_is_not_restarted_before_its_delay`. |
| **A-S3** Medium: one lock and one single-threaded child | Fixed | `serve` answers on four worker lanes keyed by vendor and path (one file's reads stay in order, different files run side by side); the Python side holds no lock across a call. | `wire.rs::a_slow_read_does_not_hold_up_another_file`, `one_files_requests_are_answered_in_order`. |
| **A-S4** Medium: switch on with no binary was silent | Fixed | Logged once. Outcomes are counted per CLI (`rust`, `unsupported`, `unreadable`, `deferred`, `fallback`) and reported in `/health` diagnostics. | `test_switched_on_without_a_binary_says_so_once`, `test_status_counts_who_answered`, `test_health_diagnostics.py::test_health_reports_the_rust_cli_runtime_switch`. |
| **A-S5** Low: `_stop()` could stall the event loop | Fixed | The process is reaped in a thread. | `test_switching_off_returns_at_once_and_frees_a_stalled_call`. |
| **A-S6**: file identity off Unix, spawn flags, the wrong osplat helper | Fixed | Off Unix `observes` answers nothing, so nothing is routed. `osplat` has `bundled_executable(name)` and `helper_process_options()`. | `test_osplat_windows_discovery.py` (both helpers, Windows and POSIX). The off-Unix `observes` answer is a `cfg` constant and was not run: this machine is macOS. |
| **B-design-3** High: missing shutdown; binary found only in a dev checkout | Fixed, except packaging | A shutdown handler of its own (`app._stop_cli_runtime`, registered after `_stop_log_watcher`, so the watcher has stopped) calls `cli_runtime.shutdown` through `asyncio.to_thread`. `binary()` looks where a packaged app keeps its sidecars (`frozen_env.packaged_bin_dir()`), as `navide-stt` does. Packaging was not done in round 1; round 2 wired it (see above). | `test_shutdown_seen_flush.py::test_the_rust_cli_runtime_is_stopped_off_the_event_loop_after_the_watcher`; `test_the_binary_is_looked_up_like_the_other_sidecars`. |
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
- **Single source**: the two generated copies of `declarations launch`
  (`cli_declarations.json` for the backend, `agents/_cli_declarations.ts` for
  the frontend), read by `cli_vendors/_declared.py` and `agents/_declared.ts`.
  They are also the list of CLIs: `cli_vendors/registry.py` and
  `agents/index.ts` are built from them and keep no list of their own.
- **Seam**: `backend/agent_team_backend/cli_runtime.py`, off by default.
- **Packaging and CI**: `scripts/build-cli-runtime.mjs`, the macOS and Linux
  `extraResources`, the `release.yml` steps, and CI's `cli-runtime` job.

## Size (`wc -l`)

| Part | Lines |
|------|-------|
| Core: `src/` without `src/vendors/` (22 files, 6,322, unit tests included) plus `build.rs` (35) | 6,357 |
| Declarations: `src/vendors/` (20 files) | 3,422 |
| of which extension code: each file's `Ext` enum, `impl Vendor` and helper functions, unit tests excluded (round 1's count by item line ranges, less the 6 lines of antigravity's removed `init`) | about 700 |
| of which the three launch-and-resume-only files (devin, kiro, mcode) | 45 |
| Crate tests: `tests/*.rs` (parity replay and rule coverage, declaration checks, the checked-in copies, the outside CLI, local time) | 686 |
| Parity fixtures: `tests/parity/*.json` | 27 files |
| Seam: `cli_runtime.py` | 652 |
| Readers of the generated declarations: `_declared.py` 131, `_declared.ts` 97 | 228 |
| The two registries: `cli_vendors/registry.py` 148 (was 160, with its hand list), `agents/index.ts` 59 (was 76) | 207 |
| Round 2's tooling: `scripts/build-cli-runtime.mjs` 57, `backend/tests/cli_runtime_build.py` 63, `backend/tests/bench_cli_runtime.py` 208 | 328 |

The core grew by 221 lines in round 2: `StoreSeed` and `Order` in `decl.rs`,
the bounded header read, and unit tests (the header read on a pipe, the
SQLite cell type, a panic's reach, the test database writer).

Extension code by file (round 1's count): aider 120, antigravity 90, cursor 64, opencode 60
(shared with kilo), grok 51, openclaw 49, claude 43, goose 39, muse 37,
hermes 34, qwen 30, codex 29, kimi 25, pi 21, copilot 7 and droid 7 (their
`impl Vendor` only; both declare `NoExt`).

## Commands and results

Run in this worktree on macOS (rustc 1.99.1), on the final code of round 2.
Nothing was run on Linux or Windows.

| Command | Result |
|---------|--------|
| `cargo test --offline` (in `native/navide-cli-runtime`) | 37 passed, 8 suites |
| `cargo check --offline --all-targets`, and again with `RUSTFLAGS="-D warnings"` as CI runs it | clean, no warnings |
| `cargo build --release --locked`; `NAVIDE_CLI_RUNTIME_REQUIRED=1 node scripts/build-cli-runtime.mjs` | built; staged in `build/cli-runtime/` |
| switch off: `uv --project backend run pytest backend/tests/vendors backend/tests/test_cli_runtime_parity.py backend/tests/test_cli_runtime_seam.py backend/tests/test_claude_activity_resume.py backend/tests/test_activity_high_water.py backend/tests/test_cli_declarations.py backend/tests/test_cli_vendors_registry.py backend/tests/test_health_diagnostics.py backend/tests/test_shutdown_seen_flush.py backend/tests/test_osplat.py backend/tests/test_osplat_windows_discovery.py backend/tests/test_pyinstaller_spec.py` | 1,101 passed, 3 skipped (the runtime built from source by the tests) |
| switch on: `uv --project backend run pytest -p tests.cli_runtime_on backend/tests/vendors backend/tests/test_cli_runtime_parity.py backend/tests/test_cli_runtime_seam.py backend/tests/test_claude_activity_resume.py backend/tests/test_activity_high_water.py` | 878 passed, 4 skipped (the mcode package is not installed; the 3 Python read meters, reason printed); fallback 0 for every observed CLI |
| `node scripts/changed-backend-tests.mjs -- -n 4 --dist loadfile -m "not serial"` (the whole suite; re-run after the last change, exit 0) | 16,664 passed, 38 skipped, 1 xfailed in 3:30; its unsandboxed files: 105 passed, 4 skipped |
| `node scripts/changed-backend-tests.mjs -- -m serial` | 16 passed, 2 skipped (the script exits 5 here: its second pass has no serial test to select) |
| `uv --project backend run ruff check backend` | all checks passed |
| `bun run vitest run --changed upstream/main` (re-run after the last change) | 1,339 files, 20,551 tests, **1 failed**: `src/main/plugins/pluginExternalWorkspace.test.ts`, "Executable not found in $PATH: pnpm". pnpm is not installed on this machine; the test does not touch this branch's code, and CI installs pnpm for the job that runs it. |
| the affected files: `bun run vitest run src/renderer/src/platform/plugin-shell tests/cli plugins/navide-plans/src/retained` and the four tests that read the agent list | 29 files, 604 tests passed |
| `bun run vitest run tests/infrastructure` | 24 files, 266 passed, 4 skipped |
| `bun run typecheck` (node, integration, the plugins, web, public) | passed |

Inside the isolated runner (`changed-backend-tests`, which starts from an
empty environment with no cargo), the tests that drive the runtime used the
release build because it was newer than every crate source; one test skipped
there, the `cargo run` currency check, which the switch-off row above and
`tests/checked_in.rs` both ran.

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

Failures on the way in round 2; the results in the table are from after
their fixes.

- The first switch-on run of round 2 ended with 2 failed, 876 passed: two of
  the new seam tests compared fallback counts exactly, and
  `tests.cli_runtime_on` routes the reader class as well as the instance, so
  each call is counted twice there. They compare "at least" now, as the
  older tests in the file do. The switch-off runs before it could not show
  this.
- The first `--changed upstream/main` frontend run had 3 failures: the pnpm
  one above, `ciGate.test.ts` (the new CI job was not in its backend group)
  and `ciPathFilter.test.ts` (the new packaging test named a path under
  `backend/tests` as a string, which that ratchet reads as a frontend test
  reading backend tests). Both tests were corrected.
- The first full backend run of round 2 passed with every runtime test
  skipped: the isolated runner has no cargo on its `PATH`, and the tests had
  just been changed to build with cargo. The build helper's rule for that
  case (use the release build only when no source is newer) is the result.
- The crate's unit tests failed in about one run in five until the SQLite
  test was kept apart from the PTY test's fork (see "Found while fixing").

Round 1's two (three registry checks reading the spec sources by regular
expression; `test_acp_tui_unchanged.py` pinning `app._stop_log_watcher`) are
in the history of this file.

### CI run 43553202513 (round 2's push, `ee4d932d2`)

Three causes failed seven jobs (the gate job is the seventh, failing because
the others did). The first two are fixed; neither fix has run on the platform
it is for, so the next CI run is their first.

| Job | Cause | Fix |
|-----|-------|-----|
| Rust CLI runtime (Linux), "switch off" step: 52 failed, every one in `cli_runtime_build.cargo()` with "rustup could not choose a version of cargo to run … no default is configured". The steps after it did not run. | The suite's conftest points `HOME` at an empty directory before any test module is imported, and a rustup-managed cargo looks for its toolchains under `$HOME/.rustup`. The `cargo test` step before it ran in the step's own shell, with the real `HOME`. | `cargo()` runs cargo with `RUSTUP_HOME` and `CARGO_HOME` named from the account's home in the password database, unless the environment already names them. Test: `test_cargo_runs_with_the_accounts_rust_homes_not_the_suites_empty_home`. |
| Backend tests (Windows), all five shards: 26 failures, all `test_python_readers_match_the_parity_fixtures`. | The fixtures record what main's readers return on Unix. On Windows the same readers return the same paths with its separators (`<root>\share\…` in `file_path`, `path` and `cwd`, and a cwd of `\usr`); every differing line in the five logs is one of those. The same 26 failed on round 1's push (`aec1a324b`, run 43535726613), so this is not from round 2. | The test is skipped on Windows: the fixtures are for a runtime that is built on Unix only. The readers' own vendor tests still run there. |
| Backend tests (Windows, 3/5): `test_server_link_pin_audit.py::test_forget_called_after_settle_promotion[file]`, "condition never became true" after its 5 s wait. | Not from this branch, which changes no server-link code or test (`git diff upstream/main...HEAD` over those files is empty). It passed in the same shard on round 1's push. A wait that timed out once is all the log shows; the cause is not established. | None. |

Reproduced before the fix, on macOS, by putting a rustup-managed cargo first
on `PATH` with no `RUSTUP_TOOLCHAIN` set. This machine's environment names
its toolchain there, and with it named rustup does not fail under the
redirected `HOME`: it downloads and installs that toolchain into the empty
directory (`cargo --version` run that way printed "the missing active
toolchain … has been auto-installed"). So the round 2 runs passed here by
installing a toolchain into each run's throwaway home, which the fix also
stops.

- `cargo --version` with `HOME` at an empty directory printed the same rustup
  error as CI.
- `test_the_checked_in_declarations_are_current`, with the conftest's own
  `HOME`, failed with it too.

After the fix, in that same environment with `NAVIDE_CLI_RUNTIME_REQUIRED=1`,
the three files of the failed step passed (158). The new test, run with the
fix taken out, failed: the stand-in cargo was given no `RUSTUP_HOME`.

Why the macOS leg of the same job passed with the same rustup setup is not
established: nothing in its log shows how its cargo found a toolchain. (A
hypothesis only: that runner's environment names a rustup home or a
toolchain, as this machine's does.)

Re-run after these fixes, in the usual environment:

| Command | Result |
|---------|--------|
| switch off: the three files of the failed step, `test_cli_vendors_registry.py` and `test_pyinstaller_spec.py` | 193 passed, 1 skipped (Windows only) |
| switch on: the command in the table above | 879 passed, 4 skipped; fallback 0 for every observed CLI |
| `node scripts/changed-backend-tests.mjs -- -k "cli_runtime or cli_declarations"` (the isolated runner) | 159 passed, 1 skipped (the `cargo run` currency check; no cargo there) |
| `uv --project backend run ruff check` on the three changed files | all checks passed |

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

- **A release dry run.** Packaging is wired and tested as text
  (`cliRuntimePackaging.test.ts`), and the build script was run locally, but
  no release or dry run was run: the signed and notarized Mach-O, and the
  binary inside the Linux packages, are unverified until one is.
- **Linux.** Nothing of this crate or its backend tests was ever run on
  Linux locally (no Linux machine or container here). CI's `cli-runtime` job
  is its first run there.
- **The wire.** Still one JSON line per request over stdio; the framed duplex
  channel is planned in DESIGN.md, with the per-file header value.
- **The harness.** The perf harness was not re-run after the read-path fix.
- **opencode / kilo port debt** (review 2-A): the Rust path for copilot,
  opencode and kilo still depends on the Python reader for a failing query;
  deferral is per CLI, not per database; a busy database is read twice per
  poll.
- **Rule-coverage tracing** beyond rule matches (review 2-A N9).
- **A Settings toggle** for `rustCliRuntime`.
- **PTY ownership** (`session.rs` is kept, unwired), **Windows ConPTY** and
  **Windows file identity**.
- **devin, kiro, mcode**: main has no log reader; launch and resume only.
- **copilot's 1.0.75 `events.jsonl`** stays main's reader's (it reconciles
  against the store and needs a YAML file for its cwd).
- The first brief said 21 CLIs; main's registry has 20.
