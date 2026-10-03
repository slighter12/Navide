# 04 — Validate the full macOS test inventory and Plugin lifecycle

**What to build:** an executed, coverage-preserving macOS Vitest and packaged-Plugin verification result that demonstrates the Bun daily test path exercises the same behavior as the supported baseline.

Blocked by: 03 — Validate macOS typechecks, builds, and isolated development startup (pass and required artifacts available).

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E2 — test/lifecycle slice.
Stories: 19–21, 26, 28–30, 38–39.

## Acceptance criteria

- [ ] Obtain explicit authorization for the bounded test/lifecycle stage, including any fixture builds and existing guarded integration cases it actually needs.
- [ ] Execute the supported baseline's complete root non-watch Vitest workflow before comparing the candidate. Record actual discovered, selected, passed, failed, and skipped cases; stop the affected comparison if the baseline is not green.
- [ ] Execute the candidate with the same Vitest suites, assertions, environments, Vue transforms, and effective inventory. Do not replace Vitest, skip failures, loosen assertions, or infer a full-suite pass from a filtered run.
- [ ] Include existing public-package build/pack/global setup and inspect runtime identities of subprocesses. Adapt only allowed orchestration/helpers; the daily candidate test path must not require external Node.js/pnpm.
- [ ] Execute supplementary Mini-IDE package-boundary suites explicitly rather than assuming the root configuration includes them. Account for the existing Git and Plans boundary coverage as well.
- [ ] Execute applicable packaged Plugin roundtrip, backend Host, and supervisor lifecycle cases with the actual required native artifacts, fixtures, and opt-in flags. A default run that skips those cases is not their execution evidence.
- [ ] Retain public Plugin CLI validation, canonical archive/signature behavior, and existing negative authorization/signature cases. Use isolated fixtures and local services only, not a real registry or personal credentials.
- [ ] Record observable package installation/execution and lifecycle outcomes; do not equate manifest/type compatibility alone with a working Plugin or widen Host capabilities, Manifest Permissions, grants, or Execution Policy to make a case succeed.
- [ ] Distinguish the Bun-only daily checks here from separately isolated Node.js/npm/pnpm external-consumer probes in 05. Any existing consumer-style test inside the daily suite retains its assertions and artifact independence while using allowed daily helper orchestration; do not silently drop it or treat 05 as permission for a Node.js fallback here.
- [ ] Deliver comparable coverage inventories, command/environment details, complete logs, exit codes, runtime/process evidence, fixture/artifact identities, and pass/fail/blocked outcomes. Report gaps without repairing unrelated baseline code or violating the unchanged-dependency constraint.

## Scope and handoff

No project-wide adoption, external platform operation, production mutation, test redesign, package publication, UI automation, or terminal scrollback clearing is included. This verifies the macOS daily test/lifecycle slice only. It unlocks the performance ticket only together with passing public-consumer compatibility from 05.
