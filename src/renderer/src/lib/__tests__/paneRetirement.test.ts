import { describe, expect, it, vi } from 'vitest'
import { closePaneHidden, idsWithoutClosing, pendingPaneClose, settlePaneTerminals, withoutClosing } from '../paneRetirement'

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
  it('a second close of a closing pane joins the pending close: one retire, one answer, hidden until it settles', async () => {
    const closing = new Set<string>()
    const first = deferred<object | null>()
    const retire = vi.fn(() => settlePaneTerminals({ settle: () => first.promise }))
    const firstClose = closePaneHidden(closing, 'p1', retire)
    const secondClose = closePaneHidden(closing, 'p1', retire)
    await Promise.resolve()
    expect(closing.has('p1')).toBe(true)
    first.resolve(null)
    expect(await firstClose).toBe(false)
    expect(await secondClose).toBe(false)
    expect(retire).toHaveBeenCalledOnce()
    expect(closing.has('p1')).toBe(false)
  })
  it('a group with only a closing pane can await its refusal without starting another close', async () => {
    const closing = new Set<string>()
    const late = deferred<boolean>()
    const retire = vi.fn(() => late.promise)
    const firstClose = closePaneHidden(closing, 'p1', retire)
    const observed = pendingPaneClose(closing, 'p1')
    expect(observed).toBe(firstClose)
    late.resolve(false)
    expect(await observed).toBe(false)
    expect(retire).toHaveBeenCalledOnce()
  })
  it('a group with visible and closing panes can await both without closing the hidden member twice', async () => {
    const closing = new Set<string>()
    const late = deferred<boolean>()
    const retired: string[] = []
    const hiddenClose = closePaneHidden(closing, 'hidden', async () => {
      retired.push('hidden')
      return late.promise
    })
    const observed = pendingPaneClose(closing, 'hidden')
    const visibleClose = closePaneHidden(closing, 'visible', async () => {
      retired.push('visible')
      return true
    })
    await visibleClose
    expect(retired).toEqual(['hidden', 'visible'])
    late.resolve(true)
    expect(await observed).toBe(true)
    expect(await hiddenClose).toBe(true)
    expect(retired).toEqual(['hidden', 'visible'])
  })
  it('a pane closed again after its close finished is retired again (refusal reveals it, a retry is a new close)', async () => {
    const closing = new Set<string>()
    const retire = vi.fn(async () => false)
    await closePaneHidden(closing, 'p1', retire)
    await closePaneHidden(closing, 'p1', retire)
    expect(retire).toHaveBeenCalledTimes(2)
  })
  it('a synchronous throw from retire still reveals the pane and leaves no stale pending close', async () => {
    const closing = new Set<string>()
    await expect(closePaneHidden(closing, 'p1', () => { throw new Error('sync') })).rejects.toThrow('sync')
    expect(closing.has('p1')).toBe(false)
    expect(await closePaneHidden(closing, 'p1', async () => true)).toBe(true)
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

describe('withoutClosing — a closing pane is not listed on any surface', () => {
  const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]

  it('drops exactly the closing panes and keeps the order of the rest', () => {
    expect(withoutClosing(items, new Set(['b']))).toEqual([{ id: 'a' }, { id: 'c' }])
  })
  it('returns the input unchanged when nothing is closing', () => {
    expect(withoutClosing(items, new Set())).toBe(items)
  })
  it('lists the pane again once its close is refused', async () => {
    const closing = new Set<string>()
    let refuse: (retired: boolean) => void = () => {}
    const late = new Promise<boolean>((resolve) => { refuse = resolve })
    const pending = closePaneHidden(closing, 'b', () => late)
    expect(withoutClosing(items, closing).map((p) => p.id)).toEqual(['a', 'c'])
    refuse(false)
    await pending
    expect(withoutClosing(items, closing).map((p) => p.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('idsWithoutClosing — a hidden closing pane is not part of a batch action', () => {
  it('drops the closing ids and keeps the order of the rest', () => {
    expect(idsWithoutClosing(['a', 'b', 'c'], new Set(['b']))).toEqual(['a', 'c'])
  })
  it('returns the input unchanged when nothing is closing', () => {
    const ids = ['a', 'b']
    expect(idsWithoutClosing(ids, new Set())).toBe(ids)
  })
  it('a batch close during settlement does not close the closing pane a second time', async () => {
    const closing = new Set<string>()
    const closed: string[] = []
    const settle = new Promise<boolean>(() => {})
    void closePaneHidden(closing, 'a', async () => { closed.push('a'); return await settle })
    await Promise.resolve()
    // The user selected a and b, closed a, then picks "Remove selected".
    for (const id of idsWithoutClosing(['a', 'b'], closing)) {
      await closePaneHidden(closing, id, async () => { closed.push(id); return true })
    }
    expect(closed).toEqual(['a', 'b'])
  })
  it('review-5 B4: a batch [a, b] whose ids were snapshotted earlier closes b once when b is closed separately meanwhile', async () => {
    const closing = new Set<string>()
    const closed: string[] = []
    let settleA!: (retired: boolean) => void
    const slowA = new Promise<boolean>((resolve) => { settleA = resolve })
    let settleB!: (retired: boolean) => void
    const slowB = new Promise<boolean>((resolve) => { settleB = resolve })
    const retireOf = (id: string) => async () => { closed.push(id); return await (id === 'a' ? slowA : slowB) }
    // The batch snapshot is taken before anything is closing, exactly like batchKill's ids argument.
    const snapshot = ['a', 'b']
    const batch = (async () => { for (const id of snapshot) await closePaneHidden(closing, id, retireOf(id)) })()
    await Promise.resolve()
    // b is closed separately (card or keyboard) while a is still settling.
    const separate = closePaneHidden(closing, 'b', retireOf('b'))
    settleA(true)
    // The batch now reaches b while b's separate close is still settling.
    await new Promise((resolve) => setTimeout(resolve, 0))
    settleB(true)
    await batch
    await separate
    expect(closed.filter((id) => id === 'b')).toHaveLength(1)
    expect(closed.filter((id) => id === 'a')).toHaveLength(1)
  })
  it('review-5 B4: a batch id whose pane already settled and left the record is not closed again', async () => {
    const closing = new Set<string>()
    const open = new Set(['a', 'b'])
    const closed: string[] = []
    let settleA!: (retired: boolean) => void
    const slowA = new Promise<boolean>((resolve) => { settleA = resolve })
    const close = (id: string, wait?: Promise<boolean>) => closePaneHidden(closing, id, async () => {
      closed.push(id)
      const ok = wait ? await wait : true
      if (ok) open.delete(id)
      return ok
    }, () => open.has(id))
    const batch = (async () => { for (const id of ['a', 'b']) await close(id, id === 'a' ? slowA : undefined) })()
    await Promise.resolve()
    await close('b')
    settleA(true)
    await batch
    expect(closed).toEqual(['a', 'b'])
  })
})
