# navide-cli-runtime: one declarative CLI lifecycle

## Lifecycle model

The runtime is organised around what a CLI *does*, not around its log files.
Every agent CLI goes through the same five stages, and the core runs each one
generically from that CLI's declaration:

| Stage   | The core owns                                              | The declaration supplies                         |
|---------|------------------------------------------------------------|--------------------------------------------------|
| launch  | argv assembly, env merge/strip                             | binary, base args, model/effort/yolo flags, env  |
| session | PTY open, spawn (setsid + ctty), read/write/resize, kill   | nothing beyond launch (interrupt byte only)      |
| observe | discovery, tailing, checkpoints, rotation, dedup, events   | where records live, their format, record rules   |
| account | usage records, turn grouping, the inferred-turn tracker    | which fields are counters, which records open/close turns |
| resume  | parsing a resume id out of a command, building resume argv | resume syntax as data                            |

`src/lifecycle.rs` holds the lifecycle types; `src/observe/` is the core
engine; `src/vendors/<key>.rs` holds exactly one `pub static CLI: Cli` per
CLI. Nothing in the core names a vendor.

## Declaration schema (`src/decl.rs`)

A declaration is plain typed static data (`&'static` slices, no heap, no
vtables), checked by the compiler:

- `Launch { program, base_args, yolo_args, model_flag, effort_flag, efforts, env_defaults, env_strip }`
- `Resume { syntax: ResumeSyntax, id_shape: IdShape }` — `Subcommand("resume")`,
  `Flag("--resume")`, `FlagEq("--resume=")`, `Unsupported`; parsing and building
  are the same table read in two directions.
- `Observe { store, format, session_id, cwd, usage, activity, turns, cursor }`
  - `store`: where records live (`Glob` under a root resolved from env vars +
    home-relative defaults, or one SQLite file).
  - `format`: `Format::Jsonl` or `Format::Sqlite { tables, queries }`. Both are
    generic backends implementing `Source` (static dispatch: the walk is
    monomorphised per backend).
  - `usage`: `UsageRule { when, input, output, cache_read, cache_creation,
    dedup, model, timestamp, mode }` with `mode = PerRecord { window, credited }`
    or `Cumulative { floor }` (running totals differenced by the core).
  - `activity`: ordered `Rule { when: Pred, effects: &[Effect] }`. Effects:
    `Emit { kind, key, detail, text, require_text }`, `Set(slot, Text)`,
    `Turn(TurnOp)` (open / ensure-open / touch / close / cancel on the core's
    inferred-turn tracker), `Stop`.
  - `turns`: `Turns { method, session_filter, open, call, close }` — grouping
    by key (Claude `promptId`), a new turn per prompt record (Codex), a turn per
    closing record (Grok), or the generic cut on `turn_complete` timestamps.
- Record access is typed data too: `Path` (`&[Seg]`), `Pred` (eq / in / present /
  starts-with / any-item / all / any / not), `Text` (field, first-of, join
  blocks, user-prompt normalisation, cap, collect, tag values, slot), `Num`
  (Python `max(0, int(v))` semantics, sums, differences, map sums).

## Hooks

A hook is allowed only where behaviour cannot be data. Each is a plain `fn`
pointer in `Hooks` (one indirect call per use, no trait object), and every
declaration that sets one must give `reason` next to it (`Hook { reason, f }`).
The full list:

| Hook                    | Used by | Why it cannot be data |
|-------------------------|---------|-----------------------|
| `decode_blob`           | cursor  | store.db `blobs` are undocumented protobuf; extracting text needs a byte walker, not a field path |
| `split_sections`        | aider   | the transcript is Markdown, sectioned by `# aider chat started at` headings; a record is a section, not a line |

Everything else in main's 17 readers (cumulative deltas, recent-key windows,
credited counts on rewrites, idle-flushed turns, background-task ids, Claude
`promptId` turns, Codex sub-agent suppression, Grok byte cursors) is a core
mechanism selected by data.

## Where declarations live

One place: `native/navide-cli-runtime/src/vendors/<key>.rs`. `build.rs` lists
that directory and generates the registry (`ALL: &[&Cli]`), so adding a CLI is
one new file plus its tests, with no edit to shared code. The binary exports
the same statics as JSON (`navide-cli-runtime declarations`), and that output
is what the Python side reads (`backend/agent_team_backend/cli_runtime.py`):
which vendors the runtime observes, and their resume syntax. There is no
second declaration site and no generated per-vendor code.

## Core state and checkpoints

The core owns every cursor. Checkpoints keep main's JSON shape so a pane can
move between the Python and the Rust path without re-crediting tokens:
`{kind:"jsonl", offset, identity:"dev:ino", anchor}` (BLAKE2b-64 over the 512
bytes before the offset), plus the declared slot names (`cwd`, `session_id`,
`recent_keys`, `input_total`, …). SQLite stores use `{kind:"sqlite", row_id,
identity, anchor}`. Activity state is one JSON object (`line`, `offset`,
`inode`, slots, the turn tracker) that Python keeps as one opaque entry.

Rotation (identity change, shrink, anchor mismatch) restarts at byte 0; a
partial trailing line is never consumed; an unterminated line stops an activity
walk before it, a terminated corrupt line is stepped over (main's GitHub #21
rule).

## Errors

Every operation returns `Result<_, ObserveError>`; I/O errors on a missing file
are an empty observation, not an error (main's behaviour). A line that is not
UTF-8 aborts the activity walk with the mark already advanced, exactly as main
does (listed as a defect in REDESIGN-REPORT.md, not changed).

## Process and PTY

`src/session.rs` (Unix): `openpty`, `fork`, `setsid`, `TIOCSCTTY`, signal
dispositions reset to default, `execvp`; master non-blocking and close-on-exec.
Windows ConPTY is not implemented in this round.

## Wire

`navide-cli-runtime serve`: one JSON request per stdin line, one JSON response
per stdout line (`{"op":"usage"|"activity"|"turns"|"declarations"|"resume_id"|"launch", ...}`).
The Python seam keeps one long-lived child per backend.

## Decisions recorded

- Declarations are Rust statics, not JSON/TOML: typed at compile time, zero
  parse cost, and the Python side reads the exported JSON instead of a second
  copy.
- No regex dependency: tag extraction (`<task-id>…</task-id>`) and the two
  Qwen "background started" prefixes are `Text::TagValues` / `Text::AfterPrefix`.
- Naive ISO timestamps (no zone) are read as UTC; Python reads them as local
  time. Only the inferred-turn cut uses this, and no CLI in main writes naive
  stamps.
- `parse_session_file` (main's legacy whole-file parse) stays in Python; the
  runtime serves the incremental usage, activity and turns calls.
