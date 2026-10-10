# Ticket 03 — E2 console continuation, version 2

## Decision

**Console typechecks and builds succeeded; overall ticket03 remains HOLD for isolated development readiness.** Fresh matched-current-source Node and Bun aggregate typechecks and full application builds exited 0. The original supported Node console positives remain bound separately. The v1 development failure is an external-isolation startup failure, not demonstrated Bun or supported-Node infeasibility. No GUI retry, VM provisioning, permission expansion, sandbox-disabling flag, adoption, timing comparison or later-ticket execution occurred.

This is an additive continuation, not a rewrite of `../report.md`, `../evidence.json` or the sealed external `ticket-03` root. Their earlier NOT RUN statements describe their historical cutoff. The coordinator approved paired console continuation and the fresh sibling location; startup protection changes remain proposals only. There is no new tracker/hosted status, commit or publication.

## Authority and binding

- Worker remains the same NEW source-owner session `01a111a8-810e-701e-b0ae-a1db8e57557b`, `openai-codex/gpt-6.1-sol/high`.
- Shared worktree: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch `eval/issue-2-bun-toolchain`, HEAD `5a6b78ce70b2ef775a5b5ba201a4474a8574c342`. Index diff is empty; no new branches, worktrees or Git execution copies.
- Original issue baseline remains `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`, Node 22.23.2/pnpm 10.0.0. New original-source aggregate control exited 0; its v1 full application build exit 0 is retained. It is distinct from the fresh same-graph Node controls, which use copied Bun-installed dependencies and are NOT independent pnpm bootstrap proof.
- Bun remains exactly 1.4.2. Runtime executable visibility affects experiment children only. Candidate environments contain no external Node/pnpm fallback; the private `node` alias targets that same owned Bun binary.
- External continuation root: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-03-console-v2`. Evidence paths below are relative to it. Snapshot name: `freeze-v2`; its external anchor binds the complete current WIP, including all four untracked reports/evidence files, without a circular self-hash here.
- Accepted Checkpoint A report: `checkpoint-A/integrated-correction01-candidate-v2.md`, SHA-256 `7ae0f046a89c743c5dec25d6051a8c4b1c02fa2b2d9ae9a5a8eaaf2876301daf`, relative to the external evaluation base. Both actual same-byte endorsements and accepted source ancestry remain bound, not inferred from a badge.

## Nine saved-ticket criteria

| Criterion | Actual status | Evidence and boundary |
|---|---|---|
| 1. Authority, snapshots and isolation | Qualified | Owned non-Git copies, independently assigned HOME/XDG/config/cache/tmp/store/data/profile paths; new real denial probes and exact owned-path-only profile substitutions. No permission semantics broadened. Startup remains incompatible with the current outer/native sandbox combination. |
| 2. Existing aggregate typechecks, baseline first | Console PASS | `baseline-aggregate-typecheck-v2`, `matched-node-current-aggregate-typecheck`, `candidate-current-aggregate-typecheck`: exit 0. Node/integration, public contracts→SDK→UI, three Plugin Vue checks, web and public checks retained. |
| 3. Public/official/application builds and observations | Console PASS; observations qualified | Fresh `matched-node-current-application-build` and `candidate-current-application-build`: exit 0. Separate external-observation typecheck/build runs also exited 0; not ordinary-run equivalence or exhaustive telemetry. |
| 4. Native/Python prerequisites and products | Console PASS | Real Plans production backend binary, pane-helper bundle and fn-key helper. Independently copied owned uv/Python inputs, Python 3.12.11, existing locked backend sync. No skip-backend-build, release package or optional STT-model build. |
| 5. No daily external Node/pnpm | Qualified for observed console scope only | Fresh runtime identities plus sampled candidate child executables are owned Bun/native tools, not external Node/pnpm. Canonical private alias and nested Bun shell rewriting preserve existing recipes. Short-lived/reparented processes can evade sampling; startup/install/full daily closure is not established here. |
| 6. Bounded isolated development probe | HOLD / not retried | v1 original Node launch retained unchanged; Bun launch NOT RUN. The small startup exception proposal is saved in the new root, not executed. |
| 7. Readiness and owned cleanup | HOLD | v1 initialization EPERM/GPU fatal, no application/Plugin readiness. New observed console runs have zero remaining recorded owned PIDs; no exhaustive detached-process absence claim. |
| 8. Attributable products for 04/05 | Produced and preserved; acceptance pending | Each fresh Node/Bun ordinary product inventory has 1,105 entries. All 58 public `.d.ts` files match by path and bytes. No baseline substitution, external consumer or Plugin lifecycle acceptance. |
| 9. Results, streams, identities and limits | Bound continuation | 46 command receipts with matching complete raw streams; ordinary, diagnostic, expected-negative and externally observed runs remain distinguishable. Frozen source/evidence version awaits independent review. |

## Actual failure → minimum correction → paired verification

1. The unchanged canonical entry rejected aggregate typecheck as outside its ticket02 allowlist. A direct Bun aggregate then exited 127. Raw stderr/command expansion showed nested `pnpm run` rewritten to `bun run`, followed by unresolved `env node`; it did NOT prove an absent pnpm executable. The worker's earlier pnpm attribution was corrected, not retained as a product diagnosis.
2. An owned diagnostic alias to the actual pinned Bun executable allowed the unchanged public build to complete. Its self-report confirmed Bun 1.4.2, not external Node. The canonical `run` path now creates a private temporary `node` alias to `process.execPath`, passes it only to the guarded child environment, and removes it after synchronous completion. Version and argument rejection precede alias creation. Existing install/pack/audit recipes, dependency versions, trust and public build order are unchanged. No package-manager dispatcher or third-party patch was added.
3. Direct upstream Plugin Vue checking under Bun returned 0 for an intentionally invalid `.vue` fixture while Node returned 2 with TS2322. This false success is preserved. The three standalone and aggregate Plugin Vue routes now use the existing explicit adapter; its Node branch still delegates to upstream vue-tsc. No assertions, includes or failing cases were removed.
4. Fresh same-source, separately copied same-graph Node controls precede the Bun aggregate and full-build comparisons. Five configurations—Git, Plans, Mini IDE, renderer and public UI—each reject the same bad SFC under both runtimes: ten exits 2 with the actual diagnostic. Fixtures existed only in owned execution copies, were removed, and public positive products were restored. See `preflight/current-vue-negative-controls.json`.
5. Fresh guards reject Node execution, unsupported dev dispatch and weakened install flags; actual Bun 1.4.2 passes `check`. A required-version-1.4.3 fixture rejects actual 1.4.2; this is NOT execution of a second Bun release. Temporary alias leftover counts are zero. Existing A cache correction and its controls are retained, not relabeled as new E2 cache experiments.

## Source cost and compatibility boundary

Exactly two build/tool source paths change: `package.json` (three Plugin Vue routes, both standalone and aggregate) and `scripts/bun-evaluation.mjs` (bounded console allowlist and private guarded alias lifecycle). The explicit compiler adapter itself, applications, tests, package exports/types/peers, dependencies/overrides, script trust and lockfiles are unchanged. Node's real good/bad current-source controls pass; public consumer CLI/install compatibility still belongs to later independent validation.

The alias is evaluated on macOS only; symlink privileges/semantics on real Windows and Linux remain untested. Private compiler API exposure now covers three additional Plugin configurations and remains a maintenance cost, not an upstream fix. Arbitrary scripts and `dev` are not added to the canonical allowlist. Bypassing the canonical entry still bypasses its pin; no global tool/config modification was made.

## Products, runtimes and attribution

`artifacts/matched-node-ordinary/index.json` and `artifacts/candidate-ordinary/index.json` each bind public contracts (51 entries, 12 declarations), SDK (4, 1), UI (106, 45), Plugin products (707), application `out` (232), pane helper (4) and fn-key (1). Public bytes were last written by the ordinary positive restore after negative controls; other products by the ordinary full application build. Preservation happened BEFORE external-observation rebuilds. Counts are not prior A's 116 declaration-related rows and are not consumer success.

Separate external reachable-owned-tree observations recorded 23/24 Node/Bun typecheck identities and 67/67 build identities. Candidate sampled JS executables were the owned Bun binary; native children included esbuild, uv, owned Python, clang/ld/lipo/codesign as actually sampled. Node sampled its owned Node binary and native tools. Numeric observation errors remain: 5/5 typecheck and 21/15 build. Zero recorded owned remnants was checked without global scans or foreign argv/environment logging. Fast/detached children, every producer environment, per-file emission and exhaustive execution closure remain uncredited. No observer-neutrality, performance median or cross-cohort cache inference.

An evaluation-only Node preload first failed because the immutable profile denied writes to collector-owned `artifacts`. A later probe moved output into the already writable owned copy without permission changes: Node loaded it; Bun ignored NODE_OPTIONS and produced no preload receipt. Those probes are retained; the credited paired observations use NO runtime preload. They are not a repaired product failure or proof that Bun loaded that observer.

Final verification matched 57,385 selected frozen dependency entries and 2,825 original / 2,872 candidate / 2,872 current-control source files, with only the two declared current-source overlays. This does not certify all dirty original files, credentials, every frozen-root byte, historical process effects or continuous immutability. Frozen old producers were never executed; inspected source portions were copied into new owned producers and bound separately.

## Preserved qualifications and missing coverage

All eleven A residuals remain individually carried: duplicated configuration; unsupported candidate pnpm bootstrap; deduplicated alias scope; private compiler API/pinned maintenance; canonical-only version enforcement; frozen-range semantics; historical unexplained PID 40769; Electron lifecycle gap; local-path publication exposure; frozen dompurify advisory severity; inferred audit-ID mapping. The original Node baseline was instrumented, equivalence was not established, and original artifact selection was 1/15. Same-graph Node controls are distinct from bootstrap. Prior Node116 attribution is verified history, not fresh current-source/full-platform validation; these new products are independently scoped.

A W1–W5, R1/R2, worker appendix and coordinator failures remain by reference to the exact accepted report. V1 failed loader/sandbox/path/schema/reader/format interpretations and startup errors remain untouched. New failed path/schema/symlink readers, source-symlink traversal refusals and denied preload placement are retained as process failures, not Bun/product failures. The whole-root v1 seal caused the approved sibling-location exception; it was not reopened or retroactively reclassified. Zero active A findings is not whole-issue adoption.

Not run: GUI/VM/new startup, backend/Plugin/renderer health, root Vitest/backend/supplementary suites, independent public consumers, install/update/start-stop Plugin lifecycle, fresh install/audit/cache experiments, performance medians, release packaging, external CI, Linux/Windows, manual UI acceptance or production migration. Tests and later tickets were not borrowed to fill these gaps. No UI automation/screenshots/DevTools/terminal clearing, other-backend reuse, global profile/config change, push, PR, merge or hosted update.

## Handoff

The small English exception proposal is `startup-exception-proposal.md` (SHA-256 `0c2cdcf488b2147278429b7c53fe54ff400814eb5f3a2521075640f66d139449`). It requests separately reviewed disposable offline macOS guest readiness, preserving native Electron sandboxing and no host shares/credentials/network; it states hardware/observability/equivalence losses. Requirements are not capabilities already proved. No host change was executed.

Freeze the entire six-path WIP plus complete continuation evidence/products/producers as version 2, preserving v1 and all original failures. Independent reviewer remains read-only; source owner owns any bounded corrections. Overall HOLD is for readiness, not failed console feasibility. Await review and separate exact commit dispatch; no ticket04 authority follows.
