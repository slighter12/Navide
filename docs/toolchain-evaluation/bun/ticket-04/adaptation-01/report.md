# Ticket 04 adaptation-01 — minimal Bun adaptations and full re-run, version 1

## Decision

**The 41 previously failing files are down to 1.** With three allowed,
Bun-only adaptations across five paths, the full macOS root Vitest inventory
that ticket 04 recorded as failing under Bun now runs with **1 failed file of
41** — the independent
consumer workspace test whose pnpm-shaped install Bun 1.4.2 cannot perform
(minimal repro below). Everything else is green: full root suite 12 446 passed /
23 skipped / 1 failed (12 470), the packaged Plugin lifecycle 124/124 with real
native children, public Plugin CLI 53/53, Mini-IDE standalone 5/5, the macOS
sandbox and third-party lifecycle cases unchanged, and the Node control run of
the same adapted source is fully green (12 447 / 23 / 0).

The `TokenMonitorApp.test.ts` Bun panic is **explained and resolved**: it is a
downstream symptom of the unanchored relative `vi.mock`, not an independent
blocker. An A/B run on the same case shows the file panics Bun
(`TryFromIntError(PosOverflow)`, 143 s) without the mock anchor and passes 9/9 in
80 ms with it.

No source adaptation weakens an assertion, skips a case, changes an inventory
entry, patches a third-party package, upgrades a dependency or a runtime, or
requires Node/pnpm on the daily path.

## Authority and snapshots

- Dispatch: `coordination/ticket04-adapt-01/dispatch.md`, SHA-256
  `4c993d15c2fc693939a04aba0fc7e9486bbbeee0c0cdab0f7b841393c90f864b` (verified
  before reading), relative to the external evaluation base
  `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain`.
