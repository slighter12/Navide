# Evaluate a Bun-only daily JavaScript toolchain

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted
Source issue: https://github.com/slighter12/Navide/issues/2

## Problem Statement

Navide contributors have experienced failures after tool upgrades and confusion from multiple local Node.js/pnpm installations. Contributor setup currently requires managing both tools, and changing the command prefix without removing their underlying dependency would not solve that problem. Test execution is also slow, but Bun making the existing Vitest workflows faster remains an unverified hypothesis.

The user wants an evidence-backed answer to whether daily JavaScript development can require only one pinned Bun version, while directly reusing existing packages and preserving the Plugin Public Contract and existing package-consumption behavior. No migration decision has been made. If Bun fails the agreed gates, the outcome is to defer/reject migration and leave the existing Node.js/pnpm toolchain unchanged, not to introduce a mixed daily toolchain.

## Solution

Specify, but do not execute, a staged isolated comparison between the supported Node.js/pnpm baseline and a Bun candidate. First assess the smallest macOS installation/runtime experiment, then validate the retained daily workflows, public-package compatibility, reproducibility, security controls, and performance. Obtain Linux and Windows evidence only after the macOS candidate passes and the execution path is separately authorized.

The target daily workflows are dependency installation, development startup, typechecking, existing Vitest tests, and application/Plugin builds. They must not require externally installed Node.js or pnpm. Existing non-JavaScript prerequisites remain unchanged. The Desktop Host and Electron's embedded runtime are not being replaced. Release packaging and CI may retain an explicitly isolated Node.js environment without making it a daily contributor prerequisite.

Use the two user-confirmed verification boundaries: actual daily workflow entrypoints, and public-package compatibility for existing consumers. The latter verifies that generated artifacts satisfy existing contracts; it does not dictate which tools external consumers use or introduce new integration rules.

Deliver a recorded adopt/defer/retain recommendation with actual evidence after execution is authorized. An adoption recommendation is not migration authorization; any rollout and production rollback design remain separate follow-up work.

## User Stories

