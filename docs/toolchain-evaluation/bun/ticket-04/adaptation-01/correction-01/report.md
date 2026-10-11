# Ticket 04 adaptation-01, correction 01

This document is the correction snapshot for the ticket-04 adaptation round. It
answers the review verdict **PASS WITH QUALIFICATIONS** (review dispatch
`dfbf3f0f…`) under the correction dispatch `coordination/ticket04-adapt-01/correction-01-dispatch.md`
(SHA-256 `856fdcef392edcf15b50b63f494349ab17a8dcecc892af854458aa3894c537c9`,
verified before reading). It covers:

1. the bounded probes for the P2 residual (`src/main/plugins/pluginExternalWorkspace.test.ts`)
   and the resulting decision;
2. the P3 guard added to the mock-anchor helper, with targeted re-runs;
3. the three P3 report fixes.

Per the standing rule that evaluation corrections are new reviewed snapshots
rather than edits to accepted history, the round-1 documents
(`adaptation-01/report.md`, `adaptation-01/evidence.json`) and the round-1 anchor
`ticket-04-adapt-01/freeze/freeze-v1.json` are **byte-identical and untouched**;
this file is the corrected snapshot for the clauses below, and
`correction-01/evidence.json` carries its machine-readable form. If the reviewer
wants the v1 report text rewritten in place instead, that is a separate
instruction.

## 1. P2 — residual probes and decision: NOT APPLIED

### Probe setup

The three public packages were packed from the candidate case source with the
case Bun (`bun pm pack`), and every install shape ran against the case package
cache with explicit `--cache-dir`, `--offline --ignore-scripts --omit=peer
--no-save` unless a row says otherwise. Scripts:
`correction-01/scripts/probe-p2-install-shapes.sh`,
`probe-p2b-exec-and-store.sh`, `probe-p2c-overrides.sh`. Raw outputs:
`retrieved/v2/cases/t04a-head-bun/t04a-head-bun/tmp/correction-01/` (§ "Deviations",
item 3, for the probe environment).

### Install shapes measured

| Shape | Form | Result |
|---|---|---|
| A | the test's own shape: all three `file:` deps in the manifest, one `bun install` | **exit 1** — `no cached manifest for "@navide/plugin-contracts" / "@navide/plugin-sdk"` |
| B | sequenced via manifest: contracts-only install → then all three | contracts-only **0**, then **exit 1** |
| C | sequenced `bun add`: contracts tarball → then sdk + ui tarballs | contracts-only **0**, then **exit 1** |
| D | positional `bun install <contracts> <sdk> <ui>` with an empty manifest | **exit 1** |
| E | positional `bun install --no-save <three tarballs>` with the deps also listed | **exit 1** |
| F | `bun add <three tarballs>` then a plain `bun install` | both **exit 1** |
| G | positional sequenced: contracts only, then sdk + ui | contracts-only **0**, then **exit 1** |
| L | repeat of the only succeeding shape | contracts-only **0**; contracts + sdk **exit 1** |
| M | manifest `overrides` pinning the three names to the same tarballs | **exit 0**, all three installed, `node_modules/.bin/navide-plugin` present |
| N | `resolutions` (yarn-style) instead of `overrides` | **exit 0** |
| O | shape M with `--prefer-offline` | **exit 0** |
| — | round-1 online variants (`--prefer-offline`, no `--offline`) | **exit 1**, `GET https://registry.npmjs.org/@navide%2fplugin-contracts - 404` |

**No install shape works.** The boundary is exactly the `@navide/plugin-sdk`
package: its non-optional peer `@navide/plugin-contracts` is resolved as a
registry package even though it is the consumer's own `file:` dependency, in
every invocation shape (manifest, sequenced, positional, `--no-save`,
`--omit=peer`, `bunfig [install] peer = false` from round 1). Only a
**manifest-shape** change (M–O) makes Bun install this consumer, by duplicating
the `file:` pins into `overrides`/`resolutions` so the peer lookup finds them
locally.

M–O is not an invocation route: it changes the generated consumer manifest, so
the Bun run and the Node run would exercise *different* consumer shapes and the
daily Bun test would stop proving that a plain `file:`-dependency consumer
installs. Under the dispatch's condition ("route … with a simple mechanical Bun
branch … or do NOT apply"), the route is **not applied**.

### Would the remaining sites have been routable? (measured, for the record)

| Site in the test | pnpm-shaped invocation | Measured Bun route | Status |
|---|---|---|---|
| store path | `pnpm store path` | `bun pm cache` prints the install cache directory (the caller passes `--cache-dir` explicitly) | routable |
| install | `pnpm install --offline --ignore-scripts --config.auto-install-peers=false --lockfile=false --store-dir <path>` | none found (A–G, L, M–O) | **blocked** |
| run check / run package | `pnpm run check` / `pnpm run package` | `bun run check` / `bun run package`; the measured failure was the CLI's own ENOENT on a nonexistent package path, i.e. the runtime started | routable |
| exec navide-plugin | `pnpm exec navide-plugin …` | `bun x navide-plugin …`, or the direct `node_modules/.bin/navide-plugin` with the vitest-style `node` alias on `PATH` | routable |
| nested `node` children | `#!/usr/bin/env node` | a `node -e …` script under `bun run` executed with `process.versions.bun` set (`runtime=bun`) in a shell with no `node` on `PATH` | routable |

