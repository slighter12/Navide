# Ticket 03 — E2 concrete HOLD, version 1

## Decision

**HOLD: the original Node/pnpm development probe did not establish readiness in the required isolated environment. No Bun incompatibility or adoption conclusion is established.** Aggregate baseline typechecks and the real baseline application build succeeded. The subsequent real `pnpm dev` launched the renderer server and Electron, then reported repeated sandbox-initialization failures and a fatal GPU exit. The launcher returned 0; that is not application readiness. Dependent candidate verification was not run. This is an environment exception for coordinator routing, not a demonstrated failure of Navide in an ordinary unrestricted user profile.

The receipt does not establish the complete root cause or prove that every alternative isolation setup is impossible. No Electron sandbox-disabling flag, unsandboxed fallback, security/trust change, third-party repair, timeout retry or product-source workaround was attempted. Review and any new isolation plan belong to the coordinator. Ticket 04/05 and early-stop 09/C are not dispatched by this worker.

## Bound authority and snapshots

- Execution authority: `coordination/ticket-03-worker-dispatch.md`, SHA-256 `a4a1828ea030e95dc728e3c9cbc4239faa6548308980ee570255616a3945acf8`, relative to the external evaluation base.
- Actual worker: NEW session `01a111a8-810e-701e-b0ae-a1db8e57557b`, `openai-codex/gpt-6.1-sol/high`; live environment and native session configuration agree. Native creation: October 6, 2026, 14:40:18.190 UTC; no parent session was recorded.
- Shared worktree: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch `eval/issue-2-bun-toolchain`, HEAD `5a6b78ce70b2ef775a5b5ba201a4474a8574c342`. Index remained untouched. No commit, push, hosted change or new worktree/branch.
- Original supported baseline: `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`, Node 22.23.2/pnpm 10.0.0. Its new source archive SHA-256 is `e3da8e5c9900c599920634fa1515cb2fdd53b5d15bd95a09225a2e08d8c723b9`, matching the frozen ticket-01 archive.
- Candidate input: accepted HEAD above, Bun 1.4.2; new archive SHA-256 `cdcc5ca85a1a3b4f91ea96775ac9c022d5c9426be86c75840509d5f628a93db5`. It was prepared, not credited with new product results.
- Both execution directories are new, owned, non-Git copies. Dependency inputs came from the original ticket-01 baseline and the accepted A correction respectively; frozen producer programs were not executed. Final source-copy checks matched 2,825 baseline and 2,872 candidate source files. Selected copied frozen inputs, tools and authority bytes matched in 57,392 checks; this does not certify every original/frozen file or continuous immutability.
- Retained A report: `checkpoint-A/integrated-correction01-candidate-v2.md`, SHA-256 `7ae0f046a89c743c5dec25d6051a8c4b1c02fa2b2d9ae9a5a8eaaf2876301daf`; its actual same-byte endorsements were checked through the ratification file. Historical packet authorization text remains unchanged and is superseded only within this actual E2 grant.

