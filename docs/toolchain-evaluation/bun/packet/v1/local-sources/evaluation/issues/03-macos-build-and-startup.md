# 03 — Validate macOS typechecks, builds, and isolated development startup

**What to build:** the real daily macOS path from the frozen installed project through typechecks, public/official Plugin and application builds, to a ready isolated development instance without externally installed Node.js/pnpm.

Blocked by: 02 — Validate the minimal Bun-only macOS path (pass required).

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E2 — build/startup slice.
Stories: 18, 22–24, 29–30, 38–39.

## Acceptance criteria

- [ ] Obtain explicit authorization for this stage. Preserve the 01/02 source/dependency/tool snapshot and isolation; stop comparative claims if the snapshot changes without a corresponding baseline refresh.
- [ ] Run the existing aggregate TypeScript/Vue and public-package/Plugin typecheck behavior under the supported baseline first, then the Bun candidate. Do not drop checks to obtain a pass.
- [ ] Build actual public distributables in contracts → SDK → UI dependency order, then the existing official Plugin and application products. Record outputs and executable/runtime observations for both environments, including all nested helper processes.
- [ ] Retain real required native artifacts and Python/uv/native prerequisites. Do not use missing-fixture skips, skip-backend-build shortcuts, or stale distributions to claim a complete build.
- [ ] Confirm the candidate daily build/startup path does not require external Node.js/pnpm. Electron's embedded runtime and existing non-JavaScript prerequisites remain permitted and must be distinguished in the evidence.
- [ ] Launch a bounded development probe with a unique application profile, separate backend storage, and protected user configuration. Protect real Agent CLI configuration from hook installers and do not reuse the user's running backend or credentials.
- [ ] Observe actual development readiness using build products, process state, and existing backend/Plugin health seams rather than only a successful launcher exit. Record readiness/error evidence and terminate only experiment-owned processes.
- [ ] Preserve package/Plugin outputs needed by 04/05 as identified, attributable artifacts; do not substitute private source aliases or outputs from the baseline for candidate products.
- [ ] Record actual baseline/candidate results, logs, exit codes, runtime identities, and artifact inventories. Stop and report any need for third-party repair, unapproved upgrade, weakened validation, or forbidden runtime dependency.

## Scope and handoff

This slice does not replace Electron, Vue/Vite, the Python backend, or Agent CLI integrations. It does not execute UI automation, capture screenshots, clear terminal scrollback, request DevTools paste, or claim visual acceptance from readiness alone. Genuine visual judgments remain for a consolidated user checklist if required. It does not modify release packaging or CI. Passing results make 04 and 05 dependency-ready; complete macOS compatibility still requires both.
