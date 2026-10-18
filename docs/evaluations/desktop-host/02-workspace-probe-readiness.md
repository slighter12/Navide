# 02 — Workspace Probe Readiness: final report

**Disposition: Workspace Probe Readiness with disclosed gaps, and only that.** The R14 probe harness itself still records `NOT ESTABLISHED` (12 GAP rows), and that field is not overridden. The final reviewer (fresh `i4-t02-sol12`) returned REQUEST CHANGES and classed four of the GAPs as blocking: stale-frame native rejection (g4), wrong-run native receiver rejection (g5), wrong-response-correlation native rejection (g6) and embedded Node/Chrome versions from the consumer (g7). Since then g4 and g6 were closed by non-injecting validator proofs (section "Line 8 validator proofs", separate from R14 live evidence), and g5 and g7 are carried as open items by the user's decision below. The human-only visual acceptance is PENDING.**

> **User decision, 2026-10-18 ("option 3"), as relayed in the coordinator's dispatch `t02-sonnet-final2.md` (SHA256 `d4095e26ab7e6f762cd7e16b7b6a1c3a5412524e631e91aadce37b9e764eb910`); not a reviewer approval and not a verbatim user message:** "g4 and g6: close them now with non-injecting validator/ledger proofs. No launch. g5 and g7: carry them as disclosed, user-accepted open items, deferred to ticket 03. Ticket 03 launches the app multiple times anyway. Ticket 02 then closes as 'Workspace Probe Readiness with disclosed gaps'."

This label does not mean full workspace usability, visual correctness or complete compatibility. A missing observation is a verification gap, never a success. Evidence lives under `.scratch/desktop-host-evaluation/evidence/02/`, which is git-excluded and local to this worktree: paths below are relative to the worktree root and the manifests pin their contents.

## Scope and fixed inputs

- Worktree branch `feat/issue-4-desktop-host-evaluation`. Source HEAD `645a04256c3192c7d94e3b2ef6d77bc30483659a` (initial revision `0f66f5f34150c8e73db4b7f32d3176fcaa5a2020`; the two differ only by the accepted ticket-01 report). Prerequisites G01 (user-confirmed merge completion) and G02 (bounded runtime assignment) were closed before any runtime work.
- Approved common input bundle: 39 hashes verified, manifest SHA256 `e9e04e89a328d3f9eb29ec48445b711ba354a7d155c982d65e01ba9f3c9e6c2a`.
- Released Plans package `navide.plans@0.2.9`, darwin-arm64, SHA256 `ca51155d6d4d741bf17ee19e185f12532862baf00d852cd95651e10ab1f4a6d5`, installed through the normal Registry, consent and promotion path. The supporting Mini-IDE is a source-built receiver (app version 0.2.14), not a released package.
- Target: macOS, arm64, current user account, Electron 44.4.5 (Vue 3.5.34 renderer). Pinned tools: Node 22.23.2, pnpm 10.0.0, Python 3.12.11, uv 0.12.22, psutil 7.2.2 (`versions-summary.json` in the R14 evidence). The embedded Node/Chrome versions were NOT observed from the consumer (GAP g7).
- Environment inputs: an explicit allowlisted environment (OWN HOME/XDG/TMP/cache/store/Git config/data roots, pinned PATH, telemetry off, `NAVIDE_DESKTOP_HOST_EVALUATION_ROOT`); provider and user credentials omitted; names only are recorded, never values. Keychain: metadata-only checks of three exact items immediately before launch (all absent, exit 44) and 44-only after.
- The probe runs in a persistent OWN root `RB` = `~/.ni4/r-df0904ae6a8f/` (sources, compiled app `n10`, restored SDK, pinned uv). `RB` is NOT in the repository.

## Baseline suites (before runtime changes)

Original raw outputs, failed attempts and skips are frozen under `.scratch/desktop-host-evaluation/evidence/02/baseline-c0*`.

