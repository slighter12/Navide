# Ticket 04-01 — macOS test suite and packaged Plugin lifecycle, version 1

## Decision

**CONCRETE HOLD for whole-suite parity: the shipped macOS test suite is green under the pinned Node/pnpm baseline and is not green under the pinned Bun candidate. Two of the three retained Plugin gates match the baseline exactly, one does not.**

- Baseline (Node 22.23.2 / pnpm 10.0.0): complete root suite exit 0 — 835 files passed, 1 skipped file, 12 447 tests passed, 23 pending, 0 failed. Packaged Plugin lifecycle 124/124. Public Plugin CLI 53/53. Standalone Mini-IDE boundary 5/5. Electron 44.4.5 installed from the same verified archive in both cases.
- Candidate (Bun 1.4.2, no Node/npm/npx/pnpm on `PATH`): the same three Plugin gates are green — packaged lifecycle 124/124 (real PyInstaller and Go native children executed), public Plugin CLI 53/53, third-party macOS-sandbox backend 12/12, backend sandbox suite 19 passed + 7 Linux-only skips. The complete root suite is **not** green: 793 files passed, 41 of 835 files failed (328 tests), one file is skipped by default, and `TokenMonitorApp.test.ts` produced no result in any of the three runs — the same file that panics Bun. The run ended with a Bun process panic (`int cast: TryFromIntError(PosOverflow)`) after the last file summary, killing the runner before the aggregate and the JSON report.
- Failure classes under Bun, reproducible three times with an identical failed-file set: 37 of the 41 files share the Vitest module-mocking/interop mechanism (25 with direct symptoms, 12 whose assertion mismatch is consistent with it and whose files all declare `vi.mock`/`vi.hoisted`) — the test's module replacement is not the instance the code under test sees: empty renders behind `v-if`, `null` mocked modules, unmet spy expectations, an Electron `app` path falling through to `/app`; 2 files fail because `vue-tsc` reports `TS2307` for `.vue` imports inside packed-consumer boundary builds; 1 file's own helper still requires a `pnpm` executable on `PATH`; 1 file simulates Windows through a Node `--import data:` preload that Bun does not apply.
- This ticket fixes nothing and recommends nothing. No source change was made, no stage, commit or push. Whether Bun adoption proceeds is ticket 09's decision and needs the non-macOS evidence of issues 07/08 as well.

## Bound authority and snapshots