- Worker: session `01a128b7-7327-723f-bfa6-75b43e671f1b`, provider `deepseek`,
  model `deepseek-flash`, reasoning level `max`; worktree
  `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch
  `eval/issue-2-bun-toolchain`, source HEAD
  `e94c054813a6bb188a80898e4ab89fee78582920` (source archive
  `head-e94c0548.tar`, SHA-256
  `302d56249699fa36510b89a26819d1b639bf1c5a9fa84218f9dba84921fa8941`, produced
  by `git archive HEAD`).
- Baseline: frozen base `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`, archive
  `base-f4e9fd50.tar` re-verified in the guest as SHA-256
  `e3da8e5c9900c599920634fa1515cb2fdd53b5d15bd95a09225a2e08d8c723b9`. Ticket
  04-01 results are reused for the unmodified baseline.
- Guest: the retained ticket-03 macOS guest (`navide@192.168.64.2`, macOS
  27.0.1, Darwin 27.0.0 arm64), SSH-only through the retained options, every
  invocation logged by the retained `host-ssh.sh` wrapper into
  `host-ssh-log/ssh-invocations.log`.
- Cases (fresh, under `/Users/navide/eval/`): `t04a-head-bun` (Bun 1.4.2, binary
  SHA-256 `35d20dd0263e5c950194434b925454fdfa9ba6e4467da960410fa05b08a7a5b5`)
  and `t04a-base-node` (Node 22.23.2 `/ 18e387c9…`, pnpm 10.0.0). Both carry
  copied, re-verified pinned toolchains (uv 0.12.23, Python 3.12.11, Go 1.27.2)
  and the same checksum-verified Electron 44.4.5 zip
  (`a212eee6…`) as ticket 04. Each case has its own HOME/XDG/caches/tmp/data
  directory; the Bun case PATH contains no `node`, `npm`, `npx` or `pnpm`
  (checked with `command -v` in the case environment).
- The extracted git archives carry no `.git`; both cases gained the same
  content-neutral marker repository ticket 04 used (Node commit
  `8187166400e49f73a23e500717ea5ac98eff1f57`, Bun commit
  `f63dbfb8c0d7515dd60569664395bcde7e3680a6`).
- Backend prerequisite: `uv --project backend sync --locked` ran in both cases
  (exit 0) before the root suites; the test-only Plans fixture
  (`scripts/build-plans-backend.mjs`, PyInstaller + Go) was built in both cases
  so the fixture-gated cases execute rather than skip.

## Adaptations applied

Preference order from the dispatch was respected: one generic Bun-only
support helper first, then two mechanical test-file routes (three files). Total
footprint: **5 paths, +111 / −4 lines**.

| # | Class(es) | Adaptation | Path(s) | Lines |
|---|---|---|---|---|
| 1 | relative `vi.mock` importer (37 files) | `tests/support/bunMockImporterAnchor.ts` registered through `vitest.config.ts` `setupFiles`: on Bun only, wraps `vi.mock/unmock/doMock/doUnmock/importActual/importMock`, recovers the caller file from the frame Bun prints, resolves the relative id against it (literal → Vite's default extension order → `index.*`) and delegates a pre-anchored id. Absolute/bare specifiers pass through; the file is inert on Node. | `tests/support/bunMockImporterAnchor.ts` (new), `vitest.config.ts` | +79, +2 |
| 2 | `vue-tsc` TS2307 in packed-consumer boundary builds (2 files) | Under Bun, route the same typecheck through the repository's existing `scripts/bun/vue-tsc.cjs` adapter (the consumer's `vue-tsc` is a symlink to the same installed package); Node keeps the consumer's own entry. | `plugins/navide-mini-ide/tests/packageBoundary.test.ts`, `plugins/navide-git/tests/compositionBoundary.test.ts` | +9/−1 each |
| 3 | Node `--import data:` Windows simulation not applied by Bun (1 file) | Bun accepts `--import` but never runs it; under Bun the identical payload is written as a real preload module inside the fixture and passed as `--preload`; Node keeps `--import`. Assertions and cases unchanged. | `tests/integration/plansBuildScripts.test.ts` | +12/−2 |

Per-class result in the adapted candidate root run:

| Class | Files | Before (ticket 04) | After |
|---|---|---|---|
| Vitest relative mock importer (direct + consistent) | 37 | 37 failed | **37 passed** |
| `vue-tsc` packed-consumer boundary | 2 | 2 failed | **2 passed** |
| Node `--import` Windows simulation | 1 | 1 failed | **1 passed** |
| Test helper requires `pnpm` on `PATH` | 1 | 1 failed | 1 failed (residual, see below) |
| Bun panic (`TokenMonitorApp.test.ts`) | 1 | no result, Bun panic | **9/9 passed** (symptom of class 1) |

## Full-suite inventory: baseline vs candidate

Discovery (`vitest list --json`) is identical in every dimension between the
Node control and the Bun candidate — 835 files (unit 808, artifacts 23, cli 4),
the same 12 447 listed tests and identical per-project test-title sets — and its
file sets are identical to both ticket-04 lists. The listed total is one higher
than ticket-04's 12 446 because both cases here had the Plans test fixture built
before discovery, so the fixture-gated third-party case is listed (it is omitted
while it is conditionally skipped). The candidate differs from the Node control
only in the one failed test.

| Project | Discovered | Node control (adapted base) | Bun candidate (adapted HEAD) |
|---|---|---|---|
| unit | 808 files / 12 191 listed | 12 198 assertions, 0 failed, 7 pending | 12 198 assertions, 0 failed, 7 pending |
| artifacts | 23 files / 203 listed | 214 assertions, 0 failed, 11 pending | 214 assertions, **1 failed**, 11 pending |
| cli | 4 files / 53 listed | 53 passed | 53 passed |
| outside discovery (`plansPackagedRoundtrip`) | — | 5 conditionally skipped | 5 conditionally skipped |
| **root suite** | **835 files / 12 447 listed** | **12 447 passed / 23 skipped / 0 failed, exit 0** | **12 446 passed / 23 skipped / 1 failed, exit 1** |

Runner: Node control `pnpm test:run`; candidate
`bun scripts/bun-evaluation.mjs run test:run` (the daily entrypoint) — both with
`--reporter=default --reporter=json`. Ticket-04's frozen-base baseline
(12 447 / 23 / 0, exit 0) is reproduced exactly by the Node control on the
adapted source, so the adaptations are Node-inert at suite scale.

Supplementary gates under Bun (each run explicitly, not inferred):

| Gate | Result | Baseline reference |
|---|---|---|
| Packaged Plugin lifecycle (`NAVIDE_TEST_PACKAGED_PLANS=1`, `NAVIDE_TEST_PRODUCTION_PLANS_BACKEND=1`, `NAVIDE_TEST_ARTIFACTS_PREBUILT=1`) | 3 files / 124 tests passed, exit 0; real PyInstaller `navide-plans`, `navide-plans-go` and `esbuild` children observed executing | 124/124 |
| `pluginBackendHost.thirdParty.test.ts` (live macOS Seatbelt) | 12/12 passed | 12/12 |
| `pluginBackendSandbox.test.ts` | 19 passed + 7 Linux-only skips | same |
| `packages/plugin-sdk/bin/dev-backend.test.ts` | 7 passed + 1 skip | same |
| Public Plugin CLI (`--project cli`) | 53/53 passed | 53/53 |
| Mini-IDE standalone (`--config plugins/navide-mini-ide/vitest.config.ts`) | 3 files / 5 tests passed | 5/5 |
| Typechecks `typecheck:node`, `typecheck:integration` (Bun) | exit 0, 0 errors | — |

Runtime identity: every candidate receipt observes only the case Bun binary plus
case-owned `esbuild`, uv/Python/Go/PyInstaller children; no external
`node`/`npm`/`npx`/`pnpm` executable was observed in any candidate run and
`remaining_observed_owned` is empty everywhere (sampling-based observation, as
in ticket 04).

## Residual failure and its classification

`src/main/plugins/pluginExternalWorkspace.test.ts` still fails (1 test). Ticket
04 classified it as “its own helper still requires a `pnpm` executable on
`PATH`”. This round attempted the allowed helper route (a Bun branch in the
test's `packageManager()` delegating to an in-repo adapter) and **removed it
again after measuring**: the route reaches Bun, but Bun 1.4.2 cannot perform the
install the test asks for. The adapter and route are not part of this delivery;
the measurement is retained in `receipts/probes/` and
`logs/target-externalworkspace-r1.log`.

Minimal repro (three shapes, all in `receipts/probes/`, all with the case Bun
and the case package cache):

- consumer with only `@navide/plugin-contracts` (a `file:` tarball, no peers):
  `bun install --offline --ignore-scripts --omit=peer --no-save` → exit 0.
- consumer with `@navide/plugin-sdk` (whose peer `@navide/plugin-contracts` is
  already present as a second `file:` dependency): same command → exit 1,
  `error: --offline: no cached manifest for "@navide/plugin-contracts"`.
- the same shape online (`--prefer-offline`, or no `--offline`) → exit 1,
  `GET https://registry.npmjs.org/@navide%2fplugin-contracts - 404`; the
  non-optional peers `@navide/plugin-contracts`, `@navide/plugin-sdk`, `vue`,
  `vue-i18n` are all looked up on the registry even though the @navide ones are
  the consumer's own `file:` dependencies. `--omit=peer`, a local
  `bunfig.toml [install] peer = false`, and the online mode all reproduce it.

