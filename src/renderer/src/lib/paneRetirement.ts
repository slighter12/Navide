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
const inFlight = new WeakMap<object, Map<string, Promise<boolean>>>()

export function closePaneHidden(
  closing: Set<string>,
  paneId: string,
  retire: () => Promise<boolean>,
  isOpen: () => boolean = () => true,
): Promise<boolean> {
  // A pane whose record is already gone (an earlier close settled) has nothing left to close.
  if (!isOpen()) return Promise.resolve(false)
  // One close per pane: a pane that is already closing joins the pending close instead of starting
  // a second `retire`. The check runs when the close is requested, so a batch that snapshotted its
  // ids earlier still closes each pane once. The caller passes the same set every time.
  let pending = inFlight.get(closing)
  if (!pending) inFlight.set(closing, pending = new Map())
  const running = pending.get(paneId)
  if (running) return running
  closing.add(paneId)
  // `.then` defers `retire` one microtask, so a synchronous throw still reaches `.finally` after the map entry exists.
  const close = Promise.resolve().then(retire).finally(() => {
    pending.delete(paneId)
    closing.delete(paneId)
  })
  pending.set(paneId, close)
  return close
}

/** Observe a close already in flight without making the pane a target of another user action. */
export function pendingPaneClose(closing: ReadonlySet<string>, paneId: string): Promise<boolean> | undefined {
  return inFlight.get(closing)?.get(paneId)
}

/**
 * What every surface lists while a close is settling: the same items without the closing panes.
 * The pane record stays in `panes` for the unsettled-pane checks; only what is drawn or listed
 * (stage, sidebar cards, tab strip, spotlight and float lists) goes through this.
 */
export function withoutClosing<L extends readonly { id: string }[]>(items: L, closing: ReadonlySet<string>): L {
  return (closing.size === 0 ? items : items.filter((item) => !closing.has(item.id))) as L
}

/** The same rule for a batch of ids: a pane the user already closed is not closed (or counted) again. */
export function idsWithoutClosing(ids: string[], closing: ReadonlySet<string>): string[] {
  return closing.size === 0 ? ids : ids.filter((id) => !closing.has(id))
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
