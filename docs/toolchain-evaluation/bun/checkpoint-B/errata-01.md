# Checkpoint B — errata 01

Documentation and evidence corrections for the Checkpoint B round-1 HOLD. No product, test,
harness, build or guest change; no re-measurement. Committed reports are history and are not
edited; every entry points at the committed statement it corrects, and the corrected statement
lives here. The machine-readable form is `errata-01.json` in this directory.

- Dispatch: `coordination/checkpointB-correction-01/dispatch.md`, SHA-256
  `d7d0bf5d6d2f8ca1a678f02b2659d22846ccea14b4e5fe60931d8323da39dbfe`.
- Fixed point: `/Users/slighter12/git/worktrees/Navide/issue-2-bun-toolchain`, branch
  `eval/issue-2-bun-toolchain`, HEAD `b5ea6b65713e0ddd385e798ccc2da68d48ff93ca`.
- Raw producer and outputs: `/Users/slighter12/git/evaluations/Navide/issue-2-bun-toolchain/checkpoint-B-correction-01/`.

## CB-E01 — ticket 03: the bundle-difference classification (F1)

- Committed file: `docs/toolchain-evaluation/bun/ticket-03/startup-v1/report.md`
  (SHA-256 `1fffd914e701db72988fe62bd392e88784dd60f69a061a93133b11eb7fa57c63`), line 194.
- Quote (lines 194–197, repeated at 348): "the remaining differences — minified identifier
  names and content-hash tokens — are **length-preserving and net 0 bytes**. So the family is
  **two behaviour-preserving minifier transform choices**, and no other difference class is
  claimed."
- Corrected statement. The byte accounting stands; the length-only inference did not classify
  the remaining differences. The retained token-level producer measured, over all 389
  content-differing pairs of the two retained trees (`correction-01/retrieved/products-a0-base`,
  `guest/retrieved/products-c-bun`):
  - on-disk bytes match the pair list's recorded SHA256 in 389/389 pairs;
  - the two counted transforms account for 8 removed `if(!!`→`if(` (node 11 → bun 3) and 8
    removed `new Error(`→`Error(` (node 3888 → bun 3880) instances, each in exactly 8 pairs;
    both transforms are behaviour-preserving;
  - after masking the trailing content-hash filename token (`-XXXXXXXX.js|css` →
    `-HASHTOKEN.js`), **381 of 389** pairs are byte-identical;
  - the remaining **8** pairs have equal byte length and differ only in **2101 single-character
    minified identifier positions** (17 distinct directed substitutions, 13 undirected; 2 in
    property position; 0 keyword/literal/global hits), confirmed independently by a parse-free
    byte-run alignment (2101 differing bytes in 2101 single-character identifier runs; 0 runs
    that are not a single-character identifier).
  - The renames are **not** proven semantically equivalent: no scope analysis was performed,
    and a globally consistent renaming map is not expected (a first-seen global map conflicts in
    5 of the 8 pairs under the producer's stated definition; the Opus cross-review reported
    7 of 8 under an unstated definition — a figure this producer does not reproduce and does
    not rely on).
  - "no other difference class is claimed" is replaced by "no other difference class was
    measured".
- Evidence: `checkpoint-B-correction-01/scripts/cb01-bundle-token-producer.py`
  `70acb46d90e72801b49f3eb7c4075df8f8a8ab534d119e2b53425f3bf5a78691`;
  `checkpoint-B-correction-01/analysis/cb01-bundle-token-analysis.json`
  `7a282b4ce9768d979e72834d730d9b6a3b5cacda4daa08125fa4c0e0cb9f8614`;
  `checkpoint-B-correction-01/receipts/cb01-bundle-token-output.txt`
  `e8f6033fc0304b867ba1e13c93966ccd36a52e01c54f4d869a467fec4e1c9b8b`;
  `ticket-03-startup-01/correction-02/analysis/c02-bundle-delta.json`
  `2a6f43d01e817c68abf067d5f16e3638b8f335be87809b897d721782676fb10d`.

## CB-E02 — ticket 03: the MCP credential-refusal claim (SEC1)