| Snapshot | Executed result | Boundary |
|---|---|---|
| C0 | Frontend focused 9 files / 691 tests exit 0; restricted uv sync exit 1 | Bootstrap deviation and original failure preserved. |
| C0b | Locked isolated uv sync exit 0; backend focused 69 passed; frontend 812 files / 12,254 passed / 8 skipped, exit 0 | Full backend not run in this phase. |
| C0c | Full backend 17 failed, 8,948 passed, 31 skipped, 3 errors, exit 1 | Under-Git / long-temp interactions; no test or source repair. |
| C0d | Same two IDs isolated: 1 failure/1 error under a long path, 2 passed outside Git; full backend 8,968 passed, 31 skipped, exit 0 | Environment correction only; same skips. |

Historical limits stay open: the C0c metadata overshoot and the unexplained serialized-index change between C0d and D0 (actor unknown). These baselines are not desktop acceptance. This payload ran `pnpm test:run` on `src/main/evaluation/desktopHostEvaluation.test.ts` and `src/main/plugins/frontendPluginManager.test.ts` (2 files, 384 tests, exit 0; 374 before the ten validator tests added for ticket line 8), `pnpm typecheck:node` and `pnpm typecheck:integration` (exit 0). The aggregate `pnpm typecheck` was not run: it rebuilds the public packages, which would change files that sealed manifests hash.

## Observer seam B1 and its deltas

Tracked, Host-owned, passive and read-only: `src/main/evaluation/desktopHostEvaluation.ts` (with its test), hooks in `src/main/plugins/frontendPluginManager.ts`, `src/main/index.ts`, and the opt-in in `electron.vite.config.ts`. It is inert unless the build sets `NAVIDE_DESKTOP_HOST_EVALUATION=1` and an absolute marked OWN root: normal builds contain a literal `false` (`electron.vite.config.ts:30-43`) and every hook returns before doing anything (`if (!root) return`; `desktopHostEvaluation.enabled`). B1 records frame admission/request/response/event/revocation tuples as hashes, bounded Plans DOM rows, and one read of the pane-owned xterm getter; it adds no Plugin authority, no package mutation and no transport.

- **B1 shape fix** (authorized 2026-10-09): array projection of DOM rows.
- **N9**: caches the host window id per frame binding while the window is alive, because a native close destroys the host window and frame before revocation runs; without the cache the revocation row was lost (`evaluationHostWindowIds`).
- **N9b**: `observeQuitPrepare` records the actual `prepareForQuit` result (true / false / re-entrant / not required).
- **N10 (close-decision)**: `observeCloseDecision(outcome, reason)` records which host path decided a close preparation (nine enum outcomes, four enum reasons: begin-busy-lock, begin-busy-pending-target, receiver-busy, receiver-refused, provider-busy, provider-refused, timeout, unavailable, accepted). Enum-only, no identity or payload. A new manager test drives a real receiver/provider transaction and fails against the previous manager.

The private evaluation driver, the guard, the helpers and the private build define (`__VUE_PROD_DEVTOOLS__`) are NOT tracked; they live in the evidence packs and `RB`.

## R14 result

One detached launch on 2026-10-18: launcher 12:58:23Z, app up 12:58:26Z, guard `n2k` outcome `COMPLETED_CLEAN` (reason `EARLY_COMPLETE`, app exit 0, 967.8 s, clean scope KNOWN_IDENTITIES, child coverage bounded). Four human Plans clicks, each a genuine DOM/watch/list correlation: `human-1-initial-A` at 8.3 s, `human-2-workspace-B` at 19.1 s, `human-3-return-A` at 22.3 s and `human-4-reopened-A` at 26.6 s (new window), measured as driver elapsed time. The window close of the earlier failures was observed: the host decided `accepted` (reason `native-window-close`), the window closed 105 ms after the request, the revocation row was recorded, and the first quit was observed with `prepareForQuit` true. Compiled seal SHA256 `0e6e0d8d4f55ab89419576d99bd9d62f7f730c6d53ca16a6a4a052aab655a5a5` (238 members), plan SHA256 `193fb69c5775ce81b63d897f0498604526af0fdfd3f5c2d91355dd0b98910795`, evidence seal `runtime-probe-r14-20261017T141455Z-3e5a1dcd/r14-evidence.sha256` (139 entries) SHA256 `8280a1432d923080077eb2878fc4f7dc5686def0f0ca3ae1619649bad08a2195`.

