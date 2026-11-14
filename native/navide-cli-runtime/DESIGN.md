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

## A new CLI in the product: which files

The same holds outside the crate. The application keeps no list of CLIs:

- The backend's vendor registry (`cli_vendors/registry.py`) is the declared
  CLIs in the order they declare (`Cli::order.registry`). For each it imports
  the module named after the key, `cli_vendors/<key>.py`, and takes its
  `SPEC`; a declared CLI with no such module is registered from its
  declaration alone.
- The frontend's agent list (`agents/index.ts`) is the declared CLIs in
  `Cli::order.menu`. Spec files are found with `import.meta.glob`; a declared
  CLI with no `agents/<key>.ts` gets its spec from the declaration alone. The
  `AgentKey` type is the declared keys.

The packaged backend lists nothing either: its PyInstaller spec collects the
`cli_vendors` package (`collect_submodules`), and a test runs that collection.

So a new CLI needs exactly:

1. `src/vendors/<key>.rs`: the declaration.
2. The two generated copies of `declarations launch`, rewritten by
   `NAVIDE_DECLARATIONS_WRITE=1 cargo test --test checked_in` and committed.

With those it is in the menus, launches, and resumes if it declares how. No
shared file is edited, and no test list: the recording tests
(`cli_declarations_main.json`, `MAIN_REGISTRY_ORDER`, `MAIN_ORDER`, `MAIN`)
hold main's CLIs to main's values and compare only the keys they recorded.
Two more files exist only where the application does more for the CLI than
launch it, and each is found by its name:

3. `backend/agent_team_backend/cli_vendors/<key>.py`: credentials, usage,
   install detection, hooks, and the Python log reader (which still reads the
   records whenever the switch is off, the default).
4. `src/renderer/src/platform/plugin-shell/agents/<key>.ts`: capability
   flags, prompt patterns, quota semantics.

