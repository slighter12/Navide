/**
 * Retiring a pane's terminal (ticket 05, review-9): ONE rule for every path that closes,
 * rebuilds, reclaims or releases a pane. The pane's own `terminal.kill` is a best-effort first
 * step (it carries the caller's graceful/force choice); the proof is the backend's settlement
 * answer — `manual_pane.release_pty`, `manual_pane.unspawn` or `pipeline.slot_unspawn` — which
 * settles in-flight creates and published sessions for the pane (verified cleanup) BEFORE it
 * touches any record, and refuses otherwise.
 *
 * Only a non-null answer that does not say `ok: false` proves settlement. A refusal, a timeout or
 * a transport failure (`sendQuiet` answers `null` for all three) is a hard failure: the caller
 * keeps the pane, its terminal ref and its record, and starts no replacement.
 */
export type SettlementAnswer = object | null | undefined

export interface PaneRetirementSteps {
  /** The pane's own terminal kill, when it holds a terminal id. */
  killOwn?: () => Promise<void>
  /** Told about a failed own kill (the settlement below still decides). */
  onKillFailed?: (error: unknown) => void
  /** The backend settlement for the pane. */
  settle: () => Promise<SettlementAnswer>
}

export async function settlePaneTerminals(steps: PaneRetirementSteps): Promise<boolean> {
  if (steps.killOwn) {
    try {
      await steps.killOwn()
    } catch (error) {
      steps.onKillFailed?.(error)
    }
  }
  const answer = await steps.settle()
  return answer != null && (answer as { ok?: unknown }).ok !== false
}
