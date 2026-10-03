# 02 — Validate the minimal Bun-only macOS path

**What to build:** an isolated contributor path that installs the unchanged dependency graph and runs the initial existing SDK/DOM tests with one pinned Bun version, rejecting mismatched versions and exposing any forbidden Node.js/pnpm fallback.

Blocked by: 01 — Establish the isolated macOS environment and minimal baseline (passing baseline and isolation evidence required).

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E1.
Stories: 3–6, 12–17, 19–20, 29–31, 38–39.

## Acceptance criteria

- [ ] Obtain explicit authorization covering the isolated Bun experiment and its limited tool-setting/script/build-pack-launch-helper adaptations. Do not modify the primary working tree or globally installed tools.
- [ ] Use the recorded exact Bun candidate and the same source/dependency snapshot as 01. Record the executable actually used; project Bun upgrades require fresh validation rather than a latest alias.
- [ ] Demonstrate that installing the specified Bun version is sufficient for the evaluated JavaScript setup without a required Node.js/pnpm installation or mise/other version manager.
- [ ] Evaluate a canonical contributor entrypoint that accepts the correct version, rejects a mismatched version before installation/build mutation with actionable guidance, and does not silently download or switch versions. Include competing global-tool visibility in the probe. Failure to enforce the requirement is a reported gap, not a reason to relax it.
- [ ] Execute clean installation followed by frozen-lockfile reinstallation. Preserve direct/transitive versions and applicable package identities/integrity; record every resolution difference and verify the frozen run does not silently rewrite its lockfile or upgrade dependencies.
- [ ] Verify effective security overrides, lifecycle-script permission restrictions, local package-link behavior, and dependency auditing. Successful installation alone does not prove these semantics. Retain assurance without broadening script trust or hiding existing findings.
- [ ] Execute the same public SDK and EditorPane Vue/happy-dom suites as 01 using Vitest, unchanged assertions, and real build/pack/global setup. Do not switch to Bun's test runner or treat omitted/skipped cases as passing evidence.
- [ ] Prove actual workflow and helper-child runtime identities with controlled executable visibility and process observations. A Bun parent process or changed command prefix is not sufficient; external Node.js/pnpm must not be required by this daily path.
- [ ] Keep adaptations confined to project tool configuration, script orchestration, and test build/pack/launch helpers. Stop if success requires a third-party fork, patch, replacement, unapproved dependency upgrade, weakened assertion, or daily external-runtime fallback.
- [ ] Deliver pass/fail/blocked evidence with commands, environment, exit codes, complete logs, dependency/security comparisons, test inventory, and the minimal adaptation record. A pass establishes local minimal feasibility only, not complete macOS or project-wide readiness.

## Scope and handoff

No full migration, platform execution, production CI/release change, package publication, or unrelated baseline repair is authorized. Preserve the existing Plugin Public Contract and Desktop Host. Passing this ticket permits 03 to become dependency-ready only; a stop or concrete blocker may instead feed the early-stop route in 09.