1. As a contributor, I want an evaluation focused on upgrade failures, version ambiguity, and setup burden, so that tool replacement addresses my actual problems rather than a preference for a new command prefix.
2. As a contributor, I want daily JavaScript installation, development, typechecking, testing, and builds to need only Bun if adoption is recommended, so that I do not manage an additional Node.js/pnpm toolchain for those workflows.
3. As a contributor, I want the project to pin one exact Bun version, so that global upgrades do not silently change the project runtime.
4. As a contributor, I want a wrong Bun version rejected before installation/build side effects with actionable guidance, so that version mismatches do not first appear as downstream failures.
5. As a contributor, I want project Bun upgrades to require validation, so that a new version is not assumed compatible.
6. As a contributor, I want installing the specified Bun version to be sufficient without mise or another version manager, so that personal version-management preferences do not become project prerequisites.
7. As an evaluator, I want a fixed supported Node.js/pnpm baseline, so that Bun is compared with the intended existing toolchain rather than an unsupported global installation.
8. As an evaluator, I want both candidates to use the same recorded source snapshot and dependency versions, so that differences can be attributed to the toolchain rather than unrelated changes.
9. As an evaluator, I want independently writable experiment environments, so that dependency installation and build outputs cannot damage the primary working tree or the other candidate.
10. As the repository owner, I want source changes, dirty files, global tools, application profiles, and personal configuration preserved, so that evaluation does not disturb ongoing work or the running application.
11. As an evaluator, I want the exact Bun candidate version and executable identities recorded before comparison, so that the result can be reproduced without relying on a moving latest version.
12. As an evaluator, I want installation and dependency resolution checked before expensive workflows, so that a fundamental incompatibility stops the candidate early.
13. As a maintainer, I want the first comparison to preserve existing direct/transitive dependency versions, so that hidden upgrades do not masquerade as existing-package compatibility.
14. As a maintainer, I want every resolution difference and required upgrade reported separately, so that adopting Bun does not silently authorize dependency changes.
15. As a maintainer, I want existing security overrides and installation-script restrictions retained, so that compatibility is not obtained by weakening dependency safety.
16. As a maintainer, I want dependency auditing and frozen-lockfile reproducibility preserved, so that a different package manager does not remove existing assurance.
17. As an evaluator, I want actual runtime/package-manager evidence for daily commands and their subprocesses, so that successful Bun entrypoints cannot hide a fallback to external Node.js/pnpm.
18. As a contributor, I want the existing TypeScript and Vue typecheck workflows retained, so that removing a tool does not reduce validation.
19. As a contributor, I want the existing Vitest suites, environments, assertions, and test inventory retained, so that Bun compatibility is assessed without changing what the tests prove.
20. As a contributor, I want test build/pack/setup work included in compatibility checks, so that a fast runner does not hide an incompatible prerequisite.
21. As a maintainer, I want supplementary Plugin boundary suites explicitly accounted for, so that root test discovery omissions do not produce an incomplete pass claim.
22. As a contributor, I want public packages and official Plugins built through the existing dependency order, so that consumers use fresh distributable outputs rather than stale artifacts.
23. As a contributor, I want application builds, required native/package artifacts, and development startup assessed without bypassing prerequisites, so that the proposed daily toolchain works for the real project.
24. As an evaluator, I want development startup observed through process readiness, build results, and existing health seams, so that UI automation is unnecessary and exit status alone is not mistaken for a running application.
25. As a Plugin author, I want generated public packages to preserve exports, declarations, schemas, styles, peer dependencies, and documented subpaths, so that my existing integrations remain valid.
26. As a Plugin author, I want the public Plugin CLI's validation, packaging, and signature behavior to remain compatible, so that my development and distribution workflow is not broken by Navide's build-tool choice.
27. As a Plugin author, I want public artifacts to work in an independent consumer project using existing Node.js/npm/pnpm workflows, so that I am not forced to install Bun or rely on Navide's private source tree.
28. As a Plugin user, I want package installation and execution through the existing Desktop Host lifecycle to remain compatible, so that valid build outputs also remain usable by the Host.
29. As a maintainer, I want tool settings, scripts, and build/pack/launch helpers to be the only adaptation candidates, so that evaluation does not grow into product refactoring or third-party maintenance.
30. As a maintainer, I want a candidate needing a third-party fork, patch, replacement, or unapproved upgrade stopped and documented, so that the agreed direct-reuse goal remains meaningful.
31. As an evaluator, I want the smallest macOS experiment completed before broader platform work, so that an early failure avoids unnecessary execution cost.
32. As a contributor on macOS, Linux, or Windows, I want each platform to meet the same daily-workflow and compatibility gates, so that a macOS-only result is not presented as project-wide readiness.
33. As an evaluator, I want full-suite and representative single-file test timings measured end to end, so that build, pack, and subprocess costs are not excluded from claimed improvements.
34. As an evaluator, I want separate cold/warm-cache measurements with at least five initial samples per condition and median comparisons, so that results are not based on a favorable one-off run.
35. As a maintainer, I want more than 10% test-workflow slowdown to fail the performance gate, so that setup simplification does not cause a material daily performance regression.
36. As an evaluator, I want additional samples when variation is high, so that an unstable measurement is marked inconclusive rather than converted into a confident verdict.
37. As a contributor, I want comparable test performance to remain an acceptable outcome, so that optional speed gains do not displace the primary simplification goal.
38. As the repository owner, I want unresolved execution prerequisites and observed failures distinguished from demonstrated compatibility gaps, so that availability assumptions and missing evidence do not become false conclusions.
39. As the repository owner, I want authorization to remain separate from specification readiness, so that an agent-ready document does not trigger experiments, external CI operations, or migration.
40. As the repository owner, I want an evidence-backed adopt/defer/retain recommendation and independently verifiable follow-up work only if appropriate, so that I can decide whether to authorize a migration.
41. As the repository owner, I want failed evaluation to leave the current Node.js/pnpm toolchain unchanged, so that the evaluation cannot silently introduce partial adoption or an alternative cleanup project.

## Implementation Decisions

These are decisions about the evaluation design, not authorization to implement a migration or modify production modules.

