# navide-cli-runtime: one declaration per CLI

## Shape

The crate is organised around what every agent CLI goes through. A CLI is one
file, `src/vendors/<key>.rs`. What is live today, and what is not:

| Stage   | Where                                         | State |
|---------|-----------------------------------------------|-------|
| launch  | `decl::Launch` (data)                         | **The source.** `navide-cli-runtime declarations launch` prints it; the backend's vendor registry and the frontend's agent specs are built from that output. The argv is still assembled by the frontend and the environment by the backend, from these fields. |
| resume  | `decl::Resume` (data), `resume.rs`            | **The source.** Same export. `resume.rs` reads a session id out of a saved command; the backend reads the same declaration with its own scan, because it must work with no process running, and a test holds the two together. |
| observe | `observe/`, `decl::Observe`                   | **Live behind a switch** (`rustCliRuntime`, off by default): token usage, activity events and per-turn usage for 17 of the 20 CLIs. |
| session | `session.rs` (Unix PTY: spawn, I/O, resize, signal) | **Not wired in.** Nothing calls it: panes are still spawned by the backend's `terminals.py`. It is kept, with its tests, as the piece PTY ownership will start from. There is no Windows ConPTY. |

`build.rs` lists `src/vendors/` and generates the module list and one macro
(`each_vendor!`); `registry.rs` expands that into everything keyed by name.
Adding a CLI is its file: no registry edit, no second declaration site.
`tests/extensibility.rs` declares a CLI the crate has never seen, outside
`src/`, and runs its lifecycle.

## A vendor is a type (`decl::Vendor`)

```rust
pub struct V;
impl Vendor for V {
    type Slot = Slot;        // slots! { Cwd = "cwd", Reply = "reply" }
    type Ext = Ext;          // what only this CLI extracts (NoExt: nothing)
    type Backend = Jsonl;    // how its records are stored
    const CLI: &'static Cli = &CLI;
    const OBSERVE: Option<&'static Observe<Self>> = Some(&OBSERVE);
    fn text(ext: Ext, scope: &Scope<'_, Self>) -> String { … }   // optional
}
```

The core is generic over that type: `Observer<V>`, `Walk<'_, V>`,
`Backend<V>`. Each vendor is compiled into its own copy of the engine
(monomorphised), so there is no `dyn`, no table lookup and no comparison of a
vendor's name anywhere in a read. The one dispatch by key is `AnyObserver`, an
enum over the compiled observers, at the wire where a request names its CLI.

What this buys, as the compiler checks it:

- **Slots are typed.** `slots!` makes an enum; `Text::Slot(Slot::Reply)` with
  a misspelt slot does not compile, and a slot lookup is an array index.
- **A backend has its own declaration type** (`Backend::Spec`:
  `JsonlRecords`, `TextRecords`, `SqliteRecords`). A row cursor over a text
  file, or a SQLite usage mode on a JSONL transcript, cannot be written down.
- **No `unreachable!`.** Where a declaration and a store still cannot be
  matched at compile time (exact turns on a database), the answer is
  `Error::Unsupported`.

## What is shared data, and what is a vendor's own

Rules, cursors, checkpoints, the seen-keys bag and the inferred-turn tracker
are declared data that the core runs. So is a small record-access language,
evaluated by `expr.rs` with Python's semantics where main depends on them
(`str(x or "")`, `max(0, int(x))`, `str.strip()`, truthiness):

- `Pred`: equality, membership, presence, truthiness, shape, a test on
  computed text, any-item, slot set, all / any / not.
- `Text`: literal, field, slot, first-of, concat, content blocks, joins over a
  list, tag values, user-prompt normalisation, strip, cap, conditional, a
  number as text, the walk's line and offset, the running totals.
- `Num`: zero, a Python int at a path, a sum.

