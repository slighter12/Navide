// The renderer-side stream of the ONE agent pane of a G3 launch attempt. The READY waiter is registered BEFORE `terminal.create` is sent (review-1 finding 7): output that reaches the client before
// the create response is buffered with its arrival stamp and still completes the waiter at that arrival; `StreamBuffer.until` would otherwise return `null` for bytes already present.
// The stamp is whatever clock the caller feeds `onFrame` (performance.now() in a measured attempt, a plain counter in the pre-F0 smoke, which takes no timing value).
import { StreamBuffer } from './g1eStream'

const READY = 'BASELINE_READY '

export class LaunchReady {
  readonly stream = new StreamBuffer()
  private last = -Infinity
  private readonly tids = new Set<string>()

  /** One `terminal.output` frame. Arrival stamps are made strictly increasing, as `g1eAttempt` does per session. */
  onFrame(terminalSessionId: string, at: number, data: Uint8Array): void {
    this.tids.add(terminalSessionId)
    this.last = Math.max(at, this.last + 1e-6)
    this.stream.onChunk(this.last, data)
  }

  /** Resolves with the arrival stamp of the chunk that completes `BASELINE_READY {json}\n`; registered before any byte can have arrived. */
  waitReady(deadlineMs: number): Promise<number | null> {
    return this.stream.until((buffer) => { const at = buffer.indexOf(READY); return at >= 0 && buffer.includes('\n', at) }, 'READY line deadline', deadlineMs)
  }

  /** True when a frame of another terminal reached the client: the line cannot be attributed to the sole created pane. */
  foreign(createdTerminalSessionId: string): boolean {
    return [...this.tids].some((id) => id !== createdTerminalSessionId)
  }
}
