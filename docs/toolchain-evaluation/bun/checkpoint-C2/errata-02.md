# Checkpoint C2 errata 02 (additive)

Additive record at HEAD `97c60accfb8967b5f9024ae4bf04f8154bebb6b9`, generated 2026-10-14T14:12:29Z. `errata-01.*` and `anchor.json` (v1) are not modified; this document corrects two statements of errata-01 (E-05e and the cleanup cautions of E-06) and cites evidence by path and SHA-256 (computed). Path convention: `repo:` is relative to the worktree at HEAD; `raw:` is relative to the raw root `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/`. Machine-readable twin: `errata-02.json`. Basis: the correction-02 dispatch, the Sol phase-3 HOLD on two P3 clarifications and the Opus phase-3 PASS WITH QUALIFICATIONS.

| Entry | Severity | Subject |
|---|---|---|
| E2-01 | P3 | E-05e correction: which evidence is at-head and which is historical pre-rebase |
| E2-02 | P3 | C-12: shared bytecode cache directory - `rm -r` withdrawn |
| E2-03 | P3 | C-04 (SSH evidence dir) and C-11 (raw evaluation root) hold frozen evidence: no removal before an explicit preservation decision |
| E2-04 | P3 | Every command in the errata-01 cleanup inventory is informational, not approved for execution |

## E2-01 (P3): E-05e correction: which evidence is at-head and which is historical pre-rebase