- Committed file: `ticket-03/startup-v1/report.md` (as above), line 87.
- Quote: "A request without a credential is refused with JSON-RPC `-32602`."
- Corrected statement — withdrawn. The negative probe does not isolate authentication:
  `guest-probes.py` lines 157–160 send a tokenless `initialize` whose `params` is `{}`, so it
  also lacks `protocolVersion`, `capabilities` and `clientInfo`; the `-32602` response has the
  message `Invalid request parameters` and cannot be attributed to the missing credential. The
  probe proves neither credential enforcement nor its absence. The token-bearing
  `initialize`/`tools/list` path proves readiness of the plugin MCP seam (64 tools,
  byte-identical surface) and does not by itself prove an authorization decision for protected
  tool execution. The authenticated WebSocket comparison in the same report (tokenless
  handshake closed with 4403; `?t=<token>` accepted) is a separate, valid control and is
  unaffected. No re-probe was performed.
- Evidence: `ticket-03-startup-01/scripts/guest-probes.py`
  `509c21e864837536f7bec4ce925688cd9df6921dbd86c7c6bd4115aa17ea807b`;
  `ticket-03-startup-01/guest/retrieved/unpacked/case-c-bun/receipts/dev-c-run2/mcp-probe.json`
  `1d0aa29f2f5dc930ef6ad1bf5a9d98d7eb632db20ef735a04bb0f79c964e7120`;
  `backend/agent_team_backend/mcp_server/server.py`
  `9b74b9ac5160f3f0399ea1e3fccea673014b11213e5af9897edd7d59b1ca79c3` (caller resolution is
  per tool call).

## CB-E03 — tickets 03–06: RR1 cross-reference

- No committed report line is corrected. The record for the audit-gap finding is the
  coordinator's own `coordination/review-archive-01/rr1-coordinator-record.md`
  (`36c1427fce6473fcbe472a01cd5ea29090318edb9ca65538a921747d4a1f2653`) with its identity
  registry `index.json` (`b78aab6d612316c0eb34f9e0b114a3fb376374b50d006fa425e1fb7a1bd88642`).
  This entry points at that record only; it does not restate, summarise or judge it.

## CB-E04 — ticket 04: `vitest.config.ts` +6 attribution (S4)

- Committed file: `docs/toolchain-evaluation/bun/ticket-04/report.md`
  (`5a882606ad6f38714e5e2922bb5631d82e35835e7f863e4da6ce59728ff1af2e`), line 102.
- Quote: "`vitest.config.ts` (+6: private `node` alias)".
- Corrected statement. The +6 lines added to `vitest.config.ts` over
  `f4e9fd50..ee3174ae` are the three `test.alias` mock-id entries and their comment
  (EditorViewMonaco, `../foundation`, `../shared`), introduced by commit
  `7ce08fecafee8f4cd9c8b8f6df7b33a9fdda323f` (ticket 02); they are not a `node` alias, and
  `vitest.config.ts` contains no `node` alias. The runtime `node` alias is created by
  `scripts/bun-evaluation.mjs` (lines 63–66).
- Evidence: the report file above; `scripts/bun-evaluation.mjs`
  `7ccbe18a743c741493db635c94bd230fb45a56f22e7ef7831dd9eeeeab29e88b`; commit `7ce08fec…`.

## CB-E05 — ticket 04: alias provenance (S4)

- Committed file: `ticket-04/adaptation-01/correction-01/report.md`
  (`8c1eb7db4000323a01d59e67b624fd38a0c3709ebc20e7d80f44fb76e2b7ea42`), line 138.
- Quote: "added by the branch in ticket 04".
- Corrected statement. The three `test.alias` entries were added by ticket 02 (commit
  `7ce08fecafee8f4cd9c8b8f6df7b33a9fdda323f`), not by ticket 04. The alias-asymmetry
  measurement and its alias-symmetric Node control are unaffected.
- Evidence: the report file above; commit `7ce08fec…`.

## CB-E06 — ticket 03: who creates the `node` alias (S4)