External evidence root: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-03`. All evidence paths below are relative to that root. Frozen delivery version: `freeze-v1`; its external anchor binds the complete WIP archive and final evidence inventories without a circular self-hash in this document.

## Acceptance criteria

The numbering follows the nine criteria in the saved ticket03, not a new acceptance policy.

| Criterion | Actual status | Evidence / limitation |
|---|---|---|
| 1. Authority, snapshots and isolation | Qualified partial | `preflight/authority.json`, `packet-verification.json`, `native-session-configuration.json`, source/dependency copy inventories and isolation probes. Isolation denies were exercised; complete startup under that environment failed. |
| 2. Existing aggregate typechecks, baseline first then candidate | Baseline PASS; candidate NOT RUN | `commands/baseline-aggregate-typecheck.*`: exit 0, original Node/integration checks, contracts→SDK→UI build, three official Plugin Vue checks, web and public checks. No check substitution. |
| 3. Real public/official/application builds and runtime observations | Baseline build PASS; overall incomplete | `commands/baseline-application-build.*`: exit 0. Candidate not run; ordinary build has no exhaustive nested-child telemetry. The separate dev probe observed 35 owned process identities. |
| 4. Native/Python prerequisites and real outputs | Baseline outputs present; candidate NOT RUN | Plans production Python binary, pane bundle and fn-key helper built by the real build. Owned uv 0.12.23, Python 3.12.11 and locked backend sync; no skip-backend-build. No release package or optional STT model build is credited. |
| 5. Candidate has no daily external Node/pnpm | NOT ESTABLISHED | No candidate daily command or startup executed. Bun version identity alone is not proof. Electron's separate embedded-runtime identity reports Electron 44.4.5 / Node 24.21.0; that supplemental run is not readiness. |
| 6. Bounded isolated development probe | Baseline attempted; candidate NOT RUN | `commands/baseline-development-startup.*`: distinct HOME/XDG/cache/config/tmp/store/backend storage and explicit owned Electron profile. Startup denies real-home reads, peer writes, security executable access and signal permission on the outside-sandbox owned controller. |
| 7. Actual readiness and owned cleanup | HOLD | Captured renderer URL `localhost:5174`, Electron and uv/Python children, then fatal startup errors. No backend-ready marker or backend/Plugin health proof. Two observed orphaned uv/Python processes were identity-rechecked and terminated; final observed-owned remainder and stream-reader count were zero. Detached processes not observed by sampling are not certified absent. |
| 8. Attributable artifacts for 04/05 | Baseline preserved; candidate absent | `artifacts/baseline/index.json`: 1,105 entries. No baseline artifact is substituted for a candidate product. No independent consumer/lifecycle acceptance. |
| 9. Results, logs, identities and stop conditions | Concrete HOLD recorded | `commands/index.json`, raw complete streams, artifact inventories, additive failures and `freeze-v1`. No comparison pass or broader adoption claim. |

## Commands and products actually exercised

Ordinary runs had neither `NODE_OPTIONS` nor `BUN_OPTIONS`. Commands were executed without output pipelines, under sanitized allowlist environments and owned copies. Their real argv/cwd/environment, exits and complete stdout/stderr are in the receipts.

- `pnpm install --frozen-lockfile`: exit 0, `Already up to date`; this used copied installed dependencies and is **not** a new empty-store/bootstrap proof.
- `pnpm typecheck`: exit 0. The complete aggregate command sequence is retained in its stdout; no tests or per-file compiler-emission telemetry are implied.
- `pnpm build`: exit 0, including public packages, legacy and v2 official Plugins, Plans backend, staging, pane/fn-key helpers and Electron/Vite application products. Existing warnings are retained in stderr.
- Required setup: owned `uv python install 3.12.11`, locked backend sync, `pnpm rebuild electron` and the existing allowlisted Electron install script through the pinned owned Node; exits 0. The rebuild emitted no output and is not alone proof of an installed Electron binary. The later real executable launch supplies that observation.
- `pnpm dev --clearScreen=false -- --user-data-dir=<owned-profile>`: actual launch with a fixed 75-second bound; root exited after about 35.51 seconds, before the bound. Exit 0, readiness false. No UI driving, screenshot, terminal clearing or visual acceptance.

Preserved public declarations: 12 contracts, 1 SDK and 45 UI `.d.ts` files, with other distributable files inventoried separately. These are new actual artifact counts, not the prior A comparison's 116 declaration-related rows. Final bytes were collected after both build and dev attempt: public/official prerequisites were rebuilt or reused as their logs state; `out/main` and `out/preload` are dev outputs. There was no pre-dev per-invocation production snapshot, so those final hashes are not misattributed to the ordinary production build. `out/renderer` and all selected products retain their explicit collection provenance.

## Isolation, observation and failures

Build/startup children received independent owned paths and no inherited vendor/auth/SSH/proxy configuration. Controls applied to experiment children only. Security executable access and foreign-controller signal permission were actually denied without querying vendor credential contents. This is not an exhaustive native Keychain or process-observation proof. No production credential contents, other backend or real Agent CLI profile was intentionally used. Original dirty-work byte equivalence was not exhaustively inventoried and is not asserted.

The separate development probe uses an external reachable-owned-tree sampler, not a runtime preload. It changes supervisor/resource activity; observer neutrality is not claimed. Sampling can miss short-lived/reparented processes; six numeric observation errors are retained. Its current URL predicate does not strip ANSI escapes. The captured backend-ready marker count was zero, so its HTTP branch was not reached before the independent Chromium fatal/root exit. No backend/Plugin/renderer HTTP success is credited. See `preflight/startup-observer-output-qualification.json`.

Final producer bytes are frozen, but there are no pre-run producer digests for every invocation. Failed reader/path/schema/ownership-preflight attempts remain failures, not product failures: pnpm loader ancestor-metadata denial; wrong helper import names; rejected numeric sandbox host syntax; wrong native child-count interpretation and failed smoke; static-reader size/symlink/path and output-overflow attempts; guessed metadata/hook paths; and a directory-read identity guard detecting the worker's own authorized directory creation. Native failure exports and original command receipts are additive; later corrected attempts do not repair their history. A first document check also incorrectly asserted that Git's no-index exit must be 0; it returned 1 with empty diagnostics. Separate empty/clean controls also returned 1, while the whitespace-error control returned 3 with diagnostics. That failed interpretation is retained, not repaired. The actual `.githooks/pre-commit` was subsequently discovered through Git configuration and read, not bypassed or executed.

The startup also logged that a custom marketplace Registry lacked explicit root approval and installed v2 Plugins remained quarantined. No trust approval was synthesized. Full Plugin readiness is therefore uncredited independently of the GPU failure.

## Source deltas, inherited qualifications and missing coverage

Ticket03 changes only this report and its evidence index under `docs/toolchain-evaluation/bun/ticket-03/`; **zero product/build-helper source adaptations**. Environment/setup/observation producers live only in the owned external evidence root. Existing candidate-versus-original tool changes remain `bun.lock`, `package.json`, `scripts/bun-evaluation.mjs`, `scripts/bun/vue-tsc.cjs`, `tests/support/publicPackagesSetup.ts` and `vitest.config.ts`; no broader source comparison is claimed.

All eleven A residuals remain: duplicated configuration; unsupported candidate pnpm bootstrap; deduplicated alias scope; private compiler API/pinned maintenance; canonical-entrypoint-only version rejection; frozen-range semantics; historical unexplained PID 40769; incomplete Electron lifecycle evidence; local-path publication exposure; inherited dompurify advisory severity; inferred audit-ID mapping. The original Node baseline's instrumentation, unestablished instrumentation equivalence and 1/15 artifact selection remain historical qualifications. Same-graph Node controls remain distinct from pnpm bootstrap. The prior 116-row Node attribution is not fresh current-source/full-platform validation.

A's W1–W5, both reviewers' R1/R2, worker appendix and coordinator process failures are retained by reference to the exact A report above, without erasing historical holds, stops or severity. Zero active A findings is not issue adoption.

Not run: candidate aggregate checks/build/startup; new Vue negative/cache controls (no adapter changed); root/Vitest/artifact/CLI/backend/supplementary tests; external public consumers; complete Plugin packaging/install/update/start-stop lifecycle; performance medians; release packaging; external CI; Linux/Windows; production migration; manual visual acceptance. Prior A checks are not relabeled as new E2 executions.

## Handoff

Freeze complete current WIP, raw evidence, owned profiles and attributable products as version 1. The source WIP is an unchanged accepted HEAD plus exactly the two new report files; staged diff remains empty. The external final verification binds source/evidence/artifact inventories twice and records the cutoff. Read-only freezing is not a guarantee against all future privileged mutation.

Await independent ticket review and coordinator routing of the demonstrated startup-environment exception. Any correction requires a new additive version and renewed review. No commit until a separate exact reviewed-path grant; no push/publication, adjacent ticket, migration or adoption authority follows from this HOLD.