Classification: **Bun limitation, not a test-helper gap.** Installing a packed
private package whose peer is supplied by another `file:` dependency is not
supported by the pinned candidate. Fixing it needs either a Bun change or a
test redesign (consumer manifest/install shape), both outside this round's
authority. The consumer-workflow substance of this file is the external
Node/npm/pnpm consumer matrix that issue 05 covers separately; no Node/pnpm
fallback was added to the daily path.

## TokenMonitorApp panic: diagnosis

The ticket-04 panic is a symptom of the unanchored relative mock, proven by an
A/B run in the same case on the same source:

| Condition | Result |
|---|---|
| Anchor disabled (guest copy only, restored afterwards, hashes verified) | exit 1; `panic: int cast: TryFromIntError(PosOverflow)` in the tinypool worker after 143.0 s; RSS 3.98 GB / peak 6.77 GB |
| Anchor enabled | exit 0; 9/9 tests in 80 ms |

Without the anchor the test's `vi.mock('../../composables/useBackend', …)` never
applies, the real module graph loads, and Bun eventually panics; with it, the
intended mocked graph runs. The underlying Bun panic is still a Bun defect (it
should not crash), but it is not an independent blocker for this suite and needs
no test rewrite, no timeout change and no suppression. The A/B log and receipt
are delivered (`logs/diag-tokenmonitor-no-anchor.log`,
`receipts/diag-tokenmonitor-no-anchor.json`).

## Node regression check

- Full adapted source under Node: root suite green, 12 447 passed / 23 skipped /
  0 failed, exit 0 — identical to the frozen-base baseline numbers. The adapted
  source there is the frozen base plus exactly the five adapted paths (the three
  edited test files are byte-identical between base and HEAD; the base config
  gained only the `setupFiles` line), with the same marker repository and
  fixture build.