The rule for that vocabulary: **a variant is there because two or more
declarations use it, or because it is plainly generic.** Checked by grep over
`src/vendors/`: every `Pred`, `Text`, `Num`, `StrTest` and `SlotInit` variant
that reads a record has at least two users. The generic ones, kept whatever
their count, are the constants and combinators (`True`, `Lit`, `Zero`, `All`,
`Any`, `Not`, `FirstOf`, `Concat`, `If`, `Sum`) and the walk's own facts
(`Line`, `Offset`, `TotalIn`, `TotalOut`).

A value only one CLI reads its own way is that CLI's `Ext`: a typed id in the
declaration, evaluated by the vendor's function (`Vendor::text`, `num`,
`pred`, `init`, `probe`). Those functions are pure: the record, the slots and
the walk's facts in, the value out. They never keep state, emit an event or
move a cursor; the core does all of that. Two exceptions touch something
outside the record, and each says why at its definition: `Vendor::init` (a
slot's first value, from the transcript's path) and `Vendor::probe` (which
schema a database holds, through the one query function it is handed).

| CLI | Its `Ext` |
|-----|-----------|
| aider | the section header and token-line grammars of its Markdown history, a pane namespace from the file name or an MD5 of the resolved path |
| antigravity | text and timestamps inside undocumented protobuf blobs; the workspace chosen among `file://` copies by what is a directory on disk |
| claude | cwd from the lossy folder name; the "[Request interrupted" note anywhere in a content tree |
| codex | "this walk started past the header"; the cost ledger's uncached input |
| cursor | the `<user_query>` wrapper, Navide's own session marker, one clock per pass |
| goose | a seconds stamp, a tool name inside content, `false`-not-missing metadata flags |
| grok | cwd from a URL-encoded directory name, a millisecond stamp, the model as a map's first key |
| hermes | the lineage's Navide session name, a float stamp |
| kimi | an epoch-millisecond stamp as digits, "the open turn was cancelled" |
| muse | a microsecond stamp, the session id from the directory |
| openclaw | a stamp that is ISO or a number, usage counters that may be strings or floats |
| opencode, kilo | which of two schemas the database holds; a millisecond stamp |
| pi | the id after the file stem's first `_` |
| qwen | the id a tool result says it launched in the background, a notification's status |

droid's and copilot's records need nothing of their own: both declare `NoExt`.

Single-user items that stay in the shared part are mechanics, not value
extraction: how a kept slot is written into the bag (`KeptForm::TextOnChange`
for codex, `JsonText` for grok), and the tracker's explicit `Close` and
`Cancel` with a millisecond clock (kimi is the one CLI whose transcript ends
and cancels turns itself).

## A new store shape is a core change, by design

The promise is: **a CLI whose records fit a store shape the core already has
is one declaration file.** It is not: any CLI at all without touching the
core. How records are stored is where tailing, state and checkpoints live,
and that is the core's job (`Backend`):

- `Jsonl`: a JSON object per line. Usage reads a byte tail with identity,
  shrink and anchor checks (`FileUsage`: per record with a recent-key window,
  cumulative totals differenced, or a sidecar snapshot). Activity walks lines
  from a dense mark (`LineCursor::Lines`) or a byte cursor (`Bytes`).
- `TextLog`: plain text, each line a `{"line"}` record; the walk replays the
  whole file and only lines past the mark emit.
- `Sqlite`: one database for every session, rows read into JSON objects by
  column name, binary columns kept as bytes beside them (`Scope::blob`).
  Usage: a row-id watermark (`After`), an anchored one (`Anchored`, optionally
  with rows that are rewritten until complete), per-key counters
  (`Counters`), per-partition watermarks (`Watermarks`), or main's legacy
  checkpoint with no token records (`Legacy`). Activity: rows after one
  watermark (`Rows`), several tables each with its own (`Tables`), or one
  watermark per partition (`Partitions`).

