# Triage labels

## Category

- `bug`: existing behavior is incorrect.
- `enhancement`: user-visible or platform capability work.
- `maintenance`: internal upkeep without a new platform contract.
- `documentation`: documentation-only work.

## Readiness

- `needs-discovery`: repository evidence is incomplete.
- `needs-decision`: a material product or contract choice is unresolved.
- `ready-for-agent`: scope, dependencies, acceptance criteria, and validation
  are sufficient for ticket splitting and implementation.
- `blocked`: work cannot proceed until a named dependency changes.

Use one Category and one Readiness value. Security-sensitive trust,
authorization, signing, and capability changes must remain explicitly marked
in the specification even when otherwise ready.
