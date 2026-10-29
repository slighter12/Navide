// G1-E renderer-side stream of ONE terminal session (E2 v10 C4 and C6). The test's WebSocket handler hands every `terminal.output` chunk to `onChunk(arrival, bytes)`
// with the arrival time read in the handler; a latency is `arrival of the chunk that completes the exact bytes - send timestamp`. Nothing here polls: a waiter is
// settled only by a chunk, and its deadline is a timer that REJECTS (a failure bound, never a result).
type Waiter = { needle: Buffer | null; predicate: ((buffer: Buffer) => boolean) | null; sendAt: number | null; resolve: (value: number) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }

export class StreamBuffer {
  private readonly chunks: Buffer[] = []
  private cache: Buffer | null = null
  private tail: Buffer = Buffer.alloc(0)
  private lastArrival = -Infinity
  private readonly waiters = new Set<Waiter>()
  readonly violations: string[] = []
  length = 0

  constructor(private readonly onBytes?: (arrival: number, chunk: Buffer) => void) {}

  get pending(): number { return this.waiters.size }

  onChunk(arrival: number, bytes: Uint8Array): void {
    if (!(arrival > this.lastArrival)) this.violations.push('non-increasing arrival time')
    this.lastArrival = Math.max(this.lastArrival, arrival)
    if (bytes.length === 0) { this.violations.push('empty chunk'); return }
    const chunk = Buffer.from(bytes)
    const window = Buffer.concat([this.tail, chunk])
    const fresh = this.tail.length
    this.chunks.push(chunk)
    this.cache = null
    this.length += chunk.length
    this.onBytes?.(arrival, chunk)
    for (const waiter of [...this.waiters]) {
      if (waiter.predicate) { if (waiter.predicate(this.toBuffer())) this.settle(waiter, () => waiter.resolve(arrival)); continue }
      const needle = waiter.needle!
      for (let at = window.indexOf(needle); at >= 0; at = window.indexOf(needle, at + 1)) {
        if (at + needle.length > fresh) { this.settle(waiter, () => waiter.resolve(waiter.sendAt === null ? arrival : arrival - waiter.sendAt)); break }
      }
    }
    const keep = Math.max(0, ...[...this.waiters].map((waiter) => (waiter.needle ? waiter.needle.length - 1 : 0)))
    this.tail = keep === 0 ? Buffer.alloc(0) : window.subarray(Math.max(0, window.length - keep))
  }

  /** Latency of a request: resolves with (arrival of the completing chunk - sendAt). Only bytes arriving after registration complete it. */
  expect(needle: Buffer, sendAt: number, message: string, deadlineMs: number): Promise<number> {
    return this.register(needle, sendAt, message, deadlineMs)
  }

  /** Non-latency wait: resolves with the arrival time of the completing chunk, or `null` at once when the bytes are already present. */
  waitFor(needle: Buffer, message: string, deadlineMs: number): Promise<number | null> {
    if (this.indexOf(needle) >= 0) return Promise.resolve(null)
    return this.register(needle, null, message, deadlineMs)
  }

  /** Resolves (with the arrival time of the completing chunk, or `null` at once) when `predicate(whole stream)` holds; evaluated at registration and on each chunk, never on a timer. */
  until(predicate: (buffer: Buffer) => boolean, message: string, deadlineMs: number): Promise<number | null> {
    if (predicate(this.toBuffer())) return Promise.resolve(null)
    return new Promise<number | null>((resolve, reject) => {
      const waiter: Waiter = { needle: null, predicate, sendAt: null, resolve, reject, timer: setTimeout(() => this.settle(waiter, () => reject(new Error(message))), deadlineMs) }
      this.waiters.add(waiter)
    })
  }

  toBuffer(): Buffer {
    this.cache ??= Buffer.concat(this.chunks)
    return this.cache
  }

  indexOf(needle: Buffer, from = 0): number { return this.toBuffer().indexOf(needle, from) }

  private register(needle: Buffer, sendAt: number | null, message: string, deadlineMs: number): Promise<number> {
    if (needle.length === 0) return Promise.reject(new Error('empty needle'))
    // Keep enough of the existing tail for a string that starts before registration and completes after it.
    this.tail = this.length === 0 ? Buffer.alloc(0) : this.toBuffer().subarray(Math.max(0, this.length - (Math.max(needle.length, ...[...this.waiters].map((w) => (w.needle ? w.needle.length : 0))) - 1)))
    return new Promise<number>((resolve, reject) => {
      const waiter: Waiter = { needle, predicate: null, sendAt, resolve, reject, timer: setTimeout(() => this.settle(waiter, () => reject(new Error(message))), deadlineMs) }
      this.waiters.add(waiter)
    })
  }

  private settle(waiter: Waiter, then: () => void): void {
    clearTimeout(waiter.timer)
    this.waiters.delete(waiter)
    then()
  }
}