- Execution authority: `coordination/ticket04-01/dispatch.md`, SHA-256 `d2b44e926875d08a5baa41225aa0bd3b6fccb2e6ab4d85d825e06bd4ad2e676c`, relative to the external evaluation base `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain`. Delivery scope per dispatch: frozen delivery only — no stage, commit or push.
- Actual worker: session `01a12674-8e47-75b9-b7bb-107e2e0a9576`, provider `deepseek`, model `deepseek-flash`, reasoning level `max`; native worker directory `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch `eval/issue-2-bun-toolchain`.
- Shared worktree HEAD: `ee3174aef8f24e94a37aca5b368f8ab0568f5f1f`. `git status --porcelain` was empty before this ticket's work and contains only this ticket's two new tracked deliverables after it; the index was never written to.
- Original supported baseline: `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`; source archive `base-f4e9fd50.tar`, SHA-256 `e3da8e5c9900c599920634fa1515cb2fdd53b5d15bd95a09225a2e08d8c723b9` (the ticket-01 frozen archive, re-verified byte-for-byte in the guest before extraction).
- Candidate input: HEAD `ee3174ae`; source archive `head-ee3174ae.tar`, SHA-256 `41332c32a14c93c6db9bbd4f0b6a001f2c95d468abf942293bed087f34cf7ec4` (produced here by `git archive HEAD`, extracted in the guest after the hash matched).
- Both trees were extracted as git archives, so neither contains a `.git` directory. The suite's Plans root resolution climbs to the nearest `.git`, so both cases carry a content-neutral local marker repository (`git init` plus one empty commit, `77c9fcbe5c49ba6540076c589b432d848762af0c`) added after the first packaged-lifecycle attempt. That attempt's failure and its raw log are retained; see "Deviations".
- Guest host: macOS 27.0.1 (`Darwin`, arm64) on the ticket-03 macOS guest, account `navide`, reached over the retained ticket-03 host-to-guest SSH harness (`host-ssh.sh`, SHA-256 `440e1cde3865d9017eb26906420d87554871f167f74a74b4963447bc4138f05a`, invoked 118 times, all logged).
- Verification environment was not the shared worktree: all runs happened in two fresh guest case directories, `t04-base-node` and `t04-head-bun`.

## Cases and cleared-environment method

Each case is a new owned directory with its own `HOME`, `TMPDIR`, XDG directories, npm/Bun/uv caches, Electron cache, backend data directory and tool prefix. Every executed command ran through `t04-run.py` (SHA-256 `1f43b8b0fb3091d423b99de3dc6eb90368c2b6e5fa30f7603dbc24c2600bca3f`), which passes only an allowlisted environment (the case `env.json` delivered with the evidence plus optional allowlisted `NAVIDE_TEST_*`/`NODE_OPTIONS` keys), a `/dev/null` stdin, a fresh process group, one combined log, and a JSON receipt that records argv, cwd, environment, exit status, wall time, log SHA-256, and the observed process tree. No ambient guest environment, credential or vendor override is inherited, and no secret was read at any point.

| Case | Runtime | Binary SHA-256 | Other pinned tools | External Node/pnpm |
|---|---|---|---|---|
| `t04-base-node` | Node v22.23.2 | `18e387c90ab8a8400183e8bdd396376e1e875b91b4c874b894dcade7b35bf572` | pnpm 10.0.0 (`6ce039a469193bba0f13345f9987e842c3d049a0fb567938d08648f541449572`), uv 0.12.23, Python 3.12.11, Go 1.27.2 | present by design (`node`, `npm`, `npx`, `pnpm`) |
| `t04-head-bun` | Bun 1.4.2 | `35d20dd0263e5c950194434b925454fdfa9ba6e4467da960410fa05b08a7a5b5` | uv 0.12.23, Python 3.12.11, Go 1.27.2 | `node`, `npm`, `npx`, `pnpm` all ABSENT under the allowlisted `PATH` |

Both cases installed Electron 44.4.5 (`node_modules/electron/dist/version`, `Electron.app` present) from the same checksum-verified zip, SHA-256 `a212eee63ba2f45fd83bd28f77a3e3313a336ad17a4c25adf617942eef5e0e2c`, so neither case downloaded Electron and neither relies on a different Electron build. Dependency routes: baseline `pnpm install --frozen-lockfile` (exit 0, 11.3 s, `.pnpm` store) and candidate `bun scripts/bun-evaluation.mjs install` → `bun install --frozen-lockfile` (exit 0, 4.4 s, `.bun` store, no `.pnpm` store). No registry override existed anywhere in the case environment (no `bunfig.toml` in the case source, case home or guest user home; no `npmrc`; `BUN_CONFIG_REGISTRY` unset), so the candidate resolved 620 packages from Bun's default registry against the frozen `bun.lock`. `pnpm-lock.yaml` hashes `dd08737924d0aea21dad5f0d37dca9f09b4ee0d75def756cea55fa246e0e986b` in **both** cases after install, and the candidate's `bun.lock` (`812fcf6587ed8801d560d35804714ec47e4db0b936f1a14905f5eeae182cdafe`) was not rewritten: no lockfile mutation in either case. Backend sync was `uv --project backend sync --locked` in both (exit 0, 145 site-packages entries each).

Go 1.27.2 was installed into both cases from `go.dev` with the publisher's own SHA-256 (`76812b213b1b2302c978d28fa52fa92d541704b9e7d9d5db8002c50e4018c4c5`); it is the packaged-Plans fixture prerequisite listed in `CONTRIBUTING.md` and is a harness dependency in both cases, not a product requirement.

## Acceptance criteria

The numbering follows the nine criteria of the saved issue 04.

| Criterion | Actual status | Evidence and limitation |
|---|---|---|
| 1. Start from the same baseline snapshot; establish baseline green first, candidate identical scope, isolation recorded | Met with one recorded deviation | A1–A7 for the baseline (complete root suite, packaged lifecycle, CLI, Mini-IDE, and the four gap files). All runs used owned case copies, cleared environments and logged SSH. Deviation: the archive lacks `.git`; see "Deviations". |
| 2. Cleared-env execution with no inherited credentials, sockets, user config or external runtime leakage | Met for the exercised commands | `env.json` per case is delivered; `t04-run.py` refuses non-allowlisted keys; no secret was ever read. External runtime identity was checked from observed process executables, not from environment variables. |
| 3. `vitest list` inventory diff by project: no test file drops out, no config silently skipping a suite, no assertion content changed | Met at the file level | Discovery is identical in both cases: 835 files (unit 808, artifacts 23, cli 4) and 12 446 listed tests per project, identical file sets (`identical_file_sets: true`). `vitest list` omits conditionally-skipped tests, so listed tests (12 446) and executed assertions (12 470 baseline) differ by the pending set; the pending set is environment-gated, not dropped (baseline pending titles are enumerated in the evidence). No test assertion or skip content differs between baseline and candidate: the only changed test-adjacent file is the build helper `tests/support/publicPackagesSetup.ts` (+11/−5). |
| 4. Root `test:run` suite green on the candidate, or a precisely accounted gap list with a named blocking exception for issue 09 | **NOT MET — precise gaps delivered** | Candidate root run exit 1: 793 files passed, 41 files failed (328 tests), 1 file skipped by default, and exactly one discovered file (`TokenMonitorApp.test.ts`) without a result — it is also the file that panics Bun when run alone. Full class list below. |
| 5. Packaged Plugin lifecycle cases with real native artifacts: match baseline or named blocker | **MET — exact match** | Both cases: 3 files, 9 suites, 124/124 tests, exit 0, including production builds, the PyInstaller and Go fixture binaries, and the real packaged children (`dist-plugins/navide-plans/backend/navide-plans`, `dist-test-fixtures/plans/backend/navide-plans`, `.../navide-plans-go` all observed executing). The candidate executed them under Bun only (18 observed `bun` processes, no external Node). |
| 6. Retained public Plugin CLI validation, canonical archive/signature and negative cases | **MET** | `--project cli` 4 files / 53 tests passed on both cases, exit 0. |
| 7. Git/Plans boundary accounting; Mini-IDE as needed | **PARTIAL** | Mini-IDE standalone (`--config plugins/navide-mini-ide/vitest.config.ts`): baseline 3 files / 5 tests pass; candidate 1 failed file (packed-consumer `vue-tsc` `TS2307` on `./MiniIdeApp.vue`) / 4 passed. Plans package boundary passes under Bun; Git composition boundary fails on the same `vue-tsc` mechanism. |
| 8. macOS plugin-sandbox escape and third-party lifecycle cases | **MET** | `pluginBackendSandbox.test.ts` 19 passed + 7 Linux-only skips and `pluginBackendHost.thirdParty.test.ts` 12/12 passed — identical in both cases; no sandbox weakening was introduced or needed. |
| 9. Install-route provenance, snapshot and lock route, source of the Bun module | **MET** | Electron 44.4.5 from the identical verified zip; `bun.lock` present and unmodified after install (`--frozen-lockfile`); `pnpm-lock.yaml` unchanged; Bun binary hash, uv, Python, Go and tool identities recorded per case; failure classes reproduced across three runs. |

## Inventory and execution comparison

| Project | Discovered (list) | Baseline executed | Candidate executed |
|---|---|---|---|
| unit | 808 files / 12 190 tests | 12 198 assertions, 7 pending, 0 failed | 807 files reported: 770 passed, 37 failed |
| artifacts | 23 files / 203 tests | 214 assertions, 11 pending, 0 failed | 22 of 23 discovered files reported: 18 passed, 4 failed; `plansPackagedRoundtrip` (outside discovery, all tests conditionally skipped) reports `↓` |
| cli | 4 files / 53 tests | 53 passed | 53 passed |
| total root suite | 835 files / 12 446 listed tests | 836 report lines, 2 931 suites, 12 470 assertions, 12 447 passed, 23 pending, exit 0 | 834 of 835 discovered files reported, 793 passed files, 41 failed files, 328 failed tests, 1 default-skipped file, `TokenMonitorApp.test.ts` without a result, exit 1 |

Supplementary runs (each executed in both cases under the same command shape):

| Gate | Baseline | Candidate |
|---|---|---|
| Packaged Plans lifecycle (`NAVIDE_TEST_PACKAGED_PLANS=1`, `NAVIDE_TEST_PRODUCTION_PLANS_BACKEND=1`, `NAVIDE_TEST_ARTIFACTS_PREBUILT=1`) | 124/124 passed, exit 0 | 124/124 passed, exit 0 |
| Public Plugin CLI (`--project cli`) | 53/53 passed, exit 0 | 53/53 passed, exit 0 |
| Mini-IDE standalone config | 5/5 passed, exit 0 | 4 passed / 1 failed, exit 1 |
| `packages/plugin-sdk/bin/dev-backend.test.ts` | 7 passed + 1 skipped, exit 0 | 7 passed + 1 skipped, exit 0 |
| `pluginBackendHost.thirdParty.test.ts` (live macOS Seatbelt) | 12/12 passed, exit 0 | 12/12 passed, exit 0 |
| `pluginBackendSandbox.test.ts` | 19 passed + 7 skipped, exit 0 | 19 passed + 7 skipped, exit 0 |
| `TokenMonitorApp.test.ts` | 9/9 passed, exit 0 | Bun panic (single-file run, 134.8 s), no result |

The five files above that were also executed individually outside the root suite (`dev-backend`, `pluginBackendHost.thirdParty`, `pluginBackendSandbox`, `pluginBackendHost`, `pluginBackendSupervisor`) reported the same outcomes in the candidate root run as in their individual runs; the individual runs exist because the first analysis pass wrongly believed they had no result (see Deviation 3).

## Candidate failure inventory

The table below is the complete account. The failed-file set and per-file failed-test counts are byte-identical across the three candidate root runs (`test-run`, `test-run-r2`, `test-run-r3`).

| Class | Files | Evidence |
|---|---|---|
| Vitest module mocking under Bun — direct symptoms | 25 | `vi.mock`/`vi.hoisted` module replacement is not the instance the code under test sees. Symptoms: 100 messages "Cannot call … on an empty DOMWrapper" / "Unable to get … within: `<!--v-if-->`"; 14 × "Cannot destructure property 'send' from null or undefined value"; `[Function …] is not a spy or a call to a spy`; `[vitest] No "settingsGet" export is defined on the "@navide/plugin-ui/shared" mock`; `EROFS: read-only file system, mkdir '/app'` (Electron `app` path mock not applied); unmet `expected "spy" to be called` expectations, including the `killProcessTree` module mock in `src/main/backend.test.ts` and `src/main/backend-pending.test.ts` (those two import the real `./process-tree` module rather than the hoisted spy, so the tree-kill expectation is never observed). |
| Vitest module mocking under Bun — same mechanism, not traced file-by-file | 12 | Assertion mismatches consistent with a missing mock and unexplained by any product change, e.g. `expected 'modelcli --resume abc' to be '… --model opus-9'` (its mocked agent registry is demonstrably not consulted), `Plugin backend runtime is unavailable.`, `Plugin capability runtime is unavailable.`, `expected 'error' to be 'recording'`, `expected [] to include 'navide.plans'`, `expected '閒置' to be '待命'`. Every one of these files declares `vi.mock`/`vi.hoisted` (1–16 calls each); the mechanism was traced in samples (`resume-command.model.test.ts`, `UsageBadge.test.ts`, `backend.test.ts`), not in all twelve. |
| `vue-tsc` under Bun | 2 | `plugins/navide-git/tests/compositionBoundary.test.ts` and `plugins/navide-mini-ide/tests/packageBoundary.test.ts`: the packed-consumer typecheck fails with `TS2307: Cannot find module './MiniIdeApp.vue' or its corresponding type declarations` / consumer `tsconfig.json` failure. This is the known Volar/CommonJS `fs` interception gap; the repository's own `typecheck:*` scripts avoid it by routing through `scripts/bun/vue-tsc.cjs`, the boundary tests do not. |
| Test helper requires `pnpm` on `PATH` | 1 | `src/main/plugins/pluginExternalWorkspace.test.ts`: `Executable not found in $PATH: "pnpm"`. This file carries its own `packageManager()` helper, which returns `pnpm` when neither `NAVIDE_PNPM` nor `npm_execpath` is set; the ticket-02 adaptation covered `tests/support/publicPackagesSetup.ts` but not this file. It is a harness gap, not a product assertion about Bun. |
| Node `--import` preload not applied by Bun | 1 | `tests/integration/plansBuildScripts.test.ts`: two of eleven tests simulate a Windows build by spawning `process.execPath --import 'data:text/javascript,…' scripts/verify-plans-production.mjs`. Bun ignores that preload, `process.platform` stays `darwin`, and both assertions invert. Isolated probe (`receipts/win32-preload-probe.txt`, SHA-256 `8524346ba3804f68d990271322918b6e24bd1d24b7a33ec976b84c86b2faa7cb`): candidate exit 1 with the darwin message and byte-identical output without `--import`; baseline exit 0 with `--import` (win32 branch selected). |
| Bun process panic | 1 file, run-level | `int cast: TryFromIntError(PosOverflow)` in `bun`, after which `tinypool` raised `Subprocess.send() cannot be used after the process has exited.` and `scripts/bun-evaluation.mjs` reported exit 1. Crash-header memory across the three runs: r1 RSS 3.16 GB / peak 6.43 GB, r2 3.63 GB / 6.75 GB, r3 3.52 GB / 7.34 GB, on a 12.88 GB machine. The panic is reproducible on a single file: `TokenMonitorApp.test.ts` alone panics Bun in 134.8 s, while the same file passes 9/9 under Node. |

Diagnostic probes that separate runtime from scheduling: `--no-file-parallelism` reproduced the same class (2 files, 70 failed) and `--pool=forks` reproduced it for `aiTerminalResources.test.ts` (2 failed). The failures are therefore not an artifact of Bun's worker pool or file parallelism.

Process-identity control: every observed JavaScript runtime in the candidate receipts is the case Bun binary (`/Users/navide/eval/t04-head-bun/tools/bun/bin/bun`, 1–25 processes per run) plus case-owned `esbuild`, `uv`, Python, Go toolchain and PyInstaller children; no external `node`, `npm`, `npx` or `pnpm` executable was observed in any candidate run, and `remaining_observed_owned` is empty in every receipt (no observed orphaned case process). Observation is sampling-based and does not cover processes that exited between samples, which the receipts state explicitly.

## Deviations, exactly as they occurred

1. **Missing `.git` in the archive (environment deviation, corrected).** The first baseline packaged-lifecycle run (label `packaged-lifecycle`) failed 1 of 124 tests: `plans.resolve_root` returned `/…/t04-base-node/source/src` where the test expects the workspace root, because `resolvePlansRootPath` ascends to the nearest `.git` and the extracted git archive has none. That failure, its log and its receipt are retained. Correction applied to **both** cases: a local marker repository (`git init` + one empty commit) with no content change. The gate then ran green on both (`packaged-lifecycle-r2` 124/124 baseline; candidate 124/124). Consequence recorded rather than normalised: the baseline root suite differs between the two environments — pre-marker 12 446 passed / 24 pending, post-marker 12 447 passed / 23 pending — one git-dependent test executes only with the marker present. Both receipts are delivered.
2. **Shell quoting mistakes in the harness (no product effect).** Two guest commands failed before doing anything: a `for spec in …; set -- $spec` loop expanded inside a single-quoted remote command (`zsh:5: 2: parameter not set`, exit 1, no case created), and a `R="python3 …"; $R …` invocation relied on word splitting that zsh does not perform (`no such file or directory`). Both were re-issued with literal arguments or `setopt shwordsplit`; no case state was affected and no result was carried over from the failed forms.
3. **Analysis-parser undercount (self-correction, recorded).** The first analysis pass parsed Vitest summary lines with a pattern that did not match `(N tests | M skipped)` lines, so it reported 829 report lines and six files without a result for the candidate root run. All three runs in fact carry 835 report lines (793 passed, 41 failed, 1 default-skipped). The corrected parser (`scripts/parse-vitest-log.py`, SHA-256 `5d103d23f7b0d14e3977173e87ece0280bde216ba8d6c0bbc38a391e40af5148`) accounts for every file: **one** discovered file has no result in any run — `TokenMonitorApp.test.ts`. The superseded analysis output was overwritten in place; the raw logs it was derived from are unchanged, and the defect, its correction and the corrected outputs (`analysis/gap-accounting.txt`, `analysis/head-test-run*.json`, `analysis/base-test-run-r2.json`) are recorded here.
4. **Three-file filter returned no files (harness artifact).** One invocation passing three test paths as filters reported "No test files found" **on both cases** — a filter-semantics artifact, not a missing file. Every file involved was executed individually as well, and both the root-run and individual results are reported.
5. **`TokenMonitorApp.test.ts` has no result under Bun.** It produced no report line in any of the three candidate root runs and panics Bun (134.8 s) when executed alone; it is reported as a file without a result, not as passing or failing. No retry, timeout increase or suppression was attempted.

## Source-change surface of the evaluated branch

`git diff f4e9fd50..ee3174ae` over the evaluated branch touches: `bun.lock` (new), `package.json` (scripts, `workspaces`, `trustedDependencies`, `overrides`, `engines.bun`, `eval:bun`, `vue-tsc` routed through `scripts/bun/vue-tsc.cjs`), `scripts/bun-evaluation.mjs` (new), `scripts/bun/vue-tsc.cjs` (new), `tests/support/publicPackagesSetup.ts` (+11/−5: Bun branch for the package-manager route, Bun keys in the artifact digest, Bun lockfile in the digest inputs), `vitest.config.ts` (+6: private `node` alias), and the evaluation documents. No test file's assertions, expectation lists or skip conditions were changed; no third-party package was forked, patched or replaced; no product source under `src/`, `backend/` or `plugins/` changed. This ticket itself changed nothing in the repository: the two files of this delivery are the only working-tree additions, and `git diff` against HEAD is empty.

## Uncovered surfaces and non-claims

- Linux and Windows are not covered; issue 07/08 remain the boundary. macOS-only results support a local feasibility judgement only.
- No real application startup or development probe was run in this ticket, so "the app runs under Bun" is not claimed here (ticket 03 did not establish it either). The packaged lifecycle suites spawn real backend children, not the Electron app.
- Release packaging, signing, notarization and hosted CI were not exercised.
- The Electron install script ran against the pre-placed, checksum-verified cache in both cases; a network-downloaded Electron install was not exercised.
- No plugin was installed from the public registry; the third-party workspace install path is in fact still `pnpm`-dependent (class 3 above).
- Test-performance measurements (issue 06) and the external public-consumer matrix (issue 05) are out of scope; only the repository's own boundary tests ran.
- The candidate failure inventory is bounded to this guest host (macOS 27.0.1 arm64) and this pinned Bun 1.4.2; reproducibility is demonstrated three times on this host only.
- Root causes beyond the observed classes are not established: for the 37 mocking/interop files the evidence shows the mock is not the instance the code under test sees, but not why Bun/Vitest interop fails; for the panic only the triggered sequence and the reported integer-cast overflow are known.

## Frozen delivery and anchor

Frozen delivery `freeze-v1` binds, by SHA-256 and byte count, this report, `evidence.json`, every harness and analysis file under the external evidence root, both source archives, both retrieved case-evidence archives, and the per-file inventory of the retrieved guest evidence. The anchor is written outside the repository:

```
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-01/freeze/freeze-v1.json
/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-01/freeze/freeze-v1.sha256
```

The anchor covers this document, so the anchor's own SHA-256 is recorded beside it in `freeze-v1.sha256` and in the completion reply rather than inside this file. Raw evidence root: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-04-01` (scripts, analysis, host SSH log, retrieved case logs and receipts, `retrieved/cases/<case>/{receipts,logs,env.json}`).

## Boundaries

No stage, commit, push, hosted action, PR, worktree or branch change. No frozen input, prior ticket artifact or shared worktree file was modified. No third-party package was patched, no test assertion relaxed, no timeout raised, no failing case skipped, and no sandbox protection weakened. Every claim above is tied to a delivered receipt or log; anything not exercised is listed as uncovered rather than assumed.