Several of these serve one CLI today (cumulative totals: codex; sidecar
snapshot: droid; counters: hermes; partitions: openclaw; settling walk rows:
antigravity; the text log: aider). They are in the core because a cursor and
its checkpoint cannot be a pure function of one record. A CLI with a store
none of them describes (a new file format, a new way of resuming) needs a new
variant there, with its checkpoint format, and that is a reviewed change to
`observe/`, not a vendor file. `Vendor::probe` with `SqlUsage::OneOf` /
`SqlWalk::OneOf` is the one construct for a store that changes shape between
versions of a CLI: the variants are declared, the choice is the vendor's.

### SQLite reads

One connection per observer call (`db.rs`, `Conn`), opened read-only the way
main's readers open one, with the declared `requires` columns checked once on
it; a store without them is `Unsupported`. Partition labels that resolved are
kept in the observer between calls, unresolved ones are not (main's cache). A
query that fails is "no rows this cycle", as main treats it, unless the
declaration says the store's reader reports such a failure
(`Unreadable::Unsupported`: copilot, opencode, kilo), in which case the whole
call is declined and that reader makes it.

## The wire and the seam

`navide-cli-runtime serve` reads one JSON request per stdin line and writes
one JSON answer per stdout line (`wire.rs`). Four worker lanes; the reads of
one file always go to the same lane, so they stay in order and the reader
memory for that file lives in one place, while different files are read side
by side. Each request runs under `catch_unwind`: a panic answers an error,
the lane's observers are rebuilt, and the process keeps serving. Off Unix
(no file identity, so checkpoints could never match Python's) `observes`
answers nothing and the backend routes nothing.

An answer carries, beside its result:

- `notes`: what main's reader would have logged while reading (a session no
  table gives a key). The seam logs each once per reader, through the
  reader's own logger.
- `reads`: outcomes for the read-problem registry a reader keeps (OpenCode's
  and Kilo's, which the CLI's status shows). The seam records and clears them
  through the reader's own methods.
- a failure's `kind`: `unsupported` (the declared "this one is the Python
  reader's"), `io`, `decode`, `request`, `panic`.

The backend seam (`backend/agent_team_backend/cli_runtime.py`) routes three
reader calls (`parse_incremental`, `parse_activity`, `turns_for_session`)
when the switch is on. A writer thread and a reader thread own the pipes; a
caller queues its request and waits for its answer with a timeout, holding no
lock while the process works. A call that times out kills the process and is
answered by Python. Turning the switch off sets a flag and kills the process
without taking any lock a call holds. A process that keeps failing is
restarted after a growing delay, and after five failures in a row the runtime
stays on Python until the switch is flipped again, and says so once. Every
call's outcome is counted per CLI (`rust`, `unsupported`, `unreadable`,
`deferred`, `fallback`) and reported by `/health`.

The two sides keep one state format, so the switch can flip mid-session:
usage checkpoints are main's JSON, and the activity state is the watcher's
seen-keys bag in the key formats main's readers use (`observe/bag.rs`). A
cwd is handed on as the transcript wrote it; where main normalises it (claude:
NFC) the declaration says so (`cwd_form`) and the seam applies it, so the
crate needs no Unicode tables.

### Who reports a store that cannot be read

Rust reports what it decides; Python reports what Python decides.

- What a **probe** finds is the runtime's to say: OpenCode's schema check
  (a recognised schema, a database a 1.x binary still writes, a half-upgraded
  one, an unknown one) travels as a `reads` entry under the same key main's
  reader uses, so the status reads the same whoever read the store.
- A **query that fails** is not reported from here. The call answers
  `Unsupported`, and main's reader makes the same read and records why in its
  own words (its classification of transient errors, sqlite's message). While
  that reader has a failing query on record it makes its own reads
  (`reads_itself()`), so the query that recorded the problem is the one that
  clears it.

## Launch and resume have one source

`backend/agent_team_backend/cli_declarations.json` and
`src/renderer/src/platform/plugin-shell/agents/cli_declarations.json` are the
output of `declarations launch`, checked in (two copies because plugin-shell
imports nothing from outside itself). `cli_vendors/_declared.py` and
`agents/_declared.ts` turn an entry into the fields each spec used to type by
hand. `backend/tests/test_cli_declarations.py` fails when either file is not
what the built runtime prints, compares every field with a recording of
main's hand-written values, and runs both resume-id readers (the backend's
scan and `resume.rs` through the `resume_id` op) over the same recorded
commands.