Ledger: 30 PASS, 0 FAIL, 12 GAP (`ticket-02-ledger.json`).

| Criterion | Status | Evidence |
|---|---|---|
| Exact bindings / fresh gates | PASS | gates-complete.json; lifecycle.json |
| Original R_N byte invariance | GAP | n383-admission.json; old-n-before.json; post-integrity.json |
| Historical /tmp members read from the preserve copy only (prefix mapping, same recorded hashes) | PASS | tmp-preserve-mapping.json; tmp-preserve-mapping-used-gates.json; tmp-preserve-mapping-used-post.json |
| Persistent root RB gates (N6b/~/.ni4) | PASS | rb-gate.json; rb-before.json; rb-after.json |
| Pinned tools and explicit OWN environment | PASS | versions-summary.json; runtime-preparation.json |
| Metadata-only Keychain immediately before launch | PASS | keychain-immediately-before-launch.json |
| Canonical compiled graph | PASS | canonical-graph-admission.json |
| Normal install, consent, promotion | PASS | captured-private-driver.jsonl |
| Seed re-arm through the product route (seed-plans-recovery-retry) | PASS | captured-private-driver.jsonl |
| Host Plans state snapshots (before/after retry, Mini-IDE open, first human gate) | PASS | captured-private-driver.jsonl |
| Plans storage metadata after run | PASS | plans-storage-metadata.json |
| Per-row observer timing and capability names | GAP | observer-timing-join.json; captured-observer-timed.jsonl |
| human-1-initial-A | PASS | captured-private-driver.jsonl |
| human-2-workspace-B | PASS | captured-private-driver.jsonl |
| human-3-return-A | PASS | captured-private-driver.jsonl |
| human-4-reopened-A | PASS | captured-private-driver.jsonl |
| Natural A change -> list reply -> correlated DOM | PASS | captured-private-driver.jsonl |
| Real guest denial before side effect | PASS | captured-private-driver.jsonl |
| PTY output consumed by real xterm | PASS | captured-private-driver.jsonl |
| Echo-only and wrong-window negatives | PASS | captured-private-driver.jsonl |
| Natural rename propagation | PASS | captured-private-driver.jsonl |
| A/B instance and DOM separation | PASS | captured-private-driver.jsonl |
| Actual Mini-IDE Plans consumer | PASS | captured-private-driver.jsonl |
| Normal close revocation | PASS | captured-private-driver.jsonl |
| New admission after reopen | PASS | captured-private-driver.jsonl |
| Restored Plans DOM after reopen | PASS | captured-private-driver.jsonl |
| Native-backend-principal shell.run rejection | GAP | captured-private-driver.jsonl; approved-plan.md |
| Stale-frame native rejection | GAP | captured-private-driver.jsonl; approved-plan.md |
| Wrong-run native receiver rejection | GAP | captured-private-driver.jsonl; approved-plan.md |
| Wrong-response-correlation native rejection | GAP | captured-private-driver.jsonl; approved-plan.md |
| Embedded Node/Chrome versions from actual consumer | GAP | captured-private-driver.jsonl; approved-plan.md |
| Cumulative refresh lifetime | PASS | captured-private-driver.jsonl |
| Actual normal refresh traffic / complete origin inventory | GAP | network-origin-evidence.json |
| Bounded guard outcome | PASS | guard-result.json; guard-exit.json |
| Normal app quit: will-quit observed in the FIRST quit phase (a fallback is a GAP, never a PASS) | PASS | captured-private-driver.jsonl |
| App-quit window preparation outcome observed by the evaluation observer (quit-prepare rows: prepareForQuit true/false, re-entrant pending, not required) | PASS | captured-private-driver.jsonl; captured-observer-timed.jsonl |
| Known-identity cleanup | PASS | owned-residual-check.json |
| Complete descendant absence | GAP | owned-residual-check.json |
| Original/source/compiled seals and known N3 additions | GAP | post-integrity.json |
| Frozen inputs, in-repo archives metadata and persistent root RB (R_N not walked) | GAP | post-trees.json |
| Pin absence, Electron hash, pnpm cache/store absence | PASS | pnpm-pin-electron-post.json |
| Human-only visual acceptance | GAP | human-visual-checklist.md |

