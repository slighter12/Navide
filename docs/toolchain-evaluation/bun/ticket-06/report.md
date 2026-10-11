# Ticket 06 — macOS end-to-end test performance

**Gate result: PASS.** Bun 1.4.2 is faster than the Node 22.23.2/pnpm 10.0.0 control in all six
required workload × cache conditions. The median slowdown (`bun median / node median − 1`) ranges
from **−25.8 %** to **−41.3 %**, so no required matched condition exceeds the packet's
“more than 10 % test-workflow slowdown fails” rule. Test content is matched: the root workflow
selects the same 836 files and 12,470 tests in both runtimes, and the Bun root run carries the one
accepted ticket-04 residual failure. This is an **end-to-end workflow comparison**, not a
runtime-only delta: each side runs the workflow as its own toolchain ships it (control = `pnpm` +
lockfile + base `package.json`; candidate = Bun dispatcher + `bun.lock` + branch `package.json`),
with matched test selection, matched direct dependency versions and per-sample cache records.
Raw evidence: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-06-01/`.
Correction 01 (documentation only, no re-measurement) is recorded in that root's `correction-01/`.

## Result table

| Workload | Condition | n (Node/Bun) | Node median | Bun median | Slowdown | Dispersion (MAD) | Verdict |
|---|---|---|---|---|---|---|---|
| Full root Vitest workflow | cold | 5 / 5 | 112.949 s | 75.112 s | −33.50 % | Node 2.731 s, Bun 1.261 s | PASS |
| Full root Vitest workflow | warm | 5 / 5 | 96.779 s | 61.512 s | −36.43 % | Node 1.984 s, Bun 0.664 s | PASS |
| SDK single file (`packages/plugin-sdk/src/index.test.ts`) | cold | 5 / 5 | 1.056 s | 0.784 s | −25.76 % | Node 0.003 s, Bun 0.003 s | PASS |
| SDK single file (`packages/plugin-sdk/src/index.test.ts`) | warm | 5 / 5 | 1.036 s | 0.766 s | −26.06 % | Node 0.000 s, Bun 0.004 s | PASS |
| EditorPane DOM single file (`packages/plugin-ui/src/editor/EditorPane.test.ts`) | cold | 5 / 5 | 0.839 s | 0.508 s | −39.45 % | Node 0.009 s, Bun 0.018 s | PASS |
| EditorPane DOM single file (`packages/plugin-ui/src/editor/EditorPane.test.ts`) | warm | 5 / 5 | 0.831 s | 0.488 s | −41.28 % | Node 0.007 s, Bun 0.003 s | PASS |

Warm-up pairs, excluded from every median above: root 84.724 s / 55.156 s, SDK 1.051 s / 0.775 s,
DOM 0.828 s / 0.488 s (Node / Bun).

## Fixed points and matched inputs

| Item | Value |
|---|---|
| Node control | Node 22.23.2 + pnpm 10.0.0, source = frozen base `f4e9fd50` + the five branch adaptation paths (ticket-04 adaptation correction-01 “alias-symmetric control”; the branch tree cannot run `pnpm install`) |
| Bun candidate | Bun 1.4.2 (`1.4.2+744846f84`), source = branch HEAD `58508e2b611cc0dcfc27af1b5c66ffe896b0a7b9` |
| Source archives | `tarballs/base-f4e9fd50.tar` `e3da8e5c…`, `tarballs/head-58508e2b.tar` `637913a3…`, `tarballs/adapt-5.tar` `481254e7…` (all re-verified by SHA-256 in the guest before extraction) |
| Shared adaptation paths | all five byte-identical in control and candidate (`vitest.config.ts` `22edeec3…`, `bunMockImporterAnchor.ts` `dd377281…`, `plansBuildScripts.test.ts` `06bbb483…`, `packageBoundary.test.ts` `29dda9a7…`, `compositionBoundary.test.ts` `14f31a54…`) |
| Remaining input differences | five further files differ between the two trees (ten in total across base→HEAD); all of them are disclosed in “Control and candidate input differences” below |
| Dependencies | 14 of the 15 probed packages resolve to identical versions in both cases (vitest 3.2.7, vite 6.4.3, vue 3.5.34, @vitejs/plugin-vue 6.0.9, vue-tsc 2.2.12, typescript 5.9.3, happy-dom 20.14.5, electron 44.4.5, …); `esbuild`'s manifest is absent at the probed top-level path in **both** cases; 0 version mismatches, 24 top-level entries and 622 store entries in both layouts |
| Machine | one disposable UTM guest: macOS 27.0.1 (build 26A434), arm64, 6 CPUs, 12 GiB RAM, Apple Virtualization |
| Guest cases | `/Users/navide/eval/t06-base-node`, `/Users/navide/eval/t06-head-bun` (new; earlier `t04*`/`t05*` cases untouched) |

## What was measured

The measured wall time of every sample is the complete process wall clock of the workflow from
spawn to exit — Vitest startup, project selection, the `artifacts` project's globalSetup package
build in the cold condition, all child processes, and reporting. Installation is **not** inside any
sample.

| Workload | Node control command (cwd = case `source`) | Bun candidate command |
|---|---|---|
| root | `tools/pnpm/bin/pnpm test:run --reporter=default --reporter=json --outputFile.json=<case>/vitest-json/<label>.json` | `tools/bun/bin/bun scripts/bun-evaluation.mjs run test:run --reporter=default --reporter=json --outputFile.json=…` |
| sdk | same + `--project unit packages/plugin-sdk/src/index.test.ts` | same + `--project unit packages/plugin-sdk/src/index.test.ts` |
| dom | same + `--project unit packages/plugin-ui/src/editor/EditorPane.test.ts` | same + `--project unit packages/plugin-ui/src/editor/EditorPane.test.ts` |

`test:run` is `vitest run` (non-watch). The root run selects all three projects (`unit`, `cli`,
`artifacts`), and its 836-file selection includes every file ticket 04 used for its supplementary
gates: the 4 `tests/cli/**` files (53 tests), the 3 `plugins/navide-mini-ide/tests/**` files (5
tests), `pluginBackendSandbox.test.ts` (26 tests, 19 running + 7 platform-skipped),
`packages/plugin-sdk/bin/dev-backend.test.ts` (8 tests, 7 + 1), `pluginBackendHost.thirdParty.test.ts`
(12 tests) and the three packaged-lifecycle files — all of those ran inside the timed samples. What
ticket 04 ran outside this workflow is **not** timed here: the packaged-lifecycle coverage needs
`NAVIDE_TEST_PACKAGED_PLANS=1` / `NAVIDE_TEST_PRODUCTION_PLANS_BACKEND=1` (with
`NAVIDE_TEST_ARTIFACTS_PREBUILT=1`), which the daily workflow does not set, so its 15 gated tests are
part of the 23 pending tests below and were never executed or timed in any sample; ticket-04's own
packaged-lifecycle evidence remains the source for that coverage. Typechecks (`typecheck:*`) are not
part of the root Vitest workflow and were not timed. No suite was deselected for scheduling or
timing. The two single-file workloads are exactly the files the packet's performance-method table and
ticket-02 strategy evidence name.

Every command runs under the case environment only (`HOME`, `TMPDIR`, XDG dirs, caches,
`AGENT_TEAM_DATA_DIR`, `PATH` from the case `env.json`) with `stdin` `/dev/null`. The Node case PATH
resolves `node`, `npm`, `npx` and `pnpm` and reports `bun=ABSENT`; the Bun case PATH resolves only
`bun` and reports `node=ABSENT npm=ABSENT npx=ABSENT pnpm=ABSENT`.

## Cold and warm, exactly as applied

**Cold** removes only experiment-owned generated state inside the case copy before every sample:
`node_modules/.vite`, `node_modules/.vite-temp`, `node_modules/.cache/navide-artifact-inputs.json`,
`packages/plugin-contracts|plugin-sdk|plugin-ui/dist`, `dist-plugins/navide-mini-ide`. Installed
dependencies and lockfiles are never touched. Each removal records what existed before and after
(`reset` in the schedule; `cache_state_before/after` in every receipt).

**Warm** retains the same valid prerequisite state left by the preceding identical run. The root
workflow requires the transform cache, the artifact stamp and the package/mini-IDE dists (its
`artifacts` project rebuilds them through globalSetup); the single-file workflows run only the
`unit` project, which has no globalSetup and resolves sources through aliases, so their generated
cache is the Vite/Vitest transform cache and the public-package outputs are recorded as
informational state.

Three record levels describe that state and they do **not** cover the same paths — the retained
root warm runs predate the wider rule that the SDK/DOM re-runs use:

| Record | Root workload (as retained) | SDK / DOM (`*-r2`) |
|---|---|---|
| cold reset record, every cold sample | 7 paths: `node_modules/.vite`, `node_modules/.vite-temp`, the artifact stamp, `packages/plugin-contracts/dist`, `packages/plugin-sdk/dist`, `packages/plugin-ui/dist`, `dist-plugins/navide-mini-ide` | the same 7 |
| warm verification record, every warm sample | 5 paths: `node_modules/.vite`, the artifact stamp, `packages/plugin-contracts/dist`, `packages/plugin-ui/dist`, `dist-plugins/navide-mini-ide`. **`packages/plugin-sdk/dist` is not among them**, `node_modules/.vite-temp` is not either, and this record carries no per-path `required` flag | 7 paths with a per-path `required` flag; only `node_modules/.vite` is required |
| per-sample receipt `cache_state` (before/after) | 4 paths: `node_modules/.vite`, the artifact stamp, `packages/plugin-ui/dist`, `dist-plugins/navide-mini-ide` | the same 4 |

So the retained root warm records evidence the 5 paths listed and do **not** evidence the state of
`packages/plugin-sdk/dist` (or `.vite-temp`) at that moment; those two paths appear only in the cold
reset records and, for the SDK/DOM re-runs, as conditional entries.

The pre-`r2` records also differ in what they *require*, which is why `all_required_present` is not
true everywhere: `root-warm.json` and `root-identity.json` verify their 5 paths (true),
`sdk-warm.json` and `sdk-identity.json` require only `node_modules/.vite` (true), but the superseded
`dom-warm.json` and `dom-identity.json` still required the public-package outputs from a unit-only
workflow, so they record `all_required_present: false` with those outputs absent — the condition
that produced deviation 3 below and the workflow-conditioned re-run. The gate-set records
(`root-warm`, `sdk-warm-r2`, `dom-warm-r2`) all verify true for the sets they cover.

No reset ever touches the repository worktree: every case is an isolated copy under
`/Users/navide/eval/t06-*`, and the worktree HEAD `58508e2b` is unmodified with a clean
`git status`.

## Per-sample raw timings

| Sample | Node cold | Bun cold | Node warm | Bun warm |
|---|---|---|---|---|
| root 1 | 111.930 | 73.851 | 89.531 | 61.512 |
| root 2 | 110.218 | 72.584 | 98.422 | 60.356 |
| root 3 | 116.478 | 77.566 | 94.795 | 63.887 |
| root 4 | 112.949 | 76.193 | 99.740 | 61.908 |
| root 5 | 117.811 | 75.112 | 96.779 | 60.848 |
| sdk 1 | 1.059 | 0.784 | 1.045 | 0.773 |
| sdk 2 | 1.047 | 0.796 | 1.036 | 0.769 |
| sdk 3 | 1.052 | 0.782 | 1.055 | 0.762 |
| sdk 4 | 1.058 | 0.787 | 1.036 | 0.734 |
| sdk 5 | 1.056 | 0.778 | 1.036 | 0.766 |
| dom 1 | 0.943 | 0.542 | 0.837 | 0.485 |
| dom 2 | 0.838 | 0.490 | 0.831 | 0.495 |
| dom 3 | 0.839 | 0.508 | 0.819 | 0.494 |
| dom 4 | 0.830 | 0.490 | 0.854 | 0.486 |
| dom 5 | 0.858 | 0.513 | 0.824 | 0.488 |

Samples interleave one Node and one Bun run per index (Node first) so drift cannot land on one
runtime; the guest ran nothing else during sampling. CPU totals from the same runs (observation
disabled, `RUSAGE_CHILDREN`): root cold Node 458–502 s vs Bun 295–323 s; root warm Node 426–478 s
vs Bun 283–304 s; the single-file runs are 1–2 s vs 0.6–1.0 s. The largest observed child RSS is
uniformly higher for Bun on the root workload (median 2.55 GiB cold / 2.48 GiB warm for Node vs
4.35 GiB / 4.04 GiB for Bun, individual maxima 4.75 GiB) and equal on the single-file runs
(0.13 GiB for the SDK file, 0.21 GiB for the DOM file).

## Dispersion and outliers

Dispersion is small relative to the measured gaps: MAD/median is 1.08–2.42 % (root) and
0.00–3.54 % (single-file), and Node's own run-to-run spread (3.17 s stdev on a 112.949 s cold root
median) is an order of magnitude below the 37.837 s gap. Outlier flags use a MAD-based modified
z-score (> 3.5): the only flagged samples are `sdk-warm-r2-01-node` (1.045 vs 1.036 median),
`sdk-warm-r2-03-node` (1.055) and `sdk-warm-r2-04-bun` (0.734), i.e. +9 ms, +19 ms and −32 ms
against medians whose MAD is itself only 0–4 ms; `dom-cold-01-node` (0.943 vs 0.839) is the largest
flag at +104 ms and is the first sample of that phase. Excluding every flagged sample leaves all
six verdicts unchanged (slowdowns −0.2592 to −0.4128, all PASS). No sample failed in a way that
would need a retry; the Bun root exits are 1 by design (accepted residual below) and were never
discarded.

## Inventory comparison

| Workload | Selected | Node control | Bun candidate |
|---|---|---|---|
| root | 836 files / 12,470 tests | 836 files passed / 0 failed; 12,447 tests passed / 0 failed / 23 skipped / 0 todo; exit 0 | 835 files passed / 1 failed; 12,446 passed / 1 failed / 23 skipped / 0 todo; exit 1 |
| sdk | 1 file / 27 tests | 1 file passed; 27 passed / 0 failed / 0 skipped; exit 0 | 1 file passed; 27 passed / 0 failed / 0 skipped; exit 0 |
| dom | 1 file / 9 tests | 1 file passed; 9 passed / 0 failed / 0 skipped; exit 0 | 1 file passed; 9 passed / 0 failed / 0 skipped; exit 0 |

Selected content is identical in both runtimes: the same 836 files and the same 12,470 tests, with
an identical per-file test count for every one of the 836 files. The only per-file difference
anywhere in the selection is the status of `pluginExternalWorkspace.test.ts` (passed on Node, failed
on Bun).

The 23 skipped tests are the same 23 tests, in the same five files, in both runtimes (identical
titles, checked per sample). They are environment-gated, not disabled:

| File | Skipped | Gate |
|---|---|---|
| `src/main/plugins/pluginBackendSandbox.test.ts` | 7 | Linux bubblewrap live probes (platform gate) |
| `src/main/plugins/pluginBackendSupervisor.test.ts` | 6 | `NAVIDE_TEST_PACKAGED_PLANS=1` + packaged fixtures |
| `tests/integration/plansPackagedRoundtrip.test.ts` | 5 | `NAVIDE_TEST_PACKAGED_PLANS=1` / `NAVIDE_TEST_PRODUCTION_PLANS_BACKEND=1` |
| `src/main/plugins/pluginBackendHost.test.ts` | 4 | `NAVIDE_TEST_PACKAGED_PLANS=1` + packaged fixtures |
| `packages/plugin-sdk/bin/dev-backend.test.ts` | 1 | Linux-without-bubblewrap live probe (platform gate) |
| **total** | **23** | |

Every root sample produced exactly this inventory: one signature per runtime across all cold, warm
and identity root runs (same file set, same per-file test counts, same 23 skipped).

### The residual failure and its wall-time direction

The single Bun failure is `src/main/plugins/pluginExternalWorkspace.test.ts` — the user-accepted
ticket-04 residual. The observed message in this round is the ticket-04 classification
(`Error: Executable not found in $PATH: "pnpm"`, raised by the file's `spawn` at line 64 through
its own `packageManager()` helper path `runPnpmOrThrow`; the shipped tree carries no helper
branch). Ticket-04 adaptation additionally measured that routing that helper to Bun does not make
the test pass, which is why the residual is accepted as a documented gap rather than a product
assertion. It is present in **every** Bun root sample (cold, warm and identity) and is counted in
the Bun wall times rather than excluded.

Its wall-time direction **favours Bun**, not Node. From the frozen Vitest JSON reports, that file's
own duration is **5.007–5.315 s** on Node (cold) and **5.839–6.889 s** (warm), because Node really
performs the pnpm install/pack work the test asks for, against **0.010–0.020 s** on Bun, where the
spawn fails immediately. Bun therefore reaches its wall time having done strictly less work than
Node on this one file, so the uncorrected root ratios (−33.50 % cold, −36.43 % warm) overstate
Bun's advantage; the equal-work correction below removes it.

Equal-work correction (the reviewer's method, reproduced here from the frozen reports): credit Bun
with the work it did not perform, per sample, as `bun_corrected = bun_wall + (node_file − bun_file)`,
then take medians of the corrected sets:

- **cold**: credited Bun samples 78.845, 77.885, 82.831, 81.304, 80.271 → median **80.271 s**;
  Node median **112.949 s**; 80.271 / 112.949 − 1 = **−28.93 %** — still PASS, no slowdown.
- **warm**: credited Bun samples 67.339, 67.225, 70.159, 68.472, 67.288 → median **67.339 s**;
  Node median **96.779 s**; 67.339 / 96.779 − 1 = **−30.42 %** — still PASS.

The mirror variant, subtracting the same per-sample delta from each Node sample instead
(`node_corrected = node_wall − (node_file − bun_file)`), gives **−30.35 %** cold (75.112 / 107.838 − 1)
and **−31.91 %** warm (61.512 / 90.339 − 1). Both variants keep the verdict, and the credit-Bun
variant quoted first is the one less favourable to Bun.

## Runtime identity — no Node/pnpm fallback

Beyond the PATH check, three observation-enabled identity runs (0.25 s sampling interval,
never used for timing) recorded the whole owned process tree of the Bun root, SDK and DOM
workflows:

| Workload | Node control observed | Bun candidate observed |
|---|---|---|
| root | 664 processes, 14 case-owned executions, node-named = `t06-base-node/tools/node/bin/node` only, 0 remaining after exit | 477 processes, 13 case-owned executions, node-named = **none**, bun-named = `t06-head-bun/tools/bun/bin/bun` only, 0 remaining after exit |
| sdk | 8 processes, node-named = the case node only | 9 processes, node-named = none, bun-named = the case bun only |
| dom | 8 processes, node-named = the case node only | 9 processes, node-named = none, bun-named = the case bun only |

Foreign executables in the observed trees are system tools only (`/bin/bash`, `/bin/sh`,
`/usr/bin/env`, `clang`, `cc`, `sandbox-exec`), symmetric across both runtimes; no `node`, `npm`,
`npx` or `pnpm` executable appears anywhere in the Bun candidate's observed trees, and the Bun case
directory contains no `node`/`npm`/`npx`/`pnpm` file at all. The observation is bounded and not
claimed exhaustive: short-lived processes that exit between the child listing and the `ps` lookup
are recorded as `numeric-disappearance`/`errno 3` (72+20 in the Node root run, 70+9 in the Bun root
run) and are flagged `not_exhaustive_detached_or_fast_child_proof` in the receipts.

## Installation and preparation (measured separately, outside the gate)

| Step | Node control | Bun candidate |
|---|---|---|
| frozen dependency install | `pnpm install --frozen-lockfile`, exit 0, **10.736 s**, 11.7 s child CPU | `bun scripts/bun-evaluation.mjs install`, exit 0, **7.527 s**, 6.2 s child CPU |
| lockfiles unchanged by install | yes (`pnpm-lock.yaml` `dd087379…`; no `bun.lock`) | yes (`bun.lock` `812fcf65…`, `pnpm-lock.yaml` unchanged) |
| backend uv sync (preparation) | 271.941 s (cold uv/PyPI fetch) | 2.324 s (cache already populated by the first case) |
| Plans test fixture build (documented prerequisite) | 6.682 s | 6.091 s |

These are single preparation samples, reported for completeness and explicitly **not** part of the
performance gate: they are never inside a measured workflow's wall time, and the uv asymmetry is a
network/cache effect of running the first case cold.

## Control and candidate input differences (matched-workflow disclosure)

The control is the frozen base plus the five shared adaptation paths; the candidate is branch HEAD.
Beyond those five paths — byte-identical on both sides — five further files differ, making the
complete base→HEAD input difference ten files:

| File | Control (base + adaptations) | Candidate (branch) | Nature |
|---|---|---|---|
| `vitest.config.ts` | `22edeec3…` | `22edeec3…` | shared adaptation (identical) |
| `tests/support/bunMockImporterAnchor.ts` | `dd377281…` | `dd377281…` | shared adaptation (identical) |
| `tests/integration/plansBuildScripts.test.ts` | `06bbb483…` | `06bbb483…` | shared adaptation (identical) |
| `plugins/navide-mini-ide/tests/packageBoundary.test.ts` | `29dda9a7…` | `29dda9a7…` | shared adaptation (identical) |
| `plugins/navide-git/tests/compositionBoundary.test.ts` | `14f31a54…` | `14f31a54…` | shared adaptation (identical) |
| `tests/support/publicPackagesSetup.ts` | `64245c77…` | `cd63ae9d…` | candidate adds a `process.versions.bun` branch and Bun-only artifact-digest inputs |
| `package.json` | `313f77d7…` | `9f2163ac…` | candidate adds `workspaces`, dependency `overrides`, `trustedDependencies`, `engines.bun`, the `eval:bun` script and `node scripts/bun/vue-tsc.cjs` typecheck routes; plugin dev-dependency specifiers move from `link:` to `workspace:*` |
| `bun.lock` | absent | `812fcf65…` | Bun-only lockfile |
| `scripts/bun-evaluation.mjs` | absent | `7ccbe18a…` | Bun-only dispatcher used by the candidate's install/run |
| `scripts/bun/vue-tsc.cjs` | absent | `e1db20f8…` | Bun-only vue-tsc launcher |

Consequences, stated rather than smoothed over:

- `publicPackagesSetup.ts` takes the same `packageManager()` path on Node and still sets the two
  pnpm-only environment variables (now under a `!process.versions.bun` guard), so it is inert for
  Node test behaviour; it does, however, change the artifact-input digest inputs and hashed payload
  (it adds `scripts/bun/vue-tsc.cjs`, `bun.lock`, `scripts/bun-evaluation.mjs`, `BUN_OPTIONS` and
  `bun: undefined`). That affects only the artifact cache stamp, not test content or assertions.
- `package.json` carries candidate-side `overrides`, the only difference that could change
  *transitive* resolution. Direct dependencies were checked (14 of 15 probed packages resolve to
  identical versions; `esbuild`'s manifest is absent at the probed top-level path in both cases),
  and both cases install the same numbers of top-level entries (24) and store entries (622), but no
  full transitive-tree equality proof was performed.
- The candidate therefore installs from `bun.lock` with the branch `package.json`, and the control
  from `pnpm-lock.yaml` with the base `package.json`. This is what “the two daily toolchains” means
  here: an end-to-end workflow comparison with matched test selection and matched direct dependency
  versions, not a single-variable runtime swap.

## Deviations, exactly as they occurred

1. **Runtime-identity probe, first attempt failed.** `runtime-identity` exited 127 in the Node case
   before printing anything: it used `$CASE_ROOT`, which the allowlisted per-command environment
   does not define. The failed receipt is retained; the probe was changed to take the case root as
   an argument and re-run as `runtime-identity-r2` (exit 0).
2. **Cold definition changed mid-ticket for the single-file workloads.** The first implementation
   reset only the Vite transform cache, and the SDK cold set ran that way
   (`retrieved/schedules/sdk-cold.json`, retained). The cold rule was then made uniform across
   workloads (adding the artifact stamp, the three package dists and the mini-IDE dist), and every
   workload was measured again under it (`sdk-cold-r2.json` is the gate set). The two SDK cold sets
   agree (medians 1.016 vs 1.056 s Node, 0.728 vs 0.784 s Bun), which is the evidence that the
   extra resets are immaterial for a unit-only run. Only `sdk-cold-r2` is used for the gate.
3. **Warm verification made workflow-conditioned after the first warm sets.** The first version
   required the public-package outputs for every workload; the unit single-file workflows neither
   consume nor rebuild them, so `sdk-warm.json` and `dom-warm.json` are retained as agreement sets
   (SDK 1.043/0.770 s, DOM 0.831/0.493 s) and re-run under the workflow-conditioned rule as
   `sdk-warm-r2.json` and `dom-warm-r2.json` (the gate sets). `dom-warm.json` therefore records the
   public-package outputs as absent for a unit-only run and, because that older rule demanded them,
   reports `all_required_present: false` in every sample (as do the `dom-identity.json` runs); the
   gate-set `r2` records report true for the set they actually require.
4. **uv sync asymmetry.** 271.9 s in the first case against 2.3 s in the second: the first case paid
   the cold uv/PyPI download. Preparation only; nothing measured touches uv.
5. **Stray `cd` in one launch command.** The warm-phase launch contained an accidental
   `cd /Users/slighter12/eval 2>… || cd /Users/navide/eval` inside the backgrounded subshell. Every
   path the driver uses is absolute, so no artifact, receipt or state was affected; the invocation
   is in the host SSH log.
6. **Guest background load.** Spotlight indexing (`mds`, `mdworker_shared`) was observed working on
   the case trees during sampling. It is shared load affecting both runtimes, is not disabled (no
   sudo; disabling indexing is outside the authorized scope), and is covered by interleaving plus
   the recorded dispersion and per-sample load averages.
7. **Fixture binaries are not byte-reproducible.** Each case builds and uses its own PyInstaller
   `navide-plans` and Go `navide-plans-go` fixture before any sample (ticket-04 observation,
   unchanged here).

## Scope limits and non-claims

- **End-to-end workflow comparison, not a runtime-only delta.** Each side runs the workflow as its
  own toolchain ships it (control: `pnpm` + `pnpm-lock.yaml` + base `package.json`; candidate: Bun
  dispatcher + `bun.lock` + branch `package.json` with `workspaces`/`overrides` and Bun-only helper
  files). The numbers say how the two daily workflows compare, not what a runtime swap alone would
  measure.
- macOS only, one guest VM, one set of versions (Node 22.23.2/pnpm 10.0.0, Bun 1.4.2, Vitest 3.2.7);
  Linux and Windows remain issues 07/08. This is not project-wide readiness.
- The guest is a shared machine: ambient host load, guest background services and the guest disk
  are outside the experiment's control; results are protected by interleaving, not by isolation
  from the host.
- Timings are wall-clock medians over five samples per runtime and condition, with the recorded
  dispersion; they are not a distribution study and no confidence interval is claimed.
- The Bun root workflow keeps the accepted 1-file/1-test residual failure and still exits 1. The
  gate here is performance; correctness parity is ticket 04's verdict, not re-litigated by a faster
  wall time. That residual's own work is asymmetric — Node performs 5.0–6.9 s of real work on that
  file while Bun fails it in 10–20 ms, which is in Bun's favour; the equal-work correction above is
  the conservative reading of the root comparison.
- Single-file workloads are ~1 s runs whose MAD is 3–20 ms; their ratios are reported with that
  precision limit, and the outcome (Bun faster in both conditions of both workloads) is much larger
  than the jitter.
- Bun's higher peak child RSS on the root workload (median 4.35 GiB cold / 4.04 GiB warm vs
  2.55 GiB / 2.48 GiB for Node) is an observation, not a gate; memory headroom is a separate
  question for the recommendation ticket.
- Not run: release packaging, hosted CI, application startup/UI acceptance, registry installs,
  Linux/Windows.

## Freeze anchor

`freeze/freeze-v1.json` binds the two tracked deliverables, every harness script (host and retrieved
guest copy), all schedules, receipts, logs and frozen Vitest JSON reports, the three source archives
and the retrieval archive, the host-side analysis and the host SSH invocation log;
`freeze/freeze-v1.sha256` records the anchor's own SHA-256. The anchor is delivered, not committed.

### Correction 01 (documentation only)

An independent review of the freeze-v1 delivery returned **PASS WITH QUALIFICATIONS**. Correction 01
applies only documentation changes to the two tracked deliverables: the residual's wall-time
direction and the equal-work corrected root ratios (P2-1), the explicit inventory including the 23
conditionally pending tests and the corrected supplementary-coverage statement (P2-2), the accurate
scope of the root warm verification record (P3-1), the control/candidate input-difference disclosure
(P3-2), and the end-to-end-workflow caveat. No sample was added, removed or re-run, and no receipt,
log or frozen Vitest report was rewritten. The corrected snapshot is bound by
`correction-01/anchor.json` + `correction-01/anchor.sha256`, which records the freeze-v1 anchor it
supersedes and lists every bound file that changed relative to it (the two tracked deliverables and
the evidence generator only; all measurement artifacts unchanged). `freeze/freeze-v1.json` and its
SHA-256 are left untouched and still verify.

## Boundaries

No test, assertion, skip, timeout, coverage, dependency, lockfile, runtime version or workflow
change was made. All work happened in the evaluation root
(`/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-06-01/`) and in isolated
guest copies under `/Users/navide/eval/t06-*`; the shared worktree stayed at `58508e2b` with a clean
status, and this ticket performs no stage, commit or push.
