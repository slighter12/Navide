# Ticket 03-S — isolated macOS development startup and readiness (ticket03-startup-01, corrections 01–02)

Stage: E3 — the startup/readiness slice of ticket 03, corrected per the review's F1–F10 findings
(correction 01) and then per the re-review's P2-1 and P3-1…P3-4 findings (correction 02). Fixed
point: `5a6b78ce70b2ef775a5b5ba201a4474a8574c342` (branch `eval/issue-2-bun-toolchain`); the
original baseline is the frozen base source `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`.
Executed 2026-10-10 in the disposable UTM macOS guest `navide2-guest`, controlled over SSH only;
correction 02 is documentation and evidence only (no guest launches or builds).

This stage answers the three criteria the round-3 review left open for ticket 03: criteria 6
(isolated development probe), 7 (readiness and cleanup) and the qualified part of criterion 5
(no external daily Node/pnpm from install through startup). Correction 01 replaced the case-A
baseline with the true original base source, reclassified the context7 dependency, corrected
two factual statements and added five small evidence items (F5–F10). Correction 02 corrects the
byte attribution for the plugin-bundle difference, qualifies the withdrawn masked-structure
claim, fixes two evidence pointers, archives the correction-01 guest scripts and records the
frozen-input check with its real UTC. The prior deliveries
(`delivery/anchor.json` `b7cf4e3e…`, `correction-01/anchor.json` `a8cd264c…`) and all of their
raw evidence are untouched; correction 02's own evidence is under `correction-02/` with a new
anchor.

## 1. What was executed

| Case | Source | Toolchain on PATH | Role |
|---|---|---|---|
| **A0** `case-a0-base` | `git archive f4e9fd50136ea84f38f99e3c96f2c924f21d244f` (frozen base, no repair, no overlay) | Node 22.23.2 + pnpm 10.0.0 (case-owned) | **ORIGINAL baseline** (correction 01) |
| A `case-a-original` | `git archive HEAD` (HEAD without the six 03 WIP paths) + guest-local specifier repair | Node 22.23.2 + pnpm 10.0.0 (case-owned) | retained history (superseded by A0) |
| B `case-b-matched-node` | HEAD + the six frozen 03 WIP paths | Node 22.23.2 + pnpm 10.0.0 (case-owned) | Node control on the candidate source with the three specifiers restored to `link:` for pnpm only |
| C `case-c-bun` | HEAD + the same six frozen 03 WIP paths | Bun 1.4.2 only — `node`, `npm`, `npx`, `pnpm` absent | Bun candidate |

Each case has its own directory tree under `/Users/navide/eval/<case>/` with its own HOME, XDG
directories, caches and stores, `TMPDIR`, `AGENT_TEAM_DATA_DIR`, Electron cache, downloads,
logs and receipts. Every step runs through a wrapper that starts from a cleared environment
(allowlist only), redirects output to a log file and writes a receipt with argv, cwd, timing,
exit code, log bytes and log SHA256. No step inherits the guest's ambient login environment.
Case A0 was set up with the same harness; its toolchain artifacts were staged from the
checksum-verified copies already used by case A, and the setup step re-verified each one
against its publisher checksum (Node: `nodejs.org` `SHASUMS256.txt`; pnpm: npm registry
`dist.integrity`; uv: the release's `.sha256`; Electron: the package's own `checksums.json`).

Step sequence per case: pinned toolchain install → `install` → `uv sync` (Python backend) →
`typecheck` → `build` → `dev` launch with a bounded observation window → readiness probes →
teardown → re-verification. A0's `dev` script is the project's original one
(`… && electron-vite dev`, no evaluation dispatcher, no Bun adapter).

## 2. Results

