// G1-E event-driven wait (E2 v10 C4 and C6): replaces the 06 harness's 5 ms `delay(5)` polling loop. A waiter re-checks its predicate when, and only when, a
// WebSocket message handler calls `notify()` after updating its state; the deadline is a FAILURE bound (a timer that rejects), never a polling interval. Every latency is
// taken as `arrival timestamp - send timestamp` with the arrival timestamp read in the message handler (`now()` at `notify` time), so the wait's own scheduling adds nothing.
type Waiter = { check: () => boolean; resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }

export class ArrivalWaiter {
  private readonly waiters = new Set<Waiter>()

  /** Call from the message handler after the state the predicates read has been updated. */
  notify(): void {
    for (const waiter of [...this.waiters]) {
      if (this.waiters.has(waiter) && waiter.check()) this.settle(waiter, () => waiter.resolve())
    }
  }

  until(check: () => boolean, message: string, deadlineMs: number): Promise<void> {
    if (check()) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      const waiter: Waiter = { check, resolve, reject, timer: setTimeout(() => this.settle(waiter, () => reject(new Error(message))), deadlineMs) }
      this.waiters.add(waiter)
    })
  }

  get pending(): number { return this.waiters.size }

  private settle(waiter: Waiter, then: () => void): void {
    clearTimeout(waiter.timer)
    this.waiters.delete(waiter)
    then()
  }
}