### The 12 GAPs and their triage

Classes: (a) the plan forbids the probe (forged or synthetic injection); (b) disclosed limitation that the ticket does not require; (c) deferred to the human checklist; (d) blocking. No GAP is a PASS. Full reasoning and plan citations: the triage record of this payload.

| GAP | Class | Why |
|---|---|---|
| Original R_N byte invariance | (b) | macOS `tmp_cleaner` emptied the original temp root; user-approved self-pin and hash chain replace it. |
| Per-row observer timing and capability names | (b) | Partial join only; no ticket line requires it. |
| Native-backend-principal `shell.run` rejection | (a) | Needs a synthetic backend-principal request; the guest-principal denial is PASS. |
| Stale-frame native rejection (g4) | closed by validator proofs | The reviewer classed it blocking. Closed 2026-10-18 by non-injecting validator proofs of the real decision code (section "Line 8 validator proofs"); natural stale in-flight evidence is still NOT observed live. |
| Wrong-run native receiver rejection (g5) | deferred to ticket 03 (user-accepted) | The reviewer classed it blocking. Tuples carry no run id; needs two real epochs (plan B4:49) or an authorized observer change. Ticket 03 launches the app repeatedly. |
| Wrong-response-correlation native rejection (g6) | closed by validator proofs | The reviewer classed it blocking. Closed by validator proofs; no response was injected live. |
| Embedded Node/Chrome versions from the consumer (g7) | deferred to ticket 03 (user-accepted) | The reviewer classed it blocking. Electron 44.4.5 and tool versions are recorded; the embedded values need a consumer-side observation. |
| Actual refresh traffic / complete origin inventory | (b) | Elapsed lifetime is not network proof; no per-wire inventory. |
| Complete descendant absence | (b) | Known identities clean; a global process scan was not allowed. |
| Original/source/compiled seals and known N3 additions | (b) | 3,976 members re-hash; same R_N cause. |
| Frozen inputs, archives metadata and RB (R_N not walked) | (b) | 454 frozen entries unchanged, RB unchanged; same R_N cause. |
| Human-only visual acceptance | (c) | Pending the user (below). |

### Line 8 validator proofs (g4, g6, backend-only, ACK-only, ready-flag-only)

These are VALIDATOR proofs of the real decision code, run offline after R14. They are not R14 live evidence, nothing was launched, no response, descriptor or port was injected into a running app, and no non-test tracked source changed. Natural stale in-flight evidence was NOT observed live and is disclosed as such; wrong-run (g5) stays open.

Three layers decide whether evidence can become a PASS, and each was exercised through its real code:

1. **Driver gate** (`N10/sources/private-driver.ts`, SHA256 `ff07b11988772caaea0239fba586ca75199516993f040af0f85b26f2bec8cc86`, the file compiled into the R14 app): the exact `until(...)` callback of `humanMount` (the human-gate decision) is extracted from that source with the TypeScript AST, transpiled and run against rows; only the electron-bound `genuineFrame()` is stubbed and counted. Positive control: the real R14 first-gate rows. 23 cases, all PASS (`driver-predicate-proofs.json`).
2. **Ledger** (`X29/r9_main.py`, SHA256 `3e18f05ead9e17281b0c22d94ffe5e80342a76590811aca6fe165ea0b2c9c99b`): the real `report()` and `optional()` are extracted with `ast` and run with injected inputs (only the write sinks are replaced). Positive control: the real R14 inputs reproduce the sealed `ticket-02-ledger.json` criterion by criterion (42 rows, 30 PASS, 12 GAP). 14 cases, all PASS (`ledger-proofs.json`).
3. **Observer validators** (`src/main/evaluation/desktopHostEvaluation.ts`): 10 new tests in the tracked `desktopHostEvaluation.test.ts` (384 tests in the two focused files, exit 0), each with a genuine positive control.