| Step | **A0 (base, original)** | A (HEAD-no-WIP, history) | B (WIP + Node) | C (WIP + Bun) |
|---|---|---|---|---|
| install | `pnpm install --frozen-lockfile` **exit 0, 11 s, 620 packages, no repair** | frozen exit 0, 10.1 s after guest-local repair | frozen exit 0, 10.5 s after guest-local repair | `bun install` exit 0, 72.35 s |
| Electron binary | `node node_modules/electron/install.js` exit 0, 1 s | exit 0 (2,401 s download) | exit 0, 1 s | exit 0, <1 s |
| Python backend | `uv --project backend sync` exit 0, 5 s | exit 0, 5 s | exit 0, 2 s | exit 0, 42 s |
| typecheck | `pnpm typecheck` exit 0, 43 s | exit 0, 40 s | exit 0, 40 s | `bun … run typecheck` exit 0, 29 s |
| build (`NODE_OPTIONS=--max-old-space-size=4096`) | `pnpm build` exit 0, 85 s | exit 0, 84 s | exit 0, 83 s | exit 0, 59 s |
| products: `out/` | 232 files / 44,498,452 B | 232 / 44,498,452 B | 232 / 44,498,452 B | 232 / 44,378,530 B |
| products: `dist-plugins/` | 707 files / 143,491,451 B | 707 / 143,488,786 B | 707 / 143,490,663 B | 707 / 143,460,485 B |
| `dev` launch (LaunchAgent `gui/501`) | `bootstrap_rc=0` | `bootstrap_rc=0` | `bootstrap_rc=0` | `bootstrap_rc=0` |
| first `/health` 200 | 36 s (port 51514; 40 s in the first A0 run) | 35 s (port 50964) | 36 s (port 51078) | 31 s (port 51193) |
| owned processes left | 0 | 0 | 0 | 0 |

Resolved dependency sets are the same in all cases (620 packages; electron 44.4.5, vite
6.4.3, typescript 5.9.3, vue 3.5.34, vitest 3.2.7, @playwright/test 1.63.0). A0's frozen
install needed **no repair** and used the retained `pnpm-lock.yaml` unchanged (hash
`dd087379…`, identical in the base tree and at HEAD); the Bun case installed against
`bun.lock`, which stayed byte-identical (`812fcf65…`).

Both Node baselines need the heap setting the project's own CI uses for this step
(`NODE_OPTIONS=--max-old-space-size=4096`, `.github/workflows/ci.yml`); without it the
baseline build aborted with exit 134 (SIGABRT) in this 12 GiB guest after 18 s. The same
declared setting was used for every case's build, so the comparison is like-for-like.

## 3. Readiness evidence (criterion 7)

Readiness is established at the seams the product already has, not from a launcher exit code:

- **Backend health**: `GET http://127.0.0.1:<port>/health` → 200 with
  `{"status":"ok","version":"0.2.14","started_at":...}` in every case; first 200 at 36 s (A0),
  35 s (A), 36 s (B), 31 s (C) after launch.
- **Authenticated WebSocket seam** (`/ws`, the interface the renderer uses): per-start token
  file `<case>/data/backend-ws-token`, 43 bytes, mode 0600, written by the run (SHA256 recorded
  per case); a handshake **without** the token is accepted at the socket level and then closed
  by the backend with code **4403**; the same client **with** `?t=<token>` stays accepted.
  A0's numbers come from its second run (`dev-a0-run2`) — see §6.9.
- **Plugin backend seam** (`/plan-mcp`, the endpoint every CLI pane is wired to): `initialize`
  200, `tools/list` 200 returning **64 tools**, including the six plugin-contributed plan tools
  and the `skills_*` set; the tool surface is **byte-identical in all four cases** (146,226 B,
  SHA256 `e9a6635e…`). A request without a credential is refused with JSON-RPC `-32602`.
- **Process evidence**: the run table lists the nested processes actually involved — the
  `electron-vite dev` driver, its esbuild service, four Electron processes (browser, gpu,
  renderer, utility) and the backend `python -m agent_team_backend --port …` listening on
  127.0.0.1. In the three Node cases the `npx`-spawned `context7-mcp` helper is present; in the
  candidate no process executes a Node binary.
