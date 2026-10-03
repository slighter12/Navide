# 08 — Obtain equivalent Windows compatibility and performance evidence

**What to build:** a real Windows baseline-versus-Bun result for the same daily workflows, public contracts, and performance gates validated on macOS, including Windows-specific shell, path, native-artifact, and permission behavior.

Blocked by: 06 — Compare macOS end-to-end test performance (pass required, including passing macOS compatibility prerequisites). No dependency on 07.

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E4 — Windows.
Stories: 3–39 as applied to Windows.

## Acceptance criteria

- [ ] Obtain explicit authorization for the actual Windows execution environment and operations. No ticket approval authorizes hosted CI, runner provisioning, secrets, production workflows, or infrastructure changes. Check availability only through allowed real paths; document missing authority or a checked prerequisite instead of guessing or bypassing the gate.
- [ ] Use actual Windows execution, not mocked platform identifiers, command-string simulations on macOS/Linux, or another platform's success. Record OS/architecture, source/dependency snapshot, candidate version, and resolved tools.
- [ ] Establish isolated Windows baseline and candidate copies with protected original sources/configuration, independent stores/build outputs, separate application/backend state, and safe experiment-owned process cleanup. Do not change global tools, clear terminal scrollback, capture screenshots, or automate UI actions.
- [ ] Execute the supported Node.js 22.23.2/pnpm 10.0.0 baseline before each comparison. Preserve the exact Bun version and platform-appropriate existing direct/transitive resolutions, security overrides, script-trust policy, auditing, and frozen-install reproducibility.
- [ ] Repeat the exact-version acceptance/rejection and minimal SDK/DOM Vitest checks without requiring mise or another contributor version manager. Include competing executable visibility and Windows command resolution in runtime evidence.
- [ ] Execute equivalent aggregate typechecks, real public/official Plugin and application/native builds, safely isolated development readiness, full root/supplementary Vitest inventory, and applicable packaged Plugin lifecycle cases. Do not bypass native prerequisites or count absent/disabled fixtures as exercised evidence.
- [ ] Prove daily candidate workflows and nested helpers do not require external Node.js/pnpm. Keep allowed consumer Node.js/npm/pnpm probes separate, consume genuine packed artifacts, and validate the same Plugin Public Contract without new integration rules.
- [ ] Measure the full and both representative single-file test workflows on the same Windows machine for baseline and candidate, with matched cold/warm caches and at least five initial samples per condition. Include setup/pack/child costs, compare medians, enforce the more-than-10% slowdown limit, and add samples or report inconclusive variation as required.
- [ ] Record actual Windows native/optional dependency, shell quoting, path/temporary-directory, permission, and executable behavior. Do not hide upgrades or add third-party patches/replacements. Allowed helper adaptations stay inside experimental copies; invalidate and refresh affected earlier evidence if the source snapshot changes.
- [ ] Deliver commands/environments, complete logs/exit codes, test and artifact inventories, dependency/security/runtime observations, consumer toolchains, timing data, and a pass/fail/blocked matrix. Distinguish checked execution blockers from demonstrated compatibility failures.

## Scope and handoff

This is an evaluation on Windows, not a migration or CI implementation. Missing Windows evidence prevents adoption even if macOS/Linux pass. A gap needing forbidden adaptations stops the candidate and may feed 09. Passing 08 does not imply Linux passed; both platforms remain required for adoption.
