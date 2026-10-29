// G1-E burst latency waiter (E2 v10 C5, finding F2): a streaming port of the reference `burst_round_latencies` in
// backend/tests/cli_regression/support/g1e/reference.py. The only endpoint of round kk is the chunk that completes the EXACT bytes
// `BASELINE_DONE:r<kk>\n` on the renderer stream (split across chunks allowed). Latency = endpoint arrival - the client's send timestamp of `burst r<kk>`.
// Semantic rejections (the whole trial is invalid, no latency is ever returned): a round's endpoint earlier than its send, round kk+1 sent before round kk's
// endpoint, non-increasing send times, an endpoint occurring more than once, a round count other than the cell's registered one (v14-r2 V17: cell 1 = 40 rounds of
// one peer, cell 4 = 10 rounds per peer; `runtime-workload-g1e.json` carries the same numbers and a test pins the two together).
const REGISTERED_BURST_ROUNDS: Readonly<Record<number, number>> = { 1: 40, 4: 10 }
export const burstRounds = (cell: number): number => {
  const rounds = REGISTERED_BURST_ROUNDS[cell]
  if (rounds === undefined) throw new Error(`cell ${cell} has no registered burst rounds`)
  return rounds
}
export const doneLine = (round: number): Buffer => Buffer.from(`BASELINE_DONE:r${String(round).padStart(2, '0')}\n`, 'utf8')

export type BurstVerdict = { kind: 'pass' | 'invalid-semantic' | 'invalid-input'; why: string; latencies: Array<number | null> | null }

export class BurstTracker {
  private readonly needles: Buffer[]
  private tail: Buffer = Buffer.alloc(0)
  private readonly firstEndpoint: Array<number | null>
  private readonly occurrences: number[]
  private readonly sendAt = new Map<number, number>()
  private lastArrival = -Infinity
  private invalidInput: string | null = null

  constructor(private readonly cell: number, private readonly rounds: number = burstRounds(cell)) {
    this.needles = Array.from({ length: rounds }, (_, round) => doneLine(round))
    this.firstEndpoint = Array(rounds).fill(null)
    this.occurrences = Array(rounds).fill(0)
  }

  markSend(round: number, at: number): void {
    if (!Number.isInteger(round) || round < 0 || round >= this.rounds || !Number.isFinite(at) || at < 0 || this.sendAt.has(round)) this.invalidInput = `send of round ${round}`
    else this.sendAt.set(round, at)
  }

  /** One renderer chunk arrived at `at` (the message-handler timestamp, C4/C6). Arrival times must strictly increase. */
  onChunk(at: number, chunk: Uint8Array): void {
    if (!Number.isFinite(at) || at < 0 || at <= this.lastArrival || chunk.length === 0) { this.invalidInput = 'chunk time or size'; return }
    this.lastArrival = at
    const window = Buffer.concat([this.tail, chunk])
    const fresh = this.tail.length
    this.needles.forEach((needle, round) => {
      for (let from = window.indexOf(needle); from >= 0; from = window.indexOf(needle, from + 1)) {
        if (from + needle.length > fresh) { // ends inside the new chunk: counted exactly once
          this.occurrences[round]++
          if (this.firstEndpoint[round] === null) this.firstEndpoint[round] = at
        }
      }
    })
    const keep = this.needles[0].length - 1
    this.tail = window.subarray(Math.max(0, window.length - keep))
  }

  result(): BurstVerdict {
    const bad = (kind: BurstVerdict['kind'], why: string): BurstVerdict => ({ kind, why, latencies: null })
    if (this.invalidInput) return bad('invalid-input', this.invalidInput)
    if (this.sendAt.size !== this.rounds) return bad('invalid-input', 'send timestamps are not exactly one per round')
    if (this.rounds !== REGISTERED_BURST_ROUNDS[this.cell]) return bad('invalid-semantic', 'rounds differ from the registered burstRounds of the cell')
    for (let round = 1; round < this.rounds; round++) {
      if (!(this.sendAt.get(round)! > this.sendAt.get(round - 1)!)) return bad('invalid-semantic', `send time of round ${round} must follow round ${round - 1}`)
    }
    for (let round = 0; round < this.rounds; round++) if (this.occurrences[round] > 1) return bad('invalid-semantic', `round ${round} endpoint occurs more than once`)
    for (let round = 0; round < this.rounds; round++) {
      const end = this.firstEndpoint[round]
      if (end === null) continue
      if (end < this.sendAt.get(round)!) return bad('invalid-semantic', `round ${round} endpoint precedes its send`)
      if (round + 1 < this.rounds && this.sendAt.get(round + 1)! < end) return bad('invalid-semantic', `round ${round + 1} sent before round ${round} ended`)
    }
    return { kind: 'pass', why: 'ok', latencies: this.firstEndpoint.map((end, round) => (end === null ? null : end - this.sendAt.get(round)!)) }
  }
}