- Committed file: `ticket-03/startup-v1/report.md` (as above), line 129.
- Quote: "Bun materializes a `node` symlink to itself".
- Corrected statement. The alias directory and symlink are created by the evaluation dispatcher
  `scripts/bun-evaluation.mjs` (`mkdtempSync(join(tmpdir(), 'navide-bun-runtime-'))` then
  `symlinkSync(process.execPath, join(nodeAlias, 'node'))`, lines 63–66) so that
  `#!/usr/bin/env node` shims select the pinned Bun. Bun itself does not create the symlink.
  The observed two leftover directories and their removal are unchanged.
- Evidence: `scripts/bun-evaluation.mjs` `7ccbe18a…`.

## CB-E07 — ticket 03: stage label (S4)

- Committed file: `ticket-03/startup-v1/report.md` (as above), line 3.
- Quote: "Stage: E3 — the startup/readiness slice of ticket 03".
- Corrected statement. Ticket 03 is stage E2: the packet issue 03 states `Stage: E2 —
  build/startup slice.` (line 13); E3 is ticket 06. Corrected label: `Stage: E2`.
- Evidence: `docs/toolchain-evaluation/bun/packet/v1/local-sources/evaluation/issues/03-macos-build-and-startup.md`
  `a06e60e946e19c40a34be603a1e6fee7891904a88d11000d1b563179b69922e5`;
  `…/issues/06-macos-test-performance.md`
  `d791518fe9bead1a45bc133e07b24bb5c77a75126edc5b5d0f29c7997dd8c49d`.

## CB-E08 — ticket 05: report title (S4)

- Committed file: `docs/toolchain-evaluation/bun/ticket-05/report.md`
  (`9a2381863c29e15aaad6412985ea2558b57e23c25c96d0c21c4baf87629bbde2`), line 1.
- Quote: "# Ticket 05-01 — public artifacts for existing consumers, version 1".
- Corrected statement. The tracked report is the correction-02 revision: it carries
  `## Correction 01`, `## Correction 02 — version-1 preservation status and wording`, and the
  corrected `evidence.json`; the version-1 accounts of the overwritten runs are withdrawn in
  place. The title should identify that revision (correction 02), not "version 1".
- Evidence: the report file above (correction sections at lines 453–540).

## CB-E09 — ticket 03: worker identity (S5)

- Committed file: `ticket-03/startup-v1/report.md` (as above); no identity was recorded.
- Corrected statement. Recorded from the coordinator registry, not inferred from the
  allocation: ticket-03-startup worker session `01a125a3-9e4a-703b-b623-5bd50d621de9`, provider
  `deepseek`, model `deepseek-flash`, reasoning level `max`.
- Evidence: `coordination/review-archive-01/index.json` `b78aab6d…` (role `t03s-worker`);
  `coordination/review-archive-01/t03s-worker-01a125a3-9e4a-703b-b623-5bd50d621de9.md`
  `706c1da87bb858e67380b228350673539819cfc2f200ab17e2624ca75fcf6810`.

## CB-E10 — ticket 04: the two-layer residual (SP1, disclosure, no rerun)

- Committed file: `docs/toolchain-evaluation/bun/ticket-06/report.md`
  (`9885ca294601bdfb06f3b1fd4bdb5f6922863f393b407487994950718574e91f`), line 188.
- Quote: "The observed message in this round is the ticket-04 classification
  (`Error: Executable not found in $PATH: "pnpm"` …)".
- Corrected statement. The accepted residual has two layers that must not be merged:
  (1) in the shipped tree the failure is the test's own helper asking for `pnpm`
  (`pluginExternalWorkspace.test.ts:27–39` selects `pnpm` unless `npm_execpath` or
  `NAVIDE_PNPM` names pnpm), so the observed macOS failure is a missing-`pnpm` failure;
  (2) the Bun `file:` non-optional-peer resolution limit was measured on a withdrawn adaptation
  path (ticket-04 adaptation-01 correction-01, shapes A–O). Consequence for the gate: on macOS
  the default Bun `test:run` workflow ends with exit 1 (ticket-06 report line 160:
  "835 files passed / 1 failed; 12,446 passed / 1 failed / 23 skipped / 0 todo; exit 1"), so any
  CI gate built on it fails until (1) or (2) is resolved; user option A accepted this as a
  documented gap.