- Use isolated baseline and candidate copies of the same source snapshot. Record source revision, relevant working-tree state, dependency identities, runtime versions, operating system, architecture, and executable locations. Do not create branches/worktrees, stash, revert, or remove dirty work as part of specification publication.
- Use Node.js 22.23.2 and pnpm 10.0.0 as the fixed supported comparison baseline. Select and record one exact Bun candidate version before an authorized experiment; never benchmark a moving latest alias. A personal tool manager may be used by its owner, but is not part of the proposed contributor solution.
- Treat the supported baseline as an environment to measure, not as a green result already established by this specification. If it fails, record the real failure and mark the dependent comparison blocked; do not repair unrelated baseline code or weaken tests under this scope.
- Prioritize Bun replacing pnpm. Also evaluate the minimum runtime/launch adaptations needed to remove external Node.js from the daily workflow. Default shebang behavior and nested subprocesses must not be assumed to use Bun merely because their parent command does.
- Retain Vitest, existing dependency versions, frontend/framework choices, the Desktop Host, and all public contracts. Adaptation candidates are project tool configuration, script orchestration, and test build/pack/launch helpers inside a separately authorized experimental copy. No production code change is granted here.
- Determine whether a canonical contributor entrypoint can enforce the exact Bun version before installation/build side effects without a required version manager or silent version switching. Unsupported enforcement is an evaluation gap, not permission to relax the requirement.
- Compare complete installed dependency inventories, including versions, package identities/integrity where recorded, effective security overrides, and applicable platform-specific optional dependencies. Preserve platform-appropriate baseline resolution rather than forcing one platform's native dependencies onto another.
- Changing lockfile representation or installation layout is allowed in the experimental candidate; silently resolving different versions or broadening install-script trust is not. An incompatibility that needs an upgrade or third-party patch stops the affected candidate and is reported for a separate decision.
- Independently isolate writable package caches, build outputs, application profiles, backend data, and user configuration. Backend startup may install external Agent CLI hooks, so a data-directory override alone must not be treated as full isolation. Use an execution environment that protects real user configuration before launching startup probes.
- Establish absence of external Node.js/pnpm with controlled executable visibility and actual process/runtime observations, including build/pack/test subprocesses. Distinguish Electron's embedded runtime and permitted non-JavaScript tools from forbidden daily external-runtime fallback. Do not modify globally installed executables to construct this evidence.
- Reuse existing public-package build order: contracts, then SDK, then UI. Build and consume actual distributable artifacts; private source aliases, stale distributions, and another Plugin's source are not substitutes for package compatibility.
- Keep generated-package consumer validation separate from the Bun-only daily environment. External consumers may use their existing tools; compatibility observations do not impose a new consumer toolchain. Do not change the Plugin Public Contract, Host Capability Limits, Manifest Permissions, Package-version Grants, or Execution Policy to make artifacts work.
- Allow the explicitly isolated release/CI Node.js exception, but do not modify release/CI infrastructure or execute external workflows under the present authorization. Testing daily Bun compatibility in an automated environment does not waive the no-external-Node.js daily gate.
- Sequence macOS first, then Linux and Windows. Do not infer one platform's pass from another platform's path/shell simulations. An unavailable or unauthorized execution path is missing evidence, not a proven incompatibility or permission to waive a gate.
- Report semantic contract compatibility rather than requiring byte-identical compiler bundles. Preserve existing deterministic package/signature behavior where it is part of the CLI's contract and tests.
- Make no new persistence schema, Host capability interface, Plugin API, or product behavior part of this evaluation. Do not introduce an abstraction layer merely to accommodate hypothetical future toolchains.

## Testing Decisions

### Confirmed verification boundaries

1. **Daily workflow entrypoints:** execute the real installation, non-watch typecheck/test/build/startup workflows in isolated environments and observe their behavior, products, runtime identities, failure modes, and end-to-end duration. The version-mismatch probe belongs at the same contributor entrypoint, before side effects. New instrumentation, if needed, stays at this process boundary rather than spreading new internal testing seams across modules.
2. **Public-package compatibility for existing consumers:** consume actual packed artifacts in an independent project and exercise existing public exports/types/peer requirements, Plugin CLI operations, and applicable Host installation/execution cases. Do not require the consumer to use Bun or redefine its integration contract.

A good test asserts externally observable behavior: required products exist and are usable, types resolve, public operations succeed or deny as previously specified, the expected tests actually ran, a wrong runtime is rejected before mutation, and the observed process tree matches the declared runtime boundary. Exit code zero or metadata comparison alone is insufficient. Preserve existing unit-test assertions; this evaluation does not redesign their internal mocking strategy.

