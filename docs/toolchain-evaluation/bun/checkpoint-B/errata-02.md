# Checkpoint B — errata 02

Append-only addendum to `errata-01`. Documentation and evidence only: no product, test, harness,
build or guest change, and no re-measurement. Committed reports are history and are not edited; each
entry points at the committed statement it corrects. The machine-readable form is `errata-02.json`
in this directory.

- Dispatch: `coordination/checkpointB-correction-01/correction-02-dispatch.md`, SHA-256
  `2aa01b40c1e3a998cb8ead1b518b6ea68055318b95bb2ea3321159603942f4ec`.
- Fixed point: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch
  `eval/issue-2-bun-toolchain`, HEAD `b5ea6b65713e0ddd385e798ccc2da68d48ff93ca`.
- Relation to errata 01: additive. `errata-01.md` and `errata-01.json` are unchanged and stay
  bound by correction anchor `f74e06c1…`; both pairs are bound by `anchor-v2.json`.

## CB-E14 — ticket 03: the same classification repeated in `evidence.json` (N2)

- Committed file: `docs/toolchain-evaluation/bun/ticket-03/startup-v1/evidence.json`
  (SHA-256 `2cc308de5e05b24925f7539a21b6c158f6fbfe6cb891156654da243bb727b439`), line 1829,
  `limits[2]`.
- Quote: "the 8 size-differing pairs each lose exactly 6 bytes to one if(!!x)->if(x) (-2) and one
  new Error(->Error( (-4), and applying those two transforms to both sides leaves every
  content-differing pair with equal length, i.e. all remaining differences (minified names and
  content-hash tokens) are length-preserving and net 0. Nothing is claimed about the plans bundle
  beyond that, and the earlier masked-structure comparison cannot show name-only differences, so no
  name/hash-only statement is relied on."
- Corrected statement. This repeats the length-only inference that CB-E01 corrects and receives the
  same correction. The byte accounting is unchanged (8 size-differing pairs; the two transforms
  account for 8 removed `if(!!`→`if(` and 8 removed `new Error(`→`Error(` instances, each in exactly
  those 8 pairs). What the retained token-level measurement adds: after masking the trailing
  content-hash filename token, **381 of 389** pairs are byte-identical; the remaining **8** pairs
  have equal byte length and differ only in **2101 single-character minified identifier positions**
  (17 distinct directed substitutions, 13 undirected; 2 in property position; 0
  keyword/literal/global hits), confirmed by a parse-free byte-run alignment. The two counted
  transforms are behaviour-preserving; the identifier renames are **not** proven semantically
  equivalent (no scope analysis). The closing caution of the original sentence — "no name/hash-only
  statement is relied on" — is superseded by that measurement; it remains true only of the earlier
  masked-structure comparison.
- Evidence:
  `checkpoint-B-correction-01/scripts/cb01-bundle-token-producer.py` `70acb46d…`;
  `checkpoint-B-correction-01/analysis/cb01-bundle-token-analysis.json` `7a282b4c…`;
  `checkpoint-B-correction-01/receipts/cb01-bundle-token-output.txt` `e8f6033f…`;
  `ticket-03-startup-01/correction-02/analysis/c02-bundle-delta.json` `2a6f43d0…`;
  independent recomputation in `checkpoint-B/sol-round2.md` `e120d5db…` and
  `checkpoint-B/opus-round2.md` `720b5db1…`.

## CB-E15 — audit-trail pointer; the correction-01 manifest note is incorrect (A1/N1)

- No committed report line is corrected. The correction-01 preservation, read-protocol and
  overwrite deviations, and the outcomes of the verifiable recovery attempt, are recorded in
  `checkpoint-B-correction-01/deviation-01.md` and `deviation-01.json`.
- The note in `checkpoint-B-correction-01/manifest.json`
  (`ff6f8fe395d4b52049cae6843861af46dd08303ed3637844b1eabe19548a4c68`) that says "no delivered
  evidence was deleted or rewritten" is **incorrect**: the `__pycache__` bytecode file bound by
  `pre-freeze-01/manifest.json` was deleted, and the freeze script bound by that manifest was
  rewritten; `pre-freeze-01/manifest.json` now fails **2 of its 7 bindings**. The manifest is bound
  as-is by `anchor-v2.json` and is not edited; deviation-01 CB-D05 and CB-D06 are the correction.
- This entry points at that record and does not restate it in full.

## Limitations

- Documentation and evidence only; no measurement was rerun and no product artifact changed.
- CB-E14 corrects a committed `evidence.json` limits entry; that committed file remains untouched.
- CB-E15 is a pointer; the authoritative details live in `deviation-01.md/.json`, whose hashes are
  frozen by `anchor-v2.json`.