- Evidence: the ticket-06 report above (lines 160, 184–192);
  `src/main/plugins/pluginExternalWorkspace.test.ts`
  `7bfe298bf0e8c90af9e449b5b1d57ee5c2a634295c648aaf8d22cc2552620689`;
  `ticket-04/adaptation-01/correction-01/report.md` `8c1eb7db…`;
  `coordination/ticket04-adapt-01/user-decision-residual-A.md`
  `fc93d110e73f75536fef0408dc98e6d822f32f25f9286a42cc82a0b8c3be4f16`.

## CB-E11 — ticket 06: retained runtime compile caches (SP3, disclosure, no rerun)

- Committed file: `ticket-06/report.md` (as above), line 77.
- Quote: "**Cold** removes only experiment-owned generated state inside the case copy before
  every sample: `node_modules/.vite`, … `dist-plugins/navide-mini-ide`."
- Corrected statement. Cold as applied resets those seven workspace paths only; runtime-level
  compile caches were retained across the cold samples and are not recorded by the reset
  records. A later read-only guest inspection recorded Node
  `t06-base-node/tmp/node-compile-cache/v22.23.2-…` (180 entries), Bun
  `t06-head-bun/tmp/node-compile-cache/v1.4.2-…` (155 entries) and Bun `xdg-cache/bun/@t@`
  (425 entries) on both sides. This is a later observation, not a contemporaneous per-sample
  cache snapshot; the frozen timings are not relabelled as fully cold, and the effect is not
  bounded by the current evidence.
- Evidence: `checkpoint-B/opus-independent-round1.md`
  `d617f2de4490553339c4a2462b7aed99fd4094efe699d4ad594cdf0043bda524` (lines 123–128);
  `checkpoint-B/opus-adversarial-round1-retry.md`
  `813027207537c17d3175a5c3229f64f9298b8e1137eece9a1a6c883b7c01e9ec` (lines 81–84);
  `checkpoint-B/sol-adversarial-round1.md`
  `6190e00b83b22d354fe7fd9cb1261b96d1cd5837df6c6ad089df42cffc1a3ec9` (lines 94–104);
  the ticket-06 report above (lines 75–111).

## CB-E12 — ticket 06: warm-up pair faster than every gate sample (C5, observation)

- Committed file: `ticket-06/report.md` (as above), line 26.
- Quote: "Warm-up pairs, excluded from every median above: root 84.724 s / 55.156 s, SDK
  1.051 s / 0.775 s,".
- Corrected statement. Disclosed as an unexplained observation: the excluded root warm-up pair
  (Node 84.724 s, Bun 55.156 s) is faster than every retained root gate sample (Node warm
  89.531–99.740 s; Bun warm 60.356–63.887 s, report lines 117–123). The pair is excluded from
  the medians and symmetric between the two runtimes, so it does not change the gate; no cause
  is established and none is claimed.
- Evidence: the ticket-06 report above (lines 26, 117–123).

## CB-E13 — ticket 05: Bun-packed bin mode 0o777 (SEC2, disclosure, no rerun)

- Committed file: `ticket-05/report.md` (as above), line 195.
- Quote: "`package/bin/navide-plugin.mjs` mode `0o777` vs `0o755`; contents byte-identical
  (`c1ebe9c3…`)".
- Corrected statement. Disclosed as a release qualification: the Bun-packed SDK archive
  advertises the CLI bin entry as mode 0o777 while the pnpm-pack baseline writes 0o755. npm and
  pnpm installers were verified to normalize it to 0755 in all four consumers; Bun and yarn as
  installers were untested, and raw archive extraction is not covered either. If Bun's packer is
  ever used for publication, archive modes must be normalized (or the packer's behaviour
  revisited) before publication; no behaviour difference was observed in the exercised matrix.
- Evidence: the ticket-05 report above (lines 195, 433–437).

## Limitations

- Documentation and evidence only: nothing here changes product source, tests, harness, build
  outputs or the guest, and no measurement was rerun.
- Every entry corrects the text of a committed report; the committed reports remain untouched.
- E1 covers exactly the 389 content-differing pairs carried by the correction-02 pair list over
  the two retained trees; it is not a statement about every possible toolchain difference.
- No scope analysis was performed anywhere, so no behaviour-preservation claim is made for the
  2101 identifier substitutions.
