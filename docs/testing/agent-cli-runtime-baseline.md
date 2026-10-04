# Matched Agent CLI Runtime baseline workload (G0)

This is measurement/test support, not a runtime interface or a product policy. The numbered method below records the original version-2 workload; the dated correction below supersedes its changed details for current version 6. The frozen inputs are `tests/fixtures/cli-regression/runtime-workload.json`. Freeze its hash and this document in the evidence directory before trials. No candidate data has been collected. Later tickets must run the same inputs and native host, or identify a new incomparable workload revision. No performance percentage or adoption budget is agreed.

## Confirmed seam and workload

Use the production TypeScript `createWsClient`, authenticated loopback WebSocket, real Python dispatch, attribution, watcher, persistence and native PTY. Substitute only controlled external CLI executables and synthetic Claude JSONL/OpenCode SQLite WAL records from the existing catalog. Ordinary shell execution uses the same controlled executable without an agent adapter. No vendor installation, account or provider network is needed.

Run 5 independent fresh-backend repetitions at each of 1 and 4 sessions. In an explicitly enabled non-measurement study, run 1 repetition at each size without claiming it is a performance study. Ordinary unit/CLI collection intentionally skips this optional, non-catalog study. For each repetition:

1. Measure process launch to authenticated readiness (cold backend start; includes disposable driver's imports and discovery polling). This is process-cold, **not** a flushed OS disk-cache measurement.
2. Idle the empty backend for 1 second; create ordinary-shell peers through direct argv and POSIX login-shell wrapping (native Windows uses its shell seam), then check child-observed arguments, cwd and isolated home. Distinguish first PTY startup from a subsequent create in the same warm backend. Kill these peers.
3. Create the requested agent session count concurrently, alternating Claude and OpenCode. Measure request-to-create acknowledgement and request-to-child READY independently. One-session runs alternate vendor between repetitions so both formats are measured. Readiness includes the controlled interpreter/peer cost; it is not pure runtime cost.
4. Idle with live sessions for 1 second. Perform 10 acknowledged Unicode/quoting input rounds per session concurrently. Measure monotonic client send-to-exact-child ACK and retain received output. Terminal echo alone does not qualify.
5. Concurrently emit 128 ordered lines of 2048 ASCII payload bytes per session. Check every numbered line and the final marker; submit input after the first burst frame, check its ACK, and record burst and during-burst input latencies. This is a bounded burst, not a guarantee about infinite saturation.
6. Resize and write initial records; require the child's write acknowledgement and OpenCode's real `session.detected`. Wait the fixed 50 ms seed-settle interval, then write response/complete records. Measure final-command-to-attributed `agent.activity/turn_complete`; verify exact reply, pane/workspace/vendor identity and live `tokens.snapshot` totals (12 input, 3 output per session). This initial phase respects the reader's historical-activity seeding. After kill, restart the actual backend on the same isolated state and authenticate again; require exact cumulative global/workspace totals. Usage aggregation normally runs every 300 seconds, so a rapid initial cumulative snapshot is not the live-usage contract. Keep real watcher timing, not a fake observation clock. A missed detection/activity deadline remains a failed trial, not a substituted reader-only success.
7. Disconnect and reconnect the production client, reattach the same terminal IDs and verify continued ACKs from the same child PIDs. Kill sessions and require dead reattach results within the deadline, not merely kill-request acknowledgement. The harness requires backend exit 0, no refusal records and no surviving children, even when emergency cleanup succeeds. Concurrent Claude managed-skills construction can fail open; retain the warning and actual injected flags rather than inventing successful skills injection. This warning does not waive the separate observation/persistence assertions.

## Resource accounting and statistics

A disposable Python driver samples the actual backend and its descendants every 50 ms using psutil. Retain per-PID creation-time identities, cumulative user+system CPU seconds and RSS bytes with monotonic/wall timestamps. Retain the last sampled live counters before kill. Classify backend (and, later, explicitly identified runtime sidecar) as runtime/coordinator; controlled peers and their shell wrappers as child cost; report combined cost separately. Test runner/driver CPU and RSS are excluded and must not be confused with user runtime costs. RSS sums double-count shared pages; they are not USS or installed footprint. CPU percentage is delta CPU seconds / delta wall seconds * 100, where 100% is one core, not whole-machine normalized.

Phase boundaries use wall timestamps to join samples; latency uses the client's monotonic performance clock. For each phase report mean/peak sampled RSS and cumulative observed CPU deltas by category, along with sample counts. Sampling may miss very short helper processes and CPU at process exit; preserve this limitation rather than subtracting guessed child costs. Cold readiness has a separate elapsed measurement because the sampler attaches only after backend launch. Record system model, memory, OS/architecture, interpreter, toolchains, revision, dirty diff, workload hash and invocation. Report raw trial values plus min/median/max and population standard deviation; no cross-machine speedup claims.

## Reproduction

```sh
uv --project backend sync --locked
mise exec node@22.23.2 pnpm@10.0.0 -- pnpm install --frozen-lockfile
ACR_STUDY=1 ACR_EVIDENCE_REVISION=r2-check-method-6 mise exec node@22.23.2 pnpm@10.0.0 -- pnpm test:run --project cli tests/cli/runtimeBaseline.test.ts
ACR_MEASURE=1 ACR_EVIDENCE_REVISION=r2-method-6 mise exec node@22.23.2 pnpm@10.0.0 -- pnpm test:run --project cli tests/cli/runtimeBaseline.test.ts
```

Every attempted repetition is retained, including assertion failures and cleanup failures. A final nonzero test verdict lists failed trials; do not count their partial latency/resource samples as complete workload measurements. Development checks before the original version-2 workload was frozen are diagnostic only, never performance results. The original prose incorrectly called this version 1; the original JSON and frozen inputs were version 2.

Receipts and raw samples are written under `test-results/ci/runtime-baseline/`; copy redacted evidence to the ticket evidence directory. Do not archive discovery URLs, token files, credential stores or real home contents. Do not run concurrent benchmark invocations in the same checkout. Native Linux/Windows results must come from those hosts; mock/path-swapped Windows checks are not ConPTY evidence. This document authorizes neither candidate implementation nor hosted execution.

The original ticket-01 handoff is preserved in `.scratch/agent-cli-runtime-evaluation/evidence/t01/g0-report.md`; its blocked disposition is historical, not an added all-green G0 requirement. The correction is recorded in `.scratch/agent-cli-runtime-evaluation/evidence/t01/correction-r1/report-r1.md`; the complete declaration/coverage inventory is [agent-cli-runtime-inventory.md](agent-cli-runtime-inventory.md). Do not claim complete G0 from a failed workload.

## Correction r1 method 3 — declared 2026-10-03

Method 3 used JSON version **3**, named `correction-r1-method-3`. Preserve the original version-2 freeze, trials and report. Freeze the current source/hash, helpers and this document before fresh matched measurements; do not pool version-2 or diagnostic samples into this method.

- Keep the same native host, process-cold disposable backend, warm PTY creation in that same backend, 1/4 sessions, 5 repetitions each, alternating vendors, 1-second idle windows, payloads, burst, 50-ms resource interval, 50-ms observation settle and 15-second correctness deadlines. Change acknowledged I/O rounds from 10 to **300** because the original phase yielded zero/one CPU samples. Require at least **10 actual I/O CPU/RSS samples**; a missing sample proof is invalid evidence, not a reason to tune the workload after seeing results. The short bounded burst remains a latency/completeness check; do not present zero/one burst samples as a reliable phase CPU estimate.
- Disable terminal echo in the controlled peer. Preserve the exact numbered-line assertions. The original kernel echo could interrupt a payload despite all 128 output lines being present. Close the fixture initializer's SQLite connection; its transaction context alone did not release that handle. Keep the actual peer WAL writer open during normal observations.
- Preserve `first-` and `restart-` identity/log/scenario/cleanup files separately. Every resource row identifies the **first** backend PID, creation time and generation. Validate controlled READY PIDs against those samples. Restart is measured for correctness, not sampled CPU/RSS. Runtime cost includes backend, native bridges and intermediate shells; only Python processes executing `baseline_peer.py` are controlled-child cost. Exclude the driver/client coordinator. RSS sums double-count shared pages.
- CPU phase deltas are observed lower bounds: sum each identity's last-minus-first cumulative user+system counter inside the phase; use the monotonic sample duration for one-core percentages. Birth/exit CPU and brief unsampled children can be missed. For cold bootstrap separately retain the first backend CPU counter and near-ready RSS, with their timestamp offsets; these are not an import-time RSS peak or an exact readiness CPU boundary. Report sample counts and quantization, not guessed costs.
- A session/activity/live-total correctness failure remains a failed assertion and appears in the final nonzero verdict. Record it, then continue other meaningful observations and reconnect/kill/restart checks; never substitute direct reader/sink calls. Keep exact restarted cumulative-total assertions. Record each phase's outcome, bounded complete output/event order, latest live/restarted snapshots and cleanup result. Measure reconnect recovery through renewed child ACK, kill through dead reattach, and durable restart through exact authenticated cumulative snapshots.
- Classify a trial as **complete** only when every assertion, isolation/cleanup and resource proof passed. A failed trial with valid isolation/process proof may supply independently passed **partial phases** (including valid prefix I/O/CPU/RSS); aggregate those only in separately labeled phase statistics with their raw cohort sizes. Exclude failed assertions from successful-latency aggregates; separately count independently successful per-peer observation assertions even when another peer makes the overall observation phase fail. Session/activity deadline failures are right-censored at the fixed 15-second deadline, not assigned an invented latency. No all-green legacy requirement is added to G0; unresolved product scope/acceptance is a separate manager decision.
- `ACR_DIAGNOSE=1` instrumentation and `ACR_DIAGNOSE_CLOSE_WAL=1` writer-close counterfactuals are diagnostic only. The latter drives real terminal input, filesystem notifications, watcher, reader, sink, attribution and broadcast; it is not the principal baseline. It is forbidden in measurement mode.
- Write redacted artifacts before the CI publication boundary. A failed/refused/timed-out rescue must save its receipt, keep the trial failed and allow bounded remaining repetitions. Successful emergency rescue does not turn survivors/untracked descendants into a passing lifecycle. Keep original historical evidence separately; sanitized copies are not new green runs.

Run fresh matched samples, without another benchmark or verification workload concurrently:

```sh
ACR_MEASURE=1 ACR_EVIDENCE_REVISION=r1-method-3 mise exec node@22.23.2 pnpm@10.0.0 -- pnpm test:run --project cli tests/cli/runtimeBaseline.test.ts
```

Publish to `test-results/ci/runtime-baseline/r1-method-3/`. Report per-trial raw values, complete versus partial counts, min/median/max/population standard deviation, native machine/toolchains/source/method hashes and isolation evidence. Do not claim Rust results, speedups or native Linux/Windows coverage.

### Method 4 — controlled metadata version discovery

Method 4 used JSON version **4**, named `correction-r1-method-4`. Fresh method-3 measurements revealed four refused attempts to run the real installed `opencode --version` during the startup smoke probe. Preserve that freeze and all ten trials. Their four isolation-failing trials are not successful measurements; the guard prevented execution. This is a new fixture gap, not an inherited product blocker.

Before each first/restart backend, install the existing owned CLI shims for Claude/OpenCode and prepend their directory to that backend's PATH. A strict `--version` branch in the controlled peer returns `0.0.0-synthetic-baseline`. The real startup executable resolver/probe and unchanged refusal guard still run; real provider executables and credentials remain forbidden. No production version policy/reader is changed. Ordinary PTY peers still run the same explicit interpreter/script argv. Method 4 changes only that fixture discovery boundary; all method-3 workload counts, sampling rules, assertions and deadlines remain identical. Freeze again before fresh samples and do not pool method-3/4 measurements.

The requested sampler sleep is 50 ms **after** collection; actual cadence includes descendant-inspection overhead and is recorded by monotonic timestamps. Client predicate polling is 5 ms, so reported latency includes that granularity. Keep these limitations; do not infer sub-millisecond runtime differences from this harness.

```sh
ACR_DIAGNOSE=0 ACR_DIAGNOSE_CLOSE_WAL=0 ACR_MEASURE=1 ACR_EVIDENCE_REVISION=r1-method-4 mise exec node@22.23.2 pnpm@10.0.0 -- pnpm test:run --project cli tests/cli/runtimeBaseline.test.ts
```

### Method 5 — preserve owned discovery in the synthetic login shell

Method 5 used JSON version **5**, named `correction-r1-method-5`. Method 4 exposed the same four refused real-CLI probes despite the backend PATH prepend. The exact production chain is `app._probe_agent_cli_for_spawn` → `onboarding_deps.resolve_executable` → login-shell PATH refresh. `_refresh_path_from_login_shell` puts the answered login-shell PATH first. Without a fixture profile, the system Homebrew prefix therefore outranks the owned shim. The refused command was `[/opt/homebrew/bin/opencode, --version]`, resolving to `/opt/homebrew/Cellar/opencode/1.18.34/bin/opencode`; it did not execute. Keep method-3/4 inputs and receipts intact.

Before startup, seed only the synthetic HOME's `.bash_profile`, `.zprofile` and `.profile` with an owned-bin PATH prepend. Native Windows keeps method 4's direct environment setup; no native Windows execution is claimed. An isolated process test runs the actual login-shell refresh and original resolver, and asserts the resolved executable is the owned shim. No resolver mock, production PATH policy change, guard relaxation or reader/sink substitution is involved. Freeze the changed fixture and method before another ten samples. All method-3 measurement counts, sampling validity rules, assertions and deadlines remain unchanged; do not pool earlier methods.

```sh
ACR_DIAGNOSE=0 ACR_DIAGNOSE_CLOSE_WAL=0 ACR_MEASURE=1 ACR_EVIDENCE_REVISION=r1-method-5 mise exec node@22.23.2 pnpm@10.0.0 -- pnpm test:run --project cli tests/cli/runtimeBaseline.test.ts
```


## Correction r2 method 6 — exact native-session attribution and explicit study entry

JSON version **6**, `correction-r2-method-6`, changes assertions/entry only; preserve method-5 freezes and measurements without claiming they passed the stronger attribution checks. No retrospective positive attribution claim is made from old totals. Quantities, deadlines, sampling, statistics, phase eligibility and comparison rules remain method 5: 1/4 sessions x 5 repeats, 300 I/O rounds, 128 x 2048-byte burst, 1-second idle, 50-ms sampling/settle, 15-second observations and minimum 10 I/O samples.

This non-catalog research study is intentionally skipped by ordinary unit/CLI collection unless `ACR_STUDY=1` or `ACR_MEASURE=1`. Required vendor/historical cases and their execution audits are unchanged. An explicit study still returns nonzero for failed observations and persists later phases/repetitions; no failure is swallowed. Use `ACR_STUDY=1` for the two non-measurement attempts, or `ACR_MEASURE=1` for the complete ten-attempt matrix, as shown in Reproduction. Do not run them concurrently with tests or benchmarks.

Independent fixture identities follow the existing controlled writer: Claude's native ID is its explicit session pin (`pane`); OpenCode writes `ses_<pane>`. OpenCode `session.detected` must match that native ID plus pane/vendor/workspace, not merely a pane event. Claude retains its existing explicit-pin contract. Live totals must be 12/3 in the exact identified native-session key of `workspace.live_by_session` (the existing raw session-ID key, not a newly composed key). Unknown/mismatched identity or another bucket with identical totals cannot pass. Inherited shared DB/vendor slots and missing notifications remain failed observations; no production fallback, key rewrite or direct reader/sink pass is introduced.

Freeze this document, runner, assertion seam, fixture and aggregation inputs before fresh measurements. Publish to `test-results/ci/runtime-baseline/r2-method-6/`; retain the separate explicit check under `r2-check-method-6/`, never pooled into measurements. The correction-r2 handoff is `.scratch/agent-cli-runtime-evaluation/evidence/t01/correction-r2/report-r2.md`; the delivered r1 report remains `correction-r1/report-r1.md`. Missing/wrong attribution does not invalidate independently passed CPU/RSS/concurrency phases or authorize a product repair.