### Existing prior art and coverage inventory

| Existing source or suite | Reuse in the evaluation |
| --- | --- |
| Root `package.json` and `vitest.config.ts` | Inventory actual scripts, dependency settings, Vue transformation, per-file DOM environments, aliases, and selected tests. |
| `tests/support/publicPackagesSetup.ts` | Include existing cached public-package build and real tarball setup; account for nested package-manager calls and end-to-end cost. |
| `packages/plugin-sdk/src/index.test.ts` | Retain SDK behavior checks; use as an initial non-DOM single-file performance workload. |
| `packages/plugin-ui/src/editor/EditorPane.test.ts` | Retain the existing Vue/happy-dom component suite; use as an initial DOM single-file performance workload. |
| `packages/plugin-sdk/bin/navide-plugin.test.ts` | Reuse real CLI validation, canonical archive, signing/verification, and negative-case behavior with existing isolated fixtures. Do not publish to a real registry. |
| `src/main/plugins/pluginExternalWorkspace.test.ts` | Reuse packed public artifacts, independent consumer types/build/package behavior, portable workers, and Host capability denial. |
| `plugins/navide-git/tests/compositionBoundary.test.ts` | Reuse independent Vue consumer checks based on packed packages rather than private source imports. |
| `plugins/navide-mini-ide/tests/packageBoundary.test.ts` and `plugins/navide-mini-ide/vitest.config.ts` | Explicitly cover copied Mini-IDE builds and portable Monaco workers; these tests are not all selected by the root include list. |
| `plugins/navide-plans/tests/packageBoundary.test.ts` | Retain the existing production package and public-API boundary checks. |
| `tests/integration/plansPackagedRoundtrip.test.ts`, `src/main/plugins/pluginBackendHost.test.ts`, and `src/main/plugins/pluginBackendSupervisor.test.ts` | Reuse applicable packaged Plugin lifecycle/roundtrip cases with their actual fixtures and flags, not skipped-default runs claimed as execution evidence. |
| `src/main/index.ts` and `backend/agent_team_backend/app.py` startup behavior | Account for Electron profile isolation, separate backend data, and external Agent CLI hook-install side effects before any authorized launch. |

The existing external-consumer suites sometimes reuse installed third-party packages or pnpm-specific layout helpers. They are useful prior art, not proof that Node.js/npm/pnpm consumer workflows have already been validated for Bun-produced artifacts. Record which managers actually installed/consumed each artifact; validate the claimed existing workflows rather than inferring them from a tarball manifest. Any helper adaptation must preserve the original assertions and artifact independence.

### Stage gates and dependency ordering

| Stage | Entry condition | Required evidence and completion condition | Story coverage |
| --- | --- | --- | --- |
| E0 — Baseline and isolation | Separate authorization for the bounded local experiment | Record the exact source/dependency/tool identities; independent writable baseline/candidate copies; protected original tree, global tools, profiles, and user configuration; executed supported baseline results. Do not advance a comparative claim from an unverified or failing baseline. | 7–11, 38–39 |
| E1 — Minimal macOS candidate | E0 ready and baseline prerequisites verified | Frozen dependency installation/repeatability, matching dependency resolution and security controls, actual Bun runtime identity, exact-version acceptance/rejection behavior, and retained initial SDK/DOM Vitest cases. Stop on prohibited adaptations or demonstrated external-runtime dependency. This is a first feasibility result, not adoption readiness. | 3–6, 12–17, 19, 29–31 |
| E2 — Complete macOS compatibility | E1 passes | Actual aggregate typechecks, full root non-watch Vitest inventory, supplementary Mini-IDE suites, public/official Plugin and application builds, applicable packaged lifecycle cases, safely isolated development readiness, and packed-artifact consumer checks. Existing skipped fixtures are not counted as exercised behavior. | 18–28 |
| E3 — Performance | E2 passes with matched validation content | Full root test workflow and both selected single-file workloads, cold/warm conditions, five initial samples per candidate/condition, raw durations and exit codes, medians, slowdown calculations, and extra sampling when inconclusive. No required workload may exceed the agreed 10% slowdown gate. | 33–37 |
| E4 — Linux and Windows | macOS E1–E3 pass and platform execution separately authorized | Equivalent baseline/candidate records and E1–E3 gates on actual Linux and Windows execution environments. Record genuine native/optional dependency and shell/path differences; do not substitute platform simulations for real results. | 32, 38–39 |
| E5 — Recommendation | Evidence collected, or a stop/blocker is recorded | A workflow/platform/contract matrix of pass/fail/not-run/blocked states, supported benefits and costs, identified gaps, an adopt/defer/retain verdict, and conditional follow-up work. Adoption requires every mandatory gate, not just the macOS experiment. | 1–2, 29–30, 38–41 |

