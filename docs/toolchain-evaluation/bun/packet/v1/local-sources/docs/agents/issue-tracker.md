# Issue tracker

Navide uses repository-local Markdown specifications as the planning tracker.

## Location

- Store one implementation specification at `.scratch/<slug>/spec.md`.
- Keep supporting evidence in the same `.scratch/<slug>/` directory.
- Do not treat `.agent-team/plans/` as the implementation tracker; those HTML
  documents are design inputs and review records.

## Workflow

1. Create or revise `spec.md` with `Category`, `Triage`, and `Status` metadata.
2. Set `Triage: ready-for-agent` only when the specification can be split into
   independently verifiable tickets without inventing requirements.
3. Record unresolved choices under `Blocking decisions`. A ticket that depends
   on one remains blocked until the decision is recorded.
4. Split work by the specification's numbered stories and dependency edges.
5. Update status in the local specification; do not create external issues,
   commits, or pull requests without explicit authorization.

## Status values

- `open`: specified but not implemented.
- `in-progress`: at least one implementation ticket is active.
- `blocked`: no ticket can progress because a named blocking decision or
  external prerequisite is unresolved.
- `complete`: every acceptance criterion and required validation has passed.