A CLI with files 1 and 2 only is registered on both sides from its
declaration alone, and a backend and a frontend test declare a fake CLI of
that kind to show it. The repository's other gates still name every CLI. They
were found by declaring a throwaway CLI of that kind (never committed) and
running both suites, and they are what is left of "one declaration plus its
tests": the regression inventory (`tests/fixtures/cli-regression/catalog.json`
with its fixture, and a row in `tests/cli/vendorContracts.test.ts`: tests, as
they should be), and the older lists of `docs/adding-a-cli-vendor.md`
("Other hand-kept lists"): eight backend tests (launch argv inventory, the
README sentence, the Help panel table, `helpCliFacts.ts`, native memory,
verified version, risk declarations, the extensions declaration) and three
frontend lists (the plans plugin's retained specs, `TURN_START_SIGNAL`, the ＋
menu's cap).

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
vendor's name anywhere in a read: the vendor functions take their inputs as
generic parameters too (`Vendor::probe` is generic over the query function it
is handed), and `src/` holds no `dyn` at all. The one dispatch by key is
`AnyObserver`, an enum over the compiled observers, at the wire where a
request names its CLI.

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
move a cursor; the core does all of that. Three exceptions touch something
outside the record, and each says why at its definition: `Vendor::init` (a
slot's first value, from the transcript's path), `Vendor::probe` (which
schema a database holds, through the one query function it is handed), and
antigravity's workspace text, which asks the disk which of several candidate
paths is a directory (main's heuristic; no record can carry it).

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
for codex, `JsonText` for grok), the tracker's explicit `Close` and
`Cancel` with a millisecond clock (kimi is the one CLI whose transcript ends
and cancels turns itself), and a slot seeded from the database being walked
(`StoreSeed`, antigravity: the query runs on the walk's own connection, so
the core has to make it).

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
it; a store without them is `Unsupported`. That holds for every CLI: a slot
whose first value is in the database (`StoreSeed`) is read on that same
connection, and nothing else in the crate opens one. Partition labels that resolved are
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
the observer of the request's CLI is replaced (what it remembered is only an
optimisation; every other CLI's observer keeps its memory), and the process
keeps serving. Off Unix
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
stays on Python until the switch is flipped again, and says so once. A
failure is a process, not a caller: every started process has a generation,
and the callers that were all waiting on one process when it died report one
failure between them. An error the process answers on every poll is logged
once per CLI and kind. After the backend's shutdown has stopped the process,
no late reader call starts it again. Every call's outcome is counted per CLI
(`rust`, `unsupported`, `unreadable`, `deferred`, `fallback`) and reported by
`/health`.

### What a routed call costs, and the next wire

Measured by `backend/tests/bench_cli_runtime.py` on a Claude transcript that
grows by one record per poll (macOS, release build; microseconds per call,
median): main's reader about 23, the routed call about 62, at any transcript
length. Of the 62: the pipe round trip with the runtime's own request
handling 12, the seam's two helper threads and the caller's queue 7, the
read itself 35 (two file opens: the slot header and the tail), JSON on the
Python side 3.

So a poll that reads one small record is slower through the process boundary
than in Python, and will stay so on this wire: about 19 of the 62 are the
boundary itself. What the runtime buys today is not the per-call time; it is
that the reads are one declaration per CLI and leave the Python process.

Left on the table on purpose:

- **The header slot is read on every call** (about 15 of the 35). main's
  Claude reader keeps the cwd per file and inode. Doing the same needs the
  observer's memory passed into slot initialisation at seven call sites and a
  declared "kept" initialiser, since each CLI's Python reader differs in
  whether it keeps the value. It belongs with the next wire.
- **One request, one round trip.** The seam routes three reader methods, and
  the watcher calls them one file at a time.

The wire has to change shape before PTY or WebSocket work, and that is where
both of those go: a framed duplex channel (a local socket, length-prefixed
frames) with the runtime pushing events instead of answering polls. Requests
then batch, a watcher pass over N files is one frame each way, and PTY output
and WS fan-out have the stream they need. `wire.rs` and the seam are replaced
by it; the vendor, backend and declaration layers carry over unchanged. Not
done in this round: the transport stays stdio JSON lines. The process model
and the four open points of the channel are in the next section; none of it
is built.

The two sides keep one state format, so the switch can flip mid-session:
usage checkpoints are main's JSON, and the activity state is the watcher's
seen-keys bag in the key formats main's readers use (`observe/bag.rs`). A
cwd is handed on as the transcript wrote it; where main normalises it (claude:
NFC) the declaration says so (`cwd_form`) and the seam applies it, so the
crate needs no Unicode tables.

### The next stage: process model and channel (planned, not built)

#### Two processes from one crate

Today one process does the reads, and the seam's whole recovery is "kill it":
a call that waits longer than `CALL_TIMEOUT_S` kills the process
(`cli_runtime.py::_process_failed`), every call in flight falls back, and
every observer loses the point its walk had reached. That is right for reads,
whose memory is an optimisation. It cannot be the process that owns the
panes: killing it would close every PTY master and send every agent SIGHUP.
So the stage splits the runtime into two processes built from the same crate:

- **The PTY host** owns the panes (`session.rs`: spawn, I/O, resize,
  signal). It **never opens SQLite** and **the seam never kills it**. It ends
  when the backend shuts it down, and nothing about a stalled read can reach
  it.
- **The observe worker** is today's `serve`: the readers and the observers'
  resume memory. It **may be killed and restarted**. The recovery in the
  section above (timeout, kill, growing delay, the breaker after five
  failures, the generation count) applies to the observe worker only.

The split also makes the one open question about `session.rs` moot instead of
answered. On macOS, in this crate's unit tests, a child forked while another
thread held a SQLite connection (the system library) was killed with signal 9
before it wrote anything, about one run in five; it did not happen once the
two were kept apart (`session::FD_FORK_LOCK`). The cause is not established.
With the host never opening a database, nothing there forks beside one, and
the lock stays only as the precaution it is in the tests.

A smaller change goes with the next wire: today one file that stalls (a
transcript on a stalled network volume) costs the whole worker. A **per-file
circuit** (stop routing a file that timed out, for a while, and let every
other file keep its reads) would limit that to the file, and the worker
would be killed only when it is itself unresponsive. It needs no new process;
it needs the request to name its file in the channel, which the channel below
does.

#### The framed duplex channel: four points the plan must settle

1. **Where the WebSocket ends.** If PTY bytes went runtime, local socket,
   Python, WebSocket, the Python event loop would stay on the hot path, and
   the lag with hundreds of agents would stay with it. That hop is
   **temporary**, a step that lets the host be built and tested behind the
   backend's existing WebSocket. The end state is that **the runtime serves
   the terminal stream itself**: the host owns the socket the renderer
   connects to, and Python keeps the control plane (spawn requests,
   attribution, settings), not the bytes.
2. **Who owns checkpoints and the seen-keys bag once the runtime pushes.**
   Today both sides share one state format so the switch can flip
   mid-session (usage checkpoints are main's JSON, the activity state is the
   watcher's bag). A runtime that pushes events must own that state: a bag
   kept in two places cannot say what was already emitted. The plan: the
   worker owns both while the switch is on; the format stays main's, and
   turning the switch off or on is a **handover** (the owner writes its state
   out in that format at a checkpoint boundary, the other reads it), not a
   shared object. What gives way is "flip at any instant"; what stays is "flip
   without losing or repeating an event".
3. **The frame format.** PTY output is bytes, so a frame is a header, then a
   raw payload, with no JSON wrapped around it: `type` (1 byte), `stream id`
   (4 bytes, big endian), `length` (4 bytes, big endian), then `length`
   bytes. Control frames (open, resize, exit, a batch of reads, an event) have
   a type of their own and carry JSON; `data` frames carry the bytes as they
   came off the PTY. A reader that meets an unknown type skips `length`
   bytes, so the format can grow.
4. **Backpressure per stream.** One pane that floods its output must neither
   starve the others nor grow memory without bound: each stream has a
   bounded queue and its own window, and the writer serves the streams in
   turn. The limit is the repo's rule that **scrollback is never cleared**: a
   slow consumer is answered by pausing the read of that one PTY (the kernel's
   PTY buffer then slows the child), never by discarding what was read or
   emitting a clear. The host keeps each stream's scrollback itself, so a
   consumer that reconnects replays from it. The backend's reader today drops
   its oldest queued output at a cap (`test_terminals_reader_pause_bound.py`);
   whether the host may keep that behaviour, or must pause instead, is decided
   against that rule before the host is written.

Windows is its own stage: no file identity, no ConPTY, no named-pipe
transport, so none of this exists there yet.

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
`src/renderer/src/platform/plugin-shell/agents/_cli_declarations.ts` are the
output of `declarations launch`, checked in: the JSON as printed, and the
same JSON as a typed constant (two copies because plugin-shell imports
nothing from outside itself; a constant because a `.json` import would widen
the agent keys to `string`). `cli_vendors/_declared.py` and
`agents/_declared.ts` turn an entry into the fields each spec used to type by
hand.

Three tests hold them:

- `tests/checked_in.rs` (this crate, plain `cargo test`, no binary) compares
  both copies with what the source prints, and rewrites them with
  `NAVIDE_DECLARATIONS_WRITE=1`. A declaration changed without regenerating
  fails here.
- `backend/tests/test_cli_declarations.py` runs `cargo run -- declarations
  launch` and compares the backend's copy with it, compares every field with
  a recording of main's hand-written values, and runs both resume-id readers
  (the backend's scan and `resume.rs` through the `resume_id` op) over the
  same recorded commands.
- `agents/__tests__/declared.test.ts` compares the frontend specs with a
  recording of main's: the fields, the display order, and the pattern each
  spec recognizes a resume command by.

The backend tests that run the runtime never use a binary built earlier:
`backend/tests/cli_runtime_build.py` has cargo bring the release build up to
date first. Where cargo is expected (`NAVIDE_CLI_RUNTIME_REQUIRED=1`: CI's
`cli-runtime` job) a runtime that cannot be built fails the test; a job that
compiles no Rust says so (`=0`) and those tests skip there.

A frontend spec recognizes a saved resume command by the pattern derived
from its declared resume arguments (the command Navide itself builds). Seven
specs keep a hand-written pattern after the spread because they recognize
more than that: a short-flag alias (kilo, kimi, opencode), the `=` and the
spaced form both (copilot, cursor, openclaw), a former program name
(cursor), arguments between the base command and the flag (hermes,
openclaw); aider's is for its id-less restore. Six that only repeated the
declared command are gone.

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
- **Windows.** No Windows package carries the binary and no Windows job
  builds it (`scripts/build-cli-runtime.mjs` does nothing there).

Packaging is wired but not yet exercised: `scripts/build-cli-runtime.mjs`
stages the release binary in `build/cli-runtime/`, the macOS and Linux
packages copy it to `<resources>/bin` (where the seam looks,
`frozen_env.packaged_bin_dir()`), and `release.yml` builds it beside the
speech sidecar and verifies its signature on macOS. No release or release dry
run has been run with it.
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
