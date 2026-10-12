# Post-rebase adaptation: `src/main/dockQuitProbe.test.ts` under Bun

Branch `eval/issue-2-bun-toolchain`, base HEAD `5e46f0b6c0725df25b737f9746eff14b6b4d4553` (rebased onto upstream `8027cd8a`). Runs were made in the disposable macOS guest `navide2-guest` (macOS 27.0.1 arm64, Bun 1.4.2 only with no node/npm/pnpm on PATH; Node control Node 22.23.2 / pnpm 10.0.0). Raw evidence lives outside the repository in `evaluations/Navide/issue-2-bun-toolchain/rebase-01-dockquit/`.

## Result

| | Before (post-rebase tests 02/03) | After (this change) |
|---|---|---|
| `dockQuitProbe.test.ts` alone, Bun | 11 passed, 2 failed; test 1 took 1,614 s alone (test-03) and 707 s in the full root (test-02) | 13 passed, 2.0 s |
| `dockQuitProbe.test.ts` alone, Node | 13 passed | 13 passed, 2.5 s (change is inert on Node) |
| Full root suite, Bun | 931 files, 13,684 tests, 3 failed, 729 s | 931 files, 13,684 tests, 13,660 passed, 23 skipped, 1 failed, 78.5 s |

The one remaining Bun failure is the accepted residual `src/main/plugins/pluginExternalWorkspace.test.ts` (`Executable not found in $PATH: "pnpm"`, CB-E10). Test inventory is identical to the Node root run (same 931 files, same 13,675 unique test ids, 0 Node-only, 0 Bun-only).

## Mechanism

