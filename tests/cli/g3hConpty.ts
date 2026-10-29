// The Windows ConPTY view of a renderer stream (rh16, windows-4b). Test-only; a pack file (`g3h*`), imported by the G3 copy of the shell attempt (`g3ShellAttempt.ts`, see attempt_copy.py) and by the ConPTY probe.
//
// An inbox ConPTY renders its screen instead of passing the peer's bytes through: it ends lines with CR LF, cuts a line longer than the console width with CR LF and cursor-position sequences, and puts CSI
// sequences between the text. The product's own Windows test strips exactly that before it compares exact bytes (`tests/cli/shellRuntime.test.ts`: the READY line is read up to the first CR LF, and the burst
// view is `replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\r\n(?=x)/g, '')`, "a CR LF that is followed by the line's own filler cannot be a line end"). This module applies the same three rules, and nothing
// else, to the bytes the attempt matches and hashes, on Windows only:
//   1. a CSI sequence (the product's own pattern) is removed;
//   2. a CR LF that follows the filler character `x` and is followed by one (after any CSI) is a wrap cut and is removed;
//   3. every other CR LF becomes LF (the peer wrote LF: raw mode, no output post-processing).
// The view is the same for both arms, and off Windows it is the identity (the same object comes back). It is streaming: the bytes of a sequence that is not finished (an unterminated CSI, a trailing CR, a CR LF
// after `x` whose next byte has not arrived) are held back until they are, so a chunk boundary can never change the result, and a LF that cannot be a wrap cut is passed on at once (an ACK line ending in LF is
// seen when its chunk arrives, not when the next one does).
//
// The width: the burst line is 2048 filler characters wide, and the attempt asks for 100 columns. Where the console takes a wider size (`CreatePseudoConsole` takes a 16-bit width; the sidecar and the backend
// pass the value through), the attempt asks for `conptyColumns(n)` instead, so that the line is not cut at all and rule 2 stays a safety net.
export const FILLER = 0x78 // 'x', the burst filler of g1e_peer.py (`xs="x" * burstWidth`)
export const CONPTY_EXTRA_COLUMNS = 2200 // 100 -> 2300, 120 -> 2320, 140 -> 2340: wider than the 2048-wide burst line plus its prefix; the differences between the sizes of the workload are kept

/** The console width the attempt asks for: the workload's own on every other platform, wider by a fixed amount on Windows. */
export const conptyColumns = (columns: number, platform: string = process.platform): number => (platform === 'win32' ? columns + CONPTY_EXTRA_COLUMNS : columns)

const isParam = (byte: number): boolean => (byte >= 0x30 && byte <= 0x39) || byte === 0x3b || byte === 0x3f // [0-9;?]
const isIntermediate = (byte: number): boolean => byte >= 0x20 && byte <= 0x2f // [ -/]
const isFinal = (byte: number): boolean => byte >= 0x40 && byte <= 0x7e // [@-~]

/** The end (exclusive) of the CSI sequence starting at `at`; `'short'` when the data ends inside it; `null` when the bytes there are not a CSI sequence of the product's pattern. */
function csiEnd(data: Uint8Array, at: number): number | 'short' | null {
  if (at + 1 >= data.length) return 'short'
  if (data[at + 1] !== 0x5b) return null
  let j = at + 2
  while (j < data.length && isParam(data[j])) j++
  while (j < data.length && isIntermediate(data[j])) j++
  if (j >= data.length) return 'short'
  return isFinal(data[j]) ? j + 1 : null
}

/** The first byte at or after `at` that is not part of a CSI sequence, or `'short'` when it is not known yet. */
function nextVisible(data: Uint8Array, at: number): number | 'short' {
  let j = at
  while (j < data.length) {
    if (data[j] !== 0x1b) return data[j]
    const end = csiEnd(data, j)
    if (end === 'short') return 'short'
    if (end === null) return data[j]
    j = end
  }
  return 'short'
}

export class ConptyView {
  private carry: Buffer = Buffer.alloc(0)
  private last = -1

  /** The normalised bytes of `bytes` (after the held-back ones of the previous call); may be empty. */
  push(bytes: Uint8Array): Buffer {
    const data = this.carry.length ? Buffer.concat([this.carry, bytes]) : Buffer.from(bytes)
    const out: number[] = []
    const emit = (byte: number): void => { out.push(byte); this.last = byte }
    let i = 0
    let held = -1
    while (i < data.length) {
      const byte = data[i]
      if (byte === 0x1b) {
        const end = csiEnd(data, i)
        if (end === 'short') { held = i; break }
        if (end === null) { emit(byte); i++; continue }
        i = end
        continue
      }
      if (byte === 0x0d) {
        if (i + 1 >= data.length) { held = i; break }
        if (data[i + 1] !== 0x0a) { emit(byte); i++; continue }
        if (this.last === FILLER) {
          const next = nextVisible(data, i + 2)
          if (next === 'short') { held = i; break }
          if (next === FILLER) { i += 2; continue }
        }
        emit(0x0a)
        i += 2
        continue
      }
      emit(byte)
      i++
    }
    this.carry = held >= 0 ? Buffer.from(data.subarray(held)) : Buffer.alloc(0)
    return Buffer.from(out)
  }

  /** The bytes still held back (an unfinished sequence): the probe records them, an attempt never needs them. */
  held(): Buffer { return Buffer.from(this.carry) }
}

/** The whole `bytes` through a fresh view (a test and probe convenience; the held-back tail is not part of the result). */
export const conptyView = (bytes: Uint8Array): Buffer => new ConptyView().push(bytes)

/**
 * One view per terminal, for an attempt. Off Windows `view` returns the chunk it was given (the same object), so the attempt is the g1e attempt byte for byte; on Windows it returns the normalised chunk
 * (possibly empty: the caller does not feed an empty chunk to the stream).
 */
export function conptyViews(platform: string = process.platform): { view: (tid: string, data: Uint8Array) => Uint8Array } {
  const views = new Map<string, ConptyView>()
  return {
    view: (tid, data) => {
      if (platform !== 'win32') return data
      let view = views.get(tid)
      if (!view) { view = new ConptyView(); views.set(tid, view) }
      return view.push(data)
    },
  }
}
