// @vitest-environment happy-dom
//
// A pane's cancellation of an in-flight terminal.create must consume the backend's verdict, not
// the envelope: `{ok: true, payload: {cancelled: false}}` means the create COMMITTED and its
// success (with the only terminal id) is on its way. The pane must adopt that id, close it through
// the existing identity-verified kill, and only then let a replacement create go out. A create
// whose response is lost must fence the pane instead of assuming cleanup (review-7 R7-1).
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createMockBackend, withScope, flush } from './mockBackend'

const store = vi.hoisted(() => {
  const values = new Map<string, string>()
  values.set('terminal-last-size', JSON.stringify({ cols: 80, rows: 24 }))
  vi.stubGlobal('localStorage', {
    get length(): number { return values.size },
    key: (i: number) => Array.from(values.keys())[i] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, String(value)) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => {
      values.clear()
      values.set('terminal-last-size', JSON.stringify({ cols: 80, rows: 24 }))
    },
  })
  return values
})
const writes = vi.hoisted(() => [] as string[])
const ctrl = vi.hoisted(() => ({
  applyFit: vi.fn(), sendResizeNow: vi.fn(), requestResizeRedraw: vi.fn(), setColsCap: vi.fn(),
  capCols: vi.fn((cols: number) => cols), attachObserver: vi.fn(), dispose: vi.fn(), ackedCols: 0, ackedRows: 0,
}))
vi.mock('../useTerminalResize', () => ({ createResizeController: () => ctrl }))
vi.mock('@xterm/xterm', () => {
  class Terminal {
    cols = 80
    rows = 24
    options: Record<string, unknown> = {}
    unicode = { activeVersion: '6' }
    textarea = document.createElement('textarea')
    buffer = { active: { type: 'normal', viewportY: 0, baseY: 0, cursorX: 0, cursorY: 0, getLine: () => undefined } }
    loadAddon(): void {}
    open(): void {}
    attachCustomWheelEventHandler(): void {}
    attachCustomKeyEventHandler(): void {}
    registerLinkProvider(): { dispose(): void } { return { dispose(): void {} } }
    onResize(): { dispose(): void } { return { dispose(): void {} } }
    onData(): { dispose(): void } { return { dispose(): void {} } }
    write(data?: string): void { if (typeof data === 'string') writes.push(data) }
    writeln(data?: string): void { if (typeof data === 'string') writes.push(data) }
    resize(): void {}
    focus(): void {}
    select(): void {}
    clearSelection(): void {}
    hasSelection(): boolean { return false }
    onSelectionChange(): { dispose: () => void } { return { dispose: (): void => {} } }
    scrollLines(): void {}
    scrollToBottom(): void {}
    dispose(): void {}
  }
  return { Terminal }
})
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit(): void {}
    proposeDimensions(): { cols: number; rows: number } { return { cols: 80, rows: 24 } }
  },
}))
vi.mock('@xterm/addon-serialize', () => ({
  SerializeAddon: class { activate(): void {}; dispose(): void {}; serialize(): string { return '' } },
}))

import { useTerminal } from '../useTerminal'

type Resp = { id: string; type: string; ok: boolean; payload: any; error: any; timestamp: string }
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const response = (type: string, ok: boolean, payload: unknown, error: unknown = null): Resp =>
  ({ id: 't', type, ok, payload, error, timestamp: '' })
const created = (id: string) => response('terminal.create.result', true, { terminal_session_id: id, pid: 7 })
const verdict = (cancelled: boolean) =>
  response('terminal.create.cancel.result', true, { ok: true, pane_id: 'pane-1', create_generation: 'g', cancelled })
const killed = () => response('terminal.kill.result', true, { ok: true })

