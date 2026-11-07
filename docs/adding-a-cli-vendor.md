# Adding a CLI vendor

Navide supports one CLI agent per declaration. Adding a vendor starts with a
declaration — `shared/agent-cli/<key>.json` — and a generator run. A vendor
file on each side then adds only what a declaration cannot say, and a native
adapter exists only when the Rust core reads the CLI's log. You never need to
read or modify the shared orchestration code.

## Declare the CLI first

1. Write `shared/agent-cli/<key>.json` (short lowercase key, e.g. `mycli`; copy
   the closest existing declaration) and run

   ```sh
   uv --project backend run python scripts/generate-agent-cli-facts.py
   uv --project backend run python scripts/generate-agent-cli-facts.py --check
   ```

   The generator writes `cli_vendors/_vendor_facts.py`, `src/shared/vendorFacts.ts`,
   `native/navide-cli-runtime/src/vendor_facts.rs` and `observe/adapters/mod.rs`
   from every declaration. Never edit those four by hand. The launch options
   (`model`, `effort`, `skip_permissions`, `resume`, `session_id`, `login`) are spelled from one generated option
   table that the Python backend, the renderer and the Rust sidecar all read, so a flag is written once.

Each declaration field replaces a hook you used to write by hand:

| Declaration field | Replaces (backend `VendorSpec` / frontend spec) |
|---|---|
| `command` | `defaultCommand` |
| `model_flag`, `effort_flag`, `known_efforts` | `supports_model`, `supports_effort`, `known_efforts`; `modelArgs`, `effortArgs`, `knownEfforts` |
| `permission_flag`, `permission_aliases` | `skipPermissionFlag`; the spellings that set a permission mode |
| `resume_args`, `resume_aliases`, `subcommand` | `resumeArgs`; `resume_id_from_command` |
| `plain_only` | the rule that a command line with shell syntax names no resume (Antigravity, Grok); optional, default `false`, and only with `resume_args` |
| `session_id_flag` | the flag that pins a session id at launch |
| `login_args` | `login_command_args` |
| `data_dirs` | `data_dirs` (`data_dirs_of(FACT.data_dirs)`) and `data_dir_env_vars` |
| `session_root`, `session_path` | `session_path` |
| `logs` | where the sessions' logs are (`files`, `cwd`) |
| `shutdown` | `shutdown=ShutdownSpec(...)` |
| `supports_session_resume` | `supports_session_resume` (set it from `FACT`) |
| `capabilities` | the status of each launch option; a flag is declared only next to a status the sidecar renders |
| `observer` | the native observation of the CLI's log (see below) |

A `VendorSpec` whose key has a declaration derives `supports_model`, `supports_effort`, `known_efforts`,
`login_command_args`, `resume_id_from_command`, `shutdown`, `session_path` and `data_dir_env_vars`
from it when you leave the field out. List one only where the declaration cannot say it (Grok's reader of
a resume id, a platform-dependent session path, the data directories of Goose). The parity test
`backend/tests/test_agent_cli_facts.py` checks that each vendor keeps to its declaration, including that a vendor
reads a resume id if and only if it declares `resume_args`.

## Backend

What is left after the declaration is exactly these steps, and every one of them is checked by a test that
names what you forgot:

1. Copy `backend/agent_team_backend/cli_vendors/_template.py` to
   `cli_vendors/<key>.py` and fill in the `SPEC`, which starts from
   `FACT = FACTS["<key>"]` and `key=FACT.key`. Add only named hooks and what
   the declaration cannot say (credentials, quota, MCP and skills wiring, the install entry). Every capability you
   leave at its default is treated as "unsupported" for your vendor — the app degrades gracefully.
2. Register the spec in `cli_vendors/registry.py` (one import and one line, alphabetical). The import must stay
   static: the packaged backend finds vendor modules through it.
3. If the CLI writes local conversation logs, implement the log reader in
   your vendor file and set `make_log_reader`.
4. Add `backend/agent_team_backend/log_readers/<key>.py`. Every registered
   vendor needs a module there — a re-export shim when you wrote a reader
   (copy any existing one), an empty placeholder when you did not. If you
   wrote one, also list its class in `log_readers/__init__.py`
   (`_MIGRATED_READERS` and `__all__`). Shipping no reader has one consequence
   worth knowing up front: Navide cannot learn the id of a session it started,
   so set `supports_session_resume=False` (it defaults to **True**) and leave
   `resumeArgs` out of the frontend spec — the two sides are cross-checked.