| Evidence kind | Function exercised | Result |
|---|---|---|
| Stale frame (g4): renewed document generation, other frame tree node, evidence older than the step barrier, reply or admission after the DOM row | driver `humanMount` callback, cases S1, S2, S7, S8, S9; observer `snapshotPlans` rejects a renewed generation, a revoked binding, another instance and no binding | NO PASS row in every case; control P0 PASSes |
| Wrong instance / window / workspace (g4) | driver cases S3-S6; observer `observeFrame` writes no row for a revoked binding, generation 0, a non-integer frame node or window, a workspace outside the OWN root or relative, an illegal package id or version (7 variants) | NO PASS / no row; the genuine binding writes one row |
| Wrong response correlation (g6) | driver C1 (another request id: no pass), C2 and C3 (response id differs or backend id missing: the driver aborts with `PLANS_RESPONSE_CORRELATION_MISMATCH`, no pass row), C4 (DOM rows differ from the replied list); observer test: request, backend and response ids are recorded as separate hashes (a mismatch stays visible) and raw ids are never written | no PASS row |
| Backend-only output | driver B1 (list reply and subscribe, no DOM row) and B2; ledger H-real-mounted-watch-list and T-backend-only (status `backend-output-only`); observer: a genuine backend list result yields no DOM or terminal row | no PASS row; ledger criterion stays GAP |
| ACK-only | driver K1-K4 (ok without a list, subscribe not ok, only the subscribe ACK, subscribe without the `plans.changed` event); ledger H-acknowledged and A-ack-only; observer: an acknowledgement yields no list and no event | no PASS row |
| Ready-flag-only | driver F1-F3 (flags on every row, with or without a DOM row, wrong package version); ledger H-ready, R-unconditional (extra rows and a ready file: the five native-rejection / consumer-version criteria stay GAP and the `readiness` field stays `NOT_ESTABLISHED`) and X-exhaustive (every case/status pair seen in R14 plus generic statuses injected: the five criteria stay GAP); observer: no flag from a payload or response is copied into a receipt, an unknown error code is dropped | no PASS row |
| Echoed input / wrong status | ledger E-echo-removed, E-echo-wrongstatus, H-observed, H-wrongcase; live R14: the echo-only no-output case and the wrong-window control PASS | criterion stays GAP unless the exact consumer status is present |
| Driver abort | ledger Z-abort | a recorded abort adds a FAIL row |

What this does NOT show: that a natural stale in-flight message would be rejected at runtime in a real app (not observed live), any wrong-run behaviour (g5), or that the extracted decision code is the only way a row can be written (the sealed R14 pack and the compiled N10 tree are the evidence that this is the code that ran). `genuineFrame()` (a webContents liveness check) was stubbed.

## Ticket checklist map (13 lines)

