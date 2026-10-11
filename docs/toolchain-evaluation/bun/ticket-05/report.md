# Ticket 05-01 — public artifacts for existing consumers, version 1

## Decision

**PASS for the exercised macOS public-consumer matrix.** The three public
packages built and packed under the pinned Bun-only candidate
(`@navide/plugin-contracts`, `@navide/plugin-sdk`, `@navide/plugin-ui`) are
installed and consumed identically to the Node 22.23.2 / pnpm 10.0.0 baseline
artifacts by four independent consumer projects — npm and pnpm, each against
the candidate and the baseline tarballs. Every consumer operation exits 0:
package-manager install, runtime probes of exports/subpaths/schemas/styles/
portable workers/peer resolution, consumer `tsc` and `vue-tsc`, a consumer
Vite build with all five portable Monaco workers emitted, the public
`navide-plugin` CLI validate/package/sign/verify cases with their existing
denial cases on isolated keys, the manager-linked CLI bin, and the manager's
dependency tree. No consumer environment contains or uses Bun, and no
repository-private source, workspace link or stale distribution is involved.

This is macOS-only evidence for the three published packages and the recorded
tool versions; it supports the public-consumer part of macOS E2 and does not
claim Linux/Windows coverage (07/08), Host install/execution of the artifacts
(ticket 04's packaged lifecycle), registry publication, or historical
released binaries.

## Authority and identities

- Dispatch: `coordination/ticket05-01/dispatch.md`, SHA-256
  `e3a901fa7e9acfc34521f4aa31c4ecfc68042471186787951b0060c987c5e29d` (verified
  before reading), relative to the external evaluation base
  `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain`.
- Worker: session `01a128ea-8487-775e-be45-a9adac52f0c8`, provider `deepseek`,
  model `deepseek-flash`, reasoning level `max`; worktree
  `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch
  `eval/issue-2-bun-toolchain`, HEAD
  `2c2707a3e3cb89cea2a769b7a7b3a41efaf630a5`. `git status --porcelain` was
  empty before this ticket's work; the index was never written to.
- Guest: the retained disposable UTM macOS guest (`navide@192.168.64.2`, macOS
  27.0.1 build 26A434, arm64) over the retained SSH options; every invocation
  is recorded by the carried-over `host-ssh.sh` wrapper in
  `ticket-05-01/host-ssh-log/ssh-invocations.log`.
- Toolchain identities (recorded in each case): Bun 1.4.2
  (`35d20dd0263e5c950194434b925454fdfa9ba6e4467da960410fa05b08a7a5b5`,
  revision `1.4.2+744846f84`), Node 22.23.2
  (`18e387c90ab8a8400183e8bdd396376e1e875b91b4c874b894dcade7b35bf572`),
  npm 10.9.8 (bundled with Node 22.23.2), pnpm 10.0.0
  (`tools/pnpm/lib/bin/pnpm.cjs`
  `b276da51dc8ca5b0d3ee3371695b50fc8b3244b281b091c63a3f082a88dadeb9`),
  uv 0.12.23, Python 3.12.11, Electron 44.4.5 cache zip
  `a212eee63ba2f45fd83bd28f77a3e3313a336ad17a4c25adf617942eef5e0e2c`.

## Source, artifacts and cases

- Candidate source: `git archive HEAD` (`2c2707a3`), archive
  `head-2c2707a3.tar`, SHA-256
  `b931eb9bdd2a1a570fc2c023212fa23b4f4f8216646cab043107a4136bea834e`.
- Baseline source: `git archive f4e9fd50`, archive `base-f4e9fd50.tar`,
  SHA-256 `e3da8e5c9900c599920634fa1515cb2fdd53b5d15bd95a09225a2e08d8c723b9`
  — byte-identical to the ticket-01 frozen baseline archive. Both archives
  have no `.git` directory.
- Published packages: the repository publishes exactly three npm packages
  (`packages/plugin-contracts`, `packages/plugin-sdk`, `packages/plugin-ui`;
  the root `workspaces` field lists the same three). Plugins are distributed as
  `.vsix` archives through the Plugin CLI, not as npm packages, so no further
  public package exists to pack.
- Build/pack cases (fresh, each with its own HOME/XDG/caches/stores/TMPDIR/
  data directory and case-owned toolchains):
  - `t05-cand-bun` (Bun 1.4.2 only; `node`, `npm`, `npx`, `pnpm` absent from
    the case PATH): `bun scripts/bun-evaluation.mjs install` (frozen, exit 0,
    7.0 s), `bun scripts/bun-evaluation.mjs run build:public-packages` (exit 0,
    7.9 s), then `bun pm pack` per package through the evaluation dispatcher.
    Observed runtimes: only the case Bun plus the case esbuild child.
  - `t05-base-node` (Node 22.23.2 + pnpm 10.0.0): `pnpm install
    --frozen-lockfile` (exit 0, 10.2 s), `pnpm run build:public-packages`
    (exit 0, 9.9 s), then `pnpm pack --pack-destination` per package. Observed
    runtimes: only the case Node plus the case esbuild child.
- Consumer cases (fresh; Node 22.23.2 + bundled npm 10.9.8 + pnpm 10.0.0 on
  the case PATH, no `bun` anywhere on that PATH): `t05-cons-npm-cand`,
  `t05-cons-npm-base`, `t05-cons-pnpm-cand`, `t05-cons-pnpm-base`. Each holds
  an independent consumer project (not part of the repository, no workspace
  links, no source aliases) that declares the three packed tarballs as
  `file:./tarballs/...` dependencies and its own pinned third-party
  dependencies: vue 3.5.34, vue-i18n 11.4.5, monaco-editor 0.55.1,
  @vitejs/plugin-vue 6.0.9, typescript 5.9.3, vite 6.4.3, vue-tsc 2.2.12,
  @types/node 22.20.3.
- The repository's own external-consumer tests
  (`src/main/plugins/pluginExternalWorkspace.test.ts`,
  `plugins/navide-git/tests/compositionBoundary.test.ts`,
  `plugins/navide-mini-ide/tests/packageBoundary.test.ts`,
  `packages/plugin-sdk/bin/navide-plugin.test.ts`, and
  `tests/support/publicPackagesSetup.ts`) were used as prior art for what the
  public contract covers and for the CLI's positive/negative cases. They are
  not treated as runtime evidence for the npm/pnpm consumption paths: those
  tests extract tarballs with `tar` or link repository packages, so this
  ticket performs the manager-owned installs separately.

## Per-consumer-toolchain results

All exits are 0; every case's receipt, log and summary are delivered. The
"manager" column is the manager that performed the install and the CLI bin
wiring; the other manager was present on PATH and recorded but not used.

| Consumer case | Manager | Artifacts | Install | Probes | `tsc` | `vue-tsc` | Vite build | Build checks | CLI cases | Manager bin | Dependency tree |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `t05-cons-npm-cand` | npm 10.9.8 | candidate | 0 (24.7 s) | 49/49 | 0 | 0 | 0 (11.1 s) | 12/12 | 19/19 | 0 | 0 |
| `t05-cons-npm-base` | npm 10.9.8 | baseline | 0 (20.7 s) | 49/49 | 0 | 0 | 0 (8.9 s) | 12/12 | 19/19 | 0 | 0 |
| `t05-cons-pnpm-cand` | pnpm 10.0.0 | candidate | 0 (4.9 s) | 49/49 | 0 | 0 | 0 (9.1 s) | 12/12 | 19/19 | 0 | 0 |
| `t05-cons-pnpm-base` | pnpm 10.0.0 | baseline | 0 (4.9 s) | 49/49 | 0 | 0 | 0 (9.7 s) | 12/12 | 19/19 | 0 | 0 |

Command/environment/exit/log records: `retrieved/guest/<case>/receipts/*.json`
(46 receipts in total: 5 per build case, 9 per consumer) with the combined logs
in `retrieved/guest/<case>/logs/`. Each receipt carries the exact argv, cwd,
allowlisted environment, exit code, wall time, log SHA-256, the observed
process tree and any remaining owned processes (zero everywhere).

### Runtime probes (49 checks per consumer)

- Package resolution: all three packages resolve under the consumer project
  itself (npm: `node_modules/@navide/...`; pnpm: `node_modules/.pnpm/...`
  through the `@navide/...` links), version `1.0.0`, no `bun` field, and
  `sdk.PluginError === contracts.PluginError` instance identity through the
  peer edge.
- Exports: contracts (`PluginError`, `V2_VIEW_LOCATIONS` ×7,
  `validatePortableArchiveEntries`, `validFilePickerRequest`,
  `manifestV2Schema`, `capabilitiesV1`, `executionPolicyV1Schema` — a clean
  list returns `null`, a case-colliding list returns the documented
  `{ kind: 'duplicate' }`), SDK (`PluginError`, `PluginBackendError`), UI
  (`foundation`: `useNotify`, `DEFAULT_THEME`; `shared`: `settingsGet`;
  `file-picker`: `createTerminalFilePicker`; root: `usePluginContext`).
- Schema subpaths: `./schemas/plugin-manifest-v2.schema.json`,
  `./schemas/capabilities-v1.json`,
  `./schemas/execution-policy-v1.schema.json` load through the exports map and
  equal the re-exported schema objects.
- Styles: `@navide/plugin-ui/styles.css` resolves to a 41 256-byte asset
  containing custom-property declarations.
- Portable workers: the installed `dist/editor/index.js` references exactly
  five `*.worker-*.js` files by relative URL, each present and non-empty in
  the installed package; no absolute `/assets/` worker reference exists.
- Peers: vue 3.5.34, vue-i18n 11.4.5 and monaco-editor 0.55.1 resolve from the
  consumer's own install.
- One informational check fails identically in all four consumers: a plain
  Node import of the browser-oriented `@navide/plugin-ui/editor` bundle is not
  applicable because `monaco-editor`'s ESM imports CSS. The exercised
  consumer path for the editor is the bundler build below; this is recorded as
  an informational non-claim, not as a compatibility gap.

### Declarations/types

`tsc --noEmit` (consumer's TypeScript 5.9.3) over `src/main.ts` and
`vue-tsc --noEmit` (2.2.12) over `src/main.ts` plus a consumer SFC
(`src/Probe.vue`) importing `EditorPane`, `useNotify` and a
`Pick<PluginContext, ...>` prop both exit 0 in all four consumers.

### Consumer build

`vite build` (consumer's Vite 6.4.3 with `@vitejs/plugin-vue`) exits 0 in all
four consumers and emits 98 assets, including nine files matching the five
portable worker prefixes (the five shipped plugin-ui workers plus Monaco's own
lazily loaded worker entries). The build checks confirm the bundle carries the
consumer's own probe markup and contains no `src/renderer` reference, no
absolute consumer-case path and no `capabilityBackend` module. All four
consumers produce identical worker asset names and the same 24 398 657 bytes
of JavaScript, consistent with the identical package contents.

### Public Plugin CLI (19 cases per consumer, run through the consumer's Node)

From the installed `@navide/plugin-sdk` bin, with isolated CLI keys generated
in the case and an isolated `NAVIDE_PLUGIN_CONFIG_DIR`, all four consumers:

- scaffold a plugin (`init`), reject an invalid id, validate the scaffold;
- `package` the scaffold, and a second package is byte-identical;
- refuse source-only entries, a duplicate JSON file-list key, and an
  undeclared schema file; accept a manifest-declared detail-target schema;
- refuse a backend package with `--target universal` and package it for the
  exact build-host target with the executable backend entry listed as
  `-rwxr-xr-x` in the archive;
- generate an owner-only keypair (mode 0600) and refuse to overwrite it;
- `sign` and `verify` an archive; refuse a tampered archive, a wrong public
  key, and signing with a world-readable private key;
- refuse `publish` without a token.

The manager-linked bin is exercised separately through `npm exec --yes=false
-- navide-plugin ...` and `pnpm exec navide-plugin ...` (scaffold + validate),
both exit 0. The managers' dependency trees list the three `@navide` packages
at 1.0.0 with no unmet peers.

## Artifact diff: candidate vs baseline

Both provenance files (same producer in both cases) compare 53/53, 13/13 and
108/108 members with identical file lists. Different packers produce different
tarball container bytes; member contents are identical except:

| Package | Candidate tarball (Bun pack) | Baseline tarball (pnpm pack) | Members identical | Content difference | Mode difference |
|---|---|---|---|---|---|
| `@navide/plugin-contracts` | `9659df0f…`, 38 669 B | `3724befd…`, 40 140 B | 52/53 | `package/package.json`: trailing newline added by the Bun packer (755 vs 754 B; parsed documents equal) | none |
| `@navide/plugin-sdk` | `c3bc4e73…`, 38 168 B | `ffe0b648…`, 39 398 B | 12/13 | `package/package.json`: trailing newline (682 vs 681 B; equal) | `package/bin/navide-plugin.mjs` mode `0o777` vs `0o755`; contents byte-identical (`c1ebe9c3…`) |
| `@navide/plugin-ui` | `c269f486…`, 3 017 431 B | `c54863d3…`, 3 034 490 B | 107/108 | `package/package.json`: `"files"` array reformatted to one line plus trailing newline (1 476 vs 1 483 B; equal) | none |

`analysis/artifact-diff.json` records the full member-level comparison;
`all_semantically_equivalent` is true. No member is missing on either side and
no file-type difference exists. The `0o777` mode is a Bun-packer metadata
artefact with no consumer effect: npm and pnpm both install the file as
`-rwxr-xr-x` in all four consumers (read-only `stat` receipt in correction 01,
`c01-installed-cli-mode`), and the CLI runs from it. The three
`package.json` differences are packer formatting; the parsed documents are
equal, so versions, exports, peers, `files`, `bin` and `publishConfig` are
unchanged.

The candidate build also reproduces the ticket-03 public-package product
inventory: 51 contracts files, 4 SDK files, 106 UI files, with the same five
portable worker asset names.

## Evidence that consumers need no Bun

- The four consumer cases run under a PATH of
  `tools/node/bin:tools/pnpm/bin:/usr/bin:/bin:/usr/sbin:/sbin`, whose setup
  verifies that no PATH entry contains a `bun` executable; `command -v bun`
  reports `BUN_ABSENT` in all four version receipts.
- No `bun` file exists anywhere under the four consumer case directories.
- Every consumer receipt observes only the case Node binary (plus the
  case-owned esbuild child during install/build); no Bun executable appears in
  any observed process tree and no case-owned process remained after any
  command.
- The artifacts declare no Bun requirement: no `bun`/`bunx` script, no
  `engines.bun`, and no `workspace:` specifier inside any packed
  `package.json` (checked separately).

## Gaps and non-claims

- macOS only; Linux and Windows remain issues 07/08.
- Three npm packages were exercised. Plugin `.vsix` archives and the Desktop
  Host's install/execution lifecycle are outside this ticket; no Host install
  of these artifacts was performed.
- Registry `login`/`publish` network flows were not exercised (publication is
  not authorized); only the local missing-token denial ran.
- Historical released binaries are not covered.
- One manager version each: npm 10.9.8 (bundled with Node 22.23.2) and pnpm
  10.0.0.
- A plain Node import of the UI editor bundle is not applicable (Monaco ESM
  CSS import); the consumer Vite build is the exercised browser path.
- Timings above are recorded for completeness; issue 06 owns performance.
- pnpm 10 ignores esbuild's install script by default while npm runs it; both
  installs and both builds succeeded via the prebuilt platform binaries.
- The SDK tarball ships its `bin/*.test.ts` sources in both artifact sets
  (pre-existing `files: ["dist","bin"]` behaviour, identical on both sides).
  Noted, not changed.

## Deviations, exactly as they occurred

Wording note (correction 01): where a receipt or log named below was
overwritten in place, the version-1 account of that run is labelled an
**UNVERIFIABLE self-report** as far as the original artifact is concerned.
Correction 01 recovers the exact recorded outputs from the worker session
transcript and re-runs the failing variants under new labels; see
“Correction 01”.

1. **Consumer setup tarball naming (harness error, corrected).** The first
   `t05-consumer-setup.sh` run copied the tarballs under their unscoped names
   (`plugin-contracts-1.0.0.tgz`) while the frozen consumer `package.json`
   references `navide-plugin-*.tgz`. No install ran against those copies; the
   four project directories were removed and recreated with the
   packer-canonical names, and the setup script now keeps the packer's name.
   Version 1's claim that “the first run's `COPIED` lines remain in the host
   SSH log” was **false** and is withdrawn: the SSH wrapper records invocation
   argv only, so the invocation trail (SSH log lines 31–43: the four first-run
   setup calls at 03:11:24–25 and the re-issue at 03:11:33–34) is the only
   record there. Run-1's stdout was written to no file in the evidence root;
   it is recoverable verbatim from the worker session transcript (line 196,
   2026-10-11T03:11:25Z) and is delivered in correction 01.
2. **Template and per-consumer script copies replaced in place
   (03:13:01–02, previously unrecorded).**
   `scripts/consumer/scripts/runtime-probes.mjs` and
   `scripts/consumer/src/main.ts` were edited after the consumer projects had
   been created, and the corrected copies were then copied over the four
   consumers' own copies (`scp`/`cp` at 03:13:01, SSH log lines 57–66). The
   superseded copies inside the four consumers and the template's own earlier
   revisions no longer exist on disk; no evidence file captured their contents,
   and `main.ts` v1 never executed, so it has no outcome at all. Both earlier
   revisions are recoverable from the session transcript and are delivered in
   correction 01.
3. **Probe v1 expectation (self-correction).** The first probe run (npm-cand
   only) failed one synthetic expectation:
   `FAIL contracts portable verdict: null` — the probe assumed
   `validatePortableArchiveEntries` returns a `{kind}` object for a clean
   list, while the repository's contract returns `null` and reserves the
   object for violations. The corrected probe asserts `null` for a clean list
   and the documented duplicate classification. The v1 receipt/log were
   overwritten in place, so version 1's account of that run is an
   **UNVERIFIABLE self-report** as far as the original artifact is concerned;
   correction 01 recovers the exact output and reproduces the run.
4. **CLI-cases script defects (self-corrections).** Run 1 failed before any
   case with `ReferenceError: dirname is not defined` (missing `node:path`
   import). Run 2 executed all 19 cases but one assertion parsed the
   `unzip -Z -v` listing with a single-line pattern that cannot match its
   two-line layout, reporting 18 pass / 1 fail
   (`package-backend-exact-target ... missing=archive lists the executable
   backend entry`). Run 3 parses the per-entry block and passes 19/19. Both
   earlier receipts/logs were overwritten in place; version 1's accounts of
   runs 1 and 2 are **UNVERIFIABLE self-reports** as far as the original
   artifacts are concerned, and correction 01 recovers their exact outputs and
   reproduces both runs.
5. **Artifact-diff first pass (self-correction).** The first pass classified
   the packer's `package.json` reformatting as a content difference
   (`ALL_CONTENT_IDENTICAL=False`). The corrected pass adds JSON-semantic
   classification and reports `ALL_SEMANTICALLY_EQUIVALENT=True`;
   `analysis/artifact-diff.json` was overwritten in place, so version 1's
   account is an **UNVERIFIABLE self-report** as far as the original analysis
   file is concerned. Correction 01 recovers the v1 script text and re-runs it
   against the unchanged provenance inputs.
6. **Manager cwd difference (observed, not a failure).** `npm exec` ran the
   CLI in the requested `cli-work` directory, while `pnpm exec` ran it with
   the consumer project as cwd (the pnpm scaffold landed in
   `project/binlink`). Both invocations exited 0.
7. **Baseline extraction listing (harmless).** `shasum` printed
   “No such file or directory” for `bun.lock` and
   `scripts/bun-evaluation.mjs` in the baseline extraction because those files
   do not exist at the frozen base; the extraction succeeded and the lines are
   retained in the guest log.
8. **Host harness hiccups (no state effect).** `t05-exec.sh` was not
   executable on its first invocation (permission denied before any remote
   command ran); one `scp` of the consumer directory used `-R` instead of
   `-r` and failed with `not a regular file` before any transfer. In correction
   01 the session-recovery script needed two marker refinements before it
   selected exactly the genuine tool results (the first marker also matched
   the worker's own later inspection output); the intermediate runs wrote only
   partial snapshot files, which were removed before the clean run.

## Correction 01 — recovered content, reproductions, harness change

Reviewer verdict on version 1: HOLD on evidence integrity, technical criteria
satisfied. This correction changes no technical conclusion: the consumer
matrix, the artifact comparison and the dispositions stand. It fixes one false
statement, adds one missing deviation, labels the overwritten runs correctly,
recovers their exact content where the session transcript retains it,
reproduces the failing variants under new labels, and hardens the harness
against further overwrites. The `freeze-v1` **anchor file itself** was not
modified (`aaad420474416846636d1091956c546f5eed240706d1e52795b9fb4cae74c501` still
verifies), while **5 of the 537 files it binds changed** in this correction —
the append-only host SSH log, `scripts/t05-run.py`, `scripts/t05-build-evidence.py`
and the two corrected tracked documents (full accounting under “freeze-v1
re-verification” below). Correction 01 is a new snapshot with its own anchor.

### Recovery method (no reconstruction from memory)

`correction-01/scripts/c01-session-recover.py` reads the worker session
transcript (`PI_SESSION_FILE`,
`…/2026-10-11T03-03-40-423Z_01a128ea-8487-775e-be45-a9adac52f0c8.jsonl`) and
replays the recorded `write`/`edit` operations for each affected file. Every
edit must match exactly once, and the replayed chain must reproduce the
current on-disk file byte-for-byte before any snapshot is accepted. All four
chains verified (`chain_reproduces_on_disk_file: true`). The recovered
snapshots, the recovered tool outputs (each identified by its exact marker and
session line) and the transcript coordinates are recorded in
`correction-01/repro/session-recovery/recovery-manifest.json`.

| Recovered content | SHA-256 (recovered) | Role |
|---|---|---|
| `runtime-probes.v1-deployed-to-consumers-and-used-by-the-failing-run.mjs` | `489b5c747fcb…` | the probe revision deployed to the four consumers at 03:13 and used by the failing run |
| `runtime-probes.v2-final.mjs` | `5481714daa81…` | current template revision (matches disk) |
| `cli-cases.v1.mjs` | `2b89ddb2594c…` | CLI cases before the `dirname` import |
| `cli-cases.v2.mjs` | `72d78bcfd149…` | CLI cases after the import, before the unzip-listing fix |
| `cli-cases.v3-final.mjs` | `606bf97ba52b…` | current revision (matches disk) |
| `main.ts.v1.ts` | `755cd6362d09…` | the `src/main.ts` revision replaced at 03:13; never executed |
| `main.ts.v2-final.ts` | `ecc653f97845…` | current revision (matches disk) |
| `artifact-diff.v1.py` | `fcde1989615b…` | first-pass comparison logic |
| `artifact-diff.v2-final.py` | `44d179083ca3…` | current revision (matches disk) |

Recovered tool outputs (raw artifacts were overwritten in place, so these are
session-transcript records, not raw files): `run1-consumer-setup.stdout.txt`
(`4114e38b1436…`, session line 196), `probe-v1.stdout.txt`
(`5a584c314e80…`, line 212), `cli-cases-v1.stdout.txt`
(`803756687021…`, line 234), `cli-cases-v2.stdout.txt`
(`1d2329445b17…`, line 238), `artifact-diff-v1.stdout.txt`
(`d3155fb25edb…`, line 270).

### Reproductions under new labels (never overwriting)

Every reproduction ran in a scratch copy of the consumer project
(`project-c01-probe-v1`, `project-c01-cli`; created with `cp -a` and their
prior output files removed, as logged) so that no original consumer output was
touched, and each run used a new receipt/log label.

| Variant | Command | Result | Versions comparison |
|---|---|---|---|
| probe v1 | `node scripts/runtime-probes-v1-repro.mjs` in `project-c01-probe-v1` | exit 1; `FAIL contracts portable verdict: null`; `PROBES_TOTAL=48 PROBES_FAILED=1` | byte-identical to the recovered v1 output after scratch-path normalization |
| cli-cases v1 | `node scripts/cli-cases-v1-repro.mjs` in `project-c01-cli` | exit 1; `ReferenceError: dirname is not defined` at line 13 | identical to the recovered v1 output except the reproduction's own file path/name |
| cli-cases v2 | `node scripts/cli-cases-v2-repro.mjs` in `project-c01-cli` | exit 1; 18/19, `package-backend-exact-target` fails | byte-identical to the recovered v2 output |
| artifact-diff v1 | `python3 artifact-diff.v1.py` against the unchanged provenance inputs | `ALL_CONTENT_IDENTICAL=False`, same per-package lines | stdout byte-identical to the recovered v1 output |
| `main.ts` v1 | not run — it was replaced before any consumer operation and never executed | no outcome; recovered byte-exactly for the record | — |

Receipts and logs: `correction-01/repro/reproductions/guest/`
(`t05-cons-npm-cand.c01-probe-v1-repro.{json,log}`,
`…c01-cli-cases-v1-repro.{json,log}`,
`…c01-cli-cases-v2-repro.{json,log}`, plus the two reproduction output JSONs),
and `correction-01/repro/artifact-diff-v1-repro.stdout.txt` with the v1
analysis it regenerated at
`correction-01/repro/artifact-diff-v1-root/analysis/artifact-diff.json`
(`d811a2265933…`; the frozen `analysis/artifact-diff.json` is untouched).

### Harness change: no-clobber output paths

`t05-run.py` now computes its log and receipt paths through a `no_clobber`
helper: an existing path is replaced by an `-r2`, `-r3`, … suffixed sibling
and the receipt records `paths_renamed_to_avoid_clobber`. The change is
confined to the evaluation-root harness and is recorded here:

| Revision | SHA-256 |
|---|---|
| before (as frozen in `freeze-v1`) | `ec1fb975acfc305e50c8208bd8c3cdbf8e3b0dfbf16d6f5e3c318539d2dcec37` |
| after (used by all correction-01 runs) | `96a47392569bcf5ba61a873455b315c77ab2113e576eb0501df45e5a1c42ee9a` |

The version-1 copy is preserved verbatim in the frozen retrieval archive at
`retrieved/guest/_t05-scripts/t05-run.py` (`ec1fb975acfc…`, 9 265 bytes); the
fix changed only the live evaluation-root copy and the guest's live copy.

The helper was unit-checked on the host (free path unchanged; existing →
`-r2`; existing `-r2` → `-r3`) and the guest copy was updated to the same hash
before any correction run. Further runs of this issue cannot silently
overwrite an earlier receipt or log.

### Installed CLI mode receipt and the packaging note

The new receipt `c01-installed-cli-mode` (log and JSON per consumer in
`correction-01/repro/reproductions/guest/`) records, read-only over SSH, the
installed `@navide/plugin-sdk/bin/navide-plugin.mjs` in all four consumers:

| Consumer | Mode | Bytes | Content SHA-256 |
|---|---|---|---|
| `t05-cons-npm-cand` | `-rwxr-xr-x` (0755) | 23 186 | `c1ebe9c3fc22…` |
| `t05-cons-npm-base` | `-rwxr-xr-x` (0755) | 23 186 | `c1ebe9c3fc22…` |
| `t05-cons-pnpm-cand` | `-rwxr-xr-x` (0755) | 23 186 | `c1ebe9c3fc22…` |
| `t05-cons-pnpm-base` | `-rwxr-xr-x` (0755) | 23 186 | `c1ebe9c3fc22…` |

So the archive's `0o777` entry mode (Bun `bun pm pack`; the baseline pnpm pack
writes `0o755`) is normalized by both installing managers to `0755`, and the
installed file's content is identical in all four consumers and both artifact
sets. **Release-packaging note:** the `0o777` entry mode is a packer metadata
artefact, not behaviour, and no consumer is affected; but if Bun's packer is
ever used for publication, the archive modes should be normalized (or the
packer's behaviour revisited) so a world-writable mode is not advertised in
published `.tgz` metadata. No change is made here — this ticket's scope is
compatibility evidence.

### freeze-v1 re-verification

After the correction, `correction-01/scripts/c01-verify-freeze-v1.py` re-hashed
every file bound by `freeze-v1` (output:
`correction-01/repro/freeze-v1-reverification.txt`). The anchor file itself
still hashes to `aaad420474416846636d1091956c546f5eed240706d1e52795b9fb4cae74c501`.
Of the 537 files it binds, **532 are unchanged and 5 have changed**, each one
accounted for: the append-only host SSH log (correction-01 invocations added),
`scripts/t05-run.py` (the instructed harness fix), `scripts/t05-build-evidence.py`
(corrected evidence wording), and the two corrected tracked documents
(`report.md`, `evidence.json`). No version-1 raw receipt, log, source archive,
artifact provenance file, tarball or retrieval archive changed — every one of
them still matches `freeze-v1`.

## Correction 02 — version-1 preservation status and wording

Reviewer round-2 findings: the version-1 evidence generator was not preserved;
the version-1 preservation status of the report and evidence was unstated; the
version-1 `t05-run.py` copy had no named location; and “`freeze-v1` was not
modified” overstated the case. This correction is documentation and recovery
only: no technical conclusion, receipt or measurement changes.

### Version-1 preservation status (every file the findings name)

| Version-1 file | freeze-v1 SHA-256 | Status | Evidence |
|---|---|---|---|
| `scripts/t05-build-evidence.py` v1 | `b9eefbcb1d8f057a72b00adb3df068e3dc1db384f48fa2c7b9188f4ea6583385` (16 065 B) | **preserved and verified** | `correction-02/recovered/t05-build-evidence.v1.py`, recovered from the session record; replayed chain (write + recorded ops + the recorded inline-replace at session line 159) reproduces the current file byte-for-byte |
| `docs/…/ticket-05/report.md` v1 | `19265e66699737af6b2275ca6d152adb2b4d0241976934754bc38b8484b9d9ee` (18 876 B) | **preserved and verified** | `correction-02/recovered/report.v1.md`, the recorded initial write payload; hash matches `freeze-v1` exactly |
| `docs/…/ticket-05/evidence.json` v1 | `ee0c88aa39ddd44eeca8db60c3a9b2f3cc3e986c6e5dbd238e7a2c4d1d345a0a` (49 532 B) | **not preserved, not reproducible** | the generator stamps `generated_utc` from the host clock at run time, so the exact version-1 bytes cannot be regenerated; the version-1 generator that produced them is the recovered file above |
| `scripts/t05-run.py` v1 | `ec1fb975acfc305e50c8208bd8c3cdbf8e3b0dfbf16d6f5e3c318539d2dcec37` (9 265 B) | **preserved** | frozen retrieval archive copy `retrieved/guest/_t05-scripts/t05-run.py`; the correction-01 fix changed only the live evaluation-root and guest copies |

Recovery is by replay of the recorded session operations, and delivery is gated
on the `freeze-v1` hash: content that did not hash to the frozen value would
have been recorded as not preserved (`correction-02/recovered/recovery-manifest.json`
records every gate result, including the one recorded failed edit call at
session line 423 that was skipped because it changed nothing). For
`report.md` v1 the hash gate passes, but the whole-chain corroboration stops at
session line 401: the recorded edit arguments carry typographic quotes where
the file has straight ones (`“No such file or directory”` vs `"No such file or
directory"`), so exact replay cannot proceed; the decisive identity check is
the `freeze-v1` hash, which matches.

### freeze-v1 re-verification (round 2)

`correction-02/scripts/c02-verify-freeze-v1.py` re-hashed all 537 files bound by
`freeze-v1` into a **new** file,
`correction-02/repro/freeze-v1-reverification-r2.txt` (the round-1 record
`correction-01/repro/freeze-v1-reverification.txt` is untouched), and also
re-hashed the correction-01 anchor. Results: the `freeze-v1` anchor still
hashes to `aaad420474416846636d1091956c546f5eed240706d1e52795b9fb4cae74c501`;
532 of its bound files are unchanged and the same 5 changed (SSH log,
`t05-run.py`, `t05-build-evidence.py`, `report.md`, `evidence.json`); the
correction-01 anchor is unchanged at
`ce8e1b479d10b90233627a94014118819be80dd740c1757fb9ba859519c6f02e`.

### Wording corrected

“`freeze-v1` was not modified” (correction-01 section) is replaced by the
precise statement: the `freeze-v1` **anchor file** is unchanged and still
verifies, while 5 of the 537 files it binds changed in correction 01, each one
accounted for.

### Anchor build note

The correction-02 freeze ran twice. The first anchor file
(`4ae5c1f0eb83fd0957c44e0464e23954a752af75984c8f7f9d9ce777e6ca36d5`) excluded
`anchor.json`/`anchor.sha256` by basename and therefore failed to bind the
correction-01 anchor — the opposite of what this snapshot is meant to certify.
It was discarded, and the final correction-02 anchor binds
`correction-01/anchor.json`
(`ce8e1b479d10b90233627a94014118819be80dd740c1757fb9ba859519c6f02e`) as well as
the `freeze-v1` anchor.

## Frozen delivery and anchor

Version 1 of this delivery, `freeze-v1`, remains untouched and still verifies
(anchor SHA-256 `aaad420474416846636d1091956c546f5eed240706d1e52795b9fb4cae74c501`;
re-verification above: 532 of 537 bound files byte-identical, 5 recorded
changes, no raw evidence changed); it is the historical snapshot of the first
delivery. This ticket's current snapshot is `correction-02`, which binds this
report, `evidence.json`, the frozen file set, the correction-01 tree
(recovered content, reproductions, patched harness and its anchor), and the
correction-02 tree (the recovered version-1 files, the round-2 re-verification
and the new anchor):

```
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01/correction-02/anchor.json
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01/correction-02/anchor.sha256
```

The correction-01 anchor stays as delivered at
`ce8e1b479d10b90233627a94014118819be80dd740c1757fb9ba859519c6f02e` (verified
unchanged by the round-2 re-verification) and is bound by the new anchor.

The corrected anchor covers this document, so its own SHA-256 is recorded
beside it in `anchor.sha256` and in the completion reply rather than inside
this file. `freeze-v1` binds the same files that version 1 delivered; the two
anchors differ because this report and `evidence.json` were corrected.

Frozen delivery `freeze-v1` bound by SHA-256 and byte count the version-1
report, `evidence.json`, every harness script and consumer template file under
the external evidence root, both source archives, both artifact provenance
files and tarballs, the artifact-diff analysis, the retrieval archive, and the
raw host SSH log, and its anchor is written outside the repository:

```
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01/freeze/freeze-v1.json
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01/freeze/freeze-v1.sha256
```

The anchor covers the report as it stood at the time, so the anchor's own
SHA-256 is recorded beside it in `freeze-v1.sha256` and in the completion reply
rather than inside the file. Raw evidence root:
`/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01`
(scripts, host SSH log, source archives, `retrieved/guest/` with all 46
version-1 receipts, logs and summaries, `analysis/artifact-diff.json`, and the
`correction-01/` tree added by this correction).

## Boundaries

No stage, commit, push, hosted action, PR, worktree or branch change. No
product, test, script, lockfile or configuration change was made; the only
repository writes are this report and `evidence.json`. No third-party package
was patched, no assertion relaxed, no failing case skipped, no sandbox or
authorization control weakened, and no registry publication or personal
credential was exercised.
