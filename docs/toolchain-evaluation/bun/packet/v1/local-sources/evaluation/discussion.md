# JavaScript toolchain evaluation discussion

Source issue: https://github.com/slighter12/Navide/issues/2

## Authority and status

The user requested a grill-with-docs discussion. Documentation is authorized; migration implementation, installation experiments, test/build execution, and branch/worktree creation are not authorized. No migration decision has been made. This is an evaluation record, not an implementation specification or ADR.

## Agreed goals

- Primary problems: version upgrades have caused failures; multiple local Node.js/pnpm versions create confusion; environment setup should be simpler.
- Secondary problem: tests are slow. Bun improving this is a hypothesis, not an established result.
- The primary adoption target is a development environment that does not require separately installed Node.js or pnpm, rather than merely changing the command prefix while retaining both tools.
- Bun is a candidate, not a predetermined choice. If it does not pass the agreed adoption gates, defer/reject migration and leave the existing Node.js/pnpm toolchain unchanged. This is not permission for a mixed daily-development toolchain or partial Bun adoption that still requires external Node.js/pnpm. Improving the existing toolchain would require a separate proposal and authorization, supported by evidence rather than assurances.
- The no-separate-Node.js/pnpm target covers everyday installation, development startup, typechecking, testing, and application/plugin builds. Release packaging and CI may retain an explicitly isolated Node.js environment; this must not become an everyday local prerequisite. Electron's embedded runtime and unrelated external agent/project requirements are outside this target.
- The project must pin an exact Bun version. Global upgrades must not silently select a different version for project operations; mismatches must stop before installation/builds with actionable guidance; project upgrades require validation. Installing the specified Bun version must be sufficient without a version manager. mise and similar tools are personal conveniences, not part of the proposed contributor setup or project version-management solution. Entrypoints must reject a wrong Bun version rather than silently upgrading or downgrading it; the concrete enforcement mechanism must be evaluated.
- Preserve Vitest and existing tests. Replacing Vitest with `bun test` is outside the agreed evaluation scope. Running Vitest with Bun may be evaluated, but faster execution remains unverified.
- Prioritize Bun replacing pnpm and direct reuse of existing packages. Project tool configuration, scripts, and test build/pack/launch helpers may be adapted in a future authorized experiment, but third-party forks, patches, replacements, and changes that weaken test validation are excluded.
- Public-contract compatibility includes package exports, types, peer dependencies and CLI behavior; plugin packaging, installation and execution behavior; and continued consumption of public packages by external Node.js/npm/pnpm workflows without requiring those consumers to adopt Bun.
- The first comparison must preserve existing direct/transitive dependency versions and safety overrides. Lockfile format and installation layout may differ, but resolution differences must be reported rather than hidden. A dependency upgrade required for Bun compatibility is a separately decided change, not evidence that the existing package worked unchanged.
- Test acceleration is an optional benefit, not a mandatory adoption gate. Compare full-suite and common single-file workflows on the same machine and dependency versions, separating cold/warm-cache conditions. Start with five samples per condition and compare medians; more than 10% slowdown fails the performance gate. High-variance measurements require additional sampling rather than an immediate verdict.
- All three supported platforms (macOS, Linux, Windows) are mandatory adoption gates. macOS-only evidence can establish local feasibility but cannot justify project-wide adoption.

## Evidence collected without running workflows