| # | Ticket line (short) | Status | Evidence |
|---|---|---|---|
| 1 | Prerequisites G01 and G02 closed before runtime work | PASS | `coordination/ledger.md` lines 7 and 8 |
| 2 | Record revision, versions, artifacts, environment | GAP | Recorded except the embedded Node/Chrome versions from the consumer (g7), deferred to ticket 03 |
| 3 | Select workspace, package, identities, contracts | PASS | B4 plan, input discovery, run plan |
| 4 | Existing suites against the baseline before runtime changes | PASS | C0 to C0d, with the preserved original failures and limits |
| 5 | Exercise the actual desktop seam in an isolated profile | PASS | R14 driver rows and ledger |
| 6 | Multiple windows, mounting, real Plugin request/event, terminal output at xterm, denial without side effect | PASS | R14 ledger; the native-backend-principal variant stays a disclosed (a) GAP |
| 7 | Fresh challenges correlated to run, workspace, window, instance and pane | PASS | `I4T:<fresh16hex>:46` challenge, identity hashes on every gate row |
| 8 | Stale / wrong-instance, echo, backend-only, ACK and unconditional-flag cannot pass | PASS | Validator-proven kinds (section above) plus the live negatives (echo-only, wrong-window, A/B wrong-instance). DISCLOSED OPEN: natural stale in-flight evidence not observed live; wrong-run (g5) deferred to ticket 03 |
| 9 | Prefer existing seams; Host-owned read-only observer with an agreed contract | PASS | Observer B1 and deltas; approvals `ledger.md:84`, `:199` |
| 10 | Isolation and owned cleanup; never clear scrollback | GAP | Disclosed: complete descendant absence and the network inventory |
| 11 | Record failures and exact boundaries; label only Workspace Probe Readiness | PASS | This report |
| 12 | No screenshots, synthetic clicks, AppleScript, forged events, DevTools snippets; human-only judgments to the checklist | PASS | The tooling rule held (four genuine human Plans clicks); the human items below are PENDING, not inferred |
| 13 | Rerunnable probe and evidence/input manifest | PASS | Probe pack and seals; `RB` is outside the repo and R_N is purged (disclosed) |

## Open items handed to ticket 03

- **g5 wrong-run native receiver rejection**: needs two real epochs (two app runs with different run identities) or an authorized run id in the observer tuples. Ticket 03 launches the app repeatedly, so it can observe two epochs naturally. Accepted by the user as open (2026-10-18).
- **g7 embedded Node/Chrome versions from the consumer**: needs a consumer-side observation of the embedded versions; Electron 44.4.5 and the tool versions are already recorded. Accepted by the user as open (2026-10-18).
- Natural stale in-flight evidence is not observed live; only validator proofs exist.
- Other disclosed open items stay with their current owners: the network/origin inventory, complete descendant absence, per-row observer timing, and the pending human checklist.

## Failed attempts R1-R13i (tooling causes; nothing hidden)

All were tooling, harness or environment causes unless stated; none was a product pass or fail.

| Run | Outcome | Cause and fix |
|---|---|---|
| R1 | Blocked, no launch | The private driver/data/guard hard-coded the old temp root; the pinned Electron lacked the Registry root file. Reconciled the write scope; provisioned the pin exactly as planned. |
| R2 | Guard `OWNERSHIP_GAP` after 12 s | Guard ownership too strict (child ancestry); a pnpm 11 self-switch also appeared. Pinned pnpm 10; built a session/process-group ownership guard. |
| R3 | No launch | Guard successor and archive built; lifetime fix (R3b). |
| R4 | Guard `OWNERSHIP_GAP: UID_NOT_OWNED` | A setuid-root helper spawned by the app was treated as foreign (cause inferred, not proven). Moved to an observe-only guard (n2c/n2d/n2e). |
| R5 | Budget stop, driver abort at 1.2 s `NORMAL_UI_OPERATION_REFUSED` | The driver discarded the backend error code; fixed in N3. |
| R6 | Driver abort at 16.7 s `ui_no_window_known` | Window/workspace readiness ordering; six driver mismatches fixed (N4 D1-D6), B1 shape fix. |
| R7 / R7b | R7: 0 launches (own helper errors). R7b: driver abort at 4.5 s | Unexpected driver error after the Mini-IDE open; fixed in N5. The app stayed alive Dock-only until the guard budget. |
| R8 | 0 launches | The old temp root was purged by `tmp_cleaner`; replaced by a self-pin and hash chain with a permanent GAP. |
| R8b | Plans storage `BACKEND_UNAVAILABLE` | The legacy `package-incomplete` latch; N6 added the product `retryPlansV2` re-arm in the seed. |
| R9 | Driver abort `D02_HUMAN_PLANS_MOUNT_TIMEOUT` | The driver waited for `plans.watch`; Plans 0.2.9 emits a subscribe `plans.changed` row. Predicate fixed; post-trees read cap raised (N7). |
| R10 | Guard `STOPPED_ESCAPE` at 58 s | The terminal child runs in a new session and the guard counted it as an escape; n2k tracks owned new sessions. |
| R11 | Driver abort `D04_REAL_XTERM_256_GETTER_UNOBSERVED` | Vue strips `__vueParentComponent` in production builds; a private build define restored it (N8). |
| R12 | Driver abort `D04_WINDOW_CLOSE_REVOCATION_UNOBSERVED`; budget stop | The native close destroyed the window before revocation, so the hook skipped it (cached window id, N9); quit hang fixed with a bounded quit. |
| R13b-R13h | No launches | Gate-hardening rounds from review: observer exception table, live tracked-file gate, expanded git-status, complete-file-set gates, NOREAD predicate and its routing. |
| R13i | Driver abort at the close step; the user saw a native dialog "A plugin is still busy" | The driver opened the Mini-IDE with an editor tab on an absolute `filepath`; the tab showed a load error, and the Mini-IDE close guard answers busy (a source-supported inference; the tab state was NOT observed). N10 opens the window without a file and records the close decision. |