- **Renderer sandbox switches**: in all four cases the renderer process carries
  `--no-sandbox --no-zygote` and the browser, gpu and utility processes do not; this comes from the
  product itself (`src/main/index.ts`, `webPreferences.sandbox: false` for the main window and the
  dock window) and is identical across cases — the evaluation neither adds nor removes sandbox
  switches. Primary evidence: `correction-01/receipts/c01-f9-electron-flags.txt` (4 Electron
  processes per case, flags as described). The earlier measurement in
  `correction-01/receipts/c01-f9-sandbox-evidence.txt` is **superseded**: it selected processes by
  the exact string `Electron.app/Contents/MacOS/Electron`, which only the browser process carries
  (the gpu and utility helpers run from `Electron.app/Contents/Frameworks/Electron Helper.app/`
  and the renderer from `Electron Helper (Renderer).app/`, differentiated by `--type=`), so it
  counted 1 process and 0 flags; re-checked against the same raw process tables
  (`correction-02/receipts/c02-f9-superseded-explanation.txt`), the browser process has no
  `--no-sandbox` while the renderer has both flags, so the two measurements agree once the
  selection is accounted for.
- **GUI session**: the SSH session reports `managername=Background`, so each run is handed to
  launchd's `gui/501` domain with a per-run LaunchAgent; a probe job bootstrapped that way
  reports `launchctl managername=Aqua` with the declared HOME, and the app under test registers
  in the GUI session (`lsappinfo` entry for its own bundle path) and spawns GPU/renderer
  helpers. The Vite dev server (`http://localhost:5174`) was listening during every run.
- **Logs**: the backend logs its own port, token path, watchers, builtin plugin activation
  (`navide.plans`, `navide.skills`) and MCP state; the dev server logs the renderer URL.

## 4. No external Node/pnpm in the candidate daily path (criterion 5)

- In `case-c-bun` the case PATH resolves `bun` only; `node`, `npm`, `npx` and `pnpm` are absent,
  and there is no Node at `/usr/bin`, `/usr/local/bin` or `/opt/homebrew`. In A0/A/B they
  resolve, which is what those controls are for.
- No process in the candidate run executed a Node binary.
- Runtime identity, re-run under correction 01 with the invocation itself recorded
  (`argv: ["bun","run","probe:identity"]`, cwd `<case>/source`, PATH = case Bun + uv + system,
  cleared environment, exit 0, log SHA256 `d7b92d1e…`): `process.execPath =
  /Users/navide/eval/case-c-bun/tools/bun/bin/bun`, `process.argv0 = node`,
  `process.versions.bun = 1.4.2`, `process.versions.node = 26.3.0`, `typeof Bun = object`, and
  `ps` reports `comm = node` for that process. Bun satisfies `node` inside scripts (which is
  how the `#!/usr/bin/env node` shims such as `tsc` run); the process title still reads `node`,
  so a process table alone would mislead — the executable identity is Bun.
- Mechanism, recorded while removing a leftover (F6): Bun materializes a `node` symlink to
  itself (`<case>/tmp/navide-bun-runtime-<rand>/node → <case>/tools/bun/bin/bun`) in the case's
  TMPDIR so that `env node` shims resolve to Bun. Two such directories had been left behind by
  earlier Bun runs; both were recorded (path, target, SHA256 `35d20dd0…`) and removed, and the
  case TMPDIR is empty of them afterwards. The F5 probe re-run created none.
