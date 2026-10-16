import { describe, expect, it, vi } from 'vitest'
import { settlePaneTerminals } from '../paneRetirement'

describe('settlePaneTerminals — the settlement answer is the only proof', () => {
  it('a proven settlement after a successful own kill retires', async () => {
    expect(await settlePaneTerminals({ killOwn: async () => {}, settle: async () => ({ ok: true }) })).toBe(true)
  })
  it('a failed own kill is reported but the backend settlement still decides', async () => {
    const failed = vi.fn()
    expect(await settlePaneTerminals({
      killOwn: async () => { throw new Error('refused') }, onKillFailed: failed, settle: async () => ({ ok: true }),
    })).toBe(true)
    expect(failed).toHaveBeenCalledOnce()
  })
  it.each([
    ['a refusal / timeout / transport failure (sendQuiet null)', null],
    ['an explicit ok:false', { ok: false }],
  ])('%s is a hard failure, even after a successful own kill', async (_label, answer) => {
    expect(await settlePaneTerminals({ killOwn: async () => {}, settle: async () => answer })).toBe(false)
  })
  it('a project payload (unspawn / slot_unspawn) proves settlement', async () => {
    expect(await settlePaneTerminals({ settle: async () => ({ panes: [] } as never) })).toBe(true)
  })
  it('settles even when the pane holds no terminal id', async () => {
    const settle = vi.fn(async () => ({ ok: true }))
    expect(await settlePaneTerminals({ settle })).toBe(true)
    expect(settle).toHaveBeenCalledOnce()
  })
})