## Product observations

- **Absolute `filepath`**: `openEditorWindow` accepts an absolute `filepath`. The backend `fs_service._resolve_safe` strips the leading `/` and joins the rest under the workspace root, so the read fails with `not a file`, the editor shows a load error instead of a refusal, and the Mini-IDE close guard then answers `busy` (native dialog "A plugin is still busy"). The chain is source-supported and is an INFERENCE about R13i: the tab state was not observed, the backend function was not executed, and a possible user press of the retry button is unverified. Product routes pass relative paths.
- **Plans `package-incomplete` latch**: when the app boots without a bundled Plans artifact, the legacy recovery stays latched for the session: capability and backend calls answer `BACKEND_UNAVAILABLE` while a backend subscribe succeeds. Neither plugin restart nor activation change clears it; the Control pane's `retryPlansV2` does.
- **PTY `setsid` design**: terminal children run in new sessions by design; supervisors that treat a new session as an escape (R10) misreport a healthy terminal.
- **Vue internals in production builds**: `__vueParentComponent` and `__vnode` exist on DOM nodes only in development builds or with `__VUE_PROD_DEVTOOLS__`; a probe that reads component internals needs a build-time define.
- **macOS `tmp_cleaner`**: it purged the original `/private/tmp` roots (reads do not refresh `atime` on APFS); evaluation state belongs in a persistent root.
- **Window close ordering**: a native close destroys the host window and frame before the revocation hook runs.
- **Plans 0.2.9** never calls `plans.watch`; it uses the subscribe row `plans.changed`.
- **Host CLI version checks**: the backend runs `<pnpm> --version`; with pnpm 11 on PATH the `packageManager` self-switch produced network traffic. Controlled through PATH.

## How to rerun

1. Probe pack: `.scratch/desktop-host-evaluation/evidence/02/runtime-probe-r14-20261017T141455Z-3e5a1dcd/` (helpers, guard copy, plan `r14-run-plan.md`, preparation seal `r14-preparation.sha256`). Build/evidence packs: `n10-correction-20261017T141455Z-3e5a1dcd/` (compile procedure `compile-procedure.md`, compile and static-check scripts, guard `n2k/launch_guard.py`), `n10b-bundlecheck-20261017T145323Z-73308b45/` (successor bundle check; the original check failed on M22 because of a checker regex defect and is recorded as failed), `n10-postcompile-dryrun-20261017T150003Z-19763a53/` (full-gate dry run, 81/81).
2. `RB` (`~/.ni4/r-df0904ae6a8f/`) holds the run root `n10` (sources, compiled app, compile receipts), the restored SDK and the pinned uv. It is outside the repository and is not committed; the used `n10` root now contains R14 outputs, so a rerun needs a fresh run root, a new plan and a new preparation seal built from the same pack.
3. Compile once offline with the compile procedure; accept it with the successor bundle check; run the post-compile full-gate dry run; then, with the user present for four Plans clicks, run `r9_verify_prep.py <preparation seal sha> <plan sha>` and one `r9_start.py <dispatch path> <dispatch sha> <plan sha> <compiled sha> <source seal sha>` with the backend venv Python and `-I -B`, polling with `r9_poll.py`. Never relaunch within a run.
4. Tracked evaluation hooks stay inert without the build-time opt-in.