- Root `package.json` declares `packageManager: pnpm@10.0.0` and Node.js `>=22.12 <23`; scripts explicitly invoke both `node` and `pnpm`.
- `package.json` also declares local package links, pnpm dependency overrides, and an installation-script allowlist. Any replacement must assess these semantics, not only installation success.
- `.github/workflows/ci.yml`, `release.yml`, and `plugin-packages.yml` currently use Node.js/pnpm. `docs/en-US/architecture.md` distinguishes the Electron main process from development tooling; replacing Electron is out of scope.
- Current shell inspection found Node.js resolving through a mise `node/latest` path and reporting `v26.10.0`, outside the declared range. pnpm resolves first through `/opt/homebrew/bin/pnpm`, followed by a mise shim. The Homebrew-linked package declares version `11.17.0`, while `pnpm --version` reports `10.0.0` after warning that the root pnpm settings are ignored. This establishes entrypoint/version ambiguity; it does not reproduce the user's historical failure or establish that a particular build used these versions.
- `plugins/navide-mini-ide/tests/packageBoundary.test.ts` and `plugins/navide-git/tests/compositionBoundary.test.ts` already disable `verify-deps-before-run` in subprocesses and document the risk of a newer pnpm wiping dependencies after a lockfile/config mismatch.
- Bun's [official runtime documentation](https://github.com/oven-sh/bun/blob/main/docs/runtime/index.mdx#--bun) states that CLI Node.js shebangs are respected by default; `--bun` overrides this. Therefore, `bun run` alone is not evidence of eliminating the external Node.js runtime.
- Bun's [official test-runner documentation](https://github.com/oven-sh/bun/blob/main/docs/test/index.mdx) describes a Jest-compatible runner with incomplete compatibility and tests importing `bun:test`. Navide's `vitest.config.ts` uses Vitest, a Vue transform plugin, aliases, DOM environments, and global package-build setup. A switch to Bun's runner is a separate compatibility/migration question, not an assumed speed improvement.
- [Vitest's guide at v3.2.7](https://github.com/vitest-dev/vitest/blob/v3.2.7/docs/guide/index.md), matching the repository's declared version range floor, documents Bun as a package manager and warns that `bun test` invokes Bun's runner rather than Vitest. This is not a project-specific compatibility or performance guarantee for Vitest executing in Bun.
- [Bun's Node.js compatibility documentation](https://github.com/oven-sh/bun/blob/main/docs/runtime/nodejs-compat.mdx) explicitly describes incomplete compatibility, including gaps in child-process, worker-thread, and other APIs. It does not establish freedom from regressions across Bun upgrades; exact versions and project workflow validation remain necessary.
- `tests/support/publicPackagesSetup.ts` invokes pnpm for public-package builds and tarball creation; it caches unchanged builds but still packs packages for a run. Thus test wall time includes build/pack/subprocess work, not only test JavaScript execution. A future benchmark must include and distinguish these costs without reducing test coverage.
- `packages/plugin-contracts/package.json`, `packages/plugin-sdk/package.json`, and `packages/plugin-ui/package.json` expose distributable JavaScript, declarations, schema/CSS/subpath exports, peer dependencies, and the SDK's `navide-plugin` executable. These concrete external surfaces ground the public-contract discussion; internal package-manager commands are a different surface.

## Agreed experiment design (not execution authorization)

1. Establish a fixed Node.js/pnpm baseline and Bun candidate in isolated copies. Do not change the current working tree, global installations, or production CI.
2. Check installation and dependency resolution first, then typechecks, existing Vitest tests, public-package/plugin builds, and development startup. Prove that daily workflows do not silently invoke an external Node.js/pnpm runtime or package manager.
3. Preserve installation-script permission restrictions, security overrides, dependency auditing, and frozen-lockfile reproducibility. Separately validate public-package consumption through existing Node.js/npm/pnpm workflows.
4. Run the smallest macOS experiment first. If it passes, obtain Linux and Windows evidence. All three platforms must pass before recommending adoption.
5. Stop and report a candidate's gap if it needs a third-party patch, an unapproved dependency upgrade, or external Node.js/pnpm for a daily workflow. Do not broaden scope to make the candidate pass.
6. Return an evidence-backed adopt/defer/retain recommendation. Migration, release-flow changes, and external CI operations require separate authorization. An isolated experiment leaves the production environment unchanged; rollback is to stop using the experimental copy.

## Confirmation and publication boundary

The user accepted Q13-Q15: the benchmark gate, Bun-only contributor version management without mise, and the isolated experiment design. The user requested that the discussion be recorded and asked whether GitHub issue #2 is the better location. After clarifying that retaining Node.js/pnpm means no migration if Bun fails the gates, not a mixed daily toolchain, the user confirmed the shared understanding. The user explicitly authorized updating issue #2. Its body was updated at 2026-10-03T03:11:59Z from `.scratch/bun-toolchain-evaluation/issue-body.md`; GitHub Markdown rendering was checked for unwanted hard line breaks and the published body was fetched and verified against that file. The agreed scope is now published at https://github.com/slighter12/Navide/issues/2. No installation, tests, builds, experimental copies, branches/worktrees, or external CI operations have been executed for this evaluation.

Concrete Bun version/enforcement mechanics and workflow compatibility are findings for a separately authorized experiment, not silently assumed design decisions. An eventual release/CI implementation and rollout remain conditional follow-up work, not a migration committed to by this discussion.

## Documentation discipline

General programming terms such as package manager and test runner are not added to `CONTEXT.md`. No hard-to-reverse architectural choice has been agreed, so an ADR is not warranted yet. Agreed goals and cited evaluation evidence remain here until the discussion establishes a recommendation.

## Evaluation specification

The user invoked `/to-spec` and authorized only creation of the evaluation experiment specification, not execution or migration. The user confirmed the daily-workflow boundary and clarified that public-package compatibility means preserving existing contracts without restricting external consumers' tools. Both boundaries are recorded in `.scratch/bun-toolchain-evaluation/spec.md`, published to the configured local tracker with `Category: maintenance`, `Triage: ready-for-agent`, `Status: open`, and explicit execution gates. No further GitHub issue update is included in this assignment.