5. Add a row to `MEMORY_SOURCES` in
   `backend/agent_team_backend/native_memory.py` naming the instruction file
   your CLI reads (`AGENTS.md` for most). Every vendor must be mapped there or
   listed in `_CONFIGURED`; otherwise `test_native_memory` fails with a vendor
   in state `unknown`.
6. Add `backend/tests/vendors/test_<key>.py` covering what you implemented.
   Register executable cases and provenance-bearing fixtures in
   `tests/fixtures/cli-regression/catalog.json`, and add the frontend command
   contract under `tests/cli/`. The inventory must match both source registries;
   a missing, skipped or uncollected required case fails the regression gate.
   Exercise real Navide readers, attribution and launch wiring. Declare
   unsupported capabilities with negative assertions instead of inventing
   uniform capabilities or skipping the vendor. See
   [CI and CLI regressions](en-US/ci-and-cli-regression.md).
7. Add your entry to the `SNAPSHOT` in
   `backend/tests/vendors/test_install_deps_snapshot.py`. The snapshot is
   ordered by `DEPS`, which is `_AGENT_CLI_ORDER` first and then the remaining
   keys in **registry order** — so unless you add your key to
   `_AGENT_CLI_ORDER`, your entry goes where the key sorts among the others,
   which is usually *not* the end. Run the test and let the diff place it.
   The expected vendor set (`EXPECTED_KEYS`) and the set of vendors with a
   sign-in command are not lists to edit: they read the declarations.
8. Update the vendor counts and lists in the README (three places); a test
   compares them with the declaration set.
