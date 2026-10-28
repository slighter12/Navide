import { describe, expect, it, vi } from 'vitest'
import { closePaneHidden, settlePaneTerminals } from '../paneRetirement'

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

describe('closePaneHidden — the pane is hidden while settlement is pending', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void
    const promise = new Promise<T>((r) => { resolve = r })
    return { promise, resolve }
  }
  function close(closing: Set<string>, answer: Promise<object | null>) {
    return closePaneHidden(closing, 'p1', () => settlePaneTerminals({ killOwn: async () => {}, settle: () => answer }))
  }

  it('is hidden before a late settlement resolves, and removed after a proven answer', async () => {
    const closing = new Set<string>()
    const late = deferred<object | null>()
    const pending = close(closing, late.promise)
    await Promise.resolve()
    expect(closing.has('p1')).toBe(true)
    late.resolve({ ok: true })
    expect(await pending).toBe(true)
    expect(closing.has('p1')).toBe(false)
  })
  it.each([
    ['a refusal', { ok: false }],
    ['a timeout or transport failure (sendQuiet null)', null],
  ])('is shown again after %s, and reports it unsettled', async (_label, answer) => {
    const closing = new Set<string>()
    const late = deferred<object | null>()
    const pending = close(closing, late.promise)
    await Promise.resolve()
    expect(closing.has('p1')).toBe(true)
    late.resolve(answer)
    expect(await pending).toBe(false)
    expect(closing.has('p1')).toBe(false)
  })
  it('is shown again when the retirement throws', async () => {
    const closing = new Set<string>()
    await expect(closePaneHidden(closing, 'p1', async () => { throw new Error('boom') })).rejects.toThrow('boom')
    expect(closing.has('p1')).toBe(false)
  })
  it('a caller that starts a replacement only after a proven answer starts none while pending or refused', async () => {
    const closing = new Set<string>()
    const replacements: string[] = []
    const late = deferred<object | null>()
    const run = async () => { if (await close(closing, late.promise)) replacements.push('spawn') }
    const pending = run()
    await Promise.resolve()
    expect(replacements).toEqual([])
    late.resolve(null)
    await pending
    expect(replacements).toEqual([])
  })
})
