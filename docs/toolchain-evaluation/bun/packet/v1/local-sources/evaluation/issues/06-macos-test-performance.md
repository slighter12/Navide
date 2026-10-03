# 06 — Compare macOS end-to-end test performance

**What to build:** a reproducible macOS baseline-versus-Bun performance verdict for complete and representative single-file Vitest workflows, including their real setup costs rather than only JavaScript execution time.

Blocked by: 04 — Validate the full macOS test inventory and Plugin lifecycle; 05 — Validate public artifacts for existing consumers (both must pass, completing macOS E2 with 03).

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E3.
Stories: 33–39.

## Acceptance criteria

- [ ] Obtain explicit authorization for the benchmark stage. Use the supported Node.js 22.23.2/pnpm 10.0.0 baseline and the exact Bun candidate already validated, with matched source/dependency snapshots.
- [ ] Compare on the same macOS machine with equivalent environment, test inventory, build mode, parallelism, and prerequisite outputs. Record executable/runtime identities and confirm the Bun daily workflow has no forbidden external Node.js/pnpm fallback.
- [ ] Measure three required workloads: the full root non-watch Vitest workflow, the public SDK non-DOM single-file suite, and the EditorPane Vue/happy-dom single-file suite. Keep supplementary coverage from 04 mandatory; do not claim it was timed as part of the root suite if it was not selected there.
- [ ] Define cold/warm conditions consistently for both candidates. Cold resets only identified generated build/setup/test caches in isolated copies while keeping installed dependencies controlled; warm retains equivalent valid prerequisite outputs. Record all reset/retained state, and never delete original workspace caches or outputs.
- [ ] Start with at least five samples per runtime/workload/cache condition. Include actual global package build/pack/setup and child-process costs in wall time; measure installation separately rather than including it for only one candidate.
- [ ] Preserve raw durations, full stdout/stderr, exit codes, test inventory, exact invocation/environment, and cache conditions for every sample. Failures remain visible and are not silently discarded as timing outliers.
- [ ] Compare medians using Bun median divided by baseline median minus one. More than 10% slowdown in any required matched condition fails the performance gate; exactly 10% does not exceed it. Comparable or faster execution passes without a mandatory minimum speedup.
- [ ] Report dispersion and outliers. Interleave/add samples when variation prevents a defensible verdict, and mark unresolved measurements inconclusive instead of cherry-picking runs or assuming a speed benefit.
- [ ] Deliver an evidence-backed performance pass/fail/inconclusive result with calculations and scope limitations. Do not replace Vitest, change dependencies, weaken tests, or alter the validated workflow solely to improve the measured result.

## Scope and handoff

This is not a product optimization or migration ticket. All resets affect only experiment-owned files, and no terminal scrollback is cleared. Only a passing 06 result makes 07 and 08 dependency-ready; an observed regression or unresolved blocker may instead feed 09's early-stop route. Platform and CI execution remain separately gated by authorization.
