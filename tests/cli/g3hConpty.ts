// The Windows ConPTY view of a renderer stream (rh16, windows-4b). Test-only; a pack file (`g3h*`), imported by the G3 copy of the shell attempt (`g3ShellAttempt.ts`, see attempt_copy.py) and by the ConPTY probe.
//
// An inbox ConPTY renders its screen instead of passing the peer's bytes through: it ends lines with CR LF, cuts a line longer than the console width with CR LF and cursor-position sequences, and puts CSI
// sequences between the text. The product's own Windows test strips exactly that before it compares exact bytes (`tests/cli/shellRuntime.test.ts`: the READY line is read up to the first CR LF, and the burst
// view is `replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\r\n(?=x)/g, '')`, "a CR LF that is followed by the line's own filler cannot be a line end"). This module applies the same three rules, and
// since rh17 a fourth for a console repaint (below), to the bytes the attempt matches and hashes, on Windows only:
//   1. a CSI sequence (the product's own pattern) is removed;
//   2. a CR LF that follows the filler character `x` and is followed by one (after any CSI) is a wrap cut and is removed;
//   3. every other CR LF becomes LF (the peer wrote LF and asked for raw mode; a Windows console post-processes its output all the same, rh17 B-3).
// The view is the same for both arms, and off Windows it is the identity (the same object comes back). It is streaming: the bytes of a sequence that is not finished (an unterminated CSI, a trailing CR, a CR LF
// after `x` whose next byte has not arrived) are held back until they are, so a chunk boundary can never change the result, and a LF that cannot be a wrap cut is passed on at once (an ACK line ending in LF is
// seen when its chunk arrives, not when the next one does).
//
// rh17 (B-2, from the diagnostic run on windows-2025, `OUT/diag1/results.md`): the inbox console the sidecar uses paints its whole screen again after every size change (7 of 7 in the run, 54 to 56 KB each); the
// console the Python backend uses (pywinpty's own host) never does. A repaint is a frame `CSI H` <rows, each ended by `CSI K` CR LF> [`CSI row;col H`] `CSI ?25h`, and its rows are the lines the console
// already emitted. One more rule, the same for both arms:
//   4. a frame from a bare cursor-home sequence (`CSI H`) to the next show-cursor sequence (`CSI ?25h`), met after text has been passed on, is compared row by row with the lines that END the text already
//      passed on. A frame whose rows, down to the row the cursor is on, are exactly those lines in order, and whose rows below the cursor row are all empty, was emitted before and is dropped. Every other
//      frame is passed on whole by rules 1 to 3 (so a duplicate stays visible to the ledger), and so is a frame that does not end within `FRAME_LIMIT` bytes.
// rh18 (the rh17 review, SF-3 and T-3): rule 4 never adds or keeps a part of a frame. rh17 passed on "what the frame holds after the repainted rows" and never looked below the cursor row, so text there was
// dropped unseen. A frame that holds anything but the repainted lines (a new row, a longer last line, text below the cursor row) is now passed on whole. In the 7 real frames of the diagnostic run and in
// the 21 frames of a simulated full attempt the dropped part was the whole frame, so nothing changes for them.
// What rule 4 can hide: lines sent a second time, byte for byte and in order, inside such a frame. Lines repeated anywhere else, and any changed, missing or extra byte, still reach the digests.
//
// The width: the burst line is 2048 filler characters wide, and the attempt asks for 100 columns. Where the console takes a wider size (`CreatePseudoConsole` takes a 16-bit width; the sidecar and the backend
// pass the value through), the attempt asks for `conptyColumns(n)` instead, so that the line is not cut at all and rule 2 stays a safety net.
export const FILLER = 0x78 // 'x', the burst filler of g1e_peer.py (`xs="x" * burstWidth`)
export const CONPTY_EXTRA_COLUMNS = 2200 // 100 -> 2300, 120 -> 2320, 140 -> 2340: wider than the 2048-wide burst line plus its prefix; the differences between the sizes of the workload are kept
export const HISTORY_BYTES = 256 * 1024 // how much passed-on text rule 4 keeps to compare a repaint with: more than a 40-row screen of 2073-byte burst lines (83 KB)
export const FRAME_LIMIT = 1024 * 1024 // a frame that has not ended after this many bytes is not held back any longer

