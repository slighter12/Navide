# Domain documentation

Navide uses a single repository context. Read [`CONTEXT.md`](../../CONTEXT.md)
for the ubiquitous language. There is no `CONTEXT-MAP.md`.

For Agent CLI Integration contract design or Rust migration evaluation, read
[`ADR 0001`](../adr/0001-preserve-agent-cli-semantics.md) for semantic constraints,
evaluation sequencing and migration exclusions, and
[`ADR 0002`](../adr/0002-language-neutral-agent-cli-declarations.md) for shared
declaration ownership. Read
[`ADR 0003`](../adr/0003-evaluate-a-complete-agent-cli-runtime.md) for the target
runtime ownership boundary and capability status distinctions.

For plugin-platform work, use these sources in order:

1. `docs/en-US/plugin-development-v2.md` for the public target draft.
2. `docs/plugin-contracts/` for normative machine-readable draft contracts.
3. `.agent-team/plans/plugin-architecture-separation_a84c2f.html` for target
   ownership and architectural invariants.
4. `.agent-team/plans/plugin-migration-feasibility_7c4e91.html` for B0-B9
   migration order, evidence gates, and rollback rules.
5. `.scratch/plugin-platform-v2/spec.md` for implementation stories and
   blocking decisions.

If contexts or ADRs are added later, update this file before relying on them as
project sources of truth.