### Residual, restated precisely

> Not closed by measured routes. The blocking site is the single install
> invocation (`pnpm install --offline --ignore-scripts
> --config.auto-install-peers=false --lockfile=false --store-dir <path>`); the
> other four pnpm-shaped invocations were measured routable under Bun. Closing it
> needs either a Bun fix for peer resolution of private `file:` dependencies, or
> a decision to change the consumer fixture's install shape (manifest
> `overrides`/`resolutions` duplicating the tarball pins), which changes what the
> daily test proves and is therefore not this round's to make.

## 2. P3 — anchor helper guard

Change in `tests/support/bunMockImporterAnchor.ts` (+5/−3, net +2; 81 lines;
SHA-256 `f76256368270786f7ec09c0257d1549ccf9604849cf680f9d76304199e31ceea`):

- added `if (typeof path !== 'string') return original.call(vi, path, ...rest)`
  with its comment, so the `vi.mock(import('./x'))` form (a promise, not an id)
  is delegated untouched instead of being passed through `anchorPath`;
- dropped the unreachable `if (isAbsolute(path)) return path` branch in
  `anchorPath` (a specifier starting `./` or `../` is never absolute) and the
  now-unused `isAbsolute` import.

Verification on the corrected source:

| Run | Command | Result |
|---|---|---|
| Targeted mock sample, Bun | 4 files (`resume-command.model`, `backend`, `EditorPane`, `transport`) | **41/41 passed**, exit 0, 0.94 s (`corr-mock-sample-bun-r2`) |
| Targeted mock sample, Node | same 4 files | **41/41 passed**, exit 0, 1.02 s (`corr-mock-sample-node-r2`) |
| `typecheck:node` under Bun | `tsconfig.node.json` includes `tests/support/**` | exit 0 (`corr-bun-typecheck-node`) |
| Full root suite, Bun | `bun scripts/bun-evaluation.mjs run test:run` | exit 1 — 12 446 passed / 23 skipped / **1 failed** (`corr-test-run`) |
| Full root suite, Node | `pnpm test:run` | exit 0 — 12 447 passed / 23 skipped / 0 failed (`corr-node-adapted-full`) |

Per project on the corrected source: unit 808 files / 12 198 assertions / 0
failed / 7 pending; artifacts 23 files / 214 / 1 failed (the residual) / 11
pending; cli 4 files / 53 passed; the out-of-discovery `plansPackagedRoundtrip`
file remains conditionally skipped (5). The only failing file in the Bun run is
still `src/main/plugins/pluginExternalWorkspace.test.ts`. Runtime identity in
the corrected runs is unchanged from round 1: only the case Bun (or case Node)
plus case-owned `esbuild`, uv/Python and fixture children observed, no external
`node`/`npm`/`npx`/`pnpm`, `remaining_observed_owned` empty in every receipt.

Adapted-source footprint is now **5 paths, +113/−4 lines** (the helper grew from
79 to 81 lines).

## 3. P3 report fixes

### (a) Node-control alias asymmetry — clause and empirical closure

The round-1 Node control ran the **frozen base** config plus the `setupFiles`
line (SHA-256 `4ad4e56e…`). The candidate config is the **branch** config plus
the same line (SHA-256 `22edeec3…`), and the branch config carries three
`test.alias` entries the base does not:

```
{ find: /^\.\/view\/EditorViewMonaco\.vue$/, replacement: … },
{ find: /^\.\.\/foundation$/, replacement: … },
{ find: /^\.\.\/shared$/, replacement: … },
```