function harness() {
  const mock = createMockBackend()
  const log: string[] = []
  const creates: Array<{ generation: string; d: ReturnType<typeof deferred<Resp>> }> = []
  const cancels: Array<{ generation: string; d: ReturnType<typeof deferred<Resp>> }> = []
  const kills: Array<{ id: string; force: boolean; d: ReturnType<typeof deferred<Resp>> }> = []
  const port = {
    ...mock.backend,
    create: (request: { createGeneration: string }) => {
      const d = deferred<Resp>()
      creates.push({ generation: request.createGeneration, d })
      log.push(`create:${creates.length}`)
      return d.promise
    },
    cancelCreate: (_pane: string, generation: string) => {
      const d = deferred<Resp>()
      cancels.push({ generation, d })
      log.push(`cancel:${cancels.length}`)
      return d.promise
    },
    kill: (id: string, force: boolean) => {
      const d = deferred<Resp>()
      kills.push({ id, force, d })
      log.push(`kill:${id}`)
      return d.promise
    },
  }
  const { result, scope } = withScope(() => useTerminal('pane-1', port as never))
  result.mount(document.createElement('div'))
  return { mock, port, result, scope, log, creates, cancels, kills }
}

function tracked<T>(promise: Promise<T>) {
  const state = { settled: false, error: null as Error | null }
  promise.then(() => { state.settled = true }, (error: Error) => { state.settled = true; state.error = error })
  return state
}

const spawnOpts = { command: 'bash', cwd: '/tmp', skipReattach: true, agentKey: 'claude' }

/** The port's status is read-only to consumers; the mock's underlying ref is the transport. */
function setStatus(h: { mock: ReturnType<typeof createMockBackend> }, value: 'connected' | 'disconnected'): void {
  ;(h.mock.backend.status as unknown as { value: string }).value = value
}

