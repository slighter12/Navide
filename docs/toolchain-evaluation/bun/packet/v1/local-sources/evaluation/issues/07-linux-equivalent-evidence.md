# 07 — Obtain equivalent Linux compatibility and performance evidence

**What to build:** a real Linux baseline-versus-Bun result for the same daily workflows, public contracts, and performance gates validated on macOS, so that project readiness is supported by platform evidence rather than simulation.

Blocked by: 06 — Compare macOS end-to-end test performance (pass required, including passing macOS compatibility prerequisites). No dependency on 08.

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E4 — Linux.
Stories: 3–39 as applied to Linux.

## Acceptance criteria

- [ ] Obtain explicit authorization for the actual Linux execution environment and operations. No ticket approval authorizes hosted CI, runner provisioning, secrets, production workflows, or infrastructure changes. Check availability only through allowed real paths; document missing authority or a checked prerequisite instead of guessing or bypassing the gate.
- [ ] Use actual Linux execution, not mocked platform identifiers, shell/path simulations on macOS, or another platform's success. Record OS/architecture, source/dependency snapshot, candidate version, and resolved tools.
- [ ] Establish isolated Linux baseline and candidate copies with protected original sources/configuration, independent stores/build outputs, separate application/backend state, and safe experiment-owned process cleanup. Never clear terminal scrollback or operate the UI through automation.
- [ ] Execute the supported Node.js 22.23.2/pnpm 10.0.0 baseline before each comparison. Preserve the exact Bun version and platform-appropriate existing direct/transitive resolutions, security overrides, script-trust policy, auditing, and frozen-install reproducibility.
- [ ] Repeat the exact-version acceptance/rejection and minimal SDK/DOM Vitest checks from the macOS experiment without requiring mise or another contributor version manager.
- [ ] Execute equivalent aggregate typechecks, real public/official Plugin and application/native builds, safely isolated development readiness, full root/supplementary Vitest inventory, and applicable packaged Plugin lifecycle cases. Do not skip prerequisites or count absent/disabled fixtures as exercised evidence.
- [ ] Prove daily candidate workflows and nested helpers do not require external Node.js/pnpm. Keep allowed consumer Node.js/npm/pnpm probes separate, consume genuine packed artifacts, and validate the same Plugin Public Contract without new integration rules.
- [ ] Measure the full and both representative single-file test workflows on the same Linux machine for baseline and candidate, with matched cold/warm caches and at least five initial samples per condition. Include setup/pack/child costs, compare medians, enforce the more-than-10% slowdown limit, and add samples or report inconclusive variation as required.
- [ ] Record native/optional dependency, shell/path, permission, and executable differences without hidden upgrades, third-party patches/replacements, or weaker assertions. Any authorized helper adaptation stays inside experimental copies; invalidate and refresh affected earlier evidence if its source snapshot no longer matches.
- [ ] Deliver commands/environments, complete logs/exit codes, test and artifact inventories, dependency/security/runtime observations, consumer toolchains, timing data, and a pass/fail/blocked matrix. Preserve observed failures and distinguish them from unavailable execution evidence.

## Scope and handoff

This is an evaluation on Linux, not a migration or CI implementation. Missing Linux evidence prevents an adoption recommendation but does not prove Bun incompatibility. A gap needing forbidden adaptations stops the candidate and may feed 09. Passing 07 does not imply Windows passed; 08 is independently required for adoption.