A stop condition must name the executed workflow or concrete evidence that revealed the gap. A missing tool, inaccessible runner, or unauthorized external operation must be exercised/checked through the available real path before being reported as an availability blocker; do not guess. No requirement is waived because its evidence is expensive or unavailable.

### Reproducibility, security, and runtime checks

- Perform clean installation and repeat frozen-lockfile installation in the candidate; compare the resulting inventory with the baseline for that platform and confirm the frozen run does not silently rewrite the lockfile or resolve upgrades.
- Verify effective override enforcement and which dependency lifecycle scripts may execute; do not equate a successful install with retained policy. Retain actual dependency audit capability and distinguish pre-existing findings from changed coverage or newly introduced risk.
- Trace or otherwise observe the actual executables/runtimes used by daily entrypoints and their build, pack, test, and helper children. A renamed command or Bun parent process does not prove a Bun-only workflow.
- Test contributor entrypoints with the selected Bun version, a mismatched version, and competing global tool visibility. Mismatch rejection must precede installation/build mutation and must not auto-upgrade/downgrade or require a version manager.
- Retain required Python/uv/native build prerequisites. Do not use skip-backend-build options or missing-fixture skips to label the complete development/build contract compatible.
- Verify public artifacts resolve without Navide private-source aliases and preserve their documented schemas, declarations, exports/subpaths, styles/workers, peer requirements, and CLI semantics. Exercise applicable existing archive and Host lifecycle behavior rather than assuming that type compatibility alone proves a working Plugin.
- Keep existing authorization/signature denial cases; toolchain adaptation must not widen Host authority or Plugin permissions.

### Performance method

- Compare supported Node.js/pnpm and pinned Bun on the same machine, source snapshot, dependency versions, test inventory, build mode, environment variables, and equivalent parallelism settings. Record the exact invocations and cache conditions.
- The required initial workloads are the full root Vitest workflow, the existing SDK single-file suite, and the existing Vue/DOM single-file suite. Supplementary suites remain mandatory compatibility evidence even when not part of the root performance workload; no root/full-coverage label may imply they were measured when they were not.
- Cold means the workflow's generated build/setup/test caches are reset consistently in both isolated candidates; installed dependencies and the frozen dependency graph remain controlled. Warm means the same valid prerequisite outputs/caches are available in both candidates. Document exactly what was reset or retained; installation is evaluated separately and is not hidden inside only one candidate's test timing.
- Start with five recorded runs for each runtime/workload/cache condition. Include real build/pack/global-setup/subprocess costs, stdout/stderr, exit status, selected/passed/failed/skipped inventory, and wall time. Separate any diagnostic phase timing from the adoption metric.
- Calculate slowdown as the Bun median divided by the baseline median, minus one. More than 10% slowdown in a required matched condition fails the gate; exactly 10% does not exceed it. Comparable or faster execution is acceptable; no minimum speedup is required.
- Report dispersion and outliers. If variation prevents a defensible interpretation, interleave/add runs and mark the result inconclusive until it supports a verdict. Do not discard failures or cherry-pick runs.

### Startup and human verification

Prefer automated process readiness, build output, and existing backend/Plugin health probes. Use distinct application profiles, backend storage, and protected user-configuration environments; do not reuse the user's running backend. Bound startup probes and terminate only the experiment's own processes.

No screenshots, synthetic clicks, AppleScript, terminal scrollback clearing, or DevTools paste requests are permitted. Reserve genuinely visual/UI judgments for the user and provide a consolidated manual checklist if such evidence is required; do not claim UI acceptance from process readiness alone.

### Required evidence record

For each stage and platform, record source and dependency identities, runtime/manager versions and resolved executables, approved scope, isolation configuration, actual command/cwd/environment, exit code, full logs, test inventory, observable outputs, dependency/security comparisons, consumer toolchains exercised, timing samples/statistics, and pass/fail/not-run/blocked status. Redact secrets and keep generated credentials/keys isolated. Record results only after execution; do not turn source inspection or vendor documentation into a runtime pass.

