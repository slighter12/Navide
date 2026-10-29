// G1-E byte-ledger assembly on the renderer side (E2 v10 section 3 item 4 c-e). Pure functions over the bytes the WebSocket client received for ONE session,
// the reader counters snapshotted at the origin and at the close, and the peer's own receipt (written by backend/tests/cli_regression/support/g1e_peer.py).
// The record's field names are the decider's (`summarise.ledger_problems`); the golden file `g1e-ledger-golden.json` pins both sides to the same bytes.
import { createHash } from 'node:crypto'

export type Counters = { read_total: number; emitted_total: number; dropped_oldest_total: number }
export type PeerWindow = { wid: string; digest: string }
export type PeerReceipt = { acceptedSinceOrigin: number; finalLineBytes: number; totalDigest: string; writeFailed: boolean; windows: PeerWindow[] }
export type LedgerInput = {
  peerIndex: number; session: string; pane: string; generation: number
  burstRounds: number // the peer's cell rounds (v14-r2 V17: 40 in cell 1, 10 per peer in cell 4); names the b<peer>r<kk> windows. Not part of the record
  rawMode: { opost: boolean; onlcr: boolean; echo: boolean }
  origin: Counters & { stable: boolean; peerAcceptedAtOrigin: number }
  close: Counters
  peer: PeerReceipt
  logIdentity: Record<string, unknown>; logFile: Record<string, unknown>
}

const sha256 = (data: Uint8Array): string => createHash('sha256').update(data).digest('hex')
export const startLine = (nonce: string): Buffer => Buffer.from(`BASELINE_LEDGER_START:${nonce}\n`, 'utf8')
export const beginLine = (wid: string): Buffer => Buffer.from(`BASELINE_WIN:${wid}:BEGIN\n`, 'utf8')
export const endLine = (wid: string): Buffer => Buffer.from(`BASELINE_WIN:${wid}:END\n`, 'utf8')
export const expectedWindowIds = (peerIndex: number, burstRounds: number): string[] => [`i${peerIndex}`, ...Array.from({ length: burstRounds }, (_, k) => `b${peerIndex}r${String(k).padStart(2, '0')}`)]

function occurrences(haystack: Buffer, needle: Buffer): number[] {
  const out: number[] = []
  for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, at + 1)) out.push(at)
  return out
}

/** The bytes strictly between a window's BEGIN line and its END line (exactly one of each required), or null. */
export function windowDigest(post: Buffer, wid: string): string | null {
  const begin = occurrences(post, beginLine(wid))
  const end = occurrences(post, endLine(wid))
  if (begin.length !== 1 || end.length !== 1) return null
  const from = begin[0] + beginLine(wid).length
  return end[0] < from ? null : sha256(post.subarray(from, end[0]))
}

export function assembleLedger(stream: Buffer, nonce: string, input: LedgerInput) {
  const markers = occurrences(stream, startLine(nonce))
  const post = markers.length >= 1 ? stream.subarray(markers[0] + startLine(nonce).length) : Buffer.alloc(0)
  const finalAt = post.indexOf('BASELINE_FINAL:')
  const finalEnd = finalAt >= 0 ? post.indexOf(0x0a, finalAt) : -1
  const finalMatch = finalAt >= 0 && finalEnd >= 0 ? /^BASELINE_FINAL:(\d{1,10}):([0-9a-f]{64})$/.exec(post.subarray(finalAt, finalEnd).toString('utf8')) : null
  const windows = expectedWindowIds(input.peerIndex, input.burstRounds).flatMap((wid) => {
    const digest = windowDigest(post, wid)
    return digest === null ? [] : [{ wid, digest }]
  })
  return {
    session: input.session, pane: input.pane, generation: input.generation, peerIndex: input.peerIndex, nonceSha256: sha256(Buffer.from(nonce, 'utf8')),
    rawMode: input.rawMode, origin: input.origin, close: input.close, peer: input.peer,
    stream: { markerCount: markers.length, finalSeen: finalMatch !== null, finalAccepted: finalMatch ? Number(finalMatch[1]) : 0, finalDigest: finalMatch ? finalMatch[2] : '0'.repeat(64), windows },
    logIdentity: input.logIdentity, logFile: input.logFile,
  }
}
