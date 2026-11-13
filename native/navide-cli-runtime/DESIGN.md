# navide-cli-runtime: one declarative CLI lifecycle

## Shape

The crate is organised around what every agent CLI goes through, not around
its log files. Each stage is a core module that runs from a CLI's declaration:

| Stage   | Core (`src/`)                                   | The declaration supplies                       |
|---------|-------------------------------------------------|------------------------------------------------|
| launch  | `launch.rs`: argv, env merge and strip          | program, base/yolo args, model/effort flags    |
| session | `session.rs` (Unix): PTY, spawn, I/O, kill      | the interrupt byte                             |
| observe | `observe/`: tails, cursors, usage, activity     | where records live, their format, record rules |
| account | `observe/turns.rs`, the inferred-turn tracker   | which records open, call and close a turn      |
| resume  | `resume.rs`: id from a saved command, argv      | resume syntax as data                          |

A CLI is one file, `src/vendors/<key>.rs`, holding one `pub static CLI: Cli`.
`build.rs` lists that directory and generates `vendors::ALL`, so adding a CLI
is that file plus its tests: no registry edit, no second declaration site, no
generated per-vendor types. `navide-cli-runtime declarations` prints the same
statics as JSON. Nothing in the core names a vendor (`tests/declarations.rs`
checks every declaration against what the core can run;
`tests/extensibility.rs` declares a CLI the crate has never seen and runs its
whole lifecycle).

## Declarations are typed static data (`src/decl.rs`)

Everything is `&'static` slices, string literals and enums: no allocation, no
parsing, checked by the compiler, matched by the core with `match`, so a
vendor costs no dynamic dispatch and no string comparison of vendor names.
Record access is a small typed language evaluated by `expr.rs` with Python's
semantics where main depends on them (`str(x or "")`, `max(0, int(x))`,
`str.strip()`, truthiness):

- `Pred`: equality, membership, presence, truthiness, shape, text tests,
  any-item, slot set, `Fresh`, all/any/not.
- `Text`: field, slot, first-of, concat, content blocks, joins, tag values,
  prefixes, user-prompt normalisation, caps, ISO stamps from epochs, protobuf
  text and timestamps out of binary columns, a per-walk clock, hooks.
- `Num`: Python ints, floats, sums, differences, map sums, cases, parsed text.

`Observe { store, format, accept, slots, usage, activity, turns }`:

- **Formats.** `Jsonl` (a JSON object per line), `Text { kind }` (aider's
  Markdown: each line a `{"line"}` record) and `Sqlite` (rows of declared
  queries as JSON objects; JSON columns parsed, binary columns as hex).
- **Slots.** Per-file values (cwd, session id, pending reply text) with their
  initialisers: file stem, directory names, header records, JSON sidecars,
  joined query text, the file path or its directory.
- **Usage.** Ordered `UsageRule { when, effects, reading }`. Modes:
  `PerRecord` (recent-key window, credited count across rewrites),
  `Cumulative` (running totals differenced), `Snapshot` (totals in a sidecar
  document), `Counters` (a row per key with running counters), `Watermarks`
  (rows read per partition), and `Legacy` (no token records: main's base
  checkpoint kept).
- **Activity.** Ordered `Rule { when, effects }`; effects emit events, set or
  append slots, drive the inferred-turn tracker (open, ensure-open, touch,
  close, cancel) and stop. Cursors: `Lines` (dense line mark, resumable by
  byte offset), `Bytes` (a byte tail), `Rows` (row-id watermark, optionally
  per partition, with held last rows and settling rows re-read until done) and
  `Replay` (the whole text file each walk; already-walked lines rebuild state
  but do not emit).
- **Turns.** `Exact` (the transcript names its turns: by key, per prompt, per
  closing record), `Inferred` (main's base cut on `turn_complete` stamps) or
  `Unsupported`.

## Shared state with Python

The backend seam (`backend/agent_team_backend/cli_runtime.py`) routes three
reader calls (`parse_incremental`, `parse_activity`, `turns_for_session`) to
one `navide-cli-runtime serve` process when the `rustCliRuntime` setting is on
(off by default; off means main's readers and no process). Every other reader
method stays Python. The two sides keep one state format, so the switch can
flip mid-session: usage checkpoints are main's JSON (`{kind:"jsonl", offset,
identity, anchor}`, `aider-md`, `sqlite`, per-partition maps, the legacy
seen-set), and the activity state is the watcher's seen-keys bag in the key
formats main's readers use (`act_hw::N`, pending-text prefixes, turn-tracker
JSON, row cursors, partition marks and anchors, settling-row lists). Any error
answer falls back to the Python reader for that call; the Rust side then does
not store the bag. Where main's reader would log a warning as a side effect
(a schema it does not know, a session it cannot key), the runtime answers
`Unsupported`, so Python runs and logs.

## Hooks

A hook is a plain `fn(&str) -> String` inside `Text::Hook`, allowed only where
the behaviour cannot be data, with the reason written next to the function:

| Hook                         | CLI         | Why it is not data |
|------------------------------|-------------|--------------------|
| `session_name`               | hermes      | `<name> #<n>` continuation names parsed with a digit pattern |
| `workspace`                  | antigravity | picks among `file://` copies in a protobuf blob by "is a directory on disk", count, length |
| `stamp` / `iso`              | aider       | the fixed-width `# aider chat started at` date pattern |
| `sent` / `received`          | aider       | the loose `> Tokens: 1,234 sent, 8.5k received` number grammar |
| `namespace`                  | aider       | a pane token from the file name, else MD5 of the resolved path |

No regex dependency: these replace main's `re` patterns with hand-written
matchers, which are unit-tested against main's cases.

## Not declared here

Their readers stay Python whether the switch is on or off (`observe: None`):

- **opencode, kilo.** Every read updates a process-global problem registry
  (`read_problem()`, shown by `cli_get_status`); a read served by Rust could
  neither record nor clear an entry, so routing it would change main's
  diagnostics. They also choose between two schemas at runtime from the
  newest writes. Declaring them needs a diagnostics channel in the wire
  answer first.
- **copilot.** One reader serves two stores (`session-store.db` and
  `session-state/*/events.jsonl`), and the JSONL branch defers to the database
  for sessions it already lists, so one file's read depends on another
  store. Its cwd comes from `workspace.yaml`, which needs a YAML parser (a new
  dependency, not approved here).
- **devin, kiro, mcode.** main has no log reader for them; launch and resume
  are declared.

## Decisions recorded

- Declarations are Rust statics, not JSON or TOML: typed at compile time,
  zero parse cost, and exported as JSON instead of being kept twice.
- BLAKE2b (checkpoint anchors) and MD5 (aider's namespace) are hand-written to
  keep the dependency list at serde, serde_json, libc and libsqlite3-sys.
- SQLite cells follow main's `sqlite3` reads: TEXT must be UTF-8 or the query
  fails; a BLOB in a JSON column is parsed, else `null`; declared binary
  columns are hex.
- main's `\d` (any Unicode decimal digit) is approximated: ASCII digits in
  aider's matchers, `char::is_numeric` in hermes's. Naive ISO stamps are read
  as UTC (main: local time; only the inferred-turn cut uses them).
- `Format::Text` reads like main's text-mode file: invalid UTF-8 dropped, and
  in the activity walk `\r\n` and a lone `\r` end a line as `\n` does. The
  byte tail (usage) splits on `\n` only, as main's binary `readline` does.
- Resume memory (where a line walk stopped) lives in the process, as it does
  in main's reader; it is an optimisation and never changes results.
- Windows ConPTY and Windows file identity are not built in this round.