added by the branch in ticket 04 ("Exact mock IDs from EditorPane; let Vite
resolve them without a stack importer"). The round-1 comparison was therefore
not alias-symmetric: candidate and control differed by those three entries as
well as by runtime.

Closure: a third Node control (`corr-node-alias-symmetric`) ran the candidate
config **byte-for-byte** (`22edeec3…`) over the same frozen pnpm install — 835
files passed / 1 skipped, 12 447 passed / 23 skipped / 0 failed, exit 0. The
asymmetry is measured neutral.

Limit, stated rather than papered over: an exact branch-tree Node run is not
available in this environment, because the branch tree cannot be pnpm-installed
at all (the accepted `workspaces`/`workspace:` gap). The alias-symmetric control
is the closest available construction: identical config, identical test-adjacent
files, frozen base dependency install.

### (b) Guest-side hash listing at final-run time

`retrieved/v2/cases/<case>/<case>/tmp/correction-01/adapted-source-hashes.txt`
is the guest-side `shasum -a 256` listing captured immediately before the
corrected final runs, with the exact hashes the runs executed against:

- candidate: `vitest.config.ts 22edeec3…`, `tests/support/bunMockImporterAnchor.ts
  f7625636…`, `tests/integration/plansBuildScripts.test.ts 06bbb483…`,
  `plugins/navide-mini-ide/tests/packageBoundary.test.ts 29dda9a7…`,
  `plugins/navide-git/tests/compositionBoundary.test.ts 14f31a54…`, plus the
  untouched residual file `src/main/plugins/pluginExternalWorkspace.test.ts
  7bfe298b…` (its HEAD blob);
- node control: the same five adapted hashes with the base-variant config
  `4ad4e56e…`; the file then gained a second `vitest.config.ts` line
  (`22edeec3…`) when the config was switched for the alias-symmetric control.

The annotated copy, with those labels, is
`correction-01/receipts/adapted-source-hashes-annotated.txt` (the raw guest files
are left as written).

### (c) Receipts-file annotation (no frozen file renamed)

| Path | What it actually is |
|---|---|
| `retrieved/list/bun-list.json`, `retrieved/list/node-list.json` | **run receipts** of the first `list` label — misleading names, they contain no discovery data |
| `retrieved/list/bun-list-vitest.json`, `retrieved/list/node-list-vitest.json` | the actual `vitest list --json` discovery output the inventory comparison uses |
| `retrieved/cases/<case>/receipts/list.json` | run receipt of the first `list` attempt; the attempt's stdout was redirected into `receipts/list.json` and then overwritten by the receipt of the same label |
| `retrieved/cases/<case>/receipts/list-r2.json` + `tmp/list-vitest.json` | the usable second discovery attempt (the one behind the inventory table) |

## Deviations, exactly as they occurred

1. **Two targeted sample runs matched no files.** The first Bun and Node
   sample runs (labels `corr-mock-sample-bun`, `corr-mock-sample-node`) exited 1
   because the four mock paths were expanded from a zsh variable without word
   splitting (zsh does not split unquoted expansions), so Vitest received one
   filter argument. Re-issued with literal paths (`…-r2`), both green. The
   failed runs, logs and receipts are retained.
2. **Two first evidence tarballs missed their members.** The tarballs were
   built with an escaped glob that zsh passed literally, so the archive collected
   only `tmp/` and one JSON file. The `*-correction01-partial.tar.gz` tarballs and
   the corrected `*-correction01-final.tar.gz` / `*-correction01-v2.tar.gz`
   tarballs are all retained; `v2` is the complete set used here.
3. **The P2 probes ran in the SSH default environment**, not through the case
   `env.json` allowlist: `HOME=/Users/navide`, no `BUN_INSTALL_CACHE_DIR`, no
   `node`/`bun` on `PATH`. They used absolute case paths and the case package
   cache via `--cache-dir`, and wrote only under the case `tmp/`. No secret was
   read. (Round 1's probe scripts ran the same way; every `t04-run.py`-wrapped
   run in both rounds used the case environment.)
4. **`bun pm cache` printed the global default** (`/Users/navide/.bun/install/cache`)
   in that shell, a consequence of (3); it reports the global install cache, and
   the installer under test passes `--cache-dir` explicitly.
5. **The Node case's source identity changed after the alias-symmetric control**:
   a guest-side `vitest.config.base-variant.ts` copy was saved and
   `vitest.config.ts` was switched to the branch config. The v1 state of that
   case remains recorded in the v1 anchor and the v1 case tarballs; only the
   `corr-*` receipts, logs and `tmp/correction-01/` files were added to the case
   directories, and no existing receipt or log was modified.
6. **The v1 anchor's SSH-log entry no longer matches byte-for-byte.** The v1
   anchor bound `host-ssh-log/ssh-invocations.log` at its round-1 state (90
   invocations); the file is append-only and this correction round added 34
   more entries. A verification pass over all 125 v1-bound files
   (`receipts/v1-anchor-verification.txt`) reports 124 unchanged, none missing,
   and this one growth-only delta; every v1 document, tarball, receipt and
   script is byte-identical, and the v1 anchor itself is untouched.

## Frozen delivery and anchor

The correction anchor binds, by SHA-256 and byte count, the five adapted
worktree paths (current hashes), this document, `correction-01/evidence.json`,
the correction probe scripts and builder, and every raw receipt, log, listing
and tarball under `correction-01/`. It is written beside the frozen v1 anchor
and does not modify it:

```
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-adapt-01/correction-01/freeze/correction-01-v1.json
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-adapt-01/correction-01/freeze/correction-01-v1.sha256
```

## Boundaries

No stage, commit or push. The v1 report, v1 evidence and v1 anchor are
untouched. No test assertion, skip, timeout, mock body or inventory entry was
changed; no dependency, lockfile or runtime version was changed; no third-party
package was patched, forked or replaced; no product source was changed; no
Node/pnpm fallback was added to the daily path; no Host capability, Manifest
Permission, grant or Execution Policy was widened.