## Out of Scope

- Executing any experiment, installation, test, build, startup, benchmark, or platform operation under this specification-writing assignment.
- Production migration, rollout, release/CI configuration changes or execution, global installation changes, branches/worktrees, commits, pull requests, and further GitHub issue changes without separate authorization.
- A mixed daily Bun/Node.js/pnpm toolchain presented as achieving the target, or a required mise/other version-manager contributor dependency.
- Replacing Vitest with Bun's test runner, reducing coverage, loosening assertions, skipping failures, or using stale/missing artifacts to manufacture a pass.
- Dependency upgrades, third-party forks/patches/replacements, unrelated refactors, or automatic remediation of baseline failures.
- Replacing Vue/Vite, the Desktop Host, or Electron's embedded runtime; rewriting the Python backend, Agent CLI Integration, or Agent CLI Runtime.
- New public APIs, Manifest Permissions, Host capabilities, Execution Policy behavior, schema changes, or new rules imposed on external Plugin authors.
- Publishing packages to real registries, exercising real user credentials, or modifying external consumers' projects.
- Treating contractual compatibility as a guarantee of every existing released binary without the applicable artifact/runtime evidence.
- Implementing Node.js/pnpm version-management cleanup when Bun fails; that requires its own proposal and authorization.

## Further Notes

### Authority and readiness

The user explicitly requested only an evaluation experiment specification. Both high-level verification boundaries have been confirmed. `Triage: ready-for-agent` means the specification is sufficiently defined for ticket splitting; it does not grant execution authority. `Status: open` means no experiment has begun, not that implementation is automatically actionable.

### Blocking decisions

None for specification publication: the product scope, adoption gates, and verification boundaries are confirmed. Exact candidate Bun version, viable version-enforcement mechanics, actual compatibility, runner availability, and benchmark behavior are findings for the authorized evaluation rather than silently assumed answers.

### Execution gates

Execution remains blocked by the absence of explicit experiment authorization. Local experiment authorization must identify its bounded stage scope; external platform/CI execution needs its own explicit authorization. Neither specification readiness nor later ticket creation lifts these gates.

The specification is published to the configured repository-local tracker, not posted as a replacement GitHub issue body. The source GitHub issue remains the agreed discussion record; this assignment does not authorize another external update.

### Source evidence and ownership boundaries

- Source issue: https://github.com/slighter12/Navide/issues/2.
- Discussion record: `.scratch/bun-toolchain-evaluation/discussion.md`; published discussion body snapshot: `.scratch/bun-toolchain-evaluation/issue-body.md`.
- Source revision inspected for this specification: `3a77bc294822dd4f0900b0fb2942f08c3371f104`. The working tree contains unrelated local agent-file changes and untracked ADR work; these are not part of this specification's writes. A future experiment must capture its actual execution snapshot rather than treating this inspection revision as a runnable result.
- Domain vocabulary: `CONTEXT.md`. Architecture and contribution rules: `docs/en-US/architecture.md` and `CONTRIBUTING.md`. Adjacent Agent CLI ADRs retain their own evaluation scope and are not expanded by this toolchain evaluation.
- Local tracker and triage representation: `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.
- Bun runtime documentation: https://github.com/oven-sh/bun/blob/main/docs/runtime/index.mdx#--bun.
- Bun Node.js compatibility documentation: https://github.com/oven-sh/bun/blob/main/docs/runtime/nodejs-compat.mdx.
- Vitest guide at the repository's declared version floor: https://github.com/vitest-dev/vitest/blob/v3.2.7/docs/guide/index.md.

No new ADR is warranted: no migration technology choice has been accepted. General toolchain/testing terms are not added to the domain glossary. This document records a proposed experiment and its acceptance criteria, not runtime results or shipped capability claims.

### Follow-up boundary

After explicit authorization, evaluation tickets may follow E0–E5 dependency edges. A macOS failure stops the candidate and leads to a documented defer/retain recommendation unless the user separately changes scope. A full pass permits a recommendation only. Any migration specification must separately address release/CI exceptions, rollout, production rollback, and ownership of authoritative configuration/lockfiles.