1. `src/main/index.ts` imports `src/main/updater.ts`, which has `import { autoUpdater } from 'electron-updater'`. `dockQuitProbe.test.ts` does not mock `electron-updater`.
2. `electron-updater` is CommonJS and exports `autoUpdater` as a lazy getter (`main.js` `doLoadAutoUpdater` -> `new MacUpdater` -> `new ElectronAppAdapter` -> `require('electron')`). Node's import does not read the getter. Bun's import of this package reads the getter at import time (observed for `electron-updater` only; this report does not claim that Bun reads every CommonJS export's getter). A plain `import('electron-updater')` outside Vitest shows it: under Bun the `electron/index.js` `path.txt` check is hit once during the import (stack through `main.js:48`, `MacUpdater.js`, `AppUpdater.js`, `ElectronAppAdapter.js`); under Node it is hit zero times (`hasAutoUpdaterKey: false`).
3. That `require('electron')` is a real, externalized dependency, so `vi.mock('electron')` cannot intercept it. `electron@44.6.0/index.js` downloads the binary with a synchronous `spawnSync(process.execPath, ['install.js'])` when it is not installed (after install no `path.txt` or `dist` exists in any provisioned case, so the first load downloads it), printing `Downloading Electron binary...`. Every provisioned case therefore starts without the binary.
4. A synchronous download blocks the event loop. The `dockQuitProbe` test timeouts (60 s) then fire late. The first test fails with an empty `Error` (the timeout error, stack at the test definition), and the second test fails at `boot` with two `before-quit` listeners.
5. Duration of the download: this task's logs show the `Downloading Electron binary...` banner and test 1 taking 18.3 s (case A) and 6.4 s (fresh case B), but the download time itself was not measured. For the earlier runs, the test-02 and test-03 logs also show the banner, and test 1 took 707 s (test-02 root JSON) and 1,614 s (test-03 `dockq.json`); in both earlier Bun cases `node_modules/electron/dist` was created near the end of the run window (guest mtime). That the earlier 707 s / 1,614 s were caused by the download blocking the event loop is an inference, not a measurement: the 70 s controlled block (below) establishes the mechanism (a long synchronous block at that point yields exactly these two failures), not those historical durations. Why the earlier downloads were that slow was not diagnosed, and no claim is made about Bun versus the network.

Evidence (raw root, `retrieved-v2/<case>/`):

- Unmodified source on a fresh Bun case: 13/13 passed and `Downloading Electron binary...` printed, test 1 took 6.4 s (`bun-20261012T200333Z`); same on case A (test 1 18.3 s, `r1-baseline`).
- A trace setup file (outside the repo) records the `electron/index.js` `path.txt` check with its stack: one hit under Bun, from `electron-updater` (`probe-out/p2-bun-trace.electron-trace.log`); no hit under Node (`node-20261012T202051Z`, no trace file written, test 1 764 ms, no download).
- Controlled reproduction: the same trace file blocks the event loop once, for 70 s, at that first check (`PROBE_BLOCK_MS=70000`, case A, `p3-bun-block70`). The result is exactly the earlier failure: 2 failed, 11 passed; test 1 failed after 70.6 s; test 2 failed with `AssertionError: expected [ …(2) ] to have a length of 1 but got 2`. This reproduces the failure signature with no mock difference; the leak of the late-resuming test body into the next test is inferred from this reproduction, not stack-traced.
- Ruled out: relative `vi.mock` resolution, `electron` mock resolution, `vi.resetModules`/dynamic import differences and timers as independent causes. The mocks apply (11 tests pass with the mocks in place, and the test passes once the binary is present).

## Change

One file: `tests/support/bunMockImporterAnchor.ts` (already a `vitest.config.ts` `setupFiles` entry; inert outside Bun). It gains a second, independent part (+36 / -3 lines): under Bun, when `ELECTRON_OVERRIDE_DIST_PATH` is not set and the Electron binary is not installed (`node_modules/electron/dist/<path.txt>` missing), it sets `ELECTRON_OVERRIDE_DIST_PATH` to a path that does not exist. `electron/index.js` then returns the path string immediately; nothing is downloaded. An installed binary or a caller-set override is left alone. The file header documents both parts. No test file, product source, config, assertion, mock body, timeout, skip or dependency changed.

Helper SHA256: before `dd37728180458749ea5d4d68a8d546265b527f3de32ddaba0d8af980bc109ea7`, after `4a841c49478708767391529cb1239fd6dec088d346fa9029c0eef7f31f1a23ec`.

After-change evidence: a fresh Bun case with the changed helper (`bun-20261012T202512Z`) printed no `Downloading Electron binary...`, created no `path.txt`, `dist` or Electron cache, and passed 13/13 in 2.0 s; typecheck passed there (exit 0). The Node case with the same file (`node-20261012T203122Z`) passed 13/13, with `typecheck_supp` 0 (plain typecheck hit the known 134 heap exit, as in test-03).

## Adoption cost (disclosure under the 2026-10-11 rule)

- One edit to an existing generic Bun-only test-support helper (+36 / -3 lines); no test-file edit.
- The helper sets `ELECTRON_OVERRIDE_DIST_PATH` as a process-wide environment variable in the Vitest worker, so it is also inherited by any child process a test spawns. A future test that launches Electron (directly or through a child) without an installed binary would receive a non-existent path instead of a download. None was observed: the full Bun root has only the residual failure.
- Unresolved product/runtime risk, not tested here: Bun evaluates `electron-updater`'s lazy `autoUpdater` getter at import while Node does not (observed outside Vitest, see Mechanism step 2). This change only stops that evaluation from downloading Electron during Vitest runs. Whether a Bun-run product (development or packaged) evaluates the getter eagerly at startup, and what that would do, is NOT established by this task and is carried forward to ticket 09. Bun still evaluates the real `electron` package and constructs the updater at import time in tests that import `src/main/updater.ts` unmocked; this is left unchanged.

## Revision

Revision 2 (correction-01, documentation only; the helper bytes are unchanged, SHA256 `4a841c49…23ec`). It qualifies the historical-duration inference and the getter observation, and expands the adoption-cost disclosure. The first revision is preserved byte for byte in the raw root as `freeze/report-v1-dockquit-adaptation.md` (SHA256 `a102942ffef01def3d80ffa03d5d57554ee873de2839235302adb5fa85543692`).

## Deviations (as they occurred)

- D1: the dispatch, the referenced RESULTS/dispatch files and the worktree sources were first read with the standard Read/cat tools, not the descriptor reader; later reads used the no-follow descriptor reader.
- D2: the pre-launch count-only probe found 6 matching guest processes (same unattributed class as test-03 D2); none started or stopped by this task.
- D3: probe run `p1-bun-trace` failed at config load (probe dir could not resolve `vite`); fixed with a `node_modules` symlink in the case's probe directory; the failed log is kept.
- D4: a stray `cp dq-run.py /tmp/_unused` ran on the guest (typo, harmless, not removed).
- D5: the first node provisioning used a script whose Node branch still contained the full root `test:run`; it started an unintended Node root run (case `dockquit-node-20261012T200503Z`), which was interrupted (experiment-owned process group) after provisioning steps; the case is incomplete and was not used as evidence. A provision-only script was used afterwards.
- D6: the first evidence retrieval archived nothing (zsh did not word-split the item list); it was repeated into `retrieved-v2/`; the empty first-attempt tars are kept.
