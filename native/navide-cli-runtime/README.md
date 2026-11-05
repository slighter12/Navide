# navide-cli-runtime

A standalone executable that owns the process lifecycle of the coding CLIs and shells Navide runs, so that a
backend restart or a stuck terminal consumer can never leave an unmanaged process behind. Once selected, the
Python backend starts one sidecar per backend run and keeps everything that is application policy.

Status: the sidecar is built and shipped in every package, but it is not yet the default terminal runtime. Only
the evaluation selector chooses it; until the runtime is adopted, the backend's Python terminal service runs the
terminals.

## What the sidecar owns

- **Composition.** Turns a launch request (agent, model, effort, resume id, environment) into the program,
  arguments and environment to execute. The per-agent facts (executable, flags, resume spelling, shutdown
  strategy) are generated from the declarations in `shared/agent-cli/<key>.json` by
  `scripts/generate-agent-cli-facts.py`; shared code never compares an agent key.
- **Process lifecycle.** Creates the PTY (ConPTY on Windows), spawns the root process behind an ownership gate,
  records its identity in a journal before the target program may run, tracks the process tree it owns, signals
  and kills by recorded identity, reaps, and reports exit and verified cleanup.
- **Log observation.** Reads an agent's own session log (JSONL or SQLite) and returns the interpreted usage,
  activity and turn records together with the next cursor. macOS and Linux only; on Windows the sidecar refuses
  an observation request and the Python watcher observes.
  A vendor on the observation core is one declaration plus a thin adapter. The declaration
  (`observer` in `shared/agent-cli/<key>.json`) is typed data: layouts (source kind, file name, usage record
  pointers or SQL table/column names), the modes it answers, and named compatibility rules; the generator emits the
  parser enum, the mode enum and a static dispatch from it, so a declared mode with no implementation does not
  compile. The core owns the shared behaviour (byte-bounded resumable passes with a fingerprinted position, the
  activity high-water mark, per-turn and cumulative usage, the row cursor and row marker of a SQL store, the typed
  state); an adapter keeps only what is irregular (what a record means, with the reason in its module comment).

The sidecar **never reads or writes a PTY after the handoff**. It sends the PTY master to Python once
(`SCM_RIGHTS` on POSIX), and Python's terminal stream runs over that descriptor. After that the sidecar keeps
only the window size, signals to recorded identities, reaping, and the lifecycle facts: root exit, end of the
stream reported by Python, and verified cleanup.

Python keeps credentials and accounts, quota and failover, permissions, hook, MCP and skills configuration,
durable persistence (projects, history, token totals, checkpoints), recovery policy, and the terminal stream.

## Wire protocol

Control traffic uses binary frames on a socket pair inherited as `--control-fd` (POSIX) or on the sidecar's own
stdin and stdout (Windows). The current wire version is **2** (`WIRE_VERSION` in `src/frame.rs`). A frame is a 32-byte big-endian header (magic
`NC`, version, type, code, flags, correlation id, runtime tag, meta and blob lengths), a UTF-8 JSON `meta` and a
raw `blob`; there is no base64. Message codes are in `frame::code`. The sidecar refuses a request made for a
different wire version.

On POSIX, stdout carries only the one `ready` (or `fatal`) line and stderr carries diagnostics. `ready` reports the process
identity, the wire version, the PTY implementation, whether the ownership gate is active, and every bound
(frame size, queue depths, observation, cleanup and kill timeouts). All queues are bounded in bytes.

The sidecar is started as:

```
navide-cli-runtime --wire 2 --runtime-generation <token> --owner-pid <backend pid> \
  --ownership-journal <file> [--control-fd <fd>]
```

`--control-fd` is POSIX only. The sidecar ends the process trees it owns and exits when its backend goes away.

## Platforms

| Platform | PTY | Notes |
| --- | --- | --- |
| macOS | native PTY | Descendants tracked through the system process table. |
| Linux | native PTY | Scoped enumeration of the sidecar's own process tree when the kernel offers `/proc/<pid>/task/<tid>/children`, `PR_SET_CHILD_SUBREAPER` and a non-ignored `SIGCHLD`; otherwise it falls back to a full `/proc` scan. SQLite is bundled. |
| Windows | ConPTY | Job object and process-identity based tracking. Log observation is not integrated. |

## Build

```
cargo build --locked --release --manifest-path native/navide-cli-runtime/Cargo.toml --bin navide-cli-runtime
```

Packaging does this for you: `pnpm run build:cli-runtime` builds the release binary and stages it as
`build/cli-runtime/navide-cli-runtime[.exe]`, `pnpm build` runs it, and electron-builder copies it next to the
backend. `node scripts/check-cli-runtime-staged.mjs` verifies that the staged binary exists and matches the host
architecture; the same check runs on the packaged app for the architecture being packed and fails packaging
otherwise. The release binary has no fault-injection code.

## Test

```
cargo test --locked --manifest-path native/navide-cli-runtime/Cargo.toml
```

Rust unit tests live beside the code; `tests/` holds integration tests against the built sidecar, and
`tests/vectors/` holds the frame codec vectors. The `fault-hooks` feature adds test-only fault injection for the
backend's regression suite and is never part of the product binary. The Python side is covered by the
`backend/tests/test_cli_runtime*.py` files, run with
`uv --project backend run pytest backend/tests/<file>.py`.