9. Account switching is opt-in through `account_switch=AccountSwitchSpec(...)`
   on the spec, plus `slot_file` (the parked copy's file name). A spec without
   the declaration is never offered for switching and never inherits another
   vendor's behaviour. The fields the transaction and the UI read:
   - `auth_scope` — the credential pool. Two vendors declaring the same string
     share one switch lock and one impact set. For a per-provider store the
     declared value is the prefix; the runtime pool is `"<auth_scope>:<scope>"`
     (`cli_vendors.base.auth_scope_for`).
   - `method` — `hot` (the CLI re-reads its credential per request; panes keep
     running), `restart` (affected panes are stopped at a safe point, swapped,
     and resumed), or `manual` (the vault can park and restore, but the user
     must confirm a new conversation). Automatic switching is refused for
     `manual`, and for `restart` when `resume` is not `native`.
   - `store` — `file`, `compound-file` (one provider entry inside a shared
     document; needs `extract` / `merge`, and `scopes` listing every provider
     id a profile may bind to), `keychain` (`keychain_items` as
     `(service, account)` pairs, `live_file` as the non-macOS fallback),
     `pointer`, or `env`.
   - `evidence` — `live` (an A → B → A round-trip on an installed CLI,
     recorded against `verified_version`), `source` (read from the vendor's
     code or package), or `docs`. Anything but `live` is shown as unverified;
     it is never a reason to refuse a switch, and it must not be raised without
     the round-trip. Keep `platforms` to the tuple you actually established;
     `todo` names what is still open.
   - `verified_version` — the CLI version that evidence came from. The
     pane's startup probe says a newer installed CLI "has not been tested"
     against `VendorSpec.cli_verified_version` first (the version the
     integration as a whole — launch, logs, resume — was last checked on) and
     falls back to this one. Set `cli_verified_version` when it is newer than
     the account-switch evidence; a vendor without account switching sets only
     `cli_verified_version`.
   - `shadowing_env` — variable names the CLI ranks above the stored
     credential. A pane launched with one of them present is reported as not
     switchable rather than swapped.
   - Quota evidence: the backend trusts an exhaustion report only from its own
     per-account usage snapshot (`fetch_usage`) or from a text the vendor
     declared in `quota_exhausted_patterns`. A vendor declaring neither can
     only ever notify.
   - Sign-in isolation: set `login_home_env` (and `login_home_secret_file`)
     when the CLI can be pointed at a private home; the sign-in pane then
     runs there and the live credential is untouched. Without it the sign-in
     is *global*: the vault snapshots the live credential before the pane
     spawns, parks what the CLI wrote into the profile's slot afterwards and
     restores the snapshot. Say which one applies in the integration record.
   The structural test `backend/tests/test_account_switch_capabilities.py`
   checks the declaration; `backend/tests/test_quota_failover_api_contract.py`
   and `test_quota_failover_login_contract.py` exercise the switch and sign-in
   flows through the real handlers — add your vendor to their vendor lists
   when you declare `account_switch`.
10. Set `extensions` to say where the CLI keeps the CLI extensions it installs
    for itself (plugins, extensions, hooks; see the [glossary](en-US/glossary.md)),
    for the read-only inventory on Settings → Extensions. Declare an
    `ExtensionsSpec` of `ExtensionsSource` rows, each naming a reader in
    `backend/agent_team_backend/cli_extensions.py`, a path under the user's
    home and the vendor's own type label ("Codex hook"). A CLI with an
    extension system whose location you have not verified declares
    `ExtensionsSpec(supported=False, note=...)` rather than a guess; only a CLI
    with no extension system at all leaves it `None`, which hides it from the
    page. `backend/tests/test_cli_extensions.py` fails for a vendor that
    declares nothing. A CLI Navide lists without running it in a pane (Gemini
    CLI) is declared in `_UNMANAGED_CLIS` in `cli_extensions.py`; when it
    becomes a vendor, move that declaration onto its spec (the vendor spec
    wins while both exist).

## Native observation (only if the CLI's log is read by the Rust core)

A JSONL log is read by `native/navide-cli-runtime/src/observe/`; the vendor adds a declaration and a thin adapter, not a reader.
The core runs on every platform through the one source seam `observe/os.rs` (regular file only, no link followed, shared-read
open, positioned reads), so an `observer` block names `"platforms": ["unix", "windows"]`; the generator refuses an observer that
omits a platform unless the layout is a named exception (`OBSERVER_PLATFORM_EXCEPTIONS` in `scripts/generate-agent-cli-facts.py`,
with its reason). The adapter's own tests must not assume `/` paths or POSIX-only file APIs; use `os::link_file` and `os::stat_regular`.
The test recipe, in the order that keeps each step falsifiable:

1. Extend the `observer` block of the declaration you already wrote (usage fields, modes, `legacy_state` for every key of the Python bag the
   typed state replaces, `core.modes`) and run the generator again, then `--check`. A vendor with no native observation
   has no adapter and needs none of the steps below.
2. Write `observe/adapters/<key>.rs`: an `Observer` with a typed `State`, and a `JsonlObserver` with only the hooks the log needs
   (`identify`, `activity`, `settle`, `position_key`, the consts). Name the Python source in the module doc with `file:line`.
3. Add a `Log` scenario in the adapter's `tests` module (`observe/parity.rs`): `Log::new`, `append`, `passes`, `replaced`, a
   half-written last line, a record that is no object. The record shapes cite the Python reader's own tests with `file:line`.
   Each difference from the Python reader is a named flag on the scenario (`beyond_golden`, `legacy_lacks_activity_flags`,
   `core_adds_to_events`, `legacy_kept_no_key_window`); an unnamed difference fails the pass.
4. The oracle is the Python reader. A CLI that has a retired Rust observer is checked against its golden through
   `parity::check_log_golden`; the golden is a frozen record (`tests/fixtures/agent-cli-observe-goldens/<key>.json`, re-recorded
   only with `restore-legacy-observers.patch`) and its `legacy_source.blob` must equal the blob the adapter's module doc names
   (`each_golden_records_the_blob_its_adapter_names_as_its_source`). A new CLI has no golden: compare the sidecar's answer with the
   Python reader's field for field over the same log through `tests/native_reader_peer.py` `wire(monkeypatch, root)`, as
   `backend/tests/test_cli_runtime_grok_turns.py` does. Rebuild the release binary (`cargo build --release --locked`) first.
5. Add the vendor to `backend/tests/test_cli_runtime_bound_matrix.py`: one `Cell` per mode, with a log builder whose records
   cite the Python fixtures. The matrix proves every mode stays inside the answer and state caps; a persisted key window is bounded
   by its serialized bytes (`JsonlObserver::RECENT` is an upper bound: heavy keys keep fewer), so add a long-key axis if the vendor
   keys on a record field, and make its ids escape-heavy (control characters weigh six bytes each) or the axis cannot reach the bound.
6. Add the vendor's `State` to `every_adapter_state_at_its_worst_fits_the_state_share_beside_the_core_fields`, and the vendor to
   `what_every_adapter_persists_at_its_worst_leaves_room_for_half_of_a_window_of_the_longest_keys` (both in `observe/jsonl_tests.rs`;
   a vendor's `turns` rows are the core's `TurnRow`, filled by the adapter's builder), then run `cargo test --locked --manifest-path native/navide-cli-runtime/Cargo.toml` and the
   backend tests that name the vendor, bare, with a green exit code.

## Frontend

1. Copy `src/renderer/src/platform/plugin-shell/agents/_template.ts` to `agents/<key>.ts` and
   fill in the spec. It starts from `const FACT = VENDOR_FACTS.<key>`,
   `defaultCommand: FACT.command` and `...factOptions(FACT)`, which supplies
   `modelArgs`, `effortArgs`, `knownEfforts`, `skipPermissionFlag` and
   `resumeArgs` from the declaration. Add the label, the capability flags and
   a named hook only where the facts cannot spell it (aider's
   `resumeCommandPattern`); the template lists every optional field with
   pointers to the full docs in `agents/types.ts`.
2. Register it in `agents/index.ts` (one line, display order).
3. Add a row to `vendors` in `src/renderer/src/components/CliAgentsHelp.vue`
   (Settings ▸ Help). It is a hand-maintained mirror, and
   `test_help_panel_sign_in_column_matches_login_command_args` compares its
   `signIn` column against every declaration's `login_args`.
4. `CliAgentsHelp.test.ts` needs no edit: its row count and sign-in column read the declarations.
5. Append your `{ agentKey, label, hint }` to
   `plugins/navide-plans/src/retained/agentSpecs.ts` — the plans plugin keeps a
   retained copy that a test compares against the live list.
6. Only if your spec declares `home_env_vars`: add each one to
   `SPAWN_ENV_RESERVED_KEYS` in
   `src/renderer/src/platform/plugin-shell/lib/cliLaunchOverride.ts`. This is
   product code, not a test list: a home relocator Navide sets itself must not
   be offered as a user-editable spawn env var, or the page promises an
   override that gets overwritten.
7. Run `pnpm vitest run src/renderer/src/platform/plugin-shell/agents` — the structural tests there
   check your spec against the rules the template states (key matches the
   filename, the file is registered, the vendor's facts read back the
   resume command Navide builds for you, no `/g` on a matcher). They need no edit
   when you add a vendor; they discover your file on their own.

Write the spec as `export const SPEC = { … } as const satisfies AgentSpec`,
never `SPEC: AgentSpec`. The annotation widens `agentKey` to `string`, and
`index.ts` derives the app-wide `AgentKey` union from these literals — one
annotated spec collapses that union everywhere. A compile-time assertion in
`agents/__tests__/specs.test.ts` fails if this regresses.

## Where your vendor shows up in the UI

Registering the spec is all the wiring there is — every surface below reads
the assembled list, so none of them needs an edit. What the list is, though,
passes through one filter that is easy to mistake for a bug:

```
agents/<key>.ts  →  agents/index.ts (ORDERED)  →  AGENT_SPECS / CLI_AGENT_SPECS
                                                          │
                        App.vue `enabledAgentSpecs` ──────┘
                        (user's Settings → CLI Agents order + disabled list)
                                     │
                                     ├─ ControlPane `manualAgentSpecs` — the ＋
                                     │  menu on a workspace heading, and
                                     │  "Handle Issue As…" (terminal filtered out)
                                     └─ everything else reads the unfiltered list:
                                        CliAccountsPane, TokenStatsPanel,
                                        DebugModal, PipelineManagerModal,
                                        AgentMessagesPanel, useTerminal
```

Three consequences worth knowing before you go looking for a missing entry:

- **The ＋ menu is a user-filtered subset, not the vendor list.**
  `useCliAgentPrefs` keeps a *disabled* list (a blocklist) and a custom
  *order*. A brand-new vendor appears with no action from the user: it is not
  in anyone's blocklist, and `orderedAgentKeys` appends keys it has never seen
  after the ordered ones. It lands at the END of the menu, which is where to
  look first. Both lists are per-workspace (`project.json` ui_state), with the
  global KV as the fallback — so a vendor can be visible in one workspace and
  hidden in another.
- **Not being installed does not hide a vendor.** The menu shows every enabled
  vendor and marks the missing ones with a badge (`missingClis`, refreshed on
  backend connect and on dropdown focus). So "it is in the menu" says nothing
  about whether the binary exists, and "it is missing from the menu" is never
  explained by a missing binary.
- **The renderer is a build artifact.** A newly registered vendor reaches a
  running app only after that app is rebuilt (packaged build) or its dev
  server restarted — and only if the branch it was added on is the one being
  built. Checking the menu of an installed release for a vendor that lives on
  a feature branch will always come up empty.

## Install detection

Set `install_dep=Dep(...)` in your vendor's `SPEC` (see any existing
vendor file) so Navide can detect, install, and update your CLI. The
onboarding wizard aggregates every vendor's entry automatically — no
other file to edit.

## The marketing site (`navide-web`)

The public site lists the supported CLIs, and it counts them by fetching this
very directory through the GitHub API — so a vendor appears there on its own
once the commit is on `main`, with no copy edit and no redeploy. Two things in
`src/vendors.ts` are still hardcoded and will lag:

- `ICONS` — an unlisted vendor falls back to the generic terminal glyph. Add
  `<key>: "/vendor-icons/<key>.svg"`, drop the official asset in
  `public/vendor-icons/` (downscale raster sources to 84 px; the frame renders
  at 38–42 px), and record where it came from in that directory's `README.md`.
- `LABELS` — an unlisted key is title-cased (`droid` → "Droid"), which is often
  right, but `LABELS` also *is* `FALLBACK`, the roster shown when the API call
  fails. Unauthenticated GitHub allows 60 requests/hour per IP, so that path is
  hit routinely, not rarely — a vendor missing from `LABELS` silently
  disappears from the site whenever the limit is reached.

Neither blocks the vendor from working; both make the site quietly wrong.

## Known exemptions

"No shared module carries per-vendor branches" holds for the orchestration
you go through when adding a vendor. Three places are deliberately outside
that rule — you do not need to touch them, but do not be surprised to find
vendor names there:

- **Credential vault** (`credential_vault.py`, `ws_handlers.py`'s hot-swap
  path). Account switching is per-vendor by nature — where a secret lives,
  whether a login home isolates it, how an identity is read out of it. A new
  vendor gets multi-account support by filling in the credential fields of its
  own `SPEC`; the vault's remaining `claude` branches are its own history.
- **`CODEX_HOME` per-pane isolation** (`ws_handlers.py`, `codex_home.py`).
  `CodexHomeManager` is owned by the app and injected into the spawn path, so
  moving it into `cli_vendors/codex.py` would invert the import direction that
  `cli_vendors/base.py` forbids. It stays put until the spawn path grows a
  hook that can express it.
- **Plugins** (`plugins/builtin/**`). Plugin wiring — MCP server injection,
  per-pane shim homes, skills — still branches on `agent_key` inside each
  plugin. Plugins are not part of the vendor contract: a new vendor works
  without touching them, and gains plugin-specific behaviour only if that
  plugin adds it.

Anything else that makes you edit a shared module to add a vendor is a gap in
the contract. Open an issue rather than adding a branch.

## Verify

```sh
uv --project backend run pytest backend/tests/test_cli_vendors_registry.py
uv --project backend run pytest backend/tests
pnpm test:run && pnpm typecheck
```

The registry test is your checklist: it fails with a message naming exactly
which list you forgot (registry, DEPS, log reader, frontend spec) and
rejects forbidden imports (vendor modules may import only `base`,
`_protocols`, the standard library, and httpx — never another vendor or any
app module).

## Boundaries

- Never add `if agent_key == "<yours>"` branches to shared modules — put
  the behavior in your vendor file; if the contract in
  `cli_vendors/base.py` lacks a hook you need, open an issue first.
- Credential-vault behavior (`credential_vault.py`) is security-sensitive
  and not extensible from vendor files beyond the file-layout fields in
  the spec.