- Targeted Node runs of every edited file on that adapted source: the three
  artifact/boundary files 13/13, a previously mock-failing unit sample 38/38.
- The mock anchor is gated on `process.versions.bun` and the two test-file
  routes keep their Node branch, so Node behavior is unchanged by construction
  and by the full-suite result.

## Deviations, exactly as they occurred

1. **First Node control runs used the branch tree and the wrong install mode.**
   `pnpm install --frozen-lockfile` on the branch source fails (known,
   accepted branch gap: `workspaces` in `package.json` with `workspace:`
   specifiers; pnpm 10 does not read that field) and
   `pnpm install --no-frozen-lockfile` fails with
   `ERR_PNPM_WORKSPACE_PKG_NOT_FOUND`. The Node control therefore switched to
   the frozen base source plus this round's adaptations, which is the faithful
   control for the edited files. Both failed attempts and their logs are
   retained in the case receipts (`node-install.json`,
   `node-install-nonfrozen.json`).
2. **First Node full run had one non-adaptation failure.** A green control run
   initially failed `tests/cli/backendWire.test.ts` with “run `uv --project
   backend sync --locked` first” because the case backend environment had not
   been synced yet. After the sync (exit 0 in both cases) the control is fully
   green; the failed run is retained (`node-adapted-full.json`).
3. **First full comparison had 24 pending instead of 23.**
   `pluginBackendHost.thirdParty.test.ts` is gated on
   `dist-test-fixtures/plans/backend/navide-plans`, which had not been built yet.
   After building the fixture in both cases the pending set matches ticket 04
   exactly (23) and that case runs. The pre-fixture results are retained.
4. **An attempted pnpm-compat adapter was written and reverted.** A new
   `scripts/bun/pnpm-compat.cjs` plus a 7-line `packageManager()` branch in
   `pluginExternalWorkspace.test.ts` were applied, run, and judged not worth
   keeping once Bun's peer-resolution failure was measured: the adapter did not
   produce a pass and adding a pnpm-emulation shim to the daily path is an
   adoption cost without a result. Both were removed; the worktree diff shows
   neither. The measured failure is kept as evidence
   (`logs/target-externalworkspace-r1.log`, `receipts/probes/`).
5. **Probe harness paths.** The first external-consumer probe packed the
   repository root instead of the target packages (the dispatcher's `pack` route
   takes the package as its working directory, not as a positional argument);
   the corrected probe is the one analysed here. Both probe scripts are retained
   under `scripts/`.
6. **PyInstaller fixture hashes differ between the two cases** (9 142 288 vs
   9 144 656 bytes for `navide-plans`), as in ticket 04: the builder is not
   byte-reproducible. Each case builds and uses its own fixture; no cross-case
   fixture was copied.
7. **`receipts/list.json` was overwritten once** by the `t04-run.py` receipt of
   the same label; the discovery runs were repeated with a distinct output path
   (`tmp/list-vitest.json`, `receipts/list-r2.json`) and only those are used.

## Frozen delivery and anchor

Frozen delivery `freeze-v1` binds, by SHA-256 and byte count, the five changed
tracked source paths, this report, `evidence.json`, every harness script, probe
script, raw case receipt and raw log under the evidence root, and the guest
identities. The anchor is written outside the repository:

```
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-adapt-01/freeze/freeze-v1.json
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-adapt-01/freeze/freeze-v1.sha256
```

The anchor covers this document, so the anchor's own SHA-256 is recorded beside
it in `freeze-v1.sha256` and in the completion reply rather than inside this
file. Raw evidence root:
`/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-adapt-01`
(`scripts/`, `host-ssh-log/`, `retrieved/cases/<case>/{receipts,logs,env.json}`,
`retrieved/list/`, `receipts/`).

## Boundaries and non-claims

- No stage, commit or push; the index was never written to.
- No test assertion, skip, timeout, mock body or inventory entry was changed;
  no dependency, lockfile or runtime version was changed; no third-party package
  was patched, forked or replaced; no product source under `src/`, `backend/` or
  `plugins/*/src` was changed.
- The adaptations are evaluated on macOS only under pinned Bun 1.4.2; they are a
  candidate-branch feasibility result, not adoption, not ticket acceptance, and
  not a Linux/Windows claim (issues 07/08).
- The residual external-workspace install failure is bounded to the pinned Bun
  version and this guest host; only the shapes above were exercised.
