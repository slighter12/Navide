# CI refresh 01: Linux and Windows at the rebased head

Worker: anthropic/claude-sonnet-5-5 high. Branch `eval/issue-2-bun-toolchain` HEAD `b6caf8c8262a66bfb5a470e55403359c0450fd81` (unchanged, nothing committed or staged). Upstream main `9af042ec3683b21a67da68b2c9e9352b0e612dbf`.
Raw root: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ci-refresh-01/`. Machine-readable companion: `evidence.json`.

**Revision 3 (correction 01, wording only).** Revision 1 is preserved byte-for-byte in `rev1-raw/` (hashes in `rev1-raw-hashes.txt`) and revision 2 in `rev2-raw/` (hashes in `rev2-raw-hashes.txt`). Revision 2 added one diagnostic-only attempt (`eval-bun-ci-08-10`, decision 01) for the Windows `pluginBackendHost` item. Revision 3 follows a reviewer HOLD and is documentation only: no new CI attempt, no rerun, workflows unchanged. It (1) restates the `pluginBackendHost` finding as a Bun-specific stack symptom whose root mechanism is not established, (2) corrects two over-stated sentences, (3) states the deviations as deviations, (4) states plainly that neither measurement run passed its correctness gate, and (5) records the user decision below. Correction dispatch: `coordination/ci-refresh-01/correction-01-dispatch.md`, SHA-256 `09d1232a8bcb45b458d9bb88b41e0a52ff6234c1bf6f2a96a8e257896a1b4f10`.

**User decision A (2026-10-13)**, `coordination/ci-refresh-01/user-decision-pluginBackendHost-A.md`, SHA-256 `783633606d672d55d02ddfefcc5cbdec811dda0442d1607817c6e56310a07257`: the Windows Bun `pluginBackendHost` failure is accepted as a documented known Bun-on-Windows defect (about 7% per whole-file run under Bun on Windows, 0 under Node, diagnostic run 38412461745); no product change (a robustness change at `pluginBackendSupervisor.ts:1685` is recorded only as a possible future product change); carried into ticket 09 as adoption cost; filing an upstream Bun issue is deferred until the whole evaluation is finished and no issue is filed now.

Tickets 07 (Linux, `eval-bun-ci-07-09`, run 38131404079) and 08 (Windows, `eval-bun-ci-08-08`, run 38177189208) were accepted against the old base `f4e9fd50`. This refresh re-measures both at the rebased head. Old evidence stands wherever it is not re-measured.

## Verdict summary

| Platform | Attempt | Run | Conclusion | Bun-only result |
|---|---|---|---|---|
| Linux ubuntu-24.04 | `eval-bun-ci-07-11` (commit `9faf4fe9`) | 38326714242 | failure (same shape as accepted 07-09) | Same three Bun-only failures as the accepted run, nothing new |
| Windows windows-2025 | `eval-bun-ci-08-09` (commit `8da4b51`) | 38326727409 | failure (accepted 08-08 was success) | One new intermittent Bun-only failure (`pluginBackendHost`), a Bun-specific stack symptom whose root mechanism is not established; accepted by user decision A as a documented known Bun-on-Windows defect and carried to ticket 09; plus one Node-side timeout |
| Windows windows-2025 (DIAGNOSTIC ONLY, not a measurement) | `eval-bun-ci-08-10` (commit `a4fcc263`) | 38412461745 | success (the diagnostic job executed; this is NOT a passing Windows evaluation: it ran no suite for a verdict and its own results contain 47 Bun failures in 620 runs) | see "Decision 01 diagnosis" |

Both measurement runs concluded `failure`; see "Correctness-gate status" directly below. The other jobs (readiness, consumers, every performance job) succeeded on both platforms.

Linux: no new Bun-only failure relative to the accepted 07-09 run. Windows: one new candidate-only failure appeared in 2 of 16 Bun runs of `src/main/plugins/pluginBackendHost.test.ts` in 08-09; the decision-01 diagnostic observed it at about 7% per whole-file Bun run and 0% per Node run. It is a Bun-specific stack symptom (the stack string lacks the message header); its root mechanism was not established. No adaptation was applied.

### Correctness-gate status (stated plainly)

**Neither measurement run passed its correctness gate.**

- **Linux run 38326714242 (`eval-bun-ci-07-11`):** the run concluded `failure`; the job that did not succeed is `Correctness, builds and inventories (ubuntu-24.04)`, and its recorded required failures are `root-bun:bun:exit=1`. The Bun full root run exited 1 with three failing tests: the accepted residual `pluginExternalWorkspace` (1) and two known `pluginIpc` tests (the Linux-only trust-decision-order finding already recorded at 07-09). The previously accepted 07-09 run recorded the identical required failure, so it also did not pass its gate: Linux is in the same state as the accepted run, not better.
- **Windows run 38326727409 (`eval-bun-ci-08-09`):** the run concluded `failure`; the job that did not succeed is `Correctness, builds and inventories (windows-2025)`, and its recorded required failures are `root-prebuild-bun:bun:exit=1` (residual plus the new `pluginBackendHost` failure) and `root-base:base:exit=1` (a Node-side timeout: `windowControlsStacking` "is actually scanning something", 7,273 ms against the 5 s default). The accepted 08-08 run had no required failures and concluded `success`, so Windows is worse than the accepted run in conclusion.
- **Why the evaluation still compares:** (1) each run records every failing test per runtime over an identical test inventory (Linux 936 files / 13,703 ids; Windows 936 files / 13,702 ids; 0 only-Node and 0 only-Bun), so Node and Bun are compared on the same tests and per-test outcomes, which an exit-code gate does not replace; (2) every failing test is classified from its own report (accepted residual, previously recorded finding, new item, or Node-side flake) and none is hidden or released (the only release logic, the typecheck heap supplemental, was not triggered); (3) typecheck, frozen installs, readiness, consumers and every performance job completed and are recorded on both platforms. The comparison is therefore a comparison of recorded outcomes that include known failures. These two runs do not establish a gate-clean result on either platform, and nothing in this report claims one.

## Attempts (every one, as it happened)

| Tag | Attempt commit | Parent | Run | Conclusion | Runner image |
|---|---|---|---|---|---|
| `eval-bun-ci-07-11` | `9faf4fe95140d1ba4fac405f6843413762ebdc7e` | `b6caf8c8` | 38326714242 | failure | ubuntu-24.04 image version 20261002.596 (runner 20261004.327.1) |
| `eval-bun-ci-08-09` | `8da4b51706c960544abc1d776affa4fde44c93f1` | `b6caf8c8` | 38326727409 | failure | windows-2025-vs2026 image version 20260901.588 (runner 20260925.250.1) |

Each attempt commit differs from the branch head in exactly two paths: its own workflow (modified) and the other platform's evaluation workflow (deleted, so the Linux workflow never starts on an `08` tag). The run list shows exactly one run per tag, from its own workflow. Both attempt commits were built with a temporary index, identity `slighter12 <plokm.slighter@gmail.com>`, only the tag was pushed, and the branch did not move. The workflow blob in each attempt commit is byte-identical to the delivered workflow file (verified by `git hash-object`).
No `gh run rerun`, no cancellation, no tag deletion. Two measurement attempts were needed, plus the one diagnostic-only attempt of decision 01 (`eval-bun-ci-08-10`, commit `a4fcc263eb78ad7b8cba9d07e1376bc498394975`, parent `b6caf8c8`, run 38412461745, runner image windows-2025-vs2026 20260901.588, started 2026-10-13T14:20:11Z, completed 2026-10-13T15:14:14Z). It is labelled diagnostic in the workflow name, the job name, and every record; it runs no full suite and no performance job; its attempt commit differs from the head in exactly the same two paths as every other Windows attempt.

## Workflow changes (structural diff)

Full diffs excluding the embedded base64 harness: `analysis/workflow-structural-diff-linux.txt`, `analysis/workflow-structural-diff-windows.txt`. Edit log per harness file: `authoring/harness-edits.json`.

- Baseline / Node control = upstream main `9af042ec` plus the seven runtime-neutral overlay files taken from the branch head: `plugins/navide-git/tests/compositionBoundary.test.ts`, `plugins/navide-mini-ide/tests/packageBoundary.test.ts`, `scripts/bun/vue-tsc.cjs`, `tests/integration/plansBuildScripts.test.ts`, `tests/support/bunMockImporterAnchor.ts`, `tests/support/publicPackagesSetup.ts`, `vitest.config.ts`. The path set and the head blob hashes are cross-checked against `rebase-01-test-04/receipts/overlay-manifest.txt` (the dispatch said eight files; the manifest and the test-04 tarball diff contain seven). Candidate = the attempt commit's tree.
- Source expectations regenerated from git objects (`authoring/build_expected_v2.py`); nothing typed.
- Linux harness: candidate is `T07_HEAD_SHA` (= `GITHUB_SHA`, set in all four jobs), both evaluation workflow files removed from the candidate, tree required to equal the head modulo those files; records the expected-head tree digest and parent. `publicPackagesSetup.ts` and `scripts/bun/vue-tsc.cjs` moved out of the candidate-only overlay checks, because they are now overlay files in both trees.
- Windows harness: the generation-2 `scripts/bun-evaluation.mjs` overlay is removed (the head already carries that script blob), a generation-3 source expectation is added, and the same "tree equals head modulo workflows" check is added.
- Both: a labelled Node typecheck heap supplemental (`NODE_OPTIONS=--max-old-space-size=4096`) triggered only if the plain Node typecheck dies with the V8 heap signature. It was not triggered: plain Node typecheck exited 0 on both runners.
- Not changed: gates, timing method, sandbox handling, artifact upload, jobs, triggers, permissions (`contents: read`), no `secrets.*`.
- Upstream harness dependency check: a static scan of source-file paths named in both harnesses against both revisions found only (a) runtime-created or tool-internal paths absent at both revisions (agent hook/config files, installed-tool entry points, `node_modules` cache), (b) harness-owned consumer scripts, and (c) two Bun-only files absent at the upstream base by design (`scripts/bun-evaluation.mjs`, candidate only; `tests/support/bunMockImporterAnchor.ts`, an overlay file). No harness adaptation to upstream changes was found necessary beyond the edits listed here; upstream's Windows CI sharding (#186) was not examined and nothing was adapted for it.
- Authoring check: the unchanged generators reproduce the committed workflows byte-for-byte (Linux; Windows with `T08_PAIRS=5`), which is how the regenerated tarballs and manifests were produced.

## Public package dependency graph (consumer and packaging criteria)

Between the accepted head `8c674ca6` and `b6caf8c8` the three public packages' `package.json` files are byte-identical and their `bun.lock` workspace entries are identical for `plugin-contracts` and `plugin-sdk`. For `plugin-ui` the workspace entry is identical too; only the lock resolution of its dev dependency `vue` moved (`3.5.34` to `3.5.43`). `vue` is a peer dependency (`^3.5.13`) of `plugin-ui` and a dev dependency, so the published dependency graph and peer ranges are unchanged (`logs/lock-public-graph.json`). Consumer and packaging evidence was nevertheless re-measured at the new head (the consumers job runs on every attempt), so nothing here relies on old evidence.

## Linux matrix (`eval-bun-ci-07-11`)

| Criterion | Result | Compared to accepted 07-09 |
|---|---|---|
| Source binding | PASS: baseline `9af042ec` + overlay (3,156 files, 0 mismatches); candidate tree equals head modulo workflows (3,229 files incl. attempt, 0 mismatches) | new base |
| Exact-version guard (Bun 1.4.1 rejected on check/install/run, 1.4.2 accepted) | PASS | same |
| Frozen installs (Node `pnpm install --frozen-lockfile`, Bun frozen) | PASS (all prep steps exit 0, lockfiles unchanged on both sides) | same |
| Typecheck | PASS: Node plain exit 0 (no heap OOM; supplemental not needed), Bun exit 0 | same |
| Full root Vitest parity | PASS: 936 files each side, 13,703 unique test ids each side, 0 Node-only, 0 Bun-only | was 836 files / 12,461 ids |
| Root failures, Node | 0 failed (13,712 tests: 13,673 passed, 39 pending) | 0 |
| Root failures, Bun | 3 failed (13,670 passed, 39 pending): see classification | 3 (same tests) |
| Supplementary (minimal SDK/DOM, mini-ide, dev-backend, packaged lifecycle) | PASS and equal per side; packaged lifecycle 136/136 passed on both | equal; packaged lifecycle 124 then, 136 now |
| `dockQuitProbe.test.ts` (new upstream file) | PASS on both runtimes (13/13) | file not present in old base |
| Bun-only proof | PASS: no `node`, `npm`, `npx` or `pnpm` executable outside the candidate case directory was observed; the only `node`-named executable observed (1,210 processes) is the candidate-owned alias `.../t07-head-bun/tmp/bun-node-<hash>/node` | same pattern |
| Isolated dev readiness (backend health, authenticated WS, plugin MCP) | PASS both sides: health 200 after 114.5 s (Node) / 94.5 s (Bun); ws and mcp probes exit 0; no leftover owned processes; no sandbox flag added | was 130.9 / 112.7 s |
| Sandbox | PASS: `NAVIDE_EXPECT_BWRAP` restricted and permitted both recorded; Chromium never launched with a weakened sandbox | same |
| Consumers (npm and pnpm, base and Bun artifacts) | PASS: 19 CLI cases each, 0 failed; 49 probes each with 1 failed probe on every case including the Node baseline (unchanged) | same |
| C3 anchor regex (paths with spaces/parentheses) | UNCHANGED: plain path 9/9 pass; space, parentheses and both-shapes paths: 9/9 fail on Bun, 0 fail on Node, identical failure text to 07-09 | same |
| Peak RSS | 8.10 GiB (`base:c3-anchor-regex-probe`) | 6.79 GiB |

### Linux root failures (candidate only)

1. `src/main/plugins/pluginExternalWorkspace.test.ts` (1 test): `Executable not found in $PATH: "pnpm"`. Class: accepted residual (CB-E10 layer 1).
2. `src/main/plugins/pluginIpc.test.ts` (2 tests): `plugins:prepareInstall wire → verifier mapping skips an expired HTTP 200 candidate and accepts the next valid candidate` and `… continues trust metadata refresh when the first installed package detail is missing`. Class: the Linux-only trust-decision-order finding already recorded in ticket 07 (decision list follows `readdirSync` order). Same two tests, same ordering diff in the console log (`acme.demo` and `beta.other` swapped). The Vitest JSON carries a bare `Error` instead of the assertion text for the first test at this head (the console log shows the same assertion diff), a reporting difference only.

No third class appeared. Node baseline 0 failures.

## Windows matrix (`eval-bun-ci-08-09`)

| Criterion | Result | Compared to accepted 08-08 |
|---|---|---|
| Source binding | PASS: baseline 3,156 files / candidate 3,229 files, 0 mismatches | new base |
| Exact-version guard | PASS (1.4.1 rejected x3 with guidance, 1.4.2 accepted) | same |
| Frozen installs | PASS (all prep steps 0, lockfiles unchanged) | same |
| Typecheck | PASS: Node plain exit 0, Bun exit 0 (supplemental not needed) | same |
| Full root Vitest parity | PASS: 936 files each side; 13,702 unique ids each side, 0 only-Node, 0 only-Bun (both the `root` and `root-prebuild` runs) | was 836 files / 12,460 ids |
| Root failures, Node | `root-base`: 1 (`windowControlsStacking.test.ts` "is actually scanning something", test timeout marker, 7,273 ms); `root-prebuild-base`: 0 | 0 and 0 |
| Root failures, Bun | `root-bun`: 1 (accepted residual only); `root-prebuild-bun`: 2 (residual + `pluginBackendHost.test.ts`) | 1 and 1 (residual only) |
| Supplementary (SDK/DOM, mini-ide, dev-backend, Windows sandbox unit) | PASS and equal per side | equal |
| Packaged lifecycle | Node 1 failed, Bun 2 failed of 136 (12 pending): Plans packaged roundtrip fails on both runtimes (as at 08-08); Bun additionally `pluginBackendHost.test.ts` | Node 1, Bun 1 |
| Bun-only proof | PASS: only node-named executable is the candidate-owned alias `C:\t08-eval\t08-head-bun\tmp\bun-node-<hash>\node.exe` (1,210 processes); no external node/npm/pnpm | same pattern |
| Isolated dev readiness | PASS: health 200 after 172.6 s (Node) / 115.6 s (Bun); ws and mcp probes exit 0; no leftovers; no sandbox flag; runner home unchanged | was 141.0 / 104.9 s |
| Consumers | PASS: 19 CLI cases, 0 failed, 49 probes with the same 1 failed probe on every case | same |
| C2 `node` alias (symlink, PATHEXT) | UNCHANGED: the `node-alias` probe records equal 08-08 except the random temp directory name | same |
| C3 helper regex with Windows paths | UNCHANGED: all four path shapes pass on both runtimes | same |
| `readdir` order on NTFS (`pluginIpc`) | not reproduced: no `pluginIpc` failure on Windows | same |
| Native dependencies / shell quoting / paths | UNCHANGED apart from version bumps (`electron` 44.4.5 to 44.6.0, `koffi` 3.3.1 to 3.3.2) | same |
| Peak working set | 4,004 MiB (`bun:mini-ide-bun`) | 4,278 MiB |
| Production Plans backend prep | blocked on both runtimes (exit 1), recorded as before | same |

### Windows root failures (candidate only) and classification

1. `pluginExternalWorkspace.test.ts` (1 test): `Executable not found in $PATH: "pnpm"`. Class: accepted residual (CB-E10). `root-bun` is residual-only.
2. `pluginBackendHost.test.ts` > `PluginBackendHost forwards host-only diagnostics and original cause without leaking OS paths to caller`: fails in `root-prebuild-bun` and `packaged-lifecycle-bun`; passes in the other 14 Bun runs of that file (`root-bun` and all performance samples), and passes in all 16 Node runs. Class: new at this head (08-08 at the old base had no such failure), intermittent, Bun-specific stack symptom, root mechanism not established; see "Decision 01 diagnosis" and user decision A.
3. Node-side items, not Bun-related, classified as host-load flakes of the baseline: `windowControlsStacking.test.ts` "is actually scanning something" failed once in `root-base` (7,273 ms against Vitest's default 5 s test timeout; passes in 14 of the 15 Node runs and in 15 of 15 Bun runs of that test at this head, and in 15 of 15 and 15 of 15 at 08-08), and two Node root-cold samples (02 and 03) exited 1 with all tests passed because Vitest reported one unhandled `[vitest-worker]: Timeout calling "onTaskUpdate"` error (`analysis/perf-anomalies-v2-windows-08-09.json`). The Windows Node root run is therefore not clean in `root-base`, which is why `required_failures` lists `root-base:base:exit=1`; `root-prebuild-base` is clean (13,602 passed, 0 failed) and supplies the zero-failure Node baseline.

## Decision 01 diagnosis: `pluginBackendHost.test.ts` on Windows (diagnostic attempt `eval-bun-ci-08-10`)

Coordinator decision 01 chose bounded diagnosis from runtime receipts (one diagnostic-only attempt). Nothing in the repository, the product source or the test files was changed: the instrumentation is a case-local Vitest setup file plus a case-local config copy, written into each case's extracted source directory (`vitest.diag-observe.config.ts`, `diag-setup-observe.ts`, `diag-setup-early.ts` and the early-mode config), disclosed here. Plain (uninstrumented) runs use the project's own config unchanged. The harness is the 08-09 harness plus `t08_diag.py` and `stack_repro.mjs`; workflow `generated/diag-windows-v2.yml` (SHA-256 in `logs/diag-workflow-hashes.txt`). The attempt's `success` conclusion means only that the diagnostic job executed; it is not a passing Windows evaluation.

**Finding, stated at the strength the evidence supports:** under Bun 1.4.2 on Windows, `Error.stack` of an error whose `message` is set is sometimes a bare `Error` line plus frames, without the message. This is a Bun-specific stack symptom: Bun failed 47 of 620 runs and Node 0 of 130; the instrumented Bun failures (26 of 26) show a populated `message` with a bare `Error` header on the very object the spawn stub threw; and a standalone Bun repro (no Vitest) produced the same bare header once in 6,000 worker-thread stacks (0 in 12,000 Node stacks). The root mechanism (which Bun component builds the header, why it is omitted, and why it is rate-amplified inside Vitest) is **not established**. User decision A records it as a documented known Bun-on-Windows defect.

### What failed, exactly (receipts: 47 failing Bun runs of the diagnostic attempt)

- In all 47 failing runs the target test `forwards host-only diagnostics and original cause without leaking OS paths to caller` was the only failing test, with the same failure: `onStderr` received a text that begins with the bare line `Error` followed by frames, no `Error: spawn /secret/path/plans ENOENT` header. All 47 show the same first frame position `pluginBackendHost.test.ts:167:33` followed by the Vitest runner frames. (That position does not correspond to the line of `new Error('spawn …')` in the original source, which is line 196; what the position denotes was not determined.)
- Instrumented failures (26 of 26 bare: 9 in I1 and 17 in I2; the counts are the runner-side tallies, the per-run rows were lost, see "What dev-12 left unverified"): the object passed to `emitDiagnostic` was the same object the spawn stub threw (`sameAsThrown=true`), its `message` property was the non-empty `spawn /secret/path/plans ENOENT`, but its `stack` string began with the bare line `Error`. In the 174 instrumented passing Bun runs (91 in I1, 83 in I2) and all 30 instrumented Node runs the stack began with the full `Error: spawn /secret/path/plans ENOENT` header. The product code forwards `cause.stack ?? cause.message` as written, so the test observes whatever `stack` holds.

### Rates

| Condition (file alone unless stated, project `artifacts`) | Bun failed/runs (rate, Wilson 95%) | Node failed/runs |
|---|---|---|
| B0 no prebuilt switch (root-bun / performance-sample condition) | 4/100 (4%), 1.6%–9.8% | 0/30 |
| B1 `NAVIDE_TEST_ARTIFACTS_PREBUILT=1` (root-prebuild condition) | 8/100 (8%), 4.1%–15.0% | 0/30 |
| B2 packaged-lifecycle environment | 9/100 (9%), 4.8%–16.2% | 0/30 |
| B3 = B1 with `-t` selecting only the target test | 0/100 (0%), 0.0%–3.7% | not run |
| B5 the three packaged-lifecycle files together, packaged environment (target test outcome only; see note) | 0/20 (0%) | 0/10 |
| I1 = B1 plus the observing setup file | 9/100 (9%), 4.8%–16.2% | 0/30 |
| I2 = I1 plus an early read of the thrown error's stack | 17/100 (17%), 10.9%–25.6% | not run |

Pooled plain whole-file Bun runs (B0, B1, B2): 21/300 (7.0%, 95% CI 4.6%–10.5%); Node 0/90 plain plus 0/30 instrumented. Bun versus Node, pooled plain, Fisher exact two-sided p = 0.0060. Totals: Bun 47 failures in 620 runs, Node 0 failures in 130 runs. Each condition used 100 Bun runs and 30 Node runs, except B5 (20 and 10). Diagnostic stage wall time 49 minutes; every planned run executed.

**B5 note:** the B5 rate counts only the outcome of the target test. Every B5 command (all 20 Bun and all 10 Node) exited 1 on both runtimes because of the already recorded Windows Plans failure (`plansPackagedRoundtrip.test.ts`), so B5 is not a clean-exit condition. B5 ran the whole host file, and 0/20 is not distinguishable from the pooled rate (probability of 0 failures in 20 runs at 7.0% is about 0.23).

What the rates do and do not show:
- The three environments do not separate (Fisher p: B0 vs B1 0.3727, B0 vs B2 0.2507, B1 vs B2 1.0). The 08-09 pattern (failures only in the two runs with the prebuilt or packaged switch, none in the other 14 Bun runs of the file) is consistent with a per-run rate near 7% (probability of 0 failures in 14 runs at that rate is about 0.36) and is not evidence of an environment dependence.
- **Association with running the whole file, cause not identified.** Single-test runs (`-t`) failed 0/100 against pooled plain whole-file 21/300 (Fisher p = 0.0032; B3 vs B1 p = 0.0068). `-t` changes the whole run (which tests execute, their order of execution and the process state they leave), so this does not show that preceding tests are required, and no earlier test, state or mechanism was identified. In the file the target is the fourth test; with `-t` the three before it are skipped.
- The early read of the stack at throw time (I2) did not lower the rate (17/100 versus I1 9/100; Fisher p = 0.14, not significant). Whether the bare header was already present at that first read is not known (dev-12); what is known is that reading the stack early did not remove the failures.
- Failing runs are scattered through the run order (adjacent failing pairs: B0 0, B1 2, B2 0, I1 1, I2 3), with no sign of a host-wide interference window.

### Standalone Bun repro (no Vitest), Windows, Bun 1.4.2 and Node 22.23.2, 1,000 iterations per variant in the main thread and in a `worker_threads` worker

Six variants (closure throw read at once; read after `setImmediate`; read after allocation and forced GC; promise-wrapped async runner; wrapped `cause`; message assigned after construction), classifying the first stack line as `full` (`Error: spawn /secret/path/plans ENOENT`), `bare` (`Error`) or other.
- Bun main thread: 6,000 stacks, 0 bare. Bun worker thread: 6,000 stacks, **1 bare** (variant `read after setImmediate`, iteration unknown), 5,999 full.
- Node 22.23.2: 12,000 stacks, 0 bare.
The bare header therefore exists in Bun 1.4.2 on Windows without Vitest, but as a single event in 6,000 worker-thread stacks, against about 7% of whole-file runs inside Vitest. The standalone loop does not reproduce the Vitest rate, and one event is not a rate estimate. This reproduces the symptom, not the mechanism.

### Classification

**Bun-specific stack symptom; root mechanism not established.** Evidence: Bun-only failure rate (47/620 against Node 0/130; pooled whole-file p = 0.0060); a populated `message` with a bare `Error` header on the same error object in 26/26 instrumented failures; the standalone worker-thread repro, 1 in 6,000 Bun stacks and 0 in 12,000 Node stacks.
Not shown to be a harness effect: the instrumented condition I1 and the plain condition B1 fail at the same rate (9/100 and 8/100, p = 1.0). Not shown to depend on the environment switch (see above). Time clustering was not seen.
Not established: which Bun component computes the header and why it omits the message; why the symptom is rarer in the standalone loop than in Vitest runs; why single-test runs did not fail (association only); whether `Error.prepareStackTrace` (installed by Vitest) was in effect at the failures.
Status by user decision A: documented known Bun-on-Windows defect, carried into ticket 09 as adoption cost; no product change; no upstream Bun issue filed.

### What dev-12 left unverified

The per-run instrumentation `*.jsonl` files were written under each case root and not included in the artifact upload; the runner is gone and the loss is unrecoverable. Only the runner-side tallies in `diag/summary.json` and the per-run row counts survive. Consequently the following were NOT verified: whether `Error.prepareStackTrace` was present or what it was at the failures; the property descriptors of `stack` and `message` (own data property versus accessor); stack line 1 and the frames of the failing stacks; whether the bare header was already present at the first read of the stack in the early-read condition; per-run timings of the records. The 26/26 statement rests on the surviving tallies (`sameAsThrown`, non-empty `messageBefore`, first stack line bare) and cannot be re-derived from per-run rows.

### Adaptation assessment (none applied)

No safe or effective in-rule adaptation was identified or tested.
- A generic Bun-only test-support helper: none was written or tested. It would have to change how `Error.stack` is formed or read for the whole test process; the I2 probe shows that reading the stack early did not prevent the failures, so there is no evidence that a small helper would help.
- A mechanical syntax-level test edit: none was identified. The failing assertion expects `onStderr` to be called with a string containing `/secret/path/plans ENOENT`; editing the assertion would change what the test asserts, which the rule does not allow.
- Product change: a possible future robustness change at `src/main/plugins/pluginBackendSupervisor.ts:1685` (build the diagnostic from `cause.message` as well as `cause.stack`) is recorded by user decision A as a possible future product change. It is not applied and not required by this evaluation.
Adoption cost (ticket 09): with Bun 1.4.2 on Windows the full-suite run has an intermittent single-test failure at about 7% per whole-file run of `pluginBackendHost.test.ts` (the full suite runs the file once per run: 2 of 16 observed at 08-09, 0 of 16 at 08-08). Linux showed 0 of 15 Bun runs of the file at this head; at that rate the probability of 0 in 15 is about 0.34, so Linux is not shown to be immune.

### Other limitations of this diagnosis

- One diagnostic attempt only (decision 01); no second attempt was started to recover the lost fields.
- All rates come from one runner (windows-2025-vs2026 image 20260901.588) in one 49-minute diagnostic stage; they are not a population estimate across runners.

## Performance (same job, interleaved, 5 samples each after warm-up, cold and warm)

Same method as 06/07/08: same runner per job, interleaved pairs, warm-up discarded, medians, Bun slowdown above 10% fails. Medians in seconds; MAD in parentheses.

| Platform | workload / condition | Node median (MAD) | Bun median (MAD) | slowdown | gate | accepted Node | accepted Bun | accepted slowdown |
|---|---|---|---|---|---|---|---|---|
| Linux | dom-cold | 2.222 (0.01) | 1.252 (0.004) | -43.7% | PASS | 2.294 | 1.304 | -43.2% |
| Linux | dom-warm | 2.209 (0.004) | 1.256 (0.006) | -43.1% | PASS | 2.299 | 1.3 | -43.5% |
| Linux | root-cold | 470.619 (0.983) | 286.668 (1.048) | -39.1% | PASS | 436.053 | 267.248 | -38.7% |
| Linux | root-warm | 413.615 (0.644) | 239.432 (0.068) | -42.1% | PASS | 372.197 | 218.178 | -41.4% |
| Linux | sdk-cold | 1.822 (0.009) | 1.116 (0.002) | -38.7% | PASS | 1.88 | 1.165 | -38.0% |
| Linux | sdk-warm | 1.841 (0.013) | 1.113 (0.006) | -39.5% | PASS | 1.886 | 1.148 | -39.1% |
| Windows | dom-cold | 3.235 (0.016) | 1.813 (0.031) | -44.0% | PASS | 3.625 | 1.938 | -46.5% |
| Windows | dom-warm | 3.141 (0.0) | 1.796 (0.001) | -42.8% | PASS | 3.594 | 1.859 | -48.3% |
| Windows | root-cold | 665.062 (24.593) | 405.422 (21.328) | -39.0% | PASS | 345.312 | 217.359 | -37.1% |
| Windows | root-warm | 582.219 (3.437) | 326.891 (3.203) | -43.9% | PASS | 489.063 | 273.235 | -44.1% |
| Windows | sdk-cold | 2.563 (0.048) | 1.563 (0.047) | -39.0% | PASS | 2.671 | 1.516 | -43.2% |
| Windows | sdk-warm | 2.719 (0.11) | 1.609 (0.125) | -40.8% | PASS | 2.703 | 1.516 | -43.9% |

Comparability notes:

- Linux root samples: every Bun sample exits 1 because of the three known candidate-only failures; every Node sample exits 0. File counts are equal (936). Same as the accepted run.
- Windows root samples: every Bun sample exits 1 on the accepted residual only (936 files). Node root-cold samples 02 and 03 exited 1 with all tests passed (unhandled Vitest worker RPC timeout); they are included in the Node median as recorded (Node cold samples 640.5, 646.8, 665.1, 727.4, 753.5 s; Bun 372.1 to 426.8 s), and no verdict depends on them (the slowest Bun sample is faster than the fastest Node sample).
- Windows absolute root medians are higher than at 08-08 (suite grew from 836 to 936 files and Windows hosted runners are noisy: Defender and indexing are not controlled). The slowdown ratio is the comparable quantity, and it is unchanged in sign and similar in size. No gate fails; no performance result is inconclusive.

## Deviations and departures from the evidence rules (stated as deviations)

None of the items below is a compliant execution of the evidence rules (exclusive-create, never modify or delete, descriptor-based reads); each is a deviation, listed as it occurred. `logs/dev-*.md` hold the notes written at the time. They were written with shell redirection (`cat >`), which is not exclusive-create; the dev-01 to dev-09 notes are byte-identical between the revision-1 freeze sweep and the revision-2 freeze sweep (43,238 files common to both sweeps, 0 changed), which is the only check made that they were not edited afterwards.

**A. Deletions and removals**
1. dev-01: the four outputs of the first generator reproduction (`authoring/generated/repro-*.yml` and their manifests) were deleted with `rm -f` before the second run; they had not been bound by any hash.
2. dev-03, dev-03b: `__pycache__` directories created by `py_compile` and by the authoring dry-run were removed with `rm -rf` from the harness directories.
3. dev-10: `diag-harness-src/t08_diag.py` was edited with `sed -i.bak` and the `.bak` was deleted.

**B. Edits in place before binding (not new-file revisions)**
4. dev-01: the two generator copies in `authoring/` were edited in place after creation (skip of the harness `MANIFEST.sha256`). dev-04: `authoring/push-attempt.sh` was edited in place after the first dry-run failed. dev-05: `authoring/analyze_v2.py` was edited in place once. dev-10: `t08_diag.py` was edited in place twice (warm-up stage; budget constant). The unused earlier outputs are listed in the notes (`analyze_v2` outputs, `repro-windows-pairs5.yml`, `build_expected` v1 outputs, `derive_diag_workflow.py` output, the draft reports `report-draft-1.md` and `report-rev2-draft-1.md`: dev-02, dev-05 to dev-07, dev-09, dev-11, dev-14).
5. dev-01: `authoring/generated/repro-windows-pairs5.yml` is misnamed (it is the default `T08_PAIRS=1` build); `repro-windows-env-pairs5.yml` is the one equal to the committed Windows workflow. One extractor call failed with a missing argument. The dispatch said the overlay has 8 files; the test-04 tarball and manifest contain 7 changed paths, so 7 were used.

**C. Side effects outside the raw root**
6. dev-03: the authoring dry-runs wrote commit, tree and blob objects into the shared repository's object store. Three of the resulting commits are unreachable from every ref and still present: `b4757ef7`, `0c30a074` (dev-03b) and `596d53c8` (the decision-01 dry run, same tree as the pushed attempt commit `a4fcc263`). They were never tagged or pushed. Trial source extractions were left in `/tmp/cr01-dry-t07`, `/tmp/cr01-dry-t08` and, for dev-10, `/tmp/cr01-local`.
7. dev-04, dev-04b: the first `push-attempt.sh --dry-run` calls used wrong relative paths and aborted before any commit; they left two directories `authoring/index-eval-bun-ci-07-11-dry` and `authoring/index-eval-bun-ci-08-09-dry` holding a temporary index file (dev-04 wrongly said empty; dev-04b corrects that).
8. dev-10: one local `node` run under Node 26 hung for 300 s and was killed with `pkill`.

**D. Reads that did not use descriptor-based readers**
9. The dispatch, the referenced ticket dispatches, reports and RESULTS files, and the frozen harness and source files were first read with the standard Read tool and shell (`cat`, `sed`, `grep`, `ls`, `diff`), not descriptor-based no-follow readers. In addition, the revision-1 analysis and authoring scripts (`analyze*.py`, `perf_anomalies*.py`, `build_evidence.py`, `build_expected*.py`, `make_harness.py`) read downloaded artifacts and harness files with standard file opens. Delivered bytes were swept with a descriptor-based reader only at freeze. From decision 01 on, evidence and source content reads used `diag-tools/fdread.py` and `analyze_diag*.py` read through it; `git` commands still read repository objects normally, and `shasum` hashing and `cp` copies still opened files by path.

**E. Interruptions, losses and correctness of the record**
10. dev-08: the model provider returned HTTP 429 after the Linux run completed; the coordinator resumed the same session about 10 h 16 min later. No evidence was written between the stop and the resumption.
11. dev-12: the instrumentation JSONL files of the diagnostic attempt were not uploaded; the loss is unrecoverable and what it leaves unverified is listed above. dev-13: notes on the diagnostic attempt. dev-15: the docstring of `freeze/freeze_sweep_v2.py` names a script that does not exist; the sweep script as hashed is what ran.
12. dev-09 and dev-14: two report drafts contained statements not supported by receipts and were replaced before binding (kept unused). dev-16: this correction's own notes.
13. Both measurement attempts and the diagnostic attempt were pushed after dry runs of the attempt-building script (against `/tmp` indexes) and local dry runs of the harness setup; none of those is evidence.

## Freeze

Revision 1: `rev1-raw/anchor.json` (SHA-256 `1ce790eaf3cafce9feebd034d7df06b34df9e7bb037fe2d56456b5ebe8af8afe`, preserved unchanged in `freeze/anchor.json`). Revision 2: `rev2-raw/anchor-v2.json` (SHA-256 `0d8f3a7dff9c63aeafed491884c3cd89a11f5be928de80c74e627b1d6c366476`, preserved unchanged in `freeze/anchor-v2.json`). Revision 3: `freeze/anchor-v3.json`. The refreshed workflows in the worktree are unchanged since revision 1.