describe('useTerminal — consuming the backend cancellation verdict (review-7 R7-1)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    store.clear()
    writes.length = 0
  })

  it('cancelled:false then the late success: adopts the id, kills it, and resolves only after the kill', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    expect(h.creates).toHaveLength(1)
    const cancelling = h.result.cancelPendingCreate()
    const state = tracked(cancelling)
    await flush()
    h.cancels[0].d.resolve(verdict(false))
    await flush()
    expect(state.settled, 'cancelled:false is not a verified cancellation').toBe(false)
    h.creates[0].d.resolve(created('late-1'))
    await flush()
    expect(h.kills.map((k) => [k.id, k.force])).toEqual([['late-1', true]])
    expect(state.settled, 'resolved before the adopted term was killed').toBe(false)
    expect(h.result.sessionId.value).not.toBe('late-1')
    h.kills[0].d.resolve(killed())
    await cancelling
    expect(state.error).toBeNull()
    h.scope.stop()
  })

  it('a late success that lands before the cancel verdict is adopted and killed the same way', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    const state = tracked(cancelling)
    await flush()
    h.creates[0].d.resolve(created('late-2'))
    await flush()
    h.cancels[0].d.resolve(verdict(false))
    await flush()
    expect(h.kills.map((k) => k.id)).toEqual(['late-2'])
    expect(state.settled).toBe(false)
    h.kills[0].d.resolve(killed())
    await cancelling
    expect(h.result.sessionId.value).not.toBe('late-2')
    h.scope.stop()
  })

  it('a replacement create is not sent until the adopted predecessor is verifiably killed', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    await flush()
    h.cancels[0].d.resolve(verdict(false))
    h.creates[0].d.resolve(created('late-3'))
    await flush()
    h.result.status.value = 'idle'  // as the panel's cancel wrapper does
    const respawn = h.result.spawn(spawnOpts)
    const respawnState = tracked(respawn)
    await flush()
    expect(h.creates, 'a replacement went out while the predecessor was alive').toHaveLength(1)
    h.kills[0].d.resolve(killed())
    await cancelling
    await flush()
    expect(h.creates).toHaveLength(2)
    expect(h.log.indexOf('kill:late-3')).toBeLessThan(h.log.indexOf('create:2'))
    h.creates[1].d.resolve(created('fresh'))
    await respawn
    expect(respawnState.error).toBeNull()
    expect(h.result.sessionId.value).toBe('fresh')
    h.scope.stop()
  })

  it('a refused kill of the adopted term is not a cancellation: the pane is fenced', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    cancelling.catch(() => {})  // observed below; attached now so the rejection is never unhandled
    await flush()
    h.cancels[0].d.resolve(verdict(false))
    h.creates[0].d.resolve(created('late-4'))
    await flush()
    h.kills[0].d.resolve(response('terminal.kill.result', false, null, { code: 'CLEANUP_FAILED', message: 'x' }))
    await expect(cancelling).rejects.toThrow()
    expect(h.result.status.value).toBe('error')
    h.result.status.value = 'idle'
    await expect(h.result.spawn(spawnOpts)).rejects.toThrow(/fenced|unresolved/i)
    expect(h.creates).toHaveLength(1)
    h.scope.stop()
  })

  it('cancelled:true on the same connection is the proof: no kill, the replacement may go out', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    await flush()
    h.cancels[0].d.resolve(verdict(true))
    await cancelling
    h.creates[0].d.resolve(response('terminal.create.result', false, null, { code: 'CREATE_CANCELLED', message: 'c' }))
    await flush()
    expect(h.kills).toEqual([])
    h.scope.stop()
  })

  it('a lost create response on the same connection is recovered by the existing same-generation replay and killed', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const generation = h.creates[0].generation
    const cancelling = h.result.cancelPendingCreate()
    await flush()
    h.creates[0].d.reject(new Error('request terminal.create timeout'))
    await flush()
    h.cancels[0].d.resolve(verdict(false))
    await flush()
    expect(h.creates).toHaveLength(2)
    expect(h.creates[1].generation, 'the replay must reuse the committed generation').toBe(generation)
    h.creates[1].d.resolve(created('replayed-1'))
    await flush()
    expect(h.kills.map((k) => k.id)).toEqual(['replayed-1'])
    h.kills[0].d.resolve(killed())
    await cancelling
    h.scope.stop()
  })

  it('a create lost to a reconnect cannot be proven by a cancel on the new connection: the pane is fenced', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    cancelling.catch(() => {})
    await flush()
    // The cancel went out on the old connection and was lost with it, as was the create.
    setStatus(h, 'disconnected')
    await flush()
    h.creates[0].d.reject(new Error('WebSocket closed'))
    h.cancels[0].d.reject(new Error('WebSocket closed'))
    setStatus(h, 'connected')
    await flush()
    await expect(cancelling).rejects.toThrow()
    expect(h.result.status.value).toBe('error')
    expect(h.kills).toEqual([])
    h.result.status.value = 'idle'
    await expect(h.result.spawn(spawnOpts)).rejects.toThrow(/fenced|unresolved/i)
    expect(h.creates).toHaveLength(1)
    h.scope.stop()
  })

  it('a cancelled:true received after a reconnect is not proof for a create sent before it', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    setStatus(h, 'disconnected')
    await flush()
    h.creates[0].d.reject(new Error('WebSocket closed'))
    setStatus(h, 'connected')
    await flush()
    const cancelling = h.result.cancelPendingCreate()
    cancelling.catch(() => {})
    await flush()
    if (h.cancels.length) h.cancels[h.cancels.length - 1].d.resolve(verdict(true))
    await expect(cancelling).rejects.toThrow()
    expect(h.result.status.value).toBe('error')
    h.scope.stop()
  })
})

describe('useTerminal — a cancel queued across a reconnect (review-7 R7-1)', () => {
  it('a cancelled:true that was requested before the reconnect but answered after it is not proof', async () => {
    const h = harness()
    void h.result.spawn(spawnOpts)
    await flush()
    setStatus(h, 'disconnected')
    await flush()
    // The create is lost with the old connection; its settlement's cancel is queued now ...
    h.creates[0].d.reject(new Error('socket superseded'))
    await flush()
    expect(h.cancels).toHaveLength(1)
    // ... and answered on the NEW connection, where that create never existed.
    setStatus(h, 'connected')
    await flush()
    h.cancels[0].d.resolve(verdict(true))
    await flush()
    expect(h.result.status.value).toBe('error')
    expect(h.result.error.value).toMatch(/unresolved/)
    h.result.status.value = 'idle'
    await expect(h.result.spawn(spawnOpts)).rejects.toThrow(/unresolved/)
    expect(h.creates).toHaveLength(1)
    h.scope.stop()
  })
})
