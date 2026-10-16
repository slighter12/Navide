// @vitest-environment happy-dom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// useTerminal.kill rejects a refused kill (TERMINAL_NOT_OWNED) instead of
// resolving it as done. onKill catches that — and for the callers that keep the
// pane record (idle reclaim, kill-all) no unspawn follows to sweep what the kill
// missed, so the PTY ran on with nothing pointing at it. The catch has to ask
// the backend for the same sweep a kept-record workspace close asks for.
//
// Source-scanned, like the other App.*.test.ts files: App.vue cannot be
// mounted, since backend and terminal lifecycles start on mount.
const read = (p: string): string => readFileSync(resolve(process.cwd(), p), 'utf8')
const appSource = read('src/renderer/src/App.vue')

/** A top-level function's text, up to the next declaration. */
function body(source: string, name: string): string {
  for (const pat of [`async function ${name}(`, `function ${name}(`]) {
    const at = source.indexOf(pat)
    if (at < 0) continue
    const rest = source.slice(at + pat.length)
    const next = /\n(?:async )?function \w+|\nconst \w+ =|\n\/\*\*/.exec(rest)
    return source.slice(at, at + pat.length + (next ? next.index : 3000))
  }
  throw new Error(`${name} not found`)
}

describe('onKill when the terminal kill is refused', () => {
  const fn = body(appSource, 'onKill')

  // Review-9: the backend settlement is no longer a fallback of a failed kill — it always follows
  // the pane's own kill and is the proof (lib/paneRetirement.ts; App.retirementRule.test.ts).
  it('records the refused kill and still has the backend settle (a release when no unspawn will follow)', () => {
    expect(fn).toContain("onKillFailed: (err) => recordDiagnostic({")
    expect(fn).toContain("code: 'pane.killFailed'")
    expect(fn).toContain("return sendQuiet<{ ok: boolean }>('manual_pane.release_pty', { pane_id: paneId }, PANE_RELEASE_TIMEOUT_MS)")
  })
})