## Evidence and input manifest pointers

- R14 evidence seal `runtime-probe-r14-20261017T141455Z-3e5a1dcd/r14-evidence.sha256` (139 entries, SHA256 `8280a1432d923080077eb2878fc4f7dc5686def0f0ca3ae1619649bad08a2195`); ledger `ticket-02-ledger.json`; report `r14-report.md`.
- Source seal `n10-correction-20261017T141455Z-3e5a1dcd/n10-evidence.sha256` (SHA256 `b7a159a7034a104d77b345a6c9e675ef2d477ba068ac318503384b6bcff6e424`); preparation seal `r14-preparation.sha256` (SHA256 `8798ede74446f4e0f5d10963aeeef7329ad8260c46328a06cbd2a579c7f5461b`); successor bundle-check seal `n10b-bundlecheck-20261017T145323Z-73308b45/n10b-evidence.sha256` (SHA256 `05d3e4900f63691bfa7c49133d0a901ac5e0cb4e6828ea258fb3087a90575eed`); dry-run seal `n10-postcompile-dryrun-20261017T150003Z-19763a53/n10pd-evidence.sha256` (SHA256 `276ebefbcb76dba9c2d08a70ac1a26bc3cb6a4679fcf9f650765901786a9f264`).
- Known permanent gaps recorded in those packs: the E10 37/38 seal exception, the deleted first-run outputs of one earlier snapshot, a NOREAD violation in the E30 seal (synthetic fixtures named like protected files) and the R13g predicate and its digest-prefix receipts; stale plan text (items 35, 43, 57, 66(d)) is noted, not edited.

## Exact boundaries

- Success is labelled **Workspace Probe Readiness** only, for the 30 PASS rows. It is not full workspace usability, visual correctness or complete compatibility.
- Not proven live: native-backend-principal rejection, natural stale in-flight rejection, wrong-run rejection (g5, ticket 03), embedded Node/Chrome versions (g7, ticket 03), actual refresh traffic, complete descendant absence, original-root byte invariance. Stale/wrong-instance, wrong-correlation, backend-only, ACK-only and ready-flag-only rules are validator-proven (separate from R14 live evidence).
- No screenshots, synthetic clicks, AppleScript, forged events, gesture bypass or user-run DevTools were used. Secrets, tokens, credential material and NOREAD content are not part of this report; the compile-time dev token and production credentials were never read.
- **Dangling link from ticket 01**: the committed ticket-01 report (`docs/evaluations/desktop-host/01-capability-discovery.md`, line 182) links to `../../adr/0001-preserve-agent-cli-semantics.md`. The three ADRs under `docs/adr/` belong to the Agent CLI Runtime evaluation, are untracked in this worktree, and are EXCLUDED from this payload, so that link stays dangling at HEAD and in this payload; it is disclosed, not repaired.

## Human-only checklist: PENDING

These are not inferred from any receipt.

- [ ] Main terminal visible with a clean marker.
- [ ] Plans names and paths follow the rename naturally.
- [ ] A/B workspace isolation is visible.
- [ ] Mini-IDE really embeds Plans in its visible native window.
- [ ] Normal close/reopen restores the view with no stale content.
- [ ] Terminal focus and scrollback remain intact.
- [ ] Actual OS prompts, refusals or Dock-only behavior, if any.
