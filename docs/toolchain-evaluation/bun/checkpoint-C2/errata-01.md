# Checkpoint C2 errata 01 (additive)

Additive record at HEAD `97c60accfb8967b5f9024ae4bf04f8154bebb6b9`, generated 2026-10-14T14:00:48Z. No committed file is edited; each entry names the committed target, states the corrected statement and cites evidence by path and SHA-256 (computed). Path convention: `repo:` is relative to the worktree at HEAD; `raw:` is relative to the raw root `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/`. Machine-readable twin: `errata-01.json`. Basis: the four checkpoint C2 reviewer reports (both PASS WITH QUALIFICATIONS, DEFER confirmed, no P0/P1) and the correction-01 dispatch.

| Entry | Severity | Subject |
|---|---|---|
| E-01 | P2 | The public evaluation payload is UNSCANNED for secrets |
| E-02 | P2 (Sol) / P3 (Opus) | Ticket 09 section 7.1: authority attribution and the 'inert on Node' scope |
| E-03 | P3 | ci-refresh-01 Linux matrix: the 1,210 node-named alias count is the Windows figure |
| E-04a | P3 | Ticket 09 matrix, macOS 'Supplementary suites' row: qualifier |
| E-04b | P3 | Windows root-cold performance: robustness of the verdict with two exit-1 Node samples |
| E-05 | P3 | Handoff notes |
| E-06 | P3 (Opus RR-1) / P2 (Sol) | Cleanup inventory (record only; the user decides; nothing was removed) |

## E-01 (P2): The public evaluation payload is UNSCANNED for secrets

- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 212: "dependency security on all platforms; release/CI with Bun."
- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 80: "| Dependency security (overrides, lifecycle scripts, audit) |"
- Corrected statement: Ticket 09 marks dependency security `not-run` but never says that no secret scan covers the evaluation payload. The payload is UNSCANNED: the branch `eval/issue-2-bun-toolchain`, the 21 detached `eval-bun-ci-*` tag commits and the public Actions artifacts on `slighter12/Navide` have no gitleaks scan receipt in any record examined, and no workflow was triggered to produce one. In all 7 commit/push receipts examined (listed below) the local hook printed that the scan was skipped because gitleaks is not installed, and the hook exits 0 in that case. `.github/workflows/secret-scan.yml` triggers (line 3ff.) only on push to `main`/`master`, `pull_request` and manual dispatch, so no workflow scans the eval branch or tags; the fork run listing shows 58 Secret scan runs, all on `main`. Nothing about this evaluation may be described as secret-scan clean. Whether to scan (and where) is a separate user decision; this erratum authorizes and performs no scan.
- Limits: The examined receipts are the ones listed; I did not enumerate every earlier ticket commit or tag-push receipt (tickets 01-09, 07/08 attempt pushes), so the statement is 'no examined receipt shows a scan that ran' plus the workflow-trigger fact, not a claim about every historical hook invocation. gitleaks absence was observed on the PATH of this worker's shell only. A reviewer's ad-hoc pattern sweep is not equivalent to the pinned gitleaks check (project pin 8.24.3 in secret-scan.yml).
- Evidence:
  - `raw:rebase-01/34-push.stderr` - `cfe122c0b1ae88b7ae03fb95e80f434692d6356bbf0c523b9d4cd059494208d0` - rebase-01 branch push (the once-only force push) (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:rebase-01-lock-refresh/commands/resume-16-commit.stderr` - `22b3d443f1251b4049c2cf13c02af03a01cd4a8dbb2613ce1f4c148adaf1a76a` - lock-refresh commit: pre-commit hook (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:rebase-01-lock-refresh/commands/resume-27-push.stderr` - `ff4137ee851c7d5d37619c90d6bbf6a43ea3eb5e0ccede6012b863912ba25558` - lock-refresh branch push (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:rebase-01-merge-sync/34-push.stderr` - `f8a318bb3334be8b3b8c50219c2a43b21ee5482dba3b0c6e0589751fba5c2f45` - merge-sync branch push (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:ci-refresh-01/attempts/eval-bun-ci-07-11/push.log` - `bcfbb8bec6e568349a076f2e557e33d634d5161b7a4d2d6c0c5aa7b73bc57f50` - ci-refresh tag push eval-bun-ci-07-11 (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:ci-refresh-01/attempts/eval-bun-ci-08-09/push.log` - `1afd23114d7ee1f8ad98fa915763ffbaed3bcb45b0a1bdf90b9eac0acaea25a1` - ci-refresh tag push eval-bun-ci-08-09 (hook prints: secret scan skipped, gitleaks not installed)
  - `raw:ci-refresh-01/attempts/eval-bun-ci-08-10/push.log` - `bbd27db225e2c711b51eeb7a1911bf90ee6780500e2d0e77da6d739232c39682` - ci-refresh tag push eval-bun-ci-08-10 (hook prints: secret scan skipped, gitleaks not installed)
  - `repo:.githooks/pre-push` - `6d5afee54710231f07cff4b622d6ea4f572bbb38b7b1f084043b8e9d07dbb0d9` - pre-push hook: exits 0 when gitleaks is missing
  - `repo:.github/workflows/secret-scan.yml` - `4e0bc51651f1176608870f85c3f96edf269b1020ac6b01479619d47454d7fda1` - Secret scan workflow: triggers are push to main/master, pull_request, workflow_dispatch
  - `raw:checkpoint-C2-correction-01/receipts/gh-runs.tsv` - `cc9c086d29bb03176a8660d741c4e6878d69c8563ef026a9256ee3322f01fc8c` - read-only `gh api` GET of fork workflow runs, observed 2026-10-14T13:53:08Z: 58 Secret scan runs, all on head_branch main
  - `raw:checkpoint-C2-correction-01/receipts/facts-03-gitleaks-on-path.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `gitleaks-on-path` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-02-hooks-path.out` - `239b057f9b05bafd589c56bedb8337bbd4f8be338092c5acee42c243ef407811` - read-only command receipt `hooks-path` (argv/exit/time in facts-index.json)

## E-02 (P2 (Sol) / P3 (Opus)): Ticket 09 section 7.1: authority attribution and the 'inert on Node' scope

- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 148: "All edits fall under the user's 2026-10-11 rule"
- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 144: "inert on Node"
- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 146: "`setupFiles` entry for the anchor helper + 3 EditorPane mock aliases"
- Corrected statement: Not all 12 listed paths fall under the user's 2026-10-11 test-adaptation rule. That rule covers only the test-support and test-file edits made under it (on or after 2026-10-11). The Bun manifest and lock, the dispatcher, the vue-tsc launcher, `publicPackagesSetup.ts`, the `test.alias` entries and the two evaluation workflows were initial evaluation-toolchain configuration under the evaluation's own authority (ticket 02 allowlist and review, ticket 03 startup, tickets 07/08 and ci-refresh-01 dispatches); their commits predate the rule or were dispatched separately (attribution tables below, by commit). 'Inert on Node' holds for the helper's *effects*, not for its loading: `tests/support/bunMockImporterAnchor.ts` is registered through `setupFiles` for every runtime, so on Node the module is still imported and evaluated (imports, function definitions, `ownFile` at line 45). Both mutations are gated by `isBun` (line 43): the `vi.mock`/`unmock`/`doMock`/`doUnmock`/`importActual`/`importMock` wrapping (line 90) and the `ELECTRON_OVERRIDE_DIST_PATH` assignment (line 112). This is by reading the code; it was not re-measured here. The three `test.alias` entries in `vitest.config.ts` (line 48) are NOT Bun-gated and apply to Node runs; they match only the exact specifiers `./view/EditorViewMonaco.vue`, `../foundation` and `../shared`, which at HEAD occur only in `packages/plugin-ui/src/editor/EditorPane.test.ts`, `packages/plugin-ui/src/editor/EditorPane.vue` (scan receipt). That is harmless today but fragile for any future file using those specifiers.
- Limits: Attribution is by commit date and dispatch text; it does not re-judge any edit and changes no conclusion about the amount of adaptation. Commit ids are the post-rebase ids at HEAD.
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - committed ticket-09 report at HEAD
  - `raw:coordination/ticket04-adapt-01/dispatch.md` - `4c993d15c2fc693939a04aba0fc7e9486bbbeee0c0cdab0f7b841393c90f864b` - user rule of 2026-10-11 as dispatched (allowed items 1 and 2; scope of edits)
  - `raw:coordination/rebase-01/dockquit-adapt-dispatch.md` - `930872307734fabdf640a4a2b1cd93b6d6ede3bb7702a9b0b779607e302037b0` - the same rule restated for the dockQuitProbe round (commit a23e21b4)
  - `raw:coordination/ticket07-01/dispatch.md` - `706980ef432204c6e2abe5fe143fee7d1a34735354c03bd03a2a5d8923b47984` - Authority (user, 2026-10-10): Linux evaluation workflow
  - `raw:coordination/ticket08-01/dispatch.md` - `5e63f42f03732d5b90613df36caa56a88b4776f2e91558c1e001d789c6284db6` - Windows evaluation workflow (ticket 07 authority applies with 07 -> 08)
  - `raw:coordination/ci-refresh-01/dispatch.md` - `17c24fcd8ab977fa1fff50994d0ec146c2af21f6c2f3813bd1a8aa125ecba966` - refresh of both workflows at the rebased head
  - `raw:coordination/rebase-01/lock-refresh-dispatch.md` - `0c1c8936f4a40210d50b574e7fbe96e9c2e55165e166dc94e8f977a16ca7c16f` - bun.lock refresh method (ticket-02 derivation)
  - `repo:docs/toolchain-evaluation/bun/ticket-02/report.md` - `12c4709ac1a66c5dcdac27ff77e8cf3fe6430931476006abc7c8d86eb45da9bd` - ticket-02 report: 'Minimal adaptations and native lock conversion' allowlist (package.json, bun.lock, ...)
  - `repo:docs/toolchain-evaluation/bun/ticket-02/adaptation-01/REVIEW-PASS.md` - `a58b1b2b1b373c78ac50883dcacfcd2979e67497324ec50be36c76dee02d0dab` - ticket-02 adaptation-01 corrected E1 review: PASS
  - `repo:tests/support/bunMockImporterAnchor.ts` - `4a841c49478708767391529cb1239fd6dec088d346fa9029c0eef7f31f1a23ec` - helper at HEAD (gates at lines 90 and 112)
  - `repo:vitest.config.ts` - `8b4f0d09d12fc907b9a3371ad1605b73729734906fd56849cd369687ea2e1b50` - vitest.config.ts at HEAD (setupFiles line 46, alias line 48)
  - `raw:checkpoint-C2-correction-01/receipts/facts-20-attribution-log.out` - `b392230c9b9b6dd1947351b20a7f64068c28c9e369fe02bc3d5b5202aae7b18a` - read-only command receipt `attribution-log` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/alias-scan.json` - `3cc7997445375988f0bb905c046a782ae98f0094ac4087e34831c142cdbcdfb9` - descriptor-read scan of 1827 tracked source files (docs excluded) for the three alias specifiers

Evaluation's own authority (initial toolchain configuration):

| Paths | Nature | Commits (author date) |
|---|---|---|
| `package.json`, `bun.lock` | manifest workspaces/overrides/scripts and the native lock; lock refresh for the rebase | package.json: `985589b3` (2026-10-04), `1ca9538c` (2026-10-05), `5f7de440` (2026-10-10); bun.lock: `985589b3` (2026-10-04), `5e46f0b6` (2026-10-13) |
| `scripts/bun-evaluation.mjs`, `scripts/bun/vue-tsc.cjs` | dispatcher (incl. Windows re-entry) and vue-tsc launcher | dispatcher: `985589b3` (2026-10-04), `1ca9538c` (2026-10-05), `5f7de440` (2026-10-10), `dfaf170c` (2026-10-12); launcher: `1ca9538c` (2026-10-05) |
| `tests/support/publicPackagesSetup.ts` | test-support file, but every commit predates the 2026-10-11 rule, so it cannot have been made under it | `985589b3` (2026-10-04), `1ca9538c` (2026-10-05), `b88753f1` (2026-10-06) |
| `vitest.config.ts` `test.alias` entries (3 EditorPane ids) | added in the ticket-02 commit | `1ca9538c` |
| `.github/workflows/eval-bun-toolchain.yml`, `...-windows.yml` | evaluation-only tag-triggered workflows (authority: tickets 07/08 and ci-refresh-01 dispatches) | Linux: `a324e67d` (2026-10-11), `0bbb6e0f` (2026-10-14); Windows: `dfaf170c` (2026-10-12), `0bbb6e0f` (2026-10-14) |

Under the user's 2026-10-11 rule:

| Paths | Nature | Commits (author date) |
|---|---|---|
| `plugins/navide-git/tests/compositionBoundary.test.ts`, `plugins/navide-mini-ide/tests/packageBoundary.test.ts`, `tests/integration/plansBuildScripts.test.ts` | mechanical test-file edits (rule item 2) | compositionBoundary.test.ts: `7cbf97e6` (2026-10-11); packageBoundary.test.ts: `7cbf97e6` (2026-10-11); plansBuildScripts.test.ts: `7cbf97e6` (2026-10-11) |
| `tests/support/bunMockImporterAnchor.ts` | generic Bun-only test-support helper (rule item 1); the Electron-download guard was added by the later dockQuitProbe round, whose dispatch restates the rule | `7cbf97e6` (2026-10-11), `a23e21b4` (2026-10-13) |
| `vitest.config.ts` `setupFiles` entry | registers the helper (rule item 1 names this mechanism) | `7cbf97e6` (2026-10-11) |

## E-03 (P3): ci-refresh-01 Linux matrix: the 1,210 node-named alias count is the Windows figure

- Target: `repo:docs/toolchain-evaluation/bun/ci-refresh-01/report.md` line 72: "the only `node`-named executable observed (1,210 processes) is the candidate-owned alias `.../t07-head-bun/tmp/bun-node-<hash>/node`" (Linux matrix, heading at line 59)
- Corrected statement: In the Linux matrix the 1,210 figure does not belong. `evidence.json` records for Linux `bun_node_named_executables_inside_candidate_case = {}` (line 1014; outside the candidate case also `{}`) and observed executables `{"bun": 876, "esbuild": 15, "navide-plans": 25, "python3.12": 5}`, i.e. no node-named executable was observed on Linux. The 1,210 processes are the Windows candidate-owned alias: `bun_node_named_executables_inside_candidate_case = {"C:/t08-eval/t08-head-bun/tmp/bun-node-<hash>/node.exe": 1210}` (line 2133). The Linux sentence, as a statement about Linux, is superseded by the `evidence.json` value; the committed report is not edited and the Linux `PASS` rating of that row is unchanged in substance (no node-named executable outside the candidate case on either platform). Ticket 09 already used the `evidence.json` value.
- Limits: The census is the sampled correctness-job census recorded there; it is not an exhaustive child-process proof (see ticket 09 section 13).
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/ci-refresh-01/report.md` - `5555e37f84bbb2d60e233231867b16af7b6e96aa49cbab5d373e7bc9dda70a31` - committed ci-refresh-01 report
  - `repo:docs/toolchain-evaluation/bun/ci-refresh-01/evidence.json` - `68d5aaef9b9928655a8799eba71a7a0953c29f9d17e91e64bf48002f2de206d8` - committed ci-refresh-01 evidence.json; fields platforms/linux/... and platforms/windows/...
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - ticket 09 section 12 and section 8 item 7 already identify the discrepancy

## E-04a (P3): Ticket 09 matrix, macOS 'Supplementary suites' row: qualifier

- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 74: "| Supplementary suites (Mini-IDE, dev-backend, SDK/DOM) | **pass** `H` RB4: covered inside the 936-file root run"
- Corrected statement: The macOS cell's `pass` at HEAD means root-covered only: the Mini-IDE, dev-backend and SDK/DOM tests were executed inside the 936-file root run (RB4). Read the cell as `root-covered; standalone supplementary configs not-run at HEAD`. The standalone configs (for example Mini-IDE with its own `globalSetup`) ran only before the rebase. The recorded macOS Bun step list at HEAD is backend_sync, check, fixture_build, install, root, typecheck - there is no standalone supplementary step. The row's own text already says this; the erratum only fixes the short label. Matrix counts in `matrix.json` are not recomputed or changed here.
- Limits: Absence of a standalone step is read from the Bun exits receipt step names, not from an exhaustive listing of every receipt in the raw root.
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - committed ticket-09 report
  - `raw:rebase-01-test-04/retrieved/bun/receipts/exits.txt` - `fd52b259edf1c2c433344622c8b9b3a3fc3006da4f4c5469c5c3912c1b2f06cf` - macOS HEAD Bun step list and exit codes
  - `raw:rebase-01-test-04/receipts/retrieved-hashes.txt` - `0af1200065773a0f31875ad54c7f0d85089d6f16cc0cb5c851a065e022d7f1c0` - hash index of the retrieved macOS HEAD receipts

## E-04b (P3): Windows root-cold performance: robustness of the verdict with two exit-1 Node samples

- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 79: "2 Node root-cold samples exited 1 (worker RPC timeout) and stay in the median"
- Target: `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` line 127: "Windows Node root-cold has 2 of 5 samples that exited 1"
- Corrected statement: Recomputed from the raw receipt (5 measured pairs after the warm-up; Node = `base`): Node samples 640.469, 646.812, 665.062, 727.422, 753.468 s, of which the two exit-1 samples are 727.422, 753.468 s; Bun samples 372.109, 375.797, 405.422, 425.016, 426.75 s (all exit 1: the accepted `pluginExternalWorkspace` residual). Medians Node 665.062 s / Bun 405.422 s (-39.04%), as in the report. The verdict is robust to the two failed Node samples: Bun's slowest sample (426.75 s) is faster than Node's fastest (640.469 s) by 213.72 s (worst-case pairing -33.37%), so no pairing of the recorded samples approaches the +10% gate. The two exit-1 Node samples are the two slowest Node samples and the Node median (665.062 s) is itself an exit-0 sample; with them excluded the Node median of the remaining three would be 646.812 s and the ratio -37.32% (arithmetic on the same samples, not a new measurement). This supports 'no observed Bun slowdown above the gate'. It does not make the -39.0% a clean five-pair speedup magnitude (unequal successful-work coverage: two Node samples exit 1, all Bun samples exit 1), and it does not relabel the failures or imply a passing Windows correctness gate; the two failed samples stay disclosed and stay in the median as reported.
- Limits: Single hosted-runner attempt (one run); no extra samples were taken, as the dispatch forbids measurements. Warm-up pair excluded as in the report.
- Evidence:
  - `raw:ci-refresh-01/attempts/eval-bun-ci-08-09/artifacts/t08-performance-root-cold/performance/performance-root-cold.json` - `e186b78ab40e94707ddd8d503a5aecdc547e4cd6fe92705ba7e415db4af5068e` - raw root-cold performance receipt (eval-bun-ci-08-09, 12 sample records)
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - committed ticket-09 report

## E-05 (P3): Handoff notes

Handoff notes only; no committed statement is corrected.

### E-05a: Overlapping evaluation tag globs

`eval-bun-toolchain.yml` triggers on `eval-bun-ci-*` (line 39); `eval-bun-toolchain-windows.yml` on `eval-bun-ci-08-*` (line 45). A tag such as `eval-bun-ci-08-NN` pushed from a tree that contains both workflow files triggers both. The historical Windows attempts avoided this by removing the Linux workflow from the detached attempt tree (workflow header, line 11). Leaving both files on the branch is harmless only provided no matching tag is pushed from a tree that holds both.

  - `repo:.github/workflows/eval-bun-toolchain.yml` - `a0b90475b6f441d6f3e9169c9eeaba45a4036ccacbec5e07783bc4235b5702c7` - Linux workflow: tag glob at line 39
  - `repo:.github/workflows/eval-bun-toolchain-windows.yml` - `c849b6f92e34aecb0bb5853836e435f8cc1ff0fe42d607b18fbb803d08eca991` - Windows workflow: tag glob at line 45

### E-05b: Same-origin Bun checksum

Both workflows download the Bun archive and `SHASUMS256.txt` from the same release URL and compare them (Linux line 152ff., Windows line 1519ff.). That proves integrity in transit and version selection, not independent authenticity of the publisher artifact.

### E-05c: Unpinned pnpm install and major-tag actions

`pnpm` is installed with `npm install ... pnpm@10.0.0 --ignore-scripts` (exact version, no integrity digest recorded; Linux line 142, Windows line 1508). Actions are referenced by mutable major tag, not commit digest: `actions/checkout@v7`, `actions/download-artifact@v7`, `actions/setup-go@v7`, `actions/setup-node@v7`, `actions/setup-python@v7`, `actions/upload-artifact@v7`, `astral-sh/setup-uv@v7`. Of these, `actions/checkout@v7`, `actions/setup-go@v7`, `actions/setup-node@v7`, `actions/setup-python@v7`, `actions/upload-artifact@v7`, `astral-sh/setup-uv@v7` also appear in the project's own `ci.yml` with the same major-tag convention; `actions/download-artifact@v7` does not appear in `ci.yml` and was not compared. Hardening belongs to any future evaluation workflow, not to this erratum.

  - `repo:.github/workflows/ci.yml` - `1dcfb9c16b9180dd850a548f83497ccc483ac2429d309a3146dc6c61db9fdcda` - project ci.yml: same major-tag convention for the common actions

### E-05d: rebase-01-test-04 has receipts but no RESULTS.md

`rebase-01-test-01`, `-02` and `-03` each have a `RESULTS.md`; `rebase-01-test-04` has `receipts/`, `retrieved/`, `scripts/`, `source/` and `host-ssh-log/` but no `RESULTS.md`. Its results exist only in the retrieved receipts (for example `retrieved/bun/receipts/exits.txt` and `retrieved/*/vitest-json/root.json`). Ticket 09 cites them as RB4. An index would help navigation; it would not add measurements.

  - `raw:rebase-01-test-04/receipts/retrieved-hashes.txt` - `0af1200065773a0f31875ad54c7f0d85089d6f16cc0cb5c851a065e022d7f1c0` - hash index of the retrieved receipts
  - `raw:rebase-01-test-03/RESULTS.md` - `d7ece0fd2f82cdc2d89879d04ccf82d0f0611dcb3dbf2222fb7024f18d1cdc84` - sibling RESULTS.md for comparison

### E-05e: HEAD position relative to upstream main

At observation time 2026-10-14T13:56:01Z, the local remote-tracking ref `upstream/main` (not refreshed by this worker, no fetch) was `11178c3c06d4c77c0c2a097590ae03ca5e1c0ea0` (committer date 2026-10-14T02:52:42+08:00). `HEAD` `97c60accfb8967b5f9024ae4bf04f8154bebb6b9` relative to it: 19 commits only on HEAD, 18 commits only on `upstream/main`; merge-base `9af042ec3683b21a67da68b2c9e9352b0e612dbf` (the commit all evidence is pinned to); HEAD is not an ancestor of `upstream/main`. All evaluation evidence is pinned to that merge-base (plus the recorded overlay); nothing was re-tested against the newer upstream commits.

  - `raw:checkpoint-C2-correction-01/receipts/facts-01-head.out` - `2868aebca430f06be99a9e080cd9f3c0daeabcbbc2b788553d54173f28374577` - read-only command receipt `head` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-07-upstream-main-ref.out` - `a4bdcb8f649f0b2b4ef1ebc96deafd11a73575630de8d3bd88b1bdcbae975b0f` - read-only command receipt `upstream-main-ref` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-08-head-vs-upstream-main-counts.out` - `9cd1c1a9a6ae52aae049dda91164c4c686314c021d471682a8e3c61e3b928ae5` - read-only command receipt `head-vs-upstream-main-counts` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-09-head-upstream-merge-base.out` - `224caa64f635e33b3d7e783c25b89d8c384d397f394d4d55cc73a0bc98455ac1` - read-only command receipt `head-upstream-merge-base` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-10-head-is-ancestor-of-upstream-main.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `head-is-ancestor-of-upstream-main` (argv/exit/time in facts-index.json)

### E-05f: Pre-existing `git diff --check` whitespace (accepted)

`git diff --check` reports whitespace in the evaluation files only (blank line at EOF in both evaluation workflows; trailing whitespace at ticket-08 report lines 776 and 785); none in product, test or script files. The coordinator accepted these as pre-existing in reviewed, anchor-bound bytes (byte-identical before the rebase). They are recorded here, not fixed. This erratum did not re-run the command; it cites the existing records.

  - `raw:coordination/rebase-01/push-decision.md` - `addc7733a82f5c8ed1fef852aa6171a4c6e8da5db344381d807dd92d2c65bd2c` - coordinator acceptance of the four findings
  - `raw:rebase-01/31-STOP-report.md` - `3ef83ee85f6dc4f9eba9f5b2b6a6d77f48ebfca7403de6e07f5b7a397f173731` - the diff --check output as recorded at the rebase
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - section 7.2 item 12 records the same finding

## E-06 (P3 (Opus RR-1) / P2 (Sol)): Cleanup inventory (record only; the user decides; nothing was removed)

Record only. Nothing was removed, moved or changed. Sizes and counts come from read-only receipts (command, exit code and UTC time in `facts-index.json`).

| ID | Item | Location | Size / count | How to remove (not run) | Note |
|---|---|---|---|---|---|
| C-01 | UTM guest `navide2-guest.utm` | `~/Library/Containers/com.utmapp.UTM/Data/Documents/navide2-guest.utm` | 104.07 GiB (du, 109126156 KiB) | stop the VM, then delete it in UTM (or `rm -r` the bundle) | disposable evaluation guest; macOS HEAD/pre-rebase runs ran here; needed only if macOS evidence is to be re-run |
| C-02 | UTM base clone `navide2-guest-base.utm` | `~/Library/Containers/com.utmapp.UTM/Data/Documents/navide2-guest-base.utm` | 32.12 GiB (du, 33680096 KiB) | delete in UTM (or `rm -r` the bundle) | base for re-creating the guest; keep if the guest may be rebuilt without re-installing macOS |
| C-03 | Cached macOS IPSW `UniversalMac_27.0.1_26A434_Restore.ipsw` | `~/Library/Containers/com.utmapp.UTM/Data/Library/Caches/UniversalMac_27.0.1_26A434_Restore.ipsw` | 24.81 GiB (26637307067 bytes) | `rm` the file (re-downloadable) | mode -rw-------, owner slighter12, modified Oct 10 16:44:09 2026 |
| C-04 | Evaluation SSH key dir (private key present; contents not read or hashed) | `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-03-macos-guest-v1/ssh/` | 12 KiB (files: id_ed25519, id_ed25519.pub, known_hosts) | after the guest is deleted: `rm -r` the `ssh/` dir (inside the raw evidence root; it was frozen with the ticket-03 evidence) | authorizes access to the guest only; ~/.ssh config entries for the guest were NOT inspected by this worker |
| C-05 | `eval-bun-ci-*` tags on origin and locally | origin slighter12/Navide (refs/tags) and the local clone | 21 on origin, 21 local: 07-01, 07-02, 07-03, 07-04, 07-05, 07-06, 07-07, 07-08, 07-09, 07-10, 07-11, 08-01, 08-02, 08-03, 08-04, 08-05, 08-06, 08-07, 08-08, 08-09, 08-10 | `git push origin --delete refs/tags/<tag> ...` (remote, needs user authorization) and `git tag -d <tag> ...` (local) | attempt commits were built detached (ticket 07 dispatch), so each tag is expected to be the only ref to its commit (not verified per tag); deleting both the remote and local tag would then leave the commit unreachable. Logs and artifacts they produced stay in the raw root |
| C-06 | Fork workflow runs | `https://github.com/slighter12/Navide/actions` | 144 runs total at observation; 21 are evaluation runs (tag-triggered, including 2 invalid-workflow runs with no head_branch); 123 others (CI, Secret scan, Linux backend sandbox, OpenConsole probe) are not evaluation-owned | `gh api -X DELETE repos/slighter12/Navide/actions/runs/<run_id>` per run (needs user authorization); ids are in the receipt | run logs are also in the raw root attempts directories |
| C-07 | Fork Actions artifacts | same fork repository | 335 artifacts, 5218565159 bytes (4.86 GiB): 95 from evaluation tags (5121536109 bytes, 4.77 GiB, expire 2026-11-10 to 2026-11-12); 240 from `main` CI runs (97029050 bytes, expire 2026-10-15 to 2027-01-10, not evaluation-owned); none expired at observation | `gh api -X DELETE repos/slighter12/Navide/actions/artifacts/<artifact_id>`, or let them expire | uploaded readiness artifacts include token-derived metadata files (digest/length, per the coordinator acknowledgement); the user decides whether to delete them early |
| C-08 | Local branch `backup/issue-2-pre-rebase-01` | local clone only (no remote branch contains it) | 1 ref -> `a290cf05068d2b51f466788de8a3805bf2099457` | `git branch -D backup/issue-2-pre-rebase-01` (needs user authorization) | the only ref that contains a290cf05 (the pre-rebase tip); other `backup/*` refs belong to other efforts and are not listed here |
| C-09 | Known unreachable dry-run commits `b4757ef7`, `0c30a074`, `596d53c8` | local object store (no ref contains any of them) | 3 commit objects | no targeted removal; `git reflog expire --expire-unreachable=now --all && git gc --prune=now` would prune ALL unreachable objects in the shared repository, including other efforts', so it is not recommended without the user's decision | recorded as ci-refresh-01 deviations (ticket 09 section 12) |
| C-10 | `/tmp/dc.txt`, `/tmp/dc2.txt` (ticket 09 `git diff --check` output) and `/tmp/.noop` (created by this worker, see deviations) | `/tmp` | .noop 1449 B, dc.txt 538 B, dc2.txt 148 B | `rm /tmp/dc.txt /tmp/dc2.txt /tmp/.noop` | outside any raw root; macOS may clear /tmp on its own schedule |
| C-11 | Evaluation raw evidence root | `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/` | 63.38 GiB (du -k -d1, 66455872 KiB); largest: ticket-02 23.74 GiB, ticket-08-01 18.14 GiB, ticket-07-01 5.48 GiB, ticket-03-console-v2 5.20 GiB, ci-refresh-01 3.20 GiB | archive first if the audit record is to be kept; then `chmod -R u+w` the restricted dirs (ticket-03 is mode 500; several are 700) and `rm -rf` | sole audit record; tracked reports cite its paths and hashes; this correction's own raw root (checkpoint-C2-correction-01) is inside it |
| C-12 | macOS Python bytecode caches written outside the raw root during utm-dmg-verify-01 (D9) | `~/Library/Caches/com.apple.python` | 1184 KiB, 71 files | `rm -r ~/Library/Caches/com.apple.python` (regenerated on demand) | disclosed, not repaired, in the utm-dmg-verify-01 acceptance; the directory is shared with any use of the system Python, so contents are not exclusively evaluation-owned |

- Not covered: `~/.ssh` config entries and the UTM VM registry were not inspected
- Not covered: the pushed branch `eval/issue-2-bun-toolchain` and the worktree are not cleanup candidates in this inventory
- Evidence:
  - `raw:checkpoint-C2-correction-01/receipts/facts-23-utm-guest-du-k.out` - `493ea1029b8e98d16f70dd2f9bdb8ba807a7802c940550076485f1d9815ce4ec` - C-01: read-only command receipt `utm-guest-du-k` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-22-utm-documents-ls.out` - `6264faa75c0324f3cbc52ba2317d973959b4e048f9b26a57f7261fd224c6f111` - C-01: read-only command receipt `utm-documents-ls` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-24-utm-guest-base-du-k.out` - `301d6ba0dea55aebb298634f2d5c980863104397e7736633761178042bd0bccf` - C-02: read-only command receipt `utm-guest-base-du-k` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-21-ipsw-stat.out` - `d942669bbdd6569268096f00dd803f21c4888ce1a45129f8b589d30b781b634c` - C-03: read-only command receipt `ipsw-stat` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-25-ssh-dir-ls.out` - `708b8e2a41a14ce958fa04aaca8d1e66aa851cc975e2a4b35889da6ddddb5baf` - C-04: read-only command receipt `ssh-dir-ls` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-26-ssh-dir-du-k.out` - `0f65bfca41e46b4454833ae2a68ad4d20ed75635ba63e9f7cb4c60bee617fa33` - C-04: read-only command receipt `ssh-dir-du-k` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-04-origin-tags.out` - `3b6f36301745896c2c6b5531aa1c35a46e59cbcf83c77f3b771cf5f115022170` - C-05: read-only command receipt `origin-tags` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-06-local-tags.out` - `29b82db567f3b2b5450b6993c66f06e72618547cb73823936da59446a3b7275c` - C-05: read-only command receipt `local-tags` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-05-origin-branch.out` - `5e09e768f62b0db438b21d90972c5a1aeb4708e946c5013ce4080d10b29cbe8e` - C-05: read-only command receipt `origin-branch` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/gh-runs.tsv` - `cc9c086d29bb03176a8660d741c4e6878d69c8563ef026a9256ee3322f01fc8c` - C-06: receipt for C-06
  - `raw:checkpoint-C2-correction-01/receipts/gh-artifacts.tsv` - `46c60e6f5475fbd3aafc3f45c3206bdb0a4fc2d2e4c314c7796ca8d66a1b6a96` - C-07: receipt for C-07
  - `raw:checkpoint-C2-correction-01/receipts/facts-11-backup-ref.out` - `4e5a9274125be75a39da5bf2e385eed5b80eb946afb9b49e50e9016ddefe49e0` - C-08: read-only command receipt `backup-ref` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-12-backup-ref-contained-in-refs.out` - `e9633e0d8eb12fe01356c1a64ba4f03824dc9ffe09c40057c989051399b96423` - C-08: read-only command receipt `backup-ref-contained-in-refs` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-13-backup-ref-contained-in-remote-branches.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - C-08: read-only command receipt `backup-ref-contained-in-remote-branches` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-14-unreachable-b4757ef7.out` - `50836eee574ecff79dea3b4fd40673d7d000f7a5f177d8a6a3000b59c78383b8` - C-09: read-only command receipt `unreachable-b4757ef7` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-15-unreachable-0c30a074.out` - `50836eee574ecff79dea3b4fd40673d7d000f7a5f177d8a6a3000b59c78383b8` - C-09: read-only command receipt `unreachable-0c30a074` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-16-unreachable-596d53c8.out` - `50836eee574ecff79dea3b4fd40673d7d000f7a5f177d8a6a3000b59c78383b8` - C-09: read-only command receipt `unreachable-596d53c8` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-17-unreachable-b4757ef7-refs.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - C-09: read-only command receipt `unreachable-b4757ef7-refs` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-18-unreachable-0c30a074-refs.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - C-09: read-only command receipt `unreachable-0c30a074-refs` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-19-unreachable-596d53c8-refs.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - C-09: read-only command receipt `unreachable-596d53c8-refs` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-27-tmp-ls.out` - `7b0c031e356ef0ce1c9174c84042d04f3fe0003572e2f36fdb8a361e0c83237a` - C-10: read-only command receipt `tmp-ls` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/du-raw-root-d1-r2.txt` - `090ef9bd45700fb520456461be89a837064560452d38dfcc0c649839ea42810d` - C-11: receipt for C-11
  - `raw:checkpoint-C2-correction-01/receipts/facts-28-apple-python-cache-du-k.out` - `45be637cee68610c24e6d5cce7b079eb5c01f91d524a9c364253828ca7f559a2` - C-12: read-only command receipt `apple-python-cache-du-k` (argv/exit/time in facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/facts-29-apple-python-cache-files.out` - `f160a76c5f49555aa37eabcfa64f8e670e01e79c2668207dd20a2823b40501e1` - C-12: read-only command receipt `apple-python-cache-files` (argv/exit/time in facts-index.json)
  - `raw:coordination/utm-dmg-verify-01/acceptance.md` - `80b67c703dd5ae5f51b5c5908002fea81ae2ef013d503b91c0b402f59830879e` - disclosure of the ~/Library/Caches/com.apple.python write (D9)
  - `raw:checkpoint-C2-correction-01/receipts/du-raw-root-d1.stderr` - `774cc2dee1c03319a035c583c0461fb2663327fd1c34f6836662c3b3328849cc` - first du attempt failed on conflicting flags (usage error); superseded by `du -k -d1`

## Worker deviations (as they occurred up to rendering)

- The dispatch file was first read with the standard Read tool, not the descriptor reader (its SHA-256 was computed with shasum before and matched the dispatched value); every later evidence read used `tools/fdread.py` (ancestor-relative descriptors, `O_NOFOLLOW|O_NONBLOCK`, pre/post `fstat`) or `tools/fdgrep.py`, except the shell commands listed next.
- Read-only shell commands that were not descriptor reads and are not all receipted: `ls`/`find`/`stat`/`du` listings of the raw root, `rebase-01-test-04`, `~/Library/Containers/com.utmapp.UTM`, `~/Library/Caches` and `~/Downloads` (names/sizes only; the `~/Downloads` listing was piped to `grep -i ipsw` while searching for the IPSW), `git` read-only queries, `gh api` GETs, `date`, `command -v gitleaks`. The facts used in the inventory were re-collected by `tools/collect_facts.py` into receipts.
- `tools/fdgrep.py` failed three times, writing nothing: once because `-i` was taken as the pattern and the real pattern as a file (assertion on a non-absolute path), twice on paths that do not exist (`docs/.../rebase-01/report.md`, `docs/.../rebase-01/evidence.json`, which are not tracked). One `du -sk -d1` call failed on conflicting flags (usage error; receipts `receipts/du-raw-root-d1.txt` and `.stderr` kept, superseded by `du-raw-root-d1-r2.txt`).
- I accidentally created `/tmp/.noop` by piping `git ls-remote` output through `tee /tmp/.noop` (it holds that tag list); it is outside the raw root, left in place, and listed in C-10.
- No NOREAD or credential-bearing file (`ws-token.json`, connection files, key contents) was opened, hashed or read; the SSH key directory was listed by name and size only. `du -k -d1` and directory listings enumerate paths and sizes, not contents.
- Earlier renderings are kept, superseded after I found wording/layout defects in them on review: `payload/r1/` (renderer copy `tools/render_r1_backup.py`) and `payload/r2/` (renderer copy `tools/render_r2_backup.py`); nothing was deleted. The delivered rendering is the one named in the anchor.
- The tool wrapper may keep output copies under `~/Library/Application Support/rtk/tee/` (harness behaviour, not issued by me).

## Sources and scope

- `raw:coordination/checkpoint-C2/correction-01-dispatch.md` - `d644de981b78c84a242a25deafb63a58ea649035e30f6035e1a5d1bf86e1803d` - correction-01 dispatch (authority and scope for this erratum)
- `raw:checkpoint-C2/sol-independent-round1.md` - `4143060f01e2d2b0eb4dba6f0df6865c1169109f6f957d9d0d09cea1461a1d1d` - checkpoint C2 reviewer report
- `raw:checkpoint-C2/sol-adversarial-round1.md` - `d9725998dfe69b42ba480305d74336460ee29cd82e98f69f32e943e7552ed306` - checkpoint C2 reviewer report
- `raw:checkpoint-C2/opus-independent-round1.md` - `871fab46a7a11e1e1f96a5c07727676e721c3d812bd4824d8189125dc9262c8d` - checkpoint C2 reviewer report
- `raw:checkpoint-C2/opus-adversarial-round1.md` - `f6bc9f06dd88c4376b0f32a7c863301dd29a12881e416be919be4d71c39ac458` - checkpoint C2 reviewer report
- Not applied: Opus SEC-2 (coordinator acknowledgement wording), EI-1 (ticket 07 deviation 02 wording) and EI-3 (stale registry) are in the reviewers' lists but not in the dispatch; not recorded here
