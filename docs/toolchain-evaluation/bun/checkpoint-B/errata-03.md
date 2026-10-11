# Checkpoint B — errata 03

Append-only addendum to `errata-01` and `errata-02`. Documentation and audit only: no product, test,
harness, build or guest change, and no re-measurement. Committed reports are history and are not
edited. The machine-readable form is `errata-03.json`.

- Dispatch: `coordination/checkpointB-correction-01/correction-03-dispatch.md`, SHA-256
  `23dff226b3609f24b9414d886df1c61fbf32ead50fd120b9a9b23ee3d921d7b9`.
- Fixed point: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch
  `eval/issue-2-bun-toolchain`, HEAD `b5ea6b65713e0ddd385e798ccc2da68d48ff93ca`.
- Relation to the earlier pairs: additive. `errata-01.*` stays bound by `f74e06c1…`; `errata-02.*`
  stays bound by `anchor-v2.json` (`26c9f68c…`); all three pairs are bound by `anchor-v3.json`.

## CB-E16 — correction-02 preservation account corrected; pointer to deviation-02

- No committed report line is corrected. The authoritative record is
  `checkpoint-B-correction-01/deviation-02.md` and its machine-readable revision
  `checkpoint-B-correction-01/deviation-02.revision-02.json` (the first revision,
  `deviation-02.json`, is kept in place and superseded; see "revision note" below).
- Withdrawn: the blanket claim of `deviation-01` that "Nothing existing was edited, moved or deleted
  by this correction" (and its restatement as "Nothing else in correction 01 or correction 02
  changed"). It is replaced by an exact inventory.
- Recorded in its place, with identities: three correction-02 scripts rewritten in place
  (`scripts/cb02-forensics.py` 9,282 B → 12,212 B; `scripts/cb02-recover-artifacts.py` 11,372 B →
  14,158 B → 14,243 B; `scripts/cb02-freeze-anchor-v2.py` 9,133 B → 9,095 B), two audit-record
  files replaced after the first `anchor-v2` attempt froze them (`deviation-01.md` 11,630 B
  `aa76509d…` → 12,746 B `51b19f68…`; `deviation-01.json` 20,665 B `e9992b48…` → 22,318 B
  `cd892a2d…`), and two path moves (`scripts/cb02-stablefd.py` → `scripts/cb02_stablefd.py`;
  `anchor-v2.json`/`anchor-v2.sha256` → `superseded/anchor-v2-first-attempt.*`). No deletions
  occurred during correction 02.
- Consequence, re-measured: the first `anchor-v2` attempt
  (`superseded/anchor-v2-first-attempt.json`, `b15bd20b…`) verifies **36 of its 38 bindings**; the
  two that do not are exactly the replaced audit records. Everything else in that snapshot,
  including all of correction 01, still verifies.
- Recovery: the two earlier bound versions are published, hash-exact and labelled RECONSTRUCTED, as
  `recovered/deviation-01.first-anchor.reconstructed.md` (`aa76509d…`, 11,630 B) and
  `recovered/deviation-01.first-anchor.reconstructed.json` (`e9992b48…`, 20,665 B), each derived
  twice (inverse transforms and the session-recorded original write payload) with both derivations
  agreeing byte-for-byte.
- What remains true: no committed report, no tracked file and no correction-01 delivered artifact
  was edited, moved or deleted during correction 02, and no product/test/build artifact was touched
  by any of the three corrections.
- CB-E10 wording: re-timed by the round-3 review to a later P3 for 07/08 — a future passing gate
  must address the path actually exercised; fixing Bun's `file:` non-optional-peer resolution alone
  cannot repair the shipped helper that still requests `pnpm`.
- Revision note (disclosed, not repaired): the first revision of `deviation-02.json`
  (`5821e8cb…`, 13,220 B) recorded one 64-character hash whose leading 16 characters were real and
  whose remaining 48 characters came from no record — the same failure mode as CB-D08. Revision 02
  supersedes it with four hashes verified directly from the session payloads. Under the
  correction-03 strict mode the wrong revision is left in place.

## Limitations

- Documentation and audit only; no measurement was rerun and no product artifact changed.
- CB-E16 is a pointer; the authoritative details and identities live in `deviation-02.*`, whose
  hashes are frozen by `anchor-v3.json`.
