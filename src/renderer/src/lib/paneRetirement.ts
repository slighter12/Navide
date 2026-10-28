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
 * keeps the pane, its terminal ref and its record, and starts no replacement. On the user's own
 * close (`closePaneHidden`) the pane is hidden while settling and shown again, with the error, on
 * such a failure.
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

/**
 * The user's own pane close hides the pane while its settlement is pending, so the CLI's shutdown
 * output is never shown. Hiding only removes the pane from the layout: its record, terminal ref and
 * the per-pane create fence stay until the backend proves settlement, so nothing may start a
 * replacement. A proven answer drops the pane as before; a refusal, a timeout, a transport failure
 * or a throw shows the same pane again (the caller reports the error).
 */
const inFlight = new WeakMap<object, Map<string, number>>()

export async function closePaneHidden(
  closing: Set<string>,
  paneId: string,
  retire: () => Promise<boolean>,
): Promise<boolean> {
  // A count per pane keeps the hide until the LAST overlapping close settles (an earlier refusal
  // must not reveal it). The caller passes the same set every time.
  let counts = inFlight.get(closing)
  if (!counts) inFlight.set(closing, counts = new Map())
  counts.set(paneId, (counts.get(paneId) ?? 0) + 1)
  closing.add(paneId)
  try {
    return await retire()
  } finally {
    const left = (counts.get(paneId) ?? 1) - 1
    if (left > 0) {
      counts.set(paneId, left)
    } else {
      counts.delete(paneId)
      closing.delete(paneId)
    }
  }
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
