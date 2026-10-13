// Source-coupled finite controls interpret this restricted boolean expression;
// they do not execute JS, a backend, or a native process.
// This is native reader observation, not an app-store commit acknowledgment.
export function matchesUsageObservation(row: any, expected: any): boolean {
  if (!row || !row.checkpoint || !row.identity) return false
  return (
    row.event === 'observed'
    && row.parser === 'rust-opencode-sqlite-v1'
    && row.mode === 'usage'
    && row.runtime_generation === expected.generation
    && row.path === expected.path
    && row.error === undefined
    && row.identity.dev === expected.dev
    && row.identity.ino === expected.ino
    && row.checkpoint.kind === 'sqlite'
    && row.checkpoint.identity === expected.identity
    && row.checkpoint.row_id === expected.row_id
    && row.checkpoint.anchor === expected.anchor
    && JSON.stringify(row.checkpoint.pending) === JSON.stringify(expected.pending)
    && row.event_count === expected.event_count
  )
}