- Target: `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` line 135: "All evaluation evidence is pinned to that merge-base (plus the recorded overlay); nothing was re-tested against the newer upstream commits." (E-05e heading at line 133)
- Corrected statement: The quoted sentence is too broad and is withdrawn. It conflates two things and omits the pre-rebase evidence. Per the ticket-09 section 3 classification (class H = measured at the current tree, class P = pre-rebase), the evidence splits as follows. (1) AT-HEAD evidence (class H, ticket-09 lines 54, 55, 56, 57): the Bun candidate is the tree of `b6caf8c8`. `HEAD` differs from `b6caf8c8` only in the two evaluation workflows, the ci-refresh-01 report/evidence and (after `0bbb6e0f`) the ticket-09 report/evidence; the product, test and script tree is the same (computed from the receipts below). The Node control is `9af042ec` plus the recorded runtime-neutral overlay (the macOS rebase-01-test-04 overlay manifest lists 7 entries), NOT the candidate. That covers: macOS rebase-01-test-04 (RB4, candidate `b6caf8c8`, control `9af042ec` + overlay); ci-refresh-01 Linux `eval-bun-ci-07-11` and Windows `eval-bun-ci-08-09`/`08-10` (CIR; each tag commit differs from `b6caf8c8` only in the two workflow files); the dockquit helper (DQ, commit `a23e21b4`, an ancestor of `b6caf8c8`); and the bun.lock refresh (RBL, valid against the `8027cd8a` manifest; the fork-sync merge touched neither `package.json` nor `pnpm-lock.yaml`). The parenthetical "(the commit all evidence is pinned to)" that follows the merge-base in the same errata-01 paragraph is withdrawn for the same reason. The correct statement: at-head evidence is pinned to the candidate tree `b6caf8c8` measured against a `9af042ec`+overlay Node control; the merge-base of HEAD and `upstream/main` happens to be `9af042ec`, the same commit as the Node-control base. The rest of E-05e (the `upstream/main` observation and the ahead/behind counts) stands. (2) HISTORICAL pre-rebase evidence (class P, ticket-09 lines 45, 46, 47, 48, 49, 50, 51, 52, 53) was measured on earlier frozen inputs. Apart from the T01 baseline (`f4e9fd50`, an ancestor of both HEAD and the backup branch, history only), those inputs are not in HEAD's history: `e5578080`, `7ce08fec`, `5a6b78ce`, `e94c0548`, `2c2707a3`, `58508e2b`, `b5ea6b65`, `8c674ca6` are ancestors of the local `backup/issue-2-pre-rebase-01` and not of HEAD (computed). That is the macOS build/startup (`5a6b78ce`), suite/lifecycle (`e94c0548`, `2c2707a3`), consumers (`2c2707a3`) and performance (`58508e2b`) evidence, the ticket-02 guard/install/security evidence (`e5578080`, `7ce08fec`), checkpoint B (`b5ea6b65`), and the ticket-07/08 pre-refresh runs (`8c674ca6` and the Windows 08-08 attempt, the latter's commit id not checked here). Ticket 09 already uses them only under its stated rule: pre-rebase evidence counts only where the rebase did not change the measured input, never alone for an adoption-critical claim whose input changed. (3) Upstream position, unchanged from errata-01: at 2026-10-14T14:10:06Z the unrefreshed local `upstream/main` was `11178c3c`; HEAD was 19 commits ahead and 18 behind it. No at-head evidence was measured against those newer upstream commits, and the pre-rebase evidence was not measured against upstream `9af042ec` at all.
- Limits: Class assignments are ticket 09's own (section 3 table) and are cited, not re-judged. The Windows ticket-08 pre-refresh attempt (08-08) commit id was not looked up. `0bbb6e0f` is the ci-refresh-01 commit and `97c60acc` (HEAD) adds the ticket-09 documents. The ticket-09 sentence that HEAD `0bbb6e0f` differs from `b6caf8c8` in 4 paths is reproduced by the diff receipt; the diff to HEAD `97c60acc` adds the 2 ticket-09 files.
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` - `3e3561702f00fb65b8b04f670770dbeda8cdb379a7e81d96af67ef99f828000c` - errata-01.md as anchored (unchanged)
  - `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.json` - `e50c90e2d9c108508f8ed69c60c8cc7b00d863ce97bc9c496d220f00771e7a5b` - errata-01.json as anchored (unchanged)
  - `repo:docs/toolchain-evaluation/bun/ticket-09/recommendation-02/report.md` - `d0c850040ea95922b4db936e6f602e72c63c498a3b315e5a67087c56b486eeb6` - committed ticket-09 report: section 3 'Snapshot consistency' table (lines 43-57) classifies each evidence set
  - `raw:rebase-01-test-04/receipts/host-inputs.txt` - `7631016c8b03906872fb6ce370d1f9b457f7782b09fddf900ed8e0f5b4ed2d53` - rebase-01-test-04 host inputs: Node control tarball `source-node-9af042ec-overlay.tar`, Bun candidate tarball `source-bun-b6caf8c8.tar`
  - `raw:rebase-01-test-04/receipts/overlay-manifest.txt` - `e015620a9b599e6de53f24c1d4ea50aa088f9a00382e918d44500e482c4e8f8a` - overlay manifest: 7 entries
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-003-b6caf8c8-full.out` - `123d052c82541b719d5432cfb9a702523144b1e1122235c40996e807fd4ee91a` - read-only command receipt `b6caf8c8-full` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-004-b6caf8c8-log.out` - `6fade2920764911bfc385184cc54ff02fcbebe212c78207a86a60d9077beab45` - read-only command receipt `b6caf8c8-log` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-005-0bbb6e0f-log.out` - `ad464abc4e10baf45ac03f83f71c7239b6e2ea9a426c2ec7dfceeaa0503b2b8b` - read-only command receipt `0bbb6e0f-log` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-006-9af042ec-log.out` - `3c7d955ec407a8348a9f366cbed06eb88ca03a580d1181ad05cb98d6bc366597` - read-only command receipt `9af042ec-log` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-007-anc-9af042ec-b6caf8c8.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-9af042ec-b6caf8c8` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-008-anc-a23e21b4-b6caf8c8.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-a23e21b4-b6caf8c8` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-009-anc-b6caf8c8-0bbb6e0f.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-b6caf8c8-0bbb6e0f` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-010-anc-0bbb6e0f-head.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-0bbb6e0f-head` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-011-diff-b6caf8c8-0bbb6e0f.out` - `e7dd4ed9e73d101f54e6388d29d9036ac9e8c3b9c929512d7581cad043e1506d` - read-only command receipt `diff-b6caf8c8-0bbb6e0f` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-012-diff-0bbb6e0f-head.out` - `55d17170d02ba7005408eea51585f03f3ee541cd44f7fbc8507a96992ba5df6f` - read-only command receipt `diff-0bbb6e0f-head` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-013-diff-b6caf8c8-tag-07-11.out` - `cbd46221909d9666e4566929f4cb9b20ec62ce374898aa34a054356c05d60a70` - read-only command receipt `diff-b6caf8c8-tag-07-11` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-014-diff-b6caf8c8-tag-08-09.out` - `cbd46221909d9666e4566929f4cb9b20ec62ce374898aa34a054356c05d60a70` - read-only command receipt `diff-b6caf8c8-tag-08-09` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-015-diff-b6caf8c8-tag-08-10.out` - `cbd46221909d9666e4566929f4cb9b20ec62ce374898aa34a054356c05d60a70` - read-only command receipt `diff-b6caf8c8-tag-08-10` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-016-tag-07-11-sha.out` - `20ee313b58ebbe9215270dd04013f9fba59a27ebd6bd568c75d894e1eb7ea257` - read-only command receipt `tag-07-11-sha` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-017-tag-08-09-sha.out` - `b6b0d12635257f19539fb8f20d610f7811f3146c9258c50298b2e36b6611b961` - read-only command receipt `tag-08-09-sha` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-018-tag-08-10-sha.out` - `825fbe4d1694e758d5fc5eb1c90ee8c41637a75359bfc0c266986b90c7490cc9` - read-only command receipt `tag-08-10-sha` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-022-backup-ref.out` - `4e5a9274125be75a39da5bf2e385eed5b80eb946afb9b49e50e9016ddefe49e0` - read-only command receipt `backup-ref` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-027-anc-e5578080-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-e5578080-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-028-anc-e5578080-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-e5578080-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-030-anc-7ce08fec-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-7ce08fec-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-031-anc-7ce08fec-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-7ce08fec-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-033-anc-5a6b78ce-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-5a6b78ce-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-034-anc-5a6b78ce-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-5a6b78ce-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-036-anc-e94c0548-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-e94c0548-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-037-anc-e94c0548-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-e94c0548-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-039-anc-2c2707a3-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-2c2707a3-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-040-anc-2c2707a3-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-2c2707a3-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-042-anc-58508e2b-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-58508e2b-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-043-anc-58508e2b-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-58508e2b-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-045-anc-b5ea6b65-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-b5ea6b65-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-046-anc-b5ea6b65-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-b5ea6b65-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-048-anc-8c674ca6-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-8c674ca6-HEAD` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-049-anc-8c674ca6-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-8c674ca6-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-024-anc-f4e9fd50-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-f4e9fd50-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-025-anc-f4e9fd50-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-f4e9fd50-backup` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-051-anc-8027cd8a-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-8027cd8a-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-052-anc-8027cd8a-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-8027cd8a-backup` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-054-anc-9af042ec-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-9af042ec-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-055-anc-9af042ec-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-9af042ec-backup` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-057-anc-b6caf8c8-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-b6caf8c8-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-058-anc-b6caf8c8-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-b6caf8c8-backup` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-060-anc-a23e21b4-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-a23e21b4-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-061-anc-a23e21b4-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-a23e21b4-backup` (exit 1; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-063-anc-0bbb6e0f-HEAD.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-0bbb6e0f-HEAD` (exit 0; argv/time in c02-facts-index.json)
  - `raw:checkpoint-C2-correction-01/receipts/c02-facts-064-anc-0bbb6e0f-backup.out` - `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` - read-only command receipt `anc-0bbb6e0f-backup` (exit 1; argv/time in c02-facts-index.json)

At-head evidence (class H), as classified by ticket 09 section 3:

| ID | Evidence | Measured at | Inputs changed by rebase/merge? | Use in ticket 09 | Ticket-09 line |
|---|---|---|---|---|---|
| RBL | RBL bun.lock refresh | `8027cd8a` manifest | lock stays valid: merge did not touch `package.json` or `pnpm-lock.yaml` (computed) | used | 54 |
| RB4 | RB4 macOS root at HEAD tree | `b6caf8c8` | none | used | 55 |
| DQ | DQ dockquit helper | `a23e21b4` | part of HEAD | used | 56 |
| CIR | CIR Linux 07-11, Windows 08-09/08-10 | `b6caf8c8` tree | HEAD differs from `b6caf8c8` only in 2 workflows and ci-refresh docs (computed) | used | 57 |

Historical pre-rebase evidence (class P), as classified by ticket 09 section 3:

| ID | Evidence | Measured at | Inputs changed by rebase/merge? | Use in ticket 09 | Ticket-09 line |
|---|---|---|---|---|---|
| T01 | T01 baseline | `f4e9fd50` | baseline source replaced by the upstream-based control | history only | 45 |
| T02A | T02A E1 guard, install, security | `e5578080`, `7ce08fec` | guard block byte-identical to the ticket-02 commit (computed); override block and the whole dependency set changed, lock regenerated | guard rejection kept; security and lifecycle results not carried | 46 |
| T03S/T03C | T03S/T03C builds and dev startup | `5a6b78ce` | yes: main-process files, Electron, backend, dependencies | not used at HEAD | 47 |
| T04A | T04A macOS suite, lifecycle | `e94c0548`, `2c2707a3` | yes: suite 836 to 936 files, packaged tests 124 to 136, Electron | root suite replaced by RB4; lifecycle not-run at HEAD | 48 |
| T05 | T05 macOS consumers | `2c2707a3` | yes: public package sources and schemas changed (manifests identical) | not used at HEAD | 49 |
| T06 | T06 macOS performance | `58508e2b` | yes: inventory, dependencies | shown as pre-rebase only | 50 |
| CKB | CKB checkpoint B | `b5ea6b65` | review of tickets 03 to 06 | qualifications carried forward | 51 |
| T07 | T07 Linux 07-09 | `8c674ca6` | superseded by CIR (same findings re-observed at HEAD) | history | 52 |
| T08 | T08 Windows 08-08 | pre-rebase | superseded by CIR; CIR records C2/C3/readdir/native-dependency findings as unchanged | history; Windows-only findings F3 to F10 cited with that limit | 53 |

## E2-02 (P3): C-12: shared bytecode cache directory - `rm -r` withdrawn

- Target: `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` line 168: "`rm -r ~/Library/Caches/com.apple.python` (regenerated on demand)"
- Corrected statement: The `rm -r ~/Library/Caches/com.apple.python` suggestion in errata-01 C-12 is withdrawn: the directory is shared (errata-01 itself says its contents are not exclusively evaluation-owned) and must be left alone. File-level ownership can be shown for only 3 of its 71 files: the three D9 files `Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/glob.cpython-39.pyc`, `Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/fnmatch.cpython-39.pyc`, `Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/datetime.cpython-39.pyc` (D9 names them and the write time 2026-10-10T08:26:24Z; the receipt shows each exists with that exact mtime and no other file shares it). Only those 3 files are candidates for removal, and only by explicit user decision, with the caveat below; the other files are not attributed and are not candidates. If the user makes no such decision, leave the directory unchanged.
- Limits: The other 68 files are not attributed to utm-dmg-verify-01 (mtime buckets by UTC date: {"2026-09-20": 16, "2026-10-08": 25, "2026-10-11": 27}). 16 of them are dated 2026-09-20, before the earliest evaluation date I looked at (2026-10-03, the ticket-02 acceptance record); the rest fall in the evaluation period and are likewise not attributed; one (`Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/ticket-05-01/scripts/t05-run.cpython-39.pyc`) is a bytecode cache of an evaluation ticket-05 script path and is not covered by D9, and whether that one was disclosed elsewhere was not checked. None of the other files was attributed to any owner. Name plus exact mtime match is evidence, not proof, of sole authorship: the receipt does not show which process created each file, and the same system Python may legitimately recreate them on a later run. Python 3.9 regenerates these caches on demand, so removal is low-risk, but that is not an authorization.
- Evidence:
  - `raw:checkpoint-C2-correction-01/receipts/c02-cache-listing.json` - `52b78f67ae014c4cf9477ea0409bad2846ce05c914d80a86d35e5d5186f82961` - metadata-only (lstat) listing of ~/Library/Caches/com.apple.python: 71 files, 23 directories, observed 2026-10-14T14:10:07Z
  - `raw:ticket-03-macos-guest-v1/utm-verify-01/REPORT.md` - `ae71798391af4d2cf8585e3f05aa0bd5aba690376baad985faa80c3d72a5dd0b` - utm-verify-01 REPORT.md, deviation D9: names the three files and the write time (line 232)
  - `raw:coordination/utm-dmg-verify-01/acceptance.md` - `80b67c703dd5ae5f51b5c5908002fea81ae2ef013d503b91c0b402f59830879e` - coordinator acceptance: D9 disclosed, not repaired
  - `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` - `3e3561702f00fb65b8b04f670770dbeda8cdb379a7e81d96af67ef99f828000c` - errata-01.md as anchored (unchanged)

File-level view of `~/Library/Caches/com.apple.python` (metadata receipt; `Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/` abbreviated as `<py39>/`):

| File | mtime (UTC) | Bytes | Attribution |
|---|---|---|---|
| `<py39>/glob.cpython-39.pyc` | 2026-10-10T08:26:24Z | 4623 | named by D9 and mtime equals the D9 write time: candidate only by user decision |
| `<py39>/fnmatch.cpython-39.pyc` | 2026-10-10T08:26:24Z | 4021 | named by D9 and mtime equals the D9 write time: candidate only by user decision |
| `<py39>/datetime.cpython-39.pyc` | 2026-10-10T08:26:24Z | 58176 | named by D9 and mtime equals the D9 write time: candidate only by user decision |
| the other 68 files | by UTC date: 2026-09-20: 16, 2026-10-08: 25, 2026-10-11: 27 | - | not attributed to utm-dmg-verify-01; leave alone |

## E2-03 (P3): C-04 (SSH evidence dir) and C-11 (raw evaluation root) hold frozen evidence: no removal before an explicit preservation decision

- Target: `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` line 160: "after the guest is deleted: `rm -r` the `ssh/` dir"
- Target: `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` line 167: "archive first if the audit record is to be kept; then `chmod -R u+w` the restricted dirs ... and `rm -rf`"
- Corrected statement: Both removal suggestions are withdrawn as written and are not approved. The SSH directory `ticket-03-macos-guest-v1/ssh/` is part of the frozen ticket-03 evidence tree, and the raw evaluation root is the sole audit record that the tracked reports cite by path and hash. Neither may be removed, chmod'ed, moved or archived-then-deleted until the user records an explicit PRESERVATION DECISION first: what to keep (for example the full root, or an archive with preserved hashes and the anchors), where, and for how long. Until that decision exists, the status of both items is `keep`. Removing the SSH key material is a separate question from removing the evidence (it was not read or hashed by this work), and also needs that decision.
- Limits: The raw root's own checkpoint-C2-correction-01 directory (this work's receipts and anchors) is inside it; deleting the root would also delete the anchors that bind the tracked errata.
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` - `3e3561702f00fb65b8b04f670770dbeda8cdb379a7e81d96af67ef99f828000c` - errata-01.md as anchored (unchanged)
  - `raw:checkpoint-C2-correction-01/receipts/facts-25-ssh-dir-ls.out` - `708b8e2a41a14ce958fa04aaca8d1e66aa851cc975e2a4b35889da6ddddb5baf` - directory listing receipt of the SSH evidence dir (names and sizes only; contents not read)
  - `raw:checkpoint-C2-correction-01/receipts/du-raw-root-d1-r2.txt` - `090ef9bd45700fb520456461be89a837064560452d38dfcc0c649839ea42810d` - raw root size receipt

## E2-04 (P3): Every command in the errata-01 cleanup inventory is informational, not approved for execution

- Target: `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` line 157: "(description of the table rows) C-01..C-12, column 'How to remove (not run)', lines 157-168"
- Corrected statement: The 'How to remove (not run)' column of errata-01 E-06 (C-01..C-12) and the `rm` command listed for the stray `/tmp/.noop` are descriptive only. None is an approved command, none was run, and none may be run on the strength of this record. Any removal (UTM bundles, IPSW, tags and runs on the fork, artifacts, the local backup branch, unreachable commits, /tmp files) needs its own explicit user decision, and C-04, C-11 and C-12 are further qualified by E2-02 and E2-03 above. Remote tag, run and artifact deletions act on the public fork and would need the user's separate authorization.
- Limits: No inventory number is recomputed or changed by this erratum; they were observed on 2026-10-14 and are stale to the extent the machine or fork changed since.
- Evidence:
  - `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` - `3e3561702f00fb65b8b04f670770dbeda8cdb379a7e81d96af67ef99f828000c` - errata-01.md as anchored (unchanged)

## Worker deviations (as they occurred)

- errata-01.md, errata-01.json and anchor.json (927a351c...) were not edited; all new files are additions. Before rendering, the three were re-hashed by descriptor reads and matched the anchored hashes (asserted in the renderer).
- I read the dispatch with the descriptor reader; the Sol and Opus phase-3 reports were read with the descriptor reader. The ticket-09 section 3 table was read with the descriptor reader and parsed programmatically.
- Read-only shell commands that are not descriptor reads: one `find ... -exec stat` listing of ~/Library/Caches/com.apple.python (names, mtimes, sizes), one `find` of the utm-verify-01 tree (names only), one `ls` of the raw root; their facts were re-collected into receipts by `tools/collect2.py` (metadata only; no cache file content was read), and one ad-hoc `git` ancestry/diff pass whose results are the same as the receipts.
- The first ad-hoc `git diff b6caf8c8 HEAD --stat` shows 6 files (HEAD adds the ticket-09 report/evidence); ticket 09 says 4 paths because it describes `0bbb6e0f`. Both are in the receipts; E2-01 states both.
- No NOREAD or credential-bearing file was opened, hashed or read. The SSH directory was not listed again in this correction; E2-03 cites the earlier receipt.
- The overlay manifest was opened (it records file paths and hashes only); ticket 09 says 7 overlay files for rebase-01-test-04 and the manifest has the entry count stated in E2-01.
- Earlier attempts of this renderer failed on its own assertions (a wrong evidence-row count, a wrong row-id expectation, an em dash in my own text) before anything was written, because the output directory is created only after all sweeps pass; `tools/render2_c02r1_backup.py` is the copy of the renderer that produced `payload/c02-r1/`, kept after I revised the wording, and `payload/c02-r2/` is the delivered rendering.
- One `sed -i` call failed on macOS syntax (no file changed); I edited the renderer with Python instead.
- No stage, commit or push; nothing outside the raw root checkpoint-C2-correction-01 and the new tracked errata-02 files was written.

## Sources and scope

- `raw:coordination/checkpoint-C2/correction-02-dispatch.md` - `1df97995817fbb6b28c2fa5b7b01292a37217bb59845fcf26514ba6b0eecedcd` - correction-02 dispatch (authority and scope)
- `raw:checkpoint-C2/sol-round3.md` - `1bbb4f70df86b94227ed05ac55fa93e33a2f2a926e22b69aa7945d2bbfd82d4a` - Sol phase 3 review (HOLD on two P3 clarifications)
- `raw:checkpoint-C2/opus-round3.md` - `9a3d466b475dec3c6addf5c81173bbc5687c961747415015444165807b3ee37e` - Opus phase 3 review (PASS WITH QUALIFICATIONS)
- `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.md` - `3e3561702f00fb65b8b04f670770dbeda8cdb379a7e81d96af67ef99f828000c` - errata-01.md as anchored (unchanged)
- `repo:docs/toolchain-evaluation/bun/checkpoint-C2/errata-01.json` - `e50c90e2d9c108508f8ed69c60c8cc7b00d863ce97bc9c496d220f00771e7a5b` - errata-01.json as anchored (unchanged)
- `raw:checkpoint-C2-correction-01/anchor.json` - `927a351c72ea5c0e514fdbb5fa59bcd1fec4e7806470b4b37d61b9e4ec3f5681` - anchor.json (v1), unchanged
