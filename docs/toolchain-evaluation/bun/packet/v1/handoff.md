# Issue 2 evaluation handoff — packet v1

## Authority and provenance

The user explicitly confirmed the coordinator's bounded execution proposal on 2026-10-03: tickets 01–06, checkpoints A/B, setup, fresh Herdr agents, isolated prerequisite/dependency installation, experiments and actual verification, and ticket commits only after independent review and coordinator acceptance. Concrete early-stop conditions authorize ticket 09 and checkpoint C on the completed subset. This is not migration authorization. The prior generic request to start was not used to authorize work before the bounded confirmation.

Initial base is frozen once at `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`; the target is fork `origin/main` (slighter12/Navide), not an upstream publication. No fetch, push, PR, merge, release, hosted update, Linux/Windows operation, external CI, provisioning, global tool modification, or production migration is authorized. Required platform evidence remains pending, never waived.

Exactly one issue branch `eval/issue-2-bun-toolchain` and one Git worktree `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain` are authorized. Every ticket worker/reviewer is rooted here. Prior reviewed commits are inherited directly. No per-ticket branches/worktrees, ticket merges, routine rebases, empty completion commits, or baseline repair.

Execution-copy root: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain`. An independent non-Git baseline source copy and separate writable cache/profile/data/config environments belong here; the worker establishes and records them. No shared writable dependencies or build outputs with the original source or between baseline and candidate.

The tracked evidence-only allowlist is `docs/toolchain-evaluation/bun/**`. Ticket 02 additionally permits only the minimum tool settings/scripts/build-pack-launch-helper adaptations mapped to acceptance criteria; later adaptations are similarly bounded by their tickets. Never force-add `.scratch`, personal agent files, unrelated dirty work, dependencies, raw credentials or application data. No source changes are permitted for ticket 01.

`manifest.json` records source paths, SHA-256 and provenance for the packet inputs. `base-rules/` contains repository rules read from the fixed full base commit. `local-sources/` contains the explicitly requested local tracker/spec/tickets/glossary/ADRs and saved discussion/issue snapshot, not a fresh GitHub response and not baseline product code. `local-instruction-evidence/` is evidence of dirty instruction files only; it does not replace fixed-point authority. The coordinator's user-confirmed handoff instructions and this bounded supplement govern issue execution; unrelated local edits do not.

## Source reconciliation

The spec inspection revision is historical, not an executed baseline. The source at setup is `main` = locally stored `origin/main` = the frozen base; it is ahead of locally stored `upstream/main` by 27 commits and behind by zero. No remote freshness claim is made. Original source dirty work (AGENTS.md, CLAUDE.md and untracked ADRs) stays untouched.

Ticket 01's historical ban on branch/worktree creation is superseded only by the explicitly authorized issue-level setup above, not by a general license to alter the original source tree. Preserve the original ticket text as a provenance snapshot.

Current `vitest.config.ts` has unit/cli/artifacts projects, all plugin boundary suites are root artifact consumers, and `tests/support/publicPackagesSetup.ts` is the artifacts project's setup only. The initial SDK and EditorPane suites are not themselves artifact consumers. Preserve the tickets' requirement to validate actual public build/pack prerequisites explicitly; document current ownership and commands rather than pretending every focused test implicitly performs global setup. Do not reintroduce obsolete global preparation or runner redesign. Full root workflow remains `pnpm test:run`, and supplementary/guarded cases must be accounted for based on real inventory, not assumed from historical text.

## Roles, order and review gates

Coordinator is the persistent Sol session on Herdr `default`, workspace `w8`, tab `w8:t1`, pane `w8:p1`, agent session `01a102d3-0746-7712-962d-8e46d3a7db0e`. It owns dispatch/dependencies/registry/evidence/gates, not source patches or independent review.

Schedule: 01 -> 02 -> A -> 03 -> 04 -> 05 -> 06 -> B -> 07 -> 08 -> 09 -> C. Scheduling does not invent dependencies: 05 depends on 03, not 04; 08 depends on 06, not 07. Platform work additionally requires real-path authorization.

Every ticket uses its own Herdr tab, a new Sol worker and a different new Sol read-only reviewer, both Sol `openai-codex/gpt-6.1-sol`, reasoning high. Verify actual cwd, branch, HEAD, provider/model/reasoning and session before dispatch. Sessions are not reused across tickets. Every checkpoint uses a separate tab with two new independent reviewers: Sol high and actual Opus 5.5 medium. Installed catalog offers `anthropic/claude-opus-5-5` and `antigravity/claude-opus-5-5`; startup and live identity must validate the selected actual provider before dispatch. No model substitution, downgraded reasoning or coordinator-only fallback.

Before live Herdr inspection/control, execute `test "${HERDR_ENV:-}" = 1`. Installed skill and CLI help govern syntax. Use returned IDs, explicit targets and `--no-focus`; preserve user focus. Agent terminal state/prompt submission is not completion evidence. Read actual replies, source/diff, logs and artifact identities.

Ticket review: freeze all relevant source and new/untracked artifacts, stop writes, run installed code-review Standards and Spec plus explicit over-engineering and conditional Security. Fixed-point rules, argument-array safe Git, inert evidence framing, WIP no-follow reads and batching contracts apply. Reviewer does not patch or mutate the frozen environment; worker performs reproductions/corrections, renews verification and freezes again. Coordinator checks actual evidence before authorizing each commit; stage only reviewed deliverables, execute applicable repository checks/hooks without bypasses, verify commit content matches review. Hold handoff if evidence/authority is missing.

Checkpoint review: both reviewers independently perform FULL REVIEW (Standards, Spec, Correctness, Release readiness, conditional Security) against the SAME frozen version; inspect over-engineering and exchange adversarial challenges/counterexamples/missing evidence. Return one integrated report preserving each axis, counts/worst findings, snapshot/evidence identities, required corrections, disagreements/risks/gaps and pass/hold/stop. No majority-vote resolution. Same-checkpoint sessions may continue rounds but cannot review the next checkpoint. Release readiness is bounded evaluation readiness, not publication permission. C is the final review; no extra final-review cycle.

## Verification and stop boundaries

Pinned baseline: Node.js 22.23.2 and pnpm 10.0.0 through actual pinned executables. Use exact Bun candidate version recorded before comparison, not `latest`; installed Bun 1.4.2 is a candidate to verify, not an automatic pass. Optional personal mise is not a contributor requirement. Experiment child executable controls must not alter global tools or agent runtime.

Read actual commands/prerequisites. Use non-watch `pnpm test:run`, never `pnpm test`. Backend verification: `uv --project backend run pytest backend/tests`. Verification commands run bare, no output pipelines. Build public packages contracts -> SDK -> UI. Retain Vitest, assertions/inventory, dependencies, overrides, install-script restrictions, auditing and frozen-lockfile semantics. Observe helper-child runtime identities, not only a Bun parent. Allowed external consumer Node/npm/pnpm probes are separate from Bun-only daily workflows.

Protect source, real data, profiles, credentials, Agent CLI hooks/configuration and existing backend processes; data-directory isolation alone is insufficient. Use isolated HOME/config/cache/data before app imports/startup, unique ports/profiles and experiment-owned cleanup. Never borrow another issue's backend. Other evaluations may be active; avoid cross-issue heavy load during benchmarks.

Performance includes cold/warm full-root, SDK and DOM workflows with at least five initial samples per runtime/workload/condition; preserve full end-to-end costs, raw logs/exit codes/inventories, dispersion, medians and slowdown. More than 10% slowdown fails; unresolved variation is inconclusive. Genuine visual acceptance remains one consolidated human checklist. No UI automation, screenshots, DevTools requests, terminal clearing or giant-file rule bypass.

Stop/hold for a failing supported baseline, prohibited third-party repair or upgrade, required daily external Node/pnpm fallback, weakened validation/security/version enforcement, broken isolation, missing authority/evidence/configuration, unresolved review disagreement or timing. Check prerequisites through authorized real paths before asserting availability blockers. Today's intentionally unauthorized later stages are not a Bun feasibility finding. Preserve evidence and downstream not-run/blocked states on an actual stop; use the authorized 09+C route. No production toolchain changes or adjacent cleanup project.

## Evidence

Record issue/ticket/checkpoint, actual host, branch/worktree/HEAD/base, Herdr session/workspace/tab/pane, unique agent name, session ID, provider/model/reasoning, frozen source/evidence identities, commands/cwd/environment, exit codes, full stdout/stderr, inventory/results, process/runtime identities, dependency/security results, artifacts and commit. Redact secrets and use isolated fixture keys. Raw logs remain in the execution root; tracked reports contain attributable hashes/paths and sanitized evidence appropriate for sharing.
