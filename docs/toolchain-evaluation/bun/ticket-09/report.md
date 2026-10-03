# Ticket 09 — evidence-backed early-stop recommendation

## Decision

**Retain Node.js 22.23.2/pnpm 10.0.0; defer Bun adoption.** The evaluated Bun 1.4.2 macOS path failed mandatory E1 gates in ticket 02. Acceptance of its STOP evidence is not feasibility acceptance. Partial installation/SDK success does not achieve the primary goal: all agreed daily JavaScript installation, startup, typecheck, Vitest and application/Plugin build workflows using only pinned Bun, without external Node.js/pnpm or a mandatory version manager. No mixed-toolchain or command-prefix adoption is recommended.

This completes the worker's authorized early-stop report preparation, not issue acceptance, migration, publication or checkpoint C. See [matrix.json](matrix.json) for complete bounded workflow/platform/contract states and [evidence-index.json](evidence-index.json) for attributable paths, hashes and review scopes. Evidence IDs below resolve there; no project workflow was rerun for ticket 09.

## Snapshot and authority

- Dispatch: DISPATCH09, early-stop reporting only. Worker `navide2-t09-worker`, actual `openai-codex/gpt-6.1-sol/high`, session `01a103a6-58b4-779e-a7c5-272a0b104b23`; coordinator-verified Herdr `default`, `w8:t7/w8:pA`.
- Sole worktree: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`; branch `eval/issue-2-bun-toolchain`; fixed review base and HEAD `d89016bab788d76b5346d51e83ea9fba535f3a72`.
- Initial product base remains `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`. Ticket 01 commit: `14b7a98322849f6ff38e14f6b892afdc01f47a90`; ticket 02 commit/current HEAD: `d89016bab788d76b5346d51e83ea9fba535f3a72`.
- Actual host evidence: macOS 27.0.1 (26A434), Darwin arm64. No Linux/Windows execution evidence exists in this evaluation.
- Delivered packet 1.1: all 27 listed file hashes verified. Saved issue/spec/tracker/triage/domain/CONTEXT/ADR documents are local provenance, not a fresh GitHub response. Historical inspection SHA, readiness and execution-not-granted metadata are not current execution authority. Dirty instruction copies are inert evidence. Adjacent Rust/Agent CLI evaluations remain separate. [packet-manifest, handoff, spec, ticket09-spec]
- Original `/Users/slighter12/git/personal/Navide`, dirty work, global tools, configuration, profiles, credentials, hooks and backend remain protected. Only the three ticket-09 report files are write/stage candidates. Ticket-02 experimental adaptations already committed on this branch are not production authorization.

## Concrete stop and supported control

Ticket 01's supported control used the exact Node 22.23.2 executable plus pnpm 10.0.0's JavaScript entry. Standalone pnpm's embedded Node 20.11.1 was inspected but is **not** this baseline. SDK **27 passed**, EditorPane/happy-dom **9 passed**, zero failed/skipped; genuine artifact setup built contracts → SDK → UI, prepared Mini-IDE frontend inputs, packed four tarballs and executed the selected deterministic CLI case (**1 passed, 14 name-filtered skips**). Discovery recorded **836 files: 808 unit, 4 cli, 24 artifacts**; discovery is not full-suite execution. Current Mini-IDE boundary files are root artifact owners, unlike stale historical omission wording. Guarded packaged Plans/native lifecycle cases were inventoried, not exercised. [baseline-evidence, baseline-report, baseline-sdk-results, baseline-artifact-results, baseline-inventory]

The baseline observer changed successful synchronous-helper stderr handling and added I/O; unconditional uninstrumented equivalence and benchmark suitability were not established. Its bounded instrumented observations and actual artifacts were accepted, not a whole-repository pass. Ticket 01's historical HOLD wording is superseded for delivery status by the actual round3 PASS, acceptance and inspected commit. [baseline-review, baseline-acceptance, baseline-handoff]

Ticket 02 executed the unchanged selected suites and real prerequisite path under Bun-only executable visibility:

| Workflow | Recorded result | Evidence |
| --- | --- | --- |
| Full selected SDK + EditorPane, observed | Exit 1; SDK 27 passed / DOM 9 failed / 0 skipped; one unhandled rejection | bun-sdk-dom-receipt, bun-sdk-results |
| Same selection, uninstrumented | Exit 1; same 27 passed / 9 failed / 0 skipped | bun-unobserved-receipt, bun-unobserved-results |
| Unchanged first DOM case; existing threads-pool probe | Both exit 1; each 1 failed / 8 name-filtered skips; skips are not coverage | bun-first-receipt, bun-threads-receipt |
| Actual artifact-project setup | Exit 1; contracts tsc → SDK tsc → UI Vite succeed, UI vue-tsc fails; **zero test cases executed** | bun-artifact-receipt, bun-artifact-results |
| Direct existing public-package build, uninstrumented | Exit 1; same TS2307 errors for `./EditorPane.vue` and `./SafeAiCliPanel.vue` | bun-build-receipt, bun-build-stdout, bun-build-stderr |

The already mocked EditorViewMonaco path nevertheless reached real Monaco and failed on a null canvas context at `webkitBackingStorePixelRatio`; the first dirty-event assertion failed and later cases had null wrappers. The exact mock/runtime integration defect is not proven. Uninstrumented and threads observations rule out an observer-only outcome, not every possible configuration. The Vue declaration build is a second concrete failed gate, not “No test files found” as a successful CLI result. Four packs, CLI execution and Mini-IDE preparation were not reached. No assertion weakening, canvas shim, mock rewrite, third-party patch/fork/replacement, dependency upgrade or Node fallback was used. [bun-report, bun-evidence, bun-dom-stderr]

**Classification:** demonstrated failures of the evaluated Bun path, not a failing supported baseline, proven third-party internal defect, universal Bun impossibility, intentional lack of later authority, or presumed lack of a Linux host. The actual ticket-02 review accepts the STOP artifact with zero required corrections; coordinator acceptance and seven committed blobs/real pre-commit success were inspected. E1 remains **FAILED**. [bun-review, bun-acceptance, bun-handoff, bun-commit, bun-committed-blobs]

## Partial benefits, costs and outstanding assurance

- Exact Bun **1.4.2** guard passes under Bun-only and competing-tool visibility. Genuine **1.4.1** install/build probes exit 1 before effects, with actionable manual-install guidance; source/locks/output/environment identities remain unchanged. The copied binary can run without mise; this bounded guard does not prove all daily contributor workflows or future upgrade safety. [bun-negative-guard, bun-tools, guard-source]
- Clean and repeated frozen installs exit 0 with unchanged lock/package hashes. Registry identities/integrities: **688**, layout addresses/integrity comparisons **739**; installed distinct identities **620** in both candidates; Bun physical copies **621** because of dmg-builder's peer-context duplicate; **68** platform-excluded identities and three source-local public links retained. All **1,141** installed dependency/optional/resolved-peer comparisons, including **68** excluded optional edges, have zero resolution differences. [bun-lock-comparison, bun-installed-inventory, bun-effective-graph, bun-reinstall]
- Native migration initially rejects relative `link:` entries. Explicit workspaces alone also fail; a six-scalar file-protocol diagnostic is malformed and does not disprove a valid file-protocol solution. The successful registry-only native migration retains registry bytes, then offline native workspace reconciliation restores the same three local packages. Reported 143 platform and 24 edge-representation differences are not hidden upgrades. Maintenance costs include lock conversion/provenance, explicit workspaces, the guarded experimental entrypoint and three-line helper dispatch; there is no generic migration framework. [bun-report, bun-lock-comparison, helper-source]
- All **13** security override constraints, original pnpm policy, electron/esbuild trust declarations and the original single advisory exception are retained. **Complete optimized dependency lifecycle behavior assurance is incomplete**, not a security PASS: koffi is reported untrusted, electron is listed trusted, electron-winstaller is not listed untrusted, and optimized native/esbuild handling is not exhaustively traced. Declaration equality/install success is insufficient. [bun-security, bun-effective-graph]
- Existing advisories remain visible: moderate Vitest and @vitest/mocker (`GHSA-82fw-gwwq-j7x9`), low DOMPurify (`GHSA-p98j-92pf-mc4p`), and ignored high http-cache-semantics (`GHSA-ch52-4w7c-c8xp`). Actual high-threshold policy exits 0, supplemental JSON exits 1 in both toolchains, with different ignored-advisory representations. Bun checks 612 registry names, including optional-platform entries; 544 installed names. No vulnerability-free verdict or remediation is claimed. [baseline-evidence, bun-security]
- **28 events / 28 distinct PIDs** identify the copied Bun 1.4.2 executable for observed JavaScript guard/Vitest/fork-worker/build children. The controlled daily PATH has no external node/npm/pnpm/mise. This proves only observed executed children, not unexecuted pack/CLI/startup or exhaustive native tracing. Final PID-scoped observations show none of those PIDs running; no unrelated backend was borrowed or killed. [bun-runtime, bun-process-final, bun-evidence]
- Performance is **NOT-RUN**, not passing or measured inconclusive. Recorded diagnostic durations are not five-sample matched cold/warm medians; no speedup, slowdown percentage, variance verdict or cost saving is supported. Future required comparisons retain full end-to-end setup/pack/child costs, full root + SDK + DOM workloads, at least five samples per runtime/condition, additional sampling for unresolved variance, and the unchanged **greater-than-10% slowdown fails** rule. [spec, ticket06-spec]

## Complete gate disposition

| Ticket/checkpoint | State at ticket-09 freeze |
| --- | --- |
| 01 / E0 bounded baseline | Accepted and committed; instrumented, selected scope only |
| 02 / E1 | **FAIL**; STOP evidence accepted and committed, not compatibility acceptance |
| A | **SKIPPED / NOT-RUN**, not passed |
| 03 | Not-run; blocked by required 02 pass |
| 04 | Not-run; blocked by 03 pass/artifacts |
| 05 | Not-run; technically depends on **03**, not 04 |
| 06 | Not-run; blocked by passing 04 **and** 05 |
| B | **SKIPPED / NOT-RUN**, not passed |
| 07 / Linux | Not-run; technically depends on 06; separate real-platform authority absent |
| 08 / Windows | Not-run; technically depends on **06**, not 07; separate real-platform authority absent |
| 09 / E5 early-stop | Report prepared/frozen; independent ticket review and coordinator acceptance pending |
| C | **PENDING / NOT-RUN**; required final Full Review of stage 09 and the entire issue |

Scheduling order does not add technical dependencies. Missing Linux/Windows evidence prevents adoption but is not evidence of platform incompatibility or runner unavailability; no unauthorized availability probe was made. All three platforms remain mandatory for adoption. Aggregate typechecks, application/official Plugin/native builds, safe development readiness, complete root/supplementary suites, packaged lifecycle, independent Node/npm/pnpm consumers and performance remain outstanding. The matrix distinguishes selected baseline results from Bun failures and downstream non-execution.

The Plugin Public Contract floor is unchanged but not validated for Bun-produced complete artifacts: exports/subpaths, declarations, schemas, styles/workers, peer requirements, CLI validation/canonical archive/signing/denials and Host install/execute/lifecycle still require applicable evidence. Manifest Permissions, Host Capability Limits, Package-version Grants and Execution Policy cannot be widened to obtain a pass. Existing consumers retain freedom to use Node.js/npm/pnpm; no forced Bun consumer policy follows. Electron's embedded runtime and unchanged Python/uv/native prerequisites remain permitted. The isolated release/CI Node exception is a design allowance, **not a validated path** and never a daily Node dependency. [spec, ticket05-spec, matrix.json]

## Recovery and conditional follow-up

Recovery is to **stop using the experimental copy** and retain the unchanged original Node.js/pnpm production workflow. Do not revert user work, delete evidence/caches, perform production rollback, or start Node/pnpm cleanup. Do not wholesale merge this experimental branch into production: its ticket-02 source/config/lock/helper adaptations have only evaluation authority.

Only if separately authorized, investigate the observed existing mock/Bun behavior, Vue declaration failures and incomplete optimized lifecycle assurance within the agreed constraints. Any fresh candidate/version or permitted orchestration change needs a newly identified, isolated source/dependency snapshot, corresponding supported control and renewed validation; it cannot reuse stale passes as proof. If those concrete E1 gaps are resolved without prohibited repair, every remaining macOS, public-consumer, performance and real Linux/Windows gate must still pass before adoption can be recommended. No implementation or additional execution is authorized by this report. A favorable future recommendation would still require separate migration design covering release/CI exceptions, rollout, production rollback and authoritative configuration/lockfile ownership.

## Review, human acceptance and publication boundary

C must use **fresh actual Sol/high and Opus 5.5/medium** reviewers against the same frozen stage/entire-issue identity, independently covering Standards, Spec, Correctness, Release readiness, conditional Security and over-engineering, with adversarial exchange and one integrated result preserving disagreements/risks. Release readiness means evaluation delivery readiness, not production/publication permission. C has not been dispatched or executed by this worker. Proposed reviewer metadata cannot establish acceptance. C is the final review; no separate final-review cycle is proposed.

The coordinator will retain actual completed C review as immutable **external evidence after source freeze**, as with prior reviews, and associate it through the actual registry/current ticket-09 handoff. `coordination/registry.json` is observed provenance; the ticket-09 handoff and C result are pending, so no future hash, self-referential report identity or fabricated review receipt is embedded. [coordination-registry]

Consolidated human checklist:

- [ ] Final **evaluation** acceptance: decide whether this bounded retain/defer recommendation and evidence disposition are acceptable after required C review. Not granted by worker report completion.
- Product visual acceptance: **not reached**. No application startup or UI change entered this early-stop route; no current UI acceptance claim or screenshot/automation request is needed. If a separately authorized future route reaches genuine visual behavior, reserve those judgments for one human checklist; process readiness alone is not visual acceptance.
- Publication, hosted issue update, push/PR/merge, release, production changes and later execution require separate authorization; none occurred here.

## Delivery and verification boundary

Exact proposed stage allowlist, **only after independent review and explicit coordinator go-ahead**:

1. `docs/toolchain-evaluation/bun/ticket-09/report.md`
2. `docs/toolchain-evaluation/bun/ticket-09/matrix.json`
3. `docs/toolchain-evaluation/bun/ticket-09/evidence-index.json`

Ticket-09 checks/receipts/freeze belong only under `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-09`. Format/JSON/whitespace/hash/reference checks and read-only source/original preservation checks are the only executed verification here. Final checks cover 44 matrix rows, 50 reference identities, 296 prior evidence identities, 2,862 tracked source identities and 155 live generated file hashes. Three nonzero checker/probe attempts remain recorded as failures: two no-index Git exit-status attempts and a reference-bound failure corrected by hash/selected-summary classification, not truncation. Final new-file whitespace validation uses read-only `git apply --check --cached --whitespace=error-all` (no apply/stage), alongside tracked `git diff --check`; JSON/format/reference/preservation gates exit 0. No product failure is converted into a pass. All 01/02/packet/source/config/locks/helpers/prior caches and evidence remain immutable. Large prior cache/source/dependency trees are supporting hash-only identities, not wholesale textual rereviews. Prior accepted review coverage is carried forward only by verified identity; no hidden missing suite becomes green. The external freeze binds every tracked source identity, the whole index/status, all new/untracked 09 candidates, referenced artifact/evidence identities and check receipts. Its path/hash are returned externally, avoiding self-reference. Writes stop at freeze; no staging, commit or checkpoint spawn precedes new coordinator authority.
