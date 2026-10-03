# 05 — Validate public artifacts for existing consumers

**What to build:** demonstrated compatibility of actual Bun-produced public artifacts with existing independent Plugin-author projects and Node.js/npm/pnpm workflows, without imposing Bun or new integration rules on consumers.

Blocked by: 03 — Validate macOS typechecks, builds, and isolated development startup (pass and identified candidate artifacts available).

Category: maintenance
Triage: ready-for-agent
Status: open
Execution authorization: not granted

Source: Bun-only daily JavaScript toolchain evaluation, issue #2.
Stage: E2 — public-consumer slice.
Stories: 25–27, 29–30, 38–39.

## Acceptance criteria

- [ ] Obtain explicit authorization for the isolated consumer probes. Use the recorded candidate artifacts from 03 and an equivalent supported-baseline artifact/control; do not publish packages or modify external authors' projects.
- [ ] Consume actual packed contracts, SDK, and UI packages in independent projects. Public Navide packages must not resolve through repository-private source aliases, workspace-only dependencies, another Plugin's source, or stale distributions.
- [ ] Verify existing public exports and subpaths, declarations/types, schemas, styles/portable workers, peer dependencies, and documented CLI behavior through observable consumer operations, not package metadata alone.
- [ ] Install/consume the artifacts through the claimed existing npm and pnpm workflows using an explicitly recorded supported Node.js environment. Record exact manager versions and executables; successful manifest inspection or reused peer links alone does not prove either manager's consumption path.
- [ ] Exercise consumer typechecks, builds, and applicable public Plugin CLI validation/packaging/signing-verification behavior using the existing public contract. Preserve relevant existing denial and signature cases with isolated keys/fixtures.
- [ ] Show consumers are not required to install Bun or adopt a version manager. The evaluator must not introduce a new consumer-facing tool restriction, contract, or workaround to compensate for broken artifacts.
- [ ] Record the provenance and independence of packed artifacts and consumer dependencies. Reuse existing external-workspace/composition/package-boundary tests as prior art without claiming their existing source/peer-link setup is already new runtime evidence.
- [ ] Keep consumer Node.js/npm/pnpm environments separate from the Bun-only daily environment. Their allowed use here does not authorize a runtime fallback in 02–04 or change the daily-development goal.
- [ ] Preserve package versions and public contracts. Stop if compatibility requires a third-party patch/replacement, unapproved upgrade, weaker assertions, private API, or expanded Host/Plugin authority.
- [ ] Deliver per-consumer-toolchain command, environment, exit-code, log, type/build/CLI result, and artifact records. Report genuine gaps; do not claim every historical released binary is covered by these cases.

## Scope and handoff

Existing public contracts are the floor, not new integration requirements. This ticket does not dictate consumer tools, perform registry publication, exercise personal credentials, modify the Desktop Host, or authorize a migration. Passing results complete the public-consumer part of macOS E2 and make 06 dependency-ready together with 04.
