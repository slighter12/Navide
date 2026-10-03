## Purpose

Evaluate whether replacing Navide's daily JavaScript development toolchain with Bun would simplify setup and version management without breaking existing packages or public contracts.

The `grill-with-docs` discussion has concluded and the evaluation design below is agreed. This is not a decision to migrate. Only documentation has been authorized; experiments, migration implementation, branch/worktree creation, and external CI operations require a separate go-ahead.

## Agreed goals

- Primary: avoid failures and confusion caused by tool upgrades and multiple local Node.js/pnpm installations, and simplify contributor setup.
- Target: daily dependency installation, development startup, typechecking, tests, and application/plugin builds require only Bun for JavaScript tooling, with no separately installed Node.js or pnpm.
- Secondary: improve test speed if possible. Faster Vitest execution under Bun is a hypothesis to measure, not an established benefit or mandatory adoption condition.
- Bun is a candidate, not a predetermined choice. If it fails the agreed gates, defer/reject migration and leave the existing Node.js/pnpm toolchain unchanged. This does not permit a mixed daily-development toolchain or partial adoption that still requires external Node.js/pnpm.
- Improving the existing Node.js/pnpm setup would be a separate proposal requiring evidence and authorization, not an automatic fallback implementation.

## Scope and runtime boundaries

- Prioritize replacing pnpm with Bun while directly reusing existing packages. Assess the additional script/runtime adaptations required to satisfy the Bun-only daily-workflow target.
- Evaluate the existing Vue/Vite frontend, Electron development/build tooling, workspace packages, public packages, plugin builds, and Vitest workflows.
- Release packaging and CI may retain an explicitly isolated Node.js environment, but must not turn that exception into a daily local prerequisite. Compatibility validation must still establish Bun-only daily workflows on all three platforms.
- Electron's embedded Node.js runtime is not the external development runtime and is not being replaced. Node.js requirements of unrelated projects or external agent CLIs are outside this target.

## Version-management requirements

- Pin one exact Bun version for the project. A global upgrade must not silently select a different version for project operations.
- Reject a version mismatch before installation/build execution and provide actionable instructions. Do not silently upgrade or downgrade Bun.
- Validate project Bun upgrades before accepting them.
- Installing the specified Bun version must be sufficient. Do not require mise or another version manager or make one part of the proposed contributor setup; personal use remains optional.
- Determine the concrete enforcement mechanism through the separately authorized evaluation rather than assuming it already works.

## Compatibility floor and change limits

- Preserve Vitest and existing test validation. Replacing Vitest with `bun test`, deleting tests, weakening assertions, or skipping failures to make Bun pass is out of scope.
- Project tool configuration, scripts, and test build/pack/launch helpers may be adapted only in a future authorized experiment.
- Do not fork, patch, or replace third-party packages to obtain Bun compatibility.
- Preserve existing direct/transitive dependency versions and security overrides in the first comparison. Lockfile format and installation layout may differ, but resolution differences must be reported. Any required dependency upgrade is a separate decision, not proof that the existing package worked unchanged.
- Preserve installation-script permission restrictions, dependency auditing, and frozen-lockfile reproducibility; do not weaken these controls to make installation pass.
- Preserve public package exports, types, peer dependencies, and CLI behavior, including `@navide/plugin-contracts`, `@navide/plugin-sdk`, `@navide/plugin-ui`, and `navide-plugin`.
- Preserve plugin packaging, installation, and execution behavior.
- External plugin developers must remain able to consume public packages through existing Node.js/npm/pnpm workflows without being forced to adopt Bun.
- macOS, Linux, and Windows are all mandatory adoption gates. macOS-only results establish local feasibility, not project-wide migration readiness.

## Performance gate

- Compare full-suite and common single-file test workflows on the same machine and dependency versions, separating cold/warm-cache conditions.
- Start with five samples per condition and compare medians. More than 10% slowdown against the fixed Node.js/pnpm baseline fails the performance gate.
- Add samples when variance is high rather than drawing an immediate conclusion.
- Include build, pack, and subprocess costs in workflow timings; do not report runtime-only microbenchmarks as end-to-end gains or reduce validation coverage for speed.

## Minimal experiment design — not yet authorized to execute

1. Establish fixed Node.js/pnpm baseline and Bun candidate environments in isolated copies. Do not change the current working tree, global installations, or production CI.
2. Check installation and dependency resolution first, then typechecks, existing Vitest tests, public-package/plugin builds, and development startup. Prove that daily workflows do not silently invoke external Node.js/pnpm.
3. Verify reproducibility and retained security controls, and separately verify public-package consumption through existing Node.js/npm/pnpm workflows.
4. Run the smallest macOS experiment first. If it passes, obtain Linux and Windows evidence through separately authorized execution paths. All three platforms must pass before recommending adoption.
5. Stop and report a candidate's gap if it needs a third-party patch, an unapproved dependency upgrade, or external Node.js/pnpm for a daily workflow. Do not broaden scope to make it pass.
6. Record actual compatibility and performance results and recommend adoption, deferral, or retaining the current toolchain. Any migration, release-flow changes, or rollout requires separate authorization and separately verifiable follow-up work.

The isolated experiment leaves the production environment unchanged; its rollback is to stop using the experimental copy. Production migration and rollback design remain conditional follow-up work after a favorable evaluation.

## Evidence and hypotheses

- Root `package.json` declares `pnpm@10.0.0` and Node.js `>=22.12 <23`, contains explicit `node`/`pnpm` script calls, and uses local package links, security overrides, and an installation-script allowlist. A command-prefix change alone does not establish compatibility.
- `tests/support/publicPackagesSetup.ts` invokes pnpm for public-package builds and tarball creation. Test wall time therefore includes work beyond JavaScript test execution.
- Bun's [official runtime documentation](https://github.com/oven-sh/bun/blob/main/docs/runtime/index.mdx#--bun) explains that CLI Node.js shebangs are respected by default and can be overridden with `--bun`. `bun run` alone does not prove that external Node.js has been eliminated.
- The [Vitest guide at v3.2.7](https://github.com/vitest-dev/vitest/blob/v3.2.7/docs/guide/index.md) documents Bun as a package manager and distinguishes `bun run test` from Bun's own `bun test` runner. It is not a Navide-specific Bun runtime compatibility or speed guarantee.
- Bun's [Node.js compatibility documentation](https://github.com/oven-sh/bun/blob/main/docs/runtime/nodejs-compat.mdx) lists remaining API differences. Bun upgrades must still be pinned and validated; freedom from version regressions is not assumed.
- No installation experiment, test/build run, or three-platform compatibility/performance result has been produced by this discussion.

## Out of scope

- Replacing Vue/Vite or the Electron desktop host.
- Rewriting the Python backend or CLI integrations.
- Replacing Electron's embedded Node.js runtime.
- Third-party package forks/patches/replacements, a new test runner, or hidden dependency upgrades.
- Treating a mixed daily-development toolchain as achieving the agreed goal.
- Modifying release/CI infrastructure, global installations, or the current working tree as part of this documentation update.

## Coordination and next step

This evaluation remains independent of the Rust CLI and desktop-host evaluations. Any eventual shared build or CI changes must be coordinated explicitly rather than silently included here.

The next step is separate authorization for the isolated experiment, not automatic implementation. Confirming this design or updating this issue does not grant that authorization.
