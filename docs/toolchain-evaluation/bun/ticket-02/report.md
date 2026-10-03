# Ticket 02 — minimal Bun-only macOS feasibility

## Verdict

**STOP: E1 did not pass.** Bun 1.4.2 installed the retained dependency identities and ran Vitest without an external Node.js/pnpm/mise daily fallback, but the required unchanged EditorPane suite failed and the real artifact setup failed at the existing Vue declaration build. No adoption, complete macOS compatibility, performance, public-consumer, lifecycle, startup, or other-platform pass is claimed. No third-party repair, dependency upgrade, assertion change, skip-as-pass, fallback, staging, or commit was performed. The exact internal runtime defect is not proven; this is an observed gate failure, not a claim that every possible configuration has been exhausted.

Raw evidence root (`E`): `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-02`.

- Dispatch: DISPATCH02; worker `navide2-t02-worker`, actual `openai-codex/gpt-6.1-sol`, reasoning `high`, session `01a10365-6598-7510-8063-0fdf9fff52ef`, coordinator-dispatched Herdr `w8:t6/w8:p8`.
- Shared worktree: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`; sole branch `eval/issue-2-bun-toolchain`.
- Fixed review point/current HEAD: `14b7a98322849f6ff38e14f6b892afdc01f47a90`; immutable initial product base: `f4e9fd50136ea84f38f99e3c96f2c924f21d244f`.
- Packet version 1.1: all 27 manifest-listed delivered hashes verified before routed instructions/spec inspection. Saved issue documents are local provenance, not a fresh hosted response. Current dispatch/handoff, not historical execution-not-granted metadata, supplies authority.
- Ticket 01 was accepted and committed according to `coordination/ticket-01-handoff.json`; its obsolete report HOLD wording is historical. The supported baseline used exact Node 22.23.2 with pnpm 10.0.0's JavaScript entry, not standalone pnpm's embedded Node 20.

## Isolation and exact pin

The already verified Bun binary was copied into `E/tools/bin/bun`, SHA-256 `35d20dd0263e5c950194434b925454fdfa9ba6e4467da960410fa05b08a7a5b5`. It actually reports `1.4.2 (744846f84)`; `process.versions.bun` is `1.4.2`, `process.version` is Bun's compatibility label `v26.3.0`, and platform/architecture are Darwin arm64. That compatibility label is not an external Node runtime. The contributor path needs only that binary, not its original mise installation path.

All children use new ticket-02 HOME, XDG config/cache/data/state, TMPDIR, npm config/cache, Bun install/transpiler cache, Electron cache, uv cache/install directory, backend data, and reserved profile. The child environment is constructed from an allowlist, not inherited credentials or user CLI configuration. Daily PATH is exactly the copied Bun directory plus OS system directories; actual `command -v` found Bun and found no node/npm/pnpm/mise. Agent/global PATH is unchanged. No app/backend import, startup, external hook installation, UI automation, platform execution, CI, hosted update, or publication occurred.

`E/preservation-before.json` and `E/preservation-after.json` are equal inventories of **64,916 prior ticket-01 tree entries**, including every baseline source/dependency/tool/cache/evidence entry. Bytes, symlink targets, modes, original dirty AGENTS/CLAUDE/ADR contents and original Git status, pinned original tool hashes, and all prior tracked packet/ticket-01 files were verified unchanged. These inventories do not claim that unrelated user processes or all machine configuration were globally frozen. Partial candidate outputs and dependency caches belong only to this shared issue worktree/new execution environment.

## Minimal adaptations and native lock conversion

`E/plan.md` was written before source changes and updated before each additional bounded migration probe. Actual source/config adaptation allowlist:

| Path | Acceptance mapping |
| --- | --- |
| `package.json` | Explicit three public-package workspaces and `workspace:*` local references; exact Bun engine/experimental script; identical existing 13 overrides and electron/esbuild trust allowlist. Original pnpm scripts, engine requirement, packageManager and entire pnpm security policy remain intact. |
| `bun.lock` | Native Bun representation of the original registry graph plus the same three source-local packages. |
| `scripts/bun-evaluation.mjs` | Candidate-only exact-version guard; bounded check/frozen-install/selected-run/pack/audit dispatch; Bun `--bun` selects Bun for Node shebangs; existing single advisory exception passed explicitly. No download, automatic version switch, mandatory manager, framework or application rewrite. |
| `tests/support/publicPackagesSetup.ts` | Three-line Bun-runtime selection dispatches its existing build/pack helper through the guard. Existing setup ownership, order, cache verification, stdio/maxBuffer/error behavior, assertions and teardown are retained. |

Native `bun install --lockfile-only --ignore-scripts` initially exited 1: relative `link:` entries cannot be migrated; Bun warned that it ignored the pnpm lockfile and performed fresh metadata resolution. **That attempt is not graph preservation or an install pass.** It created only isolated cache data, no candidate lockfile/node_modules. Native `bun pm migrate` and `bun audit --json` independently reproduced the migration rejection. Adding explicit workspaces alone also failed.

Two small external input fixtures contain only package/lock metadata, not a second source checkout or Git worktree. A six-scalar `link:` to `file:` diagnostic failed because the derived pnpm input lacked required file-package entries; that malformed diagnostic is not evidence against a valid file-protocol solution. The final registry-only fixture omitted exactly the three local importer entries while preserving every registry package/snapshot byte. Native `bun pm migrate` then succeeded: **688 registry identities, 739 layout addresses, every original integrity retained**. Its native lockfile was copied to the candidate and reconciled through Bun's own **offline, lockfile-only, ignore-scripts** operation after declaring the three workspaces. No manual complete lockfile generator or third-party patch was introduced. Original pnpm lockfile bytes stayed unchanged throughout. Derivation receipts record each input hash and exact operation.

## Exact-version entrypoint probes

Canonical candidate entrypoint: `bun scripts/bun-evaluation.mjs <command>` (also exposed as `eval:bun`). It compares the actually executing Bun runtime against 1.4.2 before spawning any install/build child. Frozen installation accepts no user-supplied flags. Project candidate upgrades require a fresh evaluation; no latest alias was used.

A genuine **Bun 1.4.1** archive was downloaded from its explicit official release URL into `E/tools`, extracted there, and actually version-probed. With wrong Bun ahead of correct Bun, exact Node, standalone pnpm, Homebrew tools and system tools in a controlled competing-tool PATH, both `install` and `run build:public-packages` exited 1 with actionable exact-version/manual-install guidance. Candidate source/lock hashes, every install/build-output inventory and the entire isolated environment inventory were unchanged. No node_modules or build output existed before/after those probes. Correct 1.4.2 succeeded both in Bun-only and competing-tool visibility probes. The guard never installed, downloaded or switched a runtime. `E/negative-guard.json` records the real before/after evidence; this is not a mocked version test.

## Executed workflows

Every exact argv/cwd/allowlisted environment/start/end/exit/full outer stdout/stderr/hash is retained at `E/commands/<label>.*`; no verification output was piped and all Vitest invocations are non-watch. Durations are diagnostic receipts, not benchmarks.

| Actual label/workflow | Result |
| --- | --- |
| `clean-frozen-install`: guard `install`, initially absent node_modules | Exit 0; Bun reports 620 installed packages; lock/package hashes unchanged. |
| `frozen-reinstall`: same guarded frozen install | Exit 0; identical Bun/pnpm lock and package hashes before/after. |
| `bun-audit-all`: real `bun audit --json` | Exit 1; all four existing package findings visible. |
| `bun-audit-policy`: guarded audit at high threshold with original one-GHSA exception | Exit 0; 612 lock package names checked, 3 below threshold, 1 ignored. |
| `bun-audit-policy-json`: guarded JSON audit | Exit 1; still includes ignored advisory body. This reporting difference is not hidden. |
| `sdk-dom-vitest`: original SDK and EditorPane files, unit project | Exit 1; **SDK 27 passed, DOM 9 failed, 0 skipped, 1 unhandled rejection**; 3.072 s outer receipt. |
| `sdk-dom-uninstrumented`: same complete selection without observer | Exit 1; same **27 passed/9 failed/0 skipped** and Monaco error; 2.285 s. |
| `dom-first-case`: unchanged first DOM case only | Exit 1; 1 failed/8 name-filtered skips; 2.298 s. Skips are not coverage. |
| `dom-first-threads-probe`: same first case with existing Vitest threads pool | Exit 1; 1 failed/8 name-filtered skips; 1.786 s. No config file or assertion was changed. |
| `artifact-build-pack-vitest`: artifacts project and original deterministic CLI-case filter | Exit 1 during genuine `publicPackagesSetup.ts` build; 6.022 s. **No test executed; packs/CLI case not reached.** |
| `public-build-uninstrumented`: guarded existing public-package build | Exit 1; same Vue TS2307 failure without observer; 4.772 s. |
| Source syntax check | Exit 0 for guard JavaScript and adapted helper TypeScript syntax. No complete application typecheck pass is claimed. |

The DOM test already mocks `EditorViewMonaco.vue`; nevertheless the real Monaco editor path executes and fails at `ctx.webkitBackingStorePixelRatio` because canvas context is null. Its first original assertion also fails (`dirty` event undefined); later cases see null wrappers. Reproduction without observer and with a different existing worker pool rules out an instrumentation-only outcome. The physical duplicate is dmg-builder, not Vue. The specific mock/runtime integration defect is not conclusively localized. No canvas shim, changed mock, weaker assertion, source refactor, test replacement or dependency patch was used.

The artifact setup actually invokes the existing build order **contracts tsc → SDK tsc → UI Vite → UI vue-tsc**. The first three commands produce outputs; Vue declaration compilation then reports TS2307 for `./EditorPane.vue` and `./SafeAiCliPanel.vue`. Uninstrumented direct execution reproduces both errors. This is a build-prerequisite failure, **not** a passing discovery or CLI result, despite Vitest additionally printing “No test files found” after failed setup. Mini-IDE preparation, four packs and the deterministic CLI case remain blocked; unlike baseline's selected pass/14 filtered skips, this invocation executed **zero** cases. No partial output or missing success stamp is treated as valid prepared artifacts.

## Dependency/security findings

- Candidate and baseline have exactly the same **688 registry identities/integrities**, **620 installed distinct identities**, and **68 excluded non-macOS-arm64 identities**. Candidate stores **621 physical package copies** because dmg-builder 26.16.1 has two peer-context copies; that is layout, not an upgrade. The three local public links resolve to this worktree's exact source directories.
- **1,141 installed dependency/optional/resolved-peer edge checks**, including **68 platform-excluded optional edges**, had zero resolution differences. Native lock representation has 143 platform scalar-versus-array differences and 24 dependency-versus-peer/alias representation differences; full reports retain every difference. Effective installed resolution checks, not metadata similarity alone, establish the same retained target versions. Original package lifecycle declarations also compare equal.
- Root override selectors and their constraints exactly match all 13 existing pnpm overrides; pnpm security policy bytes/values remain unchanged. Bun `trustedDependencies` is exactly electron/esbuild, never Bun's broader default allowlist. No `--trust`, manual dependency script execution, remediation, `--no-verify`, ignore expansion or upgrade was used.
- Bun's actual untrusted report identifies koffi; its trusted list displays electron. Esbuild/native install optimizations did not emit separate JavaScript runtime-entry events. Electron-winstaller declares an install script but is not listed by `pm untrusted`. **Complete behavioral equivalence of Bun's optimized lifecycle handling is not established.** Exact declaration retention and these concrete observations are reported, not promoted into a full lifecycle-trust PASS. No subsequent install/repair was performed after the runtime/build STOP.
- Real advisory responses retain the same findings: moderate Vitest and @vitest/mocker (`GHSA-82fw-gwwq-j7x9`), low DOMPurify (`GHSA-p98j-92pf-mc4p`), and the existing ignored high http-cache-semantics (`GHSA-ch52-4w7c-c8xp`). Bun scans 612 registry names including optional-platform identities; 544 are installed names. The high-threshold result explicitly reports the ignored finding and lower severities. JSON still prints that ignored finding and exits 1; no vulnerability-free claim is made. Baseline already had a supplemental JSON-versus-normal reporting discrepancy, with a different JSON representation.

## Child-runtime proof and observer limits

`E/runtime.cjs` is external process-entry-only instrumentation, loaded by the actually supported `BUN_OPTIONS=--preload=...`. Preliminary probes showed that NODE_OPTIONS and an isolated HOME bunfig preload did **not** create events; neither was assumed to work. There is no child_process patch, stderr redirection, buffering substitution or maxBuffer/error modification. The observer appends process metadata to one external log; its I/O can add overhead or fail, so equivalence/performance is not asserted. Full successful helper stderr that the original synchronous pipe API does not expose is not retroactively claimed as captured. The failing helper's original error includes both complete buffered streams in the retained outer stderr, and the direct uninstrumented build retains separate full streams.

**28 process-entry observations, 28 distinct PIDs** all report the copied exact Bun executable/1.4.2: guard, actual Vitest entry, real tinypool fork workers, artifact setup's guard child, both tsc invocations, Vite and vue-tsc. Specific build children: PIDs 65623 (contracts), 65644 (SDK), 65647 (Vite), 65655 (vue-tsc), parent shell 65622; all logged JavaScript executables are Bun. Native OS/esbuild helpers are not external JavaScript fallback; exhaustive native/syscall tracing is not claimed. Pack/CLI runtime proof is **not-run/blocked**, not inferred from the parent. Final PID-scoped check found none of the 28 observed PIDs still running; no unrelated process was inspected or killed.

## Review freeze and downstream handoff

`evidence.json` and `review-index.json` identify command receipts, raw hashes/provenance, ordered lossless inventory parts, bounded review scopes and preservation results. Large source/cache/dependency snapshots are supporting **hash-only** evidence, not requests to review megabytes of third-party source. All text review candidates remain below 256 KiB and 2,000 lines; each named scope uses canonical bytewise consecutive batches with at most 32 files/256 KiB each and four batches/1 MiB total. No raw items were discarded or truncated to meet limits.

The external freeze manifest captures HEAD, full diff/status, every relevant new/untracked candidate via stable descriptor-relative no-follow reads, source identities, partial generated outputs, installed dependency tree, new environment/tools/cache/evidence snapshots and hashes. Its exact path/SHA-256 is returned with handoff, not embedded self-referentially. Prior packet/ticket-01/baseline tree bytes are unchanged. All writes stop at freeze pending independent fresh reviewer/coordinator authority.

Proposed stage allowlist **only after independent review and coordinator go-ahead**: the four adaptation paths above and `docs/toolchain-evaluation/bun/ticket-02/report.md`, `evidence.json`, `review-index.json`. These are evaluation-branch artifacts, not production migration approval. Never stage raw cache/logs/dependencies, prior ticket artifacts, private agent files or unrelated work. No staging or commit has occurred.

Ticket 02 fails its retained DOM and build/setup gates; lifecycle optimizer assurance is additionally incomplete. Checkpoint A/03–06 and platform gates are not released by this result. Later workflows, four packs/deterministic CLI execution, startup, consumer probes, full tests, timing, Linux and Windows remain not-run/blocked as applicable. Coordinator may use the expressly authorized actual-stop 09+C route; this worker does not dispatch it or proceed independently.
