# 01 — Establish the isolated macOS environment and minimal baseline

**What to build:** a reproducible, safely isolated starting point with real supported Node.js/pnpm installation and initial SDK/DOM test results, so that the first Bun comparison is meaningful and cannot damage ongoing work.

Blocked by: None — no ticket dependency; execution still requires separate explicit authorization.

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E0.
Stories: 7–11, 38–39.

## Acceptance criteria

- [ ] Obtain explicit authorization for this bounded experiment before creating experiment copies, installing dependencies, or executing project workflows. Document authorization scope; ticket publication and readiness do not grant it.
- [ ] Record the actual source revision and relevant working-tree snapshot, OS/architecture, dependency identities, tool versions, and resolved executable locations. Preserve the user's dirty work without checkout, stash, revert, deletion, or branch/worktree creation.
- [ ] Establish independently writable baseline and candidate copies of the same snapshot, with isolated dependency stores/caches, build outputs, profiles, backend data, and user configuration. Do not share writable outputs with the primary workspace or between candidates.
- [ ] Verify the baseline actually uses Node.js 22.23.2 and pnpm 10.0.0 rather than an unsupported global executable. Personal tool-manager use is allowed for the evaluator, but is not proposed as a contributor prerequisite.
- [ ] Record the intended exact Bun candidate version before its comparison begins; do not use a moving latest alias or silently change candidate versions between stages.
- [ ] Execute a frozen baseline installation with the existing versions, security overrides, and install-script restrictions, and record the complete applicable dependency inventory for macOS.
- [ ] Execute the existing public SDK non-DOM suite and EditorPane Vue/happy-dom suite through the normal non-watch Vitest workflow, including real public-package build/pack setup. Record selected/passed/failed/skipped cases, full logs, exit codes, and runtime identities.
- [ ] Confirm isolation protects original sources, global installations, application state, personal credentials, and external Agent CLI configuration. A backend data-directory override alone is not sufficient protection against startup hook installers.
- [ ] Deliver actual baseline and isolation evidence. A failed baseline or checked prerequisite blocker stops dependent comparisons; do not repair unrelated code, waive requirements, or describe it as Bun incompatibility.

## Scope and handoff

Only the authorized local macOS baseline/isolation work belongs here. No Bun adoption, full benchmark, platform CI operation, release change, registry publication, UI automation, or production modification is included. Do not clear terminal scrollback. Passing this ticket unlocks 02 only at the ticket-dependency level; executing 02 needs its own applicable authorization.