## Tests are the contract

- **Parity fixtures** (`tests/parity/*.json`) are recorded from main's Python
  readers only (`backend/tests/test_cli_runtime_parity.py`,
  `NAVIDE_PARITY_WRITE=1`), built from the vendor tests' own record builders.
  `tests/parity.rs` replays each through the Rust observer: usage events,
  checkpoints, activity events, the bag, turns and, where recorded, the open
  read problem. The Python test replays each through the seam and requires
  that Rust answered every call.
- **Every declared rule has a record.** While it replays, the parity test
  traces which activity and usage rules matched (`observe/trace.rs`, off
  outside tests: one relaxed atomic load per matched rule) and fails for a
  rule no fixture reaches. A new declaration cannot ship a rule nobody
  compared with main. Not traced: the effects inside a rule and the parts of
  an exact-turns declaration.
- **`tests/declarations.rs`** checks what the compiler cannot: kept slots and
  their bag keys, tracked turns and their reply slot, bound query parameters,
  marks kept under distinct keys.
- **`pytest -p tests.cli_runtime_on`** runs any backend suite with the switch
  on, prints who answered each routed call per CLI, and fails on a fallback
  or on a declined store that is not listed with its reason.

## Not declared, not wired

- **devin, kiro, mcode**: main has no log reader for them; launch and resume
  are declared (`unobserved!`).
- **copilot's 1.0.75 `events.jsonl`**: it reconciles against the central
  store (a session the store lists is skipped) and reads its cwd from a YAML
  file, so it stays main's reader's: any file but `session-store.db` answers
  `Unsupported`.
- **opencode and kilo on Windows**: OpenCode 2 stores a Windows path with
  forward slashes and main turns it back; the runtime observes nothing off
  Unix, so that conversion is not written.
- **PTY ownership** (`session.rs`), **Windows ConPTY** and **Windows file
  identity**.
- **Packaging.** The seam looks for the binary where a packaged app keeps its
  other sidecars (`frozen_env.packaged_bin_dir()`), but no build script
  compiles it into the app bundle yet: in a packaged app the switch finds no
  binary, says so once, and stays on Python.
- **A Settings toggle.** The switch is a key of the UI settings document.

## Decisions recorded

- Declarations are Rust statics, not JSON or TOML: typed at compile time, no
  parse cost, exported as JSON rather than kept twice.
- A family of CLIs with one store (OpenCode and its fork Kilo) shares one
  declaration through a macro (`vendors::opencode::store!`) expanded for each
  vendor type: one source, two monomorphised copies.
- No regex dependency: main's patterns are hand-written matchers in the
  vendor files, unit-tested against main's cases. BLAKE2b (checkpoint
  anchors) and MD5 (aider's namespace) are hand-written too, to keep the
  dependencies at serde, serde_json, libc and (Linux) libsqlite3-sys.
- A naive ISO timestamp is local time, as Python's `fromisoformat().timestamp()`
  reads it: `time.rs` follows CPython's algorithm over `libc::localtime_r`.
- Text files read like main's text mode: invalid UTF-8 dropped, and `\r\n`
  and a lone `\r` end a line as `\n` does, in the walk and in the whole-file
  usage pass behind turns. The byte tail splits on `\n` only, as main's
  binary `readline` does.
- SQLite cells follow main's `sqlite3` reads: TEXT must be UTF-8 or the query
  fails; a BLOB in a JSON column is parsed, else `null`; declared binary
  columns are bytes.
- Resume memory (where a line walk stopped) and resolved partition labels live
  in the process, as they do in main's reader objects; both are optimisations
  and never change results.
- `launch.rs` (argv and environment assembly in Rust) was removed: nothing
  outside a test called it, and the frontend and backend assemble theirs from
  the exported fields. It comes back with PTY ownership.