- Permitted non-JavaScript prerequisites are distinguished in the evidence: Electron's own
  embedded runtime (binary verified against the package's `checksums.json`), CPython 3.12.11
  via uv for the backend, and the platform toolchain for native helpers.
- **Default configuration depends on an external `npx`** (F2, reclassified): the product ships
  an enabled-by-default MCP integration — `backend/agent_team_backend/mcp_settings.py`
  (`default_mcp_servers()`: name `context7`, command `npx`, args `-y @upstash/context7-mcp`,
  `enabled: True`) and the corresponding catalog entry in
  `src/renderer/src/data/mcpCatalog.ts`. In A0/A/B the backend logs
  `[backend] Context7 Documentation MCP Server v4.3.0 running on stdio` and
  `[backend] MCP 'context7' connected`, and the `npx`-spawned helper appears in the process
  table. In C the backend logs `[backend] MCP 'context7' failed to start: [Errno 2] No such
  file or directory` and no helper appears, while the application, its backend and its plugin
  surface stay healthy. Classification: a **feature difference in default product
  configuration** — a hidden external-Node dependency of the shipped default configuration, not
  of build or startup — to be carried into 04/05/09. No product code was changed.

## 5. Build products and cross-toolchain comparison (criterion 8)

All four cases produced the same product set: 888 files per case across
`packages/*/dist`, `dist-plugins`, `out/main`, `out/preload` and `build` — A0 159,637,308 B,
A 159,634,643 B, B 159,636,520 B, C 159,486,767 B (manifests with per-file SHA256 and size are
in the raw evidence). Every retrieved payload was re-hashed on the host against the guest's own
manifest: A0 888/888 files, C 888/888 files, 0 mismatches.

Byte-identical across all four cases (public packages and both native helpers):

| Product | SHA256 (all cases) |
|---|---|
| `packages/plugin-contracts/dist` (51 files) | tree `ea09a2df…` |
| `packages/plugin-sdk/dist` (4 files) | tree `a33c73fd…` |
| `packages/plugin-ui/dist` (106 files, 13,117,322 B) | tree `c06ee37c…` |
| `build/pane-helper/…/navide-pane` | `bdbe82bc…` (66,816 B) |
| `build/fn-key/navide-fn-key` | `1ea71396…` (68,272 B) |
| `out/preload/index.js` | `e4d69810…` (29,981 B) |

Source-version effect (same toolchain): A0 vs A and A0 vs B are 882 identical / 6 differing of
888 paths — the differences are the four packaged `.vsix`/`.sig` pairs and the two compiled
`navide-plans` backend binaries. So between the frozen base and HEAD the product bytes are
unchanged apart from per-build artifacts; A vs B (the ticket-02 WIP footprint) shows the same
882/6 pattern.

Toolchain-dependent:

- `out/main/index.js`: A0, A and B `e49f87bf…` (2,341,106 B) — identical; C `c31005f2…`
  (2,221,531 B). The difference is module resolution in the bundler: the Node build inlines
  `ws` and keeps its optional native speedups `bufferutil` / `utf-8-validate` external, while
  the Bun build leaves `require("ws")` as an external reference resolved from `node_modules`
  and consequently does not reference the optional modules. The candidate resolves `ws` at
  runtime — demonstrated by the readiness evidence, not by inspection alone.
- Plugin frontend bundles, A0 (Node) vs C (Bun): 670 logical js/css assets under `/assets/`
  (asset path with its trailing content-hash segment removed) in each tree — **281
  byte-identical** and **389 content-differing**, of which **8 differ in size** (all by exactly
  −6 bytes) and **381 differ at equal length**. Every one of the eight size-differing pairs is
  accounted for **exactly** by two transforms: one `if(!!x)` → `if(x)` (−2 bytes) and one
  `new Error(` → `Error(` (−4 bytes), for example
  `dist-plugins/plans/assets/index.js` 2,711,437 → 2,711,431 B with `if(!!` 1 → 0 and
  `new Error(` 133 → 132; the five distinct bundles involved (`mini-ide/assets/index.js`,
  `navide-git/assets/index.js`, `navide-mini-ide/frontend/assets/window.js`,
  `navide-plans/frontend/assets/index.js`, `plans/assets/index.js`) and their three packaged
  copies under `official-artifacts/factory-resources` all show the same two-hit pattern.
  Applying those two transforms to both sides of every content-differing pair yields equal
  lengths, i.e. the remaining differences — minified identifier names and content-hash tokens —
  are **length-preserving and net 0 bytes**. So the family is **two behaviour-preserving
  minifier transform choices**, and no other difference class is claimed.
  Reproducible producer: `correction-02/scripts/c02-bundle-delta-producer.py`, output
  `correction-02/receipts/c02-bundle-delta.txt` and `correction-02/analysis/c02-bundle-delta.json`.
  Two earlier statements are withdrawn: the claim that `dist-plugins/plans/assets/index.js`
  "differs only by minifier naming and content hashes" (it carries the same two transforms as
  the others), and the earlier "masked structure" measurement it rested on — masking token
  *classes* erases the names themselves, so it cannot show name-only differences, and the
  byte-level attribution above comes from the transform counts instead. The earlier stage's
  broader-scope classification (690 logical keys including non-js assets and `.html`, 287
  identical / 395 hash-token-only) came from an inline script that was not retained; the
  archived producer above is the reproducible source of this stage's numbers.
- Official plugin artifacts (`dist-plugins/official-artifacts`, 242 files per case): A0 vs A
  and A0 vs B 237 identical / 5 differing (four `.vsix`/`.sig` pairs and one compiled backend);
  A0/A/B vs C 99 identical / 13 differing / 260 renames. Packaged archives and their signatures
  differ between two builds of different sources, and between the two toolchains, because the
  archive embeds build timestamps and the signature is minted per build — the two *universal*
  `.vsix` are in fact byte-identical within A0/A/B (`5b79f014…`, `1bc984aa…`) and the
  `darwin-arm64` one differs even there; archive bytes are therefore not an equivalence signal.
  The native plans backend is a fresh build per case (A0 `5c9405b0…` / 9,425,712 B,
  A `a5de95fc…` / 9,424,944 B, B `0a4873c8…` / 9,425,424 B, C `2a109c38…` / 9,426,288 B).
  Practical consequence for 04/05: compare unpacked plugin content and product inventories, not
  archive bytes.
- `out/renderer` is not byte-comparable across builds at all: the product embeds
  `v0.2.14 @<MM/DD HH:MM>` (`electron.vite.config.ts` `buildTag()`) into the renderer bundle,
  so every build minute differs — including A0 vs A.

Preserved candidate products for 04/05 (retrieved to the raw evidence roots, re-verified on the
host byte-for-byte): `products-c-bun.tar` (153 MB, `d3fd357f…`), `products-b-node.tar` (21 MB,
`7b619d01…`) from the prior delivery, and now `products-a0-base.tar` (153 MB, `a55f7cbc…`) for
the corrected original baseline.

## 6. Deviations, failures and how they were handled

Recorded exactly as they occurred; nothing was repaired retroactively, and no prior record was
relabelled.

1. **Candidate-branch frozen-install gap (F1).** `pnpm install --frozen-lockfile` fails on the
   branch's current source, and the failure is a **candidate-branch compatibility gap, accepted
   at Checkpoint A and carried into 09**: commit `d89016ba` (ticket 02) changed the three
   `@navide/plugin-*` devDependency specifiers to `workspace:*` and added `bun.lock`, while
   `pnpm-lock.yaml` (unchanged since `a4e1fc01`, hash `dd087379…` at both the base and HEAD)
   still records `link:packages/…`; pnpm 10 then reports `ERR_PNPM_OUTDATED_LOCKFILE`, and
   `--no-frozen-lockfile` reports `ERR_PNPM_WORKSPACE_PKG_NOT_FOUND` because pnpm 10 does not
   read `package.json`'s `workspaces` field (it says so itself). Consequence: existing CI jobs
   that run `pnpm install --frozen-lockfile` on this source would fail. Adoption would require a
   dual-bootstrap or a lockfile/workspace decision; per the Checkpoint A disposition the gap is
   documented and **not** repaired. **The corrected original baseline A0 is the frozen base
   source `f4e9fd50`, which installs frozen with no repair** (exit 0, §2). Case B is labelled
   exactly "Node control on the candidate source with the three specifiers restored to `link:`
   for pnpm only"; case A (HEAD without the 03 WIP files, same guest-local repair) is retained
   history and is not the baseline. **No `package.json` specifier, no `pnpm-lock.yaml` and no
   `pnpm-workspace.yaml` was changed anywhere in the repository.**
2. **Electron binary not installed by either client.** `electron@44.4.5`'s registry metadata
   declares no install scripts (verified against `registry.npmjs.org` and the installed
   package), so neither pnpm's frozen install nor Bun's install fetches the binary; the
   libraries are present but `dist/` and `path.txt` are not. Every case therefore ran the
   package's own `node_modules/electron/install.js` once, from a checksum-verified cache copy
   (SHA256 `a212eee6…`, matching the package's `checksums.json`); in the candidate this ran
   under Bun. `pnpm rebuild electron` does not fetch it.
3. **Guest egress asymmetry (environment observation, not a product finding).** The guest's NAT
   delivered the GitHub release asset at roughly 54 KB/s (the 130 MB Electron zip took 2,401 s
   in case A), while `registry.npmjs.org` was fast (620 packages in ~10 s for both Node cases).
   A0 reused the verified zip from case A's cache rather than re-downloading it.
4. **Harness development history (retained as raw receipts).** Earlier runner revisions and
   probe runs are kept: `dev-a`, `dev-b`, `dev-c` (no explicit Chromium `--user-data-dir`),
   `dev-a2`/`dev-a3`/`dev-smoke*` (runner corrections), `dev-a-final`/`dev-b-final`/
   `dev-a-run1`/`dev-b-run1` (the leftover check counted the runner's own command line, which
   contains the case path, reporting `owned_processes_remaining: 2` for processes that were in
   fact already gone). The delivered readiness runs of the prior stage are `dev-a-run2`,
   `dev-b-run2`, `dev-c-run2`; correction 01 adds `dev-a0-run1` (complete except its WebSocket
   probe) and `dev-a0-run2` (complete). Three early probes files
   (`dev-a.probes.json` and its siblings) were written with a nested object embedded as text and
   are not parseable; they are retained as raw files and explicitly excluded from the delivered
   evidence set.
5. **Teardown incident and correction.** During the `dev-a3` probe the first teardown revision
   killed the runner's own session (its selector matched any command line containing the case
   path), leaving the app processes behind; those processes were terminated by hand immediately
   afterwards, and the runner was replaced with a selector that excludes the runner's own
   process tree and group. Post-incident note: the manual termination was performed from the
   SSH session with `kill -TERM`/`kill -KILL` on the specific PIDs listed by the run's own
   process table, verified by a follow-up `ps` listing that showed no remaining case processes
   and no listening ports before the next run; the incident left no orphaned job definition
   (`launchctl print` for that label returned nothing after `bootout`). All delivered runs finish
   with `owned_processes_remaining: 0`, `defunct_awaiting_reap: 0`, `listen_ports_remaining: 0`,
   `job_definition_removed: true`.
6. **Electron profile path on macOS (F4, corrected count).** `HOME` alone does not isolate
   Electron's userData: Chromium resolves the home directory from the macOS user record, so
   **four** probe runs used the guest's real
   `~/Library/Application Support/agent-team-dev` (46 MB of Chromium profile data) before the
   explicit switch was added — `dev-a`, `dev-b`, `dev-c` and `dev-a2`. The delivered runs (and
   A0's two runs) pass `-- --user-data-dir=<case>/profile/chromium`, which Chromium honours
   (effective path `<case>/profile/chromium-dev`, verified in the process table). The guest home
   is disposable and holds no user credentials; the stray directory is left in place as evidence
   rather than deleted. Declared arguments are recorded per case: for Bun the dispatcher's own
   argument boundary means the workflow command carries `-- -- --user-data-dir=…`.
7. **User configuration protection.** The backend's hook installers wrote only inside each
   case's own HOME (`<case>/home/.claude/settings.json`, `port_file=<case>/data/backend-port`);
   a before/after snapshot of the guest's real `~/.claude`, `~/.claude.json`, `~/.codex`,
   `~/.codex/hooks.json`, `~/.qwen/settings.json`, `~/.gemini/settings.json` and
   `~/.copilot/hooks` is byte-identical for every delivered run
   (`real_user_config_unchanged: true`), A0's runs included.
8. **Baseline build heap.** Without the CI-declared `--max-old-space-size=4096`, the original
   Node build aborted (exit 134, 18 s) in the 12 GiB guest; with it, all four cases build. This
   is a resource fact of the guest, not a toolchain difference, and the same declared setting
   was used everywhere.
9. **A0's first run had an incomplete probe set (harness gap).** `dev-a0-run1` (window 301 s,
   first `/health` 200 after 40 s, port 51378, teardown clean) ran its WebSocket probe before
   the probe script had been staged into the new case directory, so that one probe artifact
   contains a module-not-found error instead of a result. The script was staged and a second A0
   run (`dev-a0-run2`, window 123 s) delivered the complete probe set — health 200 after 36 s
   on port 51514, WebSocket negative control closed **4403**, positive control accepted, MCP
   seam 200/64 tools, four Electron processes, `owned_processes_remaining: 0` — together with a
   full teardown re-verification. Both runs are kept as raw evidence and this report's A0
   readiness numbers come from `dev-a0-run2`; `dev-a0-run1`'s own values are quoted alongside.
10. **Host-side SSH record and one no-op removal attempt (F8/F6).** Every host-to-guest
    invocation of the correction-01 stage was logged before it ran (UTC, program, full argv; the
    private key appears by path only) in `correction-01/host-ssh-log/ssh-invocations.log`. The
    first attempt to remove the two leftover Bun `node` aliases ran through the guest's login
    shell (zsh, which does not word-split unquoted parameter expansions) and therefore removed
    nothing; **that attempt is recorded at line 18 of that log** (15:04:47Z, the invocation whose
    remote command starts `set -e` + `BEFORE=$(find …`), immediately after the listing at 15:04:40Z
    and immediately before the successful POSIX-sh removal at 15:04:55Z. The aborted attempt's own
    stdout was overwritten when the successful retry's `tee` wrote the same receipt file
    (`correction-01/receipts/c01-f6-node-alias-removal.txt` now holds only the successful pass), so
    the invocation record is what remains of it; the before/after counts (2 → 0) are in that
    receipt and in `correction-01/receipts/c01-f6-node-alias-before.txt`.

## 7. Acceptance-criteria mapping

| # | Where it is answered |
|---|---|
| 1 Authority, snapshot, isolation | §1, §6.10, `evidence.json` (`frozen_input_check`: six WIP paths verified against the round-3 anchor; only `scripts/bun-evaluation.mjs` changed, with its pre-edit hash reconstructed and equal to the anchor value) |
| 2 Typechecks baseline then candidate | §2 (`pnpm typecheck` in A0/A/B; `bun … run typecheck` in C; all exit 0, no check dropped) |
| 3 Builds and observations | §2, §3, §5 |
| 4 Native/Python prerequisites retained | §2 (`uv sync` and the compiled plans backend per case; helper binaries built and hashed) |
| 5 No external daily Node/pnpm | §4 (PATH, process table, recorded runtime-identity invocation, distinguished prerequisites, and the reclassified default-configuration `npx` dependency) |
| 6 Isolated development probe | §1, §6.6, §6.7 |
| 7 Readiness and cleanup | §3, §6.5, §6.9 |
| 8 Attributable products | §5 (per-case inventories and manifests; A0, B and C payloads retrieved and re-verified on the host) |
| 9 Results and limits | §2, §5, §6, §8 |

## 8. Limits

- One guest, one macOS version (27.0.1 arm64, Apple Virtualization). Linux and Windows remain
  unproven, so this stage supports no adoption recommendation.
- Readiness is proven at the HTTP/WebSocket/MCP seams, at the process level and in the logs. No
  visual or UX judgement is claimed or implied; no screenshots, UI automation, DevTools paste or
  scrollback clearing were used.
- Byte-level equivalence between toolchains is bounded by §5: plugin bundles and the
  main-process bundle are not byte-identical across toolchains; the size difference is fully
  attributed to two minifier transform choices (`if(!!x)` → `if(x)` and `new Error(` → `Error(`),
  all other differences (minified names, content-hash tokens) are length-preserving, and the
  main-process bundle additionally differs in how the bundler resolves `ws`. No functional
  difference was demonstrated, and none is claimed beyond what the readiness evidence shows.
- The candidate-branch compatibility gap of §6.1 is documented, not repaired; 09 owns the
  adoption question (dual bootstrap or lockfile/workspace decision). The prior stage's residual
  qualifications (ticket 03 rounds 1–3) are untouched.
- `dev` is permitted by the evaluation dispatcher; `start` (packaged-mode startup) remains
  unavailable, and the dispatcher change (`scripts/bun-evaluation.mjs`) is the only source
  change of this ticket. Correction 01 changed no source file.

## 9. Raw evidence

Prior delivery (untouched): `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-03-startup-01/`
— harness, SSH transcripts, per-case logs/receipts/probes, transfer tarballs, host analyses,
`wip-anchor-check.json`, `delivery/anchor.json` (`b7cf4e3e…`).

Correction 01 (new): `…/ticket-03-startup-01/correction-01/`

- `scripts/` — the host SSH recorder (`host-ssh.sh`), the A0 base extraction, the A0 identity
  receipt, the F5 runtime-identity runner, and the host analyses (product comparison, bundle
  structure checks, freeze).
- `host-ssh-log/ssh-invocations.log` — every host-to-guest invocation of this stage (F8).
- `receipts/` — the A0 setup/extraction/step summaries, the A0 dev-run receipts and probe
  summaries, the F5 runtime-identity receipt, the F6 before/removal records, the F9 sandbox-flag
  record and the F2 evidence capture.
- `guest-ssh/`, `retrieved/` — this directory was created but stayed empty and holds no
  transcripts; the SSH transcripts of this stage live in `receipts/*.txt` (one file per guest
  action) and the invocations themselves in `host-ssh-log/ssh-invocations.log`. `retrieved/`
  holds the A0 logs/receipts archive (`logs-receipts-a0.tar`, `4ba2c3e5…`), the A0 payload
  (`products-a0-base.tar`, `a55f7cbc…`) and its extracted tree, plus the A0 manifests and the
  case-C logs/receipts archive (`logs-receipts-c-c01.tar`, `e2b5bd9a…`).
- `analysis/` — retrieval faithfulness (A0 888/888, C 888/888), the four-case product
  comparison, the official-artifacts comparison and the bundle-diff detail/structure checks.
- `tarballs/base-f4e9fd50.tar` — the `git archive` of the corrected baseline source
  (`e3da8e5c…`); `manifest.json` and `anchor.json` — the freeze of correction 01.

Correction 02 (new): `…/ticket-03-startup-01/correction-02/`

- `scripts/c02-bundle-delta-producer.py` — the reproducible producer of the −6-byte attribution
  (§5) with its output in `receipts/c02-bundle-delta.txt` and `analysis/c02-bundle-delta.json`.
- `scripts/c02-frozen-input-check.py` — the re-run of the six-path frozen-input check with the
  real UTC (P3-4), recorded in `frozen-input-check.json` and `receipts/c02-frozen-input-check.txt`;
  it supersedes the hand-written placeholder timestamp inside correction-01's
  `wip-anchor-check.json` (that file and its anchor are untouched).
- `scripts/host-ssh.sh`, `host-ssh-log/ssh-invocations.log` — the separately logged read-only
  guest access of this stage (P3-2).
- `archived-correction-01-scripts/` — the four correction-01 guest measurement scripts
  (`c01-a0-summary.py`, `c01-probe-summary.py`, `c01-f9-flags.py`, `c01-f9-flags2.py`) retrieved
  from the guest and verified byte-identical to the host-side originals that produced the
  correction-01 receipts; the correction-01 shell helpers were already archived under
  `correction-01/scripts/`, and the correction-01 bundle-structure analysis was an inline script
  that was **not retained** (its two outputs are, and this stage's producer supersedes it).
- `receipts/` — the above outputs plus `c02-guest-script-listing.txt` (the guest listing and
  hashes of the archived scripts) and `c02-f9-superseded-explanation.txt` (the checked
  explanation of the one-process sandbox sample).
- `manifest.json` and `anchor.json` — the freeze of correction 02.