/** The console width the attempt asks for: the workload's own on every other platform, wider by a fixed amount on Windows. */
export const conptyColumns = (columns: number, platform: string = process.platform): number => (platform === 'win32' ? columns + CONPTY_EXTRA_COLUMNS : columns)

/**
 * rh17 (B-2): true on Windows, where the attempt asks for no size change before the peer's FINAL line. The product's log mirror keeps a repaint's rows as text, and the pinned log identity compares that text
 * with the peer's up to the FINAL line, so a size change before it cannot pass on the inbox console. The attempt then sends the observation-phase resize right after the FINAL lines, before the 20 resize
 * probes, which are sent unchanged: every peer gets the requests of the workload in the same order, and every one of them still changes the size.
 */
export const conptySizeFixedUntilFinal = (platform: string = process.platform): boolean => platform === 'win32'

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

const SHOW_CURSOR = Buffer.from('\x1b[?25h')
const CURSOR_POSITION = /\x1b\[(\d+);\d+H/g

function splitLines(text: Buffer): Buffer[] {
  const out: Buffer[] = []
  let from = 0
  for (let at = text.indexOf(0x0a); at >= 0; at = text.indexOf(0x0a, from)) { out.push(text.subarray(from, at)); from = at + 1 }
  out.push(text.subarray(from))
  return out
}

export class ConptyView {
  private carry: Buffer = Buffer.alloc(0)
  private last = -1
  private recent: Buffer[] = [] // the bytes passed on by earlier calls, the last HISTORY_BYTES of them at least (rule 4)
  private recentBytes = 0
  private trimmed = false

  /** `frames: false` applies rules 1 to 3 only (the rows of a frame are read that way). */
  constructor(private readonly frames: boolean = true) {}

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
        if (this.frames && end === i + 3 && data[i + 2] === 0x48 && (this.recentBytes > 0 || out.length > 0)) { // rule 4: a bare `CSI H` after text
          const stop = data.indexOf(SHOW_CURSOR, end)
          if (stop < 0 && data.length - i <= FRAME_LIMIT) { held = i; break }
          if (stop >= 0) {
            const body = data.subarray(end, stop)
            const rows = new ConptyView(false).push(Buffer.concat([body, SHOW_CURSOR]))
            if (!this.isRepaint(body, rows, out)) for (const byte of rows) emit(byte)
            i = stop + SHOW_CURSOR.length
            continue
          }
        }
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
    const passed = Buffer.from(out)
    if (passed.length > 0) {
      this.recent.push(passed)
      this.recentBytes += passed.length
      while (this.recent.length > 1 && this.recentBytes - this.recent[0].length >= HISTORY_BYTES) { this.recentBytes -= this.recent.shift()!.length; this.trimmed = true }
    }
    return passed
  }

  /**
   * Rule 4. `rows` is a frame's text by rules 1 to 3 and `body` its raw bytes; `out` is what this call has passed on so far. True when the frame holds the lines that end the passed-on text and nothing
   * else: its rows below the cursor row are empty, the row the cursor is on is the line being written, and the rows above it are the complete lines before that one, in order.
   */
  private isRepaint(body: Buffer, rows: Buffer, out: number[]): boolean {
    const own = out.length > HISTORY_BYTES ? out.slice(out.length - HISTORY_BYTES) : out
    const history = splitLines(own === out ? Buffer.concat([...this.recent, Buffer.from(own)]) : Buffer.from(own))
    if ((this.trimmed || own !== out) && history.length > 1) history.shift() // the oldest piece may start inside a line
    const partial = history.pop()! // the line being written: no LF yet
    const screen = splitLines(rows)
    const cursor = [...body.toString('latin1').matchAll(CURSOR_POSITION)].pop()
    const cursorRows = cursor ? Number(cursor[1]) : screen.length // how many rows there are down to the cursor's
    if (screen.slice(cursorRows).some((row) => row.length > 0)) return false // a repaint has only empty rows below the cursor row
    const content = screen.slice(0, cursorRows)
    const above = content.length - 1
    if (above < (history.length > 0 ? 1 : 0) || above > history.length) return false
    return content[above].equals(partial) && content.slice(0, above).every((row, k) => row.equals(history[history.length - above + k]))
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
