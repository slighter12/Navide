// Pure helpers of the G1-E trial receipt (ticket E4, assignment 2): everything the attempt runner turns into the closed records the Python decider recomputes.
// No I/O and no clock here; the attempt (`g1eAttempt.ts`) feeds these from the WebSocket stream, the counters socket, the peer receipt and the log helper.
import { assembleLedger, startLine, type Counters, type PeerReceipt } from './g1eLedger'
import type { RuntimeMode } from './runtimeMatched'

export const trialFileNames = (mode: RuntimeMode, count: number, repeat: number): { trial: string; resources: string } =>
  ({ trial: `trials/trial-${mode}-${count}-${repeat}.json`, resources: `trials/trial-${mode}-${count}-${repeat}-resources.jsonl` })

/** The discarded warm-up receipts (E2 v14-r2 V19) sit beside the trials in the run directory under their own names; the assembler routes them (`calibration/` or `warmup/`). */
export const warmupFileNames = (mode: RuntimeMode, index: number): { trial: string; resources: string } => {
  const stem = `warmup-${mode === 'rust-shell' ? 'rust' : 'python'}-${String(index).padStart(2, '0')}`
  return { trial: `trials/${stem}.json`, resources: `trials/${stem}-resources.jsonl` }
}

/** The number of processes a `ps -A -o pid=` listing shows: its non-empty lines. No process name and no argument is read. */
export const countProcessLines = (output: string): number => output.split('\n').filter((line) => line.trim() !== '').length

export type HostNumbers = { at: number; loadavg: number[]; ncpu: number; processCount: number }

/** The host numbers of one point of an attempt (E2 v14-r2 V19 item 5), numbers only: the clock, the three load averages, the CPU count and the process count. Information only. */
export const hostNumbers = (read: { now: () => number; loadavg: () => number[]; ncpu: () => number; ps: () => string }): HostNumbers =>
  ({ at: read.now(), loadavg: read.loadavg(), ncpu: read.ncpu(), processCount: countProcessLines(read.ps()) })

type RawTable = Record<string, { frames: number; bytes: number }>
type RawLedger = { tx: RawTable; rx: RawTable; unknown_types: unknown[]; tx_bytes: number; rx_bytes: number }

/** The sidecar control-plane ledger in the decider's shape: frame COUNTS by wire-v1 type name, unknown types as strings, control bytes in both directions, and the stream bytes (descriptive). */
export function mapControlLedger(raw: RawLedger | null, streamBytes: number) {
  if (raw === null) return null
  const counts = (table: RawTable): Record<string, number> => Object.fromEntries(Object.entries(table).map(([name, entry]) => [name, entry.frames]))
  return { tx: counts(raw.tx), rx: counts(raw.rx), unknownTypes: raw.unknown_types.map(String), controlBytes: raw.tx_bytes + raw.rx_bytes, streamBytes }
}

const FINAL = /^BASELINE_FINAL:(\d{1,10}):([0-9a-f]{64})\n/

/** The FINAL line at the start of `bytes`, or null when it is absent or incomplete (no LF yet). */
export function finalLine(bytes: Buffer): { accepted: number; digest: string; end: number } | null {
  const match = FINAL.exec(bytes.subarray(0, 100).toString('latin1'))
  return match ? { accepted: Number(match[1]), digest: match[2], end: match[0].length } : null
}

/** What the peer accepted after its start marker line, through the end of the FINAL line; null when the marker is missing/repeated or the FINAL line is missing/incomplete. */
export function peerTextAfterMarker(stream: Buffer, nonce: string): Buffer | null {
  const marker = startLine(nonce)
  const first = stream.indexOf(marker)
  if (first < 0 || stream.indexOf(marker, first + 1) >= 0) return null
  const after = stream.subarray(first + marker.length)
  const at = after.indexOf('BASELINE_FINAL:')
  if (at < 0) return null
  const final = finalLine(after.subarray(at))
  return final ? after.subarray(0, at + final.end) : null
}

export const countersStable = (a: Counters, b: Counters): boolean =>
  a.read_total === b.read_total && a.emitted_total === b.emitted_total && a.dropped_oldest_total === b.dropped_oldest_total

/** Reads until two consecutive reads agree; `stable=false` (the last read) after `maxReads` without agreement. The caller decides what an unstable read means (the trial is invalid). */
export async function readStable(read: () => Promise<Counters>, maxReads = 8): Promise<{ counters: Counters; stable: boolean }> {
  let previous = await read()
  for (let n = 1; n < maxReads; n++) {
    const next = await read()
    if (countersStable(previous, next)) return { counters: next, stable: true }
    previous = next
  }
  return { counters: previous, stable: false }
}

export type PeerFile = PeerReceipt & { rawMode: { opost: boolean; onlcr: boolean; echo: boolean }; peerAcceptedAtOrigin: number; windows: Array<{ wid: string; digest: string; start?: number; end?: number }> }

/** One session's closed ledger record (the decider's `summarise.ledger_problems` input). `generation` is a constant 0: the harness creates every session once per attempt. */
export function ledgerRecord(input: {
  stream: Buffer; nonce: string; peerIndex: number; burstRounds: number; session: string; pane: string
  origin: { counters: Counters; stable: boolean }; close: Counters; peerReceipt: PeerFile
  logIdentity: Record<string, unknown>; logFile: Record<string, unknown>
}) {
  const { peerReceipt: peer } = input
  return assembleLedger(input.stream, input.nonce, {
    peerIndex: input.peerIndex, burstRounds: input.burstRounds, session: input.session, pane: input.pane, generation: 0, rawMode: peer.rawMode,
    origin: { ...input.origin.counters, stable: input.origin.stable, peerAcceptedAtOrigin: peer.peerAcceptedAtOrigin }, close: input.close,
    peer: { acceptedSinceOrigin: peer.acceptedSinceOrigin, finalLineBytes: peer.finalLineBytes, totalDigest: peer.totalDigest, writeFailed: peer.writeFailed,
      windows: peer.windows.map(({ wid, digest }) => ({ wid, digest })) },
    logIdentity: input.logIdentity, logFile: input.logFile,
  })
}
