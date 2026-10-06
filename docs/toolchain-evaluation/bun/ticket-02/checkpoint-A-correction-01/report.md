# Ticket 02: current checkpoint A correction 01

**Current status: correction WIP, independent review pending; checkpoint A remains HOLD.** This is a local macOS arm64 feasibility candidate, not adoption, production migration, publication approval, or checkpoint acceptance. This current entry supersedes the status headings of earlier ticket-02 reports; their bodies, failures, and frozen receipts remain immutable historical records. The historical `adaptation-01/REVIEW-PASS.md` is its original bounded E1 review, not acceptance of this new WIP.

## Candidate/bootstrap scope and retained C1 compatibility gap

The coordinator selected a **Bun-only candidate compared with the independent original Node/pnpm baseline**. A fresh candidate dual bootstrap is not a new strict E1 requirement. This scope qualification does not waive C1's reproduced compatibility gap or turn either failure into success:

1. A fresh frozen install of the committed candidate by Node 22.23.2 / pnpm 10.0.0 failed: repro01, exit **1**, `ERR_PNPM_OUTDATED_LOCKFILE`; no modules were installed and inputs stayed unchanged.
2. Restoring only the three original `link:packages/...` root specifiers in repro02 gave Node/pnpm frozen install **0**, but Bun 1.4.2 frozen install **1**: all three packages were "not linked" and failed resolution. Both locks and the security policy stayed unchanged. That variant was not integrated.
3. `packageManager: pnpm@10.0.0`, pnpm metadata, and the helper's Node branch are retained only as baseline references and for separate Node runtime controls on a Bun-installed layout. They are **not evidence of a supported fresh candidate pnpm installation** and are not a daily-development fallback.
4. The conventional `CONTRIBUTING.md` pnpm development flow is **unsupported on this candidate**. In the frozen/CI form, following the branch's `pnpm install --frozen-lockfile` step would fail with the reproduced `ERR_PNPM_OUTDATED_LOCKFILE` if this branch were ever published. No hosted CI or nonfrozen pnpm repair/install was run; ordinary nonfrozen pnpm behavior is not asserted as a separate executed proof.
5. The supported original Node/pnpm baseline at `f4e9fd50136ea84f38f99e3c96f2c924f21d244f` is separate and not edited by this correction. Node controls on copied Bun layouts are distinct from that baseline. Historical report headings are superseded here, not rewritten or retrospectively converted to current acceptance.

The failed original candidate pnpm bootstrap is not an original-baseline failure or, under the explicit chosen comparison scope, a strict E1 feasibility failure. Only two root representations were tested; no universal claim about every possible dual-bootstrap solution is made. No workspace YAML, lock regeneration, global Bun link registration, Node cleanup, upgrade, or workspace framework was added.

## Minimum cache correction and actual proof

Base HEAD: `7ce08fecafee8f4cd9c8b8f6df7b33a9fdda323f`; tree: `b546e223155fb21382c11570157dcadc554e2a5c`. The only implementation delta adds `scripts/bun-evaluation.mjs` to the existing Bun-only artifact fingerprint list in `tests/support/publicPackagesSetup.ts`. Cache mechanism, build dispatcher, compiler adapter, package/lock files, Node branch, mocks, assertions, and test cases are unchanged.

| Actual bounded command group | Result and qualification |
|---|---|
| SDK/DOM, genuine cold selected artifact/globalSetup, normal warm tests | Unobserved **36/36, 51/51, 51/51**, exits 0; no skips/failures/todos |
| Public package build and node/web typechecks | Unobserved exits **0/0/0** under actual Bun 1.4.2 |
| Owned Vue good/bad fixtures | Unobserved exits **0/2**; bad reports **TS2322** |
| Declaration/map parity | All **116** current products match prior Node controls; not a fresh Node run |
| Matched-observer cold/warm tests | **51/51** each; cold logs two build entries, unchanged warm logs **zero**, with exact stamp identity and product content preserved |
| Owned guard-only direct public/Mini-IDE sentinels | Exits **73/73** |
| Guard-only warm negative | GlobalSetup/build failure, exit **1**, **zero passing cases**, one attempted public build entry; success stamp removed, not a false 51-case pass |
| Restored exact guard, normal tests | **51/51**, exit **0**; two build entries and a new success stamp |

The observer is an owned entry-identity logger with the same fixed `BUN_OPTIONS` in all observed controls/negative/restoration runs. It does not patch runtime, fs, module resolution, or assertions, but is **not claimed neutral or unobserved**. Unobserved positive receipts remain separate and were not rerun after recovery. The temporary failing guard existed only in the owned execution copy and was restored byte-for-byte; the shared guard was never edited. Named positive case sets agree.

## Evidence and carryover limits

The small [evidence index](evidence.json) binds actual raw command receipts, current proof JSON, old failure roots, dispatch, and mandatory identity erratum. Local execution root: `ticket-02/adaptation-01/checkpoint-A-correction-01` under the issue evaluation root. Complete `COMMANDS.json` contains argv/cwd/owned allowlisted environment/PIDs/exits/full stdout-stderr identities; bulky source, library, product, runtime, and case inventories are separately classified supporting payload. The complete root is bound by `freeze/anchor.json`; all new untracked WIP documents and full source identities are included, without a count cap or dropped files.

Manifest/native/pnpm locks, thirteen effective security overrides, restricted trust, and the three owned workspace links are unchanged. Libraries are byte-identical owned copies of the approved frozen Bun layout. This correction did not perform a new install, lifecycle or audit run, nor claim a newly reconciled dependency graph. Wrong-version/install/audit/lifecycle/negative compiler evidence from prior roots carries over only for unchanged consumed guard, compiler, manifest, locks, and policy; no old cache proof is substituted for the new cache result. Audit findings remain findings, not a clean audit claim.

## Residuals, process history, and downstream gates

Candidate pnpm bootstrap remains unsupported. C2 global alias handling, C4 private CommonJS/compiler API use, C5 pin enforcement through the canonical entrypoint only, and C6 compatible-range acceptance by Bun frozen install remain residual/later topics. The reviewers' S1-S3 advisory-versus-residual labeling disagreement is retained, not resolved by the worker. Absolute local evidence references are a publication concern; publication is not authorized.

The coordinator's invalid reviewer field and failed lookup receive no identity credit; the mandatory erratum identifies the actual reviewer. Response `aa7501e5` and producer/wait-aborted history remain aborted attempts in their original API/session history, not correction completion. Existing failure receipts were not overwritten. Earlier automatic Git maintenance's detached effects remain unknown, not presumed harmless. Future Git commands use per-command `maintenance.auto=false` and `gc.auto=0`, not persisted configuration; original earlier reader commands remain as issued.

Required next: independent whole-WIP ticket review, both checkpoint reviewers' re-review/challenges and one jointly ratified integrated FULL REVIEW, then a separate exact commit dispatch. No source acceptance or checkpoint PASS is claimed. Full-root/frontend/backend, application startup, external consumer contracts, performance, Linux/Windows, CI/platform packaging, and later ticket/checkpoint gates remain unverified here. No next-ticket work, staging, commit, hooks, publication, or remote operations occurred in this correction.
